"""
Orchestrator Service for the Multi-Exchange Trading Bot
Main trading coordination and decision making
"""

import asyncio
import logging
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
import signal
import sys
import os
import httpx
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import uuid

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="Orchestrator Service",
    description="Main trading coordination and decision making",
    version="1.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Data Models
class TradingStatus(BaseModel):
    status: str  # 'running', 'stopped', 'emergency_stop'
    cycle_count: int
    active_trades: int
    total_pnl: float
    last_cycle: datetime
    uptime: timedelta

class TradingCycle(BaseModel):
    cycle_id: str
    cycle_type: str  # 'entry', 'exit', 'maintenance'
    start_time: datetime
    end_time: Optional[datetime] = None
    trades_processed: int
    signals_generated: int
    errors: List[str]

class RiskLimits(BaseModel):
    max_concurrent_trades: int
    max_daily_trades: int
    max_daily_loss: float
    max_total_loss: float
    position_size_percentage: float

class TradeSignal(BaseModel):
    exchange: str
    pair: str
    signal: str  # 'buy', 'sell', 'hold'
    confidence: float
    strength: float
    strategy: str
    timestamp: datetime

class HealthResponse(BaseModel):
    status: str
    timestamp: datetime
    version: str
    trading_status: str
    cycle_count: int
    active_trades: int

# Global variables
trading_status = "stopped"
cycle_count = 0
active_trades = {}
pair_selections = {}
balances = {}
running = False
start_time = None

# Service URLs
config_service_url = os.getenv("CONFIG_SERVICE_URL", "http://config-service:8001")
database_service_url = os.getenv("DATABASE_SERVICE_URL", "http://database_service:8002")
exchange_service_url = os.getenv("EXCHANGE_SERVICE_URL", "http://exchange-service:8003")
strategy_service_url = os.getenv("STRATEGY_SERVICE_URL", "http://strategy-service:8004")

