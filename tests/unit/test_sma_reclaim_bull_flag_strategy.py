"""Spot wrapper for SMA Reclaim Bull Flag."""

import pandas as pd
import pytest

from strategy.sma_reclaim_bull_flag_strategy import SmaReclaimBullFlagStrategy


def _ohlcv(n: int = 260) -> pd.DataFrame:
    import numpy as np

    idx = pd.date_range("2024-06-01", periods=n, freq="5min", tz="UTC")
    close = 100 * (1 + np.linspace(0, 0.02, n))
    return pd.DataFrame(
        {
            "open": close * 0.999,
            "high": close * 1.002,
            "low": close * 0.998,
            "close": close,
            "volume": np.full(n, 1000.0),
        },
        index=idx,
    )


@pytest.mark.asyncio
async def test_spot_strategy_returns_buy_or_hold():
    strat = SmaReclaimBullFlagStrategy(
        config={
            "parameters": {
                "entry_timeframe": "5m",
                "execution_timeframe": "1m",
                "session_filter": {"enabled": False},
            }
        },
        exchange=None,
        database=None,
    )
    await strat.initialize("BTC/USDC")
    strat.state.market_regime = "trending_up"
    md = {"5m": _ohlcv(), "1m": _ohlcv()}
    signal, conf, strength = await strat.generate_signal(md, pair="BTC/USDC")
    assert signal in ("buy", "hold")
    assert 0 <= conf <= 1
    assert "invalidation_reason" in strat.state.indicators


@pytest.mark.asyncio
async def test_spot_hold_populates_invalidation():
    strat = SmaReclaimBullFlagStrategy(
        config={"parameters": {"session_filter": {"enabled": False}}},
        exchange=None,
        database=None,
    )
    await strat.initialize("ETH")
    signal, _, _ = await strat.generate_signal({"5m": _ohlcv(30)}, pair="ETH")
    assert signal == "hold"
    assert strat.state.indicators.get("invalidation_reason")
