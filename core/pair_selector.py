import ccxt.async_support as ccxt_async
import logging
from datetime import datetime
import re
import httpx

logger = logging.getLogger(__name__)

async def select_top_pairs_ccxt(exchange_id, base_currency='USDC', num_pairs=10, market_type='spot'):
    # Load blacklisted pairs from config
    blacklisted_pairs = await _get_blacklisted_pairs()
    
    exchange_class = getattr(ccxt_async, exchange_id)
    exchange = exchange_class({'enableRateLimit': True})
    await exchange.load_markets()
    # Log market info for debugging
    market_count = len(exchange.markets)
    usdc_markets = [s for s, m in exchange.markets.items() if m.get('quote') == base_currency]
    logger.info(f"[CCXT PairSelector] {exchange_id} has {market_count} total markets, {len(usdc_markets)} with quote {base_currency}")
    logger.info(f"[CCXT PairSelector] Sample {base_currency} markets: {usdc_markets[:10]}")
    
    # Filter for active, correct quote, type, and not blacklisted
    pairs = []
    for symbol, market in exchange.markets.items():
        if not market.get('active'):
            logger.debug(f"[PairSelector] Excluded {symbol}: not active")
            continue
        if market.get('quote') != base_currency:
            logger.debug(f"[PairSelector] Excluded {symbol}: quote {market.get('quote')} != {base_currency}")
            continue
        if not (market.get('type') == market_type):
            logger.debug(f"[PairSelector] Excluded {symbol}: type {market.get('type')} != {market_type}")
            continue
        # PERFORMANCE FILTER: Exclude blacklisted pairs
        if symbol in blacklisted_pairs:
            logger.warning(f"[PairSelector] ⚠️  BLACKLISTED: {symbol} excluded due to poor performance")
            continue
        pairs.append(symbol)
    
    logger.info(f"[PairSelector] Filtered out {len(blacklisted_pairs)} blacklisted pairs: {blacklisted_pairs}")
    
    # Sort by 24h volume if available
    def get_volume(symbol):
        info = exchange.markets[symbol].get('info', {})
        # Try several possible volume keys
        for key in ['quoteVolume', 'turnover24h', 'volume', 'baseVolume']:
            v = info.get(key)
            if v is not None:
                try:
                    return float(v)
                except Exception:
                    continue
        return 0
    pairs = sorted(pairs, key=get_volume, reverse=True)
    await exchange.close()
    # Do not sanitize symbols: use the exact symbol as returned by the exchange
    sanitized_pairs = pairs[:num_pairs]
    logger.info(f"[CCXT PairSelector] Top {num_pairs} pairs for {exchange_id} {base_currency}: {sanitized_pairs}")
    return {
        "selected_pairs": sanitized_pairs,
        "timestamp": datetime.utcnow().isoformat()
    }

async def _get_blacklisted_pairs():
    """Fetch active blacklisted pairs (with cooling periods) from blacklist manager"""
    try:
        from core.dynamic_blacklist_manager import blacklist_manager
        blacklisted_pairs = await blacklist_manager.get_active_blacklist()
        logger.info(f"[PairSelector] Loaded {len(blacklisted_pairs)} active blacklisted pairs")
        return blacklisted_pairs
    except Exception as e:
        logger.error(f"[PairSelector] Error loading blacklisted pairs: {e}")
        # Fallback to static config
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get("http://config-service:8001/api/v1/config/all")
                if response.status_code == 200:
                    config = response.json()
                    blacklisted_pairs = config.get('pair_selector', {}).get('blacklisted_pairs', [])
                    return blacklisted_pairs
        except:
            pass
        return []

