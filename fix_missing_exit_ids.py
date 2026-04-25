#!/usr/bin/env python3
"""
Fix Missing Exit IDs for Closed Trades

This script matches closed trades with their corresponding exchange sell orders
to populate missing exit_id fields, which are crucial for trade reconciliation.
"""

import asyncio
import asyncpg
import httpx
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class ExitIDFixer:
    def __init__(self):
        self.db_config = {
            'host': 'localhost',
            'port': 5432,
            'user': 'carloslarramba',
            'password': 'CarLarr123',
            'database': 'trading_bot_futures'
        }
        self.api_base = "http://localhost:8002"
        
    async def get_missing_exit_id_trades(self) -> List[Dict[str, Any]]:
        """Get all closed trades missing exit_id"""
        conn = await asyncpg.connect(**self.db_config)
        try:
            query = """
                SELECT trade_id, pair, entry_price, exit_price, position_size, 
                       exit_time, entry_id, exit_reason
                FROM trading.trades 
                WHERE status = 'CLOSED' AND exit_id IS NULL
                ORDER BY exit_time DESC
            """
            results = await conn.fetch(query)
            return [dict(row) for row in results]
        finally:
            await conn.close()
    
    async def find_matching_orders(self, trade: Dict[str, Any]) -> Optional[str]:
        """
        Find matching sell order for a trade using multiple criteria:
        1. Pair/symbol match
        2. Amount/position_size match (with tolerance)
        3. Price match (within reasonable range)
        4. Time proximity to exit_time
        """
        
        # Get orders from API
        async with httpx.AsyncClient() as client:
            try:
                symbol = trade['pair']
                amount = float(trade['position_size'])
                exit_price = float(trade['exit_price'])
                exit_time = trade['exit_time']
                
                # Search for sell orders
                response = await client.get(f"{self.api_base}/api/v1/orders?side=sell&symbol={symbol}&limit=500")
                if response.status_code != 200:
                    logger.error(f"Failed to get orders: {response.status_code}")
                    return None
                
                orders = response.json().get('orders', [])
                
                # Filter and score potential matches
                candidates = []
                for order in orders:
                    if order['status'] != 'FILLED':
                        continue
                        
                    order_amount = float(order['amount'])
                    order_price = float(order['price'])
                    order_time = datetime.fromisoformat(order['created_at'].replace('Z', '+00:00'))
                    
                    # Amount match (within 1% tolerance)
                    amount_diff = abs(order_amount - amount) / amount
                    if amount_diff > 0.01:  # 1% tolerance
                        continue
                    
                    # Price match (within 5% tolerance)
                    price_diff = abs(order_price - exit_price) / exit_price
                    if price_diff > 0.05:  # 5% tolerance
                        continue
                    
                    # Time proximity (within 2 hours)
                    time_diff = abs((order_time - exit_time).total_seconds())
                    if time_diff > 7200:  # 2 hours
                        continue
                    
                    # Calculate match score (lower is better)
                    score = amount_diff * 100 + price_diff * 10 + time_diff / 3600
                    candidates.append((order['exchange_order_id'], score, order))
                
                if candidates:
                    # Return best match
                    candidates.sort(key=lambda x: x[1])
                    best_match = candidates[0]
                    logger.info(f"Found match for trade {trade['trade_id']}: order {best_match[0]} (score: {best_match[1]:.4f})")
                    return best_match[0]
                
                logger.warning(f"No matching order found for trade {trade['trade_id']}")
                return None
                
            except Exception as e:
                logger.error(f"Error finding matching orders for trade {trade['trade_id']}: {e}")
                return None
    
    async def update_exit_id(self, trade_id: str, exit_id: str) -> bool:
        """Update trade with exit_id"""
        conn = await asyncpg.connect(**self.db_config)
        try:
            query = """
                UPDATE trading.trades 
                SET exit_id = $1, updated_at = CURRENT_TIMESTAMP
                WHERE trade_id = $2
            """
            await conn.execute(query, exit_id, trade_id)
            logger.info(f"✅ Updated trade {trade_id} with exit_id {exit_id}")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to update trade {trade_id}: {e}")
            return False
        finally:
            await conn.close()
    
    async def run_fix(self) -> Dict[str, int]:
        """Run the complete fix process"""
        stats = {
            'total_missing': 0,
            'matched': 0,
            'updated': 0,
            'failed': 0
        }
        
        # Get trades missing exit_id
        missing_trades = await self.get_missing_exit_id_trades()
        stats['total_missing'] = len(missing_trades)
        
        logger.info(f"Found {len(missing_trades)} trades missing exit_id")
        
        # Process each trade
        for trade in missing_trades:
            logger.info(f"Processing trade {trade['trade_id']} ({trade['pair']})")
            
            # Find matching order
            exit_id = await self.find_matching_orders(trade)
            if exit_id:
                stats['matched'] += 1
                
                # Update the trade
                if await self.update_exit_id(trade['trade_id'], exit_id):
                    stats['updated'] += 1
                else:
                    stats['failed'] += 1
            else:
                stats['failed'] += 1
        
        return stats

async def main():
    """Main execution function"""
    logger.info("🔧 Starting Exit ID Fix Process")
    
    fixer = ExitIDFixer()
    stats = await fixer.run_fix()
    
    logger.info("📊 Fix Process Complete!")
    logger.info(f"   Total Missing: {stats['total_missing']}")
    logger.info(f"   Matched: {stats['matched']}")
    logger.info(f"   Updated: {stats['updated']}")
    logger.info(f"   Failed: {stats['failed']}")
    
    success_rate = (stats['updated'] / stats['total_missing']) * 100 if stats['total_missing'] > 0 else 0
    logger.info(f"   Success Rate: {success_rate:.1f}%")

if __name__ == "__main__":
    asyncio.run(main())
