"""Unit tests for Chandelier Stop indicator."""

from __future__ import annotations

import pandas as pd
import pytest

pandas_ta = pytest.importorskip("pandas_ta")

from strategy.indicators.chandelier_stop import compute_chandelier_stop


def _ohlcv(n: int = 30, base: float = 100.0) -> pd.DataFrame:
    rows = []
    price = base
    for i in range(n):
        o = price
        c = price + 0.5
        h = c + 1.0
        l = o - 0.5
        rows.append({"open": o, "high": h, "low": l, "close": c, "volume": 1000})
        price = c
    idx = pd.date_range("2025-01-01", periods=n, freq="4h")
    return pd.DataFrame(rows, index=idx)


def test_chandelier_long_stop_below_price():
    df = _ohlcv(30)
    stop = compute_chandelier_stop(df, len(df) - 1, "long", period=22, atr_mult=3.0)
    assert stop is not None
    assert stop < float(df["close"].iloc[-1])


def test_chandelier_short_stop_computed():
    rows = []
    price = 120.0
    for _ in range(30):
        o = price
        c = price - 0.5
        h = o + 0.1
        l = c - 0.5
        rows.append({"open": o, "high": h, "low": l, "close": c, "volume": 1000})
        price = c
    idx = pd.date_range("2025-01-01", periods=30, freq="4h")
    df = pd.DataFrame(rows, index=idx)
    stop = compute_chandelier_stop(df, len(df) - 1, "short", period=22, atr_mult=3.0)
    assert stop is not None
    assert stop > float(df["close"].iloc[-1])
