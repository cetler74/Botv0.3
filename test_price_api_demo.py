#!/usr/bin/env python3
"""
Test Price API Demo Script

This script demonstrates how to use the Test Price API to test
entry and exit strategies with controlled pricing scenarios.

Author: Claude Code
Created: 2025-01-27
"""

import asyncio
import logging
import sys
import os
import json
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
import httpx

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class TestPriceAPIDemo:
    """Demo class for Test Price API functionality"""
    
    def __init__(self):
        self.test_price_service_url = "http://localhost:8010"
        self.exchange_service_url = "http://localhost:8003"
        
    async def run_demo(self):
        """Run the complete demo"""
        logger.info("🚀 Starting Test Price API Demo")
        logger.info("=" * 60)
        
        try:
            # Step 1: Check service health
            await self.check_service_health()
            
            # Step 2: Enable test mode
            await self.enable_test_mode()
            
            # Step 3: Demonstrate different override types
            await self.demo_fixed_price_override()
            await self.demo_percentage_override()
            await self.demo_scenario_override()
            
            # Step 4: Test integration with exchange service
            await self.test_exchange_integration()
            
            # Step 5: Clean up
            await self.cleanup_demo()
            
            logger.info("🎉 Demo completed successfully!")
            
        except Exception as e:
            logger.error(f"❌ Demo failed: {e}")
            await self.cleanup_demo()
    
    async def check_service_health(self):
        """Check if the test price service is healthy"""
        logger.info("📊 Step 1: Checking service health...")
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.test_price_service_url}/health")
                
                if response.status_code == 200:
                    health_data = response.json()
                    logger.info(f"✅ Test Price Service is healthy: {health_data['status']}")
                else:
                    raise Exception(f"Service unhealthy: {response.status_code}")
                    
        except Exception as e:
            logger.error(f"❌ Service health check failed: {e}")
            raise
    
    async def enable_test_mode(self):
        """Enable test mode"""
        logger.info("📊 Step 2: Enabling test mode...")
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(f"{self.test_price_service_url}/api/v1/test-mode/enable")
                
                if response.status_code == 200:
                    result = response.json()
                    logger.info(f"✅ Test mode enabled: {result['message']}")
                else:
                    raise Exception(f"Failed to enable test mode: {response.status_code}")
                    
        except Exception as e:
            logger.error(f"❌ Failed to enable test mode: {e}")
            raise
    
    async def demo_fixed_price_override(self):
        """Demonstrate fixed price override"""
        logger.info("📊 Step 3a: Demonstrating fixed price override...")
        
        try:
            # Set a fixed price override
            override_request = {
                "exchange": "binance",
                "pair": "BTC/USDC",
                "override_type": "fixed",
                "value": 50000.0,
                "duration_minutes": 5,
                "description": "Demo: Fixed price at $50,000"
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.test_price_service_url}/api/v1/prices/override",
                    json=override_request
                )
                
                if response.status_code == 200:
                    result = response.json()
                    logger.info(f"✅ Fixed price override set: {result['message']}")
                    
                    # Verify the override
                    await self.verify_override("binance", "BTC/USDC", 50000.0)
                else:
                    raise Exception(f"Failed to set fixed price override: {response.status_code}")
                    
        except Exception as e:
            logger.error(f"❌ Fixed price override demo failed: {e}")
    
    async def demo_percentage_override(self):
        """Demonstrate percentage override"""
        logger.info("📊 Step 3b: Demonstrating percentage override...")
        
        try:
            # Set a percentage override (2% profit)
            override_request = {
                "exchange": "binance",
                "pair": "ETH/USDC",
                "override_type": "percentage",
                "value": 0.02,  # 2% profit
                "duration_minutes": 5,
                "description": "Demo: 2% profit scenario"
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.test_price_service_url}/api/v1/prices/override",
                    json=override_request
                )
                
                if response.status_code == 200:
                    result = response.json()
                    logger.info(f"✅ Percentage override set: {result['message']}")
                    
                    # Get the calculated override price
                    override_price = result['data']['override_price']
                    await self.verify_override("binance", "ETH/USDC", override_price)
                else:
                    raise Exception(f"Failed to set percentage override: {response.status_code}")
                    
        except Exception as e:
            logger.error(f"❌ Percentage override demo failed: {e}")
    
    async def demo_scenario_override(self):
        """Demonstrate scenario override"""
        logger.info("📊 Step 3c: Demonstrating scenario override...")
        
        try:
            # Set a scenario override (trailing trigger)
            override_request = {
                "exchange": "binance",
                "pair": "LINK/USDC",
                "override_type": "scenario",
                "value": "trailing_trigger",  # 0.7% profit
                "duration_minutes": 5,
                "description": "Demo: Trailing trigger scenario"
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.test_price_service_url}/api/v1/prices/override",
                    json=override_request
                )
                
                if response.status_code == 200:
                    result = response.json()
                    logger.info(f"✅ Scenario override set: {result['message']}")
                    
                    # Get the calculated override price
                    override_price = result['data']['override_price']
                    await self.verify_override("binance", "LINK/USDC", override_price)
                else:
                    raise Exception(f"Failed to set scenario override: {response.status_code}")
                    
        except Exception as e:
            logger.error(f"❌ Scenario override demo failed: {e}")
    
    async def verify_override(self, exchange: str, pair: str, expected_price: float):
        """Verify that an override is working correctly"""
        try:
            async with httpx.AsyncClient() as client:
                # Check test price
                response = await client.get(
                    f"{self.test_price_service_url}/api/v1/prices/test/{exchange}/{pair.replace('/', '')}"
                )
                
                if response.status_code == 200:
                    data = response.json()
                    test_price = data.get('test_price')
                    
                    if test_price:
                        logger.info(f"✅ Override verified: {exchange}/{pair} = ${test_price:.2f}")
                        
                        # Check if price matches expected (within 1% tolerance)
                        if abs(test_price - expected_price) / expected_price < 0.01:
                            logger.info(f"✅ Price matches expected value: ${expected_price:.2f}")
                        else:
                            logger.warning(f"⚠️ Price mismatch: expected ${expected_price:.2f}, got ${test_price:.2f}")
                    else:
                        logger.warning(f"⚠️ No test price found for {exchange}/{pair}")
                        
        except Exception as e:
            logger.error(f"❌ Override verification failed: {e}")
    
    async def test_exchange_integration(self):
        """Test integration with exchange service"""
        logger.info("📊 Step 4: Testing exchange service integration...")
        
        try:
            # Test getting price from exchange service (should return test price if override exists)
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.exchange_service_url}/api/v1/market/ticker/binance/BTCUSDC"
                )
                
                if response.status_code == 200:
                    ticker_data = response.json()
                    price = ticker_data.get('last', 0)
                    is_test_override = ticker_data.get('test_override', False)
                    
                    if is_test_override:
                        logger.info(f"✅ Exchange service returning test price: ${price:.2f}")
                        logger.info(f"✅ Test override flag: {is_test_override}")
                    else:
                        logger.info(f"ℹ️ Exchange service returning real price: ${price:.2f}")
                        logger.info("ℹ️ No test override applied")
                else:
                    logger.warning(f"⚠️ Exchange service request failed: {response.status_code}")
                    
        except Exception as e:
            logger.error(f"❌ Exchange integration test failed: {e}")
    
    async def cleanup_demo(self):
        """Clean up demo overrides"""
        logger.info("📊 Step 5: Cleaning up demo...")
        
        try:
            async with httpx.AsyncClient() as client:
                # Clear all overrides
                response = await client.delete(f"{self.test_price_service_url}/api/v1/prices/override")
                
                if response.status_code == 200:
                    result = response.json()
                    logger.info(f"✅ Cleanup completed: {result['message']}")
                else:
                    logger.warning(f"⚠️ Cleanup failed: {response.status_code}")
                
                # Disable test mode
                response = await client.post(f"{self.test_price_service_url}/api/v1/test-mode/disable")
                
                if response.status_code == 200:
                    result = response.json()
                    logger.info(f"✅ Test mode disabled: {result['message']}")
                else:
                    logger.warning(f"⚠️ Failed to disable test mode: {response.status_code}")
                    
        except Exception as e:
            logger.error(f"❌ Cleanup failed: {e}")
    
    async def show_available_scenarios(self):
        """Show available test scenarios"""
        logger.info("📊 Available test scenarios:")
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.test_price_service_url}/api/v1/scenarios")
                
                if response.status_code == 200:
                    data = response.json()
                    scenarios = data.get('scenarios', {})
                    
                    for scenario_name, scenario_info in scenarios.items():
                        description = scenario_info.get('description', 'No description')
                        percentage = scenario_info.get('percentage_change', 0) * 100
                        logger.info(f"  • {scenario_name}: {description} ({percentage:+.1f}%)")
                else:
                    logger.warning(f"⚠️ Failed to get scenarios: {response.status_code}")
                    
        except Exception as e:
            logger.error(f"❌ Failed to get scenarios: {e}")

async def main():
    """Main demo function"""
    demo = TestPriceAPIDemo()
    
    # Show available scenarios first
    await demo.show_available_scenarios()
    print()
    
    # Run the main demo
    await demo.run_demo()

if __name__ == "__main__":
    asyncio.run(main())
