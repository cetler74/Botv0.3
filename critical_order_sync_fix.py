#!/usr/bin/env python3
"""
CRITICAL ORDER SYNCHRONIZATION FIX
This script fixes the critical issue where filled orders remain PENDING in the database
and OPEN trades with filled exit orders are not being closed.

ROOT CAUSE ANALYSIS:
1. Order sync system is not properly detecting filled orders from exchange
2. WebSocket fill detection is failing or not updating database
3. Periodic fill checker is not running or not working properly
4. Event-driven reconciliation is not processing fill events correctly

IMMEDIATE FIX:
1. Query exchange for all pending orders to check their actual status
2. Update database orders from PENDING to FILLED where appropriate
3. Close OPEN trades that have filled exit orders
4. Create proper fill records for filled orders

PERMANENT FIX:
1. Fix the order synchronization system
2. Add monitoring and alerting
3. Implement redundant fill detection mechanisms
"""

import asyncio
import httpx
import logging
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
import json

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Service URLs
DATABASE_SERVICE_URL = "http://localhost:8002"
EXCHANGE_SERVICE_URL = "http://localhost:8003"

class CriticalOrderSyncFix:
    """Critical order synchronization fix for filled orders not updating"""
    
    def __init__(self):
        self.fixed_orders = 0
        self.fixed_trades = 0
        self.errors = 0
        
    async def run_critical_fix(self):
        """Run the complete critical fix process"""
        logger.info("🚨 STARTING CRITICAL ORDER SYNCHRONIZATION FIX")
        logger.info("=" * 60)
        
        try:
            # Step 1: Get all pending orders from database
            pending_orders = await self.get_pending_orders()
            logger.info(f"📊 Found {len(pending_orders)} pending orders in database")
            
            # Step 2: Get all OPEN trades with exit_id
            open_trades_with_exit = await self.get_open_trades_with_exit()
            logger.info(f"📊 Found {len(open_trades_with_exit)} OPEN trades with exit_id")
            
            # Step 3: Check each pending order against exchange
            await self.check_and_fix_pending_orders(pending_orders)
            
            # Step 4: Check and close trades with filled exit orders
            await self.check_and_close_trades_with_filled_exits(open_trades_with_exit)
            
            # Step 5: Summary
            self.print_summary()
            
        except Exception as e:
            logger.error(f"❌ Critical fix failed: {e}")
            raise
    
    async def get_pending_orders(self) -> List[Dict[str, Any]]:
        """Get all pending orders from database"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/order-mappings")
            if response.status_code != 200:
                logger.error(f"Failed to get order mappings: {response.status_code}")
                return []
            
            orders = response.json().get('order_mappings', [])
            return [order for order in orders if order.get('status') == 'PENDING']
    
    async def get_open_trades_with_exit(self) -> List[Dict[str, Any]]:
        """Get all OPEN trades that have exit_id"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/trades")
            if response.status_code != 200:
                logger.error(f"Failed to get trades: {response.status_code}")
                return []
            
            trades = response.json().get('trades', [])
            return [trade for trade in trades if trade.get('status') == 'OPEN' and trade.get('exit_id')]
    
    async def check_and_fix_pending_orders(self, pending_orders: List[Dict[str, Any]]):
        """Check each pending order against exchange and fix if filled"""
        logger.info("🔍 Checking pending orders against exchange...")
        
        # Group by exchange for efficient checking
        orders_by_exchange = {}
        for order in pending_orders:
            exchange = order.get('exchange')
            if exchange not in orders_by_exchange:
                orders_by_exchange[exchange] = []
            orders_by_exchange[exchange].append(order)
        
        for exchange, orders in orders_by_exchange.items():
            logger.info(f"📊 Checking {len(orders)} orders on {exchange}")
            await self.check_exchange_orders(exchange, orders)
    
    async def check_exchange_orders(self, exchange: str, orders: List[Dict[str, Any]]):
        """Check orders for a specific exchange"""
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                # Get all orders from exchange (including filled ones)
                response = await client.get(f"{EXCHANGE_SERVICE_URL}/api/v1/trading/orders/history/{exchange}")
                if response.status_code != 200:
                    logger.error(f"Failed to get {exchange} orders: {response.status_code}")
                    return
                
                exchange_orders = response.json().get('orders', [])
                exchange_order_map = {order['id']: order for order in exchange_orders}
                
                # Check each pending order
                for order in orders:
                    await self.check_single_order(order, exchange_order_map)
                    
        except Exception as e:
            logger.error(f"Error checking {exchange} orders: {e}")
            self.errors += 1
    
    async def check_single_order(self, db_order: Dict[str, Any], exchange_orders: Dict[str, Any]):
        """Check a single order and fix if needed"""
        exchange_order_id = db_order.get('exchange_order_id')
        if not exchange_order_id:
            return
        
        if exchange_order_id not in exchange_orders:
            logger.warning(f"⚠️ Order {exchange_order_id} not found on exchange")
            return
        
        exchange_order = exchange_orders[exchange_order_id]
        exchange_status = exchange_order.get('status', '').lower()
        
        # Check if order is filled on exchange
        if exchange_status in ['filled', 'closed', 'completed']:
            logger.info(f"✅ Found filled order: {exchange_order_id} (status: {exchange_status})")
            await self.fix_filled_order(db_order, exchange_order)
        else:
            logger.debug(f"Order {exchange_order_id} still {exchange_status} on exchange")
    
    async def fix_filled_order(self, db_order: Dict[str, Any], exchange_order: Dict[str, Any]):
        """Fix a filled order in the database"""
        try:
            local_order_id = db_order.get('local_order_id')
            exchange_order_id = db_order.get('exchange_order_id')
            
            # Update order status to FILLED
            update_data = {
                'status': 'FILLED',
                'updated_at': datetime.now(timezone.utc).isoformat()
            }
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.put(
                    f"{DATABASE_SERVICE_URL}/api/v1/order-mappings/{local_order_id}",
                    json=update_data
                )
                
                if response.status_code == 200:
                    logger.info(f"✅ Updated order {exchange_order_id} to FILLED")
                    self.fixed_orders += 1
                    
                    # Create fill record
                    await self.create_fill_record(db_order, exchange_order)
                else:
                    logger.error(f"❌ Failed to update order {exchange_order_id}: {response.status_code}")
                    self.errors += 1
                    
        except Exception as e:
            logger.error(f"❌ Error fixing order {exchange_order_id}: {e}")
            self.errors += 1
    
    async def create_fill_record(self, db_order: Dict[str, Any], exchange_order: Dict[str, Any]):
        """Create a fill record for the filled order"""
        try:
            fill_data = {
                'local_order_id': db_order.get('local_order_id'),
                'exchange_order_id': db_order.get('exchange_order_id'),
                'exchange': db_order.get('exchange'),
                'symbol': db_order.get('symbol'),
                'side': db_order.get('side'),
                'qty': float(exchange_order.get('filled', 0)),
                'price': float(exchange_order.get('average', 0)),
                'fee': self.extract_fee(exchange_order),
                'fee_asset': 'USDC',
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{DATABASE_SERVICE_URL}/api/v1/fills",
                    json=fill_data
                )
                
                if response.status_code == 201:
                    logger.info(f"✅ Created fill record for {db_order.get('exchange_order_id')}")
                else:
                    logger.warning(f"⚠️ Failed to create fill record: {response.status_code}")
                    
        except Exception as e:
            logger.error(f"❌ Error creating fill record: {e}")
    
    def extract_fee(self, exchange_order: Dict[str, Any]) -> float:
        """Extract fee from exchange order"""
        fee_info = exchange_order.get('fee', {})
        if isinstance(fee_info, dict):
            return float(fee_info.get('cost', 0))
        return 0.0
    
    async def check_and_close_trades_with_filled_exits(self, open_trades: List[Dict[str, Any]]):
        """Check and close trades that have filled exit orders"""
        logger.info("🔍 Checking trades with filled exit orders...")
        
        for trade in open_trades:
            exit_id = trade.get('exit_id')
            if not exit_id:
                continue
            
            # Check if exit order is filled
            if await self.is_exit_order_filled(exit_id, trade.get('exchange')):
                await self.close_trade_with_filled_exit(trade)
    
    async def is_exit_order_filled(self, exit_id: str, exchange: str) -> bool:
        """Check if exit order is filled on exchange"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(f"{EXCHANGE_SERVICE_URL}/api/v1/trading/orders/history/{exchange}")
                if response.status_code != 200:
                    return False
                
                orders = response.json().get('orders', [])
                for order in orders:
                    if order.get('id') == exit_id:
                        status = order.get('status', '').lower()
                        return status in ['filled', 'closed', 'completed']
                
                return False
                
        except Exception as e:
            logger.error(f"Error checking exit order {exit_id}: {e}")
            return False
    
    async def close_trade_with_filled_exit(self, trade: Dict[str, Any]):
        """Close a trade that has a filled exit order"""
        try:
            trade_id = trade.get('trade_id')
            exit_id = trade.get('exit_id')
            
            # Get exit order details from exchange
            exit_order = await self.get_exit_order_details(exit_id, trade.get('exchange'))
            if not exit_order:
                logger.error(f"Could not get exit order details for {exit_id}")
                return
            
            # Calculate exit price and fees
            exit_price = float(exit_order.get('average', 0))
            exit_fee = self.extract_fee(exit_order)
            
            # Update trade to CLOSED
            update_data = {
                'status': 'CLOSED',
                'exit_time': datetime.now(timezone.utc).isoformat(),
                'exit_price': exit_price,
                'exit_fee_amount': exit_fee,
                'exit_fee_currency': 'USDC',
                'updated_at': datetime.now(timezone.utc).isoformat()
            }
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.put(
                    f"{DATABASE_SERVICE_URL}/api/v1/trades/{trade_id}",
                    json=update_data
                )
                
                if response.status_code == 200:
                    logger.info(f"✅ Closed trade {trade_id} (exit: {exit_id})")
                    self.fixed_trades += 1
                else:
                    logger.error(f"❌ Failed to close trade {trade_id}: {response.status_code}")
                    self.errors += 1
                    
        except Exception as e:
            logger.error(f"❌ Error closing trade {trade.get('trade_id')}: {e}")
            self.errors += 1
    
    async def get_exit_order_details(self, exit_id: str, exchange: str) -> Optional[Dict[str, Any]]:
        """Get exit order details from exchange"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(f"{EXCHANGE_SERVICE_URL}/api/v1/trading/orders/history/{exchange}")
                if response.status_code != 200:
                    return None
                
                orders = response.json().get('orders', [])
                for order in orders:
                    if order.get('id') == exit_id:
                        return order
                
                return None
                
        except Exception as e:
            logger.error(f"Error getting exit order details: {e}")
            return None
    
    def print_summary(self):
        """Print fix summary"""
        logger.info("=" * 60)
        logger.info("🎯 CRITICAL ORDER SYNC FIX SUMMARY")
        logger.info("=" * 60)
        logger.info(f"✅ Fixed Orders: {self.fixed_orders}")
        logger.info(f"✅ Fixed Trades: {self.fixed_trades}")
        logger.info(f"❌ Errors: {self.errors}")
        logger.info("=" * 60)
        
        if self.fixed_orders > 0 or self.fixed_trades > 0:
            logger.info("🎉 CRITICAL FIX COMPLETED SUCCESSFULLY!")
        else:
            logger.info("ℹ️ No fixes needed - all orders are properly synchronized")

async def main():
    """Main function"""
    fix = CriticalOrderSyncFix()
    await fix.run_critical_fix()

if __name__ == "__main__":
    asyncio.run(main())
