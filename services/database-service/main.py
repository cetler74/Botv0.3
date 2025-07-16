"""
Database Service for the Multi-Exchange Trading Bot
Centralized database operations and data persistence
"""

import asyncio
import logging
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
import json
from decimal import Decimal
import psycopg2
from psycopg2.extras import RealDictCursor, Json
from psycopg2.pool import SimpleConnectionPool
from contextlib import asynccontextmanager
import uuid
import httpx
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import os

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="Database Service",
    description="Centralized database operations for the trading bot",
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
class Trade(BaseModel):
    trade_id: str
    pair: str
    exchange: str
    entry_price: float
    exit_price: Optional[float] = None
    status: str  # 'OPEN', 'CLOSED', 'CANCELLED'
    position_size: float
    strategy: str
    entry_time: datetime
    exit_time: Optional[datetime] = None
    pnl: Optional[float] = None
    entry_reason: Optional[str] = None
    exit_reason: Optional[str] = None
    fees: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    realized_pnl: Optional[float] = None
    highest_price: Optional[float] = None
    profit_protection: Optional[str] = None
    profit_protection_trigger: Optional[float] = None
    trail_stop: Optional[str] = None
    trail_stop_trigger: Optional[float] = None

class Balance(BaseModel):
    exchange: str
    balance: float
    available_balance: float
    total_pnl: float
    daily_pnl: float
    timestamp: datetime

class Alert(BaseModel):
    alert_id: str
    level: str  # 'INFO', 'WARNING', 'ERROR'
    category: str
    message: str
    exchange: Optional[str] = None
    details: Dict[str, Any]
    created_at: datetime  # Changed from timestamp to created_at to match schema
    resolved: bool = False

class MarketDataCache(BaseModel):
    exchange: str
    pair: str
    data_type: str
    data: Dict[str, Any]
    timestamp: datetime
    expires_at: datetime

class PortfolioSummary(BaseModel):
    total_balance: float
    total_pnl: float
    daily_pnl: float
    active_trades: int
    total_trades: int
    win_rate: float
    exchanges: Dict[str, Dict[str, Any]]

class HealthResponse(BaseModel):
    status: str
    timestamp: datetime
    version: str
    database_connected: bool
    pool_size: int
    active_connections: int

# Global database manager
class DatabaseManager:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.pool = None
        self._initialize_connection_pool()
        
    def _initialize_connection_pool(self) -> None:
        """Initialize database connection pool"""
        try:
            db_config = self.config.get('database', {})
            self.pool = SimpleConnectionPool(
                minconn=1,
                maxconn=db_config.get('pool_size', 10),
                host=db_config.get('host', 'localhost'),
                port=db_config.get('port', 5432),
                database=db_config.get('name', 'trading_bot_futures'),  # Use the test database
                user=db_config.get('user', 'carloslarramba'),
                password=db_config.get('password', ''),
                cursor_factory=RealDictCursor,
                options='-c search_path=trading'  # Set search path to trading schema
            )
            logger.info("Database connection pool initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize database connection pool: {e}")
            raise
            
    @asynccontextmanager
    async def get_connection(self):
        """Get database connection from pool"""
        conn = None
        try:
            if self.pool is None:
                raise Exception("Database connection pool is not initialized")
            conn = self.pool.getconn()
            yield conn
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"Database connection error: {e}")
            raise
        finally:
            if conn and self.pool:
                self.pool.putconn(conn)
                
    async def execute_query(self, query: str, params: Optional[Tuple] = None) -> List[Dict[str, Any]]:
        """Execute a query and return results"""
        async with self.get_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, params)
                if query.strip().upper().startswith('SELECT'):
                    return cursor.fetchall()
                conn.commit()
                return []
                
    async def execute_single_query(self, query: str, params: Optional[Tuple] = None) -> Optional[Dict[str, Any]]:
        """Execute a query and return single result"""
        results = await self.execute_query(query, params)
        return results[0] if results else None

    async def get_pool_stats(self) -> Dict[str, Any]:
        """Get connection pool statistics"""
        if self.pool:
            return {
                "pool_size": self.pool.maxconn,
                "active_connections": self.pool.maxconn - self.pool.minconn,
                "available_connections": self.pool.minconn
            }
        return {"pool_size": 0, "active_connections": 0, "available_connections": 0}