class TradingOrchestrator:
    """Main orchestrator for the multi-exchange trading bot"""
    
    def __init__(self):
        self.running = False
        self.cycle_count = 0
        self.active_trades = {}
        self.pair_selections = {}
        self.balances = {}
        self.start_time = None
        
    async def initialize(self) -> bool:
        """Initialize the orchestrator"""
        try:
            logger.info("Initializing Trading Orchestrator...")
            
            # Initialize balances
            await self._initialize_balances()
            
            # Initialize pair selections
            await self._initialize_pair_selections()
            
            logger.info("Trading Orchestrator initialized successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize Trading Orchestrator: {e}")
            return False
            
    async def _initialize_balances(self) -> None:
        """Initialize balance tracking for all exchanges"""
        try:
            # Get exchanges from config service
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{config_service_url}/api/v1/config/exchanges/list")
                response.raise_for_status()
                exchanges = response.json()['exchanges']
            
            for exchange_name in exchanges:
                # Check if simulation mode
                mode_response = await client.get(f"{config_service_url}/api/v1/config/mode")
                mode_response.raise_for_status()
                mode_data = mode_response.json()
                
                if mode_data['is_simulation']:
                    # In simulation mode, get balance from database
                    balance_response = await client.get(f"{database_service_url}/api/v1/balances/{exchange_name}")
                    if balance_response.status_code == 200:
                        balance = balance_response.json()
                        self.balances[exchange_name] = {
                            'total': float(balance['balance']),
                            'available': float(balance['available_balance']),
                            'total_pnl': float(balance['total_pnl']),
                            'daily_pnl': float(balance['daily_pnl'])
                        }
                    else:
                        # Initialize with default values
                        self.balances[exchange_name] = {
                            'total': 10000.0,  # Default simulation balance
                            'available': 10000.0,
                            'total_pnl': 0.0,
                            'daily_pnl': 0.0
                        }
                else:
                    # In live mode, get balance from exchange
                    balance_response = await client.get(f"{exchange_service_url}/api/v1/account/balance/{exchange_name}")
                    if balance_response.status_code == 200:
                        balance = balance_response.json()
                        self.balances[exchange_name] = {
                            'total': balance['total'].get('USDC', 0),
                            'available': balance['free'].get('USDC', 0),
                            'total_pnl': 0.0,  # Will be calculated from trades
                            'daily_pnl': 0.0   # Will be calculated from trades
                        }
                        
            logger.info(f"Initialized balances for {len(exchanges)} exchanges")
            
        except Exception as e:
            logger.error(f"Error initializing balances: {e}")
            
    async def _initialize_pair_selections(self) -> None:
        """Initialize pair selections for all exchanges"""
        try:
            # Get exchanges from config service
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{config_service_url}/api/v1/config/exchanges/list")
                response.raise_for_status()
                exchanges = response.json()['exchanges']
            
            for exchange_name in exchanges:
                # Get latest pair selection from database
                pairs_response = await client.get(f"{database_service_url}/api/v1/pairs/{exchange_name}")
                if pairs_response.status_code == 200:
                    pairs_data = pairs_response.json()
                    self.pair_selections[exchange_name] = pairs_data.get('pairs', [])
                else:
                    # Initialize with empty list
                    self.pair_selections[exchange_name] = []
                    
            logger.info(f"Initialized pair selections for {len(exchanges)} exchanges")
            
        except Exception as e:
            logger.error(f"Error initializing pair selections: {e}")
            
    async def start_trading(self) -> bool:
        """Start the trading orchestrator"""
        try:
            if self.running:
                logger.warning("Trading orchestrator is already running")
                return True
                
            if not await self.initialize():
                logger.error("Failed to initialize, cannot start trading")
                return False
                
            self.running = True
            self.start_time = datetime.utcnow()
            global trading_status, start_time
            trading_status = "running"
            start_time = self.start_time
            
            logger.info("Starting trading orchestrator...")
            
            # Start trading loop in background
            asyncio.create_task(self._trading_loop())
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to start trading: {e}")
            return False
            
    async def stop_trading(self) -> bool:
        """Stop the trading orchestrator"""
        try:
            self.running = False
            global trading_status
            trading_status = "stopped"
            logger.info("Trading orchestrator stopped")
            return True
            
        except Exception as e:
            logger.error(f"Failed to stop trading: {e}")
            return False
            
    async def emergency_stop(self) -> bool:
        """Emergency stop all trading activities"""
        try:
            self.running = False
            global trading_status
            trading_status = "emergency_stop"
            
            # Close all active trades
            await self._close_all_trades()
            
            logger.info("Emergency stop executed - all trading activities halted")
            return True
            
        except Exception as e:
            logger.error(f"Failed to execute emergency stop: {e}")
            return False
            
    async def _trading_loop(self) -> None:
        """Main trading loop"""
        try:
            # Get trading configuration
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{config_service_url}/api/v1/config/trading")
                response.raise_for_status()
                trading_config = response.json()
            
            cycle_interval = trading_config.get('cycle_interval_seconds', 60)
            exit_cycle_first = trading_config.get('exit_cycle_first', True)
            
            while self.running:
                try:
                    cycle_start = datetime.utcnow()
                    self.cycle_count += 1
                    global cycle_count
                    cycle_count = self.cycle_count
                    
                    logger.info(f"Starting trading cycle {self.cycle_count}")
                    
                    # Run exit cycle first if configured
                    if exit_cycle_first:
                        await self._run_exit_cycle()
                        
                    # Run entry cycle
                    await self._run_entry_cycle()
                    
                    # Run maintenance tasks
                    await self._run_maintenance_tasks()
                    
                    # Calculate cycle duration
                    cycle_duration = (datetime.utcnow() - cycle_start).total_seconds()
                    logger.info(f"Completed trading cycle {self.cycle_count} in {cycle_duration:.2f}s")
                    
                    # Wait for next cycle
                    if cycle_duration < cycle_interval:
                        await asyncio.sleep(cycle_interval - cycle_duration)
                        
                except Exception as e:
                    logger.error(f"Error in trading cycle {self.cycle_count}: {e}")
                    await asyncio.sleep(10)  # Wait before retrying
                    
        except Exception as e:
            logger.error(f"Fatal error in trading orchestrator: {e}")
            self.running = False
            global trading_status
            trading_status = "stopped"
            
    async def _run_exit_cycle(self) -> None:
        """Run exit cycle to check for trade exits"""
        try:
            logger.info("Running exit cycle...")
            
            # Get all open trades
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{database_service_url}/api/v1/trades/open")
                response.raise_for_status()
                open_trades = response.json()['trades']
            
            for trade in open_trades:
                await self._check_trade_exit(trade)
                
        except Exception as e:
            logger.error(f"Error in exit cycle: {e}")
            
    async def _check_trade_exit(self, trade: Dict[str, Any]) -> None:
        """Check if a trade should be exited"""
        try:
            exchange = trade['exchange']
            pair = trade['pair']
            
            # Get current price
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{exchange_service_url}/api/v1/market/ticker/{exchange}/{pair}")
                response.raise_for_status()
                ticker = response.json()
                current_price = ticker['last']
            
            # Check stop loss and take profit
            entry_price = trade['entry_price']
            position_size = trade['position_size']
            
            # Calculate PnL
            if trade['position_size'] > 0:  # Long position
                pnl_percentage = ((current_price - entry_price) / entry_price) * 100
            else:  # Short position
                pnl_percentage = ((entry_price - current_price) / entry_price) * 100
                
            # Check exit conditions
            exit_reason = None
            should_exit = False
            
            # Stop loss check (assuming 2% stop loss)
            if pnl_percentage <= -2.0:
                should_exit = True
                exit_reason = "stop_loss"
                
            # Take profit check (assuming 5% take profit)
            elif pnl_percentage >= 5.0:
                should_exit = True
                exit_reason = "take_profit"
                
            if should_exit:
                await self._execute_trade_exit(trade, current_price, exit_reason)
                
        except Exception as e:
            logger.error(f"Error checking trade exit for {trade.get('trade_id', 'unknown')}: {e}")
            
    async def _execute_trade_exit(self, trade: Dict[str, Any], exit_price: float, reason: str) -> None:
        """Execute trade exit"""
        try:
            trade_id = trade['trade_id']
            exchange = trade['exchange']
            pair = trade['pair']
            
            logger.info(f"Executing trade exit: {trade_id} at {exit_price} ({reason})")
            
            # Update trade in database
            update_data = {
                'exit_price': exit_price,
                'exit_time': datetime.utcnow().isoformat(),
                'status': 'CLOSED',
                'exit_reason': reason
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.put(f"{database_service_url}/api/v1/trades/{trade_id}", json=update_data)
                response.raise_for_status()
            
            # Remove from active trades
            if trade_id in self.active_trades:
                del self.active_trades[trade_id]
                
            logger.info(f"Trade {trade_id} closed successfully")
            
        except Exception as e:
            logger.error(f"Error executing trade exit for {trade.get('trade_id', 'unknown')}: {e}")
            
    async def _run_entry_cycle(self) -> None:
        """Run entry cycle to check for new trade opportunities"""
        try:
            logger.info("Running entry cycle...")
            
            # Check available balance
            if not await self._check_available_balance():
                logger.info("Insufficient balance for new trades")
                return
                
            # Check each exchange and pair
            for exchange_name, pairs in self.pair_selections.items():
                for pair in pairs:
                    await self._check_pair_entry(exchange_name, pair)
                    
        except Exception as e:
            logger.error(f"Error in entry cycle: {e}")
            
    async def _check_available_balance(self) -> bool:
        """Check if there's sufficient balance for new trades"""
        try:
            total_available = 0
            for exchange_name, balance in self.balances.items():
                total_available += balance['available']
                
            # Check if we have at least $100 available
            return total_available >= 100.0
            
        except Exception as e:
            logger.error(f"Error checking available balance: {e}")
            return False
            
    async def _check_pair_entry(self, exchange_name: str, pair: str) -> None:
        """Check if a pair should be entered"""
        try:
            # Get strategy signals
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{strategy_service_url}/api/v1/signals/consensus/{exchange_name}/{pair}")
                response.raise_for_status()
                consensus = response.json()
            
            signal = consensus['consensus_signal']
            confidence = consensus['confidence']
            agreement = consensus['agreement']
            
            # Check if we should enter a trade
            if signal == 'buy' and confidence > 0.7 and agreement > 60:
                await self._execute_trade_entry(exchange_name, pair, consensus)
                
        except Exception as e:
            logger.error(f"Error checking pair entry for {pair} on {exchange_name}: {e}")
            
    async def _execute_trade_entry(self, exchange_name: str, pair: str, signal: Dict[str, Any]) -> None:
        """Execute trade entry"""
        try:
            # Calculate position size
            balance = self.balances[exchange_name]['available']
            position_size = balance * 0.1  # Use 10% of available balance
            
            # Create trade record
            trade_id = str(uuid.uuid4())
            trade_data = {
                'trade_id': trade_id,
                'pair': pair,
                'exchange': exchange_name,
                'entry_price': 0.0,  # Will be filled by order execution
                'status': 'OPEN',
                'position_size': position_size,
                'strategy': 'consensus',
                'entry_time': datetime.utcnow().isoformat(),
                'entry_reason': f"Consensus signal: {signal['consensus_signal']} (confidence: {signal['confidence']:.2f})"
            }
            
            # Save trade to database
            async with httpx.AsyncClient() as client:
                response = await client.post(f"{database_service_url}/api/v1/trades", json=trade_data)
                response.raise_for_status()
            
            # Add to active trades
            self.active_trades[trade_id] = trade_data
            
            logger.info(f"Created trade {trade_id} for {pair} on {exchange_name}")
            
        except Exception as e:
            logger.error(f"Error executing trade entry for {pair} on {exchange_name}: {e}")
            
    async def _run_maintenance_tasks(self) -> None:
        """Run maintenance tasks"""
        try:
            # Update balances
            await self._update_balances()
            
            # Clean up old data
            await self._cleanup_old_data()
            
        except Exception as e:
            logger.error(f"Error in maintenance tasks: {e}")
            
    async def _update_balances(self) -> None:
        """Update balances from exchanges"""
        try:
            for exchange_name in self.balances.keys():
                async with httpx.AsyncClient() as client:
                    response = await client.get(f"{exchange_service_url}/api/v1/account/balance/{exchange_name}")
                    if response.status_code == 200:
                        balance = response.json()
                        self.balances[exchange_name] = {
                            'total': balance['total'].get('USDC', 0),
                            'available': balance['free'].get('USDC', 0),
                            'total_pnl': 0.0,  # Will be calculated from trades
                            'daily_pnl': 0.0   # Will be calculated from trades
                        }
                        
        except Exception as e:
            logger.error(f"Error updating balances: {e}")
            
    async def _cleanup_old_data(self) -> None:
        """Clean up old data"""
        try:
            # This would clean up old cache entries, logs, etc.
            pass
        except Exception as e:
            logger.error(f"Error cleaning up old data: {e}")
            
    async def _close_all_trades(self) -> None:
        """Close all active trades"""
        try:
            for trade_id, trade in self.active_trades.items():
                await self._execute_trade_exit(trade, 0.0, "emergency_stop")
                
        except Exception as e:
            logger.error(f"Error closing all trades: {e}")

# Global orchestrator
orchestrator = TradingOrchestrator()

# API Endpoints
@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint"""
    return HealthResponse(
        status="healthy",
        timestamp=datetime.utcnow(),
        version="1.0.0",
        trading_status=trading_status,
        cycle_count=cycle_count,
        active_trades=len(active_trades)
    )

@app.get("/ready")
async def readiness_check():
    """Readiness check endpoint"""
    return {"status": "ready"}

@app.get("/live")
async def liveness_check():
    """Liveness check endpoint"""
    return {"status": "alive"}

# Trading Control Endpoints
@app.post("/api/v1/trading/start")
async def start_trading():
    """Start trading"""
    try:
        success = await orchestrator.start_trading()
        if success:
            return {"message": "Trading started successfully"}
        else:
            raise HTTPException(status_code=500, detail="Failed to start trading")
    except Exception as e:
        logger.error(f"Error starting trading: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/trading/stop")
async def stop_trading():
    """Stop trading"""
    try:
        success = await orchestrator.stop_trading()
        if success:
            return {"message": "Trading stopped successfully"}
        else:
            raise HTTPException(status_code=500, detail="Failed to stop trading")
    except Exception as e:
        logger.error(f"Error stopping trading: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/trading/emergency-stop")
async def emergency_stop():
    """Emergency stop all trading"""
    try:
        success = await orchestrator.emergency_stop()
        if success:
            return {"message": "Emergency stop executed successfully"}
        else:
            raise HTTPException(status_code=500, detail="Failed to execute emergency stop")
    except Exception as e:
        logger.error(f"Error executing emergency stop: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/trading/status")
async def get_trading_status():
    """Get trading status"""
    uptime = None
    if start_time:
        uptime = datetime.utcnow() - start_time
        
    return TradingStatus(
        status=trading_status,
        cycle_count=cycle_count,
        active_trades=len(active_trades),
        total_pnl=0.0,  # Would calculate from trades
        last_cycle=datetime.utcnow(),
        uptime=uptime or timedelta(0)
    )

# Trading Operations Endpoints
@app.post("/api/v1/trading/cycle/entry")
async def run_entry_cycle():
    """Manually run entry cycle"""
    try:
        await orchestrator._run_entry_cycle()
        return {"message": "Entry cycle completed"}
    except Exception as e:
        logger.error(f"Error running entry cycle: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/trading/cycle/exit")
async def run_exit_cycle():
    """Manually run exit cycle"""
    try:
        await orchestrator._run_exit_cycle()
        return {"message": "Exit cycle completed"}
    except Exception as e:
        logger.error(f"Error running exit cycle: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/trading/active-trades")
async def get_active_trades():
    """Get active trades"""
    return {
        "active_trades": list(active_trades.values()),
        "count": len(active_trades)
    }

@app.get("/api/v1/trading/cycle-stats")
async def get_cycle_stats():
    """Get trading cycle statistics"""
    return {
        "cycle_count": cycle_count,
        "trading_status": trading_status,
        "start_time": start_time.isoformat() if start_time else None,
        "uptime": str(datetime.utcnow() - start_time) if start_time else "0:00:00"
    }

# Risk Management Endpoints
@app.get("/api/v1/risk/limits")
async def get_risk_limits():
    """Get current risk limits"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{config_service_url}/api/v1/config/trading")
            response.raise_for_status()
            trading_config = response.json()
        
        return RiskLimits(
            max_concurrent_trades=trading_config.get('max_concurrent_trades', 5),
            max_daily_trades=trading_config.get('max_daily_trades', 20),
            max_daily_loss=trading_config.get('max_daily_loss', 100.0),
            max_total_loss=trading_config.get('max_total_loss', 500.0),
            position_size_percentage=trading_config.get('position_size_percentage', 0.1)
        )
    except Exception as e:
        logger.error(f"Error getting risk limits: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/v1/risk/limits")
