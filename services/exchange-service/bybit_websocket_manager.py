"""
Unified Bybit WebSocket Manager - Version 3.0.0 (Consolidated)
Combines user data stream and websocket integration into a single manager

CONSOLIDATION CHANGES:
- Merged bybit_user_data_stream.py functionality
- Merged bybit_websocket_integration.py functionality  
- Single unified manager for all Bybit WebSocket operations
- Eliminates code duplication and simplifies architecture
"""

import asyncio
import json
import logging
import time
import hmac
import hashlib
import os
from datetime import datetime
from typing import Dict, Any, List, Optional, Callable
import websockets
import websockets.exceptions
from fastapi import APIRouter, HTTPException
from bybit_connection_manager import BybitConnectionManager

logger = logging.getLogger(__name__)

class UnifiedBybitWebSocketManager:
    """
    CONSOLIDATED: Single manager for all Bybit WebSocket operations
    
    Combines functionality from:
    - BybitUserDataStreamManager (real-time order execution)
    - BybitWebSocketIntegration (service integration)
    
    Benefits:
    - Single connection management
    - Unified authentication handling
    - Simplified event processing
    - Reduced code duplication
    """
    
    def __init__(self):
        # Configuration
        self.enabled = os.getenv("BYBIT_ENABLE_USER_DATA_STREAM", "false").lower() == "true"
        self.api_key = os.getenv("BYBIT_API_KEY")
        self.api_secret = os.getenv("BYBIT_API_SECRET")
        self.testnet = os.getenv("BYBIT_TESTNET", "false").lower() == "true"
        
        # WebSocket URLs
        if self.testnet:
            self.base_url = "wss://stream-testnet.bybit.com/v5/private"
        else:
            self.base_url = "wss://stream.bybit.com/v5/private"
        
        # Core components
        self.connection_manager = BybitConnectionManager(
            websocket_url=self.base_url,
            api_key=self.api_key,
            api_secret=self.api_secret,
            testnet=self.testnet
        )
        
        # State management
        self.websocket = None
        self.is_running = False
        self.is_connected = False
        self.is_initialized = False
        self.is_authenticated = False
        
        # Subscription management
        self.subscribed_topics = set()
        self.topic_callbacks = {}
        
        # Heartbeat settings
        self.ping_interval = 20  # seconds
        self.last_pong = None
        
        # Metrics
        self.metrics = {
            'connection_attempts': 0,
            'successful_connections': 0,
            'messages_received': 0,
            'pings_sent': 0,
            'pongs_received': 0,
            'order_updates_processed': 0,
            'execution_updates_processed': 0,
            'position_updates_processed': 0,
            'wallet_updates_processed': 0,
            'connection_errors': 0,
            'processing_errors': 0,
            'reconnections': 0,
            'uptime_start': None
        }
        
        logger.info(f"⚡ Unified Bybit WebSocket Manager initialized (enabled: {self.enabled}, testnet: {self.testnet})")
    
    async def initialize(self) -> bool:
        """Initialize the unified WebSocket manager"""
        if not self.enabled:
            logger.info("⚡ Bybit User Data Stream disabled by configuration")
            return True
            
        if not self.api_key or not self.api_secret:
            logger.error("❌ Bybit API credentials not configured for User Data Stream")
            return False
        
        try:
            logger.info("🚀 Initializing Unified Bybit WebSocket Manager")
            
            # Initialize connection manager
            await self.connection_manager.initialize()
            
            self.is_initialized = True
            logger.info("✅ Unified Bybit WebSocket Manager initialized successfully")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to initialize Unified Bybit WebSocket Manager: {e}")
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
            logger.info("🚀 Starting Unified Bybit WebSocket Manager")
            
            # Start connection monitoring
            self.is_running = True
            self.metrics['uptime_start'] = datetime.utcnow()
            
            # Start main connection loop
            asyncio.create_task(self._connection_loop())
            
            # Start ping loop
            asyncio.create_task(self._ping_loop())
            
            logger.info("✅ Unified Bybit WebSocket Manager started successfully")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to start Unified Bybit WebSocket Manager: {e}")
            self.is_running = False
            return False
    
    async def stop(self):
        """Stop the unified WebSocket manager"""
        logger.info("🛑 Stopping Unified Bybit WebSocket Manager")
        
        self.is_running = False
        self.is_connected = False
        self.is_authenticated = False
        
        # Close WebSocket connection
        if self.websocket:
            await self.websocket.close()
            self.websocket = None
        
        # Clear subscriptions
        self.subscribed_topics.clear()
        self.topic_callbacks.clear()
        
        logger.info("✅ Unified Bybit WebSocket Manager stopped")
    
    async def _connection_loop(self):
        """Main connection loop with automatic reconnection"""
        while self.is_running:
            try:
                await self._connect_and_listen()
            except Exception as e:
                logger.error(f"❌ Connection loop error: {e}")
                self.metrics['connection_errors'] += 1
                self.is_connected = False
                self.is_authenticated = False
                
                # Wait before reconnecting
                await asyncio.sleep(5)
                
                if self.is_running:
                    self.metrics['reconnections'] += 1
                    logger.info("🔄 Attempting to reconnect...")
    
    async def _connect_and_listen(self):
        """Connect to WebSocket and listen for messages"""
        self.metrics['connection_attempts'] += 1
        logger.info(f"🔌 Connecting to Bybit WebSocket: {self.base_url}")
        
        try:
            async with websockets.connect(
                self.base_url,
                ping_interval=None,  # We handle our own pings
                ping_timeout=10,
                close_timeout=10
            ) as websocket:
                self.websocket = websocket
                self.metrics['successful_connections'] += 1
                self.is_connected = True
                
                logger.info("✅ Connected to Bybit WebSocket")
                
                # Authenticate
                await self._authenticate()
                
                # Subscribe to user data topics
                await self._subscribe_to_user_topics()
                
                # Listen for messages
                async for message in websocket:
                    if not self.is_running:
                        break
                    
                    await self._handle_message(message)
                    
        except websockets.exceptions.ConnectionClosed as e:
            logger.warning(f"🔌 WebSocket connection closed: {e}")
            self.is_connected = False
            self.is_authenticated = False
        except Exception as e:
            logger.error(f"❌ WebSocket connection error: {e}")
            self.is_connected = False
            self.is_authenticated = False
            raise
        finally:
            self.websocket = None
            self.is_connected = False
            self.is_authenticated = False
    
    async def _authenticate(self):
        """Authenticate with Bybit WebSocket"""
        try:
            # Generate authentication signature
            expires = int((time.time() + 10) * 1000)
            signature = self._generate_signature(expires)
            
            auth_message = {
                "op": "auth",
                "args": [self.api_key, expires, signature]
            }
            
            await self.websocket.send(json.dumps(auth_message))
            logger.info("🔐 Authentication message sent")
            
        except Exception as e:
            logger.error(f"❌ Authentication failed: {e}")
            raise
    
    def _generate_signature(self, expires: int) -> str:
        """Generate HMAC signature for authentication"""
        param_str = f"GET/realtime{expires}"
        hash = hmac.new(
            bytes(self.api_secret, "utf-8"), 
            param_str.encode("utf-8"),
            hashlib.sha256
        )
        return hash.hexdigest()
    
    async def _subscribe_to_user_topics(self):
        """Subscribe to user data topics"""
        try:
            # Wait for authentication confirmation
            await asyncio.sleep(2)
            
            # Subscribe to order updates
            await self._subscribe("order")
            
            # Subscribe to execution updates
            await self._subscribe("execution")
            
            # Subscribe to position updates
            await self._subscribe("position")
            
            # Subscribe to wallet updates
            await self._subscribe("wallet")
            
            logger.info("📡 Subscribed to user data topics")
            
        except Exception as e:
            logger.error(f"❌ Failed to subscribe to topics: {e}")
    
    async def _subscribe(self, topic: str):
        """Subscribe to a specific topic"""
        if not self.websocket:
            return
        
        subscribe_message = {
            "op": "subscribe",
            "args": [topic]
        }
        
        await self.websocket.send(json.dumps(subscribe_message))
        self.subscribed_topics.add(topic)
        
        logger.info(f"📡 Subscribed to topic: {topic}")
    
    async def _handle_message(self, message: str):
        """Handle incoming WebSocket message"""
        try:
            self.metrics['messages_received'] += 1
            data = json.loads(message)
            
            # Handle different message types
            if "op" in data:
                op = data["op"]
                
                if op == "auth":
                    await self._handle_auth_response(data)
                elif op == "subscribe":
                    await self._handle_subscription_response(data)
                elif op == "pong":
                    await self._handle_pong_response(data)
                else:
                    logger.debug(f"📋 Unhandled op: {op}")
            
            elif "topic" in data and "data" in data:
                # Data message
                topic = data["topic"]
                topic_data = data["data"]
                
                if topic == "order":
                    await self._handle_order_update(topic_data)
                elif topic == "execution":
                    await self._handle_execution_update(topic_data)
                elif topic == "position":
                    await self._handle_position_update(topic_data)
                elif topic == "wallet":
                    await self._handle_wallet_update(topic_data)
                else:
                    logger.debug(f"📋 Unhandled topic: {topic}")
            
            else:
                logger.debug(f"📋 Unhandled message format: {data}")
                
        except Exception as e:
            logger.error(f"❌ Error handling message: {e}")
            self.metrics['processing_errors'] += 1
    
    async def _handle_auth_response(self, data: Dict[str, Any]):
        """Handle authentication response"""
        try:
            if data.get("success"):
                self.is_authenticated = True
                logger.info("✅ Authentication successful")
            else:
                logger.error("❌ Authentication failed")
        except Exception as e:
            logger.error(f"❌ Error handling auth response: {e}")
    
    async def _handle_subscription_response(self, data: Dict[str, Any]):
        """Handle subscription response"""
        try:
            if data.get("success"):
                logger.info("✅ Subscription successful")
            else:
                logger.error("❌ Subscription failed")
        except Exception as e:
            logger.error(f"❌ Error handling subscription response: {e}")
    
    async def _handle_pong_response(self, data: Dict[str, Any]):
        """Handle pong response"""
        try:
            self.metrics['pongs_received'] += 1
            self.last_pong = datetime.utcnow()
            logger.debug("🏓 Pong received")
        except Exception as e:
            logger.error(f"❌ Error handling pong: {e}")
    
    async def _handle_order_update(self, data: List[Dict[str, Any]]):
        """Handle order update"""
        try:
            self.metrics['order_updates_processed'] += 1
            
            for order_update in data:
                logger.info(f"📋 Order update: {order_update.get('symbol')} "
                          f"{order_update.get('side')} {order_update.get('orderStatus')}")
                
                # TODO: Process with event processor when available
                
        except Exception as e:
            logger.error(f"❌ Error handling order update: {e}")
    
    async def _handle_execution_update(self, data: List[Dict[str, Any]]):
        """Handle execution update"""
        try:
            self.metrics['execution_updates_processed'] += 1
            
            for execution_update in data:
                logger.info(f"💰 Execution update: {execution_update.get('symbol')} "
                          f"{execution_update.get('side')} {execution_update.get('execQty')} @ {execution_update.get('execPrice')}")
                
                # TODO: Process with event processor when available
                
        except Exception as e:
            logger.error(f"❌ Error handling execution update: {e}")
    
    async def _handle_position_update(self, data: List[Dict[str, Any]]):
        """Handle position update"""
        try:
            self.metrics['position_updates_processed'] += 1
            
            for position_update in data:
                logger.info(f"📊 Position update: {position_update.get('symbol')} "
                          f"Size: {position_update.get('size')}")
                
                # TODO: Process with event processor when available
                
        except Exception as e:
            logger.error(f"❌ Error handling position update: {e}")
    
    async def _handle_wallet_update(self, data: List[Dict[str, Any]]):
        """Handle wallet update"""
        try:
            self.metrics['wallet_updates_processed'] += 1
            
            for wallet_update in data:
                logger.info(f"💰 Wallet update: {wallet_update.get('coin')} "
                          f"Available: {wallet_update.get('availableBalance')}")
                
                # TODO: Process with event processor when available
                
        except Exception as e:
            logger.error(f"❌ Error handling wallet update: {e}")
    
    async def _ping_loop(self):
        """Send periodic pings to maintain connection"""
        while self.is_running:
            try:
                if self.websocket and self.is_connected:
                    ping_message = {"op": "ping"}
                    
                    await self.websocket.send(json.dumps(ping_message))
                    self.metrics['pings_sent'] += 1
                    logger.debug("🏓 Ping sent")
                
                await asyncio.sleep(self.ping_interval)
                
            except Exception as e:
                logger.error(f"❌ Error in ping loop: {e}")
                break
    
    def get_connection_status(self) -> Dict[str, Any]:
        """Get current connection status and metrics"""
        uptime_seconds = 0
        if self.metrics['uptime_start']:
            uptime_seconds = (datetime.utcnow() - self.metrics['uptime_start']).total_seconds()
        
        return {
            'enabled': self.enabled,
            'initialized': self.is_initialized,
            'running': self.is_running,
            'connected': self.is_connected,
            'authenticated': self.is_authenticated,
            'testnet': self.testnet,
            'subscribed_topics': list(self.subscribed_topics),
            'uptime_seconds': uptime_seconds,
            'last_pong': self.last_pong.isoformat() if self.last_pong else None,
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
def create_bybit_websocket_router() -> APIRouter:
    """Create API router for unified Bybit WebSocket management"""
    router = APIRouter()
    manager = UnifiedBybitWebSocketManager()
    
    @router.get("/bybit/websocket/status")
    async def get_websocket_status():
        """Get WebSocket connection status"""
        return manager.get_connection_status()
    
    @router.post("/bybit/websocket/initialize")
    async def initialize_websocket():
        """Initialize WebSocket manager"""
        success = await manager.initialize()
        if success:
            return {"status": "initialized"}
        else:
            raise HTTPException(status_code=500, detail="Failed to initialize WebSocket manager")
    
    @router.post("/bybit/websocket/start")
    async def start_websocket():
        """Start WebSocket connection"""
        success = await manager.start()
        if success:
            return {"status": "started"}
        else:
            raise HTTPException(status_code=500, detail="Failed to start WebSocket connection")
    
    @router.post("/bybit/websocket/stop")
    async def stop_websocket():
        """Stop WebSocket connection"""
        await manager.stop()
        return {"status": "stopped"}
    
    return router

# Global instance for import compatibility
unified_bybit_manager = UnifiedBybitWebSocketManager()

# CONSOLIDATION NOTICE
logger.info("✅ CONSOLIDATION: Unified Bybit WebSocket Manager loaded")
logger.info("✅ CONSOLIDATION: Replaces bybit_user_data_stream.py + bybit_websocket_integration.py")
logger.info("✅ CONSOLIDATION: Single manager for all Bybit WebSocket operations")