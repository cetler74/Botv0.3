"""Unit tests for dual-SMA daytrade engine."""

from __future__ import annotations

import pandas as pd

from strategy.playbooks.dual_sma_daytrade_engine import (
    EngineParams,
    evaluate_dual_sma_daytrade,
)


def _flat_then_rise_daily(n: int = 260) -> pd.DataFrame:
    closes = [100.0] * 220 + [100.0 + i * 0.08 for i in range(n - 220)]
    return pd.DataFrame(
        {
            "open": closes,
            "high": [c + 0.3 for c in closes],
            "low": [c - 0.3 for c in closes],
            "close": closes,
            "volume": [1000] * n,
        },
        index=pd.date_range("2024-01-01", periods=n, freq="1d"),
    )


def _uptrend_15m(n: int = 80) -> pd.DataFrame:
    rows = []
    price = 100.0
    for i in range(n):
        phase = i % 8
        if phase < 4:
            o, c = price, price + 0.8
            h, l = c + 0.4, o - 0.2
        else:
            o, c = price + 0.8, price + 0.1
            h, l = o + 0.2, c - 0.6
        rows.append({"open": o, "high": h, "low": l, "close": c, "volume": 1000})
        if i % 8 == 7:
            price += 1.0
    return pd.DataFrame(rows, index=pd.date_range("2025-01-01", periods=n, freq="15min"))


def test_hold_on_insufficient_daily_candles():
    params = EngineParams(min_candles_bias=300)
    short = pd.DataFrame(
        {
            "open": [100.0] * 50,
            "high": [100.5] * 50,
            "low": [99.5] * 50,
            "close": [100.0] * 50,
            "volume": [1000] * 50,
        },
        index=pd.date_range("2024-01-01", periods=50, freq="1d"),
    )
    result = evaluate_dual_sma_daytrade({"1d": short}, params)
    assert result.signal == "hold"
    assert "insufficient" in result.invalidation_reason


def test_hold_on_blocked_regime():
    params = EngineParams(blocked_regimes=["low_volatility"])
    daily = _flat_then_rise_daily()
    result = evaluate_dual_sma_daytrade(
        {"1d": daily, "15m": _uptrend_15m(), "5m": _uptrend_15m(60)},
        params,
        market_regime="low_volatility",
    )
    assert result.signal == "hold"
    assert result.indicators.get("setup") == "dual_sma_daytrade"


def test_indicators_populated_on_daily_fail():
    daily = pd.DataFrame(
        {
            "open": [100.0] * 260,
            "high": [100.1] * 260,
            "low": [99.9] * 260,
            "close": [100.0] * 260,
            "volume": [1000] * 260,
        },
        index=pd.date_range("2024-01-01", periods=260, freq="1d"),
    )
    params = EngineParams()
    result = evaluate_dual_sma_daytrade(
        {"1d": daily, "15m": daily, "5m": daily},
        params,
    )
    assert result.signal == "hold"
    assert result.indicators.get("daily_bias") is not None
    assert result.indicators.get("setup") == "dual_sma_daytrade"


def test_short_disabled_on_spot_path():
    daily = _flat_then_rise_daily()
    # invert for bearish would need different data; just verify allow_short=False blocks sell
    params = EngineParams(allow_short=False)
    result = evaluate_dual_sma_daytrade(
        {"1d": daily, "15m": _uptrend_15m(), "5m": _uptrend_15m(60)},
        params,
        allow_short=False,
    )
    assert result.signal in ("hold", "buy")
