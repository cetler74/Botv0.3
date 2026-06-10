"""Dual-SMA (20/200) indicator helpers for day-trading playbook."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional, Tuple

import pandas as pd

SlopeDirection = Literal["rising", "falling", "flat"]


@dataclass
class DualSmaSnapshot:
    price: float
    sma20: float
    sma200: float
    price_vs_sma20_pct: float
    price_vs_sma200_pct: float
    sma20_slope: float
    sma20_direction: SlopeDirection
    sma200_slope: float
    sma200_flat: bool
    daily_gap_vs_sma200_pct: float


def _sma(series: pd.Series, period: int) -> pd.Series:
    return series.astype(float).rolling(period).mean()


def sma_slope(series: pd.Series, lookback: int = 5) -> float:
    """Relative slope over lookback bars."""
    if series is None or len(series) < lookback + 1:
        return 0.0
    tail = series.dropna()
    if len(tail) < lookback + 1:
        return 0.0
    prev = float(tail.iloc[-(lookback + 1)])
    curr = float(tail.iloc[-1])
    return (curr - prev) / max(abs(prev), 1e-12)


def classify_slope(slope: float, flat_threshold: float = 0.0008) -> SlopeDirection:
    if slope > flat_threshold:
        return "rising"
    if slope < -flat_threshold:
        return "falling"
    return "flat"


def is_sma_flat(series: pd.Series, max_slope_pct: float, lookback: int = 5) -> bool:
    return abs(sma_slope(series, lookback)) <= max_slope_pct


def extension_from_sma20_pct(price: float, sma20: float) -> float:
    return abs(price - sma20) / max(abs(price), 1e-12)


def compute_dual_sma(
    df: pd.DataFrame,
    *,
    sma20_period: int = 20,
    sma200_period: int = 200,
    slope_lookback: int = 5,
    sma200_max_slope_pct: float = 0.0015,
    sma20_flat_threshold: float = 0.0008,
) -> Optional[DualSmaSnapshot]:
    if df is None or len(df) < sma200_period + slope_lookback + 2:
        return None
    close = df["close"].astype(float)
    s20 = _sma(close, sma20_period)
    s200 = _sma(close, sma200_period)
    if pd.isna(s20.iloc[-1]) or pd.isna(s200.iloc[-1]):
        return None
    price = float(close.iloc[-1])
    sma20 = float(s20.iloc[-1])
    sma200 = float(s200.iloc[-1])
    slope20 = sma_slope(s20, slope_lookback)
    slope200 = sma_slope(s200, slope_lookback)
    gap = daily_gap_vs_sma200(df, sma200_period=sma200_period)
    return DualSmaSnapshot(
        price=price,
        sma20=sma20,
        sma200=sma200,
        price_vs_sma20_pct=(price - sma20) / max(price, 1e-12),
        price_vs_sma200_pct=(price - sma200) / max(price, 1e-12),
        sma20_slope=slope20,
        sma20_direction=classify_slope(slope20, sma20_flat_threshold),
        sma200_slope=slope200,
        sma200_flat=is_sma_flat(s200, sma200_max_slope_pct, slope_lookback),
        daily_gap_vs_sma200_pct=gap,
    )


def daily_gap_vs_sma200(df: pd.DataFrame, *, sma200_period: int = 200) -> float:
    """Gap of latest close vs SMA200 as fraction of price."""
    if df is None or len(df) < sma200_period:
        return 0.0
    close = df["close"].astype(float)
    sma200 = float(_sma(close, sma200_period).iloc[-1])
    price = float(close.iloc[-1])
    return (price - sma200) / max(price, 1e-12)


def detect_sma20_retrace(
    df: pd.DataFrame,
    direction: Literal["long", "short"],
    *,
    sma20_period: int = 20,
    tolerance_pct: float = 0.003,
    lookback_bars: int = 6,
) -> Tuple[bool, str]:
    """
    Price touched or consolidated near SMA20 within tolerance on recent bars.
    Long: rising SMA20, price pulled back to SMA20 from above.
    Short: falling SMA20, price pulled back to SMA20 from below.
    """
    if df is None or len(df) < sma20_period + lookback_bars + 2:
        return False, "insufficient_candles"
    close = df["close"].astype(float)
    low = df["low"].astype(float)
    high = df["high"].astype(float)
    s20 = _sma(close, sma20_period)
    if pd.isna(s20.iloc[-1]):
        return False, "sma20_unavailable"
    sma20_dir = classify_slope(sma_slope(s20, 5))
    window = df.tail(lookback_bars + 1)
    for i in range(-lookback_bars, 0):
        bar_close = float(close.iloc[i])
        bar_low = float(low.iloc[i])
        bar_high = float(high.iloc[i])
        bar_sma = float(s20.iloc[i])
        if pd.isna(bar_sma):
            continue
        dist_low = abs(bar_low - bar_sma) / max(bar_close, 1e-12)
        dist_high = abs(bar_high - bar_sma) / max(bar_close, 1e-12)
        dist_close = abs(bar_close - bar_sma) / max(bar_close, 1e-12)
        near = min(dist_low, dist_high, dist_close) <= tolerance_pct
        if direction == "long":
            if sma20_dir != "rising":
                return False, f"sma20_not_rising:{sma20_dir}"
            if near and bar_close >= bar_sma * (1.0 - tolerance_pct):
                return True, f"retrace_into_rising_sma20 dist={min(dist_low, dist_high, dist_close):.4f}"
        else:
            if sma20_dir != "falling":
                return False, f"sma20_not_falling:{sma20_dir}"
            if near and bar_close <= bar_sma * (1.0 + tolerance_pct):
                return True, f"retrace_into_falling_sma20 dist={min(dist_low, dist_high, dist_close):.4f}"
    return False, "no_sma20_retrace_in_window"


def detect_sma20_rejection_short(
    df: pd.DataFrame,
    *,
    sma20_period: int = 20,
    lookback_bars: int = 8,
) -> Tuple[bool, str]:
    """Failed break above SMA20 in downtrend — continuation short."""
    if df is None or len(df) < sma20_period + lookback_bars + 2:
        return False, "insufficient_candles"
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    s20 = _sma(close, sma20_period)
    if pd.isna(s20.iloc[-1]):
        return False, "sma20_unavailable"
    if classify_slope(sma_slope(s20, 5)) != "falling":
        return False, "sma20_not_falling"
    last_close = float(close.iloc[-1])
    last_sma = float(s20.iloc[-1])
    if last_close >= last_sma:
        return False, "close_not_below_sma20"
    for i in range(-lookback_bars, -1):
        if float(high.iloc[i]) > float(s20.iloc[i]) and float(close.iloc[i]) < float(s20.iloc[i]):
            return True, f"rejection_above_sma20 bar={i}"
    return False, "no_rejection_pattern"


def detect_squeeze_setup(
    daily_df: pd.DataFrame,
    *,
    sma20_period: int = 20,
    sma200_period: int = 200,
    sma200_max_slope_pct: float = 0.0015,
    slope_lookback: int = 5,
    crossing_lookback: int = 10,
) -> Tuple[bool, str]:
    """Flat SMA200 + rising SMA20 crossing or nearing SMA200."""
    snap = compute_dual_sma(
        daily_df,
        sma20_period=sma20_period,
        sma200_period=sma200_period,
        slope_lookback=slope_lookback,
        sma200_max_slope_pct=sma200_max_slope_pct,
    )
    if snap is None:
        return False, "insufficient_daily_data"
    if not snap.sma200_flat:
        return False, "sma200_not_flat"
    if snap.sma20_direction != "rising":
        return False, "sma20_not_rising"
    close = daily_df["close"].astype(float)
    s20 = _sma(close, sma20_period)
    s200 = _sma(close, sma200_period)
    spread = (s20 - s200) / close.replace(0, pd.NA)
    recent = spread.dropna().tail(max(2, crossing_lookback))
    if len(recent) < 2:
        return False, "insufficient_spread_history"
    crossed = bool(((recent.iloc[:-1] <= 0) & (recent.iloc[1:].values > 0)).any())
    near_cross = float(recent.iloc[-1]) < 0 and abs(float(recent.iloc[-1])) <= 0.005
    if crossed:
        return True, "sma20_crossed_above_flat_sma200"
    if near_cross:
        return True, "sma20_approaching_flat_sma200"
    if snap.sma20 > snap.sma200 and snap.price < snap.sma200:
        return True, "squeeze_oscillation_between_20_and_200"
    return False, "no_squeeze_pattern"
