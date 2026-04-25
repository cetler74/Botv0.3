#!/usr/bin/env python3
"""
CRITICAL: Emergency fix for Binance order tracking synchronization
This script reconciles database trades with actual Binance exchange positions
"""

import asyncio
import httpx
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATABASE_SERVICE_URL = "http://localhost:8002"
EXCHANGE_SERVICE_URL = "http://localhost:8003"

# Your Binance exchange data (from manual verification)
BINANCE_SOL_ORDERS = {
    # BUY orders (should create trades)
    '2453575518': {'type': 'buy', 'time': '2025-08-29 01:30:45', 'price': 216.25, 'qty': 0.952, 'status': 'Filled'},
    '2454504031': {'type': 'buy', 'time': '2025-08-29 03:30:50', 'price': 216.39, 'qty': 0.951, 'status': 'Filled'},  
    '2454538252': {'type': 'buy', 'time': '2025-08-29 03:35:03', 'price': 216.39, 'qty': 0.951, 'status': 'Filled'},
    
    # SELL orders (should close trades) - MISSING from our tracking
    'SELL_217.4': {'type': 'sell', 'time': '2025-08-29 05:31:39', 'price': 217.4, 'qty': 0.952, 'status': 'Filled'},
    'SELL_217.32': {'type': 'sell', 'time': '2025-08-29 05:32:10', 'price': 217.32, 'qty': 0.952, 'status': 'Filled', 'order_type': 'Market'},
}

async def fix_binance_sol_sync():
    """Fix the Binance SOL/USDC order tracking synchronization"""
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            # Step 1: Get current SOL/USDC trades on Binance
            logger.info("📊 Analyzing current SOL/USDC trades on Binance...")
            
            response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/trades?pair=SOL/USDC&exchange=binance&limit=20")
            response.raise_for_status()
            trades_data = response.json()
            trades = trades_data.get('trades', [])
            
            logger.info(f"🔍 Found {len(trades)} SOL/USDC trades in database")
            
            # Step 2: Identify problematic trades
            open_trades = [t for t in trades if t.get('status') == 'OPEN']
            logger.info(f"⚠️  Found {len(open_trades)} OPEN trades (should be 1)")
            
            # Step 3: Check which trades should be closed based on exchange data
            trades_to_fix = []
            
            for trade in open_trades:
                entry_id = str(trade.get('entry_id', ''))
                trade_id = trade.get('trade_id')
                entry_price = trade.get('entry_price', 0)
                
                if entry_id in BINANCE_SOL_ORDERS:
                    buy_order = BINANCE_SOL_ORDERS[entry_id]
                    logger.info(f"✅ Trade {trade_id[:8]}... matches Binance buy order {entry_id}")
                    
                    # Based on your manual verification, 2 of 3 should be closed
                    # We'll close the first 2 chronologically
                    if entry_id in ['2453575518', '2454504031']:  # First 2 buy orders
                        trades_to_fix.append({
                            'trade_id': trade_id,
                            'entry_id': entry_id,
                            'should_be': 'CLOSED',
                            'exit_price': 217.36,  # Average of 217.4 and 217.32
                            'exit_time': '2025-08-29T05:31:50.000Z'  # Average exit time
                        })
                        logger.info(f"🔧 Trade {trade_id[:8]}... should be CLOSED (has corresponding sell)")
                    else:
                        logger.info(f"✅ Trade {trade_id[:8]}... should remain OPEN (no sell match)")
                else:
                    logger.warning(f"❌ Trade {trade_id[:8]}... has unknown entry_id: {entry_id}")
            
            # Step 4: Fix the trades
            logger.info(f"\\n🔧 FIXING {len(trades_to_fix)} TRADES...")
            
            for fix in trades_to_fix:
                trade_id = fix['trade_id']
                logger.info(f"🔄 Closing trade {trade_id[:8]}...")
                
                # Calculate realized PnL (simplified)
                original_trade = next((t for t in open_trades if t.get('trade_id') == trade_id), None)
                if original_trade:
                    entry_price = float(original_trade.get('entry_price', 0))
                    exit_price = fix['exit_price']
                    position_size = float(original_trade.get('position_size', 0))
                    realized_pnl = (exit_price - entry_price) * position_size
                    
                    update_data = {
                        'status': 'CLOSED',
                        'exit_price': fix['exit_price'],
                        'exit_time': fix['exit_time'],
                        'realized_pnl': round(realized_pnl, 4),
                        'exit_reason': 'manual_sync_correction_missing_rest_api_sell',
                        'exit_id': f"SYNC_FIX_{fix['entry_id']}"
                    }
                    
                    logger.info(f"   Entry: ${entry_price}, Exit: ${exit_price}, PnL: ${realized_pnl:.4f}")
                    
                    # Update the trade
                    update_response = await client.put(
                        f"{DATABASE_SERVICE_URL}/api/v1/trades/{trade_id}",
                        json=update_data
                    )
                    
                    if update_response.status_code == 200:
                        logger.info(f"✅ Successfully closed trade {trade_id[:8]}...")
                    else:
                        logger.error(f"❌ Failed to close trade {trade_id[:8]}: {update_response.text}")
            
            # Step 5: Verify the fix
            logger.info(f"\\n📊 VERIFICATION: Checking final state...")
            response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/trades?pair=SOL/USDC&exchange=binance&status=OPEN&limit=10")
            response.raise_for_status()
            final_trades = response.json().get('trades', [])
            
            logger.info(f"✅ FINAL RESULT: {len(final_trades)} OPEN SOL/USDC trades on Binance")
            logger.info(f"🎯 EXPECTED: 1 OPEN trade (matches exchange reality)")
            
            if len(final_trades) == 1:
                remaining_trade = final_trades[0]
                logger.info(f"✅ SUCCESS: Remaining trade {remaining_trade.get('trade_id', '')[:8]}... with entry_id {remaining_trade.get('entry_id')}")
            elif len(final_trades) == 0:
                logger.warning(f"⚠️  All trades closed - check if this is correct")
            else:
                logger.error(f"❌ Still have {len(final_trades)} OPEN trades - manual intervention needed")
            
        except Exception as e:
            logger.error(f"❌ Critical error in sync fix: {e}")
            raise

if __name__ == "__main__":
    asyncio.run(fix_binance_sol_sync())