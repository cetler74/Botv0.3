"""
Hyperliquid public market data (candleSnapshot).

Shared by exchange-service and unit tests.
"""

from __future__ import annotations

import time
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
