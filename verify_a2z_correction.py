#!/usr/bin/env python3
"""
Verify A2Z position corrections and check current database vs exchange state
"""

import asyncio
import httpx
import json
from datetime import datetime

async def verify_a2z_correction():
    """Verify A2Z position corrections and current state"""
    
    async with httpx.AsyncClient() as client:
        try:
            print("🔍 VERIFYING A2Z POSITION CORRECTIONS")
            print("=" * 60)
            
            # 1. Check current A2Z/USD trades in database
            print("1. DATABASE STATE:")
            trades_response = await client.get("http://localhost:8002/api/v1/trades?status=OPEN&exchange=cryptocom")
            if trades_response.status_code != 200:
                print(f"❌ Failed to get trades: {trades_response.status_code}")
                return
            
            trades_data = trades_response.json()
            a2z_trades = []
            total_db_position = 0
            
            for trade in trades_data.get('trades', []):
                if trade['pair'] == 'A2Z/USD':
                    a2z_trades.append(trade)
                    total_db_position += float(trade['position_size'])
            
            print(f"📋 Found {len(a2z_trades)} A2Z/USD trades in database:")
            for trade in a2z_trades:
                print(f"   Trade ID: {trade['trade_id'][:8]}...")
                print(f"   Position size: {trade['position_size']}")
                print(f"   Entry price: ${trade['entry_price']}")
                print(f"   Status: {trade['status']}")
                print("-" * 30)
            
            print(f"💰 Total database position: {total_db_position:,} A2Z")
            
            # 2. Check actual exchange balance
            print("\n2. EXCHANGE STATE:")
            balance_response = await client.get("http://localhost:8003/api/v1/account/balance/cryptocom")
            if balance_response.status_code != 200:
                print(f"❌ Failed to get balance: {balance_response.status_code}")
                return
                
            balance_data = balance_response.json()
            actual_a2z_balance = balance_data.get('A2Z', {}).get('free', 0)
            
            print(f"💰 Actual A2Z balance on exchange: {actual_a2z_balance:,}")
            
            # 3. Calculate discrepancy
            print("\n3. DISCREPANCY ANALYSIS:")
            discrepancy = total_db_position - actual_a2z_balance
            print(f"📊 Database total: {total_db_position:,} A2Z")
            print(f"📊 Exchange total: {actual_a2z_balance:,} A2Z")
            print(f"⚠️  Discrepancy: {discrepancy:,} A2Z")
            
            if abs(discrepancy) > 0.01:
                print(f"❌ CRITICAL: Still have {abs(discrepancy):,} A2Z discrepancy!")
                
                # 4. Check if we need to fix again
                print("\n4. APPLYING CORRECTION:")
                if actual_a2z_balance > 0:
                    print(f"🔄 Updating all A2Z trades to match exchange balance: {actual_a2z_balance:,}")
                    
                    for trade in a2z_trades:
                        update_data = {
                            'position_size': actual_a2z_balance
                        }
                        
                        update_response = await client.put(
                            f"http://localhost:8002/api/v1/trades/{trade['trade_id']}",
                            json=update_data
                        )
                        
                        if update_response.status_code == 200:
                            print(f"✅ Updated trade {trade['trade_id'][:8]}... to {actual_a2z_balance:,} A2Z")
                        else:
                            print(f"❌ Failed to update trade {trade['trade_id'][:8]}...: {update_response.status_code}")
                else:
                    print("❌ No A2Z balance on exchange - should close all A2Z trades")
                    
                    # Close all A2Z trades since there's no balance
                    for trade in a2z_trades:
                        close_data = {
                            'status': 'CLOSED',
                            'exit_price': 0.0,
                            'exit_reason': 'No balance on exchange - forced close',
                            'realized_pnl': 0.0
                        }
                        
                        close_response = await client.put(
                            f"http://localhost:8002/api/v1/trades/{trade['trade_id']}",
                            json=close_data
                        )
                        
                        if close_response.status_code == 200:
                            print(f"✅ Closed trade {trade['trade_id'][:8]}... (no balance)")
                        else:
                            print(f"❌ Failed to close trade {trade['trade_id'][:8]}...: {close_response.status_code}")
            else:
                print("✅ Position sizes are now accurate!")
            
            # 5. Final verification
            print("\n5. FINAL VERIFICATION:")
            final_trades_response = await client.get("http://localhost:8002/api/v1/trades?status=OPEN&exchange=cryptocom")
            if final_trades_response.status_code == 200:
                final_trades_data = final_trades_response.json()
                final_a2z_trades = [t for t in final_trades_data.get('trades', []) if t['pair'] == 'A2Z/USD']
                final_total = sum(float(t['position_size']) for t in final_a2z_trades)
                
                print(f"📊 Final database total: {final_total:,} A2Z")
                print(f"📊 Exchange balance: {actual_a2z_balance:,} A2Z")
                
                if abs(final_total - actual_a2z_balance) <= 0.01:
                    print("✅ SUCCESS: Database now matches exchange!")
                else:
                    print("❌ Still have discrepancy - manual intervention needed")
            
        except Exception as e:
            print(f"❌ Error: {e}")
            import traceback
            print(f"Full traceback: {traceback.format_exc()}")

if __name__ == "__main__":
    asyncio.run(verify_a2z_correction())
