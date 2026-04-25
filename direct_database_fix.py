#!/usr/bin/env python3
"""
DIRECT DATABASE FIX
This script directly fixes the database by updating the specific orders that are known to be filled
based on the user's exchange data.

CRITICAL ORDERS TO FIX:
- 208548970 (TRX/USDC Sell) - Filled on 2025-09-11 02:14:00
- 686517390 (LTC/USDC Sell) - Filled on 2025-09-10 17:22:37
- 2356767637 (XRP/USDC Sell) - Likely filled
- 6898047748 (ETH/USDC Sell) - Likely filled

This is a direct fix that bypasses the broken exchange service.
"""

import asyncio
import httpx
import logging
from datetime import datetime, timezone
from typing import Dict, List, Any

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Service URLs
DATABASE_SERVICE_URL = "http://localhost:8002"

class DirectDatabaseFix:
    """Direct database fix for known filled orders"""
    
    def __init__(self):
        self.fixed_orders = 0
        self.fixed_trades = 0
        self.errors = 0
        
        # Known filled orders from user's exchange data
        self.known_filled_orders = {
            '208548970': {
                'symbol': 'TRX/USDC',
                'side': 'sell',
                'exchange': 'binance',
                'filled_price': 0.3412,
                'filled_amount': 303,
                'fee': 0.09821442,
                'fee_currency': 'USDC',
                'filled_time': '2025-09-11 02:14:00'
            },
            '686517390': {
                'symbol': 'LTC/USDC',
                'side': 'sell',
                'exchange': 'binance',
                'filled_price': 116.42,
                'filled_amount': 0.897,
                'fee': 0.0992073,
                'fee_currency': 'USDC',
                'filled_time': '2025-09-10 17:22:37'
            }
        }
        
        # Trades that should be closed (have filled exit orders)
        self.trades_to_close = [
            'dd85627c-af7d-4d5c-a3d2-0f02a49acdd2',  # TRX/USDC with exit 208548970
            '87da62cc-1bd0-485f-b881-1b00df20a7d2',  # LTC/USDC with exit 686517390
            'c01d318b-7ec7-4b9b-9c98-8e2434893175',  # XRP/USDC with exit 2356767637
            '7839e102-3ef6-48bb-80cb-4fae3267ff67'   # ETH/USDC with exit 6898047748
        ]
    
    async def run_direct_fix(self):
        """Run the direct database fix"""
        logger.info("🚨 STARTING DIRECT DATABASE FIX")
        logger.info("=" * 60)
        
        try:
            # Step 1: Fix known filled orders
            await self.fix_known_filled_orders()
            
            # Step 2: Close trades with filled exit orders
            await self.close_trades_with_filled_exits()
            
            # Step 3: Summary
            self.print_summary()
            
        except Exception as e:
            logger.error(f"❌ Direct fix failed: {e}")
            raise
    
    async def fix_known_filled_orders(self):
        """Fix the known filled orders"""
        logger.info("🔧 Fixing known filled orders...")
        
        for exchange_order_id, order_data in self.known_filled_orders.items():
            try:
                await self.fix_single_filled_order(exchange_order_id, order_data)
            except Exception as e:
                logger.error(f"❌ Failed to fix order {exchange_order_id}: {e}")
                self.errors += 1
    
    async def fix_single_filled_order(self, exchange_order_id: str, order_data: Dict[str, Any]):
        """Fix a single filled order"""
        logger.info(f"🔧 Fixing order {exchange_order_id} ({order_data['symbol']})")
        
        # Get the order from database
        order = await self.get_order_by_exchange_id(exchange_order_id)
        if not order:
            logger.warning(f"⚠️ Order {exchange_order_id} not found in database")
            return
        
        local_order_id = order.get('local_order_id')
        
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
                await self.create_fill_record(order, order_data)
            else:
                logger.error(f"❌ Failed to update order {exchange_order_id}: {response.status_code}")
                self.errors += 1
    
    async def get_order_by_exchange_id(self, exchange_order_id: str) -> Dict[str, Any]:
        """Get order by exchange order ID"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/order-mappings")
            if response.status_code != 200:
                return None
            
            orders = response.json().get('order_mappings', [])
            for order in orders:
                if order.get('exchange_order_id') == exchange_order_id:
                    return order
            
            return None
    
    async def create_fill_record(self, order: Dict[str, Any], order_data: Dict[str, Any]):
        """Create fill record for the order"""
        try:
            fill_data = {
                'local_order_id': order.get('local_order_id'),
                'exchange_order_id': order.get('exchange_order_id'),
                'exchange': order.get('exchange'),
                'symbol': order.get('symbol'),
                'side': order.get('side'),
                'qty': order_data['filled_amount'],
                'price': order_data['filled_price'],
                'fee': order_data['fee'],
                'fee_asset': order_data['fee_currency'],
                'timestamp': datetime.now(timezone.utc).isoformat()
            }
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{DATABASE_SERVICE_URL}/api/v1/fills",
                    json=fill_data
                )
                
                if response.status_code == 201:
                    logger.info(f"✅ Created fill record for {order.get('exchange_order_id')}")
                else:
                    logger.warning(f"⚠️ Failed to create fill record: {response.status_code}")
                    
        except Exception as e:
            logger.error(f"❌ Error creating fill record: {e}")
    
    async def close_trades_with_filled_exits(self):
        """Close trades that have filled exit orders"""
        logger.info("🔧 Closing trades with filled exit orders...")
        
        for trade_id in self.trades_to_close:
            try:
                await self.close_single_trade(trade_id)
            except Exception as e:
                logger.error(f"❌ Failed to close trade {trade_id}: {e}")
                self.errors += 1
    
    async def close_single_trade(self, trade_id: str):
        """Close a single trade"""
        logger.info(f"🔧 Closing trade {trade_id}")
        
        # Get trade details
        trade = await self.get_trade_by_id(trade_id)
        if not trade:
            logger.warning(f"⚠️ Trade {trade_id} not found")
            return
        
        exit_id = trade.get('exit_id')
        if not exit_id:
            logger.warning(f"⚠️ Trade {trade_id} has no exit_id")
            return
        
        # Get exit order data
        exit_order_data = self.known_filled_orders.get(exit_id)
        if not exit_order_data:
            logger.warning(f"⚠️ Exit order {exit_id} not in known filled orders")
            return
        
        # Update trade to CLOSED
        update_data = {
            'status': 'CLOSED',
            'exit_time': datetime.now(timezone.utc).isoformat(),
            'exit_price': exit_order_data['filled_price'],
            'exit_fee_amount': exit_order_data['fee'],
            'exit_fee_currency': exit_order_data['fee_currency'],
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
    
    async def get_trade_by_id(self, trade_id: str) -> Dict[str, Any]:
        """Get trade by ID"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/trades")
            if response.status_code != 200:
                return None
            
            trades = response.json().get('trades', [])
            for trade in trades:
                if trade.get('trade_id') == trade_id:
                    return trade
            
            return None
    
    def print_summary(self):
        """Print fix summary"""
        logger.info("=" * 60)
        logger.info("🎯 DIRECT DATABASE FIX SUMMARY")
        logger.info("=" * 60)
        logger.info(f"✅ Fixed Orders: {self.fixed_orders}")
        logger.info(f"✅ Fixed Trades: {self.fixed_trades}")
        logger.info(f"❌ Errors: {self.errors}")
        logger.info("=" * 60)
        
        if self.fixed_orders > 0 or self.fixed_trades > 0:
            logger.info("🎉 DIRECT FIX COMPLETED SUCCESSFULLY!")
        else:
            logger.info("ℹ️ No fixes applied")

async def main():
    """Main function"""
    fix = DirectDatabaseFix()
    await fix.run_direct_fix()

if __name__ == "__main__":
    asyncio.run(main())
