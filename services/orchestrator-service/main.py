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
                
                # Check if simulation mode
                mode_response = await client.get(f"{config_service_url}/api/v1/config/mode")
                mode_response.raise_for_status()
                mode_data = mode_response.json()
                
                if mode_data['is_simulation']:
                    # In simulation mode, get balance from database
                    for exchange_name in exchanges:
                        try:
                            balance_response = await client.get(f"{database_service_url}/api/v1/balances/{exchange_name}")
                            if balance_response.status_code == 200:
                                balance = balance_response.json()
                                self.balances[exchange_name] = {
                                    'total': float(balance['balance']),
                                    'available': float(balance['available_balance']),
                                    'total_pnl': float(balance['total_pnl']),
                                    'daily_pnl': float(balance['daily_pnl'])
                                }
                                logger.info(f"Loaded balance for {exchange_name}: {balance['available_balance']}")
                            else:
                                # Initialize with default values
                                self.balances[exchange_name] = {
                                    'total': 10000.0,  # Default simulation balance
                                    'available': 10000.0,
                                    'total_pnl': 0.0,
                                    'daily_pnl': 0.0
                                }
                                logger.info(f"Using default balance for {exchange_name}: 10000.0")
                        except Exception as balance_error:
                            logger.error(f"Error getting balance for {exchange_name}: {balance_error}")
                            # Initialize with default values
                            self.balances[exchange_name] = {
                                'total': 10000.0,
                                'available': 10000.0,
                                'total_pnl': 0.0,
                                'daily_pnl': 0.0
                            }
                else:
                    # In live mode, get balance from exchange
                    for exchange_name in exchanges:
                        try:
                            balance_response = await client.get(f"{exchange_service_url}/api/v1/account/balance/{exchange_name}")
                            if balance_response.status_code == 200:
                                balance = balance_response.json()
                                self.balances[exchange_name] = {
                                    'total': balance['total'].get('USDC', 0),
                                    'available': balance['free'].get('USDC', 0),
                                    'total_pnl': 0.0,  # Will be calculated from trades
                                    'daily_pnl': 0.0   # Will be calculated from trades
                                }
                            else:
                                logger.warning(f"Could not get balance for {exchange_name}")
                        except Exception as balance_error:
                            logger.error(f"Error getting balance for {exchange_name}: {balance_error}")
                        
            logger.info(f"Initialized balances for {len(exchanges)} exchanges: {list(self.balances.keys())}")
            
        except Exception as e:
            logger.error(f"Error initializing balances: {e}")
            # Set default balances on error
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(f"{config_service_url}/api/v1/config/exchanges/list")
                    response.raise_for_status()
                    exchanges = response.json()['exchanges']
                    
                    for exchange_name in exchanges:
                        self.balances[exchange_name] = {
                            'total': 10000.0,
                            'available': 10000.0,
                            'total_pnl': 0.0,
                            'daily_pnl': 0.0
                        }
                    logger.info(f"Set default balances for {len(exchanges)} exchanges due to initialization error")
            except Exception as fallback_error:
                logger.error(f"Failed to set default balances: {fallback_error}")
            
    async def _initialize_pair_selections(self) -> None:
        """Initialize pair selections for all exchanges"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{config_service_url}/api/v1/config/exchanges/list")
                response.raise_for_status()
                exchanges = response.json()['exchanges']

                for exchange_name in exchanges:
                    # Get exchange configuration to check max_pairs limit
                    exchange_config_response = await client.get(f"{config_service_url}/api/v1/config/exchanges/{exchange_name}")
                    if exchange_config_response.status_code == 200:
                        exchange_config = exchange_config_response.json()
                        max_pairs = exchange_config.get('max_pairs', 10)
                    else:
                        max_pairs = 10  # Default fallback
                    
                    # Get latest pair selection from database
                    pairs_response = await client.get(f"{database_service_url}/api/v1/pairs/{exchange_name}")
                    if pairs_response.status_code == 200:
                        pairs_data = pairs_response.json()
                        all_pairs = pairs_data.get('pairs', [])
                        
                        # Enforce max_pairs limit
                        if len(all_pairs) > max_pairs:
                            logger.warning(f"Exchange {exchange_name} has {len(all_pairs)} pairs, limiting to {max_pairs} as per configuration")
                            self.pair_selections[exchange_name] = all_pairs[:max_pairs]
                        else:
                            self.pair_selections[exchange_name] = all_pairs
                        
                        logger.info(f"Exchange {exchange_name}: {len(self.pair_selections[exchange_name])}/{max_pairs} pairs selected")
                    else:
                        self.pair_selections[exchange_name] = []
                        logger.warning(f"No pairs found for exchange {exchange_name}")

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
            logger.info(f"Exit cycle processing {len(open_trades)} open trades")
            
            processed_count = 0
            skipped_count = 0
            for trade in open_trades:
                processed_count += 1
                if trade['entry_price'] <= 0:
                    skipped_count += 1
                await self._check_trade_exit(trade)
                
            logger.info(f"Exit cycle completed: {processed_count} trades processed, {skipped_count} skipped")
                
        except Exception as e:
            logger.error(f"Error in exit cycle: {e}")
            
    async def _check_trade_exit(self, trade: Dict[str, Any]) -> None:
        """Check if a trade should be exited"""
        try:
            trade_id = trade.get('trade_id')
            if not trade_id or not isinstance(trade_id, str):
                logger.warning(f"[ExitCheck] Skipping: invalid or missing trade_id: {trade_id}")
                return

            # Defensive: Validate and convert entry_price, position_size
            try:
                entry_price = trade.get('entry_price')
                position_size = trade.get('position_size')
                if entry_price is None or position_size is None:
                    raise ValueError("entry_price or position_size is None")
                entry_price = float(entry_price)
                position_size = float(position_size)
                if entry_price == 0.0 or position_size == 0.0:
                    raise ValueError("entry_price or position_size is zero")
            except Exception as e:
                logger.warning(f"[Trade {trade_id}] [ExitCheck] Skipping: invalid entry/size: {e}")
                return

            # Get current price
            try:
                exchange = trade.get('exchange')
                pair = trade.get('pair')
                exchange_pair = pair.replace('/', '') if pair else ''
                async with httpx.AsyncClient() as client:
                    response = await client.get(f"{exchange_service_url}/api/v1/market/ticker/{exchange}/{exchange_pair}")
                    response.raise_for_status()
                    ticker = response.json()
                    current_price = float(ticker.get('last', 0.0))
                if current_price == 0.0:
                    raise ValueError("current_price is zero")
            except Exception as e:
                logger.warning(f"[Trade {trade_id}] [ExitCheck] Skipping: invalid current_price: {e}")
                return

            # Defensive: Validate and convert trail_stop_trigger
            raw_stop = trade.get('trail_stop_trigger')
            try:
                if raw_stop is None:
                    raise ValueError
                current_stop_loss = float(raw_stop)
            except Exception:
                current_stop_loss = -2.0  # Default -2%
            logger.info(f"[Trade {trade_id}] [ExitCheck] Using stop loss: {current_stop_loss}")

            # Calculate PnL and update trade data
            try:
                if position_size > 0:  # Long position
                    pnl_percentage = ((current_price - entry_price) / entry_price) * 100
                    unrealized_pnl = (current_price - entry_price) * position_size
                else:  # Short position
                    pnl_percentage = ((entry_price - current_price) / entry_price) * 100
                    unrealized_pnl = (entry_price - current_price) * position_size
            except Exception as e:
                logger.warning(f"[Trade {trade_id}] [ExitCheck] Skipping: error calculating PnL: {e}")
                return

            # Update highest price if current price is higher
            highest_price = trade.get('highest_price')
            try:
                if highest_price is None:
                    highest_price = entry_price
                highest_price = float(highest_price)
                if current_price > highest_price:
                    highest_price = current_price
            except Exception:
                highest_price = current_price

            # Update trade with current data
            await self._update_trade_data(trade_id, {
                'unrealized_pnl': unrealized_pnl,
                'highest_price': highest_price,
                'current_price': current_price
            })

            # Check exit conditions with profit protection
            exit_reason = None
            should_exit = False

            # Profit protection: Move stop loss to breakeven when profit reaches 1%
            profit_protection_status = trade.get('profit_protection')
            logger.info(f"[Trade {trade_id}] [ProfitProtection] Checking conditions - PnL: {pnl_percentage:.2f}%, Current Stop: {current_stop_loss:.2f}%, Target: 1.0%, Status: {profit_protection_status}")
            
            # Only trigger profit protection if it hasn't been activated yet
            if pnl_percentage >= 1.0 and not profit_protection_status:
                # Move to guarantee 0.5% profit instead of just breakeven
                new_stop_loss = 0.5  # Guarantee 0.5% profit
                logger.info(f"[Trade {trade_id}] [ProfitProtection] ✅ TRIGGERED: PnL {pnl_percentage:.2f}% >= 1.0% AND no profit protection active")
                logger.info(f"[Trade {trade_id}] [ProfitProtection] Moving stop loss: {current_stop_loss:.2f}% → {new_stop_loss:.2f}% (guaranteeing 0.5% profit)")
                await self._update_trade_data(trade_id, {
                    'trail_stop_trigger': new_stop_loss,
                    'profit_protection': 'profit_guaranteed',
                    'profit_protection_trigger': pnl_percentage
                })
                current_stop_loss = new_stop_loss
            else:
                # Fix the log message to show the correct logic
                if pnl_percentage < 1.0:
                    reason = f"PnL {pnl_percentage:.2f}% < 1.0%"
                    logger.info(f"[Trade {trade_id}] [ProfitProtection] ❌ NOT TRIGGERED: {reason}")
                elif profit_protection_status:
                    reason = f"profit protection already active: {profit_protection_status}"
                    logger.info(f"[Trade {trade_id}] [ProfitProtection] ✅ ACTIVE: {reason}")
                else:
                    reason = "unknown condition"
                    logger.info(f"[Trade {trade_id}] [ProfitProtection] ❌ NOT TRIGGERED: {reason}")

            # Trailing stop: Move stop loss up as price increases (when PnL >= 2.0%)
            logger.info(f"[Trade {trade_id}] [TrailingStop] Checking conditions - PnL: {pnl_percentage:.2f}%, Current Stop: {current_stop_loss:.2f}%, Target: 2.0%")
            if pnl_percentage >= 2.0:
                trailing_stop = pnl_percentage - 1.0  # Keep 1% profit locked in
                logger.info(f"[Trade {trade_id}] [TrailingStop] Calculating trailing stop: {pnl_percentage:.2f}% - 1.0% = {trailing_stop:.2f}%")
                logger.info(f"[Trade {trade_id}] [TrailingStop] Comparing: new stop {trailing_stop:.2f}% > current stop {current_stop_loss:.2f}%")
                if trailing_stop > current_stop_loss:
                    logger.info(f"[Trade {trade_id}] [TrailingStop] ✅ UPDATED: PnL {pnl_percentage:.2f}% >= 2.0% AND new stop {trailing_stop:.2f}% > current {current_stop_loss:.2f}%")
                    logger.info(f"[Trade {trade_id}] [TrailingStop] Moving stop loss: {current_stop_loss:.2f}% → {trailing_stop:.2f}% (locking {trailing_stop:.2f}% profit)")
                    await self._update_trade_data(trade_id, {
                        'trail_stop_trigger': trailing_stop,
                        'trail_stop': 'active',
                        'profit_protection': 'trailing'
                    })
                    current_stop_loss = trailing_stop
                else:
                    logger.info(f"[Trade {trade_id}] [TrailingStop] ❌ NOT UPDATED: new stop {trailing_stop:.2f}% <= current stop {current_stop_loss:.2f}% (already higher)")
            else:
                logger.info(f"[Trade {trade_id}] [TrailingStop] ❌ NOT TRIGGERED: PnL {pnl_percentage:.2f}% < 2.0% (need 2.0%+ for trailing)")

            # Stop loss check
            logger.info(f"[Trade {trade_id}] [StopLoss] Checking exit - PnL: {pnl_percentage:.2f}%, Stop Level: {current_stop_loss:.2f}%")
            if pnl_percentage <= current_stop_loss:
                should_exit = True
                exit_reason = f"stop_loss_{current_stop_loss:.1f}%"
                logger.info(f"[Trade {trade_id}] [StopLoss] ✅ EXIT TRIGGERED: PnL {pnl_percentage:.2f}% <= stop loss {current_stop_loss:.2f}%")
            else:
                logger.info(f"[Trade {trade_id}] [StopLoss] ❌ NO EXIT: PnL {pnl_percentage:.2f}% > stop loss {current_stop_loss:.2f}%")

            # Take profit check (5% take profit)
            logger.info(f"[Trade {trade_id}] [TakeProfit] Checking exit - PnL: {pnl_percentage:.2f}%, Target: 5.0%")
            if pnl_percentage >= 5.0:
                should_exit = True
                exit_reason = "take_profit_5%"
                logger.info(f"[Trade {trade_id}] [TakeProfit] ✅ EXIT TRIGGERED: PnL {pnl_percentage:.2f}% >= 5.0%")
            else:
                logger.info(f"[Trade {trade_id}] [TakeProfit] ❌ NO EXIT: PnL {pnl_percentage:.2f}% < 5.0%")

            # Log current status for monitoring
            if pnl_percentage > 0:
                logger.info(f"[Trade {trade_id}] [Status] Current: PnL {pnl_percentage:.2f}%, Stop Loss: {current_stop_loss:.2f}%, Highest: {highest_price:.6f}, Current: {current_price:.6f}")

            if should_exit:
                logger.info(f"[Trade {trade_id}] [Exit] 🚨 EXECUTING EXIT: {exit_reason}")
                await self._execute_trade_exit(trade, current_price, exit_reason or "unknown")
            else:
                logger.info(f"[Trade {trade_id}] [Exit] ❌ NO EXIT: Continuing to monitor")

        except Exception as e:
            logger.error(f"[Trade {trade.get('trade_id', 'unknown')}] Error in _check_trade_exit: {e}")
            
    async def _execute_trade_exit(self, trade: Dict[str, Any], exit_price: float, reason: str) -> None:
        """Execute trade exit"""
        try:
            trade_id = trade['trade_id']
            exchange = trade['exchange']
            pair = trade['pair']
            
            logger.info(f"Executing trade exit: {trade_id} at {exit_price} ({reason})")
            
            # Calculate realized PnL
            entry_price = trade['entry_price']
            position_size = trade['position_size']
            realized_pnl = (exit_price - entry_price) * position_size
            
            # Update trade in database
            update_data = {
                'exit_price': exit_price,
                'exit_time': datetime.utcnow().isoformat(),
                'status': 'CLOSED',
                'exit_reason': reason,
                'realized_pnl': realized_pnl
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.put(f"{database_service_url}/api/v1/trades/{trade_id}", json=update_data)
                response.raise_for_status()
            
            # Calculate original position value in USDC (what was deducted on entry)
            original_position_value = entry_price * position_size
            
            # Restore available balance and add realized PnL
            current_available = self.balances[exchange]['available']
            new_available_balance = current_available + original_position_value + realized_pnl
            
            # Update balance in database
            balance_update_data = {
                'exchange': exchange,
                'balance': self.balances[exchange]['total'] + realized_pnl,
                'available_balance': new_available_balance,
                'total_pnl': self.balances[exchange]['total_pnl'] + realized_pnl,
                'daily_pnl': self.balances[exchange]['daily_pnl'] + realized_pnl,
                'timestamp': datetime.utcnow().isoformat()
            }
            
            balance_response = await client.put(f"{database_service_url}/api/v1/balances/{exchange}", json=balance_update_data)
            if balance_response.status_code == 200:
                # Update local balance cache
                self.balances[exchange]['available'] = new_available_balance
                self.balances[exchange]['total'] += realized_pnl
                self.balances[exchange]['total_pnl'] += realized_pnl
                self.balances[exchange]['daily_pnl'] += realized_pnl
                logger.info(f"Updated balance for {exchange}: available={current_available} -> {new_available_balance}, PnL={realized_pnl}")
            else:
                logger.error(f"Failed to update balance for {exchange}: {balance_response.status_code}")
            
            # Remove from active trades
            if trade_id in self.active_trades:
                del self.active_trades[trade_id]
                
            logger.info(f"Trade {trade_id} closed successfully with PnL: {realized_pnl}")
            
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
            # Check trade limits
            if not await self._check_trade_limits():
                logger.info("Trade limits reached, skipping entry cycle")
                return
            # Check each exchange and pair
            for exchange_name, pairs in self.pair_selections.items():
                # Log trade count for this exchange before processing
                open_trades_count = 0
                max_trades = 3
                try:
                    async with httpx.AsyncClient() as db_client:
                        response = await db_client.get(f"{database_service_url}/api/v1/trades/open")
                        response.raise_for_status()
                        open_trades = response.json()['trades']
                        open_trades_count = sum(1 for t in open_trades if t['exchange'] == exchange_name)
                    async with httpx.AsyncClient() as client:
                        response = await client.get(f"{config_service_url}/api/v1/config/trading")
                        response.raise_for_status()
                        trading_config = response.json()
                        max_trades = trading_config.get('max_trades_per_exchange', 3)
                except Exception as e:
                    logger.warning(f"[EntryCycle] Could not fetch open trades or config for {exchange_name}: {e}")
                logger.info(f"[EntryCycle] {exchange_name}: open_trades={open_trades_count}, max_trades={max_trades}")
                if open_trades_count >= max_trades:
                    logger.info(f"[EntryCycle] Trade limit reached for {exchange_name}: {open_trades_count}/{max_trades}. Skipping entry for this exchange.")
                    continue
                else:
                    logger.info(f"[EntryCycle] Trade limit NOT reached for {exchange_name}: {open_trades_count}/{max_trades}. Processing entry cycle for this exchange.")
                for pair in pairs:
                    await self._check_pair_entry(exchange_name, pair)
        except Exception as e:
            logger.error(f"Error in entry cycle: {e}")
            
    async def _check_available_balance(self) -> bool:
        """Check if there's sufficient balance for new trades"""
        try:
            total_available = 0
            logger.info(f"Checking available balance. Current balances: {self.balances}")
            
            for exchange_name, balance in self.balances.items():
                available = balance.get('available', 0)
                total_available += available
                logger.info(f"Exchange {exchange_name}: available={available}")
                
            logger.info(f"Total available balance: {total_available}")
            
            # Check if we have at least $100 available
            has_sufficient = total_available >= 100.0
            logger.info(f"Sufficient balance for new trades: {has_sufficient}")
            return has_sufficient
            
        except Exception as e:
            logger.error(f"Error checking available balance: {e}")
            return False
            
    async def _check_trade_limits(self) -> bool:
        """Check if trade limits allow new trades"""
        try:
            # Get trading configuration
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{config_service_url}/api/v1/config/trading")
                response.raise_for_status()
                trading_config = response.json()
            max_concurrent_trades = trading_config.get('max_concurrent_trades', 10)
            max_trades_per_exchange = trading_config.get('max_trades_per_exchange', 3)
            # Get current open trades - use a new client to avoid closed client issues
            async with httpx.AsyncClient() as db_client:
                response = await db_client.get(f"{database_service_url}/api/v1/trades/open")
                response.raise_for_status()
                open_trades = response.json()['trades']
            total_open_trades = len(open_trades)
            logger.info(f"Current open trades: {total_open_trades}, max allowed: {max_concurrent_trades}")
            # Check total concurrent trades limit
            if total_open_trades >= max_concurrent_trades:
                logger.info(f"Maximum concurrent trades reached: {total_open_trades}/{max_concurrent_trades}")
                return False
            # Check per-exchange limits
            trades_per_exchange = {}
            for trade in open_trades:
                exchange = trade['exchange']
                trades_per_exchange[exchange] = trades_per_exchange.get(exchange, 0) + 1
            # Log all exchanges' trade counts
            for exchange in self.balances.keys():
                count = trades_per_exchange.get(exchange, 0)
                logger.info(f"[EntryCycle] {exchange}: open_trades={count}, max_trades={max_trades_per_exchange}")
                if count >= max_trades_per_exchange:
                    logger.info(f"[EntryCycle] Trade limit reached for {exchange}: {count}/{max_trades_per_exchange}. No new entries will be processed for this exchange.")
                else:
                    logger.info(f"[EntryCycle] Trade limit NOT reached for {exchange}: {count}/{max_trades_per_exchange}. New entries can be processed for this exchange.")
            # Still enforce the original logic for overall entry cycle
            for exchange, count in trades_per_exchange.items():
                if count >= max_trades_per_exchange:
                    logger.info(f"Maximum trades per exchange reached for {exchange}: {count}/{max_trades_per_exchange}")
                    return False
            logger.info(f"Trade limits check passed: {total_open_trades} open trades")
            return True
        except Exception as e:
            logger.error(f"Error checking trade limits: {e}")
            return False
            
    async def _check_pair_entry(self, exchange_name: str, pair: str) -> None:
        """Check if a pair should be entered - each strategy is independent"""
        try:
            # Handle different symbol formats for different exchanges
            # All exchanges use format without slashes for strategy service
            strategy_pair = pair.replace('/', '')
            
            # Get individual strategy signals instead of consensus
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{strategy_service_url}/api/v1/signals/{exchange_name}/{strategy_pair}")
                response.raise_for_status()
                signals_data = response.json()
            
            # Check each strategy independently
            strategies = signals_data.get('strategies', {})
            for strategy_name, signal_data in strategies.items():
                signal = signal_data.get('signal')
                confidence = signal_data.get('confidence', 0)
                strength = signal_data.get('strength', 0)
                
                # If any strategy generates a buy signal, execute the trade
                if signal == 'buy' and confidence > 0.5:  # Minimum confidence threshold
                    logger.info(f"Strategy {strategy_name} generated BUY signal for {pair} on {exchange_name} (confidence: {confidence:.2f})")
                    await self._execute_trade_entry(exchange_name, pair, {
                        'strategy': strategy_name,
                        'signal': signal,
                        'confidence': confidence,
                        'strength': strength
                    })
                    # Only execute one trade per pair per cycle to avoid over-trading
                    break
                
        except Exception as e:
            logger.error(f"Error checking pair entry for {pair} on {exchange_name}: {e}")
            
    async def _execute_trade_entry(self, exchange_name: str, pair: str, signal: Dict[str, Any]) -> None:
        """Execute trade entry"""
        try:
            # Calculate position size in USDC
            balance = self.balances[exchange_name]['available']
            position_value_usdc = balance * 0.1  # Use 10% of available balance
            
            # Get current market price for entry
            entry_price = await self._get_current_price(exchange_name, pair)
            
            # Calculate position size in cryptocurrency units
            position_size_units = position_value_usdc / entry_price if entry_price > 0 else 0
            
            # Create trade record
            trade_id = str(uuid.uuid4())
            strategy_name = signal.get('strategy', 'unknown')
            trade_data = {
                'trade_id': trade_id,
                'pair': pair,
                'exchange': exchange_name,
                'entry_price': entry_price,
                'status': 'OPEN',
                'position_size': position_size_units,  # Store cryptocurrency units, not USDC value
                'strategy': strategy_name,
                'entry_time': datetime.utcnow().isoformat(),
                'entry_reason': f"{strategy_name} strategy signal: {signal['signal']} (confidence: {signal['confidence']:.2f}, strength: {signal['strength']:.2f})"
            }
            
            # Save trade to database
            async with httpx.AsyncClient() as client:
                response = await client.post(f"{database_service_url}/api/v1/trades", json=trade_data)
                response.raise_for_status()
            
            # Update available balance in database after successful trade creation
            new_available_balance = balance - position_value_usdc
            balance_update_data = {
                'exchange': exchange_name,
                'balance': self.balances[exchange_name]['total'],
                'available_balance': new_available_balance,
                'total_pnl': self.balances[exchange_name]['total_pnl'],
                'daily_pnl': self.balances[exchange_name]['daily_pnl'],
                'timestamp': datetime.utcnow().isoformat()
            }
            
            # Update balance in database
            try:
                balance_response = await client.put(f"{database_service_url}/api/v1/balances/{exchange_name}", json=balance_update_data)
                if balance_response.status_code == 200:
                    # Update local balance cache
                    self.balances[exchange_name]['available'] = new_available_balance
                    logger.info(f"Updated available balance for {exchange_name}: {balance} -> {new_available_balance} (deducted {position_value_usdc})")
                else:
                    logger.error(f"Failed to update balance for {exchange_name}: {balance_response.status_code}")
            except Exception as balance_error:
                logger.error(f"Error updating balance for {exchange_name}: {balance_error}")
            
            # Add to active trades
            self.active_trades[trade_id] = trade_data
            
            logger.info(f"Created trade {trade_id} for {pair} on {exchange_name}")
            
        except Exception as e:
            logger.error(f"Error executing trade entry for {pair} on {exchange_name}: {e}")
            
    async def _update_trade_data(self, trade_id: str, update_data: Dict[str, Any]) -> None:
        """Update trade data in database"""
        try:
            logger.info(f"Updating trade {trade_id} with data: {update_data}")
            async with httpx.AsyncClient() as client:
                response = await client.put(f"{database_service_url}/api/v1/trades/{trade_id}", json=update_data)
                response.raise_for_status()
                logger.info(f"Successfully updated trade {trade_id}")
        except httpx.HTTPStatusError as e:
            logger.error(f"Error updating trade data for {trade_id}: {e}")
            logger.error(f"Response content: {e.response.text}")
        except Exception as e:
            logger.error(f"Error updating trade data for {trade_id}: {e}")
            
    async def _get_current_price(self, exchange_name: str, pair: str) -> float:
        """Get current market price for a pair"""
        try:
            # Convert pair format for exchange service (remove slashes)
            exchange_symbol = pair.replace('/', '')
            
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{exchange_service_url}/api/v1/market/ticker/{exchange_name}/{exchange_symbol}")
                response.raise_for_status()
                ticker_data = response.json()
                return float(ticker_data.get('last', 0.0))
        except Exception as e:
            logger.error(f"Error getting current price for {pair} on {exchange_name}: {e}")
            return 0.0
            
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
            # Check if we're in simulation mode
            async with httpx.AsyncClient() as client:
                mode_response = await client.get(f"{config_service_url}/api/v1/config/mode")
                mode_response.raise_for_status()
                mode_data = mode_response.json()
                
                if mode_data['is_simulation']:
                    # In simulation mode, update from database instead of exchange
                    for exchange_name in self.balances.keys():
                        try:
                            balance_response = await client.get(f"{database_service_url}/api/v1/balances/{exchange_name}")
                            if balance_response.status_code == 200:
                                balance = balance_response.json()
                                self.balances[exchange_name] = {
                                    'total': float(balance['balance']),
                                    'available': float(balance['available_balance']),
                                    'total_pnl': float(balance['total_pnl']),
                                    'daily_pnl': float(balance['daily_pnl'])
                                }
                                logger.debug(f"Updated balance for {exchange_name} from database: {balance['available_balance']}")
                        except Exception as balance_error:
                            logger.error(f"Error updating balance for {exchange_name} from database: {balance_error}")
                else:
                    # In live mode, update from exchange
                    for exchange_name in self.balances.keys():
                        try:
                            response = await client.get(f"{exchange_service_url}/api/v1/account/balance/{exchange_name}")
                            if response.status_code == 200:
                                balance = response.json()
                                self.balances[exchange_name] = {
                                    'total': balance['total'].get('USDC', 0),
                                    'available': balance['free'].get('USDC', 0),
                                    'total_pnl': 0.0,  # Will be calculated from trades
                                    'daily_pnl': 0.0   # Will be calculated from trades
                                }
                            else:
                                logger.warning(f"Could not get balance for {exchange_name}")
                        except Exception as balance_error:
                            logger.error(f"Error getting balance for {exchange_name}: {balance_error}")
                        
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
    if orchestrator.start_time:
        uptime = datetime.utcnow() - orchestrator.start_time
        
    return TradingStatus(
        status="running" if orchestrator.running else "stopped",
        cycle_count=orchestrator.cycle_count,
        active_trades=len(orchestrator.active_trades),
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
    """Initialize orchestrator and start trading loop on startup"""
    await orchestrator.initialize()
    await orchestrator.start_trading()

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