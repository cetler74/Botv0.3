#!/usr/bin/env python3
"""
FIX TOTAL FEES CALCULATION

Current Issue:
- Total fees are not being calculated correctly
- Wrong formula being used for fee calculations

Correct Fee Calculation Logic:
- OPEN trades: total_fees = entry_fee_amount (in currency units, NOT entry_price * entry_fee)
- CLOSED trades: total_fees = entry_fee_amount + exit_fee_amount (both in currency units)

The fees are already in their respective currencies (BNB, USDT, etc.), not percentages!
"""
import requests
import json

DATABASE_URL = "http://localhost:8002"

def analyze_current_fee_calculation():
    """Analyze how fees are currently being calculated"""
    print("🔍 ANALYZING CURRENT FEE CALCULATION ISSUES")
    print("=" * 60)
    
    try:
        response = requests.get(f"{DATABASE_URL}/api/v1/trades")
        trades = response.json().get("trades", [])
        
        print("📊 CURRENT FEE CALCULATION ANALYSIS:")
        print("-" * 40)
        
        for trade in trades[-5:]:  # Check last 5 trades
            trade_id = trade["trade_id"][:8]
            status = trade["status"]
            entry_price = trade.get("entry_price", 0)
            entry_fee_amount = trade.get("entry_fee_amount", 0)
            entry_fee_currency = trade.get("entry_fee_currency", "")
            exit_fee_amount = trade.get("exit_fee_amount", 0)
            exit_fee_currency = trade.get("exit_fee_currency", "")
            current_total_fees = trade.get("fees", 0)
            
            print(f"\nTrade {trade_id} ({status}):")
            print(f"  Entry Price: ${entry_price}")
            print(f"  Entry Fee: {entry_fee_amount} {entry_fee_currency}")
            print(f"  Exit Fee: {exit_fee_amount} {exit_fee_currency}")
            print(f"  Current Total Fees: ${current_total_fees}")
            
            # Calculate correct total fees
            if status == "OPEN":
                # For OPEN trades: only entry fee exists
                correct_total = entry_fee_amount if entry_fee_amount else 0
                print(f"  ✅ CORRECT Total (OPEN): {correct_total} {entry_fee_currency}")
            else:
                # For CLOSED trades: entry fee + exit fee
                correct_total = (entry_fee_amount or 0) + (exit_fee_amount or 0)
                print(f"  ✅ CORRECT Total (CLOSED): {correct_total}")
            
            if abs(float(current_total_fees or 0) - float(correct_total or 0)) > 0.0001:
                print(f"  🚨 INCORRECT CALCULATION!")
            else:
                print(f"  ✅ Calculation is correct")
                
    except Exception as e:
        print(f"❌ Error analyzing fees: {e}")

