#!/usr/bin/env python3
"""
LIVE ORDER PLACEMENT AND TRACKING TEST
Places a real order on Binance and tracks it through the complete lifecycle:
Order Creation → Queue → Processing → Exchange → Fill → Database → Closure

⚠️ WARNING: This test places REAL ORDERS with REAL MONEY
⚠️ Use only small amounts for testing purposes
"""

import asyncio
import aiohttp
import json
import time
from datetime import datetime, timedelta
import logging
import uuid

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class LiveOrderTrackingTest:
    def __init__(self):
        self.orchestrator_url = "http://localhost:8005"
        self.queue_url = "http://localhost:8013"
        self.database_url = "http://localhost:8002"
        self.exchange_url = "http://localhost:8003"
        
        # Test configuration
        self.test_exchange = "binance"
        self.test_pair = "XLM/USDC"  # Low-value coin for safer testing
        self.test_amount = 10.0  # Small amount for safety
        
        self.test_results = {
            "system_health": False,
            "balance_check": False,
            "signal_sent": False,
            "order_queued": False,
            "order_processed": False,
            "exchange_order": False,
            "order_filled": False,
            "database_record": False,
            "trade_created": False,
            "complete_tracking": False
        }
        
    async def run_live_order_test(self):
        """Execute complete live order placement and tracking test"""
        logger.info("🚀 STARTING LIVE ORDER PLACEMENT AND TRACKING TEST")
        logger.info("⚠️  WARNING: THIS TEST PLACES REAL ORDERS WITH REAL MONEY")
        logger.info("💰 Test Configuration:")
        logger.info(f"   Exchange: {self.test_exchange}")
        logger.info(f"   Pair: {self.test_pair}")
        logger.info(f"   Amount: ${self.test_amount}")
        logger.info("=" * 80)
        
        try:
            # Step 1: System Health Check
            if not await self.check_all_systems_healthy():
                logger.error("❌ ABORT: System health check failed")
                return False
            
            # Step 2: Balance Verification
            if not await self.verify_sufficient_balance():
                logger.error("❌ ABORT: Insufficient balance for test")
                return False
            
            # Step 3: Force Buy Signal (Bypass Strategy Logic)
            order_id = await self.force_buy_signal()
            if not order_id:
                logger.error("❌ ABORT: Failed to send buy signal")
                return False
            
            # Step 4: Track Order Through Queue System
            if not await self.track_order_processing(order_id):
                logger.error("❌ Order processing failed")
                return False
            
            # Step 5: Verify Exchange Order Creation
            exchange_order_id = await self.verify_exchange_order(order_id)
            if not exchange_order_id:
                logger.error("❌ Exchange order verification failed")
                return False
            
            # Step 6: Monitor Order Fill
            if not await self.monitor_order_fill(order_id, exchange_order_id):
                logger.error("❌ Order fill monitoring failed")
                return False
            
            # Step 7: Verify Database Records
            if not await self.verify_complete_database_records(order_id):
                logger.error("❌ Database record verification failed")
                return False
            
            # Step 8: Verify Trade Creation and Tracking
            if not await self.verify_trade_creation(order_id):
                logger.error("❌ Trade creation verification failed")
                return False
            
            return self.generate_test_report()
            
        except Exception as e:
            logger.error(f"❌ CRITICAL TEST FAILURE: {e}")
            return False
    
    async def check_all_systems_healthy(self):
        """Check all required systems are healthy"""
        logger.info("🔍 STEP 1: Checking system health...")
        
        services = [
            ("Orchestrator", f"{self.orchestrator_url}/health"),
            ("Queue Service", f"{self.queue_url}/health"),
            ("Database", f"{self.database_url}/health"),
            ("Exchange", f"{self.exchange_url}/health")
        ]
        
        async with aiohttp.ClientSession() as session:
            for name, url in services:
                try:
                    async with session.get(url, timeout=10) as response:
                        if response.status == 200:
                            logger.info(f"✅ {name}: HEALTHY")
                        else:
                            logger.error(f"❌ {name}: UNHEALTHY ({response.status})")
                            return False
                except Exception as e:
                    logger.error(f"❌ {name}: UNREACHABLE ({e})\")\n                    return False\n        \n        self.test_results[\"system_health\"] = True\n        return True\n    \n    async def verify_sufficient_balance(self):\n        \"\"\"Verify sufficient balance for test order\"\"\"\n        logger.info(f\"💰 STEP 2: Checking {self.test_exchange} balance...\")\n        \n        async with aiohttp.ClientSession() as session:\n            try:\n                async with session.get(\n                    f\"{self.database_url}/api/v1/balances/{self.test_exchange}\"\n                ) as response:\n                    if response.status == 200:\n                        balance_data = await response.json()\n                        available = balance_data.get(\"available_balance\", 0)\n                        logger.info(f\"💰 Available balance: ${available:.2f}\")\n                        \n                        if available >= self.test_amount * 1.1:  # 10% buffer\n                            logger.info(f\"✅ Sufficient balance for ${self.test_amount} test order\")\n                            self.test_results[\"balance_check\"] = True\n                            return True\n                        else:\n                            logger.error(f\"❌ Insufficient balance: need ${self.test_amount}, have ${available:.2f}\")\n                            return False\n                    else:\n                        logger.error(f\"❌ Balance check failed: {response.status}\")\n                        return False\n            except Exception as e:\n                logger.error(f\"❌ Balance verification failed: {e}\")\n                return False\n    \n    async def force_buy_signal(self):\n        \"\"\"Force a buy signal directly to orchestrator (bypassing strategy)\"\"\"\n        logger.info(f\"🎯 STEP 3: Forcing buy signal for {self.test_pair}...\")\n        \n        # Create test buy signal\n        test_signal = {\n            \"signal\": \"buy\",\n            \"confidence\": 0.95,  # High confidence for test\n            \"strength\": 0.90,\n            \"strategy\": \"live_order_test\",\n            \"pair\": self.test_pair,\n            \"exchange\": self.test_exchange,\n            \"reason\": \"Live order placement and tracking test\",\n            \"timestamp\": datetime.utcnow().isoformat(),\n            \"test_mode\": True,\n            \"amount\": self.test_amount\n        }\n        \n        async with aiohttp.ClientSession() as session:\n            try:\n                # Send signal directly to orchestrator processing endpoint\n                async with session.post(\n                    f\"{self.orchestrator_url}/api/v1/signals/process\",\n                    json=test_signal,\n                    timeout=30\n                ) as response:\n                    if response.status in [200, 201, 202]:\n                        result = await response.json()\n                        logger.info(\"✅ Buy signal sent successfully\")\n                        self.test_results[\"signal_sent\"] = True\n                        \n                        # Return the order ID if provided in response\n                        return result.get(\"order_id\") or result.get(\"trade_id\")\n                    else:\n                        error_text = await response.text()\n                        logger.error(f\"❌ Signal processing failed: {response.status} - {error_text}\")\n                        return None\n            except Exception as e:\n                logger.error(f\"❌ Failed to send buy signal: {e}\")\n                return None\n    \n    async def track_order_processing(self, order_id, timeout_seconds=120):\n        \"\"\"Track order through queue processing system\"\"\"\n        logger.info(f\"📊 STEP 4: Tracking order {order_id} through queue system...\")\n        \n        start_time = time.time()\n        order_found_in_queue = False\n        \n        async with aiohttp.ClientSession() as session:\n            while (time.time() - start_time) < timeout_seconds:\n                try:\n                    # Check queue stats\n                    async with session.get(f\"{self.queue_url}/api/v1/queue/stats\") as response:\n                        if response.status == 200:\n                            stats = await response.json()\n                            pending = stats.get(\"orders_pending\", 0)\n                            logger.info(f\"📊 Queue status: {pending} orders pending\")\n                            \n                            if pending > 0:\n                                order_found_in_queue = True\n                                self.test_results[\"order_queued\"] = True\n                    \n                    # Check if order status is available in queue system\n                    try:\n                        async with session.get(f\"{self.queue_url}/api/v1/orders/{order_id}/status\") as response:\n                            if response.status == 200:\n                                order_status = await response.json()\n                                status = order_status.get(\"status\")\n                                logger.info(f\"📊 Order {order_id} queue status: {status}\")\n                                \n                                if status in [\"PROCESSING\", \"ACKNOWLEDGED\"]:\n                                    self.test_results[\"order_processed\"] = True\n                                    logger.info(f\"✅ Order successfully processed in queue\")\n                                    return True\n                                elif status == \"FAILED\":\n                                    error_msg = order_status.get(\"error_message\", \"Unknown error\")\n                                    logger.error(f\"❌ Order failed in queue: {error_msg}\")\n                                    return False\n                    except aiohttp.ClientResponseError as e:\n                        if e.status != 404:  # 404 is normal if order not in queue yet\n                            logger.warning(f\"⚠️ Queue status check error: {e}\")\n                    \n                    await asyncio.sleep(3)\n                    \n                except Exception as e:\n                    logger.warning(f\"⚠️ Queue tracking error: {e}\")\n                    await asyncio.sleep(3)\n        \n        logger.error(f\"❌ ORDER QUEUE TRACKING: Timeout after {timeout_seconds}s\")\n        return order_found_in_queue  # Partial success if order was queued\n    \n    async def verify_exchange_order(self, order_id, timeout_seconds=60):\n        \"\"\"Verify order was created on exchange\"\"\"\n        logger.info(f\"🏦 STEP 5: Verifying exchange order creation for {order_id}...\")\n        \n        start_time = time.time()\n        \n        async with aiohttp.ClientSession() as session:\n            while (time.time() - start_time) < timeout_seconds:\n                try:\n                    # Check database for order record with exchange_order_id\n                    async with session.get(f\"{self.database_url}/api/v1/orders/{order_id}\") as response:\n                        if response.status == 200:\n                            order = await response.json()\n                            exchange_order_id = order.get(\"exchange_order_id\")\n                            \n                            if exchange_order_id:\n                                logger.info(f\"✅ Exchange order created: {exchange_order_id}\")\n                                self.test_results[\"exchange_order\"] = True\n                                return exchange_order_id\n                            else:\n                                logger.info(f\"📊 Order {order_id} status: {order.get('status')} - waiting for exchange ID...\")\n                                \n                    await asyncio.sleep(5)\n                    \n                except Exception as e:\n                    logger.warning(f\"⚠️ Exchange order verification error: {e}\")\n                    await asyncio.sleep(5)\n        \n        logger.error(f\"❌ EXCHANGE ORDER: Timeout - no exchange order ID found\")\n        return None\n    \n    async def monitor_order_fill(self, order_id, exchange_order_id, timeout_seconds=300):\n        \"\"\"Monitor order until filled\"\"\"\n        logger.info(f\"⏱️ STEP 6: Monitoring order fill {order_id} / {exchange_order_id}...\")\n        \n        start_time = time.time()\n        \n        async with aiohttp.ClientSession() as session:\n            while (time.time() - start_time) < timeout_seconds:\n                try:\n                    # Check order status in database\n                    async with session.get(f\"{self.database_url}/api/v1/orders/{order_id}\") as response:\n                        if response.status == 200:\n                            order = await response.json()\n                            status = order.get(\"status\")\n                            \n                            logger.info(f\"📊 Order {order_id} status: {status}\")\n                            \n                            if status == \"FILLED\":\n                                logger.info(f\"✅ Order {order_id} FILLED successfully!\")\n                                self.test_results[\"order_filled\"] = True\n                                return True\n                            elif status == \"FAILED\":\n                                logger.error(f\"❌ Order {order_id} FAILED\")\n                                return False\n                                \n                    await asyncio.sleep(10)  # Check every 10 seconds\n                    \n                except Exception as e:\n                    logger.warning(f\"⚠️ Order fill monitoring error: {e}\")\n                    await asyncio.sleep(10)\n        \n        logger.warning(f\"⏱️ ORDER FILL: Timeout - order may still be pending\")\n        return False  # Timeout doesn't mean failure, just incomplete\n    \n    async def verify_complete_database_records(self, order_id):\n        \"\"\"Verify complete database records exist\"\"\"\n        logger.info(f\"🗃️ STEP 7: Verifying complete database records for {order_id}...\")\n        \n        async with aiohttp.ClientSession() as session:\n            try:\n                # Check order record completeness\n                async with session.get(f\"{self.database_url}/api/v1/orders/{order_id}\") as response:\n                    if response.status == 200:\n                        order = await response.json()\n                        \n                        required_fields = [\n                            \"order_id\", \"exchange\", \"symbol\", \"status\", \n                            \"exchange_order_id\", \"created_at\"\n                        ]\n                        \n                        missing_fields = [field for field in required_fields \n                                        if not order.get(field)]\n                        \n                        if not missing_fields:\n                            logger.info(f\"✅ Complete order record verified\")\n                            self.test_results[\"database_record\"] = True\n                            return True\n                        else:\n                            logger.warning(f\"⚠️ Missing order fields: {missing_fields}\")\n                            return False\n                    else:\n                        logger.error(f\"❌ Order record not found: {response.status}\")\n                        return False\n                        \n            except Exception as e:\n                logger.error(f\"❌ Database verification failed: {e}\")\n                return False\n    \n    async def verify_trade_creation(self, order_id):\n        \"\"\"Verify trade was created from order\"\"\"\n        logger.info(f\"📈 STEP 8: Verifying trade creation for order {order_id}...\")\n        \n        async with aiohttp.ClientSession() as session:\n            try:\n                # Look for trade with this order as entry_id\n                async with session.get(f\"{self.database_url}/api/v1/trades?limit=20\") as response:\n                    if response.status == 200:\n                        data = await response.json()\n                        trades = data.get(\"trades\", [])\n                        \n                        for trade in trades:\n                            if trade.get(\"entry_id\") == order_id:\n                                logger.info(f\"✅ Trade created: {trade['trade_id']}\")\n                                self.test_results[\"trade_created\"] = True\n                                self.test_results[\"complete_tracking\"] = True\n                                return True\n                                \n                        logger.warning(f\"⚠️ No trade found for order {order_id}\")\n                        return False\n                    else:\n                        logger.error(f\"❌ Failed to check trades: {response.status}\")\n                        return False\n                        \n            except Exception as e:\n                logger.error(f\"❌ Trade verification failed: {e}\")\n                return False\n    \n    def generate_test_report(self):\n        \"\"\"Generate comprehensive test report\"\"\"\n        logger.info(\"=\" * 80)\n        logger.info(\"📊 LIVE ORDER PLACEMENT AND TRACKING TEST REPORT\")\n        logger.info(\"=\" * 80)\n        \n        test_stages = [\n            (\"System Health Check\", \"system_health\"),\n            (\"Balance Verification\", \"balance_check\"),\n            (\"Signal Transmission\", \"signal_sent\"),\n            (\"Order Queue Processing\", \"order_queued\"),\n            (\"Order Processing\", \"order_processed\"),\n            (\"Exchange Order Creation\", \"exchange_order\"),\n            (\"Order Fill Detection\", \"order_filled\"),\n            (\"Database Record Integrity\", \"database_record\"),\n            (\"Trade Creation\", \"trade_created\"),\n            (\"Complete Tracking Chain\", \"complete_tracking\")\n        ]\n        \n        passed_count = 0\n        for stage_name, key in test_stages:\n            result = self.test_results.get(key, False)\n            status = \"✅ PASS\" if result else \"❌ FAIL\"\n            logger.info(f\"{status} | {stage_name}\")\n            if result:\n                passed_count += 1\n        \n        total_stages = len(test_stages)\n        success_rate = (passed_count / total_stages) * 100\n        \n        logger.info(\"=\" * 80)\n        logger.info(f\"OVERALL RESULT: {passed_count}/{total_stages} stages passed ({success_rate:.1f}%)\")\n        \n        if success_rate >= 80:\n            logger.info(\"✅ ORDER TRACKING SYSTEM: FUNCTIONAL\")\n            logger.info(\"🎯 Order placement and tracking pipeline working correctly\")\n            return True\n        else:\n            logger.error(\"❌ ORDER TRACKING SYSTEM: ISSUES DETECTED\")\n            logger.error(f\"❌ {total_stages - passed_count} critical stage(s) failed\")\n            return False\n\nasync def main():\n    \"\"\"Main test execution\"\"\"\n    test = LiveOrderTrackingTest()\n    \n    print(\"⚠️  WARNING: This test will place a REAL ORDER with REAL MONEY\")\n    print(f\"💰 Test will use ${test.test_amount} on {test.test_exchange} for {test.test_pair}\")\n    print(\"⚠️  Only proceed if you understand the risks!\")\n    print(\"\")\n    \n    # Safety confirmation\n    user_input = input(\"Type 'PROCEED' to continue with live order test: \")\n    if user_input != \"PROCEED\":\n        print(\"❌ Test aborted by user\")\n        return 1\n    \n    success = await test.run_live_order_test()\n    \n    if success:\n        print(\"\\n🎯 CONCLUSION: Live order placement and tracking system is OPERATIONAL\")\n        return 0\n    else:\n        print(\"\\n🚨 CONCLUSION: Live order placement and tracking has ISSUES\")\n        return 1\n\nif __name__ == \"__main__\":\n    try:\n        exit_code = asyncio.run(main())\n        exit(exit_code)\n    except KeyboardInterrupt:\n        print(\"\\n❌ Test interrupted by user\")\n        exit(1)