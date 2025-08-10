#!/usr/bin/env python3
"""
Fix A2Z duplicate trades - consolidate multiple trades into single trade with correct position size
"""

import asyncio
import httpx
import json
from datetime import datetime

async def fix_a2z_duplicate_trades():
    """Fix A2Z duplicate trades by consolidating into single trade"""
    
    async with httpx.AsyncClient() as client:
        try:
            print("🔍 FIXING A2Z DUPLICATE TRADES")
            print("=" * 60)
            
            # Get current A2Z/USD trades from database
            trades_response = await client.get("http://localhost:8002/api/v1/trades?status=OPEN&exchange=cryptocom")
            if trades_response.status_code != 200:
                print(f"❌ Failed to get trades: {trades_response.status_code}")
                return
            
            trades_data = trades_response.json()
            a2z_trades = []
            
            for trade in trades_data.get('trades', []):
                if trade['pair'] == 'A2Z/USD':
                    a2z_trades.append(trade)
            
            print(f"📋 Found {len(a2z_trades)} A2Z/USD trades in database")
            
            if len(a2z_trades) <= 1:
                print("✅ No duplicate trades to fix")
                return
            
            # Get actual exchange balance
            balance_response = await client.get("http://localhost:8003/api/v1/account/balance/cryptocom")
            if balance_response.status_code != 200:
                print(f"❌ Failed to get balance: {balance_response.status_code}")
                return
                
            balance_data = balance_response.json()
            actual_a2z_balance = balance_data.get('A2Z', {}).get('free', 0)
            
            print(f"💰 Actual A2Z balance on exchange: {actual_a2z_balance:,}")
            
            # Calculate current database total
            total_db_position = sum(float(trade['position_size']) for trade in a2z_trades)
            print(f"📊 Current database total: {total_db_position:,} A2Z")
            print(f"📊 Should be: {actual_a2z_balance:,} A2Z")
            
            if abs(total_db_position - actual_a2z_balance) <= 0.01:
                print("✅ Position sizes are correct, but we have duplicate trades")
            else:
                print("❌ Position sizes are still incorrect")
                return
            
            # Strategy: Keep the most recent trade, close all others
            print(f"\n🔧 CONSOLIDATING {len(a2z_trades)} TRADES INTO 1:")
            
            # Sort by entry time, keep the most recent
            a2z_trades.sort(key=lambda x: x['entry_time'], reverse=True)
            keep_trade = a2z_trades[0]
            close_trades = a2z_trades[1:]
            
            print(f"✅ Keeping trade: {keep_trade['trade_id'][:8]}... (most recent)")
            print(f"❌ Closing {len(close_trades)} duplicate trades")
            
            # Update the kept trade to have the correct position size
            update_data = {
                'position_size': actual_a2z_balance
            }
            
            update_response = await client.put(
                f"http://localhost:8002/api/v1/trades/{keep_trade['trade_id']}",
                json=update_data
            )
            
            if update_response.status_code == 200:
                print(f"✅ Updated kept trade position size to {actual_a2z_balance:,} A2Z")
            else:
                print(f"❌ Failed to update kept trade: {update_response.status_code}")
                return
            
            # Close all duplicate trades
            closed_count = 0
            for trade in close_trades:
                close_data = {
                    'status': 'CLOSED',
                    'exit_price': 0.0,
                    'exit_reason': 'Duplicate trade - consolidated',
                    'realized_pnl': 0.0
                }
                
                close_response = await client.put(
                    f"http://localhost:8002/api/v1/trades/{trade['trade_id']}",
                    json=close_data
                )
                
                if close_response.status_code == 200:
                    closed_count += 1
                    print(f"✅ Closed duplicate trade {trade['trade_id'][:8]}...")
                else:
                    print(f"❌ Failed to close trade {trade['trade_id'][:8]}...: {close_response.status_code}")
            
            print(f"\n🎯 RESULT:")
            print(f"   ✅ Kept 1 trade with {actual_a2z_balance:,} A2Z")
            print(f"   ✅ Closed {closed_count} duplicate trades")
            print(f"   📊 Database now matches exchange reality")
            
            # Final verification
            print(f"\n🔍 FINAL VERIFICATION:")
            final_response = await client.get("http://localhost:8002/api/v1/trades?status=OPEN&exchange=cryptocom")
            if final_response.status_code == 200:
                final_data = final_response.json()
                final_a2z_trades = [t for t in final_data.get('trades', []) if t['pair'] == 'A2Z/USD']
                
                print(f"📋 Remaining A2Z trades: {len(final_a2z_trades)}")
                if len(final_a2z_trades) == 1:
                    final_trade = final_a2z_trades[0]
                    print(f"✅ Single trade with {final_trade['position_size']} A2Z")
                    print(f"✅ Database now correctly reflects exchange reality")
                else:
                    print(f"❌ Still have {len(final_a2z_trades)} A2Z trades - manual intervention needed")
            
        except Exception as e:
            print(f"❌ Error: {e}")
            import traceback
            print(f"Full traceback: {traceback.format_exc()}")

if __name__ == "__main__":
    asyncio.run(fix_a2z_duplicate_trades())
