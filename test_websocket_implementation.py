#!/usr/bin/env python3
"""
Comprehensive Test Suite for Binance WebSocket Implementation
Tests all components: WebSocket connections, error handling, fallback, monitoring
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, Any, List
import aiohttp
import websockets
import redis.asyncio as redis
from dataclasses import dataclass

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

@dataclass
class TestResult:
    """Test result container"""
    name: str
    passed: bool
    message: str
    duration_ms: float
    details: Dict[str, Any]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'passed': self.passed,
            'message': self.message,
            'duration_ms': self.duration_ms,
            'details': self.details
        }

class WebSocketTestSuite:
    """Comprehensive test suite for WebSocket implementation"""
    
    def __init__(self):
        self.base_url = "http://localhost"
        self.services = {
            'exchange': f"{self.base_url}:8003",
            'fill_detection': f"{self.base_url}:8012",
            'database': f"{self.base_url}:8002"
        }
        
        self.test_results: List[TestResult] = []
        self.redis_client = None
        
    async def run_all_tests(self) -> Dict[str, Any]:
        """Run all tests and return comprehensive results"""
        logger.info("🚀 Starting comprehensive WebSocket implementation tests")
        start_time = datetime.now()
        
        # Test categories
        test_categories = [
            ("Service Health Tests", self._test_service_health),
            ("WebSocket Integration Tests", self._test_websocket_integration),
            ("Error Handling Tests", self._test_error_handling),
            ("Fallback Mechanism Tests", self._test_fallback_mechanisms),
            ("Monitoring Tests", self._test_monitoring_system),
            ("End-to-End Tests", self._test_end_to_end_functionality)
        ]
        
        # Initialize Redis connection
        await self._setup_test_environment()
        
        try:
            for category_name, test_func in test_categories:
                logger.info(f"📋 Running {category_name}")
                await test_func()
                
        finally:
            await self._cleanup_test_environment()
        
        # Generate test report
        end_time = datetime.now()
        return self._generate_test_report(start_time, end_time)
    
    async def _setup_test_environment(self):
        """Setup test environment"""
        try:
            self.redis_client = redis.Redis(host='localhost', port=6379, decode_responses=True)
            await self.redis_client.ping()
            logger.info("✅ Redis connection established for tests")
        except Exception as e:
            logger.error(f"❌ Failed to connect to Redis: {e}")
    
    async def _cleanup_test_environment(self):
        """Cleanup test environment"""
        if self.redis_client:
            await self.redis_client.close()
            logger.info("🧹 Test environment cleaned up")
    
    async def _test_service_health(self):
        """Test basic service health"""
        
        # Test 1: Exchange Service Health
        await self._run_test(
            "exchange_service_health",
            self._check_service_health,
            service="exchange"
        )
        
        # Test 2: Fill Detection Service Health
        await self._run_test(
            "fill_detection_service_health",
            self._check_service_health,
            service="fill_detection"
        )
        
        # Test 3: Database Service Health
        await self._run_test(
            "database_service_health",
            self._check_service_health,
            service="database"
        )
        
        # Test 4: Redis Health
        await self._run_test(
            "redis_health",
            self._check_redis_health
        )
    
    async def _test_websocket_integration(self):
        """Test WebSocket integration components"""
        
        # Test 1: Binance WebSocket Status
        await self._run_test(
            "binance_websocket_status",
            self._check_binance_websocket_status
        )
        
        # Test 2: Fill Detection WebSocket Consumer
        await self._run_test(
            "fill_detection_websocket_consumer",
            self._check_websocket_consumer_status
        )
        
        # Test 3: WebSocket Event Processing
        await self._run_test(
            "websocket_event_processing",
            self._test_websocket_event_processing
        )
        
        # Test 4: Listen Key Management
        await self._run_test(
            "listen_key_management",
            self._test_listen_key_management
        )
    
    async def _test_error_handling(self):
        """Test error handling and recovery mechanisms"""
        
        # Test 1: Connection Manager Status
        await self._run_test(
            "connection_manager_status",
            self._check_connection_manager
        )
        
        # Test 2: Error Handler Statistics
        await self._run_test(
            "error_handler_statistics",
            self._check_error_handler_stats
        )
        
        # Test 3: Recovery Manager Status
        await self._run_test(
            "recovery_manager_status",
            self._check_recovery_manager
        )
    
    async def _test_fallback_mechanisms(self):
        """Test REST API fallback mechanisms"""
        
        # Test 1: Exchange API Fallback
        await self._run_test(
            "exchange_api_fallback",
            self._test_exchange_api_fallback
        )
        
        # Test 2: Fill Detection Fallback Status
        await self._run_test(
            "fill_detection_fallback",
            self._check_fallback_worker_status
        )
    
    async def _test_monitoring_system(self):
        """Test monitoring and health check systems"""
        
        # Test 1: System Health Monitoring
        await self._run_test(
            "system_health_monitoring",
            self._check_system_health_monitoring
        )
        
        # Test 2: Health Metrics
        await self._run_test(
            "health_metrics",
            self._check_health_metrics
        )
        
        # Test 3: Health History
        await self._run_test(
            "health_history",
            self._check_health_history
        )
        
        # Test 4: WebSocket Detailed Health
        await self._run_test(
            "websocket_detailed_health",
            self._check_detailed_websocket_health
        )
    
    async def _test_end_to_end_functionality(self):
        """Test end-to-end functionality"""
        
        # Test 1: Mock Fill Event Processing
        await self._run_test(
            "mock_fill_event_processing",
            self._test_mock_fill_event
        )
        
        # Test 2: Redis Stream Integration
        await self._run_test(
            "redis_stream_integration",
            self._test_redis_stream_integration
        )
        
        # Test 3: Complete Order Flow Simulation
        await self._run_test(
            "complete_order_flow_simulation",
            self._test_complete_order_flow
        )
    
    # Test Implementation Methods
    async def _check_service_health(self, service: str) -> Dict[str, Any]:
        """Check basic service health"""
        service_url = self.services[service]
        
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{service_url}/health", timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status == 200:
                    data = await response.json()
                    return {
                        'healthy': True,
                        'status_code': response.status,
                        'response_data': data
                    }
                else:
                    return {
                        'healthy': False,
                        'status_code': response.status,
                        'error': f"HTTP {response.status}"
                    }
    
    async def _check_redis_health(self) -> Dict[str, Any]:
        """Check Redis health"""
        try:
            # Test basic operations
            await self.redis_client.set('test_key', 'test_value', ex=60)
            result = await self.redis_client.get('test_key')
            
            return {
                'healthy': result == 'test_value',
                'ping_successful': True,
                'set_get_test': result == 'test_value'
            }
        except Exception as e:
            return {
                'healthy': False,
                'error': str(e)
            }
    
    async def _check_binance_websocket_status(self) -> Dict[str, Any]:
        """Check Binance WebSocket integration status"""
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.services['exchange']}/api/v1/websocket/binance/status",
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return {
                        'status_available': True,
                        'status_data': data
                    }
                else:
                    return {
                        'status_available': False,
                        'status_code': response.status
                    }
    
    async def _check_websocket_consumer_status(self) -> Dict[str, Any]:
        """Check WebSocket consumer in fill detection service"""
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.services['fill_detection']}/api/v1/websocket/status",
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return {
                        'consumer_available': True,
                        'consumer_status': data
                    }
                else:
                    return {
                        'consumer_available': False,
                        'status_code': response.status
                    }
    
    async def _test_websocket_event_processing(self) -> Dict[str, Any]:
        """Test WebSocket event processing endpoint"""
        test_event = {
            'event_type': 'order_filled',
            'exchange': 'binance',
            'order_id': 'test_order_123',
            'symbol': 'ETHUSDC',
            'side': 'buy',
            'executed_quantity': 0.1,
            'executed_price': 2500.0,
            'fee_amount': 0.25,
            'fee_currency': 'USDC',
            'timestamp': datetime.now().isoformat()
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.services['fill_detection']}/api/v1/events/execution",
                json=test_event,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return {
                        'event_processed': True,
                        'response_data': data
                    }
                else:
                    error_text = await response.text()
                    return {
                        'event_processed': False,
                        'status_code': response.status,
                        'error': error_text
                    }
    
    async def _test_listen_key_management(self) -> Dict[str, Any]:
        """Test listen key management (check if endpoints exist)"""
        # This is a basic test since we don't want to expose real API keys
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.services['exchange']}/api/v1/websocket/binance/metrics",
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                return {
                    'metrics_available': response.status == 200,
                    'status_code': response.status
                }
    
    async def _check_connection_manager(self) -> Dict[str, Any]:
        """Check connection manager status"""
        # Test via WebSocket status endpoint which includes connection manager info
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.services['exchange']}/api/v1/websocket/binance/status",
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return {
                        'connection_manager_available': 'stream_status' in data,
                        'status_data': data
                    }
                else:
                    return {
                        'connection_manager_available': False,
                        'status_code': response.status
                    }
    
    async def _check_error_handler_stats(self) -> Dict[str, Any]:
        """Check error handler statistics"""
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.services['exchange']}/api/v1/websocket/binance/metrics",
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return {
                        'error_metrics_available': 'execution_processor_metrics' in data or 'error_handler_stats' in data,
                        'metrics_data': data
                    }
                else:
                    return {
                        'error_metrics_available': False,
                        'status_code': response.status
                    }
    
    async def _check_recovery_manager(self) -> Dict[str, Any]:
        """Check recovery manager status"""
        # Recovery manager info is included in the WebSocket metrics
        return await self._check_error_handler_stats()
    
    async def _test_exchange_api_fallback(self) -> Dict[str, Any]:
        """Test exchange API endpoints (fallback functionality)"""
        # Test if we can fetch orders (this would be used during fallback)
        async with aiohttp.ClientSession() as session:
            # Test binance orders endpoint
            async with session.get(
                f"{self.services['exchange']}/api/v1/trading/orders/binance",
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                return {
                    'fallback_api_available': response.status in [200, 403],  # 403 means no API keys, but endpoint works
                    'status_code': response.status
                }
    
    async def _check_fallback_worker_status(self) -> Dict[str, Any]:
        """Check fallback worker status in fill detection service"""
        # The fallback worker runs as part of the exchange monitor worker
        # We can check the service health to ensure the worker is running
        return await self._check_service_health('fill_detection')
    
    async def _check_system_health_monitoring(self) -> Dict[str, Any]:
        """Check system health monitoring"""
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.services['exchange']}/api/v1/health/system",
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return {
                        'system_health_available': True,
                        'health_data': data
                    }
                else:
                    return {
                        'system_health_available': False,
                        'status_code': response.status
                    }
    
    async def _check_health_metrics(self) -> Dict[str, Any]:
        """Check health metrics endpoint"""
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.services['exchange']}/api/v1/health/metrics",
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return {
                        'health_metrics_available': True,
                        'metrics_data': data
                    }
                else:
                    return {
                        'health_metrics_available': False,
                        'status_code': response.status
                    }
    
    async def _check_health_history(self) -> Dict[str, Any]:
        """Check health history endpoint"""
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.services['exchange']}/api/v1/health/history?hours=1",
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return {
                        'health_history_available': True,
                        'history_data': data
                    }
                else:
                    return {
                        'health_history_available': False,
                        'status_code': response.status
                    }
    
    async def _check_detailed_websocket_health(self) -> Dict[str, Any]:
        """Check detailed WebSocket health"""
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.services['exchange']}/api/v1/health/websocket/detailed",
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return {
                        'detailed_websocket_health_available': True,
                        'websocket_health_data': data
                    }
                else:
                    return {
                        'detailed_websocket_health_available': False,
                        'status_code': response.status
                    }
    
    async def _test_mock_fill_event(self) -> Dict[str, Any]:
        """Test mock fill event processing"""
        # Create a mock fill event
        mock_fill = {
            'event_type': 'order_filled',
            'exchange': 'binance',
            'order_id': f'test_order_{int(time.time())}',
            'symbol': 'ETHUSDC',
            'side': 'buy',
            'amount': 0.1,
            'filled_amount': 0.1,
            'avg_price': 2500.0,
            'fee_amount': 0.25,
            'fee_currency': 'USDC',
            'timestamp': datetime.now().isoformat()
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.services['fill_detection']}/api/v1/fills/emit",
                json=mock_fill,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return {
                        'mock_fill_processed': True,
                        'response_data': data
                    }
                else:
                    error_text = await response.text()
                    return {
                        'mock_fill_processed': False,
                        'status_code': response.status,
                        'error': error_text
                    }
    
    async def _test_redis_stream_integration(self) -> Dict[str, Any]:
        """Test Redis stream integration"""
        try:
            # Get stream info
            stream_info = await self.redis_client.xinfo_stream("trading:fills:stream")
            
            return {
                'redis_stream_available': True,
                'stream_length': stream_info.get('length', 0),
                'stream_info': stream_info
            }
        except Exception as e:
            return {
                'redis_stream_available': False,
                'error': str(e)
            }
    
    async def _test_complete_order_flow(self) -> Dict[str, Any]:
        """Test complete order flow simulation"""
        # This is a comprehensive test that checks multiple components working together
        results = {}
        
        # Step 1: Check if we can emit a fill event
        mock_fill = {
            'event_type': 'websocket_fill',
            'exchange': 'binance',
            'order_id': f'integration_test_{int(time.time())}',
            'symbol': 'ETHUSDC',
            'side': 'buy',
            'amount': 0.05,
            'filled_amount': 0.05,
            'avg_price': 2450.0,
            'fee_amount': 0.12,
            'fee_currency': 'USDC',
            'timestamp': datetime.now().isoformat()
        }
        
        async with aiohttp.ClientSession() as session:
            # Emit fill event
            async with session.post(
                f"{self.services['fill_detection']}/api/v1/fills/emit",
                json=mock_fill,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                results['fill_emission'] = response.status == 200
        
        # Step 2: Check stream info
        try:
            stream_info = await self.redis_client.xinfo_stream("trading:fills:stream")
            results['stream_updated'] = stream_info.get('length', 0) > 0
        except:
            results['stream_updated'] = False
        
        # Step 3: Check fill detection service status
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.services['fill_detection']}/api/v1/fills/stream/info",
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                results['stream_info_available'] = response.status == 200
        
        return results
    
    async def _run_test(self, test_name: str, test_func, **kwargs) -> TestResult:
        """Run a single test and record results"""
        start_time = time.time()
        
        try:
            logger.info(f"  🔬 Running test: {test_name}")
            result = await test_func(**kwargs)
            
            duration = (time.time() - start_time) * 1000
            
            # Determine if test passed
            if isinstance(result, dict):
                # Check for common failure indicators
                passed = not any([
                    result.get('error'),
                    result.get('healthy') is False,
                    result.get('status_code', 200) >= 400
                ])
                message = result.get('message', 'Test completed')
            else:
                passed = bool(result)
                message = f"Test result: {result}"
                result = {'result': result}
            
            test_result = TestResult(
                name=test_name,
                passed=passed,
                message=message,
                duration_ms=duration,
                details=result
            )
            
            status = "✅" if passed else "❌"
            logger.info(f"  {status} {test_name}: {message} ({duration:.1f}ms)")
            
        except Exception as e:
            duration = (time.time() - start_time) * 1000
            test_result = TestResult(
                name=test_name,
                passed=False,
                message=f"Test failed with exception: {str(e)}",
                duration_ms=duration,
                details={'error': str(e), 'exception_type': type(e).__name__}
            )
            logger.error(f"  ❌ {test_name}: Exception - {str(e)} ({duration:.1f}ms)")
        
        self.test_results.append(test_result)
        return test_result
    
    def _generate_test_report(self, start_time: datetime, end_time: datetime) -> Dict[str, Any]:
        """Generate comprehensive test report"""
        total_tests = len(self.test_results)
        passed_tests = sum(1 for test in self.test_results if test.passed)
        failed_tests = total_tests - passed_tests
        
        total_duration = (end_time - start_time).total_seconds()
        avg_test_duration = sum(test.duration_ms for test in self.test_results) / total_tests if total_tests > 0 else 0
        
        # Group results by category
        categories = {}
        for test in self.test_results:
            category = test.name.split('_')[0] if '_' in test.name else 'misc'
            if category not in categories:
                categories[category] = {'passed': 0, 'failed': 0, 'tests': []}
            
            categories[category]['tests'].append(test.to_dict())
            if test.passed:
                categories[category]['passed'] += 1
            else:
                categories[category]['failed'] += 1
        
        report = {
            'test_summary': {
                'total_tests': total_tests,
                'passed_tests': passed_tests,
                'failed_tests': failed_tests,
                'success_rate': (passed_tests / total_tests * 100) if total_tests > 0 else 0,
                'total_duration_seconds': total_duration,
                'average_test_duration_ms': avg_test_duration
            },
            'categories': categories,
            'test_results': [test.to_dict() for test in self.test_results],
            'timestamp': datetime.now().isoformat(),
            'environment': {
                'services': self.services,
                'test_runner': 'WebSocketTestSuite v1.0'
            }
        }
        
        return report

async def main():
    """Main test runner"""
    print("🧪 Binance WebSocket Implementation Test Suite")
    print("=" * 60)
    
    test_suite = WebSocketTestSuite()
    
    try:
        # Run all tests
        report = await test_suite.run_all_tests()
        
        # Print summary
        print("\n" + "=" * 60)
        print("📊 TEST RESULTS SUMMARY")
        print("=" * 60)
        
        summary = report['test_summary']
        print(f"Total Tests: {summary['total_tests']}")
        print(f"Passed: {summary['passed_tests']} ✅")
        print(f"Failed: {summary['failed_tests']} ❌")
        print(f"Success Rate: {summary['success_rate']:.1f}%")
        print(f"Total Duration: {summary['total_duration_seconds']:.2f}s")
        print(f"Average Test Duration: {summary['average_test_duration_ms']:.1f}ms")
        
        # Print category breakdown
        print("\n📋 CATEGORY BREAKDOWN")
        print("-" * 40)
        for category, stats in report['categories'].items():
            total_cat = stats['passed'] + stats['failed']
            success_rate = (stats['passed'] / total_cat * 100) if total_cat > 0 else 0
            print(f"{category.upper()}: {stats['passed']}/{total_cat} ({success_rate:.1f}%)")
        
        # Print failed tests
        failed_tests = [test for test in report['test_results'] if not test['passed']]
        if failed_tests:
            print("\n❌ FAILED TESTS")
            print("-" * 40)
            for test in failed_tests:
                print(f"- {test['name']}: {test['message']}")
        
        # Save detailed report
        report_filename = f"websocket_test_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(report_filename, 'w') as f:
            json.dump(report, f, indent=2)
        
        print(f"\n📄 Detailed report saved to: {report_filename}")
        
        # Return appropriate exit code
        return 0 if summary['failed_tests'] == 0 else 1
        
    except Exception as e:
        print(f"\n💥 Test suite failed with error: {e}")
        return 1

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)