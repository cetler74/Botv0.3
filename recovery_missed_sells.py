#!/usr/bin/env python3
"""
Recovery script for missed sell order fills
"""
import requests
import json
from datetime import datetime, timezone

# Database service URL
DATABASE_URL = "http://localhost:8002"

def close_trade(trade_id, exit_price, exit_amount, exit_reason="Manual recovery - missed exchange fill"):
    """Close a trade that was filled on exchange but missed by fill-detection"""
    
    # Calculate PnL
    # Get current trade data
    response = requests.get(f"{DATABASE_URL}/api/v1/trades")
    trades = response.json()["trades"]
    
    trade = None
    for t in trades:
        if t["trade_id"] == trade_id:
            trade = t
            break
    
    if not trade:
        print(f"❌ Trade {trade_id} not found")
        return False
        
    entry_price = float(trade["entry_price"])
    position_size = float(trade["position_size"])
    
    # Calculate PnL
    gross_pnl = (exit_price - entry_price) * exit_amount
    entry_fees = float(trade.get("entry_fee_amount", 0))
    exit_fees = 0.0  # Assume 0 for recovery
    net_pnl = gross_pnl - entry_fees - exit_fees
    
    # Prepare update data
    update_data = {
        "status": "CLOSED",
        "exit_price": exit_price,
        "exit_time": datetime.now(timezone.utc).isoformat(),
        "exit_reason": exit_reason,
        "realized_pnl": net_pnl,
        "exit_fee_amount": exit_fees,
        "exit_fee_currency": "BNB",  # Binance default
        "fees": entry_fees + exit_fees
    }
    
    print(f"🔄 Closing trade {trade_id}:")
    print(f"   Entry: {entry_price} × {position_size} = {entry_price * position_size:.2f}")
    print(f"   Exit: {exit_price} × {exit_amount} = {exit_price * exit_amount:.2f}")  
    print(f"   PnL: {net_pnl:.4f} USDC")
    
    # Update the trade
    response = requests.put(
        f"{DATABASE_URL}/api/v1/trades/{trade_id}",
        headers={"Content-Type": "application/json"},
        data=json.dumps(update_data)
    )
    
    if response.status_code == 200:
        print(f"✅ Successfully closed trade {trade_id}")
        return True
    else:
        print(f"❌ Failed to close trade {trade_id}: {response.text}")
        return False

def main():
    """Main recovery function"""
    print("🚨 CRITICAL RECOVERY: Closing missed sell orders")
    
    # Recovery data from exchange (corrected trade IDs)
    recoveries = [
        {
            "trade_id": "bf4f5726-7416-4566-8eaa-708d1bff1409",
            "exit_price": 113.39,
            "exit_amount": 1.829,
            "reason": "Recovery - Exchange sell at 2025-08-26 20:07:51"
        },
        {
            "trade_id": "d11803d9-8adb-4ebf-b2e8-07f59e71b51e", 
            "exit_price": 113.45,
            "exit_amount": 1.826,
            "reason": "Recovery - Exchange sell at 2025-08-26 20:08:07"
        }
    ]
    
    for recovery in recoveries:
        success = close_trade(
            recovery["trade_id"],
            recovery["exit_price"], 
            recovery["exit_amount"],
            recovery["reason"]
        )
        
        if success:
            print(f"✅ Recovery completed for {recovery['trade_id']}")
        else:
            print(f"❌ Recovery failed for {recovery['trade_id']}")
        print("-" * 50)

if __name__ == "__main__":
    main()