#!/usr/bin/env python3
"""
SIMPLE: Close All Phantom Trades
================================

Since all 6 OPEN trades have 0 balance on exchange, close them all.
"""

import asyncio
import asyncpg
from datetime import datetime
import logging

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class SimplePhantomTradeCloser:
    def __init__(self):
        self.db_config = {
            'host': 'localhost',
            'port': 5432,
            'user': 'carloslarramba',
            'password': 'carloslarramba',
            'database': 'trading_bot_futures'
        }
        
    async def get_connection(self):
        """Get database connection"""
        return await asyncpg.connect(**self.db_config)
    
    async def close_all_open_trades(self):
        """Close all OPEN trades since they have no exchange balance"""
        conn = await self.get_connection()
        try:
            # Get all OPEN trades
            query = """
            SELECT trade_id, pair, entry_price, current_price, position_size
            FROM trading.trades 
            WHERE status = 'OPEN'
            ORDER BY entry_time DESC
            """
            trades = await conn.fetch(query)
            
            logger.info(f"🚨 Found {len(trades)} OPEN trades to close")
            logger.info("=" * 60)
            
            closed_count = 0
            for trade in trades:
                trade_id = trade['trade_id']
                pair = trade['pair']
                entry_price = trade['entry_price']
                current_price = trade['current_price']
                position_size = trade['position_size']
                
                # Calculate realized PnL using current price as exit price
                realized_pnl = (current_price - entry_price) * position_size
                exit_time = datetime.utcnow().isoformat()
                
                # Close the trade
                close_query = """
                UPDATE trading.trades 
                SET status = 'CLOSED',
                    exit_price = $1,
                    exit_time = $2,
                    realized_pnl = $3,
                    updated_at = CURRENT_TIMESTAMP
                WHERE trade_id = $4
                """
                
                await conn.execute(close_query, current_price, exit_time, realized_pnl, trade_id)
                
                logger.info(f"✅ CLOSED {pair} ({trade_id[:8]}...) - Exit: ${current_price:.4f}, PnL: ${realized_pnl:.2f}")
                closed_count += 1
            
            logger.info(f"\n🏁 CLOSED {closed_count}/{len(trades)} phantom trades")
            return closed_count, len(trades)
            
        except Exception as e:
            logger.error(f"❌ Failed to close trades: {e}")
            raise
        finally:
            await conn.close()

async def main():
    """Main execution"""
    closer = SimplePhantomTradeCloser()
    
    try:
        closed, total = await closer.close_all_open_trades()
        
        if closed == total:
            print(f"\n🎉 SUCCESS: All {total} phantom trades closed!")
        else:
            print(f"\n⚠️  PARTIAL: {closed}/{total} phantom trades closed")
            
    except Exception as e:
        logger.error(f"❌ Close failed: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(main())
