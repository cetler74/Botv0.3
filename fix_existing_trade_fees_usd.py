#!/usr/bin/env python3
"""
Fix existing trades to properly calculate total_fees_usd from entry and exit fee amounts
This script updates all trades to have correct USD fee calculations
"""

import asyncio
import httpx
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATABASE_SERVICE_URL = "http://localhost:8002"

async def fix_trade_fees():
    """Update all trades to recalculate total_fees_usd"""
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            # Get all trades
            logger.info("📊 Fetching all trades to fix fee calculations...")
            response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/trades?limit=1000")
            response.raise_for_status()
            
            trades_data = response.json()
            trades = trades_data.get('trades', [])
            
            logger.info(f"🔍 Found {len(trades)} trades to process")
            
            updated_count = 0
            
            for trade in trades:
                trade_id = trade.get('trade_id')
                entry_fee_amount = trade.get('entry_fee_amount', 0)
                entry_fee_currency = trade.get('entry_fee_currency')
                exit_fee_amount = trade.get('exit_fee_amount', 0) 
                exit_fee_currency = trade.get('exit_fee_currency')
                current_total_fees_usd = trade.get('total_fees_usd', 0)
                
                # Skip if already has correct USD fees
                if current_total_fees_usd > 0:
                    continue
                    
                # Skip if no fees at all
                if not entry_fee_amount and not exit_fee_amount:
                    continue
                    
                # Calculate expected USD fees
                expected_usd_fees = 0.0
                if entry_fee_amount and entry_fee_amount > 0:
                    expected_usd_fees += float(entry_fee_amount)
                if exit_fee_amount and exit_fee_amount > 0:
                    expected_usd_fees += float(exit_fee_amount)
                    
                if expected_usd_fees > 0:
                    logger.info(f"🔧 Updating trade {trade_id}: entry_fee={entry_fee_amount} {entry_fee_currency}, "
                              f"exit_fee={exit_fee_amount} {exit_fee_currency}, expected_usd={expected_usd_fees}")
                    
                    # Trigger an update that will recalculate total_fees_usd
                    update_data = {
                        'entry_fee_amount': float(entry_fee_amount or 0),
                        'entry_fee_currency': entry_fee_currency
                    }
                    
                    update_response = await client.put(
                        f"{DATABASE_SERVICE_URL}/api/v1/trades/{trade_id}",
                        json=update_data
                    )
                    
                    if update_response.status_code == 200:
                        updated_count += 1
                        logger.info(f"✅ Updated trade {trade_id}")
                    else:
                        logger.error(f"❌ Failed to update trade {trade_id}: {update_response.text}")
                        
            logger.info(f"🎯 Successfully updated {updated_count} trades with USD fee calculations")
            
        except Exception as e:
            logger.error(f"❌ Error fixing trade fees: {e}")
            raise

if __name__ == "__main__":
    asyncio.run(fix_trade_fees())