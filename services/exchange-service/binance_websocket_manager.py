"""
Unified Binance WebSocket Manager - Version 3.0.0 (Consolidated)
Combines user data stream and websocket integration into a single manager

CONSOLIDATION CHANGES:
- Merged binance_user_data_stream.py functionality
- Merged binance_websocket_integration.py functionality  
- Single unified manager for all Binance WebSocket operations
- Eliminates code duplication and simplifies architecture
"""

import asyncio
import json
import logging
import time
import websockets
import os
from datetime import datetime
from typing import Optional, Dict, Any, Callable, List
from dataclasses import dataclass, asdict
import aiohttp
from fastapi import APIRouter, HTTPException
from listen_key_manager import ListenKeyManager
from connection_manager import ConnectionManager, ConnectionState
from error_handlers import ErrorHandler, ErrorCategory, ErrorSeverity, RecoveryManager, ErrorAction
from event_processors import ExecutionReportProcessor, AccountUpdateProcessor

logger = logging.getLogger(__name__)

@dataclass
class ExecutionReport:
    """Structured representation of Binance executionReport event"""
    event_type: str              # "executionReport"
    event_time: int              # Event time in milliseconds
    symbol: str                  # Trading symbol (e.g., "ETHUSDC")
    client_order_id: str         # Client order ID
    side: str                    # "BUY" or "SELL"
    order_type: str              # "MARKET", "LIMIT", etc.
    time_in_force: str           # "GTC", "IOC", "FOK"
    order_quantity: float        # Original order quantity
    order_price: float           # Original order price
    execution_type: str          # "NEW", "CANCELED", "REPLACED", "REJECTED", "TRADE", "EXPIRED"
    order_status: str            # "NEW", "PARTIALLY_FILLED", "FILLED", "CANCELED", "REJECTED", "EXPIRED"
    order_id: int                # Exchange order ID
    last_executed_quantity: float # Last executed quantity
    cumulative_filled_quantity: float # Total filled quantity
    last_executed_price: float   # Last execution price
    commission_amount: float     # Fee amount
    commission_asset: str        # Fee currency
    transaction_time: int        # Transaction time in milliseconds
    trade_id: int                # Trade ID
    is_maker: bool               # True if maker, False if taker
    order_creation_time: int     # Order creation time in milliseconds
    cumulative_quote_qty: float  # Total quote quantity transacted
    last_quote_qty: float        # Last quote quantity transacted

    @classmethod
    def from_binance_event(cls, event: Dict[str, Any]) -> 'ExecutionReport':
        """Create ExecutionReport from Binance WebSocket event"""
        return cls(
            event_type=event.get('e', ''),
            event_time=int(event.get('E', 0)),
            symbol=event.get('s', ''),
            client_order_id=event.get('c', ''),
            side=event.get('S', ''),
            order_type=event.get('o', ''),
            time_in_force=event.get('f', ''),
            order_quantity=float(event.get('q', 0)),
            order_price=float(event.get('p', 0)),
            execution_type=event.get('x', ''),
            order_status=event.get('X', ''),
            order_id=int(event.get('i', 0)),
            last_executed_quantity=float(event.get('l', 0)),
            cumulative_filled_quantity=float(event.get('z', 0)),
            last_executed_price=float(event.get('L', 0)),
            commission_amount=float(event.get('n', 0)),
            commission_asset=event.get('N', ''),
            transaction_time=int(event.get('T', 0)),
            trade_id=int(event.get('t', 0)),
            is_maker=event.get('m', False),
            order_creation_time=int(event.get('O', 0)),
            cumulative_quote_qty=float(event.get('Z', 0)),
            last_quote_qty=float(event.get('Y', 0))
        )

