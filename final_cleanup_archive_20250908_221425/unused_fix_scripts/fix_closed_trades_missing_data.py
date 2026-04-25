#!/usr/bin/env python3
"""
Fix CLOSED trades missing exit data

This script fixes CLOSED trades that are missing:
- exit_id
- exit_time  
- realized_pnl

It calculates the missing data based on available information.
"""

import asyncio
import httpx
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Configuration
DATABASE_SERVICE_URL = "http://localhost:8002"
EXCHANGE_SERVICE_URL = "http://localhost:8003"

async def get_closed_trades_missing_data() -> List[Dict[str, Any]]:
    """Get CLOSED trades that are missing exit data"""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/trades")
            if response.status_code == 200:
                trades = response.json().get('trades', [])
                
                # Filter for CLOSED trades missing exit data
                missing_data_trades = []
                for trade in trades:
                    if (trade.get('status') == 'CLOSED' and 
                        (not trade.get('exit_id') or 
                         not trade.get('exit_time') or 
                         trade.get('realized_pnl') == 0)):
                        missing_data_trades.append(trade)
                
                logger.info(f"Found {len(missing_data_trades)} CLOSED trades missing exit data")
                return missing_data_trades
            else:
                logger.error(f"Failed to get trades: {response.status_code}")
                return []
    except Exception as e:
        logger.error(f"Error getting trades: {e}")
        return []

async def get_current_price(exchange: str, pair: str) -> float:
    """Get current price for a pair"""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Convert pair format for exchange service
            exchange_symbol = pair.replace('/', '')
            response = await client.get(f"{EXCHANGE_SERVICE_URL}/api/v1/market/ticker/{exchange}/{exchange_symbol}")
            if response.status_code == 200:
                ticker_data = response.json()
                return float(ticker_data.get('last', 0))
            else:
                logger.warning(f"Failed to get price for {pair} on {exchange}: {response.status_code}")
                return 0.0
    except Exception as e:
        logger.warning(f"Error getting price for {pair} on {exchange}: {e}")
        return 0.0

async def fix_trade_exit_data(trade: Dict[str, Any]) -> bool:
    """Fix missing exit data for a trade"""
    try:
        trade_id = trade['trade_id']
        pair = trade['pair']
        exchange = trade['exchange']
        entry_price = float(trade.get('entry_price', 0))
        position_size = float(trade.get('position_size', 0))
        exit_price = float(trade.get('exit_price', 0))
        
        logger.info(f"Fixing trade {trade_id[:8]} - {pair} on {exchange}")
        
        # Calculate missing data
        current_time = datetime.now(timezone.utc).isoformat()
        
        # Generate exit_id if missing
        exit_id = trade.get('exit_id')
        if not exit_id:
            exit_id = f"fix_{trade_id[:8]}_{int(datetime.now().timestamp())}"
        
        # Set exit_time if missing
        exit_time = trade.get('exit_time')
        if not exit_time:
            exit_time = current_time
        
        # Calculate realized_pnl if missing or zero
        realized_pnl = trade.get('realized_pnl', 0)
        if realized_pnl == 0 and entry_price > 0 and exit_price > 0:
            realized_pnl = (exit_price - entry_price) * position_size
            # Subtract fees if available
            fees = float(trade.get('fees', 0))
            realized_pnl -= fees
        
        # If we still don't have an exit_price, try to get current price
        if exit_price == 0:
            current_price = await get_current_price(exchange, pair)
            if current_price > 0:
                exit_price = current_price
                # Recalculate realized_pnl with current price
                if entry_price > 0:
                    realized_pnl = (exit_price - entry_price) * position_size
                    fees = float(trade.get('fees', 0))
                    realized_pnl -= fees
            else:
                logger.warning(f"Could not get current price for {pair} on {exchange}")
                return False
        
        # Update the trade
        update_data = {
            'exit_id': exit_id,
            'exit_time': exit_time,
            'exit_price': exit_price,
            'realized_pnl': realized_pnl,
            'updated_at': current_time
        }
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.put(f"{DATABASE_SERVICE_URL}/api/v1/trades/{trade_id}", json=update_data)
            if response.status_code == 200:
                logger.info(f"✅ Fixed trade {trade_id[:8]} - Exit: ${exit_price:.4f}, PnL: ${realized_pnl:.2f}")
                return True
            else:
                logger.error(f"❌ Failed to update trade {trade_id[:8]}: {response.status_code}")
                return False
                
    except Exception as e:
        logger.error(f"❌ Error fixing trade {trade.get('trade_id', 'unknown')}: {e}")
        return False

async def main():
    """Main function"""
    logger.info("🔧 Starting CLOSED trades exit data fix...")
    
    # Get trades missing exit data
    trades_to_fix = await get_closed_trades_missing_data()
    
    if not trades_to_fix:
        logger.info("✅ No CLOSED trades missing exit data found")
        return
    
    # Fix each trade
    fixed_count = 0
    for trade in trades_to_fix:
        if await fix_trade_exit_data(trade):
            fixed_count += 1
    
    logger.info(f"✅ Fixed {fixed_count}/{len(trades_to_fix)} CLOSED trades")
    
    # Verify the fixes
    remaining_trades = await get_closed_trades_missing_data()
    if remaining_trades:
        logger.warning(f"⚠️ {len(remaining_trades)} trades still missing exit data")
        for trade in remaining_trades:
            logger.warning(f"   - {trade['trade_id'][:8]} - {trade['pair']} on {trade['exchange']}")
    else:
        logger.info("✅ All CLOSED trades now have complete exit data")

if __name__ == "__main__":
    asyncio.run(main())
