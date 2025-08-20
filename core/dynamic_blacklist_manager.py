import asyncio
import httpx
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Set
import json

logger = logging.getLogger(__name__)

class DynamicBlacklistManager:
    """Manages dynamic blacklisting of poor-performing pairs with cooling periods"""
    
    def __init__(self):
        self.blacklist_cache = {}
        self.cooling_periods = {}
        self.config = None
        
    async def load_config(self):
        """Load configuration from config service"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get("http://config-service:8001/api/v1/config/all")
                if response.status_code == 200:
                    self.config = response.json()
                    return True
                else:
                    logger.error(f"Failed to load config: {response.status_code}")
                    return False
        except Exception as e:
            logger.error(f"Error loading config: {e}")
            return False
    
    async def get_active_blacklist(self) -> List[str]:
        """Get currently active blacklisted pairs (considering cooling periods)"""
        if not self.config:
            await self.load_config()
            
        current_time = datetime.utcnow()
        active_blacklist = []
        
        # Get static blacklist from config
        static_blacklist = self.config.get('pair_selector', {}).get('blacklisted_pairs', [])
        
        for pair in static_blacklist:
            # Check if cooling period has expired
            if pair in self.cooling_periods:
                blacklist_time = datetime.fromisoformat(self.cooling_periods[pair])
                cooling_hours = self.config.get('pair_selector', {}).get('auto_blacklist', {}).get('cooling_period_hours', 24)
                
                if current_time - blacklist_time < timedelta(hours=cooling_hours):
                    active_blacklist.append(pair)
                    logger.debug(f"[Blacklist] {pair} still in cooling period")
                else:
                    logger.info(f"[Blacklist] ✅ {pair} cooling period expired, removing from blacklist")
                    del self.cooling_periods[pair]
            else:
                # First time seeing this pair, add cooling period
                self.cooling_periods[pair] = current_time.isoformat()
                active_blacklist.append(pair)
                logger.info(f"[Blacklist] 🚫 {pair} added to blacklist with 24h cooling period")
        
        return active_blacklist
    
    async def evaluate_pair_performance(self, exchange: str, pair: str) -> bool:
        """Evaluate if a pair should be blacklisted based on performance"""
        try:
            if not self.config:
                await self.load_config()
                
            auto_blacklist_config = self.config.get('pair_selector', {}).get('auto_blacklist', {})
            if not auto_blacklist_config.get('enabled', False):
                return False
                
            # Get pair performance data
            pair_stats = await self._get_pair_performance(exchange, pair)
            if not pair_stats:
                return False
                
            min_trades = auto_blacklist_config.get('min_trades_threshold', 10)
            max_win_rate = auto_blacklist_config.get('max_win_rate_threshold', 0.45)
            max_avg_pnl = auto_blacklist_config.get('max_avg_pnl_threshold', -0.10)
            
            # Only evaluate pairs with sufficient trade history
            if pair_stats['closed_trades'] < min_trades:
                return False
                
            should_blacklist = (
                pair_stats['win_rate'] < max_win_rate or 
                pair_stats['avg_pnl_per_trade'] < max_avg_pnl
            )
            
            if should_blacklist:
                logger.warning(f"[Blacklist] 🚫 AUTO-BLACKLISTING {pair} on {exchange}:")
                logger.warning(f"   - Closed trades: {pair_stats['closed_trades']}")
                logger.warning(f"   - Win rate: {pair_stats['win_rate']:.2%} (threshold: {max_win_rate:.2%})")
                logger.warning(f"   - Avg PnL: ${pair_stats['avg_pnl_per_trade']:.4f} (threshold: ${max_avg_pnl:.4f})")
                
                # Add to dynamic blacklist
                await self._add_to_blacklist(pair)
                
            return should_blacklist
            
        except Exception as e:
            logger.error(f"Error evaluating pair {pair} performance: {e}")
            return False
    
    async def _get_pair_performance(self, exchange: str, pair: str) -> Dict:
        """Get performance statistics for a pair"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Get analytics data
                response = await client.get(f"http://database-service:8001/api/v1/analytics/pair/{exchange}/{pair}")
                if response.status_code == 200:
                    return response.json()
                else:
                    logger.debug(f"No performance data found for {pair} on {exchange}")
                    return None
        except Exception as e:
            logger.error(f"Error fetching performance data for {pair}: {e}")
            return None
    
    async def _add_to_blacklist(self, pair: str):
        """Add pair to dynamic blacklist"""
        try:
            current_time = datetime.utcnow()
            self.cooling_periods[pair] = current_time.isoformat()
            
            # Update config with new blacklisted pair
            if not self.config:
                await self.load_config()
                
            current_blacklist = self.config.get('pair_selector', {}).get('blacklisted_pairs', [])
            if pair not in current_blacklist:
                current_blacklist.append(pair)
                
                # Update config service
                await self._update_config_blacklist(current_blacklist)
                
        except Exception as e:
            logger.error(f"Error adding {pair} to blacklist: {e}")
    
    async def _update_config_blacklist(self, blacklist: List[str]):
        """Update config service with new blacklist"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                update_data = {
                    "pair_selector.blacklisted_pairs": blacklist
                }
                response = await client.patch("http://config-service:8003/config", json=update_data)
                if response.status_code == 200:
                    logger.info(f"[Blacklist] Updated config with {len(blacklist)} blacklisted pairs")
                else:
                    logger.error(f"Failed to update config blacklist: {response.status_code}")
        except Exception as e:
            logger.error(f"Error updating config blacklist: {e}")
    
    async def get_replacement_pairs(self, exchange: str, num_pairs: int, blacklisted_pairs: List[str]) -> List[str]:
        """Get replacement pairs when blacklisting occurs"""
        try:
            if not self.config:
                await self.load_config()
                
            replacement_config = self.config.get('pair_selector', {}).get('replacement_selection', {})
            if not replacement_config.get('enabled', False):
                return []
                
            # Use enhanced pair selector with stricter criteria
            from core.pair_selector import select_top_pairs_ccxt_enhanced
            
            exchange_config = self.config.get('exchanges', {}).get(exchange, {})
            base_currency = exchange_config.get('base_currency', 'USDC')
            
            # Get more pairs than needed to account for blacklisted ones
            extra_pairs = len(blacklisted_pairs) + 5
            
            result = await select_top_pairs_ccxt_enhanced(
                exchange, 
                base_currency, 
                num_pairs + extra_pairs,
                'spot',
                blacklisted_pairs,
                replacement_config
            )
            
            # Filter out blacklisted pairs and return requested number
            valid_pairs = [p for p in result['selected_pairs'] if p not in blacklisted_pairs]
            return valid_pairs[:num_pairs]
            
        except Exception as e:
            logger.error(f"Error getting replacement pairs for {exchange}: {e}")
            return []

# Global instance
blacklist_manager = DynamicBlacklistManager()