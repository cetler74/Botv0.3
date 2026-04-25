#!/usr/bin/env python3
"""
Register All Orders in Redis
This script registers all existing orders in Redis for comprehensive tracking.
"""

import asyncio
import httpx
import redis
import json
import logging
from datetime import datetime
from typing import Dict, Any, List

# Configuration
REDIS_URL = "redis://localhost:6379"
DATABASE_SERVICE_URL = "http://localhost:8002"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class OrderRegistrationSystem:
    """
    System to register all orders in Redis for comprehensive tracking
    """
    
    def __init__(self):
        self.redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        self.metrics = {
            'orders_processed': 0,
            'orders_registered': 0,
            'orders_skipped': 0,
            'errors': 0
        }
    
    async def register_all_orders(self):
        """Register all existing orders in Redis"""
        logger.info("🚀 Starting comprehensive order registration in Redis...")
        
        try:
            # Get all order mappings from database
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/order-mappings")
                if response.status_code == 200:
                    orders_data = response.json()
                    orders = orders_data.get('order_mappings', [])
                    
                    logger.info(f"📊 Found {len(orders)} order mappings in database")
                    
                    # Process each order
                    for order in orders:
                        await self._process_order(order)
                    
                    # Print summary
                    self._print_summary()
                    
                else:
                    logger.error(f"❌ Failed to get order mappings: {response.status_code}")
                    
        except Exception as e:
            logger.error(f"❌ Error in order registration: {e}")
            self.metrics['errors'] += 1
    
    async def _process_order(self, order: Dict[str, Any]):
        """Process a single order for registration"""
        try:
            self.metrics['orders_processed'] += 1
            
            client_order_id = order.get('client_order_id')
            exchange_order_id = order.get('exchange_order_id')
            symbol = order.get('symbol')
            side = order.get('side')
            status = order.get('status')
            
            # Skip orders without exchange order ID
            if not exchange_order_id:
                logger.debug(f"⚠️ Skipping order {client_order_id} - no exchange order ID")
                self.metrics['orders_skipped'] += 1
                return
            
            # Check if order is already tracked
            if self.redis_client.exists(f"order:{client_order_id}"):
                logger.debug(f"📝 Order {client_order_id} already tracked in Redis")
                return
            
            # Register order in Redis
            await self._register_order_in_redis(order)
            self.metrics['orders_registered'] += 1
            
            logger.info(f"✅ Registered order {client_order_id} ({symbol} {side} {status})")
            
        except Exception as e:
            logger.error(f"❌ Error processing order {order.get('client_order_id', 'unknown')}: {e}")
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
                'order_type': order.get('order_type', 'limit'),
                'timestamp': datetime.utcnow().isoformat(),
                'registered_at': datetime.utcnow().isoformat()
            }
            
            # Store in Redis hash
            self.redis_client.hset(f"order:{order['client_order_id']}", mapping=order_data)
            
            # Add to tracking set
            self.redis_client.sadd("tracked_orders", order['client_order_id'])
            
            # Add to exchange-specific tracking sets
            exchange = order['exchange']
            self.redis_client.sadd(f"tracked_orders_{exchange}", order['client_order_id'])
            
            # Add to symbol-specific tracking sets
            symbol = order['symbol'].replace('/', '_')
            self.redis_client.sadd(f"tracked_orders_{symbol}", order['client_order_id'])
            
        except Exception as e:
            logger.error(f"❌ Error registering order {order.get('client_order_id')}: {e}")
            raise
    
    def _print_summary(self):
        """Print registration summary"""
        logger.info("\n" + "="*60)
        logger.info("📊 ORDER REGISTRATION SUMMARY")
        logger.info("="*60)
        
        total_processed = self.metrics['orders_processed']
        total_registered = self.metrics['orders_registered']
        total_skipped = self.metrics['orders_skipped']
        total_errors = self.metrics['errors']
        
        logger.info(f"📋 Orders Processed: {total_processed}")
        logger.info(f"✅ Orders Registered: {total_registered}")
        logger.info(f"⚠️ Orders Skipped: {total_skipped}")
        logger.info(f"❌ Errors: {total_errors}")
        
        # Check Redis tracking
        tracked_count = self.redis_client.scard("tracked_orders")
        redis_orders = len(self.redis_client.keys("order:*"))
        
        logger.info(f"📊 Redis Tracking:")
        logger.info(f"   • Tracked Orders Set: {tracked_count}")
        logger.info(f"   • Order Records: {redis_orders}")
        
        # Check by exchange
        exchanges = ['binance', 'bybit', 'cryptocom']
        for exchange in exchanges:
            count = self.redis_client.scard(f"tracked_orders_{exchange}")
            if count > 0:
                logger.info(f"   • {exchange.upper()}: {count} orders")
        
        logger.info("="*60)
        
        if total_registered > 0:
            logger.info("🎉 Order registration completed successfully!")
        else:
            logger.warning("⚠️ No orders were registered - check for issues")
    
    def get_redis_stats(self) -> Dict[str, Any]:
        """Get Redis tracking statistics"""
        return {
            'tracked_orders_count': self.redis_client.scard("tracked_orders"),
            'order_records_count': len(self.redis_client.keys("order:*")),
            'binance_orders': self.redis_client.scard("tracked_orders_binance"),
            'bybit_orders': self.redis_client.scard("tracked_orders_bybit"),
            'cryptocom_orders': self.redis_client.scard("tracked_orders_cryptocom")
        }

async def main():
    """Main function"""
    registration_system = OrderRegistrationSystem()
    await registration_system.register_all_orders()
    
    # Show final Redis stats
    stats = registration_system.get_redis_stats()
    logger.info(f"📊 Final Redis Stats: {stats}")

if __name__ == "__main__":
    asyncio.run(main())
