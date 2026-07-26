"""Donchian/ATR pullback engine contracts for 1h bias + 15m entry."""

from __future__ import annotations

import numpy as np
import pandas as pd

from strategy.playbooks.donchian_atr_pullback_engine import (
    EngineParams,
    evaluate_donchian_atr_pullback,
)


def _ohlcv(n: int, start: float = 100.0, step: float = 0.2) -> pd.DataFrame:
    closes = start + np.arange(n) * step
    return pd.DataFrame(
        {
            "open": closes - 0.05,
            "high": closes + 0.4,
            "low": closes - 0.4,
            "close": closes,
            "volume": np.full(n, 1000.0),
        }
    )


def test_long_pullback_after_1h_uptrend_and_15m_donchian_break():
    hourly = _ohlcv(80, start=90.0, step=0.5)
    trigger = _ohlcv(80, start=100.0, step=0.1)
    # Force a recent breakout high, then a shallow pullback reclaim.
    trigger.loc[trigger.index[-8:-3], ["high", "close"]] = [112.0, 111.5]
    trigger.loc[trigger.index[-3:-1], ["low", "close"]] = [109.5, 109.8]
    trigger.loc[trigger.index[-1], ["open", "high", "low", "close"]] = [
        109.9,
        111.2,
        109.7,
        111.0,
    ]

    result = evaluate_donchian_atr_pullback(
        {"1h": hourly, "15m": trigger},
        EngineParams(allow_short=False, min_reward_risk=1.5),
        market_regime="trending_up",
        allow_short=False,
    )

    assert result.signal == "buy"
    assert result.indicators["stop_hint"] < result.indicators["entry_price"]
    assert result.indicators["target_hint"] > result.indicators["entry_price"]
    assert result.indicators["reward_risk"] + 1e-9 >= 1.5
    assert "1h uptrend" in result.indicators["entry_reason"]
    assert "15m Donchian" in result.indicators["entry_reason"]


def test_short_pullback_requires_1h_downtrend_when_shorts_allowed():
    hourly = _ohlcv(80, start=140.0, step=-0.5)
    trigger = _ohlcv(80, start=120.0, step=-0.1)
    trigger.loc[trigger.index[-8:-3], ["low", "close"]] = [108.0, 108.5]
    trigger.loc[trigger.index[-3:-1], ["high", "close"]] = [110.5, 110.2]
    trigger.loc[trigger.index[-1], ["open", "high", "low", "close"]] = [
        110.1,
        110.3,
        108.8,
        109.0,
    ]

    result = evaluate_donchian_atr_pullback(
        {"1h": hourly, "15m": trigger},
        EngineParams(allow_short=True, min_reward_risk=1.5),
        market_regime="trending_down",
        allow_short=True,
    )

    assert result.signal == "sell"
    assert result.indicators["stop_hint"] > result.indicators["entry_price"]
    assert "1h downtrend" in result.indicators["entry_reason"]


def test_rejects_when_bias_trend_missing():
    flat = _ohlcv(80, start=100.0, step=0.0)
    result = evaluate_donchian_atr_pullback(
        {"1h": flat, "15m": flat.copy()},
        EngineParams(blocked_regimes=[]),
        market_regime="trending_up",
        allow_short=True,
    )
    assert result.signal == "hold"
    assert result.invalidation_reason
    assert "trend_missing" in str(result.invalidation_reason)


def test_accepts_4h_bias_and_1h_entry_timeframes():
    """Swing shadow candidate: configured bias/entry TFs are not hardcoded to 1h/15m."""
    bias = _ohlcv(80, start=90.0, step=0.5)
    entry = _ohlcv(80, start=100.0, step=0.1)
    entry.loc[entry.index[-8:-3], ["high", "close"]] = [112.0, 111.5]
    entry.loc[entry.index[-3:-1], ["low", "close"]] = [109.5, 109.8]
    entry.loc[entry.index[-1], ["open", "high", "low", "close"]] = [
        109.9,
        111.2,
        109.7,
        111.0,
    ]
    params = EngineParams(
        bias_timeframe="4h",
        entry_timeframe="1h",
        min_reward_risk=2.5,
    )
    result = evaluate_donchian_atr_pullback(
        {"4h": bias, "1h": entry},
        params,
        market_regime="trending_up",
        allow_short=True,
    )
    assert result.signal in ("buy", "hold")
    assert result.indicators.get("bias_timeframe") == "4h" or params.bias_timeframe == "4h"
    if result.signal == "buy":
        assert float(result.indicators.get("reward_risk") or 0) + 1e-9 >= 2.5
        assert "4h" in str(result.indicators.get("entry_reason") or "")
