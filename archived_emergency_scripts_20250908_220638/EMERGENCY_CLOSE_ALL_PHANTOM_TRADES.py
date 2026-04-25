#!/usr/bin/env python3
"""
EMERGENCY: Close All Phantom Trades
===================================

This script identifies and closes all phantom trades where:
1. Trades are OPEN in database
2. No balance exists on exchange (positions were sold)
3. FILLED sell orders exist but aren't linked to trades

CRITICAL: This will close ALL 6 OPEN trades since they have no exchange balance.
"""

import asyncio
import asyncpg
import httpx
import json
from datetime import datetime
import logging

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class EmergencyPhantomTradeCloser:
    def __init__(self):
        self.db_config = {
            'host': 'localhost',
            'port': 5432,
            'user': 'carloslarramba',
            'password': 'carloslarramba',
            'database': 'trading_bot_futures'
        }
        self.exchange_service_url = "http://localhost:8003"
        
    async def get_connection(self):
        """Get database connection"""
        return await asyncpg.connect(**self.db_config)
    
    async def get_exchange_balances(self):
        """Get current exchange balances"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.exchange_service_url}/api/v1/trading/balance/binance")
                if response.status_code == 200:
                    data = response.json()
                    return data.get('balances', {})
                else:
                    logger.error(f"Failed to get balances: {response.status_code}")
                    return {}
        except Exception as e:
            logger.error(f"Error getting balances: {e}")
            return {}
    
    async def get_open_trades(self):
        """Get all OPEN trades from database"""
        conn = await self.get_connection()
        try:
            query = """
            SELECT trade_id, pair, entry_price, current_price, position_size, 
                   entry_time, realized_pnl, unrealized_pnl
            FROM trading.trades 
            WHERE status = 'OPEN'
            ORDER BY entry_time DESC
            """
            rows = await conn.fetch(query)
            return [dict(row) for row in rows]
        finally:
            await conn.close()
    
    async def get_filled_sell_orders(self):
        """Get all FILLED sell orders without trade_id links"""
        conn = await self.get_connection()
        try:
            query = """
            SELECT om.exchange_order_id, om.side, om.status, om.trade_id, om.created_at
            FROM trading.order_mappings om 
            WHERE om.side = 'sell' AND om.status = 'FILLED' AND om.trade_id IS NULL
            ORDER BY om.created_at DESC
            """
            rows = await conn.fetch(query)
            return [dict(row) for row in rows]
        finally:
            await conn.close()
    
    async def close_trade_emergency(self, trade_id, exit_price=None, exit_time=None):
        """Close a trade with emergency parameters"""
        conn = await self.get_connection()
        try:
            # Get trade details
            trade_query = """
            SELECT entry_price, position_size, realized_pnl, unrealized_pnl
            FROM trading.trades 
            WHERE trade_id = $1
            """
            trade = await conn.fetchrow(trade_query, trade_id)
            
            if not trade:
                logger.error(f"Trade {trade_id} not found")
                return False
            
            # Use current time and current price if not provided
            if not exit_time:
                exit_time = datetime.utcnow().isoformat()
            
            if not exit_price:
                # Use current price from trade
                current_price_query = "SELECT current_price FROM trading.trades WHERE trade_id = $1"
                current_price = await conn.fetchval(current_price_query, trade_id)
                exit_price = current_price
            
            # Calculate realized PnL
            entry_price = trade['entry_price']
            position_size = trade['position_size']
            realized_pnl = (exit_price - entry_price) * position_size
            
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
            
            await conn.execute(close_query, exit_price, exit_time, realized_pnl, trade_id)
            logger.info(f"✅ CLOSED trade {trade_id} - Exit: ${exit_price:.4f}, PnL: ${realized_pnl:.2f}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to close trade {trade_id}: {e}")
            return False
        finally:
            await conn.close()
    
    async def run_emergency_close(self):
        """Run the emergency close process"""
        logger.info("🚨 STARTING EMERGENCY PHANTOM TRADE CLOSE")
        logger.info("=" * 60)
        
        # Get current balances
        balances = await self.get_exchange_balances()
        logger.info(f"📊 Exchange balances retrieved: {len(balances)} assets")
        
        # Get OPEN trades
        open_trades = await self.get_open_trades()
        logger.info(f"📋 Found {len(open_trades)} OPEN trades")
        
        # Get orphaned FILLED sell orders
        filled_orders = await self.get_filled_sell_orders()
        logger.info(f"🔍 Found {len(filled_orders)} orphaned FILLED sell orders")
        
        # Check each trade
        trades_to_close = []
        for trade in open_trades:
            pair = trade['pair']
            symbol = pair.split('/')[0]  # Get base symbol (XRP, DOGE, etc.)
            position_size = trade['position_size']
            
            # Check if we have balance for this symbol
            balance = balances.get(symbol, 0)
            
            if balance < position_size * 0.1:  # Less than 10% of expected position
                trades_to_close.append(trade)
                logger.warning(f"❌ {pair}: Expected {position_size:.3f}, Balance: {balance:.3f} - WILL CLOSE")
            else:
                logger.info(f"✅ {pair}: Expected {position_size:.3f}, Balance: {balance:.3f} - KEEP OPEN")
        
        logger.info(f"\n🎯 CLOSING {len(trades_to_close)} PHANTOM TRADES")
        logger.info("=" * 60)
        
        # Close each phantom trade
        closed_count = 0
        for trade in trades_to_close:
            trade_id = trade['trade_id']
            pair = trade['pair']
            
            logger.info(f"🔄 Closing {pair} ({trade_id[:8]}...)")
            
            success = await self.close_trade_emergency(trade_id)
            if success:
                closed_count += 1
                logger.info(f"✅ Successfully closed {pair}")
            else:
                logger.error(f"❌ Failed to close {pair}")
        
        logger.info(f"\n🏁 EMERGENCY CLOSE COMPLETE")
        logger.info(f"📊 Closed {closed_count}/{len(trades_to_close)} phantom trades")
        logger.info(f"🔍 Found {len(filled_orders)} orphaned sell orders")
        
        return closed_count, len(trades_to_close)

async def main():
    """Main execution"""
    closer = EmergencyPhantomTradeCloser()
    
    try:
        closed, total = await closer.run_emergency_close()
        
        if closed == total:
            print(f"\n🎉 SUCCESS: All {total} phantom trades closed!")
        else:
            print(f"\n⚠️  PARTIAL: {closed}/{total} phantom trades closed")
            
    except Exception as e:
        logger.error(f"❌ Emergency close failed: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(main())
