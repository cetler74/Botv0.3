#!/usr/bin/env python3

import asyncio
import aiohttp
import json

async def fix_all_trade_fees():
    """Fix fee calculations for all existing trades by triggering updates"""
    
    base_url = "http://localhost:8002/api/v1"
    
    async with aiohttp.ClientSession() as session:
        print("🔧 Fixing fee calculations for all existing trades\n")
        
        # Get all trades in batches
        page = 1
        limit = 100
        total_fixed = 0
        total_checked = 0
        
        while True:
            try:
                async with session.get(f"{base_url}/trades?page={page}&limit={limit}") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        trades = data["trades"]
                        total = data.get("total", 0)
                        
                        if not trades:
                            break
                            
                        print(f"📄 Processing page {page} ({len(trades)} trades)...")
                        
                        for trade in trades:
                            total_checked += 1
                            trade_id = trade["trade_id"]
                            status = trade.get("status", "UNKNOWN")
                            fees = float(trade.get("fees", 0))
                            entry_fee = float(trade.get("entry_fee_amount", 0))
                            exit_fee = float(trade.get("exit_fee_amount", 0))
                            
                            # Calculate expected fees
                            if status == "OPEN":
                                expected_fees = entry_fee
                            else:
                                expected_fees = entry_fee + exit_fee
                            
                            # Check if fees need fixing
                            if abs(fees - expected_fees) > 0.0001:
                                print(f"   🔄 Fixing {trade_id[:8]}... (${fees} -> ${expected_fees})")
                                
                                # Trigger update to recalculate fees
                                update_data = {"current_price": trade.get("current_price", trade.get("entry_price", 0))}
                                
                                try:
                                    async with session.put(f"{base_url}/trades/{trade_id}", json=update_data) as update_resp:
                                        if update_resp.status == 200:
                                            total_fixed += 1
                                        else:
                                            error_text = await update_resp.text()
                                            print(f"      ❌ Failed to update: {error_text}")
                                except Exception as e:
                                    print(f"      ❌ Error updating trade: {e}")
                        
                        page += 1
                        
                        # Stop if we've processed all trades
                        if len(trades) < limit:
                            break
                            
                    else:
                        print(f"❌ Failed to get trades page {page}: {resp.status}")
                        break
            except Exception as e:
                print(f"❌ Error getting trades page {page}: {e}")
                break
        
        print(f"\n✅ Migration completed!")
        print(f"   Total trades checked: {total_checked}")
        print(f"   Trades with fees fixed: {total_fixed}")
        
        if total_fixed > 0:
            print(f"\n🔍 Verifying fixes...")
            # Run a quick verification
            async with session.get(f"{base_url}/trades?limit=50") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    trades = data["trades"]
                    
                    incorrect_count = 0
                    for trade in trades:
                        status = trade.get("status", "UNKNOWN")
                        fees = float(trade.get("fees", 0))
                        entry_fee = float(trade.get("entry_fee_amount", 0))
                        exit_fee = float(trade.get("exit_fee_amount", 0))
                        
                        if status == "OPEN":
                            expected_fees = entry_fee
                        else:
                            expected_fees = entry_fee + exit_fee
                        
                        if abs(fees - expected_fees) > 0.0001:
                            incorrect_count += 1
                    
                    if incorrect_count == 0:
                        print("   ✅ All checked trades now have correct fees!")
                    else:
                        print(f"   ⚠️  Still found {incorrect_count} trades with incorrect fees")

if __name__ == "__main__":
    asyncio.run(fix_all_trade_fees())