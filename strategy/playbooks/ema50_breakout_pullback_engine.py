"""
EMA50 Breakout Pullback — shared engine for spot and HL perps (4H single-TF).

Data Trader "50 EMA Breakout Pullback" playbook: EMA50 cross, pullback, swing break,
Chandelier Stop SL, fixed 2R TP.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

from strategy.indicators.chandelier_stop import compute_chandelier_stop
from strategy.indicators.ema50_breakout import (
    compute_ema,
    find_setup_context,
    rolling_candle_range,
)
from strategy.playbooks.ohlcv_closed_bar import prepare_closed_ohlcv


@dataclass
class EngineParams:
    timeframe_mode: str = "single"
    primary_timeframe: str = "4h"
    ema_period: int = 50
    predominant_lookback_bars: int = 12
    predominant_side_pct: float = 0.60
    min_pullback_candles: int = 2
    chandelier_period: int = 22
    chandelier_atr_mult: float = 3.0
    target_reward_risk: float = 2.0
    max_candle_size_mult: float = 4.0
    range_avg_window: int = 20
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
    invalidation_reason: str


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
        "entry_reason": "",
        "setup": "ema50_breakout_pullback",
    }
    if extra:
        payload.update(extra)
    return EngineResult("hold", 0.0, 0.0, payload, reason)


def _base_indicators(params: EngineParams) -> Dict[str, Any]:
    return {
        "setup": "ema50_breakout_pullback",
        "timeframe_mode": params.timeframe_mode,
        "primary_timeframe": params.primary_timeframe,
        "direction": "none",
        "setup_state": "waiting_breakout",
        "ema50_side": "unknown",
        "breakout_pass": False,
        "pullback_pass": False,
        "trigger_pass": False,
        "breakout_reason": "",
        "pullback_reason": "",
        "trigger_reason": "",
    }


def _ema50_side(close: float, ema_val: float) -> str:
    return "above" if close >= ema_val else "below"


def _build_entry_reason(
    *,
    side: str,
    tf: str,
    predominant_pct: Optional[float],
    lookback: int,
    breakout_reason: str,
    pullback_reason: str,
    swing_level: float,
    entry: float,
    ema_val: float,
    stop: float,
    target: float,
    rr: float,
    chandelier_period: int,
    chandelier_mult: float,
) -> str:
    pct_txt = f"{predominant_pct * 100:.0f}%" if predominant_pct is not None else "n/a"
    swing_label = "swing_high" if side == "long" else "swing_low"
    return (
        f"EMA50 Breakout Pullback {side.upper()} ({tf}): predominant "
        f"{'below' if side == 'long' else 'above'} EMA50 ({pct_txt} of {lookback} bars); "
        f"{breakout_reason}; {pullback_reason}; {swing_label} {swing_level:.6f}; "
        f"entry body close {entry:.6f} (EMA50 {ema_val:.6f}); "
        f"Chandelier SL {stop:.6f} (ATR{chandelier_period}×{chandelier_mult:g}); "
        f"TP {target:.6f}; R:R {rr:.2f}"
    )


def evaluate_ema50_breakout_pullback(
    market_data: Dict[str, pd.DataFrame],
    params: EngineParams,
    *,
    market_regime: str = "unknown",
    allow_short: bool = True,
    asset_class: Optional[str] = None,
) -> EngineResult:
    """Run EMA50 breakout-pullback pipeline on 4H closed bars."""
    base = _base_indicators(params)
    if asset_class:
        base["asset_class"] = asset_class

    regime_l = str(market_regime or "unknown").strip().lower()
    blocked = {str(x).strip().lower() for x in (params.blocked_regimes or [])}
    if regime_l in blocked:
        return _hold_payload(f"regime_blocked:{regime_l}", {**base, "setup_state": "invalid"})

    tf = str(params.primary_timeframe or "4h").lower()
    raw = _df(market_data, tf)
    if raw is None:
        return _hold_payload(f"missing_timeframe:{tf}", base)

    df = prepare_closed_ohlcv(raw, tf)
    if len(df) < params.min_candles:
        return _hold_payload(
            f"insufficient_candles:{len(df)}<{params.min_candles}",
            {**base, "candle_count": len(df)},
        )

    ema = compute_ema(df["close"].astype(float), period=params.ema_period)
    if ema is None or pd.isna(ema.iloc[-1]):
        return _hold_payload("ema_unavailable", base)

    ema_val = float(ema.iloc[-1])
    close_now = float(df["close"].iloc[-1])
    base["ema50_value"] = ema_val
    base["ema50_side"] = _ema50_side(close_now, ema_val)
    base["candle_ts"] = df.index[-1].isoformat() if hasattr(df.index[-1], "isoformat") else str(df.index[-1])

    scan_params = {
        "min_candles": params.min_candles,
        "predominant_lookback_bars": params.predominant_lookback_bars,
        "predominant_side_pct": params.predominant_side_pct,
        "min_pullback_candles": params.min_pullback_candles,
    }
    ctx = find_setup_context(df, ema, scan_params, allow_short=allow_short)

    base.update(
        {
            "setup_state": ctx.setup_state,
            "direction": ctx.direction,
            "breakout_pass": ctx.breakout_pass,
            "pullback_pass": ctx.pullback_pass,
            "trigger_pass": ctx.trigger_pass,
            "breakout_reason": ctx.breakout_reason,
            "pullback_reason": ctx.pullback_reason,
            "trigger_reason": ctx.trigger_reason,
            "swing_level": ctx.swing_level,
            "predominant_pct": ctx.predominant_pct,
            "pullback_count": ctx.pullback_count,
            "invalidation_reason": ctx.invalidation_reason,
        }
    )

    if ctx.setup_state != "triggered":
        reason = ctx.invalidation_reason if ctx.setup_state == "invalid" else ctx.setup_state
        return _hold_payload(reason or ctx.setup_state, base)

    side = ctx.direction
    if side == "long" and not params.allow_long:
        return _hold_payload("long_disabled", base)
    if side == "short" and not allow_short:
        return _hold_payload("short_disabled", base)
    if side == "short" and not params.allow_short:
        return _hold_payload("short_disabled", base)

    idx = len(df) - 1
    entry = close_now
    ch_side = "long" if side == "long" else "short"
    stop = compute_chandelier_stop(
        df,
        idx,
        ch_side,
        period=params.chandelier_period,
        atr_mult=params.chandelier_atr_mult,
    )
    if stop is None or stop <= 0:
        return _hold_payload("chandelier_stop_unavailable", base)

    risk = abs(entry - stop)
    if risk <= 0:
        return _hold_payload("zero_risk_distance", base)

    if side == "long":
        if stop >= entry:
            return _hold_payload("invalid_long_stop_above_entry", base)
        target = entry + params.target_reward_risk * risk
    else:
        if stop <= entry:
            return _hold_payload("invalid_short_stop_below_entry", base)
        target = entry - params.target_reward_risk * risk

    avg_range = rolling_candle_range(df, window=params.range_avg_window)
    cur_range = float(df["high"].iloc[idx] - df["low"].iloc[idx])
    avg_r = float(avg_range.iloc[idx]) if not pd.isna(avg_range.iloc[idx]) else 0.0
    base["entry_candle_range"] = cur_range
    base["avg_candle_range"] = avg_r
    if avg_r > 0 and cur_range > params.max_candle_size_mult * avg_r:
        base["invalidation_reason"] = "oversized_entry_candle"
        return _hold_payload(
            f"oversized_entry_candle:{cur_range:.6f}>{params.max_candle_size_mult}×{avg_r:.6f}",
            base,
        )

    rr = params.target_reward_risk
    reward = abs(target - entry)
    stop_pct = risk / entry
    target_pct = reward / entry
    confidence = params.sell_confidence if side == "short" else params.buy_confidence
    # Percent units for min-edge gate (decimals in (0.1, 1) are misread as sub-1%).
    stop_pct_percent = stop_pct * 100.0
    target_pct_percent = target_pct * 100.0
    expected_move_pct = target_pct_percent - stop_pct_percent * (1.0 - confidence)
    base.update(
        {
            "entry_price": entry,
            "stop_hint": stop,
            "target_hint": target,
            "stop_pct": stop_pct,
            "target_pct": target_pct,
            "expected_move_pct": expected_move_pct,
            "reward_risk": rr,
            "chandelier_period": params.chandelier_period,
            "chandelier_atr_mult": params.chandelier_atr_mult,
        }
    )
    base["entry_reason"] = _build_entry_reason(
        side=side,
        tf=tf,
        predominant_pct=ctx.predominant_pct,
        lookback=params.predominant_lookback_bars,
        breakout_reason=ctx.breakout_reason,
        pullback_reason=ctx.pullback_reason,
        swing_level=float(ctx.swing_level or 0),
        entry=entry,
        ema_val=ema_val,
        stop=stop,
        target=target,
        rr=rr,
        chandelier_period=params.chandelier_period,
        chandelier_mult=params.chandelier_atr_mult,
    )

    if side == "long":
        return EngineResult("buy", params.buy_confidence, params.buy_strength, base, "none")
    return EngineResult("sell", params.sell_confidence, params.sell_strength, base, "none")