async def select_top_pairs_ccxt_enhanced(exchange_id, base_currency='USDC', num_pairs=10, market_type='spot', 
                                       exclude_pairs=None, replacement_config=None):
    """Enhanced pair selector with stricter criteria for replacements"""
    if exclude_pairs is None:
        exclude_pairs = []
        
    # Load blacklisted pairs
    blacklisted_pairs = await _get_blacklisted_pairs()
    all_excluded = set(blacklisted_pairs + exclude_pairs)
    
    exchange_class = getattr(ccxt_async, exchange_id)
    exchange = exchange_class({'enableRateLimit': True})
    await exchange.load_markets()
    
    # Get base criteria from config
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get("http://config-service:8001/api/v1/config/all")
        config = response.json() if response.status_code == 200 else {}
    
    selection_criteria = config.get('pair_selector', {}).get('selection_criteria', {})
    base_min_volume = selection_criteria.get('min_volume_24h', 1000000)
    base_min_market_cap = selection_criteria.get('min_market_cap', 100000000)
    
    # Apply replacement multipliers if provided
    if replacement_config:
        min_volume_requirement = base_min_volume * replacement_config.get('min_volume_24h_multiplier', 1.5)
        min_market_cap_requirement = base_min_market_cap * replacement_config.get('min_market_cap_multiplier', 1.2)
    else:
        min_volume_requirement = base_min_volume
        min_market_cap_requirement = base_min_market_cap
    
    logger.info(f"[EnhancedPairSelector] Enhanced criteria: min_volume={min_volume_requirement}, min_market_cap={min_market_cap_requirement}")
    
    # Filter pairs with enhanced criteria
    pairs = []
    for symbol, market in exchange.markets.items():
        if not market.get('active'):
            continue
        if market.get('quote') != base_currency:
            continue
        if not (market.get('type') == market_type):
            continue
        if symbol in all_excluded:
            logger.debug(f"[EnhancedPairSelector] Excluded {symbol}: blacklisted/excluded")
            continue
            
        # Enhanced volume filtering
        volume = get_volume_enhanced(symbol, exchange, min_volume_requirement)
        if volume < min_volume_requirement:
            continue
            
        pairs.append((symbol, volume))
    
    # Sort by volume and select top pairs
    pairs = sorted(pairs, key=lambda x: x[1], reverse=True)
    selected_pairs = [pair[0] for pair in pairs[:num_pairs]]
    
    await exchange.close()
    
    logger.info(f"[EnhancedPairSelector] Selected {len(selected_pairs)} enhanced pairs for {exchange_id}: {selected_pairs}")
    return {
        "selected_pairs": selected_pairs,
        "timestamp": datetime.utcnow().isoformat(),
        "enhanced": True,
        "excluded_count": len(all_excluded)
    }

def get_volume_enhanced(symbol, exchange, min_threshold):
    """Enhanced volume calculation with stricter filtering"""
    info = exchange.markets[symbol].get('info', {})
    # Try several possible volume keys
    for key in ['quoteVolume', 'turnover24h', 'volume', 'baseVolume']:
        v = info.get(key)
        if v is not None:
            try:
                volume = float(v)
                return volume if volume >= min_threshold else 0
            except Exception:
                continue
    return 0

class PairSelector:
    def __init__(self, base_pair="USDC", num_pairs=15, exchange_id='cryptocom'):
        self.base_pair = base_pair
        self.num_pairs = num_pairs
        self.exchange_id = exchange_id
    async def select_top_pairs(self):
        return await select_top_pairs_ccxt(self.exchange_id, self.base_pair, self.num_pairs)

class BinancePairSelector:
    def __init__(self, base_pair="USDC", num_pairs=15):
        self.base_pair = base_pair
        self.num_pairs = num_pairs
        self.exchange_id = 'binance'
    async def select_top_pairs(self):
        return await select_top_pairs_ccxt(self.exchange_id, self.base_pair, self.num_pairs)

class BybitPairSelector:
    def __init__(self, base_pair="USDC", num_pairs=15):
        self.base_pair = base_pair
        self.num_pairs = num_pairs
        self.exchange_id = 'bybit'
    async def select_top_pairs(self):
        return await select_top_pairs_ccxt(self.exchange_id, self.base_pair, self.num_pairs) 