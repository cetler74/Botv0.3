#!/usr/bin/env python3
"""
Fix Trade Status Casing
Convert all lowercase 'closed' statuses to uppercase 'CLOSED' for consistency
"""

import asyncio
import httpx
import sys
from datetime import datetime

DATABASE_SERVICE_URL = "http://localhost:8002"

async def fix_status_casing():
    """Fix all lowercase 'closed' statuses to uppercase 'CLOSED'"""
    print("🔧 FIXING TRADE STATUS CASING")
    print("=" * 50)
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            # Get all trades to check current status distribution
            response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/trades")
            if response.status_code != 200:
                print(f"❌ Failed to get trades: {response.status_code}")
                return
            
            all_trades = response.json().get('trades', [])
            print(f"📋 Total trades: {len(all_trades)}")
            
            # Count status distribution
            status_counts = {}
            trades_to_fix = []
            
            for trade in all_trades:
                status = trade.get('status', 'unknown')
                status_counts[status] = status_counts.get(status, 0) + 1
                
                if status == 'closed':  # lowercase
                    trades_to_fix.append(trade)
            
            print(f"📊 Current status distribution:")
            for status, count in status_counts.items():
                print(f"   {status}: {count}")
            
            if not trades_to_fix:
                print("✅ No trades with lowercase 'closed' status found")
                return
            
            print(f"\n🔧 Found {len(trades_to_fix)} trades with lowercase 'closed' status to fix")
            
            # Fix each trade
            fixed_count = 0
            for trade in trades_to_fix:
                trade_id = trade['trade_id']
                pair = trade.get('pair', 'N/A')
                
                # Update just the status field
                update_data = {
                    'status': 'CLOSED'  # uppercase
                }
                
                update_response = await client.put(
                    f"{DATABASE_SERVICE_URL}/api/v1/trades/{trade_id}",
                    json=update_data
                )
                
                if update_response.status_code == 200:
                    print(f"   ✅ Fixed {trade_id[:8]}... ({pair})")
                    fixed_count += 1
                else:
                    print(f"   ❌ Failed to fix {trade_id[:8]}... ({pair}): {update_response.status_code}")
            
            print(f"\n🎉 STATUS CASING FIX COMPLETE: {fixed_count}/{len(trades_to_fix)} trades updated")
            
        except Exception as e:
            print(f"❌ Error fixing status casing: {e}")

async def main():
    print("🔄 This will update all lowercase 'closed' statuses to uppercase 'CLOSED'")
    print("This ensures consistency with the database schema and queries.")
    print()
    
    await fix_status_casing()

if __name__ == "__main__":
    asyncio.run(main())