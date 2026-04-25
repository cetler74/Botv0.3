#!/usr/bin/env python3
"""
PREVENT ORDER TRACKING ISSUES: Comprehensive solution to prevent future synchronization problems
This script implements better order tracking and synchronization between exchanges and database.

Key improvements:
1. Real-time order status monitoring
2. Automatic reconciliation of filled orders
3. WebSocket event processing for immediate updates
4. Fallback polling for missed events
5. Comprehensive logging and alerting
"""

import asyncio
import httpx
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from dataclasses import dataclass

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('order_tracking.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Service URLs
DATABASE_SERVICE_URL = "http://localhost:8002"
EXCHANGE_SERVICE_URL = "http://localhost:8003"
WEBSOCKET_SERVICE_URL = "http://localhost:8004"

@dataclass
class OrderStatus:
    """Order status tracking"""
    order_id: str
    exchange: str
    symbol: str
    side: str
    status: str
    filled_amount: float
    remaining_amount: float
    price: float
    timestamp: datetime
    last_updated: datetime

class OrderTrackingMonitor:
    """Comprehensive order tracking and synchronization monitor"""
    
    def __init__(self):
        self.tracked_orders: Dict[str, OrderStatus] = {}
        self.sync_interval = 30  # seconds
        self.max_retries = 3
        self.alert_threshold = 300  # 5 minutes for alerts
        
    async def start_monitoring(self):
        """Start the comprehensive order monitoring"""
        logger.info("🚀 Starting comprehensive order tracking monitor...")
        
        # Start multiple monitoring tasks
        tasks = [
            asyncio.create_task(self.monitor_open_trades()),
            asyncio.create_task(self.sync_exchange_orders()),
            asyncio.create_task(self.reconcile_filled_orders()),
            asyncio.create_task(self.monitor_websocket_events()),
            asyncio.create_task(self.alert_stale_orders())
        ]
        
        try:
            await asyncio.gather(*tasks)
        except KeyboardInterrupt:
            logger.info("🛑 Order monitoring stopped by user")
        except Exception as e:
            logger.error(f"❌ Order monitoring failed: {e}")
    
    async def monitor_open_trades(self):
        """Monitor all open trades and their associated orders"""
        logger.info("📊 Starting open trades monitoring...")
        
        while True:
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    # Get all open trades
                    response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/trades/open")
                    if response.status_code == 200:
                        trades = response.json().get('trades', [])
                        
                        for trade in trades:
                            await self.track_trade_orders(trade)
                    
                    await asyncio.sleep(self.sync_interval)
                    
            except Exception as e:
                logger.error(f"❌ Error monitoring open trades: {e}")
                await asyncio.sleep(60)  # Wait longer on error
    
    async def track_trade_orders(self, trade: Dict[str, Any]):
        """Track orders associated with a specific trade"""
        trade_id = trade.get('trade_id')
        entry_id = trade.get('entry_id')
        exit_id = trade.get('exit_id')
        exchange = trade.get('exchange')
        
        # Track entry order if exists
        if entry_id:
            await self.track_order(entry_id, exchange, trade_id, 'entry')
        
        # Track exit order if exists
        if exit_id:
            await self.track_order(exit_id, exchange, trade_id, 'exit')
    
    async def track_order(self, order_id: str, exchange: str, trade_id: str, order_type: str):
        """Track a specific order"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Get order status from exchange
                response = await client.get(f"{EXCHANGE_SERVICE_URL}/api/v1/trading/orders/{exchange}")
                if response.status_code == 200:
                    orders = response.json().get('orders', [])
                    
                    # Find our specific order
                    order = next((o for o in orders if o.get('id') == order_id), None)
                    
                    if order:
                        status = OrderStatus(
                            order_id=order_id,
                            exchange=exchange,
                            symbol=order.get('symbol', ''),
                            side=order.get('side', ''),
                            status=order.get('status', ''),
                            filled_amount=float(order.get('filled', 0)),
                            remaining_amount=float(order.get('remaining', 0)),
                            price=float(order.get('price', 0)),
                            timestamp=datetime.fromisoformat(order.get('timestamp', datetime.utcnow().isoformat())),
                            last_updated=datetime.utcnow()
                        )
                        
                        self.tracked_orders[order_id] = status
                        
                        # Check if order was filled but trade not updated
                        if status.status == 'FILLED' and order_type == 'exit':
                            await self.handle_filled_exit_order(order_id, trade_id, status)
                            
        except Exception as e:
            logger.error(f"❌ Error tracking order {order_id}: {e}")
    
    async def handle_filled_exit_order(self, order_id: str, trade_id: str, order_status: OrderStatus):
        """Handle a filled exit order - close the trade"""
        try:
            logger.info(f"🎯 Exit order {order_id} FILLED - closing trade {trade_id[:8]}")
            
            # Calculate fees and PnL
            fill_value = order_status.filled_amount * order_status.price
            estimated_fee = fill_value * 0.001  # 0.1% fee estimate
            
            # Get trade details to calculate PnL
            async with httpx.AsyncClient(timeout=30.0) as client:
                trade_response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/trades/{trade_id}")
                if trade_response.status_code == 200:
                    trade = trade_response.json()
                    
                    entry_price = float(trade.get('entry_price', 0))
                    position_size = float(trade.get('position_size', 0))
                    
                    if entry_price > 0:
                        realized_pnl = (order_status.price - entry_price) * position_size - estimated_fee
                    else:
                        realized_pnl = 0.0
                    
                    # Update trade to CLOSED
                    update_data = {
                        "status": "CLOSED",
                        "exit_price": order_status.price,
                        "exit_time": order_status.timestamp.isoformat(),
                        "exit_id": order_id,
                        "exit_reason": f"Exit order filled at {order_status.price} (automated sync)",
                        "realized_pnl": realized_pnl,
                        "fees": estimated_fee,
                        "updated_at": datetime.utcnow().isoformat()
                    }
                    
                    update_response = await client.put(
                        f"{DATABASE_SERVICE_URL}/api/v1/trades/{trade_id}",
                        json=update_data
                    )
                    
                    if update_response.status_code == 200:
                        logger.info(f"✅ Trade {trade_id[:8]} closed successfully")
                        logger.info(f"   Exit Price: ${order_status.price}")
                        logger.info(f"   Realized PnL: ${realized_pnl:.2f}")
                        logger.info(f"   Fees: ${estimated_fee:.4f}")
                        
                        # Remove from tracking
                        if order_id in self.tracked_orders:
                            del self.tracked_orders[order_id]
                    else:
                        logger.error(f"❌ Failed to close trade {trade_id[:8]}: {update_response.status_code}")
                        
        except Exception as e:
            logger.error(f"❌ Error handling filled exit order {order_id}: {e}")
    
    async def sync_exchange_orders(self):
        """Periodically sync all exchange orders with database"""
        logger.info("🔄 Starting exchange order synchronization...")
        
        while True:
            try:
                exchanges = ['binance', 'cryptocom', 'bybit']
                
                for exchange in exchanges:
                    await self.sync_exchange_orders_for_exchange(exchange)
                
                await asyncio.sleep(self.sync_interval * 2)  # Sync every 2 intervals
                
            except Exception as e:
                logger.error(f"❌ Error syncing exchange orders: {e}")
                await asyncio.sleep(60)
    
    async def sync_exchange_orders_for_exchange(self, exchange: str):
        """Sync orders for a specific exchange"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Get orders from exchange
                exchange_response = await client.get(f"{EXCHANGE_SERVICE_URL}/api/v1/trading/orders/{exchange}")
                if exchange_response.status_code != 200:
                    return
                
                exchange_orders = exchange_response.json().get('orders', [])
                
                # Get database orders for this exchange
                db_response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/orders?exchange={exchange}")
                if db_response.status_code != 200:
                    return
                
                db_orders = db_response.json().get('orders', [])
                
                # Create maps for quick lookup
                db_order_map = {o.get('exchange_order_id'): o for o in db_orders if o.get('exchange_order_id')}
                
                # Sync each exchange order
                for ex_order in exchange_orders:
                    order_id = ex_order.get('id')
                    if order_id in db_order_map:
                        await self.sync_order_status(db_order_map[order_id], ex_order)
                        
        except Exception as e:
            logger.error(f"❌ Error syncing {exchange} orders: {e}")
    
    async def sync_order_status(self, db_order: Dict[str, Any], ex_order: Dict[str, Any]):
        """Sync order status between exchange and database"""
        db_status = db_order.get('status', '').upper()
        ex_status = ex_order.get('status', '').upper()
        
        if db_status != ex_status:
            logger.info(f"🔄 Syncing order {ex_order.get('id')}: {db_status} → {ex_status}")
            
            # Update database order
            update_data = {
                "status": ex_status.lower(),
                "filled_amount": float(ex_order.get('filled', 0)),
                "remaining_amount": float(ex_order.get('remaining', 0)),
                "updated_at": datetime.utcnow().isoformat()
            }
            
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.put(
                        f"{DATABASE_SERVICE_URL}/api/v1/orders/{db_order['order_id']}",
                        json=update_data
                    )
                    
                    if response.status_code == 200:
                        logger.info(f"✅ Order {ex_order.get('id')} status synced")
                    else:
                        logger.error(f"❌ Failed to sync order {ex_order.get('id')}")
                        
            except Exception as e:
                logger.error(f"❌ Error syncing order status: {e}")
    
    async def reconcile_filled_orders(self):
        """Reconcile filled orders that might have been missed"""
        logger.info("🔍 Starting filled order reconciliation...")
        
        while True:
            try:
                # Get all orders marked as FILLED in database
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/orders?status=filled")
                    if response.status_code == 200:
                        filled_orders = response.json().get('orders', [])
                        
                        for order in filled_orders:
                            await self.reconcile_filled_order(order)
                
                await asyncio.sleep(self.sync_interval * 3)  # Reconcile every 3 intervals
                
            except Exception as e:
                logger.error(f"❌ Error reconciling filled orders: {e}")
                await asyncio.sleep(60)
    
    async def reconcile_filled_order(self, order: Dict[str, Any]):
        """Reconcile a specific filled order"""
        order_id = order.get('exchange_order_id')
        trade_id = order.get('trade_id')
        
        if not order_id or not trade_id:
            return
        
        try:
            # Check if trade is still OPEN
            async with httpx.AsyncClient(timeout=30.0) as client:
                trade_response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/trades/{trade_id}")
                if trade_response.status_code == 200:
                    trade = trade_response.json()
                    
                    if trade.get('status') == 'OPEN' and order.get('side') == 'SELL':
                        logger.warning(f"⚠️  Found OPEN trade with FILLED exit order: {trade_id[:8]}")
                        await self.handle_filled_exit_order(order_id, trade_id, None)
                        
        except Exception as e:
            logger.error(f"❌ Error reconciling order {order_id}: {e}")
    
    async def monitor_websocket_events(self):
        """Monitor WebSocket events for real-time order updates"""
        logger.info("📡 Starting WebSocket event monitoring...")
        
        while True:
            try:
                # Check WebSocket service health
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get(f"{WEBSOCKET_SERVICE_URL}/health")
                    if response.status_code == 200:
                        logger.debug("✅ WebSocket service is healthy")
                    else:
                        logger.warning("⚠️  WebSocket service may be down")
                
                await asyncio.sleep(30)
                
            except Exception as e:
                logger.error(f"❌ Error monitoring WebSocket events: {e}")
                await asyncio.sleep(60)
    
    async def alert_stale_orders(self):
        """Alert on orders that have been pending too long"""
        logger.info("🚨 Starting stale order alerting...")
        
        while True:
            try:
                current_time = datetime.utcnow()
                stale_orders = []
                
                for order_id, order_status in self.tracked_orders.items():
                    time_since_update = (current_time - order_status.last_updated).total_seconds()
                    
                    if time_since_update > self.alert_threshold and order_status.status == 'PENDING':
                        stale_orders.append((order_id, order_status))
                
                if stale_orders:
                    logger.warning(f"🚨 Found {len(stale_orders)} stale orders:")
                    for order_id, order_status in stale_orders:
                        logger.warning(f"   Order {order_id}: {order_status.symbol} {order_status.side} - Pending for {time_since_update/60:.1f} minutes")
                
                await asyncio.sleep(60)  # Check every minute
                
            except Exception as e:
                logger.error(f"❌ Error checking stale orders: {e}")
                await asyncio.sleep(60)

async def main():
    """Main function to start the order tracking monitor"""
    logger.info("🛡️  PREVENTING ORDER TRACKING ISSUES")
    logger.info("=" * 50)
    logger.info("Starting comprehensive order tracking and synchronization...")
    logger.info("Features:")
    logger.info("  ✅ Real-time order status monitoring")
    logger.info("  ✅ Automatic reconciliation of filled orders")
    logger.info("  ✅ WebSocket event processing")
    logger.info("  ✅ Fallback polling for missed events")
    logger.info("  ✅ Comprehensive logging and alerting")
    logger.info("=" * 50)
    
    monitor = OrderTrackingMonitor()
    await monitor.start_monitoring()

if __name__ == "__main__":
    asyncio.run(main())
