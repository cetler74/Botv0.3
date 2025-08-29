"""
Bybit WebSocket Integration - Version 2.6.0
FastAPI integration for Bybit WebSocket with health checks, status monitoring, and event processing
"""

import asyncio
import json
import logging
import os
from typing import Dict, Any, Optional
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse

from bybit_user_data_stream import BybitUserDataStreamManager
from bybit_event_processors import BybitEventProcessor
from bybit_health_monitor import BybitHealthMonitor

logger = logging.getLogger(__name__)

# Create router
router = APIRouter(prefix="/api/v1/websocket/bybit", tags=["bybit-websocket"])

# Global manager instance
bybit_manager: Optional[BybitUserDataStreamManager] = None
event_processor: Optional[BybitEventProcessor] = None
health_monitor: Optional[BybitHealthMonitor] = None

def initialize_bybit_websocket():
    """Initialize Bybit WebSocket manager"""
    global bybit_manager, event_processor, health_monitor
    
    try:
        # Get configuration from environment
        api_key = os.getenv('EXCHANGE_BYBIT_API_KEY') or os.getenv('BYBIT_API_KEY')
        api_secret = os.getenv('EXCHANGE_BYBIT_API_SECRET') or os.getenv('BYBIT_API_SECRET')
        websocket_url = os.getenv('BYBIT_WEBSOCKET_URL', 'wss://stream.bybit.com/v5/private')
        max_reconnect_attempts = int(os.getenv('BYBIT_WEBSOCKET_MAX_RECONNECT_ATTEMPTS', '10'))
        reconnect_delay = int(os.getenv('BYBIT_WEBSOCKET_RECONNECT_DELAY', '5'))
        heartbeat_interval = int(os.getenv('BYBIT_WEBSOCKET_HEARTBEAT_INTERVAL', '20'))
        
        if not api_key or not api_secret:
            logger.warning("⚠️ Bybit API credentials not configured")
            return
        
        # Initialize managers
        bybit_manager = BybitUserDataStreamManager(
            api_key=api_key,
            api_secret=api_secret,
            websocket_url=websocket_url,
            max_reconnect_attempts=max_reconnect_attempts,
            reconnect_delay=reconnect_delay,
            heartbeat_interval=heartbeat_interval
        )
        
        event_processor = BybitEventProcessor()
        health_monitor = BybitHealthMonitor("bybit-websocket")
        
        # Setup event callbacks
        setup_event_callbacks()
        
        # Note: Health monitoring will be started when async context is available
        
        logger.info("✅ Bybit WebSocket integration initialized")
        
    except Exception as e:
        logger.error(f"❌ Failed to initialize Bybit WebSocket: {e}")

async def start_bybit_websocket():
    """Start Bybit WebSocket services (async)"""
    global health_monitor, bybit_manager
    
    try:
        if health_monitor:
            # Start health monitoring
            asyncio.create_task(health_monitor.start_monitoring())
            
        if bybit_manager:
            # Start the WebSocket manager
            await bybit_manager.start()
            
        logger.info("🚀 Bybit WebSocket services started")
        
    except Exception as e:
        logger.error(f"❌ Failed to start Bybit WebSocket: {e}")

def setup_event_callbacks():
    """Setup event callbacks for Bybit WebSocket"""
    if not bybit_manager or not event_processor:
        return
    
    # Order callbacks
    bybit_manager.add_order_callback(handle_order_event)
    bybit_manager.add_execution_callback(handle_execution_event)
    bybit_manager.add_position_callback(handle_position_event)
    bybit_manager.add_wallet_callback(handle_wallet_event)
    bybit_manager.add_connection_callback(handle_connection_event)
    bybit_manager.add_error_callback(handle_error_event)
    
    logger.info("✅ Bybit event callbacks configured")

