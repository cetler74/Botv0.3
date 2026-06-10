"""TradingLab-style validated swing market structure (pure price action)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Tuple

import pandas as pd

TrendDirection = Literal["uptrend", "downtrend", "neutral"]
SwingKind = Literal["high", "low"]


@dataclass
class ValidatedSwing:
    kind: SwingKind
    price: float
    bar_index: int
    timestamp: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "price": self.price,
            "bar_index": self.bar_index,
            "timestamp": self.timestamp,
        }


@dataclass
class MarketStructureState:
    trend: TrendDirection
    validated_swings: List[ValidatedSwing] = field(default_factory=list)
    last_swing_high: Optional[float] = None
    last_swing_low: Optional[float] = None
    step1_pass: bool = False
    step1_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trend": self.trend,
            "validated_swings": [s.to_dict() for s in self.validated_swings],
            "last_swing_high": self.last_swing_high,
            "last_swing_low": self.last_swing_low,
            "step1_pass": self.step1_pass,
            "step1_reason": self.step1_reason,
        }


def _pivot_indices(highs: pd.Series, lows: pd.Series, order: int) -> Tuple[List[int], List[int]]:
    """Local extrema indices for candidate swing highs and lows."""
    hi_vals = highs.astype(float).values
    lo_vals = lows.astype(float).values
    n = len(hi_vals)
    pivot_highs: List[int] = []
    pivot_lows: List[int] = []
    for i in range(order, n - order):
        hi_window = hi_vals[i - order : i + order + 1]
        lo_window = lo_vals[i - order : i + order + 1]
        if hi_vals[i] == max(hi_window):
            pivot_highs.append(i)
        if lo_vals[i] == min(lo_window):
            pivot_lows.append(i)
    return pivot_highs, pivot_lows


def _ts_str(df: pd.DataFrame, idx: int) -> Optional[str]:
    try:
        ts = df.index[idx]
        return str(ts)
    except Exception:
        return None


def analyze_market_structure(
    df: pd.DataFrame,
    *,
    pivot_order: int = 3,
) -> MarketStructureState:
    """
    Walk validated swings and derive trend.

    Rules:
    - Swing low validates when price breaks above the prior validated swing high.
    - Swing high validates when price breaks below the prior validated swing low.
    - Uptrend: higher validated highs and lows; invalidated on close below last validated low.
    - Downtrend: lower validated highs and lows; invalidated on close above last validated high.
    """
    empty = MarketStructureState(
        trend="neutral",
        step1_pass=False,
        step1_reason="insufficient_candles",
    )
    if df is None or len(df) < pivot_order * 2 + 10:
        return empty

    work = df.copy()
    highs = work["high"].astype(float)
    lows = work["low"].astype(float)
    closes = work["close"].astype(float)
    pivot_highs, pivot_lows = _pivot_indices(highs, lows, pivot_order)

    # Merge pivot events chronologically
    events: List[Tuple[int, str, float]] = []
    for i in pivot_highs:
        events.append((i, "high", float(highs.iloc[i])))
    for i in pivot_lows:
        events.append((i, "low", float(lows.iloc[i])))
    events.sort(key=lambda x: x[0])

    validated: List[ValidatedSwing] = []
    last_valid_high: Optional[ValidatedSwing] = None
    last_valid_low: Optional[ValidatedSwing] = None
    pending_high: Optional[Tuple[int, float]] = None
    pending_low: Optional[Tuple[int, float]] = None

    n = len(work)
    event_idx = 0

    for bar in range(n):
        close = float(closes.iloc[bar])

        while event_idx < len(events) and events[event_idx][0] == bar:
            _, kind, price = events[event_idx]
            if kind == "high":
                pending_high = (bar, price)
            else:
                pending_low = (bar, price)
            event_idx += 1

        # Validate pending swing low when price breaks above last validated high
        if pending_low is not None and last_valid_high is not None:
            if close > last_valid_high.price:
                swing = ValidatedSwing(
                    "low", pending_low[1], pending_low[0], _ts_str(work, pending_low[0])
                )
                validated.append(swing)
                last_valid_low = swing
                pending_low = None

        # Validate pending swing high when price breaks below last validated low
        if pending_high is not None and last_valid_low is not None:
            if close < last_valid_low.price:
                swing = ValidatedSwing(
                    "high", pending_high[1], pending_high[0], _ts_str(work, pending_high[0])
                )
                validated.append(swing)
                last_valid_high = swing
                pending_high = None

        # Seed only the first validated low; subsequent swings require break confirmation.
        if pending_low is not None and last_valid_low is None and last_valid_high is None and not validated:
            swing = ValidatedSwing(
                "low", pending_low[1], pending_low[0], _ts_str(work, pending_low[0])
            )
            validated.append(swing)
            last_valid_low = swing
            pending_low = None

    if len(validated) < 2:
        return MarketStructureState(
            trend="neutral",
            validated_swings=validated,
            last_swing_high=last_valid_high.price if last_valid_high else None,
            last_swing_low=last_valid_low.price if last_valid_low else None,
            step1_pass=False,
            step1_reason="insufficient_validated_swings",
        )

    # Determine trend from last two highs and lows
    val_highs = [s for s in validated if s.kind == "high"]
    val_lows = [s for s in validated if s.kind == "low"]

    trend: TrendDirection = "neutral"
    reason = "no_clear_trend"

    if len(val_highs) >= 2 and len(val_lows) >= 2:
        hh = val_highs[-1].price > val_highs[-2].price
        hl = val_lows[-1].price > val_lows[-2].price
        lh = val_highs[-1].price < val_highs[-2].price
        ll = val_lows[-1].price < val_lows[-2].price

        last_close = float(closes.iloc[-1])
        if hh and hl and last_valid_low is not None and last_close >= last_valid_low.price:
            trend = "uptrend"
            reason = (
                f"uptrend: HH {val_highs[-2].price:.6f}->{val_highs[-1].price:.6f}, "
                f"HL {val_lows[-2].price:.6f}->{val_lows[-1].price:.6f}; "
                f"close {last_close:.6f} above last validated low {last_valid_low.price:.6f}"
            )
        elif lh and ll and last_valid_high is not None and last_close <= last_valid_high.price:
            trend = "downtrend"
            reason = (
                f"downtrend: LH {val_highs[-2].price:.6f}->{val_highs[-1].price:.6f}, "
                f"LL {val_lows[-2].price:.6f}->{val_lows[-1].price:.6f}; "
                f"close {last_close:.6f} below last validated high {last_valid_high.price:.6f}"
            )
        elif hh and hl and last_valid_low is not None and last_close < last_valid_low.price:
            reason = (
                f"uptrend_invalidated: close {last_close:.6f} broke below "
                f"last validated low {last_valid_low.price:.6f}"
            )
        elif lh and ll and last_valid_high is not None and last_close > last_valid_high.price:
            reason = (
                f"downtrend_invalidated: close {last_close:.6f} broke above "
                f"last validated high {last_valid_high.price:.6f}"
            )
        else:
            reason = "mixed_structure_no_hh_hl_or_lh_ll"

    step1_pass = trend in ("uptrend", "downtrend")
    return MarketStructureState(
        trend=trend,
        validated_swings=validated,
        last_swing_high=last_valid_high.price if last_valid_high else None,
        last_swing_low=last_valid_low.price if last_valid_low else None,
        step1_pass=step1_pass,
        step1_reason=reason,
    )
