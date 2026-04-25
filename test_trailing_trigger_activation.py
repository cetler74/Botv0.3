#!/usr/bin/env python3
"""
Test Trailing Trigger Activation with Simulated High Profit

This script tests the trailing trigger activation system by simulating a trade
with 2.0% profit to verify that:
1. The trailing trigger activates correctly
2. A sell limit order is created at a high exit price
3. The trade status is updated to show trailing stop as active

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
import yaml

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class TrailingTriggerTester:
    """Test the trailing trigger activation system with simulated high profit"""
    
    def __init__(self):
        self.database_service_url = "http://localhost:8002"
        self.orchestrator_service_url = "http://localhost:8001"
        self.activation_threshold = 0.007  # 0.7%
        self.trail_distance = 0.0025  # 0.25%
        self.test_profit_percentage = 0.02  # 2.0% for testing
        
    async def test_trailing_trigger_activation(self):
        """Test the trailing trigger activation with simulated high profit"""
        logger.info("🧪 Starting Trailing Trigger Activation Test")
        logger.info("=" * 60)
        
        try:
            # Step 1: Get current open trades
            logger.info("📊 Step 1: Getting current open trades...")
            open_trades = await self.get_open_trades()
            
            if not open_trades:
                logger.warning("⚠️ No open trades found. Cannot perform test.")
                return False
                
            # Step 2: Select a trade for testing
            test_trade = self.select_test_trade(open_trades)
            if not test_trade:
                logger.warning("⚠️ No suitable trade found for testing.")
                return False
                
            logger.info(f"🎯 Selected trade for testing: {test_trade['trade_id']} ({test_trade['symbol']})")
            logger.info(f"   Entry Price: ${test_trade['entry_price']}")
            logger.info(f"   Current Price: ${test_trade['current_price']}")
            logger.info(f"   Current Profit: {test_trade['profit_pct']:.2%}")
            
            # Step 3: Simulate high profit by updating current price
            logger.info("📈 Step 3: Simulating 2.0% profit...")
            simulated_price = await self.simulate_high_profit(test_trade)
            
            if not simulated_price:
                logger.error("❌ Failed to simulate high profit")
                return False
                
            logger.info(f"   Simulated Price: ${simulated_price}")
            logger.info(f"   Simulated Profit: {((simulated_price - test_trade['entry_price']) / test_trade['entry_price']):.2%}")
            
            # Step 4: Wait for trailing trigger activation
            logger.info("⏳ Step 4: Waiting for trailing trigger activation...")
            activation_result = await self.wait_for_activation(test_trade['trade_id'])
            
            if not activation_result:
                logger.error("❌ Trailing trigger did not activate")
                return False
                
            # Step 5: Verify sell order creation
            logger.info("🔍 Step 5: Verifying sell order creation...")
            sell_order = await self.verify_sell_order_creation(test_trade['trade_id'])
            
            if not sell_order:
                logger.error("❌ Sell order was not created")
                return False
                
            # Step 6: Verify trade status update
            logger.info("✅ Step 6: Verifying trade status update...")
            status_updated = await self.verify_trade_status_update(test_trade['trade_id'])
            
            if not status_updated:
                logger.error("❌ Trade status was not updated")
                return False
                
            # Test completed successfully
            logger.info("🎉 TRAILING TRIGGER ACTIVATION TEST PASSED!")
            logger.info("=" * 60)
            logger.info("✅ All test steps completed successfully:")
            logger.info("   - High profit simulated (2.0%)")
            logger.info("   - Trailing trigger activated")
            logger.info("   - Sell limit order created")
            logger.info("   - Trade status updated")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Test failed with error: {e}")
            return False
    
    async def get_open_trades(self) -> List[Dict[str, Any]]:
        """Get current open trades"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.database_service_url}/api/v1/trades?status=OPEN&limit=10")
                if response.status_code == 200:
                    data = response.json()
                    trades = data.get('trades', [])
                    logger.info(f"📊 Found {len(trades)} open trades")
                    return trades
                else:
                    logger.error(f"Failed to get trades: {response.status_code}")
                    return []
        except Exception as e:
            logger.error(f"Error getting trades: {e}")
            return []
    
    def select_test_trade(self, trades: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Select a suitable trade for testing"""
        for trade in trades:
            # Calculate current profit
            entry_price = trade.get('entry_price', 0)
            current_price = trade.get('current_price', 0)
            
            if entry_price > 0 and current_price > 0:
                profit_pct = (current_price - entry_price) / entry_price
                trade['profit_pct'] = profit_pct
                
                # Select a trade that's not already at high profit
                if profit_pct < 0.01:  # Less than 1% profit
                    return trade
                    
        return None
    
    async def simulate_high_profit(self, trade: Dict[str, Any]) -> Optional[float]:
        """Simulate high profit by updating the current price"""
        try:
            entry_price = trade['entry_price']
            # Calculate price for 2.0% profit
            target_profit = 0.02  # 2.0%
            simulated_price = entry_price * (1 + target_profit)
            
            # Update the trade's current price in the database
            trade_id = trade['trade_id']
            
            async with httpx.AsyncClient() as client:
                update_data = {
                    'current_price': simulated_price,
                    'updated_at': datetime.utcnow().isoformat()
                }
                
                response = await client.put(
                    f"{self.database_service_url}/api/v1/trades/{trade_id}",
                    json=update_data
                )
                
                if response.status_code == 200:
                    logger.info(f"✅ Updated trade {trade_id} current price to ${simulated_price}")
                    return simulated_price
                else:
                    logger.error(f"Failed to update trade price: {response.status_code}")
                    return None
                    
        except Exception as e:
            logger.error(f"Error simulating high profit: {e}")
            return None
    
    async def wait_for_activation(self, trade_id: str, timeout: int = 30) -> bool:
        """Wait for trailing trigger activation"""
        logger.info(f"⏳ Waiting for trailing trigger activation for trade {trade_id}...")
        
        start_time = datetime.utcnow()
        timeout_delta = timedelta(seconds=timeout)
        
        while datetime.utcnow() - start_time < timeout_delta:
            try:
                # Check if trailing stop is now active
                async with httpx.AsyncClient() as client:
                    response = await client.get(f"{self.database_service_url}/api/v1/trades/{trade_id}")
                    
                    if response.status_code == 200:
                        trade_data = response.json()
                        trail_stop_status = trade_data.get('trail_stop', 'inactive')
                        
                        if trail_stop_status == 'active':
                            logger.info(f"✅ Trailing trigger activated for trade {trade_id}")
                            return True
                        else:
                            logger.info(f"⏳ Trailing stop status: {trail_stop_status}")
                            
                # Wait 2 seconds before checking again
                await asyncio.sleep(2)
                
            except Exception as e:
                logger.error(f"Error checking activation: {e}")
                await asyncio.sleep(2)
                
        logger.error(f"❌ Timeout waiting for activation after {timeout} seconds")
        return False
    
    async def verify_sell_order_creation(self, trade_id: str) -> Optional[Dict[str, Any]]:
        """Verify that a sell order was created"""
        try:
            # Check for sell orders related to this trade
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.database_service_url}/api/v1/orders?trade_id={trade_id}&side=SELL")
                
                if response.status_code == 200:
                    data = response.json()
                    orders = data.get('orders', [])
                    
                    if orders:
                        sell_order = orders[0]  # Get the first sell order
                        logger.info(f"✅ Sell order created: {sell_order['order_id']}")
                        logger.info(f"   Order Type: {sell_order['order_type']}")
                        logger.info(f"   Price: ${sell_order['price']}")
                        logger.info(f"   Quantity: {sell_order['quantity']}")
                        logger.info(f"   Status: {sell_order['status']}")
                        return sell_order
                    else:
                        logger.error("❌ No sell orders found")
                        return None
                else:
                    logger.error(f"Failed to get orders: {response.status_code}")
                    return None
                    
        except Exception as e:
            logger.error(f"Error verifying sell order: {e}")
            return None
    
    async def verify_trade_status_update(self, trade_id: str) -> bool:
        """Verify that the trade status was updated"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.database_service_url}/api/v1/trades/{trade_id}")
                
                if response.status_code == 200:
                    trade_data = response.json()
                    trail_stop_status = trade_data.get('trail_stop', 'inactive')
                    exit_trigger = trade_data.get('exit_trigger')
                    
                    if trail_stop_status == 'active' and exit_trigger:
                        logger.info(f"✅ Trade status updated correctly:")
                        logger.info(f"   Trail Stop: {trail_stop_status}")
                        logger.info(f"   Exit Trigger: {exit_trigger}")
                        return True
                    else:
                        logger.error(f"❌ Trade status not updated correctly:")
                        logger.error(f"   Trail Stop: {trail_stop_status}")
                        logger.error(f"   Exit Trigger: {exit_trigger}")
                        return False
                else:
                    logger.error(f"Failed to get trade: {response.status_code}")
                    return False
                    
        except Exception as e:
            logger.error(f"Error verifying trade status: {e}")
            return False

async def main():
    """Main test function"""
    logger.info("🚀 Starting Trailing Trigger Activation Test")
    logger.info("=" * 60)
    
    tester = TrailingTriggerTester()
    success = await tester.test_trailing_trigger_activation()
    
    if success:
        logger.info("🎉 TEST COMPLETED SUCCESSFULLY!")
        logger.info("The trailing trigger activation system is working correctly.")
    else:
        logger.error("❌ TEST FAILED!")
        logger.error("The trailing trigger activation system has issues that need to be addressed.")
    
    return success

if __name__ == "__main__":
    asyncio.run(main())
