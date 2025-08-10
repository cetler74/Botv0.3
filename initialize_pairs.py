#!/usr/bin/env python3
"""
Pair Initialization Script
Populates the database with trading pairs based on config.yaml settings
"""

import asyncio
import asyncpg
import httpx
import logging
import sys
from pathlib import Path

# Add the project root to the path to import local modules
sys.path.append(str(Path(__file__).parent))

from core.pair_selector import select_top_pairs_ccxt

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class PairInitializer:
    """Initialize trading pairs in the database based on config"""
    
    def __init__(self):
        self.db_pool = None
        self.config_service_url = "http://localhost:8001"
        self.database_service_url = "http://localhost:8002"
        
    async def initialize_db(self):
        """Initialize database connection"""
        try:
            self.db_pool = await asyncpg.create_pool(
                host='localhost',
                port=5432,
                database='trading_bot_futures',
                user='carloslarramba',
                password='mypassword',
                min_size=1,
                max_size=5
            )
            logger.info("✅ Database connection pool initialized")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to initialize database: {e}")
            return False
    
    async def get_config(self):
        """Get configuration from config service"""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{self.config_service_url}/api/v1/config/all")
                if response.status_code == 200:
                    config = response.json()
                    logger.info("✅ Retrieved configuration from config service")
                    return config
                else:
                    logger.error(f"❌ Failed to get config: {response.status_code}")
                    return None
        except Exception as e:
            logger.error(f"❌ Error getting config: {e}")
            return None
    
    async def select_pairs_for_exchange(self, exchange_id, base_currency, max_pairs):
        """Select top pairs for a specific exchange"""
        try:
            logger.info(f"🔍 Selecting top {max_pairs} pairs for {exchange_id} with base currency {base_currency}")
            
            result = await select_top_pairs_ccxt(
                exchange_id=exchange_id,
                base_currency=base_currency,
                num_pairs=max_pairs,
                market_type='swap'
            )
            
            selected_pairs = result['selected_pairs']
            logger.info(f"✅ Selected {len(selected_pairs)} pairs for {exchange_id}: {selected_pairs}")
            return selected_pairs
            
        except Exception as e:
            logger.error(f"❌ Error selecting pairs for {exchange_id}: {e}")
            return []
    
    async def store_pairs_in_database(self, exchange_id, pairs):
        """Store selected pairs in the database"""
        try:
            async with self.db_pool.acquire() as conn:
                # First, delete existing pairs for this exchange
                await conn.execute(
                    "DELETE FROM trading.pairs WHERE exchange = $1",
                    exchange_id
                )
                logger.info(f"🗑️  Removed existing pairs for {exchange_id}")
                
                # Insert new pairs as JSONB
                import json
                pairs_json = json.dumps(pairs)
                await conn.execute("""
                    INSERT INTO trading.pairs (exchange, pair_list, timestamp, created_at)
                    VALUES ($1, $2, NOW(), NOW())
                """, exchange_id, pairs_json)
                
                logger.info(f"✅ Stored {len(pairs)} pairs for {exchange_id} in database")
                
        except Exception as e:
            logger.error(f"❌ Error storing pairs for {exchange_id}: {e}")
    
    async def verify_pairs_in_database(self):
        """Verify that pairs were stored correctly"""
        try:
            async with self.db_pool.acquire() as conn:
                result = await conn.fetch("""
                    SELECT exchange, pair_list, timestamp
                    FROM trading.pairs 
                    ORDER BY exchange
                """)
                
                logger.info("📊 Current pairs in database:")
                for row in result:
                    import json
                    pairs = json.loads(row['pair_list'])
                    logger.info(f"  {row['exchange']}: {len(pairs)} pairs")
                    logger.info(f"    Pairs: {pairs}")
                    logger.info(f"    Updated: {row['timestamp']}")
                
        except Exception as e:
            logger.error(f"❌ Error verifying pairs: {e}")
    
    async def initialize_all_pairs(self):
        """Initialize pairs for all exchanges based on config"""
        logger.info("🚀 Starting pair initialization process")
        
        # Initialize database connection
        if not await self.initialize_db():
            return False
        
        # Get configuration
        config = await self.get_config()
        if not config:
            logger.error("❌ Cannot proceed without configuration")
            return False
        
        exchanges = config.get('exchanges', {})
        logger.info(f"📋 Found {len(exchanges)} exchanges in config: {list(exchanges.keys())}")
        
        success_count = 0
        
        for exchange_id, exchange_config in exchanges.items():
            try:
                max_pairs = exchange_config.get('max_pairs', 10)
                base_currency = exchange_config.get('base_currency', 'USDC')
                
                logger.info(f"\n🏪 Processing {exchange_id}:")
                logger.info(f"   Max pairs: {max_pairs}")
                logger.info(f"   Base currency: {base_currency}")
                
                # Select pairs for this exchange
                selected_pairs = await self.select_pairs_for_exchange(
                    exchange_id, base_currency, max_pairs
                )
                
                if selected_pairs:
                    # Store pairs in database
                    await self.store_pairs_in_database(exchange_id, selected_pairs)
                    success_count += 1
                else:
                    logger.warning(f"⚠️  No pairs selected for {exchange_id}")
                
            except Exception as e:
                logger.error(f"❌ Error processing {exchange_id}: {e}")
        
        # Verify results
        logger.info(f"\n📊 Pair initialization completed for {success_count}/{len(exchanges)} exchanges")
        await self.verify_pairs_in_database()
        
        # Close database connection
        if self.db_pool:
            await self.db_pool.close()
        
        return success_count == len(exchanges)

async def main():
    """Main entry point"""
    initializer = PairInitializer()
    success = await initializer.initialize_all_pairs()
    
    if success:
        logger.info("✅ Pair initialization completed successfully")
        return 0
    else:
        logger.error("❌ Pair initialization failed")
        return 1

if __name__ == "__main__":
    import sys
    sys.exit(asyncio.run(main()))