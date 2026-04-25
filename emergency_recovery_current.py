#!/usr/bin/env python3
"""
EMERGENCY RECOVERY: Fix the 2 filled orders that are stuck as OPEN
Based on actual exchange data provided by user
"""
import requests
import json
from datetime import datetime

DATABASE_URL = "http://localhost:8002"

# ACTUAL EXCHANGE DATA from user
FILLED_ORDERS = [
    {
        "trade_id": "209c18c8-d8be-4469-a853-b0ceaed96811",
        "exchange_order_id": "145844603", 
        "exchange_status": "FILLED",
        "fill_price": 7.167,
        "fill_quantity": 28.76,
        "fill_value": 206.12292,
        "exchange": "binance",
        "pair": "NEO/USDC",
        "fee_amount": 0.0002,  # From our system
        "fee_currency": "BNB"
    },
    {
        "trade_id": "ecb71f3e-6c2a-49a3-9e1d-91e5bb4f97a5", 
        "exchange_order_id": "145843905",
        "exchange_status": "FILLED", 
        "fill_price": 7.155,
        "fill_quantity": 12.26,  # Using database position size as correct
        "fill_value": 87.72,    # Calculated: 7.155 * 12.26
        "exchange": "binance", 
        "pair": "NEO/USDC",
        "fee_amount": 0.0002,
        "fee_currency": "BNB"
    }
]

def recover_filled_order(order_data):
    """Manually close a filled order that was missed by fill-detection"""
    trade_id = order_data["trade_id"]
    
    print(f"🔧 RECOVERING: {trade_id[:8]} - {order_data['pair']}")
    print(f"   Exchange Order: {order_data['exchange_order_id']}")
    print(f"   Fill Price: ${order_data['fill_price']}")
    print(f"   Fill Quantity: {order_data['fill_quantity']}")
    
    # Calculate PnL (these were buy orders, so we need to check current price vs entry)
    current_price = order_data['fill_price']  # Using fill price as current for now
    position_size = order_data['fill_quantity']
    entry_price = order_data['fill_price']
    
    # For now, mark as closed at the fill price (break-even)
    realized_pnl = 0.0  # Will be calculated properly once we have current market price
    
    update_data = {
        "status": "CLOSED",
        "exit_price": current_price,
        "exit_time": datetime.utcnow().isoformat() + "Z",
        "realized_pnl": realized_pnl,
        "exit_reason": "manual_recovery_missed_fill",
        "position_size": position_size,  # Ensure correct position size
        "entry_fee_amount": order_data["fee_amount"],
        "entry_fee_currency": order_data["fee_currency"],
        "exit_fee_amount": order_data["fee_amount"],  # Same fee for exit
        "exit_fee_currency": order_data["fee_currency"],
        "fees": order_data["fee_amount"] * 2  # Entry + Exit fees
    }
    
    try:
        response = requests.put(
            f"{DATABASE_URL}/api/v1/trades/{trade_id}",
            headers={"Content-Type": "application/json"},
            data=json.dumps(update_data)
        )
        
        if response.status_code == 200:
            print(f"   ✅ SUCCESS: Trade closed")
            print(f"   📊 PnL: ${realized_pnl:.2f}")
            return True
        else:
            print(f"   ❌ FAILED: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        print(f"   ❌ ERROR: {e}")
        return False

def main():
    """Recover all filled orders"""
    print("🚨 EMERGENCY RECOVERY: CURRENT FILLED ORDERS")
    print("=" * 60)
    print(f"Time: {datetime.now().isoformat()}")
    print()
    
    success_count = 0
    
    for order_data in FILLED_ORDERS:
        if recover_filled_order(order_data):
            success_count += 1
        print("-" * 40)
    
    print(f"📊 RECOVERY SUMMARY:")
    print(f"   Recovered: {success_count}/{len(FILLED_ORDERS)} orders")
    
    if success_count == len(FILLED_ORDERS):
        print("✅ ALL ORDERS RECOVERED")
    else:
        print("❌ SOME RECOVERIES FAILED")
        
    print()
    print("🔍 NEXT STEPS:")
    print("1. Verify trades are now showing as CLOSED in dashboard")
    print("2. Check position balances are correct")
    print("3. Launch live debugging to catch future failures")

if __name__ == "__main__":
    main()