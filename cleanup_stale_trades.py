#!/usr/bin/env python3
"""
Cleanup stale/phantom trades in database that don't correspond to actual exchange positions
"""
import asyncio
import httpx
import json
from datetime import datetime

async def cleanup_stale_trades():
    """Close phantom trades that don't exist on the exchange"""
    
    print("🔍 Starting cleanup of stale trades...")
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        # Get all open trades from database
        db_response = await client.get("http://localhost:8002/api/v1/trades?exchange=cryptocom&status=OPEN")
        if db_response.status_code != 200:
            print(f"❌ Failed to get database trades: {db_response.status_code}")
            return
            
        db_trades = db_response.json().get("trades", [])
        print(f"📊 Found {len(db_trades)} open trades in database")
        
        # Get actual balance from exchange
        balance_response = await client.get("http://localhost:8003/api/v1/account/balance/cryptocom")
        if balance_response.status_code != 200:
            print(f"❌ Failed to get exchange balance: {balance_response.status_code}")
            return
            
        exchange_data = balance_response.json()
        exchange_positions = exchange_data.get("free", {})
        print(f"💰 Exchange has {len(exchange_positions)} different coins")
        
        # Find trades to close
        trades_to_close = []
        coins_with_balance = set()
        
        # Identify coins that have meaningful balances (> dust amounts)
        for coin, balance in exchange_positions.items():
            if balance > 0.001:  # Above dust threshold
                coins_with_balance.add(coin)
        
        print(f"🪙 Coins with meaningful balance: {', '.join(sorted(coins_with_balance))}")
        
        # Check each database trade against exchange reality
        for trade in db_trades:
            pair = trade.get("pair", "")
            trade_id = trade.get("trade_id")
            entry_time = trade.get("entry_time", "")
            
            if not pair:
                continue
                
            # Extract base currency from pair (e.g., "ACX/USD" -> "ACX")
            if "/" in pair:
                base_currency = pair.split("/")[0]
                
                # If we don't have this coin in meaningful amounts, the trade should be closed
                if base_currency not in coins_with_balance:
                    trades_to_close.append({
                        "trade_id": trade_id,
                        "pair": pair,
                        "entry_time": entry_time,
                        "reason": f"No {base_currency} balance on exchange"
                    })
        
        print(f"🎯 Found {len(trades_to_close)} phantom trades to close")
        
        if not trades_to_close:
            print("✅ No phantom trades found - database is clean!")
            return
            
        # Show first 10 trades that will be closed
        print("\n📋 Sample trades to be closed:")
        for i, trade in enumerate(trades_to_close[:10]):
            print(f"  {i+1}. {trade['pair']} (ID: {trade['trade_id']}) - {trade['reason']}")
        if len(trades_to_close) > 10:
            print(f"  ... and {len(trades_to_close) - 10} more")
        
        # Confirm cleanup (auto-proceed for automation)
        print(f"\n⚠️  About to close {len(trades_to_close)} phantom trades")
        print("🤖 Auto-proceeding with cleanup...")
            
        # Close phantom trades
        closed_count = 0
        for trade in trades_to_close:
            try:
                # Update trade to CLOSED status with cleanup reason
                update_data = {
                    "status": "CLOSED",
                    "exit_time": datetime.utcnow().isoformat(),
                    "exit_reason": f"PHANTOM_TRADE_CLEANUP: {trade['reason']}",
                    "realized_pnl": 0.0  # No PnL impact since these are phantom trades
                }
                
                response = await client.put(
                    f"http://localhost:8002/api/v1/trades/{trade['trade_id']}", 
                    json=update_data
                )
                
                if response.status_code == 200:
                    closed_count += 1
                    print(f"✅ Closed phantom trade {trade['pair']} ({trade['trade_id']})")
                else:
                    print(f"❌ Failed to close {trade['pair']}: {response.status_code}")
                    
            except Exception as e:
                print(f"❌ Error closing {trade['pair']}: {e}")
        
        print(f"\n🎉 Cleanup complete! Closed {closed_count}/{len(trades_to_close)} phantom trades")
        
        # Verify final count
        final_response = await client.get("http://localhost:8002/api/v1/trades?exchange=cryptocom&status=OPEN")
        if final_response.status_code == 200:
            final_trades = final_response.json().get("trades", [])
            print(f"📊 Remaining open trades in database: {len(final_trades)}")
            
            if len(final_trades) <= 15:
                print("✅ CryptoCom is now under the 15 trade limit - strategies should resume!")
            else:
                print(f"⚠️  Still {len(final_trades)} trades - may need manual review")
        
if __name__ == "__main__":
    asyncio.run(cleanup_stale_trades())