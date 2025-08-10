#!/usr/bin/env python3
"""
Fix ACX position size discrepancy - update database to match actual exchange balance
"""

import asyncio
import httpx
import json

async def fix_acx_position_size():
    """Fix ACX/USD position size to match actual exchange balance"""
    
    async with httpx.AsyncClient() as client:
        try:
            print("🔍 Investigating ACX/USD position size discrepancy...")
            
            # Get the current ACX/USD trade from database
            trades_response = await client.get("http://localhost:8002/api/v1/trades?status=OPEN&exchange=cryptocom")
            if trades_response.status_code != 200:
                print(f"❌ Failed to get trades: {trades_response.status_code}")
                return
            
            trades_data = trades_response.json()
            acx_trade = None
            for trade in trades_data.get('trades', []):
                if trade['pair'] == 'ACX/USD':
                    acx_trade = trade
                    break
            
            if not acx_trade:
                print("❌ No ACX/USD trade found")
                return
                
            print(f"📋 Current ACX/USD trade details:")
            print(f"   Trade ID: {acx_trade['trade_id']}")
            print(f"   Entry ID: {acx_trade['entry_id']}")
            print(f"   Database position size: {acx_trade['position_size']}")
            print(f"   Entry price: ${acx_trade['entry_price']}")
            
            # Get actual ACX balance from exchange
            balance_response = await client.get("http://localhost:8003/api/v1/account/balance/cryptocom")
            if balance_response.status_code != 200:
                print(f"❌ Failed to get balance: {balance_response.status_code}")
                return
                
            balance_data = balance_response.json()
            actual_acx_balance = balance_data.get('ACX', {}).get('free', 0)
            
            print(f"💰 Actual ACX balance on exchange: {actual_acx_balance}")
            print(f"📊 Discrepancy: {acx_trade['position_size']} (DB) - {actual_acx_balance} (Exchange) = {acx_trade['position_size'] - actual_acx_balance}")
            
            if actual_acx_balance == 0:
                print("❌ No ACX balance found on exchange")
                return
                
            # Update the database with the correct position size
            if abs(float(acx_trade['position_size']) - actual_acx_balance) > 0.01:  # Only update if significant difference
                print(f"\n🔧 Updating position size from {acx_trade['position_size']} to {actual_acx_balance}")
                
                update_data = {
                    'position_size': actual_acx_balance
                }
                
                update_response = await client.put(
                    f"http://localhost:8002/api/v1/trades/{acx_trade['trade_id']}",
                    json=update_data
                )
                
                if update_response.status_code == 200:
                    print(f"✅ SUCCESS: Position size updated to {actual_acx_balance} ACX")
                    print(f"📈 Original investment: ${float(acx_trade['entry_price']) * float(acx_trade['position_size']):.6f}")
                    print(f"📈 Adjusted investment: ${float(acx_trade['entry_price']) * actual_acx_balance:.6f}")
                    
                    # The difference represents the discrepancy (likely due to fees or partial fills)
                    cost_difference = float(acx_trade['entry_price']) * (float(acx_trade['position_size']) - actual_acx_balance)
                    print(f"💸 Cost difference (fees/partial fill): ${cost_difference:.6f}")
                    
                else:
                    print(f"❌ FAILED to update position size: {update_response.status_code} - {update_response.text}")
            else:
                print("✅ Position size is already accurate")
                
            print(f"\n🎯 The sell order should now work with {actual_acx_balance} ACX")
            
        except Exception as e:
            print(f"❌ Error: {e}")

if __name__ == "__main__":
    asyncio.run(fix_acx_position_size())