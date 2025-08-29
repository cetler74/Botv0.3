"""
Crypto.com Connection Manager - Version 2.6.0
Advanced connection management with circuit breaker pattern and intelligent reconnection
"""

import asyncio
import logging
import websockets
from datetime import datetime, timedelta
from typing import Optional, Callable, Dict, Any, List
from enum import Enum
import json
from cryptocom_error_handlers import CryptocomErrorHandler, CryptocomErrorType

logger = logging.getLogger(__name__)

class ConnectionState(Enum):
    """WebSocket connection states"""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    FAILED = "failed"

class CircuitBreakerState(Enum):
    """Circuit breaker states"""
    CLOSED = "closed"     # Normal operation
    OPEN = "open"         # Failing, prevent connections
    HALF_OPEN = "half_open"  # Testing recovery

class CryptocomCircuitBreaker:
    """
    Circuit breaker implementation for connection management
    Prevents repeated connection attempts when service is down
    """
    
    def __init__(self, 
                 failure_threshold: int = 5,
                 recovery_timeout: float = 60.0,
                 success_threshold: int = 3):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold
        
        self.state = CircuitBreakerState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: Optional[datetime] = None
        self.state_change_callbacks: List[Callable] = []
        
        logger.info(f"⚡ Circuit Breaker initialized (threshold: {failure_threshold}, recovery: {recovery_timeout}s)")
    
    def add_state_change_callback(self, callback: Callable):
        """Add callback for state changes"""
        self.state_change_callbacks.append(callback)
    
    async def can_proceed(self) -> bool:
        """Check if operation can proceed based on circuit breaker state"""
        if self.state == CircuitBreakerState.CLOSED:
            return True
        elif self.state == CircuitBreakerState.OPEN:
            # Check if recovery timeout has passed
            if (self.last_failure_time and 
                datetime.utcnow() - self.last_failure_time > timedelta(seconds=self.recovery_timeout)):
                await self._transition_to_half_open()
                return True
            return False
        elif self.state == CircuitBreakerState.HALF_OPEN:
            return True
        
        return False
    
    async def record_success(self):
        """Record a successful operation"""
        if self.state == CircuitBreakerState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.success_threshold:
                await self._transition_to_closed()
        elif self.state == CircuitBreakerState.CLOSED:
            # Reset failure count on success
            self.failure_count = 0
    
    async def record_failure(self):
        """Record a failed operation"""
        self.failure_count += 1
        self.last_failure_time = datetime.utcnow()
        
        if self.state == CircuitBreakerState.CLOSED:
            if self.failure_count >= self.failure_threshold:
                await self._transition_to_open()
        elif self.state == CircuitBreakerState.HALF_OPEN:
            await self._transition_to_open()
    
    async def _transition_to_open(self):
        """Transition to OPEN state"""
        if self.state != CircuitBreakerState.OPEN:
            logger.warning(f"⚡ Circuit Breaker OPEN - preventing connections after {self.failure_count} failures")
            self.state = CircuitBreakerState.OPEN
            self.success_count = 0
            await self._notify_state_change()
    
    async def _transition_to_half_open(self):
        """Transition to HALF_OPEN state"""
        if self.state != CircuitBreakerState.HALF_OPEN:
            logger.info("⚡ Circuit Breaker HALF_OPEN - testing recovery")
            self.state = CircuitBreakerState.HALF_OPEN
            self.success_count = 0
            await self._notify_state_change()
    
    async def _transition_to_closed(self):
        """Transition to CLOSED state"""
        if self.state != CircuitBreakerState.CLOSED:
            logger.info(f"⚡ Circuit Breaker CLOSED - normal operation restored after {self.success_count} successes")
            self.state = CircuitBreakerState.CLOSED
            self.failure_count = 0
            self.success_count = 0
            await self._notify_state_change()
    
    async def _notify_state_change(self):
        """Notify callbacks of state change"""
        for callback in self.state_change_callbacks:
            try:
                await callback(self.state)
            except Exception as e:
                logger.error(f"❌ Circuit breaker callback error: {e}")
    
    def get_status(self) -> Dict[str, Any]:
        """Get circuit breaker status"""
        return {
            "state": self.state.value,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "last_failure_time": self.last_failure_time.isoformat() if self.last_failure_time else None,
            "can_proceed": self.state != CircuitBreakerState.OPEN or (
                self.last_failure_time and 
                datetime.utcnow() - self.last_failure_time > timedelta(seconds=self.recovery_timeout)
            )
        }