async def update_risk_limits(limits: RiskLimits):
    """Update risk limits"""
    try:
        # Update configuration
        async with httpx.AsyncClient() as client:
            updates = [
                {"path": "trading.max_concurrent_trades", "value": limits.max_concurrent_trades},
                {"path": "trading.max_daily_trades", "value": limits.max_daily_trades},
                {"path": "trading.max_daily_loss", "value": limits.max_daily_loss},
                {"path": "trading.max_total_loss", "value": limits.max_total_loss},
                {"path": "trading.position_size_percentage", "value": limits.position_size_percentage}
            ]
            
            for update in updates:
                response = await client.put(f"{config_service_url}/api/v1/config/update", json=update)
                response.raise_for_status()
        
        return {"message": "Risk limits updated successfully"}
    except Exception as e:
        logger.error(f"Error updating risk limits: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/risk/exposure")
async def get_risk_exposure():
    """Get current risk exposure"""
    try:
        total_exposure = 0
        for trade in active_trades.values():
            total_exposure += abs(trade['position_size'])
        
        return {
            "total_exposure": total_exposure,
            "active_trades": len(active_trades),
            "total_balance": sum(balance['total'] for balance in orchestrator.balances.values()),
            "available_balance": sum(balance['available'] for balance in orchestrator.balances.values()),
            "exposure_percentage": (total_exposure / sum(balance['total'] for balance in orchestrator.balances.values())) * 100 if sum(balance['total'] for balance in orchestrator.balances.values()) > 0 else 0
        }
    except Exception as e:
        logger.error(f"Error getting risk exposure: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/risk/check")
