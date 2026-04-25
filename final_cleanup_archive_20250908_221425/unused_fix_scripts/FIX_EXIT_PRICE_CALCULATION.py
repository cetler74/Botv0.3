#!/usr/bin/env python3
"""
Fix Exit Price Calculation
This script fixes the exit price calculation by using the actual filled price from Redis data
"""

import asyncio
import httpx
import redis
import json
import logging
from datetime import datetime
from typing import Dict, Any, List

# Configuration
REDIS_URL = "redis://localhost:6379"
DATABASE_SERVICE_URL = "http://localhost:8002"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ExitPriceFixer:
    """
    System to fix exit price calculations using actual filled prices from Redis
    """
    
    def __init__(self):
        self.redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
        self.metrics = {
            'trades_processed': 0,
            'trades_fixed': 0,
            'errors': 0
        }
    
    async def fix_exit_prices(self):
        """Fix exit prices for all closed trades using Redis filled prices"""
        logger.info("🚀 Starting exit price fix...")
        
        try:
            # Get all closed trades
            closed_trades = await self._get_closed_trades()
            
            # Process each trade
            for trade in closed_trades:
                await self._fix_trade_exit_price(trade)
            
            # Print summary
            self._print_summary()
            
        except Exception as e:
            logger.error(f"❌ Error in exit price fix: {e}")
            self.metrics['errors'] += 1
    
    async def _get_closed_trades(self) -> List[Dict[str, Any]]:
        """Get all closed trades from database"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/trades")
                if response.status_code == 200:
                    trades_data = response.json()
                    trades = trades_data.get('trades', [])
                    
                    # Filter closed trades
                    closed_trades = [trade for trade in trades if trade.get('status') == 'CLOSED']
                    
                    logger.info(f"📊 Found {len(closed_trades)} closed trades")
                    return closed_trades
                else:
                    logger.error(f"❌ Failed to get trades: {response.status_code}")
                    return []
                    
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
            
            if not exit_id or not current_exit_price:
                logger.debug(f"⚠️ Trade {trade_id} missing exit_id or exit_price")
                return
            
            # Get actual filled price from Redis
            actual_filled_price = await self._get_filled_price_from_redis(exit_id)
            
            if not actual_filled_price:
                logger.debug(f"⚠️ No filled price found in Redis for order {exit_id}")
                return
            
            # Check if exit price needs fixing
            if abs(float(current_exit_price) - float(actual_filled_price)) < 0.000001:
                logger.debug(f"✅ Trade {trade_id} exit price already correct: {current_exit_price}")
                return
            
            # Calculate new realized PnL with correct exit price
            if entry_price and position_size:
                new_realized_pnl = (float(actual_filled_price) - float(entry_price)) * float(position_size)
            else:
                logger.warning(f"⚠️ Missing entry_price or position_size for trade {trade_id}")
                return
            
            # Update trade with correct exit price and PnL
            await self._update_trade_exit_price(trade_id, actual_filled_price, new_realized_pnl)
            
            self.metrics['trades_fixed'] += 1
            logger.info(f"✅ Fixed trade {trade_id}: {current_exit_price} → {actual_filled_price}, PnL: {new_realized_pnl:.4f}")
            
        except Exception as e:
            logger.error(f"❌ Error fixing trade {trade.get('trade_id')}: {e}")
            self.metrics['errors'] += 1
    
    async def _get_filled_price_from_redis(self, exit_id: str) -> float:
        """Get actual filled price from Redis data"""
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
            
            if order_data and order_data.get("filled_price"):
                return float(order_data["filled_price"])
            
            return None
            
        except Exception as e:
            logger.error(f"❌ Error getting filled price for {exit_id}: {e}")
            return None
    
    async def _update_trade_exit_price(self, trade_id: str, exit_price: float, realized_pnl: float):
        """Update trade with correct exit price and PnL"""
        try:
            # Update directly in database
            import subprocess
            result = subprocess.run([
                'docker', 'exec', '-i', 'trading-bot-postgres', 
                'psql', '-U', 'carloslarramba', '-d', 'trading_bot_futures', '-c',
                f"UPDATE trading.trades SET exit_price = {exit_price}, realized_pnl = {realized_pnl}, updated_at = NOW() WHERE trade_id = '{trade_id}';"
            ], capture_output=True, text=True)
            
            if result.returncode != 0:
                logger.error(f"❌ Failed to update trade {trade_id}: {result.stderr}")
            else:
                logger.info(f"✅ Updated trade {trade_id} in database")
                
        except Exception as e:
            logger.error(f"❌ Error updating trade {trade_id}: {e}")
    
    def _print_summary(self):
        """Print fix summary"""
        logger.info("\n" + "="*60)
        logger.info("📊 EXIT PRICE FIX SUMMARY")
        logger.info("="*60)
        
        total_processed = self.metrics['trades_processed']
        total_fixed = self.metrics['trades_fixed']
        total_errors = self.metrics['errors']
        
        logger.info(f"📋 Trades Processed: {total_processed}")
        logger.info(f"✅ Trades Fixed: {total_fixed}")
        logger.info(f"❌ Errors: {total_errors}")
        
        logger.info("="*60)
        
        if total_errors == 0:
            logger.info("🎉 Exit price fix completed successfully!")
        else:
            logger.warning(f"⚠️ Fix completed with {total_errors} errors")

async def main():
    """Main function"""
    fixer = ExitPriceFixer()
    await fixer.fix_exit_prices()

if __name__ == "__main__":
    asyncio.run(main())
