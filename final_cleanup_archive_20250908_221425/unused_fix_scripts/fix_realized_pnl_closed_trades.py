"""
SPOT Trading PnL Calculator - Long positions only
"""
from strategy.strategy_pnl_enhanced import calculate_unrealized_pnl_with_fees, calculate_realized_pnl_with_fees

#!/usr/bin/env python3
"""
Fix realized_pnl values for closed trades
Calculate: (exit_price - entry_price) * position_size
"""

import asyncio
import asyncpg
import os
from datetime import datetime
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class PnLFixer:
    def __init__(self):
        self.db_url = os.getenv("DATABASE_URL", "postgresql://carloslarramba:mypassword@localhost:5432/trading_bot_futures")
        
    async def connect(self):
        """Connect to database"""
        try:
            self.conn = await asyncpg.connect(self.db_url)
            logger.info("✅ Connected to database")
        except Exception as e:
            logger.error(f"❌ Failed to connect to database: {e}")
            raise
            
    async def close(self):
        """Close database connection"""
        if hasattr(self, 'conn'):
            await self.conn.close()
            logger.info("🔌 Database connection closed")
            
    async def get_closed_trades_with_zero_pnl(self):
        """Get all closed trades with realized_pnl = 0"""
        query = """
        SELECT 
            id, entry_price, exit_price, position_size, realized_pnl,
            entry_time, exit_time, pair, exchange
        FROM trading.trades 
        WHERE status = 'CLOSED' 
        AND realized_pnl = 0
        AND exit_price IS NOT NULL 
        AND entry_price IS NOT NULL 
        AND position_size IS NOT NULL
        ORDER BY exit_time DESC
        """
        
        try:
            trades = await self.conn.fetch(query)
            logger.info(f"📊 Found {len(trades)} closed trades with realized_pnl = 0")
            return trades
        except Exception as e:
            logger.error(f"❌ Error fetching trades: {e}")
            return []
            
    async def calculate_and_update_pnl(self, trade):
        """Calculate and update realized_pnl for a trade"""
        try:
            # Calculate realized PnL
            entry_price = float(trade['entry_price'])
            exit_price = float(trade['exit_price'])
            position_size = float(trade['position_size'])
            
            realized_pnl = calculate_realized_pnl_with_fees(entry_price, exit_price, position_size)
            
            # Update the trade
            update_query = """
            UPDATE trading.trades 
            SET realized_pnl = $1, updated_at = $2
            WHERE id = $3
            """
            
            await self.conn.execute(update_query, realized_pnl, datetime.utcnow(), trade['id'])
            
            logger.info(f"✅ Updated trade {trade['id']} ({trade['pair']}): "
                       f"Entry: ${entry_price:.6f}, Exit: ${exit_price:.6f}, "
                       f"Size: {position_size:.3f}, PnL: ${realized_pnl:.2f}")
            
            return realized_pnl
            
        except Exception as e:
            logger.error(f"❌ Error updating trade {trade['id']}: {e}")
            return None
            
    async def fix_all_closed_trades(self):
        """Fix realized_pnl for all closed trades"""
        try:
            # Get closed trades with zero/null PnL
            trades = await self.get_closed_trades_with_zero_pnl()
            
            if not trades:
                logger.info("✅ No trades need fixing - all closed trades have proper realized_pnl values")
                return
                
            # Calculate and update PnL for each trade
            total_pnl = 0
            updated_count = 0
            
            for trade in trades:
                pnl = await self.calculate_and_update_pnl(trade)
                if pnl is not None:
                    total_pnl += pnl
                    updated_count += 1
                    
            logger.info(f"🎉 Fixed {updated_count} trades")
            logger.info(f"💰 Total realized PnL: ${total_pnl:.2f}")
            
            # Verify the fix
            await self.verify_fix()
            
        except Exception as e:
            logger.error(f"❌ Error fixing trades: {e}")
            
    async def verify_fix(self):
        """Verify that the fix worked"""
        try:
            # Check total realized PnL
            query = """
            SELECT 
                COUNT(*) as total_closed_trades,
                COALESCE(SUM(realized_pnl), 0) as total_realized_pnl,
                COALESCE(SUM(CASE 
                    WHEN exit_time >= CURRENT_DATE 
                    THEN realized_pnl 
                    ELSE 0 
                END), 0) as daily_realized_pnl
            FROM trading.trades 
            WHERE status = 'CLOSED' AND realized_pnl IS NOT NULL
            """
            
            result = await self.conn.fetchrow(query)
            
            logger.info("📊 Verification Results:")
            logger.info(f"   Total Closed Trades: {result['total_closed_trades']}")
            logger.info(f"   Total Realized PnL: ${result['total_realized_pnl']:.2f}")
            logger.info(f"   Daily Realized PnL: ${result['daily_realized_pnl']:.2f}")
            
        except Exception as e:
            logger.error(f"❌ Error verifying fix: {e}")

async def main():
    """Main function"""
    fixer = PnLFixer()
    
    try:
        await fixer.connect()
        await fixer.fix_all_closed_trades()
        
    except Exception as e:
        logger.error(f"❌ Main error: {e}")
        
    finally:
        await fixer.close()

if __name__ == "__main__":
    asyncio.run(main())
