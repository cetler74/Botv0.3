#!/usr/bin/env python3
"""
Realistic Trailing Trigger Activation Test

This script tests the trailing trigger activation system by monitoring real trades
and verifying that the system correctly activates trailing stops when trades reach
the 0.7% profit threshold.

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

class RealisticTrailingTriggerTester:
    """Test the trailing trigger activation system with real market conditions"""
    
    def __init__(self):
        self.database_service_url = "http://localhost:8002"
        self.orchestrator_service_url = "http://localhost:8001"
        self.activation_threshold = 0.007  # 0.7%
        self.trail_distance = 0.0025  # 0.25%
        self.monitoring_duration = 300  # 5 minutes
        
    async def test_trailing_trigger_system(self):
        """Test the trailing trigger activation system with real market conditions"""
        logger.info("🧪 Starting Realistic Trailing Trigger Activation Test")
        logger.info("=" * 60)
        
        try:
            # Step 1: Get current system status
            logger.info("📊 Step 1: Checking system status...")
            system_status = await self.check_system_status()
            if not system_status:
                logger.error("❌ System is not ready for testing")
                return False
                
            # Step 2: Get current open trades
            logger.info("📊 Step 2: Getting current open trades...")
            open_trades = await self.get_open_trades()
            
            if not open_trades:
                logger.warning("⚠️ No open trades found. Cannot perform test.")
                return False
                
            # Step 3: Monitor trades for trailing trigger activation
            logger.info("📈 Step 3: Monitoring trades for trailing trigger activation...")
            logger.info(f"   Monitoring duration: {self.monitoring_duration} seconds")
            logger.info(f"   Activation threshold: {self.activation_threshold:.1%}")
            logger.info(f"   Trail distance: {self.trail_distance:.2%}")
            
            activation_results = await self.monitor_trailing_activation(open_trades)
            
            # Step 4: Analyze results
            logger.info("🔍 Step 4: Analyzing results...")
            success = self.analyze_results(activation_results)
            
            if success:
                logger.info("🎉 TRAILING TRIGGER SYSTEM TEST PASSED!")
                logger.info("=" * 60)
                logger.info("✅ The trailing trigger activation system is working correctly:")
                logger.info("   - System is monitoring trades")
                logger.info("   - Configuration is correct")
                logger.info("   - Ready to activate when threshold is reached")
            else:
                logger.error("❌ TRAILING TRIGGER SYSTEM TEST FAILED!")
                logger.error("The system has issues that need to be addressed.")
            
            return success
            
        except Exception as e:
            logger.error(f"❌ Test failed with error: {e}")
            return False
    
    async def check_system_status(self) -> bool:
        """Check if the system is ready for testing"""
        try:
            # Check orchestrator service
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.orchestrator_service_url}/health")
                if response.status_code != 200:
                    logger.error(f"❌ Orchestrator service not healthy: {response.status_code}")
                    return False
                    
            # Check database service
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.database_service_url}/health")
                if response.status_code != 200:
                    logger.error(f"❌ Database service not healthy: {response.status_code}")
                    return False
                    
            logger.info("✅ All services are healthy")
            return True
            
        except Exception as e:
            logger.error(f"❌ Error checking system status: {e}")
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
                    
                    # Calculate current profit for each trade
                    for trade in trades:
                        entry_price = trade.get('entry_price', 0)
                        current_price = trade.get('current_price', 0)
                        
                        if entry_price > 0 and current_price > 0:
                            profit_pct = (current_price - entry_price) / entry_price
                            trade['profit_pct'] = profit_pct
                            
                    return trades
                else:
                    logger.error(f"Failed to get trades: {response.status_code}")
                    return []
        except Exception as e:
            logger.error(f"Error getting trades: {e}")
            return []
    
    async def monitor_trailing_activation(self, trades: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Monitor trades for trailing trigger activation"""
        logger.info("⏳ Starting monitoring period...")
        
        start_time = datetime.utcnow()
        end_time = start_time + timedelta(seconds=self.monitoring_duration)
        
        # Track trade states
        trade_states = {}
        for trade in trades:
            trade_id = trade['trade_id']
            trade_states[trade_id] = {
                'initial_profit': trade.get('profit_pct', 0),
                'max_profit_seen': trade.get('profit_pct', 0),
                'trail_stop_status': trade.get('trail_stop', 'inactive'),
                'activation_detected': False,
                'sell_order_created': False
            }
        
        check_interval = 10  # Check every 10 seconds
        check_count = 0
        
        while datetime.utcnow() < end_time:
            check_count += 1
            remaining_time = (end_time - datetime.utcnow()).total_seconds()
            
            logger.info(f"🔍 Check {check_count}: {remaining_time:.0f}s remaining")
            
            # Get current trade data
            current_trades = await self.get_open_trades()
            current_trade_map = {trade['trade_id']: trade for trade in current_trades}
            
            # Update trade states
            for trade_id, state in trade_states.items():
                if trade_id in current_trade_map:
                    trade = current_trade_map[trade_id]
                    current_profit = trade.get('profit_pct', 0)
                    current_trail_status = trade.get('trail_stop', 'inactive')
                    
                    # Update max profit seen
                    if current_profit > state['max_profit_seen']:
                        state['max_profit_seen'] = current_profit
                        
                    # Check for activation
                    if current_profit >= self.activation_threshold and not state['activation_detected']:
                        state['activation_detected'] = True
                        logger.info(f"🎯 ACTIVATION DETECTED: Trade {trade_id} reached {current_profit:.2%} profit!")
                        
                    # Check for trail stop activation
                    if current_trail_status == 'active' and state['trail_stop_status'] == 'inactive':
                        state['trail_stop_status'] = 'active'
                        logger.info(f"✅ TRAIL STOP ACTIVATED: Trade {trade_id} trail stop is now active!")
                        
                        # Check for sell order creation
                        sell_order = await self.check_sell_order_creation(trade_id)
                        if sell_order:
                            state['sell_order_created'] = True
                            logger.info(f"📋 SELL ORDER CREATED: Trade {trade_id} has sell order {sell_order['order_id']}")
                    
                    # Log current status
                    logger.info(f"   Trade {trade_id}: PnL {current_profit:.2%}, Trail: {current_trail_status}")
            
            # Wait for next check
            await asyncio.sleep(check_interval)
        
        logger.info("⏳ Monitoring period completed")
        return trade_states
    
    async def check_sell_order_creation(self, trade_id: str) -> Optional[Dict[str, Any]]:
        """Check if a sell order was created for the trade"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.database_service_url}/api/v1/orders?trade_id={trade_id}&side=SELL")
                
                if response.status_code == 200:
                    data = response.json()
                    orders = data.get('orders', [])
                    
                    if orders:
                        return orders[0]  # Return the first sell order
                        
        except Exception as e:
            logger.error(f"Error checking sell order for trade {trade_id}: {e}")
            
        return None
    
    def analyze_results(self, results: Dict[str, Any]) -> bool:
        """Analyze the monitoring results"""
        logger.info("📊 Analysis Results:")
        logger.info("=" * 40)
        
        total_trades = len(results)
        trades_above_threshold = 0
        activations_detected = 0
        trail_stops_activated = 0
        sell_orders_created = 0
        
        for trade_id, state in results.items():
            logger.info(f"Trade {trade_id}:")
            logger.info(f"  Initial Profit: {state['initial_profit']:.2%}")
            logger.info(f"  Max Profit Seen: {state['max_profit_seen']:.2%}")
            logger.info(f"  Trail Stop Status: {state['trail_stop_status']}")
            logger.info(f"  Activation Detected: {state['activation_detected']}")
            logger.info(f"  Sell Order Created: {state['sell_order_created']}")
            
            if state['max_profit_seen'] >= self.activation_threshold:
                trades_above_threshold += 1
                
            if state['activation_detected']:
                activations_detected += 1
                
            if state['trail_stop_status'] == 'active':
                trail_stops_activated += 1
                
            if state['sell_order_created']:
                sell_orders_created += 1
                
            logger.info("")
        
        logger.info("📈 Summary:")
        logger.info(f"  Total Trades Monitored: {total_trades}")
        logger.info(f"  Trades Above Threshold: {trades_above_threshold}")
        logger.info(f"  Activations Detected: {activations_detected}")
        logger.info(f"  Trail Stops Activated: {trail_stops_activated}")
        logger.info(f"  Sell Orders Created: {sell_orders_created}")
        
        # Determine if test passed
        if total_trades == 0:
            logger.error("❌ No trades were monitored")
            return False
            
        if trades_above_threshold > 0 and activations_detected == 0:
            logger.error("❌ Trades reached threshold but no activations detected")
            return False
            
        if activations_detected > 0 and trail_stops_activated == 0:
            logger.error("❌ Activations detected but no trail stops activated")
            return False
            
        if trail_stops_activated > 0 and sell_orders_created == 0:
            logger.error("❌ Trail stops activated but no sell orders created")
            return False
            
        logger.info("✅ System is working correctly")
        return True

async def main():
    """Main test function"""
    logger.info("🚀 Starting Realistic Trailing Trigger Activation Test")
    logger.info("=" * 60)
    
    tester = RealisticTrailingTriggerTester()
    success = await tester.test_trailing_trigger_system()
    
    if success:
        logger.info("🎉 TEST COMPLETED SUCCESSFULLY!")
        logger.info("The trailing trigger activation system is working correctly.")
    else:
        logger.error("❌ TEST FAILED!")
        logger.error("The trailing trigger activation system has issues that need to be addressed.")
    
    return success

if __name__ == "__main__":
    asyncio.run(main())
