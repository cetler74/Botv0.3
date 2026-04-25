"""
WebSocket Connection Fix - Phase 1 Implementation
Fixes WebSocket connection issues and ensures reliable real-time fill detection
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Callable
from enum import Enum
import websockets
import json
import ssl
import httpx

logger = logging.getLogger(__name__)

class ConnectionState(Enum):
    """WebSocket connection states"""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    FAILED = "failed"

class WebSocketConnectionFix:
    """
    Fixed WebSocket connection manager with proper error handling and reconnection
    """
    
    def __init__(self, exchange: str, websocket_url: str, config: Dict[str, Any]):
        self.exchange = exchange
        self.websocket_url = websocket_url
        self.config = config
        
        # Connection state
        self.state = ConnectionState.DISCONNECTED
        self.websocket = None
        self.is_running = False
        
        # Reconnection settings
        self.max_reconnect_attempts = 10
        self.reconnect_delay = 1.0
        self.max_reconnect_delay = 300.0  # 5 minutes
        self.reconnect_attempts = 0
        
        # Health monitoring
        self.last_message_time = None
        self.message_timeout = 300  # 5 minutes
        self.ping_interval = 30  # 30 seconds
        self.pong_timeout = 10  # 10 seconds
        
        # Callbacks
        self.message_callbacks: List[Callable] = []
        self.connection_callbacks: List[Callable] = []
        self.error_callbacks: List[Callable] = []
        
        # Metrics
        self.metrics = {
            'connection_attempts': 0,
            'successful_connections': 0,
            'failed_connections': 0,
            'reconnections': 0,
            'messages_received': 0,
            'errors': 0,
            'last_connection_time': None,
            'uptime_seconds': 0
        }
        
        logger.info(f"🔧 WebSocket Connection Fix initialized for {exchange}")
    
    async def start(self):
        """Start WebSocket connection with proper error handling"""
        if self.is_running:
            logger.warning(f"WebSocket connection for {self.exchange} already running")
            return
        
        self.is_running = True
        logger.info(f"🚀 Starting WebSocket connection for {self.exchange}")
        
        # Start connection loop
        asyncio.create_task(self._connection_loop())
        
        # Start health monitoring
        asyncio.create_task(self._health_monitoring_loop())
        
        logger.info(f"✅ WebSocket connection started for {self.exchange}")
    
    async def stop(self):
        """Stop WebSocket connection"""
        if not self.is_running:
            return
        
        self.is_running = False
        logger.info(f"🛑 Stopping WebSocket connection for {self.exchange}")
        
        # Close WebSocket if connected
        if self.websocket:
            try:
                await self.websocket.close()
            except Exception as e:
                logger.debug(f"Error closing WebSocket: {e}")
        
        self.state = ConnectionState.DISCONNECTED
        logger.info(f"✅ WebSocket connection stopped for {self.exchange}")
    
    async def _connection_loop(self):
        """Main connection loop with exponential backoff"""
        while self.is_running:
            try:
                if self.state in [ConnectionState.DISCONNECTED, ConnectionState.FAILED]:
                    await self._connect()
                
                # If connected, wait for disconnection
                if self.state == ConnectionState.CONNECTED:
                    await asyncio.sleep(1)
                
            except Exception as e:
                logger.error(f"❌ Connection loop error for {self.exchange}: {e}")
                self.metrics['errors'] += 1
                await asyncio.sleep(5)
    
    async def _connect(self):
        """Connect to WebSocket with proper error handling"""
        if self.state == ConnectionState.CONNECTING:
            return
        
        self.state = ConnectionState.CONNECTING
        self.metrics['connection_attempts'] += 1
        
        try:
            logger.info(f"🔌 Connecting to {self.exchange} WebSocket: {self.websocket_url}")
            
            # Create SSL context for secure connections
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            
            # Connect with proper timeout and error handling
            self.websocket = await asyncio.wait_for(
                websockets.connect(
                    self.websocket_url,
                    ssl=ssl_context,
                    ping_interval=self.ping_interval,
                    ping_timeout=self.pong_timeout,
                    close_timeout=10,
                    max_size=2**20,  # 1MB max message size
                    compression=None  # Disable compression for better performance
                ),
                timeout=30.0
            )
            
            # Connection successful
            self.state = ConnectionState.CONNECTED
            self.reconnect_attempts = 0
            self.metrics['successful_connections'] += 1
            self.metrics['last_connection_time'] = datetime.utcnow()
            
            logger.info(f"✅ Connected to {self.exchange} WebSocket")
            
            # Notify connection callbacks
            await self._notify_connection_callbacks(True)
            
            # Start message processing
            await self._process_messages()
            
        except asyncio.TimeoutError:
            logger.error(f"❌ Connection timeout for {self.exchange} WebSocket")
            self.metrics['failed_connections'] += 1
            await self._handle_connection_failure("timeout")
            
        except websockets.exceptions.ConnectionClosed as e:
            logger.warning(f"🔌 WebSocket connection closed for {self.exchange}: {e}")
            await self._handle_connection_failure("connection_closed")
            
        except Exception as e:
            logger.error(f"❌ WebSocket connection error for {self.exchange}: {e}")
            self.metrics['failed_connections'] += 1
            await self._handle_connection_failure(str(e))
        
        finally:
            if self.websocket:
                try:
                    await self.websocket.close()
                except:
                    pass
                self.websocket = None
    
    async def _process_messages(self):
        """Process incoming WebSocket messages"""
        try:
            async for message in self.websocket:
                if not self.is_running:
                    break
                
                # Update last message time
                self.last_message_time = datetime.utcnow()
                self.metrics['messages_received'] += 1
                
                # Process message
                await self._handle_message(message)
                
        except websockets.exceptions.ConnectionClosed:
            logger.warning(f"🔌 Message processing ended for {self.exchange} - connection closed")
        except Exception as e:
            logger.error(f"❌ Message processing error for {self.exchange}: {e}")
            self.metrics['errors'] += 1
    
    async def _handle_message(self, message: str):
        """Handle incoming WebSocket message"""
        try:
            # Parse JSON message
            data = json.loads(message)
            
            # Notify message callbacks
            for callback in self.message_callbacks:
                try:
                    await callback(data)
                except Exception as e:
                    logger.error(f"❌ Error in message callback: {e}")
        
        except json.JSONDecodeError as e:
            logger.warning(f"⚠️ Invalid JSON message from {self.exchange}: {e}")
        except Exception as e:
            logger.error(f"❌ Error handling message from {self.exchange}: {e}")
            self.metrics['errors'] += 1
    
    async def _handle_connection_failure(self, reason: str):
        """Handle connection failure with reconnection logic"""
        self.state = ConnectionState.FAILED
        
        # Notify error callbacks
        await self._notify_error_callbacks(reason)
        
        # Attempt reconnection if still running
        if self.is_running:
            await self._schedule_reconnection()
    
    async def _schedule_reconnection(self):
        """Schedule reconnection with exponential backoff"""
        if self.reconnect_attempts >= self.max_reconnect_attempts:
            logger.error(f"❌ Max reconnection attempts reached for {self.exchange}")
            self.state = ConnectionState.FAILED
            return
        
        self.reconnect_attempts += 1
        self.metrics['reconnections'] += 1
        
        # Calculate delay with exponential backoff
        delay = min(
            self.reconnect_delay * (2 ** (self.reconnect_attempts - 1)),
            self.max_reconnect_delay
        )
        
        logger.info(f"🔄 Reconnecting {self.exchange} in {delay:.1f}s (attempt {self.reconnect_attempts}/{self.max_reconnect_attempts})")
        
        self.state = ConnectionState.RECONNECTING
        await asyncio.sleep(delay)
        
        # Reset state to allow reconnection
        self.state = ConnectionState.DISCONNECTED
    
    async def _health_monitoring_loop(self):
        """Monitor connection health"""
        while self.is_running:
            try:
                await asyncio.sleep(30)  # Check every 30 seconds
                
                if self.state == ConnectionState.CONNECTED:
                    # Check if we've received messages recently
                    if self.last_message_time:
                        time_since_last_message = (datetime.utcnow() - self.last_message_time).total_seconds()
                        
                        if time_since_last_message > self.message_timeout:
                            logger.warning(f"⚠️ No messages received from {self.exchange} for {time_since_last_message:.1f}s")
                            await self._handle_connection_failure("message_timeout")
                    
                    # Send ping to test connection
                    if self.websocket:
                        try:
                            await asyncio.wait_for(self.websocket.ping(), timeout=5.0)
                        except asyncio.TimeoutError:
                            logger.warning(f"⚠️ Ping timeout for {self.exchange}")
                            await self._handle_connection_failure("ping_timeout")
                        except Exception as e:
                            logger.warning(f"⚠️ Ping failed for {self.exchange}: {e}")
                            await self._handle_connection_failure("ping_failed")
                
            except Exception as e:
                logger.error(f"❌ Health monitoring error for {self.exchange}: {e}")
                await asyncio.sleep(60)
    
    async def _notify_connection_callbacks(self, connected: bool):
        """Notify connection status change"""
        for callback in self.connection_callbacks:
            try:
                await callback(connected)
            except Exception as e:
                logger.error(f"❌ Error in connection callback: {e}")
    
    async def _notify_error_callbacks(self, error: str):
        """Notify error callbacks"""
        for callback in self.error_callbacks:
            try:
                await callback(error)
            except Exception as e:
                logger.error(f"❌ Error in error callback: {e}")
    
    def add_message_callback(self, callback: Callable):
        """Add message callback"""
        self.message_callbacks.append(callback)
    
    def add_connection_callback(self, callback: Callable):
        """Add connection status callback"""
        self.connection_callbacks.append(callback)
    
    def add_error_callback(self, callback: Callable):
        """Add error callback"""
        self.error_callbacks.append(callback)
    
    async def is_healthy(self) -> bool:
        """Check if connection is healthy"""
        if self.state != ConnectionState.CONNECTED:
            return False
        
        if not self.websocket:
            return False
        
        # Check if we've received messages recently
        if self.last_message_time:
            time_since_last_message = (datetime.utcnow() - self.last_message_time).total_seconds()
            if time_since_last_message > self.message_timeout:
                return False
        
        # Test connection with ping
        try:
            await asyncio.wait_for(self.websocket.ping(), timeout=5.0)
            return True
        except:
            return False
    
    def get_status(self) -> Dict[str, Any]:
        """Get connection status"""
        return {
            'exchange': self.exchange,
            'state': self.state.value,
            'is_running': self.is_running,
            'reconnect_attempts': self.reconnect_attempts,
            'last_message_time': self.last_message_time.isoformat() if self.last_message_time else None,
            'metrics': self.metrics
        }
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get connection metrics"""
        return {
            **self.metrics,
            'state': self.state.value,
            'reconnect_attempts': self.reconnect_attempts,
            'is_healthy': self.state == ConnectionState.CONNECTED
        }

class WebSocketHealthChecker:
    """
    Health checker for WebSocket connections
    """
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.health_endpoints = {
            'binance': 'http://exchange-service:8003/api/v1/websocket/binance/health',
            'bybit': 'http://exchange-service:8003/api/v1/websocket/bybit/health',
            'cryptocom': 'http://exchange-service:8003/api/v1/websocket/cryptocom/health'
        }
    
    async def check_websocket_health(self, exchange: str) -> bool:
        """Check WebSocket health for a specific exchange"""
        try:
            endpoint = self.health_endpoints.get(exchange)
            if not endpoint:
                logger.warning(f"No health endpoint for {exchange}")
                return False
            
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(endpoint)
                
                if response.status_code == 200:
                    data = response.json()
                    return data.get('healthy', False)
                else:
                    logger.warning(f"Health check failed for {exchange}: HTTP {response.status_code}")
                    return False
        
        except Exception as e:
            logger.error(f"❌ Error checking {exchange} WebSocket health: {e}")
            return False
    
    async def check_all_websocket_health(self) -> Dict[str, bool]:
        """Check health for all exchanges"""
        results = {}
        
        for exchange in ['binance', 'bybit', 'cryptocom']:
            results[exchange] = await self.check_websocket_health(exchange)
        
        return results
