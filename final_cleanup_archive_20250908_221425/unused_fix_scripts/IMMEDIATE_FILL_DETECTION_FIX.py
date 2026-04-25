#!/usr/bin/env python3
"""
IMMEDIATE FILL DETECTION FIX
This script provides an immediate fix for the fill detection system
by registering existing orders in Redis and implementing a simple polling system.
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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ImmediateFillDetectionFix:
    """
    Immediate fix for fill detection system
    """
    
    def __init__(self):
        self.redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        self.metrics = {
            'orders_registered': 0,
            'fills_detected': 0,
            'trades_closed': 0,
            'errors': 0
        }
    
    async def run(self):
        """Run the immediate fix"""
        logger.info("🚀 Starting Immediate Fill Detection Fix...")
        
        try:
            # Step 1: Register existing orders in Redis
            await self._register_existing_orders()
            
            # Step 2: Start polling for fills
            await self._start_polling()
            
        except Exception as e:
            logger.error(f"❌ Error in immediate fix: {e}")
            self.metrics['errors'] += 1
    
    async def _register_existing_orders(self):
        """Register all existing orders in Redis"""
        logger.info("📝 Registering existing orders in Redis...")
        
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
                    
                    logger.info(f"✅ Registered {registered_count} existing orders in Redis")
                    self.metrics['orders_registered'] = registered_count
                else:
                    logger.error(f"❌ Failed to get order mappings: {response.status_code}")
                    
        except Exception as e:
            logger.error(f"❌ Error registering existing orders: {e}")
            self.metrics['errors'] += 1
    
    async def _register_order_in_redis(self, order: Dict[str, Any]):
        """Register a single order in Redis"""
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
            logger.error(f"❌ Error registering order {order.get('client_order_id')}: {e}")
            self.metrics['errors'] += 1
    
    async def _start_polling(self):
        """Start polling for order fills"""
        logger.info("🔄 Starting order fill polling...")
        
        while True:
            try:
                await self._poll_for_fills()
                await asyncio.sleep(10)  # Poll every 10 seconds
            except Exception as e:
                logger.error(f"❌ Error in polling: {e}")
                self.metrics['errors'] += 1
                await asyncio.sleep(30)  # Wait longer on error
    
    async def _poll_for_fills(self):
        """Poll for order fills"""
        try:
            # Get all tracked orders
            tracked_orders = self.redis_client.smembers("tracked_orders")
            
            for client_order_id in tracked_orders:
                order_data = self.redis_client.hgetall(f"order:{client_order_id}")
                if not order_data:
                    continue
                
                exchange = order_data.get('exchange')
                order_id = order_data.get('order_id')
                current_status = order_data.get('status')
                
                if not exchange or not order_id or current_status == 'FILLED':
                    continue
                
                # Check order status on exchange
                try:
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        status_response = await client.get(
                            f"{EXCHANGE_SERVICE_URL}/api/v1/trading/order-status/{exchange}/{order_id}"
                        )
                        
                        if status_response.status_code == 200:
                            status_data = status_response.json()
                            exchange_status = status_data.get('status', '').lower()
                            
                            # Check if order was filled
                            if exchange_status == 'filled' and current_status != 'FILLED':
                                await self._handle_order_filled(order_data, status_data)
                                
                except Exception as e:
                    logger.debug(f"⚠️ Error checking order {order_id}: {e}")
                    continue
                    
        except Exception as e:
            logger.error(f"❌ Error polling for fills: {e}")
            self.metrics['errors'] += 1
    
    async def _handle_order_filled(self, order_data: Dict[str, Any], status_data: Dict[str, Any]):
        """Handle when an order is filled"""
        try:
            client_order_id = order_data['client_order_id']
            order_id = order_data['order_id']
            symbol = order_data['symbol']
            side = order_data['side']
            price = status_data.get('price', 0)
            quantity = status_data.get('filled', 0)
            
            logger.info(f"🎯 ORDER FILLED: {client_order_id} ({symbol} {side} {quantity} @ {price})")
            
            # Update order status in Redis
            self.redis_client.hset(f"order:{client_order_id}", 'status', 'FILLED')
            self.redis_client.hset(f"order:{client_order_id}", 'filled_at', datetime.utcnow().isoformat())
            self.redis_client.hset(f"order:{client_order_id}", 'filled_price', price)
            self.redis_client.hset(f"order:{client_order_id}", 'filled_quantity', quantity)
            
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
            async with httpx.AsyncClient(timeout=10.0) as client:
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
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Get all trades
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
            async with httpx.AsyncClient(timeout=10.0) as client:
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
    """Main function"""
    fix = ImmediateFillDetectionFix()
    
    # Run once to register orders
    await fix._register_existing_orders()
    
    # Show metrics
    metrics = fix.get_metrics()
    logger.info(f"📊 Initial Metrics: {metrics}")
    
    # Start polling
    await fix._start_polling()

if __name__ == "__main__":
    asyncio.run(main())
