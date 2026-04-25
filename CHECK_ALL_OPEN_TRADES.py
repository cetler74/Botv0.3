#!/usr/bin/env python3
"""
Check All Open Trades Against Exchange
This script checks all OPEN trades and pending sell orders against the exchange to identify phantom trades
"""

import asyncio
import httpx
import subprocess
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class OpenTradesChecker:
    """
    System to check all OPEN trades against exchange order status
    """
    
    def __init__(self):
        self.exchange_service_url = "http://localhost:8003"
        self.metrics = {
            'open_trades_checked': 0,
            'pending_orders_checked': 0,
            'phantom_trades_found': 0,
            'filled_orders_found': 0,
            'errors': 0
        }
    
    async def check_all_open_trades(self):
        """Check all OPEN trades against exchange"""
        logger.info("🚀 Starting comprehensive open trades check...")
        
        try:
            # Get all OPEN trades
            open_trades = await self._get_open_trades()
            logger.info(f"📊 Found {len(open_trades)} OPEN trades")
            
            # Get all pending sell orders
            pending_orders = await self._get_pending_sell_orders()
            logger.info(f"📊 Found {len(pending_orders)} pending sell orders")
            
            # Check each pending order against exchange
            for order in pending_orders:
                await self._check_order_against_exchange(order)
            
            # Check OPEN trades that have exit_id
            for trade in open_trades:
                if trade.get('exit_id'):
                    await self._check_trade_exit_order(trade)
            
            # Print summary
            self._print_summary()
            
        except Exception as e:
            logger.error(f"❌ Error in open trades check: {e}")
            self.metrics['errors'] += 1
    
    async def _get_open_trades(self) -> List[Dict[str, Any]]:
        """Get all OPEN trades from database"""
        try:
            result = subprocess.run([
                'docker', 'exec', '-i', 'trading-bot-postgres', 
                'psql', '-U', 'carloslarramba', '-d', 'trading_bot_futures', '-c',
                "SELECT trade_id, pair, entry_price, current_price, exit_id, status FROM trading.trades WHERE status = 'OPEN' ORDER BY entry_time DESC;"
            ], capture_output=True, text=True)
            
            if result.returncode != 0:
                logger.error(f"❌ Failed to get open trades: {result.stderr}")
                return []
            
            # Parse the output
            lines = result.stdout.strip().split('\n')
            trades = []
            
            for line in lines:
                if '|' in line and 'trade_id' not in line and '---' not in line and line.strip():
                    parts = [part.strip() for part in line.split('|')]
                    if len(parts) >= 6:
                        trades.append({
                            'trade_id': parts[0],
                            'pair': parts[1],
                            'entry_price': float(parts[2]) if parts[2] else 0,
                            'current_price': float(parts[3]) if parts[3] else 0,
                            'exit_id': parts[4],
                            'status': parts[5]
                        })
            
            return trades
                    
        except Exception as e:
            logger.error(f"❌ Error getting open trades: {e}")
            return []
    
    async def _get_pending_sell_orders(self) -> List[Dict[str, Any]]:
        """Get all pending sell orders from database"""
        try:
            result = subprocess.run([
                'docker', 'exec', '-i', 'trading-bot-postgres', 
                'psql', '-U', 'carloslarramba', '-d', 'trading_bot_futures', '-c',
                "SELECT client_order_id, symbol, side, price, status, exchange_order_id FROM trading.order_mappings WHERE status = 'PENDING' AND side = 'sell' ORDER BY created_at DESC;"
            ], capture_output=True, text=True)
            
            if result.returncode != 0:
                logger.error(f"❌ Failed to get pending orders: {result.stderr}")
                return []
            
            # Parse the output
            lines = result.stdout.strip().split('\n')
            orders = []
            
            for line in lines:
                if '|' in line and 'client_order_id' not in line and '---' not in line and line.strip():
                    parts = [part.strip() for part in line.split('|')]
                    if len(parts) >= 6:
                        orders.append({
                            'client_order_id': parts[0],
                            'symbol': parts[1],
                            'side': parts[2],
                            'price': float(parts[3]) if parts[3] else 0,
                            'status': parts[3],
                            'exchange_order_id': parts[5]
                        })
            
            return orders
                    
        except Exception as e:
            logger.error(f"❌ Error getting pending orders: {e}")
            return []
    
    async def _check_order_against_exchange(self, order: Dict[str, Any]):
        """Check a single order against exchange"""
        try:
            self.metrics['pending_orders_checked'] += 1
            
            exchange_order_id = order['exchange_order_id']
            symbol = order['symbol']
            
            # Get order status from exchange
            order_status = await self._get_order_status_from_exchange('binance', exchange_order_id, symbol)
            
            if not order_status:
                logger.debug(f"⚠️ Could not get status for order {exchange_order_id}")
                return
            
            # Check if order is filled
            order_data = order_status.get('order', {})
            if order_data.get('status') in ['closed', 'FILLED', 'COMPLETED']:
                logger.warning(f"🚨 PHANTOM ORDER FOUND: {exchange_order_id} ({symbol}) is FILLED on exchange but PENDING in database!")
                logger.info(f"   📊 Order details: price={order_data.get('price')}, filled={order_data.get('filled')}, average={order_data.get('average')}")
                self.metrics['filled_orders_found'] += 1
            else:
                logger.debug(f"✅ Order {exchange_order_id} ({symbol}) is correctly PENDING")
            
        except Exception as e:
            logger.error(f"❌ Error checking order {order.get('exchange_order_id')}: {e}")
            self.metrics['errors'] += 1
    
    async def _check_trade_exit_order(self, trade: Dict[str, Any]):
        """Check if a trade's exit order is filled"""
        try:
            self.metrics['open_trades_checked'] += 1
            
            exit_id = trade['exit_id']
            pair = trade['pair']
            
            # Get order status from exchange
            order_status = await self._get_order_status_from_exchange('binance', exit_id, pair)
            
            if not order_status:
                logger.debug(f"⚠️ Could not get status for exit order {exit_id}")
                return
            
            # Check if order is filled
            order_data = order_status.get('order', {})
            if order_data.get('status') in ['closed', 'FILLED', 'COMPLETED']:
                logger.warning(f"🚨 PHANTOM TRADE FOUND: Trade {trade['trade_id']} ({pair}) has FILLED exit order but is still OPEN!")
                logger.info(f"   📊 Exit order {exit_id}: price={order_data.get('price')}, filled={order_data.get('filled')}, average={order_data.get('average')}")
                self.metrics['phantom_trades_found'] += 1
            else:
                logger.debug(f"✅ Trade {trade['trade_id']} exit order {exit_id} is correctly PENDING")
            
        except Exception as e:
            logger.error(f"❌ Error checking trade {trade.get('trade_id')}: {e}")
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
    
    def _print_summary(self):
        """Print check summary"""
        logger.info("\n" + "="*70)
        logger.info("📊 OPEN TRADES CHECK SUMMARY")
        logger.info("="*70)
        
        open_checked = self.metrics['open_trades_checked']
        pending_checked = self.metrics['pending_orders_checked']
        phantom_found = self.metrics['phantom_trades_found']
        filled_found = self.metrics['filled_orders_found']
        errors = self.metrics['errors']
        
        logger.info(f"📋 OPEN Trades Checked: {open_checked}")
        logger.info(f"📋 Pending Orders Checked: {pending_checked}")
        logger.info(f"🚨 Phantom Trades Found: {phantom_found}")
        logger.info(f"🚨 Filled Orders Found: {filled_found}")
        logger.info(f"❌ Errors: {errors}")
        
        logger.info("="*70)
        
        if phantom_found > 0 or filled_found > 0:
            logger.warning(f"⚠️ CRITICAL: Found {phantom_found + filled_found} phantom trades/orders!")
        else:
            logger.info("✅ No phantom trades found - all orders are correctly tracked")

async def main():
    """Main function"""
    checker = OpenTradesChecker()
    await checker.check_all_open_trades()

if __name__ == "__main__":
    asyncio.run(main())
