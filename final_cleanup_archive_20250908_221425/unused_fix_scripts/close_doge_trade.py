#!/usr/bin/env python3
"""
Close DOGE trade manually based on exchange fill data
"""

import requests
import json
from datetime import datetime, timezone

# Database service URL
DATABASE_URL = "http://localhost:8002"

def close_doge_trade():
    """Close the DOGE trade based on exchange fill data"""
    
    # Get the DOGE trade
    response = requests.get(f"{DATABASE_URL}/api/v1/trades")
    trades = response.json()["trades"]
    
    doge_trade = None
    for trade in trades:
        if trade["pair"] == "DOGE/USDC" and trade["status"] == "OPEN":
            doge_trade = trade
            break
    
    if not doge_trade:
        print("❌ DOGE/USDC trade not found")
        return False
    
    print(f"📊 Found DOGE trade: {doge_trade['trade_id'][:8]}...")
    print(f"   Entry Price: ${doge_trade['entry_price']:.6f}")
    print(f"   Position Size: {doge_trade['position_size']:.3f} DOGE")
    print(f"   Current Price: ${doge_trade['current_price']:.6f}")
    print(f"   Unrealized PnL: ${doge_trade['unrealized_pnl']:.4f}")
    
    # Based on exchange data:
    # - Filled order: 343.1 DOGE at $0.22311
    # - Total position: 916 DOGE
    # - Remaining: 916 - 343.1 = 572.9 DOGE
    
    filled_amount = 343.1
    exit_price = 0.22311
    remaining_amount = 916.0 - filled_amount
    
    entry_price = float(doge_trade["entry_price"])
    position_size = float(doge_trade["position_size"])
    
    # Calculate PnL for the filled portion
    realized_pnl = (exit_price - entry_price) * filled_amount
    
    print(f"\n📈 Exchange Fill Data:")
    print(f"   Filled Amount: {filled_amount} DOGE")
    print(f"   Exit Price: ${exit_price:.6f}")
    print(f"   Remaining: {remaining_amount} DOGE")
    print(f"   Realized PnL: ${realized_pnl:.4f}")
    
    # Close the trade
    update_data = {
        "status": "CLOSED",
        "exit_price": exit_price,
        "exit_time": datetime.now(timezone.utc).isoformat(),
        "exit_reason": "Manual close - exchange fill detected",
        "realized_pnl": realized_pnl,
        "exit_fee_amount": 0.0,
        "exit_fee_currency": "USDC",
        "fees": float(doge_trade.get("entry_fee_amount", 0))
    }
    
    print(f"\n🔄 Closing trade...")
    
    # Update the trade
    response = requests.put(
        f"{DATABASE_URL}/api/v1/trades/{doge_trade['trade_id']}",
        headers={"Content-Type": "application/json"},
        data=json.dumps(update_data)
    )
    
    if response.status_code == 200:
        print(f"✅ Successfully closed DOGE trade")
        print(f"   Final PnL: ${realized_pnl:.4f}")
        
        # Verify the trade is now closed
        verify_response = requests.get(f"{DATABASE_URL}/api/v1/trades/{doge_trade['trade_id']}")
        if verify_response.status_code == 200:
            updated_trade = verify_response.json()
            print(f"   Status: {updated_trade['status']}")
            print(f"   Exit Time: {updated_trade['exit_time']}")
        
        return True
    else:
        print(f"❌ Failed to close trade: {response.status_code}")
        print(f"   Response: {response.text}")
        return False

if __name__ == "__main__":
    close_doge_trade()
