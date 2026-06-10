"""
TradingLab 3-Step Supply/Demand — shared engine for spot and HL perps.

Step 1: Validated swing market structure (trend)
Step 2: Supply/demand zones + retest
Step 3: Stop below/above zone, target at swing, min R:R gate
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

import pandas as pd

from strategy.indicators.market_structure import MarketStructureState, analyze_market_structure
from strategy.indicators.supply_demand_zones import ZoneScanResult, scan_supply_demand_zones
from strategy.playbooks.ohlcv_closed_bar import prepare_closed_ohlcv


@dataclass
class EngineParams:
    timeframe_mode: str = "multi"
    structure_timeframe: str = "1h"
    entry_timeframe: str = "15m"
    pivot_order: int = 3
    min_reward_risk: float = 2.5
    min_stop_pct: float = 0.0
    chop_regime_min_trend_swing_pct: float = 0.0
    chop_regime_filters: List[str] = field(default_factory=lambda: ["sideways", "reversal_zone"])
    min_close_above_swing_low_pct: float = 0.0
    stop_buffer_pct: float = 0.001
    max_consolidation_body_ratio: float = 0.45
    max_zone_width_pct: float = 0.012
    impulse_min_body_pct: float = 0.55
    impulse_min_range_atr_mult: float = 1.8
    zone_retest_tolerance_pct: float = 0.002
    zone_lookback_bars: int = 120
    range_median_window: int = 20
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
    min_candles_structure: int = 50
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


def _hold_payload(reason: str, extra: Optional[Dict[str, Any]] = None) -> EngineResult:
    payload: Dict[str, Any] = {
        "invalidation_reason": reason,
        "skip_reason": reason,
        "entry_reason": "",
        "setup": "supply_demand_3step",
    }
    if extra:
        payload.update(extra)
    return EngineResult("hold", 0.0, 0.0, payload, reason)


def _resolve_zone_tf(params: EngineParams) -> str:
    mode = str(params.timeframe_mode or "multi").lower()
    if mode == "single":
        return str(params.structure_timeframe or "1h").lower()
    return str(params.entry_timeframe or "15m").lower()


def _base_indicators(params: EngineParams) -> Dict[str, Any]:
    return {
        "setup": "supply_demand_3step",
        "timeframe_mode": params.timeframe_mode,
        "structure_timeframe": params.structure_timeframe,
        "entry_timeframe": params.entry_timeframe,
    }


def _trend_swing_pct(structure: MarketStructureState, entry_price: float) -> float:
    if entry_price <= 0:
        return 0.0
    hi = structure.last_swing_high
    lo = structure.last_swing_low
    if hi is None or lo is None or hi <= lo:
        return 0.0
    return (float(hi) - float(lo)) / float(entry_price)


def _is_chop_regime(params: EngineParams, market_regime: str) -> bool:
    regime_key = str(market_regime or "").strip().lower()
    filters = {str(x).strip().lower() for x in (params.chop_regime_filters or []) if str(x).strip()}
    return regime_key in filters if filters else False


def evaluate_supply_demand_3step(
    market_data: Dict[str, pd.DataFrame],
    params: EngineParams,
    *,
    market_regime: str = "unknown",
    allow_short: bool = True,
    asset_class: Optional[str] = None,
) -> EngineResult:
    """Run 3-step supply/demand pipeline."""
    base = _base_indicators(params)
    if asset_class:
        base["asset_class"] = asset_class

    allowed_classes = {str(x).strip().lower() for x in (params.allowed_asset_classes or [])}
    if asset_class and allowed_classes and str(asset_class).lower() not in allowed_classes:
        return _hold_payload(
            f"asset_class_blocked:{asset_class}",
            {**base, "step1_pass": False, "step1_reason": f"asset class {asset_class} not allowed"},
        )

    blocked = {str(x).strip().lower() for x in getattr(params, "blocked_regimes", []) or []}
    if blocked and str(market_regime or "").lower() in blocked:
        return _hold_payload(
            "blocked_regime",
            {**base, "market_regime": market_regime, "step1_pass": False, "step1_reason": "blocked_regime"},
        )

    struct_tf = str(params.structure_timeframe or "1h").lower()
    zone_tf = _resolve_zone_tf(params)

    struct_raw = _df(market_data, struct_tf)
    zone_raw = _df(market_data, zone_tf)
    if struct_raw is None or len(struct_raw) < params.min_candles_structure:
        return _hold_payload("insufficient_structure_candles", base)
    if zone_raw is None or len(zone_raw) < params.min_candles_entry:
        return _hold_payload("insufficient_entry_candles", base)

    struct_df = prepare_closed_ohlcv(struct_raw, struct_tf)
    zone_df = prepare_closed_ohlcv(zone_raw, zone_tf)

    # Step 1 — market structure
    structure: MarketStructureState = analyze_market_structure(struct_df, pivot_order=params.pivot_order)
    base.update(structure.to_dict())
    base["trend_direction"] = structure.trend

    if not structure.step1_pass:
        return _hold_payload(
            f"step1_fail:{structure.step1_reason}",
            {**base, "step2_pass": False, "step2_reason": "skipped_no_trend", "step3_pass": False, "step3_reason": "skipped"},
        )

    # Step 2 — zones + retest
    zones: ZoneScanResult = scan_supply_demand_zones(
        zone_df,
        structure.trend,
        max_consolidation_body_ratio=params.max_consolidation_body_ratio,
        max_zone_width_pct=params.max_zone_width_pct,
        impulse_min_body_pct=params.impulse_min_body_pct,
        impulse_min_range_atr_mult=params.impulse_min_range_atr_mult,
        zone_retest_tolerance_pct=params.zone_retest_tolerance_pct,
        lookback_bars=params.zone_lookback_bars,
        range_median_window=params.range_median_window,
    )
    base.update(zones.to_dict())
    base["step2_pass"] = zones.step2_pass
    base["step2_reason"] = zones.step2_reason

    if not zones.step2_pass or zones.retest_zone is None:
        return _hold_payload(
            f"step2_fail:{zones.step2_reason}",
            {**base, "step3_pass": False, "step3_reason": "skipped_no_retest"},
        )

    zone = zones.retest_zone
    entry_price = float(zone_df["close"].astype(float).iloc[-1])

    # Step 3 — trade management
    if structure.trend == "uptrend":
        stop = zone.zone_low * (1.0 - params.stop_buffer_pct)
        target = structure.last_swing_high
        if target is None or target <= entry_price:
            val_highs = [s.price for s in structure.validated_swings if s.kind == "high"]
            target = max(val_highs) if val_highs else None
        side = "buy"
    else:
        stop = zone.zone_high * (1.0 + params.stop_buffer_pct)
        target = structure.last_swing_low
        if target is None or target >= entry_price:
            val_lows = [s.price for s in structure.validated_swings if s.kind == "low"]
            target = min(val_lows) if val_lows else None
        side = "sell"

    base["step3_pass"] = False
    base["step3_reason"] = "pending_rr_calc"

    if target is None:
        return _hold_payload(
            "step3_fail:no_swing_target",
            {**base, "step3_reason": "no valid swing target for take profit"},
        )

    if structure.trend == "uptrend":
        risk = entry_price - stop
        reward = target - entry_price
    else:
        risk = stop - entry_price
        reward = entry_price - target

    if risk <= 0 or reward <= 0:
        return _hold_payload(
            "step3_fail:invalid_stop_or_target",
            {
                **base,
                "entry_price": entry_price,
                "stop_hint": stop,
                "target_hint": target,
                "step3_reason": f"invalid geometry risk={risk:.6f} reward={reward:.6f}",
            },
        )

    rr = reward / risk
    stop_pct = risk / entry_price
    target_pct = reward / entry_price
    swing_pct = _trend_swing_pct(structure, entry_price)
    close_px = float(zone_df["close"].astype(float).iloc[-1])
    close_above_low_pct = 0.0
    if structure.last_swing_low and structure.last_swing_low > 0:
        close_above_low_pct = (close_px - float(structure.last_swing_low)) / float(
            structure.last_swing_low
        )

    base.update(
        {
            "entry_price": entry_price,
            "stop_hint": stop,
            "target_hint": target,
            "stop_pct": stop_pct,
            "target_pct": target_pct,
            "reward_risk": rr,
            "trend_swing_pct": swing_pct,
            "close_above_swing_low_pct": close_above_low_pct,
            "market_regime": market_regime,
        }
    )

    if params.min_stop_pct > 0 and stop_pct < params.min_stop_pct:
        base["step3_pass"] = False
        base["step3_reason"] = (
            f"stop_pct {stop_pct:.4f} below minimum {params.min_stop_pct:.4f}"
        )
        return _hold_payload("step3_fail:stop_too_tight", base)

    if (
        side == "buy"
        and _is_chop_regime(params, market_regime)
        and params.chop_regime_min_trend_swing_pct > 0
        and swing_pct < params.chop_regime_min_trend_swing_pct
    ):
        base["step3_pass"] = False
        base["step3_reason"] = (
            f"trend swing {swing_pct:.2%} below chop minimum "
            f"{params.chop_regime_min_trend_swing_pct:.2%} in {market_regime}"
        )
        return _hold_payload("step3_fail:weak_structure_in_chop", base)

    if (
        side == "buy"
        and params.min_close_above_swing_low_pct > 0
        and structure.last_swing_low is not None
        and close_above_low_pct < params.min_close_above_swing_low_pct
    ):
        base["step3_pass"] = False
        base["step3_reason"] = (
            f"close only {close_above_low_pct:.2%} above last swing low "
            f"(need {params.min_close_above_swing_low_pct:.2%})"
        )
        return _hold_payload("step3_fail:close_too_close_to_swing_low", base)

    if rr < params.min_reward_risk:
        base["step3_pass"] = False
        base["step3_reason"] = f"R:R {rr:.2f} below minimum {params.min_reward_risk:.2f}"
        return _hold_payload(f"step3_fail:rr_below_min", base)

    base["step3_pass"] = True
    base["step3_reason"] = (
        f"R:R {rr:.2f} >= {params.min_reward_risk:.2f}; "
        f"stop {stop:.6f}, target {target:.6f}, entry {entry_price:.6f}"
    )

    if side == "buy":
        if not params.allow_long:
            return _hold_payload("long_disabled", base)
        entry_reason = (
            f"Supply/Demand 3-Step LONG ({struct_tf}/{zone_tf}): {structure.step1_reason}; "
            f"{zones.step2_reason}; {base['step3_reason']}"
        )
        base["entry_reason"] = entry_reason
        base["side_intent"] = "long"
        return EngineResult("buy", params.buy_confidence, params.buy_strength, base, "none")

    if not allow_short or not params.allow_short:
        return _hold_payload("short_disabled_spot", {**base, "entry_reason": ""})

    entry_reason = (
        f"Supply/Demand 3-Step SHORT ({struct_tf}/{zone_tf}): {structure.step1_reason}; "
        f"{zones.step2_reason}; {base['step3_reason']}"
    )
    base["entry_reason"] = entry_reason
    base["side_intent"] = "short"
    return EngineResult("sell", params.sell_confidence, params.sell_strength, base, "none")
