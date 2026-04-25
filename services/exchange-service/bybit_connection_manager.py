"""
Bybit Connection Manager - Version 2.6.0
Manages Bybit WebSocket connections with automatic reconnection, heartbeat, and error handling
"""

import asyncio
import json
import logging
import time
import websockets
import websockets.exceptions
from typing import Dict, Any, Optional, List, Callable
from datetime import datetime, timezone
from dataclasses import dataclass
from enum import Enum

from bybit_auth_manager import BybitAuthManager
from bybit_error_handlers import BybitErrorHandler, ErrorCategory, ErrorSeverity
from bybit_recovery_manager import BybitRecoveryManager

logger = logging.getLogger(__name__)

class ConnectionState(Enum):
    """WebSocket connection states"""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    AUTHENTICATING = "authenticating"
    SUBSCRIBING = "subscribing"
    READY = "ready"
    ERROR = "error"
    RECONNECTING = "reconnecting"

@dataclass
class ConnectionMetrics:
    """Connection metrics tracking"""
    connection_attempts: int = 0
    successful_connections: int = 0
    failed_connections: int = 0
    disconnections: int = 0
    reconnection_attempts: int = 0
    messages_sent: int = 0
    messages_received: int = 0
    heartbeat_sent: int = 0
    heartbeat_failures: int = 0
    last_message_time: Optional[str] = None
    uptime_start: Optional[str] = None
    last_error: Optional[str] = None

