"""ARC Step 2 — range move filter, target geometry, box/swing classification."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional

import pandas as pd

RangeGeometry = Literal["pinball", "direct", "neutral"]


@dataclass
class RangeMoveResult:
    pass_move: bool
    range_pct_move: float
    move_direction: str
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pass_move": self.pass_move,
            "range_pct_move": self.range_pct_move,
            "move_direction": self.move_direction,
            "reason": self.reason,
        }


@dataclass
class ArcTargets:
    target_50: float
    target_100: float
    direction: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_50": self.target_50,
            "target_100": self.target_100,
            "direction": self.direction,
        }


def measure_uninterrupted_move(
    entry_df: pd.DataFrame,
    range_size: float,
    min_pct: float,
    *,
    lookback: int = 60,
) -> RangeMoveResult:
    """Largest single-direction leg as fraction of daily range."""
    if entry_df is None or len(entry_df) < 3 or range_size <= 0:
        return RangeMoveResult(False, 0.0, "none", "insufficient_data")

    df = entry_df.iloc[-lookback:] if len(entry_df) > lookback else entry_df
    closes = df["close"].astype(float).values
    best = 0.0
    best_dir = "none"
    anchor = closes[0]
    direction = 0

    for i in range(1, len(closes)):
        c = closes[i]
        move_up = (c - min(closes[: i + 1])) / range_size
        move_down = (max(closes[: i + 1]) - c) / range_size
        if move_up >= move_down:
            leg = move_up
            leg_dir = "up"
        else:
            leg = move_down
            leg_dir = "down"
        if leg > best:
            best = leg
            best_dir = leg_dir

    passed = best >= min_pct
    reason = f"leg_{best:.2%}_of_range_need_{min_pct:.2%}" if passed else f"leg_{best:.2%}_below_{min_pct:.2%}"
    return RangeMoveResult(passed, round(best, 4), best_dir, reason)


def compute_arc_targets(
    entry: float,
    direction: str,
    range_size: float,
) -> Optional[ArcTargets]:
    """TP at 50% and 100% of range from entry."""
    if entry <= 0 or range_size <= 0:
        return None
    d = str(direction or "").lower()
    if d in {"buy", "long", "up"}:
        return ArcTargets(
            target_50=entry + range_size * 0.5,
            target_100=entry + range_size,
            direction="long",
        )
    if d in {"sell", "short", "down"}:
        return ArcTargets(
            target_50=entry - range_size * 0.5,
            target_100=entry - range_size,
            direction="short",
        )
    return None


def classify_range_geometry(
    box_width: float,
    swing_high: Optional[float],
    swing_low: Optional[float],
    box_high: float,
    box_low: float,
) -> RangeGeometry:
    """Wide box + tight swings → pinball; tight box + distant swings → direct."""
    if box_width <= 0:
        return "neutral"
    swing_span = 0.0
    if swing_high is not None:
        swing_span += max(0.0, swing_high - box_high)
    if swing_low is not None:
        swing_span += max(0.0, box_low - swing_low)
    avg_ext = swing_span / 2.0 if swing_span > 0 else 0.0
    ratio = avg_ext / box_width if box_width > 0 else 0.0
    if ratio < 0.15:
        return "pinball"
    if ratio > 0.45:
        return "direct"
    return "neutral"
