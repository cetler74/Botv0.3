#!/usr/bin/env python3
"""
Periodic Fill Checker
This service periodically checks for filled orders on the exchange and updates the database
"""

import asyncio
import httpx
import redis
import logging
import json
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class PeriodicFillChecker:
    """
    Service that periodically checks for filled orders on exchanges and updates the database
    """
    
    def __init__(self, db_manager, config: Dict[str, Any]):
        self.db_manager = db_manager
        self.config = config
        self.redis_client = redis.Redis.from_url("redis://localhost:6379", decode_responses=True)
        self.exchange_service_url = "http://localhost:8003"
        
        # Configuration
        self.check_interval = 30  # seconds
        self.batch_size = 10
        self.max_retries = 3
        
        # Metrics
        self.metrics = {
            'checks_performed': 0,
            'orders_checked': 0,
            'orders_updated': 0,
            'trades_closed': 0,
            'errors': 0,
            'last_check': None
        }
        
        self.is_running = False
        
        logger.info("🔍 Periodic Fill Checker initialized")
    
    async def start(self):
        """Start the periodic fill checker"""
        if self.is_running:
            logger.warning("Periodic Fill Checker already running")
            return
        
        self.is_running = True
        logger.info("🚀 Starting Periodic Fill Checker")
        
        # Start the main loop
        asyncio.create_task(self._main_loop())
        
        logger.info("✅ Periodic Fill Checker started")
    
    async def stop(self):
        """Stop the periodic fill checker"""
        self.is_running = False
        logger.info("🛑 Stopping Periodic Fill Checker")
    
    async def _main_loop(self):
        """Main loop for periodic checking"""
        while self.is_running:
            try:
                await self._check_pending_orders()
                await asyncio.sleep(self.check_interval)
            except Exception as e:
                logger.error(f"❌ Error in periodic fill checker main loop: {e}")
                self.metrics['errors'] += 1
                await asyncio.sleep(self.check_interval)
    
    async def _check_pending_orders(self):
        """Check all pending orders for fills"""
        try:
            self.metrics['checks_performed'] += 1
            self.metrics['last_check'] = datetime.utcnow()
            
            # Get all pending orders
            pending_orders = await self._get_pending_orders()
            
            if not pending_orders:
                logger.debug("No pending orders to check")
                return
            
            logger.info(f"🔍 Checking {len(pending_orders)} pending orders for fills")
            
            # Process orders in batches
            for i in range(0, len(pending_orders), self.batch_size):
                batch = pending_orders[i:i + self.batch_size]
                await self._process_order_batch(batch)
                
                # Small delay between batches
                await asyncio.sleep(1)
            
        except Exception as e:
            logger.error(f"❌ Error checking pending orders: {e}")
            self.metrics['errors'] += 1
    
    async def _get_pending_orders(self) -> List[Dict[str, Any]]:
        """Get all pending orders from database"""
        try:
            query = """
                SELECT om.client_order_id, om.symbol, om.side, om.price, om.exchange_order_id, om.exchange,
                       t.trade_id, t.pair, t.entry_price, t.position_size, t.status as trade_status
                FROM trading.order_mappings om
                LEFT JOIN trading.trades t ON om.trade_id = t.trade_id
                WHERE om.status = 'PENDING' 
                AND om.exchange_order_id IS NOT NULL
                AND om.exchange_order_id != ''
                ORDER BY om.created_at DESC
            """
            
            results = await self.db_manager.fetch_all(query)
            
            orders = []
            for row in results:
                orders.append({
                    'client_order_id': row['client_order_id'],
                    'symbol': row['symbol'],
                    'side': row['side'],
                    'price': row['price'],
                    'exchange_order_id': row['exchange_order_id'],
                    'exchange': row['exchange'],
                    'trade_id': row['trade_id'],
                    'pair': row['pair'],
                    'entry_price': row['entry_price'],
                    'position_size': row['position_size'],
                    'trade_status': row['trade_status']
                })
            
            return orders
            
        except Exception as e:
            logger.error(f"❌ Error getting pending orders: {e}")
            return []
    
    async def _process_order_batch(self, orders: List[Dict[str, Any]]):
        """Process a batch of orders"""
        for order in orders:
            try:
                await self._check_single_order(order)
                self.metrics['orders_checked'] += 1
            except Exception as e:
                logger.error(f"❌ Error checking order {order['exchange_order_id']}: {e}")
                self.metrics['errors'] += 1
    
    async def _check_single_order(self, order: Dict[str, Any]):
        """Check a single order for fill status"""
        try:
            exchange_order_id = order['exchange_order_id']
            exchange = order['exchange']
            symbol = order['symbol']
            
            # Get order status from exchange
            order_status = await self._get_order_status_from_exchange(exchange, exchange_order_id, symbol)
            
            if not order_status:
                logger.debug(f"⚠️ Could not get status for order {exchange_order_id}")
                return
            
            # Check if order is filled (CCXT format)
            order_data = order_status.get('order', {})
            if order_data.get('status') in ['closed', 'FILLED', 'COMPLETED']:
                logger.info(f"✅ Order {exchange_order_id} is FILLED on {exchange}")
                
                # Update order mapping
                await self._update_order_mapping(order, order_status)
                
                # Close trade if it's an exit order
                if order['side'].lower() == 'sell' and order['trade_status'] == 'OPEN':
                    await self._close_trade_for_filled_order(order, order_status)
                
                self.metrics['orders_updated'] += 1
            
        except Exception as e:
            logger.error(f"❌ Error checking single order: {e}")
            self.metrics['errors'] += 1
    
    async def _get_order_status_from_exchange(self, exchange: str, order_id: str, symbol: str) -> Optional[Dict[str, Any]]:
        """Get order status from exchange service"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                url = f"{self.exchange_service_url}/api/v1/trading/order/{exchange}/{order_id}"
                params = {'symbol': symbol}
                
                response = await client.get(url, params=params)
                
                if response.status_code == 200:
                    return response.json()
                else:
                    logger.debug(f"⚠️ Failed to get order status: {response.status_code}")
                    return None
                    
        except Exception as e:
            logger.error(f"❌ Error getting order status from exchange: {e}")
            return None
    
    async def _update_order_mapping(self, order: Dict[str, Any], order_status: Dict[str, Any]):
        """Update order mapping with fill information"""
        try:
            client_order_id = order['client_order_id']
            
            # Extract fill information from CCXT response
            order_data = order_status.get('order', {})
            filled_price = order_data.get('average', order_data.get('price', 0))
            filled_amount = order_data.get('filled', order_data.get('amount', 0))
            fees = 0  # CCXT doesn't always provide fees in order response
            fill_time = datetime.fromtimestamp(order_data.get('timestamp', 0) / 1000).isoformat() if order_data.get('timestamp') else datetime.utcnow().isoformat()
            
            # Update order mapping (order_mappings table doesn't have filled_price, filled_amount, fees columns)
            update_query = """
                UPDATE trading.order_mappings 
                SET status = 'FILLED',
                    updated_at = CURRENT_TIMESTAMP
                WHERE client_order_id = %s
            """
            
            await self.db_manager.execute_query(update_query, (client_order_id,))
            
            # Update Redis
            await self._update_redis_order_status(client_order_id, order_status)
            
            logger.info(f"✅ Updated order mapping for {client_order_id}")
            
        except Exception as e:
            logger.error(f"❌ Error updating order mapping: {e}")
            self.metrics['errors'] += 1
    
    async def _close_trade_for_filled_order(self, order: Dict[str, Any], order_status: Dict[str, Any]):
        """Close trade when exit order is filled"""
        try:
            trade_id = order['trade_id']
            entry_price = order['entry_price']
            position_size = order['position_size']
            order_data = order_status.get('order', {})
            filled_price = order_data.get('average', order_data.get('price', 0))
            fees = 0  # CCXT doesn't always provide fees in order response
            
            # Calculate realized PnL
            realized_pnl = (filled_price - entry_price) * position_size - fees
            
            # Close the trade
            close_query = """
                UPDATE trading.trades 
                SET status = 'CLOSED',
                    exit_price = %s,
                    exit_time = %s,
                    exit_id = %s,
                    realized_pnl = %s,
                    exit_reason = 'periodic_fill_check',
                    updated_at = CURRENT_TIMESTAMP
                WHERE trade_id = %s
            """
            
            exit_time = datetime.fromtimestamp(order_data.get('timestamp', 0) / 1000).isoformat() if order_data.get('timestamp') else datetime.utcnow().isoformat()
            exit_id = order['exchange_order_id']
            
            await self.db_manager.execute_query(close_query, (
                filled_price, exit_time, exit_id, realized_pnl, trade_id
            ))
            
            self.metrics['trades_closed'] += 1
            logger.info(f"✅ Closed trade {trade_id} via periodic fill check - PnL: ${realized_pnl:.4f}")
            
        except Exception as e:
            logger.error(f"❌ Error closing trade: {e}")
            self.metrics['errors'] += 1
    
    async def _update_redis_order_status(self, client_order_id: str, order_status: Dict[str, Any]):
        """Update Redis with order status"""
        try:
            # Update both key formats
            status_update = {
                "status": "FILLED",
                "filled_price": str(order_status.get('filled_price', order_status.get('price', 0))),
                "filled_amount": str(order_status.get('filled_amount', order_status.get('amount', 0))),
                "fees": str(order_status.get('fees', 0)),
                "updated_at": str(time.time())
            }
            
            # Update legacy format
            exchange_order_id = order_status.get('order_id', '')
            if exchange_order_id:
                self.redis_client.hmset(f"orders:{exchange_order_id}", status_update)
            
            # Update client order ID format
            self.redis_client.hmset(f"order:{client_order_id}", status_update)
            
        except Exception as e:
            logger.error(f"❌ Error updating Redis order status: {e}")
    
    def get_status(self) -> Dict[str, Any]:
        """Get service status"""
        return {
            'is_running': self.is_running,
            'check_interval': self.check_interval,
            'metrics': self.metrics
        }
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get service metrics"""
        return self.metrics
