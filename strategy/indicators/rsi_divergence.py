"""Bullish RSI divergence detection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

import pandas as pd

try:
    import pandas_ta as ta
except ImportError:  # pragma: no cover
    ta = None


@dataclass
class BullishDivergenceResult:
    detected: bool
    price_low1: float
    price_low2: float
    rsi_low1: float
    rsi_low2: float
    bars_apart: int


def _rsi(close: pd.Series, length: int = 14) -> Optional[pd.Series]:
    if ta is None:
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(length).mean()
        loss = (-delta.clip(upper=0)).rolling(length).mean()
        rs = gain / loss.replace(0, 1e-12)
        return 100 - (100 / (1 + rs))
    out = ta.rsi(close.astype(float), length=length)
    return out if out is not None else None


def _swing_low_indices(series: pd.Series, order: int = 3) -> List[int]:
    """Local minima indices (simple pivot)."""
    vals = series.values
    n = len(vals)
    pivots = []
    for i in range(order, n - order):
        window = vals[i - order : i + order + 1]
        if vals[i] == min(window) and vals[i] == window[order]:
            pivots.append(i)
    return pivots


def detect_bullish_rsi_divergence(
    df: pd.DataFrame,
    *,
    rsi_period: int = 14,
    lookback_bars: int = 40,
    swing_order: int = 3,
) -> BullishDivergenceResult:
    """
    Price makes lower low while RSI makes higher low between two swing lows.
    """
    empty = BullishDivergenceResult(False, 0, 0, 0, 0, 0)
    if df is None or len(df) < lookback_bars + rsi_period + 5:
        return empty
    work = df.tail(lookback_bars + rsi_period + 10)
    close = work["close"].astype(float)
    price_lows = work["low"].astype(float) if "low" in work.columns else close
    rsi = _rsi(close, rsi_period)
    if rsi is None:
        return empty

    price_pivots = _swing_low_indices(price_lows, swing_order)
    if len(price_pivots) < 2:
        return empty

    # Use last two price swing lows in lookback; RSI is still derived from close.
    i2, i1 = price_pivots[-1], price_pivots[-2]
    p1 = float(price_lows.iloc[i1])
    p2 = float(price_lows.iloc[i2])
    r1 = float(rsi.iloc[i1])
    r2 = float(rsi.iloc[i2])
    if pd.isna(r1) or pd.isna(r2):
        return empty

    detected = p2 < p1 and r2 > r1
    return BullishDivergenceResult(
        detected=detected,
        price_low1=p1,
        price_low2=p2,
        rsi_low1=r1,
        rsi_low2=r2,
        bars_apart=i2 - i1,
    )
