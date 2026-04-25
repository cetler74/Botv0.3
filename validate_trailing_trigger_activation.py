#!/usr/bin/env python3
"""
Trailing Trigger Activation Validation Script

This script validates that the trailing trigger activation system is working correctly
by testing the activation logic, checking recent trades, and verifying the system status.

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

# Add project root to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class TrailingTriggerValidator:
    """Comprehensive validator for trailing trigger activation system"""
    
    def __init__(self):
        self.config = None
        self.database_service_url = "http://localhost:8002"
        self.orchestrator_service_url = "http://localhost:8001"
        self.exchange_service_url = "http://localhost:8003"
        
    async def load_config(self):
        """Load configuration from config.yaml"""
        try:
            with open('config/config.yaml', 'r') as f:
                self.config = yaml.safe_load(f)
            logger.info("✅ Configuration loaded successfully")
        except Exception as e:
            logger.error(f"❌ Failed to load configuration: {e}")
            raise
    
    async def validate_activation_threshold(self):
        """Validate that activation threshold is correctly configured"""
        logger.info("🔍 Validating activation threshold configuration...")
        
        try:
            activation_threshold = self.config.get('trading', {}).get('trailing_stop', {}).get('activation_threshold', 0.007)
            expected_threshold = 0.007  # 0.7%
            
            if abs(activation_threshold - expected_threshold) < 0.0001:
                logger.info(f"✅ Activation threshold correctly set to {activation_threshold:.3f} ({activation_threshold*100:.1f}%)")
                return True
            else:
                logger.error(f"❌ Activation threshold mismatch: {activation_threshold:.3f} (expected {expected_threshold:.3f})")
                return False
                
        except Exception as e:
            logger.error(f"❌ Error validating activation threshold: {e}")
            return False
    
    async def check_recent_trades(self):
        """Check recent trades for trailing trigger activation issues"""
        logger.info("🔍 Checking recent trades for trailing trigger issues...")
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Get recent trades
                response = await client.get(f"{self.database_service_url}/api/v1/trades?limit=20&status=OPEN")
                
                if response.status_code == 200:
                    data = response.json()
                    trades = data.get('trades', [])
                    logger.info(f"📊 Found {len(trades)} recent OPEN trades")
                    
                    issues_found = []
                    
                    for trade in trades:
                        trade_id = trade.get('trade_id')
                        entry_price = trade.get('entry_price')
                        current_price = trade.get('current_price')
                        created_at = trade.get('created_at')
                        trail_stop_status = trade.get('trail_stop', 'inactive')
                        
                        if not current_price or not entry_price:
                            continue
                            
                        # Calculate profit percentage
                        profit_pct = (current_price - entry_price) / entry_price
                        
                        # Check if trade should have been activated
                        activation_threshold = 0.007  # 0.7%
                        
                        if profit_pct >= activation_threshold and trail_stop_status == 'inactive':
                            issue = {
                                'trade_id': trade_id,
                                'entry_price': entry_price,
                                'current_price': current_price,
                                'profit_pct': profit_pct,
                                'should_be_activated': True,
                                'trail_stop_status': trail_stop_status,
                                'created_at': created_at
                            }
                            issues_found.append(issue)
                            
                            logger.warning(f"⚠️ Trade {trade_id}: Profit {profit_pct:.2%} >= {activation_threshold:.1%} but trail_stop is {trail_stop_status}")
                    
                    if issues_found:
                        logger.error(f"❌ Found {len(issues_found)} trades that should have trailing triggers activated")
                        return False, issues_found
                    else:
                        logger.info("✅ No trailing trigger activation issues found in recent trades")
                        return True, []
                        
                else:
                    logger.error(f"❌ Failed to fetch trades: {response.status_code}")
                    return False, []
                    
        except Exception as e:
            logger.error(f"❌ Error checking recent trades: {e}")
            return False, []
    
    async def test_activation_logic(self):
        """Test the activation logic with sample data"""
        logger.info("🧪 Testing activation logic with sample data...")
        
        try:
            # Test cases
            test_cases = [
                {
                    'name': 'Should activate - 0.8% profit',
                    'entry_price': 100.0,
                    'current_price': 100.8,
                    'expected_activation': True
                },
                {
                    'name': 'Should not activate - 0.5% profit',
                    'entry_price': 100.0,
                    'current_price': 100.5,
                    'expected_activation': False
                },
                {
                    'name': 'Should activate - exactly 0.7% profit',
                    'entry_price': 100.0,
                    'current_price': 100.7,
                    'expected_activation': True
                },
                {
                    'name': 'Should not activate - loss',
                    'entry_price': 100.0,
                    'current_price': 99.5,
                    'expected_activation': False
                }
            ]
            
            activation_threshold = 0.007  # 0.7%
            all_passed = True
            
            for test_case in test_cases:
                entry_price = test_case['entry_price']
                current_price = test_case['current_price']
                expected = test_case['expected_activation']
                
                profit_pct = (current_price - entry_price) / entry_price
                should_activate = profit_pct >= activation_threshold
                
                if should_activate == expected:
                    logger.info(f"✅ {test_case['name']}: PASS")
                else:
                    logger.error(f"❌ {test_case['name']}: FAIL (expected {expected}, got {should_activate})")
                    all_passed = False
            
            return all_passed
            
        except Exception as e:
            logger.error(f"❌ Error testing activation logic: {e}")
            return False
    
    async def check_system_health(self):
        """Check the health of the trailing trigger system"""
        logger.info("🏥 Checking system health...")
        
        try:
            health_checks = []
            
            # Check orchestrator service
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get(f"{self.orchestrator_service_url}/health")
                    if response.status_code == 200:
                        health_checks.append(("Orchestrator Service", True))
                    else:
                        health_checks.append(("Orchestrator Service", False))
            except Exception as e:
                health_checks.append(("Orchestrator Service", False))
                logger.warning(f"⚠️ Orchestrator service health check failed: {e}")
            
            # Check database service
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get(f"{self.database_service_url}/health")
                    if response.status_code == 200:
                        health_checks.append(("Database Service", True))
                    else:
                        health_checks.append(("Database Service", False))
            except Exception as e:
                health_checks.append(("Database Service", False))
                logger.warning(f"⚠️ Database service health check failed: {e}")
            
            # Check exchange service
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get(f"{self.exchange_service_url}/health")
                    if response.status_code == 200:
                        health_checks.append(("Exchange Service", True))
                    else:
                        health_checks.append(("Exchange Service", False))
            except Exception as e:
                health_checks.append(("Exchange Service", False))
                logger.warning(f"⚠️ Exchange service health check failed: {e}")
            
            # Report results
            all_healthy = True
            for service_name, is_healthy in health_checks:
                if is_healthy:
                    logger.info(f"✅ {service_name}: Healthy")
                else:
                    logger.error(f"❌ {service_name}: Unhealthy")
                    all_healthy = False
            
            return all_healthy
            
        except Exception as e:
            logger.error(f"❌ Error checking system health: {e}")
            return False
    
    async def validate_trailing_stop_config(self):
        """Validate trailing stop configuration"""
        logger.info("🔍 Validating trailing stop configuration...")
        
        try:
            trailing_config = self.config.get('trading', {}).get('trailing_stop', {})
            
            required_settings = {
                'enabled': True,
                'activation_threshold': 0.007,  # 0.7%
                'step_percentage': 0.0035,      # 0.35%
            }
            
            all_valid = True
            
            for setting, expected_value in required_settings.items():
                actual_value = trailing_config.get(setting)
                
                if setting == 'enabled':
                    if actual_value == expected_value:
                        logger.info(f"✅ {setting}: {actual_value}")
                    else:
                        logger.error(f"❌ {setting}: {actual_value} (expected {expected_value})")
                        all_valid = False
                else:
                    if abs(actual_value - expected_value) < 0.0001:
                        logger.info(f"✅ {setting}: {actual_value:.4f}")
                    else:
                        logger.error(f"❌ {setting}: {actual_value:.4f} (expected {expected_value:.4f})")
                        all_valid = False
            
            return all_valid
            
        except Exception as e:
            logger.error(f"❌ Error validating trailing stop config: {e}")
            return False
    
    async def run_comprehensive_validation(self):
        """Run comprehensive validation of trailing trigger activation system"""
        logger.info("🚀 Starting comprehensive trailing trigger activation validation...")
        
        results = {
            'timestamp': datetime.utcnow().isoformat(),
            'tests': {},
            'overall_status': 'UNKNOWN'
        }
        
        try:
            # Load configuration
            await self.load_config()
            
            # Run all validation tests
            tests = [
                ('config_validation', self.validate_trailing_stop_config),
                ('activation_threshold', self.validate_activation_threshold),
                ('activation_logic', self.test_activation_logic),
                ('system_health', self.check_system_health),
                ('recent_trades', self.check_recent_trades),
            ]
            
            all_passed = True
            
            for test_name, test_func in tests:
                logger.info(f"\n{'='*50}")
                logger.info(f"Running test: {test_name}")
                logger.info(f"{'='*50}")
                
                try:
                    if test_name == 'recent_trades':
                        result, issues = await test_func()
                        results['tests'][test_name] = {
                            'passed': result,
                            'issues': issues
                        }
                    else:
                        result = await test_func()
                        results['tests'][test_name] = {
                            'passed': result
                        }
                    
                    if not result:
                        all_passed = False
                        
                except Exception as e:
                    logger.error(f"❌ Test {test_name} failed with exception: {e}")
                    results['tests'][test_name] = {
                        'passed': False,
                        'error': str(e)
                    }
                    all_passed = False
            
            # Determine overall status
            if all_passed:
                results['overall_status'] = 'PASS'
                logger.info(f"\n{'='*50}")
                logger.info("🎉 ALL TESTS PASSED - Trailing trigger activation system is working correctly!")
                logger.info(f"{'='*50}")
            else:
                results['overall_status'] = 'FAIL'
                logger.error(f"\n{'='*50}")
                logger.error("❌ SOME TESTS FAILED - Trailing trigger activation system has issues!")
                logger.error(f"{'='*50}")
            
            # Save results
            with open('trailing_trigger_validation_results.json', 'w') as f:
                json.dump(results, f, indent=2, default=str)
            
            logger.info(f"📄 Results saved to trailing_trigger_validation_results.json")
            
            return results
            
        except Exception as e:
            logger.error(f"❌ Comprehensive validation failed: {e}")
            results['overall_status'] = 'ERROR'
            results['error'] = str(e)
            return results

async def main():
    """Main function"""
    validator = TrailingTriggerValidator()
    results = await validator.run_comprehensive_validation()
    
    # Exit with appropriate code
    if results['overall_status'] == 'PASS':
        sys.exit(0)
    else:
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
