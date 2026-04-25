#!/usr/bin/env python3
"""
Complete Exit Price Fix
This script fixes all exit prices for closed trades using actual filled prices from Redis data
"""

import asyncio
import httpx
import redis
import json
import logging
import subprocess
from datetime import datetime
from typing import Dict, Any, List

# Configuration
REDIS_URL = "redis://localhost:6379"
DATABASE_SERVICE_URL = "http://localhost:8002"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class CompleteExitPriceFixer:
    """
    Complete system to fix exit price calculations for all closed trades
    """
    
    def __init__(self):
        self.redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        self.metrics = {
            'trades_processed': 0,
            'trades_fixed': 0,
            'trades_skipped': 0,
            'errors': 0
        }
    
    async def fix_all_exit_prices(self):
        """Fix exit prices for all closed trades using Redis filled prices"""
        logger.info("🚀 Starting complete exit price fix...")
        
        try:
            # Get all closed trades
            closed_trades = await self._get_closed_trades()
            
            logger.info(f"📊 Found {len(closed_trades)} closed trades to process")
            
            # Process each trade
            for trade in closed_trades:
                await self._fix_trade_exit_price(trade)
            
            # Print summary
            self._print_summary()
            
        except Exception as e:
            logger.error(f"❌ Error in complete exit price fix: {e}")
            self.metrics['errors'] += 1
    
    async def _get_closed_trades(self) -> List[Dict[str, Any]]:
        """Get all closed trades from database"""
        try:
            # Get trades directly from database
            result = subprocess.run([
                'docker', 'exec', '-i', 'trading-bot-postgres', 
                'psql', '-U', 'carloslarramba', '-d', 'trading_bot_futures', '-c',
                "SELECT trade_id, pair, entry_price, exit_price, exit_id, position_size, realized_pnl FROM trading.trades WHERE status = 'CLOSED' ORDER BY exit_time DESC;"
            ], capture_output=True, text=True)
            
            if result.returncode != 0:
                logger.error(f"❌ Failed to get trades: {result.stderr}")
                return []
            
            # Parse the output
            lines = result.stdout.strip().split('\n')
            trades = []
            
            # Skip header lines and empty lines
            for line in lines:
                if '|' in line and 'trade_id' not in line and '---' not in line and line.strip():
                    parts = [part.strip() for part in line.split('|')]
                    if len(parts) >= 7:
                        trades.append({
                            'trade_id': parts[0],
                            'pair': parts[1],
                            'entry_price': float(parts[2]) if parts[2] else 0,
                            'exit_price': float(parts[3]) if parts[3] else 0,
                            'exit_id': parts[4],
                            'position_size': float(parts[5]) if parts[5] else 0,
                            'realized_pnl': float(parts[6]) if parts[6] else 0
                        })
            
            logger.info(f"📊 Retrieved {len(trades)} closed trades from database")
            return trades
                    
        except Exception as e:
            logger.error(f"❌ Error getting closed trades: {e}")
            return []
    
    async def _fix_trade_exit_price(self, trade: Dict[str, Any]):
        """Fix exit price for a single trade"""
        try:
            self.metrics['trades_processed'] += 1
            
            trade_id = trade['trade_id']
            exit_id = trade.get('exit_id')
            current_exit_price = trade.get('exit_price')
            entry_price = trade.get('entry_price')
            position_size = trade.get('position_size')
            current_realized_pnl = trade.get('realized_pnl')
            
            if not exit_id or not current_exit_price:
                logger.debug(f"⚠️ Trade {trade_id} missing exit_id or exit_price")
                self.metrics['trades_skipped'] += 1
                return
            
            # Get actual filled price from Redis
            redis_data = await self._get_redis_data_for_order(exit_id)
            
            if not redis_data:
                logger.debug(f"⚠️ No Redis data found for order {exit_id}")
                self.metrics['trades_skipped'] += 1
                return
            
            filled_price = redis_data.get('filled_price')
            order_price = redis_data.get('price')
            fees = float(redis_data.get('fees', 0))
            
            if not filled_price:
                logger.debug(f"⚠️ No filled_price in Redis for order {exit_id}")
                self.metrics['trades_skipped'] += 1
                return
            
            filled_price = float(filled_price)
            order_price = float(order_price)
            
            # Check if exit price needs fixing
            if abs(current_exit_price - filled_price) < 0.000001:
                logger.debug(f"✅ Trade {trade_id} exit price already correct: {current_exit_price}")
                self.metrics['trades_skipped'] += 1
                return
            
            # Calculate new realized PnL with correct exit price
            if entry_price and position_size:
                new_realized_pnl = (filled_price - entry_price) * position_size - fees
            else:
                logger.warning(f"⚠️ Missing entry_price or position_size for trade {trade_id}")
                self.metrics['trades_skipped'] += 1
                return
            
            # Update trade with correct exit price and PnL
            await self._update_trade_exit_price(trade_id, filled_price, new_realized_pnl)
            
            self.metrics['trades_fixed'] += 1
            logger.info(f"✅ Fixed trade {trade_id}: {current_exit_price} → {filled_price}, PnL: {current_realized_pnl:.4f} → {new_realized_pnl:.4f}")
            
        except Exception as e:
            logger.error(f"❌ Error fixing trade {trade.get('trade_id')}: {e}")
            self.metrics['errors'] += 1
    
    async def _get_redis_data_for_order(self, exit_id: str) -> Dict[str, Any]:
        """Get Redis data for an order"""
        try:
            # Check both key formats
            order_data = None
            
            # Try old format first
            if self.redis_client.exists(f"orders:{exit_id}"):
                order_data = self.redis_client.hgetall(f"orders:{exit_id}")
            else:
                # Try new format
                keys = self.redis_client.keys("order:*")
                for key in keys:
                    data = self.redis_client.hgetall(key)
                    if data.get("exchange_order_id") == exit_id:
                        order_data = data
                        break
            
            return order_data or {}
            
        except Exception as e:
            logger.error(f"❌ Error getting Redis data for {exit_id}: {e}")
            return {}
    
    async def _update_trade_exit_price(self, trade_id: str, exit_price: float, realized_pnl: float):
        """Update trade with correct exit price and PnL"""
        try:
            # Update directly in database
            result = subprocess.run([
                'docker', 'exec', '-i', 'trading-bot-postgres', 
                'psql', '-U', 'carloslarramba', '-d', 'trading_bot_futures', '-c',
                f"UPDATE trading.trades SET exit_price = {exit_price}, realized_pnl = {realized_pnl}, updated_at = NOW() WHERE trade_id = '{trade_id}';"
            ], capture_output=True, text=True)
            
            if result.returncode != 0:
                logger.error(f"❌ Failed to update trade {trade_id}: {result.stderr}")
            else:
                logger.debug(f"✅ Updated trade {trade_id} in database")
                
        except Exception as e:
            logger.error(f"❌ Error updating trade {trade_id}: {e}")
    
    def _print_summary(self):
        """Print fix summary"""
        logger.info("\n" + "="*70)
        logger.info("📊 COMPLETE EXIT PRICE FIX SUMMARY")
        logger.info("="*70)
        
        total_processed = self.metrics['trades_processed']
        total_fixed = self.metrics['trades_fixed']
        total_skipped = self.metrics['trades_skipped']
        total_errors = self.metrics['errors']
        
        logger.info(f"📋 Trades Processed: {total_processed}")
        logger.info(f"✅ Trades Fixed: {total_fixed}")
        logger.info(f"⏭️ Trades Skipped: {total_skipped}")
        logger.info(f"❌ Errors: {total_errors}")
        
        logger.info("="*70)
        
        if total_errors == 0:
            logger.info("🎉 Complete exit price fix completed successfully!")
        else:
            logger.warning(f"⚠️ Fix completed with {total_errors} errors")

async def main():
    """Main function"""
    fixer = CompleteExitPriceFixer()
    await fixer.fix_all_exit_prices()

if __name__ == "__main__":
    asyncio.run(main())
