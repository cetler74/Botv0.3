"""
Dual-SMA day-trading — shared engine for spot and HL perps.

Top-down: daily bias (SMA200 flat + gap) → 15m trend (SMA20 + structure)
→ 5m entry (retrace/rejection) → optional 1m precision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

from strategy.indicators.dual_sma import (
    compute_dual_sma,
    detect_sma20_rejection_short,
    detect_sma20_retrace,
    detect_squeeze_setup,
    extension_from_sma20_pct,
)
from strategy.indicators.market_structure import MarketStructureState, analyze_market_structure
from strategy.playbooks.ohlcv_closed_bar import prepare_closed_ohlcv


@dataclass
class EngineParams:
    bias_timeframe: str = "1d"
    confirmation_timeframe: str = "15m"
    entry_timeframe: str = "5m"
    precision_timeframe: str = "1m"
    use_precision_entry: bool = False
    context_timeframes: List[str] = field(default_factory=lambda: ["1w"])
    sma20_period: int = 20
    sma200_period: int = 200
    sma200_max_slope_pct: float = 0.0015
    sma20_slope_lookback: int = 5
    sma20_flat_threshold: float = 0.0008
    max_extension_pct: float = 0.012
    retrace_tolerance_pct: float = 0.003
    retrace_lookback_bars: int = 6
    rejection_lookback_bars: int = 8
    pivot_order: int = 3
    min_reward_risk: float = 1.8
    stop_buffer_pct: float = 0.001
    squeeze_entries_enabled: bool = False
    allow_long: bool = True
    allow_short: bool = True
    buy_confidence: float = 0.74
    buy_strength: float = 0.72
    sell_confidence: float = 0.74
    sell_strength: float = 0.72
    blocked_regimes: List[str] = field(default_factory=lambda: ["low_volatility"])
    min_candles_bias: int = 210
    min_candles_confirm: int = 50
    min_candles_entry: int = 40


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


def _base_indicators(params: EngineParams) -> Dict[str, Any]:
    return {
        "setup": "dual_sma_daytrade",
        "bias_timeframe": params.bias_timeframe,
        "confirmation_timeframe": params.confirmation_timeframe,
        "entry_timeframe": params.entry_timeframe,
        "precision_timeframe": params.precision_timeframe,
        "daily_bias": "neutral",
        "trend_15m": "flat",
        "entry_signal_5m": "hold",
        "daily_pass": False,
        "confirm_15m_pass": False,
        "entry_5m_pass": False,
        "precision_pass": False,
        "daily_reason": "",
        "confirm_15m_reason": "",
        "entry_5m_reason": "",
        "precision_reason": "",
        "entry_reason": "",
        "skip_reason": "",
        "squeeze_detected": False,
        "sma200_flat": False,
        "sma20_slope": 0.0,
        "price_vs_sma20_pct": 0.0,
        "price_vs_sma200_pct": 0.0,
        "extension_distance_pct": 0.0,
        "daily_gap_vs_sma200_pct": 0.0,
    }


def _hold_payload(reason: str, extra: Optional[Dict[str, Any]] = None) -> EngineResult:
    payload: Dict[str, Any] = {
        "invalidation_reason": reason,
        "skip_reason": reason,
        "entry_reason": "",
        "setup": "dual_sma_daytrade",
    }
    if extra:
        payload.update(extra)
    return EngineResult("hold", 0.0, 0.0, payload, reason)


def _evaluate_daily_bias(
    daily_df: pd.DataFrame,
    params: EngineParams,
    weekly_df: Optional[pd.DataFrame],
) -> Dict[str, Any]:
    snap = compute_dual_sma(
        daily_df,
        sma20_period=params.sma20_period,
        sma200_period=params.sma200_period,
        slope_lookback=params.sma20_slope_lookback,
        sma200_max_slope_pct=params.sma200_max_slope_pct,
        sma20_flat_threshold=params.sma20_flat_threshold,
    )
    if snap is None:
        return {
            "daily_pass": False,
            "daily_bias": "neutral",
            "daily_reason": "insufficient_daily_candles",
            "squeeze_detected": False,
            "sma200_flat": False,
            "sma20_slope": 0.0,
            "price_vs_sma20_pct": 0.0,
            "price_vs_sma200_pct": 0.0,
            "daily_gap_vs_sma200_pct": 0.0,
            "sma20": 0.0,
            "sma200": 0.0,
            "price": 0.0,
        }

    squeeze, squeeze_reason = detect_squeeze_setup(
        daily_df,
        sma20_period=params.sma20_period,
        sma200_period=params.sma200_period,
        sma200_max_slope_pct=params.sma200_max_slope_pct,
        slope_lookback=params.sma20_slope_lookback,
    )
    if weekly_df is not None and len(weekly_df) >= params.sma200_period:
        w_squeeze, w_reason = detect_squeeze_setup(
            weekly_df,
            sma20_period=params.sma20_period,
            sma200_period=params.sma200_period,
            sma200_max_slope_pct=params.sma200_max_slope_pct * 2,
            slope_lookback=3,
        )
        if w_squeeze:
            squeeze = True
            squeeze_reason = f"{squeeze_reason}; weekly:{w_reason}"

    bias = "neutral"
    daily_pass = False
    reason = "no_clear_daily_bias"

    if not snap.sma200_flat:
        reason = f"sma200_not_flat slope={snap.sma200_slope:.6f}"
    elif squeeze:
        bias = "squeeze"
        if params.squeeze_entries_enabled:
            daily_pass = True
            reason = f"squeeze_setup:{squeeze_reason}"
        else:
            reason = f"squeeze_detected_no_entry:{squeeze_reason}"
    elif snap.price > snap.sma200 and snap.daily_gap_vs_sma200_pct > 0:
        bias = "bullish"
        daily_pass = True
        reason = (
            f"bullish: price {snap.price:.6f} above flat SMA200 {snap.sma200:.6f} "
            f"(gap {snap.daily_gap_vs_sma200_pct * 100:.2f}%)"
        )
    elif snap.price < snap.sma200 and snap.daily_gap_vs_sma200_pct < 0:
        bias = "bearish"
        daily_pass = True
        reason = (
            f"bearish: price {snap.price:.6f} below flat SMA200 {snap.sma200:.6f} "
            f"(gap {snap.daily_gap_vs_sma200_pct * 100:.2f}%)"
        )
    else:
        reason = "price_gap_conflict_with_sma200"

    if snap.sma20_direction == "flat" and bias not in ("squeeze",):
        daily_pass = False
        reason = f"{reason}; daily SMA20 flat — no momentum"

    return {
        "daily_pass": daily_pass,
        "daily_bias": bias,
        "daily_reason": reason,
        "squeeze_detected": squeeze,
        "sma200_flat": snap.sma200_flat,
        "sma20_slope": snap.sma20_slope,
        "price_vs_sma20_pct": snap.price_vs_sma20_pct,
        "price_vs_sma200_pct": snap.price_vs_sma200_pct,
        "daily_gap_vs_sma200_pct": snap.daily_gap_vs_sma200_pct,
        "sma20": snap.sma20,
        "sma200": snap.sma200,
        "price": snap.price,
    }


def _evaluate_15m_confirm(
    confirm_df: pd.DataFrame,
    params: EngineParams,
    daily_bias: str,
) -> Dict[str, Any]:
    snap = compute_dual_sma(
        confirm_df,
        sma20_period=params.sma20_period,
        sma200_period=params.sma200_period,
        slope_lookback=params.sma20_slope_lookback,
        sma200_max_slope_pct=params.sma200_max_slope_pct,
        sma20_flat_threshold=params.sma20_flat_threshold,
    )
    structure: MarketStructureState = analyze_market_structure(confirm_df, pivot_order=params.pivot_order)

    trend_15m = "flat"
    confirm_pass = False
    reason = "sideways_no_momentum"

    if snap is None:
        return {
            "confirm_15m_pass": False,
            "trend_15m": "flat",
            "confirm_15m_reason": "insufficient_15m_candles",
            "structure": structure.to_dict(),
        }

    if snap.sma20_direction == "flat":
        reason = "15m SMA20 flat — sideways, no momentum"
    elif (
        snap.sma20_direction == "rising"
        and snap.price > snap.sma20
        and structure.trend == "uptrend"
        and daily_bias in ("bullish", "squeeze")
    ):
        trend_15m = "uptrend"
        confirm_pass = True
        reason = (
            f"15m uptrend: SMA20 rising below price; {structure.step1_reason}"
        )
    elif (
        snap.sma20_direction == "falling"
        and snap.price < snap.sma20
        and structure.trend == "downtrend"
        and daily_bias in ("bearish", "squeeze")
    ):
        trend_15m = "downtrend"
        confirm_pass = True
        reason = (
            f"15m downtrend: SMA20 falling above price; {structure.step1_reason}"
        )
    elif daily_bias == "bullish" and snap.sma20_direction == "rising" and snap.price > snap.sma20:
        reason = f"15m structure not confirmed: {structure.step1_reason}"
    elif daily_bias == "bearish" and snap.sma20_direction == "falling" and snap.price < snap.sma20:
        reason = f"15m structure not confirmed: {structure.step1_reason}"
    else:
        reason = (
            f"15m misaligned: sma20_dir={snap.sma20_direction} "
            f"price_vs_sma20={snap.price_vs_sma20_pct:.4f} structure={structure.trend}"
        )

    return {
        "confirm_15m_pass": confirm_pass,
        "trend_15m": trend_15m,
        "confirm_15m_reason": reason,
        "structure": structure.to_dict(),
        "confirm_sma20": snap.sma20,
        "confirm_sma200": snap.sma200,
        "confirm_price": snap.price,
        "last_swing_high": structure.last_swing_high,
        "last_swing_low": structure.last_swing_low,
    }


def _evaluate_entry_tf(
    entry_df: pd.DataFrame,
    params: EngineParams,
    trend_15m: str,
    daily_sma200: float,
) -> Dict[str, Any]:
    snap = compute_dual_sma(
        entry_df,
        sma20_period=params.sma20_period,
        sma200_period=params.sma200_period,
        slope_lookback=params.sma20_slope_lookback,
        sma200_max_slope_pct=params.sma200_max_slope_pct,
        sma20_flat_threshold=params.sma20_flat_threshold,
    )
    structure: MarketStructureState = analyze_market_structure(entry_df, pivot_order=max(2, params.pivot_order - 1))

    if snap is None:
        return {
            "entry_5m_pass": False,
            "entry_signal_5m": "hold",
            "entry_5m_reason": "insufficient_entry_candles",
            "extension_distance_pct": 0.0,
            "entry_price": 0.0,
            "side_intent": None,
            "stop_hint": None,
            "target_hint": None,
            "reward_risk": 0.0,
            "mean_reversion_tag": False,
        }

    ext = extension_from_sma20_pct(snap.price, snap.sma20)
    mean_reversion = ext > params.max_extension_pct

    entry_pass = False
    entry_signal = "hold"
    entry_reason = ""
    side_intent = None

    if mean_reversion:
        return {
            "entry_5m_pass": False,
            "entry_signal_5m": "hold",
            "entry_5m_reason": f"extension_skip:{ext * 100:.2f}%>{params.max_extension_pct * 100:.2f}%",
            "extension_distance_pct": ext,
            "entry_price": snap.price,
            "side_intent": None,
            "stop_hint": None,
            "target_hint": None,
            "reward_risk": 0.0,
            "mean_reversion_tag": True,
            "entry_structure": structure.to_dict(),
        }

    if trend_15m == "uptrend":
        ok, r = detect_sma20_retrace(
            entry_df,
            "long",
            sma20_period=params.sma20_period,
            tolerance_pct=params.retrace_tolerance_pct,
            lookback_bars=params.retrace_lookback_bars,
        )
        if ok:
            entry_pass = True
            entry_signal = "long"
            side_intent = "long"
            entry_reason = r
    elif trend_15m == "downtrend":
        ok, r = detect_sma20_retrace(
            entry_df,
            "short",
            sma20_period=params.sma20_period,
            tolerance_pct=params.retrace_tolerance_pct,
            lookback_bars=params.retrace_lookback_bars,
        )
        if ok:
            entry_pass = True
            entry_signal = "short"
            side_intent = "short"
            entry_reason = r
        else:
            rej_ok, rej_r = detect_sma20_rejection_short(
                entry_df,
                sma20_period=params.sma20_period,
                lookback_bars=params.rejection_lookback_bars,
            )
            if rej_ok:
                entry_pass = True
                entry_signal = "short"
                side_intent = "short"
                entry_reason = rej_r

    stop_hint = None
    target_hint = None
    reward_risk = 0.0

    if entry_pass and side_intent == "long":
        swing_low = structure.last_swing_low
        if swing_low is None:
            lows = [s.price for s in structure.validated_swings if s.kind == "low"]
            swing_low = min(lows) if lows else snap.price * 0.99
        stop_hint = float(swing_low) * (1.0 - params.stop_buffer_pct)
        targets = []
        if structure.last_swing_high and structure.last_swing_high > snap.price:
            targets.append(structure.last_swing_high)
        if daily_sma200 > snap.price:
            targets.append(daily_sma200)
        if snap.sma200 > snap.price:
            targets.append(snap.sma200)
        target_hint = max(targets) if targets else snap.price * 1.02
        risk = snap.price - stop_hint
        reward = target_hint - snap.price
        if risk > 0 and reward > 0:
            reward_risk = reward / risk
    elif entry_pass and side_intent == "short":
        swing_high = structure.last_swing_high
        if swing_high is None:
            highs = [s.price for s in structure.validated_swings if s.kind == "high"]
            swing_high = max(highs) if highs else snap.price * 1.01
        stop_hint = float(swing_high) * (1.0 + params.stop_buffer_pct)
        targets = []
        if structure.last_swing_low and structure.last_swing_low < snap.price:
            targets.append(structure.last_swing_low)
        if daily_sma200 < snap.price:
            targets.append(daily_sma200)
        if snap.sma200 < snap.price:
            targets.append(snap.sma200)
        target_hint = min(targets) if targets else snap.price * 0.98
        risk = stop_hint - snap.price
        reward = snap.price - target_hint
        if risk > 0 and reward > 0:
            reward_risk = reward / risk

    if entry_pass and (reward_risk < params.min_reward_risk or stop_hint is None or target_hint is None):
        entry_pass = False
        entry_signal = "hold"
        entry_reason = (
            f"{entry_reason}; rr={reward_risk:.2f} below min {params.min_reward_risk:.2f}"
            if reward_risk < params.min_reward_risk
            else f"{entry_reason}; invalid_stop_target"
        )
        side_intent = None

    return {
        "entry_5m_pass": entry_pass,
        "entry_signal_5m": entry_signal,
        "entry_5m_reason": entry_reason or "no_entry_trigger",
        "extension_distance_pct": ext,
        "entry_price": snap.price,
        "side_intent": side_intent,
        "stop_hint": stop_hint,
        "target_hint": target_hint,
        "reward_risk": reward_risk,
        "mean_reversion_tag": mean_reversion,
        "entry_structure": structure.to_dict(),
        "price_vs_sma20_pct": snap.price_vs_sma20_pct,
        "price_vs_sma200_pct": snap.price_vs_sma200_pct,
        "sma20_slope": snap.sma20_slope,
    }


def _evaluate_precision(
    precision_df: pd.DataFrame,
    params: EngineParams,
    trend_15m: str,
) -> Dict[str, Any]:
    if not params.use_precision_entry:
        return {"precision_pass": True, "precision_reason": "precision_entry_disabled"}
    if trend_15m == "uptrend":
        ok, r = detect_sma20_retrace(
            precision_df,
            "long",
            sma20_period=params.sma20_period,
            tolerance_pct=params.retrace_tolerance_pct * 0.8,
            lookback_bars=4,
        )
    elif trend_15m == "downtrend":
        ok, r = detect_sma20_retrace(
            precision_df,
            "short",
            sma20_period=params.sma20_period,
            tolerance_pct=params.retrace_tolerance_pct * 0.8,
            lookback_bars=4,
        )
        if not ok:
            ok, r = detect_sma20_rejection_short(
                precision_df,
                sma20_period=params.sma20_period,
                lookback_bars=5,
            )
    else:
        return {"precision_pass": False, "precision_reason": "no_trend_for_precision"}
    return {
        "precision_pass": ok,
        "precision_reason": r if ok else f"precision_fail:{r}",
    }


def evaluate_dual_sma_daytrade(
    market_data: Dict[str, pd.DataFrame],
    params: EngineParams,
    *,
    market_regime: str = "unknown",
    allow_short: bool = True,
    asset_class: Optional[str] = None,
) -> EngineResult:
    base = _base_indicators(params)
    if asset_class:
        base["asset_class"] = asset_class

    blocked = {str(x).strip().lower() for x in getattr(params, "blocked_regimes", []) or []}
    if blocked and str(market_regime or "").lower() in blocked:
        return _hold_payload(
            "blocked_regime",
            {**base, "market_regime": market_regime, "daily_reason": "blocked_regime"},
        )

    bias_tf = str(params.bias_timeframe or "1d").lower()
    confirm_tf = str(params.confirmation_timeframe or "15m").lower()
    entry_tf = str(params.entry_timeframe or "5m").lower()
    precision_tf = str(params.precision_timeframe or "1m").lower()

    bias_raw = _df(market_data, bias_tf)
    confirm_raw = _df(market_data, confirm_tf)
    entry_raw = _df(market_data, entry_tf)
    precision_raw = _df(market_data, precision_tf) if params.use_precision_entry else None
    weekly_raw = None
    for wtf in params.context_timeframes or []:
        if str(wtf).lower() == "1w":
            weekly_raw = _df(market_data, "1w")
            break

    if bias_raw is None or len(bias_raw) < params.min_candles_bias:
        return _hold_payload("insufficient_bias_candles", base)
    if confirm_raw is None or len(confirm_raw) < params.min_candles_confirm:
        return _hold_payload("insufficient_confirm_candles", base)
    if entry_raw is None or len(entry_raw) < params.min_candles_entry:
        return _hold_payload("insufficient_entry_candles", base)

    daily_df = prepare_closed_ohlcv(bias_raw, bias_tf)
    confirm_df = prepare_closed_ohlcv(confirm_raw, confirm_tf)
    entry_df = prepare_closed_ohlcv(entry_raw, entry_tf)
    weekly_df = prepare_closed_ohlcv(weekly_raw, "1w") if weekly_raw is not None else None

    daily = _evaluate_daily_bias(daily_df, params, weekly_df)
    base.update(daily)

    if not daily["daily_pass"]:
        return _hold_payload(f"daily_fail:{daily['daily_reason']}", base)

    confirm = _evaluate_15m_confirm(confirm_df, params, daily["daily_bias"])
    base.update({k: v for k, v in confirm.items() if k != "structure"})
    base["validated_swings_15m"] = (confirm.get("structure") or {}).get("validated_swings") or []

    if not confirm["confirm_15m_pass"]:
        return _hold_payload(f"confirm_15m_fail:{confirm['confirm_15m_reason']}", base)

    entry = _evaluate_entry_tf(entry_df, params, confirm["trend_15m"], float(daily.get("sma200") or 0))
    base.update({k: v for k, v in entry.items() if k != "entry_structure"})
    base["validated_swings_5m"] = (entry.get("entry_structure") or {}).get("validated_swings") or []

    if not entry["entry_5m_pass"]:
        return _hold_payload(f"entry_5m_fail:{entry['entry_5m_reason']}", base)

    if params.use_precision_entry and precision_raw is not None:
        precision_df = prepare_closed_ohlcv(precision_raw, precision_tf)
        prec = _evaluate_precision(precision_df, params, confirm["trend_15m"])
        base.update(prec)
        if not prec["precision_pass"]:
            return _hold_payload(f"precision_fail:{prec['precision_reason']}", base)
    else:
        base["precision_pass"] = True
        base["precision_reason"] = "precision_entry_disabled"

    side = entry.get("side_intent")
    tf_label = f"{bias_tf}/{confirm_tf}/{entry_tf}"
    ext_pct = float(entry.get("extension_distance_pct") or 0) * 100
    rr = float(entry.get("reward_risk") or 0)
    stop = entry.get("stop_hint")
    target = entry.get("target_hint")
    entry_px = float(entry.get("entry_price") or 0)

    if side == "long":
        if not params.allow_long:
            return _hold_payload("long_disabled", base)
        entry_reason = (
            f"Dual-SMA LONG ({tf_label}): daily {daily['daily_bias']} {daily['daily_reason']}; "
            f"15m {confirm['trend_15m']} {confirm['confirm_15m_reason']}; "
            f"5m {entry['entry_5m_reason']} (ext {ext_pct:.2f}%); "
            f"SL below swing {stop:.6f}; TP {target:.6f}; R:R {rr:.2f}"
        )
        base["entry_reason"] = entry_reason
        base["side_intent"] = "long"
        return EngineResult("buy", params.buy_confidence, params.buy_strength, base, "none")

    if side == "short":
        if not allow_short or not params.allow_short:
            return _hold_payload("short_disabled", {**base, "entry_reason": ""})
        entry_reason = (
            f"Dual-SMA SHORT ({tf_label}): daily {daily['daily_bias']} {daily['daily_reason']}; "
            f"15m {confirm['trend_15m']} {confirm['confirm_15m_reason']}; "
            f"5m {entry['entry_5m_reason']} (ext {ext_pct:.2f}%); "
            f"SL above swing {stop:.6f}; TP {target:.6f}; R:R {rr:.2f}"
        )
        base["entry_reason"] = entry_reason
        base["side_intent"] = "short"
        return EngineResult("sell", params.sell_confidence, params.sell_strength, base, "none")

    return _hold_payload("no_directional_entry", base)
