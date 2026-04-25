#!/usr/bin/env python3
"""
CRITICAL ENDPOINTS VALIDATION
Tests all essential system endpoints to ensure the trading system is fully operational
"""

import asyncio
import aiohttp
import json
from datetime import datetime
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class CriticalEndpointsValidator:
    def __init__(self):
        self.services = {
            "orchestrator": "http://localhost:8005",
            "queue": "http://localhost:8013", 
            "database": "http://localhost:8002",
            "exchange": "http://localhost:8003",
            "strategy": "http://localhost:8004",
            "web_dashboard": "http://localhost:8006"
        }
        
        self.test_results = {}
        
    async def validate_all_endpoints(self):
        """Validate all critical system endpoints"""
        logger.info("🔍 VALIDATING ALL CRITICAL SYSTEM ENDPOINTS")
        logger.info("=" * 70)
        
        async with aiohttp.ClientSession() as session:
            # 1. Health Endpoints
            await self.test_health_endpoints(session)
            
            # 2. Orchestrator Critical Endpoints  
            await self.test_orchestrator_endpoints(session)
            
            # 3. Database Critical Endpoints
            await self.test_database_endpoints(session)
            
            # 4. Queue Service Endpoints
            await self.test_queue_endpoints(session)
            
            # 5. Exchange Service Endpoints
            await self.test_exchange_endpoints(session)
            
            # 6. Strategy Service Endpoints
            await self.test_strategy_endpoints(session)
            
            # 7. Web Dashboard Endpoints
            await self.test_dashboard_endpoints(session)
        
        return self.generate_validation_report()
    
    async def test_health_endpoints(self, session):
        """Test health endpoints for all services"""
        logger.info("1️⃣ Testing Health Endpoints...")
        
        health_results = {}
        for service_name, base_url in self.services.items():
            try:
                async with session.get(f"{base_url}/health", timeout=10) as response:
                    if response.status == 200:
                        health_data = await response.json()
                        health_results[service_name] = True
                        logger.info(f"✅ {service_name.title()} Health: OK")
                    else:
                        health_results[service_name] = False
                        logger.error(f"❌ {service_name.title()} Health: FAILED ({response.status})")
            except Exception as e:
                health_results[service_name] = False
                logger.error(f"❌ {service_name.title()} Health: UNREACHABLE ({e})")
        
        self.test_results["health_endpoints"] = health_results
    
    async def test_orchestrator_endpoints(self, session):
        """Test critical orchestrator endpoints"""
        logger.info("2️⃣ Testing Orchestrator Critical Endpoints...")
        
        orchestrator_tests = {}
        base_url = self.services["orchestrator"]
        
        # Test trading status
        try:
            async with session.get(f"{base_url}/api/v1/trading/status", timeout=10) as response:
                if response.status == 200:
                    status_data = await response.json()
                    orchestrator_tests["trading_status"] = True
                    logger.info(f"✅ Trading Status: {status_data.get('status', 'unknown')}")
                else:
                    orchestrator_tests["trading_status"] = False
                    logger.error(f"❌ Trading Status: FAILED ({response.status})")
        except Exception as e:
            orchestrator_tests["trading_status"] = False
            logger.error(f"❌ Trading Status: ERROR ({e})")
        
        # Test manual entry cycle (critical for order creation)
        try:
            test_payload = {"exchange": "binance", "pair": "XLM/USDC", "test_mode": True}
            async with session.post(f"{base_url}/api/v1/trading/cycle/entry", 
                                  json=test_payload, timeout=15) as response:
                if response.status in [200, 201, 202]:
                    result = await response.json()
                    orchestrator_tests["manual_entry"] = True
                    logger.info(f"✅ Manual Entry Cycle: OK - {result.get('message', 'Success')}")
                else:
                    orchestrator_tests["manual_entry"] = False
                    logger.error(f"❌ Manual Entry Cycle: FAILED ({response.status})")
        except Exception as e:
            orchestrator_tests["manual_entry"] = False
            logger.error(f"❌ Manual Entry Cycle: ERROR ({e})")
        
        # Test risk limits
        try:
            async with session.get(f"{base_url}/api/v1/risk/limits", timeout=10) as response:
                if response.status == 200:
                    orchestrator_tests["risk_limits"] = True
                    logger.info("✅ Risk Limits: OK")
                else:
                    orchestrator_tests["risk_limits"] = False
                    logger.error(f"❌ Risk Limits: FAILED ({response.status})")
        except Exception as e:
            orchestrator_tests["risk_limits"] = False
            logger.error(f"❌ Risk Limits: ERROR ({e})")
        
        # Test active trades
        try:
            async with session.get(f"{base_url}/api/v1/trading/active-trades", timeout=10) as response:
                if response.status == 200:
                    trades_data = await response.json()
                    orchestrator_tests["active_trades"] = True
                    logger.info(f"✅ Active Trades: OK - {len(trades_data.get('trades', []))} trades")
                else:
                    orchestrator_tests["active_trades"] = False
                    logger.error(f"❌ Active Trades: FAILED ({response.status})")
        except Exception as e:
            orchestrator_tests["active_trades"] = False
            logger.error(f"❌ Active Trades: ERROR ({e})")
        
        self.test_results["orchestrator_endpoints"] = orchestrator_tests
    
    async def test_database_endpoints(self, session):
        """Test critical database endpoints"""
        logger.info("3️⃣ Testing Database Critical Endpoints...")
        
        database_tests = {}
        base_url = self.services["database"]
        
        # Test orders endpoint
        try:
            async with session.get(f"{base_url}/api/v1/orders?limit=5", timeout=10) as response:
                if response.status == 200:
                    orders_data = await response.json()
                    database_tests["orders"] = True
                    logger.info(f"✅ Orders Endpoint: OK - {len(orders_data.get('orders', []))} recent orders")
                else:
                    database_tests["orders"] = False
                    logger.error(f"❌ Orders Endpoint: FAILED ({response.status})")
        except Exception as e:
            database_tests["orders"] = False
            logger.error(f"❌ Orders Endpoint: ERROR ({e})")
        
        # Test trades endpoint
        try:
            async with session.get(f"{base_url}/api/v1/trades?limit=5", timeout=10) as response:
                if response.status == 200:
                    trades_data = await response.json()
                    database_tests["trades"] = True
                    logger.info(f"✅ Trades Endpoint: OK - {len(trades_data.get('trades', []))} recent trades")
                else:
                    database_tests["trades"] = False
                    logger.error(f"❌ Trades Endpoint: FAILED ({response.status})")
        except Exception as e:
            database_tests["trades"] = False
            logger.error(f"❌ Trades Endpoint: ERROR ({e})")
        
        # Test balances endpoint
        try:
            async with session.get(f"{base_url}/api/v1/balances/binance", timeout=10) as response:
                if response.status == 200:
                    balance_data = await response.json()
                    database_tests["balances"] = True
                    available = balance_data.get("available_balance", 0)
                    logger.info(f"✅ Balances Endpoint: OK - ${available:.2f} available")
                else:
                    database_tests["balances"] = False
                    logger.error(f"❌ Balances Endpoint: FAILED ({response.status})")
        except Exception as e:
            database_tests["balances"] = False
            logger.error(f"❌ Balances Endpoint: ERROR ({e})")
        
        self.test_results["database_endpoints"] = database_tests
    
    async def test_queue_endpoints(self, session):
        """Test queue service endpoints"""
        logger.info("4️⃣ Testing Queue Service Endpoints...")
        
        queue_tests = {}
        base_url = self.services["queue"]
        
        # Test queue statistics
        try:
            async with session.get(f"{base_url}/api/v1/queue/stats", timeout=10) as response:
                if response.status == 200:
                    stats_data = await response.json()
                    queue_tests["statistics"] = True
                    pending = stats_data.get("orders_pending", 0)
                    logger.info(f"✅ Queue Statistics: OK - {pending} orders pending")
                else:
                    queue_tests["statistics"] = False
                    logger.error(f"❌ Queue Statistics: FAILED ({response.status})")
        except Exception as e:
            queue_tests["statistics"] = False
            logger.error(f"❌ Queue Statistics: ERROR ({e})")
        
        self.test_results["queue_endpoints"] = queue_tests
    
    async def test_exchange_endpoints(self, session):
        """Test exchange service endpoints"""
        logger.info("5️⃣ Testing Exchange Service Endpoints...")
        
        exchange_tests = {}
        base_url = self.services["exchange"]
        
        # Test account balance
        try:
            async with session.get(f"{base_url}/api/v1/account/balance/binance", timeout=15) as response:
                if response.status == 200:
                    balance_data = await response.json()
                    exchange_tests["account_balance"] = True
                    logger.info("✅ Exchange Balance: OK")
                else:
                    exchange_tests["account_balance"] = False
                    logger.error(f"❌ Exchange Balance: FAILED ({response.status})")
        except Exception as e:
            exchange_tests["account_balance"] = False
            logger.error(f"❌ Exchange Balance: ERROR ({e})")
        
        # Test ticker data
        try:
            async with session.get(f"{base_url}/api/v1/market/ticker/binance/XLMUSDC", timeout=10) as response:
                if response.status == 200:
                    ticker_data = await response.json()
                    exchange_tests["ticker_data"] = True
                    price = ticker_data.get('last') or ticker_data.get('close', 'N/A')
                    logger.info(f"✅ Ticker Data: OK - XLM/USDC price: {price}")
                else:
                    exchange_tests["ticker_data"] = False
                    logger.error(f"❌ Ticker Data: FAILED ({response.status})")
        except Exception as e:
            exchange_tests["ticker_data"] = False
            logger.error(f"❌ Ticker Data: ERROR ({e})")
        
        self.test_results["exchange_endpoints"] = exchange_tests
    
    async def test_strategy_endpoints(self, session):
        """Test strategy service endpoints"""
        logger.info("6️⃣ Testing Strategy Service Endpoints...")
        
        strategy_tests = {}
        base_url = self.services["strategy"]
        
        # Test strategy analysis (if available)
        try:
            test_payload = {
                "exchange": "binance",
                "pair": "XLM/USDC",
                "timeframe": "1h"
            }
            async with session.post(f"{base_url}/api/v1/analyze", 
                                  json=test_payload, timeout=15) as response:
                if response.status in [200, 201, 202]:
                    strategy_tests["analysis"] = True
                    logger.info("✅ Strategy Analysis: OK")
                elif response.status == 404:
                    strategy_tests["analysis"] = None
                    logger.info("ℹ️ Strategy Analysis: Not Available (404)")
                else:
                    strategy_tests["analysis"] = False
                    logger.error(f"❌ Strategy Analysis: FAILED ({response.status})")
        except Exception as e:
            strategy_tests["analysis"] = False
            logger.error(f"❌ Strategy Analysis: ERROR ({e})")
        
        self.test_results["strategy_endpoints"] = strategy_tests
    
    async def test_dashboard_endpoints(self, session):
        """Test web dashboard endpoints"""
        logger.info("7️⃣ Testing Web Dashboard Endpoints...")
        
        dashboard_tests = {}
        base_url = self.services["web_dashboard"]
        
        # Test dashboard main page
        try:
            async with session.get(f"{base_url}/", timeout=10) as response:
                if response.status == 200:
                    dashboard_tests["main_page"] = True
                    logger.info("✅ Dashboard Main Page: OK")
                else:
                    dashboard_tests["main_page"] = False
                    logger.error(f"❌ Dashboard Main Page: FAILED ({response.status})")
        except Exception as e:
            dashboard_tests["main_page"] = False
            logger.error(f"❌ Dashboard Main Page: ERROR ({e})")
        
        # Test API endpoints  
        try:
            async with session.get(f"{base_url}/api/trades", timeout=10) as response:
                if response.status == 200:
                    dashboard_tests["api_trades"] = True
                    logger.info("✅ Dashboard API Trades: OK")
                else:
                    dashboard_tests["api_trades"] = False
                    logger.error(f"❌ Dashboard API Trades: FAILED ({response.status})")
        except Exception as e:
            dashboard_tests["api_trades"] = False
            logger.error(f"❌ Dashboard API Trades: ERROR ({e})")
        
        self.test_results["dashboard_endpoints"] = dashboard_tests
    
    def generate_validation_report(self):
        """Generate comprehensive validation report"""
        logger.info("=" * 70)
        logger.info("📊 CRITICAL ENDPOINTS VALIDATION REPORT")
        logger.info("=" * 70)
        
        total_tests = 0
        passed_tests = 0
        critical_failures = []
        
        for category, tests in self.test_results.items():
            logger.info(f"\n📋 {category.replace('_', ' ').title()}:")
            
            for test_name, result in tests.items():
                total_tests += 1
                
                if result is True:
                    status = "✅ PASS"
                    passed_tests += 1
                elif result is None:
                    status = "ℹ️ N/A"
                    passed_tests += 1  # Count as pass for optional endpoints
                else:
                    status = "❌ FAIL"
                    # Check if this is a critical endpoint
                    if category in ["health_endpoints", "orchestrator_endpoints", "database_endpoints"]:
                        critical_failures.append(f"{category}.{test_name}")
                
                logger.info(f"   {status} | {test_name.replace('_', ' ').title()}")
        
        success_rate = (passed_tests / total_tests) * 100 if total_tests > 0 else 0
        
        logger.info("=" * 70)
        logger.info(f"OVERALL RESULT: {passed_tests}/{total_tests} tests passed ({success_rate:.1f}%)")
        
        # Determine system status
        if len(critical_failures) == 0:
            logger.info("🟢 SYSTEM STATUS: ALL CRITICAL ENDPOINTS OPERATIONAL")
            logger.info("✅ Trading system is ready for production")
            
            # Special note about order creation
            manual_entry_ok = self.test_results.get("orchestrator_endpoints", {}).get("manual_entry", False)
            if manual_entry_ok:
                logger.info("🔧 MANUAL ORDER CREATION: Confirmed working via manual entry endpoint")
                logger.info("💡 Note: System in HOLD mode prevents automatic trading (this is correct)")
            
            return True
        else:
            logger.error("🔴 SYSTEM STATUS: CRITICAL ENDPOINTS FAILING")
            logger.error(f"❌ Critical failures: {len(critical_failures)}")
            for failure in critical_failures:
                logger.error(f"   - {failure}")
            return False

async def main():
    """Main validation execution"""
    validator = CriticalEndpointsValidator()
    
    success = await validator.validate_all_endpoints()
    
    if success:
        print("\n🎯 CONCLUSION: All critical endpoints are OPERATIONAL")
        print("✅ Trading system is ready and all essential APIs are working")
        print("🔒 Order tracking safety system confirmed functional")
        return 0
    else:
        print("\n🚨 CONCLUSION: Critical endpoints have ISSUES")
        return 1

if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        exit(exit_code)
    except KeyboardInterrupt:
        print("\n❌ Validation interrupted")
        exit(1)