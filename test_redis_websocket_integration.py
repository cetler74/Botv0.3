#!/usr/bin/env python3
"""
Test Redis WebSocket Integration
Verifies the complete flow from WebSocket events to Redis-based order tracking
"""

import asyncio
import json
import logging
import time
import httpx
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Service URLs
EXCHANGE_SERVICE_URL = "http://localhost:8003"
ORCHESTRATOR_SERVICE_URL = "http://localhost:8005"
DATABASE_SERVICE_URL = "http://localhost:8002"

class RedisWebSocketIntegrationTester:
    """Test the complete Redis WebSocket integration flow"""
    
    def __init__(self):
        self.test_results = {}
        
    async def test_exchange_service_health(self):
        """Test exchange service health and WebSocket status"""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Test exchange service health
                response = await client.get(f"{EXCHANGE_SERVICE_URL}/health")
                if response.status_code == 200:
                    logger.info("✅ Exchange service is healthy")
                    self.test_results['exchange_health'] = True
                else:
                    logger.error(f"❌ Exchange service unhealthy: {response.status_code}")
                    self.test_results['exchange_health'] = False
                    return False
                
                # Test Binance WebSocket status
                response = await client.get(f"{EXCHANGE_SERVICE_URL}/api/v1/websocket/binance/status")
                if response.status_code == 200:
                    status_data = response.json()
                    logger.info(f"📊 Binance WebSocket Status: {status_data}")
                    self.test_results['binance_websocket_status'] = status_data
                    
                    if status_data.get('enabled') and status_data.get('connected'):
                        logger.info("✅ Binance WebSocket is connected and enabled")
                        return True
                    else:
                        logger.warning("⚠️ Binance WebSocket not fully operational")
                        return False
                else:
                    logger.error(f"❌ Failed to get Binance WebSocket status: {response.status_code}")
                    return False
                    
        except Exception as e:
            logger.error(f"❌ Error testing exchange service: {e}")
            return False
    
    async def test_orchestrator_service_health(self):
        """Test orchestrator service health"""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{ORCHESTRATOR_SERVICE_URL}/health")
                if response.status_code == 200:
                    logger.info("✅ Orchestrator service is healthy")
                    self.test_results['orchestrator_health'] = True
                    return True
                else:
                    logger.error(f"❌ Orchestrator service unhealthy: {response.status_code}")
                    self.test_results['orchestrator_health'] = False
                    return False
                    
        except Exception as e:
            logger.error(f"❌ Error testing orchestrator service: {e}")
            return False
    
    async def test_redis_connection(self):
        """Test Redis connection through orchestrator"""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Test Redis health through orchestrator
                response = await client.get(f"{ORCHESTRATOR_SERVICE_URL}/api/v1/system/health")
                if response.status_code == 200:
                    health_data = response.json()
                    logger.info(f"📊 System Health: {health_data}")
                    
                    redis_healthy = health_data.get('redis', {}).get('healthy', False)
                    if redis_healthy:
                        logger.info("✅ Redis connection is healthy")
                        self.test_results['redis_health'] = True
                        return True
                    else:
                        logger.error("❌ Redis connection unhealthy")
                        self.test_results['redis_health'] = False
                        return False
                else:
                    logger.error(f"❌ Failed to get system health: {response.status_code}")
                    return False
                    
        except Exception as e:
            logger.error(f"❌ Error testing Redis connection: {e}")
            return False
    
    async def test_websocket_callback_endpoint(self):
        """Test the WebSocket callback endpoint"""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Test with a mock order created event
                test_event = {
                    "event_type": "order_created",
                    "exchange": "binance",
                    "order_id": "test_order_123",
                    "client_order_id": "test_client_123",
                    "symbol": "BTCUSDC",
                    "side": "buy",
                    "order_type": "limit",
                    "quantity": 0.001,
                    "price": 50000.0,
                    "status": "new",
                    "timestamp": int(time.time() * 1000)
                }
                
                response = await client.post(
                    f"{ORCHESTRATOR_SERVICE_URL}/api/v1/websocket/callback/binance",
                    json=test_event
                )
                
                if response.status_code == 200:
                    result = response.json()
                    logger.info(f"✅ WebSocket callback test successful: {result}")
                    self.test_results['websocket_callback'] = True
                    return True
                else:
                    logger.error(f"❌ WebSocket callback test failed: {response.status_code}")
                    self.test_results['websocket_callback'] = False
                    return False
                    
        except Exception as e:
            logger.error(f"❌ Error testing WebSocket callback: {e}")
            return False
    
    async def test_redis_order_tracking(self):
        """Test Redis order tracking functionality"""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Test order registration
                test_order = {
                    "symbol": "BTCUSDC",
                    "side": "buy",
                    "order_type": "limit",
                    "amount": 0.001,
                    "price": 50000.0,
                    "trade_id": "test_trade_123"
                }
                
                response = await client.post(
                    f"{ORCHESTRATOR_SERVICE_URL}/api/v1/orders/register",
                    json=test_order
                )
                
                if response.status_code == 200:
                    logger.info("✅ Order registration test successful")
                    self.test_results['order_registration'] = True
                    return True
                else:
                    logger.error(f"❌ Order registration test failed: {response.status_code}")
                    self.test_results['order_registration'] = False
                    return False
                    
        except Exception as e:
            logger.error(f"❌ Error testing order tracking: {e}")
            return False
    
    async def test_complete_integration_flow(self):
        """Test the complete integration flow"""
        logger.info("🚀 Starting complete Redis WebSocket integration test")
        
        # Test all components
        tests = [
            ("Exchange Service Health", self.test_exchange_service_health),
            ("Orchestrator Service Health", self.test_orchestrator_service_health),
            ("Redis Connection", self.test_redis_connection),
            ("WebSocket Callback Endpoint", self.test_websocket_callback_endpoint),
            ("Redis Order Tracking", self.test_redis_order_tracking)
        ]
        
        results = {}
        for test_name, test_func in tests:
            logger.info(f"🧪 Running test: {test_name}")
            try:
                result = await test_func()
                results[test_name] = result
                if result:
                    logger.info(f"✅ {test_name}: PASSED")
                else:
                    logger.error(f"❌ {test_name}: FAILED")
            except Exception as e:
                logger.error(f"❌ {test_name}: ERROR - {e}")
                results[test_name] = False
        
        # Summary
        passed_tests = sum(1 for result in results.values() if result)
        total_tests = len(results)
        
        logger.info(f"\n📊 TEST SUMMARY:")
        logger.info(f"Passed: {passed_tests}/{total_tests}")
        logger.info(f"Success Rate: {(passed_tests/total_tests)*100:.1f}%")
        
        for test_name, result in results.items():
            status = "✅ PASS" if result else "❌ FAIL"
            logger.info(f"  {test_name}: {status}")
        
        self.test_results['integration_summary'] = {
            'passed': passed_tests,
            'total': total_tests,
            'success_rate': (passed_tests/total_tests)*100,
            'details': results
        }
        
        return passed_tests == total_tests
    
    def generate_report(self):
        """Generate a detailed test report"""
        report = {
            "timestamp": datetime.utcnow().isoformat(),
            "test_name": "Redis WebSocket Integration Test",
            "results": self.test_results
        }
        
        # Save report
        with open("redis_websocket_integration_test_report.json", "w") as f:
            json.dump(report, f, indent=2)
        
        logger.info("📄 Test report saved to: redis_websocket_integration_test_report.json")
        return report

async def main():
    """Main test function"""
    logger.info("🔧 Redis WebSocket Integration Test Suite")
    logger.info("=" * 50)
    
    tester = RedisWebSocketIntegrationTester()
    
    try:
        success = await tester.test_complete_integration_flow()
        
        if success:
            logger.info("🎉 ALL TESTS PASSED - Redis WebSocket integration is working correctly!")
        else:
            logger.error("💥 SOME TESTS FAILED - Check the logs above for details")
        
        # Generate report
        report = tester.generate_report()
        
        return success
        
    except Exception as e:
        logger.error(f"❌ Test suite failed with error: {e}")
        return False

if __name__ == "__main__":
    asyncio.run(main())
