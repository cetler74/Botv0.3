"""EMA50 breakout-pullback setup scan helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional

import pandas as pd

try:
    import pandas_ta as ta
except ImportError:  # pragma: no cover
    ta = None


@dataclass
class SetupScanResult:
    direction: Literal["long", "short", "none"]
    setup_state: str
    breakout_pass: bool
    pullback_pass: bool
    trigger_pass: bool
    breakout_reason: str
    pullback_reason: str
    trigger_reason: str
    swing_level: Optional[float]
    breakout_idx: Optional[int]
    predominant_pct: Optional[float]
    pullback_count: int
    invalidation_reason: str


def compute_ema(close: pd.Series, period: int = 50) -> Optional[pd.Series]:
    if ta is None or close is None or len(close) < period:
        return None
    ema = ta.ema(close, length=period)
    return ema if ema is not None else None


def is_bearish_candle(row: pd.Series) -> bool:
    return float(row["close"]) < float(row["open"])


def is_bullish_candle(row: pd.Series) -> bool:
    return float(row["close"]) > float(row["open"])


def rolling_candle_range(df: pd.DataFrame, window: int = 20) -> pd.Series:
    rng = (df["high"] - df["low"]).astype(float)
    return rng.rolling(window=window, min_periods=max(2, window // 2)).mean()


def predominant_ema_side(
    df: pd.DataFrame,
    ema: pd.Series,
    end_idx: int,
    *,
    lookback: int,
    side: Literal["below", "above"],
) -> float:
    start = max(0, end_idx - lookback)
    if end_idx <= start:
        return 0.0
    slice_df = df.iloc[start:end_idx]
    slice_ema = ema.iloc[start:end_idx]
    if slice_df.empty:
        return 0.0
    if side == "below":
        hits = (slice_df["close"].astype(float) < slice_ema.astype(float)).sum()
    else:
        hits = (slice_df["close"].astype(float) > slice_ema.astype(float)).sum()
    return float(hits) / float(len(slice_df))


def _is_long_breakout(df: pd.DataFrame, ema: pd.Series, idx: int) -> bool:
    if idx < 1:
        return False
    c = float(df["close"].iloc[idx])
    e = float(ema.iloc[idx])
    prev_c = float(df["close"].iloc[idx - 1])
    prev_e = float(ema.iloc[idx - 1])
    return c > e and prev_c <= prev_e


def _is_short_breakout(df: pd.DataFrame, ema: pd.Series, idx: int) -> bool:
    if idx < 1:
        return False
    c = float(df["close"].iloc[idx])
    e = float(ema.iloc[idx])
    prev_c = float(df["close"].iloc[idx - 1])
    prev_e = float(ema.iloc[idx - 1])
    return c < e and prev_c >= prev_e


def _scan_long_forward(
    df: pd.DataFrame,
    ema: pd.Series,
    breakout_idx: int,
    current_idx: int,
    *,
    min_pullback_candles: int,
) -> SetupScanResult:
    swing_high = float(df["high"].iloc[breakout_idx])
    pullback_run = 0
    pullback_complete = False
    invalidation = ""

    for j in range(breakout_idx + 1, current_idx + 1):
        close_j = float(df["close"].iloc[j])
        ema_j = float(ema.iloc[j])
        if close_j < ema_j:
            return SetupScanResult(
                direction="long",
                setup_state="invalid",
                breakout_pass=True,
                pullback_pass=pullback_complete,
                trigger_pass=False,
                breakout_reason="breakout_close_above_ema50",
                pullback_reason="pullback_bearish_candles" if pullback_complete else f"pullback_run_{pullback_run}",
                trigger_reason="invalidated_below_ema50",
                swing_level=swing_high,
                breakout_idx=breakout_idx,
                predominant_pct=None,
                pullback_count=pullback_run,
                invalidation_reason="long_close_below_ema50_before_trigger",
            )

        bearish = is_bearish_candle(df.iloc[j])
        if not pullback_complete:
            if bearish:
                if pullback_run == 0:
                    swing_high = max(
                        swing_high,
                        float(df["high"].iloc[breakout_idx : j].max()),
                    )
                pullback_run += 1
                if pullback_run >= min_pullback_candles:
                    pullback_complete = True
            else:
                pullback_run = 0
                swing_high = max(swing_high, float(df["high"].iloc[j]))

    close_now = float(df["close"].iloc[current_idx])
    if pullback_complete:
        if close_now > swing_high:
            return SetupScanResult(
                direction="long",
                setup_state="triggered",
                breakout_pass=True,
                pullback_pass=True,
                trigger_pass=True,
                breakout_reason="breakout_close_above_ema50",
                pullback_reason=f"pullback_{min_pullback_candles}_bearish",
                trigger_reason="body_close_above_swing_high",
                swing_level=swing_high,
                breakout_idx=breakout_idx,
                predominant_pct=None,
                pullback_count=pullback_run,
                invalidation_reason="none",
            )
        return SetupScanResult(
            direction="long",
            setup_state="waiting_trigger",
            breakout_pass=True,
            pullback_pass=True,
            trigger_pass=False,
            breakout_reason="breakout_close_above_ema50",
            pullback_reason=f"pullback_{min_pullback_candles}_bearish",
            trigger_reason=f"close_{close_now:.6f}_below_swing_{swing_high:.6f}",
            swing_level=swing_high,
            breakout_idx=breakout_idx,
            predominant_pct=None,
            pullback_count=pullback_run,
            invalidation_reason="none",
        )

    if pullback_run > 0:
        state = "waiting_pullback"
        pb_reason = f"pullback_run_{pullback_run}_of_{min_pullback_candles}"
    else:
        state = "waiting_pullback"
        pb_reason = "awaiting_pullback_after_breakout"

    return SetupScanResult(
        direction="long",
        setup_state=state,
        breakout_pass=True,
        pullback_pass=False,
        trigger_pass=False,
        breakout_reason="breakout_close_above_ema50",
        pullback_reason=pb_reason,
        trigger_reason="awaiting_trigger",
        swing_level=swing_high,
        breakout_idx=breakout_idx,
        predominant_pct=None,
        pullback_count=pullback_run,
        invalidation_reason="none",
    )


def _scan_short_forward(
    df: pd.DataFrame,
    ema: pd.Series,
    breakout_idx: int,
    current_idx: int,
    *,
    min_pullback_candles: int,
) -> SetupScanResult:
    swing_low = float(df["low"].iloc[breakout_idx])
    pullback_run = 0
    pullback_complete = False

    for j in range(breakout_idx + 1, current_idx + 1):
        close_j = float(df["close"].iloc[j])
        ema_j = float(ema.iloc[j])
        if close_j > ema_j:
            return SetupScanResult(
                direction="short",
                setup_state="invalid",
                breakout_pass=True,
                pullback_pass=pullback_complete,
                trigger_pass=False,
                breakout_reason="breakout_close_below_ema50",
                pullback_reason="pullback_bullish_candles" if pullback_complete else f"pullback_run_{pullback_run}",
                trigger_reason="invalidated_above_ema50",
                swing_level=swing_low,
                breakout_idx=breakout_idx,
                predominant_pct=None,
                pullback_count=pullback_run,
                invalidation_reason="short_close_above_ema50_before_trigger",
            )

        bullish = is_bullish_candle(df.iloc[j])
        if not pullback_complete:
            if bullish:
                if pullback_run == 0:
                    swing_low = min(
                        swing_low,
                        float(df["low"].iloc[breakout_idx : j].min()),
                    )
                pullback_run += 1
                if pullback_run >= min_pullback_candles:
                    pullback_complete = True
            else:
                pullback_run = 0
                swing_low = min(swing_low, float(df["low"].iloc[j]))

    close_now = float(df["close"].iloc[current_idx])
    if pullback_complete:
        if close_now < swing_low:
            return SetupScanResult(
                direction="short",
                setup_state="triggered",
                breakout_pass=True,
                pullback_pass=True,
                trigger_pass=True,
                breakout_reason="breakout_close_below_ema50",
                pullback_reason=f"pullback_{min_pullback_candles}_bullish",
                trigger_reason="body_close_below_swing_low",
                swing_level=swing_low,
                breakout_idx=breakout_idx,
                predominant_pct=None,
                pullback_count=pullback_run,
                invalidation_reason="none",
            )
        return SetupScanResult(
            direction="short",
            setup_state="waiting_trigger",
            breakout_pass=True,
            pullback_pass=True,
            trigger_pass=False,
            breakout_reason="breakout_close_below_ema50",
            pullback_reason=f"pullback_{min_pullback_candles}_bullish",
            trigger_reason=f"close_{close_now:.6f}_above_swing_{swing_low:.6f}",
            swing_level=swing_low,
            breakout_idx=breakout_idx,
            predominant_pct=None,
            pullback_count=pullback_run,
            invalidation_reason="none",
        )

    pb_reason = (
        f"pullback_run_{pullback_run}_of_{min_pullback_candles}"
        if pullback_run > 0
        else "awaiting_pullback_after_breakout"
    )
    return SetupScanResult(
        direction="short",
        setup_state="waiting_pullback",
        breakout_pass=True,
        pullback_pass=False,
        trigger_pass=False,
        breakout_reason="breakout_close_below_ema50",
        pullback_reason=pb_reason,
        trigger_reason="awaiting_trigger",
        swing_level=swing_low,
        breakout_idx=breakout_idx,
        predominant_pct=None,
        pullback_count=pullback_run,
        invalidation_reason="none",
    )


def find_setup_context(
    df: pd.DataFrame,
    ema: pd.Series,
    params: Dict[str, Any],
    *,
    allow_short: bool = True,
) -> SetupScanResult:
    """Scan for the most recent non-invalid EMA50 breakout-pullback setup."""
    min_candles = int(params.get("min_candles", 80))
    lookback = int(params.get("predominant_lookback_bars", 12))
    side_pct = float(params.get("predominant_side_pct", 0.60))
    min_pullback = int(params.get("min_pullback_candles", 2))

    empty = SetupScanResult(
        direction="none",
        setup_state="waiting_breakout",
        breakout_pass=False,
        pullback_pass=False,
        trigger_pass=False,
        breakout_reason="no_breakout",
        pullback_reason="none",
        trigger_reason="none",
        swing_level=None,
        breakout_idx=None,
        predominant_pct=None,
        pullback_count=0,
        invalidation_reason="none",
    )

    if df is None or ema is None or len(df) < min_candles:
        empty.breakout_reason = "insufficient_candles"
        return empty

    current_idx = len(df) - 1
    start_scan = max(lookback + 2, 2)

    best_long: Optional[SetupScanResult] = None
    best_short: Optional[SetupScanResult] = None

    for b in range(current_idx - 1, start_scan - 1, -1):
        if _is_long_breakout(df, ema, b):
            pct = predominant_ema_side(df, ema, b, lookback=lookback, side="below")
            if pct >= side_pct:
                fwd = _scan_long_forward(
                    df, ema, b, current_idx, min_pullback_candles=min_pullback
                )
                fwd.predominant_pct = pct
                if fwd.setup_state != "invalid":
                    best_long = fwd
                    break
        if allow_short and _is_short_breakout(df, ema, b):
            pct = predominant_ema_side(df, ema, b, lookback=lookback, side="above")
            if pct >= side_pct:
                fwd = _scan_short_forward(
                    df, ema, b, current_idx, min_pullback_candles=min_pullback
                )
                fwd.predominant_pct = pct
                if fwd.setup_state != "invalid":
                    best_short = fwd
                    break

    if best_long and best_long.setup_state == "triggered":
        return best_long
    if best_short and best_short.setup_state == "triggered":
        return best_short
    if best_long and (not best_short or best_long.breakout_idx >= (best_short.breakout_idx or -1)):
        return best_long
    if best_short:
        return best_short
    return empty
