"""
Crypto.com User Data Stream Manager - Version 2.6.0
Manages Crypto.com WebSocket connection with authentication, subscriptions, and event processing
"""

import asyncio
import json
import logging
import time
import hmac
import hashlib
from datetime import datetime
from typing import Dict, Any, List, Optional, Callable
import websockets
import websockets.exceptions
from cryptocom_auth_manager import CryptocomAuthManager
from cryptocom_connection_manager import CryptocomConnectionManager
from cryptocom_error_handlers import CryptocomErrorHandler

logger = logging.getLogger(__name__)

class CryptocomUserDataStreamManager:
    """
    Manages Crypto.com User Data Stream WebSocket connection
    
    Features:
    - HMAC-SHA256 authentication with API keys
    - Channel subscriptions (user.order, user.trade, user.balance)
    - Heartbeat mechanism for connection maintenance
    - Event processing and callback system
    - Error recovery and automatic reconnection
    """
    
    def __init__(self, api_key: str, api_secret: str, base_url: str = "wss://stream.crypto.com/exchange/v1/user"):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url
        self.websocket = None
        self.is_running = False
        self.is_connected = False
        
        # Authentication and subscription management
        self.auth_manager = CryptocomAuthManager(api_key, api_secret)
        
        # Heartbeat settings
        self.heartbeat_interval = 30  # seconds
        
        # Advanced connection management with circuit breaker
        self.connection_manager = CryptocomConnectionManager(
            websocket_url=base_url,
            max_reconnect_attempts=10,
            heartbeat_interval=self.heartbeat_interval
        )
        self.error_handler = CryptocomErrorHandler()
        self.last_heartbeat = None
        self.heartbeat_task = None
        
        # Event callbacks
        self.event_callbacks = {}
        self.order_callbacks: List[Callable] = []
        self.trade_callbacks: List[Callable] = []
        self.balance_callbacks: List[Callable] = []
        self.error_callbacks: List[Callable] = []
        self.connection_callbacks: List[Callable] = []
        
        # Channel subscriptions
        self.subscribed_channels = []
        self.target_channels = ["user.order", "user.trade", "user.balance"]
        
        # Metrics tracking
        self.metrics = {
            "connection_attempts": 0,
            "successful_connections": 0,
            "messages_received": 0,
            "messages_processed": 0,
            "authentication_failures": 0,
            "subscription_errors": 0,
            "heartbeat_sent": 0,
            "heartbeat_failures": 0,
            "processing_errors": 0,
            "last_message_time": None,
            "uptime_start": None,
            "reconnect_attempts": 0
        }
        
        logger.info("🏢 Crypto.com User Data Stream Manager initialized")
    
    async def start(self) -> bool:
        """Start the WebSocket connection and event processing"""
        try:
            logger.info("🚀 Starting Crypto.com User Data Stream")
            
            self.is_running = True
            self.metrics["uptime_start"] = datetime.utcnow().isoformat()
            
            # Setup connection manager callbacks
            self.connection_manager.add_connection_callback(self._on_connection_change)
            self.connection_manager.add_message_callback(self._on_message_received)
            self.connection_manager.add_error_callback(self._on_error)
            
            # Start connection with advanced management
            success = await self.connection_manager.connect()
            if not success:
                logger.error("❌ Failed to establish Crypto.com WebSocket connection")
                return False
            
            # Authenticate and subscribe
            auth_success = await self._authenticate()
            if not auth_success:
                await self.connection_manager.disconnect()
                return False
            
            subscribe_success = await self._subscribe_to_channels()
            if not subscribe_success:
                await self.connection_manager.disconnect()
                return False
            
            # Start auto-reconnection management
            await self.connection_manager.start_auto_reconnect()
            
            logger.info("✅ Crypto.com User Data Stream started successfully")
            return True
            
        except Exception as e:
            logger.error(f"❌ Error starting Crypto.com User Data Stream: {e}")
            await self.error_handler.handle_error(e, "stream_start")
            return False
    
    async def stop(self):
        """Stop the WebSocket connection and cleanup resources"""
        logger.info("🛑 Stopping Crypto.com User Data Stream")
        
        self.is_running = False
        
        # Stop connection manager (handles all cleanup)
        await self.connection_manager.stop_auto_reconnect()
        await self.connection_manager.disconnect()
        
        # Cleanup connection manager
        await self.connection_manager.cleanup()
        
        self.is_connected = False
        await self._notify_connection_change(False)
        
        logger.info("✅ Crypto.com User Data Stream stopped")
    
    # Connection manager callbacks
    async def _on_connection_change(self, connected: bool):
        """Handle connection state changes from connection manager"""
        self.is_connected = connected
        
        if connected:
            logger.info("🔌 Connected to Crypto.com WebSocket")
            self.metrics["successful_connections"] += 1
            
            # Re-authenticate and re-subscribe on reconnection
            try:
                await self._authenticate()
                await self._subscribe_to_channels()
            except Exception as e:
                logger.error(f"❌ Error during reconnection setup: {e}")
                await self.error_handler.handle_error(e, "reconnection_setup")
        else:
            logger.warning("🔌 Disconnected from Crypto.com WebSocket")
            self.subscribed_channels.clear()
        
        await self._notify_connection_change(connected)
    
    async def _on_message_received(self, message: Dict[str, Any]):
        """Handle incoming messages from connection manager"""
        try:
            self.metrics["messages_received"] += 1
            self.metrics["last_message_time"] = datetime.utcnow().isoformat()
            
            await self._process_message(message)
            self.metrics["messages_processed"] += 1
            
        except Exception as e:
            logger.error(f"❌ Error processing message: {e}")
            self.metrics["processing_errors"] += 1
            await self.error_handler.handle_error(e, "message_processing")
    
    async def _on_error(self, error):
        """Handle errors from connection manager"""
        logger.error(f"🚨 Connection manager error: {error}")
        
        # Notify error callbacks
        for callback in self.error_callbacks:
            try:
                await callback(str(error))
            except Exception as e:
                logger.error(f"❌ Error callback failed: {e}")
    
    async def _connect_with_retry(self) -> bool:
        """Connect to WebSocket with retry logic"""
        max_attempts = 5
        base_delay = 1
        
        for attempt in range(max_attempts):
            try:
                self.metrics["connection_attempts"] += 1
                
                logger.info(f"🔌 Connecting to Crypto.com WebSocket (attempt {attempt + 1}/{max_attempts})")
                
                # Establish WebSocket connection
                self.websocket = await websockets.connect(
                    self.base_url,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=10
                )
                
                # Authenticate and subscribe
                auth_success = await self._authenticate()
                if not auth_success:
                    await self.websocket.close()
                    continue
                
                subscribe_success = await self._subscribe_to_channels()
                if not subscribe_success:
                    await self.websocket.close()
                    continue
                
                # Connection successful
                self.is_connected = True
                self.metrics["successful_connections"] += 1
                await self.connection_manager.record_connection_success()
                await self._notify_connection_change(True)
                
                logger.info("✅ Crypto.com WebSocket connected and subscribed successfully")
                return True
                
            except Exception as e:
                logger.error(f"❌ Connection attempt {attempt + 1} failed: {e}")
                await self.error_handler.handle_error("network", str(e), "medium")
                
                if attempt < max_attempts - 1:
                    delay = base_delay * (2 ** attempt)  # Exponential backoff
                    logger.info(f"⏳ Retrying in {delay} seconds...")
                    await asyncio.sleep(delay)
        
        logger.error("❌ Failed to connect after maximum attempts")
        await self.connection_manager.record_connection_failure()
        return False
    
    async def _authenticate(self) -> bool:
        """Authenticate with Crypto.com WebSocket API"""
        try:
            logger.info("🔐 Authenticating with Crypto.com WebSocket")
            
            # Generate authentication message
            auth_message = self.auth_manager.generate_auth_message()
            
            # Send authentication through connection manager
            success = await self.connection_manager.send_message(auth_message)
            if not success:
                logger.error("❌ Failed to send authentication message")
                self.metrics["authentication_failures"] += 1
                return False
            
            # Note: Response will be handled by _on_message_received callback
            # For now, assume authentication is successful if message was sent
            # TODO: Implement proper authentication response handling
            logger.info("✅ Crypto.com WebSocket authentication message sent")
            await asyncio.sleep(2)  # Give time for authentication to process
            return True
                
        except Exception as e:
            logger.error(f"❌ Authentication error: {e}")
            self.metrics["authentication_failures"] += 1
            await self.error_handler.handle_error(e, "authentication")
            return False
    
    async def _subscribe_to_channels(self) -> bool:
        """Subscribe to user data channels"""
        try:
            logger.info("📡 Subscribing to Crypto.com user data channels")
            
            # Generate subscription message
            subscribe_message = self.auth_manager.generate_subscription_message(self.target_channels)
            
            # Send subscription through connection manager
            success = await self.connection_manager.send_message(subscribe_message)
            if not success:
                logger.error("❌ Failed to send subscription message")
                self.metrics["subscription_errors"] += 1
                return False
            
            # Note: Response will be handled by _on_message_received callback
            # For now, assume subscription is successful if message was sent
            # TODO: Implement proper subscription response handling
            self.subscribed_channels = self.target_channels.copy()
            logger.info(f"✅ Subscription message sent for channels: {', '.join(self.target_channels)}")
            await asyncio.sleep(1)  # Give time for subscription to process
            return True
                
        except Exception as e:
            logger.error(f"❌ Subscription error: {e}")
            self.metrics["subscription_errors"] += 1
            await self.error_handler.handle_error(e, "subscription")
            return False
    
    async def _message_processing_loop(self):
        """Main message processing loop"""
        logger.info("🔄 Starting message processing loop")
        
        while self.is_running and self.websocket:
            try:
                # Receive message with timeout
                message = await asyncio.wait_for(self.websocket.recv(), timeout=60)
                
                # Update metrics
                self.metrics["messages_received"] += 1
                self.metrics["last_message_time"] = datetime.utcnow().isoformat()
                
                # Process message
                await self._process_message(message)
                
            except asyncio.TimeoutError:
                logger.warning("⚠️ Message receive timeout, checking connection...")
                if not await self._is_connection_alive():
                    await self._handle_disconnect()
                    break
                    
            except websockets.exceptions.ConnectionClosed:
                logger.warning("🔌 WebSocket connection closed")
                await self._handle_disconnect()
                break
                
            except Exception as e:
                logger.error(f"❌ Error in message processing loop: {e}")
                await self.error_handler.handle_error("system", str(e), "medium")
                
                # Continue processing unless critical error
                if not self.is_running:
                    break
    
    async def _process_message(self, message: str):
        """Process incoming WebSocket message"""
        try:
            data = json.loads(message)
            
            # Handle different message types
            method = data.get("method")
            
            if method == "subscription":
                await self._handle_subscription_message(data)
            elif method == "heartbeat":
                await self._handle_heartbeat_response(data)
            elif data.get("id"):  # Response to our requests
                await self._handle_response_message(data)
            else:
                logger.debug(f"Unknown message type: {data}")
            
            self.metrics["messages_processed"] += 1
            
        except json.JSONDecodeError as e:
            logger.error(f"❌ Failed to parse message: {e}")
            await self.error_handler.handle_error("data_format", str(e), "low")
        except Exception as e:
            logger.error(f"❌ Error processing message: {e}")
            self.metrics["processing_errors"] += 1
            await self.error_handler.handle_error("system", str(e), "medium")
    
    async def _handle_subscription_message(self, data: Dict[str, Any]):
        """Handle subscription event messages"""
        try:
            params = data.get("params", {})
            channel = params.get("channel")
            event_data = params.get("data")
            
            if not channel or not event_data:
                logger.warning("⚠️ Invalid subscription message format")
                return
            
            # Route to appropriate event processor
            if channel == "user.order":
                await self._process_order_event(event_data)
            elif channel == "user.trade":
                await self._process_trade_event(event_data)
            elif channel == "user.balance":
                await self._process_balance_event(event_data)
            else:
                logger.debug(f"Unknown channel: {channel}")
                
        except Exception as e:
            logger.error(f"❌ Error handling subscription message: {e}")
            await self.error_handler.handle_error("system", str(e), "medium")
    
    async def _process_order_event(self, event_data: Dict[str, Any]):
        """Process order status update events"""
        try:
            logger.debug(f"📋 Processing order event: {event_data.get('order_id', 'unknown')}")
            
            # Notify all order callbacks
            for callback in self.order_callbacks:
                try:
                    await callback(event_data)
                except Exception as e:
                    logger.error(f"❌ Error in order callback: {e}")
                    
        except Exception as e:
            logger.error(f"❌ Error processing order event: {e}")
            await self.error_handler.handle_error("system", str(e), "medium")
    
    async def _process_trade_event(self, event_data: Dict[str, Any]):
        """Process trade execution events"""
        try:
            logger.debug(f"💰 Processing trade event: {event_data.get('trade_id', 'unknown')}")
            
            # Notify all trade callbacks
            for callback in self.trade_callbacks:
                try:
                    await callback(event_data)
                except Exception as e:
                    logger.error(f"❌ Error in trade callback: {e}")
                    
        except Exception as e:
            logger.error(f"❌ Error processing trade event: {e}")
            await self.error_handler.handle_error("system", str(e), "medium")
    
    async def _process_balance_event(self, event_data: Dict[str, Any]):
        """Process balance update events"""
        try:
            logger.debug(f"💳 Processing balance event: {event_data.get('currency', 'unknown')}")
            
            # Notify all balance callbacks
            for callback in self.balance_callbacks:
                try:
                    await callback(event_data)
                except Exception as e:
                    logger.error(f"❌ Error in balance callback: {e}")
                    
        except Exception as e:
            logger.error(f"❌ Error processing balance event: {e}")
            await self.error_handler.handle_error("system", str(e), "medium")
    
    async def _heartbeat_loop(self):
        """Send periodic heartbeat messages"""
        logger.info("💓 Starting heartbeat loop")
        
        while self.is_running and self.is_connected:
            try:
                await asyncio.sleep(self.heartbeat_interval)
                
                if not self.is_running or not self.websocket:
                    break
                
                # Send heartbeat message
                heartbeat_msg = {
                    "id": int(time.time() * 1000),
                    "method": "heartbeat"
                }
                
                await self.websocket.send(json.dumps(heartbeat_msg))
                self.metrics["heartbeat_sent"] += 1
                self.last_heartbeat = datetime.utcnow()
                
                logger.debug("💓 Heartbeat sent")
                
            except Exception as e:
                logger.error(f"❌ Heartbeat error: {e}")
                self.metrics["heartbeat_failures"] += 1
                await self.error_handler.handle_error("network", str(e), "medium")
                break
    
    async def _handle_heartbeat_response(self, data: Dict[str, Any]):
        """Handle heartbeat response from server"""
        logger.debug("💓 Heartbeat response received")
    
    async def _handle_response_message(self, data: Dict[str, Any]):
        """Handle response messages to our requests"""
        request_id = data.get("id")
        code = data.get("code")
        message = data.get("message", "")
        
        if code == 0:
            logger.debug(f"✅ Request {request_id} successful")
        else:
            logger.warning(f"⚠️ Request {request_id} failed: {message}")
    
    async def _is_connection_alive(self) -> bool:
        """Check if WebSocket connection is still alive"""
        try:
            if not self.websocket:
                return False
            
            # Send ping to test connection
            pong = await self.websocket.ping()
            await asyncio.wait_for(pong, timeout=5)
            return True
            
        except Exception:
            return False
    
    async def _handle_disconnect(self):
        """Handle WebSocket disconnection"""
        logger.warning("🔌 Handling WebSocket disconnection")
        
        self.is_connected = False
        await self._notify_connection_change(False)
        
        # Attempt reconnection if still running
        if self.is_running:
            logger.info("🔄 Attempting to reconnect...")
            self.metrics["reconnect_attempts"] += 1
            success = await self._connect_with_retry()
            
            if success:
                # Restart message processing loop
                asyncio.create_task(self._message_processing_loop())
    
    async def _notify_connection_change(self, connected: bool):
        """Notify connection status change to callbacks"""
        try:
            for callback in self.connection_callbacks:
                try:
                    await callback(connected)
                except Exception as e:
                    logger.error(f"❌ Error in connection callback: {e}")
        except Exception as e:
            logger.error(f"❌ Error notifying connection change: {e}")
    
    # Callback management methods
    def add_order_callback(self, callback: Callable):
        """Add callback for order events"""
        self.order_callbacks.append(callback)
    
    def add_trade_callback(self, callback: Callable):
        """Add callback for trade events"""
        self.trade_callbacks.append(callback)
    
    def add_balance_callback(self, callback: Callable):
        """Add callback for balance events"""
        self.balance_callbacks.append(callback)
    
    def add_error_callback(self, callback: Callable):
        """Add callback for error events"""
        self.error_callbacks.append(callback)
    
    def add_connection_callback(self, callback: Callable):
        """Add callback for connection status changes"""
        self.connection_callbacks.append(callback)
    
    # Status and metrics methods
    def get_status(self) -> Dict[str, Any]:
        """Get current connection status"""
        return {
            "is_running": self.is_running,
            "is_connected": self.is_connected,
            "is_healthy": self.is_healthy(),
            "subscribed_channels": self.subscribed_channels,
            "last_heartbeat": self.last_heartbeat.isoformat() if self.last_heartbeat else None,
            "uptime": self.metrics["uptime_start"],
            "last_message": self.metrics["last_message_time"],
            "connection_manager_status": self.connection_manager.get_status(),
            "error_handler_stats": self.error_handler.get_stats()
        }
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get detailed metrics"""
        return {
            **self.metrics,
            "connection_manager_metrics": self.connection_manager.get_metrics(),
            "error_handler_metrics": self.error_handler.get_metrics()
        }
    
    def is_healthy(self) -> bool:
        """Check if the connection is healthy"""
        if not self.is_running or not self.is_connected:
            return False
        
        # Check if we've received messages recently
        if self.metrics["last_message_time"]:
            from datetime import datetime
            last_message = datetime.fromisoformat(self.metrics["last_message_time"])
            time_since_last = (datetime.utcnow() - last_message).total_seconds()
            
            # Consider unhealthy if no messages for more than 2 minutes
            if time_since_last > 120:
                return False
        
        return True