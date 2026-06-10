"""
ORB 5m Scalp (Casper SMC) — Opening Range Breakout + Retest on 5m only.

US Eastern opening range 9:30–9:35 AM; body breakout then retest entry at close.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd

from strategy.indicators.opening_range_5m import (
    find_session_or_candle,
    resolve_session_date,
    scan_orb_retest_fsm,
)
from strategy.playbooks.ohlcv_closed_bar import prepare_closed_ohlcv


@dataclass
class EngineParams:
    entry_timeframe: str = "5m"
    session_timezone: str = "America/New_York"
    or_open_time: str = "09:30"
    or_close_time: str = "09:35"
    entry_watch_end: str = "16:00"
    block_weekends: bool = False
    target_reward_risk: float = 2.0
    min_candles: int = 80
    allow_long: bool = True
    allow_short: bool = True
    buy_confidence: float = 0.74
    buy_strength: float = 0.72
    sell_confidence: float = 0.74
    sell_strength: float = 0.72
    blocked_regimes: List[str] = field(default_factory=lambda: ["low_volatility"])


@dataclass
class EngineResult:
    signal: str
    confidence: float
    strength: float
    indicators: Dict[str, Any]
    invalidation_reason: str = ""


def params_from_config(config: Dict[str, Any]) -> EngineParams:
    p = dict(config.get("parameters") or {}) if isinstance(config, dict) else {}
    kw = {k: v for k, v in p.items() if k in EngineParams.__dataclass_fields__}
    base = EngineParams()
    for key, val in kw.items():
        setattr(base, key, val)
    return base


def _df(market_data: Any, key: str) -> Optional[pd.DataFrame]:
    if isinstance(market_data, dict):
        df = market_data.get(key)
        return df if isinstance(df, pd.DataFrame) and not df.empty else None
    return market_data if isinstance(market_data, pd.DataFrame) and not market_data.empty else None


def _hold_payload(reason: str, extra: Optional[Dict[str, Any]] = None) -> EngineResult:
    payload: Dict[str, Any] = {
        "invalidation_reason": reason,
        "skip_reason": reason,
        "rejection_reason": reason,
        "entry_reason": "",
        "setup": "orb_5m_scalp",
    }
    if extra:
        payload.update(extra)
    return EngineResult("hold", 0.0, 0.0, payload, reason)


def _base_indicators(params: EngineParams) -> Dict[str, Any]:
    return {
        "setup": "orb_5m_scalp",
        "entry_timeframe": params.entry_timeframe,
        "session_timezone": params.session_timezone,
        "direction": "none",
        "session_state": "waiting_or",
        "or_high": None,
        "or_low": None,
        "or_mid": None,
        "breakout_valid": False,
        "retest_valid": False,
        "breakout_reason": "",
        "retest_reason": "",
        "rejection_reason": "",
        "entry_price": None,
        "stop_hint": None,
        "target_hint": None,
        "reward_risk": None,
        "entry_reason": "",
    }


def _build_entry_reason(
    *,
    side: str,
    or_high: float,
    or_low: float,
    or_mid: float,
    entry: float,
    stop: float,
    target: float,
    rr: float,
    breakout_reason: str,
    retest_reason: str,
) -> str:
    level = or_high if side == "long" else or_low
    level_label = "or_high" if side == "long" else "or_low"
    return (
        f"ORB 5m {side.upper()}: body breakout ({breakout_reason}) beyond OR "
        f"[{or_low:.6f}, {or_high:.6f}]; retest held {level_label} {level:.6f} "
        f"({retest_reason}); entry close {entry:.6f}; SL or_mid {stop:.6f}; "
        f"TP {target:.6f}; R:R {rr:.2f}"
    )


def evaluate_orb_5m_scalp(
    market_data: Dict[str, pd.DataFrame],
    params: EngineParams,
    *,
    market_regime: str = "unknown",
    allow_short: bool = True,
    asset_class: Optional[str] = None,
    now: Optional[datetime] = None,
) -> EngineResult:
    """Run ORB 5m breakout+retest pipeline on closed 5m bars."""
    base = _base_indicators(params)
    if asset_class:
        base["asset_class"] = asset_class

    regime_l = str(market_regime or "unknown").strip().lower()
    blocked = {str(x).strip().lower() for x in (params.blocked_regimes or [])}
    if regime_l in blocked:
        return _hold_payload(f"regime_blocked:{regime_l}", {**base, "session_state": "invalid"})

    if params.block_weekends:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(params.session_timezone)
        now_et = (now or datetime.utcnow()).astimezone(tz) if now and now.tzinfo else datetime.now(tz)
        if now_et.weekday() >= 5:
            return _hold_payload("session_blocked_weekend", {**base, "session_state": "invalid"})

    tf = str(params.entry_timeframe or "5m").lower()
    raw = _df(market_data, tf)
    if raw is None:
        return _hold_payload(f"missing_timeframe:{tf}", base)

    df = prepare_closed_ohlcv(raw, tf)
    if len(df) < params.min_candles:
        return _hold_payload(
            f"insufficient_candles:{len(df)}<{params.min_candles}",
            {**base, "candle_count": len(df)},
        )

    current_idx = len(df) - 1
    base["candle_ts"] = (
        df.index[current_idx].isoformat()
        if hasattr(df.index[current_idx], "isoformat")
        else str(df.index[current_idx])
    )

    session_date = resolve_session_date(
        now,
        session_timezone=params.session_timezone,
        or_open_time=params.or_open_time,
    )
    base["session_date"] = session_date

    or_candle = find_session_or_candle(
        df,
        session_timezone=params.session_timezone,
        or_open_time=params.or_open_time,
        session_date=session_date,
        now=now,
    )
    if or_candle is None:
        return _hold_payload(
            "or_candle_missing",
            {**base, "session_state": "invalid", "rejection_reason": "or_candle_missing"},
        )

    scan = scan_orb_retest_fsm(
        df,
        or_candle,
        current_idx,
        session_timezone=params.session_timezone,
        or_close_time=params.or_close_time,
        entry_watch_end=params.entry_watch_end,
        target_reward_risk=params.target_reward_risk,
        now=now,
    )

    base.update(
        {
            "session_state": scan.session_state,
            "direction": scan.direction,
            "or_high": scan.or_high,
            "or_low": scan.or_low,
            "or_mid": scan.or_mid,
            "breakout_valid": scan.breakout_valid,
            "retest_valid": scan.retest_valid,
            "breakout_reason": scan.breakout_reason,
            "retest_reason": scan.retest_reason,
            "rejection_reason": scan.rejection_reason,
            "entry_price": scan.entry_price,
            "stop_hint": scan.stop_hint,
            "target_hint": scan.target_hint,
            "reward_risk": scan.reward_risk,
            "or_candle_ts": scan.or_candle_ts,
            "breakout_candle_ts": scan.breakout_candle_ts,
            "retest_candle_ts": scan.retest_candle_ts,
        }
    )

    if scan.session_state == "invalid":
        reason = scan.rejection_reason or "invalid"
        return _hold_payload(reason, base)

    if scan.session_state == "signal" and not scan.fire_signal:
        reason = scan.rejection_reason or "session_entry_taken"
        return _hold_payload(reason, base)

    if not scan.fire_signal:
        reason = scan.rejection_reason or scan.session_state
        return _hold_payload(reason or scan.session_state, base)

    side = scan.direction
    if side == "long" and not params.allow_long:
        return _hold_payload("long_disabled", base)
    if side == "short" and (not allow_short or not params.allow_short):
        return _hold_payload("short_disabled", base)

    entry = float(scan.entry_price or 0)
    stop = float(scan.stop_hint or 0)
    target = float(scan.target_hint or 0)
    rr = float(scan.reward_risk or params.target_reward_risk)

    base["entry_reason"] = _build_entry_reason(
        side=side,
        or_high=float(scan.or_high or 0),
        or_low=float(scan.or_low or 0),
        or_mid=float(scan.or_mid or 0),
        entry=entry,
        stop=stop,
        target=target,
        rr=rr,
        breakout_reason=scan.breakout_reason,
        retest_reason=scan.retest_reason,
    )

    if side == "long":
        return EngineResult("buy", params.buy_confidence, params.buy_strength, base, "none")
    return EngineResult("sell", params.sell_confidence, params.sell_strength, base, "none")