class CryptocomReconnectionStrategy:
    """
    Intelligent reconnection strategy with exponential backoff
    """
    
    def __init__(self,
                 initial_delay: float = 1.0,
                 max_delay: float = 60.0,
                 exponential_base: float = 2.0,
                 jitter_range: float = 0.1):
        self.initial_delay = initial_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.jitter_range = jitter_range
        
        self.current_delay = initial_delay
        self.attempt_count = 0
        
        logger.info(f"🔄 Reconnection strategy initialized (initial: {initial_delay}s, max: {max_delay}s)")
    
    def get_next_delay(self) -> float:
        """Get delay for next reconnection attempt"""
        import random
        
        # Exponential backoff with jitter
        delay = min(self.current_delay, self.max_delay)
        
        # Add jitter to prevent thundering herd
        jitter = delay * self.jitter_range * (2 * random.random() - 1)  # ±10% jitter
        delay += jitter
        
        # Update for next time
        self.current_delay *= self.exponential_base
        self.attempt_count += 1
        
        return max(0.1, delay)  # Minimum 0.1s delay
    
    def reset(self):
        """Reset reconnection strategy after successful connection"""
        self.current_delay = self.initial_delay
        self.attempt_count = 0
        logger.debug("🔄 Reconnection strategy reset")
    
    def get_status(self) -> Dict[str, Any]:
        """Get reconnection status"""
        return {
            "attempt_count": self.attempt_count,
            "current_delay": self.current_delay,
            "next_delay": min(self.current_delay, self.max_delay)
        }

