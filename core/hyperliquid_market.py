"""
Hyperliquid public market data (candleSnapshot).

Shared by exchange-service and unit tests.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

HYPERLIQUID_INFO_URL = "https://api.hyperliquid.xyz/info"

# Map common aliases to HL interval strings.
TIMEFRAME_TO_INTERVAL: Dict[str, str] = {
    "1m": "1m",
    "3m": "3m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "2h": "2h",
    "4h": "4h",
    "8h": "8h",
    "12h": "12h",
    "1d": "1d",
    "3d": "3d",
    "1w": "1w",
    "1M": "1M",
}

INTERVAL_MS: Dict[str, int] = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
    "3d": 259_200_000,
    "1w": 604_800_000,
    "1M": 2_592_000_000,
}


@dataclass
class _CandleCacheEntry:
    rows: List[Dict[str, Any]]
    fetched_at: float


class HyperliquidCandleCache:
    """Coalesce candle requests, pace upstream calls, and serve stale on 429.

    Strategy scans request the same coin/timeframe repeatedly and concurrently.
    Hyperliquid's public info endpoint is shared by every request type, so an
    unpaced OHLCV fan-out can exhaust the account/IP weight budget. This cache
    keeps the latest complete response and serializes refreshes per key.
    """

    def __init__(
        self,
        *,
        max_concurrency: int = 2,
        min_request_interval_seconds: float = 0.30,
    ) -> None:
        self._entries: Dict[tuple[str, str, int], _CandleCacheEntry] = {}
        self._locks: Dict[tuple[str, str, int], asyncio.Lock] = {}
        self._semaphore = asyncio.Semaphore(max(1, int(max_concurrency)))
        self._rate_lock = asyncio.Lock()
        self._last_request_at = 0.0
        self._min_request_interval = max(0.0, float(min_request_interval_seconds))

    @staticmethod
    def ttl_seconds(timeframe: str) -> float:
        interval = normalize_timeframe(timeframe)
        return {
            "1m": 45.0,
            "3m": 90.0,
            "5m": 120.0,
            "15m": 240.0,
            "30m": 300.0,
            "1h": 600.0,
            "2h": 900.0,
            "4h": 1200.0,
            "8h": 1800.0,
            "12h": 1800.0,
            "1d": 1800.0,
        }.get(interval, 300.0)

    async def _pace(self) -> None:
        async with self._rate_lock:
            now = time.monotonic()
            delay = self._min_request_interval - (now - self._last_request_at)
            if delay > 0:
                await asyncio.sleep(delay)
            self._last_request_at = time.monotonic()

    async def get(
        self,
        coin: str,
        timeframe: str,
        limit: int,
        *,
        fetcher=None,
    ) -> List[Dict[str, Any]]:
        # The default is resolved at call time because the fetch function is
        # declared below this class in the module.
        if fetcher is None:
            fetcher = fetch_hyperliquid_candles
        key = (
            normalize_hyperliquid_coin(coin),
            normalize_timeframe(timeframe),
            max(10, min(int(limit or 100), 5000)),
        )
        now = time.monotonic()
        cached = self._entries.get(key)
        if cached and now - cached.fetched_at <= self.ttl_seconds(key[1]):
            return [dict(row) for row in cached.rows]

        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            now = time.monotonic()
            cached = self._entries.get(key)
            if cached and now - cached.fetched_at <= self.ttl_seconds(key[1]):
                return [dict(row) for row in cached.rows]

            last_error: Optional[Exception] = None
            for attempt in range(3):
                try:
                    async with self._semaphore:
                        await self._pace()
                        rows = await fetcher(key[0], key[1], key[2])
                    if rows:
                        self._entries[key] = _CandleCacheEntry(
                            rows=[dict(row) for row in rows],
                            fetched_at=time.monotonic(),
                        )
                    return rows
                except httpx.HTTPStatusError as exc:
                    last_error = exc
                    if exc.response.status_code != 429:
                        break
                    retry_after = exc.response.headers.get("Retry-After")
                    try:
                        delay = float(retry_after) if retry_after else 0.75 * (attempt + 1)
                    except (TypeError, ValueError):
                        delay = 0.75 * (attempt + 1)
                    await asyncio.sleep(max(0.25, min(delay, 5.0)))
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    last_error = exc
                    await asyncio.sleep(0.5 * (attempt + 1))

            # Stale candles are safer than turning an entire strategy scan into
            # a 500 response. Closed-bar strategies remain deterministic and the
            # caller can refresh on the next cache window.
            cached = self._entries.get(key)
            if cached:
                return [dict(row) for row in cached.rows]
            if last_error:
                raise last_error
            return []


def normalize_hyperliquid_coin(symbol: str) -> str:
    """BTC, BTCUSD, BTC/USDC -> BTC."""
    raw = str(symbol or "").strip()
    if "/" in raw:
        return raw.split("/", 1)[0]
    if ":" in raw:
        dex, base = raw.split(":", 1)
        raw = f"{dex.lower()}:{base.upper()}"
    else:
        raw = raw.upper()
    for suffix in ("USDC", "USDT", "USD", "-PERP"):
        if raw.endswith(suffix):
            return raw[: -len(suffix)]
    return raw


def normalize_timeframe(timeframe: str) -> str:
    tf = str(timeframe or "1h").strip()
    return TIMEFRAME_TO_INTERVAL.get(tf, tf)


async def fetch_hyperliquid_candles(
    coin: str,
    timeframe: str = "1h",
    limit: int = 100,
    *,
    client: Optional[httpx.AsyncClient] = None,
    info_url: str = HYPERLIQUID_INFO_URL,
) -> List[Dict[str, Any]]:
    """
    Fetch OHLCV rows from Hyperliquid candleSnapshot.

    Returns list of dicts: timestamp (ms), open, high, low, close, volume (floats).
    """
    symbol = normalize_hyperliquid_coin(coin)
    interval = normalize_timeframe(timeframe)
    interval_ms = INTERVAL_MS.get(interval, 3_600_000)
    lim = max(10, min(int(limit or 100), 5000))
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - (lim + 2) * interval_ms

    payload = {
        "type": "candleSnapshot",
        "req": {
            "coin": symbol,
            "interval": interval,
            "startTime": start_ms,
            "endTime": end_ms,
        },
    }

    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=30.0)
    try:
        resp = await client.post(info_url, json=payload)
        resp.raise_for_status()
        raw = resp.json()
    finally:
        if own_client and client is not None:
            await client.aclose()

    if not isinstance(raw, list):
        return []

    rows: List[Dict[str, Any]] = []
    for candle in raw:
        if not isinstance(candle, dict):
            continue
        try:
            ts = int(candle.get("t") or candle.get("T") or 0)
            rows.append(
                {
                    "timestamp": ts,
                    "open": float(candle.get("o", 0)),
                    "high": float(candle.get("h", 0)),
                    "low": float(candle.get("l", 0)),
                    "close": float(candle.get("c", 0)),
                    "volume": float(candle.get("v", 0)),
                }
            )
        except (TypeError, ValueError):
            continue

    rows.sort(key=lambda r: r["timestamp"])
    if len(rows) > lim:
        rows = rows[-lim:]
    return rows


def candles_to_ohlcv_columns(candles: List[Dict[str, Any]]) -> Dict[str, List]:
    """Exchange-service JSON shape."""
    return {
        "timestamp": [c["timestamp"] for c in candles],
        "open": [c["open"] for c in candles],
        "high": [c["high"] for c in candles],
        "low": [c["low"] for c in candles],
        "close": [c["close"] for c in candles],
        "volume": [c["volume"] for c in candles],
    }
