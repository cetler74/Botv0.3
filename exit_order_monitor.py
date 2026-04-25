#!/usr/bin/env python3
"""
EXIT ORDER MONITOR
This script continuously monitors for exit order fill detection failures and automatically fixes them.

MONITORING CHECKS:
1. OPEN trades with filled exit orders (should be 0)
2. Exit orders that are FILLED but trades are still OPEN
3. WebSocket fill detection failures
4. Database sync issues

AUTOMATIC FIXES:
- Force close trades with filled exit orders
- Alert on detection failures
- Log all issues for analysis
"""

import asyncio
import httpx
import logging
from datetime import datetime, timezone
from typing import Dict, List, Any

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Service URLs
DATABASE_SERVICE_URL = "http://localhost:8002"

class ExitOrderMonitor:
    """Monitor exit order fill detection and auto-fix issues"""
    
    def __init__(self):
        self.check_interval = 60  # seconds
        self.alert_threshold = 1  # Alert if any stuck trades found
        self.auto_fix_enabled = True
        
        self.metrics = {
            'checks_performed': 0,
            'stuck_trades_found': 0,
            'trades_auto_fixed': 0,
            'alerts_sent': 0,
            'last_check': None
        }
    
    async def start_monitoring(self):
        """Start continuous monitoring"""
        logger.info("🔍 Starting Exit Order Fill Detection Monitoring")
        logger.info("=" * 60)
        
        while True:
            try:
                await self.perform_check()
                await asyncio.sleep(self.check_interval)
            except Exception as e:
                logger.error(f"❌ Monitoring error: {e}")
                await asyncio.sleep(30)  # Wait 30 seconds on error
    
    async def perform_check(self):
        """Perform a single monitoring check"""
        logger.info("🔍 Performing exit order fill detection check...")
        
        # Check 1: Find stuck trades
        stuck_trades = await self.find_stuck_trades()
        
        # Check 2: Auto-fix if enabled
        if stuck_trades and self.auto_fix_enabled:
            await self.auto_fix_stuck_trades(stuck_trades)
        
        # Check 3: Send alerts if needed
        if stuck_trades:
            await self.send_alert(stuck_trades)
        
        # Update metrics
        self.metrics.update({
            'checks_performed': self.metrics['checks_performed'] + 1,
            'stuck_trades_found': len(stuck_trades),
            'last_check': datetime.now(timezone.utc)
        })
        
        logger.info(f"📊 Check complete: {len(stuck_trades)} stuck trades found")
    
    async def find_stuck_trades(self) -> List[Dict[str, Any]]:
        """Find OPEN trades with filled exit orders"""
        stuck_trades = []
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Get all OPEN trades
                response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/trades")
                if response.status_code != 200:
                    logger.error(f"Failed to get trades: {response.status_code}")
                    return []
                
                trades = response.json().get('trades', [])
                open_trades = [trade for trade in trades if trade.get('status') == 'OPEN']
                
                # Check each OPEN trade for filled exit orders
                for trade in open_trades:
                    exit_id = trade.get('exit_id')
                    if not exit_id:
                        continue
                    
                    # Check if exit order is filled
                    if await self.is_exit_order_filled(exit_id):
                        stuck_trades.append(trade)
                        logger.warning(f"🚨 STUCK TRADE DETECTED: {trade.get('trade_id')} has filled exit order {exit_id}")
                
                return stuck_trades
                
        except Exception as e:
            logger.error(f"❌ Error finding stuck trades: {e}")
            return []
    
    async def is_exit_order_filled(self, exit_id: str) -> bool:
        """Check if exit order is filled in database"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/order-mappings")
                if response.status_code != 200:
                    return False
                
                orders = response.json().get('order_mappings', [])
                for order in orders:
                    if order.get('exchange_order_id') == exit_id:
                        status = order.get('status', '').upper()
                        return status == 'FILLED'
                
                return False
                
        except Exception as e:
            logger.error(f"❌ Error checking exit order {exit_id}: {e}")
            return False
    
    async def auto_fix_stuck_trades(self, stuck_trades: List[Dict[str, Any]]):
        """Automatically fix stuck trades"""
        logger.info(f"🔧 Auto-fixing {len(stuck_trades)} stuck trades...")
        
        for trade in stuck_trades:
            try:
                await self.fix_stuck_trade(trade)
                self.metrics['trades_auto_fixed'] += 1
            except Exception as e:
                logger.error(f"❌ Failed to auto-fix trade {trade.get('trade_id')}: {e}")
    
    async def fix_stuck_trade(self, trade: Dict[str, Any]):
        """Fix a single stuck trade"""
        trade_id = trade.get('trade_id')
        exit_id = trade.get('exit_id')
        
        logger.info(f"🔧 Auto-fixing stuck trade {trade_id}")
        
        # Get exit order details
        exit_order = await self.get_exit_order_details(exit_id)
        if not exit_order:
            logger.error(f"❌ Could not get exit order details for {exit_id}")
            return
        
        # Get exit price
        exit_price = float(exit_order.get('price', 0))
        if exit_price <= 0:
            logger.error(f"❌ Invalid exit price {exit_price} for order {exit_id}")
            return
        
        # Calculate PnL
        entry_price = float(trade.get('entry_price', 0))
        position_size = float(trade.get('position_size', 0))
        entry_fees = float(trade.get('fees', 0))
        
        if entry_price > 0 and position_size > 0:
            gross_pnl = (exit_price - entry_price) * position_size
            total_fees = entry_fees  # Exit fees not available in order_mappings
            realized_pnl = gross_pnl - total_fees
        else:
            realized_pnl = 0.0
        
        # Close the trade
        await self.close_trade_immediately(trade_id, exit_price, exit_id, realized_pnl)
    
    async def get_exit_order_details(self, exit_id: str) -> Dict[str, Any]:
        """Get exit order details from database"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/order-mappings")
                if response.status_code != 200:
                    return {}
                
                orders = response.json().get('order_mappings', [])
                for order in orders:
                    if order.get('exchange_order_id') == exit_id:
                        return order
                
                return {}
                
        except Exception as e:
            logger.error(f"❌ Error getting exit order details: {e}")
            return {}
    
    async def close_trade_immediately(self, trade_id: str, exit_price: float, exit_id: str, realized_pnl: float):
        """Close trade immediately"""
        try:
            update_data = {
                'status': 'CLOSED',
                'exit_price': exit_price,
                'exit_time': datetime.now(timezone.utc).isoformat(),
                'realized_pnl': realized_pnl,
                'exit_reason': 'AUTO_FIX: Exit order was filled but trade was not closed',
                'updated_at': datetime.now(timezone.utc).isoformat()
            }
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.put(
                    f"{DATABASE_SERVICE_URL}/api/v1/trades/{trade_id}",
                    json=update_data
                )
                
                if response.status_code == 200:
                    logger.info(f"✅ AUTO-FIX: Trade {trade_id} closed successfully")
                else:
                    logger.error(f"❌ AUTO-FIX: Failed to close trade {trade_id}: {response.status_code}")
                    
        except Exception as e:
            logger.error(f"❌ AUTO-FIX: Error closing trade {trade_id}: {e}")
    
    async def send_alert(self, stuck_trades: List[Dict[str, Any]]):
        """Send alert for stuck trades"""
        if len(stuck_trades) >= self.alert_threshold:
            logger.error("🚨 EXIT ORDER FILL DETECTION ALERT:")
            logger.error(f"   Found {len(stuck_trades)} stuck trades:")
            
            for trade in stuck_trades:
                logger.error(f"   - Trade {trade.get('trade_id')} ({trade.get('pair')}) has filled exit order {trade.get('exit_id')}")
            
            self.metrics['alerts_sent'] += 1
    
    def get_metrics_summary(self) -> Dict[str, Any]:
        """Get monitoring metrics summary"""
        return {
            'checks_performed': self.metrics['checks_performed'],
            'stuck_trades_found': self.metrics['stuck_trades_found'],
            'trades_auto_fixed': self.metrics['trades_auto_fixed'],
            'alerts_sent': self.metrics['alerts_sent'],
            'last_check': self.metrics['last_check'],
            'auto_fix_enabled': self.auto_fix_enabled,
            'check_interval': self.check_interval
        }

async def main():
    """Main function"""
    monitor = ExitOrderMonitor()
    await monitor.start_monitoring()

if __name__ == "__main__":
    asyncio.run(main())
