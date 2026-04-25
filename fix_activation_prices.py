#!/usr/bin/env python3
"""
Fix activation prices for OPEN trades
Set activation_price to entry_price * 1.007 (0.7% above entry)
"""

import asyncio
import httpx
import logging
from typing import List, Dict, Any

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Configuration
DATABASE_SERVICE_URL = "http://localhost:8002"

async def get_open_trades() -> List[Dict[str, Any]]:
    """Get all OPEN trades from database"""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/trades?status=OPEN")
            if response.status_code == 200:
                data = response.json()
                return data.get('trades', [])
            else:
                logger.error(f"Failed to get trades: {response.status_code}")
                return []
    except Exception as e:
        logger.error(f"Error getting trades: {e}")
        return []

async def update_trade_activation_price(trade_id: str, activation_price: float) -> bool:
    """Update trail_stop_trigger for a specific trade"""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.put(
                f"{DATABASE_SERVICE_URL}/api/v1/trades/{trade_id}",
                json={"trail_stop_trigger": activation_price}
            )
            if response.status_code == 200:
                logger.info(f"✅ Updated trade {trade_id}: trail_stop_trigger = ${activation_price:.4f}")
                return True
            else:
                logger.error(f"❌ Failed to update trade {trade_id}: {response.status_code}")
                return False
    except Exception as e:
        logger.error(f"❌ Error updating trade {trade_id}: {e}")
        return False

async def fix_activation_prices():
    """Fix activation prices for all OPEN trades"""
    logger.info("🔧 Starting activation price fix for OPEN trades...")
    
    # Get all OPEN trades
    trades = await get_open_trades()
    if not trades:
        logger.warning("⚠️ No OPEN trades found")
        return
    
    logger.info(f"📊 Found {len(trades)} OPEN trades to process")
    
    fixed_count = 0
    skipped_count = 0
    
    for trade in trades:
        trade_id = trade.get('trade_id')
        entry_price = trade.get('entry_price')
        current_activation_price = trade.get('trail_stop_trigger')
        
        if not entry_price:
            logger.warning(f"⚠️ Trade {trade_id} has no entry_price, skipping")
            skipped_count += 1
            continue
        
        # Calculate correct activation price: entry_price * 1.007 (0.7% above entry)
        correct_activation_price = round(entry_price * 1.007, 4)
        
        # Check if activation price needs updating
        if current_activation_price is None or abs(current_activation_price - correct_activation_price) > 0.0001:
            logger.info(f"🔄 Trade {trade_id}: entry=${entry_price:.4f}, current_trigger=${current_activation_price}, correct_trigger=${correct_activation_price:.4f}")
            
            success = await update_trade_activation_price(trade_id, correct_activation_price)
            if success:
                fixed_count += 1
            else:
                skipped_count += 1
        else:
            logger.info(f"✅ Trade {trade_id}: trail stop trigger already correct (${current_activation_price:.4f})")
            skipped_count += 1
    
    logger.info(f"🎯 Fix complete: {fixed_count} trades updated, {skipped_count} skipped")

async def main():
    """Main function"""
    try:
        await fix_activation_prices()
    except Exception as e:
        logger.error(f"❌ Script failed: {e}")

if __name__ == "__main__":
    asyncio.run(main())