async def check_risk():
    """Perform risk check"""
    try:
        # Get current exposure
        exposure = await get_risk_exposure()
        
        # Get risk limits
        limits = await get_risk_limits()
        
        # Check risk violations
        violations = []
        
        if exposure['active_trades'] > limits.max_concurrent_trades:
            violations.append(f"Too many active trades: {exposure['active_trades']} > {limits.max_concurrent_trades}")
        
        if exposure['exposure_percentage'] > (limits.position_size_percentage * 100):
            violations.append(f"Exposure too high: {exposure['exposure_percentage']:.2f}%")
        
        return {
            "risk_check_passed": len(violations) == 0,
            "violations": violations,
            "exposure": exposure,
            "limits": limits
        }
    except Exception as e:
        logger.error(f"Error performing risk check: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Pair Selection Endpoints
@app.get("/api/v1/pairs/selected")
async def get_selected_pairs():
    """Get selected pairs for all exchanges"""
    return {
        "pair_selections": orchestrator.pair_selections,
        "total_exchanges": len(orchestrator.pair_selections)
    }

@app.post("/api/v1/pairs/select")
async def select_pairs(exchange: str, pairs: List[str]):
    """Select pairs for an exchange"""
    try:
        orchestrator.pair_selections[exchange] = pairs
        
        # Save to database
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{database_service_url}/api/v1/pairs/{exchange}", json={"pairs": pairs})
            response.raise_for_status()
        
        return {"message": f"Selected {len(pairs)} pairs for {exchange}"}
    except Exception as e:
        logger.error(f"Error selecting pairs for {exchange}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/pairs/candidates")
async def get_pair_candidates(exchange: str):
    """Get candidate pairs for an exchange"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{exchange_service_url}/api/v1/market/pairs/{exchange}")
            response.raise_for_status()
            pairs_data = response.json()
        
        return {
            "exchange": exchange,
            "candidates": pairs_data['pairs'],
            "total": pairs_data['total']
        }
    except Exception as e:
        logger.error(f"Error getting pair candidates for {exchange}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Startup and shutdown events
@app.on_event("startup")
async def startup_event():
    """Initialize orchestrator on startup"""
    await orchestrator.initialize()

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    if orchestrator.running:
        await orchestrator.stop_trading()
    logger.info("Orchestrator service shutdown complete")

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8005,
        reload=True,
        log_level="info"
    ) 