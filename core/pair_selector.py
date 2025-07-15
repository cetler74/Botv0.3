import aiohttp
import math
import numpy as np
from datetime import datetime
import logging

class PairSelector:
    BASE_URL = "https://api.crypto.com/exchange/v1"

    def __init__(self, base_pair="USDC", num_pairs=15):
        self.base_pair = base_pair
        self.num_pairs = num_pairs
        self.logger = logging.getLogger(__name__)

    async def fetch_json(self, session, endpoint, params=None):
        url = f"{self.BASE_URL}/{endpoint}"
        async with session.get(url, params=params) as resp:
            resp.raise_for_status()
            data = await resp.json()
            self.logger.debug(f"Fetched from {url} with params {params}: {data}")
            return data

    async def get_perpetual_instruments(self, session):
        data = await self.fetch_json(session, "public/get-instruments")
        self.logger.info(f"Crypto.com get-instruments response: {data}")
        if "result" not in data or "instruments" not in data["result"]:
            self.logger.error(f"Missing 'instruments' in get-instruments response: {data}")
            return []
        return [
            inst for inst in data["result"]["instruments"]
            if inst["inst_type"] == "PERPETUAL_SWAP" and inst["quote_currency"] == self.base_pair
        ]

    async def get_ticker(self, session, symbol):
        data = await self.fetch_json(session, "public/get-tickers", {"instrument_name": symbol})
        for ticker in data["result"]["data"]:
            if ticker["i"] == symbol:
                return ticker
        return None

    async def get_candlesticks(self, session, symbol, timeframe="1h", count=24):
        data = await self.fetch_json(session, "public/get-candlestick", {
            "instrument_name": symbol,
            "timeframe": timeframe,
            "count": count
        })
        return data["result"]["data"]

    def compute_volatility(self, candles):
        closes = [float(c[4]) for c in candles]  # c[4] is close price
        returns = [math.log(closes[i] / closes[i-1]) for i in range(1, len(closes))]
        return np.std(returns) * math.sqrt(len(returns)) if returns else 0

    async def select_top_pairs(self):
        async with aiohttp.ClientSession() as session:
            instruments = await self.get_perpetual_instruments(session)
            pair_data = []
            for inst in instruments:
                symbol = inst["instrument_name"]
                ticker = await self.get_ticker(session, symbol)
                candles = await self.get_candlesticks(session, symbol)
                if not ticker or not candles or len(candles) < 2:
                    continue
                volume_usd = float(ticker.get("vv", 0))
                volatility = self.compute_volatility(candles)
                pair_data.append({
                    "symbol": symbol,
                    "volume_usd": volume_usd,
                    "volatility": volatility
                })

            top_liquidity = sorted(pair_data, key=lambda x: x["volume_usd"], reverse=True)[:self.num_pairs]
            top_volatility = sorted(pair_data, key=lambda x: x["volatility"], reverse=True)[:self.num_pairs]
            selected = {p["symbol"] for p in top_liquidity + top_volatility}
            selected = list(selected)[:self.num_pairs]

            return {
                "selected_pairs": selected,
                "timestamp": datetime.utcnow().isoformat()
            }

