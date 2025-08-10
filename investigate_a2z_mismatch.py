#!/usr/bin/env python3
"""
Investigate and fix A2Z/USD position size mismatch
Based on order history analysis
"""

import asyncio
import httpx
import json
from datetime import datetime

async def investigate_a2z_mismatch():
    """Investigate A2Z/USD position size mismatch and fix it"""
    
    async with httpx.AsyncClient() as client:
        try:
            print("🔍 Investigating A2Z/USD position size mismatch...")
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
            
            print(f"📋 Found {len(a2z_trades)} A2Z/USD trades in database:")
            for trade in a2z_trades:
                print(f"   Trade ID: {trade['trade_id']}")
                print(f"   Entry ID: {trade['entry_id']}")
                print(f"   Database position size: {trade['position_size']}")
                print(f"   Entry price: ${trade['entry_price']}")
                print(f"   Status: {trade['status']}")
                print("-" * 40)
            
            # Get actual A2Z balance from exchange
            balance_response = await client.get("http://localhost:8003/api/v1/account/balance/cryptocom")
            if balance_response.status_code != 200:
                print(f"❌ Failed to get balance: {balance_response.status_code}")
                return
                
            balance_data = balance_response.json()
            actual_a2z_balance = balance_data.get('A2Z', {}).get('free', 0)
            
            print(f"💰 Actual A2Z balance on exchange: {actual_a2z_balance}")
            print("=" * 60)
            
            # Analyze the order history provided
            print("📊 ORDER HISTORY ANALYSIS:")
            print("Based on the provided order history:")
            
            # Buy orders (incoming A2Z)
            buy_orders = [
                {"time": "2025-08-07 21:34:36", "quantity": 12800, "price": 0.0078109, "status": "Filled"},
                {"time": "2025-08-07 21:33:16", "quantity": 12700, "price": 0.0078644, "status": "Filled"}
            ]
            
            # Sell orders (outgoing A2Z)
            sell_orders = [
                {"time": "2025-08-07 21:33:57", "quantity": 14500, "price": 0.0078676, "status": "Filled"},
                {"time": "2025-08-07 21:33:47", "quantity": 2800, "price": 0.007866, "status": "Partially Filled"},
                {"time": "2025-08-07 21:31:27", "quantity": 13200, "price": 0.0078396, "status": "Filled"}
            ]
            
            # Calculate net A2Z position
            total_bought = sum(order["quantity"] for order in buy_orders)
            total_sold = sum(order["quantity"] for order in sell_orders)
            net_position = total_bought - total_sold
            
            print(f"📈 Total A2Z bought: {total_bought:,}")
            print(f"📉 Total A2Z sold: {total_sold:,}")
            print(f"💰 Net A2Z position: {net_position:,}")
            print(f"💰 Actual exchange balance: {actual_a2z_balance:,}")
            
            # Calculate discrepancy
            if a2z_trades:
                db_position = float(a2z_trades[0]['position_size'])
                discrepancy = db_position - actual_a2z_balance
                print(f"📊 Database shows: {db_position:,}")
                print(f"📊 Exchange has: {actual_a2z_balance:,}")
                print(f"⚠️  Discrepancy: {discrepancy:,}")
                
                # The issue is clear: database thinks we have 13,348.95 A2Z but exchange only has 216.30
                # This suggests the database wasn't properly updated when sell orders were executed
                
                print("\n🔧 FIXING THE DISCREPANCY:")
                print("The database position size should match the actual exchange balance")
                
                if abs(discrepancy) > 0.01:  # Only update if significant difference
                    print(f"🔄 Updating database position size from {db_position:,} to {actual_a2z_balance:,}")
                    
                    # Update the database with the correct position size
                    for trade in a2z_trades:
                        update_data = {
                            'position_size': actual_a2z_balance
                        }
                        
                        update_response = await client.put(
                            f"http://localhost:8002/api/v1/trades/{trade['trade_id']}",
                            json=update_data
                        )
                        
                        if update_response.status_code == 200:
                            print(f"✅ SUCCESS: Updated trade {trade['trade_id'][:8]}... position size to {actual_a2z_balance:,} A2Z")
                        else:
                            print(f"❌ FAILED to update trade {trade['trade_id'][:8]}...: {update_response.status_code}")
                    
                    # Calculate the cost impact
                    if a2z_trades:
                        entry_price = float(a2z_trades[0]['entry_price'])
                        original_cost = entry_price * db_position
                        adjusted_cost = entry_price * actual_a2z_balance
                        cost_difference = original_cost - adjusted_cost
                        
                        print(f"\n💰 COST ANALYSIS:")
                        print(f"   Original investment: ${original_cost:.6f}")
                        print(f"   Adjusted investment: ${adjusted_cost:.6f}")
                        print(f"   Cost difference: ${cost_difference:.6f}")
                        
                        # This difference represents the value of A2Z that was sold but not properly recorded
                        print(f"   💸 This represents the value of A2Z sold but not properly recorded in database")
                else:
                    print("✅ Position size is already accurate")
            else:
                print("❌ No A2Z/USD trades found in database")
                
            print("\n🎯 RESULT:")
            print(f"   Exchange balance: {actual_a2z_balance:,} A2Z")
            print(f"   Database should now show: {actual_a2z_balance:,} A2Z")
            print(f"   ✅ The sell order should now work with the correct position size")
            
        except Exception as e:
            print(f"❌ Error: {e}")
            import traceback
            print(f"Full traceback: {traceback.format_exc()}")

if __name__ == "__main__":
    asyncio.run(investigate_a2z_mismatch())
