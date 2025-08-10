#!/usr/bin/env python3
"""
Real-time Balance Update Monitor
Monitors for balance update failures by checking database consistency
"""

import asyncio
import asyncpg
import httpx
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
import json
import os

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class BalanceMonitor:
    """Monitor for balance update failures and inconsistencies"""
    
    def __init__(self):
        self.db_pool = None
        self.services = {
            'database': 'http://localhost:8002',
            'orchestrator': 'http://localhost:8005'
        }
        
    async def initialize(self):
        """Initialize database connection"""
        try:
            self.db_pool = await asyncpg.create_pool(
                host='localhost',
                port=5432,
                database='trading_bot_futures',
                user='carloslarramba',
                password='mypassword',
                min_size=1,
                max_size=5
            )
            logger.info("Database connection pool initialized")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
            return False
    
    async def check_service_health(self) -> Dict[str, bool]:
        """Check if all required services are running"""
        health_status = {}
        
        async with httpx.AsyncClient(timeout=5.0) as client:
            for service_name, service_url in self.services.items():
                try:
                    response = await client.get(f"{service_url}/health")
                    health_status[service_name] = response.status_code == 200
                    if health_status[service_name]:
                        logger.info(f"✅ {service_name.title()} Service: Healthy")
                    else:
                        logger.warning(f"⚠️  {service_name.title()} Service: Unhealthy (Status: {response.status_code})")
                except Exception as e:
                    health_status[service_name] = False
                    logger.error(f"❌ {service_name.title()} Service: Failed ({e})")
        
        return health_status
    
    async def check_trades_without_balance_updates(self) -> List[Dict[str, Any]]:
        """Find trades that may not have corresponding balance updates"""
        query = """
        SELECT 
            t.trade_id, 
            t.exchange, 
            t.entry_time, 
            t.exit_time, 
            t.realized_pnl,
            t.status,
            b.timestamp as last_balance_update,
            b.total_pnl as current_balance_pnl,
            EXTRACT(EPOCH FROM (b.timestamp - t.exit_time)) as update_delay_seconds
        FROM trading.trades t
        LEFT JOIN trading.balance b ON t.exchange = b.exchange 
        WHERE t.status = 'CLOSED' 
          AND t.realized_pnl IS NOT NULL
          AND t.exit_time IS NOT NULL
          AND t.exit_time >= NOW() - INTERVAL '24 hours'
        ORDER BY t.exit_time DESC
        LIMIT 20;
        """
        
        try:
            async with self.db_pool.acquire() as conn:
                results = await conn.fetch(query)
                
                problematic_trades = []
                for row in results:
                    row_dict = dict(row)
                    
                    # Check if balance was updated after trade exit
                    if row_dict['last_balance_update']:
                        delay = row_dict['update_delay_seconds']
                        if delay < -300:  # Balance update more than 5 minutes before exit
                            row_dict['issue'] = f"Balance update {abs(delay):.1f}s before trade exit"
                            problematic_trades.append(row_dict)
                        elif delay > 300:  # Balance update more than 5 minutes after exit
                            row_dict['issue'] = f"Balance update delayed by {delay:.1f}s after trade exit"
                            problematic_trades.append(row_dict)
                    else:
                        row_dict['issue'] = "No balance record found for this exchange"
                        problematic_trades.append(row_dict)
                
                return problematic_trades
                
        except Exception as e:
            logger.error(f"Error checking trades without balance updates: {e}")
            return []
    
    async def check_balance_consistency(self) -> List[Dict[str, Any]]:
        """Check for balance consistency issues"""
        query = """
        SELECT 
            b.exchange,
            b.balance,
            b.available_balance,
            b.total_pnl,
            b.daily_pnl,
            b.timestamp,
            (b.balance - b.available_balance) as locked_balance,
            COALESCE(SUM(CASE WHEN t.status = 'OPEN' THEN (t.entry_price * ABS(t.position_size)) ELSE 0 END), 0) as expected_locked
        FROM trading.balance b
        LEFT JOIN trading.trades t ON b.exchange = t.exchange
        GROUP BY b.exchange, b.balance, b.available_balance, b.total_pnl, b.daily_pnl, b.timestamp
        ORDER BY b.exchange;
        """
        
        try:
            async with self.db_pool.acquire() as conn:
                results = await conn.fetch(query)
                
                inconsistencies = []
                for row in results:
                    row_dict = dict(row)
                    locked_balance = float(row_dict['locked_balance'] or 0)
                    expected_locked = float(row_dict['expected_locked'] or 0)
                    
                    # Check for significant discrepancy (more than $1)
                    if abs(locked_balance - expected_locked) > 1.0:
                        row_dict['locked_balance_discrepancy'] = locked_balance - expected_locked
                        row_dict['issue'] = f"Locked balance mismatch: expected {expected_locked:.2f}, actual {locked_balance:.2f}"
                        inconsistencies.append(row_dict)
                    
                    # Check for negative available balance
                    if float(row_dict['available_balance'] or 0) < 0:
                        row_dict['issue'] = f"Negative available balance: {row_dict['available_balance']}"
                        inconsistencies.append(row_dict)
                
                return inconsistencies
                
        except Exception as e:
            logger.error(f"Error checking balance consistency: {e}")
            return []
    
    async def check_recent_balance_update_failures(self) -> List[Dict[str, Any]]:
        """Check for exchanges that haven't had balance updates recently"""
        query = """
        SELECT 
            b.exchange,
            b.timestamp as last_update,
            COUNT(t.trade_id) as recent_closed_trades,
            EXTRACT(EPOCH FROM (NOW() - b.timestamp)) / 3600 as hours_since_update
        FROM trading.balance b
        LEFT JOIN trading.trades t ON b.exchange = t.exchange 
            AND t.status = 'CLOSED' 
            AND t.exit_time >= NOW() - INTERVAL '2 hours'
        GROUP BY b.exchange, b.timestamp
        HAVING COUNT(t.trade_id) > 0 AND EXTRACT(EPOCH FROM (NOW() - b.timestamp)) / 3600 > 1
        ORDER BY hours_since_update DESC;
        """
        
        try:
            async with self.db_pool.acquire() as conn:
                results = await conn.fetch(query)
                
                stale_balances = []
                for row in results:
                    row_dict = dict(row)
                    row_dict['issue'] = f"Balance not updated for {row_dict['hours_since_update']:.1f} hours despite {row_dict['recent_closed_trades']} recent trades"
                    stale_balances.append(row_dict)
                
                return stale_balances
                
        except Exception as e:
            logger.error(f"Error checking recent balance update failures: {e}")
            return []
    
    async def get_orchestrator_balance_status(self) -> Dict[str, Any]:
        """Get current balance status from orchestrator service"""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{self.services['orchestrator']}/api/v1/risk/exposure")
                if response.status_code == 200:
                    return response.json()
                else:
                    logger.error(f"Failed to get orchestrator balance status: {response.status_code}")
                    return {}
        except Exception as e:
            logger.error(f"Error getting orchestrator balance status: {e}")
            return {}
    
    async def monitor_cycle(self):
        """Run one monitoring cycle"""
        logger.info("🔍 Starting balance update monitoring cycle")
        
        # 1. Check service health
        health_status = await self.check_service_health()
        if not all(health_status.values()):
            logger.warning("⚠️  Some services are not healthy - monitoring may be incomplete")
        
        # 2. Check for trades without proper balance updates
        logger.info("Checking for trades without proper balance updates...")
        problematic_trades = await self.check_trades_without_balance_updates()
        
        if problematic_trades:
            logger.warning(f"🚨 Found {len(problematic_trades)} trades with balance update issues:")
            for trade in problematic_trades:
                logger.warning(f"  Trade {trade['trade_id']}: {trade['issue']}")
        else:
            logger.info("✅ No trades with balance update issues found")
        
        # 3. Check balance consistency
        logger.info("Checking balance consistency...")
        inconsistencies = await self.check_balance_consistency()
        
        if inconsistencies:
            logger.warning(f"🚨 Found {len(inconsistencies)} balance consistency issues:")
            for issue in inconsistencies:
                logger.warning(f"  Exchange {issue['exchange']}: {issue['issue']}")
        else:
            logger.info("✅ No balance consistency issues found")
        
        # 4. Check for stale balance updates
        logger.info("Checking for stale balance updates...")
        stale_balances = await self.check_recent_balance_update_failures()
        
        if stale_balances:
            logger.warning(f"🚨 Found {len(stale_balances)} exchanges with stale balance updates:")
            for stale in stale_balances:
                logger.warning(f"  Exchange {stale['exchange']}: {stale['issue']}")
        else:
            logger.info("✅ No stale balance updates found")
        
        # 5. Get orchestrator balance status
        logger.info("Checking orchestrator balance status...")
        orchestrator_status = await self.get_orchestrator_balance_status()
        
        if orchestrator_status:
            logger.info(f"📊 Orchestrator Balance Status:")
            logger.info(f"  Total Balance: ${orchestrator_status.get('total_balance', 0):.2f}")
            logger.info(f"  Available Balance: ${orchestrator_status.get('available_balance', 0):.2f}")
            logger.info(f"  Active Trades: {orchestrator_status.get('active_trades', 0)}")
            logger.info(f"  Total Exposure: ${orchestrator_status.get('total_exposure', 0):.2f}")
        
        # Summary
        total_issues = len(problematic_trades) + len(inconsistencies) + len(stale_balances)
        if total_issues > 0:
            logger.warning(f"🚨 SUMMARY: Found {total_issues} total balance-related issues")
        else:
            logger.info("✅ SUMMARY: No balance issues detected")
        
        logger.info("🔍 Monitoring cycle completed")
        return total_issues
    
    async def run_continuous_monitoring(self, interval_seconds: int = 300):
        """Run continuous monitoring with specified interval"""
        logger.info(f"🚀 Starting continuous balance monitoring (interval: {interval_seconds}s)")
        
        if not await self.initialize():
            logger.error("Failed to initialize - exiting")
            return
        
        while True:
            try:
                await self.monitor_cycle()
                logger.info(f"⏰ Next check in {interval_seconds} seconds...")
                await asyncio.sleep(interval_seconds)
            except KeyboardInterrupt:
                logger.info("👋 Monitoring stopped by user")
                break
            except Exception as e:
                logger.error(f"Error in monitoring cycle: {e}")
                await asyncio.sleep(30)  # Wait 30 seconds before retrying
        
        if self.db_pool:
            await self.db_pool.close()
    
    async def run_single_check(self):
        """Run a single monitoring check"""
        logger.info("🔍 Running single balance monitoring check")
        
        if not await self.initialize():
            logger.error("Failed to initialize - exiting")
            return
        
        await self.monitor_cycle()
        
        if self.db_pool:
            await self.db_pool.close()

async def main():
    """Main entry point"""
    import sys
    
    monitor = BalanceMonitor()
    
    if len(sys.argv) > 1 and sys.argv[1] == "--continuous":
        interval = int(sys.argv[2]) if len(sys.argv) > 2 else 300
        await monitor.run_continuous_monitoring(interval)
    else:
        await monitor.run_single_check()

if __name__ == "__main__":
    asyncio.run(main())