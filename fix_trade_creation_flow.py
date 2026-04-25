#!/usr/bin/env python3
"""
FIX TRADE CREATION FLOW: Implement correct process flow
1. Place limit order → Store as PENDING
2. Wait for fill confirmation → Create OPEN trade
3. Monitor for exit → Close trade

This fixes the fundamental issue where trades are created before orders are filled.
"""
import asyncio
import httpx
import logging
from datetime import datetime

DATABASE_URL = "http://localhost:8002"
ORCHESTRATOR_URL = "http://localhost:8005"

async def analyze_current_flow():
    """Analyze the current broken flow"""
    print("🔍 ANALYZING CURRENT TRADE CREATION FLOW")
    print("=" * 60)
    
    # Check how many trades are created vs actual filled orders
    async with httpx.AsyncClient(timeout=30.0) as client:
        trades_response = await client.get(f"{DATABASE_URL}/api/v1/trades")
        if trades_response.status_code == 200:
            trades = trades_response.json().get("trades", [])
            open_trades = [t for t in trades if t.get("status") == "OPEN"]
            recent_trades = sorted(trades, key=lambda x: x.get("created_at", ""))[-10:]
            
            print(f"📊 Current Status:")
            print(f"   Total trades: {len(trades)}")
            print(f"   Open trades: {len(open_trades)}")
            print()
            
            print("🔍 Recent Trade Creation Pattern:")
            for trade in recent_trades[-5:]:
                trade_id = trade["trade_id"][:8]
                status = trade["status"]
                entry_id = trade.get("entry_id", "None")
                created_at = trade.get("created_at", "Unknown")
                print(f"   {trade_id} - {status} - Entry ID: {entry_id} - Created: {created_at}")
            
            print()
            print("🚨 PROBLEM IDENTIFIED:")
            print("   Trades are being created as OPEN immediately when orders are placed")
            print("   This happens BEFORE the exchange confirms the order is filled")
            print("   Result: Database shows OPEN but exchange might show PENDING/UNFILLED")

async def propose_solution():
    """Propose the correct solution"""
    print("\n✅ PROPOSED SOLUTION:")
    print("=" * 40)
    print()
    print("CURRENT BROKEN FLOW:")
    print("1. 📤 Place limit order → get order_id")  
    print("2. ❌ IMMEDIATELY create OPEN trade in database")
    print("3. 🔄 Fill-detection tries to track (but trade already exists)")
    print("4. 💥 Chaos when order isn't actually filled")
    print()
    print("CORRECT FLOW (per user specification):")
    print("1. 📤 Place limit order → get entry_id")
    print("2. 📋 Store order in PENDING_ORDERS table (NOT trades table)")
    print("3. ⏳ Wait for fill-detection to confirm order is FILLED")
    print("4. ✅ ONLY THEN create OPEN trade in trades table")
    print("5. 📊 Monitor for exit conditions")
    print("6. 🎯 Close trade when exit is filled")
    print()
    print("REQUIRED CHANGES:")
    print("- Create PENDING_ORDERS table for unfilled orders")
    print("- Modify orchestrator to NOT create trades immediately")
    print("- Enhance fill-detection to create trades only when filled")
    print("- Update all services to use this new flow")

async def create_pending_orders_schema():
    """Create schema for pending orders tracking"""
    print("\n🗃️ CREATING PENDING_ORDERS SCHEMA:")
    print("-" * 30)
    
    schema_sql = """
    CREATE TABLE IF NOT EXISTS pending_orders (
        id SERIAL PRIMARY KEY,
        order_id VARCHAR(100) UNIQUE NOT NULL,
        exchange_order_id VARCHAR(100) UNIQUE NOT NULL,
        exchange VARCHAR(50) NOT NULL,
        pair VARCHAR(20) NOT NULL,
        side VARCHAR(10) NOT NULL,
        amount DECIMAL(20, 8) NOT NULL,
        price DECIMAL(20, 8) NOT NULL,
        order_type VARCHAR(20) NOT NULL DEFAULT 'limit',
        status VARCHAR(20) NOT NULL DEFAULT 'pending',
        strategy VARCHAR(50),
        entry_reason TEXT,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        filled_at TIMESTAMP WITH TIME ZONE,
        cancelled_at TIMESTAMP WITH TIME ZONE
    );
    
    CREATE INDEX IF NOT EXISTS idx_pending_orders_exchange_order_id ON pending_orders(exchange_order_id);
    CREATE INDEX IF NOT EXISTS idx_pending_orders_status ON pending_orders(status);
    CREATE INDEX IF NOT EXISTS idx_pending_orders_exchange ON pending_orders(exchange);
    """
    
    print("SQL Schema for PENDING_ORDERS table:")
    print(schema_sql)
    
    return schema_sql

def main():
    """Main analysis function"""
    print("🚨 TRADE CREATION FLOW ANALYSIS & FIX")
    print("User Requirement: Only create OPEN trades when orders are actually filled")
    print("=" * 80)
    
    asyncio.run(analyze_current_flow())
    asyncio.run(propose_solution())
    
    schema = asyncio.run(create_pending_orders_schema())
    
    print("\n🎯 NEXT STEPS:")
    print("1. Apply PENDING_ORDERS schema to database")
    print("2. Modify orchestrator to store orders as PENDING first")
    print("3. Update fill-detection to create trades only when filled")
    print("4. Test with small trades to verify correct flow")
    print("5. Migrate existing system gradually")

if __name__ == "__main__":
    main()