class BinancePairSelector:
    BASE_URL = "https://fapi.binance.com"

    def __init__(self, base_pair="USDT", num_pairs=15):
        self.base_pair = base_pair
        self.num_pairs = num_pairs

    async def fetch_json(self, session, endpoint, params=None):
        url = f"{self.BASE_URL}{endpoint}"
        async with session.get(url, params=params) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def get_perpetual_symbols(self, session):
        data = await self.fetch_json(session, "/fapi/v1/exchangeInfo")
        return [
            s["symbol"] for s in data["symbols"]
            if s["contractType"] == "PERPETUAL" and s["quoteAsset"] == self.base_pair
        ]

    async def get_ticker_map(self, session):
        data = await self.fetch_json(session, "/fapi/v1/ticker/24hr")
        return {d["symbol"]: d for d in data}

    async def get_candlesticks(self, session, symbol, interval="1h", limit=24):
        data = await self.fetch_json(session, "/fapi/v1/klines", {
            "symbol": symbol,
            "interval": interval,
            "limit": limit
        })
        return data

    def compute_volatility(self, candles):
        closes = [float(c[4]) for c in candles]
        returns = [math.log(closes[i] / closes[i-1]) for i in range(1, len(closes))]
        return np.std(returns) * math.sqrt(len(returns)) if returns else 0

    async def select_top_pairs(self):
        async with aiohttp.ClientSession() as session:
            symbols = await self.get_perpetual_symbols(session)
            ticker_map = await self.get_ticker_map(session)
            pair_data = []
            for symbol in symbols:
                ticker = ticker_map.get(symbol)
                candles = await self.get_candlesticks(session, symbol)
                if not ticker or not candles or len(candles) < 2:
                    continue
                volume_usd = float(ticker.get("quoteVolume", 0))
                volatility = self.compute_volatility(candles)
                pair_data.append({
                    "symbol": symbol,
                    "volume_usd": volume_usd,
                    "volatility": volatility
                })

            top_liquidity = sorted(pair_data, key=lambda x: x["volume_usd"], reverse=True)[:self.num_pairs]
            top_volatility = sorted(pair_data, key=lambda x: x["volatility"], reverse=True)[:self.num_pairs]
            selected = {p["symbol"] for p in top_liquidity + top_volatility}
            selected = list(selected)[:self.num_pairs]

            return {
                "selected_pairs": selected,
                "timestamp": datetime.utcnow().isoformat()
            }

class BybitPairSelector:
    BASE_URL = "https://api.bybit.com"

    def __init__(self, base_pair="USDT", num_pairs=15):
        self.base_pair = base_pair
        self.num_pairs = num_pairs

    async def fetch_json(self, session, endpoint, params=None):
        url = f"{self.BASE_URL}{endpoint}"
        async with session.get(url, params=params) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def get_perpetual_symbols(self, session):
        data = await self.fetch_json(session, "/v5/market/instruments-info", {"category": "linear"})
        return [
            s["symbol"] for s in data["result"]["list"]
            if s["quoteCoin"] == self.base_pair and s["contractType"] == "LinearPerpetual"
        ]

    async def get_ticker_map(self, session):
        data = await self.fetch_json(session, "/v5/market/tickers", {"category": "linear"})
        return {d["symbol"]: d for d in data["result"]["list"]}

    async def get_candlesticks(self, session, symbol, interval="60", limit=24):
        data = await self.fetch_json(session, "/v5/market/kline", {
            "category": "linear",
            "symbol": symbol,
            "interval": interval,
            "limit": limit
        })
        return data["result"]["list"]

    def compute_volatility(self, candles):
        closes = [float(c[4]) for c in candles]
        returns = [math.log(closes[i] / closes[i-1]) for i in range(1, len(closes))]
        return np.std(returns) * math.sqrt(len(returns)) if returns else 0

    async def select_top_pairs(self):
        async with aiohttp.ClientSession() as session:
            symbols = await self.get_perpetual_symbols(session)
            ticker_map = await self.get_ticker_map(session)
            pair_data = []
            for symbol in symbols:
                ticker = ticker_map.get(symbol)
                candles = await self.get_candlesticks(session, symbol)
                if not ticker or not candles or len(candles) < 2:
                    continue
                volume_usd = float(ticker.get("turnover24h", 0))
                volatility = self.compute_volatility(candles)
                pair_data.append({
                    "symbol": symbol,
                    "volume_usd": volume_usd,
                    "volatility": volatility
                })

            top_liquidity = sorted(pair_data, key=lambda x: x["volume_usd"], reverse=True)[:self.num_pairs]
            top_volatility = sorted(pair_data, key=lambda x: x["volatility"], reverse=True)[:self.num_pairs]
            selected = {p["symbol"] for p in top_liquidity + top_volatility}
            selected = list(selected)[:self.num_pairs]

            return {
                "selected_pairs": selected,
                "timestamp": datetime.utcnow().isoformat()
            } 