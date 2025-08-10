"""
Database Service for the Multi-Exchange Trading Bot
Centralized database operations and data persistence
"""

import asyncio
import logging
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
import json
from decimal import Decimal, ROUND_HALF_UP
import httpx
import psycopg2
from psycopg2.extras import RealDictCursor, Json
from psycopg2.pool import SimpleConnectionPool
from contextlib import asynccontextmanager
import uuid
import httpx
from fastapi import FastAPI, HTTPException, BackgroundTasks, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import os
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST, CollectorRegistry

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create custom registry to avoid conflicts
db_registry = CollectorRegistry()

# Prometheus Metrics
db_queries_total = Counter('database_queries_total', 'Total database queries', ['operation', 'table', 'status'], registry=db_registry)
db_query_duration = Histogram('database_query_duration_seconds', 'Database query duration', ['operation', 'table'], registry=db_registry)
db_connections_active = Gauge('database_connections_active', 'Active database connections', registry=db_registry)
db_pool_size = Gauge('database_pool_size', 'Database connection pool size', registry=db_registry)
trades_stored = Counter('database_trades_stored_total', 'Total trades stored in database', ['exchange', 'status'], registry=db_registry)
balance_updates = Counter('database_balance_updates_total', 'Total balance updates', ['exchange'], registry=db_registry)

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
    entry_price: Optional[float] = None  # Optional for PENDING trades
    expected_entry_price: Optional[float] = None  # Expected price for PENDING trades
    exit_price: Optional[float] = None
    status: str  # 'PENDING', 'OPEN', 'CLOSED', 'FAILED', 'CANCELLED'
    position_size: float
    strategy: str
    entry_time: datetime
    exit_time: Optional[datetime] = None
    pnl: Optional[float] = None
    entry_reason: Optional[str] = None
    exit_reason: Optional[str] = None
    fees: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    entry_id: Optional[str] = None  # Exchange order ID
    exit_id: Optional[str] = None   # Exchange exit order ID
    status_reason: Optional[str] = None  # Reason for status changes
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    realized_pnl: Optional[float] = None
    highest_price: Optional[float] = None
    profit_protection: Optional[str] = None
    profit_protection_trigger: Optional[float] = None
    trail_stop: Optional[str] = None
    trail_stop_trigger: Optional[float] = None
    current_price: Optional[float] = None
    entry_id: Optional[str] = None

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

class Order(BaseModel):
    order_id: str
    trade_id: Optional[str] = None
    exchange: str
    symbol: str
    order_type: str  # 'market', 'limit', 'stop'
    side: str  # 'buy', 'sell'
    amount: float
    price: Optional[float] = None
    filled_amount: Optional[float] = 0
    filled_price: Optional[float] = None
    status: str  # 'pending', 'filled', 'cancelled', 'rejected'
    fees: Optional[float] = 0
    fee_rate: Optional[float] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    filled_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    timeout_seconds: Optional[int] = 300
    retry_count: Optional[int] = 0
    error_message: Optional[str] = None
    exchange_order_id: Optional[str] = None
    client_order_id: Optional[str] = None

class PortfolioSummary(BaseModel):
    total_balance: float
    available_balance: float
    total_pnl: float
    daily_pnl: float
    total_unrealized_pnl: float
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

# Status normalization function for Phase 0
def normalize_status(status: Optional[str], entity_type: str = "trade") -> str:
    """Normalize status to uppercase and validate against allowed values"""
    if not status:
        return "OPEN" if entity_type == "trade" else "PENDING"
    
    normalized = status.upper().strip()
    
    # Valid statuses by entity type
    valid_statuses = {
        "trade": ["PENDING", "OPEN", "CLOSED", "FAILED", "CANCELLED"],
        # Allow EXPIRED as a terminal state for orders to avoid falling back to PENDING
        "order": ["PENDING", "ACKNOWLEDGED", "PARTIALLY_FILLED", "FILLED", "CANCELLED", "REJECTED", "FAILED", "EXPIRED"]
    }
    
    if normalized in valid_statuses.get(entity_type, valid_statuses["trade"]):
        return normalized
    
    # Default fallbacks
    return "OPEN" if entity_type == "trade" else "PENDING"

# Phase 2: Event Sourcing Helpers (moved after DatabaseManager class definition)
async def append_event(
    db_manager,  # Type hint removed to avoid forward reference issue
    aggregate_id: str,
    aggregate_type: str,
    event_type: str,
    payload: Dict[str, Any],
    correlation_id: Optional[str] = None,
    causation_id: Optional[str] = None
) -> str:
    """Append event to event store (Phase 2)"""
    try:
        event_id = str(uuid.uuid4())
        query = """
            INSERT INTO trading.events (
                event_id, aggregate_id, aggregate_type, event_type, 
                payload, correlation_id, causation_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING event_id
        """
        
        result = await db_manager.execute_single_query(query, (
            event_id, aggregate_id, aggregate_type, event_type,
            Json(payload), correlation_id, causation_id
        ))
        
        logger.info(f"📝 Event appended: {event_type} for {aggregate_type}:{aggregate_id}")
        return event_id
    except Exception as e:
        logger.error(f"Failed to append event {event_type}: {e}")
        raise

