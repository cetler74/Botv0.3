"""ARC Step 3 — John Wick candle patterns and entry triggers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Tuple

import pandas as pd

PatternKind = Literal["hammer", "inverted_hammer", "rejection_wick", "none"]


@dataclass
class ArcSignalCandle:
    pattern: PatternKind
    open: float
    high: float
    low: float
    close: float
    bar_index: int
    timestamp: Optional[str]
    lower_wick_ratio: float
    upper_wick_ratio: float
    body_size: float
    entry_price: float
    stop_hint: float
    entry_triggered: bool
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pattern": self.pattern,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "bar_index": self.bar_index,
            "timestamp": self.timestamp,
            "lower_wick_ratio": self.lower_wick_ratio,
            "upper_wick_ratio": self.upper_wick_ratio,
            "body_size": self.body_size,
            "entry_price": self.entry_price,
            "stop_hint": self.stop_hint,
            "entry_triggered": self.entry_triggered,
            "reason": self.reason,
        }


def _candle_parts(row: pd.Series) -> Tuple[float, float, float, float, float, float, float]:
    o = float(row["open"])
    h = float(row["high"])
    l = float(row["low"])
    c = float(row["close"])
    body = abs(c - o)
    lower_wick = min(o, c) - l
    upper_wick = h - max(o, c)
    ref = body if body > 0 else (h - l) * 0.25 or 1e-9
    return o, h, l, c, body, lower_wick / ref, upper_wick / ref


def _ts_str(df: pd.DataFrame, idx: int) -> Optional[str]:
    try:
        return str(df.index[idx])
    except Exception:
        return None


def is_hammer(row: pd.Series, min_wick_body_ratio: float) -> bool:
    _, _, _, c, body, lower_ratio, _ = _candle_parts(row)
    rng = float(row["high"]) - float(row["low"])
    if rng <= 0:
        return False
    return lower_ratio >= min_wick_body_ratio and c >= float(row["open"])


def is_inverted_hammer(row: pd.Series, min_wick_body_ratio: float) -> bool:
    _, _, _, _, body, lower_ratio, _ = _candle_parts(row)
    rng = float(row["high"]) - float(row["low"])
    if rng <= 0:
        return False
    return lower_ratio >= min_wick_body_ratio


def is_rejection_wick(row: pd.Series, min_upper_ratio: float) -> bool:
    _, _, _, _, _, _, upper_ratio = _candle_parts(row)
    return upper_ratio >= min_upper_ratio


def detect_john_wick_entry(
    entry_df: pd.DataFrame,
    zone: str,
    *,
    hammer_min_wick_body_ratio: float = 2.0,
    rejection_min_upper_wick_ratio: float = 2.0,
    lookback_bars: int = 12,
) -> Optional[ArcSignalCandle]:
    """
    Find signal candle at zone edge; entry when latest bar breaks signal high/low.
    Long at buy zone: hammer/inverted hammer + break above signal high.
    Short at sell zone: rejection wick + break below signal low.
    """
    if entry_df is None or len(entry_df) < 3:
        return None

    zone_l = str(zone or "").lower()
    if zone_l not in {"buy", "sell"}:
        return None

    n = len(entry_df)
    start = max(1, n - lookback_bars - 1)
    latest = entry_df.iloc[-1]
    latest_close = float(latest["close"])
    latest_high = float(latest["high"])
    latest_low = float(latest["low"])

    best: Optional[ArcSignalCandle] = None
    for sig_idx in range(start, n - 1):
        sig_row = entry_df.iloc[sig_idx]
        o, h, l, c, body, lower_r, upper_r = _candle_parts(sig_row)

        if zone_l == "buy":
            if not (is_hammer(sig_row, hammer_min_wick_body_ratio) or is_inverted_hammer(sig_row, hammer_min_wick_body_ratio)):
                continue
            pattern: PatternKind = "hammer" if c >= o else "inverted_hammer"
            triggered = latest_high > h or latest_close > h
            stop = l * (1.0 - 0.0005)
            entry_px = h if triggered else h
            reason = "long_break_above_signal_high" if triggered else "signal_pending_long"
            cand = ArcSignalCandle(
                pattern=pattern,
                open=o,
                high=h,
                low=l,
                close=c,
                bar_index=sig_idx,
                timestamp=_ts_str(entry_df, sig_idx),
                lower_wick_ratio=lower_r,
                upper_wick_ratio=upper_r,
                body_size=body,
                entry_price=entry_px,
                stop_hint=stop,
                entry_triggered=triggered,
                reason=reason,
            )
        else:
            if not is_rejection_wick(sig_row, rejection_min_upper_wick_ratio):
                continue
            pattern = "rejection_wick"
            triggered = latest_low < l or latest_close < l
            stop = h * (1.0 + 0.0005)
            entry_px = l if triggered else l
            reason = "short_break_below_signal_low" if triggered else "signal_pending_short"
            cand = ArcSignalCandle(
                pattern=pattern,
                open=o,
                high=h,
                low=l,
                close=c,
                bar_index=sig_idx,
                timestamp=_ts_str(entry_df, sig_idx),
                lower_wick_ratio=lower_r,
                upper_wick_ratio=upper_r,
                body_size=body,
                entry_price=entry_px,
                stop_hint=stop,
                entry_triggered=triggered,
                reason=reason,
            )

        if best is None or sig_idx > best.bar_index:
            best = cand

    return best


def check_invalidation_reversal(
    entry_df: pd.DataFrame,
    entry_price: float,
    target_50: float,
    direction: str,
    reversal_pct: float = 0.50,
) -> Tuple[bool, str]:
    """Price reached ~50% of target distance then sharp reversal."""
    if entry_df is None or entry_df.empty or entry_price <= 0:
        return False, "no_data"
    d = str(direction or "").lower()
    latest = float(entry_df["close"].iloc[-1])
    if d in {"long", "buy", "up"}:
        dist = target_50 - entry_price
        if dist <= 0:
            return False, "invalid_target"
        progress = (latest - entry_price) / dist
        if progress >= reversal_pct:
            recent = entry_df["close"].astype(float).iloc[-3:]
            if len(recent) >= 2 and recent.iloc[-1] < recent.iloc[-2]:
                return True, f"long_reversal_after_{progress:.0%}_of_target50"
    elif d in {"short", "sell", "down"}:
        dist = entry_price - target_50
        if dist <= 0:
            return False, "invalid_target"
        progress = (entry_price - latest) / dist
        if progress >= reversal_pct:
            recent = entry_df["close"].astype(float).iloc[-3:]
            if len(recent) >= 2 and recent.iloc[-1] > recent.iloc[-2]:
                return True, f"short_reversal_after_{progress:.0%}_of_target50"
    return False, "none"
