#!/usr/bin/env python3
"""
Compare database trade entry/exit IDs with provided order history
"""

import asyncio
import httpx
import json
from datetime import datetime

async def compare_trade_order_ids():
    """Compare database trade IDs with order history"""
    
    async with httpx.AsyncClient() as client:
        try:
            print("🔍 COMPARING TRADE ORDER IDS")
            print("=" * 60)
            
            # Order history provided by user
            order_history = [
                {"time": "2025-08-07 21:36:36", "order_id": "6530...5421", "symbol": "A2Z/USD", "side": "Sell", "quantity": 12700, "status": "Canceled"},
                {"time": "2025-08-07 21:34:36", "order_id": "6530...7262", "symbol": "A2Z/USD", "side": "Buy", "quantity": 12800, "status": "Filled"},
                {"time": "2025-08-07 21:33:57", "order_id": "6530...5452", "symbol": "A2Z/USD", "side": "Sell", "quantity": 14500, "status": "Filled"},
                {"time": "2025-08-07 21:33:47", "order_id": "6530...8403", "symbol": "A2Z/USD", "side": "Sell", "quantity": 14000, "status": "Partially Filled"},
                {"time": "2025-08-07 21:33:16", "order_id": "6530...3421", "symbol": "A2Z/USD", "side": "Buy", "quantity": 12700, "status": "Filled"},
                {"time": "2025-08-07 21:31:27", "order_id": "6530...5937", "symbol": "A2Z/USD", "side": "Sell", "quantity": 13200, "status": "Filled"}
            ]
            
            # Extract order IDs from history
            history_order_ids = set()
            for order in order_history:
                order_id = order["order_id"].replace("...", "")  # Remove ellipsis
                history_order_ids.add(order_id)
            
            print(f"📊 Order history IDs: {len(history_order_ids)}")
            for order_id in history_order_ids:
                print(f"   {order_id}")
            
            # Get A2Z trades from database
            trades_response = await client.get("http://localhost:8002/api/v1/trades?status=OPEN&exchange=cryptocom")
            if trades_response.status_code != 200:
                print(f"❌ Failed to get trades: {trades_response.status_code}")
                return
            
            trades_data = trades_response.json()
            a2z_trades = []
            
            for trade in trades_data.get('trades', []):
                if trade['pair'] == 'A2Z/USD':
                    a2z_trades.append(trade)
            
            print(f"\n📋 A2Z trades in database: {len(a2z_trades)}")
            
            # Check each trade's entry/exit IDs against order history
            matched_orders = 0
            unmatched_orders = 0
            
            for trade in a2z_trades:
                entry_id = trade.get('entry_id')
                exit_id = trade.get('exit_id')
                
                entry_matched = False
                exit_matched = False
                
                if entry_id:
                    # Check if entry_id matches any order history ID
                    for history_id in history_order_ids:
                        if str(entry_id).endswith(history_id) or history_id.endswith(str(entry_id)):
                            entry_matched = True
                            break
                
                if exit_id:
                    # Check if exit_id matches any order history ID
                    for history_id in history_order_ids:
                        if str(exit_id).endswith(history_id) or history_id.endswith(str(exit_id)):
                            exit_matched = True
                            break
                
                print(f"\n   Trade ID: {trade['trade_id'][:8]}...")
                print(f"   Entry ID: {entry_id}")
                print(f"   Exit ID: {exit_id}")
                print(f"   Status: {trade['status']}")
                print(f"   Entry matched: {'✅' if entry_matched else '❌'}")
                print(f"   Exit matched: {'✅' if exit_matched else '❌'}")
                
                if entry_matched and (not exit_id or exit_matched):
                    matched_orders += 1
                else:
                    unmatched_orders += 1
            
            print(f"\n📊 SUMMARY:")
            print(f"   ✅ Matched orders: {matched_orders}")
            print(f"   ❌ Unmatched orders: {unmatched_orders}")
            
            # Check for missing orders in database
            print(f"\n🔍 MISSING ORDERS IN DATABASE:")
            missing_count = 0
            for order in order_history:
                order_id = order["order_id"].replace("...", "")
                found_in_db = False
                
                for trade in a2z_trades:
                    entry_id = trade.get('entry_id')
                    exit_id = trade.get('exit_id')
                    
                    if (entry_id and str(entry_id).endswith(order_id)) or (exit_id and str(exit_id).endswith(order_id)):
                        found_in_db = True
                        break
                
                if not found_in_db:
                    missing_count += 1
                    print(f"   ❌ Missing: {order_id} ({order['side']} {order['quantity']} {order['symbol']} - {order['status']})")
            
            if missing_count == 0:
                print("   ✅ All order history entries found in database")
            else:
                print(f"   ⚠️  {missing_count} orders from history not found in database")
            
            # Check for orphaned database entries
            print(f"\n🔍 ORPHANED DATABASE ENTRIES:")
            orphaned_count = 0
            for trade in a2z_trades:
                entry_id = trade.get('entry_id')
                exit_id = trade.get('exit_id')
                
                entry_found = False
                exit_found = False
                
                if entry_id:
                    for order in order_history:
                        order_id = order["order_id"].replace("...", "")
                        if str(entry_id).endswith(order_id) or order_id.endswith(str(entry_id)):
                            entry_found = True
                            break
                
                if exit_id:
                    for order in order_history:
                        order_id = order["order_id"].replace("...", "")
                        if str(exit_id).endswith(order_id) or order_id.endswith(str(exit_id)):
                            exit_found = True
                            break
                
                if not entry_found or (exit_id and not exit_found):
                    orphaned_count += 1
                    print(f"   ❌ Orphaned: Trade {trade['trade_id'][:8]}...")
                    print(f"      Entry ID: {entry_id} ({'✅' if entry_found else '❌'})")
                    print(f"      Exit ID: {exit_id} ({'✅' if exit_found else '❌'})")
            
            if orphaned_count == 0:
                print("   ✅ No orphaned database entries")
            else:
                print(f"   ⚠️  {orphaned_count} orphaned database entries")
            
        except Exception as e:
            print(f"❌ Error: {e}")
            import traceback
            print(f"Full traceback: {traceback.format_exc()}")

if __name__ == "__main__":
    asyncio.run(compare_trade_order_ids())