class UnifiedBinanceWebSocketManager:
    """
    CONSOLIDATED: Single manager for all Binance WebSocket operations
    
    Combines functionality from:
    - BinanceUserDataStreamManager (real-time order execution)
    - BinanceWebSocketIntegration (service integration)
    
    Benefits:
    - Single connection management
    - Unified error handling
    - Simplified event processing
    - Reduced code duplication
    """
    
    def __init__(self):
        # Configuration (from integration)
        self.enabled = os.getenv("BINANCE_ENABLE_USER_DATA_STREAM", "false").lower() == "true"
        self.api_key = os.getenv("BINANCE_API_KEY")
        self.api_secret = os.getenv("BINANCE_API_SECRET")
        
        # Core components (from user data stream)
        self.listen_key_manager = ListenKeyManager(self.api_key, self.api_secret)
        self.connection_manager = ConnectionManager()
        self.error_handler = ErrorHandler()
        self.recovery_manager = RecoveryManager(max_retries=3, base_delay=1.0)
        
        # Event processors (from integration)
        self.execution_processor: Optional[ExecutionReportProcessor] = None
        self.account_processor: Optional[AccountUpdateProcessor] = None
        
        # State management
        self.websocket: Optional[websockets.WebSocketServerProtocol] = None
        self.is_running = False
        self.is_initialized = False
        self.current_listen_key: Optional[str] = None
        
        # Metrics
        self.metrics = {
            'connection_attempts': 0,
            'successful_connections': 0,
            'messages_received': 0,
            'execution_reports_processed': 0,
            'account_updates_processed': 0,
            'connection_errors': 0,
            'processing_errors': 0,
            'reconnections': 0,
            'uptime_start': None
        }
        
        logger.info(f"🔌 Unified Binance WebSocket Manager initialized (enabled: {self.enabled})")
    
    async def initialize(self) -> bool:
        """Initialize the unified WebSocket manager"""
        if not self.enabled:
            logger.info("🔌 Binance User Data Stream disabled by configuration")
            return True
            
        if not self.api_key or not self.api_secret:
            logger.error("❌ Binance API credentials not configured for User Data Stream")
            return False
        
        try:
            logger.info("🚀 Initializing Unified Binance WebSocket Manager")
            
            # Initialize event processors
            self.execution_processor = ExecutionReportProcessor()
            self.account_processor = AccountUpdateProcessor()
            
            # Initialize connection components
            await self.listen_key_manager.initialize()
            
            self.is_initialized = True
            logger.info("✅ Unified Binance WebSocket Manager initialized successfully")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to initialize Unified Binance WebSocket Manager: {e}")
            return False
    
    async def start(self) -> bool:
        """Start the unified WebSocket connection"""
        if not self.is_initialized:
            logger.error("❌ Manager not initialized - call initialize() first")
            return False
        
        if self.is_running:
            logger.warning("⚠️ WebSocket manager already running")
            return True
        
        try:
            # Get listen key
            self.current_listen_key = await self.listen_key_manager.get_listen_key()
            if not self.current_listen_key:
                logger.error("❌ Failed to obtain Binance listen key")
                return False
            
            # Start connection monitoring
            self.is_running = True
            self.metrics['uptime_start'] = datetime.utcnow()
            
            # Start main connection loop
            asyncio.create_task(self._connection_loop())
            
            # Start maintenance tasks
            asyncio.create_task(self._listen_key_keep_alive())
            
            logger.info("✅ Unified Binance WebSocket Manager started successfully")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to start Unified Binance WebSocket Manager: {e}")
            self.is_running = False
            return False
    
    async def stop(self):
        """Stop the unified WebSocket manager"""
        logger.info("🛑 Stopping Unified Binance WebSocket Manager")
        
        self.is_running = False
        
        # Close WebSocket connection
        if self.websocket:
            await self.websocket.close()
            self.websocket = None
        
        # Close listen key
        if self.current_listen_key:
            await self.listen_key_manager.close_listen_key(self.current_listen_key)
            self.current_listen_key = None
        
        logger.info("✅ Unified Binance WebSocket Manager stopped")
    
    async def _connection_loop(self):
        """Main connection loop with automatic reconnection"""
        while self.is_running:
            try:
                await self._connect_and_listen()
            except Exception as e:
                logger.error(f"❌ Connection loop error: {e}")
                self.metrics['connection_errors'] += 1
                
                # Wait before reconnecting
                await asyncio.sleep(5)
                
                if self.is_running:
                    self.metrics['reconnections'] += 1
                    logger.info("🔄 Attempting to reconnect...")
    
    async def _connect_and_listen(self):
        """Connect to WebSocket and listen for messages"""
        if not self.current_listen_key:
            logger.error("❌ No listen key available for connection")
            return
        
        ws_url = f"wss://stream.binance.com:9443/ws/{self.current_listen_key}"
        
        self.metrics['connection_attempts'] += 1
        logger.info(f"🔌 Connecting to Binance User Data Stream...")
        
        try:
            async with websockets.connect(
                ws_url,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=10
            ) as websocket:
                self.websocket = websocket
                self.metrics['successful_connections'] += 1
                self.connection_manager.set_state(ConnectionState.CONNECTED)
                
                logger.info("✅ Connected to Binance User Data Stream")
                
                # Listen for messages
                async for message in websocket:
                    if not self.is_running:
                        break
                    
                    await self._handle_message(message)
                    
        except websockets.exceptions.ConnectionClosed as e:
            logger.warning(f"🔌 WebSocket connection closed: {e}")
            self.connection_manager.set_state(ConnectionState.DISCONNECTED)
        except Exception as e:
            logger.error(f"❌ WebSocket connection error: {e}")
            self.connection_manager.set_state(ConnectionState.ERROR)
            raise
        finally:
            self.websocket = None
    
    async def _handle_message(self, message: str):
        """Handle incoming WebSocket message"""
        try:
            self.metrics['messages_received'] += 1
            data = json.loads(message)
            
            event_type = data.get('e')
            
            if event_type == 'executionReport':
                await self._handle_execution_report(data)
            elif event_type == 'outboundAccountInfo':
                await self._handle_account_update(data)
            elif event_type == 'balanceUpdate':
                await self._handle_balance_update(data)
            else:
                logger.debug(f"📋 Unhandled event type: {event_type}")
                
        except Exception as e:
            logger.error(f"❌ Error handling message: {e}")
            self.metrics['processing_errors'] += 1
    
    async def _handle_execution_report(self, data: Dict[str, Any]):
        """Handle execution report event"""
        try:
            self.metrics['execution_reports_processed'] += 1
            
            execution_report = ExecutionReport.from_binance_event(data)
            
            if self.execution_processor:
                await self.execution_processor.process_execution_report(execution_report)
            else:
                logger.warning("⚠️ No execution processor configured")
                
        except Exception as e:
            logger.error(f"❌ Error handling execution report: {e}")
    
    async def _handle_account_update(self, data: Dict[str, Any]):
        """Handle account update event"""
        try:
            self.metrics['account_updates_processed'] += 1
            
            if self.account_processor:
                await self.account_processor.process_position_update(data)
            else:
                logger.debug("📋 No account processor configured")
                
        except Exception as e:
            logger.error(f"❌ Error handling account update: {e}")
    
    async def _handle_balance_update(self, data: Dict[str, Any]):
        """Handle balance update event"""
        try:
            if self.account_processor:
                await self.account_processor.process_balance_update(data)
            else:
                logger.debug("📋 No account processor configured")
                
        except Exception as e:
            logger.error(f"❌ Error handling balance update: {e}")
    
    async def _listen_key_keep_alive(self):
        """Keep listen key alive with periodic refresh"""
        while self.is_running and self.current_listen_key:
            try:
                # Refresh every 30 minutes (Binance requires every 60 minutes)
                await asyncio.sleep(30 * 60)
                
                if self.is_running:
                    success = await self.listen_key_manager.keep_alive(self.current_listen_key)
                    if success:
                        logger.debug("🔄 Listen key refreshed successfully")
                    else:
                        logger.error("❌ Failed to refresh listen key")
                        
            except Exception as e:
                logger.error(f"❌ Error in listen key keep-alive: {e}")
    
    def get_connection_status(self) -> Dict[str, Any]:
        """Get current connection status and metrics"""
        uptime_seconds = 0
        if self.metrics['uptime_start']:
            uptime_seconds = (datetime.utcnow() - self.metrics['uptime_start']).total_seconds()
        
        return {
            'enabled': self.enabled,
            'initialized': self.is_initialized,
            'running': self.is_running,
            'connected': self.connection_manager.get_state() == ConnectionState.CONNECTED,
            'connection_state': self.connection_manager.get_state().value,
            'uptime_seconds': uptime_seconds,
            'metrics': {
                **self.metrics,
                'uptime_seconds': uptime_seconds,
                'success_rate': (
                    self.metrics['successful_connections'] / max(1, self.metrics['connection_attempts'])
                ),
                'message_processing_rate': (
                    self.metrics['messages_received'] / max(1, uptime_seconds)
                )
            }
        }