def create_fee_calculation_fixes():
    """Create the correct fee calculation logic"""
    
    print("\n🔧 CORRECT FEE CALCULATION LOGIC:")
    print("=" * 40)
    print()
    
    print("❌ WRONG (Current Logic):")
    print("   total_fees = entry_price * entry_fee + exit_fee")
    print("   ↳ This assumes fees are percentages, but they're actual amounts!")
    print()
    
    print("✅ CORRECT Logic:")
    print("   For OPEN trades:")
    print("     total_fees = entry_fee_amount")
    print("     ↳ Only entry fee exists, already in currency units")
    print()
    print("   For CLOSED trades:")
    print("     total_fees = entry_fee_amount + exit_fee_amount") 
    print("     ↳ Both fees in their respective currency units")
    print()
    
    print("📝 IMPLEMENTATION:")
    
    # Database service fix
    database_fix = '''
# In database service (main.py) - PUT /api/v1/trades/{trade_id} endpoint:

def calculate_total_fees(trade_data, update_data):
    """Calculate total fees correctly based on trade status"""
    
    # Get current values
    entry_fee_amount = float(update_data.get("entry_fee_amount", trade_data.get("entry_fee_amount", 0) or 0))
    exit_fee_amount = float(update_data.get("exit_fee_amount", trade_data.get("exit_fee_amount", 0) or 0))
    status = update_data.get("status", trade_data.get("status", "OPEN"))
    
    if status == "OPEN":
        # For OPEN trades: only entry fee exists
        total_fees = entry_fee_amount
    else:
        # For CLOSED trades: entry fee + exit fee  
        total_fees = entry_fee_amount + exit_fee_amount
    
    return total_fees

# Update the trade update logic:
if any(key in update_data for key in ["entry_fee_amount", "exit_fee_amount", "status"]):
    # Recalculate total fees when fee fields or status change
    update_data["fees"] = calculate_total_fees(existing_trade, update_data)
'''
    
    print(database_fix)
    
    # Fill-detection service fix
    fill_detection_fix = '''
# In fill-detection service (main.py) - handle_buy_order and handle_sell_order:

async def handle_buy_order(self, fill_event: Dict[str, Any]):
    """Handle buy order fill - create OPEN trade with correct entry fee"""
    
    # ... existing logic ...
    
    trade_data = {
        # ... other fields ...
        "entry_fee_amount": fill_event.get("fee_amount", 0.0),
        "entry_fee_currency": fill_event.get("fee_currency"),
        "fees": fill_event.get("fee_amount", 0.0),  # For OPEN: total = entry fee only
        "status": "OPEN"
    }

async def handle_sell_order(self, fill_event: Dict[str, Any]):
    """Handle sell order fill - close trade with correct total fees"""
    
    # ... get existing trade ...
    
    entry_fee_amount = float(existing_trade.get("entry_fee_amount", 0) or 0)
    exit_fee_amount = float(fill_event.get("fee_amount", 0) or 0)
    total_fees = entry_fee_amount + exit_fee_amount  # CORRECT calculation
    
    update_data = {
        # ... other fields ...
        "exit_fee_amount": exit_fee_amount,
        "exit_fee_currency": fill_event.get("fee_currency"),
        "fees": total_fees,  # For CLOSED: total = entry + exit
        "status": "CLOSED"
    }
'''
    
    print(fill_detection_fix)

def fix_existing_trades_fees():
    """Fix existing trades with incorrect fee calculations"""
    print("\n🔧 FIXING EXISTING TRADES:")
    print("=" * 30)
    
    try:
        response = requests.get(f"{DATABASE_URL}/api/v1/trades")
        trades = response.json().get("trades", [])
        
        fixes_needed = []
        
        for trade in trades:
            trade_id = trade["trade_id"]
            status = trade["status"]
            entry_fee_amount = float(trade.get("entry_fee_amount", 0) or 0)
            exit_fee_amount = float(trade.get("exit_fee_amount", 0) or 0)
            current_total = float(trade.get("fees", 0) or 0)
            
            # Calculate correct total
            if status == "OPEN":
                correct_total = entry_fee_amount
            else:
                correct_total = entry_fee_amount + exit_fee_amount
            
            # Check if fix needed
            if abs(current_total - correct_total) > 0.0001:
                fixes_needed.append({
                    "trade_id": trade_id,
                    "current_total": current_total,
                    "correct_total": correct_total,
                    "status": status
                })
        
        print(f"Found {len(fixes_needed)} trades needing fee fixes:")
        for fix in fixes_needed[:10]:  # Show first 10
            print(f"  {fix['trade_id'][:8]} ({fix['status']}): ${fix['current_total']:.6f} → ${fix['correct_total']:.6f}")
            
        if fixes_needed:
            print(f"\n🔧 Apply fixes by running fix_all_trade_fees.py")
        else:
            print("✅ All trade fees are correctly calculated")
            
    except Exception as e:
        print(f"❌ Error checking trades: {e}")

def main():
    """Main analysis and fix generation"""
    print("🚨 TOTAL FEES CALCULATION FIX")
    print("Issue: Fees being calculated incorrectly")
    print("Solution: Use actual fee amounts, not price * fee formulas")
    print("=" * 70)
    
    analyze_current_fee_calculation()
    create_fee_calculation_fixes()
    fix_existing_trades_fees()
    
    print(f"\n📋 IMPLEMENTATION STEPS:")
    print("1. Update database service fee calculation logic")
    print("2. Update fill-detection service fee handling") 
    print("3. Fix existing trades with incorrect fees")
    print("4. Test with new trades to verify fixes")
    print("5. Update dashboard to show correct totals")

if __name__ == "__main__":
    main()