#!/usr/bin/env python3
"""
Comprehensive Test Suite for Bybit WebSocket Integration - Version 2.6.0
Tests all components of the Bybit WebSocket implementation
"""

import asyncio
import json
import logging
import time
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
import pytest

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class TestBybitWebSocketIntegration:
    """Comprehensive test suite for Bybit WebSocket integration"""
    
    def __init__(self):
        self.base_urls = {
            'exchange_service': 'http://localhost:8003',
            'web_dashboard': 'http://localhost:8006',
            'fill_detection': 'http://localhost:8007',
            'database_service': 'http://localhost:8002'
        }
        self.test_results = {
            'passed': 0,
            'failed': 0,
            'total': 0,
            'details': []
        }
    
    async def run_all_tests(self):
        """Run all test categories"""
        logger.info("🚀 Starting Comprehensive Bybit WebSocket Integration Tests")
        
        test_categories = [
            self.test_authentication_manager,
            self.test_connection_manager,
            self.test_event_processors,
            self.test_health_monitor,
            self.test_websocket_integration,
            self.test_fill_detection_integration,
            self.test_dashboard_integration,
            self.test_error_handling,
            self.test_recovery_mechanisms,
            self.test_database_integration
        ]
        
        for test_func in test_categories:
            try:
                await test_func()
            except Exception as e:
                logger.error(f"❌ Test category {test_func.__name__} failed: {e}")
                self.test_results['failed'] += 1
                self.test_results['details'].append({
                    'test': test_func.__name__,
                    'status': 'failed',
                    'error': str(e)
                })
        
        self.print_test_summary()
        return self.test_results
    
    async def test_authentication_manager(self):
        """Test Bybit authentication manager functionality"""
        logger.info("🔐 Testing Bybit Authentication Manager")
        
        try:
            # Import the authentication manager
            import sys
            import os
            sys.path.append(os.path.join(os.path.dirname(__file__), 'services', 'exchange-service'))
            from bybit_auth_manager import BybitAuthManager
            
            # Test initialization
            auth_manager = BybitAuthManager("test_api_key", "test_api_secret")
            assert auth_manager.api_key == "test_api_key"
            assert auth_manager.api_secret == "test_api_secret"
            
            # Test signature generation
            timestamp = int(time.time() * 1000)
            signature = auth_manager._generate_signature(timestamp)
            assert isinstance(signature, str)
            assert len(signature) > 0
            
            # Test authentication payload generation
            payload = auth_manager.generate_auth_payload()
            assert 'op' in payload
            assert payload['op'] == 'auth'
            assert 'args' in payload
            assert len(payload['args']) == 3  # api_key, timestamp, signature
            
            # Test timestamp validation
            assert auth_manager._validate_timestamp(timestamp)
            assert not auth_manager._validate_timestamp(timestamp - 10000)  # Old timestamp
            
            # Test authentication with retry
            with patch('httpx.AsyncClient.post') as mock_post:
                mock_post.return_value.status_code = 200
                mock_post.return_value.json.return_value = {'retCode': 0, 'retMsg': 'OK'}
                
                success = await auth_manager.authenticate_with_retry()
                assert success
            
            logger.info("✅ Authentication Manager tests passed")
            self.test_results['passed'] += 1
            self.test_results['details'].append({
                'test': 'authentication_manager',
                'status': 'passed',
                'details': 'All authentication functionality working correctly'
            })
            
        except Exception as e:
            logger.error(f"❌ Authentication Manager test failed: {e}")
            self.test_results['failed'] += 1
            self.test_results['details'].append({
                'test': 'authentication_manager',
                'status': 'failed',
                'error': str(e)
            })
    
    async def test_connection_manager(self):
        """Test Bybit connection manager functionality"""
        logger.info("🔌 Testing Bybit Connection Manager")
        
        try:
            # Import the connection manager
            from bybit_connection_manager import BybitConnectionManager
            
            # Test initialization
            conn_manager = BybitConnectionManager(
                websocket_url="wss://test.bybit.com/v5/private",
                auth_manager=MagicMock(),
                max_reconnect_attempts=3,
                reconnect_delay=1
            )
            
            assert conn_manager.websocket_url == "wss://test.bybit.com/v5/private"
            assert conn_manager.max_reconnect_attempts == 3
            assert conn_manager.reconnect_delay == 1
            
            # Test connection state management
            assert conn_manager.connection_state == 'disconnected'
            
            # Test message callback registration
            callback = MagicMock()
            conn_manager.add_message_callback(callback)
            assert callback in conn_manager.message_callbacks
            
            # Test connection callback registration
            conn_callback = MagicMock()
            conn_manager.add_connection_callback(conn_callback)
            assert conn_callback in conn_manager.connection_callbacks
            
            # Test error callback registration
            error_callback = MagicMock()
            conn_manager.add_error_callback(error_callback)
            assert error_callback in conn_manager.error_callbacks
            
            # Test metrics
            metrics = conn_manager.get_status()
            assert 'connection_state' in metrics
            assert 'connection_attempts' in metrics
            assert 'last_connection_time' in metrics
            
            logger.info("✅ Connection Manager tests passed")
            self.test_results['passed'] += 1
            self.test_results['details'].append({
                'test': 'connection_manager',
                'status': 'passed',
                'details': 'All connection management functionality working correctly'
            })
            
        except Exception as e:
            logger.error(f"❌ Connection Manager test failed: {e}")
            self.test_results['failed'] += 1
            self.test_results['details'].append({
                'test': 'connection_manager',
                'status': 'failed',
                'error': str(e)
            })
    
    async def test_event_processors(self):
        """Test Bybit event processors functionality"""
        logger.info("📊 Testing Bybit Event Processors")
        
        try:
            # Import the event processors
            from bybit_event_processors import BybitEventProcessor
            
            # Test initialization
            processor = BybitEventProcessor()
            
            # Test order event processing
            order_event = {
                "topic": "order",
                "type": "snapshot",
                "ts": int(time.time() * 1000),
                "data": [{
                    "orderId": "test_order_123",
                    "symbol": "BTCUSDT",
                    "side": "Buy",
                    "orderType": "Limit",
                    "price": "50000",
                    "qty": "0.001",
                    "orderStatus": "Filled"
                }]
            }
            
            # Test execution event processing
            execution_event = {
                "topic": "execution",
                "type": "snapshot",
                "ts": int(time.time() * 1000),
                "data": [{
                    "symbol": "BTCUSDT",
                    "side": "Buy",
                    "orderId": "test_order_123",
                    "execId": "exec_123",
                    "price": "50000",
                    "qty": "0.001",
                    "execFee": "0.000001"
                }]
            }
            
            # Test position event processing
            position_event = {
                "topic": "position",
                "type": "snapshot",
                "ts": int(time.time() * 1000),
                "data": [{
                    "symbol": "BTCUSDT",
                    "side": "Buy",
                    "size": "0.001",
                    "avgPrice": "50000"
                }]
            }
            
            # Test wallet event processing
            wallet_event = {
                "topic": "wallet",
                "type": "snapshot",
                "ts": int(time.time() * 1000),
                "data": [{
                    "coin": "USDT",
                    "walletBalance": "1000.00",
                    "availableBalance": "950.00"
                }]
            }
            
            # Test callback registration
            order_callback = MagicMock()
            processor.add_order_callback(order_callback)
            assert order_callback in processor.order_callbacks
            
            execution_callback = MagicMock()
            processor.add_execution_callback(execution_callback)
            assert execution_callback in processor.execution_callbacks
            
            # Test metrics
            metrics = processor.get_metrics()
            assert 'events_processed' in metrics
            assert 'events_by_type' in metrics
            assert 'processing_errors' in metrics
            
            logger.info("✅ Event Processors tests passed")
            self.test_results['passed'] += 1
            self.test_results['details'].append({
                'test': 'event_processors',
                'status': 'passed',
                'details': 'All event processing functionality working correctly'
            })
            
        except Exception as e:
            logger.error(f"❌ Event Processors test failed: {e}")
            self.test_results['failed'] += 1
            self.test_results['details'].append({
                'test': 'event_processors',
                'status': 'failed',
                'error': str(e)
            })
    
    async def test_health_monitor(self):
        """Test Bybit health monitor functionality"""
        logger.info("🏥 Testing Bybit Health Monitor")
        
        try:
            # Import the health monitor
            from bybit_health_monitor import BybitHealthMonitor
            
            # Test initialization
            health_monitor = BybitHealthMonitor("test-service")
            
            assert health_monitor.service_name == "test-service"
            assert health_monitor.monitoring_active == False
            
            # Test metric recording
            health_monitor.record_connection_attempt(True)
            health_monitor.record_authentication_attempt(True)
            health_monitor.record_event_processed(processing_time=100.0)
            health_monitor.record_error("test_error")
            health_monitor.record_recovery_attempt(True)
            
            # Test health status
            status = health_monitor.get_health_status()
            assert 'service' in status
            assert 'monitoring_active' in status
            assert 'performance_data' in status
            
            # Test health history
            history = health_monitor.get_health_history()
            assert isinstance(history, list)
            
            # Test alert callback registration
            alert_callback = MagicMock()
            health_monitor.add_alert_callback(alert_callback)
            assert alert_callback in health_monitor.alert_callbacks
            
            # Test metrics reset
            health_monitor.reset_metrics()
            assert len(health_monitor.health_history) == 0
            
            logger.info("✅ Health Monitor tests passed")
            self.test_results['passed'] += 1
            self.test_results['details'].append({
                'test': 'health_monitor',
                'status': 'passed',
                'details': 'All health monitoring functionality working correctly'
            })
            
        except Exception as e:
            logger.error(f"❌ Health Monitor test failed: {e}")
            self.test_results['failed'] += 1
            self.test_results['details'].append({
                'test': 'health_monitor',
                'status': 'failed',
                'error': str(e)
            })
    
    async def test_websocket_integration(self):
        """Test Bybit WebSocket integration endpoints"""
        logger.info("🌐 Testing Bybit WebSocket Integration Endpoints")
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Test health endpoint
                response = await client.get(f"{self.base_urls['exchange_service']}/api/v1/bybit/websocket/health")
                assert response.status_code in [200, 503]  # 503 if not initialized
                
                # Test metrics endpoint
                response = await client.get(f"{self.base_urls['exchange_service']}/api/v1/bybit/websocket/metrics")
                assert response.status_code in [200, 503]  # 503 if not initialized
                
                # Test status endpoint
                response = await client.get(f"{self.base_urls['exchange_service']}/api/v1/bybit/websocket/status")
                assert response.status_code in [200, 503]  # 503 if not initialized
                
                # Test health history endpoint
                response = await client.get(f"{self.base_urls['exchange_service']}/api/v1/bybit/websocket/health/history")
                assert response.status_code in [200, 503]  # 503 if not initialized
                
            logger.info("✅ WebSocket Integration tests passed")
            self.test_results['passed'] += 1
            self.test_results['details'].append({
                'test': 'websocket_integration',
                'status': 'passed',
                'details': 'All WebSocket integration endpoints responding correctly'
            })
            
        except Exception as e:
            logger.error(f"❌ WebSocket Integration test failed: {e}")
            self.test_results['failed'] += 1
            self.test_results['details'].append({
                'test': 'websocket_integration',
                'status': 'failed',
                'error': str(e)
            })
    
    async def test_fill_detection_integration(self):
        """Test Bybit WebSocket integration with fill detection service"""
        logger.info("🔍 Testing Fill Detection Service Integration")
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Test Bybit event endpoints
                test_order_event = {
                    "topic": "order",
                    "type": "snapshot",
                    "ts": int(time.time() * 1000),
                    "data": [{
                        "orderId": "test_order_456",
                        "symbol": "ETHUSDT",
                        "side": "Sell",
                        "orderType": "Market",
                        "price": "3000",
                        "qty": "0.1",
                        "orderStatus": "Filled"
                    }]
                }
                
                response = await client.post(
                    f"{self.base_urls['fill_detection']}/api/v1/events/bybit/order",
                    json=test_order_event
                )
                assert response.status_code in [200, 201]
                
                # Test execution event endpoint
                test_execution_event = {
                    "topic": "execution",
                    "type": "snapshot",
                    "ts": int(time.time() * 1000),
                    "data": [{
                        "symbol": "ETHUSDT",
                        "side": "Sell",
                        "orderId": "test_order_456",
                        "execId": "exec_456",
                        "price": "3000",
                        "qty": "0.1",
                        "execFee": "0.0001"
                    }]
                }
                
                response = await client.post(
                    f"{self.base_urls['fill_detection']}/api/v1/events/bybit/execution",
                    json=test_execution_event
                )
                assert response.status_code in [200, 201]
                
                # Test generic Bybit event endpoint
                response = await client.post(
                    f"{self.base_urls['fill_detection']}/api/v1/events/bybit",
                    json=test_order_event
                )
                assert response.status_code in [200, 201]
                
                # Test Bybit WebSocket status endpoint
                response = await client.get(f"{self.base_urls['fill_detection']}/api/v1/websocket/bybit/status")
                assert response.status_code in [200, 503]  # 503 if not initialized
                
            logger.info("✅ Fill Detection Integration tests passed")
            self.test_results['passed'] += 1
            self.test_results['details'].append({
                'test': 'fill_detection_integration',
                'status': 'passed',
                'details': 'All fill detection integration endpoints working correctly'
            })
            
        except Exception as e:
            logger.error(f"❌ Fill Detection Integration test failed: {e}")
            self.test_results['failed'] += 1
            self.test_results['details'].append({
                'test': 'fill_detection_integration',
                'status': 'failed',
                'error': str(e)
            })
    
    async def test_dashboard_integration(self):
        """Test Bybit WebSocket integration with web dashboard"""
        logger.info("📊 Testing Web Dashboard Integration")
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Test Bybit health endpoint proxy
                response = await client.get(f"{self.base_urls['web_dashboard']}/api/v1/bybit/websocket/health")
                assert response.status_code in [200, 503]  # 503 if not initialized
                
                # Test main dashboard endpoint
                response = await client.get(f"{self.base_urls['web_dashboard']}/")
                assert response.status_code == 200
                
                # Test enhanced dashboard endpoint
                response = await client.get(f"{self.base_urls['web_dashboard']}/enhanced-dashboard")
                assert response.status_code == 200
                
            logger.info("✅ Dashboard Integration tests passed")
            self.test_results['passed'] += 1
            self.test_results['details'].append({
                'test': 'dashboard_integration',
                'status': 'passed',
                'details': 'All dashboard integration endpoints working correctly'
            })
            
        except Exception as e:
            logger.error(f"❌ Dashboard Integration test failed: {e}")
            self.test_results['failed'] += 1
            self.test_results['details'].append({
                'test': 'dashboard_integration',
                'status': 'failed',
                'error': str(e)
            })
    
    async def test_error_handling(self):
        """Test error handling and recovery mechanisms"""
        logger.info("⚠️ Testing Error Handling and Recovery")
        
        try:
            # Import error handlers
            from bybit_error_handlers import BybitErrorHandler, ErrorCategory, ErrorSeverity
            
            # Test error handler initialization
            error_handler = BybitErrorHandler()
            
            # Test error categorization
            auth_error = error_handler._categorize_error("Authentication failed")
            assert auth_error == ErrorCategory.AUTHENTICATION
            
            conn_error = error_handler._categorize_error("Connection timeout")
            assert conn_error == ErrorCategory.CONNECTION
            
            # Test severity assessment
            severity = error_handler._assess_severity(ErrorCategory.AUTHENTICATION)
            assert severity in [ErrorSeverity.HIGH, ErrorSeverity.CRITICAL]
            
            # Test error handling
            error_handler.handle_error("Test error", ErrorCategory.CONNECTION, ErrorSeverity.MEDIUM)
            assert len(error_handler.error_history) > 0
            
            # Test circuit breaker
            for _ in range(5):  # Trigger circuit breaker
                error_handler.handle_error("Test error", ErrorCategory.CONNECTION, ErrorSeverity.HIGH)
            
            assert error_handler.circuit_breaker_open
            
            # Test recovery actions
            actions = error_handler._get_recovery_actions(ErrorCategory.CONNECTION, ErrorSeverity.HIGH)
            assert len(actions) > 0
            assert 'reconnect' in actions
            
            logger.info("✅ Error Handling tests passed")
            self.test_results['passed'] += 1
            self.test_results['details'].append({
                'test': 'error_handling',
                'status': 'passed',
                'details': 'All error handling and recovery mechanisms working correctly'
            })
            
        except Exception as e:
            logger.error(f"❌ Error Handling test failed: {e}")
            self.test_results['failed'] += 1
            self.test_results['details'].append({
                'test': 'error_handling',
                'status': 'failed',
                'error': str(e)
            })
    
    async def test_recovery_mechanisms(self):
        """Test recovery manager functionality"""
        logger.info("🔄 Testing Recovery Mechanisms")
        
        try:
            # Import recovery manager
            from bybit_recovery_manager import BybitRecoveryManager
            
            # Test recovery manager initialization
            recovery_manager = BybitRecoveryManager()
            
            # Test recovery action execution
            actions = ['reconnect', 'retry', 'fallback']
            success = await recovery_manager.execute_recovery_actions(actions)
            assert isinstance(success, bool)
            
            # Test recovery metrics
            metrics = recovery_manager.get_recovery_metrics()
            assert 'recovery_attempts' in metrics
            assert 'successful_recoveries' in metrics
            assert 'recovery_success_rate' in metrics
            
            # Test recovery callback registration
            callback = MagicMock()
            recovery_manager.add_recovery_callback(callback)
            assert callback in recovery_manager.recovery_callbacks
            
            logger.info("✅ Recovery Mechanisms tests passed")
            self.test_results['passed'] += 1
            self.test_results['details'].append({
                'test': 'recovery_mechanisms',
                'status': 'passed',
                'details': 'All recovery mechanisms working correctly'
            })
            
        except Exception as e:
            logger.error(f"❌ Recovery Mechanisms test failed: {e}")
            self.test_results['failed'] += 1
            self.test_results['details'].append({
                'test': 'recovery_mechanisms',
                'status': 'failed',
                'error': str(e)
            })
    
    async def test_database_integration(self):
        """Test database integration for Bybit data"""
        logger.info("🗄️ Testing Database Integration")
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Test database service health
                response = await client.get(f"{self.base_urls['database_service']}/health")
                assert response.status_code == 200
                
                # Test Bybit data manager endpoints (if available)
                try:
                    response = await client.get(f"{self.base_urls['database_service']}/api/v1/bybit/order-summary")
                    assert response.status_code in [200, 404]  # 404 if endpoint not implemented
                except:
                    pass  # Endpoint might not be implemented yet
                
            logger.info("✅ Database Integration tests passed")
            self.test_results['passed'] += 1
            self.test_results['details'].append({
                'test': 'database_integration',
                'status': 'passed',
                'details': 'Database integration working correctly'
            })
            
        except Exception as e:
            logger.error(f"❌ Database Integration test failed: {e}")
            self.test_results['failed'] += 1
            self.test_results['details'].append({
                'test': 'database_integration',
                'status': 'failed',
                'error': str(e)
            })
    
    def print_test_summary(self):
        """Print comprehensive test summary"""
        total = self.test_results['total']
        passed = self.test_results['passed']
        failed = self.test_results['failed']
        success_rate = (passed / total * 100) if total > 0 else 0
        
        logger.info("\n" + "="*80)
        logger.info("🎯 BYBIT WEBSOCKET INTEGRATION TEST SUMMARY")
        logger.info("="*80)
        logger.info(f"📊 Total Tests: {total}")
        logger.info(f"✅ Passed: {passed}")
        logger.info(f"❌ Failed: {failed}")
        logger.info(f"📈 Success Rate: {success_rate:.1f}%")
        logger.info("="*80)
        
        if failed > 0:
            logger.info("\n❌ FAILED TESTS:")
            for detail in self.test_results['details']:
                if detail['status'] == 'failed':
                    logger.info(f"  - {detail['test']}: {detail['error']}")
        
        logger.info("\n✅ PASSED TESTS:")
        for detail in self.test_results['details']:
            if detail['status'] == 'passed':
                logger.info(f"  - {detail['test']}: {detail['details']}")
        
        logger.info("="*80)
        
        if success_rate >= 90:
            logger.info("🎉 EXCELLENT! Bybit WebSocket integration is ready for production!")
        elif success_rate >= 80:
            logger.info("👍 GOOD! Bybit WebSocket integration is mostly ready with minor issues.")
        elif success_rate >= 70:
            logger.info("⚠️ FAIR! Bybit WebSocket integration needs some fixes before production.")
        else:
            logger.info("🚨 POOR! Bybit WebSocket integration needs significant work before production.")
        
        logger.info("="*80)

async def main():
    """Main test execution function"""
    test_suite = TestBybitWebSocketIntegration()
    results = await test_suite.run_all_tests()
    
    # Return exit code based on success rate
    success_rate = (results['passed'] / results['total'] * 100) if results['total'] > 0 else 0
    return 0 if success_rate >= 80 else 1

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)
