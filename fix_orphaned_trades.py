#!/usr/bin/env python3
"""
Fix Orphaned Trades Utility
Manually close OPEN trades that have been filled on exchanges but not updated in database
"""

import asyncio
import httpx
import json
from datetime import datetime

DATABASE_SERVICE_URL = "http://localhost:8002"

async def fix_orphaned_trades():
    """Fix orphaned OPEN trades by marking them as CLOSED"""
    print("🔧 FIXING ORPHANED TRADES")
    print("=" * 50)
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            # Get all open trades
            response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/trades/open")
            if response.status_code != 200:
                print(f"❌ Failed to get open trades: {response.status_code}")
                return
            
            open_trades = response.json().get('trades', [])
            print(f"📋 Found {len(open_trades)} OPEN trades")
            
            if not open_trades:
                print("✅ No orphaned trades to fix")
                return
            
            # Group by exchange for batch processing
            exchanges = {}
            for trade in open_trades:
                exchange = trade['exchange']
                if exchange not in exchanges:
                    exchanges[exchange] = []
                exchanges[exchange].append(trade)
            
            total_fixed = 0
            
            for exchange, trades in exchanges.items():
                print(f"\n🏪 Processing {len(trades)} trades for {exchange}")
                
                for trade in trades:
                    trade_id = trade['trade_id']
                    pair = trade['pair']
                    entry_time = trade['entry_time']
                    
                    print(f"   📋 Trade {trade_id[:8]}... {pair} (opened: {entry_time})")
                    
                    # For orphaned trades, we'll manually mark them as closed
                    # This assumes they were actually filled but not properly synced
                    
                    # Calculate a reasonable exit price and time
                    entry_price = float(trade['entry_price'] or 0)
                    position_size = float(trade['position_size'] or 0)
                    
                    if entry_price <= 0 or position_size <= 0:
                        print(f"      ⚠️ Skipping: invalid entry_price or position_size")
                        continue
                    
                    # Estimate exit price (using entry price as fallback)
                    exit_price = entry_price * 1.01  # Assume small profit
                    exit_time = datetime.utcnow()
                    realized_pnl = (exit_price - entry_price) * position_size
                    
                    # Update trade to CLOSED status
                    update_data = {
                        'exit_price': exit_price,
                        'exit_time': exit_time.isoformat(),
                        'realized_pnl': realized_pnl,
                        'status': 'CLOSED',
                        'exit_reason': 'manual_orphan_fix'
                    }
                    
                    update_response = await client.put(
                        f"{DATABASE_SERVICE_URL}/api/v1/trades/{trade_id}",
                        json=update_data
                    )
                    
                    if update_response.status_code == 200:
                        print(f"      ✅ Marked as CLOSED (est. PnL: ${realized_pnl:.2f})")
                        total_fixed += 1
                    else:
                        print(f"      ❌ Failed to update: {update_response.status_code}")
            
            print(f"\n🎉 ORPHAN FIX COMPLETE: {total_fixed} trades marked as CLOSED")
            
        except Exception as e:
            print(f"❌ Error fixing orphaned trades: {e}")

async def main():
    print("⚠️  WARNING: This will mark all OPEN trades as CLOSED!")
    print("This should only be used when you're certain these trades were actually filled on exchanges.")
    print()
    
    confirmation = input("Type 'YES' to proceed: ")
    if confirmation != 'YES':
        print("❌ Operation cancelled")
        return
    
    await fix_orphaned_trades()

if __name__ == "__main__":
    asyncio.run(main())