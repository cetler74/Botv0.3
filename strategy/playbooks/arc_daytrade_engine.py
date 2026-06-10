"""
The Rumers ARC day-trading — shared engine for spot and HL perps.

Step 1 AREA: daily box + swings + zone bias
Step 2 RANGE: uninterrupted move filter + target geometry
Step 3 CANDLE: John Wick entry at zone edge
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

import pandas as pd

from strategy.indicators.arc_area import (
    apply_utc_gap_rebox,
    classify_arc_zone,
    compute_daily_box,
    find_external_swings,
)
from strategy.indicators.arc_candles import (
    check_invalidation_reversal,
    detect_john_wick_entry,
)
from strategy.indicators.arc_range import (
    classify_range_geometry,
    compute_arc_targets,
    measure_uninterrupted_move,
)
from strategy.playbooks.ohlcv_closed_bar import prepare_closed_ohlcv


@dataclass
class EngineParams:
    bias_timeframe: str = "1d"
    entry_timeframe: str = "5m"
    precision_timeframe: str = "1m"
    use_1m_precision: bool = False
    swing_lookback_bars: int = 120
    pivot_order: int = 3
    zone_tolerance_pct: float = 0.08
    middle_no_trade_pct: float = 0.40
    min_range_move_pct: float = 0.20
    volatile_min_range_move_pct: float = 0.30
    hammer_min_wick_body_ratio: float = 2.0
    rejection_min_upper_wick_ratio: float = 2.0
    gap_rebox_enabled: bool = True
    invalidation_reversal_pct: float = 0.50
    candle_lookback_bars: int = 12
    min_reward_risk: float = 1.2
    min_stop_pct: float = 0.0
    stop_buffer_pct: float = 0.0005
    long_blocked_candle_patterns: List[str] = field(default_factory=list)
    regime_min_range_move_pct: Dict[str, float] = field(default_factory=dict)
    regime_blocked_geometries: Dict[str, List[str]] = field(default_factory=dict)
    require_up_leg_for_long: bool = True
    allow_long: bool = True
    allow_short: bool = True
    buy_confidence: float = 0.74
    buy_strength: float = 0.72
    sell_confidence: float = 0.74
    sell_strength: float = 0.72
    blocked_regimes: List[str] = field(default_factory=lambda: ["low_volatility"])
    allowed_asset_classes: List[str] = field(
        default_factory=lambda: ["crypto", "stocks", "indices", "fx", "commodities"]
    )
    min_candles_bias: int = 3
    min_candles_entry: int = 30


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
        "setup": "arc_daytrade",
        "bias_timeframe": params.bias_timeframe,
        "entry_timeframe": params.entry_timeframe,
        "precision_timeframe": params.precision_timeframe,
        "box_high": None,
        "box_low": None,
        "swing_high": None,
        "swing_low": None,
        "range_size": 0.0,
        "range_pct_move": 0.0,
        "range_geometry_tag": "neutral",
        "zone": "no_trade",
        "zone_level": "",
        "area_pass": False,
        "range_pass": False,
        "candle_pass": False,
        "area_reason": "",
        "range_reason": "",
        "candle_reason": "",
        "signal_candle": {},
        "entry_price": None,
        "stop_hint": None,
        "target_hint": None,
        "target_50": None,
        "target_100": None,
        "reward_risk": 0.0,
        "setup_state": "idle",
        "invalidation_reason": "none",
        "entry_reason": "",
        "side_intent": "none",
        "gap_info": {},
    }


def _hold_payload(reason: str, extra: Optional[Dict[str, Any]] = None) -> EngineResult:
    payload: Dict[str, Any] = {
        "invalidation_reason": reason,
        "skip_reason": reason,
        "entry_reason": "",
        "setup": "arc_daytrade",
    }
    if extra:
        payload.update(extra)
    state = payload.get("setup_state") or "idle"
    if state == "idle" and payload.get("area_pass"):
        payload["setup_state"] = "area" if not payload.get("range_pass") else "range"
    return EngineResult("hold", 0.0, 0.0, payload, reason)


def _min_move_pct(params: EngineParams, asset_class: Optional[str]) -> float:
    if asset_class and str(asset_class).lower() == "crypto":
        return params.volatile_min_range_move_pct
    return params.min_range_move_pct


def _regime_min_move_pct(
    params: EngineParams,
    market_regime: str,
    asset_class: Optional[str],
) -> float:
    regime_key = str(market_regime or "").strip().lower()
    overrides = getattr(params, "regime_min_range_move_pct", None) or {}
    if isinstance(overrides, Mapping) and regime_key in overrides:
        try:
            return float(overrides[regime_key])
        except (TypeError, ValueError):
            pass
    return _min_move_pct(params, asset_class)


def _regime_blocked_geometries(params: EngineParams, market_regime: str) -> set[str]:
    regime_key = str(market_regime or "").strip().lower()
    raw = getattr(params, "regime_blocked_geometries", None) or {}
    if not isinstance(raw, Mapping):
        return set()
    blocked = raw.get(regime_key) or []
    return {str(g).strip().lower() for g in blocked if str(g).strip()}


def _score_confidence_strength(
    zone_dist: float,
    range_move: float,
    wick_quality: float,
    base_conf: float,
    base_str: float,
) -> tuple[float, float]:
    prox = max(0.0, 1.0 - min(zone_dist, 1.0))
    move = min(1.0, range_move / 0.5)
    wick = min(1.0, wick_quality / 4.0)
    conf = min(0.95, base_conf * (0.85 + 0.15 * prox) + 0.05 * move + 0.03 * wick)
    strength = min(0.95, base_str * (0.85 + 0.15 * wick) + 0.05 * move)
    return round(conf, 3), round(strength, 3)


def evaluate_arc_daytrade(
    market_data: Dict[str, pd.DataFrame],
    params: EngineParams,
    *,
    market_regime: str = "unknown",
    allow_short: bool = True,
    asset_class: Optional[str] = None,
) -> EngineResult:
    """Run ARC (Area, Range, Candle) pipeline."""
    base = _base_indicators(params)
    if asset_class:
        base["asset_class"] = asset_class

    allowed_classes = {str(x).strip().lower() for x in (params.allowed_asset_classes or [])}
    if asset_class and allowed_classes and str(asset_class).lower() not in allowed_classes:
        return _hold_payload(
            f"asset_class_blocked:{asset_class}",
            {**base, "area_reason": f"asset class {asset_class} not allowed"},
        )

    blocked = {str(x).strip().lower() for x in getattr(params, "blocked_regimes", []) or []}
    if blocked and str(market_regime or "").lower() in blocked:
        return _hold_payload(
            "blocked_regime",
            {**base, "market_regime": market_regime, "area_reason": "blocked_regime"},
        )

    bias_tf = str(params.bias_timeframe or "1d").lower()
    entry_tf = str(params.entry_timeframe or "5m").lower()
    precision_tf = str(params.precision_timeframe or "1m").lower()

    daily_raw = _df(market_data, bias_tf)
    entry_raw = _df(market_data, entry_tf)
    if daily_raw is None or len(daily_raw) < params.min_candles_bias:
        return _hold_payload("insufficient_daily_candles", base)
    if entry_raw is None or len(entry_raw) < params.min_candles_entry:
        return _hold_payload("insufficient_entry_candles", base)

    daily_df = prepare_closed_ohlcv(daily_raw, bias_tf)
    entry_df = prepare_closed_ohlcv(entry_raw, entry_tf)

    # --- Step 1 AREA ---
    daily_box = compute_daily_box(daily_df)
    if daily_box is None:
        return _hold_payload("no_daily_box", {**base, "area_reason": "no_previous_daily_bar"})

    box_high, box_low, gap_info = apply_utc_gap_rebox(
        daily_box,
        entry_df,
        enabled=params.gap_rebox_enabled,
    )
    range_size = box_high - box_low
    if range_size <= 0:
        return _hold_payload("invalid_box_range", {**base, "area_reason": "box_high <= box_low"})

    swing_high, swing_low = find_external_swings(
        entry_df,
        box_high,
        box_low,
        lookback=params.swing_lookback_bars,
        pivot_order=params.pivot_order,
    )
    price = float(entry_df["close"].iloc[-1])
    arc_zone = classify_arc_zone(
        price,
        box_high,
        box_low,
        swing_high,
        swing_low,
        tolerance_pct=params.zone_tolerance_pct,
        middle_no_trade_pct=params.middle_no_trade_pct,
    )

    base.update(
        {
            "box_high": box_high,
            "box_low": box_low,
            "swing_high": swing_high,
            "swing_low": swing_low,
            "range_size": range_size,
            "gap_info": gap_info,
            "zone": arc_zone.zone,
            "zone_level": arc_zone.zone_level,
            "area_pass": arc_zone.zone in {"buy", "sell"},
            "area_reason": arc_zone.reason,
            "setup_state": "area" if arc_zone.zone in {"buy", "sell"} else "idle",
        }
    )

    if arc_zone.zone == "no_trade":
        return _hold_payload(
            f"area_fail:{arc_zone.reason}",
            {**base, "area_pass": False},
        )

    # --- Step 2 RANGE ---
    min_move = _regime_min_move_pct(params, market_regime, asset_class)
    move_result = measure_uninterrupted_move(entry_df, range_size, min_move)
    geometry = classify_range_geometry(range_size, swing_high, swing_low, box_high, box_low)
    blocked_geometries = _regime_blocked_geometries(params, market_regime)
    geometry_blocked = geometry in blocked_geometries

    base.update(
        {
            "range_pct_move": move_result.range_pct_move,
            "range_geometry_tag": geometry,
            "move_direction": move_result.move_direction,
            "range_pass": move_result.pass_move and not geometry_blocked,
            "range_reason": (
                f"geometry_{geometry}_blocked_in_{market_regime}"
                if geometry_blocked
                else move_result.reason
            ),
            "setup_state": "range" if move_result.pass_move and not geometry_blocked else "area",
            "market_regime": market_regime,
        }
    )

    if geometry_blocked:
        return _hold_payload(f"range_fail:geometry_{geometry}_blocked_in_{market_regime}", base)

    if not move_result.pass_move:
        return _hold_payload(f"range_fail:{move_result.reason}", base)

    direction = "long" if arc_zone.zone == "buy" else "short"

    if (
        direction == "long"
        and params.require_up_leg_for_long
        and str(move_result.move_direction or "").lower() != "up"
    ):
        base["range_pass"] = False
        base["range_reason"] = (
            f"long_requires_up_leg got_{move_result.move_direction or 'none'}"
        )
        return _hold_payload(base["range_reason"], base)

    # --- Step 3 CANDLE ---
    candle_df = entry_df
    if params.use_1m_precision:
        prec_raw = _df(market_data, precision_tf)
        if prec_raw is not None and len(prec_raw) >= 5:
            candle_df = prepare_closed_ohlcv(prec_raw, precision_tf)

    signal = detect_john_wick_entry(
        candle_df,
        arc_zone.zone,
        hammer_min_wick_body_ratio=params.hammer_min_wick_body_ratio,
        rejection_min_upper_wick_ratio=params.rejection_min_upper_wick_ratio,
        lookback_bars=params.candle_lookback_bars,
    )

    if signal is None:
        base.update(
            {
                "candle_pass": False,
                "candle_reason": "no_john_wick_pattern",
                "setup_state": "range",
            }
        )
        return _hold_payload("candle_fail:no_pattern", base)

    base["signal_candle"] = signal.to_dict()
    base["candle_reason"] = signal.reason

    if not signal.entry_triggered:
        base.update({"candle_pass": False, "setup_state": "candle"})
        return _hold_payload(f"candle_fail:{signal.reason}", base)

    entry_price = float(signal.entry_price)
    stop_hint = float(signal.stop_hint)
    targets = compute_arc_targets(entry_price, direction, range_size)
    if targets is None:
        return _hold_payload("candle_fail:no_targets", base)

    target_50 = targets.target_50
    target_100 = targets.target_100

    if direction == "long":
        risk = entry_price - stop_hint
        reward = target_50 - entry_price
    else:
        risk = stop_hint - entry_price
        reward = entry_price - target_50

    if risk <= 0 or reward <= 0:
        base.update(
            {
                "entry_price": entry_price,
                "stop_hint": stop_hint,
                "target_50": target_50,
                "target_100": target_100,
                "candle_pass": False,
                "candle_reason": f"invalid_geometry risk={risk:.6f} reward={reward:.6f}",
            }
        )
        return _hold_payload("candle_fail:invalid_geometry", base)

    rr = reward / risk
    stop_pct = risk / entry_price if entry_price > 0 else 0.0
    base["stop_pct"] = stop_pct

    if params.min_stop_pct > 0 and stop_pct < params.min_stop_pct:
        base.update(
            {
                "candle_pass": False,
                "candle_reason": (
                    f"stop_pct {stop_pct:.4f} below min {params.min_stop_pct:.4f}"
                ),
            }
        )
        return _hold_payload("candle_fail:stop_too_tight", base)

    blocked_patterns = {
        str(p).strip().lower()
        for p in (getattr(params, "long_blocked_candle_patterns", None) or [])
        if str(p).strip()
    }
    if direction == "long" and str(signal.pattern or "").lower() in blocked_patterns:
        base.update(
            {
                "candle_pass": False,
                "candle_reason": f"pattern_{signal.pattern}_blocked_for_long",
            }
        )
        return _hold_payload(f"candle_fail:pattern_{signal.pattern}_blocked", base)

    inv, inv_reason = check_invalidation_reversal(
        entry_df,
        entry_price,
        target_50,
        direction,
        reversal_pct=params.invalidation_reversal_pct,
    )
    if inv:
        base.update(
            {
                "candle_pass": False,
                "invalidation_reason": inv_reason,
                "setup_state": "invalidated",
                "entry_price": entry_price,
                "stop_hint": stop_hint,
                "target_50": target_50,
                "target_100": target_100,
            }
        )
        return _hold_payload(f"invalidated:{inv_reason}", base)

    wick_q = max(signal.lower_wick_ratio, signal.upper_wick_ratio)
    conf, strength = _score_confidence_strength(
        arc_zone.distance_pct,
        move_result.range_pct_move,
        wick_q,
        params.buy_confidence if direction == "long" else params.sell_confidence,
        params.buy_strength if direction == "long" else params.sell_strength,
    )

    base.update(
        {
            "candle_pass": True,
            "entry_price": entry_price,
            "stop_hint": stop_hint,
            "target_50": target_50,
            "target_100": target_100,
            "target_hint": target_100,
            "reward_risk": round(rr, 3),
            "setup_state": "ready",
            "invalidation_reason": "none",
            "side_intent": direction,
        }
    )

    if rr < params.min_reward_risk:
        base["candle_pass"] = False
        base["candle_reason"] = f"R:R {rr:.2f} below min {params.min_reward_risk:.2f}"
        return _hold_payload("candle_fail:rr_below_min", base)

    entry_reason = (
        f"ARC daytrade {direction.upper()} ({bias_tf}/{entry_tf}): "
        f"AREA box[{box_low:.6f}-{box_high:.6f}] zone={arc_zone.zone}@{arc_zone.zone_level} "
        f"swingH={swing_high} swingL={swing_low}; "
        f"RANGE move={move_result.range_pct_move:.1%} geometry={geometry}; "
        f"CANDLE {signal.pattern} entry={entry_price:.6f} SL={stop_hint:.6f} "
        f"TP50={target_50:.6f} TP100={target_100:.6f} R:R={rr:.2f}; "
        f"exit=trailing_stop+profit_protection"
    )
    base["entry_reason"] = entry_reason

    if direction == "long":
        if not params.allow_long:
            return _hold_payload("long_disabled", base)
        return EngineResult("buy", conf, strength, base, "none")

    if not allow_short or not params.allow_short:
        return _hold_payload("short_disabled_spot", {**base, "entry_reason": ""})

    return EngineResult("sell", conf, strength, base, "none")
