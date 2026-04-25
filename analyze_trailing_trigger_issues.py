#!/usr/bin/env python3
"""
Detailed Analysis of Trailing Trigger Activation Issues

This script provides a detailed analysis of the trailing trigger activation system,
including checking for trades that should have been activated but weren't.

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

class TrailingTriggerAnalyzer:
    """Detailed analyzer for trailing trigger activation issues"""
    
    def __init__(self):
        self.database_service_url = "http://localhost:8002"
        self.activation_threshold = 0.007  # 0.7%
        
    async def analyze_all_trades(self):
        """Analyze all trades for trailing trigger issues"""
        logger.info("🔍 Analyzing all trades for trailing trigger issues...")
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Get all trades
                response = await client.get(f"{self.database_service_url}/api/v1/trades?limit=100")
                
                if response.status_code == 200:
                    data = response.json()
                    trades = data.get('trades', [])
                    logger.info(f"📊 Analyzing {len(trades)} total trades")
                    
                    analysis_results = {
                        'total_trades': len(trades),
                        'open_trades': 0,
                        'closed_trades': 0,
                        'trades_that_should_have_activated': [],
                        'trades_with_active_trailing': [],
                        'trades_with_inactive_trailing': [],
                        'profit_analysis': {
                            'profitable_trades': 0,
                            'losing_trades': 0,
                            'trades_above_threshold': 0
                        }
                    }
                    
                    for trade in trades:
                        trade_id = trade.get('trade_id')
                        status = trade.get('status')
                        entry_price = trade.get('entry_price')
                        current_price = trade.get('current_price')
                        exit_price = trade.get('exit_price')
                        trail_stop_status = trade.get('trail_stop', 'inactive')
                        created_at = trade.get('created_at')
                        
                        if status == 'OPEN':
                            analysis_results['open_trades'] += 1
                            
                            if current_price and entry_price:
                                profit_pct = (current_price - entry_price) / entry_price
                                
                                if profit_pct > 0:
                                    analysis_results['profit_analysis']['profitable_trades'] += 1
                                    
                                    if profit_pct >= self.activation_threshold:
                                        analysis_results['profit_analysis']['trades_above_threshold'] += 1
                                        
                                        if trail_stop_status == 'inactive':
                                            issue = {
                                                'trade_id': trade_id,
                                                'entry_price': entry_price,
                                                'current_price': current_price,
                                                'profit_pct': profit_pct,
                                                'trail_stop_status': trail_stop_status,
                                                'created_at': created_at,
                                                'should_be_activated': True
                                            }
                                            analysis_results['trades_that_should_have_activated'].append(issue)
                                            
                                            logger.warning(f"⚠️ ISSUE: Trade {trade_id} - Profit {profit_pct:.2%} >= {self.activation_threshold:.1%} but trail_stop is {trail_stop_status}")
                                        else:
                                            analysis_results['trades_with_active_trailing'].append({
                                                'trade_id': trade_id,
                                                'profit_pct': profit_pct,
                                                'trail_stop_status': trail_stop_status
                                            })
                                            
                                            logger.info(f"✅ GOOD: Trade {trade_id} - Profit {profit_pct:.2%} with active trailing stop")
                                else:
                                    analysis_results['profit_analysis']['losing_trades'] += 1
                                    
                                if trail_stop_status == 'inactive':
                                    analysis_results['trades_with_inactive_trailing'].append({
                                        'trade_id': trade_id,
                                        'profit_pct': profit_pct,
                                        'trail_stop_status': trail_stop_status
                                    })
                        else:
                            analysis_results['closed_trades'] += 1
                    
                    # Summary
                    logger.info(f"\n{'='*60}")
                    logger.info("📊 TRAILING TRIGGER ANALYSIS SUMMARY")
                    logger.info(f"{'='*60}")
                    logger.info(f"Total trades analyzed: {analysis_results['total_trades']}")
                    logger.info(f"Open trades: {analysis_results['open_trades']}")
                    logger.info(f"Closed trades: {analysis_results['closed_trades']}")
                    logger.info(f"Profitable trades: {analysis_results['profit_analysis']['profitable_trades']}")
                    logger.info(f"Trades above 0.7% threshold: {analysis_results['profit_analysis']['trades_above_threshold']}")
                    logger.info(f"Trades that should have activated: {len(analysis_results['trades_that_should_have_activated'])}")
                    logger.info(f"Trades with active trailing: {len(analysis_results['trades_with_active_trailing'])}")
                    
                    if analysis_results['trades_that_should_have_activated']:
                        logger.error(f"\n❌ CRITICAL ISSUES FOUND:")
                        for issue in analysis_results['trades_that_should_have_activated']:
                            logger.error(f"   Trade {issue['trade_id']}: {issue['profit_pct']:.2%} profit but trailing inactive")
                    else:
                        logger.info(f"\n✅ NO CRITICAL ISSUES FOUND - All trades with sufficient profit have active trailing stops")
                    
                    # Save detailed results
                    with open('trailing_trigger_analysis_results.json', 'w') as f:
                        json.dump(analysis_results, f, indent=2, default=str)
                    
                    logger.info(f"\n📄 Detailed results saved to trailing_trigger_analysis_results.json")
                    
                    return analysis_results
                    
                else:
                    logger.error(f"❌ Failed to fetch trades: {response.status_code}")
                    return None
                    
        except Exception as e:
            logger.error(f"❌ Error analyzing trades: {e}")
            return None
    
    async def check_system_status(self):
        """Check the current status of the trailing trigger system"""
        logger.info("🔍 Checking trailing trigger system status...")
        
        try:
            # Check if the orchestrator service is running the new trailing system
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{self.database_service_url}/api/v1/system/status")
                
                if response.status_code == 200:
                    status = response.json()
                    logger.info(f"✅ System status retrieved")
                    return status
                else:
                    logger.warning(f"⚠️ Could not get system status: {response.status_code}")
                    return None
                    
        except Exception as e:
            logger.warning(f"⚠️ Error checking system status: {e}")
            return None
    
    async def test_activation_with_sample_trade(self):
        """Test activation logic with a sample trade scenario"""
        logger.info("🧪 Testing activation logic with sample trade scenario...")
        
        # Create a hypothetical trade that should trigger activation
        sample_trade = {
            'trade_id': 'test-trade-123',
            'entry_price': 100.0,
            'current_price': 100.8,  # 0.8% profit - should activate
            'status': 'OPEN',
            'trail_stop': 'inactive'
        }
        
        profit_pct = (sample_trade['current_price'] - sample_trade['entry_price']) / sample_trade['entry_price']
        should_activate = profit_pct >= self.activation_threshold
        
        logger.info(f"Sample trade: Entry ${sample_trade['entry_price']}, Current ${sample_trade['current_price']}")
        logger.info(f"Profit: {profit_pct:.2%}")
        logger.info(f"Should activate: {should_activate} (threshold: {self.activation_threshold:.1%})")
        
        if should_activate and sample_trade['trail_stop'] == 'inactive':
            logger.error(f"❌ TEST FAILED: Trade should have activated but didn't")
            return False
        else:
            logger.info(f"✅ TEST PASSED: Activation logic working correctly")
            return True

async def main():
    """Main function"""
    analyzer = TrailingTriggerAnalyzer()
    
    logger.info("🚀 Starting detailed trailing trigger analysis...")
    
    # Run all analyses
    results = await analyzer.analyze_all_trades()
    await analyzer.check_system_status()
    await analyzer.test_activation_with_sample_trade()
    
    if results and results['trades_that_should_have_activated']:
        logger.error(f"\n❌ ANALYSIS COMPLETE - Issues found that need attention!")
        sys.exit(1)
    else:
        logger.info(f"\n✅ ANALYSIS COMPLETE - No critical issues found!")
        sys.exit(0)

if __name__ == "__main__":
    asyncio.run(main())
