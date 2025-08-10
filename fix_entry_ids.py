#!/usr/bin/env python3
"""
Fix Missing Entry IDs

Updates all recovered trades with their correct entry_id values for exchange traceability
"""

import asyncio
import httpx
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Mapping of trade_id to entry_id from our recovery
TRADE_TO_ENTRY_ID_MAPPING = {
    'a46a4971-dcf5-43fc-a526-08d5cc2bb151': '6530219581771941056',  # AAVE/USD
    'd66f85ca-8af7-4e85-8721-52c4c7fa2917': '6530219581768167803',  # ADA/USD
    'cc10948e-5b7e-4dfb-85c8-6522536a2883': '6530219581768058699',  # ADA/USD  
    '546c24fa-629a-4e56-a87d-5643796ef7ec': '6530219581767942498',  # ADA/USD
    '980ec17c-4ff5-4e53-8f4e-5f7d67000777': '6530219581767853040',  # ADA/USD
    '6f8684d3-e400-434e-9237-7bb1b8795450': '6530219581761276035',  # ACX/USD
}

async def fix_missing_entry_ids():
    """Update all recovered trades with their correct entry_id values"""
    database_service_url = "http://localhost:8002"
    
    logger.info("🔧 Starting entry_id fix for recovered trades...")
    
    async with httpx.AsyncClient() as client:
        # Get all current trades
        response = await client.get(f"{database_service_url}/api/v1/trades")
        if response.status_code != 200:
            logger.error(f"❌ Failed to get trades: HTTP {response.status_code}")
            return
        
        trades = response.json().get('trades', [])
        logger.info(f"📊 Found {len(trades)} trades in database")
        
        updated_count = 0
        
        for trade in trades:
            trade_id = trade.get('trade_id')
            current_entry_id = trade.get('entry_id')
            
            if trade_id in TRADE_TO_ENTRY_ID_MAPPING:
                correct_entry_id = TRADE_TO_ENTRY_ID_MAPPING[trade_id]
                
                if current_entry_id != correct_entry_id:
                    logger.info(f"🔧 Updating {trade_id}: entry_id {current_entry_id} -> {correct_entry_id}")
                    
                    # Update the trade with correct entry_id
                    update_data = {
                        'entry_id': correct_entry_id
                    }
                    
                    update_response = await client.put(
                        f"{database_service_url}/api/v1/trades/{trade_id}",
                        json=update_data
                    )
                    
                    if update_response.status_code == 200:
                        logger.info(f"✅ Successfully updated entry_id for {trade['pair']} - Order {correct_entry_id}")
                        updated_count += 1
                    else:
                        logger.error(f"❌ Failed to update {trade_id}: HTTP {update_response.status_code}")
                        logger.error(f"Response: {update_response.text}")
                else:
                    logger.info(f"✅ {trade_id} already has correct entry_id: {correct_entry_id}")
        
        logger.info(f"\n✅ ENTRY_ID FIX COMPLETE!")
        logger.info(f"📊 Updated {updated_count} trades with correct entry_id values")
        
        # Verify the fixes
        logger.info(f"\n🔍 Verifying entry_id fixes...")
        verify_response = await client.get(f"{database_service_url}/api/v1/trades")
        if verify_response.status_code == 200:
            verified_trades = verify_response.json().get('trades', [])
            
            logger.info(f"📋 VERIFICATION RESULTS:")
            for trade in verified_trades:
                trade_id = trade.get('trade_id')
                entry_id = trade.get('entry_id')
                pair = trade.get('pair')
                
                if trade_id in TRADE_TO_ENTRY_ID_MAPPING:
                    expected_entry_id = TRADE_TO_ENTRY_ID_MAPPING[trade_id]
                    status = "✅ CORRECT" if entry_id == expected_entry_id else "❌ MISSING"
                    logger.info(f"  {status} {pair} - Trade: {trade_id[:8]}... Entry ID: {entry_id}")

async def main():
    logger.info("🚀 Starting Entry ID Fix Script...")
    await fix_missing_entry_ids()
    logger.info("🎉 Entry ID fix completed!")

if __name__ == "__main__":
    asyncio.run(main())