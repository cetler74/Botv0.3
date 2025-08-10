#!/usr/bin/env python3
"""
Docker Pair Initialization Script
Populates the Docker PostgreSQL database with trading pairs based on config.yaml settings
"""

import asyncio
import asyncpg
import httpx
import logging
import sys
from pathlib import Path
import json

# Add the project root to the path to import local modules
sys.path.append(str(Path(__file__).parent))

from core.pair_selector import select_top_pairs_ccxt

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def populate_docker_database():
    """Populate Docker PostgreSQL with trading pairs"""
    logger.info("🚀 Starting Docker pair initialization process")
    
    try:
        # Connect directly to Docker PostgreSQL
        conn = await asyncpg.connect(
            host='localhost',
            port=5432,
            database='trading_bot_futures',
            user='carloslarramba',
            password='mypassword'
        )
        logger.info("✅ Connected to Docker PostgreSQL database")
        
        # Get config from service
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get("http://localhost:8001/api/v1/config/all")
            if response.status_code != 200:
                logger.error(f"❌ Failed to get config: {response.status_code}")
                return False
            config = response.json()
            logger.info("✅ Retrieved configuration from config service")
        
        exchanges = config.get('exchanges', {})
        logger.info(f"📋 Found {len(exchanges)} exchanges in config: {list(exchanges.keys())}")
        
        # Clear existing pairs
        await conn.execute("DELETE FROM trading.pairs")
        logger.info("🗑️  Cleared existing pairs")
        
        success_count = 0
        
        for exchange_id, exchange_config in exchanges.items():
            try:
                max_pairs = exchange_config.get('max_pairs', 10)
                base_currency = exchange_config.get('base_currency', 'USDC')
                
                logger.info(f"\n🏪 Processing {exchange_id}:")
                logger.info(f"   Max pairs: {max_pairs}")
                logger.info(f"   Base currency: {base_currency}")
                
                # Select pairs for this exchange
                logger.info(f"🔍 Selecting top {max_pairs} pairs for {exchange_id} with base currency {base_currency}")
                
                result = await select_top_pairs_ccxt(
                    exchange_id=exchange_id,
                    base_currency=base_currency,
                    num_pairs=max_pairs,
                    market_type=exchange_config.get('market_type', 'spot')
                )
                
                selected_pairs = result['selected_pairs']
                logger.info(f"✅ Selected {len(selected_pairs)} pairs for {exchange_id}: {selected_pairs}")
                
                if selected_pairs:
                    # Store pairs in database
                    pairs_json = json.dumps(selected_pairs)
                    await conn.execute("""
                        INSERT INTO trading.pairs (exchange, pair_list, timestamp, created_at)
                        VALUES ($1, $2, NOW(), NOW())
                    """, exchange_id, pairs_json)
                    
                    logger.info(f"✅ Stored {len(selected_pairs)} pairs for {exchange_id} in Docker database")
                    success_count += 1
                else:
                    logger.warning(f"⚠️  No pairs selected for {exchange_id}")
                
            except Exception as e:
                logger.error(f"❌ Error processing {exchange_id}: {e}")
        
        # Verify results
        logger.info(f"\n📊 Pair initialization completed for {success_count}/{len(exchanges)} exchanges")
        
        result = await conn.fetch("""
            SELECT exchange, pair_list, timestamp
            FROM trading.pairs 
            ORDER BY exchange
        """)
        
        logger.info("📊 Current pairs in Docker database:")
        for row in result:
            pairs = json.loads(row['pair_list'])
            logger.info(f"  {row['exchange']}: {len(pairs)} pairs")
            logger.info(f"    Pairs: {pairs}")
            logger.info(f"    Updated: {row['timestamp']}")
        
        await conn.close()
        logger.info("✅ Docker pair initialization completed successfully")
        return success_count == len(exchanges)
        
    except Exception as e:
        logger.error(f"❌ Error in Docker pair initialization: {e}")
        return False

if __name__ == "__main__":
    success = asyncio.run(populate_docker_database())
    sys.exit(0 if success else 1)