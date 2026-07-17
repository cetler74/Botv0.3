"""Heikin-Ashi 1m pullback/doji scalper.

Signal candles are Heikin-Ashi. Executable entry, stop, and target hints use
raw OHLC prices because Heikin-Ashi body prices are synthetic averages.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from strategy.playbooks.ohlcv_closed_bar import prepare_closed_ohlcv


@dataclass
class EngineParams:
    entry_timeframe: str = "1m"
    ema_period: int = 100
    min_candles: int = 130
    session_timezone: str = "America/New_York"
    session_start: str = "10:00"
    session_end: str = "12:00"
    block_outside_session: bool = True
    block_weekends: bool = False
    min_pullback_candles: int = 2
    flat_wick_tolerance_pct: float = 0.0002
    doji_body_max_pct: float = 0.25
    doji_wick_min_pct: float = 0.20
    high_range_lookback: int = 2
    ema_chop_buffer_pct: float = 0.0002
    max_recent_ema_crosses: int = 1
    ema_chop_lookback: int = 8
    target_reward_risk: float = 1.0
    stop_buffer_pct: float = 0.0
    min_stop_pct: float = 0.0005
    max_stop_pct: float = 0.02
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
    params = EngineParams()
    for key, value in p.items():
        if key in EngineParams.__dataclass_fields__:
            setattr(params, key, value)
    params.ema_period = int(params.ema_period)
    params.min_candles = int(params.min_candles)
    params.min_pullback_candles = int(params.min_pullback_candles)
    params.high_range_lookback = int(params.high_range_lookback)
    params.ema_chop_lookback = int(params.ema_chop_lookback)
    params.max_recent_ema_crosses = int(params.max_recent_ema_crosses)
    return params


def compute_heikin_ashi(df: pd.DataFrame) -> pd.DataFrame:
    required = {"open", "high", "low", "close"}
    if not required.issubset(df.columns):
        missing = ", ".join(sorted(required - set(df.columns)))
        raise ValueError(f"missing OHLC columns: {missing}")

    raw = df.loc[:, ["open", "high", "low", "close"]].astype(float)
    ha_close = raw.mean(axis=1)
    ha_open = np.zeros(len(raw), dtype=float)
    if len(raw):
        ha_open[0] = (float(raw["open"].iloc[0]) + float(raw["close"].iloc[0])) / 2.0
    for idx in range(1, len(raw)):
        ha_open[idx] = (ha_open[idx - 1] + float(ha_close.iloc[idx - 1])) / 2.0
    out = pd.DataFrame(index=df.index)
    out["ha_open"] = ha_open
    out["ha_close"] = ha_close
    out["ha_high"] = pd.concat(
        [raw["high"], out["ha_open"], out["ha_close"]], axis=1
    ).max(axis=1)
    out["ha_low"] = pd.concat(
        [raw["low"], out["ha_open"], out["ha_close"]], axis=1
    ).min(axis=1)
    out["ha_range"] = (out["ha_high"] - out["ha_low"]).clip(lower=0.0)
    out["ha_body"] = (out["ha_close"] - out["ha_open"]).abs()
    out["ha_upper_wick"] = out["ha_high"] - out[["ha_open", "ha_close"]].max(axis=1)
    out["ha_lower_wick"] = out[["ha_open", "ha_close"]].min(axis=1) - out["ha_low"]
    return out


def _df(market_data: Any, key: str) -> Optional[pd.DataFrame]:
    if isinstance(market_data, dict):
        df = market_data.get(key)
        return df if isinstance(df, pd.DataFrame) and not df.empty else None
    return market_data if isinstance(market_data, pd.DataFrame) and not market_data.empty else None


def _hold(reason: str, extra: Optional[Dict[str, Any]] = None) -> EngineResult:
    payload: Dict[str, Any] = {
        "setup": "heikin_ashi_1m_scalper",
        "invalidation_reason": reason,
        "skip_reason": reason,
        "rejection_reason": reason,
        "entry_reason": "",
        "direction": "none",
    }
    if extra:
        payload.update(extra)
    return EngineResult("hold", 0.0, 0.0, payload, reason)


def _base_indicators(params: EngineParams) -> Dict[str, Any]:
    return {
        "setup": "heikin_ashi_1m_scalper",
        "entry_timeframe": params.entry_timeframe,
        "ema_period": params.ema_period,
        "session_timezone": params.session_timezone,
        "session_start": params.session_start,
        "session_end": params.session_end,
        "direction": "none",
        "entry_price": None,
        "stop_hint": None,
        "target_hint": None,
        "reward_risk": params.target_reward_risk,
        "pullback_count": 0,
        "doji_valid": False,
        "high_range_doji": False,
        "entry_reason": "",
    }


def _in_session(ts: pd.Timestamp, params: EngineParams, now: Optional[datetime]) -> bool:
    tz = ZoneInfo(params.session_timezone)
    if now is not None:
        local = pd.Timestamp(now)
        local = local.tz_localize("UTC") if local.tzinfo is None else local.tz_convert("UTC")
        local = local.tz_convert(tz)
    else:
        local = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert(tz)
    if params.block_weekends and local.weekday() >= 5:
        return False
    start_h, start_m = [int(part) for part in str(params.session_start).split(":", 1)]
    end_h, end_m = [int(part) for part in str(params.session_end).split(":", 1)]
    start = local.replace(hour=start_h, minute=start_m, second=0, microsecond=0)
    end = local.replace(hour=end_h, minute=end_m, second=0, microsecond=0)
    return start <= local <= end


def _ema(series: pd.Series, length: int) -> pd.Series:
    return series.astype(float).ewm(span=length, adjust=False, min_periods=length).mean()


def _flat_top(row: pd.Series, tolerance_pct: float) -> bool:
    rng = max(float(row["ha_range"]), 1e-12)
    return float(row["ha_upper_wick"]) / rng <= tolerance_pct


def _flat_bottom(row: pd.Series, tolerance_pct: float) -> bool:
    rng = max(float(row["ha_range"]), 1e-12)
    return float(row["ha_lower_wick"]) / rng <= tolerance_pct


def _is_doji(row: pd.Series, params: EngineParams) -> bool:
    rng = float(row["ha_range"])
    if rng <= 0:
        return False
    return (
        float(row["ha_body"]) / rng <= params.doji_body_max_pct
        and float(row["ha_upper_wick"]) / rng >= params.doji_wick_min_pct
        and float(row["ha_lower_wick"]) / rng >= params.doji_wick_min_pct
    )


def _high_range_doji(ha: pd.DataFrame, idx: int, params: EngineParams) -> bool:
    if idx < 1:
        return False
    start = max(0, idx - params.high_range_lookback)
    prior = ha["ha_range"].iloc[start:idx]
    if prior.empty:
        return False
    return bool((float(ha["ha_range"].iloc[idx]) > prior.astype(float)).any())


def _count_pullback(ha: pd.DataFrame, *, side: str, end_idx: int, params: EngineParams) -> int:
    count = 0
    idx = end_idx
    while idx >= 0:
        row = ha.iloc[idx]
        if side == "long":
            valid = (
                float(row["ha_close"]) < float(row["ha_open"])
                and _flat_top(row, params.flat_wick_tolerance_pct)
            )
        else:
            valid = (
                float(row["ha_close"]) > float(row["ha_open"])
                and _flat_bottom(row, params.flat_wick_tolerance_pct)
            )
        if not valid:
            break
        count += 1
        idx -= 1
    return count


def _recent_ema_crosses(close: pd.Series, ema: pd.Series, params: EngineParams) -> int:
    lookback = max(2, params.ema_chop_lookback)
    c = close.iloc[-lookback:].astype(float)
    e = ema.iloc[-lookback:].astype(float)
    signs = np.sign(c.to_numpy() - e.to_numpy())
    signs = signs[signs != 0]
    if len(signs) < 2:
        return 0
    return int(np.sum(signs[1:] != signs[:-1]))


def evaluate_heikin_ashi_1m_scalper(
    market_data: Dict[str, pd.DataFrame],
    params: EngineParams,
    *,
    market_regime: str = "unknown",
    now: Optional[datetime] = None,
) -> EngineResult:
    base = _base_indicators(params)
    regime_l = str(market_regime or "unknown").strip().lower()
    blocked = {str(x).strip().lower() for x in (params.blocked_regimes or [])}
    if regime_l in blocked:
        return _hold(f"regime_blocked:{regime_l}", base)

    tf = str(params.entry_timeframe or "1m").lower()
    raw = _df(market_data, tf)
    if raw is None:
        return _hold(f"missing_timeframe:{tf}", base)

    df = prepare_closed_ohlcv(raw, tf).copy()
    if len(df) < params.min_candles:
        return _hold(f"insufficient_candles:{len(df)}<{params.min_candles}", base)

    required = {"open", "high", "low", "close", "volume"}
    if not required.issubset(df.columns):
        return _hold("missing_ohlcv_columns", base)
    df = df.loc[:, ["open", "high", "low", "close", "volume"]].apply(pd.to_numeric, errors="coerce")
    if not np.isfinite(df.to_numpy(dtype=float)).all():
        return _hold("non_finite_ohlcv", base)

    ts = pd.Timestamp(df.index[-1])
    base["candle_ts"] = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
    if params.block_outside_session and not _in_session(ts, params, now):
        return _hold("outside_session", base)

    close = df["close"].astype(float)
    ema = _ema(close, params.ema_period)
    if pd.isna(ema.iloc[-1]):
        return _hold("ema_not_ready", base)

    ha = compute_heikin_ashi(df)
    idx = len(df) - 1
    entry = float(close.iloc[-1])
    ema_now = float(ema.iloc[-1])
    dist_pct = abs(entry - ema_now) / max(abs(ema_now), 1e-12)
    crosses = _recent_ema_crosses(close, ema, params)
    base.update(
        {
            "entry_price": entry,
            "ema100": ema_now,
            "ema_distance_pct": dist_pct,
            "recent_ema_crosses": crosses,
            "ha_doji_range": float(ha["ha_range"].iloc[-1]),
            "ha_doji_body_pct": float(ha["ha_body"].iloc[-1] / max(ha["ha_range"].iloc[-1], 1e-12)),
        }
    )

    if dist_pct <= params.ema_chop_buffer_pct or crosses > params.max_recent_ema_crosses:
        return _hold("ema_chop_zone", base)

    doji_valid = _is_doji(ha.iloc[idx], params)
    high_range = _high_range_doji(ha, idx, params)
    base["doji_valid"] = doji_valid
    base["high_range_doji"] = high_range
    if not doji_valid:
        return _hold("missing_doji", base)
    if not high_range:
        return _hold("doji_not_high_range", base)

    if params.allow_long and entry > ema_now:
        pullback_count = _count_pullback(ha, side="long", end_idx=idx - 1, params=params)
        base["pullback_count"] = pullback_count
        if pullback_count >= params.min_pullback_candles:
            stop = float(df["low"].iloc[-1]) * (1.0 - params.stop_buffer_pct)
            risk = entry - stop
            risk_pct = risk / max(entry, 1e-12)
            if risk <= 0:
                return _hold("invalid_long_stop", base)
            if risk_pct < params.min_stop_pct or risk_pct > params.max_stop_pct:
                return _hold("long_stop_size_out_of_bounds", {**base, "risk_pct": risk_pct})
            target = entry + risk * params.target_reward_risk
            reason = (
                f"Heikin-Ashi 1m LONG: close above EMA{params.ema_period}; "
                f"{pullback_count} flat-top bearish pullback candles; high-range doji; "
                f"entry {entry:.8f}; SL {stop:.8f}; TP {target:.8f}; R:R {params.target_reward_risk:.2f}"
            )
            payload = {
                **base,
                "direction": "long",
                "stop_hint": stop,
                "target_hint": target,
                "risk_pct": risk_pct,
                "entry_reason": reason,
            }
            return EngineResult("buy", params.buy_confidence, params.buy_strength, payload)

    if params.allow_short and entry < ema_now:
        pullback_count = _count_pullback(ha, side="short", end_idx=idx - 1, params=params)
        base["pullback_count"] = pullback_count
        if pullback_count >= params.min_pullback_candles:
            stop = float(df["high"].iloc[-1]) * (1.0 + params.stop_buffer_pct)
            risk = stop - entry
            risk_pct = risk / max(entry, 1e-12)
            if risk <= 0:
                return _hold("invalid_short_stop", base)
            if risk_pct < params.min_stop_pct or risk_pct > params.max_stop_pct:
                return _hold("short_stop_size_out_of_bounds", {**base, "risk_pct": risk_pct})
            target = entry - risk * params.target_reward_risk
            reason = (
                f"Heikin-Ashi 1m SHORT: close below EMA{params.ema_period}; "
                f"{pullback_count} flat-bottom bullish pullback candles; high-range doji; "
                f"entry {entry:.8f}; SL {stop:.8f}; TP {target:.8f}; R:R {params.target_reward_risk:.2f}"
            )
            payload = {
                **base,
                "direction": "short",
                "stop_hint": stop,
                "target_hint": target,
                "risk_pct": risk_pct,
                "entry_reason": reason,
            }
            return EngineResult("sell", params.sell_confidence, params.sell_strength, payload)

    return _hold("missing_clean_pullback", base)
