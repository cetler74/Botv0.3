"""
Unified Crypto.com WebSocket Manager - Version 3.0.0 (Consolidated)
Combines user data stream and websocket integration into a single manager

CONSOLIDATION CHANGES:
- Merged cryptocom_user_data_stream.py functionality
- Merged cryptocom_websocket_integration.py functionality  
- Single unified manager for all Crypto.com WebSocket operations
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
from cryptocom_auth_manager import CryptocomAuthManager
from cryptocom_connection_manager import CryptocomConnectionManager
from cryptocom_error_handlers import CryptocomErrorHandler
from cryptocom_event_processors import CryptocomEventProcessorManager
from cryptocom_market_websocket import CryptocomMarketWebSocket

logger = logging.getLogger(__name__)

class UnifiedCryptocomWebSocketManager:
    """
    CONSOLIDATED: Single manager for all Crypto.com WebSocket operations
    
    Combines functionality from:
    - CryptocomUserDataStreamManager (real-time order execution)
    - CryptocomWebSocketIntegration (service integration)
    - CryptocomMarketWebSocket (market data)
    
    Benefits:
    - Single connection management
    - Unified authentication handling
    - Simplified event processing
    - Reduced code duplication
    """
    
    def __init__(self):
        # Configuration (from integration)
        self.enabled = os.getenv("CRYPTOCOM_ENABLE_USER_DATA_STREAM", "false").lower() == "true"
        self.api_key = os.getenv("CRYPTOCOM_API_KEY")
        self.api_secret = os.getenv("CRYPTOCOM_API_SECRET")
        self.base_url = os.getenv("CRYPTOCOM_WEBSOCKET_URL", "wss://stream.crypto.com/exchange/v1/user")
        
        # Core components (from user data stream)
        self.auth_manager = CryptocomAuthManager(self.api_key or "", self.api_secret or "")
        self.connection_manager = CryptocomConnectionManager(
            websocket_url=self.base_url,
            max_reconnect_attempts=5,
            reconnect_delay=5.0,
            heartbeat_interval=30
        )
        self.error_handler = CryptocomErrorHandler()
        
        # Event processing (from integration)
        self.event_processor_manager: Optional[CryptocomEventProcessorManager] = None
        self.market_websocket: Optional[CryptocomMarketWebSocket] = None
        
        # State management
        self.websocket = None
        self.is_running = False
        self.is_connected = False
        self.is_initialized = False
        
        # Heartbeat settings
        self.heartbeat_interval = 30  # seconds
        self.last_heartbeat = None
        
        # Subscription management
        self.subscribed_channels = set()
        self.subscription_callbacks = {}
        
        # Metrics
        self.metrics = {
            'connection_attempts': 0,
            'successful_connections': 0,
            'messages_received': 0,
            'heartbeats_sent': 0,
            'heartbeats_received': 0,
            'order_updates_processed': 0,
            'trade_updates_processed': 0,
            'balance_updates_processed': 0,
            'connection_errors': 0,
            'processing_errors': 0,
            'reconnections': 0,
            'uptime_start': None
        }
        
        logger.info(f"🏢 Unified Crypto.com WebSocket Manager initialized (enabled: {self.enabled})")
    
    async def initialize(self) -> bool:
        """Initialize the unified WebSocket manager"""
        if not self.enabled:
            logger.info("🏢 Crypto.com User Data Stream disabled by configuration")
            return True
            
        if not self.api_key or not self.api_secret:
            logger.error("❌ Crypto.com API credentials not configured for User Data Stream")
            return False
        
        try:
            logger.info("🚀 Initializing Unified Crypto.com WebSocket Manager")
            
            # Initialize event processor manager
            self.event_processor_manager = CryptocomEventProcessorManager()
            await self.event_processor_manager.initialize()
            
            # Initialize market websocket if needed
            self.market_websocket = CryptocomMarketWebSocket(
                api_key=self.api_key,
                api_secret=self.api_secret
            )
            
            # Initialize authentication
            await self.auth_manager.initialize()
            
            self.is_initialized = True
            logger.info("✅ Unified Crypto.com WebSocket Manager initialized successfully")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to initialize Unified Crypto.com WebSocket Manager: {e}")
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
            logger.info("🚀 Starting Unified Crypto.com WebSocket Manager")
            
            # Start connection monitoring
            self.is_running = True
            self.metrics['uptime_start'] = datetime.utcnow()
            
            # Start main connection loop
            asyncio.create_task(self._connection_loop())
            
            # Start heartbeat task
            asyncio.create_task(self._heartbeat_loop())
            
            logger.info("✅ Unified Crypto.com WebSocket Manager started successfully")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to start Unified Crypto.com WebSocket Manager: {e}")
            self.is_running = False
            return False
    
    async def stop(self):
        """Stop the unified WebSocket manager"""
        logger.info("🛑 Stopping Unified Crypto.com WebSocket Manager")
        
        self.is_running = False
        self.is_connected = False
        
        # Close WebSocket connection
        if self.websocket:
            await self.websocket.close()
            self.websocket = None
        
        # Clear subscriptions
        self.subscribed_channels.clear()
        self.subscription_callbacks.clear()
        
        logger.info("✅ Unified Crypto.com WebSocket Manager stopped")
    
    async def _connection_loop(self):
        """Main connection loop with automatic reconnection"""
        while self.is_running:
            try:
                await self._connect_and_listen()
            except Exception as e:
                logger.error(f"❌ Connection loop error: {e}")
                self.metrics['connection_errors'] += 1
                self.is_connected = False
                
                # Wait before reconnecting
                await asyncio.sleep(self.connection_manager.reconnect_delay)
                
                if self.is_running:
                    self.metrics['reconnections'] += 1
                    logger.info("🔄 Attempting to reconnect...")
    
    async def _connect_and_listen(self):
        """Connect to WebSocket and listen for messages"""
        self.metrics['connection_attempts'] += 1
        logger.info(f"🔌 Connecting to Crypto.com WebSocket: {self.base_url}")
        
        try:
            async with websockets.connect(
                self.base_url,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=10
            ) as websocket:
                self.websocket = websocket
                self.metrics['successful_connections'] += 1
                self.is_connected = True
                
                logger.info("✅ Connected to Crypto.com WebSocket")
                
                # Authenticate
                await self._authenticate()
                
                # Subscribe to user data channels
                await self._subscribe_to_user_channels()
                
                # Listen for messages
                async for message in websocket:
                    if not self.is_running:
                        break
                    
                    await self._handle_message(message)
                    
        except websockets.exceptions.ConnectionClosed as e:
            logger.warning(f"🔌 WebSocket connection closed: {e}")
            self.is_connected = False
        except Exception as e:
            logger.error(f"❌ WebSocket connection error: {e}")
            self.is_connected = False
            raise
        finally:
            self.websocket = None
            self.is_connected = False
    
    async def _authenticate(self):
        """Authenticate with Crypto.com WebSocket"""
        try:
            auth_message = await self.auth_manager.create_auth_message()
            await self.websocket.send(json.dumps(auth_message))
            logger.info("🔐 Authentication message sent")
            
        except Exception as e:
            logger.error(f"❌ Authentication failed: {e}")
            raise
    
    async def _subscribe_to_user_channels(self):
        """Subscribe to user data channels"""
        try:
            # Subscribe to user order updates
            await self._subscribe("user.order")
            
            # Subscribe to user trade updates
            await self._subscribe("user.trade")
            
            # Subscribe to user balance updates
            await self._subscribe("user.balance")
            
            logger.info("📡 Subscribed to user data channels")
            
        except Exception as e:
            logger.error(f"❌ Failed to subscribe to channels: {e}")
    
    async def _subscribe(self, channel: str):
        """Subscribe to a specific channel"""
        if not self.websocket:
            return
        
        subscribe_message = {
            "id": int(time.time() * 1000),
            "method": "subscribe",
            "params": {
                "channels": [channel]
            }
        }
        
        await self.websocket.send(json.dumps(subscribe_message))
        self.subscribed_channels.add(channel)
        
        logger.info(f"📡 Subscribed to channel: {channel}")
    
    async def _handle_message(self, message: str):
        """Handle incoming WebSocket message"""
        try:
            self.metrics['messages_received'] += 1
            data = json.loads(message)
            
            # Handle different message types
            if "method" in data:
                method = data["method"]
                
                if method == "subscribe":
                    await self._handle_subscription_response(data)
                elif method == "public/heartbeat":
                    await self._handle_heartbeat_response(data)
                else:
                    logger.debug(f"📋 Unhandled method: {method}")
            
            elif "result" in data and "channel" in data["result"]:
                # Channel data message
                channel = data["result"]["channel"]
                channel_data = data["result"]["data"]
                
                if channel == "user.order":
                    await self._handle_order_update(channel_data)
                elif channel == "user.trade":
                    await self._handle_trade_update(channel_data)
                elif channel == "user.balance":
                    await self._handle_balance_update(channel_data)
                else:
                    logger.debug(f"📋 Unhandled channel: {channel}")
            
            else:
                logger.debug(f"📋 Unhandled message format: {data}")
                
        except Exception as e:
            logger.error(f"❌ Error handling message: {e}")
            self.metrics['processing_errors'] += 1
    
    async def _handle_subscription_response(self, data: Dict[str, Any]):
        """Handle subscription confirmation"""
        try:
            if "result" in data and data["result"]["channel"]:
                channel = data["result"]["channel"]
                logger.info(f"✅ Subscription confirmed: {channel}")
        except Exception as e:
            logger.error(f"❌ Error handling subscription response: {e}")
    
    async def _handle_heartbeat_response(self, data: Dict[str, Any]):
        """Handle heartbeat response"""
        try:
            self.metrics['heartbeats_received'] += 1
            self.last_heartbeat = datetime.utcnow()
            logger.debug("💓 Heartbeat received")
        except Exception as e:
            logger.error(f"❌ Error handling heartbeat: {e}")
    
    async def _handle_order_update(self, data: List[Dict[str, Any]]):
        """Handle user order update"""
        try:
            self.metrics['order_updates_processed'] += 1
            
            for order_update in data:
                logger.info(f"📋 Order update: {order_update.get('instrument_name')} "
                          f"{order_update.get('side')} {order_update.get('status')}")
                
                # Process with event processor
                if self.event_processor_manager:
                    await self.event_processor_manager.process_order_event(order_update)
                
        except Exception as e:
            logger.error(f"❌ Error handling order update: {e}")
    
    async def _handle_trade_update(self, data: List[Dict[str, Any]]):
        """Handle user trade update"""
        try:
            self.metrics['trade_updates_processed'] += 1
            
            for trade_update in data:
                logger.info(f"💰 Trade update: {trade_update.get('instrument_name')} "
                          f"{trade_update.get('side')} {trade_update.get('quantity')} @ {trade_update.get('price')}")
                
                # Process with event processor
                if self.event_processor_manager:
                    await self.event_processor_manager.process_trade_event(trade_update)
                
        except Exception as e:
            logger.error(f"❌ Error handling trade update: {e}")
    
    async def _handle_balance_update(self, data: List[Dict[str, Any]]):
        """Handle user balance update"""
        try:
            self.metrics['balance_updates_processed'] += 1
            
            for balance_update in data:
                logger.info(f"💰 Balance update: {balance_update.get('currency')} "
                          f"Available: {balance_update.get('available')} "
                          f"Balance: {balance_update.get('balance')}")
                
                # Process with event processor
                if self.event_processor_manager:
                    await self.event_processor_manager.process_balance_event(balance_update)
                
        except Exception as e:
            logger.error(f"❌ Error handling balance update: {e}")
    
    async def _heartbeat_loop(self):
        """Send periodic heartbeats to maintain connection"""
        while self.is_running:
            try:
                if self.websocket and self.is_connected:
                    heartbeat_message = {
                        "id": int(time.time() * 1000),
                        "method": "public/heartbeat"
                    }
                    
                    await self.websocket.send(json.dumps(heartbeat_message))
                    self.metrics['heartbeats_sent'] += 1
                    logger.debug("💓 Heartbeat sent")
                
                await asyncio.sleep(self.heartbeat_interval)
                
            except Exception as e:
                logger.error(f"❌ Error in heartbeat loop: {e}")
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
            'subscribed_channels': list(self.subscribed_channels),
            'uptime_seconds': uptime_seconds,
            'last_heartbeat': self.last_heartbeat.isoformat() if self.last_heartbeat else None,
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
def create_cryptocom_websocket_router() -> APIRouter:
    """Create API router for unified Crypto.com WebSocket management"""
    router = APIRouter()
    manager = UnifiedCryptocomWebSocketManager()
    
    @router.get("/cryptocom/websocket/status")
    async def get_websocket_status():
        """Get WebSocket connection status"""
        return manager.get_connection_status()
    
    @router.post("/cryptocom/websocket/initialize")
    async def initialize_websocket():
        """Initialize WebSocket manager"""
        success = await manager.initialize()
        if success:
            return {"status": "initialized"}
        else:
            raise HTTPException(status_code=500, detail="Failed to initialize WebSocket manager")
    
    @router.post("/cryptocom/websocket/start")
    async def start_websocket():
        """Start WebSocket connection"""
        success = await manager.start()
        if success:
            return {"status": "started"}
        else:
            raise HTTPException(status_code=500, detail="Failed to start WebSocket connection")
    
    @router.post("/cryptocom/websocket/stop")
    async def stop_websocket():
        """Stop WebSocket connection"""
        await manager.stop()
        return {"status": "stopped"}
    
    return router

# Global instance for import compatibility
unified_cryptocom_manager = UnifiedCryptocomWebSocketManager()

# CONSOLIDATION NOTICE
logger.info("✅ CONSOLIDATION: Unified Crypto.com WebSocket Manager loaded")
logger.info("✅ CONSOLIDATION: Replaces cryptocom_user_data_stream.py + cryptocom_websocket_integration.py")
logger.info("✅ CONSOLIDATION: Single manager for all Crypto.com WebSocket operations")