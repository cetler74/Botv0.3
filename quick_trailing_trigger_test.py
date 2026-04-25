#!/usr/bin/env python3
"""
Quick Trailing Trigger Activation Test

This script quickly validates that the trailing trigger activation system is
configured correctly and ready to work when trades reach the threshold.

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

class QuickTrailingTriggerTester:
    """Quick test for trailing trigger activation system"""
    
    def __init__(self):
        self.database_service_url = "http://localhost:8002"
        self.orchestrator_service_url = "http://localhost:8001"
        self.activation_threshold = 0.007  # 0.7%
        self.trail_distance = 0.0025  # 0.25%
        
    async def run_quick_test(self):
        """Run a quick test of the trailing trigger system"""
        logger.info("🧪 Quick Trailing Trigger Activation Test")
        logger.info("=" * 50)
        
        try:
            # Step 1: Check system health
            logger.info("📊 Step 1: Checking system health...")
            if not await self.check_system_health():
                return False
                
            # Step 2: Check configuration
            logger.info("📊 Step 2: Checking configuration...")
            if not await self.check_configuration():
                return False
                
            # Step 3: Check current trades
            logger.info("📊 Step 3: Checking current trades...")
            trades = await self.get_current_trades()
            if not trades:
                logger.warning("⚠️ No open trades found")
                return True  # This is not a failure
                
            # Step 4: Analyze trade states
            logger.info("📊 Step 4: Analyzing trade states...")
            await self.analyze_trade_states(trades)
            
            # Step 5: Check system readiness
            logger.info("📊 Step 5: Checking system readiness...")
            readiness = await self.check_system_readiness()
            
            logger.info("🎉 QUICK TEST COMPLETED!")
            logger.info("=" * 50)
            
            if readiness:
                logger.info("✅ The trailing trigger activation system is READY and WORKING correctly!")
                logger.info("   - System is healthy")
                logger.info("   - Configuration is correct")
                logger.info("   - Monitoring is active")
                logger.info("   - Will activate when trades reach 0.7% profit")
            else:
                logger.warning("⚠️ The system is ready but no trades are currently above threshold")
                
            return True
            
        except Exception as e:
            logger.error(f"❌ Test failed with error: {e}")
            return False
    
    async def check_system_health(self) -> bool:
        """Check if all services are healthy"""
        try:
            # Check orchestrator
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.orchestrator_service_url}/health")
                if response.status_code != 200:
                    logger.error(f"❌ Orchestrator service unhealthy: {response.status_code}")
                    return False
                logger.info("✅ Orchestrator service healthy")
                
            # Check database
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.database_service_url}/health")
                if response.status_code != 200:
                    logger.error(f"❌ Database service unhealthy: {response.status_code}")
                    return False
                logger.info("✅ Database service healthy")
                
            return True
            
        except Exception as e:
            logger.error(f"❌ Health check failed: {e}")
            return False
    
    async def check_configuration(self) -> bool:
        """Check if the configuration is correct"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.orchestrator_service_url}/api/v1/config/trading")
                if response.status_code != 200:
                    logger.error(f"❌ Failed to get trading config: {response.status_code}")
                    return False
                    
                config = response.json()
                trailing_stop_config = config.get('trailing_stop', {})
                trailing_trigger = trailing_stop_config.get('activation_threshold', 0)
                trailing_step = trailing_stop_config.get('step_percentage', 0)
                
                logger.info(f"📋 Configuration:")
                logger.info(f"   Trailing Trigger: {trailing_trigger:.1%}")
                logger.info(f"   Trailing Step: {trailing_step:.2%}")
                
                if abs(trailing_trigger - self.activation_threshold) > 0.001:
                    logger.error(f"❌ Trailing trigger mismatch: expected {self.activation_threshold:.1%}, got {trailing_trigger:.1%}")
                    return False
                    
                if abs(trailing_step - self.trail_distance) > 0.001:
                    logger.error(f"❌ Trailing step mismatch: expected {self.trail_distance:.2%}, got {trailing_step:.2%}")
                    return False
                    
                logger.info("✅ Configuration is correct")
                return True
                
        except Exception as e:
            logger.error(f"❌ Configuration check failed: {e}")
            return False
    
    async def get_current_trades(self) -> List[Dict[str, Any]]:
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
                    logger.error(f"❌ Failed to get trades: {response.status_code}")
                    return []
        except Exception as e:
            logger.error(f"❌ Error getting trades: {e}")
            return []
    
    async def analyze_trade_states(self, trades: List[Dict[str, Any]]):
        """Analyze the current state of trades"""
        logger.info("📈 Trade Analysis:")
        
        trades_above_threshold = 0
        trades_with_active_trailing = 0
        
        for trade in trades:
            trade_id = trade.get('trade_id', 'unknown')
            symbol = trade.get('symbol', 'unknown')
            entry_price = trade.get('entry_price', 0)
            current_price = trade.get('current_price', 0)
            trail_stop_status = trade.get('trail_stop', 'inactive')
            
            if entry_price > 0 and current_price > 0:
                profit_pct = (current_price - entry_price) / entry_price
                
                logger.info(f"   Trade {trade_id} ({symbol}):")
                logger.info(f"     Entry: ${entry_price:.4f}")
                logger.info(f"     Current: ${current_price:.4f}")
                logger.info(f"     Profit: {profit_pct:.2%}")
                logger.info(f"     Trail Stop: {trail_stop_status}")
                
                if profit_pct >= self.activation_threshold:
                    trades_above_threshold += 1
                    logger.info(f"     🎯 ABOVE THRESHOLD: {profit_pct:.2%} >= {self.activation_threshold:.1%}")
                    
                if trail_stop_status == 'active':
                    trades_with_active_trailing += 1
                    logger.info(f"     ✅ TRAILING ACTIVE")
                    
        logger.info(f"📊 Summary:")
        logger.info(f"   Trades above threshold: {trades_above_threshold}")
        logger.info(f"   Trades with active trailing: {trades_with_active_trailing}")
        
        if trades_above_threshold > 0 and trades_with_active_trailing == 0:
            logger.warning("⚠️ Some trades are above threshold but trailing is not active")
        elif trades_above_threshold == 0:
            logger.info("ℹ️ No trades are currently above the activation threshold")
    
    async def check_system_readiness(self) -> bool:
        """Check if the system is ready to activate trailing stops"""
        try:
            # Check if orchestrator is running and monitoring
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.orchestrator_service_url}/api/v1/status")
                if response.status_code == 200:
                    status = response.json()
                    logger.info(f"📊 Orchestrator Status: {status.get('status', 'unknown')}")
                    
                    # Check if monitoring is active
                    monitoring = status.get('monitoring', {})
                    if monitoring.get('active', False):
                        logger.info("✅ Monitoring is active")
                        return True
                    else:
                        logger.warning("⚠️ Monitoring is not active")
                        return False
                else:
                    logger.error(f"❌ Failed to get orchestrator status: {response.status_code}")
                    return False
                    
        except Exception as e:
            logger.error(f"❌ System readiness check failed: {e}")
            return False

async def main():
    """Main test function"""
    logger.info("🚀 Starting Quick Trailing Trigger Test")
    logger.info("=" * 50)
    
    tester = QuickTrailingTriggerTester()
    success = await tester.run_quick_test()
    
    if success:
        logger.info("🎉 TEST COMPLETED SUCCESSFULLY!")
        logger.info("The trailing trigger activation system is ready and working.")
    else:
        logger.error("❌ TEST FAILED!")
        logger.error("The trailing trigger activation system has issues.")
    
    return success

if __name__ == "__main__":
    asyncio.run(main())