# Global variables
db_manager: Optional[DatabaseManager] = None
config_service_url: str = "http://config-service:8001"

async def get_config_from_service() -> Dict[str, Any]:
    """Get configuration from config service"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{config_service_url}/api/v1/config/database")
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.error(f"Failed to get config from service: {e}")
        # Fallback to environment variables
        return {
            "host": os.getenv("DB_HOST", "localhost"),
            "port": int(os.getenv("DB_PORT", "5432")),
            "name": os.getenv("DB_NAME", "trading_bot_futures"),  # Use test database
            "user": os.getenv("DB_USER", "carloslarramba"),
            "password": os.getenv("DB_PASSWORD", ""),
            "pool_size": int(os.getenv("DB_POOL_SIZE", "10"))
        }

async def initialize_database():
    """Initialize database connection"""
    global db_manager
    try:
        config = await get_config_from_service()
        db_manager = DatabaseManager({"database": config})
        logger.info("Database service initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize database service: {e}")
        raise

# API Endpoints
@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint"""
    pool_stats = await db_manager.get_pool_stats() if db_manager else {}
    return HealthResponse(
        status="healthy" if db_manager else "unhealthy",
        timestamp=datetime.utcnow(),
        version="1.0.0",
        database_connected=bool(db_manager),
        pool_size=pool_stats.get("pool_size", 0),
        active_connections=pool_stats.get("active_connections", 0)
    )

@app.get("/ready")
async def readiness_check():
    """Readiness check endpoint"""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    return {"status": "ready"}

@app.get("/live")
async def liveness_check():
    """Liveness check endpoint"""
    return {"status": "alive"}

# Trade Management Endpoints
@app.post("/api/v1/trades")
async def create_trade(trade: Trade):
    """Create new trade record"""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        query = """
            INSERT INTO trading.trades (
                trade_id, pair, entry_price, status, entry_time,
                exchange, entry_reason, position_size, strategy
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """
        params = (
            trade.trade_id,
            trade.pair,
            trade.entry_price,
            'OPEN',
            trade.entry_time,
            trade.exchange,
            trade.entry_reason,
            trade.position_size,
            trade.strategy
        )
        result = await db_manager.execute_single_query(query, params)
        logger.info(f"Created trade {trade.trade_id} for {trade.pair} on {trade.exchange}")
        return {"trade_id": trade.trade_id, "id": result['id']} if result else {"trade_id": trade.trade_id, "id": None}
    except Exception as e:
        logger.error(f"Failed to create trade: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/v1/trades/{trade_id}")
async def delete_trade(trade_id: str):
    """Delete trade record"""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        query = "DELETE FROM trading.trades WHERE trade_id = %s"
        await db_manager.execute_query(query, (trade_id,))
        logger.info(f"Deleted trade {trade_id}")
        return {"message": "Trade deleted successfully"}
    except Exception as e:
        logger.error(f"Failed to delete trade {trade_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/trades")
async def get_trades(limit: int = 100, offset: int = 0):
    """Get recent trades"""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        query = """
            SELECT * FROM trading.trades 
            ORDER BY entry_time DESC 
            LIMIT %s OFFSET %s
        """
        trades = await db_manager.execute_query(query, (limit, offset))
        return {"trades": trades}
    except Exception as e:
        logger.error(f"Failed to get trades: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/trades/{trade_id}")
