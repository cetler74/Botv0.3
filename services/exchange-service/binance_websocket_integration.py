"""
Binance WebSocket Integration for Exchange Service - Version 2.5.0
Integrates Binance User Data Stream with the existing exchange service
"""

import asyncio
import logging
import os
from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException
from binance_user_data_stream import BinanceUserDataStreamManager, ExecutionReport
from event_processors import ExecutionReportProcessor, AccountUpdateProcessor

logger = logging.getLogger(__name__)

class BinanceWebSocketIntegration:
    """
    Integrates Binance User Data Stream WebSocket with exchange service
    Manages lifecycle and event processing
    """
    
    def __init__(self):
        # Configuration
        self.enabled = os.getenv("BINANCE_ENABLE_USER_DATA_STREAM", "false").lower() == "true"
        self.api_key = os.getenv("BINANCE_API_KEY")
        self.api_secret = os.getenv("BINANCE_API_SECRET")
        
        # Components
        self.stream_manager: Optional[BinanceUserDataStreamManager] = None
        self.execution_processor: Optional[ExecutionReportProcessor] = None
        self.account_processor: Optional[AccountUpdateProcessor] = None
        
        # State
        self.is_initialized = False
        self.is_running = False
        
        logger.info(f"🔌 Binance WebSocket Integration initialized (enabled: {self.enabled})")
    
    async def initialize(self) -> bool:
        """Initialize the WebSocket integration"""
        if not self.enabled:
            logger.info("🔌 Binance User Data Stream disabled by configuration")
            return True
            
        if not self.api_key or not self.api_secret:
            logger.error("❌ Binance API credentials not configured for User Data Stream")
            return False
        
        try:
            logger.info("🚀 Initializing Binance WebSocket Integration")
            
            # Create stream manager
            self.stream_manager = BinanceUserDataStreamManager(
                api_key=self.api_key,
                api_secret=self.api_secret
            )
            
            # Create processors
            self.execution_processor = ExecutionReportProcessor()
            self.account_processor = AccountUpdateProcessor()
            
            # Register event callbacks
            self.stream_manager.add_execution_callback(self._handle_execution_report)
            self.stream_manager.add_position_callback(self._handle_position_update)
            self.stream_manager.add_balance_callback(self._handle_balance_update)
            self.stream_manager.add_error_callback(self._handle_error)
            self.stream_manager.add_connection_callback(self._handle_connection_change)
            
            self.is_initialized = True
            logger.info("✅ Binance WebSocket Integration initialized successfully")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to initialize Binance WebSocket Integration: {e}")
            return False
    
    async def start(self) -> bool:
        """Start the WebSocket integration"""
        if not self.enabled:
            return True
            
        if not self.is_initialized:
            logger.error("❌ Cannot start - integration not initialized")
            return False
        
        try:
            logger.info("🎯 Starting Binance WebSocket Integration")
            
            # Start stream manager
            success = await self.stream_manager.start()
            if success:
                self.is_running = True
                logger.info("✅ Binance WebSocket Integration started successfully")
                return True
            else:
                logger.error("❌ Failed to start stream manager")
                return False
                
        except Exception as e:
            logger.error(f"❌ Failed to start Binance WebSocket Integration: {e}")
            return False
    
    async def stop(self):
        """Stop the WebSocket integration"""
        if not self.enabled or not self.is_running:
            return
            
        try:
            logger.info("🛑 Stopping Binance WebSocket Integration")
            
            if self.stream_manager:
                await self.stream_manager.stop()
            
            self.is_running = False
            logger.info("✅ Binance WebSocket Integration stopped")
            
        except Exception as e:
            logger.error(f"❌ Error stopping Binance WebSocket Integration: {e}")
    
    # Event handlers
    async def _handle_execution_report(self, execution_report: ExecutionReport):
        """Handle execution report events"""
        if self.execution_processor:
            await self.execution_processor.process_execution_report(execution_report)
    
    async def _handle_position_update(self, position_data: Dict[str, Any]):
        """Handle position update events"""
        if self.account_processor:
            await self.account_processor.process_position_update(position_data)
    
    async def _handle_balance_update(self, balance_data: Dict[str, Any]):
        """Handle balance update events"""
        if self.account_processor:
            await self.account_processor.process_balance_update(balance_data)
    
    async def _handle_error(self, error: str):
        """Handle error events"""
        logger.error(f"🚨 Binance WebSocket Error: {error}")
        
        # You could add alerting logic here
        # For example, send to monitoring service or create alert
    
    async def _handle_connection_change(self, connected: bool):
        """Handle connection status changes"""
        if connected:
            logger.info("🔌 Binance WebSocket connected")
        else:
            logger.warning("🔌 Binance WebSocket disconnected")
    
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
        is_connected = stream_status.get('is_connected', False) and stream_status.get('is_running', False)
        
        return {
            'enabled': True,
            'status': 'active' if self.is_running else 'initialized',
            'initialized': self.is_initialized,
            'running': self.is_running,
            'connected': is_connected,  # Add connected field for orchestrator compatibility
            'stream_status': stream_status,
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
        
        if self.execution_processor:
            metrics['execution_processor_metrics'] = self.execution_processor.get_metrics()
        
        if self.account_processor:
            metrics['account_processor_metrics'] = self.account_processor.get_metrics()
        
        return metrics

# Global instance
binance_websocket = BinanceWebSocketIntegration()

# FastAPI router for WebSocket endpoints
router = APIRouter(prefix="/api/v1/websocket/binance", tags=["binance-websocket"])

@router.get("/status")
async def get_binance_websocket_status():
    """Get Binance WebSocket integration status"""
    return binance_websocket.get_status()

@router.get("/metrics")
async def get_binance_websocket_metrics():
    """Get detailed Binance WebSocket metrics"""
    return binance_websocket.get_metrics()

@router.post("/restart")
async def restart_binance_websocket():
    """Restart Binance WebSocket connection"""
    if not binance_websocket.enabled:
        raise HTTPException(status_code=400, detail="Binance WebSocket integration disabled")
    
    try:
        logger.info("🔄 Restarting Binance WebSocket integration via API")
        
        # Stop and start
        await binance_websocket.stop()
        await asyncio.sleep(2)  # Brief pause
        success = await binance_websocket.start()
        
        if success:
            return {"message": "Binance WebSocket restarted successfully"}
        else:
            raise HTTPException(status_code=500, detail="Failed to restart WebSocket")
            
    except Exception as e:
        logger.error(f"❌ Error restarting WebSocket: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/enable")
async def enable_binance_websocket():
    """Enable Binance WebSocket integration (if disabled)"""
    if binance_websocket.enabled:
        return {"message": "Already enabled"}
    
    # This would require configuration changes and restart
    raise HTTPException(
        status_code=400, 
        detail="WebSocket integration can only be enabled via environment configuration"
    )

@router.post("/disable")
async def disable_binance_websocket():
    """Temporarily disable Binance WebSocket integration"""
    if not binance_websocket.enabled:
        return {"message": "Already disabled"}
    
    try:
        await binance_websocket.stop()
        return {"message": "Binance WebSocket integration disabled"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Health check endpoint for monitoring
@router.get("/health")
async def binance_websocket_health():
    """Health check for Binance WebSocket integration"""
    status = binance_websocket.get_status()
    
    if not status.get('enabled'):
        return {"status": "disabled", "healthy": True}
    
    is_healthy = status.get('stream_healthy', False)
    
    if is_healthy:
        return {"status": "healthy", "healthy": True}
    else:
        raise HTTPException(
            status_code=503, 
            detail="Binance WebSocket connection unhealthy"
        )