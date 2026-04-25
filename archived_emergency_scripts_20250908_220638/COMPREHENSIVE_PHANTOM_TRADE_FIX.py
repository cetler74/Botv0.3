#!/usr/bin/env python3
"""
Comprehensive Phantom Trade Fix
This script fixes all existing phantom trades and implements additional safeguards
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

class ComprehensivePhantomTradeFixer:
    """
    System to fix all phantom trades and implement comprehensive safeguards
    """
    
    def __init__(self):
        self.redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        self.metrics = {
            'phantom_trades_found': 0,
            'phantom_trades_fixed': 0,
            'orders_synchronized': 0,
            'errors': 0
        }
    
    async def fix_all_phantom_trades(self):
        """Fix all phantom trades comprehensively"""
        logger.info("🚀 Starting comprehensive phantom trade fix...")
        
        try:
            # Step 1: Find all phantom trades
            phantom_trades = await self._find_phantom_trades()
            
            # Step 2: Fix each phantom trade
            for trade in phantom_trades:
                await self._fix_phantom_trade(trade)
            
            # Step 3: Synchronize all order statuses
            await self._synchronize_all_order_statuses()
            
            # Step 4: Implement additional safeguards
            await self._implement_additional_safeguards()
            
            # Print summary
            self._print_summary()
            
        except Exception as e:
            logger.error(f"❌ Error in comprehensive phantom trade fix: {e}")
            self.metrics['errors'] += 1
    
    async def _find_phantom_trades(self) -> List[Dict[str, Any]]:
        """Find all phantom trades (trades with exit_id but status OPEN)"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/trades")
                if response.status_code == 200:
                    trades_data = response.json()
                    trades = trades_data.get('trades', [])
                    
                    phantom_trades = []
                    for trade in trades:
                        if (trade.get('status') == 'OPEN' and 
                            trade.get('exit_id') and 
                            trade.get('exit_id') != 'N/A'):
                            phantom_trades.append(trade)
                    
                    self.metrics['phantom_trades_found'] = len(phantom_trades)
                    logger.info(f"📊 Found {len(phantom_trades)} phantom trades")
                    
                    return phantom_trades
                else:
                    logger.error(f"❌ Failed to get trades: {response.status_code}")
                    return []
                    
        except Exception as e:
            logger.error(f"❌ Error finding phantom trades: {e}")
            return []
    
    async def _fix_phantom_trade(self, trade: Dict[str, Any]):
        """Fix a single phantom trade"""
        try:
            trade_id = trade['trade_id']
            exit_id = trade['exit_id']
            symbol = trade['pair']
            
            logger.info(f"🔧 Fixing phantom trade {trade_id} ({symbol}) with exit_id {exit_id}")
            
            # Step 1: Check if order is tracked in Redis
            order_tracked = await self._check_order_tracking(exit_id)
            
            # Step 2: Verify order status on exchange
            exchange_status = await self._check_exchange_order_status(exit_id, symbol)
            
            # Step 3: Synchronize Redis order status
            if order_tracked and exchange_status == 'FILLED':
                await self._synchronize_redis_order_status(exit_id)
            
            # Step 4: Close the trade
            await self._close_phantom_trade(trade_id)
            
            self.metrics['phantom_trades_fixed'] += 1
            logger.info(f"✅ Fixed phantom trade {trade_id}")
            
        except Exception as e:
            logger.error(f"❌ Error fixing phantom trade {trade.get('trade_id')}: {e}")
            self.metrics['errors'] += 1
    
    async def _check_order_tracking(self, exit_id: str) -> bool:
        """Check if order is tracked in Redis"""
        try:
            # Check both key formats
            old_format = self.redis_client.exists(f"orders:{exit_id}")
            new_format = False
            
            if old_format:
                order_data = self.redis_client.hgetall(f"orders:{exit_id}")
                client_order_id = order_data.get("client_order_id")
                if client_order_id:
                    new_format = self.redis_client.exists(f"order:{client_order_id}")
            
            return old_format or new_format
            
        except Exception as e:
            logger.error(f"❌ Error checking order tracking for {exit_id}: {e}")
            return False
    
    async def _check_exchange_order_status(self, exit_id: str, symbol: str) -> str:
        """Check order status on exchange"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(f"{EXCHANGE_SERVICE_URL}/api/v1/trading/order/binance/{exit_id}?symbol={symbol}")
                if response.status_code == 200:
                    order_data = response.json()
                    return order_data.get('status', 'unknown')
                else:
                    logger.warning(f"⚠️ Failed to get exchange order status for {exit_id}: {response.status_code}")
                    return 'unknown'
                    
        except Exception as e:
            logger.error(f"❌ Error checking exchange order status for {exit_id}: {e}")
            return 'unknown'
    
    async def _synchronize_redis_order_status(self, exit_id: str):
        """Synchronize Redis order status between both key formats"""
        try:
            # Get order data from old format
            order_data = self.redis_client.hgetall(f"orders:{exit_id}")
            if not order_data:
                return
            
            client_order_id = order_data.get("client_order_id")
            if not client_order_id:
                return
            
            # Update new format with current status
            status_update = {
                "status": order_data.get("status", "pending"),
                "updated_at": str(datetime.utcnow().timestamp())
            }
            
            if order_data.get("filled_amount"):
                status_update["filled_amount"] = order_data["filled_amount"]
            if order_data.get("filled_price"):
                status_update["filled_price"] = order_data["filled_price"]
            if order_data.get("fees"):
                status_update["fees"] = order_data["fees"]
            if order_data.get("filled_at"):
                status_update["filled_at"] = order_data["filled_at"]
            
            self.redis_client.hset(f"order:{client_order_id}", mapping=status_update)
            self.metrics['orders_synchronized'] += 1
            
            logger.info(f"✅ Synchronized Redis order status for {exit_id}")
            
        except Exception as e:
            logger.error(f"❌ Error synchronizing Redis order status for {exit_id}: {e}")
    
    async def _close_phantom_trade(self, trade_id: str):
        """Close a phantom trade in the database"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                update_data = {
                    'status': 'CLOSED',
                    'updated_at': datetime.utcnow().isoformat()
                }
                
                response = await client.patch(f"{DATABASE_SERVICE_URL}/api/v1/trades/{trade_id}", json=update_data)
                
                if response.status_code == 200:
                    logger.info(f"✅ Closed phantom trade {trade_id}")
                else:
                    logger.error(f"❌ Failed to close phantom trade {trade_id}: {response.status_code}")
                    
        except Exception as e:
            logger.error(f"❌ Error closing phantom trade {trade_id}: {e}")
    
    async def _synchronize_all_order_statuses(self):
        """Synchronize all order statuses between Redis key formats"""
        try:
            logger.info("🔄 Synchronizing all order statuses...")
            
            # Get all orders in old format
            old_keys = self.redis_client.keys("orders:*")
            
            for old_key in old_keys:
                try:
                    order_data = self.redis_client.hgetall(old_key)
                    client_order_id = order_data.get("client_order_id")
                    
                    if client_order_id:
                        # Check if new format exists and is different
                        new_data = self.redis_client.hgetall(f"order:{client_order_id}")
                        if new_data and new_data.get("status") != order_data.get("status"):
                            # Synchronize status
                            status_update = {
                                "status": order_data.get("status", "pending"),
                                "updated_at": str(datetime.utcnow().timestamp())
                            }
                            
                            if order_data.get("filled_amount"):
                                status_update["filled_amount"] = order_data["filled_amount"]
                            if order_data.get("filled_price"):
                                status_update["filled_price"] = order_data["filled_price"]
                            if order_data.get("fees"):
                                status_update["fees"] = order_data["fees"]
                            if order_data.get("filled_at"):
                                status_update["filled_at"] = order_data["filled_at"]
                            
                            self.redis_client.hset(f"order:{client_order_id}", mapping=status_update)
                            self.metrics['orders_synchronized'] += 1
                            
                except Exception as e:
                    logger.error(f"❌ Error synchronizing order {old_key}: {e}")
            
            logger.info(f"✅ Synchronized {self.metrics['orders_synchronized']} order statuses")
            
        except Exception as e:
            logger.error(f"❌ Error synchronizing order statuses: {e}")
    
    async def _implement_additional_safeguards(self):
        """Implement additional safeguards to prevent future phantom trades"""
        try:
            logger.info("🛡️ Implementing additional safeguards...")
            
            # Add all orders to tracked_orders set
            old_keys = self.redis_client.keys("orders:*")
            for old_key in old_keys:
                try:
                    order_data = self.redis_client.hgetall(old_key)
                    client_order_id = order_data.get("client_order_id")
                    if client_order_id:
                        self.redis_client.sadd("tracked_orders", client_order_id)
                        
                        # Add to exchange-specific tracking
                        exchange = order_data.get("exchange", "unknown")
                        self.redis_client.sadd(f"tracked_orders_{exchange}", client_order_id)
                        
                        # Add to symbol-specific tracking
                        symbol = order_data.get("symbol", "").replace("/", "_")
                        if symbol:
                            self.redis_client.sadd(f"tracked_orders_{symbol}", client_order_id)
                            
                except Exception as e:
                    logger.error(f"❌ Error adding safeguard for order {old_key}: {e}")
            
            logger.info("✅ Additional safeguards implemented")
            
        except Exception as e:
            logger.error(f"❌ Error implementing safeguards: {e}")
    
    def _print_summary(self):
        """Print comprehensive fix summary"""
        logger.info("\n" + "="*70)
        logger.info("📊 COMPREHENSIVE PHANTOM TRADE FIX SUMMARY")
        logger.info("="*70)
        
        phantom_found = self.metrics['phantom_trades_found']
        phantom_fixed = self.metrics['phantom_trades_fixed']
        orders_synced = self.metrics['orders_synchronized']
        errors = self.metrics['errors']
        
        logger.info(f"📋 Phantom Trades Found: {phantom_found}")
        logger.info(f"✅ Phantom Trades Fixed: {phantom_fixed}")
        logger.info(f"🔄 Orders Synchronized: {orders_synced}")
        logger.info(f"❌ Errors: {errors}")
        
        # Check Redis tracking
        tracked_count = self.redis_client.scard("tracked_orders")
        old_format_count = len(self.redis_client.keys("orders:*"))
        new_format_count = len(self.redis_client.keys("order:*"))
        
        logger.info(f"📊 Redis Tracking:")
        logger.info(f"   • Tracked Orders Set: {tracked_count}")
        logger.info(f"   • Old Format (orders:*): {old_format_count}")
        logger.info(f"   • New Format (order:*): {new_format_count}")
        
        logger.info("="*70)
        
        if errors == 0:
            logger.info("🎉 Comprehensive phantom trade fix completed successfully!")
        else:
            logger.warning(f"⚠️ Fix completed with {errors} errors")

async def main():
    """Main function"""
    fixer = ComprehensivePhantomTradeFixer()
    await fixer.fix_all_phantom_trades()

if __name__ == "__main__":
    asyncio.run(main())
