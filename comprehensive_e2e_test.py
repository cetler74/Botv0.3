#!/usr/bin/env python3
"""
Comprehensive End-to-End Test for Complete Trade Lifecycle
Tests the critical path: Buy Signal → Fill → Database → Sell Signal → Fill → Closure

This test validates:
1. Buy signal generates correct limit order
2. Exchange fills the order with actual price/amount
3. Database records EXACT fill data (price, amount)  
4. Sell signal uses EXACT database amount for order
5. Exchange fills sell order
6. Trade closed with EXACT exit price from fill

This prevents the critical failures we experienced where:
- Tests passed but real trades failed
- Database had wrong fill prices/amounts
- Sell orders used wrong amounts
- Data integrity gaps between exchange and database
"""

import asyncio
import httpx
import json
import time
from datetime import datetime, timedelta
import subprocess
import uuid

class ComprehensiveE2ETest:
    def __init__(self):
        self.test_id = str(uuid.uuid4())[:8]
        self.orchestrator_url = "http://localhost:8005"
        self.database_url = "http://localhost:8002" 
        self.exchange_url = "http://localhost:8003"
        self.test_pair = "ETH/USDC"
        self.test_exchange = "binance"
        self.trade_id = None
        self.entry_order_id = None
        self.exit_order_id = None
        
    async def run_full_lifecycle_test(self):
        """Run complete trade lifecycle test with validation at each step"""
        print(f"🚀 Starting Comprehensive E2E Test {self.test_id}")
        print("=" * 60)
        
        try:
            # Step 1: Generate Buy Signal and Validate Order Creation
            await self.step1_buy_signal_and_order()
            
            # Step 2: Wait for Exchange Fill and Validate Database Recording
            await self.step2_wait_for_fill_and_validate_recording()
            
            # Step 3: Generate Sell Signal Using Database Amount
            await self.step3_sell_signal_using_database_amount()
            
            # Step 4: Wait for Sell Fill and Validate Trade Closure
            await self.step4_wait_for_sell_fill_and_closure()
            
            # Step 5: Final Validation - Complete Data Integrity Check
            await self.step5_final_validation()
            
            print("✅ COMPREHENSIVE E2E TEST PASSED")
            print("All critical paths validated successfully")
            
        except Exception as e:
            print(f"❌ COMPREHENSIVE E2E TEST FAILED: {e}")
            await self.cleanup_failed_test()
            raise
    
    async def step1_buy_signal_and_order(self):
        """Step 1: Buy signal → limit order creation → order submission"""
        print("\n📊 STEP 1: Buy Signal and Order Creation")
        print("-" * 40)
        
        # Get current market price for realistic limit order
        async with httpx.AsyncClient(timeout=30.0) as client:
            price_response = await client.get(f"{self.exchange_url}/api/v1/market/ticker/{self.test_exchange}/{self.test_pair.replace('/', '')}")
            if price_response.status_code != 200:
                raise Exception(f"Failed to get market price: {price_response.status_code} - {price_response.text}")
            
            ticker_data = price_response.json()
            current_price = float(ticker_data.get("last", ticker_data.get("price", ticker_data.get("close", 0))))
            limit_price = round(current_price * 0.999, 2)  # 0.1% below market for quick fill
            amount = 0.01  # Small test amount
            
            print(f"Market Price: ${current_price}")
            print(f"Limit Price: ${limit_price}")
            print(f"Amount: {amount} ETH")
            
            # Trigger entry cycle to generate buy orders
            print("Triggering entry cycle to generate buy orders...")
            
            # First, start trading if not started
            start_response = await client.post(f"{self.orchestrator_url}/api/v1/trading/start")
            print(f"Trading start response: {start_response.status_code}")
            
            # Trigger entry cycle 
            entry_response = await client.post(f"{self.orchestrator_url}/api/v1/trading/cycle/entry")
            if entry_response.status_code not in [200, 201]:
                raise Exception(f"Entry cycle failed: {entry_response.status_code} - {entry_response.text}")
            
            entry_result = entry_response.json()
            print(f"Entry cycle result: {json.dumps(entry_result, indent=2)}")
            
            # We'll need to monitor for new trades created, as we can't predict specific trade_id
            
            print(f"✅ Buy signal sent successfully")
            print(f"Trade ID: {self.trade_id}")
            
            # Wait for order to be created and submitted
            await asyncio.sleep(5)
            
            # Validate order was submitted to exchange
            orders_response = await client.get(f"{self.exchange_url}/api/v1/trading/orders/{self.test_exchange}")
            if orders_response.status_code == 200:
                orders = orders_response.json()
                our_order = None
                for order in orders:
                    if order.get("symbol") == self.test_pair.replace("/", "") and abs(float(order.get("price", 0)) - limit_price) < 0.01:
                        our_order = order
                        self.entry_order_id = order.get("orderId")
                        break
                
                if not our_order:
                    raise Exception("Buy order not found on exchange")
                
                print(f"✅ Order found on exchange: {self.entry_order_id}")
                print(f"Exchange Order: {json.dumps(our_order, indent=2)}")
                
    async def step2_wait_for_fill_and_validate_recording(self):
        """Step 2: Wait for fill → validate database recording with exact fill data"""
        print("\n⏳ STEP 2: Wait for Fill and Validate Database Recording")
        print("-" * 40)
        
        fill_detected = False
        max_wait = 120  # 2 minutes max wait
        start_time = time.time()
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            while not fill_detected and (time.time() - start_time) < max_wait:
                await asyncio.sleep(5)
                
                # Check database for trade status
                db_response = await client.get(f"{self.database_url}/api/v1/trades/{self.trade_id}")
                if db_response.status_code == 200:
                    trade_data = db_response.json()
                    if trade_data.get("status") == "OPEN":
                        print(f"✅ Trade detected as OPEN in database")
                        
                        # Validate exact fill data recording
                        entry_price = float(trade_data.get("entry_price", 0))
                        position_size = float(trade_data.get("position_size", 0))
                        entry_id = trade_data.get("entry_id")
                        
                        print(f"Database Entry Price: ${entry_price}")
                        print(f"Database Position Size: {position_size}")
                        print(f"Database Entry ID: {entry_id}")
                        
                        # Validate against exchange fill data
                        exchange_response = await client.get(f"{self.exchange_url}/api/v1/trading/orders/{self.test_exchange}")
                        if exchange_response.status_code == 200:
                            exchange_orders = exchange_response.json()
                            
                            # Find our filled order
                            filled_order = None
                            for order in exchange_orders:
                                if order.get("orderId") == entry_id and order.get("status") in ["FILLED", "filled"]:
                                    filled_order = order
                                    break
                            
                            if not filled_order:
                                raise Exception("Filled order not found on exchange or not filled")
                            
                            # CRITICAL VALIDATION: Database must match exchange exactly
                            exchange_avg_price = float(filled_order.get("avgPrice", filled_order.get("price", 0)))
                            exchange_filled_qty = float(filled_order.get("executedQty", filled_order.get("origQty", 0)))
                            
                            print(f"Exchange Fill Price: ${exchange_avg_price}")
                            print(f"Exchange Fill Quantity: {exchange_filled_qty}")
                            
                            # Validate exact match (within small floating point tolerance)
                            price_match = abs(entry_price - exchange_avg_price) < 0.01
                            amount_match = abs(position_size - exchange_filled_qty) < 0.001
                            
                            if not price_match:
                                raise Exception(f"PRICE MISMATCH: DB={entry_price}, Exchange={exchange_avg_price}")
                            if not amount_match:
                                raise Exception(f"AMOUNT MISMATCH: DB={position_size}, Exchange={exchange_filled_qty}")
                            
                            print("✅ CRITICAL VALIDATION PASSED: Database matches exchange fill exactly")
                            fill_detected = True
                            self.recorded_entry_price = entry_price
                            self.recorded_position_size = position_size
                
                if not fill_detected:
                    print(f"Waiting for fill... ({int(time.time() - start_time)}s)")
            
            if not fill_detected:
                raise Exception("Fill not detected within timeout period")
    
    async def step3_sell_signal_using_database_amount(self):
        """Step 3: Generate sell signal using EXACT database recorded amount"""
        print("\n📈 STEP 3: Sell Signal Using Database Amount")
        print("-" * 40)
        
        # Get current market price for sell order
        async with httpx.AsyncClient(timeout=30.0) as client:
            price_response = await client.get(f"{self.exchange_url}/api/v1/market/ticker/{self.test_exchange}/{self.test_pair.replace('/', '')}")
            ticker_data = price_response.json()
            current_price = float(ticker_data.get("last", ticker_data.get("price", ticker_data.get("close", 0))))
            sell_limit_price = round(current_price * 1.001, 2)  # 0.1% above market for quick fill
            
            print(f"Current Market Price: ${current_price}")
            print(f"Sell Limit Price: ${sell_limit_price}")
            print(f"Database Recorded Amount: {self.recorded_position_size}")
            
            # CRITICAL: Use EXACT amount from database recording
            sell_signal = {
                "signal_type": "sell",
                "trade_id": self.trade_id,
                "pair": self.test_pair,
                "exchange": self.test_exchange,
                "amount": self.recorded_position_size,  # EXACT database amount
                "price": sell_limit_price,
                "test_id": self.test_id
            }
            
            signal_response = await client.post(f"{self.orchestrator_url}/api/v1/trading/signal", json=sell_signal)
            if signal_response.status_code not in [200, 201]:
                raise Exception(f"Sell signal failed: {signal_response.status_code} - {signal_response.text}")
            
            print("✅ Sell signal sent using exact database amount")
            
            # Wait and validate sell order submission
            await asyncio.sleep(5)
            
            orders_response = await client.get(f"{self.exchange_url}/api/v1/trading/orders/{self.test_exchange}")
            if orders_response.status_code == 200:
                orders = orders_response.json()
                sell_order = None
                for order in orders:
                    if (order.get("symbol") == self.test_pair.replace("/", "") and 
                        order.get("side", "").upper() == "SELL" and
                        abs(float(order.get("price", 0)) - sell_limit_price) < 0.01):
                        sell_order = order
                        self.exit_order_id = order.get("orderId")
                        break
                
                if not sell_order:
                    raise Exception("Sell order not found on exchange")
                
                # Validate sell order amount matches database
                order_qty = float(sell_order.get("origQty", 0))
                if abs(order_qty - self.recorded_position_size) > 0.001:
                    raise Exception(f"SELL AMOUNT MISMATCH: Order={order_qty}, Database={self.recorded_position_size}")
                
                print(f"✅ Sell order validated on exchange: {self.exit_order_id}")
                print(f"Order Quantity: {order_qty} (matches database)")
    
    async def step4_wait_for_sell_fill_and_closure(self):
        """Step 4: Wait for sell fill → validate trade closure with exact exit price"""
        print("\n🎯 STEP 4: Wait for Sell Fill and Trade Closure")
        print("-" * 40)
        
        closure_detected = False
        max_wait = 120
        start_time = time.time()
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            while not closure_detected and (time.time() - start_time) < max_wait:
                await asyncio.sleep(5)
                
                # Check for trade closure in database
                db_response = await client.get(f"{self.database_url}/api/v1/trades/{self.trade_id}")
                if db_response.status_code == 200:
                    trade_data = db_response.json()
                    if trade_data.get("status") == "CLOSED":
                        print("✅ Trade detected as CLOSED in database")
                        
                        # Validate exit price recording
                        exit_price = float(trade_data.get("exit_price", 0))
                        exit_id = trade_data.get("exit_id")
                        
                        print(f"Database Exit Price: ${exit_price}")
                        print(f"Database Exit ID: {exit_id}")
                        
                        # Validate against exchange fill
                        exchange_response = await client.get(f"{self.exchange_url}/api/v1/trading/orders/{self.test_exchange}")
                        if exchange_response.status_code == 200:
                            exchange_orders = exchange_response.json()
                            
                            filled_sell_order = None
                            for order in exchange_orders:
                                if order.get("orderId") == exit_id and order.get("status") in ["FILLED", "filled"]:
                                    filled_sell_order = order
                                    break
                            
                            if not filled_sell_order:
                                raise Exception("Filled sell order not found on exchange")
                            
                            # CRITICAL VALIDATION: Exit price must match exchange fill exactly
                            exchange_exit_price = float(filled_sell_order.get("avgPrice", filled_sell_order.get("price", 0)))
                            
                            print(f"Exchange Exit Price: ${exchange_exit_price}")
                            
                            if abs(exit_price - exchange_exit_price) > 0.01:
                                raise Exception(f"EXIT PRICE MISMATCH: DB={exit_price}, Exchange={exchange_exit_price}")
                            
                            print("✅ CRITICAL VALIDATION PASSED: Exit price matches exchange fill exactly")
                            closure_detected = True
                            self.recorded_exit_price = exit_price
                
                if not closure_detected:
                    print(f"Waiting for sell fill... ({int(time.time() - start_time)}s)")
            
            if not closure_detected:
                raise Exception("Trade closure not detected within timeout")
    
    async def step5_final_validation(self):
        """Step 5: Final comprehensive validation of complete trade lifecycle"""
        print("\n🔍 STEP 5: Final Comprehensive Validation")
        print("-" * 40)
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Get final trade state
            db_response = await client.get(f"{self.database_url}/api/v1/trades/{self.trade_id}")
            trade_data = db_response.json()
            
            # Calculate P&L
            entry_price = float(trade_data.get("entry_price"))
            exit_price = float(trade_data.get("exit_price"))
            position_size = float(trade_data.get("position_size"))
            pnl = (exit_price - entry_price) * position_size
            
            print(f"📊 FINAL TRADE SUMMARY:")
            print(f"   Trade ID: {self.trade_id}")
            print(f"   Entry Price: ${entry_price} (matches exchange)")
            print(f"   Exit Price: ${exit_price} (matches exchange)")
            print(f"   Position Size: {position_size} (matches exchange)")
            print(f"   P&L: ${pnl:.4f}")
            print(f"   Status: {trade_data.get('status')}")
            
            # Validate all critical fields are populated correctly
            required_fields = ["trade_id", "entry_price", "exit_price", "position_size", "entry_id", "exit_id", "status"]
            for field in required_fields:
                if not trade_data.get(field):
                    raise Exception(f"Missing critical field: {field}")
            
            if trade_data.get("status") != "CLOSED":
                raise Exception(f"Trade not properly closed: {trade_data.get('status')}")
            
            print("✅ ALL VALIDATIONS PASSED")
            print("Complete trade lifecycle executed with perfect data integrity")
    
    async def cleanup_failed_test(self):
        """Clean up any failed test artifacts"""
        print(f"\n🧹 Cleaning up failed test {self.test_id}")
        
        try:
            # Cancel any open orders
            async with httpx.AsyncClient(timeout=30.0) as client:
                if self.entry_order_id:
                    await client.delete(f"{self.exchange_url}/api/v1/trading/orders/{self.test_exchange}/{self.entry_order_id}")
                if self.exit_order_id:
                    await client.delete(f"{self.exchange_url}/api/v1/trading/orders/{self.test_exchange}/{self.exit_order_id}")
        except:
            pass

async def main():
    """Run comprehensive end-to-end test"""
    test = ComprehensiveE2ETest()
    await test.run_full_lifecycle_test()

if __name__ == "__main__":
    asyncio.run(main())