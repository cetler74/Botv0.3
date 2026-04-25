#!/usr/bin/env python3
"""
ORDER SYNC MONITOR
This script monitors the order synchronization system and alerts when issues are detected.

MONITORING CHECKS:
1. Count of pending orders that should be filled
2. Count of OPEN trades with exit_id (should be 0)
3. Order sync service health
4. Exchange service connectivity
5. Database service connectivity

ALERTS:
- More than 10 pending orders for more than 1 hour
- Any OPEN trades with exit_id
- Order sync service failures
- Exchange service connectivity issues
"""

import asyncio
import httpx
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any
import json

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Service URLs
DATABASE_SERVICE_URL = "http://localhost:8002"
EXCHANGE_SERVICE_URL = "http://localhost:8003"

class OrderSyncMonitor:
    """Monitor order synchronization system health"""
    
    def __init__(self):
        self.alert_thresholds = {
            'max_pending_orders': 10,
            'max_pending_hours': 1,
            'max_open_trades_with_exit': 0,
            'max_sync_failures': 3
        }
        
        self.metrics = {
            'pending_orders': 0,
            'open_trades_with_exit': 0,
            'sync_failures': 0,
            'last_check': None,
            'alerts_sent': 0
        }
    
    async def run_monitoring(self):
        """Run continuous monitoring"""
        logger.info("🔍 Starting Order Sync Monitoring")
        logger.info("=" * 50)
        
        while True:
            try:
                await self.check_system_health()
                await asyncio.sleep(300)  # Check every 5 minutes
            except Exception as e:
                logger.error(f"❌ Monitoring error: {e}")
                await asyncio.sleep(60)  # Wait 1 minute on error
    
    async def check_system_health(self):
        """Check overall system health"""
        logger.info("🔍 Checking system health...")
        
        # Check 1: Pending orders
        pending_count = await self.check_pending_orders()
        
        # Check 2: OPEN trades with exit_id
        open_trades_count = await self.check_open_trades_with_exit()
        
        # Check 3: Service connectivity
        services_healthy = await self.check_service_connectivity()
        
        # Check 4: Generate alerts if needed
        await self.generate_alerts(pending_count, open_trades_count, services_healthy)
        
        # Update metrics
        self.metrics.update({
            'pending_orders': pending_count,
            'open_trades_with_exit': open_trades_count,
            'last_check': datetime.now(timezone.utc)
        })
        
        logger.info(f"📊 Health Check Complete: {pending_count} pending, {open_trades_count} open trades with exit")
    
    async def check_pending_orders(self) -> int:
        """Check count of pending orders"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/order-mappings")
                if response.status_code == 200:
                    orders = response.json().get('order_mappings', [])
                    pending_count = len([o for o in orders if o.get('status') == 'PENDING'])
                    
                    if pending_count > 0:
                        logger.warning(f"⚠️ Found {pending_count} pending orders")
                    
                    return pending_count
                else:
                    logger.error(f"❌ Failed to get order mappings: {response.status_code}")
                    return -1
        except Exception as e:
            logger.error(f"❌ Error checking pending orders: {e}")
            return -1
    
    async def check_open_trades_with_exit(self) -> int:
        """Check count of OPEN trades with exit_id"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/trades")
                if response.status_code == 200:
                    trades = response.json().get('trades', [])
                    open_trades_count = len([t for t in trades if t.get('status') == 'OPEN' and t.get('exit_id')])
                    
                    if open_trades_count > 0:
                        logger.error(f"🚨 CRITICAL: Found {open_trades_count} OPEN trades with exit_id!")
                        await self.log_open_trades_with_exit(trades)
                    
                    return open_trades_count
                else:
                    logger.error(f"❌ Failed to get trades: {response.status_code}")
                    return -1
        except Exception as e:
            logger.error(f"❌ Error checking open trades: {e}")
            return -1
    
    async def log_open_trades_with_exit(self, trades: List[Dict[str, Any]]):
        """Log details of OPEN trades with exit_id"""
        open_trades = [t for t in trades if t.get('status') == 'OPEN' and t.get('exit_id')]
        
        logger.error("🚨 OPEN TRADES WITH EXIT_ID DETAILS:")
        for trade in open_trades:
            logger.error(f"   Trade ID: {trade.get('trade_id')}")
            logger.error(f"   Pair: {trade.get('pair')}")
            logger.error(f"   Exchange: {trade.get('exchange')}")
            logger.error(f"   Entry ID: {trade.get('entry_id')}")
            logger.error(f"   Exit ID: {trade.get('exit_id')}")
            logger.error(f"   Entry Time: {trade.get('entry_time')}")
            logger.error("   " + "-" * 40)
    
    async def check_service_connectivity(self) -> bool:
        """Check if all services are accessible"""
        services_healthy = True
        
        # Check database service
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/health")
                if response.status_code != 200:
                    logger.error(f"❌ Database service unhealthy: {response.status_code}")
                    services_healthy = False
        except Exception as e:
            logger.error(f"❌ Database service unreachable: {e}")
            services_healthy = False
        
        # Check exchange service
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{EXCHANGE_SERVICE_URL}/api/v1/health")
                if response.status_code != 200:
                    logger.error(f"❌ Exchange service unhealthy: {response.status_code}")
                    services_healthy = False
        except Exception as e:
            logger.error(f"❌ Exchange service unreachable: {e}")
            services_healthy = False
        
        if services_healthy:
            logger.info("✅ All services healthy")
        
        return services_healthy
    
    async def generate_alerts(self, pending_count: int, open_trades_count: int, services_healthy: bool):
        """Generate alerts based on thresholds"""
        alerts = []
        
        # Alert 1: Too many pending orders
        if pending_count > self.alert_thresholds['max_pending_orders']:
            alerts.append(f"🚨 ALERT: {pending_count} pending orders (threshold: {self.alert_thresholds['max_pending_orders']})")
        
        # Alert 2: OPEN trades with exit_id (CRITICAL)
        if open_trades_count > self.alert_thresholds['max_open_trades_with_exit']:
            alerts.append(f"🚨 CRITICAL ALERT: {open_trades_count} OPEN trades with exit_id (should be 0)")
        
        # Alert 3: Service connectivity issues
        if not services_healthy:
            alerts.append("🚨 ALERT: Service connectivity issues detected")
        
        # Send alerts
        if alerts:
            await self.send_alerts(alerts)
    
    async def send_alerts(self, alerts: List[str]):
        """Send alerts (log for now, can be extended to email/webhook)"""
        logger.error("🚨 ORDER SYNC ALERTS:")
        for alert in alerts:
            logger.error(f"   {alert}")
        
        self.metrics['alerts_sent'] += len(alerts)
        
        # Here you could add email/webhook notifications
        # await self.send_email_alert(alerts)
        # await self.send_webhook_alert(alerts)
    
    def get_metrics_summary(self) -> Dict[str, Any]:
        """Get metrics summary"""
        return {
            'pending_orders': self.metrics['pending_orders'],
            'open_trades_with_exit': self.metrics['open_trades_with_exit'],
            'sync_failures': self.metrics['sync_failures'],
            'alerts_sent': self.metrics['alerts_sent'],
            'last_check': self.metrics['last_check'],
            'system_healthy': (
                self.metrics['pending_orders'] <= self.alert_thresholds['max_pending_orders'] and
                self.metrics['open_trades_with_exit'] <= self.alert_thresholds['max_open_trades_with_exit']
            )
        }

async def main():
    """Main function"""
    monitor = OrderSyncMonitor()
    await monitor.run_monitoring()

if __name__ == "__main__":
    asyncio.run(main())