#!/usr/bin/env python3
"""
Check realized_pnl values in the database
"""

import asyncio
import asyncpg
import os
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class PnLChecker:
    def __init__(self):
        self.db_url = os.getenv("DATABASE_URL", "postgresql://carloslarramba:mypassword@postgres:5432/trading_bot_futures")
        
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
            
    async def check_closed_trades(self):
        """Check all closed trades and their realized_pnl values"""
        query = """
        SELECT 
            id, status, realized_pnl, entry_price, exit_price, position_size,
            pair, exchange, entry_time, exit_time
        FROM trading.trades 
        WHERE status = 'CLOSED'
        ORDER BY exit_time DESC
        """
        
        try:
            trades = await self.conn.fetch(query)
            logger.info(f"📊 Found {len(trades)} closed trades")
            
            total_realized_pnl = 0
            zero_pnl_count = 0
            
            for trade in trades:
                realized_pnl = float(trade['realized_pnl']) if trade['realized_pnl'] is not None else 0
                total_realized_pnl += realized_pnl
                
                if realized_pnl == 0:
                    zero_pnl_count += 1
                    logger.info(f"Trade {trade['id']} ({trade['pair']}): realized_pnl = {realized_pnl}")
                    
                    # Calculate what it should be
                    entry_price = float(trade['entry_price'])
                    exit_price = float(trade['exit_price'])
                    position_size = float(trade['position_size'])
                    calculated_pnl = (exit_price - entry_price) * position_size
                    
                    logger.info(f"  Entry: ${entry_price:.6f}, Exit: ${exit_price:.6f}, Size: {position_size:.3f}")
                    logger.info(f"  Calculated PnL: ${calculated_pnl:.2f}")
                    
            logger.info(f"💰 Total realized PnL: ${total_realized_pnl:.2f}")
            logger.info(f"🔢 Trades with zero PnL: {zero_pnl_count}")
            
        except Exception as e:
            logger.error(f"❌ Error checking trades: {e}")
            
    async def check_portfolio_summary_query(self):
        """Check what the portfolio summary query returns"""
        query = """
        SELECT 
            COALESCE(SUM(realized_pnl), 0) as total_realized_pnl,
            COALESCE(SUM(CASE 
                WHEN exit_time >= CURRENT_DATE 
                THEN realized_pnl 
                ELSE 0 
            END), 0) as daily_realized_pnl
        FROM trading.trades 
        WHERE status = 'CLOSED' AND realized_pnl IS NOT NULL
        """
        
        try:
            result = await self.conn.fetchrow(query)
            logger.info("📊 Portfolio Summary Query Results:")
            logger.info(f"   Total Realized PnL: ${result['total_realized_pnl']:.2f}")
            logger.info(f"   Daily Realized PnL: ${result['daily_realized_pnl']:.2f}")
            
        except Exception as e:
            logger.error(f"❌ Error checking portfolio summary: {e}")

async def main():
    """Main function"""
    checker = PnLChecker()
    
    try:
        await checker.connect()
        await checker.check_closed_trades()
        await checker.check_portfolio_summary_query()
        
    except Exception as e:
        logger.error(f"❌ Main error: {e}")
        
    finally:
        await checker.close()

if __name__ == "__main__":
    asyncio.run(main())
