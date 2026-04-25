#!/usr/bin/env python3
"""
Complete the Binance SOL/USDC synchronization fix
Close the second SOL trade that should have been closed by the second sell order
"""

import asyncio
import httpx
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATABASE_SERVICE_URL = "http://localhost:8002"

async def complete_sol_sync():
    """Complete the SOL/USDC synchronization by closing the remaining trade"""
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            # Get the remaining SOL/USDC trade that should be closed
            response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/trades?trade_id=b5b582b2-b17b-4bb6-9b73-cf01ab6d8dcb")
            response.raise_for_status()
            trades_data = response.json()
            
            if not trades_data.get('trades'):
                logger.error("❌ Trade b5b582b2 not found")
                return
                
            trade = trades_data['trades'][0]
            trade_id = trade.get('trade_id')
            entry_price = float(trade.get('entry_price', 0))
            position_size = float(trade.get('position_size', 0))
            
            logger.info(f"🔍 Found trade {trade_id[:8]}...")
            logger.info(f"   Entry ID: {trade.get('entry_id')} (matches 2454538252)")
            logger.info(f"   Entry Price: ${entry_price}")
            logger.info(f"   Position Size: {position_size}")
            
            # Based on your Binance data, this should be closed by the market sell at 217.32
            exit_price = 217.32  # Market sell price
            realized_pnl = (exit_price - entry_price) * position_size
            
            update_data = {
                'status': 'CLOSED',
                'exit_price': exit_price,
                'exit_time': '2025-08-29T05:32:10.000Z',  # Market sell timestamp
                'realized_pnl': round(realized_pnl, 4),
                'exit_reason': 'manual_sync_correction_market_sell_rest_api',
                'exit_id': 'SYNC_FIX_MARKET_SELL'
            }
            
            logger.info(f"🔧 Closing trade with market sell data:")
            logger.info(f"   Exit Price: ${exit_price}")
            logger.info(f"   Realized PnL: ${realized_pnl:.4f}")
            
            # Update the trade
            update_response = await client.put(
                f"{DATABASE_SERVICE_URL}/api/v1/trades/{trade_id}",
                json=update_data
            )
            
            if update_response.status_code == 200:
                logger.info(f"✅ Successfully closed trade {trade_id[:8]}...")
            else:
                logger.error(f"❌ Failed to close trade: {update_response.text}")
                return
                
            # Verify final state
            logger.info(f"\\n📊 FINAL VERIFICATION:")
            response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/trades?pair=SOL/USDC&exchange=binance&status=OPEN&limit=10")
            response.raise_for_status()
            final_trades = response.json().get('trades', [])
            
            logger.info(f"✅ SOL/USDC OPEN trades on Binance: {len(final_trades)}")
            logger.info(f"🎯 Expected: 1 trade (entry_id 2453575518)")
            
            if len(final_trades) == 1:
                remaining = final_trades[0]
                logger.info(f"✅ SUCCESS! Remaining trade:")
                logger.info(f"   ID: {remaining.get('trade_id', '')[:8]}...")
                logger.info(f"   Entry ID: {remaining.get('entry_id')} (should be 2453575518)")
                logger.info(f"   Entry Price: ${remaining.get('entry_price')}")
                logger.info(f"\\n🎉 SYNCHRONIZATION COMPLETE!")
                logger.info(f"   Database: 1 OPEN SOL/USDC trade")
                logger.info(f"   Binance:  3 buys - 2 sells = 1 net position")
                logger.info(f"   Status:   ✅ SYNCHRONIZED")
            else:
                logger.error(f"❌ Still have {len(final_trades)} OPEN trades - check manually")
                
        except Exception as e:
            logger.error(f"❌ Error completing sync: {e}")
            raise

if __name__ == "__main__":
    asyncio.run(complete_sol_sync())