# CONSOLIDATED API ROUTER
def create_binance_websocket_router() -> APIRouter:
    """Create API router for unified Binance WebSocket management"""
    router = APIRouter()
    manager = UnifiedBinanceWebSocketManager()
    
    @router.get("/binance/websocket/status")
    async def get_websocket_status():
        """Get WebSocket connection status"""
        return manager.get_connection_status()
    
    @router.post("/binance/websocket/initialize")
    async def initialize_websocket():
        """Initialize WebSocket manager"""
        success = await manager.initialize()
        if success:
            return {"status": "initialized"}
        else:
            raise HTTPException(status_code=500, detail="Failed to initialize WebSocket manager")
    
    @router.post("/binance/websocket/start")
    async def start_websocket():
        """Start WebSocket connection"""
        success = await manager.start()
        if success:
            return {"status": "started"}
        else:
            raise HTTPException(status_code=500, detail="Failed to start WebSocket connection")
    
    @router.post("/binance/websocket/stop")
    async def stop_websocket():
        """Stop WebSocket connection"""
        await manager.stop()
        return {"status": "stopped"}
    
    return router

# Global instance for import compatibility
unified_binance_manager = UnifiedBinanceWebSocketManager()

# CONSOLIDATION NOTICE
logger.info("✅ CONSOLIDATION: Unified Binance WebSocket Manager loaded")
logger.info("✅ CONSOLIDATION: Replaces binance_user_data_stream.py + binance_websocket_integration.py")
logger.info("✅ CONSOLIDATION: Single manager for all Binance WebSocket operations")