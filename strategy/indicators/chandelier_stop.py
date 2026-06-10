"""Chandelier Exit / Stop — ATR-based trailing stop levels."""

from __future__ import annotations

from typing import Literal, Optional

import pandas as pd

try:
    import pandas_ta as ta
except ImportError:  # pragma: no cover
    ta = None


def compute_chandelier_stop(
    df: pd.DataFrame,
    idx: int,
    side: Literal["long", "short"],
    *,
    period: int = 22,
    atr_mult: float = 3.0,
) -> Optional[float]:
    """
    Chandelier Stop at bar `idx` (pipCharlie / StockCharts defaults: period=22, mult=3).

    Long SL  = highest_high(period) - ATR(period) * mult
    Short SL = lowest_low(period)  + ATR(period) * mult
    """
    if df is None or df.empty or ta is None:
        return None
    if idx < 0 or idx >= len(df):
        return None
    if len(df) < period:
        return None

    atr_series = ta.atr(df["high"], df["low"], df["close"], length=period)
    if atr_series is None or pd.isna(atr_series.iloc[idx]):
        return None
    atr_val = float(atr_series.iloc[idx])

    start = max(0, idx - period + 1)
    window = df.iloc[start : idx + 1]

    if side == "long":
        hh = float(window["high"].max())
        return hh - atr_val * atr_mult
    low = float(window["low"].min())
    return low + atr_val * atr_mult