async def handle_order_event(order_event):
    """Handle order events"""
    try:
        logger.info(f"📋 Order event: {order_event.order_id} - {order_event.order_status}")
        
        # Process with event processor
        if event_processor:
            await event_processor._handle_order_update([{
                'orderId': order_event.order_id,
                'orderLinkId': order_event.order_link_id,
                'symbol': order_event.symbol,
                'side': order_event.side,
                'orderType': order_event.order_type,
                'price': order_event.price,
                'qty': order_event.qty,
                'cumExecQty': order_event.cum_exec_qty,
                'cumExecFee': order_event.cum_exec_fee,
                'avgPrice': order_event.avg_price,
                'orderStatus': order_event.order_status,
                'lastExecPrice': order_event.last_exec_price,
                'lastExecQty': order_event.last_exec_qty,
                'execTime': order_event.exec_time
            }], "snapshot", order_event.timestamp)
        
    except Exception as e:
        logger.error(f"❌ Error handling order event: {e}")

async def handle_execution_event(execution_event):
    """Handle execution events"""
    try:
        logger.info(f"⚡ Execution event: {execution_event.order_id} - {execution_event.exec_id}")
        
        # Process with event processor
        if event_processor:
            await event_processor._handle_execution_report([{
                'symbol': execution_event.symbol,
                'side': execution_event.side,
                'orderId': execution_event.order_id,
                'execId': execution_event.exec_id,
                'orderLinkId': execution_event.order_link_id,
                'price': execution_event.price,
                'qty': execution_event.qty,
                'execFee': execution_event.exec_fee,
                'execTime': execution_event.exec_time
            }], "snapshot", execution_event.timestamp)
        
    except Exception as e:
        logger.error(f"❌ Error handling execution event: {e}")

async def handle_position_event(position_event):
    """Handle position events"""
    try:
        logger.info(f"📈 Position event: {position_event.symbol} - {position_event.size}")
        
        # Process with event processor
        if event_processor:
            await event_processor._handle_position_update([{
                'symbol': position_event.symbol,
                'side': position_event.side,
                'size': position_event.size,
                'avgPrice': position_event.avg_price,
                'unrealizedPnl': position_event.unrealized_pnl,
                'markPrice': position_event.mark_price,
                'positionValue': position_event.position_value
            }], "snapshot", position_event.timestamp)
        
    except Exception as e:
        logger.error(f"❌ Error handling position event: {e}")

async def handle_wallet_event(wallet_event):
    """Handle wallet events"""
    try:
        logger.info(f"💰 Wallet event: {wallet_event.currency} - {wallet_event.wallet_balance}")
        
        # Process with event processor
        if event_processor:
            await event_processor._handle_wallet_update([{
                'currency': wallet_event.currency,
                'walletBalance': wallet_event.wallet_balance,
                'availableBalance': wallet_event.available_balance
            }], "snapshot", wallet_event.timestamp)
        
    except Exception as e:
        logger.error(f"❌ Error handling wallet event: {e}")

async def handle_connection_event(connected: bool):
    """Handle connection events"""
    try:
        status = "Connected" if connected else "Disconnected"
        logger.info(f"🔌 Bybit WebSocket {status}")
        
    except Exception as e:
        logger.error(f"❌ Error handling connection event: {e}")

async def handle_error_event(error: Exception):
    """Handle error events"""
    try:
        logger.error(f"❌ Bybit WebSocket error: {error}")
        
    except Exception as e:
        logger.error(f"❌ Error handling error event: {e}")

