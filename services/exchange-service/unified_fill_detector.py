"""
Unified Fill Detection System - Phase 1 Implementation
Consolidates all fill detection into a single system with WebSocket primary and REST fallback
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Callable
from enum import Enum
import httpx
import json
import uuid

logger = logging.getLogger(__name__)

class FillDetectionStatus(Enum):
    """Fill detection system status"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"  # WebSocket down, using REST fallback
    UNHEALTHY = "unhealthy"  # Both WebSocket and REST failing
    DISABLED = "disabled"

class FillEventType(Enum):
    """Types of fill events"""
    ORDER_FILLED = "order_filled"
    ORDER_PARTIALLY_FILLED = "order_partially_filled"
    ORDER_CANCELLED = "order_cancelled"
    ORDER_REJECTED = "order_rejected"

class UnifiedFillDetector:
    """
    Unified fill detection system that consolidates all fill detection methods
    
    Architecture:
    1. PRIMARY: WebSocket User Data Streams (real-time execution reports)
    2. FALLBACK: REST API Polling (always active as backup)
    3. OUTPUT: Standardized Fill Events → Trade Closure
    """
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.is_running = False
        
        # Service URLs
        self.database_service_url = config.get('database_service_url', 'http://database-service:8002')
        self.orchestrator_service_url = config.get('orchestrator_service_url', 'http://orchestrator-service:8005')
        
        # WebSocket managers for each exchange
        self.websocket_managers = {}
        self.websocket_health = {
            'binance': {'healthy': False, 'last_check': None, 'connection_attempts': 0},
            'bybit': {'healthy': False, 'last_check': None, 'connection_attempts': 0},
            'cryptocom': {'healthy': False, 'last_check': None, 'connection_attempts': 0}
        }
        
        # REST API fallback settings
        self.rest_poll_interval = 30  # seconds
        self.rest_fallback_active = {
            'binance': False,
            'bybit': False,
            'cryptocom': False
        }
        
        # Fill event callbacks
        self.fill_callbacks: List[Callable] = []
        self.trade_closure_callbacks: List[Callable] = []
        
        # Metrics
        self.metrics = {
            'websocket_fills_detected': 0,
            'rest_fills_detected': 0,
            'trades_closed': 0,
            'websocket_errors': 0,
            'rest_errors': 0,
            'fallback_activations': 0,
            'last_activity': None
        }
        
        logger.info("🔧 Unified Fill Detector initialized")
    
    async def start(self):
        """Start the unified fill detection system"""
        if self.is_running:
            logger.warning("Unified Fill Detector already running")
            return
        
        self.is_running = True
        logger.info("🚀 Starting Unified Fill Detection System")
        
        # Start WebSocket connections
        await self._start_websocket_connections()
        
        # Start REST API fallback monitoring
        await self._start_rest_fallback_monitoring()
        
        # Start health monitoring
        asyncio.create_task(self._health_monitoring_loop())
        
        logger.info("✅ Unified Fill Detection System started")
    
    async def stop(self):
        """Stop the unified fill detection system"""
        if not self.is_running:
            return
        
        self.is_running = False
        logger.info("🛑 Stopping Unified Fill Detection System")
        
        # Stop WebSocket connections
        for exchange, manager in self.websocket_managers.items():
            if manager:
                await manager.stop()
        
        logger.info("✅ Unified Fill Detection System stopped")
    
    async def _start_websocket_connections(self):
        """Start WebSocket connections for all exchanges"""
        exchanges = ['binance', 'bybit', 'cryptocom']
        
        for exchange in exchanges:
            try:
                await self._connect_websocket(exchange)
            except Exception as e:
                logger.error(f"❌ Failed to start WebSocket for {exchange}: {e}")
                # Activate REST fallback immediately
                self.rest_fallback_active[exchange] = True
                self.metrics['fallback_activations'] += 1
    
    async def _connect_websocket(self, exchange: str):
        """Connect WebSocket for a specific exchange"""
        try:
            # Import exchange-specific managers
            if exchange == 'binance':
                from binance_websocket_manager import UnifiedBinanceWebSocketManager
                manager = UnifiedBinanceWebSocketManager(self.config)
            elif exchange == 'bybit':
                from bybit_websocket_manager import UnifiedBybitWebSocketManager
                manager = UnifiedBybitWebSocketManager(self.config)
            elif exchange == 'cryptocom':
                from cryptocom_websocket_manager import UnifiedCryptocomWebSocketManager
                manager = UnifiedCryptocomWebSocketManager(self.config)
            else:
                raise ValueError(f"Unsupported exchange: {exchange}")
            
            # Set up fill event callback
            manager.add_fill_callback(self._handle_websocket_fill_event)
            
            # Start the manager
            await manager.start()
            
            self.websocket_managers[exchange] = manager
            self.websocket_health[exchange]['healthy'] = True
            self.websocket_health[exchange]['last_check'] = datetime.utcnow()
            
            logger.info(f"✅ WebSocket connected for {exchange}")
            
        except Exception as e:
            logger.error(f"❌ WebSocket connection failed for {exchange}: {e}")
            self.websocket_health[exchange]['healthy'] = False
            self.websocket_health[exchange]['connection_attempts'] += 1
            raise
    
    async def _start_rest_fallback_monitoring(self):
        """Start REST API fallback monitoring for all exchanges"""
        # Start REST polling tasks for all exchanges
        for exchange in ['binance', 'bybit', 'cryptocom']:
            asyncio.create_task(self._rest_polling_loop(exchange))
    
    async def _rest_polling_loop(self, exchange: str):
        """REST API polling loop for a specific exchange"""
        logger.info(f"🔄 Starting REST polling for {exchange}")
        
        while self.is_running:
            try:
                # Only poll if WebSocket is down or fallback is active
                if not self.websocket_health[exchange]['healthy'] or self.rest_fallback_active[exchange]:
                    await self._poll_exchange_orders(exchange)
                
                await asyncio.sleep(self.rest_poll_interval)
                
            except Exception as e:
                logger.error(f"❌ REST polling error for {exchange}: {e}")
                self.metrics['rest_errors'] += 1
                await asyncio.sleep(60)  # Wait longer on error
    
    async def _poll_exchange_orders(self, exchange: str):
        """Poll exchange for order status updates"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Get recent orders from exchange
                response = await client.get(
                    f"http://exchange-service:8003/api/v1/trading/orders/{exchange}",
                    params={"status": "open,partially_filled,filled"}
                )
                
                if response.status_code == 200:
                    orders = response.json().get('orders', [])
                    
                    # Check for filled orders
                    for order in orders:
                        if order.get('status') in ['filled', 'partially_filled']:
                            await self._handle_rest_fill_event(exchange, order)
                
        except Exception as e:
            logger.error(f"❌ Error polling {exchange} orders: {e}")
            self.metrics['rest_errors'] += 1
    
    async def _handle_websocket_fill_event(self, exchange: str, fill_data: Dict[str, Any]):
        """Handle fill event from WebSocket"""
        try:
            logger.info(f"📡 WebSocket fill detected: {exchange} order {fill_data.get('order_id')}")
            
            # Process the fill event
            await self._process_fill_event(exchange, fill_data, source='websocket')
            
            self.metrics['websocket_fills_detected'] += 1
            self.metrics['last_activity'] = datetime.utcnow()
            
        except Exception as e:
            logger.error(f"❌ Error handling WebSocket fill event: {e}")
            self.metrics['websocket_errors'] += 1
    
    async def _handle_rest_fill_event(self, exchange: str, order_data: Dict[str, Any]):
        """Handle fill event from REST API polling"""
        try:
            logger.info(f"📈 REST fill detected: {exchange} order {order_data.get('id')}")
            
            # Convert order data to fill event format
            fill_data = {
                'order_id': order_data.get('id'),
                'exchange': exchange,
                'symbol': order_data.get('symbol'),
                'side': order_data.get('side'),
                'amount': order_data.get('amount'),
                'price': order_data.get('price'),
                'status': order_data.get('status'),
                'timestamp': order_data.get('timestamp'),
                'fee': order_data.get('fee', {}).get('cost', 0)
            }
            
            # Process the fill event
            await self._process_fill_event(exchange, fill_data, source='rest')
            
            self.metrics['rest_fills_detected'] += 1
            self.metrics['last_activity'] = datetime.utcnow()
            
        except Exception as e:
            logger.error(f"❌ Error handling REST fill event: {e}")
            self.metrics['rest_errors'] += 1
    
    async def _process_fill_event(self, exchange: str, fill_data: Dict[str, Any], source: str):
        """Process fill event and trigger trade closure"""
        try:
            order_id = fill_data.get('order_id')
            status = fill_data.get('status', '').lower()
            
            # Determine fill event type
            if status == 'filled':
                event_type = FillEventType.ORDER_FILLED
            elif status == 'partially_filled':
                event_type = FillEventType.ORDER_PARTIALLY_FILLED
            else:
                logger.debug(f"Non-fill status: {status}")
                return
            
            # Create standardized fill event
            fill_event = {
                'event_id': str(uuid.uuid4()),
                'event_type': event_type.value,
                'exchange': exchange,
                'order_id': order_id,
                'symbol': fill_data.get('symbol'),
                'side': fill_data.get('side'),
                'amount': fill_data.get('amount'),
                'price': fill_data.get('price'),
                'fee': fill_data.get('fee', 0),
                'timestamp': fill_data.get('timestamp', datetime.utcnow().isoformat()),
                'source': source
            }
            
            # Emit fill event to callbacks
            await self._emit_fill_event(fill_event)
            
            # Trigger trade closure for fully filled orders
            if event_type == FillEventType.ORDER_FILLED:
                await self._trigger_trade_closure(fill_event)
            
        except Exception as e:
            logger.error(f"❌ Error processing fill event: {e}")
    
    async def _emit_fill_event(self, fill_event: Dict[str, Any]):
        """Emit fill event to registered callbacks"""
        for callback in self.fill_callbacks:
            try:
                await callback(fill_event)
            except Exception as e:
                logger.error(f"❌ Error in fill event callback: {e}")
    
    async def _trigger_trade_closure(self, fill_event: Dict[str, Any]):
        """Trigger trade closure for filled orders"""
        try:
            order_id = fill_event['order_id']
            exchange = fill_event['exchange']
            side = fill_event['side']
            
            # Only close trades for sell orders (exit orders)
            if side.lower() != 'sell':
                logger.debug(f"Buy order filled - not closing trade: {order_id}")
                return
            
            # Find the trade associated with this order
            trade = await self._find_trade_by_exit_order(order_id, exchange)
            
            if trade:
                # Close the trade
                await self._close_trade(trade, fill_event)
                self.metrics['trades_closed'] += 1
                
                logger.info(f"✅ Trade closed: {trade['trade_id']} via {source} fill detection")
            else:
                logger.debug(f"No open trade found for exit order: {order_id}")
            
        except Exception as e:
            logger.error(f"❌ Error triggering trade closure: {e}")
    
    async def _find_trade_by_exit_order(self, order_id: str, exchange: str) -> Optional[Dict[str, Any]]:
        """Find trade associated with exit order"""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{self.database_service_url}/api/v1/trades",
                    params={
                        "exchange": exchange,
                        "status": "OPEN",
                        "exit_id": order_id
                    }
                )
                
                if response.status_code == 200:
                    trades = response.json().get('trades', [])
                    return trades[0] if trades else None
                
        except Exception as e:
            logger.error(f"❌ Error finding trade by exit order: {e}")
        
        return None
    
    async def _close_trade(self, trade: Dict[str, Any], fill_event: Dict[str, Any]):
        """Close trade with fill event data"""
        try:
            trade_id = trade['trade_id']
            exit_price = fill_event['price']
            exit_time = fill_event['timestamp']
            fees = fill_event['fee']
            
            # Calculate realized PnL
            entry_price = trade['entry_price']
            position_size = trade['position_size']
            realized_pnl = (exit_price - entry_price) * position_size - fees
            
            # Update trade in database
            async with httpx.AsyncClient(timeout=10.0) as client:
                update_data = {
                    "trade_id": trade_id,
                    "exit_price": exit_price,
                    "exit_time": exit_time,
                    "exit_id": fill_event['order_id'],
                    "realized_pnl": realized_pnl,
                    "status": "CLOSED",
                    "exit_reason": f"unified_fill_detection_{fill_event['source']}"
                }
                
                response = await client.put(
                    f"{self.database_service_url}/api/v1/trades/{trade_id}",
                    json=update_data
                )
                
                if response.status_code == 200:
                    logger.info(f"✅ Trade {trade_id} closed successfully")
                    
                    # Emit trade closure event
                    await self._emit_trade_closure_event(trade_id, update_data)
                else:
                    logger.error(f"❌ Failed to close trade {trade_id}: {response.status_code}")
        
        except Exception as e:
            logger.error(f"❌ Error closing trade: {e}")
    
    async def _emit_trade_closure_event(self, trade_id: str, closure_data: Dict[str, Any]):
        """Emit trade closure event to callbacks"""
        for callback in self.trade_closure_callbacks:
            try:
                await callback(trade_id, closure_data)
            except Exception as e:
                logger.error(f"❌ Error in trade closure callback: {e}")
    
    async def _health_monitoring_loop(self):
        """Monitor WebSocket health and manage fallbacks"""
        while self.is_running:
            try:
                for exchange in ['binance', 'bybit', 'cryptocom']:
                    await self._check_websocket_health(exchange)
                
                await asyncio.sleep(30)  # Check every 30 seconds
                
            except Exception as e:
                logger.error(f"❌ Health monitoring error: {e}")
                await asyncio.sleep(60)
    
    async def _check_websocket_health(self, exchange: str):
        """Check WebSocket health for a specific exchange"""
        try:
            manager = self.websocket_managers.get(exchange)
            
            if not manager:
                # No manager - activate fallback
                if not self.rest_fallback_active[exchange]:
                    logger.warning(f"⚠️ No WebSocket manager for {exchange}, activating REST fallback")
                    self.rest_fallback_active[exchange] = True
                    self.metrics['fallback_activations'] += 1
                return
            
            # Check if manager is healthy
            is_healthy = await manager.is_healthy()
            prev_healthy = self.websocket_health[exchange]['healthy']
            
            self.websocket_health[exchange]['healthy'] = is_healthy
            self.websocket_health[exchange]['last_check'] = datetime.utcnow()
            
            # Handle state changes
            if prev_healthy and not is_healthy:
                # WebSocket disconnected - activate fallback
                logger.warning(f"⚠️ {exchange} WebSocket disconnected, activating REST fallback")
                self.rest_fallback_active[exchange] = True
                self.metrics['fallback_activations'] += 1
                
            elif not prev_healthy and is_healthy:
                # WebSocket reconnected - deactivate fallback
                logger.info(f"✅ {exchange} WebSocket reconnected, deactivating REST fallback")
                self.rest_fallback_active[exchange] = False
        
        except Exception as e:
            logger.error(f"❌ Error checking {exchange} WebSocket health: {e}")
    
    def add_fill_callback(self, callback: Callable):
        """Add callback for fill events"""
        self.fill_callbacks.append(callback)
    
    def add_trade_closure_callback(self, callback: Callable):
        """Add callback for trade closure events"""
        self.trade_closure_callbacks.append(callback)
    
    def get_status(self) -> Dict[str, Any]:
        """Get current system status"""
        return {
            'status': self._get_overall_status(),
            'websocket_health': self.websocket_health,
            'rest_fallback_active': self.rest_fallback_active,
            'metrics': self.metrics,
            'is_running': self.is_running
        }
    
    def _get_overall_status(self) -> str:
        """Get overall system status"""
        healthy_websockets = sum(1 for health in self.websocket_health.values() if health['healthy'])
        active_fallbacks = sum(1 for active in self.rest_fallback_active.values() if active)
        
        if healthy_websockets == 3:
            return FillDetectionStatus.HEALTHY.value
        elif healthy_websockets > 0 or active_fallbacks > 0:
            return FillDetectionStatus.DEGRADED.value
        else:
            return FillDetectionStatus.UNHEALTHY.value
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get system metrics"""
        return {
            **self.metrics,
            'websocket_health_summary': {
                exchange: health['healthy'] 
                for exchange, health in self.websocket_health.items()
            },
            'fallback_summary': {
                exchange: active 
                for exchange, active in self.rest_fallback_active.items()
            }
        }