async def get_trade(trade_id: str):
    """Get specific trade by ID"""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        query = "SELECT * FROM trading.trades WHERE trade_id = %s"
        trade = await db_manager.execute_single_query(query, (trade_id,))
        if not trade:
            raise HTTPException(status_code=404, detail="Trade not found")
        return trade
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get trade {trade_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/v1/trades/{trade_id}")
async def update_trade(trade_id: str, update_data: Dict[str, Any]):
    """Update trade record"""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        # Build dynamic update query
        set_clauses = []
        params = []
        
        for key, value in update_data.items():
            if key in ['exit_price', 'exit_id', 'exit_time', 'unrealized_pnl', 
                      'realized_pnl', 'highest_price', 'profit_protection', 
                      'profit_protection_trigger', 'trail_stop', 'trail_stop_trigger',
                      'exit_reason', 'fees', 'status']:
                set_clauses.append(f"{key} = %s")
                params.append(value)
                
        if not set_clauses:
            raise HTTPException(status_code=400, detail="No valid fields to update")
            
        query = f"""
            UPDATE trading.trades 
            SET {', '.join(set_clauses)}, updated_at = CURRENT_TIMESTAMP
            WHERE trade_id = %s
        """
        params.append(trade_id)
        
        await db_manager.execute_query(query, tuple(params))
        logger.info(f"Updated trade {trade_id}")
        return {"message": "Trade updated successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update trade {trade_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/trades/open")
async def get_open_trades(exchange: Optional[str] = None):
    """Get open trades"""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        if exchange:
            query = "SELECT * FROM trading.trades WHERE status = 'OPEN' AND exchange = %s ORDER BY entry_time DESC"
            trades = await db_manager.execute_query(query, (exchange,))
        else:
            query = "SELECT * FROM trading.trades WHERE status = 'OPEN' ORDER BY entry_time DESC"
            trades = await db_manager.execute_query(query)
        
        return {"trades": trades}
    except Exception as e:
        logger.error(f"Failed to get open trades: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/trades/exchange/{exchange_name}")
async def get_trades_by_exchange(exchange_name: str, limit: int = 100):
    """Get trades by exchange"""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        query = """
            SELECT * FROM trading.trades 
            WHERE exchange = %s 
            ORDER BY entry_time DESC 
            LIMIT %s
        """
        trades = await db_manager.execute_query(query, (exchange_name, limit))
        return {"trades": trades}
    except Exception as e:
        logger.error(f"Failed to get trades for {exchange_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Balance Management Endpoints
@app.post("/api/v1/balances")
async def create_balance(balance: Balance):
    """Create new balance record"""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        query = """
            INSERT INTO trading.balance (exchange, balance, available_balance, total_pnl, daily_pnl, timestamp)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (exchange) 
            DO UPDATE SET 
                balance = EXCLUDED.balance,
                available_balance = EXCLUDED.available_balance,
                total_pnl = EXCLUDED.total_pnl,
                daily_pnl = EXCLUDED.daily_pnl,
                timestamp = EXCLUDED.timestamp,
                updated_at = CURRENT_TIMESTAMP
        """
        await db_manager.execute_query(query, (
            balance.exchange, balance.balance, balance.available_balance,
            balance.total_pnl, balance.daily_pnl, balance.timestamp
        ))
        logger.info(f"Created/updated balance for {balance.exchange}")
        return {"message": "Balance created/updated successfully"}
    except Exception as e:
        logger.error(f"Failed to create balance: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/balances")
async def get_all_balances():
    """Get balances for all exchanges"""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        query = """
            SELECT DISTINCT ON (exchange) 
                exchange, balance, available_balance, total_pnl, daily_pnl, timestamp
            FROM trading.balance 
            ORDER BY exchange, timestamp DESC
        """
        balances = await db_manager.execute_query(query)
        return {"balances": balances}
    except Exception as e:
        logger.error(f"Failed to get balances: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/balances/{exchange_name}")
async def get_balance(exchange_name: str):
    """Get balance for specific exchange"""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        query = """
            SELECT * FROM trading.balance 
            WHERE exchange = %s 
            ORDER BY timestamp DESC 
            LIMIT 1
        """
        balance = await db_manager.execute_single_query(query, (exchange_name,))
        if not balance:
            raise HTTPException(status_code=404, detail="Balance not found")
        return balance
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get balance for {exchange_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/v1/balances/{exchange_name}")
async def update_balance(exchange_name: str, balance: Balance):
    """Update balance for exchange"""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        query = """
            INSERT INTO trading.balance (exchange, balance, available_balance, total_pnl, daily_pnl, timestamp)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (exchange) 
            DO UPDATE SET 
                balance = EXCLUDED.balance,
                available_balance = EXCLUDED.available_balance,
                total_pnl = EXCLUDED.total_pnl,
                daily_pnl = EXCLUDED.daily_pnl,
                timestamp = EXCLUDED.timestamp,
                updated_at = CURRENT_TIMESTAMP
        """
        await db_manager.execute_query(query, (
            exchange_name, balance.balance, balance.available_balance,
            balance.total_pnl, balance.daily_pnl, balance.timestamp
        ))
        logger.info(f"Updated balance for {exchange_name}")
        return {"message": "Balance updated successfully"}
    except Exception as e:
        logger.error(f"Failed to update balance for {exchange_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/portfolio/summary")
async def get_portfolio_summary():
    """Get portfolio summary"""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        # Get total balance
        balance_query = """
            SELECT 
                SUM(balance) as total_balance,
                SUM(total_pnl) as total_pnl,
                SUM(daily_pnl) as daily_pnl
            FROM (
                SELECT DISTINCT ON (exchange) 
                    balance, total_pnl, daily_pnl
                FROM trading.balance 
                ORDER BY exchange, timestamp DESC
            ) latest_balances
        """
        balance_result = await db_manager.execute_single_query(balance_query)
        if not balance_result:
            balance_result = {"total_balance": 0, "total_pnl": 0, "daily_pnl": 0}
        # Get trade statistics
        trade_query = """
            SELECT 
                COUNT(*) as total_trades,
                COUNT(CASE WHEN status = 'OPEN' THEN 1 END) as active_trades,
                COUNT(CASE WHEN status = 'CLOSED' AND realized_pnl > 0 THEN 1 END) as winning_trades,
                COUNT(CASE WHEN status = 'CLOSED' THEN 1 END) as closed_trades
            FROM trading.trades
        """
        trade_result = await db_manager.execute_single_query(trade_query)
        if not trade_result:
            trade_result = {"total_trades": 0, "active_trades": 0, "winning_trades": 0, "closed_trades": 0}
        # Calculate win rate
        win_rate = 0
        if trade_result['closed_trades'] > 0:
            win_rate = (trade_result['winning_trades'] / trade_result['closed_trades']) * 100
        # Get exchange breakdown
        exchange_query = """
            SELECT DISTINCT ON (exchange) 
                exchange, balance, total_pnl, daily_pnl
            FROM trading.balance 
            ORDER BY exchange, timestamp DESC
        """
        exchanges = await db_manager.execute_query(exchange_query)
        exchange_dict = {ex['exchange']: ex for ex in exchanges} if exchanges else {}
        
        return PortfolioSummary(
            total_balance=balance_result['total_balance'] or 0,
            total_pnl=balance_result['total_pnl'] or 0,
            daily_pnl=balance_result['daily_pnl'] or 0,
            active_trades=trade_result['active_trades'] or 0,
            total_trades=trade_result['total_trades'] or 0,
            win_rate=win_rate,
            exchanges=exchange_dict
        )
    except Exception as e:
        logger.error(f"Failed to get portfolio summary: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Alert Management Endpoints
@app.post("/api/v1/alerts")
async def create_alert(alert: Alert):
    """Create new alert"""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        query = """
            INSERT INTO trading.alerts (alert_id, level, category, message, exchange, details, created_at, resolved)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """
        await db_manager.execute_query(query, (
            alert.alert_id,
            alert.level,
            alert.category,
            alert.message,
            alert.exchange,
            Json(alert.details),
            alert.created_at,  # Changed from timestamp to created_at
            alert.resolved
        ))
        logger.info(f"Created alert {alert.alert_id}")
        return {"alert_id": alert.alert_id}
    except Exception as e:
        logger.error(f"Failed to create alert: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/alerts")
async def get_alerts(level: Optional[str] = None, limit: int = 100):
    """Get alerts"""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        if level:
            query = """
                SELECT * FROM trading.alerts 
                WHERE level = %s 
                ORDER BY created_at DESC 
                LIMIT %s
            """
            alerts = await db_manager.execute_query(query, (level, limit))
        else:
            query = """
                SELECT * FROM trading.alerts 
                ORDER BY created_at DESC 
                LIMIT %s
            """
            alerts = await db_manager.execute_query(query, (limit,))
        
        return {"alerts": alerts}
    except Exception as e:
        logger.error(f"Failed to get alerts: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/alerts/unresolved")
async def get_unresolved_alerts(level: Optional[str] = None):
    """Get unresolved alerts"""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        if level:
            query = """
                SELECT * FROM trading.alerts 
                WHERE resolved = false AND level = %s 
                ORDER BY created_at DESC
            """
            alerts = await db_manager.execute_query(query, (level,))
        else:
            query = """
                SELECT * FROM trading.alerts 
                WHERE resolved = false 
                ORDER BY created_at DESC
            """
            alerts = await db_manager.execute_query(query)
        
        return {"alerts": alerts}
    except Exception as e:
        logger.error(f"Failed to get unresolved alerts: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/v1/alerts/{alert_id}/resolve")
async def resolve_alert(alert_id: str):
    """Resolve alert"""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        query = """
            UPDATE trading.alerts 
            SET resolved = true, resolved_at = CURRENT_TIMESTAMP
            WHERE alert_id = %s
        """
        await db_manager.execute_query(query, (alert_id,))
        logger.info(f"Resolved alert {alert_id}")
        return {"message": "Alert resolved successfully"}
    except Exception as e:
        logger.error(f"Failed to resolve alert {alert_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Strategy Performance Endpoints
@app.get("/api/v1/performance/strategy/{strategy_name}")
async def get_strategy_performance(strategy_name: str, period: str = "30d"):
    """Get performance metrics for a strategy"""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        # Calculate date range based on period
        if period == "7d":
            since_date = datetime.utcnow() - timedelta(days=7)
        elif period == "30d":
            since_date = datetime.utcnow() - timedelta(days=30)
        elif period == "90d":
            since_date = datetime.utcnow() - timedelta(days=90)
        else:
            since_date = datetime.utcnow() - timedelta(days=30)  # Default to 30 days
        
        # Get strategy performance from database
        query = """
            SELECT * FROM trading.strategy_performance 
            WHERE strategy_name = %s AND last_updated >= %s
            ORDER BY last_updated DESC
            LIMIT 1
        """
        result = await db_manager.execute_single_query(query, (strategy_name, since_date))
        
        if result:
            return {
                "strategy_name": result["strategy_name"],
                "total_trades": result["total_trades"],
                "winning_trades": result["winning_trades"],
                "win_rate": float(result["win_rate"]),
                "total_pnl": float(result["total_pnl"]),
                "sharpe_ratio": float(result["sharpe_ratio"]),
                "max_drawdown": float(result["max_drawdown"]),
                "period": period
            }
        else:
            # Return default values if no data found
            return {
                "strategy_name": strategy_name,
                "total_trades": 0,
                "winning_trades": 0,
                "win_rate": 0.0,
                "total_pnl": 0.0,
                "sharpe_ratio": 0.0,
                "max_drawdown": 0.0,
                "period": period
            }
            
    except Exception as e:
        logger.error(f"Failed to get strategy performance for {strategy_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/performance/strategy/{strategy_name}/update")
async def update_strategy_performance(strategy_name: str, performance_data: Dict[str, Any]):
    """Update performance metrics for a strategy"""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        # Extract performance data
        exchange = performance_data.get('exchange', 'unknown')
        pair = performance_data.get('pair', 'unknown')
        total_trades = performance_data.get('total_trades', 0)
        winning_trades = performance_data.get('winning_trades', 0)
        losing_trades = performance_data.get('losing_trades', 0)
        total_pnl = performance_data.get('total_pnl', 0.0)
        win_rate = performance_data.get('win_rate', 0.0)
        avg_win = performance_data.get('avg_win', 0.0)
        avg_loss = performance_data.get('avg_loss', 0.0)
        max_drawdown = performance_data.get('max_drawdown', 0.0)
        sharpe_ratio = performance_data.get('sharpe_ratio', 0.0)
        
        query = """
            INSERT INTO trading.strategy_performance (
                strategy_name, exchange, pair, total_trades, winning_trades, losing_trades,
                total_pnl, win_rate, avg_win, avg_loss, max_drawdown, sharpe_ratio, last_updated
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (strategy_name, exchange, pair) 
            DO UPDATE SET 
                total_trades = EXCLUDED.total_trades,
                winning_trades = EXCLUDED.winning_trades,
                losing_trades = EXCLUDED.losing_trades,
                total_pnl = EXCLUDED.total_pnl,
                win_rate = EXCLUDED.win_rate,
                avg_win = EXCLUDED.avg_win,
                avg_loss = EXCLUDED.avg_loss,
                max_drawdown = EXCLUDED.max_drawdown,
                sharpe_ratio = EXCLUDED.sharpe_ratio,
                last_updated = EXCLUDED.last_updated
        """
        
        await db_manager.execute_query(query, (
            strategy_name,
            exchange,
            pair,
            total_trades,
            winning_trades,
            losing_trades,
            total_pnl,
            win_rate,
            avg_win,
            avg_loss,
            max_drawdown,
            sharpe_ratio,
            datetime.utcnow()
        ))
        
        logger.info(f"Updated performance for strategy {strategy_name}")
        return {"message": f"Performance updated for {strategy_name}"}
        
    except Exception as e:
        logger.error(f"Failed to update strategy performance for {strategy_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Market Data Cache Endpoints
@app.post("/api/v1/cache/market-data")
async def cache_market_data(cache_data: MarketDataCache):
    """Cache market data"""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        query = """
            INSERT INTO trading.market_data_cache (exchange, pair, data_type, data, timestamp, expires_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (exchange, pair, data_type) 
            DO UPDATE SET 
                data = EXCLUDED.data,
                timestamp = EXCLUDED.timestamp,
                expires_at = EXCLUDED.expires_at
        """
        await db_manager.execute_query(query, (
            cache_data.exchange,
            cache_data.pair,
            cache_data.data_type,
            Json(cache_data.data),
            cache_data.timestamp,
            cache_data.expires_at
        ))
        return {"message": "Market data cached successfully"}
    except Exception as e:
        logger.error(f"Failed to cache market data: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/cache/market-data/{exchange}/{pair}/{data_type}")
async def get_cached_market_data(exchange: str, pair: str, data_type: str):
    """Get cached market data"""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        query = """
            SELECT * FROM trading.market_data_cache 
            WHERE exchange = %s AND pair = %s AND data_type = %s AND expires_at > %s
        """
        result = await db_manager.execute_single_query(query, (exchange, pair, data_type, datetime.utcnow()))
        if not result:
            raise HTTPException(status_code=404, detail="Cached data not found or expired")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get cached market data: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/v1/cache/expired")
async def cleanup_expired_cache(background_tasks: BackgroundTasks):
    """Clean up expired cache entries"""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    async def cleanup():
        try:
            if db_manager is None:
                logger.error("Database manager is not initialized; cannot clean up expired cache entries.")
                return
            query = "DELETE FROM trading.market_data_cache WHERE expires_at < %s"
            result = await db_manager.execute_query(query, (datetime.utcnow(),))
            logger.info(f"Cleaned up expired cache entries")
        except Exception as e:
            logger.error(f"Failed to cleanup expired cache: {e}")
    
    background_tasks.add_task(cleanup)
    return {"message": "Cache cleanup initiated"}

# Startup and shutdown events
@app.on_event("startup")
async def startup_event():
    """Initialize database on startup"""
    await initialize_database()

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    global db_manager
    if db_manager and db_manager.pool:
        db_manager.pool.closeall()
        logger.info("Database connections closed")

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8002,
        reload=True,
        log_level="info"
    ) 