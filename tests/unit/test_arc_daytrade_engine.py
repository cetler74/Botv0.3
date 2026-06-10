"""Unit tests for ARC daytrade engine."""

from __future__ import annotations

import pandas as pd

from strategy.playbooks.arc_daytrade_engine import EngineParams, evaluate_arc_daytrade


def _daily(n: int = 5, box_high: float = 110, box_low: float = 95) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": [100] * n,
            "high": [box_high if i < n - 1 else box_high - 1 for i in range(n)],
            "low": [box_low if i < n - 1 else box_low + 1 for i in range(n)],
            "close": [102] * n,
            "volume": [1000] * n,
        },
        index=pd.date_range("2025-06-01", periods=n, freq="1d"),
    )


def test_hold_insufficient_daily():
    result = evaluate_arc_daytrade({"1d": _daily(2), "5m": _daily(40)}, EngineParams(min_candles_bias=5))
    assert result.signal == "hold"
    assert "insufficient" in result.invalidation_reason


def test_hold_blocked_regime():
    result = evaluate_arc_daytrade(
        {"1d": _daily(), "5m": _daily(40)},
        EngineParams(blocked_regimes=["low_volatility"]),
        market_regime="low_volatility",
    )
    assert result.signal == "hold"
    assert result.indicators.get("setup") == "arc_daytrade"


def test_area_levels_populated():
    entry_rows = []
    price = 96.0
    for i in range(40):
        entry_rows.append(
            {"open": price, "high": price + 0.5, "low": price - 0.5, "close": price, "volume": 100}
        )
        price += 0.02
    entry = pd.DataFrame(
        entry_rows,
        index=pd.date_range("2025-06-07", periods=40, freq="5min"),
    )
    result = evaluate_arc_daytrade({"1d": _daily(), "5m": entry}, EngineParams())
    assert result.indicators.get("box_high") is not None
    assert result.indicators.get("zone") in {"buy", "sell", "no_trade"}


def test_hold_pinball_blocked_in_sideways_regime():
    params = EngineParams(
        regime_blocked_geometries={"sideways": ["pinball"]},
        min_range_move_pct=0.05,
        volatile_min_range_move_pct=0.05,
    )
    entry_rows = []
    price = 96.0
    for i in range(40):
        entry_rows.append(
            {"open": price, "high": price + 0.5, "low": price - 0.5, "close": price, "volume": 100}
        )
        price += 0.02
    entry = pd.DataFrame(
        entry_rows,
        index=pd.date_range("2025-06-07", periods=40, freq="5min"),
    )
    result = evaluate_arc_daytrade(
        {"1d": _daily(), "5m": entry},
        params,
        market_regime="sideways",
    )
    if result.indicators.get("range_geometry_tag") == "pinball":
        assert result.signal == "hold"
        assert "geometry_pinball_blocked" in result.invalidation_reason


def test_hold_when_stop_pct_below_floor():
    params = EngineParams(min_stop_pct=0.01, min_range_move_pct=0.05, volatile_min_range_move_pct=0.05)
    entry_rows = []
    price = 96.0
    for i in range(40):
        entry_rows.append(
            {"open": price, "high": price + 0.5, "low": price - 0.5, "close": price, "volume": 100}
        )
        price += 0.02
    entry = pd.DataFrame(
        entry_rows,
        index=pd.date_range("2025-06-07", periods=40, freq="5min"),
    )
    result = evaluate_arc_daytrade({"1d": _daily(), "5m": entry}, params, market_regime="trending_up")
    if result.invalidation_reason == "candle_fail:stop_too_tight":
        assert result.signal == "hold"
        assert float(result.indicators.get("stop_pct") or 0) < 0.01
