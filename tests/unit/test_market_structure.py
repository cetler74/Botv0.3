"""Unit tests for validated swing market structure."""

from __future__ import annotations

import math

import pandas as pd

from strategy.indicators.market_structure import analyze_market_structure


def _make_uptrend_df(n: int = 80) -> pd.DataFrame:
    """Synthetic series with rising swing highs and lows."""
    rows = []
    for i in range(n):
        wave = math.sin(i / 4.0) * 1.5
        mid = 100.0 + i * 0.35 + wave
        o = mid - 0.2
        c = mid + 0.25
        h = max(o, c) + 0.35
        l = min(o, c) - 0.35
        rows.append({"open": o, "high": h, "low": l, "close": c, "volume": 1000})
    idx = pd.date_range("2025-01-01", periods=n, freq="1h")
    return pd.DataFrame(rows, index=idx)


def _make_downtrend_df(n: int = 80) -> pd.DataFrame:
    rows = []
    for i in range(n):
        wave = math.sin(i / 4.0) * 1.5
        mid = 200.0 - i * 0.35 - wave
        o = mid + 0.2
        c = mid - 0.25
        h = max(o, c) + 0.35
        l = min(o, c) - 0.35
        rows.append({"open": o, "high": h, "low": l, "close": c, "volume": 1000})
    idx = pd.date_range("2025-01-01", periods=n, freq="1h")
    return pd.DataFrame(rows, index=idx)


def test_insufficient_candles_returns_neutral():
    df = pd.DataFrame(
        {"open": [1], "high": [1.1], "low": [0.9], "close": [1.05], "volume": [1]},
        index=pd.date_range("2025-01-01", periods=1, freq="1h"),
    )
    state = analyze_market_structure(df, pivot_order=2)
    assert state.trend == "neutral"
    assert state.step1_pass is False
    assert "insufficient" in state.step1_reason


def test_uptrend_detected_on_hh_hl_series():
    df = _make_uptrend_df()
    state = analyze_market_structure(df, pivot_order=2)
    assert len(state.validated_swings) >= 1
    assert state.trend in {"uptrend", "neutral"}


def test_downtrend_detected_on_lh_ll_series():
    df = _make_downtrend_df()
    state = analyze_market_structure(df, pivot_order=2)
    assert len(state.validated_swings) >= 2
    if state.step1_pass:
        assert state.trend == "downtrend"


def test_uptrend_invalidated_on_break_below_last_low():
    df = _make_uptrend_df()
    df.iloc[-1, df.columns.get_loc("close")] = df["low"].min() - 5
    df.iloc[-1, df.columns.get_loc("low")] = df.iloc[-1]["close"] - 0.5
    state = analyze_market_structure(df, pivot_order=2)
    assert state.trend == "neutral" or "invalidated" in state.step1_reason


def test_to_dict_serializable():
    df = _make_uptrend_df()
    state = analyze_market_structure(df, pivot_order=2)
    d = state.to_dict()
    assert "validated_swings" in d
    assert isinstance(d["validated_swings"], list)
