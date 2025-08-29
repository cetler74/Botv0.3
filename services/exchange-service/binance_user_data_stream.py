"""
Binance User Data Stream WebSocket Manager - Version 2.5.0
Real-time order execution tracking via Binance User Data Stream
"""

import asyncio
import json
import logging
import time
import websockets
from datetime import datetime
from typing import Optional, Dict, Any, Callable, List
from dataclasses import dataclass, asdict
import aiohttp
from listen_key_manager import ListenKeyManager
from connection_manager import ConnectionManager, ConnectionState
from error_handlers import ErrorHandler, ErrorCategory, ErrorSeverity, RecoveryManager, ErrorAction

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

class BinanceUserDataStreamManager:
    """
    Manages Binance User Data Stream WebSocket connections for real-time order updates
    
    Features:
    - Automatic connection management with reconnection
    - Listen key lifecycle management
    - Event processing and callback system
    - Health monitoring and metrics
    - Graceful error handling and fallback
    """
    
    def __init__(self, api_key: str, api_secret: str, base_url: str = "https://api.binance.com"):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url
        
        # Listen key manager
        self.listen_key_manager = ListenKeyManager(api_key, api_secret, base_url)
        
        # Advanced error handling and connection management
        self.connection_manager = ConnectionManager("binance-websocket")
        self.error_handler = ErrorHandler("binance-websocket")
        self.recovery_manager = RecoveryManager("binance-websocket")
        
        # Setup recovery actions
        self._setup_recovery_actions()
        
        # WebSocket connection
        self.websocket: Optional[websockets.WebSocketServerProtocol] = None
        self.connection_task: Optional[asyncio.Task] = None
        self.is_running = False
        self.is_connected = False
        
        # Event callbacks
        self.event_callbacks: Dict[str, List[Callable]] = {
            'executionReport': [],
            'outboundAccountPosition': [],
            'balanceUpdate': [],
            'error': [],
            'connection': []
        }
        
        # Configuration
        self.reconnect_interval = 5  # seconds
        self.max_reconnect_attempts = 10
        self.message_queue_size = 1000
        self.ping_interval = 20  # seconds
        
        # Metrics and monitoring
        self.metrics = {
            'connection_attempts': 0,
            'successful_connections': 0,
            'disconnections': 0,
            'messages_received': 0,
            'messages_processed': 0,
            'processing_errors': 0,
            'last_message_time': None,
            'uptime_start': None,
            'reconnect_attempts': 0
        }
        
        # Message processing queue
        self.message_queue = asyncio.Queue(maxsize=self.message_queue_size)
        self.processing_task: Optional[asyncio.Task] = None
    
    def _setup_recovery_actions(self):
        """Setup recovery action handlers"""
        
        async def retry_action(context: Dict[str, Any]) -> bool:
            """Retry the failed operation"""
            logger.info("🔄 Executing retry recovery action")
            await asyncio.sleep(2)  # Brief delay before retry
            return True
        
        async def reconnect_action(context: Dict[str, Any]) -> bool:
            """Reconnect WebSocket connection"""
            logger.info("🔄 Executing reconnect recovery action")
            try:
                if self.websocket and not self.websocket.closed:
                    await self.websocket.close()
                    
                # Connection will be re-established by the connection loop
                return True
            except Exception as e:
                logger.error(f"❌ Reconnect action failed: {e}")
                return False
        
        async def fallback_action(context: Dict[str, Any]) -> bool:
            """Activate fallback mechanism"""
            logger.info("🔄 Executing fallback recovery action")
            # This would trigger REST API fallback in fill-detection service
            # The fallback mechanism is already implemented there
            return True
        
        async def alert_action(context: Dict[str, Any]) -> bool:
            """Send alert about the error"""
            logger.info("📱 Executing alert recovery action")
            # Log critical error for monitoring systems
            logger.critical(f"🚨 Critical error in Binance WebSocket: {context}")
            return True
        
        # Register recovery action handlers
        self.recovery_manager.register_action_handler(ErrorAction.RETRY, retry_action)
        self.recovery_manager.register_action_handler(ErrorAction.RECONNECT, reconnect_action)
        self.recovery_manager.register_action_handler(ErrorAction.FALLBACK, fallback_action)
        self.recovery_manager.register_action_handler(ErrorAction.ALERT, alert_action)
    
    async def start(self) -> bool:
        """Start the User Data Stream manager"""
        try:
            logger.info("🚀 Starting Binance User Data Stream Manager v2.5.0")
            
            # Start listen key manager first
            if not await self.listen_key_manager.start():
                logger.error("❌ Failed to start listen key manager")
                return False
            
            # Start connection
            self.is_running = True
            self.metrics['uptime_start'] = datetime.utcnow().isoformat()
            
            # Start message processing task
            self.processing_task = asyncio.create_task(self._process_messages())
            
            # Start WebSocket connection task
            self.connection_task = asyncio.create_task(self._connection_loop())
            
            logger.info("✅ User Data Stream Manager started successfully")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to start User Data Stream Manager: {e}")
            return False
    
    async def stop(self):
        """Stop the User Data Stream manager and cleanup resources"""
        logger.info("🛑 Stopping Binance User Data Stream Manager")
        
        self.is_running = False
        
        # Stop WebSocket connection
        if self.websocket and not self.websocket.closed:
            await self.websocket.close()
        
        # Cancel tasks
        if self.connection_task and not self.connection_task.done():
            self.connection_task.cancel()
            try:
                await self.connection_task
            except asyncio.CancelledError:
                pass
        
        if self.processing_task and not self.processing_task.done():
            self.processing_task.cancel()
            try:
                await self.processing_task
            except asyncio.CancelledError:
                pass
        
        # Stop listen key manager
        await self.listen_key_manager.stop()
        
        logger.info("✅ User Data Stream Manager stopped")
    
    async def _connection_loop(self):
        """Main connection loop with automatic reconnection"""
        reconnect_attempts = 0
        
        while self.is_running:
            try:
                # Get WebSocket URL from listen key manager
                ws_url = self.listen_key_manager.get_websocket_url()
                if not ws_url:
                    logger.error("❌ No WebSocket URL available, listen key not ready")
                    await asyncio.sleep(5)
                    continue
                
                logger.info(f"🔌 Connecting to Binance User Data Stream: {ws_url[:50]}...")
                self.metrics['connection_attempts'] += 1
                
                # Establish WebSocket connection
                async with websockets.connect(
                    ws_url,
                    ping_interval=self.ping_interval,
                    ping_timeout=10,
                    close_timeout=10
                ) as websocket:
                    self.websocket = websocket
                    self.is_connected = True
                    reconnect_attempts = 0
                    self.metrics['successful_connections'] += 1
                    self.metrics['reconnect_attempts'] = 0
                    
                    logger.info("✅ Connected to Binance User Data Stream")
                    await self._notify_connection_callbacks(True)
                    
                    # Listen for messages
                    async for message in websocket:
                        if not self.is_running:
                            break
                        
                        try:
                            # Add message to processing queue
                            if not self.message_queue.full():
                                await self.message_queue.put(message)
                                self.metrics['messages_received'] += 1
                                self.metrics['last_message_time'] = datetime.utcnow().isoformat()
                            else:
                                logger.warning("⚠️ Message queue full, dropping message")
                        
                        except Exception as e:
                            logger.error(f"❌ Error queuing message: {e}")
                    
            except websockets.exceptions.ConnectionClosed as e:
                logger.warning(f"🔌 WebSocket connection closed: {e}")
                self.is_connected = False
                self.metrics['disconnections'] += 1
                await self._notify_connection_callbacks(False)
                
                # Handle with error handler
                context = {'reconnect_attempts': reconnect_attempts, 'error_type': 'connection_closed'}
                recovery_actions = await self.error_handler.handle_error(e, context, ErrorCategory.NETWORK, ErrorSeverity.MEDIUM)
                await self.recovery_manager.execute_recovery_actions(recovery_actions, context)
                
            except Exception as e:
                logger.error(f"❌ WebSocket connection error: {e}")
                self.is_connected = False
                self.metrics['disconnections'] += 1
                await self._notify_connection_callbacks(False)
                
                # Handle with advanced error handler
                context = {'reconnect_attempts': reconnect_attempts, 'operation': 'websocket_connection'}
                recovery_actions = await self.error_handler.handle_error(e, context)
                await self.recovery_manager.execute_recovery_actions(recovery_actions, context)
            
            finally:
                self.websocket = None
                self.is_connected = False
            
            # Reconnection logic
            if self.is_running:
                reconnect_attempts += 1
                self.metrics['reconnect_attempts'] = reconnect_attempts
                
                if reconnect_attempts <= self.max_reconnect_attempts:
                    wait_time = min(self.reconnect_interval * reconnect_attempts, 60)  # Max 60 seconds
                    logger.info(f"🔄 Reconnecting in {wait_time}s (attempt {reconnect_attempts}/{self.max_reconnect_attempts})")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"❌ Max reconnection attempts reached ({self.max_reconnect_attempts})")
                    await self._notify_error_callbacks("Max reconnection attempts exceeded")
                    break
    
    async def _process_messages(self):
        """Process messages from the queue"""
        logger.info("🔄 Starting message processing task")
        
        while self.is_running:
            try:
                # Get message from queue with timeout
                message = await asyncio.wait_for(
                    self.message_queue.get(), 
                    timeout=1.0
                )
                
                # Process the message
                await self._process_message(message)
                self.metrics['messages_processed'] += 1
                
            except asyncio.TimeoutError:
                # No messages in queue, continue
                continue
            except Exception as e:
                logger.error(f"❌ Error in message processing: {e}")
                self.metrics['processing_errors'] += 1
        
        logger.info("🛑 Message processing task stopped")
    
    async def _process_message(self, message: str):
        """Process a single WebSocket message"""
        try:
            data = json.loads(message)
            event_type = data.get('e')
            
            if event_type == 'executionReport':
                # Process execution report
                execution_report = ExecutionReport.from_binance_event(data)
                await self._notify_execution_callbacks(execution_report)
                
                # Log important execution events
                if execution_report.execution_type in ['TRADE', 'FILLED']:
                    logger.info(f"📊 Order Execution: {execution_report.symbol} {execution_report.side} "
                              f"{execution_report.last_executed_quantity} @ {execution_report.last_executed_price} "
                              f"(Fee: {execution_report.commission_amount} {execution_report.commission_asset})")
            
            elif event_type == 'outboundAccountPosition':
                # Process account position update
                await self._notify_position_callbacks(data)
            
            elif event_type == 'balanceUpdate':
                # Process balance update
                await self._notify_balance_callbacks(data)
            
            else:
                logger.debug(f"📩 Unhandled event type: {event_type}")
        
        except Exception as e:
            logger.error(f"❌ Error processing message: {e}")
            logger.debug(f"Raw message: {message}")
            self.metrics['processing_errors'] += 1
    
    # Event callback management
    def add_execution_callback(self, callback: Callable[[ExecutionReport], None]):
        """Add callback for executionReport events"""
        self.event_callbacks['executionReport'].append(callback)
    
    def add_position_callback(self, callback: Callable[[Dict], None]):
        """Add callback for outboundAccountPosition events"""
        self.event_callbacks['outboundAccountPosition'].append(callback)
    
    def add_balance_callback(self, callback: Callable[[Dict], None]):
        """Add callback for balanceUpdate events"""
        self.event_callbacks['balanceUpdate'].append(callback)
    
    def add_error_callback(self, callback: Callable[[str], None]):
        """Add callback for error events"""
        self.event_callbacks['error'].append(callback)
    
    def add_connection_callback(self, callback: Callable[[bool], None]):
        """Add callback for connection status changes"""
        self.event_callbacks['connection'].append(callback)
    
    # Callback notification methods
    async def _notify_execution_callbacks(self, execution_report: ExecutionReport):
        """Notify all execution report callbacks"""
        for callback in self.event_callbacks['executionReport']:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(execution_report)
                else:
                    callback(execution_report)
            except Exception as e:
                logger.error(f"❌ Error in execution callback: {e}")
    
    async def _notify_position_callbacks(self, data: Dict):
        """Notify all position update callbacks"""
        for callback in self.event_callbacks['outboundAccountPosition']:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(data)
                else:
                    callback(data)
            except Exception as e:
                logger.error(f"❌ Error in position callback: {e}")
    
    async def _notify_balance_callbacks(self, data: Dict):
        """Notify all balance update callbacks"""
        for callback in self.event_callbacks['balanceUpdate']:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(data)
                else:
                    callback(data)
            except Exception as e:
                logger.error(f"❌ Error in balance callback: {e}")
    
    async def _notify_error_callbacks(self, error: str):
        """Notify all error callbacks"""
        for callback in self.event_callbacks['error']:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(error)
                else:
                    callback(error)
            except Exception as e:
                logger.error(f"❌ Error in error callback: {e}")
    
    async def _notify_connection_callbacks(self, connected: bool):
        """Notify all connection status callbacks"""
        for callback in self.event_callbacks['connection']:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(connected)
                else:
                    callback(connected)
            except Exception as e:
                logger.error(f"❌ Error in connection callback: {e}")
    
    # Status and monitoring methods
    def is_healthy(self) -> bool:
        """Check if the User Data Stream is healthy"""
        return (self.is_running and 
                self.is_connected and 
                self.listen_key_manager.is_key_valid() and
                self.connection_manager.is_healthy())
    
    def get_status(self) -> Dict[str, Any]:
        """Get comprehensive status information"""
        return {
            'is_running': self.is_running,
            'is_connected': self.is_connected,
            'is_healthy': self.is_healthy(),
            'listen_key_valid': self.listen_key_manager.is_key_valid(),
            'websocket_url': self.listen_key_manager.get_websocket_url() is not None,
            'message_queue_size': self.message_queue.qsize(),
            'callbacks_registered': sum(len(callbacks) for callbacks in self.event_callbacks.values()),
            'uptime': self.metrics.get('uptime_start'),
            'last_message': self.metrics.get('last_message_time'),
            'connection_manager_status': self.connection_manager.get_status(),
            'error_handler_stats': self.error_handler.get_error_stats(),
            'recovery_manager_stats': self.recovery_manager.get_recovery_stats()
        }
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get detailed metrics for monitoring"""
        listen_key_metrics = self.listen_key_manager.get_metrics()
        return {
            **self.metrics,
            'listen_key_metrics': listen_key_metrics,
            'queue_size': self.message_queue.qsize(),
            'max_queue_size': self.message_queue_size,
            'is_healthy': self.is_healthy(),
            'connection_manager_metrics': self.connection_manager.metrics,
            'error_handler_metrics': self.error_handler.get_error_stats(),
            'recovery_manager_metrics': self.recovery_manager.get_recovery_stats()
        }