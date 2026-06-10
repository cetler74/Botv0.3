"""Opening range (9:30–9:35 ET) breakout + retest scan for 5m ORB scalping."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any, Dict, Literal, Optional, Tuple
from zoneinfo import ZoneInfo

import pandas as pd


Direction = Literal["long", "short", "none"]
SessionState = Literal[
    "waiting_or",
    "waiting_breakout",
    "waiting_retest",
    "signal",
    "invalid",
]


@dataclass
class OpeningRangeCandle:
    or_high: float
    or_low: float
    or_mid: float
    or_idx: int
    or_candle_ts: str
    session_date: str


@dataclass
class OrbScanResult:
    session_state: SessionState
    direction: Direction
    or_high: Optional[float]
    or_low: Optional[float]
    or_mid: Optional[float]
    breakout_valid: bool
    retest_valid: bool
    breakout_reason: str
    retest_reason: str
    rejection_reason: str
    entry_price: Optional[float]
    stop_hint: Optional[float]
    target_hint: Optional[float]
    reward_risk: Optional[float]
    or_candle_ts: Optional[str] = None
    breakout_candle_ts: Optional[str] = None
    retest_candle_ts: Optional[str] = None
    breakout_idx: Optional[int] = None
    retest_idx: Optional[int] = None
    fire_signal: bool = False


def _parse_time(value: str) -> time:
    parts = str(value).strip().split(":")
    hour = int(parts[0])
    minute = int(parts[1]) if len(parts) > 1 else 0
    return time(hour, minute)


def _to_utc_ts(ts: Any) -> pd.Timestamp:
    stamp = pd.Timestamp(ts)
    if stamp.tzinfo is None:
        return stamp.tz_localize("UTC")
    return stamp.tz_convert("UTC")


def _ts_iso(ts: Any) -> str:
    if hasattr(ts, "isoformat"):
        return ts.isoformat()
    return str(ts)


def _bar_open_et(ts: Any, tz: ZoneInfo) -> datetime:
    return _to_utc_ts(ts).tz_convert(tz).to_pydatetime().replace(tzinfo=None)


def resolve_session_date(
    now: Optional[datetime] = None,
    *,
    session_timezone: str = "America/New_York",
    or_open_time: str = "09:30",
) -> str:
    """Return YYYY-MM-DD for the active OR session in ET."""
    tz = ZoneInfo(session_timezone)
    or_open = _parse_time(or_open_time)
    if now is None:
        now_et = datetime.now(tz)
    else:
        if now.tzinfo is None:
            now_et = now.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
        else:
            now_et = now.astimezone(tz)
    session_day = now_et.date()
    if now_et.time() < or_open:
        session_day = session_day - timedelta(days=1)
    return session_day.isoformat()


def find_session_or_candle(
    df: pd.DataFrame,
    *,
    session_timezone: str = "America/New_York",
    or_open_time: str = "09:30",
    session_date: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Optional[OpeningRangeCandle]:
    """Locate the 5m bar opening at or_open_time ET for session_date."""
    if df is None or df.empty:
        return None

    tz = ZoneInfo(session_timezone)
    target_open = _parse_time(or_open_time)
    target_date = session_date or resolve_session_date(
        now, session_timezone=session_timezone, or_open_time=or_open_time
    )

    for idx in range(len(df)):
        bar_open = _bar_open_et(df.index[idx], tz)
        if bar_open.date().isoformat() != target_date:
            continue
        if bar_open.time().hour == target_open.hour and bar_open.time().minute == target_open.minute:
            high = float(df["high"].iloc[idx])
            low = float(df["low"].iloc[idx])
            return OpeningRangeCandle(
                or_high=high,
                or_low=low,
                or_mid=(high + low) / 2.0,
                or_idx=idx,
                or_candle_ts=_ts_iso(df.index[idx]),
                session_date=target_date,
            )
    return None


def _is_past_watch_end(
    bar_ts: Any,
    *,
    session_timezone: str,
    entry_watch_end: str,
) -> bool:
    tz = ZoneInfo(session_timezone)
    end_t = _parse_time(entry_watch_end)
    bar_et = _to_utc_ts(bar_ts).tz_convert(tz)
    return (bar_et.hour, bar_et.minute) > (end_t.hour, end_t.minute) or (
        bar_et.hour == end_t.hour and bar_et.minute > end_t.minute
    )


def _compute_risk_levels(
    *,
    direction: Direction,
    entry: float,
    or_mid: float,
    target_reward_risk: float,
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    if direction == "long":
        stop = or_mid
        if stop >= entry:
            return None, None, None
        risk = entry - stop
        target = entry + target_reward_risk * risk
        return stop, target, target_reward_risk
    if direction == "short":
        stop = or_mid
        if stop <= entry:
            return None, None, None
        risk = stop - entry
        target = entry - target_reward_risk * risk
        return stop, target, target_reward_risk
    return None, None, None


def scan_orb_retest_fsm(
    df: pd.DataFrame,
    or_candle: OpeningRangeCandle,
    current_idx: int,
    *,
    session_timezone: str = "America/New_York",
    or_close_time: str = "09:35",
    entry_watch_end: str = "16:00",
    target_reward_risk: float = 2.0,
    now: Optional[datetime] = None,
) -> OrbScanResult:
    """Stateless forward scan from OR close through current_idx."""
    or_high = or_candle.or_high
    or_low = or_candle.or_low
    or_mid = or_candle.or_mid
    or_idx = or_candle.or_idx

    base = dict(
        session_state="waiting_breakout",
        direction="none",
        or_high=or_high,
        or_low=or_low,
        or_mid=or_mid,
        breakout_valid=False,
        retest_valid=False,
        breakout_reason="",
        retest_reason="",
        rejection_reason="",
        entry_price=None,
        stop_hint=None,
        target_hint=None,
        reward_risk=None,
        or_candle_ts=or_candle.or_candle_ts,
        breakout_candle_ts=None,
        retest_candle_ts=None,
        breakout_idx=None,
        retest_idx=None,
        fire_signal=False,
    )

    tz = ZoneInfo(session_timezone)
    or_close = _parse_time(or_close_time)
    if now is None:
        now_et = datetime.now(tz)
    else:
        now_et = now.astimezone(tz) if now.tzinfo else now.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)

    if current_idx <= or_idx and now_et.time() < or_close:
        return OrbScanResult(session_state="waiting_or", **base)

    if current_idx <= or_idx:
        return OrbScanResult(session_state="waiting_breakout", **base)

    direction: Direction = "none"
    breakout_idx: Optional[int] = None
    last_retest_reason = ""

    for j in range(or_idx + 1, current_idx + 1):
        if _is_past_watch_end(
            df.index[j],
            session_timezone=session_timezone,
            entry_watch_end=entry_watch_end,
        ):
            if breakout_idx is None:
                return OrbScanResult(
                    session_state="invalid",
                    direction="none",
                    or_high=or_high,
                    or_low=or_low,
                    or_mid=or_mid,
                    breakout_valid=False,
                    retest_valid=False,
                    breakout_reason="",
                    retest_reason="",
                    rejection_reason="session_expired_no_breakout",
                    entry_price=None,
                    stop_hint=None,
                    target_hint=None,
                    reward_risk=None,
                    or_candle_ts=or_candle.or_candle_ts,
                )
            return OrbScanResult(
                session_state="invalid",
                direction=direction,
                or_high=or_high,
                or_low=or_low,
                or_mid=or_mid,
                breakout_valid=True,
                retest_valid=False,
                breakout_reason=base.get("breakout_reason", ""),
                retest_reason="",
                rejection_reason="session_expired_no_retest",
                entry_price=None,
                stop_hint=None,
                target_hint=None,
                reward_risk=None,
                or_candle_ts=or_candle.or_candle_ts,
                breakout_idx=breakout_idx,
                breakout_candle_ts=base.get("breakout_candle_ts"),
            )

        o = float(df["open"].iloc[j])
        h = float(df["high"].iloc[j])
        l = float(df["low"].iloc[j])
        c = float(df["close"].iloc[j])

        if breakout_idx is None:
            if c > or_high:
                direction = "long"
                breakout_idx = j
                base["breakout_valid"] = True
                base["breakout_reason"] = "body_close_above_or_high"
                base["breakout_candle_ts"] = _ts_iso(df.index[j])
                base["breakout_idx"] = j
                base["session_state"] = "waiting_retest"
                base["direction"] = direction
                continue
            if c < or_low:
                direction = "short"
                breakout_idx = j
                base["breakout_valid"] = True
                base["breakout_reason"] = "body_close_below_or_low"
                base["breakout_candle_ts"] = _ts_iso(df.index[j])
                base["breakout_idx"] = j
                base["session_state"] = "waiting_retest"
                base["direction"] = direction
                continue
            base["session_state"] = "waiting_breakout"
            continue

        if direction == "long" and c < or_low:
            return OrbScanResult(
                session_state="invalid",
                direction="long",
                rejection_reason="opposite_breakout",
                breakout_valid=True,
                breakout_reason=base["breakout_reason"],
                breakout_idx=breakout_idx,
                breakout_candle_ts=base["breakout_candle_ts"],
                or_high=or_high,
                or_low=or_low,
                or_mid=or_mid,
                retest_valid=False,
                retest_reason="",
                entry_price=None,
                stop_hint=None,
                target_hint=None,
                reward_risk=None,
                or_candle_ts=or_candle.or_candle_ts,
            )
        if direction == "short" and c > or_high:
            return OrbScanResult(
                session_state="invalid",
                direction="short",
                rejection_reason="opposite_breakout",
                breakout_valid=True,
                breakout_reason=base["breakout_reason"],
                breakout_idx=breakout_idx,
                breakout_candle_ts=base["breakout_candle_ts"],
                or_high=or_high,
                or_low=or_low,
                or_mid=or_mid,
                retest_valid=False,
                retest_reason="",
                entry_price=None,
                stop_hint=None,
                target_hint=None,
                reward_risk=None,
                or_candle_ts=or_candle.or_candle_ts,
            )

        if direction == "long":
            touched = l <= or_high
            if touched and or_low <= c <= or_high:
                last_retest_reason = "retest_closed_inside_range"
                base["session_state"] = "waiting_retest"
                base["retest_valid"] = False
                base["retest_reason"] = last_retest_reason
                continue
            if touched and c > or_high:
                entry = c
                stop, target, rr = _compute_risk_levels(
                    direction="long",
                    entry=entry,
                    or_mid=or_mid,
                    target_reward_risk=target_reward_risk,
                )
                if stop is None:
                    return OrbScanResult(
                        session_state="invalid",
                        direction="long",
                        rejection_reason="invalid_long_stop",
                        breakout_valid=True,
                        breakout_reason=base["breakout_reason"],
                        or_high=or_high,
                        or_low=or_low,
                        or_mid=or_mid,
                        retest_valid=False,
                        retest_reason="",
                        entry_price=entry,
                        stop_hint=None,
                        target_hint=None,
                        reward_risk=None,
                        or_candle_ts=or_candle.or_candle_ts,
                        breakout_idx=breakout_idx,
                        breakout_candle_ts=base["breakout_candle_ts"],
                    )
                if j == current_idx:
                    return OrbScanResult(
                        session_state="signal",
                        direction="long",
                        breakout_valid=True,
                        retest_valid=True,
                        breakout_reason=base["breakout_reason"],
                        retest_reason="retest_held_or_high_close_above",
                        rejection_reason="",
                        entry_price=entry,
                        stop_hint=stop,
                        target_hint=target,
                        reward_risk=rr,
                        or_high=or_high,
                        or_low=or_low,
                        or_mid=or_mid,
                        or_candle_ts=or_candle.or_candle_ts,
                        breakout_idx=breakout_idx,
                        breakout_candle_ts=base["breakout_candle_ts"],
                        retest_idx=j,
                        retest_candle_ts=_ts_iso(df.index[j]),
                        fire_signal=True,
                    )
                return OrbScanResult(
                    session_state="signal",
                    direction="long",
                    rejection_reason="session_entry_taken",
                    breakout_valid=True,
                    retest_valid=True,
                    breakout_reason=base["breakout_reason"],
                    retest_reason="retest_held_or_high_close_above",
                    entry_price=entry,
                    stop_hint=stop,
                    target_hint=target,
                    reward_risk=rr,
                    or_high=or_high,
                    or_low=or_low,
                    or_mid=or_mid,
                    or_candle_ts=or_candle.or_candle_ts,
                    breakout_idx=breakout_idx,
                    breakout_candle_ts=base["breakout_candle_ts"],
                    retest_idx=j,
                    retest_candle_ts=_ts_iso(df.index[j]),
                    fire_signal=False,
                )

        if direction == "short":
            touched = h >= or_low
            if touched and or_low <= c <= or_high:
                last_retest_reason = "retest_closed_inside_range"
                base["session_state"] = "waiting_retest"
                base["retest_valid"] = False
                base["retest_reason"] = last_retest_reason
                continue
            if touched and c < or_low:
                entry = c
                stop, target, rr = _compute_risk_levels(
                    direction="short",
                    entry=entry,
                    or_mid=or_mid,
                    target_reward_risk=target_reward_risk,
                )
                if stop is None:
                    return OrbScanResult(
                        session_state="invalid",
                        direction="short",
                        rejection_reason="invalid_short_stop",
                        breakout_valid=True,
                        breakout_reason=base["breakout_reason"],
                        or_high=or_high,
                        or_low=or_low,
                        or_mid=or_mid,
                        retest_valid=False,
                        retest_reason="",
                        entry_price=entry,
                        stop_hint=None,
                        target_hint=None,
                        reward_risk=None,
                        or_candle_ts=or_candle.or_candle_ts,
                        breakout_idx=breakout_idx,
                        breakout_candle_ts=base["breakout_candle_ts"],
                    )
                if j == current_idx:
                    return OrbScanResult(
                        session_state="signal",
                        direction="short",
                        breakout_valid=True,
                        retest_valid=True,
                        breakout_reason=base["breakout_reason"],
                        retest_reason="retest_held_or_low_close_below",
                        rejection_reason="",
                        entry_price=entry,
                        stop_hint=stop,
                        target_hint=target,
                        reward_risk=rr,
                        or_high=or_high,
                        or_low=or_low,
                        or_mid=or_mid,
                        or_candle_ts=or_candle.or_candle_ts,
                        breakout_idx=breakout_idx,
                        breakout_candle_ts=base["breakout_candle_ts"],
                        retest_idx=j,
                        retest_candle_ts=_ts_iso(df.index[j]),
                        fire_signal=True,
                    )
                return OrbScanResult(
                    session_state="signal",
                    direction="short",
                    rejection_reason="session_entry_taken",
                    breakout_valid=True,
                    retest_valid=True,
                    breakout_reason=base["breakout_reason"],
                    retest_reason="retest_held_or_low_close_below",
                    entry_price=entry,
                    stop_hint=stop,
                    target_hint=target,
                    reward_risk=rr,
                    or_high=or_high,
                    or_low=or_low,
                    or_mid=or_mid,
                    or_candle_ts=or_candle.or_candle_ts,
                    breakout_idx=breakout_idx,
                    breakout_candle_ts=base["breakout_candle_ts"],
                    retest_idx=j,
                    retest_candle_ts=_ts_iso(df.index[j]),
                    fire_signal=False,
                )

        base["session_state"] = "waiting_retest"
        base["direction"] = direction

    return OrbScanResult(
        session_state=base["session_state"],
        direction=direction if direction != "none" else "none",
        or_high=or_high,
        or_low=or_low,
        or_mid=or_mid,
        breakout_valid=base["breakout_valid"],
        retest_valid=base.get("retest_valid", False),
        breakout_reason=base.get("breakout_reason", ""),
        retest_reason=base.get("retest_reason", "") or last_retest_reason,
        rejection_reason="",
        entry_price=None,
        stop_hint=None,
        target_hint=None,
        reward_risk=None,
        or_candle_ts=or_candle.or_candle_ts,
        breakout_idx=breakout_idx,
        breakout_candle_ts=base.get("breakout_candle_ts"),
    )
