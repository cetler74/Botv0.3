#!/usr/bin/env python3
"""
Fix Phantom Trades
This script fixes the phantom trades by:
1. Registering the missing orders in Redis
2. Updating order status to FILLED
3. Closing the phantom trades in the database
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
EXCHANGE_SERVICE_URL = "http://localhost:8003"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class PhantomTradeFixer:
    """
    System to fix phantom trades by updating order status and closing trades
    """
    
    def __init__(self):
        self.redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        self.metrics = {
            'orders_processed': 0,
            'orders_registered': 0,
            'orders_updated': 0,
            'trades_closed': 0,
            'errors': 0
        }
    
    async def fix_phantom_trades(self):
        """Fix all phantom trades"""
        logger.info("🚀 Starting phantom trade fix...")
        
        # Define the phantom orders based on the user's data
        phantom_orders = [
            {
                'client_order_id': 'oms696c8bb7L175728781833996c15807',
                'exchange_order_id': '1298977455',
                'symbol': 'BNB/USDC',
                'side': 'sell',
                'status': 'FILLED',
                'exchange': 'binance',
                'trade_id': '696c8bb7...'  # Partial trade ID for reference
            },
            {
                'client_order_id': 'oms7f25f0e4L175728722030090b5e9bd',
                'exchange_order_id': '6861084352',
                'symbol': 'ETH/USDC',
                'side': 'sell',
                'status': 'FILLED',
                'exchange': 'binance',
                'trade_id': '7f25f0e4...'
            },
            {
                'client_order_id': 'omsc9700220L175728721578886b4b045',
                'exchange_order_id': '6861083659',
                'symbol': 'ETH/USDC',
                'side': 'sell',
                'status': 'FILLED',
                'exchange': 'binance',
                'trade_id': 'c9700220...'
            },
            {
                'client_order_id': 'oms9d3c90f5L1757287154566ffcdf0ea',
                'exchange_order_id': '1298910113',
                'symbol': 'BNB/USDC',
                'side': 'sell',
                'status': 'FILLED',
                'exchange': 'binance',
                'trade_id': '9d3c90f5...'
            }
        ]
        
        try:
            # Step 1: Register orders in Redis
            for order in phantom_orders:
                await self._register_order_in_redis(order)
            
            # Step 2: Update order status in database
            for order in phantom_orders:
                await self._update_order_status(order)
            
            # Step 3: Close phantom trades
            await self._close_phantom_trades()
            
            # Print summary
            self._print_summary()
            
        except Exception as e:
            logger.error(f"❌ Error in phantom trade fix: {e}")
            self.metrics['errors'] += 1
    
    async def _register_order_in_redis(self, order: Dict[str, Any]):
        """Register order in Redis"""
        try:
            self.metrics['orders_processed'] += 1
            
            order_data = {
                'order_id': order['exchange_order_id'],
                'client_order_id': order['client_order_id'],
                'symbol': order['symbol'],
                'side': order['side'],
                'status': order['status'],
                'exchange': order['exchange'],
                'order_type': 'limit',
                'timestamp': datetime.utcnow().isoformat(),
                'registered_at': datetime.utcnow().isoformat(),
                'fixed_at': datetime.utcnow().isoformat()
            }
            
            # Store in Redis hash
            self.redis_client.hset(f"order:{order['client_order_id']}", mapping=order_data)
            
            # Add to tracking set
            self.redis_client.sadd("tracked_orders", order['client_order_id'])
            
            # Add to exchange-specific tracking sets
            self.redis_client.sadd(f"tracked_orders_{order['exchange']}", order['client_order_id'])
            
            self.metrics['orders_registered'] += 1
            logger.info(f"✅ Registered order {order['client_order_id']} in Redis")
            
        except Exception as e:
            logger.error(f"❌ Error registering order {order['client_order_id']}: {e}")
            self.metrics['errors'] += 1
    
    async def _update_order_status(self, order: Dict[str, Any]):
        """Update order status in database"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Update order mapping status
                update_data = {
                    'status': 'FILLED',
                    'updated_at': datetime.utcnow().isoformat()
                }
                
                response = await client.patch(
                    f"{DATABASE_SERVICE_URL}/api/v1/order-mappings/{order['client_order_id']}",
                    json=update_data
                )
                
                if response.status_code == 200:
                    self.metrics['orders_updated'] += 1
                    logger.info(f"✅ Updated order {order['client_order_id']} status to FILLED")
                else:
                    logger.error(f"❌ Failed to update order {order['client_order_id']}: {response.status_code}")
                    self.metrics['errors'] += 1
                    
        except Exception as e:
            logger.error(f"❌ Error updating order {order['client_order_id']}: {e}")
            self.metrics['errors'] += 1
    
    async def _close_phantom_trades(self):
        """Close phantom trades in database"""
        try:
            # Get all trades to find the phantom ones
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/trades")
                if response.status_code == 200:
                    trades_data = response.json()
                    trades = trades_data.get('trades', [])
                    
                    # Find trades that should be closed (have exit_id but status is OPEN)
                    phantom_trades = []
                    for trade in trades:
                        if (trade.get('status') == 'OPEN' and 
                            trade.get('exit_id') and 
                            trade.get('symbol') in ['BNB/USDC', 'ETH/USDC', 'BTC/USDC']):
                            phantom_trades.append(trade)
                    
                    logger.info(f"📊 Found {len(phantom_trades)} phantom trades to close")
                    
                    # Close each phantom trade
                    for trade in phantom_trades:
                        await self._close_trade(trade)
                        
        except Exception as e:
            logger.error(f"❌ Error closing phantom trades: {e}")
            self.metrics['errors'] += 1
    
    async def _close_trade(self, trade: Dict[str, Any]):
        """Close a single trade"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Update trade status to CLOSED
                update_data = {
                    'status': 'CLOSED',
                    'updated_at': datetime.utcnow().isoformat()
                }
                
                response = await client.patch(
                    f"{DATABASE_SERVICE_URL}/api/v1/trades/{trade['trade_id']}",
                    json=update_data
                )
                
                if response.status_code == 200:
                    self.metrics['trades_closed'] += 1
                    logger.info(f"✅ Closed trade {trade['trade_id']} ({trade['symbol']})")
                else:
                    logger.error(f"❌ Failed to close trade {trade['trade_id']}: {response.status_code}")
                    self.metrics['errors'] += 1
                    
        except Exception as e:
            logger.error(f"❌ Error closing trade {trade['trade_id']}: {e}")
            self.metrics['errors'] += 1
    
    def _print_summary(self):
        """Print fix summary"""
        logger.info("\n" + "="*60)
        logger.info("📊 PHANTOM TRADE FIX SUMMARY")
        logger.info("="*60)
        
        total_processed = self.metrics['orders_processed']
        total_registered = self.metrics['orders_registered']
        total_updated = self.metrics['orders_updated']
        total_closed = self.metrics['trades_closed']
        total_errors = self.metrics['errors']
        
        logger.info(f"📋 Orders Processed: {total_processed}")
        logger.info(f"✅ Orders Registered in Redis: {total_registered}")
        logger.info(f"✅ Orders Updated to FILLED: {total_updated}")
        logger.info(f"✅ Trades Closed: {total_closed}")
        logger.info(f"❌ Errors: {total_errors}")
        
        # Check Redis tracking
        tracked_count = self.redis_client.scard("tracked_orders")
        logger.info(f"📊 Redis Tracking: {tracked_count} orders tracked")
        
        logger.info("="*60)
        
        if total_errors == 0:
            logger.info("🎉 Phantom trade fix completed successfully!")
        else:
            logger.warning(f"⚠️ Fix completed with {total_errors} errors")

async def main():
    """Main function"""
    fixer = PhantomTradeFixer()
    await fixer.fix_phantom_trades()

if __name__ == "__main__":
    asyncio.run(main())
