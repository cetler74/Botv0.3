#!/usr/bin/env python3
"""
Trading Bot Orchestrator - Enhanced Version with Scalping-Optimized Pair Selection
"""

import asyncio
import logging
import httpx
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from core.enhanced_pair_selector import EnhancedPairSelector
from core.config_manager import ConfigManager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Service URLs
config_service_url = os.getenv('CONFIG_SERVICE_URL', 'http://config-service:8001')
database_service_url = os.getenv('DATABASE_SERVICE_URL', 'http://database-service:8002')
exchange_service_url = os.getenv('EXCHANGE_SERVICE_URL', 'http://exchange-service:8003')
strategy_service_url = os.getenv('STRATEGY_SERVICE_URL', 'http://strategy-service:8004')

class TradingOrchestrator:
    """Enhanced Trading Orchestrator with Scalping-Optimized Pair Selection"""
    
    def __init__(self):
        self.pair_selections: Dict[str, List[str]] = {}
        self.config_manager = None  # Will be initialized in initialize()
        self.enhanced_pair_selector = None
        self.last_update = {}
        self.is_running = False
        
    async def initialize(self):
        """Initialize the orchestrator with enhanced pair selection"""
        logger.info("🚀 Initializing Enhanced Trading Orchestrator...")
        
        try:
            # Initialize config manager with service URL
            self.config_manager = ConfigManager()
            self.config_manager.config_service_url = config_service_url
            logger.info("✅ Config manager initialized with service URL")
            
            # Initialize the enhanced pair selector
            self.enhanced_pair_selector = EnhancedPairSelector(self.config_manager)
            logger.info("✅ Enhanced pair selector initialized successfully")
            
            # Initialize pair selections using enhanced selector
            await self._initialize_pair_selections()
            
            self.is_running = True
            logger.info("🎯 Enhanced Trading Orchestrator initialized successfully")
            
        except Exception as e:
            logger.error(f"❌ Failed to initialize orchestrator: {e}", exc_info=True)
            raise

    async def _initialize_pair_selections(self) -> None:
        """Initialize pair selections using the enhanced scalping-optimized selector"""
        logger.info("🚀 Initializing pair selections with EnhancedPairSelector...")
        
        try:
            # Get list of exchanges from config
            exchanges = []
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.get(f"{config_service_url}/api/v1/config/exchanges/list")
                response.raise_for_status()
                exchanges = response.json()['exchanges']

            for exchange_name in exchanges:
                try:
                    # Get exchange configuration
                    exchange_config_response = await client.get(f"{config_service_url}/api/v1/config/exchanges/{exchange_name}")
                    if exchange_config_response.status_code == 200:
                        exchange_config = exchange_config_response.json()
                        max_pairs = exchange_config.get('max_pairs', 15)
                        base_currency = exchange_config.get('base_currency', 'USDC')
                    else:
                        max_pairs = 15  # Default for scalping
                        base_currency = 'USDC'
                        logger.warning(f"⚠️ {exchange_name} config service error, using defaults: max_pairs={max_pairs}, base_currency={base_currency}")

                    logger.info(f"🎯 Attempting to select top {max_pairs} scalping pairs for {exchange_name.upper()} with base {base_currency}...")
                    
                    # Use enhanced pair selector
                    selected_pairs_with_scores = await self.enhanced_pair_selector.select_top_scalping_pairs(
                        exchange_name, base_currency, max_pairs
                    )

                    if selected_pairs_with_scores:
                        selected_pairs_list = [pair[0] for pair in selected_pairs_with_scores]
                        scores = [pair[1] for pair in selected_pairs_with_scores]
                        
                        self.pair_selections[exchange_name] = selected_pairs_list
                        logger.info(f"✅ Enhanced selector found {len(selected_pairs_list)} pairs for {exchange_name}: {list(zip(selected_pairs_list, scores))}")
                        
                        # Persist selected pairs to database
                        await self._persist_pairs_to_database(exchange_name, selected_pairs_list)
                        
                    else:
                        logger.warning(f"⚠️ Enhanced selector found no suitable pairs for {exchange_name}. Falling back to legacy selection.")
                        await self._fallback_to_legacy_selector(exchange_name, base_currency, max_pairs)
                        
                except Exception as e:
                    logger.error(f"❌ Error selecting pairs for {exchange_name}: {e}")
                    # Fallback to legacy selection
                    await self._fallback_to_legacy_selector(exchange_name, 'USDC', 15)

            logger.info(f"🎯 Initialized pair selections for {len(self.pair_selections)} exchanges using enhanced selector")

        except Exception as e:
            logger.error(f"❌ Fatal error during enhanced pair selection initialization: {e}", exc_info=True)
            logger.warning("⚠️ Falling back to legacy pair selection due to error.")
            # If enhanced selector fails entirely, try to initialize all exchanges with legacy
            await self._fallback_all_exchanges_to_legacy()

    async def _fallback_to_legacy_selector(self, exchange_name: str, base_currency: str, max_pairs: int):
        """Fallback to legacy pair selection method"""
        try:
            logger.info(f"🔄 Using legacy pair selection for {exchange_name}")
            
            # Get pairs from database as fallback
            async with httpx.AsyncClient(timeout=60.0) as client:
                pairs_response = await client.get(f"{database_service_url}/api/v1/pairs/{exchange_name}")
                if pairs_response.status_code == 200:
                    pairs_data = pairs_response.json()
                    all_pairs = pairs_data.get('pairs', [])
                    
                    if all_pairs and len(all_pairs) > 0:
                        # Enforce max_pairs limit
                        if len(all_pairs) > max_pairs:
                            logger.warning(f"Exchange {exchange_name} has {len(all_pairs)} pairs, limiting to {max_pairs}")
                            self.pair_selections[exchange_name] = all_pairs[:max_pairs]
                        else:
                            self.pair_selections[exchange_name] = all_pairs
                        logger.info(f"✅ Legacy fallback: {exchange_name} loaded {len(self.pair_selections[exchange_name])} pairs from database")
                    else:
                        logger.warning(f"⚠️ No pairs found in database for {exchange_name}")
                        self.pair_selections[exchange_name] = []
                else:
                    logger.error(f"❌ Failed to get pairs from database for {exchange_name}")
                    self.pair_selections[exchange_name] = []
                    
        except Exception as e:
            logger.error(f"❌ Error in legacy fallback for {exchange_name}: {e}")
            self.pair_selections[exchange_name] = []

    async def _fallback_all_exchanges_to_legacy(self):
        """Fallback all exchanges to legacy selection"""
        try:
            exchanges = []
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.get(f"{config_service_url}/api/v1/config/exchanges/list")
                response.raise_for_status()
                exchanges = response.json()['exchanges']

            for exchange_name in exchanges:
                await self._fallback_to_legacy_selector(exchange_name, 'USDC', 15)
                
        except Exception as e:
            logger.error(f"❌ Error in fallback all exchanges: {e}")

    async def _persist_pairs_to_database(self, exchange_name: str, pairs: List[str]):
        """Persist selected pairs to database"""
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{database_service_url}/api/v1/pairs/{exchange_name}",
                    json={"pairs": pairs}
                )
                if response.status_code == 200:
                    logger.info(f"✅ Persisted {len(pairs)} pairs to database for {exchange_name}")
                else:
                    logger.warning(f"⚠️ Failed to persist pairs to database for {exchange_name}: {response.status_code}")
        except Exception as e:
            logger.error(f"❌ Error persisting pairs to database for {exchange_name}: {e}")

    async def update_pair_selections(self):
        """Update pair selections using enhanced selector"""
        if not self.enhanced_pair_selector:
            logger.warning("⚠️ Enhanced pair selector not initialized, skipping update")
            return
            
        logger.info("🔄 Updating pair selections with enhanced selector...")
        
        try:
            # Get list of exchanges
            exchanges = []
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.get(f"{config_service_url}/api/v1/config/exchanges/list")
                response.raise_for_status()
                exchanges = response.json()['exchanges']

            for exchange_name in exchanges:
                try:
                    # Get exchange configuration
                    exchange_config_response = await client.get(f"{config_service_url}/api/v1/config/exchanges/{exchange_name}")
                    if exchange_config_response.status_code == 200:
                        exchange_config = exchange_config_response.json()
                        max_pairs = exchange_config.get('max_pairs', 15)
                        base_currency = exchange_config.get('base_currency', 'USDC')
                    else:
                        max_pairs = 15
                        base_currency = 'USDC'

                    # Use enhanced pair selector for update
                    selected_pairs_with_scores = await self.enhanced_pair_selector.select_top_scalping_pairs(
                        exchange_name, base_currency, max_pairs
                    )

                    if selected_pairs_with_scores:
                        selected_pairs_list = [pair[0] for pair in selected_pairs_with_scores]
                        scores = [pair[1] for pair in selected_pairs_with_scores]
                        
                        old_pairs = self.pair_selections.get(exchange_name, [])
                        self.pair_selections[exchange_name] = selected_pairs_list
                        
                        logger.info(f"🔄 Updated {exchange_name}: {len(selected_pairs_list)} pairs (scores: {scores})")
                        
                        # Persist to database
                        await self._persist_pairs_to_database(exchange_name, selected_pairs_list)
                        
                        # Update timestamp
                        self.last_update[exchange_name] = datetime.now()
                        
                    else:
                        logger.warning(f"⚠️ Enhanced selector found no suitable pairs for {exchange_name} during update")
                        
                except Exception as e:
                    logger.error(f"❌ Error updating pairs for {exchange_name}: {e}")

        except Exception as e:
            logger.error(f"❌ Error during pair selection update: {e}")

    async def get_pair_selections(self) -> Dict[str, List[str]]:
        """Get current pair selections"""
        return self.pair_selections.copy()

    async def get_exchange_pairs(self, exchange_name: str) -> List[str]:
        """Get pairs for a specific exchange"""
        return self.pair_selections.get(exchange_name, [])

    async def run(self):
        """Main orchestrator loop"""
        logger.info("🚀 Starting Enhanced Trading Orchestrator main loop...")
        
        while self.is_running:
            try:
                # Update pair selections every 15 minutes (scalping optimized)
                await self.update_pair_selections()
                
                # Wait 15 minutes before next update
                await asyncio.sleep(900)  # 15 minutes
                
            except Exception as e:
                logger.error(f"❌ Error in orchestrator main loop: {e}", exc_info=True)
                await asyncio.sleep(60)  # Wait 1 minute before retrying

    async def stop(self):
        """Stop the orchestrator"""
        logger.info("🛑 Stopping Enhanced Trading Orchestrator...")
        self.is_running = False

# Global orchestrator instance
orchestrator = TradingOrchestrator()

async def main():
    """Main entry point"""
    try:
        await orchestrator.initialize()
        await orchestrator.run()
    except KeyboardInterrupt:
        logger.info("🛑 Received interrupt signal")
    except Exception as e:
        logger.error(f"❌ Fatal error in main: {e}", exc_info=True)
    finally:
        await orchestrator.stop()

if __name__ == "__main__":
    asyncio.run(main())
