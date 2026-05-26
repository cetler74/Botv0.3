"""SMA stack and compression metrics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import pandas as pd


@dataclass
class SmaStackSnapshot:
    price: float
    smas: Dict[int, float]
    sma21_above_sma200: bool
    sma21_crossed_above_sma200_recent: bool
    sma21_crossing_above_sma200: bool
    distance_to_sma21_pct: float
    compression_spread_pct: float
    sma50_slope: float
    sma100_slope: float
    price_below_sma200_recent: bool


def _sma(series: pd.Series, period: int) -> Optional[pd.Series]:
    if len(series) < period:
        return None
    return series.astype(float).rolling(period).mean()


def compute_sma_stack(
    df: pd.DataFrame,
    *,
    periods: Optional[List[int]] = None,
    selloff_lookback: int = 60,
    crossing_lookback_bars: int = 8,
    crossing_max_gap_pct: float = 0.003,
) -> Optional[SmaStackSnapshot]:
    if df is None or len(df) < 210:
        return None
    periods = periods or [21, 50, 80, 100, 200]
    close = df["close"].astype(float)
    price = float(close.iloc[-1])
    smas: Dict[int, float] = {}
    series_map: Dict[int, pd.Series] = {}
    for p in periods:
        s = _sma(close, p)
        if s is None or pd.isna(s.iloc[-1]):
            return None
        smas[p] = float(s.iloc[-1])
        series_map[p] = s

    sma_vals = list(smas.values())
    spread = (max(sma_vals) - min(sma_vals)) / max(price, 1e-12)
    dist21 = abs(price - smas.get(21, price)) / max(price, 1e-12)
    s50 = series_map.get(50)
    s100 = series_map.get(100)
    s21 = series_map.get(21)
    s200 = series_map.get(200)
    slope50 = 0.0
    slope100 = 0.0
    if s50 is not None and len(s50) >= 6 and not pd.isna(s50.iloc[-6]):
        slope50 = float((s50.iloc[-1] - s50.iloc[-6]) / max(s50.iloc[-6], 1e-12))
    if s100 is not None and len(s100) >= 6 and not pd.isna(s100.iloc[-6]):
        slope100 = float((s100.iloc[-1] - s100.iloc[-6]) / max(s100.iloc[-6], 1e-12))

    sma21_above_sma200 = smas.get(21, 0) >= smas.get(200, 0)
    sma21_crossed_recent = False
    sma21_crossing_above = sma21_above_sma200
    if s21 is not None and s200 is not None:
        spread_series = (s21 - s200) / close.replace(0, pd.NA)
        recent = spread_series.dropna().tail(max(2, crossing_lookback_bars + 1))
        if len(recent) >= 2:
            prev = recent.iloc[:-1]
            curr_gap = float(recent.iloc[-1])
            prev_gap = float(prev.iloc[-1])
            sma21_crossed_recent = bool(((prev <= 0) & (recent.iloc[1:].values > 0)).any())
            narrowing = curr_gap > prev_gap
            near_cross = curr_gap < 0 and abs(curr_gap) <= crossing_max_gap_pct and narrowing
            sma21_crossing_above = bool(sma21_above_sma200 or sma21_crossed_recent or near_cross)

    below200 = False
    if s200 is not None:
        window = close.tail(min(selloff_lookback, len(close)))
        below200 = bool((window < s200.reindex(window.index, method="ffill")).any())

    return SmaStackSnapshot(
        price=price,
        smas=smas,
        sma21_above_sma200=sma21_above_sma200,
        sma21_crossed_above_sma200_recent=sma21_crossed_recent,
        sma21_crossing_above_sma200=sma21_crossing_above,
        distance_to_sma21_pct=dist21,
        compression_spread_pct=spread,
        sma50_slope=slope50,
        sma100_slope=slope100,
        price_below_sma200_recent=below200,
    )
