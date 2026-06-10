"""Unit tests for ARC candle patterns."""

from __future__ import annotations

import pandas as pd

from strategy.indicators.arc_candles import detect_john_wick_entry, is_hammer


def test_hammer_detection():
    row = pd.Series({"open": 100, "high": 101, "low": 95, "close": 100.5})
    assert is_hammer(row, min_wick_body_ratio=2.0) is True


def test_detect_john_wick_entry_long_trigger():
    rows = []
    rows.append({"open": 100, "high": 100.5, "low": 99.5, "close": 100.2})
    rows.append({"open": 100, "high": 101, "low": 94, "close": 100.5})
    rows.append({"open": 100.5, "high": 102, "low": 100, "close": 101.5})
    df = pd.DataFrame(rows, index=pd.date_range("2025-06-07 10:00", periods=3, freq="5min"))
    sig = detect_john_wick_entry(df, "buy", hammer_min_wick_body_ratio=2.0, lookback_bars=5)
    assert sig is not None
    assert sig.entry_triggered is True
