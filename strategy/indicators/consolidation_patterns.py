"""Bull flag / wedge / channel consolidation detection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass
class ConsolidationResult:
    detected: bool
    pattern_type: str
    consolidation_high: float
    consolidation_low: float
    pole_height: float
    width_pct: float
    bar_count: int


def detect_consolidation(
    df: pd.DataFrame,
    *,
    min_bars: int = 8,
    max_width_pct: float = 0.025,
    pole_lookback: int = 30,
) -> ConsolidationResult:
    """
    Tight range after reclaim: flag (drifting down highs), channel, or wedge.
    """
    empty = ConsolidationResult(False, "none", 0, 0, 0, 0, 0)
    if df is None or len(df) < min_bars + 5:
        return empty

    window = df.tail(min_bars + 2)
    highs = window["high"].astype(float).values
    lows = window["low"].astype(float).values
    closes = window["close"].astype(float).values
    cons_high = float(np.max(highs))
    cons_low = float(np.min(lows))
    mid = (cons_high + cons_low) / 2.0
    if mid <= 0:
        return empty
    width_pct = (cons_high - cons_low) / mid
    if width_pct > max_width_pct:
        return empty

    # Pole: drop into consolidation from lookback before window
    prior = df.iloc[-(min_bars + pole_lookback) : -min_bars]
    if len(prior) < 5:
        return empty
    pole_top = float(prior["high"].max())
    pole_bottom = float(prior["low"].min())
    pole_height = max(pole_top - cons_low, cons_high - pole_bottom, cons_high - cons_low)

    # Pattern typing
    x = np.arange(len(highs))
    high_slope = np.polyfit(x, highs, 1)[0] if len(highs) >= 3 else 0
    low_slope = np.polyfit(x, lows, 1)[0] if len(lows) >= 3 else 0
    pattern = "channel"
    if high_slope < 0 and low_slope <= 0:
        pattern = "bull_flag"
    elif high_slope < 0 and low_slope > 0:
        pattern = "wedge"
    elif abs(high_slope) < abs(low_slope) * 0.5:
        pattern = "channel"

    return ConsolidationResult(
        detected=True,
        pattern_type=pattern,
        consolidation_high=cons_high,
        consolidation_low=cons_low,
        pole_height=float(pole_height),
        width_pct=float(width_pct),
        bar_count=len(window),
    )
