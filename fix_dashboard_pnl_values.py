#!/usr/bin/env python3
"""
Fix Dashboard PnL Values - Update current unrealized PnL to include fees
This script updates all open trades in the database to use fee-inclusive PnL calculations.
"""

import asyncio
import logging
import sys
import os
from datetime import datetime

# Add the project root to the path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from strategy.strategy_pnl_enhanced import calculate_unrealized_pnl

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class DashboardPnLFixer:
    def __init__(self):
        self.db_manager = None
        
    async def connect_database(self):
        """Connect to the database service"""
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                # Test connection to database service
                response = await client.get("http://localhost:8002/health")
                if response.status_code == 200:
                    logger.info("✅ Database service connection successful")
                    return True
                else:
                    logger.error(f"❌ Database service health check failed: {response.status_code}")
                    return False
        except Exception as e:
            logger.error(f"❌ Failed to connect to database service: {e}")
            return False
    
    def create_mock_position(self, trade_data):
        """Create a mock position object for PnL calculation"""
        class MockPosition:
            def __init__(self, entry_price, position_size, entry_fee, exit_fee):
                self.entry_price = entry_price
                self.position_size = position_size
                self.entry_fee_amount = entry_fee
                self.exit_fee_amount = exit_fee
                self.position = 'long'  # SPOT trading - always long
                self.side = 'buy'       # SPOT trading - always buy
        
        return MockPosition(
            trade_data['entry_price'],
            trade_data['position_size'],
            trade_data.get('entry_fee_amount', 0) or 0,
            trade_data.get('exit_fee_amount', 0) or 0
        )
    
    async def get_open_trades(self):
        """Get all open trades from the database"""
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                response = await client.get("http://localhost:8002/api/v1/trades", params={
                    "status": "OPEN",
                    "limit": 1000  # Get all open trades
                })
                
                if response.status_code == 200:
                    data = response.json()
                    trades = data.get('trades', [])
                    logger.info(f"📊 Found {len(trades)} open trades")
                    return trades
                else:
                    logger.error(f"❌ Failed to get trades: {response.status_code}")
                    return []
                    
        except Exception as e:
            logger.error(f"❌ Error getting open trades: {e}")
            return []
    
    async def update_trade_pnl(self, trade_id, unrealized_pnl):
        """Update a single trade's unrealized PnL"""
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                response = await client.put(f"http://localhost:8002/api/v1/trades/{trade_id}", json={
                    "unrealized_pnl": unrealized_pnl
                })
                
                if response.status_code == 200:
                    return True
                else:
                    logger.error(f"❌ Failed to update trade {trade_id}: {response.status_code}")
                    return False
                    
        except Exception as e:
            logger.error(f"❌ Error updating trade {trade_id}: {e}")
            return False
    
    async def fix_pnl_values(self):
        """Fix PnL values for all open trades"""
        logger.info("🚀 Starting Dashboard PnL Fix")
        logger.info("=" * 50)
        
        # Connect to database
        if not await self.connect_database():
            logger.error("❌ Cannot proceed without database connection")
            return False
        
        # Get all open trades
        trades = await self.get_open_trades()
        if not trades:
            logger.warning("⚠️  No open trades found")
            return True
        
        # Process each trade
        fixed_count = 0
        error_count = 0
        
        for trade in trades:
            try:
                trade_id = trade['trade_id']
                entry_price = trade['entry_price']
                current_price = trade['current_price']
                position_size = trade['position_size']
                
                # Skip trades without required data
                if not all([entry_price, current_price, position_size]):
                    logger.warning(f"⚠️  Skipping trade {trade_id}: missing required data")
                    continue
                
                # Create mock position for PnL calculation
                position = self.create_mock_position(trade)
                
                # Calculate new PnL with fees
                new_pnl = calculate_unrealized_pnl(position, current_price)
                
                # Get old PnL for comparison
                old_pnl = trade.get('unrealized_pnl', 0) or 0
                
                # Update the trade
                if await self.update_trade_pnl(trade_id, new_pnl):
                    fixed_count += 1
                    difference = new_pnl - old_pnl
                    logger.info(f"✅ Fixed trade {trade_id[:8]}... - Old: ${old_pnl:.2f}, New: ${new_pnl:.2f}, Diff: ${difference:.2f}")
                else:
                    error_count += 1
                    
            except Exception as e:
                logger.error(f"❌ Error processing trade {trade.get('trade_id', 'unknown')}: {e}")
                error_count += 1
        
        # Summary
        logger.info("=" * 50)
        logger.info(f"📊 FIX SUMMARY:")
        logger.info(f"   Total trades processed: {len(trades)}")
        logger.info(f"   Successfully fixed: {fixed_count}")
        logger.info(f"   Errors: {error_count}")
        
        if fixed_count > 0:
            logger.info(f"✅ Dashboard PnL values updated successfully!")
            logger.info(f"   Fee-inclusive PnL calculations now active")
            logger.info(f"   Dashboard will show accurate profit/loss values")
        else:
            logger.warning(f"⚠️  No trades were updated")
        
        return error_count == 0

async def main():
    """Main function"""
    fixer = DashboardPnLFixer()
    success = await fixer.fix_pnl_values()
    
    if success:
        print("\n🎉 DASHBOARD PnL FIX COMPLETED SUCCESSFULLY!")
        print("   ✅ All open trades updated with fee-inclusive PnL")
        print("   ✅ Dashboard will now show accurate values")
        print("   ✅ Refresh your dashboard to see the changes")
    else:
        print("\n⚠️  DASHBOARD PnL FIX COMPLETED WITH ERRORS")
        print("   Check the logs above for details")

if __name__ == "__main__":
    asyncio.run(main())
