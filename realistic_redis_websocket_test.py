#!/usr/bin/env python3
"""
Realistic Redis WebSocket Integration Test
This script simulates the real order lifecycle with proper trade linking
"""

import asyncio
import httpx
import time
import logging
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Configuration
ORCHESTRATOR_URL = "http://localhost:8005"
DATABASE_URL = "http://localhost:8002"
TEST_ORDER_ID = f"test_realistic_{int(time.time())}"

class RealisticWebSocketTest:
    def __init__(self):
        self.test_results = []
        self.buy_trade_id = None
        
    def log_test(self, test_name: str, success: bool, details: str = ""):
        """Log test result"""
        status = "✅ PASS" if success else "❌ FAIL"
        logger.info(f"{status} - {test_name}: {details}")
        self.test_results.append({
            "test": test_name,
            "success": success,
            "details": details,
            "timestamp": datetime.now().isoformat()
        })
        
    async def test_buy_order_creation(self):
        """Test 1: Buy Order Creation"""
        logger.info("🔍 Testing Buy Order Creation...")
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Send buy order WebSocket execution report
                buy_order_data = {
                    "event_type": "order_filled",
                    "order_id": f"{TEST_ORDER_ID}_buy",
                    "exchange": "binance",
                    "symbol": "ADAUSDC",
                    "side": "buy",
                    "executed_quantity": 100.0,
                    "executed_price": 0.826,
                    "fees": 0.1
                }
                
                response = await client.post(
                    f"{ORCHESTRATOR_URL}/api/v1/websocket/callback/binance",
                    json=buy_order_data
                )
                
                if response.status_code == 200:
                    self.log_test("Buy Order WebSocket Callback", True, "Callback processed successfully")
                    
                    # Wait for processing
                    await asyncio.sleep(2)
                    
                    # Get the created trade_id
                    response = await client.get(f"{DATABASE_URL}/api/v1/trades")
                    if response.status_code == 200:
                        trades = response.json().get("trades", [])
                        buy_trade = next((t for t in trades if t.get("entry_id") == f"{TEST_ORDER_ID}_buy"), None)
                        
                        if buy_trade:
                            self.buy_trade_id = buy_trade.get("trade_id")
                            self.log_test("Buy Trade Creation", True, f"Trade created with ID: {self.buy_trade_id}")
                            return True
                        else:
                            self.log_test("Buy Trade Creation", False, "Trade not found")
                    else:
                        self.log_test("Buy Trade Creation", False, f"Database query failed: {response.status_code}")
                else:
                    self.log_test("Buy Order WebSocket Callback", False, f"Callback failed: {response.status_code}")
                    
        except Exception as e:
            self.log_test("Buy Order Creation", False, f"Error: {str(e)}")
            
        return False
        
    async def test_sell_order_with_trade_link(self):
        """Test 2: Sell Order with Trade Link"""
        logger.info("🔍 Testing Sell Order with Trade Link...")
        
        if not self.buy_trade_id:
            self.log_test("Sell Order with Trade Link", False, "No buy trade ID available")
            return False
            
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # First, create a sell order record in the database with the correct trade_id
                sell_order_data = {
                    "order_id": f"{TEST_ORDER_ID}_sell",
                    "trade_id": self.buy_trade_id,  # Link to the buy trade
                    "exchange": "binance",
                    "symbol": "ADA/USDC",
                    "order_type": "limit",
                    "side": "sell",
                    "amount": 100.0,
                    "price": 0.828,
                    "filled_amount": 0.0,
                    "filled_price": 0.0,
                    "status": "PENDING",
                    "fees": 0.0,
                    "fee_rate": 0.0,
                    "exchange_order_id": f"{TEST_ORDER_ID}_sell",
                    "client_order_id": "",
                    "error_message": "Test sell order"
                }
                
                # Create the sell order record
                response = await client.post(f"{DATABASE_URL}/api/v1/orders", json=sell_order_data)
                if response.status_code == 200:
                    self.log_test("Sell Order Database Creation", True, "Sell order created in database")
                    
                    # Now send the WebSocket execution report
                    websocket_data = {
                        "event_type": "order_filled",
                        "order_id": f"{TEST_ORDER_ID}_sell",
                        "exchange": "binance",
                        "symbol": "ADAUSDC",
                        "side": "sell",
                        "executed_quantity": 100.0,
                        "executed_price": 0.828,
                        "fees": 0.1
                    }
                    
                    response = await client.post(
                        f"{ORCHESTRATOR_URL}/api/v1/websocket/callback/binance",
                        json=websocket_data
                    )
                    
                    if response.status_code == 200:
                        self.log_test("Sell Order WebSocket Callback", True, "Callback processed successfully")
                        
                        # Wait for processing
                        await asyncio.sleep(2)
                        
                        # Check if trade was closed
                        response = await client.get(f"{DATABASE_URL}/api/v1/trades")
                        if response.status_code == 200:
                            trades = response.json().get("trades", [])
                            closed_trade = next((t for t in trades if t.get("trade_id") == self.buy_trade_id), None)
                            
                            if closed_trade and closed_trade.get("status") == "CLOSED":
                                realized_pnl = closed_trade.get("realized_pnl", 0)
                                exit_price = closed_trade.get("exit_price")
                                self.log_test("Trade Closure", True, f"Trade closed with PnL: {realized_pnl}, Exit price: {exit_price}")
                                return True
                            else:
                                self.log_test("Trade Closure", False, f"Trade not closed. Status: {closed_trade.get('status') if closed_trade else 'Not found'}")
                        else:
                            self.log_test("Trade Closure", False, f"Database query failed: {response.status_code}")
                    else:
                        self.log_test("Sell Order WebSocket Callback", False, f"Callback failed: {response.status_code}")
                else:
                    self.log_test("Sell Order Database Creation", False, f"Failed to create sell order: {response.status_code}")
                    
        except Exception as e:
            self.log_test("Sell Order with Trade Link", False, f"Error: {str(e)}")
            
        return False
        
    async def test_order_status_validation(self):
        """Test 3: Order Status Validation"""
        logger.info("🔍 Testing Order Status Validation...")
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{DATABASE_URL}/api/v1/orders")
                if response.status_code == 200:
                    orders = response.json().get("orders", [])
                    test_orders = [o for o in orders if TEST_ORDER_ID in o.get("order_id", "")]
                    
                    buy_order = next((o for o in test_orders if o.get("side") == "buy"), None)
                    sell_order = next((o for o in test_orders if o.get("side") == "sell"), None)
                    
                    if buy_order and sell_order:
                        if buy_order.get("status") == "FILLED" and sell_order.get("status") == "FILLED":
                            self.log_test("Order Status Validation", True, "Both orders are FILLED")
                            return True
                        else:
                            self.log_test("Order Status Validation", False, f"Buy: {buy_order.get('status')}, Sell: {sell_order.get('status')}")
                    else:
                        self.log_test("Order Status Validation", False, "Missing buy or sell order")
                else:
                    self.log_test("Order Status Validation", False, f"Database query failed: {response.status_code}")
                    
        except Exception as e:
            self.log_test("Order Status Validation", False, f"Error: {str(e)}")
            
        return False
        
    async def run_realistic_test(self):
        """Run the realistic test suite"""
        logger.info("🚀 Starting Realistic Redis WebSocket Integration Test")
        logger.info("=" * 60)
        
        # Test 1: Buy Order Creation
        if await self.test_buy_order_creation():
            # Test 2: Sell Order with Trade Link
            await self.test_sell_order_with_trade_link()
        
        # Test 3: Order Status Validation
        await self.test_order_status_validation()
        
        # Summary
        logger.info("=" * 60)
        logger.info("📊 Test Summary")
        logger.info("=" * 60)
        
        passed = sum(1 for result in self.test_results if result["success"])
        total = len(self.test_results)
        
        logger.info(f"Total Tests: {total}")
        logger.info(f"Passed: {passed}")
        logger.info(f"Failed: {total - passed}")
        logger.info(f"Success Rate: {(passed/total)*100:.1f}%")
        
        # Detailed results
        logger.info("\n📋 Detailed Results:")
        for result in self.test_results:
            status = "✅ PASS" if result["success"] else "❌ FAIL"
            logger.info(f"{status} - {result['test']}: {result['details']}")
            
        return passed == total

async def main():
    """Main test runner"""
    test = RealisticWebSocketTest()
    success = await test.run_realistic_test()
    
    if success:
        logger.info("\n🎉 ALL TESTS PASSED! Realistic Redis WebSocket integration is working correctly.")
    else:
        logger.info("\n⚠️  SOME TESTS FAILED! Please review the results above.")
        
    return success

if __name__ == "__main__":
    asyncio.run(main())
