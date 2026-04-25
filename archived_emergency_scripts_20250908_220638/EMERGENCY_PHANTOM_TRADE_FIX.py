#!/usr/bin/env python3
"""
Emergency Phantom Trade Fix
This script fixes all 12 phantom trades/orders immediately
"""

import asyncio
import httpx
import subprocess
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class EmergencyPhantomTradeFix:
    """
    Emergency system to fix all phantom trades immediately
    """
    
    def __init__(self):
        self.exchange_service_url = "http://localhost:8003"
        self.metrics = {
            'phantom_orders_fixed': 0,
            'phantom_trades_fixed': 0,
            'errors': 0
        }
    
    async def fix_all_phantom_trades(self):
        """Fix all phantom trades immediately"""
        logger.info("🚨 Starting EMERGENCY phantom trade fix...")
        
        # List of all phantom orders found
        phantom_orders = [
            {'order_id': '1541710426', 'symbol': 'DOGE/USDC', 'price': 0.23444, 'filled': 440.0, 'average': 0.23532},
            {'order_id': '12731046', 'symbol': 'ETC/USDC', 'price': 20.71, 'filled': 0.32, 'average': 20.78},
            {'order_id': '182307285', 'symbol': 'ALGO/USDC', 'price': 0.2349, 'filled': 470.0, 'average': 0.2359},
            {'order_id': '151487066', 'symbol': 'NEO/USDC', 'price': 6.698, 'filled': 12.98, 'average': 6.723},
            {'order_id': '345324236', 'symbol': 'ATOM/USDC', 'price': 4.537, 'filled': 27.45, 'average': 4.553},
            {'order_id': '171368393', 'symbol': 'XLM/USDC', 'price': 0.3694, 'filled': 422.0, 'average': 0.3706},
            {'order_id': '151485753', 'symbol': 'NEO/USDC', 'price': 6.696, 'filled': 5.62, 'average': 6.719},
            {'order_id': '1223769256', 'symbol': 'ADA/USDC', 'price': 0.8504, 'filled': 6.4, 'average': 0.8532},
            {'order_id': '1223769055', 'symbol': 'ADA/USDC', 'price': 0.8505, 'filled': 140.1, 'average': 0.8534},
            {'order_id': '520695983', 'symbol': 'LINK/USDC', 'price': 22.7, 'filled': 3.56, 'average': 22.77},
            {'order_id': '520692175', 'symbol': 'LINK/USDC', 'price': 22.61, 'filled': 2.62, 'average': 22.66}
        ]
        
        # Fix each phantom order
        for order in phantom_orders:
            await self._fix_phantom_order(order)
        
        # Fix the phantom trade
        await self._fix_phantom_trade('f1b7a830-4b6f-479d-8df5-fad36e071746', '1541710426', 'DOGE/USDC', 0.23532, 440.0)
        
        # Print summary
        self._print_summary()
    
    async def _fix_phantom_order(self, order: Dict[str, Any]):
        """Fix a single phantom order"""
        try:
            order_id = order['order_id']
            symbol = order['symbol']
            filled_price = order['average']
            filled_amount = order['filled']
            
            logger.info(f"🔧 Fixing phantom order {order_id} ({symbol})")
            
            # Get client_order_id from database
            client_order_id = await self._get_client_order_id(order_id)
            if not client_order_id:
                logger.error(f"❌ Could not find client_order_id for {order_id}")
                return
            
            # Update order mapping to FILLED
            await self._update_order_mapping_to_filled(client_order_id)
            
            # Get trade_id from order mapping
            trade_id = await self._get_trade_id_from_order(client_order_id)
            if trade_id:
                # Close the trade
                await self._close_trade_with_fill_data(trade_id, order_id, filled_price, filled_amount)
                self.metrics['phantom_trades_fixed'] += 1
            
            self.metrics['phantom_orders_fixed'] += 1
            logger.info(f"✅ Fixed phantom order {order_id}")
            
        except Exception as e:
            logger.error(f"❌ Error fixing phantom order {order.get('order_id')}: {e}")
            self.metrics['errors'] += 1
    
    async def _fix_phantom_trade(self, trade_id: str, exit_id: str, symbol: str, filled_price: float, filled_amount: float):
        """Fix a specific phantom trade"""
        try:
            logger.info(f"🔧 Fixing phantom trade {trade_id} ({symbol})")
            
            # Get trade data
            trade_data = await self._get_trade_data(trade_id)
            if not trade_data:
                logger.error(f"❌ Could not get trade data for {trade_id}")
                return
            
            # Calculate realized PnL
            entry_price = trade_data['entry_price']
            position_size = trade_data['position_size']
            realized_pnl = (filled_price - entry_price) * position_size
            
            # Close the trade
            await self._close_trade_directly(trade_id, exit_id, filled_price, realized_pnl)
            
            logger.info(f"✅ Fixed phantom trade {trade_id} - PnL: ${realized_pnl:.4f}")
            
        except Exception as e:
            logger.error(f"❌ Error fixing phantom trade {trade_id}: {e}")
            self.metrics['errors'] += 1
    
    async def _get_client_order_id(self, exchange_order_id: str) -> Optional[str]:
        """Get client_order_id from exchange_order_id"""
        try:
            result = subprocess.run([
                'docker', 'exec', '-i', 'trading-bot-postgres', 
                'psql', '-U', 'carloslarramba', '-d', 'trading_bot_futures', '-c',
                f"SELECT client_order_id FROM trading.order_mappings WHERE exchange_order_id = '{exchange_order_id}';"
            ], capture_output=True, text=True)
            
            if result.returncode != 0:
                return None
            
            lines = result.stdout.strip().split('\n')
            for line in lines:
                if line.strip() and 'client_order_id' not in line and '---' not in line:
                    return line.strip()
            
            return None
            
        except Exception as e:
            logger.error(f"❌ Error getting client_order_id: {e}")
            return None
    
    async def _get_trade_id_from_order(self, client_order_id: str) -> Optional[str]:
        """Get trade_id from client_order_id"""
        try:
            result = subprocess.run([
                'docker', 'exec', '-i', 'trading-bot-postgres', 
                'psql', '-U', 'carloslarramba', '-d', 'trading_bot_futures', '-c',
                f"SELECT trade_id FROM trading.order_mappings WHERE client_order_id = '{client_order_id}';"
            ], capture_output=True, text=True)
            
            if result.returncode != 0:
                return None
            
            lines = result.stdout.strip().split('\n')
            for line in lines:
                if line.strip() and 'trade_id' not in line and '---' not in line:
                    return line.strip()
            
            return None
            
        except Exception as e:
            logger.error(f"❌ Error getting trade_id: {e}")
            return None
    
    async def _get_trade_data(self, trade_id: str) -> Optional[Dict[str, Any]]:
        """Get trade data from database"""
        try:
            result = subprocess.run([
                'docker', 'exec', '-i', 'trading-bot-postgres', 
                'psql', '-U', 'carloslarramba', '-d', 'trading_bot_futures', '-c',
                f"SELECT entry_price, position_size FROM trading.trades WHERE trade_id = '{trade_id}';"
            ], capture_output=True, text=True)
            
            if result.returncode != 0:
                return None
            
            lines = result.stdout.strip().split('\n')
            for line in lines:
                if '|' in line and 'entry_price' not in line and '---' not in line and line.strip():
                    parts = [part.strip() for part in line.split('|')]
                    if len(parts) >= 2:
                        return {
                            'entry_price': float(parts[0]) if parts[0] else 0,
                            'position_size': float(parts[1]) if parts[1] else 0
                        }
            
            return None
            
        except Exception as e:
            logger.error(f"❌ Error getting trade data: {e}")
            return None
    
    async def _update_order_mapping_to_filled(self, client_order_id: str):
        """Update order mapping to FILLED status"""
        try:
            result = subprocess.run([
                'docker', 'exec', '-i', 'trading-bot-postgres', 
                'psql', '-U', 'carloslarramba', '-d', 'trading_bot_futures', '-c',
                f"UPDATE trading.order_mappings SET status = 'FILLED', updated_at = NOW() WHERE client_order_id = '{client_order_id}';"
            ], capture_output=True, text=True)
            
            if result.returncode != 0:
                logger.error(f"❌ Failed to update order mapping: {result.stderr}")
            
        except Exception as e:
            logger.error(f"❌ Error updating order mapping: {e}")
    
    async def _close_trade_with_fill_data(self, trade_id: str, exit_id: str, filled_price: float, filled_amount: float):
        """Close trade with fill data"""
        try:
            # Get trade data
            trade_data = await self._get_trade_data(trade_id)
            if not trade_data:
                return
            
            # Calculate realized PnL
            entry_price = trade_data['entry_price']
            position_size = trade_data['position_size']
            realized_pnl = (filled_price - entry_price) * position_size
            
            # Close the trade
            await self._close_trade_directly(trade_id, exit_id, filled_price, realized_pnl)
            
        except Exception as e:
            logger.error(f"❌ Error closing trade with fill data: {e}")
    
    async def _close_trade_directly(self, trade_id: str, exit_id: str, exit_price: float, realized_pnl: float):
        """Close trade directly in database"""
        try:
            result = subprocess.run([
                'docker', 'exec', '-i', 'trading-bot-postgres', 
                'psql', '-U', 'carloslarramba', '-d', 'trading_bot_futures', '-c',
                f"UPDATE trading.trades SET status = 'CLOSED', exit_price = {exit_price}, exit_time = NOW(), exit_id = '{exit_id}', realized_pnl = {realized_pnl}, exit_reason = 'emergency_phantom_fix', updated_at = NOW() WHERE trade_id = '{trade_id}';"
            ], capture_output=True, text=True)
            
            if result.returncode != 0:
                logger.error(f"❌ Failed to close trade: {result.stderr}")
            
        except Exception as e:
            logger.error(f"❌ Error closing trade: {e}")
    
    def _print_summary(self):
        """Print fix summary"""
        logger.info("\n" + "="*70)
        logger.info("🚨 EMERGENCY PHANTOM TRADE FIX SUMMARY")
        logger.info("="*70)
        
        orders_fixed = self.metrics['phantom_orders_fixed']
        trades_fixed = self.metrics['phantom_trades_fixed']
        errors = self.metrics['errors']
        
        logger.info(f"✅ Phantom Orders Fixed: {orders_fixed}")
        logger.info(f"✅ Phantom Trades Fixed: {trades_fixed}")
        logger.info(f"❌ Errors: {errors}")
        
        logger.info("="*70)
        
        if errors == 0:
            logger.info("🎉 Emergency phantom trade fix completed successfully!")
        else:
            logger.warning(f"⚠️ Fix completed with {errors} errors")

async def main():
    """Main function"""
    fixer = EmergencyPhantomTradeFix()
    await fixer.fix_all_phantom_trades()

if __name__ == "__main__":
    asyncio.run(main())
