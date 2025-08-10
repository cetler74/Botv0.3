import ccxt.async_support as ccxt_async
import logging
from datetime import datetime
import re

logger = logging.getLogger(__name__)

async def select_top_pairs_ccxt(exchange_id, base_currency='USDC', num_pairs=10, market_type='spot'):
    exchange_class = getattr(ccxt_async, exchange_id)
    exchange = exchange_class({'enableRateLimit': True})
    await exchange.load_markets()
    # Log market info for debugging
    market_count = len(exchange.markets)
    usdc_markets = [s for s, m in exchange.markets.items() if m.get('quote') == base_currency]
    logger.info(f"[CCXT PairSelector] {exchange_id} has {market_count} total markets, {len(usdc_markets)} with quote {base_currency}")
    logger.info(f"[CCXT PairSelector] Sample {base_currency} markets: {usdc_markets[:10]}")
    
    # Filter for active, correct quote, and type
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
        pairs.append(symbol)
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