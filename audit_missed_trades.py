#!/usr/bin/env python3
"""
CRITICAL: Audit all open trades against exchange balances
Find trades that should be closed but are still showing as OPEN
"""
import requests
import json
from datetime import datetime, timezone

# Service URLs
DATABASE_URL = "http://localhost:8002"
EXCHANGE_URL = "http://localhost:8003"

def get_exchange_balances():
    """Get current balances from all exchanges"""
    balances = {}
    exchanges = ["binance", "bybit", "cryptocom"]
    
    for exchange in exchanges:
        try:
            response = requests.get(f"{EXCHANGE_URL}/api/v1/balances/{exchange}")
            if response.status_code == 200:
                data = response.json()
                balances[exchange] = data.get("balances", {})
                print(f"✅ Got {exchange} balances: {len(balances[exchange])} assets")
            else:
                print(f"❌ Failed to get {exchange} balances: {response.status_code}")
                balances[exchange] = {}
        except Exception as e:
            print(f"❌ Error getting {exchange} balances: {e}")
            balances[exchange] = {}
    
    return balances

def get_open_trades():
    """Get all open trades from database"""
    try:
        response = requests.get(f"{DATABASE_URL}/api/v1/trades?status=OPEN")
        if response.status_code == 200:
            trades = response.json()["trades"]
            print(f"📊 Found {len(trades)} OPEN trades in database")
            return trades
        else:
            print(f"❌ Failed to get trades: {response.status_code}")
            return []
    except Exception as e:
        print(f"❌ Error getting trades: {e}")
        return []

def analyze_trade_discrepancies(open_trades, exchange_balances):
    """Find trades where database shows OPEN but exchange balance suggests closed"""
    discrepancies = []
    
    for trade in open_trades:
        exchange = trade["exchange"]
        pair = trade["pair"]
        position_size = float(trade.get("position_size", 0))
        trade_id = trade["trade_id"]
        entry_time = trade.get("entry_time", "")
        
        # Extract base currency (e.g., "BTC/USDC" -> "BTC")
        if "/" in pair:
            base_currency = pair.split("/")[0]
        else:
            continue
            
        # Check if we have balance data for this exchange
        if exchange not in exchange_balances:
            print(f"⚠️  No balance data for {exchange}")
            continue
            
        exchange_balance = exchange_balances[exchange]
        current_balance = float(exchange_balance.get(base_currency, 0))
        
        # If position size is much larger than current balance, trade might be closed
        balance_ratio = current_balance / position_size if position_size > 0 else 0
        
        discrepancy = {
            "trade_id": trade_id[:8],
            "full_trade_id": trade_id,
            "exchange": exchange,
            "pair": pair,
            "base_currency": base_currency,
            "expected_position": position_size,
            "current_balance": current_balance,
            "balance_ratio": balance_ratio,
            "entry_time": entry_time,
            "entry_price": trade.get("entry_price", 0),
            "potential_issue": balance_ratio < 0.1  # Less than 10% remaining = likely sold
        }
        
        if discrepancy["potential_issue"]:
            discrepancies.append(discrepancy)
            
        print(f"🔍 {trade_id[:8]} {exchange} {pair}: Expected {position_size:.6f}, Balance {current_balance:.6f} ({balance_ratio:.1%})")
    
    return discrepancies

def create_recovery_script(discrepancies):
    """Create a recovery script for all found discrepancies"""
    if not discrepancies:
        print("✅ No discrepancies found!")
        return
        
    print(f"\n🚨 FOUND {len(discrepancies)} POTENTIAL MISSED TRADES:")
    print("=" * 80)
    
    for disc in discrepancies:
        print(f"❌ {disc['trade_id']} - {disc['exchange']} {disc['pair']}")
        print(f"   Expected: {disc['expected_position']:.6f} {disc['base_currency']}")
        print(f"   Current:  {disc['current_balance']:.6f} {disc['base_currency']} ({disc['balance_ratio']:.1%} remaining)")
        print(f"   Entry: {disc['entry_price']} at {disc['entry_time']}")
        print()
    
    # Generate recovery commands
    print("\n💊 RECOVERY COMMANDS:")
    print("=" * 40)
    for disc in discrepancies:
        if disc['balance_ratio'] < 0.05:  # Less than 5% remaining = definitely sold
            print(f"# Likely fully sold - needs manual recovery")
            print(f"# Trade: {disc['trade_id']} {disc['pair']}")
            print(f"curl -X PUT {DATABASE_URL}/api/v1/trades/{disc['full_trade_id']} -H 'Content-Type: application/json' -d '{{\"status\": \"CLOSED\", \"exit_reason\": \"Manual recovery - detected sold position\"}}'")
            print()

def main():
    """Main audit function"""
    print("🚨 CRITICAL AUDIT: Checking for missed sell orders")
    print("=" * 60)
    
    # Get current state
    print("\n1️⃣ Getting exchange balances...")
    exchange_balances = get_exchange_balances()
    
    print("\n2️⃣ Getting open trades...")
    open_trades = get_open_trades()
    
    print("\n3️⃣ Analyzing discrepancies...")
    discrepancies = analyze_trade_discrepancies(open_trades, exchange_balances)
    
    print("\n4️⃣ Creating recovery plan...")
    create_recovery_script(discrepancies)
    
    print(f"\n📋 AUDIT SUMMARY:")
    print(f"   Open trades in DB: {len(open_trades)}")
    print(f"   Potential issues:  {len(discrepancies)}")
    print(f"   Exchanges checked: {len(exchange_balances)}")
    
    if discrepancies:
        print(f"\n🚨 ACTION REQUIRED: {len(discrepancies)} trades need manual review!")
    else:
        print(f"\n✅ All trades appear consistent with exchange balances")

if __name__ == "__main__":
    main()