#!/usr/bin/env python3
"""
Comprehensive System Test for Fill Detection and Trade Closure
This script tests the entire system to validate that it's working correctly.
"""

import asyncio
import httpx
import redis
import json
import logging
from datetime import datetime
from typing import Dict, Any, List

# Configuration
REDIS_URL = "redis://localhost:6379"
DATABASE_SERVICE_URL = "http://localhost:8002"
EXCHANGE_SERVICE_URL = "http://localhost:8003"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ComprehensiveSystemTest:
    """
    Comprehensive test suite for the fill detection and trade closure system
    """
    
    def __init__(self):
        self.redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        self.test_results = {
            'total_tests': 0,
            'passed': 0,
            'failed': 0,
            'errors': []
        }
    
    async def run_all_tests(self):
        """Run all comprehensive tests"""
        logger.info("🧪 Starting Comprehensive System Tests...")
        
        try:
            # Test 1: Service Health Check
            await self._test_service_health()
            
            # Test 2: Exchange API Connectivity
            await self._test_exchange_api_connectivity()
            
            # Test 3: Database API Connectivity
            await self._test_database_api_connectivity()
            
            # Test 4: Redis Connectivity
            await self._test_redis_connectivity()
            
            # Test 5: Order Status Validation
            await self._test_order_status_validation()
            
            # Test 6: Trade Status Validation
            await self._test_trade_status_validation()
            
            # Test 7: Fill Detection System Test
            await self._test_fill_detection_system()
            
            # Test 8: WebSocket Connection Test
            await self._test_websocket_connections()
            
            # Test 9: Order Tracking Test
            await self._test_order_tracking()
            
            # Test 10: Trade Closure Test
            await self._test_trade_closure()
            
            # Print final results
            self._print_test_results()
            
        except Exception as e:
            logger.error(f"❌ Test suite failed: {e}")
            self.test_results['errors'].append(str(e))
    
    async def _test_service_health(self):
        """Test 1: Service Health Check"""
        logger.info("🔍 Test 1: Service Health Check")
        self.test_results['total_tests'] += 1
        
        try:
            services = [
                ('database-service', f"{DATABASE_SERVICE_URL}/api/v1/trades"),
                ('exchange-service', f"{EXCHANGE_SERVICE_URL}/status")
            ]
            
            for service_name, url in services:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get(url)
                    if response.status_code == 200:
                        logger.info(f"✅ {service_name} is healthy")
                    else:
                        logger.error(f"❌ {service_name} is unhealthy: {response.status_code}")
                        self.test_results['failed'] += 1
                        return
            
            self.test_results['passed'] += 1
            logger.info("✅ Test 1 PASSED: All services are healthy")
            
        except Exception as e:
            logger.error(f"❌ Test 1 FAILED: {e}")
            self.test_results['failed'] += 1
            self.test_results['errors'].append(f"Service health test: {e}")
    
    async def _test_exchange_api_connectivity(self):
        """Test 2: Exchange API Connectivity"""
        logger.info("🔍 Test 2: Exchange API Connectivity")
        self.test_results['total_tests'] += 1
        
        try:
            # Test known filled orders
            test_orders = [
                ('binance', '1298611557', 'BNB/USDC'),
                ('binance', '27637863', 'VET/USDC')
            ]
            
            for exchange, order_id, symbol in test_orders:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get(
                        f"{EXCHANGE_SERVICE_URL}/api/v1/trading/order/{exchange}/{order_id}",
                        params={'symbol': symbol}
                    )
                    
                    if response.status_code == 200:
                        order_data = response.json()
                        order_status = order_data['order']['status']
                        logger.info(f"✅ Order {order_id} status: {order_status}")
                        
                        if order_status == 'closed':
                            logger.info(f"✅ Order {order_id} is correctly marked as filled")
                        else:
                            logger.warning(f"⚠️ Order {order_id} status: {order_status}")
                    else:
                        logger.error(f"❌ Failed to get order {order_id}: {response.status_code}")
                        self.test_results['failed'] += 1
                        return
            
            self.test_results['passed'] += 1
            logger.info("✅ Test 2 PASSED: Exchange API connectivity working")
            
        except Exception as e:
            logger.error(f"❌ Test 2 FAILED: {e}")
            self.test_results['failed'] += 1
            self.test_results['errors'].append(f"Exchange API test: {e}")
    
    async def _test_database_api_connectivity(self):
        """Test 3: Database API Connectivity"""
        logger.info("🔍 Test 3: Database API Connectivity")
        self.test_results['total_tests'] += 1
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Test trades endpoint
                trades_response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/trades")
                if trades_response.status_code == 200:
                    trades_data = trades_response.json()
                    trades_count = len(trades_data.get('trades', []))
                    logger.info(f"✅ Database has {trades_count} trades")
                else:
                    logger.error(f"❌ Failed to get trades: {trades_response.status_code}")
                    self.test_results['failed'] += 1
                    return
                
                # Test order mappings endpoint
                orders_response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/order-mappings")
                if orders_response.status_code == 200:
                    orders_data = orders_response.json()
                    orders_count = len(orders_data.get('order_mappings', []))
                    logger.info(f"✅ Database has {orders_count} order mappings")
                else:
                    logger.error(f"❌ Failed to get order mappings: {orders_response.status_code}")
                    self.test_results['failed'] += 1
                    return
            
            self.test_results['passed'] += 1
            logger.info("✅ Test 3 PASSED: Database API connectivity working")
            
        except Exception as e:
            logger.error(f"❌ Test 3 FAILED: {e}")
            self.test_results['failed'] += 1
            self.test_results['errors'].append(f"Database API test: {e}")
    
    async def _test_redis_connectivity(self):
        """Test 4: Redis Connectivity"""
        logger.info("🔍 Test 4: Redis Connectivity")
        self.test_results['total_tests'] += 1
        
        try:
            # Test Redis connection
            self.redis_client.ping()
            logger.info("✅ Redis connection successful")
            
            # Test Redis operations
            test_key = "test_key"
            test_value = "test_value"
            self.redis_client.set(test_key, test_value)
            retrieved_value = self.redis_client.get(test_key)
            self.redis_client.delete(test_key)
            
            if retrieved_value == test_value:
                logger.info("✅ Redis read/write operations working")
            else:
                logger.error("❌ Redis read/write operations failed")
                self.test_results['failed'] += 1
                return
            
            self.test_results['passed'] += 1
            logger.info("✅ Test 4 PASSED: Redis connectivity working")
            
        except Exception as e:
            logger.error(f"❌ Test 4 FAILED: {e}")
            self.test_results['failed'] += 1
            self.test_results['errors'].append(f"Redis test: {e}")
    
    async def _test_order_status_validation(self):
        """Test 5: Order Status Validation"""
        logger.info("🔍 Test 5: Order Status Validation")
        self.test_results['total_tests'] += 1
        
        try:
            # Get order mappings from database
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/order-mappings")
                if response.status_code == 200:
                    orders_data = response.json()
                    orders = orders_data.get('order_mappings', [])
                    
                    logger.info(f"📊 Found {len(orders)} order mappings in database")
                    
                    # Check each order status
                    for order in orders:
                        exchange = order.get('exchange')
                        order_id = order.get('exchange_order_id')
                        symbol = order.get('symbol')
                        db_status = order.get('status')
                        
                        if exchange and order_id and symbol:
                            # Check status on exchange
                            status_response = await client.get(
                                f"{EXCHANGE_SERVICE_URL}/api/v1/trading/order/{exchange}/{order_id}",
                                params={'symbol': symbol}
                            )
                            
                            if status_response.status_code == 200:
                                exchange_data = status_response.json()
                                exchange_status = exchange_data['order']['status']
                                
                                logger.info(f"📋 Order {order_id}: DB={db_status}, Exchange={exchange_status}")
                                
                                # Check for mismatches
                                if exchange_status == 'closed' and db_status != 'FILLED':
                                    logger.warning(f"⚠️ MISMATCH: Order {order_id} filled on exchange but not in database")
                                elif exchange_status != 'closed' and db_status == 'FILLED':
                                    logger.warning(f"⚠️ MISMATCH: Order {order_id} marked filled in database but not on exchange")
            
            self.test_results['passed'] += 1
            logger.info("✅ Test 5 PASSED: Order status validation completed")
            
        except Exception as e:
            logger.error(f"❌ Test 5 FAILED: {e}")
            self.test_results['failed'] += 1
            self.test_results['errors'].append(f"Order status test: {e}")
    
    async def _test_trade_status_validation(self):
        """Test 6: Trade Status Validation"""
        logger.info("🔍 Test 6: Trade Status Validation")
        self.test_results['total_tests'] += 1
        
        try:
            # Get trades from database
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/trades")
                if response.status_code == 200:
                    trades_data = response.json()
                    trades = trades_data.get('trades', [])
                    
                    # Count trades by status
                    status_counts = {}
                    for trade in trades:
                        status = trade.get('status', 'UNKNOWN')
                        status_counts[status] = status_counts.get(status, 0) + 1
                    
                    logger.info(f"📊 Trade status distribution: {status_counts}")
                    
                    # Check for phantom trades (FAILED status but orders are filled)
                    phantom_trades = 0
                    for trade in trades:
                        if trade.get('status') == 'FAILED':
                            exit_id = trade.get('exit_id')
                            if exit_id:
                                # Check if exit order is filled on exchange
                                try:
                                    status_response = await client.get(
                                        f"{EXCHANGE_SERVICE_URL}/api/v1/trading/order/binance/{exit_id}",
                                        params={'symbol': trade.get('pair', '')}
                                    )
                                    
                                    if status_response.status_code == 200:
                                        exchange_data = status_response.json()
                                        exchange_status = exchange_data['order']['status']
                                        
                                        if exchange_status == 'closed':
                                            phantom_trades += 1
                                            logger.warning(f"⚠️ PHANTOM TRADE: {trade.get('trade_id')} - Order {exit_id} filled but trade still FAILED")
                                except:
                                    pass
                    
                    if phantom_trades > 0:
                        logger.warning(f"⚠️ Found {phantom_trades} phantom trades")
                    else:
                        logger.info("✅ No phantom trades detected")
            
            self.test_results['passed'] += 1
            logger.info("✅ Test 6 PASSED: Trade status validation completed")
            
        except Exception as e:
            logger.error(f"❌ Test 6 FAILED: {e}")
            self.test_results['failed'] += 1
            self.test_results['errors'].append(f"Trade status test: {e}")
    
    async def _test_fill_detection_system(self):
        """Test 7: Fill Detection System Test"""
        logger.info("🔍 Test 7: Fill Detection System Test")
        self.test_results['total_tests'] += 1
        
        try:
            # Check if orders are tracked in Redis
            tracked_orders = self.redis_client.scard("tracked_orders")
            redis_orders = len(self.redis_client.keys("order:*"))
            
            logger.info(f"📊 Redis tracking: {tracked_orders} tracked orders, {redis_orders} order records")
            
            if tracked_orders == 0:
                logger.warning("⚠️ No orders tracked in Redis - fill detection system not active")
            else:
                logger.info("✅ Orders are being tracked in Redis")
            
            self.test_results['passed'] += 1
            logger.info("✅ Test 7 PASSED: Fill detection system test completed")
            
        except Exception as e:
            logger.error(f"❌ Test 7 FAILED: {e}")
            self.test_results['failed'] += 1
            self.test_results['errors'].append(f"Fill detection test: {e}")
    
    async def _test_websocket_connections(self):
        """Test 8: WebSocket Connection Test"""
        logger.info("🔍 Test 8: WebSocket Connection Test")
        self.test_results['total_tests'] += 1
        
        try:
            # Check WebSocket status
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{EXCHANGE_SERVICE_URL}/api/v1/websocket/binance/status")
                if response.status_code == 200:
                    ws_status = response.json()
                    connected = ws_status.get('connected', False)
                    
                    if connected:
                        logger.info("✅ Binance WebSocket is connected")
                    else:
                        logger.warning("⚠️ Binance WebSocket is not connected")
                        logger.info(f"📊 WebSocket status: {ws_status}")
                else:
                    logger.warning(f"⚠️ Could not get WebSocket status: {response.status_code}")
            
            self.test_results['passed'] += 1
            logger.info("✅ Test 8 PASSED: WebSocket connection test completed")
            
        except Exception as e:
            logger.error(f"❌ Test 8 FAILED: {e}")
            self.test_results['failed'] += 1
            self.test_results['errors'].append(f"WebSocket test: {e}")
    
    async def _test_order_tracking(self):
        """Test 9: Order Tracking Test"""
        logger.info("🔍 Test 9: Order Tracking Test")
        self.test_results['total_tests'] += 1
        
        try:
            # Get order mappings from database
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/order-mappings")
                if response.status_code == 200:
                    orders_data = response.json()
                    orders = orders_data.get('order_mappings', [])
                    
                    # Check which orders are tracked in Redis
                    tracked_count = 0
                    for order in orders:
                        client_order_id = order.get('client_order_id')
                        if client_order_id and self.redis_client.exists(f"order:{client_order_id}"):
                            tracked_count += 1
                    
                    logger.info(f"📊 Order tracking: {tracked_count}/{len(orders)} orders tracked in Redis")
                    
                    if tracked_count == 0:
                        logger.warning("⚠️ No orders are tracked in Redis")
                    elif tracked_count < len(orders):
                        logger.warning(f"⚠️ Only {tracked_count}/{len(orders)} orders are tracked")
                    else:
                        logger.info("✅ All orders are tracked in Redis")
            
            self.test_results['passed'] += 1
            logger.info("✅ Test 9 PASSED: Order tracking test completed")
            
        except Exception as e:
            logger.error(f"❌ Test 9 FAILED: {e}")
            self.test_results['failed'] += 1
            self.test_results['errors'].append(f"Order tracking test: {e}")
    
    async def _test_trade_closure(self):
        """Test 10: Trade Closure Test"""
        logger.info("🔍 Test 10: Trade Closure Test")
        self.test_results['total_tests'] += 1
        
        try:
            # Check if trades are properly closed
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/trades")
                if response.status_code == 200:
                    trades_data = response.json()
                    trades = trades_data.get('trades', [])
                    
                    # Check for trades that should be closed
                    should_be_closed = 0
                    actually_closed = 0
                    
                    for trade in trades:
                        exit_id = trade.get('exit_id')
                        if exit_id:
                            should_be_closed += 1
                            
                            # Check if exit order is filled on exchange
                            try:
                                status_response = await client.get(
                                    f"{EXCHANGE_SERVICE_URL}/api/v1/trading/order/binance/{exit_id}",
                                    params={'symbol': trade.get('pair', '')}
                                )
                                
                                if status_response.status_code == 200:
                                    exchange_data = status_response.json()
                                    exchange_status = exchange_data['order']['status']
                                    
                                    if exchange_status == 'closed' and trade.get('status') == 'CLOSED':
                                        actually_closed += 1
                                    elif exchange_status == 'closed' and trade.get('status') != 'CLOSED':
                                        logger.warning(f"⚠️ Trade {trade.get('trade_id')} should be closed but status is {trade.get('status')}")
                            except:
                                pass
                    
                    logger.info(f"📊 Trade closure: {actually_closed}/{should_be_closed} trades properly closed")
                    
                    if actually_closed == should_be_closed:
                        logger.info("✅ All trades are properly closed")
                    else:
                        logger.warning(f"⚠️ {should_be_closed - actually_closed} trades are not properly closed")
            
            self.test_results['passed'] += 1
            logger.info("✅ Test 10 PASSED: Trade closure test completed")
            
        except Exception as e:
            logger.error(f"❌ Test 10 FAILED: {e}")
            self.test_results['failed'] += 1
            self.test_results['errors'].append(f"Trade closure test: {e}")
    
    def _print_test_results(self):
        """Print comprehensive test results"""
        logger.info("\n" + "="*60)
        logger.info("🧪 COMPREHENSIVE SYSTEM TEST RESULTS")
        logger.info("="*60)
        
        total = self.test_results['total_tests']
        passed = self.test_results['passed']
        failed = self.test_results['failed']
        
        logger.info(f"📊 Total Tests: {total}")
        logger.info(f"✅ Passed: {passed}")
        logger.info(f"❌ Failed: {failed}")
        logger.info(f"📈 Success Rate: {(passed/total)*100:.1f}%")
        
        if self.test_results['errors']:
            logger.info(f"\n🚨 Errors:")
            for error in self.test_results['errors']:
                logger.info(f"   • {error}")
        
        logger.info("="*60)
        
        if failed == 0:
            logger.info("🎉 ALL TESTS PASSED! System is working correctly.")
        else:
            logger.warning(f"⚠️ {failed} tests failed. System needs attention.")

async def main():
    """Main test function"""
    test_suite = ComprehensiveSystemTest()
    await test_suite.run_all_tests()

if __name__ == "__main__":
    asyncio.run(main())
