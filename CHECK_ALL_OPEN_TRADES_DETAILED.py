#!/usr/bin/env python3
"""
Check All Open Trades Detailed
This script checks all OPEN trades to identify any that should be closed
"""

import asyncio
import httpx
import subprocess
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class DetailedOpenTradesChecker:
    """
    System to check all OPEN trades in detail
    """
    
    def __init__(self):
        self.exchange_service_url = "http://localhost:8003"
        self.metrics = {
            'open_trades_checked': 0,
            'trades_with_exit_id': 0,
            'trades_without_exit_id': 0,
            'trades_with_orders': 0,
            'trades_without_orders': 0,
            'potential_phantoms': 0
        }
    
    async def check_all_open_trades_detailed(self):
        """Check all OPEN trades in detail"""
        logger.info("🚀 Starting detailed open trades check...")
        
        try:
            # Get all OPEN trades
            open_trades = await self._get_open_trades()
            logger.info(f"📊 Found {len(open_trades)} OPEN trades")
            
            # Check each trade in detail
            for trade in open_trades:
                await self._check_trade_detailed(trade)
            
            # Print summary
            self._print_summary()
            
        except Exception as e:
            logger.error(f"❌ Error in detailed open trades check: {e}")
            self.metrics['errors'] = 1
    
    async def _get_open_trades(self) -> List[Dict[str, Any]]:
        """Get all OPEN trades from database"""
        try:
            result = subprocess.run([
                'docker', 'exec', '-i', 'trading-bot-postgres', 
                'psql', '-U', 'carloslarramba', '-d', 'trading_bot_futures', '-c',
                "SELECT trade_id, pair, entry_price, current_price, exit_id, status, entry_time FROM trading.trades WHERE status = 'OPEN' ORDER BY entry_time DESC;"
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
                    if len(parts) >= 7:
                        trades.append({
                            'trade_id': parts[0],
                            'pair': parts[1],
                            'entry_price': float(parts[2]) if parts[2] else 0,
                            'current_price': float(parts[3]) if parts[3] else 0,
                            'exit_id': parts[4],
                            'status': parts[5],
                            'entry_time': parts[6]
                        })
            
            return trades
                    
        except Exception as e:
            logger.error(f"❌ Error getting open trades: {e}")
            return []
    
    async def _check_trade_detailed(self, trade: Dict[str, Any]):
        """Check a single trade in detail"""
        try:
            self.metrics['open_trades_checked'] += 1
            
            trade_id = trade['trade_id']
            pair = trade['pair']
            exit_id = trade['exit_id']
            
            logger.info(f"🔍 Checking trade {trade_id} ({pair})")
            
            # Check if trade has exit_id
            if exit_id:
                self.metrics['trades_with_exit_id'] += 1
                logger.info(f"   📊 Has exit_id: {exit_id}")
                
                # Check if exit_id exists on exchange
                await self._check_exit_id_on_exchange(trade)
            else:
                self.metrics['trades_without_exit_id'] += 1
                logger.info(f"   📊 No exit_id")
            
            # Check if trade has any order mappings
            order_mappings = await self._get_order_mappings_for_trade(trade_id)
            if order_mappings:
                self.metrics['trades_with_orders'] += 1
                logger.info(f"   📊 Has {len(order_mappings)} order mappings")
                for order in order_mappings:
                    logger.info(f"      - {order['side']} {order['symbol']} {order['status']} (ID: {order['exchange_order_id']})")
            else:
                self.metrics['trades_without_orders'] += 1
                logger.info(f"   📊 No order mappings")
            
            # Check for potential phantom trade indicators
            if exit_id and not order_mappings:
                logger.warning(f"   🚨 POTENTIAL PHANTOM: Has exit_id but no order mappings!")
                self.metrics['potential_phantoms'] += 1
            elif not exit_id and not order_mappings:
                logger.info(f"   ✅ LEGITIMATE: No exit_id and no order mappings (normal for new trades)")
            
        except Exception as e:
            logger.error(f"❌ Error checking trade {trade.get('trade_id')}: {e}")
    
    async def _check_exit_id_on_exchange(self, trade: Dict[str, Any]):
        """Check if exit_id exists on exchange"""
        try:
            exit_id = trade['exit_id']
            pair = trade['pair']
            
            # Get order status from exchange
            order_status = await self._get_order_status_from_exchange('binance', exit_id, pair)
            
            if order_status:
                order_data = order_status.get('order', {})
                if order_data.get('status') in ['closed', 'FILLED', 'COMPLETED']:
                    logger.warning(f"   🚨 PHANTOM TRADE: exit_id {exit_id} is FILLED on exchange but trade is OPEN!")
                    self.metrics['potential_phantoms'] += 1
                else:
                    logger.info(f"   ✅ exit_id {exit_id} is correctly PENDING on exchange")
            else:
                logger.warning(f"   🚨 exit_id {exit_id} not found on exchange!")
                self.metrics['potential_phantoms'] += 1
            
        except Exception as e:
            logger.error(f"❌ Error checking exit_id on exchange: {e}")
    
    async def _get_order_mappings_for_trade(self, trade_id: str) -> List[Dict[str, Any]]:
        """Get order mappings for a trade"""
        try:
            result = subprocess.run([
                'docker', 'exec', '-i', 'trading-bot-postgres', 
                'psql', '-U', 'carloslarramba', '-d', 'trading_bot_futures', '-c',
                f"SELECT client_order_id, symbol, side, price, status, exchange_order_id FROM trading.order_mappings WHERE trade_id = '{trade_id}';"
            ], capture_output=True, text=True)
            
            if result.returncode != 0:
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
                            'status': parts[4],
                            'exchange_order_id': parts[5]
                        })
            
            return orders
                    
        except Exception as e:
            logger.error(f"❌ Error getting order mappings: {e}")
            return []
    
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
                    return None
                    
        except Exception as e:
            logger.error(f"❌ Error getting order status from exchange: {e}")
            return None
    
    def _print_summary(self):
        """Print check summary"""
        logger.info("\n" + "="*70)
        logger.info("📊 DETAILED OPEN TRADES CHECK SUMMARY")
        logger.info("="*70)
        
        open_checked = self.metrics['open_trades_checked']
        with_exit_id = self.metrics['trades_with_exit_id']
        without_exit_id = self.metrics['trades_without_exit_id']
        with_orders = self.metrics['trades_with_orders']
        without_orders = self.metrics['trades_without_orders']
        potential_phantoms = self.metrics['potential_phantoms']
        
        logger.info(f"📋 OPEN Trades Checked: {open_checked}")
        logger.info(f"📋 Trades with exit_id: {with_exit_id}")
        logger.info(f"📋 Trades without exit_id: {without_exit_id}")
        logger.info(f"📋 Trades with order mappings: {with_orders}")
        logger.info(f"📋 Trades without order mappings: {without_orders}")
        logger.info(f"🚨 Potential Phantom Trades: {potential_phantoms}")
        
        logger.info("="*70)
        
        if potential_phantoms > 0:
            logger.warning(f"⚠️ CRITICAL: Found {potential_phantoms} potential phantom trades!")
        else:
            logger.info("✅ No potential phantom trades found")

async def main():
    """Main function"""
    checker = DetailedOpenTradesChecker()
    await checker.check_all_open_trades_detailed()

if __name__ == "__main__":
    asyncio.run(main())
