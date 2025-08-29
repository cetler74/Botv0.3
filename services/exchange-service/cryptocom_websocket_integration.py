"""
Crypto.com WebSocket Integration for Exchange Service - Version 2.6.0
Integrates Crypto.com User Data Stream with the existing exchange service
"""

import asyncio
import logging
import os
from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException
from cryptocom_user_data_stream import CryptocomUserDataStreamManager
from cryptocom_event_processors import CryptocomEventProcessorManager
from cryptocom_market_websocket import CryptocomMarketWebSocket

logger = logging.getLogger(__name__)

class CryptocomWebSocketIntegration:
    """
    Integrates Crypto.com User Data Stream WebSocket with exchange service
    Manages lifecycle and event processing
    """
    
    def __init__(self):
        # Configuration
        self.enabled = os.getenv("CRYPTOCOM_ENABLE_USER_DATA_STREAM", "false").lower() == "true"
        self.api_key = os.getenv("CRYPTOCOM_API_KEY")
        self.api_secret = os.getenv("CRYPTOCOM_API_SECRET")
        self.websocket_url = os.getenv("CRYPTOCOM_WEBSOCKET_URL", "wss://stream.crypto.com/exchange/v1/user")
        
        # Components
        self.stream_manager: Optional[CryptocomUserDataStreamManager] = None
        self.event_processor_manager: Optional[CryptocomEventProcessorManager] = None
        self.market_websocket: Optional[CryptocomMarketWebSocket] = None
        
        # State
        self.is_initialized = False
        self.is_running = False
        
        logger.info(f"🏢 Crypto.com WebSocket Integration initialized (enabled: {self.enabled})")
    
    async def initialize(self) -> bool:
        """Initialize the WebSocket integration"""
        if not self.enabled:
            logger.info("🏢 Crypto.com User Data Stream disabled by configuration")
            return True
            
        if not self.api_key or not self.api_secret:
            logger.error("❌ Crypto.com API credentials not configured for User Data Stream")
            return False
        
        try:
            logger.info("🚀 Initializing Crypto.com WebSocket Integration")
            
            # Create stream manager
            self.stream_manager = CryptocomUserDataStreamManager(
                api_key=self.api_key,
                api_secret=self.api_secret,
                base_url=self.websocket_url
            )
            
            # Create event processor manager
            self.event_processor_manager = CryptocomEventProcessorManager()
            
            # Create market data WebSocket (always enabled for price feeds)
            self.market_websocket = CryptocomMarketWebSocket()
            
            # Import ticker cache update function from main module
            from main import _update_ticker_cache
            
            # Register market data callbacks
            self.market_websocket.add_ticker_callback(self._handle_ticker_update)
            self.market_websocket.add_error_callback(self._handle_market_error)
            self.market_websocket.add_connection_callback(self._handle_market_connection_change)
            
            # Store ticker cache update function
            self._update_ticker_cache = _update_ticker_cache
            
            # Register event callbacks
            self.stream_manager.add_order_callback(self._handle_order_event)
            self.stream_manager.add_trade_callback(self._handle_trade_event)
            self.stream_manager.add_balance_callback(self._handle_balance_event)
            self.stream_manager.add_error_callback(self._handle_error)
            self.stream_manager.add_connection_callback(self._handle_connection_change)
            
            self.is_initialized = True
            logger.info("✅ Crypto.com WebSocket Integration initialized successfully")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to initialize Crypto.com WebSocket Integration: {e}")
            return False
    
    async def start(self) -> bool:
        """Start the WebSocket integration"""
        if not self.enabled:
            return True
            
        if not self.is_initialized:
            logger.error("❌ Cannot start - integration not initialized")
            return False
        
        try:
            logger.info("🎯 Starting Crypto.com WebSocket Integration")
            
            # Start stream manager (user data)
            user_success = await self.stream_manager.start()
            
            # Start market data WebSocket (always start for price feeds)
            market_success = await self.market_websocket.start()
            
            if market_success:
                self.is_running = True
                if user_success:
                    logger.info("✅ Crypto.com WebSocket Integration started successfully (User Data + Market Data)")
                else:
                    logger.info("✅ Crypto.com WebSocket Integration started successfully (Market Data only)")
                return True
            else:
                logger.error("❌ Failed to start market data WebSocket")
                return False
                
        except Exception as e:
            logger.error(f"❌ Failed to start Crypto.com WebSocket Integration: {e}")
            return False
    
    async def stop(self):
        """Stop the WebSocket integration"""
        if not self.enabled or not self.is_running:
            return
            
        try:
            logger.info("🛑 Stopping Crypto.com WebSocket Integration")
            
            if self.stream_manager:
                await self.stream_manager.stop()
            
            if self.market_websocket:
                await self.market_websocket.stop()
            
            self.is_running = False
            logger.info("✅ Crypto.com WebSocket Integration stopped")
            
        except Exception as e:
            logger.error(f"❌ Error stopping Crypto.com WebSocket Integration: {e}")
    
    # Event handlers
    async def _handle_order_event(self, event_data: Dict[str, Any]):
        """Handle order events from WebSocket"""
        if self.event_processor_manager:
            try:
                processed_event = await self.event_processor_manager.process_event("user.order", event_data)
                if processed_event:
                    logger.debug(f"📋 Processed Crypto.com order event: {processed_event['order_id']}")
            except Exception as e:
                logger.error(f"❌ Error handling order event: {e}")
    
    async def _handle_trade_event(self, event_data: Dict[str, Any]):
        """Handle trade events from WebSocket"""
        if self.event_processor_manager:
            try:
                processed_event = await self.event_processor_manager.process_event("user.trade", event_data)
                if processed_event:
                    logger.debug(f"💰 Processed Crypto.com trade event: {processed_event['trade_id']}")
            except Exception as e:
                logger.error(f"❌ Error handling trade event: {e}")
    
    async def _handle_balance_event(self, event_data: Dict[str, Any]):
        """Handle balance events from WebSocket"""
        if self.event_processor_manager:
            try:
                processed_event = await self.event_processor_manager.process_event("user.balance", event_data)
                if processed_event:
                    logger.debug(f"💳 Processed Crypto.com balance event: {processed_event['currency']}")
            except Exception as e:
                logger.error(f"❌ Error handling balance event: {e}")
    
    async def _handle_error(self, error: str):
        """Handle error events"""
        logger.error(f"🚨 Crypto.com WebSocket Error: {error}")
        
        # You could add alerting logic here
        # For example, send to monitoring service or create alert
    
    async def _handle_connection_change(self, connected: bool):
        """Handle connection status changes"""
        if connected:
            logger.info("🔌 Crypto.com WebSocket connected")
        else:
            logger.warning("🔌 Crypto.com WebSocket disconnected")
    
    # Market data callback handlers
    async def _handle_ticker_update(self, exchange: str, symbol: str, last_price: float, bid_price: float, ask_price: float):
        """Handle ticker updates from market data WebSocket"""
        try:
            # Update ticker cache using the imported function
            self._update_ticker_cache(exchange, symbol, last_price, bid_price, ask_price)
            logger.debug(f"📊 Updated ticker cache: {exchange}/{symbol} = ${last_price:.8f}")
        except Exception as e:
            logger.error(f"❌ Error updating ticker cache: {e}")
    
    async def _handle_market_error(self, error: Exception):
        """Handle market data WebSocket errors"""
        logger.error(f"🚨 Crypto.com Market Data WebSocket Error: {error}")
    
    async def _handle_market_connection_change(self, connected: bool):
        """Handle market data WebSocket connection changes"""
        if connected:
            logger.info("📊 Crypto.com Market Data WebSocket connected")
        else:
            logger.warning("📊 Crypto.com Market Data WebSocket disconnected")
    
    # Status methods
    def get_status(self) -> Dict[str, Any]:
        """Get integration status"""
        if not self.enabled:
            return {
                'enabled': False,
                'status': 'disabled',
                'connected': False
            }
        
        if not self.stream_manager:
            return {
                'enabled': True,
                'status': 'not_initialized',
                'initialized': self.is_initialized,
                'running': self.is_running,
                'connected': False
            }
        
        # Get stream status for connection info
        stream_status = self.stream_manager.get_status()
        is_user_connected = stream_status.get('is_connected', False) and stream_status.get('is_running', False)
        
        # Get market data WebSocket status
        market_status = self.market_websocket.get_status() if self.market_websocket else {}
        is_market_connected = market_status.get('connected', False)
        
        return {
            'enabled': True,
            'status': 'active' if self.is_running else 'initialized',
            'initialized': self.is_initialized,
            'running': self.is_running,
            'connected': is_market_connected,  # Market data connection is primary for price feeds
            'user_data_connected': is_user_connected,
            'market_data_connected': is_market_connected,
            'stream_status': stream_status,
            'market_data_status': market_status,
            'stream_healthy': self.stream_manager.is_healthy()
        }
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get detailed metrics"""
        if not self.enabled or not self.stream_manager:
            return {'enabled': self.enabled}
        
        metrics = {
            'enabled': True,
            'initialized': self.is_initialized,
            'running': self.is_running,
            'stream_metrics': self.stream_manager.get_metrics()
        }
        
        if self.event_processor_manager:
            metrics['event_processor_metrics'] = self.event_processor_manager.get_comprehensive_metrics()
        
        return metrics

# Global instance
cryptocom_websocket = CryptocomWebSocketIntegration()

# FastAPI router for WebSocket endpoints
router = APIRouter(prefix="/api/v1/websocket/cryptocom", tags=["cryptocom-websocket"])

@router.get("/status")
async def get_cryptocom_websocket_status():
    """Get Crypto.com WebSocket integration status"""
    return cryptocom_websocket.get_status()

@router.get("/metrics")
async def get_cryptocom_websocket_metrics():
    """Get detailed Crypto.com WebSocket metrics"""
    return cryptocom_websocket.get_metrics()

@router.post("/restart")
async def restart_cryptocom_websocket():
    """Restart Crypto.com WebSocket connection"""
    if not cryptocom_websocket.enabled:
        raise HTTPException(status_code=400, detail="Crypto.com WebSocket integration disabled")
    
    try:
        logger.info("🔄 Restarting Crypto.com WebSocket integration via API")
        
        # Stop and start
        await cryptocom_websocket.stop()
        await asyncio.sleep(2)  # Brief pause
        success = await cryptocom_websocket.start()
        
        if success:
            return {"message": "Crypto.com WebSocket restarted successfully"}
        else:
            raise HTTPException(status_code=500, detail="Failed to restart WebSocket")
            
    except Exception as e:
        logger.error(f"❌ Error restarting WebSocket: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/enable")
async def enable_cryptocom_websocket():
    """Enable Crypto.com WebSocket integration (if disabled)"""
    if cryptocom_websocket.enabled:
        return {"message": "Already enabled"}
    
    # This would require configuration changes and restart
    raise HTTPException(
        status_code=400, 
        detail="WebSocket integration can only be enabled via environment configuration"
    )

@router.post("/disable")
async def disable_cryptocom_websocket():
    """Temporarily disable Crypto.com WebSocket integration"""
    if not cryptocom_websocket.enabled:
        return {"message": "Already disabled"}
    
    try:
        await cryptocom_websocket.stop()
        return {"message": "Crypto.com WebSocket integration disabled"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Health check endpoint for monitoring
@router.get("/health")
async def cryptocom_websocket_health():
    """Health check for Crypto.com WebSocket integration"""
    status = cryptocom_websocket.get_status()
    
    if not status.get('enabled'):
        return {"status": "disabled", "healthy": True}
    
    is_healthy = status.get('stream_healthy', False)
    
    if is_healthy:
        return {"status": "healthy", "healthy": True}
    else:
        raise HTTPException(
            status_code=503, 
            detail="Crypto.com WebSocket connection unhealthy"
        )