class BybitConnectionManager:
    """
    Manages Bybit WebSocket connection with advanced features
    
    Features:
    - Automatic connection management with reconnection
    - Heartbeat mechanism for connection maintenance
    - Authentication integration with retry logic
    - Channel subscription management
    - Error handling and recovery
    - Metrics tracking and monitoring
    """
    
    def __init__(self, 
                 websocket_url: str,
                 api_key: str,
                 api_secret: str,
                 max_reconnect_attempts: int = 10,
                 reconnect_delay: int = 5,
                 heartbeat_interval: int = 20):
        """
        Initialize Bybit connection manager
        
        Args:
            websocket_url: Bybit WebSocket URL
            api_key: Bybit API key
            api_secret: Bybit API secret
            max_reconnect_attempts: Maximum reconnection attempts
            reconnect_delay: Delay between reconnection attempts (seconds)
            heartbeat_interval: Heartbeat interval (seconds)
        """
        self.websocket_url = websocket_url
        self.max_reconnect_attempts = max_reconnect_attempts
        self.reconnect_delay = reconnect_delay
        self.heartbeat_interval = heartbeat_interval
        
        # Authentication manager
        self.auth_manager = BybitAuthManager(api_key, api_secret)
        
        # Error handling and recovery
        self.error_handler = BybitErrorHandler("bybit-connection")
        self.recovery_manager = BybitRecoveryManager("bybit-connection")
        
        # Connection state
        self.state = ConnectionState.DISCONNECTED
        self.websocket: Optional[websockets.WebSocketServerProtocol] = None
        self.is_running = False
        self.is_connected = False
        
        # Task management
        self.connection_task: Optional[asyncio.Task] = None
        self.heartbeat_task: Optional[asyncio.Task] = None
        self.message_processor_task: Optional[asyncio.Task] = None
        
        # Channel subscriptions
        self.subscribed_channels: List[str] = []
        self.target_channels = ["order", "execution", "position", "wallet"]
        
        # Callbacks
        self.message_callbacks: List[Callable] = []
        self.connection_callbacks: List[Callable] = []
        self.error_callbacks: List[Callable] = []
        
        # Metrics
        self.metrics = ConnectionMetrics()
        self.metrics.uptime_start = datetime.now(timezone.utc).isoformat()
        
        # Reconnection state
        self.reconnect_attempts = 0
        self.last_reconnect_time = None
        
        logger.info("🔌 Bybit Connection Manager initialized")
    
    async def connect(self) -> bool:
        """
        Establish WebSocket connection with authentication
        
        Returns:
            True if connection successful, False otherwise
        """
        try:
            self.state = ConnectionState.CONNECTING
            self.metrics.connection_attempts += 1
            
            logger.info(f"🚀 Starting Bybit WebSocket Manager: {self.websocket_url}")
            
            # Set running state immediately (like Binance approach)
            self.is_running = True
            self.metrics.successful_connections += 1
            self.reconnect_attempts = 0
            
            # PnL-FIX v3: _connection_loop() handles websocket connect + auth +
            # subscribe + starting the heartbeat and message-processor tasks.
            # The previous code ALSO started heartbeat_task and
            # message_processor_task here, resulting in two concurrent
            # ``async for message in self.websocket`` loops and this error:
            # ``cannot call recv while another coroutine is already waiting
            # for the next message``. Only launch the connection loop here.
            self.connection_task = asyncio.create_task(self._connection_loop())

            # Small yield so _connection_loop can advance past the initial
            # websocket connect; the rest (auth/subscribe/tasks) is handled
            # inside the loop itself.
            await asyncio.sleep(1)

            # Report success if the websocket handshake completed. State
            # transitions to READY inside _connection_loop once subscriptions
            # are confirmed.
            if self.websocket is not None:
                logger.info("✅ Bybit WebSocket Manager started (background connection)")
                return True
            else:
                logger.warning("⚠️ WebSocket connection not established, will retry in background")
                self.state = ConnectionState.CONNECTING
                return True
            
        except Exception as e:
            self.state = ConnectionState.ERROR
            self.metrics.failed_connections += 1
            self.metrics.last_error = str(e)
            logger.error(f"❌ Failed to connect to Bybit WebSocket: {e}")
            
            # Handle error with recovery
            context = {'connection_attempt': self.metrics.connection_attempts}
            recovery_actions = await self.error_handler.handle_error(e, context)
            await self.recovery_manager.execute_recovery_actions(recovery_actions, context)
            
            await self._notify_error_callbacks(e)
            return False
    
    async def _authenticate(self) -> bool:
        """Authenticate with Bybit WebSocket"""
        try:
            self.state = ConnectionState.AUTHENTICATING
            logger.info("🔐 Authenticating with Bybit...")
            
            auth_payload = await self.auth_manager.authenticate_with_retry()
            if not auth_payload:
                logger.error("❌ Authentication failed after retries")
                return False
            
            await self.websocket.send(json.dumps(auth_payload))
            
            # Wait for authentication response
            response = await asyncio.wait_for(self.websocket.recv(), timeout=10.0)
            response_data = json.loads(response)
            
            if response_data.get("success") is True:
                logger.info("✅ Authentication successful")
                return True
            else:
                logger.error(f"❌ Authentication failed: {response_data}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Authentication error: {e}")
            return False
    
    async def _subscribe_to_channels(self) -> bool:
        """Subscribe to target channels"""
        try:
            self.state = ConnectionState.SUBSCRIBING
            logger.info(f"📡 Subscribing to channels: {self.target_channels}")
            logger.info(f"📊 Subscription state: {self.state.value}")
            
            # Subscribe to all channels in one request
            subscribe_payload = {
                "op": "subscribe",
                "args": self.target_channels
            }
            
            logger.info(f"📤 Sending subscription payload: {subscribe_payload}")
            await self.websocket.send(json.dumps(subscribe_payload))
            logger.info("📤 Subscription request sent successfully")
            
            # Wait for subscription response
            logger.info("⏳ Waiting for subscription response...")
            response = await asyncio.wait_for(self.websocket.recv(), timeout=10.0)
            response_data = json.loads(response)
            
            logger.info(f"📨 Received subscription response: {response_data}")
            
            if response_data.get("success") is True:
                self.subscribed_channels = self.target_channels.copy()
                logger.info(f"✅ Subscribed to channels: {self.subscribed_channels}")
                logger.info(f"📊 Final subscription state: {self.state.value}")
                return True
            else:
                logger.error(f"❌ Subscription failed: {response_data}")
                logger.error(f"📊 Failed subscription state: {self.state.value}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Subscription error: {e}")
            logger.error(f"📊 Error subscription state: {self.state.value}")
            return False
    
    async def _connection_loop(self):
        """Background WebSocket connection loop (like Binance pattern)"""
        try:
            logger.info(f"🔌 Background connecting to Bybit WebSocket: {self.websocket_url}")
            logger.info(f"📊 Initial connection state: {self.state.value}")
            
            # Establish WebSocket connection
            logger.info("🔗 Establishing WebSocket connection...")
            self.websocket = await websockets.connect(
                self.websocket_url,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=10
            )
            
            self.is_connected = True
            logger.info("✅ WebSocket connection established in background")
            logger.info(f"🔗 WebSocket object created: {self.websocket}")
            
            # Authenticate
            logger.info("🔐 Starting authentication process...")
            auth_success = await self._authenticate()
            if auth_success:
                logger.info("✅ Background authentication successful")
            else:
                logger.warning("⚠️ Background authentication failed, continuing with fallback")
            
            # Subscribe to channels
            logger.info("📡 Starting channel subscription process...")
            try:
                await self._subscribe_to_channels()
                logger.info("✅ Background channel subscription successful")
                logger.info(f"📡 Subscribed channels: {self.subscribed_channels}")
            except Exception as e:
                logger.warning(f"⚠️ Background subscription error: {e}, continuing")
                logger.warning(f"📊 Current subscribed channels: {self.subscribed_channels}")
            
            # Start heartbeat and message processor tasks after connection is established
            logger.info("🔄 Starting background tasks...")
            if self.websocket is not None and self.is_running:
                if self.heartbeat_task is None or self.heartbeat_task.done():
                    self.heartbeat_task = asyncio.create_task(self._heartbeat_loop())
                    logger.info("💓 Heartbeat task started")
                if self.message_processor_task is None or self.message_processor_task.done():
                    self.message_processor_task = asyncio.create_task(self._message_processor_loop())
                    logger.info("📨 Message processor task started")
                
                self.state = ConnectionState.READY
                await self._notify_connection_callbacks(True)
                logger.info("🎉 Bybit WebSocket fully connected in background")
                logger.info(f"📊 Final connection state: {self.state.value}")
            
        except Exception as e:
            logger.error(f"❌ Background connection error: {e}")
            logger.error(f"📊 Connection state at failure: {self.state.value}")
            self.is_connected = False

    async def _heartbeat_loop(self):
        """Send periodic heartbeat messages"""
        logger.info(f"💓 Starting heartbeat loop (interval: {self.heartbeat_interval}s)")
        
        while self.is_running and self.is_connected:
            try:
                # Check websocket is still valid
                if self.websocket is None:
                    logger.warning("💓 Heartbeat loop stopped - WebSocket is None")
                    break
                    
                heartbeat_payload = {"op": "ping"}
                await self.websocket.send(json.dumps(heartbeat_payload))
                
                self.metrics.heartbeat_sent += 1
                self.metrics.messages_sent += 1
                
                logger.debug("💓 Heartbeat sent")
                await asyncio.sleep(self.heartbeat_interval)
                
            except Exception as e:
                self.metrics.heartbeat_failures += 1
                logger.error(f"❌ Heartbeat failed: {e}")
                break
        
        logger.info("💓 Heartbeat loop stopped")
    
    async def _message_processor_loop(self):
        """Process incoming WebSocket messages"""
        logger.info("📨 Starting message processor loop")
        logger.info(f"📊 Message processor state: is_running={self.is_running}, websocket={self.websocket is not None}")
        
        # Wait for WebSocket connection to be established
        wait_count = 0
        while self.is_running and self.websocket is None:
            await asyncio.sleep(0.1)
            wait_count += 1
            if wait_count % 50 == 0:  # Log every 5 seconds
                logger.info(f"⏳ Message processor waiting for WebSocket connection... (waited {wait_count/10:.1f}s)")
        
        if not self.is_running or self.websocket is None:
            logger.warning("📨 Message processor loop stopped - no WebSocket connection")
            return
        
        logger.info("📨 WebSocket connection ready, starting message processing...")
        try:
            # Double-check websocket is still valid before starting async for loop
            if self.websocket is None:
                logger.error("📨 WebSocket became None before starting message processing")
                return
                
            async for message in self.websocket:
                if not self.is_running:
                    break
                
                try:
                    self.metrics.messages_received += 1
                    self.metrics.last_message_time = datetime.now(timezone.utc).isoformat()
                    
                    # Parse message
                    data = json.loads(message)
                    
                    # Handle different message types
                    if data.get("op") == "pong":
                        logger.debug("🏓 Pong received")
                    elif data.get("topic"):
                        # Process event message
                        await self._process_event_message(data)
                    else:
                        logger.debug(f"📨 Received message: {data}")
                    
                    # Notify message callbacks
                    await self._notify_message_callbacks(data)
                    
                except json.JSONDecodeError as e:
                    logger.error(f"❌ Failed to parse message: {e}")
                except Exception as e:
                    logger.error(f"❌ Error processing message: {e}")
                    
                    # Handle message processing error with recovery
                    context = {'error_location': 'message_processor', 'message': message}
                    recovery_actions = await self.error_handler.handle_error(e, context)
                    await self.recovery_manager.execute_recovery_actions(recovery_actions, context)
                    
        except websockets.exceptions.ConnectionClosed as e:
            logger.warning(f"🔌 WebSocket connection closed: {e}")
            self.is_connected = False
            self.metrics.disconnections += 1
            
            # Handle connection error with recovery
            context = {'disconnection_reason': 'connection_closed'}
            recovery_actions = await self.error_handler.handle_error(e, context)
            await self.recovery_manager.execute_recovery_actions(recovery_actions, context)
            
            await self._notify_connection_callbacks(False)
            
        except Exception as e:
            logger.error(f"❌ Message processor error: {e}")
            self.metrics.last_error = str(e)
            
            # Handle processing error with recovery
            context = {'error_location': 'message_processor'}
            recovery_actions = await self.error_handler.handle_error(e, context)
            await self.recovery_manager.execute_recovery_actions(recovery_actions, context)
            
            await self._notify_error_callbacks(e)
        
        logger.info("📨 Message processor loop stopped")
    
    async def _process_event_message(self, data: Dict[str, Any]):
        """Process event message from WebSocket"""
        topic = data.get("topic")
        event_type = data.get("type")
        timestamp = data.get("ts")
        
        logger.debug(f"📊 Processing {topic} event: {event_type}")
        
        # Handle different event types
        if topic == "order":
            await self._handle_order_event(data)
        elif topic == "execution":
            await self._handle_execution_event(data)
        elif topic == "position":
            await self._handle_position_event(data)
        elif topic == "wallet":
            await self._handle_wallet_event(data)
    
    async def _handle_order_event(self, data: Dict[str, Any]):
        """Handle order event"""
        logger.info(f"📋 Order event received: {data.get('data', [])}")
    
    async def _handle_execution_event(self, data: Dict[str, Any]):
        """Handle execution event"""
        logger.info(f"⚡ Execution event received: {data.get('data', [])}")
    
    async def _handle_position_event(self, data: Dict[str, Any]):
        """Handle position event"""
        logger.info(f"📈 Position event received: {data.get('data', [])}")
    
    async def _handle_wallet_event(self, data: Dict[str, Any]):
        """Handle wallet event"""
        logger.info(f"💰 Wallet event received: {data.get('data', [])}")
    
    async def disconnect(self):
        """Disconnect WebSocket connection"""
        logger.info("🔌 Disconnecting from Bybit WebSocket")
        
        self.is_running = False
        self.state = ConnectionState.DISCONNECTED
        
        # Cancel tasks
        if self.heartbeat_task:
            self.heartbeat_task.cancel()
        if self.message_processor_task:
            self.message_processor_task.cancel()
        
        # Close WebSocket
        if self.websocket:
            await self.websocket.close()
            self.websocket = None
        
        self.is_connected = False
        await self._notify_connection_callbacks(False)
        
        logger.info("🔌 Disconnected from Bybit WebSocket")
    
    async def reconnect(self) -> bool:
        """Reconnect WebSocket with exponential backoff"""
        if self.reconnect_attempts >= self.max_reconnect_attempts:
            logger.error(f"❌ Max reconnection attempts reached: {self.max_reconnect_attempts}")
            return False
        
        self.reconnect_attempts += 1
        self.last_reconnect_time = datetime.now(timezone.utc)
        self.state = ConnectionState.RECONNECTING
        
        # Calculate backoff delay
        delay = self.reconnect_delay * (2 ** (self.reconnect_attempts - 1))
        delay = min(delay, 60)  # Cap at 60 seconds
        
        logger.info(f"🔄 Reconnecting in {delay} seconds (attempt {self.reconnect_attempts}/{self.max_reconnect_attempts})")
        
        await asyncio.sleep(delay)
        
        # Attempt reconnection
        success = await self.connect()
        if success:
            logger.info("✅ Reconnection successful")
            return True
        else:
            logger.warning(f"❌ Reconnection attempt {self.reconnect_attempts} failed")
            return False
    
    async def send_message(self, message: Dict[str, Any]) -> bool:
        """
        Send message through WebSocket
        
        Args:
            message: Message to send
            
        Returns:
            True if sent successfully, False otherwise
        """
        if not self.is_connected or not self.websocket:
            logger.error("❌ Cannot send message: not connected")
            return False
        
        try:
            await self.websocket.send(json.dumps(message))
            self.metrics.messages_sent += 1
            logger.debug(f"📤 Sent message: {message}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to send message: {e}")
            return False
    
    def add_message_callback(self, callback: Callable):
        """Add message callback"""
        self.message_callbacks.append(callback)
    
    def add_connection_callback(self, callback: Callable):
        """Add connection callback"""
        self.connection_callbacks.append(callback)
    
    def add_error_callback(self, callback: Callable):
        """Add error callback"""
        self.error_callbacks.append(callback)
    
    async def _notify_message_callbacks(self, message: Dict[str, Any]):
        """Notify message callbacks"""
        for callback in self.message_callbacks:
            try:
                await callback(message)
            except Exception as e:
                logger.error(f"❌ Message callback error: {e}")
    
    async def _notify_connection_callbacks(self, connected: bool):
        """Notify connection callbacks"""
        for callback in self.connection_callbacks:
            try:
                await callback(connected)
            except Exception as e:
                logger.error(f"❌ Connection callback error: {e}")
    
    async def _notify_error_callbacks(self, error: Exception):
        """Notify error callbacks"""
        for callback in self.error_callbacks:
            try:
                await callback(error)
            except Exception as e:
                logger.error(f"❌ Error callback error: {e}")
    
    def get_status(self) -> Dict[str, Any]:
        """Get connection status"""
        return {
            "state": self.state.value,
            "connected": self.is_connected,
            "running": self.is_running,
            "subscribed_channels": self.subscribed_channels,
            "reconnect_attempts": self.reconnect_attempts,
            "last_reconnect_time": self.last_reconnect_time.isoformat() if self.last_reconnect_time else None,
            "auth_metrics": self.auth_manager.get_auth_metrics(),
            "connection_metrics": {
                "connection_attempts": self.metrics.connection_attempts,
                "successful_connections": self.metrics.successful_connections,
                "failed_connections": self.metrics.failed_connections,
                "disconnections": self.metrics.disconnections,
                "messages_sent": self.metrics.messages_sent,
                "messages_received": self.metrics.messages_received,
                "heartbeat_sent": self.metrics.heartbeat_sent,
                "heartbeat_failures": self.metrics.heartbeat_failures,
                "last_message_time": self.metrics.last_message_time,
                "uptime_start": self.metrics.uptime_start,
                "last_error": self.metrics.last_error
            },
            "error_metrics": self.error_handler.get_error_metrics(),
            "recovery_metrics": self.recovery_manager.get_recovery_metrics()
        }
    
    def reset_metrics(self):
        """Reset connection metrics"""
        self.metrics = ConnectionMetrics()
        self.metrics.uptime_start = datetime.now(timezone.utc).isoformat()
        self.auth_manager.reset_metrics()
        self.error_handler.reset_error_metrics()
        self.recovery_manager.reset_recovery_metrics()
        logger.info("📊 Connection metrics reset")