@router.get("/health")
async def get_bybit_websocket_health():
    """Get Bybit WebSocket health status"""
    try:
        if not bybit_manager:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "unavailable",
                    "message": "Bybit WebSocket manager not initialized",
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
            )
        
        status = bybit_manager.get_status()
        
        # Determine health status
        if status["connected"] and status["running"]:
            health_status = "healthy"
        elif status["connected"]:
            health_status = "connected"
        else:
            health_status = "disconnected"
        
        return {
            "status": health_status,
            "connected": status["connected"],
            "running": status["running"],
            "state": status["state"],
            "subscribed_channels": status["subscribed_channels"],
            "connection_metrics": status["connection_metrics"],
            "event_metrics": status["event_metrics"],
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
    except Exception as e:
        logger.error(f"❌ Error getting Bybit WebSocket health: {e}")
        raise HTTPException(status_code=500, detail=f"Health check failed: {str(e)}")

@router.get("/status")
async def get_bybit_websocket_status():
    """Get detailed Bybit WebSocket status"""
    try:
        if not bybit_manager:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "unavailable",
                    "message": "Bybit WebSocket manager not initialized",
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
            )
        
        status = bybit_manager.get_status()
        
        return {
            "manager_status": status,
            "event_processor_metrics": event_processor.get_metrics() if event_processor else None,
            "configuration": {
                "websocket_url": bybit_manager.websocket_url,
                "max_reconnect_attempts": bybit_manager.connection_manager.max_reconnect_attempts,
                "reconnect_delay": bybit_manager.connection_manager.reconnect_delay,
                "heartbeat_interval": bybit_manager.connection_manager.heartbeat_interval
            },
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
    except Exception as e:
        logger.error(f"❌ Error getting Bybit WebSocket status: {e}")
        raise HTTPException(status_code=500, detail=f"Status check failed: {str(e)}")

@router.post("/start")
async def start_bybit_websocket(background_tasks: BackgroundTasks):
    """Start Bybit WebSocket connection"""
    try:
        if not bybit_manager:
            raise HTTPException(status_code=503, detail="Bybit WebSocket manager not initialized")
        
        if bybit_manager.connection_manager.is_running:
            return {
                "status": "already_running",
                "message": "Bybit WebSocket is already running",
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        
        # Start in background
        background_tasks.add_task(start_bybit_websocket_task)
        
        return {
            "status": "starting",
            "message": "Bybit WebSocket starting in background",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
    except Exception as e:
        logger.error(f"❌ Error starting Bybit WebSocket: {e}")
        raise HTTPException(status_code=500, detail=f"Start failed: {str(e)}")

async def start_bybit_websocket_task():
    """Background task to start Bybit WebSocket"""
    try:
        if bybit_manager:
            success = await bybit_manager.start()
            if success:
                logger.info("✅ Bybit WebSocket started successfully")
            else:
                logger.error("❌ Failed to start Bybit WebSocket")
    except Exception as e:
        logger.error(f"❌ Error in Bybit WebSocket start task: {e}")

@router.post("/stop")
async def stop_bybit_websocket():
    """Stop Bybit WebSocket connection"""
    try:
        if not bybit_manager:
            raise HTTPException(status_code=503, detail="Bybit WebSocket manager not initialized")
        
        if not bybit_manager.connection_manager.is_running:
            return {
                "status": "not_running",
                "message": "Bybit WebSocket is not running",
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        
        await bybit_manager.stop()
        
        return {
            "status": "stopped",
            "message": "Bybit WebSocket stopped successfully",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
    except Exception as e:
        logger.error(f"❌ Error stopping Bybit WebSocket: {e}")
        raise HTTPException(status_code=500, detail=f"Stop failed: {str(e)}")

@router.post("/restart")
async def restart_bybit_websocket(background_tasks: BackgroundTasks):
    """Restart Bybit WebSocket connection"""
    try:
        if not bybit_manager:
            raise HTTPException(status_code=503, detail="Bybit WebSocket manager not initialized")
        
        # Stop if running
        if bybit_manager.connection_manager.is_running:
            await bybit_manager.stop()
        
        # Start in background
        background_tasks.add_task(start_bybit_websocket_task)
        
        return {
            "status": "restarting",
            "message": "Bybit WebSocket restarting in background",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
    except Exception as e:
        logger.error(f"❌ Error restarting Bybit WebSocket: {e}")
        raise HTTPException(status_code=500, detail=f"Restart failed: {str(e)}")

@router.post("/reconnect")
async def reconnect_bybit_websocket():
    """Manually reconnect Bybit WebSocket"""
    try:
        if not bybit_manager:
            raise HTTPException(status_code=503, detail="Bybit WebSocket manager not initialized")
        
        success = await bybit_manager.connection_manager.reconnect()
        
        if success:
            return {
                "status": "reconnected",
                "message": "Bybit WebSocket reconnected successfully",
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        else:
            return {
                "status": "reconnect_failed",
                "message": "Bybit WebSocket reconnection failed",
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        
    except Exception as e:
        logger.error(f"❌ Error reconnecting Bybit WebSocket: {e}")
        raise HTTPException(status_code=500, detail=f"Reconnect failed: {str(e)}")

@router.get("/metrics")
async def get_bybit_websocket_metrics():
    """Get Bybit WebSocket metrics"""
    try:
        if not bybit_manager:
            raise HTTPException(status_code=503, detail="Bybit WebSocket manager not initialized")
        
        manager_metrics = bybit_manager.get_status()
        processor_metrics = event_processor.get_metrics() if event_processor else None
        health_metrics = health_monitor.get_health_status() if health_monitor else None
        
        return {
            "manager_metrics": manager_metrics,
            "event_processor_metrics": processor_metrics,
            "health_metrics": health_metrics,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
    except Exception as e:
        logger.error(f"❌ Error getting Bybit WebSocket metrics: {e}")
        raise HTTPException(status_code=500, detail=f"Metrics retrieval failed: {str(e)}")

@router.get("/health")
async def get_bybit_websocket_health():
    """Get Bybit WebSocket health status"""
    try:
        if not health_monitor:
            raise HTTPException(status_code=503, detail="Bybit health monitor not initialized")
        
        health_status = health_monitor.get_health_status()
        
        return {
            "status": "success",
            "data": health_status,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
    except Exception as e:
        logger.error(f"❌ Error getting Bybit WebSocket health: {e}")
        raise HTTPException(status_code=500, detail=f"Health check failed: {str(e)}")

@router.get("/health/history")
async def get_bybit_websocket_health_history(limit: int = 50):
    """Get Bybit WebSocket health history"""
    try:
        if not health_monitor:
            raise HTTPException(status_code=503, detail="Bybit health monitor not initialized")
        
        health_history = health_monitor.get_health_history(limit)
        
        return {
            "status": "success",
            "data": health_history,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
    except Exception as e:
        logger.error(f"❌ Error getting Bybit WebSocket health history: {e}")
        raise HTTPException(status_code=500, detail=f"Health history retrieval failed: {str(e)}")

@router.post("/health/reset")
async def reset_bybit_websocket_health():
    """Reset Bybit WebSocket health metrics"""
    try:
        if not health_monitor:
            raise HTTPException(status_code=503, detail="Bybit health monitor not initialized")
        
        health_monitor.reset_metrics()
        
        return {
            "status": "success",
            "message": "Bybit WebSocket health metrics reset successfully",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
    except Exception as e:
        logger.error(f"❌ Error resetting Bybit WebSocket health metrics: {e}")
        raise HTTPException(status_code=500, detail=f"Health metrics reset failed: {str(e)}")

@router.post("/reset-metrics")
async def reset_bybit_websocket_metrics():
    """Reset Bybit WebSocket metrics"""
    try:
        if not bybit_manager:
            raise HTTPException(status_code=503, detail="Bybit WebSocket manager not initialized")
        
        bybit_manager.reset_metrics()
        if event_processor:
            event_processor.reset_metrics()
        
        return {
            "status": "reset",
            "message": "Bybit WebSocket metrics reset successfully",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
    except Exception as e:
        logger.error(f"❌ Error resetting Bybit WebSocket metrics: {e}")
        raise HTTPException(status_code=500, detail=f"Metrics reset failed: {str(e)}")

@router.get("/events")
async def get_bybit_websocket_events():
    """Get recent Bybit WebSocket events"""
    try:
        if not event_processor:
            raise HTTPException(status_code=503, detail="Bybit event processor not initialized")
        
        metrics = event_processor.get_metrics()
        
        return {
            "events_processed": metrics["events_processed"],
            "events_by_type": metrics["events_by_type"],
            "processing_errors": metrics["processing_errors"],
            "last_event_time": metrics["last_event_time"],
            "uptime_start": metrics["uptime_start"],
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
    except Exception as e:
        logger.error(f"❌ Error getting Bybit WebSocket events: {e}")
        raise HTTPException(status_code=500, detail=f"Events retrieval failed: {str(e)}")

# Initialize on module import
initialize_bybit_websocket()
