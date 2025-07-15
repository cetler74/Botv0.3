"""
Database Manager for the Multi-Exchange Trading Bot
Centralized database operations with connection pooling and error handling
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

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Centralized database manager for all bot components"""
    
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
                database=db_config.get('name', 'trading_bot'),
                user=db_config.get('user', 'carloslarramba'),
                password=db_config.get('password', ''),
                cursor_factory=RealDictCursor
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
            conn = self.pool.getconn()
            yield conn
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"Database connection error: {e}")
            raise
        finally:
            if conn:
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
        
    # Balance Management
    async def get_balance(self, exchange: str) -> Optional[Dict[str, Any]]:
        """Get balance for specific exchange"""
        query = """
            SELECT * FROM balance 
            WHERE exchange = %s 
            ORDER BY timestamp DESC 
            LIMIT 1
        """
        return await self.execute_single_query(query, (exchange,))
        
    async def update_balance(self, exchange: str, balance: float, available_balance: float, 
                           total_pnl: float, daily_pnl: float) -> bool:
        """Update balance for exchange"""
        try:
            query = """
                INSERT INTO balance (exchange, balance, available_balance, total_pnl, daily_pnl, timestamp)
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
            await self.execute_query(query, (exchange, balance, available_balance, total_pnl, daily_pnl, datetime.utcnow()))
            logger.info(f"Updated balance for {exchange}: balance={balance}, available={available_balance}")
            return True
        except Exception as e:
            logger.error(f"Failed to update balance for {exchange}: {e}")
            return False
            
    async def get_all_balances(self) -> List[Dict[str, Any]]:
        """Get balances for all exchanges"""
        query = """
            SELECT DISTINCT ON (exchange) 
                exchange, balance, available_balance, total_pnl, daily_pnl, timestamp
            FROM balance 
            ORDER BY exchange, timestamp DESC
        """
        return await self.execute_query(query)
        
    # Pairs Management
    async def save_pairs(self, exchange: str, pairs: List[str]) -> bool:
        """Save selected pairs for exchange"""
        try:
            query = """
                INSERT INTO trading.pairs (exchange, pair_list, timestamp)
                VALUES (%s, %s, %s)
            """
            await self.execute_query(query, (exchange, Json(pairs), datetime.utcnow()))
            logger.info(f"Saved {len(pairs)} pairs for {exchange}")
            return True
        except Exception as e:
            logger.error(f"Failed to save pairs for {exchange}: {e}")
            return False
            
    async def get_latest_pairs(self, exchange: str) -> Optional[List[str]]:
        """Get latest selected pairs for exchange"""
        query = """
            SELECT pair_list FROM trading.pairs 
            WHERE exchange = %s 
            ORDER BY timestamp DESC 
            LIMIT 1
        """
        result = await self.execute_single_query(query, (exchange,))
        return result['pair_list'] if result else None
        
    # Trades Management
    async def create_trade(self, trade_data: Dict[str, Any]) -> Optional[str]:
        """Create new trade record"""
        try:
            trade_id = str(uuid.uuid4())
            query = """
                INSERT INTO trades (
                    trade_id, pair, entry_price, status, entry_id, entry_time,
                    exchange, entry_reason, position_size, strategy
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
            """
            params = (
                trade_id,
                trade_data['pair'],
                trade_data.get('entry_price'),
                'OPEN',
                trade_data.get('entry_id'),
                trade_data.get('entry_time', datetime.utcnow()),
                trade_data['exchange'],
                trade_data.get('entry_reason'),
                trade_data.get('position_size'),
                trade_data.get('strategy')
            )
            result = await self.execute_single_query(query, params)
            logger.info(f"Created trade {trade_id} for {trade_data['pair']} on {trade_data['exchange']}")
            return trade_id
        except Exception as e:
            logger.error(f"Failed to create trade: {e}")
            return None
            
    async def update_trade(self, trade_id: str, update_data: Dict[str, Any]) -> bool:
        """Update trade record"""
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
                return False
                
            query = f"""
                UPDATE trades 
                SET {', '.join(set_clauses)}, updated_at = CURRENT_TIMESTAMP
                WHERE trade_id = %s
            """
            params.append(trade_id)
            
            await self.execute_query(query, tuple(params))
            logger.info(f"Updated trade {trade_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to update trade {trade_id}: {e}")
            return False
            
    async def get_open_trades(self, exchange: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all open trades, optionally filtered by exchange"""
        if exchange:
            query = """
                SELECT * FROM trades 
                WHERE status = 'OPEN' AND exchange = %s
                ORDER BY entry_time DESC
            """
            return await self.execute_query(query, (exchange,))
        else:
            query = """
                SELECT * FROM trades 
                WHERE status = 'OPEN'
                ORDER BY entry_time DESC
            """
            return await self.execute_query(query)
            
    async def get_trade_by_id(self, trade_id: str) -> Optional[Dict[str, Any]]:
        """Get trade by ID"""
        query = "SELECT * FROM trades WHERE trade_id = %s"
        return await self.execute_single_query(query, (trade_id,))
        
    async def get_trades_by_exchange(self, exchange: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent trades for exchange"""
        query = """
            SELECT * FROM trades 
            WHERE exchange = %s
            ORDER BY entry_time DESC 
            LIMIT %s
        """
        return await self.execute_query(query, (exchange, limit))
        
    # Alerts Management
    async def create_alert(self, alert_data: Dict[str, Any]) -> bool:
        """Create new alert"""
        try:
            query = """
                INSERT INTO alerts (level, category, message, details, exchange, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
            """
            params = (
                alert_data['level'],
                alert_data['category'],
                alert_data['message'],
                Json(alert_data.get('details', {})),
                alert_data.get('exchange'),
                alert_data.get('created_at', datetime.utcnow())
            )
            await self.execute_query(query, params)
            logger.info(f"Created alert: {alert_data['level']} - {alert_data['category']} - {alert_data['message']}")
            return True
        except Exception as e:
            logger.error(f"Failed to create alert: {e}")
            return False
            
    async def get_unresolved_alerts(self, level: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get unresolved alerts, optionally filtered by level"""
        if level:
            query = """
                SELECT * FROM alerts 
                WHERE resolved = FALSE AND level = %s
                ORDER BY created_at DESC
            """
            return await self.execute_query(query, (level,))
        else:
            query = """
                SELECT * FROM alerts 
                WHERE resolved = FALSE
                ORDER BY created_at DESC
            """
            return await self.execute_query(query)
            
    async def resolve_alert(self, alert_id: str) -> bool:
        """Mark alert as resolved"""
        try:
            query = """
                UPDATE alerts 
                SET resolved = TRUE, resolved_at = CURRENT_TIMESTAMP
                WHERE alert_id = %s
            """
            await self.execute_query(query, (alert_id,))
            return True
        except Exception as e:
            logger.error(f"Failed to resolve alert {alert_id}: {e}")
            return False
            
    # Market Data Cache
    async def cache_market_data(self, exchange: str, pair: str, data_type: str, 
                              data: Dict[str, Any], expires_in_minutes: int = 5) -> bool:
        """Cache market data with expiration"""
        try:
            expires_at = datetime.utcnow() + timedelta(minutes=expires_in_minutes)
            query = """
                INSERT INTO market_data_cache (exchange, pair, data_type, data, expires_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (exchange, pair, data_type) 
                DO UPDATE SET 
                    data = EXCLUDED.data,
                    timestamp = CURRENT_TIMESTAMP,
                    expires_at = EXCLUDED.expires_at
            """
            await self.execute_query(query, (exchange, pair, data_type, Json(data), expires_at))
            return True
        except Exception as e:
            logger.error(f"Failed to cache market data: {e}")
            return False
            
    async def get_cached_market_data(self, exchange: str, pair: str, data_type: str) -> Optional[Dict[str, Any]]:
        """Get cached market data if not expired"""
        query = """
            SELECT data FROM market_data_cache 
            WHERE exchange = %s AND pair = %s AND data_type = %s 
            AND expires_at > CURRENT_TIMESTAMP
        """
        result = await self.execute_single_query(query, (exchange, pair, data_type))
        return result['data'] if result else None
        
    async def cleanup_expired_cache(self) -> int:
        """Clean up expired cache entries"""
        query = "DELETE FROM market_data_cache WHERE expires_at <= CURRENT_TIMESTAMP"
        await self.execute_query(query)
        return 0  # Could return actual count if needed
        
    # Strategy Performance
    async def update_strategy_performance(self, strategy_name: str, exchange: str, pair: str, 
                                        performance_data: Dict[str, Any]) -> bool:
        """Update strategy performance metrics"""
        try:
            query = """
                INSERT INTO strategy_performance (
                    strategy_name, exchange, pair, total_trades, winning_trades,
                    losing_trades, total_pnl, win_rate, avg_win, avg_loss,
                    max_drawdown, sharpe_ratio, last_updated
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
            params = (
                strategy_name, exchange, pair,
                performance_data.get('total_trades', 0),
                performance_data.get('winning_trades', 0),
                performance_data.get('losing_trades', 0),
                performance_data.get('total_pnl', 0.0),
                performance_data.get('win_rate', 0.0),
                performance_data.get('avg_win', 0.0),
                performance_data.get('avg_loss', 0.0),
                performance_data.get('max_drawdown', 0.0),
                performance_data.get('sharpe_ratio', 0.0),
                datetime.utcnow()
            )
            await self.execute_query(query, params)
            return True
        except Exception as e:
            logger.error(f"Failed to update strategy performance: {e}")
            return False
            
    async def get_strategy_performance(self, strategy_name: str, exchange: str, pair: str) -> Optional[Dict[str, Any]]:
        """Get strategy performance metrics"""
        query = """
            SELECT * FROM strategy_performance 
            WHERE strategy_name = %s AND exchange = %s AND pair = %s
        """
        return await self.execute_single_query(query, (strategy_name, exchange, pair))
        
    # Configuration Audit
    async def create_config_audit(self, audit_data: Dict[str, Any]) -> bool:
        """Create configuration audit record"""
        try:
            query = """
                INSERT INTO config_audit (component, config_key, old_value, new_value, changed_by, timestamp)
                VALUES (%s, %s, %s, %s, %s, %s)
            """
            params = (
                audit_data['component'],
                audit_data['config_key'],
                audit_data.get('old_value'),
                audit_data.get('new_value'),
                audit_data.get('changed_by', 'system'),
                audit_data.get('timestamp', datetime.utcnow())
            )
            await self.execute_query(query, params)
            return True
        except Exception as e:
            logger.error(f"Failed to create config audit: {e}")
            return False
            
    # Analytics and Reporting
    async def get_portfolio_summary(self) -> Dict[str, Any]:
        """Get portfolio summary across all exchanges"""
        try:
            # Get total balances
            balances = await self.get_all_balances()
            total_balance = sum(float(b['balance']) for b in balances)
            total_available = sum(float(b['available_balance']) for b in balances)
            total_pnl = sum(float(b['total_pnl']) for b in balances)
            daily_pnl = sum(float(b['daily_pnl']) for b in balances)
            
            # Get open trades count
            open_trades = await self.get_open_trades()
            
            # Get recent alerts
            recent_alerts = await self.get_unresolved_alerts()
            
            return {
                'total_balance': total_balance,
                'total_available_balance': total_available,
                'total_pnl': total_pnl,
                'daily_pnl': daily_pnl,
                'open_trades_count': len(open_trades),
                'unresolved_alerts_count': len(recent_alerts),
                'exchanges': [b['exchange'] for b in balances],
                'last_updated': datetime.utcnow().isoformat()
            }
        except Exception as e:
            logger.error(f"Failed to get portfolio summary: {e}")
            return {}
            
    async def get_exchange_performance(self, exchange: str, days: int = 30) -> Dict[str, Any]:
        """Get performance metrics for specific exchange"""
        try:
            # Get trades for the period
            since_date = datetime.utcnow() - timedelta(days=days)
            query = """
                SELECT * FROM trades 
                WHERE exchange = %s AND entry_time >= %s
                ORDER BY entry_time DESC
            """
            trades = await self.execute_query(query, (exchange, since_date))
            
            # Calculate metrics
            total_trades = len(trades)
            closed_trades = [t for t in trades if t['status'] == 'CLOSED']
            winning_trades = [t for t in closed_trades if float(t['realized_pnl']) > 0]
            
            total_pnl = sum(float(t['realized_pnl']) for t in closed_trades)
            win_rate = len(winning_trades) / len(closed_trades) if closed_trades else 0
            
            return {
                'exchange': exchange,
                'period_days': days,
                'total_trades': total_trades,
                'closed_trades': len(closed_trades),
                'winning_trades': len(winning_trades),
                'total_pnl': total_pnl,
                'win_rate': win_rate,
                'avg_pnl_per_trade': total_pnl / len(closed_trades) if closed_trades else 0
            }
        except Exception as e:
            logger.error(f"Failed to get exchange performance for {exchange}: {e}")
            return {}
            
    async def close(self):
        """Close database connections"""
        if self.pool:
            self.pool.closeall()
            logger.info("Database connections closed") 