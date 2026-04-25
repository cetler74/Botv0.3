#!/usr/bin/env python3
"""
Sell Order Tracker - Real-time Sell Order Monitoring and Trade Closure
======================================================================

This service specifically tracks sell orders every 20 seconds via REST API
to ensure trades are properly closed when sell orders are filled.

Features:
- 20-second periodic tracking of all pending sell orders
- REST API fallback when WebSocket fails
- Automatic trade closure when sell orders are filled
- Redis integration for order status updates
- Comprehensive logging and metrics

This is the CRITICAL FIX for phantom trades.
"""

import asyncio
import asyncpg
import httpx
import json
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
import redis

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class SellOrderTracker:
    """
    Dedicated service for tracking sell orders and closing trades
    """
    
    def __init__(self):
        # Database configuration
        self.db_config = {
            'host': 'localhost',
            'port': 5432,
            'user': 'carloslarramba',
            'password': 'carloslarramba',
            'database': 'trading_bot_futures'
        }
        
        # Service URLs
        self.exchange_service_url = "http://localhost:8003"
        
        # Redis configuration
        self.redis_client = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
        
        # Configuration
        self.check_interval = 20  # seconds
        self.max_concurrent_checks = 10
        self.request_timeout = 30
        
        # State
        self.is_running = False
        self.last_check_time = None
        self.check_task = None
        
        # Metrics
        self.metrics = {
            'checks_performed': 0,
            'sell_orders_checked': 0,
            'orders_found_filled': 0,
            'trades_closed': 0,
            'errors': 0,
            'last_check_duration': 0,
            'start_time': time.time()
        }
        
        logger.info("🎯 Sell Order Tracker initialized - 20s interval tracking")
    
    async def start(self):
        """Start the sell order tracking service"""
        if self.is_running:
            logger.warning("⚠️ Sell Order Tracker already running")
            return
        
        self.is_running = True
        self.metrics['start_time'] = time.time()
        logger.info("🚀 Starting Sell Order Tracker with 20s interval")
        
        # Start the periodic check task
        self.check_task = asyncio.create_task(self._run_periodic_checks())
        
        logger.info("✅ Sell Order Tracker started successfully")
    
    async def stop(self):
        """Stop the sell order tracking service"""
        if not self.is_running:
            return
        
        self.is_running = False
        
        if self.check_task:
            self.check_task.cancel()
            try:
                await self.check_task
            except asyncio.CancelledError:
                pass
        
        logger.info("🛑 Sell Order Tracker stopped")
    
    async def _run_periodic_checks(self):
        """Run periodic checks every 20 seconds"""
        logger.info(f"🔄 Starting periodic sell order checks every {self.check_interval}s")
        
        while self.is_running:
            try:
                start_time = time.time()
                
                # Perform the check
                await self._check_all_sell_orders()
                
                # Update metrics
                self.metrics['checks_performed'] += 1
                self.metrics['last_check_duration'] = time.time() - start_time
                self.last_check_time = datetime.utcnow()
                
                # Log progress
                if self.metrics['checks_performed'] % 10 == 0:  # Every 10 checks (200 seconds)
                    logger.info(f"📊 Sell Order Tracker Stats: {self.metrics['checks_performed']} checks, "
                              f"{self.metrics['trades_closed']} trades closed, "
                              f"{self.metrics['orders_found_filled']} filled orders found")
                
                # Wait for next interval
                await asyncio.sleep(self.check_interval)
                
            except asyncio.CancelledError:
                logger.info("🛑 Periodic check task cancelled")
                break
            except Exception as e:
                logger.error(f"❌ Error in periodic check: {e}")
                self.metrics['errors'] += 1
                await asyncio.sleep(5)  # Short delay before retry
    
    async def _check_all_sell_orders(self):
        """Check all pending sell orders for fills"""
        try:
            # Get all pending sell orders from database
            pending_sell_orders = await self._get_pending_sell_orders()
            
            if not pending_sell_orders:
                logger.debug("📋 No pending sell orders to check")
                return
            
            logger.info(f"🔍 Checking {len(pending_sell_orders)} pending sell orders")
            
            # Process orders in batches to avoid overwhelming the exchange
            batch_size = min(self.max_concurrent_checks, len(pending_sell_orders))
            
            for i in range(0, len(pending_sell_orders), batch_size):
                batch = pending_sell_orders[i:i + batch_size]
                
                # Create tasks for concurrent checking
                tasks = [self._check_single_sell_order(order) for order in batch]
                await asyncio.gather(*tasks, return_exceptions=True)
                
                # Small delay between batches
                if i + batch_size < len(pending_sell_orders):
                    await asyncio.sleep(1)
            
            logger.debug(f"✅ Completed checking {len(pending_sell_orders)} sell orders")
            
        except Exception as e:
            logger.error(f"❌ Error checking all sell orders: {e}")
            self.metrics['errors'] += 1
    
    async def _get_pending_sell_orders(self) -> List[Dict[str, Any]]:
        """Get all pending sell orders from database"""
        conn = await asyncpg.connect(**self.db_config)
        try:
            query = """
            SELECT 
                om.client_order_id,
                om.exchange_order_id,
                om.exchange,
                om.symbol,
                om.side,
                om.status as order_status,
                om.trade_id,
                t.status as trade_status,
                t.pair,
                t.entry_price,
                t.position_size,
                t.exit_id
            FROM trading.order_mappings om
            LEFT JOIN trading.trades t ON om.trade_id = t.trade_id
            WHERE om.side = 'sell' 
              AND om.status = 'PENDING'
              AND om.exchange_order_id IS NOT NULL
              AND t.status = 'OPEN'
            ORDER BY om.created_at DESC
            """
            
            rows = await conn.fetch(query)
            orders = [dict(row) for row in rows]
            
            self.metrics['sell_orders_checked'] += len(orders)
            return orders
            
        except Exception as e:
            logger.error(f"❌ Error getting pending sell orders: {e}")
            return []
        finally:
            await conn.close()
    
    async def _check_single_sell_order(self, order: Dict[str, Any]):
        """Check a single sell order for fill status"""
        try:
            exchange_order_id = order['exchange_order_id']
            exchange = order['exchange']
            symbol = order['symbol']
            trade_id = order['trade_id']
            
            # Get order status from exchange
            order_status = await self._get_order_status_from_exchange(exchange, exchange_order_id, symbol)
            
            if not order_status:
                logger.debug(f"⚠️ Could not get status for sell order {exchange_order_id}")
                return
            
            # Check if order is filled
            order_data = order_status.get('order', {})
            status = order_data.get('status', '').upper()
            
            if status in ['FILLED', 'CLOSED', 'COMPLETED']:
                logger.info(f"🎯 SELL ORDER FILLED: {exchange_order_id} ({symbol}) - CLOSING TRADE {trade_id}")
                
                # Update order mapping to FILLED
                await self._update_order_mapping_status(order['client_order_id'], 'FILLED')
                
                # Update Redis order status
                await self._update_redis_order_status(order['client_order_id'], order_status)
                
                # Close the trade
                await self._close_trade_for_filled_sell_order(order, order_status)
                
                self.metrics['orders_found_filled'] += 1
                self.metrics['trades_closed'] += 1
                
                logger.info(f"✅ Trade {trade_id} closed successfully via sell order {exchange_order_id}")
            else:
                logger.debug(f"📋 Sell order {exchange_order_id} still {status}")
                
        except Exception as e:
            logger.error(f"❌ Error checking sell order {order.get('exchange_order_id', 'unknown')}: {e}")
            self.metrics['errors'] += 1
    
    async def _get_order_status_from_exchange(self, exchange: str, order_id: str, symbol: str) -> Optional[Dict[str, Any]]:
        """Get order status from exchange via REST API"""
        try:
            url = f"{self.exchange_service_url}/api/v1/trading/order/{exchange}/{order_id}"
            params = {"symbol": symbol}
            
            async with httpx.AsyncClient(timeout=self.request_timeout) as client:
                response = await client.get(url, params=params)
                
                if response.status_code == 200:
                    return response.json()
                else:
                    logger.warning(f"⚠️ Exchange API error for order {order_id}: {response.status_code}")
                    return None
                    
        except Exception as e:
            logger.error(f"❌ Error getting order status from exchange: {e}")
            return None
    
    async def _update_order_mapping_status(self, client_order_id: str, status: str):
        """Update order mapping status in database"""
        conn = await asyncpg.connect(**self.db_config)
        try:
            query = """
            UPDATE trading.order_mappings 
            SET status = $1, updated_at = CURRENT_TIMESTAMP
            WHERE client_order_id = $2
            """
            await conn.execute(query, status, client_order_id)
            logger.debug(f"📝 Updated order mapping {client_order_id} to {status}")
            
        except Exception as e:
            logger.error(f"❌ Error updating order mapping status: {e}")
        finally:
            await conn.close()
    
    async def _update_redis_order_status(self, client_order_id: str, order_status: Dict[str, Any]):
        """Update order status in Redis"""
        try:
            order_data = order_status.get('order', {})
            filled_price = order_data.get('average', order_data.get('price', 0))
            filled_quantity = order_data.get('filled', 0)
            
            # Update Redis hash
            redis_key = f"order:{client_order_id}"
            self.redis_client.hset(redis_key, mapping={
                'status': 'FILLED',
                'filled_price': str(filled_price),
                'filled_quantity': str(filled_quantity),
                'filled_at': datetime.utcnow().isoformat(),
                'updated_by': 'sell_order_tracker'
            })
            
            # Also update legacy format for compatibility
            legacy_key = f"orders:{order_data.get('id', '')}"
            if order_data.get('id'):
                self.redis_client.hset(legacy_key, mapping={
                    'status': 'FILLED',
                    'filled_price': str(filled_price),
                    'filled_quantity': str(filled_quantity),
                    'filled_at': datetime.utcnow().isoformat()
                })
            
            logger.debug(f"📝 Updated Redis order status for {client_order_id}")
            
        except Exception as e:
            logger.error(f"❌ Error updating Redis order status: {e}")
    
    async def _close_trade_for_filled_sell_order(self, order: Dict[str, Any], order_status: Dict[str, Any]):
        """Close trade when sell order is filled"""
        try:
            trade_id = order['trade_id']
            entry_price = order['entry_price']
            position_size = order['position_size']
            
            # Get filled price and time from exchange response
            order_data = order_status.get('order', {})
            filled_price = order_data.get('average', order_data.get('price', 0))
            timestamp = order_data.get('timestamp', 0)
            
            # Calculate realized PnL
            realized_pnl = (filled_price - entry_price) * position_size
            
            # Format exit time
            if timestamp:
                exit_time = datetime.fromtimestamp(timestamp / 1000).isoformat()
            else:
                exit_time = datetime.utcnow().isoformat()
            
            # Close the trade in database
            conn = await asyncpg.connect(**self.db_config)
            try:
                close_query = """
                UPDATE trading.trades 
                SET status = 'CLOSED',
                    exit_price = $1,
                    exit_time = $2,
                    exit_id = $3,
                    realized_pnl = $4,
                    exit_reason = 'sell_order_tracker_20s',
                    updated_at = CURRENT_TIMESTAMP
                WHERE trade_id = $5
                """
                
                exit_id = order['exchange_order_id']
                
                await conn.execute(close_query, filled_price, exit_time, exit_id, realized_pnl, trade_id)
                
                logger.info(f"✅ TRADE CLOSED: {trade_id} | Exit: ${filled_price:.4f} | PnL: ${realized_pnl:.2f}")
                
            finally:
                await conn.close()
                
        except Exception as e:
            logger.error(f"❌ Error closing trade for filled sell order: {e}")
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get current metrics"""
        uptime = time.time() - self.metrics['start_time']
        return {
            **self.metrics,
            'uptime_seconds': uptime,
            'is_running': self.is_running,
            'last_check': self.last_check_time.isoformat() if self.last_check_time else None,
            'avg_check_duration': self.metrics['last_check_duration'],
            'success_rate': (self.metrics['checks_performed'] - self.metrics['errors']) / max(1, self.metrics['checks_performed']) * 100
        }

# Global instance
sell_order_tracker = None

async def start_sell_order_tracker():
    """Start the global sell order tracker"""
    global sell_order_tracker
    
    if sell_order_tracker is None:
        sell_order_tracker = SellOrderTracker()
    
    await sell_order_tracker.start()
    return sell_order_tracker

async def stop_sell_order_tracker():
    """Stop the global sell order tracker"""
    global sell_order_tracker
    
    if sell_order_tracker:
        await sell_order_tracker.stop()

def get_sell_order_tracker_metrics() -> Dict[str, Any]:
    """Get metrics from the global tracker"""
    if sell_order_tracker:
        return sell_order_tracker.get_metrics()
    return {"error": "Tracker not initialized"}

if __name__ == "__main__":
    """Run the sell order tracker as a standalone service"""
    async def main():
        tracker = SellOrderTracker()
        
        try:
            await tracker.start()
            
            # Keep running until interrupted
            while tracker.is_running:
                await asyncio.sleep(1)
                
        except KeyboardInterrupt:
            logger.info("🛑 Received interrupt signal")
        finally:
            await tracker.stop()
    
    asyncio.run(main())
