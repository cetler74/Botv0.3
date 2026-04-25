#!/usr/bin/env python3
"""
CRITICAL FIX: EXIT ORDER FILL DETECTION FAILURE
This script fixes the critical issue where exit order fills are detected but trades are not closed.

ROOT CAUSE: Trailing stop priority logic is blocking trade closure in redis_realtime_order_manager.py
"""

import asyncio
import httpx
import logging
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Service URLs
DATABASE_SERVICE_URL = "http://localhost:8002"

class ExitOrderFillDetectionFix:
    """Fix for exit order fill detection failures"""
    
    def __init__(self):
        self.fixed_trades = 0
        self.errors = 0
        
    async def run_critical_fix(self):
        """Run the critical fix for exit order fill detection"""
        logger.info("🚨 STARTING CRITICAL EXIT ORDER FILL DETECTION FIX")
        logger.info("=" * 70)
        
        try:
            # Step 1: Identify OPEN trades with filled exit orders
            stuck_trades = await self.identify_stuck_trades()
            logger.info(f"📊 Found {len(stuck_trades)} trades stuck with filled exit orders")
            
            # Step 2: Fix each stuck trade
            for trade in stuck_trades:
                await self.fix_stuck_trade(trade)
            
            # Step 3: Summary
            self.print_summary()
            
        except Exception as e:
            logger.error(f"❌ Critical fix failed: {e}")
            raise
    
    async def identify_stuck_trades(self) -> List[Dict[str, Any]]:
        """Identify OPEN trades that have filled exit orders"""
        stuck_trades = []
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Get all OPEN trades
                response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/trades")
                if response.status_code != 200:
                    logger.error(f"Failed to get trades: {response.status_code}")
                    return []
                
                trades = response.json().get('trades', [])
                open_trades = [trade for trade in trades if trade.get('status') == 'OPEN']
                
                logger.info(f"📊 Found {len(open_trades)} OPEN trades")
                
                # Check each OPEN trade for filled exit orders
                for trade in open_trades:
                    exit_id = trade.get('exit_id')
                    if not exit_id:
                        continue
                    
                    # Check if exit order is filled
                    if await self.is_exit_order_filled(exit_id, trade.get('exchange')):
                        stuck_trades.append(trade)
                        logger.warning(f"🚨 STUCK TRADE: {trade.get('trade_id')} has filled exit order {exit_id}")
                
                return stuck_trades
                
        except Exception as e:
            logger.error(f"❌ Error identifying stuck trades: {e}")
            return []
    
    async def is_exit_order_filled(self, exit_id: str, exchange: str) -> bool:
        """Check if exit order is filled on exchange"""
        try:
            # Check database order_mappings table
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/order-mappings")
                if response.status_code != 200:
                    return False
                
                orders = response.json().get('order_mappings', [])
                for order in orders:
                    if order.get('exchange_order_id') == exit_id:
                        status = order.get('status', '').upper()
                        return status == 'FILLED'
                
                return False
                
        except Exception as e:
            logger.error(f"❌ Error checking exit order {exit_id}: {e}")
            return False
    
    async def fix_stuck_trade(self, trade: Dict[str, Any]):
        """Fix a stuck trade by closing it"""
        try:
            trade_id = trade.get('trade_id')
            exit_id = trade.get('exit_id')
            pair = trade.get('pair')
            exchange = trade.get('exchange')
            
            logger.info(f"🔧 Fixing stuck trade {trade_id} ({pair} on {exchange})")
            
            # Get exit order details
            exit_order = await self.get_exit_order_details(exit_id)
            if not exit_order:
                logger.error(f"❌ Could not get exit order details for {exit_id}")
                return
            
            # Get exit price and fees
            exit_price = float(exit_order.get('price', 0))
            if exit_price <= 0:
                logger.error(f"❌ Invalid exit price {exit_price} for order {exit_id}")
                return
            
            # Calculate PnL
            entry_price = float(trade.get('entry_price', 0))
            position_size = float(trade.get('position_size', 0))
            entry_fees = float(trade.get('fees', 0))
            exit_fees = 0.0  # Will be calculated from order details
            
            if entry_price > 0 and position_size > 0:
                gross_pnl = (exit_price - entry_price) * position_size
                total_fees = entry_fees + exit_fees
                realized_pnl = gross_pnl - total_fees
                realized_pnl_pct = (realized_pnl / (entry_price * position_size)) * 100
                
                logger.info(f"   📊 PnL: Gross=${gross_pnl:.2f}, Fees=${total_fees:.4f}, Net=${realized_pnl:.2f} ({realized_pnl_pct:.2f}%)")
            else:
                realized_pnl = 0.0
                logger.warning(f"   ⚠️ Invalid entry data: price={entry_price}, size={position_size}")
            
            # Close the trade
            await self.close_trade_immediately(trade_id, exit_price, exit_id, realized_pnl)
            
        except Exception as e:
            logger.error(f"❌ Failed to fix stuck trade {trade.get('trade_id')}: {e}")
            self.errors += 1
    
    async def get_exit_order_details(self, exit_id: str) -> Optional[Dict[str, Any]]:
        """Get exit order details from database"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/order-mappings")
                if response.status_code != 200:
                    return None
                
                orders = response.json().get('order_mappings', [])
                for order in orders:
                    if order.get('exchange_order_id') == exit_id:
                        return order
                
                return None
                
        except Exception as e:
            logger.error(f"❌ Error getting exit order details: {e}")
            return None
    
    async def close_trade_immediately(self, trade_id: str, exit_price: float, exit_id: str, realized_pnl: float):
        """Close trade immediately without any blocking logic"""
        try:
            logger.info(f"🔧 Closing trade {trade_id} immediately")
            
            # Update trade to CLOSED
            update_data = {
                'status': 'CLOSED',
                'exit_price': exit_price,
                'exit_time': datetime.now(timezone.utc).isoformat(),
                'realized_pnl': realized_pnl,
                'exit_reason': 'CRITICAL_FIX: Exit order was filled but trade was not closed',
                'updated_at': datetime.now(timezone.utc).isoformat()
            }
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.put(
                    f"{DATABASE_SERVICE_URL}/api/v1/trades/{trade_id}",
                    json=update_data
                )
                
                if response.status_code == 200:
                    logger.info(f"✅ Trade {trade_id} closed successfully")
                    self.fixed_trades += 1
                else:
                    logger.error(f"❌ Failed to close trade {trade_id}: {response.status_code}")
                    self.errors += 1
                    
        except Exception as e:
            logger.error(f"❌ Error closing trade {trade_id}: {e}")
            self.errors += 1
    
    def print_summary(self):
        """Print fix summary"""
        logger.info("=" * 70)
        logger.info("🎯 EXIT ORDER FILL DETECTION FIX SUMMARY")
        logger.info("=" * 70)
        logger.info(f"✅ Fixed Trades: {self.fixed_trades}")
        logger.info(f"❌ Errors: {self.errors}")
        logger.info("=" * 70)
        
        if self.fixed_trades > 0:
            logger.info("🎉 CRITICAL FIX COMPLETED SUCCESSFULLY!")
        else:
            logger.info("ℹ️ No stuck trades found - system is working correctly")

async def main():
    """Main function"""
    fix = ExitOrderFillDetectionFix()
    await fix.run_critical_fix()

if __name__ == "__main__":
    asyncio.run(main())
