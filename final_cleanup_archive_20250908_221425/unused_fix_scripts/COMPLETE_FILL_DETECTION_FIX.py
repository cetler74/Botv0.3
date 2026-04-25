#!/usr/bin/env python3
"""
COMPLETE FILL DETECTION AND TRADE CLOSURE FIX
This script implements a unified, reliable system to prevent phantom trades.
"""

import asyncio
import httpx
import redis
import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional

# Configuration
REDIS_URL = "redis://localhost:6379"
DATABASE_SERVICE_URL = "http://localhost:8002"
EXCHANGE_SERVICE_URL = "http://localhost:8003"

logger = logging.getLogger(__name__)

class UnifiedFillDetectionSystem:
    """
    Unified Fill Detection System that consolidates all fill detection methods
    and ensures reliable trade closure.
    """
    
    def __init__(self):
        self.redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        self.metrics = {
            'orders_tracked': 0,
            'fills_detected': 0,
            'trades_closed': 0,
            'errors': 0
        }
    
    async def start(self):
        """Start the unified fill detection system"""
        logger.info("🚀 Starting Unified Fill Detection System...")
        
        # Step 1: Register all existing orders in Redis
        await self._register_existing_orders()
        
        # Step 2: Start WebSocket user data stream monitoring
        await self._start_websocket_monitoring()
        
        # Step 3: Start REST API polling as fallback
        await self._start_rest_polling()
        
        logger.info("✅ Unified Fill Detection System started")
    
    async def _register_existing_orders(self):
        """Register all existing orders in Redis for tracking"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Get all order mappings
                response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/order-mappings")
                if response.status_code == 200:
                    orders_data = response.json()
                    orders = orders_data.get('order_mappings', [])
                    
                    registered_count = 0
                    for order in orders:
                        if order.get('exchange_order_id'):
                            await self._register_order_in_redis(order)
                            registered_count += 1
                    
                    logger.info(f"📝 Registered {registered_count} existing orders in Redis")
                    self.metrics['orders_tracked'] = registered_count
                else:
                    logger.error(f"❌ Failed to get order mappings: {response.status_code}")
                    
        except Exception as e:
            logger.error(f"❌ Error registering existing orders: {e}")
            self.metrics['errors'] += 1
    
    async def _register_order_in_redis(self, order: Dict[str, Any]):
        """Register a single order in Redis for tracking"""
        try:
            order_data = {
                'order_id': order['exchange_order_id'],
                'client_order_id': order['client_order_id'],
                'symbol': order['symbol'],
                'side': order['side'],
                'amount': order['amount'],
                'price': order['price'],
                'status': order['status'],
                'exchange': order['exchange'],
                'timestamp': datetime.utcnow().isoformat()
            }
            
            # Store in Redis hash
            self.redis_client.hset(f"order:{order['client_order_id']}", mapping=order_data)
            
            # Add to tracking set
            self.redis_client.sadd("tracked_orders", order['client_order_id'])
            
            logger.debug(f"📝 Registered order {order['client_order_id']} in Redis")
            
        except Exception as e:
            logger.error(f"❌ Error registering order in Redis: {e}")
            self.metrics['errors'] += 1
    
    async def _start_websocket_monitoring(self):
        """Start WebSocket user data stream monitoring"""
        logger.info("🔌 Starting WebSocket user data stream monitoring...")
        
        # This would connect to the user data stream WebSocket
        # For now, we'll implement the REST API fallback
        logger.warning("⚠️ WebSocket monitoring not yet implemented - using REST API fallback")
    
    async def _start_rest_polling(self):
        """Start REST API polling as fallback"""
        logger.info("🔄 Starting REST API polling...")
        
        while True:
            try:
                await self._poll_exchange_orders()
                await asyncio.sleep(5)  # Poll every 5 seconds
            except Exception as e:
                logger.error(f"❌ Error in REST polling: {e}")
                self.metrics['errors'] += 1
                await asyncio.sleep(10)  # Wait longer on error
    
    async def _poll_exchange_orders(self):
        """Poll exchange for order status updates"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Get all tracked orders
                tracked_orders = self.redis_client.smembers("tracked_orders")
                
                for client_order_id in tracked_orders:
                    order_data = self.redis_client.hgetall(f"order:{client_order_id}")
                    if not order_data:
                        continue
                    
                    exchange = order_data.get('exchange')
                    order_id = order_data.get('order_id')
                    
                    if not exchange or not order_id:
                        continue
                    
                    # Check order status on exchange
                    status_response = await client.get(
                        f"{EXCHANGE_SERVICE_URL}/api/v1/trading/order-status/{exchange}/{order_id}"
                    )
                    
                    if status_response.status_code == 200:
                        status_data = status_response.json()
                        current_status = status_data.get('status', '').lower()
                        
                        # Check if order was filled
                        if current_status == 'filled' and order_data.get('status') != 'FILLED':
                            await self._handle_order_filled(order_data, status_data)
                    
        except Exception as e:
            logger.error(f"❌ Error polling exchange orders: {e}")
            self.metrics['errors'] += 1
    
    async def _handle_order_filled(self, order_data: Dict[str, Any], status_data: Dict[str, Any]):
        """Handle when an order is filled"""
        try:
            client_order_id = order_data['client_order_id']
            order_id = order_data['order_id']
            symbol = order_data['symbol']
            side = order_data['side']
            
            logger.info(f"🎯 ORDER FILLED: {client_order_id} ({symbol} {side})")
            
            # Update order status in Redis
            self.redis_client.hset(f"order:{client_order_id}", 'status', 'FILLED')
            self.redis_client.hset(f"order:{client_order_id}", 'filled_at', datetime.utcnow().isoformat())
            
            # Update order mapping in database
            await self._update_order_mapping(client_order_id, status_data)
            
            # Close trade if this is an exit order
            if side.lower() == 'sell':
                await self._close_trade_for_exit_order(client_order_id, status_data)
            
            self.metrics['fills_detected'] += 1
            
        except Exception as e:
            logger.error(f"❌ Error handling order fill: {e}")
            self.metrics['errors'] += 1
    
    async def _update_order_mapping(self, client_order_id: str, status_data: Dict[str, Any]):
        """Update order mapping in database"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                update_data = {
                    'status': 'FILLED',
                    'filled_at': datetime.utcnow().isoformat()
                }
                
                response = await client.put(
                    f"{DATABASE_SERVICE_URL}/api/v1/order-mappings/{client_order_id}",
                    json=update_data
                )
                
                if response.status_code == 200:
                    logger.info(f"✅ Updated order mapping {client_order_id}")
                else:
                    logger.error(f"❌ Failed to update order mapping: {response.status_code}")
                    
        except Exception as e:
            logger.error(f"❌ Error updating order mapping: {e}")
    
    async def _close_trade_for_exit_order(self, client_order_id: str, status_data: Dict[str, Any]):
        """Close trade when exit order is filled"""
        try:
            # Find the trade associated with this order
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Get trade by exit_id
                trades_response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/trades")
                if trades_response.status_code == 200:
                    trades_data = trades_response.json()
                    trades = trades_data.get('trades', [])
                    
                    for trade in trades:
                        if trade.get('exit_id') == client_order_id and trade.get('status') == 'OPEN':
                            await self._close_trade(trade, status_data)
                            break
                            
        except Exception as e:
            logger.error(f"❌ Error closing trade for exit order: {e}")
    
    async def _close_trade(self, trade: Dict[str, Any], status_data: Dict[str, Any]):
        """Close a trade with proper PnL calculation"""
        try:
            trade_id = trade['trade_id']
            entry_price = float(trade['entry_price'])
            position_size = float(trade['position_size'])
            exit_price = float(status_data.get('price', 0))
            
            # Calculate PnL
            entry_value = entry_price * position_size
            exit_value = exit_price * position_size
            realized_pnl = exit_value - entry_value
            realized_pnl_pct = (realized_pnl / entry_value) * 100 if entry_value > 0 else 0
            
            # Update trade
            async with httpx.AsyncClient(timeout=30.0) as client:
                trade_update = {
                    'status': 'CLOSED',
                    'exit_price': exit_price,
                    'exit_time': datetime.utcnow().isoformat(),
                    'realized_pnl': realized_pnl,
                    'realized_pnl_pct': realized_pnl_pct,
                    'trail_stop': 'inactive',
                    'profit_protection': 'inactive'
                }
                
                response = await client.put(
                    f"{DATABASE_SERVICE_URL}/api/v1/trades/{trade_id}",
                    json=trade_update
                )
                
                if response.status_code == 200:
                    logger.info(f"✅ CLOSED TRADE: {trade_id} - PnL: ${realized_pnl:.2f} ({realized_pnl_pct:.2f}%)")
                    self.metrics['trades_closed'] += 1
                else:
                    logger.error(f"❌ Failed to close trade: {response.status_code}")
                    
        except Exception as e:
            logger.error(f"❌ Error closing trade: {e}")
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get system metrics"""
        return {
            **self.metrics,
            'tracked_orders_count': self.redis_client.scard("tracked_orders"),
            'redis_orders_count': len(self.redis_client.keys("order:*"))
        }

async def main():
    """Main function to start the unified fill detection system"""
    logging.basicConfig(level=logging.INFO)
    
    system = UnifiedFillDetectionSystem()
    await system.start()
    
    # Keep running and show metrics
    while True:
        metrics = system.get_metrics()
        logger.info(f"📊 Metrics: {metrics}")
        await asyncio.sleep(60)  # Show metrics every minute

if __name__ == "__main__":
    asyncio.run(main())