class CryptocomConnectionManager:
    """
    Advanced connection manager for Crypto.com WebSocket with circuit breaker pattern
    """
    
    def __init__(self, 
                 websocket_url: str,
                 max_reconnect_attempts: int = 10,
                 heartbeat_interval: float = 30.0):
        self.websocket_url = websocket_url
        self.max_reconnect_attempts = max_reconnect_attempts
        self.heartbeat_interval = heartbeat_interval
        
        # Connection state
        self.connection_state = ConnectionState.DISCONNECTED
        self.websocket: Optional[websockets.WebSocketServerProtocol] = None
        self.connection_id = None
        self.connected_at: Optional[datetime] = None
        self.last_message_at: Optional[datetime] = None
        
        # Components
        self.circuit_breaker = CryptocomCircuitBreaker()
        self.reconnection_strategy = CryptocomReconnectionStrategy()
        self.error_handler = CryptocomErrorHandler()
        
        # Tasks
        self.heartbeat_task: Optional[asyncio.Task] = None
        self.reconnection_task: Optional[asyncio.Task] = None
        self.message_listener_task: Optional[asyncio.Task] = None
        
        # Callbacks
        self.connection_callbacks: List[Callable] = []
        self.disconnection_callbacks: List[Callable] = []
        self.message_callbacks: List[Callable] = []
        self.error_callbacks: List[Callable] = []
        
        # Metrics
        self.connection_metrics = {
            "total_connections": 0,
            "total_disconnections": 0,
            "total_reconnections": 0,
            "total_messages_sent": 0,
            "total_messages_received": 0,
            "total_errors": 0,
            "average_connection_duration": 0.0,
            "uptime_percentage": 0.0
        }
        
        # Setup error handling
        self._setup_error_handlers()
        
        logger.info(f"🔌 Connection Manager initialized for {websocket_url}")
    
    def _setup_error_handlers(self):
        """Setup error recovery handlers"""
        # Register recovery handlers for specific error types
        self.error_handler.register_recovery_handler(
            CryptocomErrorType.WEBSOCKET_CONNECTION_FAILED,
            self._handle_connection_failed
        )
        self.error_handler.register_recovery_handler(
            CryptocomErrorType.WEBSOCKET_DISCONNECTED,
            self._handle_disconnection
        )
        self.error_handler.register_recovery_handler(
            CryptocomErrorType.HEARTBEAT_TIMEOUT,
            self._handle_heartbeat_timeout
        )
        
        # Add error callback to track metrics
        self.error_handler.add_error_callback(self._on_error_occurred)
    
    async def _on_error_occurred(self, error):
        """Track error metrics"""
        self.connection_metrics["total_errors"] += 1
    
    # Callback management
    def add_connection_callback(self, callback: Callable):
        """Add callback for connection events"""
        self.connection_callbacks.append(callback)
    
    def add_disconnection_callback(self, callback: Callable):
        """Add callback for disconnection events"""
        self.disconnection_callbacks.append(callback)
    
    def add_message_callback(self, callback: Callable):
        """Add callback for incoming messages"""
        self.message_callbacks.append(callback)
    
    def add_error_callback(self, callback: Callable):
        """Add callback for errors"""
        self.error_callbacks.append(callback)
    
    # Connection management
    async def connect(self) -> bool:
        """
        Establish WebSocket connection with circuit breaker protection
        
        Returns:
            bool: True if connection successful
        """
        if self.connection_state == ConnectionState.CONNECTED:
            logger.debug("🔌 Already connected")
            return True
        
        # Check circuit breaker
        if not await self.circuit_breaker.can_proceed():
            logger.warning("⚡ Circuit breaker prevents connection attempt")
            return False
        
        try:
            self.connection_state = ConnectionState.CONNECTING
            logger.info(f"🔌 Connecting to {self.websocket_url}")
            
            # Establish WebSocket connection
            self.websocket = await websockets.connect(
                self.websocket_url,
                ping_interval=None,  # We'll handle heartbeat manually
                ping_timeout=None,
                close_timeout=10.0
            )
            
            # Connection successful
            self.connection_state = ConnectionState.CONNECTED
            self.connected_at = datetime.utcnow()
            self.last_message_at = datetime.utcnow()
            self.connection_id = id(self.websocket)
            
            # Update metrics
            self.connection_metrics["total_connections"] += 1
            
            # Reset strategies on successful connection
            self.reconnection_strategy.reset()
            await self.circuit_breaker.record_success()
            
            # Start background tasks
            await self._start_background_tasks()
            
            # Notify callbacks
            await self._notify_connection_callbacks(True)
            
            logger.info(f"✅ Connected successfully (ID: {self.connection_id})")
            return True
            
        except Exception as e:
            await self.circuit_breaker.record_failure()
            self.connection_state = ConnectionState.FAILED
            
            # Handle error through error handler
            handled = await self.error_handler.handle_error(e, "connection_attempt")
            
            if not handled:
                logger.error(f"❌ Connection failed: {e}")
            
            return False
    
    async def disconnect(self):
        """Gracefully disconnect WebSocket"""
        if self.connection_state == ConnectionState.DISCONNECTED:
            return
        
        logger.info("🔌 Disconnecting WebSocket")
        
        # Stop background tasks
        await self._stop_background_tasks()
        
        # Close WebSocket
        if self.websocket:
            try:
                await self.websocket.close()
            except Exception as e:
                logger.debug(f"Error closing WebSocket: {e}")
        
        # Update state
        self._reset_connection_state()
        
        # Update metrics
        self.connection_metrics["total_disconnections"] += 1
        
        # Notify callbacks
        await self._notify_disconnection_callbacks()
        
        logger.info("🔌 Disconnected")
    
    async def send_message(self, message: Dict[str, Any]) -> bool:
        """
        Send message through WebSocket
        
        Args:
            message: Message to send
            
        Returns:
            bool: True if sent successfully
        """
        if self.connection_state != ConnectionState.CONNECTED or not self.websocket:
            logger.warning("⚠️ Cannot send message - not connected")
            return False
        
        try:
            message_json = json.dumps(message)
            await self.websocket.send(message_json)
            
            self.connection_metrics["total_messages_sent"] += 1
            logger.debug(f"📤 Sent message: {message.get('id', 'unknown')}")
            return True
            
        except Exception as e:
            await self.error_handler.handle_error(e, "send_message")
            return False
    
    async def start_auto_reconnect(self):
        """Start automatic reconnection management"""
        if self.reconnection_task and not self.reconnection_task.done():
            return
        
        self.reconnection_task = asyncio.create_task(self._auto_reconnect_loop())
        logger.info("🔄 Auto-reconnection started")
    
    async def stop_auto_reconnect(self):
        """Stop automatic reconnection"""
        if self.reconnection_task:
            self.reconnection_task.cancel()
            try:
                await self.reconnection_task
            except asyncio.CancelledError:
                pass
            self.reconnection_task = None
        
        logger.info("🔄 Auto-reconnection stopped")
    
    # Background tasks
    async def _start_background_tasks(self):
        """Start background tasks for connection management"""
        # Start heartbeat
        self.heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        
        # Start message listener
        self.message_listener_task = asyncio.create_task(self._message_listener_loop())
    
    async def _stop_background_tasks(self):
        """Stop all background tasks"""
        tasks = [self.heartbeat_task, self.message_listener_task]
        
        for task in tasks:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        
        self.heartbeat_task = None
        self.message_listener_task = None
    
    async def _heartbeat_loop(self):
        """Heartbeat loop to maintain connection"""
        logger.debug(f"💓 Heartbeat started (interval: {self.heartbeat_interval}s)")
        
        try:
            while self.connection_state == ConnectionState.CONNECTED:
                await asyncio.sleep(self.heartbeat_interval)
                
                if self.connection_state != ConnectionState.CONNECTED:
                    break
                
                # Send heartbeat
                heartbeat_msg = {
                    "id": int(datetime.utcnow().timestamp()),
                    "method": "public/heartbeat"
                }
                
                if not await self.send_message(heartbeat_msg):
                    logger.warning("💓 Heartbeat failed")
                    await self.error_handler.handle_error(
                        Exception("Heartbeat failed"),
                        "heartbeat_send"
                    )
                else:
                    logger.debug("💓 Heartbeat sent")
                
        except asyncio.CancelledError:
            logger.debug("💓 Heartbeat cancelled")
        except Exception as e:
            await self.error_handler.handle_error(e, "heartbeat_loop")
    
    async def _message_listener_loop(self):
        """Listen for incoming messages"""
        logger.debug("👂 Message listener started")
        
        try:
            while self.connection_state == ConnectionState.CONNECTED and self.websocket:
                try:
                    # Wait for message with timeout
                    message = await asyncio.wait_for(self.websocket.recv(), timeout=self.heartbeat_interval * 2)
                    self.last_message_at = datetime.utcnow()
                    self.connection_metrics["total_messages_received"] += 1
                    
                    # Parse message
                    try:
                        data = json.loads(message)
                        await self._notify_message_callbacks(data)
                    except json.JSONDecodeError as e:
                        await self.error_handler.handle_error(e, "message_parsing")
                    
                except asyncio.TimeoutError:
                    # Check if we've missed heartbeats
                    if (datetime.utcnow() - self.last_message_at).total_seconds() > self.heartbeat_interval * 3:
                        await self.error_handler.handle_error(
                            Exception("No messages received - possible heartbeat timeout"),
                            "heartbeat_timeout"
                        )
                        break
                
        except asyncio.CancelledError:
            logger.debug("👂 Message listener cancelled")
        except Exception as e:
            await self.error_handler.handle_error(e, "message_listener")
    
    async def _auto_reconnect_loop(self):
        """Automatic reconnection loop"""
        logger.debug("🔄 Auto-reconnection loop started")
        
        try:
            while True:
                # Wait for disconnection
                while self.connection_state in [ConnectionState.CONNECTED, ConnectionState.CONNECTING]:
                    await asyncio.sleep(1)
                
                # If disconnected and should reconnect
                if (self.connection_state in [ConnectionState.DISCONNECTED, ConnectionState.FAILED] and
                    self.reconnection_strategy.attempt_count < self.max_reconnect_attempts):
                    
                    delay = self.reconnection_strategy.get_next_delay()
                    logger.info(f"🔄 Reconnecting in {delay:.1f}s (attempt {self.reconnection_strategy.attempt_count})")
                    
                    await asyncio.sleep(delay)
                    
                    # Attempt reconnection
                    self.connection_state = ConnectionState.RECONNECTING
                    self.connection_metrics["total_reconnections"] += 1
                    
                    if await self.connect():
                        logger.info("🔄 Reconnection successful")
                    else:
                        logger.warning("🔄 Reconnection failed")
                        
                else:
                    # Max attempts reached or other condition
                    logger.error("🔄 Auto-reconnection stopped - max attempts reached")
                    break
                    
        except asyncio.CancelledError:
            logger.debug("🔄 Auto-reconnection cancelled")
        except Exception as e:
            await self.error_handler.handle_error(e, "auto_reconnect_loop")
    
    # Error recovery handlers
    async def _handle_connection_failed(self, error) -> bool:
        """Handle connection failure"""
        logger.info("🔧 Handling connection failure")
        self.connection_state = ConnectionState.FAILED
        return True  # Indicate recovery should be attempted
    
    async def _handle_disconnection(self, error) -> bool:
        """Handle disconnection"""
        logger.info("🔧 Handling disconnection")
        self.connection_state = ConnectionState.DISCONNECTED
        return True  # Indicate recovery should be attempted
    
    async def _handle_heartbeat_timeout(self, error) -> bool:
        """Handle heartbeat timeout"""
        logger.info("🔧 Handling heartbeat timeout")
        await self.disconnect()
        return True  # Indicate reconnection should be attempted
    
    # Callback notifications
    async def _notify_connection_callbacks(self, connected: bool):
        """Notify connection callbacks"""
        for callback in self.connection_callbacks:
            try:
                await callback(connected)
            except Exception as e:
                logger.error(f"❌ Connection callback error: {e}")
    
    async def _notify_disconnection_callbacks(self):
        """Notify disconnection callbacks"""
        for callback in self.disconnection_callbacks:
            try:
                await callback()
            except Exception as e:
                logger.error(f"❌ Disconnection callback error: {e}")
    
    async def _notify_message_callbacks(self, message: Dict[str, Any]):
        """Notify message callbacks"""
        for callback in self.message_callbacks:
            try:
                await callback(message)
            except Exception as e:
                logger.error(f"❌ Message callback error: {e}")
    
    # Utility methods
    def _reset_connection_state(self):
        """Reset connection state"""
        self.connection_state = ConnectionState.DISCONNECTED
        self.websocket = None
        self.connection_id = None
        self.connected_at = None
        self.last_message_at = None
    
    def is_connected(self) -> bool:
        """Check if currently connected"""
        return self.connection_state == ConnectionState.CONNECTED
    
    def get_connection_duration(self) -> float:
        """Get current connection duration in seconds"""
        if self.connected_at and self.connection_state == ConnectionState.CONNECTED:
            return (datetime.utcnow() - self.connected_at).total_seconds()
        return 0.0
    
    def get_status(self) -> Dict[str, Any]:
        """Get comprehensive connection status"""
        return {
            "connection_state": self.connection_state.value,
            "connected": self.is_connected(),
            "connection_id": self.connection_id,
            "connected_at": self.connected_at.isoformat() if self.connected_at else None,
            "connection_duration": self.get_connection_duration(),
            "last_message_at": self.last_message_at.isoformat() if self.last_message_at else None,
            "circuit_breaker": self.circuit_breaker.get_status(),
            "reconnection_strategy": self.reconnection_strategy.get_status(),
            "metrics": self.connection_metrics,
            "error_metrics": self.error_handler.get_error_metrics()
        }
    
    async def cleanup(self):
        """Cleanup resources"""
        logger.info("🧹 Cleaning up connection manager")
        
        # Stop auto-reconnection
        await self.stop_auto_reconnect()
        
        # Disconnect
        await self.disconnect()
        
        logger.info("🧹 Connection manager cleanup complete")