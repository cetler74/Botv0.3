"""
Bybit Data Manager - Version 2.6.0
Handles Bybit-specific database operations and data management
"""

import asyncio
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
import asyncpg

logger = logging.getLogger(__name__)

class BybitDataManager:
    """
    Manages Bybit-specific database operations
    
    Features:
    - Order execution tracking
    - Order history management
    - Position tracking
    - Wallet balance monitoring
    - WebSocket event logging
    - Error logging and analytics
    """
    
    def __init__(self, database_url: str):
        """
        Initialize Bybit data manager
        
        Args:
            database_url: PostgreSQL connection URL
        """
        self.database_url = database_url
        self.pool = None
        
        logger.info("🗄️ Bybit Data Manager initialized")
    
    async def initialize(self) -> bool:
        """Initialize database connection pool"""
        try:
            self.pool = await asyncpg.create_pool(self.database_url)
            logger.info("✅ Bybit Data Manager connected to database")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to initialize Bybit Data Manager: {e}")
            return False
    
    async def close(self):
        """Close database connection pool"""
        if self.pool:
            await self.pool.close()
            logger.info("✅ Bybit Data Manager database connection closed")
    
    async def insert_order_execution(self, execution_data: Dict[str, Any]) -> Optional[int]:
        """
        Insert or update order execution record
        
        Args:
            execution_data: Order execution data from Bybit WebSocket
            
        Returns:
            Execution record ID if successful, None otherwise
        """
        try:
            async with self.pool.acquire() as conn:
                execution_id = await conn.fetchval(
                    """
                    SELECT upsert_bybit_execution(
                        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13
                    )
                    """,
                    execution_data.get('execution_id'),
                    execution_data.get('order_id'),
                    execution_data.get('order_link_id'),
                    execution_data.get('symbol'),
                    execution_data.get('side'),
                    execution_data.get('price'),
                    execution_data.get('qty'),
                    execution_data.get('exec_fee', 0),
                    execution_data.get('exec_time'),
                    execution_data.get('fee_rate'),
                    execution_data.get('exec_type'),
                    execution_data.get('is_maker', False),
                    execution_data.get('fee_currency', 'USDT')
                )
                
                logger.debug(f"📝 Inserted order execution: {execution_data.get('execution_id')}")
                return execution_id
                
        except Exception as e:
            logger.error(f"❌ Error inserting order execution: {e}")
            return None
    
    async def insert_order_history(self, order_data: Dict[str, Any]) -> Optional[int]:
        """
        Insert or update order history record
        
        Args:
            order_data: Order data from Bybit WebSocket
            
        Returns:
            Order record ID if successful, None otherwise
        """
        try:
            async with self.pool.acquire() as conn:
                order_id = await conn.fetchval(
                    """
                    SELECT upsert_bybit_order_history(
                        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15
                    )
                    """,
                    order_data.get('order_id'),
                    order_data.get('order_link_id'),
                    order_data.get('symbol'),
                    order_data.get('side'),
                    order_data.get('order_type'),
                    order_data.get('price'),
                    order_data.get('qty'),
                    order_data.get('cum_exec_qty', 0),
                    order_data.get('cum_exec_fee', 0),
                    order_data.get('avg_price'),
                    order_data.get('order_status'),
                    order_data.get('last_exec_price'),
                    order_data.get('last_exec_qty'),
                    order_data.get('exec_time'),
                    order_data.get('created_time'),
                    order_data.get('updated_time')
                )
                
                logger.debug(f"📝 Inserted order history: {order_data.get('order_id')}")
                return order_id
                
        except Exception as e:
            logger.error(f"❌ Error inserting order history: {e}")
            return None
    
    async def insert_position_history(self, position_data: Dict[str, Any]) -> Optional[int]:
        """
        Insert position history record
        
        Args:
            position_data: Position data from Bybit WebSocket
            
        Returns:
            Position record ID if successful, None otherwise
        """
        try:
            async with self.pool.acquire() as conn:
                position_id = await conn.fetchval(
                    """
                    INSERT INTO bybit_position_history (
                        symbol, side, size, avg_price, unrealized_pnl, mark_price, 
                        position_value, leverage, margin_type, position_idx, 
                        position_status, auto_add_margin, risk_id, free_qty, tp_sl_mode
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                    RETURNING id
                    """,
                    position_data.get('symbol'),
                    position_data.get('side'),
                    position_data.get('size'),
                    position_data.get('avg_price'),
                    position_data.get('unrealized_pnl', 0),
                    position_data.get('mark_price'),
                    position_data.get('position_value'),
                    position_data.get('leverage', 1),
                    position_data.get('margin_type', 'REGULAR_MARGIN'),
                    position_data.get('position_idx', 0),
                    position_data.get('position_status', 'Normal'),
                    position_data.get('auto_add_margin', False),
                    position_data.get('risk_id', 0),
                    position_data.get('free_qty', 0),
                    position_data.get('tp_sl_mode', 'Full')
                )
                
                logger.debug(f"📝 Inserted position history: {position_data.get('symbol')}")
                return position_id
                
        except Exception as e:
            logger.error(f"❌ Error inserting position history: {e}")
            return None
    
    async def insert_wallet_balance(self, balance_data: Dict[str, Any]) -> Optional[int]:
        """
        Insert wallet balance record
        
        Args:
            balance_data: Wallet balance data from Bybit WebSocket
            
        Returns:
            Balance record ID if successful, None otherwise
        """
        try:
            async with self.pool.acquire() as conn:
                balance_id = await conn.fetchval(
                    """
                    INSERT INTO bybit_wallet_balance_history (
                        currency, wallet_balance, available_balance, used_margin,
                        order_margin, position_margin, account_type
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7)
                    RETURNING id
                    """,
                    balance_data.get('currency'),
                    balance_data.get('wallet_balance'),
                    balance_data.get('available_balance'),
                    balance_data.get('used_margin', 0),
                    balance_data.get('order_margin', 0),
                    balance_data.get('position_margin', 0),
                    balance_data.get('account_type', 'UNIFIED')
                )
                
                logger.debug(f"📝 Inserted wallet balance: {balance_data.get('currency')}")
                return balance_id
                
        except Exception as e:
            logger.error(f"❌ Error inserting wallet balance: {e}")
            return None
    
    async def log_websocket_event(self, event_data: Dict[str, Any]) -> Optional[int]:
        """
        Log WebSocket event for debugging and analytics
        
        Args:
            event_data: WebSocket event data
            
        Returns:
            Event log ID if successful, None otherwise
        """
        try:
            async with self.pool.acquire() as conn:
                event_id = await conn.fetchval(
                    """
                    INSERT INTO bybit_websocket_events (
                        event_type, topic, event_data
                    ) VALUES ($1, $2, $3)
                    RETURNING id
                    """,
                    event_data.get('event_type'),
                    event_data.get('topic'),
                    event_data
                )
                
                logger.debug(f"📝 Logged WebSocket event: {event_data.get('event_type')}")
                return event_id
                
        except Exception as e:
            logger.error(f"❌ Error logging WebSocket event: {e}")
            return None
    
    async def log_error(self, error_data: Dict[str, Any]) -> Optional[int]:
        """
        Log error for monitoring and analytics
        
        Args:
            error_data: Error data from error handler
            
        Returns:
            Error log ID if successful, None otherwise
        """
        try:
            async with self.pool.acquire() as conn:
                error_id = await conn.fetchval(
                    """
                    INSERT INTO bybit_error_log (
                        error_category, error_severity, error_type, error_message,
                        error_context, recovery_actions, recovery_success, circuit_breaker_state
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    RETURNING id
                    """,
                    error_data.get('category'),
                    error_data.get('severity'),
                    error_data.get('error_type'),
                    error_data.get('error_message'),
                    error_data.get('context'),
                    error_data.get('recovery_actions', []),
                    error_data.get('recovery_success'),
                    error_data.get('circuit_breaker_state')
                )
                
                logger.debug(f"📝 Logged error: {error_data.get('error_type')}")
                return error_id
                
        except Exception as e:
            logger.error(f"❌ Error logging error: {e}")
            return None
    
    async def update_trade_bybit_data(self, trade_id: int, bybit_data: Dict[str, Any]) -> bool:
        """
        Update trade record with Bybit-specific data
        
        Args:
            trade_id: Trade record ID
            bybit_data: Bybit-specific data to update
            
        Returns:
            True if successful, False otherwise
        """
        try:
            async with self.pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE trades SET
                        bybit_order_id = $2,
                        bybit_order_link_id = $3,
                        bybit_execution_id = $4,
                        bybit_fee_currency = $5,
                        bybit_is_maker = $6,
                        bybit_exec_type = $7,
                        bybit_fee_rate = $8,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = $1
                    """,
                    trade_id,
                    bybit_data.get('order_id'),
                    bybit_data.get('order_link_id'),
                    bybit_data.get('execution_id'),
                    bybit_data.get('fee_currency', 'USDT'),
                    bybit_data.get('is_maker', False),
                    bybit_data.get('exec_type'),
                    bybit_data.get('fee_rate')
                )
                
                logger.debug(f"📝 Updated trade {trade_id} with Bybit data")
                return True
                
        except Exception as e:
            logger.error(f"❌ Error updating trade Bybit data: {e}")
            return False
    
    async def get_order_summary(self, symbol: str = None) -> List[Dict[str, Any]]:
        """
        Get order summary statistics
        
        Args:
            symbol: Optional symbol filter
            
        Returns:
            List of order summary records
        """
        try:
            async with self.pool.acquire() as conn:
                if symbol:
                    rows = await conn.fetch(
                        "SELECT * FROM bybit_order_summary WHERE symbol = $1",
                        symbol
                    )
                else:
                    rows = await conn.fetch("SELECT * FROM bybit_order_summary")
                
                return [dict(row) for row in rows]
                
        except Exception as e:
            logger.error(f"❌ Error getting order summary: {e}")
            return []
    
    async def get_execution_summary(self, symbol: str = None) -> List[Dict[str, Any]]:
        """
        Get execution summary statistics
        
        Args:
            symbol: Optional symbol filter
            
        Returns:
            List of execution summary records
        """
        try:
            async with self.pool.acquire() as conn:
                if symbol:
                    rows = await conn.fetch(
                        "SELECT * FROM bybit_execution_summary WHERE symbol = $1",
                        symbol
                    )
                else:
                    rows = await conn.fetch("SELECT * FROM bybit_execution_summary")
                
                return [dict(row) for row in rows]
                
        except Exception as e:
            logger.error(f"❌ Error getting execution summary: {e}")
            return []
    
    async def get_position_summary(self, symbol: str = None) -> List[Dict[str, Any]]:
        """
        Get position summary statistics
        
        Args:
            symbol: Optional symbol filter
            
        Returns:
            List of position summary records
        """
        try:
            async with self.pool.acquire() as conn:
                if symbol:
                    rows = await conn.fetch(
                        "SELECT * FROM bybit_position_summary WHERE symbol = $1",
                        symbol
                    )
                else:
                    rows = await conn.fetch("SELECT * FROM bybit_position_summary")
                
                return [dict(row) for row in rows]
                
        except Exception as e:
            logger.error(f"❌ Error getting position summary: {e}")
            return []
    
    async def get_recent_errors(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Get recent error logs
        
        Args:
            limit: Number of recent errors to retrieve
            
        Returns:
            List of recent error records
        """
        try:
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT * FROM bybit_error_log 
                    ORDER BY created_at DESC 
                    LIMIT $1
                    """,
                    limit
                )
                
                return [dict(row) for row in rows]
                
        except Exception as e:
            logger.error(f"❌ Error getting recent errors: {e}")
            return []
    
    async def get_websocket_events(self, event_type: str = None, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Get WebSocket events for debugging
        
        Args:
            event_type: Optional event type filter
            limit: Number of events to retrieve
            
        Returns:
            List of WebSocket event records
        """
        try:
            async with self.pool.acquire() as conn:
                if event_type:
                    rows = await conn.fetch(
                        """
                        SELECT * FROM bybit_websocket_events 
                        WHERE event_type = $1
                        ORDER BY created_at DESC 
                        LIMIT $2
                        """,
                        event_type, limit
                    )
                else:
                    rows = await conn.fetch(
                        """
                        SELECT * FROM bybit_websocket_events 
                        ORDER BY created_at DESC 
                        LIMIT $1
                        """,
                        limit
                    )
                
                return [dict(row) for row in rows]
                
        except Exception as e:
            logger.error(f"❌ Error getting WebSocket events: {e}")
            return []
    
    async def cleanup_old_data(self, days: int = 30) -> int:
        """
        Clean up old data to prevent database bloat
        
        Args:
            days: Number of days to keep data
            
        Returns:
            Number of records deleted
        """
        try:
            async with self.pool.acquire() as conn:
                # Clean up old WebSocket events
                events_deleted = await conn.fetchval(
                    """
                    DELETE FROM bybit_websocket_events 
                    WHERE created_at < NOW() - INTERVAL '$1 days'
                    """,
                    days
                )
                
                # Clean up old error logs
                errors_deleted = await conn.fetchval(
                    """
                    DELETE FROM bybit_error_log 
                    WHERE created_at < NOW() - INTERVAL '$1 days'
                    """,
                    days
                )
                
                # Clean up old wallet balance history
                balance_deleted = await conn.fetchval(
                    """
                    DELETE FROM bybit_wallet_balance_history 
                    WHERE created_at < NOW() - INTERVAL '$1 days'
                    """,
                    days
                )
                
                total_deleted = events_deleted + errors_deleted + balance_deleted
                logger.info(f"🧹 Cleaned up {total_deleted} old Bybit records")
                return total_deleted
                
        except Exception as e:
            logger.error(f"❌ Error cleaning up old data: {e}")
            return 0
