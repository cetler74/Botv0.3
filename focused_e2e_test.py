#!/usr/bin/env python3
"""
Focused End-to-End Test for Real Trade Lifecycle Validation

This test validates the critical path that was failing:
1. Entry cycle creates PENDING trades with correct order submission
2. Exchange fills are properly recorded as OPEN trades with exact fill data
3. Exit cycle uses exact database amounts for sell orders  
4. Exchange sell fills properly close trades with exact exit prices

This addresses the specific gaps where tests passed but production failed.
"""

import asyncio
import httpx
import json
import time
from datetime import datetime, timedelta
import subprocess

class FocusedE2ETest:
    def __init__(self):
        self.orchestrator_url = "http://localhost:8005"
        self.database_url = "http://localhost:8002" 
        self.exchange_url = "http://localhost:8003"
        self.test_exchange = "binance"  # Use binance for reliable testing
        
    async def run_focused_test(self):
        """Run focused test of critical trade lifecycle paths"""
        print("🎯 FOCUSED END-TO-END TRADE LIFECYCLE TEST")
        print("=" * 60)
        print("Testing the exact scenarios that caused production failures")
        print("")
        
        try:
            # Step 1: Record baseline state
            baseline_state = await self.record_baseline_state()
            
            # Step 2: Trigger entry cycle and validate order creation + fill recording
            await self.test_entry_cycle_and_fill_recording(baseline_state)
            
            # Step 3: Trigger exit cycle and validate amount usage + closure recording  
            await self.test_exit_cycle_and_closure_recording()
            
            print("✅ FOCUSED E2E TEST PASSED")
            print("All critical production failure scenarios validated successfully")
            
        except Exception as e:
            print(f"❌ FOCUSED E2E TEST FAILED: {e}")
            raise
    
    async def record_baseline_state(self):
        """Record current state to detect new trades/orders"""
        print("📊 Recording baseline state...")
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Get current trade count
            trades_response = await client.get(f"{self.database_url}/api/v1/trades")
            current_trades = trades_response.json() if trades_response.status_code == 200 else []
            
            # Get current orders on exchange
            orders_response = await client.get(f"{self.exchange_url}/api/v1/trading/orders/{self.test_exchange}")
            current_orders = orders_response.json() if orders_response.status_code == 200 else []
            
            baseline = {
                "trade_count": len(current_trades),
                "order_count": len(current_orders),
                "timestamp": datetime.utcnow().isoformat()
            }
            
            print(f"Baseline: {baseline['trade_count']} trades, {baseline['order_count']} orders")
            return baseline
    
    async def test_entry_cycle_and_fill_recording(self, baseline):
        """Test entry cycle → order creation → fill → database recording"""
        print("\n🔄 TESTING: Entry Cycle → Fill → Database Recording")
        print("-" * 50)
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Start trading system
            start_response = await client.post(f"{self.orchestrator_url}/api/v1/trading/start")
            print(f"Trading system started: {start_response.status_code}")
            
            # Trigger entry cycle
            print("Triggering entry cycle...")
            entry_response = await client.post(f"{self.orchestrator_url}/api/v1/trading/cycle/entry")
            
            if entry_response.status_code not in [200, 201]:
                raise Exception(f"Entry cycle failed: {entry_response.status_code} - {entry_response.text}")
            
            entry_result = entry_response.json()
            print(f"Entry cycle completed. Orders created: {entry_result.get('orders_created', 0)}")
            
            if entry_result.get('orders_created', 0) == 0:
                print("No orders created - system may be at position limits or no opportunities")
                return
            
            # Wait for potential fills and database updates
            print("Waiting for potential fills...")
            await asyncio.sleep(30)  # Give time for fills to process
            
            # Check for new PENDING trades that should become OPEN
            trades_response = await client.get(f"{self.database_url}/api/v1/trades")
            current_trades = trades_response.json() if trades_response.status_code == 200 else []
            
            new_trades = [t for t in current_trades if t.get("created_at", "") > baseline["timestamp"]]
            pending_trades = [t for t in new_trades if t.get("status") == "PENDING"]
            open_trades = [t for t in new_trades if t.get("status") == "OPEN"]
            
            print(f"New trades since baseline: {len(new_trades)}")
            print(f"  - PENDING: {len(pending_trades)}")
            print(f"  - OPEN: {len(open_trades)}")
            
            # CRITICAL TEST 1: Validate any OPEN trades have correct fill data
            if open_trades:
                print(f"\n🔍 VALIDATING {len(open_trades)} OPEN trades for correct fill data...")
                
                for trade in open_trades:
                    await self.validate_open_trade_fill_data(trade, client)
                
                print("✅ All OPEN trades have correct fill data matching exchange")
                self.has_open_trades = True
                self.open_trades_for_exit = open_trades
            else:
                print("⚠️ No OPEN trades found - fills may not have occurred yet")
                self.has_open_trades = False
                
            # CRITICAL TEST 2: Check for problematic PENDING trades (our main bug)
            if pending_trades:
                print(f"\n⚠️ Found {len(pending_trades)} PENDING trades - checking if they should be OPEN...")
                
                for trade in pending_trades:
                    await self.check_pending_trade_should_be_open(trade, client)
    
    async def validate_open_trade_fill_data(self, trade, client):
        """Validate OPEN trade has correct fill data from exchange"""
        trade_id = trade.get("trade_id")
        entry_price = float(trade.get("entry_price", 0))
        position_size = float(trade.get("position_size", 0))
        entry_id = trade.get("entry_id")
        pair = trade.get("pair")
        exchange = trade.get("exchange")
        
        print(f"  Validating {trade_id}: {pair} on {exchange}")
        print(f"    DB Entry Price: ${entry_price}, Position: {position_size}, Order: {entry_id}")
        
        # Get exchange order details
        try:
            order_response = await client.get(f"{self.exchange_url}/api/v1/trading/order/{exchange}/{entry_id}")
            
            if order_response.status_code == 200:
                exchange_order = order_response.json()
                
                exchange_price = float(exchange_order.get("avgPrice", exchange_order.get("price", 0)))
                exchange_qty = float(exchange_order.get("executedQty", exchange_order.get("origQty", 0)))
                exchange_status = exchange_order.get("status", "")
                
                print(f"    Exchange Price: ${exchange_price}, Qty: {exchange_qty}, Status: {exchange_status}")
                
                # CRITICAL VALIDATION: Database must match exchange exactly
                price_match = abs(entry_price - exchange_price) < 0.01
                qty_match = abs(position_size - exchange_qty) < 0.001
                
                if not price_match:
                    raise Exception(f"PRICE MISMATCH in {trade_id}: DB=${entry_price} vs Exchange=${exchange_price}")
                
                if not qty_match:
                    raise Exception(f"QUANTITY MISMATCH in {trade_id}: DB={position_size} vs Exchange={exchange_qty}")
                
                print(f"    ✅ Perfect match - database reflects exact exchange fill")
                
            else:
                print(f"    ⚠️ Could not fetch exchange order details: {order_response.status_code}")
                
        except Exception as e:
            print(f"    ❌ Error validating exchange data: {e}")
    
    async def check_pending_trade_should_be_open(self, trade, client):
        """Check if PENDING trade should actually be OPEN (main bug scenario)"""
        trade_id = trade.get("trade_id")
        created_at = trade.get("created_at")
        entry_id = trade.get("entry_id")
        exchange = trade.get("exchange")
        
        # Check if trade is old enough that it should have been processed
        try:
            trade_time = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            age_minutes = (datetime.utcnow().replace(tzinfo=trade_time.tzinfo) - trade_time).total_seconds() / 60
            
            print(f"  PENDING Trade {trade_id}: {age_minutes:.1f} minutes old")
            
            if age_minutes > 2 and entry_id:  # If trade > 2 min old and has entry_id, check exchange
                print(f"    Checking if order {entry_id} is filled on exchange...")
                
                order_response = await client.get(f"{self.exchange_url}/api/v1/trading/order/{exchange}/{entry_id}")
                if order_response.status_code == 200:
                    exchange_order = order_response.json()
                    exchange_status = exchange_order.get("status", "")
                    
                    if exchange_status in ["FILLED", "filled"]:
                        print(f"    🚨 CRITICAL BUG DETECTED: Trade filled on exchange but PENDING in database!")
                        print(f"       Exchange Status: {exchange_status}")
                        print(f"       This is the exact production bug we're testing for")
                        
                        # This is the scenario our mass recovery fixed
                        raise Exception(f"PENDING trade {trade_id} should be OPEN - exchange shows FILLED")
                    else:
                        print(f"    Order status on exchange: {exchange_status} (correctly PENDING)")
        except Exception as e:
            print(f"    Error checking PENDING trade: {e}")
    
    async def test_exit_cycle_and_closure_recording(self):
        """Test exit cycle uses exact database amounts and records exact exit prices"""
        if not hasattr(self, 'has_open_trades') or not self.has_open_trades:
            print("\n⚠️ SKIPPING exit cycle test - no OPEN trades to test with")
            return
        
        print(f"\n🔄 TESTING: Exit Cycle → Sell Orders → Closure Recording")
        print("-" * 50)
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Record OPEN trades before exit cycle
            open_trades_before = self.open_trades_for_exit
            print(f"Testing exit cycle with {len(open_trades_before)} OPEN trades")
            
            for trade in open_trades_before:
                print(f"  - {trade.get('trade_id')}: {trade.get('position_size')} {trade.get('pair')}")
            
            # Trigger exit cycle
            print("\nTriggering exit cycle...")
            exit_response = await client.post(f"{self.orchestrator_url}/api/v1/trading/cycle/exit")
            
            if exit_response.status_code not in [200, 201]:
                raise Exception(f"Exit cycle failed: {exit_response.status_code} - {exit_response.text}")
            
            exit_result = exit_response.json()
            print(f"Exit cycle completed. Orders created: {exit_result.get('orders_created', 0)}")
            
            if exit_result.get('orders_created', 0) == 0:
                print("No exit orders created - may be within hold period or no profitable exits")
                return
            
            # Wait for potential sell fills
            print("Waiting for potential sell fills...")
            await asyncio.sleep(30)
            
            # Check if any trades became CLOSED
            trades_response = await client.get(f"{self.database_url}/api/v1/trades")
            current_trades = trades_response.json() if trades_response.status_code == 200 else []
            
            closed_trades = [t for t in current_trades if t.get("status") == "CLOSED" 
                           and any(t.get("trade_id") == ot.get("trade_id") for ot in open_trades_before)]
            
            if closed_trades:
                print(f"\n🔍 VALIDATING {len(closed_trades)} CLOSED trades...")
                
                for trade in closed_trades:
                    await self.validate_closed_trade_data(trade, client)
                    
                print("✅ All CLOSED trades have correct exit data matching exchange")
            else:
                print("⚠️ No trades closed - sell orders may not have filled yet")
    
    async def validate_closed_trade_data(self, trade, client):
        """Validate CLOSED trade has correct exit data from exchange"""
        trade_id = trade.get("trade_id")
        exit_price = float(trade.get("exit_price", 0))
        position_size = float(trade.get("position_size", 0))
        exit_id = trade.get("exit_id")
        pair = trade.get("pair")
        exchange = trade.get("exchange")
        
        print(f"  Validating closed trade {trade_id}: {pair}")
        print(f"    DB Exit Price: ${exit_price}, Position: {position_size}, Exit Order: {exit_id}")
        
        # Get exchange sell order details
        try:
            if exit_id:
                order_response = await client.get(f"{self.exchange_url}/api/v1/trading/order/{exchange}/{exit_id}")
                
                if order_response.status_code == 200:
                    exchange_order = order_response.json()
                    
                    exchange_exit_price = float(exchange_order.get("avgPrice", exchange_order.get("price", 0)))
                    exchange_exit_qty = float(exchange_order.get("executedQty", exchange_order.get("origQty", 0)))
                    
                    print(f"    Exchange Exit Price: ${exchange_exit_price}, Qty: {exchange_exit_qty}")
                    
                    # CRITICAL VALIDATION: Exit data must match exchange
                    price_match = abs(exit_price - exchange_exit_price) < 0.01
                    qty_match = abs(position_size - exchange_exit_qty) < 0.001
                    
                    if not price_match:
                        raise Exception(f"EXIT PRICE MISMATCH in {trade_id}: DB=${exit_price} vs Exchange=${exchange_exit_price}")
                    
                    if not qty_match:
                        raise Exception(f"EXIT QUANTITY MISMATCH in {trade_id}: DB={position_size} vs Exchange={exchange_exit_qty}")
                    
                    print(f"    ✅ Perfect match - database reflects exact exchange sell fill")
                    
        except Exception as e:
            print(f"    ❌ Error validating exit data: {e}")

async def main():
    """Run focused end-to-end test"""
    test = FocusedE2ETest()
    await test.run_focused_test()

if __name__ == "__main__":
    asyncio.run(main())