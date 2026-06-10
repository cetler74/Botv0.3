"""ARC Step 1 — daily box, external swings, and zone classification."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Tuple

import pandas as pd

from strategy.indicators.market_structure import _pivot_indices

ZoneKind = Literal["buy", "sell", "no_trade"]


@dataclass
class DailyBox:
    box_high: float
    box_low: float
    box_date: Optional[str] = None
    prev_close: Optional[float] = None
    gap_type: str = "none"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "box_high": self.box_high,
            "box_low": self.box_low,
            "box_date": self.box_date,
            "prev_close": self.prev_close,
            "gap_type": self.gap_type,
        }


@dataclass
class ArcLevels:
    box_high: float
    box_low: float
    swing_high: Optional[float]
    swing_low: Optional[float]
    range_size: float
    gap_info: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "box_high": self.box_high,
            "box_low": self.box_low,
            "swing_high": self.swing_high,
            "swing_low": self.swing_low,
            "range_size": self.range_size,
            "gap_info": self.gap_info,
        }


@dataclass
class ArcZone:
    zone: ZoneKind
    zone_level: str
    distance_pct: float
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "zone": self.zone,
            "zone_level": self.zone_level,
            "distance_pct": self.distance_pct,
            "reason": self.reason,
        }


def _ts_str(df: pd.DataFrame, idx: int) -> Optional[str]:
    try:
        return str(df.index[idx])
    except Exception:
        return None


def compute_daily_box(daily_df: pd.DataFrame) -> Optional[DailyBox]:
    """Previous completed daily bar high/low (wicks included)."""
    if daily_df is None or len(daily_df) < 2:
        return None
    prev = daily_df.iloc[-2]
    try:
        box_high = float(prev["high"])
        box_low = float(prev["low"])
        prev_close = float(prev["close"])
    except (TypeError, ValueError, KeyError):
        return None
    if box_high <= box_low:
        return None
    return DailyBox(
        box_high=box_high,
        box_low=box_low,
        box_date=_ts_str(daily_df, len(daily_df) - 2),
        prev_close=prev_close,
    )


def _current_utc_day_slice(entry_df: pd.DataFrame) -> pd.DataFrame:
    if entry_df is None or entry_df.empty:
        return entry_df
    idx = entry_df.index
    if not hasattr(idx, "date"):
        return entry_df.iloc[-48:]
    try:
        last_date = idx[-1].date() if hasattr(idx[-1], "date") else None
        if last_date is None:
            return entry_df.iloc[-48:]
        mask = [getattr(ts, "date", lambda: None)() == last_date for ts in idx]
        sliced = entry_df.loc[mask]
        return sliced if not sliced.empty else entry_df.iloc[-48:]
    except Exception:
        return entry_df.iloc[-48:]


def apply_utc_gap_rebox(
    daily_box: DailyBox,
    entry_df: pd.DataFrame,
    *,
    enabled: bool = True,
) -> Tuple[float, float, Dict[str, Any]]:
    """Gap up → rebox to current UTC day H/L; gap down → widen box to include day low."""
    info: Dict[str, Any] = {"gap_type": "none", "reboxed": False}
    box_high = daily_box.box_high
    box_low = daily_box.box_low
    if not enabled or entry_df is None or entry_df.empty:
        return box_high, box_low, info

    day_slice = _current_utc_day_slice(entry_df)
    try:
        day_open = float(day_slice["open"].iloc[0])
        day_high = float(day_slice["high"].max())
        day_low = float(day_slice["low"].min())
    except (TypeError, ValueError, KeyError):
        return box_high, box_low, info

    if day_open > box_high:
        info.update({"gap_type": "gap_up", "reboxed": True, "day_open": day_open})
        return day_high, day_low, info
    if day_open < box_low:
        info.update({"gap_type": "gap_down", "reboxed": True, "day_open": day_open})
        return box_high, min(box_low, day_low), info
    info["day_open"] = day_open
    return box_high, box_low, info


def find_external_swings(
    entry_df: pd.DataFrame,
    box_high: float,
    box_low: float,
    *,
    lookback: int = 120,
    pivot_order: int = 3,
) -> Tuple[Optional[float], Optional[float]]:
    """Next swing above box_high and below box_low on entry timeframe."""
    if entry_df is None or len(entry_df) < pivot_order * 2 + 3:
        return None, None
    df = entry_df.iloc[-lookback:] if len(entry_df) > lookback else entry_df
    highs = df["high"].astype(float)
    lows = df["low"].astype(float)
    pivot_highs, pivot_lows = _pivot_indices(highs, lows, pivot_order)

    swing_high: Optional[float] = None
    for i in pivot_highs:
        px = float(highs.iloc[i])
        if px > box_high and (swing_high is None or px < swing_high):
            swing_high = px

    swing_low: Optional[float] = None
    for i in pivot_lows:
        px = float(lows.iloc[i])
        if px < box_low and (swing_low is None or px > swing_low):
            swing_low = px

    if swing_high is None:
        above = highs[highs > box_high]
        swing_high = float(above.min()) if not above.empty else None
    if swing_low is None:
        below = lows[lows < box_low]
        swing_low = float(below.max()) if not below.empty else None

    return swing_high, swing_low


def classify_arc_zone(
    price: float,
    box_high: float,
    box_low: float,
    swing_high: Optional[float],
    swing_low: Optional[float],
    *,
    tolerance_pct: float = 0.08,
    middle_no_trade_pct: float = 0.40,
) -> ArcZone:
    """Classify buy/sell/no_trade zone from price vs ARC levels."""
    rng = box_high - box_low
    if rng <= 0 or price <= 0:
        return ArcZone("no_trade", "invalid", 1.0, "invalid_range")

    pos = (price - box_low) / rng
    mid_lo = (1.0 - middle_no_trade_pct) / 2.0
    mid_hi = 1.0 - mid_lo
    if mid_lo <= pos <= mid_hi:
        return ArcZone("no_trade", "middle", abs(pos - 0.5), "middle_of_range")

    tol = tolerance_pct * rng

    levels: List[Tuple[str, float, ZoneKind]] = [
        ("box_high", box_high, "sell"),
        ("box_low", box_low, "buy"),
    ]
    if swing_high is not None:
        levels.append(("swing_high", swing_high, "sell"))
    if swing_low is not None:
        levels.append(("swing_low", swing_low, "buy"))

    best_level = ""
    best_dist = float("inf")
    best_zone: ZoneKind = "no_trade"
    for name, lvl, zone in levels:
        dist = abs(price - lvl)
        if dist < best_dist:
            best_dist = dist
            best_level = name
            best_zone = zone

    if best_dist > tol:
        return ArcZone("no_trade", "between_levels", best_dist / rng, "not_at_edge")

    return ArcZone(
        best_zone,
        best_level,
        best_dist / rng,
        f"at_{best_level}_within_tol",
    )
