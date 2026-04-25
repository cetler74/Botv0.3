#!/usr/bin/env python3
"""
Fix current trades to have proper fee information
"""
import requests
import json

DATABASE_URL = "http://localhost:8002"

# Fee fixes for current trades based on typical exchange rates
FEE_FIXES = [
    {
        "trade_id": "d11803d9-8adb-4ebf-b2e8-07f59e71b51e",
        "pair": "LTC/USDC",
        "exchange": "binance",
        "entry_fee_currency": "BNB",
        "exit_fee_currency": "BNB",
        "estimated_entry_fee": 0.0002,  # ~$0.12 in BNB at typical rates
        "estimated_exit_fee": 0.0002,
    },
    {
        "trade_id": "bf4f5726-7416-4566-8eaa-708d1bff1409", 
        "pair": "LTC/USDC",
        "exchange": "binance",
        "entry_fee_currency": "BNB",
        "exit_fee_currency": "BNB", 
        "estimated_entry_fee": 0.0002,
        "estimated_exit_fee": 0.0002,
    },
    {
        "trade_id": "dd1ad7ca-4a9e-4c87-b80b-3b9a4a7ac8d5",
        "pair": "CRO/USD", 
        "exchange": "cryptocom",
        "entry_fee_currency": "CRO",
        "exit_fee_currency": "CRO",
        "estimated_entry_fee": 0.0,  # CRO has 0% fees
        "estimated_exit_fee": 0.0,
    },
    {
        "trade_id": "799f595d-080c-49d7-9122-064df0f8e2f5",
        "pair": "NEO/USDC",
        "exchange": "binance", 
        "entry_fee_currency": "BNB",
        "exit_fee_currency": "BNB",
        "estimated_entry_fee": 0.0002,
        "estimated_exit_fee": 0.0002,
    }
]

def fix_trade_fees(fix_data):
    """Fix fees for a single trade"""
    trade_id = fix_data["trade_id"]
    
    update_data = {
        "entry_fee_currency": fix_data["entry_fee_currency"],
        "entry_fee_amount": fix_data["estimated_entry_fee"],
        "exit_fee_currency": fix_data["exit_fee_currency"], 
        "exit_fee_amount": fix_data["estimated_exit_fee"],
        "fees": fix_data["estimated_entry_fee"] + fix_data["estimated_exit_fee"]
    }
    
    print(f"🔧 Fixing {trade_id[:8]} ({fix_data['pair']}):")
    print(f"   Entry: {fix_data['estimated_entry_fee']:.4f} {fix_data['entry_fee_currency']}")
    print(f"   Exit:  {fix_data['estimated_exit_fee']:.4f} {fix_data['exit_fee_currency']}")
    print(f"   Total: {update_data['fees']:.4f}")
    
    try:
        response = requests.put(
            f"{DATABASE_URL}/api/v1/trades/{trade_id}",
            headers={"Content-Type": "application/json"},
            data=json.dumps(update_data)
        )
        
        if response.status_code == 200:
            print(f"   ✅ SUCCESS")
            return True
        else:
            print(f"   ❌ FAILED: {response.text}")
            return False
    except Exception as e:
        print(f"   ❌ ERROR: {e}")
        return False

def main():
    """Fix all current trades"""
    print("🔧 FIXING CURRENT TRADE FEES")
    print("=" * 50)
    
    success_count = 0
    
    for fix_data in FEE_FIXES:
        if fix_trade_fees(fix_data):
            success_count += 1
        print("-" * 30)
    
    print(f"📊 SUMMARY:")
    print(f"   Fixed: {success_count}/{len(FEE_FIXES)} trades")
    
    if success_count == len(FEE_FIXES):
        print("✅ ALL FEES FIXED")
    else:
        print("❌ SOME FIXES FAILED")

if __name__ == "__main__":
    main()