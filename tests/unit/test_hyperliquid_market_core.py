import pytest

from core.hyperliquid_market import (
    candles_to_ohlcv_columns,
    normalize_hyperliquid_coin,
    normalize_timeframe,
)


def test_normalize_coin():
    assert normalize_hyperliquid_coin("ETH/USDC") == "ETH"
    assert normalize_hyperliquid_coin("BTCUSD") == "BTC"


def test_normalize_timeframe():
    assert normalize_timeframe("1h") == "1h"


def test_candles_to_columns():
    cols = candles_to_ohlcv_columns(
        [
            {
                "timestamp": 1,
                "open": 1.0,
                "high": 2.0,
                "low": 0.5,
                "close": 1.5,
                "volume": 10.0,
            }
        ]
    )
    assert cols["close"] == [1.5]


@pytest.mark.asyncio
async def test_fetch_candles_live_optional():
    """Smoke test against public API when network available."""
    from core.hyperliquid_market import fetch_hyperliquid_candles

    try:
        rows = await fetch_hyperliquid_candles("ETH", "1h", 20)
    except Exception:
        pytest.skip("Hyperliquid API unreachable")
    if not rows:
        pytest.skip("empty candle response")
    assert rows[-1]["close"] > 0
