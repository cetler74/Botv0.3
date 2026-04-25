#!/usr/bin/env python3
"""
Test Trailing Trigger with Test Prices

This script demonstrates how to use the Test Price API to test
the trailing trigger activation system with controlled pricing.

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

class TrailingTriggerTestWithPrices:
    """Test trailing trigger activation using test price API"""
    
    def __init__(self):
        self.test_price_service_url = "http://localhost:8010"
        self.database_service_url = "http://localhost:8002"
        self.orchestrator_service_url = "http://localhost:8001"
        self.activation_threshold = 0.007  # 0.7%
        
    async def test_trailing_trigger_with_test_prices(self):
        """Test trailing trigger activation using test price API"""
        logger.info("🧪 Testing Trailing Trigger with Test Price API")
        logger.info("=" * 60)
        
        try:
            # Step 1: Check services
            await self.check_services()
            
            # Step 2: Enable test mode
            await self.enable_test_mode()
            
            # Step 3: Get a trade to test with
            test_trade = await self.get_test_trade()
            if not test_trade:
                logger.warning("⚠️ No suitable trade found for testing")
                return False
            
            # Step 4: Set test price to trigger trailing stop
            await self.set_trailing_trigger_price(test_trade)
            
            # Step 5: Wait for trailing trigger activation
            await self.wait_for_activation(test_trade)
            
            # Step 6: Verify results
            success = await self.verify_activation(test_trade)
            
            # Step 7: Clean up
            await self.cleanup_test()
            
            if success:
                logger.info("🎉 TRAILING TRIGGER TEST WITH TEST PRICES PASSED!")
                logger.info("✅ The trailing trigger activation system works correctly with test prices")
            else:
                logger.error("❌ TRAILING TRIGGER TEST WITH TEST PRICES FAILED!")
                
            return success
            
        except Exception as e:
            logger.error(f"❌ Test failed with error: {e}")
            await self.cleanup_test()
            return False
    
    async def check_services(self):
        """Check if all required services are healthy"""
        logger.info("📊 Step 1: Checking services...")
        
        services = [
            ("Test Price Service", f"{self.test_price_service_url}/health"),
            ("Database Service", f"{self.database_service_url}/health"),
            ("Orchestrator Service", f"{self.orchestrator_service_url}/health")
        ]
        
        for service_name, health_url in services:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(health_url)
                    if response.status_code == 200:
                        logger.info(f"✅ {service_name} is healthy")
                    else:
                        raise Exception(f"Service unhealthy: {response.status_code}")
            except Exception as e:
                logger.error(f"❌ {service_name} health check failed: {e}")
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
    
    async def get_test_trade(self) -> Optional[Dict[str, Any]]:
        """Get a suitable trade for testing"""
        logger.info("📊 Step 3: Getting test trade...")
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.database_service_url}/api/v1/trades?status=OPEN&limit=10")
                
                if response.status_code == 200:
                    data = response.json()
                    trades = data.get('trades', [])
                    
                    # Find a trade with low profit (suitable for testing)
                    for trade in trades:
                        entry_price = trade.get('entry_price', 0)
                        current_price = trade.get('current_price', 0)
                        
                        if entry_price > 0 and current_price > 0:
                            profit_pct = (current_price - entry_price) / entry_price
                            
                            # Select a trade with low profit (less than 0.5%)
                            if profit_pct < 0.005:
                                trade['profit_pct'] = profit_pct
                                logger.info(f"✅ Selected trade: {trade['trade_id']} ({trade['symbol']})")
                                logger.info(f"   Entry: ${entry_price:.4f}, Current: ${current_price:.4f}, Profit: {profit_pct:.2%}")
                                return trade
                    
                    logger.warning("⚠️ No suitable trade found (all trades have high profit)")
                    return None
                else:
                    raise Exception(f"Failed to get trades: {response.status_code}")
                    
        except Exception as e:
            logger.error(f"❌ Failed to get test trade: {e}")
            return None
    
    async def set_trailing_trigger_price(self, trade: Dict[str, Any]):
        """Set test price to trigger trailing stop (2% profit)"""
        logger.info("📊 Step 4: Setting test price to trigger trailing stop...")
        
        try:
            entry_price = trade['entry_price']
            # Calculate price for 2% profit (well above 0.7% threshold)
            target_profit = 0.02  # 2%
            target_price = entry_price * (1 + target_profit)
            
            # Set test price override
            override_request = {
                "exchange": trade['exchange'],
                "pair": trade['symbol'],
                "override_type": "fixed",
                "value": target_price,
                "duration_minutes": 10,
                "description": "Test trailing trigger activation (2% profit)"
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.test_price_service_url}/api/v1/prices/override",
                    json=override_request
                )
                
                if response.status_code == 200:
                    result = response.json()
                    logger.info(f"✅ Test price set: {result['message']}")
                    logger.info(f"   Target price: ${target_price:.4f} (2% profit)")
                    logger.info(f"   Original price: ${entry_price:.4f}")
                else:
                    raise Exception(f"Failed to set test price: {response.status_code}")
                    
        except Exception as e:
            logger.error(f"❌ Failed to set test price: {e}")
            raise
    
    async def wait_for_activation(self, trade: Dict[str, Any], timeout: int = 60):
        """Wait for trailing trigger activation"""
        logger.info("📊 Step 5: Waiting for trailing trigger activation...")
        
        start_time = datetime.utcnow()
        timeout_delta = timedelta(seconds=timeout)
        
        while datetime.utcnow() - start_time < timeout_delta:
            try:
                # Check if trailing stop is now active
                async with httpx.AsyncClient() as client:
                    response = await client.get(f"{self.database_service_url}/api/v1/trades/{trade['trade_id']}")
                    
                    if response.status_code == 200:
                        trade_data = response.json()
                        trail_stop_status = trade_data.get('trail_stop', 'inactive')
                        
                        if trail_stop_status == 'active':
                            logger.info(f"✅ Trailing trigger activated for trade {trade['trade_id']}")
                            return True
                        else:
                            logger.info(f"⏳ Trailing stop status: {trail_stop_status}")
                            
                # Wait 5 seconds before checking again
                await asyncio.sleep(5)
                
            except Exception as e:
                logger.error(f"Error checking activation: {e}")
                await asyncio.sleep(5)
                
        logger.error(f"❌ Timeout waiting for activation after {timeout} seconds")
        return False
    
    async def verify_activation(self, trade: Dict[str, Any]) -> bool:
        """Verify that trailing trigger activation worked correctly"""
        logger.info("📊 Step 6: Verifying activation results...")
        
        try:
            # Check trade status
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.database_service_url}/api/v1/trades/{trade['trade_id']}")
                
                if response.status_code == 200:
                    trade_data = response.json()
                    trail_stop_status = trade_data.get('trail_stop', 'inactive')
                    exit_trigger = trade_data.get('exit_trigger')
                    current_price = trade_data.get('current_price', 0)
                    
                    logger.info(f"📊 Trade Status:")
                    logger.info(f"   Trail Stop: {trail_stop_status}")
                    logger.info(f"   Exit Trigger: {exit_trigger}")
                    logger.info(f"   Current Price: ${current_price:.4f}")
                    
                    # Check for sell order creation
                    sell_order = await self.check_sell_order_creation(trade['trade_id'])
                    
                    if trail_stop_status == 'active' and exit_trigger and sell_order:
                        logger.info("✅ All activation checks passed:")
                        logger.info("   - Trail stop is active")
                        logger.info("   - Exit trigger is set")
                        logger.info("   - Sell order was created")
                        return True
                    else:
                        logger.error("❌ Activation verification failed:")
                        logger.error(f"   - Trail stop: {trail_stop_status}")
                        logger.error(f"   - Exit trigger: {exit_trigger}")
                        logger.error(f"   - Sell order: {'Yes' if sell_order else 'No'}")
                        return False
                else:
                    logger.error(f"Failed to get trade: {response.status_code}")
                    return False
                    
        except Exception as e:
            logger.error(f"Error verifying activation: {e}")
            return False
    
    async def check_sell_order_creation(self, trade_id: str) -> bool:
        """Check if a sell order was created"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.database_service_url}/api/v1/orders?trade_id={trade_id}&side=SELL")
                
                if response.status_code == 200:
                    data = response.json()
                    orders = data.get('orders', [])
                    
                    if orders:
                        sell_order = orders[0]
                        logger.info(f"✅ Sell order created: {sell_order['order_id']}")
                        logger.info(f"   Order Type: {sell_order['order_type']}")
                        logger.info(f"   Price: ${sell_order['price']}")
                        logger.info(f"   Quantity: {sell_order['quantity']}")
                        return True
                    else:
                        logger.warning("⚠️ No sell orders found")
                        return False
                else:
                    logger.error(f"Failed to get orders: {response.status_code}")
                    return False
                    
        except Exception as e:
            logger.error(f"Error checking sell order: {e}")
            return False
    
    async def cleanup_test(self):
        """Clean up test overrides"""
        logger.info("📊 Step 7: Cleaning up test...")
        
        try:
            async with httpx.AsyncClient() as client:
                # Clear all overrides
                response = await client.delete(f"{self.test_price_service_url}/api/v1/prices/override")
                
                if response.status_code == 200:
                    result = response.json()
                    logger.info(f"✅ Overrides cleared: {result['message']}")
                else:
                    logger.warning(f"⚠️ Failed to clear overrides: {response.status_code}")
                
                # Disable test mode
                response = await client.post(f"{self.test_price_service_url}/api/v1/test-mode/disable")
                
                if response.status_code == 200:
                    result = response.json()
                    logger.info(f"✅ Test mode disabled: {result['message']}")
                else:
                    logger.warning(f"⚠️ Failed to disable test mode: {response.status_code}")
                    
        except Exception as e:
            logger.error(f"❌ Cleanup failed: {e}")

async def main():
    """Main test function"""
    logger.info("🚀 Starting Trailing Trigger Test with Test Price API")
    logger.info("=" * 60)
    
    tester = TrailingTriggerTestWithPrices()
    success = await tester.test_trailing_trigger_with_test_prices()
    
    if success:
        logger.info("🎉 TEST COMPLETED SUCCESSFULLY!")
        logger.info("The trailing trigger activation system works correctly with test prices.")
    else:
        logger.error("❌ TEST FAILED!")
        logger.error("The trailing trigger activation system has issues.")
    
    return success

if __name__ == "__main__":
    asyncio.run(main())
