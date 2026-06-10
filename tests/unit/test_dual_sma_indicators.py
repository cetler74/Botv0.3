"""Unit tests for dual-SMA indicator helpers."""

from __future__ import annotations

import pandas as pd
import pytest

from strategy.indicators.dual_sma import (
    classify_slope,
    compute_dual_sma,
    daily_gap_vs_sma200,
    detect_sma20_retrace,
    extension_from_sma20_pct,
    is_sma_flat,
    sma_slope,
)


def _trend_df(n: int, start: float, step: float) -> pd.DataFrame:
    closes = [start + i * step for i in range(n)]
    return pd.DataFrame(
        {
            "open": closes,
            "high": [c + 0.5 for c in closes],
            "low": [c - 0.5 for c in closes],
            "close": closes,
            "volume": [1000] * n,
        },
        index=pd.date_range("2025-01-01", periods=n, freq="1d"),
    )


def test_classify_slope_directions():
    assert classify_slope(0.01) == "rising"
    assert classify_slope(-0.01) == "falling"
    assert classify_slope(0.0001) == "flat"


def test_compute_dual_sma_snapshot():
    df = _trend_df(260, 100.0, 0.2)
    snap = compute_dual_sma(df)
    assert snap is not None
    assert snap.sma20 > 0
    assert snap.sma200 > 0
    assert snap.price_vs_sma20_pct != 0


def test_extension_from_sma20():
    assert extension_from_sma20_pct(105.0, 100.0) == pytest.approx(5.0 / 105.0)


def test_daily_gap_vs_sma200():
    df = _trend_df(220, 100.0, 0.1)
    gap = daily_gap_vs_sma200(df)
    assert isinstance(gap, float)


def test_detect_retrace_long_on_rising_series():
    df = _trend_df(80, 100.0, 0.15)
    # Pull last bar close near sma20
    df.iloc[-1, df.columns.get_loc("close")] = float(df["close"].rolling(20).mean().iloc[-1])
    ok, reason = detect_sma20_retrace(df, "long", lookback_bars=3)
    assert isinstance(ok, bool)
    assert reason


def test_is_sma_flat_on_flat_series():
    flat = pd.Series([100.0] * 30)
    assert is_sma_flat(flat, max_slope_pct=0.001) is True

