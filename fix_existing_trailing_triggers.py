#!/usr/bin/env python3
"""
Fix existing trades with incorrect trailing stop triggers via database service API
"""

import httpx
import asyncio
import json

async def fix_existing_trades():
    """Fix all existing trades with incorrect trailing stop triggers"""
    
    base_url = "http://localhost:8002"  # Database service URL
    
    async with httpx.AsyncClient() as client:
        try:
            # Get all open trades
            response = await client.get(f"{base_url}/api/v1/trades?status=OPEN")
            data = response.json()
            trades = data.get('trades', [])
            
            print(f"Found {len(trades)} open trades")
            
            fixed_count = 0
            
            for trade in trades:
                trade_id = trade['trade_id']
                pair = trade['pair']
                exchange = trade['exchange']
                highest_price = float(trade.get('highest_price', 0))
                current_trigger = trade.get('trail_stop_trigger')
                trail_stop = trade.get('trail_stop', 'inactive')
                
                if trail_stop != 'active' or not current_trigger:
                    continue
                    
                current_trigger = float(current_trigger)
                
                # Check if trigger looks incorrect (percentage value or way above highest price)
                if current_trigger < 2.0 or current_trigger > highest_price * 1.2:
                    # Calculate correct trigger (1% below highest price for long positions)
                    trailing_step_pct = 1.0  # 1% default
                    correct_trigger = highest_price * (1 - trailing_step_pct / 100)
                    
                    print(f"\nTrade {trade_id}: {pair} on {exchange}")
                    print(f"  Highest price: ${highest_price:.6f}")
                    print(f"  Current trigger: {current_trigger:.6f} (INCORRECT)")
                    print(f"  Correct trigger: ${correct_trigger:.6f}")
                    
                    # Update the trade via API
                    update_data = {
                        'trail_stop_trigger': correct_trigger
                    }
                    
                    update_response = await client.put(
                        f"{base_url}/api/v1/trades/{trade_id}",
                        json=update_data
                    )
                    
                    if update_response.status_code == 200:
                        print(f"  ✅ FIXED: Updated trigger to ${correct_trigger:.6f}")
                        fixed_count += 1
                    else:
                        print(f"  ❌ FAILED: {update_response.status_code} - {update_response.text}")
                else:
                    print(f"Trade {trade_id}: Trigger ${current_trigger:.6f} looks correct")
            
            print(f"\n✅ Successfully fixed {fixed_count} trailing stop triggers")
            
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(fix_existing_trades())