async def get_events_for_aggregate(
    db_manager, 
    aggregate_id: str, 
    aggregate_type: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Get all events for an aggregate in sequence order (Phase 2)"""
    try:
        if aggregate_type:
            query = """
                SELECT * FROM trading.events 
                WHERE aggregate_id = %s AND aggregate_type = %s
                ORDER BY sequence_number ASC
            """
            params = (aggregate_id, aggregate_type)
        else:
            query = """
                SELECT * FROM trading.events 
                WHERE aggregate_id = %s
                ORDER BY sequence_number ASC
            """
            params = (aggregate_id,)
            
        events = await db_manager.execute_query(query, params)
        return events
    except Exception as e:
        logger.error(f"Failed to get events for {aggregate_id}: {e}")
        return []

async def ensure_order_mapping_exists(
    db_manager,
    local_order_id: str,
    client_order_id: str,
    exchange: str,
    symbol: str,
    side: str,
    order_type: str,
    amount: float,
    price: Optional[float] = None,
    trade_id: Optional[str] = None
) -> bool:
    """Ensure order mapping exists (idempotency check) - Phase 2"""
    try:
        # Check if mapping already exists
        check_query = """
            SELECT local_order_id FROM trading.order_mappings 
            WHERE client_order_id = %s
        """
        existing = await db_manager.execute_single_query(check_query, (client_order_id,))
        
        if existing:
            logger.info(f"🔄 Order mapping already exists for client_order_id: {client_order_id}")
            return False  # Already exists, idempotency violated
        
        # Create new mapping
        insert_query = """
            INSERT INTO trading.order_mappings (
                local_order_id, client_order_id, exchange, symbol, side,
                order_type, amount, price, trade_id, status
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        await db_manager.execute_query(insert_query, (
            local_order_id, client_order_id, exchange, symbol, side,
            order_type, amount, price, trade_id, "PENDING"
        ))
        
        logger.info(f"✅ Created order mapping: {local_order_id} -> {client_order_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to ensure order mapping: {e}")
        raise

# Phase 3: Order State Machine and Materializer
class OrderStateMachine:
    """Defines valid order state transitions (Phase 3)"""
    
    VALID_STATES = {
        "PENDING",     # Order created locally, not yet sent to exchange
        "SENT",        # Order sent to exchange, awaiting acknowledgment
        "ACKNOWLEDGED", # Exchange acknowledged the order
        "PARTIALLY_FILLED", # Order partially filled
        "FILLED",      # Order completely filled
        "CANCELLED",   # Order cancelled
        "REJECTED",    # Order rejected by exchange
        "FAILED"       # Order failed due to error
    }
    
    VALID_TRANSITIONS = {
        "PENDING": ["SENT", "FAILED", "CANCELLED"],
        "SENT": ["ACKNOWLEDGED", "REJECTED", "FAILED", "CANCELLED"],
        "ACKNOWLEDGED": ["PARTIALLY_FILLED", "FILLED", "CANCELLED", "FAILED"],
        "PARTIALLY_FILLED": ["FILLED", "CANCELLED", "FAILED"],
        "FILLED": [],  # Terminal state
        "CANCELLED": [],  # Terminal state
        "REJECTED": [],  # Terminal state
        "FAILED": []  # Terminal state
    }
    
    @classmethod
    def is_valid_transition(cls, from_state: str, to_state: str) -> bool:
        """Check if state transition is valid"""
        if from_state not in cls.VALID_STATES or to_state not in cls.VALID_STATES:
            return False
        return to_state in cls.VALID_TRANSITIONS.get(from_state, [])
    
    @classmethod
    def is_terminal_state(cls, state: str) -> bool:
        """Check if state is terminal"""
        return len(cls.VALID_TRANSITIONS.get(state, [])) == 0

class EventMaterializer:
    """Materializes views from events (Phase 3)"""
    
    def __init__(self, db_manager):
        self.db_manager = db_manager
        self.state_machine = OrderStateMachine()
        self.last_processed_event_id = None
    
    async def get_checkpoint(self) -> Optional[int]:
        """Get last processed event sequence number"""
        try:
            query = "SELECT last_sequence_number FROM trading.materializer_checkpoint WHERE id = 1"
            result = await self.db_manager.execute_single_query(query)
            return result['last_sequence_number'] if result else None
        except Exception:
            # First run - create checkpoint table
            await self.create_checkpoint_table()
            return None
    
    async def create_checkpoint_table(self):
        """Create checkpoint table for materializer"""
        query = """
            CREATE TABLE IF NOT EXISTS trading.materializer_checkpoint (
                id INTEGER PRIMARY KEY DEFAULT 1,
                last_sequence_number BIGINT DEFAULT 0,
                last_processed_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT single_checkpoint CHECK (id = 1)
            )
        """
        await self.db_manager.execute_query(query)
        
        # Insert initial record
        insert_query = """
            INSERT INTO trading.materializer_checkpoint (id, last_sequence_number) 
            VALUES (1, 0) ON CONFLICT (id) DO NOTHING
        """
        await self.db_manager.execute_query(insert_query)
    
    async def update_checkpoint(self, sequence_number: int):
        """Update checkpoint with last processed sequence number"""
        query = """
            UPDATE trading.materializer_checkpoint 
            SET last_sequence_number = %s, last_processed_at = CURRENT_TIMESTAMP 
            WHERE id = 1
        """
        await self.db_manager.execute_query(query, (sequence_number,))
    
    async def get_unprocessed_events(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get events that haven't been processed yet"""
        checkpoint = await self.get_checkpoint()
        
        query = """
            SELECT * FROM trading.events 
            WHERE sequence_number > %s 
            ORDER BY sequence_number ASC 
            LIMIT %s
        """
        
        events = await self.db_manager.execute_query(query, (checkpoint or 0, limit))
        return events
    
    async def process_event(self, event: Dict[str, Any]) -> bool:
        """Process a single event and update materialized views"""
        try:
            event_type = event['event_type']
            aggregate_type = event['aggregate_type']
            payload = event['payload']
            
            if aggregate_type == 'order':
                return await self.process_order_event(event_type, payload, event)
            elif aggregate_type == 'trade':
                return await self.process_trade_event(event_type, payload, event)
            else:
                logger.warning(f"Unknown aggregate type: {aggregate_type}")
                return True  # Skip unknown events
                
        except Exception as e:
            logger.error(f"Failed to process event {event['event_id']}: {e}")
            return False
    
    async def process_order_event(self, event_type: str, payload: Dict[str, Any], event: Dict[str, Any]) -> bool:
        """Process order-related events"""
        try:
            if event_type == 'OrderCreated':
                return await self.handle_order_created(payload)
            elif event_type == 'ExchangeAck':
                return await self.handle_exchange_ack(payload)
            elif event_type == 'OrderUpdate':
                return await self.handle_order_update(payload)
            elif event_type == 'OrderFilled':
                return await self.handle_order_filled(payload)
            elif event_type == 'OrderCancelled':
                return await self.handle_order_cancelled(payload)
            elif event_type == 'OrderImportedFromExchange':
                return await self.handle_order_imported_from_exchange(payload)
            else:
                logger.warning(f"Unknown order event type: {event_type}")
                return True
                
        except Exception as e:
            logger.error(f"Failed to process order event {event_type}: {e}")
            return False
    
    async def handle_order_created(self, payload: Dict[str, Any]) -> bool:
        """Handle OrderCreated event - create order view record"""
        local_order_id = payload['local_order_id']
        
        # Update order mapping status to SENT (from PENDING)
        update_query = """
            UPDATE trading.order_mappings 
            SET status = 'SENT', updated_at = CURRENT_TIMESTAMP
            WHERE local_order_id = %s AND status = 'PENDING'
        """
        await self.db_manager.execute_query(update_query, (local_order_id,))
        
        logger.info(f"📊 Materialized OrderCreated: {local_order_id} -> SENT")
        return True
    
    async def handle_exchange_ack(self, payload: Dict[str, Any]) -> bool:
        """Handle ExchangeAck event - update with exchange order ID"""
        local_order_id = payload['local_order_id']
        exchange_order_id = payload['exchange_order_id']
        
        # Order mapping should already be updated by orchestrator, 
        # but we can verify/ensure consistency
        update_query = """
            UPDATE trading.order_mappings 
            SET status = 'ACKNOWLEDGED', 
                exchange_order_id = %s,
                acknowledged_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE local_order_id = %s
        """
        await self.db_manager.execute_query(update_query, (exchange_order_id, local_order_id))
        
        logger.info(f"📊 Materialized ExchangeAck: {local_order_id} -> ACKNOWLEDGED ({exchange_order_id})")
        return True
    
    async def handle_order_update(self, payload: Dict[str, Any]) -> bool:
        """Handle OrderUpdate event from WebSocket"""
        local_order_id = payload.get('local_order_id')
        new_status = payload.get('status', '').upper()
        exchange_order_id = payload.get('exchange_order_id')
        client_order_id = payload.get('client_order_id')
        
        if not local_order_id:
            logger.warning("OrderUpdate event missing local_order_id")
            return True
        
        # Normalize and validate status transition
        current_mapping = await self.db_manager.execute_single_query(
            "SELECT status FROM trading.order_mappings WHERE local_order_id = %s",
            (local_order_id,)
        )
        
        if not current_mapping:
            logger.warning(f"Order mapping not found for local_order_id: {local_order_id}")
            return True
        
        current_status = current_mapping['status']
        
        # Validate state transition
        if not self.state_machine.is_valid_transition(current_status, new_status):
            logger.warning(f"Invalid state transition: {current_status} -> {new_status} for {local_order_id}")
            return True
        
        # Update order mapping status
        update_query = """
            UPDATE trading.order_mappings 
            SET status = %s, updated_at = CURRENT_TIMESTAMP
            WHERE local_order_id = %s
        """
        await self.db_manager.execute_query(update_query, (new_status, local_order_id))
        
        logger.info(f"📊 Materialized OrderUpdate: {local_order_id} -> {new_status}")

        # Attempt to propagate to trading.orders
        try:
            where_clauses = []
            params = []
            if exchange_order_id:
                where_clauses.append("exchange_order_id = %s")
                params.append(exchange_order_id)
            if client_order_id:
                where_clauses.append("client_order_id = %s")
                params.append(client_order_id)
            if not where_clauses and local_order_id:
                # Fallback via order_mappings join
                where_clauses.append("order_id = (SELECT order_id FROM trading.order_mappings WHERE local_order_id = %s LIMIT 1)")
                params.append(local_order_id)
            if where_clauses:
                orders_update_sql = f"UPDATE trading.orders SET status = %s, updated_at = CURRENT_TIMESTAMP WHERE (" + " OR ".join(where_clauses) + ")"
                await self.db_manager.execute_query(orders_update_sql, [new_status, *params])
                logger.info(f"📊 Synced trading.orders for mapping {local_order_id} -> {new_status}")
        except Exception as sync_err:
            logger.warning(f"⚠️ Failed to sync trading.orders for {local_order_id}: {sync_err}")
        return True
    
    async def handle_order_filled(self, payload: Dict[str, Any]) -> bool:
        """Handle OrderFilled event - create fill record"""
        local_order_id = payload['local_order_id']
        
        # Insert fill record
        fill_data = {
            'local_order_id': local_order_id,
            'exchange_order_id': payload.get('exchange_order_id'),
            'exchange': payload.get('exchange'),
            'symbol': payload.get('symbol'),
            'side': payload.get('side'),
            'qty': payload.get('quantity', 0),
            'price': payload.get('price', 0),
            'fee': payload.get('fee', 0),
            'fee_asset': payload.get('fee_asset', 'USD'),
            'trade_id': payload.get('trade_id'),
            'timestamp': payload.get('timestamp', datetime.utcnow())
        }
        
        insert_query = """
            INSERT INTO trading.fills (
                local_order_id, exchange_order_id, exchange, symbol, side,
                qty, price, fee, fee_asset, trade_id, timestamp
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        
        await self.db_manager.execute_query(insert_query, (
            fill_data['local_order_id'], fill_data['exchange_order_id'], 
            fill_data['exchange'], fill_data['symbol'], fill_data['side'],
            fill_data['qty'], fill_data['price'], fill_data['fee'], 
            fill_data['fee_asset'], fill_data['trade_id'], fill_data['timestamp']
        ))
        
        # Update order mapping status based on fill
        filled_status = 'FILLED' if payload.get('fully_filled', True) else 'PARTIALLY_FILLED'
        
        update_query = """
            UPDATE trading.order_mappings 
            SET status = %s, updated_at = CURRENT_TIMESTAMP
            WHERE local_order_id = %s
        """
        await self.db_manager.execute_query(update_query, (filled_status, local_order_id))
        
        logger.info(f"📊 Materialized OrderFilled: {local_order_id} -> {filled_status} (qty: {fill_data['qty']})")
        return True
    
    async def handle_order_cancelled(self, payload: Dict[str, Any]) -> bool:
        """Handle OrderCancelled event"""
        local_order_id = payload['local_order_id']
        
        update_query = """
            UPDATE trading.order_mappings 
            SET status = 'CANCELLED', updated_at = CURRENT_TIMESTAMP
            WHERE local_order_id = %s
        """
        await self.db_manager.execute_query(update_query, (local_order_id,))
        
        logger.info(f"📊 Materialized OrderCancelled: {local_order_id} -> CANCELLED")
        return True
    
    async def handle_order_imported_from_exchange(self, payload: Dict[str, Any]) -> bool:
        """Handle OrderImportedFromExchange event - create order mapping for orphaned orders"""
        local_order_id = payload['local_order_id']
        
        try:
            # Insert into order_mappings
            insert_query = """
                INSERT INTO trading.order_mappings (
                    local_order_id, client_order_id, exchange, exchange_order_id, symbol, side,
                    order_type, amount, price, status, acknowledged_at, trade_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (client_order_id) DO NOTHING
            """
            
            await self.db_manager.execute_query(insert_query, (
                local_order_id,
                payload['client_order_id'],
                payload['exchange'],
                payload['exchange_order_id'],
                payload['symbol'],
                payload['side'],
                payload['order_type'],
                payload['amount'],
                payload.get('price'),
                payload['status'],
                datetime.utcnow(),
                payload.get('trade_id')
            ))
            
            logger.info(f"📊 Materialized OrderImportedFromExchange: {local_order_id} -> {payload['status']} (imported from {payload['exchange']})")

            # Also try updating trading.orders if a record exists with these identifiers
            try:
                up_sql = """
                    UPDATE trading.orders
                    SET status = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE exchange_order_id = %s OR client_order_id = %s
                """
                await self.db_manager.execute_query(up_sql, (
                    payload['status'], payload.get('exchange_order_id'), payload.get('client_order_id')
                ))
            except Exception as e:
                logger.warning(f"⚠️ Could not sync imported order to trading.orders: {e}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to handle OrderImportedFromExchange: {e}")
            return False
    
    async def process_trade_event(self, event_type: str, payload: Dict[str, Any], event: Dict[str, Any]) -> bool:
        """Process trade-related events"""
        # Trade events can be processed here for trade lifecycle management
        logger.info(f"Processing trade event: {event_type}")
        return True
    
    async def run_materialization_cycle(self) -> int:
        """Run one cycle of event materialization"""
        processed_count = 0
        
        try:
            events = await self.get_unprocessed_events()
            
            for event in events:
                success = await self.process_event(event)
                
                if success:
                    await self.update_checkpoint(event['sequence_number'])
                    processed_count += 1
                else:
                    logger.error(f"Failed to process event {event['event_id']}, stopping materialization")
                    break
            
            if processed_count > 0:
                logger.info(f"📊 Materialized {processed_count} events")
                
        except Exception as e:
            logger.error(f"Materialization cycle failed: {e}")
        
        return processed_count

# Global materializer instance
materializer = None

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
                options=db_config.get('options', '-c search_path=trading')  # Use options from config service
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
            try:
                if conn:
                    conn.rollback()
            except Exception:
                pass
            import traceback
            logger.error(f"Database connection error: {e}\n{traceback.format_exc()}")
            raise
        finally:
            if conn and self.pool:
                self.pool.putconn(conn)
                
    async def execute_query(self, query: str, params: Optional[Tuple] = None) -> List[Dict[str, Any]]:
        """Execute a query and return results"""
        async with self.get_connection() as conn:
            with conn.cursor() as cursor:
                try:
                    if params is None:
                        cursor.execute(query)
                    else:
                        cursor.execute(query, params)
                except Exception as e:
                    import traceback
                    logger.error(f"Query failed: {e}\nSQL: {query}\nParams: {params}\n{traceback.format_exc()}")
                    raise
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
exchange_service_url: str = os.getenv("EXCHANGE_SERVICE_URL", "http://exchange-service:8003")

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

# Background task for materializer
async def materializer_background_task():
    """Background task that runs materializer periodically"""
    global materializer
    
    while True:
        try:
            if materializer and db_manager:
                processed = await materializer.run_materialization_cycle()
                if processed > 0:
                    logger.info(f"🔄 Background materializer processed {processed} events")
            
            # Run every 5 seconds
            await asyncio.sleep(5)
            
        except Exception as e:
            logger.error(f"Background materializer error: {e}")
            await asyncio.sleep(10)  # Wait longer on error

async def initialize_database():
    """Initialize database connection and materializer"""
    global db_manager, materializer
    try:
        config = await get_config_from_service()
        db_manager = DatabaseManager({"database": config})
        
        # Initialize materializer (Phase 3)
        materializer = EventMaterializer(db_manager)
        await materializer.get_checkpoint()  # Initialize checkpoint table
        
        # Start background materializer task
        asyncio.create_task(materializer_background_task())
        
        logger.info("✅ Database service initialized successfully with Phase 3 materializer")
    except Exception as e:
        logger.error(f"Failed to initialize database service: {e}")
        raise

# API Endpoints
@app.get("/metrics")
async def get_metrics():
    """Prometheus metrics endpoint"""
    return Response(generate_latest(db_registry), media_type=CONTENT_TYPE_LATEST)

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

@app.post("/api/v1/schema/apply")
async def apply_schema_update(schema_data: Dict[str, Any]):
    """Apply schema updates to database"""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        sql_script = schema_data.get("sql_script")
        if not sql_script:
            raise HTTPException(status_code=400, detail="SQL script is required")
        
        # Execute the SQL script
        await db_manager.execute_query(sql_script)
        logger.info("Schema update applied successfully")
        return {"status": "success", "message": "Schema update applied successfully"}
    except Exception as e:
        logger.error(f"Failed to apply schema update: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Trade Management Endpoints
@app.post("/api/v1/trades")
async def create_trade(trade: Trade):
    """Create new trade record"""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        # Normalize status using Phase 0 function
        requested_status = normalize_status(trade.status, "trade")

        # Idempotency/duplicate guard: if entry_id is provided, avoid duplicate OPEN trades
        existing = None
        if getattr(trade, 'entry_id', None):
            check_query = """
                SELECT trade_id FROM trading.trades
                WHERE exchange = %s AND pair = %s AND entry_id = %s
                LIMIT 1
            """
            existing = await db_manager.execute_single_query(
                check_query, (trade.exchange, trade.pair, trade.entry_id)
            )
        if existing:
            logger.info(f"Duplicate trade suppressed for {trade.exchange} {trade.pair} entry_id={trade.entry_id}, existing trade {existing['trade_id']}")
            return {"trade_id": existing['trade_id'], "status": "duplicate_suppressed"}

        query = """
            INSERT INTO trading.trades (
                trade_id, pair, entry_price, status, entry_time,
                exchange, entry_reason, position_size, strategy, entry_id,
                created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """
        params = (
            trade.trade_id,
            trade.pair,
            trade.entry_price or trade.expected_entry_price,  # Use expected_entry_price for PENDING trades
            requested_status,
            trade.entry_time,
            trade.exchange,
            trade.entry_reason,
            trade.position_size,
            trade.strategy,
            getattr(trade, 'entry_id', None),
            datetime.utcnow(),  # created_at
            datetime.utcnow()   # updated_at
        )
        result = await db_manager.execute_single_query(query, params)
        logger.info(f"Created trade {trade.trade_id} for {trade.pair} on {trade.exchange}")
        
        # Return success response - the trade was created successfully
        response_data = {"trade_id": trade.trade_id, "status": "created"}
        if result and 'id' in result:
            response_data["id"] = result['id']
        return response_data
    except Exception as e:
        logger.error(f"Failed to create trade: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/trades/cleanup-duplicates")
async def cleanup_duplicate_trades(exchange: Optional[str] = None, pair: Optional[str] = None):
    """Mark consolidated/orphaned duplicate trades as CANCELLED and clear false exit fields.
    This avoids incorrect PnL/percent displays for trades that never had a real exit order.
    """
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    try:
        conditions = ["(exit_reason ILIKE 'Duplicate trade - consolidated%' OR exit_reason ILIKE 'Orphaned trade - no matching%')"]
        params: list = []
        if exchange:
            conditions.append("exchange = %s")
            params.append(exchange)
        if pair:
            conditions.append("pair = %s")
            params.append(pair)
        where_sql = " AND ".join(conditions)

        # Update matching trades: set status=CANCELLED and clear exit fields
        update_sql = f"""
            UPDATE trading.trades
            SET status = 'CANCELLED',
                exit_price = NULL,
                exit_time = NULL,
                realized_pnl = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE {where_sql}
        """
        if params:
            await db_manager.execute_query(update_sql, tuple(params))
        else:
            await db_manager.execute_query(update_sql)

        # Return a quick summary
        count_sql = f"SELECT COUNT(*) AS affected FROM trading.trades WHERE {where_sql}"
        if params:
            summary = await db_manager.execute_single_query(count_sql, tuple(params))
        else:
            summary = await db_manager.execute_single_query(count_sql)
        return {"message": "Duplicate trades cleaned", "affected": (summary or {}).get('affected', 0)}
    except Exception as e:
        logger.error(f"Failed to cleanup duplicates: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/v1/trades/clear/open")
async def clear_open_trades():
    """Clear all open trades"""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        query = "DELETE FROM trading.trades WHERE status = 'OPEN'"
        await db_manager.execute_query(query)
        logger.info("Cleared all open trades")
        return {"message": "All open trades cleared successfully"}
    except Exception as e:
        logger.error(f"Failed to clear open trades: {e}")
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
async def get_trades(
    limit: int = 100, 
    offset: int = 0,
    page: Optional[int] = None,
    sort_by: str = "entry_time",
    sort_order: str = "desc",
    status: Optional[str] = None,
    exchange: Optional[str] = None
):
    """Get recent trades with pagination and sorting"""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    try:
        # Calculate offset from page if provided
        if page is not None and page > 0:
            offset = (page - 1) * limit
        
        # Validate sort fields to prevent SQL injection
        valid_sort_fields = [
            "trade_id", "pair", "exchange", "entry_time", "exit_time", 
            "entry_price", "exit_price", "current_price", "position_size", 
            "unrealized_pnl", "realized_pnl", "status", "entry_reason", "exit_reason",
            "profit_trigger", "trailing_stop", "highest_price", "notional_value", "realized_pnl_pct"
        ]
        
        if sort_by not in valid_sort_fields:
            sort_by = "entry_time"
        
        if sort_order.lower() not in ["asc", "desc"]:
            sort_order = "desc"
        
        # Build base query with calculated fields
        base_query = """
            SELECT *,
                   (entry_price * position_size) as notional_value,
                   profit_protection_trigger as profit_trigger,
                   trail_stop_trigger as trailing_stop,
                   CASE 
                       WHEN entry_price > 0 THEN 
                           ((exit_price - entry_price) / entry_price) * 100
                       ELSE 0 
                   END as realized_pnl_pct
            FROM trading.trades 
        """
        
        # Add WHERE conditions
        where_conditions = []
        params = []
        
        if status:
            where_conditions.append("status = %s")
            params.append(status)
        
        if exchange:
            where_conditions.append("exchange = %s")
            params.append(exchange)
        
        if where_conditions:
            base_query += " WHERE " + " AND ".join(where_conditions)
        
        # Add ORDER BY clause
        base_query += f" ORDER BY {sort_by} {sort_order.upper()}"
        
        # Add pagination
        base_query += " LIMIT %s OFFSET %s"
        params.extend([limit, offset])
        
        trades = await db_manager.execute_query(base_query, tuple(params))
        logger.info(f"Fetched {len(trades)} trades from database with sort_by={sort_by}, sort_order={sort_order}")
        return {"trades": trades}
    except Exception as e:
        logger.error(f"Failed to get trades: {e}")
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
                      'realized_pnl', 'highest_price', 'current_price', 'profit_protection', 
                      'profit_protection_trigger', 'trail_stop', 'trail_stop_trigger',
                      'exit_reason', 'fees', 'status', 'entry_id', 'position_size']:
                # Normalize status values for consistency
                if key == 'status':
                    value = normalize_status(value, "trade")
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

@app.get("/api/v1/trades/closed/history")
async def get_trade_history(
    page: int = 1,
    limit: int = 20,
    status: str = "CLOSED",
    exchange: Optional[str] = None,
    min_pnl: Optional[float] = None,
    max_pnl: Optional[float] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
):
    """Get trade history with pagination and filtering"""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        # Calculate offset for pagination
        offset = (page - 1) * limit
        
        # Build the base query
        base_query = """
            SELECT 
                trade_id,
                pair,
                exchange,
                entry_time,
                entry_price,
                position_size,
                exit_time,
                exit_price,
                entry_reason,
                exit_reason,
                realized_pnl,
                highest_price,
                profit_protection,
                profit_protection_trigger,
                trail_stop,
                trail_stop_trigger,
                CASE 
                    WHEN entry_price > 0 THEN 
                        ((exit_price - entry_price) / entry_price) * 100
                    ELSE 0 
                END as realized_pnl_pct
            FROM trading.trades 
            WHERE status = %s
        """
        params = [status]
        
        # Add exchange filter
        if exchange:
            base_query += " AND exchange = %s"
            params.append(exchange)
            
        # Add PnL filters
        if min_pnl is not None:
            base_query += " AND realized_pnl >= %s"
            params.append(str(min_pnl))
            
        if max_pnl is not None:
            base_query += " AND realized_pnl <= %s"
            params.append(str(max_pnl))
            
        # Add date filters
        if start_date:
            base_query += " AND entry_time >= %s"
            params.append(start_date)
            
        if end_date:
            base_query += " AND entry_time <= %s"
            params.append(end_date)
            
        # Get total count for pagination
        count_query = f"SELECT COUNT(*) as total FROM ({base_query}) as subquery"
        count_result = await db_manager.execute_single_query(count_query, tuple(params))
        total = count_result['total'] if count_result else 0
        
        # Add ordering and pagination
        base_query += " ORDER BY exit_time DESC LIMIT %s OFFSET %s"
        params.extend([str(limit), str(offset)])
        
        # Execute the query
        trades = await db_manager.execute_query(base_query, tuple(params))
        
        # Calculate total pages
        total_pages = (total + limit - 1) // limit
        
        return {
            "trades": trades,
            "total": total,
            "page": page,
            "total_pages": total_pages,
            "limit": limit
        }
        
    except Exception as e:
        logger.error(f"Error getting trade history: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/trades/sync-orders")
async def sync_orders_from_exchange(sync_data: Dict[str, Any]):
    """Sync filled orders from exchange to update trade status"""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        exchange = sync_data.get('exchange')
        filled_orders = sync_data.get('filled_orders', [])
        
        if not exchange or not filled_orders:
            raise HTTPException(status_code=400, detail="Missing exchange or filled_orders")
            
        updated_trades = 0
        closed_trades = 0
        
        for order in filled_orders:
            order_id = order.get('id')
            symbol = order.get('symbol')
            side = order.get('side')
            amount = order.get('filled', 0)
            price = order.get('price', 0)
            cost = order.get('cost', 0)
            timestamp = order.get('timestamp')
            
            if not order_id or not symbol:
                continue
                
            # Convert timestamp from milliseconds to datetime if needed
            if timestamp:
                if isinstance(timestamp, (int, float)) and timestamp > 1000000000000:  # Milliseconds
                    order_datetime = datetime.fromtimestamp(timestamp / 1000)
                else:
                    order_datetime = datetime.fromtimestamp(timestamp)
            else:
                order_datetime = datetime.utcnow()
            
            # Look for matching trades for this symbol and exchange
            if side == 'sell':
                # For sell orders, look for open trades to close
                amount_min = amount * 0.95
                amount_max = amount * 1.05
                
                query = """
                    SELECT trade_id, entry_price, position_size 
                    FROM trading.trades 
                    WHERE exchange = %s AND pair = %s AND status = 'OPEN'
                    AND position_size > 0
                    AND position_size BETWEEN %s AND %s
                    ORDER BY entry_time ASC
                    LIMIT 1
                """
                matching_trade = await db_manager.execute_single_query(query, (exchange, symbol, amount_min, amount_max))
                
                if matching_trade:
                    trade_id = matching_trade['trade_id']
                    entry_price = float(matching_trade['entry_price'])
                    position_size = float(matching_trade['position_size'])
                    
                    # Calculate PnL
                    if entry_price > 0:
                        realized_pnl = (price - entry_price) * amount
                        realized_pnl_pct = ((price - entry_price) / entry_price) * 100
                    else:
                        realized_pnl = 0
                        realized_pnl_pct = 0
                    
                    # Update trade as closed
                    update_query = """
                        UPDATE trading.trades 
                        SET exit_price = %s, 
                            exit_time = %s,
                            exit_id = %s,
                            realized_pnl = %s,
                            status = 'CLOSED',
                            exit_reason = 'exchange_sync',
                            updated_at = CURRENT_TIMESTAMP
                        WHERE trade_id = %s
                    """
                    await db_manager.execute_query(update_query, (
                        price, order_datetime, order_id, realized_pnl, trade_id
                    ))
                    
                    closed_trades += 1
                    logger.info(f"Closed trade {trade_id} from exchange sync - {symbol} at {price}")
                    
            elif side == 'buy':
                # For buy orders, look for FAILED trades that might actually be successful
                # or create new OPEN trades if no match is found
                amount_min = amount * 0.95
                amount_max = amount * 1.05
                
                # First check for FAILED trades that match this order
                failed_query = """
                    SELECT trade_id, entry_price, position_size 
                    FROM trading.trades 
                    WHERE exchange = %s AND pair = %s 
                    AND status = 'FAILED'
                    AND entry_id IS NULL
                    AND position_size > 0
                    AND position_size BETWEEN %s AND %s
                    AND entry_time >= %s - INTERVAL '2 hours'
                    ORDER BY entry_time DESC
                    LIMIT 1
                """
                failed_trade = await db_manager.execute_single_query(failed_query, 
                    (exchange, symbol, amount_min, amount_max, order_datetime))
                
                if failed_trade:
                    trade_id = failed_trade['trade_id']
                    
                    # Update FAILED trade to OPEN with exchange order ID
                    update_query = """
                        UPDATE trading.trades 
                        SET status = 'OPEN',
                            entry_id = %s,
                            entry_time = %s,
                            entry_price = %s,
                            position_size = %s,
                            exit_reason = NULL,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE trade_id = %s
                    """
                    await db_manager.execute_query(update_query, (
                        order_id, order_datetime, price, amount, trade_id
                    ))
                    
                    closed_trades += 1
                    logger.info(f"Recovered FAILED trade {trade_id} as OPEN - {symbol} at {price} with order {order_id}")
                else:
                    # No matching FAILED trade found, this might be a manual order or strategy order that wasn't recorded
                    logger.info(f"Found filled buy order {order_id} for {symbol} with no matching FAILED trade - manual order or missed recording")
                    
            updated_trades += 1
        
        logger.info(f"Synced {updated_trades} orders from {exchange}, closed {closed_trades} trades")
        return {
            "exchange": exchange,
            "processed_orders": updated_trades,
            "closed_trades": closed_trades,
            "filled_orders": len(filled_orders)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error syncing orders: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/trades/match-strategy-orders/{exchange}")
async def match_strategy_orders_with_exchange(exchange: str):
    """Match existing strategy-generated trades with their exchange order IDs"""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        # Use the same pattern as get_open_trades - this definitely works
        query = """
            SELECT trade_id, pair, entry_price, position_size, entry_time, status
            FROM trading.trades 
            WHERE exchange = %s AND entry_id IS NULL 
            AND entry_reason LIKE '%strategy signal%'
            AND status = 'OPEN'
            ORDER BY entry_time DESC
        """
        
        strategy_trades = await db_manager.execute_query(query, (exchange,))
        
        if not strategy_trades:
            return {"message": f"No strategy trades with missing entry_id found for {exchange}", "updated_count": 0}
        
        logger.info(f"[STRATEGY MATCH] Found {len(strategy_trades)} strategy trades with missing entry_id for {exchange}")
        
        return {
            "message": f"Strategy trade matching completed for {exchange}",
            "total_strategy_trades": len(strategy_trades),
            "updated_count": 0  # Will implement matching logic once basic query works
        }
        
    except Exception as e:
        logger.error(f"[STRATEGY MATCH] Error matching strategy orders: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/trades/validate-by-exchange-ids")
async def validate_trades_by_exchange_ids(validation_data: Dict[str, Any]):
    """Validate all recorded trades against exchange order IDs - comprehensive validation"""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        exchange = validation_data.get('exchange')
        if not exchange:
            raise HTTPException(status_code=400, detail="Missing exchange")
        
        logger.info(f"[TRADE VALIDATION] Starting comprehensive validation for {exchange}")
        
        # Get all trades for this exchange that have entry_id or exit_id
        validation_query = """
            SELECT trade_id, entry_id, exit_id, entry_price, exit_price, position_size, 
                   pair, status, entry_time, exit_time, realized_pnl
            FROM trading.trades 
            WHERE exchange = %s AND (entry_id IS NOT NULL OR exit_id IS NOT NULL)
            ORDER BY entry_time DESC
        """
        trades_to_validate = await db_manager.execute_query(validation_query, (exchange,))
        
        if not trades_to_validate:
            trades_to_validate = []
        
        validation_results = {
            "exchange": exchange,
            "total_trades_checked": len(trades_to_validate),
            "validated_trades": 0,
            "missing_entry_ids": [],
            "missing_exit_ids": [],
            "trades_needing_validation": []
        }
        
        for trade in trades_to_validate:
            trade_id = trade['trade_id']
            entry_id = trade['entry_id']
            exit_id = trade['exit_id']
            status = trade['status']
            pair = trade['pair']
            
            # Check for missing entry_id
            if status in ['open', 'closed'] and not entry_id:
                validation_results["missing_entry_ids"].append({
                    "trade_id": trade_id,
                    "pair": pair,
                    "status": status
                })
            
            # Check for missing exit_id on closed trades
            if status == 'CLOSED' and not exit_id:
                validation_results["missing_exit_ids"].append({
                    "trade_id": trade_id,
                    "pair": pair,
                    "entry_id": entry_id
                })
            
            # Add to trades needing external validation
            if entry_id or exit_id:
                validation_results["trades_needing_validation"].append({
                    "trade_id": trade_id,
                    "entry_id": entry_id,
                    "exit_id": exit_id,
                    "pair": pair,
                    "status": status
                })
            
            validation_results["validated_trades"] += 1
        
        logger.info(f"[TRADE VALIDATION] Completed for {exchange}: {validation_results['validated_trades']} trades checked, {len(validation_results['missing_entry_ids'])} missing entry IDs, {len(validation_results['missing_exit_ids'])} missing exit IDs")
        
        return validation_results
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in trade validation: {e}")
        raise HTTPException(status_code=500, detail=f"Trade validation failed: {str(e)}")

@app.post("/api/v1/trades/comprehensive-sync")
async def comprehensive_sync_from_exchange(sync_data: Dict[str, Any]):
    """Comprehensive sync from exchange - exchange is the source of truth"""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        exchange = sync_data.get('exchange')
        recent_trades = sync_data.get('recent_trades', [])
        current_balance = sync_data.get('current_balance', {})
        open_orders = sync_data.get('open_orders', [])
        sync_type = sync_data.get('sync_type', 'comprehensive')
        
        if not exchange:
            raise HTTPException(status_code=400, detail="Missing exchange")
        
        logger.info(f"[COMPREHENSIVE SYNC] Starting {sync_type} sync for {exchange}")
        logger.info(f"[COMPREHENSIVE SYNC] Processing {len(recent_trades)} recent trades")
        
        updated_trades = 0
        closed_trades = 0
        created_trades = 0
        
        # Process recent trades (exchange is source of truth)
        validated_trades = 0
        corrected_trades = 0
        
        for trade in recent_trades:
            trade_id = trade.get('id') or trade.get('order')
            symbol = trade.get('symbol')
            side = trade.get('side')
            amount = trade.get('amount', 0)
            price = trade.get('price', 0)
            cost = trade.get('cost', 0)
            timestamp = trade.get('timestamp')
            
            if not trade_id or not symbol:
                continue
            
            # Convert timestamp from milliseconds to datetime if needed
            if timestamp:
                if isinstance(timestamp, (int, float)) and timestamp > 1000000000000:
                    trade_datetime = datetime.fromtimestamp(timestamp / 1000)
                else:
                    trade_datetime = datetime.fromtimestamp(timestamp)
            else:
                trade_datetime = datetime.utcnow()
            
            # ENHANCED: Find existing trade by entry_id or exit_id for validation
            existing_trade_query = """
                SELECT trade_id, status, entry_id, exit_id, entry_price, exit_price, 
                       position_size, entry_time, exit_time, pair, exchange
                FROM trading.trades 
                WHERE (entry_id = %s OR exit_id = %s) AND exchange = %s
            """
            existing_trade = await db_manager.execute_single_query(
                existing_trade_query, (trade_id, trade_id, exchange)
            )
            
            if existing_trade:
                # ENHANCED: Validate and correct existing trade against exchange data
                db_trade_id = existing_trade['trade_id']
                corrections_needed = []
                
                # Validate based on whether this is entry or exit order
                if side == 'buy' and existing_trade['entry_id'] == trade_id:
                    # Validate entry data
                    if abs(float(existing_trade['entry_price'] or 0) - price) > 0.000001:
                        corrections_needed.append(f"entry_price: {existing_trade['entry_price']} → {price}")
                    if abs(float(existing_trade['position_size'] or 0) - amount) > 0.000001:
                        corrections_needed.append(f"position_size: {existing_trade['position_size']} → {amount}")
                    
                    if corrections_needed:
                        # Apply corrections for entry order
                        correct_entry_query = """
                            UPDATE trading.trades 
                            SET entry_price = %s, 
                                position_size = %s,
                                entry_time = %s,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE trade_id = %s
                        """
                        await db_manager.execute_query(correct_entry_query, (
                            price, amount, trade_datetime, db_trade_id
                        ))
                        corrected_trades += 1
                        logger.warning(f"🔧 [SYNC CORRECTION] Entry order {trade_id}: {', '.join(corrections_needed)}")
                    else:
                        validated_trades += 1
                        logger.debug(f"✅ [SYNC VALIDATED] Entry order {trade_id} matches database")
                        
                elif side == 'sell' and existing_trade['exit_id'] == trade_id:
                    # Validate exit data
                    if abs(float(existing_trade['exit_price'] or 0) - price) > 0.000001:
                        corrections_needed.append(f"exit_price: {existing_trade['exit_price']} → {price}")
                    
                    if corrections_needed:
                        # Recalculate PnL with corrected exit price
                        entry_price = float(existing_trade['entry_price'] or 0)
                        position_size = float(existing_trade['position_size'] or 0)
                        realized_pnl = (price - entry_price) * position_size if entry_price > 0 else 0
                        
                        # Apply corrections for exit order and CLOSE the trade
                        correct_exit_query = """
                            UPDATE trading.trades 
                            SET exit_price = %s,
                                realized_pnl = %s,
                                exit_time = %s,
                                status = 'CLOSED',
                                exit_reason = 'exchange_sync',
                                updated_at = CURRENT_TIMESTAMP
                            WHERE trade_id = %s
                        """
                        await db_manager.execute_query(correct_exit_query, (
                            price, realized_pnl, trade_datetime, db_trade_id
                        ))
                        corrected_trades += 1
                        logger.warning(f"🔧 [SYNC CORRECTION] Exit order {trade_id}: {', '.join(corrections_needed)}, recalculated PnL: {realized_pnl}")
                    else:
                        # Even if data matches, ensure the trade is marked as CLOSED
                        if existing_trade['status'] != 'CLOSED':
                            close_validated_query = """
                                UPDATE trading.trades 
                                SET status = 'CLOSED',
                                    exit_reason = 'exchange_sync',
                                    updated_at = CURRENT_TIMESTAMP
                                WHERE trade_id = %s
                            """
                            await db_manager.execute_query(close_validated_query, (db_trade_id,))
                            logger.info(f"🔧 [SYNC STATUS FIX] Exit order {trade_id} marked trade {db_trade_id} as CLOSED")
                            corrected_trades += 1
                        else:
                            validated_trades += 1
                            logger.debug(f"✅ [SYNC VALIDATED] Exit order {trade_id} matches database")
                
                continue  # Skip further processing as trade exists and is validated/corrected
            
            # Check if there's a strategy-generated trade that matches this exchange order
            # Match by symbol, side, exchange, and timing (within 10 minutes)
            strategy_match_query = """
                SELECT trade_id, status, entry_time FROM trading.trades 
                WHERE pair = %s AND exchange = %s AND status = 'OPEN'
                AND entry_id IS NULL 
                AND entry_reason LIKE %s
                AND ABS(EXTRACT(EPOCH FROM (entry_time - %s))) <= 600
                ORDER BY ABS(EXTRACT(EPOCH FROM (entry_time - %s))) ASC
                LIMIT 1
            """
            strategy_trade = await db_manager.execute_single_query(
                strategy_match_query, (symbol, exchange, '%strategy signal%', trade_datetime, trade_datetime)
            )
            
            # This is a new trade from exchange - need to handle it
            if side == 'buy':
                if strategy_trade:
                    # Update existing strategy-generated trade with exchange order ID
                    update_entry_query = """
                        UPDATE trading.trades 
                        SET entry_id = %s,
                            entry_price = %s,
                            position_size = %s,
                            entry_time = %s,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE trade_id = %s
                    """
                    await db_manager.execute_query(update_entry_query, (
                        trade_id, price, amount, trade_datetime, strategy_trade['trade_id']
                    ))
                    
                    updated_trades += 1
                    logger.info(f"[SYNC] Updated strategy trade {strategy_trade['trade_id']} with exchange entry_id {trade_id}")
                else:
                    # Create new trade entry
                    new_trade_id = str(uuid.uuid4())
                    
                    # Calculate position size
                    position_size = amount
                    
                    create_query = """
                        INSERT INTO trading.trades (
                            trade_id, pair, exchange, entry_price, position_size,
                            entry_id, entry_time, status, entry_reason, strategy,
                            created_at, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """
                    await db_manager.execute_query(create_query, (
                        new_trade_id, symbol, exchange, price, position_size,
                        trade_id, trade_datetime, 'open', 'exchange_sync', 'exchange_sync',
                        datetime.utcnow(), datetime.utcnow()
                    ))
                    
                    created_trades += 1
                    logger.info(f"[SYNC] Created new trade {new_trade_id} from exchange buy order {trade_id}")
                
            elif side == 'sell':
                # Look for matching open trade to close
                # Prioritize strategy-generated trades first, then any open trade
                match_query = """
                    SELECT trade_id, entry_price, position_size, entry_reason
                    FROM trading.trades 
                    WHERE exchange = %s AND pair = %s AND status = 'OPEN'
                    AND position_size > 0
                    ORDER BY 
                        CASE WHEN entry_reason LIKE '%strategy signal%' THEN 0 ELSE 1 END,
                        entry_time ASC
                    LIMIT 1
                """
                matching_trade = await db_manager.execute_single_query(match_query, (exchange, symbol))
                
                if matching_trade:
                    db_trade_id = matching_trade['trade_id']
                    entry_price = float(matching_trade['entry_price'])
                    
                    # Calculate PnL
                    if entry_price > 0:
                        realized_pnl = (price - entry_price) * amount
                    else:
                        realized_pnl = 0
                    
                    # Close the trade
                    close_query = """
                        UPDATE trading.trades 
                        SET exit_price = %s, 
                            exit_time = %s,
                            exit_id = %s,
                            realized_pnl = %s,
                            status = 'CLOSED',
                            exit_reason = 'exchange_sync',
                            updated_at = CURRENT_TIMESTAMP
                        WHERE trade_id = %s
                    """
                    await db_manager.execute_query(close_query, (
                        price, trade_datetime, trade_id, realized_pnl, db_trade_id
                    ))
                    
                    closed_trades += 1
                    trade_type = "strategy-generated" if "strategy signal" in matching_trade.get('entry_reason', '') else "exchange-sync"
                    logger.info(f"[SYNC] Closed {trade_type} trade {db_trade_id} with exchange exit_id {trade_id}")
                    
            updated_trades += 1
        
        # Update balance information
        balance_symbols = list(current_balance.get('free', {}).keys())
        logger.info(f"[SYNC] Updated balance for {len(balance_symbols)} symbols on {exchange}")
        
        # Check for trades that should be closed based on open orders
        # If we have no open orders for a symbol, but database shows open trades, they might be filled
        try:
            open_symbols = set(order.get('symbol') for order in open_orders if order.get('symbol'))
            
            # Get all open trades for this exchange
            open_trades_query = """
                SELECT trade_id, pair FROM trading.trades 
                WHERE exchange = %s AND status = 'OPEN'
            """
            open_trades = await db_manager.execute_query(open_trades_query, (exchange,))
            
            for trade in open_trades:
                trade_symbol = trade['pair']
                if trade_symbol not in open_symbols:
                    logger.info(f"[SYNC] Trade {trade['trade_id']} for {trade_symbol} is open in DB but no open orders on {exchange}")
                    
        except Exception as open_orders_error:
            logger.warning(f"[SYNC] Could not analyze open orders: {open_orders_error}")
        
        logger.info(f"[COMPREHENSIVE SYNC] Completed for {exchange}: {updated_trades} processed, {created_trades} created, {closed_trades} closed, {validated_trades} validated, {corrected_trades} corrected")
        
        return {
            "exchange": exchange,
            "sync_type": sync_type,
            "processed_trades": updated_trades,
            "created_trades": created_trades,
            "closed_trades": closed_trades,
            "validated_trades": validated_trades,
            "corrected_trades": corrected_trades,
            "balance_symbols": len(balance_symbols),
            "open_orders": len(open_orders)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in comprehensive sync: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Orders Management Endpoints
@app.post("/api/v1/orders")
async def create_order(order: Order):
    """Create new order record"""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        query = """
            INSERT INTO trading.orders (
                order_id, trade_id, exchange, symbol, order_type, side,
                amount, price, filled_amount, filled_price, status, fees,
                fee_rate, created_at, updated_at, timeout_seconds, retry_count,
                error_message, exchange_order_id, client_order_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """
        params = (
            order.order_id,
            order.trade_id,
            order.exchange,
            order.symbol,
            order.order_type,
            order.side,
            order.amount,
            order.price,
            order.filled_amount,
            order.filled_price,
            normalize_status(order.status, "order"),
            order.fees,
            order.fee_rate,
            datetime.utcnow(),
            datetime.utcnow(),
            order.timeout_seconds,
            order.retry_count,
            order.error_message,
            order.exchange_order_id,
            order.client_order_id
        )
        result = await db_manager.execute_single_query(query, params)
        logger.info(f"Created order {order.order_id} for {order.symbol} on {order.exchange}")
        
        return {"order_id": order.order_id, "status": "created"}
    except Exception as e:
        logger.error(f"Failed to create order: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/orders")
async def get_orders(
    exchange: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
    offset: int = 0
):
    """Get orders with filtering"""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        query = "SELECT * FROM trading.orders WHERE 1=1"
        params = []
        
        if exchange:
            query += " AND exchange = %s"
            params.append(exchange)
        
        if status:
            # Support comma-separated statuses and make filtering case-insensitive
            statuses = [s.strip().upper() for s in status.split(',') if s.strip()]
            if len(statuses) == 1:
                query += " AND status = %s"
                params.append(statuses[0])
            else:
                # Use an IN clause with the appropriate number of placeholders
                placeholders = ', '.join(['%s'] * len(statuses))
                query += f" AND status IN ({placeholders})"
                params.extend(statuses)
        
        query += " ORDER BY created_at DESC LIMIT %s OFFSET %s"
        params.extend([limit, offset])
        
        orders = await db_manager.execute_query(query, params)
        logger.info(f"Retrieved {len(orders)} orders")
        
        return {"orders": orders}
    except Exception as e:
        logger.error(f"Failed to get orders: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/orders/{order_id}")
async def get_order(order_id: str):
    """Get specific order by ID"""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        query = "SELECT * FROM trading.orders WHERE order_id = %s"
        order = await db_manager.execute_single_query(query, (order_id,))
        
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")
        
        return order
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get order {order_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/v1/orders/{order_id}")
async def update_order(order_id: str, update_data: Dict[str, Any]):
    """Update order status and details"""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        # Build dynamic update query
        set_clauses = []
        params = []
        
        for key, value in update_data.items():
            if key in ['filled_amount', 'filled_price', 'status', 'fees', 'fee_rate', 
                      'filled_at', 'cancelled_at', 'retry_count', 'error_message', 
                      'exchange_order_id', 'client_order_id']:
                # Normalize status values for consistency
                if key == 'status':
                    value = normalize_status(value, "order")
                set_clauses.append(f"{key} = %s")
                params.append(value)
        
        if not set_clauses:
            raise HTTPException(status_code=400, detail="No valid fields to update")
        
        set_clauses.append("updated_at = %s")
        params.append(datetime.utcnow())
        params.append(order_id)
        
        query = f"UPDATE trading.orders SET {', '.join(set_clauses)} WHERE order_id = %s"
        await db_manager.execute_query(query, params)
        
        logger.info(f"Updated order {order_id}")
        return {"order_id": order_id, "status": "updated"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update order {order_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/orders/performance")
async def get_order_performance_metrics():
    """Get order performance analytics"""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        # Get total orders by status
        status_query = """
            SELECT status, COUNT(*) as count 
            FROM trading.orders 
            GROUP BY status
        """
        status_counts = await db_manager.execute_query(status_query)
        
        # Get orders by exchange
        exchange_query = """
            SELECT exchange, COUNT(*) as count 
            FROM trading.orders 
            GROUP BY exchange
        """
        exchange_counts = await db_manager.execute_query(exchange_query)
        
        # Get orders by type
        type_query = """
            SELECT order_type, COUNT(*) as count 
            FROM trading.orders 
            GROUP BY order_type
        """
        type_counts = await db_manager.execute_query(type_query)
        
        # Calculate average fees
        fees_query = """
            SELECT AVG(fees) as avg_fees, SUM(fees) as total_fees
            FROM trading.orders 
            WHERE fees > 0
        """
        fees_data = await db_manager.execute_single_query(fees_query)
        
        return {
            "status_counts": status_counts,
            "exchange_counts": exchange_counts,
            "type_counts": type_counts,
            "fees_analytics": fees_data or {"avg_fees": 0, "total_fees": 0}
        }
    except Exception as e:
        logger.error(f"Failed to get order performance metrics: {e}")
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
        # Get total and available balances 
        balance_query = """
            SELECT 
                SUM(balance) as total_balance,
                SUM(available_balance) as available_balance
            FROM (
                SELECT DISTINCT ON (exchange) 
                    balance, available_balance
                FROM trading.balance 
                ORDER BY exchange, timestamp DESC
            ) latest_balances
        """
        balance_result = await db_manager.execute_single_query(balance_query)
        if not balance_result:
            balance_result = {"total_balance": 0, "available_balance": 0}
        # Calculate realized PnL from closed trades
        pnl_query = """
            SELECT 
                COALESCE(SUM(realized_pnl), 0) as total_realized_pnl,
                COALESCE(SUM(CASE 
                    WHEN exit_time >= CURRENT_DATE 
                    THEN realized_pnl 
                    ELSE 0 
                END), 0) as daily_realized_pnl
            FROM trading.trades 
            WHERE status = 'CLOSED' AND realized_pnl IS NOT NULL
        """
        pnl_result = await db_manager.execute_single_query(pnl_query)
        if not pnl_result:
            pnl_result = {"total_realized_pnl": 0, "daily_realized_pnl": 0}
        # Get trade statistics
        trade_query = """
            SELECT 
                COUNT(*) as total_trades,
                COUNT(CASE WHEN status = 'OPEN' THEN 1 END) as active_trades,
                COUNT(CASE WHEN status = 'CLOSED' AND realized_pnl > 0 THEN 1 END) as winning_trades,
                COUNT(CASE WHEN status = 'CLOSED' THEN 1 END) as closed_trades,
                COALESCE(SUM(CASE WHEN status = 'OPEN' THEN unrealized_pnl ELSE 0 END), 0) as total_unrealized_pnl
            FROM trading.trades
        """
        trade_result = await db_manager.execute_single_query(trade_query)
        if not trade_result:
            trade_result = {"total_trades": 0, "active_trades": 0, "winning_trades": 0, "closed_trades": 0, "total_unrealized_pnl": 0}
        # Calculate win rate
        win_rate = 0
        if trade_result['closed_trades'] > 0:
            win_rate = (trade_result['winning_trades'] / trade_result['closed_trades']) * 100
        # Get exchange breakdown (include both total and available balances)
        exchange_query = """
            SELECT DISTINCT ON (exchange) 
                exchange, balance, available_balance, total_pnl, daily_pnl, timestamp
            FROM trading.balance 
            ORDER BY exchange, timestamp DESC
        """
        exchanges = await db_manager.execute_query(exchange_query)
        exchange_dict = {}
        if exchanges:
            for ex in exchanges:
                exchange_dict[ex['exchange']] = {
                    'balance': float(ex['balance']),
                    'available': float(ex['available_balance']),
                    'available_balance': float(ex['available_balance']),
                    'total_pnl': float(ex.get('total_pnl', 0)),
                    'daily_pnl': float(ex.get('daily_pnl', 0)),
                    'timestamp': ex['timestamp'].isoformat() if ex.get('timestamp') is not None else None
                }
        return PortfolioSummary(
            total_balance=balance_result['total_balance'] or 0,
            available_balance=balance_result['available_balance'] or 0,
            total_pnl=pnl_result['total_realized_pnl'] or 0,
            daily_pnl=pnl_result['daily_realized_pnl'] or 0,
            total_unrealized_pnl=trade_result['total_unrealized_pnl'] or 0,
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
        testing = bool(os.getenv("TESTING"))
        if level:
            if testing:
                query = """
                    SELECT * FROM trading.alerts 
                    WHERE level = %s 
                    ORDER BY created_at DESC
                """
                alerts = await db_manager.execute_query(query, (level,))
            else:
                query = """
                    SELECT * FROM trading.alerts 
                    WHERE level = %s 
                    ORDER BY created_at DESC 
                    LIMIT %s
                """
                alerts = await db_manager.execute_query(query, (level, limit))
        else:
            if testing:
                query = """
                    SELECT * FROM trading.alerts 
                    ORDER BY created_at DESC
                """
                alerts = await db_manager.execute_query(query)
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

# Reconciliation: pull exchange data and synchronize DB (definitive fix path)
@app.post("/api/v1/trades/reconcile/{exchange}/{symbol}")
async def reconcile_trades_from_exchange(exchange: str, symbol: str, limit: int = 200):
    """Fetch fills/orders from exchange and run comprehensive sync so exit_time/exit_price/ids are accurate."""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            recent_trades: List[Dict[str, Any]] = []
            # Try fills first
            try:
                r_trades = await client.get(f"{exchange_service_url}/api/v1/trading/mytrades/{exchange}", params={"symbol": symbol, "limit": min(limit, 200)})
                if r_trades.status_code == 200:
                    recent_trades = r_trades.json().get("trades", [])
                else:
                    logger.warning(f"mytrades fetch {r_trades.status_code} for {exchange} {symbol}")
            except Exception as e:
                logger.warning(f"mytrades fetch failed for {exchange} {symbol}: {e}")

            # Fallback to orders history
            if not recent_trades:
                try:
                    r_hist = await client.get(f"{exchange_service_url}/api/v1/trading/orders/history/{exchange}", params={"symbol": symbol, "limit": min(limit, 200)})
                    r_hist.raise_for_status()
                    orders = r_hist.json().get("orders", [])
                    # Convert to trade-like structures for processing
                    for o in orders:
                        status = str(o.get("status", "")).lower()
                        if status in ("closed", "filled", "done"):
                            recent_trades.append({
                                "id": o.get("id") or o.get("order") or o.get("clientOrderId"),
                                "timestamp": o.get("timestamp"),
                                "symbol": o.get("symbol"),
                                "side": o.get("side"),
                                "amount": o.get("amount") or o.get("filled"),
                                "price": o.get("price"),
                                "cost": o.get("cost")
                            })
                except Exception as e:
                    logger.error(f"orders history fetch failed for {exchange} {symbol}: {e}")

            # Open orders and balance
            open_orders: List[Dict[str, Any]] = []
            try:
                r_open = await client.get(f"{exchange_service_url}/api/v1/trading/orders/{exchange}", params={"symbol": symbol})
                if r_open.status_code == 200:
                    open_orders = r_open.json().get("orders", [])
            except Exception as e:
                logger.warning(f"open orders fetch failed: {e}")

            current_balance: Dict[str, Any] = {}
            try:
                r_bal = await client.get(f"{exchange_service_url}/api/v1/account/balance/{exchange}")
                if r_bal.status_code == 200:
                    current_balance = r_bal.json()
            except Exception as e:
                logger.warning(f"balance fetch failed: {e}")

        # Run the comprehensive sync using exchange as source of truth
        payload = {
            "exchange": exchange,
            "recent_trades": recent_trades,
            "current_balance": current_balance,
            "open_orders": open_orders,
            "sync_type": "reconcile"
        }
        result = await comprehensive_sync_from_exchange(payload)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to reconcile trades from exchange: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/trades/reconcile")
async def reconcile_trades_body(recon_data: Dict[str, Any]):
    """Body-based reconcile to support symbols containing '/' without path routing issues."""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    try:
        exchange = recon_data.get("exchange")
        symbol = recon_data.get("symbol")
        limit = int(recon_data.get("limit", 200))
        if not exchange or not symbol:
            raise HTTPException(status_code=400, detail="Missing exchange or symbol")
        return await reconcile_trades_from_exchange(exchange, symbol, limit)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to run body reconcile: {e}")
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

# Pairs Endpoints
@app.get("/api/v1/pairs/{exchange_name}")
async def get_pairs(exchange_name: str):
    """Get trading pairs for a specific exchange"""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        query = """
            SELECT pair_list, timestamp FROM trading.pairs 
            WHERE exchange = %s 
            ORDER BY timestamp DESC 
            LIMIT 1
        """
        result = await db_manager.execute_single_query(query, (exchange_name,))
        logger.info(f"Query result for {exchange_name}: {result}")
        
        if result:
            # Handle JSONB field properly
            pair_list = result["pair_list"]
            if isinstance(pair_list, str):
                import json
                pair_list = json.loads(pair_list)
            
            timestamp = result.get("timestamp")
            
            return {
                "exchange": exchange_name,
                "pairs": pair_list,
                "timestamp": timestamp.isoformat() if timestamp else None
            }
        else:
            return {
                "exchange": exchange_name,
                "pairs": [],
                "timestamp": None
            }
            
    except Exception as e:
        logger.error(f"Failed to get pairs for {exchange_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/pairs/{exchange_name}")
async def add_pairs(exchange_name: str, pairs: List[str]):
    """Add trading pairs for a specific exchange"""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        query = """
            INSERT INTO trading.pairs (exchange, pair_list, timestamp, created_at)
            VALUES (%s, %s, %s, %s)
        """
        await db_manager.execute_query(query, (
            exchange_name,
            Json(pairs),
            datetime.utcnow(),
            datetime.utcnow()
        ))
        
        logger.info(f"Added {len(pairs)} pairs for {exchange_name}")
        return {"message": f"Added {len(pairs)} pairs for {exchange_name}"}
        
    except Exception as e:
        logger.error(f"Failed to add pairs for {exchange_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Asset Position Management Endpoints
@app.post("/api/v1/asset-positions")
async def create_or_update_asset_position(position_data: Dict[str, Any]):
    """Create or update asset position"""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        query = """
            INSERT INTO trading.asset_positions (
                exchange, asset, free_balance, used_balance, total_balance, last_updated
            ) VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (exchange, asset)
            DO UPDATE SET
                free_balance = EXCLUDED.free_balance,
                used_balance = EXCLUDED.used_balance,
                total_balance = EXCLUDED.total_balance,
                last_updated = EXCLUDED.last_updated,
                updated_at = CURRENT_TIMESTAMP
        """
        await db_manager.execute_query(query, (
            position_data['exchange'],
            position_data['asset'],
            position_data['free_balance'],
            position_data['used_balance'],
            position_data['total_balance'],
            datetime.fromisoformat(position_data['last_updated'].replace('Z', ''))
        ))
        logger.info(f"Updated asset position: {position_data['exchange']} {position_data['asset']} = {position_data['free_balance']}")
        return {"message": "Asset position updated successfully"}
    except Exception as e:
        logger.error(f"Failed to update asset position: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/asset-positions/{exchange}")
async def get_exchange_asset_positions(exchange: str):
    """Get all asset positions for an exchange"""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        query = """
            SELECT exchange, asset, free_balance, used_balance, total_balance, last_updated
            FROM trading.asset_positions
            WHERE exchange = %s
            ORDER BY asset
        """
        positions = await db_manager.execute_query(query, (exchange,))
        return {"exchange": exchange, "positions": positions}
    except Exception as e:
        logger.error(f"Failed to get asset positions for {exchange}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/asset-positions/{exchange}/{asset}")
async def get_specific_asset_position(exchange: str, asset: str):
    """Get position for a specific asset on an exchange"""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        query = """
            SELECT exchange, asset, free_balance, used_balance, total_balance, last_updated
            FROM trading.asset_positions
            WHERE exchange = %s AND asset = %s
        """
        position = await db_manager.execute_single_query(query, (exchange, asset))
        if not position:
            return {"exchange": exchange, "asset": asset, "free_balance": 0, "used_balance": 0, "total_balance": 0}
        return position
    except Exception as e:
        logger.error(f"Failed to get asset position for {exchange} {asset}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/asset-positions")
async def get_all_asset_positions():
    """Get all asset positions across all exchanges"""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        query = """
            SELECT exchange, asset, free_balance, used_balance, total_balance, last_updated
            FROM trading.asset_positions
            WHERE free_balance > 0 OR used_balance > 0
            ORDER BY exchange, asset
        """
        positions = await db_manager.execute_query(query)
        return {"positions": positions}
    except Exception as e:
        logger.error(f"Failed to get all asset positions: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Phase 2: Event Sourcing Endpoints
@app.post("/api/v1/events")
async def append_event_endpoint(event_data: Dict[str, Any]):
    """Append event to event store (Phase 2)"""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        required_fields = ['aggregate_id', 'aggregate_type', 'event_type', 'payload']
        for field in required_fields:
            if field not in event_data:
                raise HTTPException(status_code=400, detail=f"Missing required field: {field}")
        
        event_id = await append_event(
            db_manager,
            event_data['aggregate_id'],
            event_data['aggregate_type'],
            event_data['event_type'],
            event_data['payload'],
            event_data.get('correlation_id'),
            event_data.get('causation_id')
        )
        
        return {"event_id": event_id, "status": "appended"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to append event: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/events/{aggregate_id}")
async def get_aggregate_events(aggregate_id: str, aggregate_type: Optional[str] = None):
    """Get all events for an aggregate (Phase 2)"""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        events = await get_events_for_aggregate(db_manager, aggregate_id, aggregate_type)
        return {"aggregate_id": aggregate_id, "events": events}
    except Exception as e:
        logger.error(f"Failed to get events for {aggregate_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/order-mappings")
async def create_order_mapping(mapping_data: Dict[str, Any]):
    """Create order mapping with idempotency check (Phase 2)"""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        required_fields = ['local_order_id', 'client_order_id', 'exchange', 'symbol', 'side', 'order_type', 'amount']
        for field in required_fields:
            if field not in mapping_data:
                raise HTTPException(status_code=400, detail=f"Missing required field: {field}")
        
        created = await ensure_order_mapping_exists(
            db_manager,
            mapping_data['local_order_id'],
            mapping_data['client_order_id'],
            mapping_data['exchange'],
            mapping_data['symbol'],
            mapping_data['side'],
            mapping_data['order_type'],
            mapping_data['amount'],
            mapping_data.get('price'),
            mapping_data.get('trade_id')
        )
        
        if created:
            return {"status": "created", "local_order_id": mapping_data['local_order_id']}
        else:
            return {"status": "already_exists", "local_order_id": mapping_data['local_order_id']}
    except Exception as e:
        logger.error(f"Failed to create order mapping: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/v1/order-mappings/{client_order_id}")
async def update_order_mapping(client_order_id: str, update_data: Dict[str, Any]):
    """Update order mapping with exchange order ID (Phase 2)"""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        # Build dynamic update query
        set_clauses = []
        params = []
        
        for key, value in update_data.items():
            if key in ['exchange_order_id', 'status', 'acknowledged_at']:
                if key == 'status':
                    value = normalize_status(value, "order")
                set_clauses.append(f"{key} = %s")
                params.append(value)
        
        if not set_clauses:
            raise HTTPException(status_code=400, detail="No valid fields to update")
        
        set_clauses.append("updated_at = %s")
        params.append(datetime.utcnow())
        params.append(client_order_id)
        
        query = f"UPDATE trading.order_mappings SET {', '.join(set_clauses)} WHERE client_order_id = %s"
        await db_manager.execute_query(query, params)
        
        logger.info(f"Updated order mapping for client_order_id: {client_order_id}")
        return {"status": "updated", "client_order_id": client_order_id}
    except Exception as e:
        logger.error(f"Failed to update order mapping: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/order-mappings")
async def get_order_mappings(
    exchange: Optional[str] = None,
    status: Optional[str] = None,
    symbol: Optional[str] = None,
    limit: Optional[int] = 100
):
    """Get order mappings with filtering (Phase 4)"""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    
    try:
        # Build query with filters
        conditions = []
        params = []
        
        if exchange:
            conditions.append("exchange = %s")
            params.append(exchange)
        
        if status:
            # Handle comma-separated status values
            if ',' in status:
                status_list = [s.strip().upper() for s in status.split(',')]
                placeholders = ','.join(['%s'] * len(status_list))
                conditions.append(f"status IN ({placeholders})")
                params.extend(status_list)
            else:
                conditions.append("status = %s")
                params.append(status.upper())
        
        if symbol:
            conditions.append("symbol = %s")
            params.append(symbol)
        
        # Construct query
        query = """
        SELECT local_order_id, exchange, exchange_order_id, client_order_id,
               symbol, side, order_type, amount, price, status,
               created_at, updated_at, acknowledged_at, trade_id
        FROM trading.order_mappings
        """
        
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        
        query += " ORDER BY created_at DESC"
        
        if limit:
            query += f" LIMIT {limit}"
        
        # Debug logging
        logger.info(f"Executing query: {query} with params: {params}")
        result = await db_manager.execute_query(query, params)
        logger.info(f"Query returned: {len(result) if result else 0} rows")
        
        # Handle case where result might be empty or None
        if not result:
            return {
                "order_mappings": [],
                "count": 0,
                "filters": {
                    "exchange": exchange,
                    "status": status,
                    "symbol": symbol,
                    "limit": limit
                }
            }
        
        # Convert to dict format if result has rows
        order_mappings = []
        for row in result:
            try:
                order_mappings.append({
                    'local_order_id': str(row['local_order_id']) if row['local_order_id'] else None,
                    'exchange': str(row['exchange']) if row['exchange'] else None,
                    'exchange_order_id': str(row['exchange_order_id']) if row['exchange_order_id'] else None,
                    'client_order_id': str(row['client_order_id']) if row['client_order_id'] else None,
                    'symbol': str(row['symbol']) if row['symbol'] else None,
                    'side': str(row['side']) if row['side'] else None,
                    'order_type': str(row['order_type']) if row['order_type'] else None,
                    'amount': str(row['amount']) if row['amount'] is not None else "0",
                    'price': str(row['price']) if row['price'] is not None else None,
                    'status': str(row['status']) if row['status'] else None,
                    'created_at': row['created_at'].isoformat() if row['created_at'] else None,
                    'updated_at': row['updated_at'].isoformat() if row['updated_at'] else None,
                    'acknowledged_at': row['acknowledged_at'].isoformat() if row['acknowledged_at'] else None,
                    'trade_id': str(row['trade_id']) if row['trade_id'] else None
                })
            except Exception as row_error:
                logger.error(f"Failed to process row: {row_error}")
                continue
        
        return {
            "order_mappings": order_mappings,
            "count": len(order_mappings),
            "filters": {
                "exchange": exchange,
                "status": status,
                "symbol": symbol,
                "limit": limit
            }
        }
        
    except Exception as e:
        logger.error(f"Failed to get order mappings: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Phase 3: Materializer Endpoints
@app.post("/api/v1/materializer/run")
async def run_materializer():
    """Manually trigger materializer run (Phase 3)"""
    if not db_manager or not materializer:
        raise HTTPException(status_code=503, detail="Database or materializer not initialized")
    
    try:
        processed_count = await materializer.run_materialization_cycle()
        return {
            "status": "completed",
            "processed_events": processed_count,
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        logger.error(f"Failed to run materializer: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/materializer/status")
async def get_materializer_status():
    """Get materializer status and checkpoint (Phase 3)"""
    if not db_manager or not materializer:
        raise HTTPException(status_code=503, detail="Database or materializer not initialized")
    
    try:
        checkpoint = await materializer.get_checkpoint()
        
        # Get total event count
        total_events_query = "SELECT COUNT(*) as total FROM trading.events"
        total_result = await db_manager.execute_single_query(total_events_query)
        total_events = total_result['total'] if total_result else 0
        
        # Get unprocessed event count
        unprocessed_events = await materializer.get_unprocessed_events(limit=1000)
        unprocessed_count = len(unprocessed_events)
        
        return {
            "checkpoint": {
                "last_sequence_number": checkpoint,
                "total_events": total_events,
                "unprocessed_events": unprocessed_count,
                "processing_lag": unprocessed_count > 0
            },
            "status": "healthy" if unprocessed_count < 100 else "lagging"
        }
    except Exception as e:
        logger.error(f"Failed to get materializer status: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/materializer/replay")
async def replay_events(from_sequence: int = 0):
    """Replay events from a specific sequence number (Phase 3)"""
    if not db_manager or not materializer:
        raise HTTPException(status_code=503, detail="Database or materializer not initialized")
    
    try:
        # Reset checkpoint to replay from specific point
        reset_query = """
            UPDATE trading.materializer_checkpoint 
            SET last_sequence_number = %s, last_processed_at = CURRENT_TIMESTAMP 
            WHERE id = 1
        """
        await db_manager.execute_query(reset_query, (from_sequence,))
        
        # Run materialization to process events from the reset point
        processed_count = await materializer.run_materialization_cycle()
        
        return {
            "status": "replayed",
            "from_sequence": from_sequence,
            "processed_events": processed_count,
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        logger.error(f"Failed to replay events: {e}")
        raise HTTPException(status_code=500, detail=str(e))

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

@app.get("/api/v1/pnl/realized")
async def get_realized_pnl(exchange: str, symbol: str, start: Optional[str] = None, end: Optional[str] = None):
    """Compute realized PnL via FIFO using fills (shadow-only). Dates are ISO strings (UTC)."""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    try:
        # Build time filter
        where_time = ""
        params: list = [exchange, symbol]
        if start:
            where_time += " AND timestamp >= %s"
            params.append(datetime.fromisoformat(start.replace('Z', '')))
        if end:
            where_time += " AND timestamp <= %s"
            params.append(datetime.fromisoformat(end.replace('Z', '')))

        query = f"""
            SELECT exchange, symbol, side, qty, price, fee, fee_asset, timestamp
            FROM trading.fills
            WHERE exchange = %s AND symbol = %s {where_time}
            ORDER BY timestamp ASC
        """
        fills = await db_manager.execute_query(query, tuple(params))

        # FIFO lots
        buy_lots = []  # list of dicts: {qty, price, fee}
        realized_pnl_excl_fees = Decimal('0')
        fees_usd = Decimal('0')

        def to_dec(x):
            return Decimal(str(x or 0))

        async def convert_fee_to_usd(fee_amt: Decimal, fee_asset: str, ts: datetime) -> Decimal:
            if fee_amt <= 0:
                return Decimal('0')
            asset = fee_asset.upper()
            if asset in ('USD', 'USDC', 'USDT'):
                # Treat USD stablecoins as USD for now
                return fee_amt
            # Try to fetch historical-ish price via OHLCV (1h) near ts
            async with httpx.AsyncClient(timeout=5.0) as client:
                base_symbol = f"{asset}/USD"
                # Prefer USD, fallback USDC
                for conv_symbol in (base_symbol, f"{asset}/USDC"):
                    try:
                        ohlcv = await client.get(f"{exchange_service_url}/api/v1/market/ohlcv/{exchange}/{conv_symbol}", params={"timeframe":"1h", "limit": 100})
                        if ohlcv.status_code == 200:
                            data = ohlcv.json().get('data')
                            if data and 'timestamp' in data and 'close' in data:
                                # Find closest candle
                                times = data['timestamp']
                                closes = data['close']
                                target = int(ts.timestamp() * 1000)
                                idx = min(range(len(times)), key=lambda i: abs(times[i] - target))
                                px = Decimal(str(closes[idx] or 0))
                                if px > 0:
                                    # If quoted vs USDC, assume ~1:1 to USD
                                    return (fee_amt * px).quantize(Decimal('0.00000001'), rounding=ROUND_HALF_UP)
                        # Fallback to ticker last
                        tick = await client.get(f"{exchange_service_url}/api/v1/market/ticker/{exchange}/{conv_symbol}")
                        if tick.status_code == 200:
                            last = tick.json().get('last')
                            px = Decimal(str(last or 0))
                            if px > 0:
                                return (fee_amt * px).quantize(Decimal('0.00000001'), rounding=ROUND_HALF_UP)
                    except Exception:
                        continue
            return Decimal('0')

        for f in fills or []:
            side = (f.get('side') or '').lower()
            qty = to_dec(f.get('qty'))
            price = to_dec(f.get('price'))
            fee = to_dec(f.get('fee'))
            fee_asset = (f.get('fee_asset') or 'USD').upper()
            fee_usd = await convert_fee_to_usd(fee, fee_asset, f.get('timestamp') or datetime.utcnow()) if fee > 0 else Decimal('0')

            if side == 'buy':
                if qty > 0:
                    buy_lots.append({
                        'qty': qty,
                        'price': price,
                        'fee_usd': fee_usd
                    })
                fees_usd += fee_usd
            elif side == 'sell':
                remaining = qty
                sell_price = price
                # Allocate sell over FIFO buys
                i = 0
                while remaining > 0 and i < len(buy_lots):
                    lot = buy_lots[i]
                    take = min(remaining, lot['qty'])
                    if take > 0:
                        realized_pnl_excl_fees += (sell_price - lot['price']) * take
                        lot['qty'] = (lot['qty'] - take).quantize(Decimal('0.00000001'), rounding=ROUND_HALF_UP)
                        remaining = (remaining - take).quantize(Decimal('0.00000001'), rounding=ROUND_HALF_UP)
                    if lot['qty'] <= 0:
                        i += 1
                # Drop depleted lots
                buy_lots = [l for l in buy_lots if l['qty'] > 0]
                fees_usd += fee_usd
            else:
                continue

        realized_pnl_incl_fees = (realized_pnl_excl_fees - fees_usd).quantize(Decimal('0.00000001'), rounding=ROUND_HALF_UP)
        return {
            'exchange': exchange,
            'symbol': symbol,
            'period': {'start': start, 'end': end},
            'realized_pnl_excl_fees': float(realized_pnl_excl_fees),
            'fees_usd': float(fees_usd),
            'realized_pnl_incl_fees': float(realized_pnl_incl_fees)
        }
    except Exception as e:
        logger.error(f"PnL realized error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/pnl/unrealized")
async def get_unrealized_pnl(exchange: str, symbol: str):
    """Compute unrealized PnL: remaining inventory lots marked to current price."""
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    try:
        # Pull all fills to reconstruct inventory
        query = """
            SELECT side, qty, price
            FROM trading.fills
            WHERE exchange = %s AND symbol = %s
            ORDER BY timestamp ASC
        """
        fills = await db_manager.execute_query(query, (exchange, symbol))
        buy_lots = []
        from decimal import Decimal
        def to_dec(x):
            return Decimal(str(x or 0))
        for f in fills or []:
            side = (f.get('side') or '').lower()
            qty = to_dec(f.get('qty'))
            price = to_dec(f.get('price'))
            if side == 'buy' and qty > 0:
                buy_lots.append({'qty': qty, 'price': price})
            elif side == 'sell' and qty > 0:
                remaining = qty
                i = 0
                while remaining > 0 and i < len(buy_lots):
                    lot = buy_lots[i]
                    take = min(remaining, lot['qty'])
                    if take > 0:
                        lot['qty'] = (lot['qty'] - take)
                        remaining = (remaining - take)
                    if lot['qty'] <= 0:
                        i += 1
                buy_lots = [l for l in buy_lots if l['qty'] > 0]
        # Current price from exchange-service
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{exchange_service_url}/api/v1/market/ticker/{exchange}/{symbol}")
                if resp.status_code == 200:
                    data = resp.json()
                    bid = Decimal(str(data.get('bid') or 0))
                    ask = Decimal(str(data.get('ask') or 0))
                    last = Decimal(str(data.get('last') or 0))
                    if bid > 0 and ask > 0:
                        current_price = (bid + ask) / 2
                    else:
                        current_price = last
                else:
                    current_price = Decimal('0')
        except Exception:
            current_price = Decimal('0')
        notional = Decimal('0')
        unrealized = Decimal('0')
        remaining_qty = Decimal('0')
        for lot in buy_lots:
            remaining_qty += lot['qty']
            notional += lot['qty'] * current_price
            unrealized += (current_price - lot['price']) * lot['qty']
        return {
            'exchange': exchange,
            'symbol': symbol,
            'current_price': float(current_price),
            'remaining_qty': float(remaining_qty),
            'notional_value': float(notional),
            'unrealized_pnl': float(unrealized)
        }
    except Exception as e:
        logger.error(f"PnL unrealized error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- Shadow fills backfill (read-only from exchange; writes fills table idempotently) ---
@app.post("/api/v1/fills/backfill/{exchange}")
async def backfill_fills(exchange: str, symbol: Optional[str] = None, limit: int = 200):
    """Fetch recent trades (fills) from exchange-service and insert into trading.fills.
    Idempotent via UNIQUE (exchange, trade_id) partial index.
    """
    if not db_manager:
        raise HTTPException(status_code=503, detail="Database not initialized")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            params = {"limit": min(limit, 500)}
            if symbol:
                params["symbol"] = symbol
            resp = await client.get(f"{exchange_service_url}/api/v1/trading/mytrades/{exchange}", params=params)
            resp.raise_for_status()
            trades = resp.json().get('trades', [])

        inserted = 0
        for t in trades:
            try:
                ex_order_id = str(t.get('order') or t.get('id') or '')
                trade_id = str(t.get('id') or '')
                if not trade_id or not ex_order_id:
                    continue
                sym = t.get('symbol') or symbol or ''
                side = (t.get('side') or '').lower()
                qty = Decimal(str(t.get('amount') or 0))
                price = Decimal(str(t.get('price') or 0))
                fee_obj = t.get('fee') or {}
                fee_amt = Decimal(str(fee_obj.get('cost') or 0))
                fee_asset = str(fee_obj.get('currency') or 'USD')
                ts_ms = t.get('timestamp')
                ts = datetime.utcfromtimestamp(ts_ms/1000) if isinstance(ts_ms, (int, float)) else datetime.utcnow()

                # Lookup local_order_id via order_mappings
                lookup = await db_manager.execute_single_query(
                    "SELECT local_order_id FROM trading.order_mappings WHERE exchange = %s AND exchange_order_id = %s LIMIT 1",
                    (exchange, ex_order_id)
                )
                local_order_id = lookup['local_order_id'] if lookup else None
                if not local_order_id:
                    # Create a placeholder mapping to satisfy NOT NULL while preserving referential intent
                    # Use deterministic UUID derived from exchange_order_id to avoid duplicates
                    import uuid
                    placeholder_local = uuid.uuid5(uuid.NAMESPACE_URL, f"{exchange}:{ex_order_id}")
                    try:
                        await db_manager.execute_query(
                            """
                            INSERT INTO trading.order_mappings (
                                local_order_id, exchange, exchange_order_id, client_order_id, trade_id, symbol, status
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT ON CONSTRAINT idx_order_mappings_exchange_order_unique DO NOTHING
                            """,
                            (
                                str(placeholder_local), exchange, ex_order_id, None, None, sym, 'unknown'
                            )
                        )
                        local_order_id = str(placeholder_local)
                    except Exception:
                        local_order_id = str(placeholder_local)

                # Insert fill idempotently
                insert_sql = """
                    INSERT INTO trading.fills (
                        local_order_id, exchange_order_id, exchange, symbol, side,
                        qty, price, fee, fee_asset, trade_id, timestamp
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT ON CONSTRAINT fills_exchange_trade_unique DO NOTHING
                """
                await db_manager.execute_query(insert_sql, (
                    local_order_id, ex_order_id, exchange, sym, side,
                    str(qty), str(price), str(fee_amt), fee_asset, trade_id, ts
                ))
                inserted += 1
            except Exception as ie:
                logger.warning(f"Skip fill insert due to error: {ie}")

        return {"exchange": exchange, "symbol": symbol, "inserted": inserted, "total_seen": len(trades)}
    except Exception as e:
        logger.error(f"Backfill fills error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Enhanced Trailing Stop Endpoints

@app.post("/api/v1/trailing-stops", status_code=201)
async def create_trailing_stop(trailing_stop_data: dict):
    """Create a new trailing stop record"""
    try:
        insert_query = """
            INSERT INTO trading.trailing_stops (
                trade_id, exchange, pair, trailing_enabled, trailing_trigger_percentage,
                trailing_step_percentage, max_trail_distance_percentage, entry_price,
                current_price, highest_price_seen, lowest_price_seen, position_side,
                profit_protection_enabled, profit_lock_percentage
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            ) RETURNING id
        """
        
        result = await db_manager.execute_single_query(insert_query, (
            trailing_stop_data['trade_id'],
            trailing_stop_data['exchange'],
            trailing_stop_data['pair'],
            trailing_stop_data.get('trailing_enabled', True),
            trailing_stop_data.get('trailing_trigger_percentage', 0.035),
            trailing_stop_data.get('trailing_step_percentage', 0.005),
            trailing_stop_data.get('max_trail_distance_percentage', 0.025),
            trailing_stop_data['entry_price'],
            trailing_stop_data['current_price'],
            trailing_stop_data.get('highest_price_seen', trailing_stop_data['entry_price']),
            trailing_stop_data.get('lowest_price_seen', trailing_stop_data['entry_price']),
            trailing_stop_data.get('position_side', 'long'),
            trailing_stop_data.get('profit_protection_enabled', True),
            trailing_stop_data.get('profit_lock_percentage', 0.025)
        ))
        
        return {"id": result["id"], "trade_id": trailing_stop_data['trade_id']}
        
    except Exception as e:
        logger.error(f"Error creating trailing stop: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/trailing-stops/{trade_id}")
async def get_trailing_stop(trade_id: str):
    """Get trailing stop data for a specific trade"""
    try:
        query = "SELECT * FROM trading.trailing_stops WHERE trade_id = %s"
        result = await db_manager.execute_single_query(query, (trade_id,))
        
        if not result:
            raise HTTPException(status_code=404, detail="Trailing stop not found")
        
        return dict(result)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting trailing stop: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/v1/trailing-stops/{trade_id}/update")
async def update_trailing_stop(trade_id: str, update_data: dict):
    """Update trailing stop based on current price"""
    try:
        current_price = update_data['current_price']
        
        # Call the database function to update trailing stop
        adjustment_query = "SELECT trading.update_trailing_stop(%s, %s)"
        adjustment_made = await db_manager.execute_single_query(adjustment_query, (trade_id, current_price))
        
        # Check if should trigger
        trigger_query = "SELECT trading.should_trigger_trailing_stop(%s, %s)"
        should_trigger = await db_manager.execute_single_query(trigger_query, (trade_id, current_price))
        
        return {
            "adjustment_made": adjustment_made["update_trailing_stop"] if adjustment_made else False,
            "should_trigger": should_trigger["should_trigger_trailing_stop"] if should_trigger else False,
            "current_price": current_price,
            "trade_id": trade_id
        }
        
    except Exception as e:
        logger.error(f"Error updating trailing stop: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/v1/trailing-stops/{trade_id}/activate")
async def activate_trailing_stop(trade_id: str):
    """Activate trailing stop for a trade"""
    try:
        update_query = """
            UPDATE trading.trailing_stops 
            SET is_active = TRUE, updated_at = CURRENT_TIMESTAMP
            WHERE trade_id = %s
        """
        await db_manager.execute_query(update_query, (trade_id,))
        
        return {"trade_id": trade_id, "status": "activated"}
        
    except Exception as e:
        logger.error(f"Error activating trailing stop: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/v1/trailing-stops/{trade_id}/deactivate")
async def deactivate_trailing_stop(trade_id: str):
    """Deactivate trailing stop for a trade"""
    try:
        update_query = """
            UPDATE trading.trailing_stops 
            SET is_active = FALSE, updated_at = CURRENT_TIMESTAMP
            WHERE trade_id = %s
        """
        await db_manager.execute_query(update_query, (trade_id,))
        
        return {"trade_id": trade_id, "status": "deactivated"}
        
    except Exception as e:
        logger.error(f"Error deactivating trailing stop: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/trailing-stops")
async def get_all_active_trailing_stops():
    """Get all active trailing stops"""
    try:
        query = "SELECT * FROM trading.active_trailing_stops"
        results = await db_manager.execute_query(query)
        
        trailing_stops = [dict(row) for row in results] if results else []
        
        return {
            "trailing_stops": trailing_stops,
            "count": len(trailing_stops)
        }
        
    except Exception as e:
        logger.error(f"Error getting active trailing stops: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8002,
        reload=True,
        log_level="info"
    ) 