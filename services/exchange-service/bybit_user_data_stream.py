"""
Bybit User Data Stream Manager - Version 2.6.0
Main manager for Bybit WebSocket connections with authentication, event processing, and integration
"""

import asyncio
import json
import logging
import time
from typing import Dict, Any, List, Optional, Callable
from datetime import datetime, timezone
from dataclasses import dataclass

from bybit_auth_manager import BybitAuthManager
# PnL-FIX v3: Use the Bybit-specific connection manager, which has the full auth /
# heartbeat / subscription / reconnect flow. The previous code imported the
# generic ``connection_manager.ConnectionManager`` whose API is incompatible
# (no ``is_running`` / ``add_connection_callback`` / etc.), leaving
# ``bybit_manager`` None and causing every
# ``/api/v1/websocket/bybit/status`` call to 503.
from bybit_connection_manager import BybitConnectionManager, ConnectionState

logger = logging.getLogger(__name__)

@dataclass
class BybitExecutionReport:
    """Bybit execution report data structure"""
    order_id: str
    symbol: str
    side: str
    order_type: str
    price: float
    qty: float
    cum_exec_qty: float
    cum_exec_fee: float
    avg_price: float
    order_status: str
    order_link_id: str
    last_exec_price: float
    last_exec_qty: float
    exec_time: str

