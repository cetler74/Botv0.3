#!/usr/bin/env python3
"""
Binance User Data Stream Fix
This module ensures reliable WebSocket connection to Binance user data streams
for real-time execution report processing.
"""

import asyncio
import websockets
import json
import logging
import redis
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Callable
import httpx

logger = logging.getLogger(__name__)

class BinanceUserDataStreamFix:
    """
    Fixed Binance User Data Stream manager that ensures reliable connection
    and proper execution report processing.
    """
    
    def __init__(self, api_key: str, api_secret: str, redis_url: str = "redis://localhost:6379"):
        self.api_key = api_key
        self.api_secret = api_secret
        self.redis_client = redis.Redis.from_url(redis_url, decode_responses=True)
        
        self.websocket = None
        self.listen_key = None
        self.is_connected = False
        self.is_running = False
        
        # Connection management
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 10
        self.reconnect_delay = 5
        
        # Listen key management
        self.listen_key_refresh_interval = 30 * 60  # 30 minutes
        self.last_listen_key_refresh = None
        
        # Metrics
        self.metrics = {
            'connection_attempts': 0,
            'successful_connections': 0,
            'disconnections': 0,
            'execution_reports_received': 0,
            'listen_key_refreshes': 0,
            'errors': 0
        }
        
        # Callbacks
        self.execution_callbacks = []
        self.order_callbacks = {}
    
    async def start(self):
        """Start the user data stream connection"""
        logger.info("🚀 Starting Binance User Data Stream...")
        self.is_running = True
        
        while self.is_running:
            try:
                await self._connect()
                await self._listen()
            except Exception as e:
                logger.error(f"❌ Connection error: {e}")
                self.metrics['errors'] += 1
                
                if self.reconnect_attempts < self.max_reconnect_attempts:
                    self.reconnect_attempts += 1
                    delay = self.reconnect_delay * (2 ** self.reconnect_attempts)  # Exponential backoff
                    logger.info(f"🔄 Reconnecting in {delay} seconds (attempt {self.reconnect_attempts})")
                    await asyncio.sleep(delay)
                else:
                    logger.error("❌ Max reconnection attempts reached")
                    break
    
    async def stop(self):
        """Stop the user data stream connection"""
        logger.info("🛑 Stopping Binance User Data Stream...")
        self.is_running = False
        
        if self.websocket:
            await self.websocket.close()
        
        if self.listen_key:
            await self._close_listen_key()
    
    async def _connect(self):
        """Establish WebSocket connection to user data stream"""
        try:
            # Get or refresh listen key
            await self._ensure_listen_key()
            
            if not self.listen_key:
                raise Exception("Failed to get listen key")
            
            # Connect to WebSocket
            url = f"wss://stream.binance.com:9443/ws/{self.listen_key}"
            logger.info(f"🔌 Connecting to Binance User Data Stream: {url}")
            
            self.websocket = await websockets.connect(
                url,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=10
            )
            
            self.is_connected = True
            self.reconnect_attempts = 0
            self.metrics['connection_attempts'] += 1
            self.metrics['successful_connections'] += 1
            
            logger.info("✅ Connected to Binance User Data Stream")
            
        except Exception as e:
            self.is_connected = False
            self.metrics['connection_attempts'] += 1
            logger.error(f"❌ Failed to connect to user data stream: {e}")
            raise
    
    async def _listen(self):
        """Listen for messages from the WebSocket"""
        try:
            async for message in self.websocket:
                await self._process_message(message)
                
        except websockets.exceptions.ConnectionClosed:
            logger.warning("⚠️ WebSocket connection closed")
            self.is_connected = False
            self.metrics['disconnections'] += 1
            raise
        except Exception as e:
            logger.error(f"❌ Error listening to WebSocket: {e}")
            self.is_connected = False
            raise
    
    async def _process_message(self, message: str):
        """Process incoming WebSocket message"""
        try:
            data = json.loads(message)
            event_type = data.get('e')
            
            if event_type == 'executionReport':
                await self._handle_execution_report(data)
            elif event_type == 'outboundAccountPosition':
                await self._handle_account_position(data)
            elif event_type == 'balanceUpdate':
                await self._handle_balance_update(data)
            else:
                logger.debug(f"📋 Unhandled event type: {event_type}")
                
        except Exception as e:
            logger.error(f"❌ Error processing message: {e}")
            self.metrics['errors'] += 1
    
    async def _handle_execution_report(self, data: Dict[str, Any]):
        """Handle execution report event"""
        try:
            self.metrics['execution_reports_received'] += 1
            
            # Extract execution report data
            execution_report = {
                'event_type': 'executionReport',
                'symbol': data.get('s'),
                'order_id': data.get('i'),
                'client_order_id': data.get('c'),
                'side': data.get('S'),
                'order_type': data.get('o'),
                'order_status': data.get('X'),
                'execution_type': data.get('x'),
                'last_executed_quantity': data.get('l'),
                'last_executed_price': data.get('L'),
                'commission_amount': data.get('n'),
                'commission_asset': data.get('N'),
                'timestamp': data.get('T'),
                'raw_data': data
            }
            
            logger.info(f"📊 Execution Report: {execution_report['symbol']} "
                       f"{execution_report['side']} {execution_report['execution_type']} "
                       f"{execution_report['order_status']}")
            
            # Store in Redis for tracking
            await self._store_execution_report_in_redis(execution_report)
            
            # Notify callbacks
            await self._notify_execution_callbacks(execution_report)
            
            # Handle order fills
            if execution_report['execution_type'] == 'TRADE' and execution_report['order_status'] == 'FILLED':
                await self._handle_order_filled(execution_report)
                
        except Exception as e:
            logger.error(f"❌ Error handling execution report: {e}")
            self.metrics['errors'] += 1
    
    async def _store_execution_report_in_redis(self, execution_report: Dict[str, Any]):
        """Store execution report in Redis for tracking"""
        try:
            client_order_id = execution_report.get('client_order_id')
            if not client_order_id:
                return
            
            # Store execution report
            self.redis_client.hset(
                f"execution:{client_order_id}",
                mapping=execution_report
            )
            
            # Add to execution reports set
            self.redis_client.sadd("execution_reports", client_order_id)
            
            logger.debug(f"📝 Stored execution report for {client_order_id}")
            
        except Exception as e:
            logger.error(f"❌ Error storing execution report in Redis: {e}")
    
    async def _handle_order_filled(self, execution_report: Dict[str, Any]):
        """Handle when an order is filled"""
        try:
            client_order_id = execution_report.get('client_order_id')
            order_id = execution_report.get('order_id')
            symbol = execution_report.get('symbol')
            side = execution_report.get('side')
            price = execution_report.get('last_executed_price')
            quantity = execution_report.get('last_executed_quantity')
            
            logger.info(f"🎯 ORDER FILLED: {client_order_id} ({symbol} {side} {quantity} @ {price})")
            
            # Update order status in Redis
            if self.redis_client.exists(f"order:{client_order_id}"):
                self.redis_client.hset(f"order:{client_order_id}", 'status', 'FILLED')
                self.redis_client.hset(f"order:{client_order_id}", 'filled_at', datetime.utcnow().isoformat())
                self.redis_client.hset(f"order:{client_order_id}", 'filled_price', price)
                self.redis_client.hset(f"order:{client_order_id}", 'filled_quantity', quantity)
            
            # Emit fill event
            await self._emit_fill_event(execution_report)
            
        except Exception as e:
            logger.error(f"❌ Error handling order fill: {e}")
            self.metrics['errors'] += 1
    
    async def _emit_fill_event(self, execution_report: Dict[str, Any]):
        """Emit fill event to database service"""
        try:
            fill_event = {
                'event_type': 'OrderFilled',
                'order_id': execution_report.get('order_id'),
                'client_order_id': execution_report.get('client_order_id'),
                'symbol': execution_report.get('symbol'),
                'side': execution_report.get('side'),
                'amount': execution_report.get('last_executed_quantity'),
                'price': execution_report.get('last_executed_price'),
                'timestamp': datetime.utcnow().isoformat(),
                'fully_filled': True,
                'source': 'binance_websocket'
            }
            
            # Send to database service
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    "http://database-service:8002/api/v1/events",
                    json=fill_event
                )
                
                if response.status_code == 200:
                    logger.info(f"📡 Fill event emitted for {execution_report.get('client_order_id')}")
                else:
                    logger.error(f"❌ Failed to emit fill event: {response.status_code}")
                    
        except Exception as e:
            logger.error(f"❌ Error emitting fill event: {e}")
    
    async def _ensure_listen_key(self):
        """Ensure we have a valid listen key"""
        try:
            current_time = datetime.utcnow()
            
            # Check if we need to refresh the listen key
            if (not self.listen_key or 
                not self.last_listen_key_refresh or 
                (current_time - self.last_listen_key_refresh).total_seconds() > self.listen_key_refresh_interval):
                
                await self._refresh_listen_key()
                
        except Exception as e:
            logger.error(f"❌ Error ensuring listen key: {e}")
            raise
    
    async def _refresh_listen_key(self):
        """Refresh the listen key"""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                if self.listen_key:
                    # Refresh existing key
                    response = await client.put(
                        "https://api.binance.com/api/v3/userDataStream",
                        headers={'X-MBX-APIKEY': self.api_key},
                        params={'listenKey': self.listen_key}
                    )
                else:
                    # Create new key
                    response = await client.post(
                        "https://api.binance.com/api/v3/userDataStream",
                        headers={'X-MBX-APIKEY': self.api_key}
                    )
                
                if response.status_code == 200:
                    data = response.json()
                    self.listen_key = data.get('listenKey')
                    self.last_listen_key_refresh = datetime.utcnow()
                    self.metrics['listen_key_refreshes'] += 1
                    logger.info(f"✅ Listen key refreshed: {self.listen_key[:10]}...")
                else:
                    logger.error(f"❌ Failed to refresh listen key: {response.status_code}")
                    raise Exception(f"Listen key refresh failed: {response.status_code}")
                    
        except Exception as e:
            logger.error(f"❌ Error refreshing listen key: {e}")
            raise
    
    async def _close_listen_key(self):
        """Close the listen key"""
        try:
            if self.listen_key:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.delete(
                        "https://api.binance.com/api/v3/userDataStream",
                        headers={'X-MBX-APIKEY': self.api_key},
                        params={'listenKey': self.listen_key}
                    )
                    
                    if response.status_code == 200:
                        logger.info("✅ Listen key closed")
                    else:
                        logger.warning(f"⚠️ Failed to close listen key: {response.status_code}")
                        
        except Exception as e:
            logger.warning(f"⚠️ Error closing listen key: {e}")
    
    async def _handle_account_position(self, data: Dict[str, Any]):
        """Handle account position update"""
        logger.debug(f"📊 Account position update: {data}")
    
    async def _handle_balance_update(self, data: Dict[str, Any]):
        """Handle balance update"""
        logger.debug(f"💰 Balance update: {data}")
    
    async def _notify_execution_callbacks(self, execution_report: Dict[str, Any]):
        """Notify registered execution callbacks"""
        for callback in self.execution_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(execution_report)
                else:
                    callback(execution_report)
            except Exception as e:
                logger.error(f"❌ Error in execution callback: {e}")
    
    def add_execution_callback(self, callback: Callable):
        """Add execution report callback"""
        self.execution_callbacks.append(callback)
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get connection metrics"""
        return {
            **self.metrics,
            'is_connected': self.is_connected,
            'is_running': self.is_running,
            'listen_key': self.listen_key[:10] + "..." if self.listen_key else None,
            'last_refresh': self.last_listen_key_refresh.isoformat() if self.last_listen_key_refresh else None
        }

# Global instance
user_data_stream = None

async def start_binance_user_data_stream(api_key: str, api_secret: str):
    """Start the Binance user data stream"""
    global user_data_stream
    
    if user_data_stream and user_data_stream.is_running:
        logger.warning("⚠️ User data stream already running")
        return
    
    user_data_stream = BinanceUserDataStreamFix(api_key, api_secret)
    await user_data_stream.start()

async def stop_binance_user_data_stream():
    """Stop the Binance user data stream"""
    global user_data_stream
    
    if user_data_stream:
        await user_data_stream.stop()
        user_data_stream = None

def get_user_data_stream_metrics() -> Dict[str, Any]:
    """Get user data stream metrics"""
    global user_data_stream
    
    if user_data_stream:
        return user_data_stream.get_metrics()
    else:
        return {'is_connected': False, 'is_running': False}
