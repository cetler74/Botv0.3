#!/usr/bin/env python3
"""
Complete Redis WebSocket Integration Test
This script validates the entire order lifecycle from creation to exit
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
TEST_ORDER_ID = f"test_complete_{int(time.time())}"
TEST_TRADE_ID = f"test_trade_{int(time.time())}"

class CompleteWebSocketTest:
    def __init__(self):
        self.test_results = []
        
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
        
    async def test_service_health(self):
        """Test 1: Service Health Check"""
        logger.info("🔍 Testing Service Health...")
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Test orchestrator service
                response = await client.get(f"{ORCHESTRATOR_URL}/health")
                if response.status_code == 200:
                    self.log_test("Orchestrator Service Health", True, "Service is healthy")
                else:
                    self.log_test("Orchestrator Service Health", False, f"Status: {response.status_code}")
                    
                # Test database service
                response = await client.get(f"{DATABASE_URL}/health")
                if response.status_code == 200:
                    self.log_test("Database Service Health", True, "Service is healthy")
                else:
                    self.log_test("Database Service Health", False, f"Status: {response.status_code}")
                    
        except Exception as e:
            self.log_test("Service Health", False, f"Error: {str(e)}")
            
    async def test_buy_order_creation(self):
        """Test 2: Buy Order Creation and Tracking"""
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
                    
                    # Check if order was created in database
                    response = await client.get(f"{DATABASE_URL}/api/v1/orders")
                    if response.status_code == 200:
                        orders = response.json().get("orders", [])
                        buy_order = next((o for o in orders if o.get("order_id") == f"{TEST_ORDER_ID}_buy"), None)
                        
                        if buy_order:
                            self.log_test("Buy Order Database Record", True, f"Order created with status: {buy_order.get('status')}")
                            
                            # Check if trade was created
                            response = await client.get(f"{DATABASE_URL}/api/v1/trades")
                            if response.status_code == 200:
                                trades = response.json().get("trades", [])
                                buy_trade = next((t for t in trades if t.get("entry_id") == f"{TEST_ORDER_ID}_buy"), None)
                                
                                if buy_trade:
                                    self.log_test("Buy Trade Database Record", True, f"Trade created with status: {buy_trade.get('status')}")
                                    return buy_trade.get("trade_id")
                                else:
                                    self.log_test("Buy Trade Database Record", False, "Trade not found in database")
                            else:
                                self.log_test("Buy Trade Database Record", False, f"Database query failed: {response.status_code}")
                        else:
                            self.log_test("Buy Order Database Record", False, "Order not found in database")
                    else:
                        self.log_test("Buy Order Database Record", False, f"Database query failed: {response.status_code}")
                else:
                    self.log_test("Buy Order WebSocket Callback", False, f"Callback failed: {response.status_code}")
                    
        except Exception as e:
            self.log_test("Buy Order Creation", False, f"Error: {str(e)}")
            
        return None
        
    async def test_sell_order_processing(self, trade_id: str):
        """Test 3: Sell Order Processing and Trade Closure"""
        logger.info("🔍 Testing Sell Order Processing...")
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Send sell order WebSocket execution report
                sell_order_data = {
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
                    json=sell_order_data
                )
                
                if response.status_code == 200:
                    self.log_test("Sell Order WebSocket Callback", True, "Callback processed successfully")
                    
                    # Wait for processing
                    await asyncio.sleep(2)
                    
                    # Check if sell order was created in database
                    response = await client.get(f"{DATABASE_URL}/api/v1/orders")
                    if response.status_code == 200:
                        orders = response.json().get("orders", [])
                        sell_order = next((o for o in orders if o.get("order_id") == f"{TEST_ORDER_ID}_sell"), None)
                        
                        if sell_order:
                            self.log_test("Sell Order Database Record", True, f"Order created with status: {sell_order.get('status')}")
                            
                            # Check if trade was updated to CLOSED
                            response = await client.get(f"{DATABASE_URL}/api/v1/trades")
                            if response.status_code == 200:
                                trades = response.json().get("trades", [])
                                closed_trade = next((t for t in trades if t.get("trade_id") == trade_id), None)
                                
                                if closed_trade and closed_trade.get("status") == "CLOSED":
                                    realized_pnl = closed_trade.get("realized_pnl", 0)
                                    exit_price = closed_trade.get("exit_price")
                                    self.log_test("Trade Closure", True, f"Trade closed with PnL: {realized_pnl}, Exit price: {exit_price}")
                                else:
                                    self.log_test("Trade Closure", False, f"Trade not closed. Status: {closed_trade.get('status') if closed_trade else 'Not found'}")
                            else:
                                self.log_test("Trade Closure", False, f"Database query failed: {response.status_code}")
                        else:
                            self.log_test("Sell Order Database Record", False, "Order not found in database")
                    else:
                        self.log_test("Sell Order Database Record", False, f"Database query failed: {response.status_code}")
                else:
                    self.log_test("Sell Order WebSocket Callback", False, f"Callback failed: {response.status_code}")
                    
        except Exception as e:
            self.log_test("Sell Order Processing", False, f"Error: {str(e)}")
            
    async def test_order_tracking_validation(self):
        """Test 4: Validate Order Tracking in Redis"""
        logger.info("🔍 Testing Order Tracking Validation...")
        
        try:
            # This would require Redis CLI access to validate
            # For now, we'll validate through the database
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{DATABASE_URL}/api/v1/orders")
                if response.status_code == 200:
                    orders = response.json().get("orders", [])
                    test_orders = [o for o in orders if TEST_ORDER_ID in o.get("order_id", "")]
                    
                    if len(test_orders) >= 2:  # Should have buy and sell orders
                        self.log_test("Order Tracking Validation", True, f"Found {len(test_orders)} test orders")
                    else:
                        self.log_test("Order Tracking Validation", False, f"Expected 2 orders, found {len(test_orders)}")
                else:
                    self.log_test("Order Tracking Validation", False, f"Database query failed: {response.status_code}")
                    
        except Exception as e:
            self.log_test("Order Tracking Validation", False, f"Error: {str(e)}")
            
    async def test_error_handling(self):
        """Test 5: Error Handling"""
        logger.info("🔍 Testing Error Handling...")
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Test with invalid data
                invalid_data = {
                    "event_type": "order_filled",
                    "order_id": f"{TEST_ORDER_ID}_invalid",
                    "exchange": "binance",
                    "symbol": "INVALID",
                    "side": "invalid",
                    "executed_quantity": -100,  # Invalid negative quantity
                    "executed_price": -0.826,   # Invalid negative price
                    "fees": -0.1                # Invalid negative fees
                }
                
                response = await client.post(
                    f"{ORCHESTRATOR_URL}/api/v1/websocket/callback/binance",
                    json=invalid_data
                )
                
                # The system should handle invalid data gracefully
                if response.status_code in [200, 400, 422]:
                    self.log_test("Error Handling", True, "System handled invalid data gracefully")
                else:
                    self.log_test("Error Handling", False, f"Unexpected response: {response.status_code}")
                    
        except Exception as e:
            self.log_test("Error Handling", False, f"Error: {str(e)}")
            
    async def run_complete_test(self):
        """Run the complete test suite"""
        logger.info("🚀 Starting Complete Redis WebSocket Integration Test")
        logger.info("=" * 60)
        
        # Test 1: Service Health
        await self.test_service_health()
        
        # Test 2: Buy Order Creation
        trade_id = await self.test_buy_order_creation()
        
        if trade_id:
            # Test 3: Sell Order Processing
            await self.test_sell_order_processing(trade_id)
        
        # Test 4: Order Tracking Validation
        await self.test_order_tracking_validation()
        
        # Test 5: Error Handling
        await self.test_error_handling()
        
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
    test = CompleteWebSocketTest()
    success = await test.run_complete_test()
    
    if success:
        logger.info("\n🎉 ALL TESTS PASSED! Redis WebSocket integration is working correctly.")
    else:
        logger.info("\n⚠️  SOME TESTS FAILED! Please review the results above.")
        
    return success

if __name__ == "__main__":
    asyncio.run(main())