class BybitUserDataStreamManager:
    """
    Main manager for Bybit User Data Stream WebSocket connections
    
    Features:
    - Complete WebSocket lifecycle management
    - Authentication with retry mechanisms
    - Event processing and callback system
    - Health monitoring and metrics
    - Integration with existing trading infrastructure
    """
    
    def __init__(self, 
                 api_key: str, 
                 api_secret: str, 
                 websocket_url: str = "wss://stream.bybit.com/v5/private",
                 max_reconnect_attempts: int = 10,
                 reconnect_delay: int = 5,
                 heartbeat_interval: int = 20):
        """
        Initialize Bybit User Data Stream manager
        
        Args:
            api_key: Bybit API key
            api_secret: Bybit API secret
            websocket_url: Bybit WebSocket URL
            max_reconnect_attempts: Maximum reconnection attempts
            reconnect_delay: Delay between reconnection attempts (seconds)
            heartbeat_interval: Heartbeat interval (seconds)
        """
        self.api_key = api_key
        self.api_secret = api_secret
        self.websocket_url = websocket_url

        # PnL-FIX v3: BybitConnectionManager handles auth + heartbeat + channel
        # subscription internally. Previously this wrapper tried to reimplement
        # that on top of the generic ConnectionManager and failed.
        self.connection_manager = BybitConnectionManager(
            websocket_url=websocket_url,
            api_key=api_key,
            api_secret=api_secret,
            max_reconnect_attempts=max_reconnect_attempts,
            reconnect_delay=reconnect_delay,
            heartbeat_interval=heartbeat_interval,
        )

        # Store connection parameters for later use
        self.max_reconnect_attempts = max_reconnect_attempts
        self.reconnect_delay = reconnect_delay
        self.heartbeat_interval = heartbeat_interval
        
        # Event callbacks
        self.event_callbacks: Dict[str, List[Callable]] = {
            'order': [],
            'execution': [],
            'position': [],
            'wallet': [],
            'error': [],
            'connection': []
        }
        
        # Event processing
        self.event_processor = BybitEventProcessor()
        
        # Metrics and monitoring
        self.metrics = {
            "events_processed": 0,
            "events_by_type": {
                "order": 0,
                "execution": 0,
                "position": 0,
                "wallet": 0
            },
            "processing_errors": 0,
            "last_event_time": None,
            "uptime_start": datetime.now(timezone.utc).isoformat()
        }
        
        # Setup callbacks
        self._setup_callbacks()
        
        logger.info("🏢 Bybit User Data Stream Manager initialized")
    
    def _setup_callbacks(self):
        """Setup connection and message callbacks on the BybitConnectionManager.

        PnL-FIX v3: Uses ``BybitConnectionManager``'s native
        ``add_connection_callback`` (signature ``callback(connected: bool)``),
        which matches our ``_on_connection_change`` handler exactly — no lambda
        wrapping or dual (connected/disconnected) registration needed.
        """
        self.connection_manager.add_connection_callback(self._on_connection_change)
        self.connection_manager.add_error_callback(self._on_error)
        self.connection_manager.add_message_callback(self._on_message)

    async def start(self) -> bool:
        """Start the WebSocket connection and event processing.

        PnL-FIX v3: ``BybitConnectionManager.connect()`` takes no arguments and
        runs the full auth + subscribe + heartbeat flow in background tasks.
        Previously this wrapper passed a ``connect_func`` intended for the
        generic ConnectionManager, which no longer applies.
        """
        try:
            logger.info("🚀 Starting Bybit User Data Stream")

            success = await self.connection_manager.connect()
            if not success:
                logger.error("❌ Failed to start Bybit User Data Stream")
                return False

            logger.info("✅ Bybit User Data Stream started successfully")
            return True

        except Exception as e:
            logger.error(f"❌ Error starting Bybit User Data Stream: {e}")
            return False
    
    async def stop(self):
        """Stop the WebSocket connection"""
        try:
            logger.info("🛑 Stopping Bybit User Data Stream")
            await self.connection_manager.disconnect()
            logger.info("✅ Bybit User Data Stream stopped")
            
        except Exception as e:
            logger.error(f"❌ Error stopping Bybit User Data Stream: {e}")
    
    async def _on_connection_change(self, connected: bool):
        """Handle connection state changes"""
        logger.info(f"🔌 Bybit WebSocket connection: {'Connected' if connected else 'Disconnected'}")
        
        # Notify connection callbacks
        for callback in self.event_callbacks['connection']:
            try:
                await callback(connected)
            except Exception as e:
                logger.error(f"❌ Connection callback error: {e}")
    
    async def _on_error(self, error: Exception):
        """Handle connection errors"""
        logger.error(f"❌ Bybit WebSocket error: {error}")
        
        # Notify error callbacks
        for callback in self.event_callbacks['error']:
            try:
                await callback(error)
            except Exception as e:
                logger.error(f"❌ Error callback error: {e}")
    
    async def _on_message(self, message: Dict[str, Any]):
        """Handle incoming WebSocket messages"""
        try:
            # Process the message
            await self._process_message(message)
            
        except Exception as e:
            logger.error(f"❌ Error processing message: {e}")
            self.metrics["processing_errors"] += 1
    
    async def _process_message(self, message: Dict[str, Any]):
        """Process incoming WebSocket message"""
        topic = message.get("topic")
        event_type = message.get("type")
        timestamp = message.get("ts")
        data = message.get("data", [])
        
        if not topic:
            logger.debug(f"📨 Received non-event message: {message}")
            return
        
        logger.debug(f"📊 Processing {topic} event: {event_type}")
        
        # Update metrics
        self.metrics["events_processed"] += 1
        self.metrics["last_event_time"] = datetime.now(timezone.utc).isoformat()
        
        if topic in self.metrics["events_by_type"]:
            self.metrics["events_by_type"][topic] += 1
        
        # Process event based on topic
        if topic == "order":
            await self._handle_order_event(data, event_type, timestamp)
        elif topic == "execution":
            await self._handle_execution_event(data, event_type, timestamp)
        elif topic == "position":
            await self._handle_position_event(data, event_type, timestamp)
        elif topic == "wallet":
            await self._handle_wallet_event(data, event_type, timestamp)
    
    async def _handle_order_event(self, data: List[Dict[str, Any]], event_type: str, timestamp: int):
        """Handle order events"""
        for order in data:
            try:
                # Process order event
                order_event = self._parse_order_event(order)
                
                # Notify order callbacks
                for callback in self.event_callbacks['order']:
                    try:
                        await callback(order_event)
                    except Exception as e:
                        logger.error(f"❌ Order callback error: {e}")
                
                logger.info(f"📋 Order event processed: {order_event.order_id} - {order_event.order_status}")
                
            except Exception as e:
                logger.error(f"❌ Error processing order event: {e}")
    
    async def _handle_execution_event(self, data: List[Dict[str, Any]], event_type: str, timestamp: int):
        """Handle execution events"""
        for execution in data:
            try:
                # Process execution event
                execution_event = self._parse_execution_event(execution)
                
                # Notify execution callbacks
                for callback in self.event_callbacks['execution']:
                    try:
                        await callback(execution_event)
                    except Exception as e:
                        logger.error(f"❌ Execution callback error: {e}")
                
                logger.info(f"⚡ Execution event processed: {execution_event.order_id}")
                
            except Exception as e:
                logger.error(f"❌ Error processing execution event: {e}")
    
    async def _handle_position_event(self, data: List[Dict[str, Any]], event_type: str, timestamp: int):
        """Handle position events"""
        for position in data:
            try:
                # Notify position callbacks
                for callback in self.event_callbacks['position']:
                    try:
                        await callback(position)
                    except Exception as e:
                        logger.error(f"❌ Position callback error: {e}")
                
                logger.info(f"📈 Position event processed: {position.get('symbol')}")
                
            except Exception as e:
                logger.error(f"❌ Error processing position event: {e}")
    
    async def _handle_wallet_event(self, data: List[Dict[str, Any]], event_type: str, timestamp: int):
        """Handle wallet events"""
        for wallet in data:
            try:
                # Notify wallet callbacks
                for callback in self.event_callbacks['wallet']:
                    try:
                        await callback(wallet)
                    except Exception as e:
                        logger.error(f"❌ Wallet callback error: {e}")
                
                logger.info(f"💰 Wallet event processed: {wallet.get('currency')}")
                
            except Exception as e:
                logger.error(f"❌ Error processing wallet event: {e}")
    
    def _parse_order_event(self, order_data: Dict[str, Any]) -> BybitExecutionReport:
        """Parse order event data"""
        return BybitExecutionReport(
            order_id=order_data.get('orderId', ''),
            symbol=order_data.get('symbol', ''),
            side=order_data.get('side', ''),
            order_type=order_data.get('orderType', ''),
            price=float(order_data.get('price', 0)),
            qty=float(order_data.get('qty', 0)),
            cum_exec_qty=float(order_data.get('cumExecQty', 0)),
            cum_exec_fee=float(order_data.get('cumExecFee', 0)),
            avg_price=float(order_data.get('avgPrice', 0)),
            order_status=order_data.get('orderStatus', ''),
            order_link_id=order_data.get('orderLinkId', ''),
            last_exec_price=float(order_data.get('lastExecPrice', 0)),
            last_exec_qty=float(order_data.get('lastExecQty', 0)),
            exec_time=order_data.get('execTime', '')
        )
    
    def _parse_execution_event(self, execution_data: Dict[str, Any]) -> Dict[str, Any]:
        """Parse execution event data"""
        return {
            'symbol': execution_data.get('symbol', ''),
            'side': execution_data.get('side', ''),
            'order_id': execution_data.get('orderId', ''),
            'exec_id': execution_data.get('execId', ''),
            'order_link_id': execution_data.get('orderLinkId', ''),
            'price': float(execution_data.get('price', 0)),
            'qty': float(execution_data.get('qty', 0)),
            'exec_fee': float(execution_data.get('execFee', 0)),
            'exec_time': execution_data.get('execTime', '')
        }
    
    def add_order_callback(self, callback: Callable):
        """Add order event callback"""
        self.event_callbacks['order'].append(callback)
    
    def add_execution_callback(self, callback: Callable):
        """Add execution event callback"""
        self.event_callbacks['execution'].append(callback)
    
    def add_position_callback(self, callback: Callable):
        """Add position event callback"""
        self.event_callbacks['position'].append(callback)
    
    def add_wallet_callback(self, callback: Callable):
        """Add wallet event callback"""
        self.event_callbacks['wallet'].append(callback)
    
    def add_connection_callback(self, callback: Callable):
        """Add connection event callback"""
        self.event_callbacks['connection'].append(callback)
    
    def add_error_callback(self, callback: Callable):
        """Add error event callback"""
        self.event_callbacks['error'].append(callback)
    
    def get_status(self) -> Dict[str, Any]:
        """Get manager status"""
        return {
            "running": self.connection_manager.is_running,
            "connected": self.connection_manager.is_connected,
            "state": self.connection_manager.state.value,
            "subscribed_channels": self.connection_manager.subscribed_channels,
            "connection_metrics": self.connection_manager.get_status(),
            "event_metrics": self.metrics,
            "callback_counts": {
                topic: len(callbacks) for topic, callbacks in self.event_callbacks.items()
            }
        }
    
    def reset_metrics(self):
        """Reset all metrics"""
        self.metrics = {
            "events_processed": 0,
            "events_by_type": {
                "order": 0,
                "execution": 0,
                "position": 0,
                "wallet": 0
            },
            "processing_errors": 0,
            "last_event_time": None,
            "uptime_start": datetime.now(timezone.utc).isoformat()
        }
        self.connection_manager.reset_metrics()
        logger.info("📊 Bybit User Data Stream metrics reset")

class BybitEventProcessor:
    """Processes Bybit WebSocket events"""
    
    def __init__(self):
        self.processed_events = 0
        self.processing_errors = 0
    
    async def process_event(self, event_data: Dict[str, Any]) -> bool:
        """Process Bybit event"""
        try:
            self.processed_events += 1
            # Event processing logic can be extended here
            return True
            
        except Exception as e:
            self.processing_errors += 1
            logger.error(f"❌ Event processing error: {e}")
            return False
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get processing metrics"""
        return {
            "processed_events": self.processed_events,
            "processing_errors": self.processing_errors
        }
