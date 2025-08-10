#!/usr/bin/env python3
"""
Fix Crypto.com order sync by fetching recent orders and updating database
"""

import asyncio
import httpx
import json
from datetime import datetime, timedelta

async def fix_cryptocom_orders():
    """Get recent Crypto.com orders and update database"""
    
    # First, get recent orders from Crypto.com exchange via exchange service
    async with httpx.AsyncClient() as client:
        try:
            # Get balance and recent order data from exchange service
            print("Fetching recent Crypto.com orders...")
            
            # We need to trigger the exchange service to do a comprehensive sync
            # Let's get the balance first to trigger exchange connection
            balance_response = await client.get("http://localhost:8003/api/v1/account/balance/cryptocom")
            if balance_response.status_code == 200:
                print("✅ Exchange service is accessible")
            else:
                print(f"❌ Exchange service error: {balance_response.status_code}")
                return
            
            # Now let's check if there's a way to force fetch orders
            # Since the comprehensive sync isn't working, let's manually close the trades
            # based on the order IDs you provided
            
            filled_orders = [
                {"order_id": "6530219581794196852", "pair": "AAVE/USD", "type": "sell", "status": "filled"},
                # Add the other order IDs from your logs
            ]
            
            print("Getting current open Crypto.com trades...")
            trades_response = await client.get("http://localhost:8002/api/v1/trades?status=OPEN&exchange=cryptocom")
            if trades_response.status_code == 200:
                trades_data = trades_response.json()
                open_trades = trades_data.get('trades', [])
                print(f"Found {len(open_trades)} open Crypto.com trades")
                
                # Let's check each trade to see if it should be closed
                for trade in open_trades:
                    trade_id = trade['trade_id']
                    pair = trade['pair']
                    entry_id = trade['entry_id']
                    
                    print(f"\nTrade {trade_id}: {pair}")
                    print(f"  Entry ID: {entry_id}")
                    print(f"  Status: {trade['status']}")
                    
                    # The issue is that sell orders are filled but not recorded
                    # We need to manually set exit_id and close these trades
                    
                    # For AAVE/USD trade with entry_id 6530219581771941056
                    if entry_id == "6530219581771941056" and pair == "AAVE/USD":
                        # This should have exit_id 6530219581794196852 (from your logs)
                        update_data = {
                            'status': 'CLOSED',
                            'exit_id': '6530219581794196852',
                            'exit_time': datetime.utcnow().isoformat(),
                            'exit_price': 291.978,  # From your logs
                            'realized_pnl': 0.173 * (291.978 - 288.55)  # position_size * (exit_price - entry_price)
                        }
                        
                        print(f"  🔄 Closing AAVE/USD trade with exit order 6530219581794196852")
                        
                        update_response = await client.put(
                            f"http://localhost:8002/api/v1/trades/{trade_id}",
                            json=update_data
                        )
                        
                        if update_response.status_code == 200:
                            print(f"  ✅ CLOSED: Trade marked as completed")
                        else:
                            print(f"  ❌ FAILED: {update_response.status_code} - {update_response.text}")
                    
                    # For ADA/USD trades, based on your order history
                    elif pair == "ADA/USD":
                        # Map entry_ids to exit_ids from your order history
                        ada_exits = {
                            "6530219581768167803": {"exit_id": "6530219581797307436", "exit_price": 0.82547},
                            "6530219581768058699": {"exit_id": "6530219581797270551", "exit_price": 0.82390}, 
                            "6530219581767942498": {"exit_id": "6530219581797214490", "exit_price": 0.82365},
                            "6530219581767853040": {"exit_id": "6530219581797214490", "exit_price": 0.82365},  # Same exit for multiple entries
                        }
                        
                        if entry_id in ada_exits:
                            exit_info = ada_exits[entry_id]
                            position_size = float(trade['position_size'])
                            entry_price = float(trade['entry_price'])
                            exit_price = exit_info['exit_price']
                            
                            update_data = {
                                'status': 'CLOSED',
                                'exit_id': exit_info['exit_id'],
                                'exit_time': datetime.utcnow().isoformat(),
                                'exit_price': exit_price,
                                'realized_pnl': position_size * (exit_price - entry_price)
                            }
                            
                            print(f"  🔄 Closing ADA/USD trade with exit order {exit_info['exit_id']}")
                            
                            update_response = await client.put(
                                f"http://localhost:8002/api/v1/trades/{trade_id}",
                                json=update_data
                            )
                            
                            if update_response.status_code == 200:
                                print(f"  ✅ CLOSED: Trade marked as completed")
                            else:
                                print(f"  ❌ FAILED: {update_response.status_code} - {update_response.text}")
                        else:
                            print(f"  ⚠️  No exit order mapping found for entry_id {entry_id}")
                    
                    else:
                        print(f"  ⚠️  No specific handling for {pair}")
                        
                print(f"\n✅ Finished processing {len(open_trades)} open trades")
                
            else:
                print(f"❌ Failed to get open trades: {trades_response.status_code}")
                
        except Exception as e:
            print(f"❌ Error: {e}")

if __name__ == "__main__":
    asyncio.run(fix_cryptocom_orders())