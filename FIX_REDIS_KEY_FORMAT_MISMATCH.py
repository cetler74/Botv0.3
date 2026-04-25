#!/usr/bin/env python3
"""
Fix Redis Key Format Mismatch
This script fixes the mismatch between order registration and fill detection:
- Orders are registered as: orders:{exchange_order_id}
- Fill detection looks for: order:{client_order_id}
"""

import asyncio
import redis
import json
import logging
from datetime import datetime
from typing import Dict, Any, List

# Configuration
REDIS_URL = "redis://localhost:6379"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class RedisKeyFormatFixer:
    """
    System to fix Redis key format mismatch and ensure fill detection works
    """
    
    def __init__(self):
        self.redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        self.metrics = {
            'orders_processed': 0,
            'keys_created': 0,
            'keys_updated': 0,
            'errors': 0
        }
    
    async def fix_key_format_mismatch(self):
        """Fix the Redis key format mismatch"""
        logger.info("🚀 Starting Redis key format mismatch fix...")
        
        try:
            # Get all orders with the old format
            old_keys = self.redis_client.keys("orders:*")
            logger.info(f"📊 Found {len(old_keys)} orders with old key format")
            
            # Process each order
            for old_key in old_keys:
                await self._process_order_key(old_key)
            
            # Print summary
            self._print_summary()
            
        except Exception as e:
            logger.error(f"❌ Error in key format fix: {e}")
            self.metrics['errors'] += 1
    
    async def _process_order_key(self, old_key: str):
        """Process a single order key"""
        try:
            self.metrics['orders_processed'] += 1
            
            # Get order data from old key
            order_data = self.redis_client.hgetall(old_key)
            if not order_data:
                logger.warning(f"⚠️ No data found for key {old_key}")
                return
            
            # Extract client_order_id
            client_order_id = order_data.get('client_order_id')
            if not client_order_id:
                logger.warning(f"⚠️ No client_order_id found for key {old_key}")
                return
            
            # Create new key with correct format
            new_key = f"order:{client_order_id}"
            
            # Check if new key already exists
            if self.redis_client.exists(new_key):
                logger.info(f"📝 Key {new_key} already exists, updating...")
                self.metrics['keys_updated'] += 1
            else:
                logger.info(f"✅ Creating new key {new_key}")
                self.metrics['keys_created'] += 1
            
            # Copy data to new key format
            self.redis_client.hset(new_key, mapping=order_data)
            
            # Add to tracked_orders set
            self.redis_client.sadd("tracked_orders", client_order_id)
            
            # Add to exchange-specific tracking sets
            exchange = order_data.get('exchange', 'unknown')
            self.redis_client.sadd(f"tracked_orders_{exchange}", client_order_id)
            
            # Add to symbol-specific tracking sets
            symbol = order_data.get('symbol', '').replace('/', '_')
            if symbol:
                self.redis_client.sadd(f"tracked_orders_{symbol}", client_order_id)
            
            logger.info(f"✅ Processed order {client_order_id} ({order_data.get('symbol', 'unknown')} {order_data.get('side', 'unknown')} {order_data.get('status', 'unknown')})")
            
        except Exception as e:
            logger.error(f"❌ Error processing key {old_key}: {e}")
            self.metrics['errors'] += 1
    
    def _print_summary(self):
        """Print fix summary"""
        logger.info("\n" + "="*60)
        logger.info("📊 REDIS KEY FORMAT FIX SUMMARY")
        logger.info("="*60)
        
        total_processed = self.metrics['orders_processed']
        total_created = self.metrics['keys_created']
        total_updated = self.metrics['keys_updated']
        total_errors = self.metrics['errors']
        
        logger.info(f"📋 Orders Processed: {total_processed}")
        logger.info(f"✅ New Keys Created: {total_created}")
        logger.info(f"📝 Keys Updated: {total_updated}")
        logger.info(f"❌ Errors: {total_errors}")
        
        # Check Redis tracking
        tracked_count = self.redis_client.scard("tracked_orders")
        old_format_count = len(self.redis_client.keys("orders:*"))
        new_format_count = len(self.redis_client.keys("order:*"))
        
        logger.info(f"📊 Redis Tracking:")
        logger.info(f"   • Tracked Orders Set: {tracked_count}")
        logger.info(f"   • Old Format (orders:*): {old_format_count}")
        logger.info(f"   • New Format (order:*): {new_format_count}")
        
        # Check by exchange
        exchanges = ['binance', 'bybit', 'cryptocom']
        for exchange in exchanges:
            count = self.redis_client.scard(f"tracked_orders_{exchange}")
            if count > 0:
                logger.info(f"   • {exchange.upper()}: {count} orders")
        
        logger.info("="*60)
        
        if total_errors == 0:
            logger.info("🎉 Redis key format fix completed successfully!")
        else:
            logger.warning(f"⚠️ Fix completed with {total_errors} errors")
    
    def get_redis_stats(self) -> Dict[str, Any]:
        """Get Redis tracking statistics"""
        return {
            'tracked_orders_count': self.redis_client.scard("tracked_orders"),
            'old_format_count': len(self.redis_client.keys("orders:*")),
            'new_format_count': len(self.redis_client.keys("order:*")),
            'binance_orders': self.redis_client.scard("tracked_orders_binance"),
            'bybit_orders': self.redis_client.scard("tracked_orders_bybit"),
            'cryptocom_orders': self.redis_client.scard("tracked_orders_cryptocom")
        }

async def main():
    """Main function"""
    fixer = RedisKeyFormatFixer()
    await fixer.fix_key_format_mismatch()
    
    # Show final Redis stats
    stats = fixer.get_redis_stats()
    logger.info(f"📊 Final Redis Stats: {stats}")

if __name__ == "__main__":
    asyncio.run(main())
