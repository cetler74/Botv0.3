#!/usr/bin/env python3
"""
EMERGENCY: Reconcile database positions with actual exchange balances
"""
import requests
import json

DATABASE_URL = "http://localhost:8002"

# REAL EXCHANGE BALANCES (from user)
ACTUAL_BALANCES = {
    "binance": {
        "BTC": 0.00371292,  # €354.22 - USER CONFIRMED
        "LTC": 0.000341,    # €0.03 - USER CONFIRMED  
        "NEO": 0.005755,    # €0.04 - USER CONFIRMED
        # Add others as needed
    },
    "cryptocom": {
        "CRO": "user_needs_to_provide"  # Need actual balance
    },
    "bybit": {
        # Need actual balances
    }
}

def analyze_position_discrepancies():
    """Compare database positions vs actual exchange balances"""
    
    print("🚨 EMERGENCY RECONCILIATION ANALYSIS")
    print("=" * 60)
    
    # Get all trades from database  
    response = requests.get(f"{DATABASE_URL}/api/v1/trades")
    all_trades = response.json()["trades"]
    
    # Group by exchange and asset
    positions = {}
    
    for trade in all_trades:
        if trade["status"] != "CLOSED":
            continue  # Only look at closed trades for now
            
        exchange = trade["exchange"]  
        pair = trade["pair"]
        
        if "/" not in pair:
            continue
            
        base_currency = pair.split("/")[0]
        position_size = float(trade.get("position_size", 0))
        
        key = f"{exchange}:{base_currency}"
        
        if key not in positions:
            positions[key] = {
                "exchange": exchange,
                "currency": base_currency, 
                "database_total": 0,
                "trades": []
            }
            
        positions[key]["database_total"] += position_size
        positions[key]["trades"].append({
            "trade_id": trade["trade_id"][:8],
            "position_size": position_size,
            "status": trade["status"],
            "entry_time": trade.get("entry_time", "")
        })
    
    # Compare with actual balances
    print("📊 POSITION ANALYSIS:")
    print("-" * 40)
    
    for key, pos in positions.items():
        exchange = pos["exchange"]
        currency = pos["currency"]
        db_total = pos["database_total"]
        
        # Get actual balance
        actual_balance = 0
        if exchange in ACTUAL_BALANCES and currency in ACTUAL_BALANCES[exchange]:
            balance_val = ACTUAL_BALANCES[exchange][currency] 
            actual_balance = float(balance_val) if isinstance(balance_val, (int, float, str)) and str(balance_val).replace('.','').isdigit() else 0
            
        difference = db_total - actual_balance
        
        print(f"\n{exchange.upper()} {currency}:")
        print(f"  Database total: {db_total:.8f}")
        print(f"  Actual balance: {actual_balance:.8f}") 
        print(f"  Difference:     {difference:.8f}")
        
        if abs(difference) > 0.0001:  # Significant difference
            print(f"  🚨 DISCREPANCY: {difference:.8f} {currency}")
            print(f"  Recent trades:")
            for trade in pos["trades"][-3:]:  # Show last 3 trades
                print(f"    - {trade['trade_id']}: {trade['position_size']:.8f} ({trade['status']})")

def main():
    analyze_position_discrepancies()

if __name__ == "__main__":
    main()