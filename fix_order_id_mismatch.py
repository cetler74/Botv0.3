#!/usr/bin/env python3
"""
Fix order ID mismatch between database and exchange
"""

import asyncio
import httpx
import json
from datetime import datetime

async def fix_order_id_mismatch():
    """Fix order ID mismatch by updating database with correct exchange order IDs"""
    
    async with httpx.AsyncClient() as client:
        try:
            print("🔍 FIXING ORDER ID MISMATCH")
            print("=" * 60)
            
            # Order history with correct order IDs
            order_mapping = {
                # Buy orders (entry orders)
                "65307262": {"side": "Buy", "quantity": 12800, "status": "Filled", "time": "2025-08-07 21:34:36"},
                "65303421": {"side": "Buy", "quantity": 12700, "status": "Filled", "time": "2025-08-07 21:33:16"},
                
                # Sell orders (exit orders)
                "65305452": {"side": "Sell", "quantity": 14500, "status": "Filled", "time": "2025-08-07 21:33:57"},
                "65308403": {"side": "Sell", "quantity": 14000, "status": "Partially Filled", "time": "2025-08-07 21:33:47"},
                "65305937": {"side": "Sell", "quantity": 13200, "status": "Filled", "time": "2025-08-07 21:31:27"},
                "65305421": {"side": "Sell", "quantity": 12700, "status": "Canceled", "time": "2025-08-07 21:36:36"}
            }
            
            print(f"📊 Order mapping created: {len(order_mapping)} orders")
            
            # Get current A2Z trades from database
            trades_response = await client.get("http://localhost:8002/api/v1/trades?exchange=cryptocom")
            if trades_response.status_code != 200:
                print(f"❌ Failed to get trades: {trades_response.status_code}")
                return
            
            trades_data = trades_response.json()
            a2z_trades = []
            
            for trade in trades_data.get('trades', []):
                if trade['pair'] == 'A2Z/USD':
                    a2z_trades.append(trade)
            
            print(f"📋 Found {len(a2z_trades)} A2Z trades in database")
            
            # Analyze current state
            print(f"\n📊 CURRENT STATE ANALYSIS:")
            open_trades = [t for t in a2z_trades if t['status'] == 'OPEN']
            closed_trades = [t for t in a2z_trades if t['status'] == 'CLOSED']
            
            print(f"   Open trades: {len(open_trades)}")
            print(f"   Closed trades: {len(closed_trades)}")
            
            # Strategy: 
            # 1. Find the most recent buy order that matches our current balance
            # 2. Update the open trade to use the correct entry order ID
            # 3. Close any trades that don't match the order history
            
            print(f"\n🔧 APPLYING FIXES:")
            
            # Find the most recent buy order that could be our entry
            buy_orders = {k: v for k, v in order_mapping.items() if v['side'] == 'Buy' and v['status'] == 'Filled'}
            sell_orders = {k: v for k, v in order_mapping.items() if v['side'] == 'Sell' and v['status'] == 'Filled'}
            
            print(f"   Buy orders: {len(buy_orders)}")
            print(f"   Sell orders: {len(sell_orders)}")
            
            # Calculate net position from order history
            total_bought = sum(order['quantity'] for order in buy_orders.values())
            total_sold = sum(order['quantity'] for order in sell_orders.values())
            net_position = total_bought - total_sold
            
            print(f"   Total bought: {total_bought:,}")
            print(f"   Total sold: {total_sold:,}")
            print(f"   Net position: {net_position:,}")
            
            # Get actual exchange balance
            balance_response = await client.get("http://localhost:8003/api/v1/account/balance/cryptocom")
            if balance_response.status_code != 200:
                print(f"❌ Failed to get balance: {balance_response.status_code}")
                return
                
            balance_data = balance_response.json()
            actual_a2z_balance = balance_data.get('A2Z', {}).get('free', 0)
            
            print(f"   Actual balance: {actual_a2z_balance:,}")
            
            # Find the most recent buy order that could be our entry
            most_recent_buy = None
            for order_id, order in buy_orders.items():
                if not most_recent_buy or order['time'] > most_recent_buy['time']:
                    most_recent_buy = {'order_id': order_id, **order}
            
            if most_recent_buy:
                print(f"   Most recent buy: {most_recent_buy['order_id']} ({most_recent_buy['quantity']} A2Z)")
                
                # Update the open trade with the correct entry order ID
                if open_trades:
                    open_trade = open_trades[0]  # Should be only one after our previous fix
                    
                    print(f"   Updating open trade {open_trade['trade_id'][:8]}...")
                    print(f"   Current entry_id: {open_trade['entry_id']}")
                    print(f"   New entry_id: {most_recent_buy['order_id']}")
                    
                    update_data = {
                        'entry_id': most_recent_buy['order_id']
                    }
                    
                    update_response = await client.put(
                        f"http://localhost:8002/api/v1/trades/{open_trade['trade_id']}",
                        json=update_data
                    )
                    
                    if update_response.status_code == 200:
                        print(f"   ✅ Updated entry_id to {most_recent_buy['order_id']}")
                    else:
                        print(f"   ❌ Failed to update: {update_response.status_code}")
                else:
                    print(f"   ⚠️  No open trades to update")
            
            # Close any trades that don't match the order history
            print(f"\n🔍 CLOSING ORPHANED TRADES:")
            orphaned_count = 0
            
            for trade in a2z_trades:
                entry_id = trade.get('entry_id')
                exit_id = trade.get('exit_id')
                
                entry_found = False
                exit_found = False
                
                # Check if entry_id matches any order in our mapping
                if entry_id:
                    for order_id in order_mapping.keys():
                        if str(entry_id).endswith(order_id) or order_id.endswith(str(entry_id)):
                            entry_found = True
                            break
                
                # Check if exit_id matches any order in our mapping
                if exit_id:
                    for order_id in order_mapping.keys():
                        if str(exit_id).endswith(order_id) or order_id.endswith(str(exit_id)):
                            exit_found = True
                            break
                
                # If trade doesn't match any order history, close it
                if not entry_found or (exit_id and not exit_found):
                    if trade['status'] == 'OPEN':
                        print(f"   ❌ Closing orphaned trade {trade['trade_id'][:8]}...")
                        print(f"      Entry ID: {entry_id} ({'✅' if entry_found else '❌'})")
                        print(f"      Exit ID: {exit_id} ({'✅' if exit_found else '❌'})")
                        
                        close_data = {
                            'status': 'CLOSED',
                            'exit_price': 0.0,
                            'exit_reason': 'Orphaned trade - no matching exchange order',
                            'realized_pnl': 0.0
                        }
                        
                        close_response = await client.put(
                            f"http://localhost:8002/api/v1/trades/{trade['trade_id']}",
                            json=close_data
                        )
                        
                        if close_response.status_code == 200:
                            orphaned_count += 1
                            print(f"      ✅ Closed")
                        else:
                            print(f"      ❌ Failed: {close_response.status_code}")
            
            print(f"\n🎯 RESULT:")
            print(f"   ✅ Updated entry order ID")
            print(f"   ✅ Closed {orphaned_count} orphaned trades")
            print(f"   📊 Database now matches exchange order history")
            
            # Final verification
            print(f"\n🔍 FINAL VERIFICATION:")
            final_response = await client.get("http://localhost:8002/api/v1/trades?exchange=cryptocom")
            if final_response.status_code == 200:
                final_data = final_response.json()
                final_a2z_trades = [t for t in final_data.get('trades', []) if t['pair'] == 'A2Z/USD']
                
                open_final = [t for t in final_a2z_trades if t['status'] == 'OPEN']
                closed_final = [t for t in final_a2z_trades if t['status'] == 'CLOSED']
                
                print(f"   Open A2Z trades: {len(open_final)}")
                print(f"   Closed A2Z trades: {len(closed_final)}")
                
                if open_final:
                    trade = open_final[0]
                    print(f"   Open trade entry_id: {trade['entry_id']}")
                    print(f"   Position size: {trade['position_size']}")
            
        except Exception as e:
            print(f"❌ Error: {e}")
            import traceback
            print(f"Full traceback: {traceback.format_exc()}")

if __name__ == "__main__":
    asyncio.run(fix_order_id_mismatch())
