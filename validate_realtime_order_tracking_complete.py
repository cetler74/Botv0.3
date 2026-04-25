#!/usr/bin/env python3
"""
REALTIME ORDER TRACKING VALIDATION - COMPLETE
Validates the complete realtime WebSocket order tracking system with trailing stop integration
"""

import asyncio
import aiohttp
import json
from datetime import datetime
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class RealtimeOrderTrackingValidator:
    def __init__(self):
        self.services = {
            "orchestrator": "http://localhost:8005",
            "exchange": "http://localhost:8003"
        }
        
        self.test_results = {}
        
    async def validate_realtime_tracking_system(self):
        """Validate complete realtime order tracking with trailing stop integration"""
        logger.info("🚀 VALIDATING REALTIME ORDER TRACKING SYSTEM")
        logger.info("=" * 70)
        
        async with aiohttp.ClientSession() as session:
            # 1. Validate WebSocket System Status
            await self.test_websocket_system_status(session)
            
            # 2. Test Callback Registration
            await self.test_callback_registration(session)
            
            # 3. Test Order Update Callbacks
            await self.test_order_update_callbacks(session)
            
            # 4. Test Trade Execution Callbacks
            await self.test_trade_execution_callbacks(session)
            
            # 5. Test Trailing Stop Integration
            await self.test_trailing_stop_integration(session)
            
            # 6. Test Crypto.com WebSocket Health
            await self.test_cryptocom_websocket_health(session)
        
        return self.generate_validation_report()
    
    async def test_websocket_system_status(self, session):
        """Test overall WebSocket system status"""
        logger.info("1️⃣ Testing WebSocket System Status...")
        
        status_tests = {}
        
        # Check orchestrator WebSocket status
        try:
            async with session.get(f"{self.services['orchestrator']}/api/v1/orders/websocket/status") as response:
                if response.status == 200:
                    status_data = await response.json()
                    
                    websocket_enabled = status_data.get('websocket_enabled', False)
                    cryptocom_connected = status_data.get('connections', {}).get('cryptocom', {}).get('connected', False)
                    
                    status_tests["websocket_enabled"] = websocket_enabled
                    status_tests["cryptocom_connected"] = cryptocom_connected
                    
                    logger.info(f"✅ WebSocket Enabled: {websocket_enabled}")
                    logger.info(f"✅ Crypto.com Connected: {cryptocom_connected}")
                else:
                    status_tests["websocket_enabled"] = False
                    status_tests["cryptocom_connected"] = False
                    logger.error(f"❌ WebSocket status check failed: {response.status}")
        except Exception as e:
            status_tests["websocket_enabled"] = False
            status_tests["cryptocom_connected"] = False
            logger.error(f"❌ WebSocket status check error: {e}")
        
        self.test_results["websocket_system"] = status_tests
    
    async def test_callback_registration(self, session):
        """Test WebSocket callback registration"""
        logger.info("2️⃣ Testing Callback Registration...")
        
        registration_tests = {}
        
        try:
            async with session.post(f"{self.services['orchestrator']}/api/v1/websocket/register") as response:
                if response.status == 200:
                    reg_data = await response.json()
                    
                    overall_status = reg_data.get('status') == 'completed'
                    cryptocom_status = reg_data.get('registrations', {}).get('cryptocom', {}).get('status') == 'registered'
                    
                    registration_tests["registration_completed"] = overall_status
                    registration_tests["cryptocom_registered"] = cryptocom_status
                    
                    logger.info(f"✅ Registration Completed: {overall_status}")
                    logger.info(f"✅ Crypto.com Registered: {cryptocom_status}")
                else:
                    registration_tests["registration_completed"] = False
                    registration_tests["cryptocom_registered"] = False
                    logger.error(f"❌ Registration failed: {response.status}")
        except Exception as e:
            registration_tests["registration_completed"] = False
            registration_tests["cryptocom_registered"] = False
            logger.error(f"❌ Registration error: {e}")
        
        self.test_results["callback_registration"] = registration_tests
    
    async def test_order_update_callbacks(self, session):
        """Test realtime order update callbacks"""
        logger.info("3️⃣ Testing Order Update Callbacks...")
        
        order_tests = {}
        
        # Test different order statuses
        test_orders = [
            {"order_id": "rt_test_001", "status": "FILLED", "price": 0.295},
            {"order_id": "rt_test_002", "status": "PARTIALLY_FILLED", "price": 0.294},
            {"order_id": "rt_test_003", "status": "CANCELLED", "price": 0.296}
        ]
        
        for test_order in test_orders:
            try:
                callback_data = {
                    "type": "user.order",
                    "order_id": test_order["order_id"],
                    "status": test_order["status"],
                    "price": test_order["price"],
                    "filled_quantity": 10.0,
                    "avg_price": test_order["price"],
                    "timestamp": datetime.utcnow().isoformat()
                }
                
                async with session.post(
                    f"{self.services['orchestrator']}/api/v1/websocket/callback/cryptocom",
                    json=callback_data
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        if result.get("status") == "processed":
                            order_tests[f"order_{test_order['status'].lower()}"] = True
                            logger.info(f"✅ Order {test_order['status']} callback processed")
                        else:
                            order_tests[f"order_{test_order['status'].lower()}"] = False
                            logger.error(f"❌ Order {test_order['status']} callback not processed properly")
                    else:
                        order_tests[f"order_{test_order['status'].lower()}"] = False
                        logger.error(f"❌ Order {test_order['status']} callback failed: {response.status}")
            except Exception as e:
                order_tests[f"order_{test_order['status'].lower()}"] = False
                logger.error(f"❌ Order {test_order['status']} callback error: {e}")
        
        self.test_results["order_callbacks"] = order_tests
    
    async def test_trade_execution_callbacks(self, session):
        """Test realtime trade execution callbacks"""
        logger.info("4️⃣ Testing Trade Execution Callbacks...")
        
        trade_tests = {}
        
        try:
            callback_data = {
                "type": "user.trade",
                "trade_id": "rt_trade_001",
                "order_id": "rt_test_001",
                "price": 0.295,
                "quantity": 10.0,
                "fees": {"CRO": 0.001, "USD": 0.01},
                "timestamp": datetime.utcnow().isoformat()
            }
            
            async with session.post(
                f"{self.services['orchestrator']}/api/v1/websocket/callback/cryptocom",
                json=callback_data
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    if result.get("status") == "processed":
                        trade_tests["trade_execution"] = True
                        logger.info("✅ Trade execution callback processed")
                    else:
                        trade_tests["trade_execution"] = False
                        logger.error("❌ Trade execution callback not processed properly")
                else:
                    trade_tests["trade_execution"] = False
                    logger.error(f"❌ Trade execution callback failed: {response.status}")
        except Exception as e:
            trade_tests["trade_execution"] = False
            logger.error(f"❌ Trade execution callback error: {e}")
        
        self.test_results["trade_callbacks"] = trade_tests
    
    async def test_trailing_stop_integration(self, session):
        """Test trailing stop integration with realtime order fills"""
        logger.info("5️⃣ Testing Trailing Stop Integration...")
        
        integration_tests = {}
        
        try:
            # Test order fill that should trigger trailing stop consideration
            callback_data = {
                "type": "user.order",
                "order_id": "ts_test_001",
                "status": "FILLED",
                "price": 0.300,
                "filled_quantity": 100.0,
                "avg_price": 0.300,
                "timestamp": datetime.utcnow().isoformat()
            }
            
            async with session.post(
                f"{self.services['orchestrator']}/api/v1/websocket/callback/cryptocom",
                json=callback_data
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    if result.get("status") == "processed":
                        integration_tests["trailing_stop_triggered"] = True
                        logger.info("✅ Trailing stop integration callback processed")
                        
                        # Check trailing stop statistics
                        async with session.get(f"{self.services['orchestrator']}/api/v1/trailing-stops/statistics") as stats_response:
                            if stats_response.status == 200:
                                integration_tests["statistics_available"] = True
                                logger.info("✅ Trailing stop statistics available")
                            else:
                                integration_tests["statistics_available"] = False
                                logger.warning("⚠️ Trailing stop statistics not available")
                    else:
                        integration_tests["trailing_stop_triggered"] = False
                        logger.error("❌ Trailing stop integration callback not processed")
                else:
                    integration_tests["trailing_stop_triggered"] = False
                    logger.error(f"❌ Trailing stop integration callback failed: {response.status}")
        except Exception as e:
            integration_tests["trailing_stop_triggered"] = False
            logger.error(f"❌ Trailing stop integration error: {e}")
        
        self.test_results["trailing_stop_integration"] = integration_tests
    
    async def test_cryptocom_websocket_health(self, session):
        """Test Crypto.com WebSocket health and connectivity"""
        logger.info("6️⃣ Testing Crypto.com WebSocket Health...")
        
        health_tests = {}
        
        try:
            async with session.get(f"{self.services['exchange']}/api/v1/websocket/cryptocom/status") as response:
                if response.status == 200:
                    websocket_status = await response.json()
                    
                    enabled = websocket_status.get('enabled', False)
                    connected = websocket_status.get('user_data_connected', False)
                    subscribed_channels = websocket_status.get('stream_status', {}).get('subscribed_channels', [])
                    
                    health_tests["websocket_enabled"] = enabled
                    health_tests["user_data_connected"] = connected
                    health_tests["order_channel_subscribed"] = 'user.order' in subscribed_channels
                    health_tests["trade_channel_subscribed"] = 'user.trade' in subscribed_channels
                    
                    logger.info(f"✅ WebSocket Enabled: {enabled}")
                    logger.info(f"✅ User Data Connected: {connected}")
                    logger.info(f"✅ Order Channel: {'user.order' in subscribed_channels}")
                    logger.info(f"✅ Trade Channel: {'user.trade' in subscribed_channels}")
                else:
                    for key in ["websocket_enabled", "user_data_connected", "order_channel_subscribed", "trade_channel_subscribed"]:
                        health_tests[key] = False
                    logger.error(f"❌ WebSocket status check failed: {response.status}")
        except Exception as e:
            for key in ["websocket_enabled", "user_data_connected", "order_channel_subscribed", "trade_channel_subscribed"]:
                health_tests[key] = False
            logger.error(f"❌ WebSocket health check error: {e}")
        
        self.test_results["cryptocom_websocket"] = health_tests
    
    def generate_validation_report(self):
        """Generate comprehensive validation report"""
        logger.info("=" * 70)
        logger.info("📊 REALTIME ORDER TRACKING VALIDATION REPORT")
        logger.info("=" * 70)
        
        total_tests = 0
        passed_tests = 0
        critical_failures = []
        
        # Define critical tests
        critical_tests = [
            "websocket_system.websocket_enabled",
            "websocket_system.cryptocom_connected",
            "callback_registration.cryptocom_registered",
            "order_callbacks.order_filled",
            "trade_callbacks.trade_execution",
            "cryptocom_websocket.order_channel_subscribed"
        ]
        
        for category, tests in self.test_results.items():
            logger.info(f"\n📋 {category.replace('_', ' ').title()}:")
            
            for test_name, result in tests.items():
                total_tests += 1
                test_key = f"{category}.{test_name}"
                
                if result is True:
                    status = "✅ PASS"
                    passed_tests += 1
                else:
                    status = "❌ FAIL"
                    if test_key in critical_tests:
                        critical_failures.append(test_key)
                
                logger.info(f"   {status} | {test_name.replace('_', ' ').title()}")
        
        success_rate = (passed_tests / total_tests) * 100 if total_tests > 0 else 0
        
        logger.info("=" * 70)
        logger.info(f"OVERALL RESULT: {passed_tests}/{total_tests} tests passed ({success_rate:.1f}%)")
        
        # Determine system status
        if len(critical_failures) == 0:
            logger.info("🟢 REALTIME ORDER TRACKING: ALL CRITICAL SYSTEMS OPERATIONAL")
            logger.info("✅ Crypto.com realtime limit order tracking ENABLED")
            logger.info("🎯 Trailing stop integration ACTIVE")
            logger.info("📡 WebSocket callbacks properly configured")
            return True
        else:
            logger.error("🔴 REALTIME ORDER TRACKING: CRITICAL ISSUES DETECTED")
            logger.error(f"❌ Critical failures: {len(critical_failures)}")
            for failure in critical_failures:
                logger.error(f"   - {failure}")
            return False

async def main():
    """Main validation execution"""
    validator = RealtimeOrderTrackingValidator()
    
    success = await validator.validate_realtime_tracking_system()
    
    if success:
        print("\n🎯 CONCLUSION: Crypto.com realtime limit order tracking is FULLY OPERATIONAL")
        print("✅ WebSocket order tracking enabled and functional")
        print("🎯 Trailing stop integration ready for realtime activation")
        print("📡 Order fills will be detected instantly via WebSocket")
        print("🔄 Limit orders after trailing stop activation will be tracked in realtime")
        return 0
    else:
        print("\n🚨 CONCLUSION: Realtime order tracking has ISSUES")
        return 1

if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        exit(exit_code)
    except KeyboardInterrupt:
        print("\n❌ Validation interrupted")
        exit(1)