"""
Hyperliquid perpetual paper-trading helpers.

This module intentionally has no live-order path. It mirrors existing strategy
signals into isolated paper positions so spot trading remains untouched.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from strategy.hyperliquid.consensus import normalize_perp_entry_signal

from profit_protection_state import (
    evaluate_profit_protection_arm,
    evaluate_tiered_profit_lock,
    format_breach_exit_reason,
    format_late_arm_exit_reason,
    is_feature_enabled,
    merge_trail_trigger_for_side,
    resolve_profit_lock_floor_decimal,
    should_breach_exit_for_status,
)

logger = logging.getLogger(__name__)


def ta_evidence_from_signal(signal: Mapping[str, Any]) -> Dict[str, Any]:
    """Return bounded, serializable closed-bar TA evidence from a selected signal."""
    details = signal.get("details") if isinstance(signal, Mapping) else {}
    source = details.get("ta_evidence") if isinstance(details, Mapping) else {}
    if not isinstance(source, Mapping):
        return {}
    raw_inputs = source.get("inputs")
    inputs: Dict[str, float] = {}
    if isinstance(raw_inputs, Mapping):
        for key, value in raw_inputs.items():
            if len(inputs) >= 16:
                break
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(numeric):
                inputs[str(key)] = numeric
    return {
        "bar_closed": bool(source.get("bar_closed")),
        "timeframes": [
            str(timeframe)
            for timeframe in source.get("timeframes") or ()
            if str(timeframe) in {"1h", "15m"}
        ][:2],
        "bar_times": dict(source.get("bar_times") or {}) if isinstance(source.get("bar_times"), Mapping) else {},
        "inputs": inputs,
    }


HYPERLIQUID_STRATEGY_FAMILIES = {
    "heikin_ashi": "trend_momentum",
    "vwma_hull": "trend_momentum",
    "macd_momentum": "trend_momentum",
    "multi_timeframe_confluence": "trend_momentum",
    "swing_hull_rsi_ema": "trend_momentum",
    "pullback_long_scalping": "pullback_scalp",
    "vwap_bounce_scalping": "pullback_scalp",
    "macd_ema_vwap_scalper": "pullback_scalp",
    "small_size_momentum_scalp": "pullback_scalp",
    "sma_reclaim_bull_flag": "reversal_reclaim",
    "rsi_oversold_checklist": "reversal_reclaim",
    "rsi_oversold_override": "reversal_reclaim",
    "breakout_retest_long": "pattern_breakout",
    "engulfing_multi_tf": "pattern_breakout",
    "rsi_stoch_reversal_15m": "reversal_reclaim",
    "rsi_stoch_reversal_5m": "reversal_reclaim",
    "rsi_stoch_reversal_1m": "reversal_reclaim",
    "supply_demand_3step": "pattern_breakout",
    "dual_sma_daytrade": "trend_retrace",
    "arc_daytrade": "pattern_breakout",
    "ema50_breakout_pullback": "pattern_breakout",
    "orb_5m_scalp": "opening_range_breakout",
}


DEFAULT_STANDALONE_STRATEGY_GATES = {
    "heikin_ashi": {"min_confidence": 0.75, "min_strength": 0.50, "size_multiplier": None},
    "vwma_hull": {"min_confidence": 0.70, "min_strength": 0.20, "size_multiplier": None},
    "macd_momentum": {"min_confidence": 0.65, "min_strength": 0.50, "size_multiplier": None},
    "multi_timeframe_confluence": {"min_confidence": 0.65, "min_strength": 0.50, "size_multiplier": None},
    "swing_hull_rsi_ema": {"min_confidence": 0.62, "min_strength": 0.60, "size_multiplier": None},
    "pullback_long_scalping": {"min_confidence": 0.70, "min_strength": 0.65, "size_multiplier": None},
    "vwap_bounce_scalping": {"min_confidence": 0.70, "min_strength": 0.65, "size_multiplier": None},
    "macd_ema_vwap_scalper": {"min_confidence": 0.65, "min_strength": 0.55, "size_multiplier": None},
    "small_size_momentum_scalp": {"min_confidence": 0.70, "min_strength": 0.55, "size_multiplier": None},
    "breakout_retest_long": {"min_confidence": 0.70, "min_strength": 0.70, "size_multiplier": None},
    "engulfing_multi_tf": {"min_confidence": 0.72, "min_strength": 0.70, "size_multiplier": None},
    "rsi_stoch_reversal_15m": {"min_confidence": 0.70, "min_strength": 0.65, "size_multiplier": None},
    "rsi_stoch_reversal_5m": {"min_confidence": 0.70, "min_strength": 0.65, "size_multiplier": None},
    "rsi_stoch_reversal_1m": {"min_confidence": 0.70, "min_strength": 0.65, "size_multiplier": None},
    "supply_demand_3step": {"min_confidence": 0.70, "min_strength": 0.65, "size_multiplier": None},
    "dual_sma_daytrade": {"min_confidence": 0.70, "min_strength": 0.65, "size_multiplier": None},
    "arc_daytrade": {"min_confidence": 0.70, "min_strength": 0.65, "size_multiplier": None},
    "ema50_breakout_pullback": {"min_confidence": 0.70, "min_strength": 0.65, "size_multiplier": None},
    "orb_5m_scalp": {"min_confidence": 0.70, "min_strength": 0.65, "size_multiplier": None},
}


PRIORITY_STANDALONE_ENTRY_STRATEGIES = (
    "rsi_stoch_reversal_15m",
    "supply_demand_3step",
    "dual_sma_daytrade",
)

DEFAULT_CONSENSUS_EXECUTABLE_DENYLIST = (
    "rsi_stoch_reversal_15m",
    "rsi_stoch_reversal_5m",
    "rsi_stoch_reversal_1m",
)


def pair_to_hyperliquid_coin(pair: str) -> str:
    """Convert BTC/USDC or BTCUSD-style symbols to Hyperliquid perp coin names."""
    raw = str(pair or "").strip()
    if "/" in raw:
        return raw.split("/", 1)[0]
    if ":" in raw:
        dex, base = raw.split(":", 1)
        raw = f"{dex.lower()}:{base.upper()}"
    else:
        raw = raw.upper()
    for suffix in ("USDC", "USDT", "USD"):
        if raw.endswith(suffix):
            return raw[: -len(suffix)]
    return raw


def merge_active_trades_with_paper_perps(
    active_trades: Iterable[Mapping[str, Any]],
    paper_perp_open_trades: Iterable[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """Return active spot/in-memory rows plus DB-backed Hyperliquid paper perps."""
    rows: List[Dict[str, Any]] = [
        dict(row) for row in (active_trades or []) if isinstance(row, Mapping)
    ]
    for raw in paper_perp_open_trades or []:
        if not isinstance(raw, Mapping):
            continue
        row = dict(raw)
        coin = pair_to_hyperliquid_coin(str(row.get("coin") or row.get("pair") or ""))
        row.setdefault("exchange", "hyperliquid")
        if coin:
            row.setdefault("coin", coin)
            row.setdefault("pair", f"{coin}/USD-PERP")
        row.setdefault("asset_class", "perp")
        row.setdefault("source", "hyperliquid_paper_perp")
        rows.append(row)
    return rows


def position_sides_from_signal(signal: str) -> Optional[str]:
    sig = normalize_perp_entry_signal(signal)
    if sig == "long":
        return "long"
    if sig == "short":
        return "short"
    return None


def disabled_strategy_side_exit_reason(
    trade: Mapping[str, Any],
    root_config: Mapping[str, Any],
) -> Optional[str]:
    """Close legacy paper positions when their strategy side is now disabled."""
    source = str(trade.get("source_strategy") or trade.get("strategy") or "").strip()
    if not source:
        return None
    side = str(trade.get("position_side") or "").strip().lower()
    if side not in {"long", "short"}:
        return None

    strat_root = (root_config or {}).get("strategies_hyperliquid") or {}
    strat_cfg = strat_root.get(source) or {}
    if not strat_cfg:
        return None
    if strat_cfg.get("enabled") is False:
        return f"paper_disabled_strategy_{source}"

    params = strat_cfg.get("parameters") or {}
    if side == "long" and params.get("allow_long") is False:
        return f"paper_disabled_side_{source}_long"
    if side == "short" and params.get("allow_short") is False:
        return f"paper_disabled_side_{source}_short"
    return None


def adaptive_regime_side_exit_exempt_strategies(
    hl_cfg: Optional[Mapping[str, Any]] = None,
) -> set:
    """Strategies that keep open positions through adaptive regime/side blocks.

    Dual SMA shorts in trending_down are a primary earner; adaptive book-level
    blocks must not force-close them (or block new Dual SMA entries).
    """
    raw = (hl_cfg or {}).get("adaptive_regime_side_exit_exempt_strategies")
    if raw is None:
        return {"dual_sma_daytrade"}
    if not isinstance(raw, (list, tuple, set)):
        return {"dual_sma_daytrade"}
    return {str(item or "").strip().lower() for item in raw if str(item or "").strip()}


def adaptive_blocked_regime_side_exit_reason(
    trade: Mapping[str, Any],
    hl_cfg: Mapping[str, Any],
) -> Optional[str]:
    """Close open paper positions when adaptive PnL control now blocks their regime/side."""
    source = str(
        trade.get("source_strategy") or trade.get("strategy") or ""
    ).strip().lower()
    if source and source in adaptive_regime_side_exit_exempt_strategies(hl_cfg):
        return None

    metadata = _metadata_dict(trade)
    regime = str(
        metadata.get("market_regime")
        or metadata.get("stable_regime")
        or trade.get("market_regime")
        or ""
    ).strip().lower()
    side = str(trade.get("position_side") or "").strip().lower()
    if not regime or side not in {"long", "short"}:
        return None

    adaptive = (hl_cfg or {}).get("_adaptive_pnl_control") or {}
    for decision in adaptive.get("decisions") or []:
        if str(decision.get("action") or "").strip().lower() != "block":
            continue
        if str(decision.get("targetType") or "").strip().lower() != "regime_side":
            continue
        if str(decision.get("target") or "").strip().lower() != regime:
            continue
        if str(decision.get("side") or "").strip().lower() != side:
            continue
        decision_type = str(decision.get("type") or decision.get("decisionType") or "block_regime_side")
        safe_type = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in decision_type.lower())
        safe_regime = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in regime)
        return f"paper_{safe_type}_{safe_regime}_{side}"
    return None


def portfolio_control_exit_reason(
    trade: Mapping[str, Any],
    root_config: Mapping[str, Any],
    hl_cfg: Mapping[str, Any],
) -> Optional[str]:
    """Apply executable-portfolio exits without contaminating shadow outcomes."""
    metadata = trade.get("metadata") or {}
    if isinstance(metadata, Mapping):
        shadow_raw = metadata.get("shadow_trade")
        if shadow_raw is True or str(shadow_raw or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }:
            return None
    return disabled_strategy_side_exit_reason(trade, root_config) or (
        adaptive_blocked_regime_side_exit_reason(trade, hl_cfg)
    )


def perp_side_fee(notional: float, fee_rate_per_side: float) -> float:
    """Taker-style fee for one fill (entry or exit) on notional USD."""
    if notional <= 0 or fee_rate_per_side <= 0:
        return 0.0
    return float(notional) * float(fee_rate_per_side)


def calculate_perp_pnl(
    position_side: str,
    entry_price: float,
    current_price: float,
    size: float,
    fees: float = 0.0,
) -> float:
    """Side-aware gross PnL less supplied fees."""
    side = str(position_side or "").lower()
    if entry_price <= 0 or current_price <= 0 or size <= 0:
        return 0.0
    if side == "short":
        gross = (entry_price - current_price) * size
    else:
        gross = (current_price - entry_price) * size
    return gross - float(fees or 0.0)


def pnl_percentage(position_side: str, entry_price: float, current_price: float) -> float:
    if entry_price <= 0 or current_price <= 0:
        return 0.0
    if str(position_side or "").lower() == "short":
        return ((entry_price - current_price) / entry_price) * 100.0
    return ((current_price - entry_price) / entry_price) * 100.0


def promoted_cohort_selection_boost(
    candidate: Dict[str, Any],
    *,
    coin: str,
    market_regime: str,
    promoted_cohorts: Optional[Iterable[Mapping[str, Any]]],
    hl_cfg: Optional[Dict[str, Any]],
) -> float:
    """Additive cross-strategy score boost when live signal matches a promoted cohort."""
    promotion_cfg = (hl_cfg or {}).get("shadow_cohort_promotion") or {}
    try:
        boost = float(promotion_cfg.get("selection_boost", 0.0) or 0.0)
    except (TypeError, ValueError):
        boost = 0.0
    if boost <= 0 or not promoted_cohorts:
        return 0.0
    strategy = str(candidate.get("strategy") or "").strip().lower()
    side = position_sides_from_signal(candidate.get("signal"))
    regime = str(market_regime or "").strip().lower()
    coin_key = pair_to_hyperliquid_coin(str(coin or ""))
    for cohort in promoted_cohorts:
        cohort_coin = pair_to_hyperliquid_coin(str(cohort.get("coin") or ""))
        if cohort_coin.lower() != coin_key.lower():
            continue
        if str(cohort.get("strategy") or "").strip().lower() != strategy:
            continue
        if str(cohort.get("side") or "").strip().lower() != side:
            continue
        cohort_regime = str(cohort.get("regime") or "").strip().lower()
        if cohort_regime and cohort_regime != regime:
            continue
        return boost
    return 0.0


def perp_lane_notional_multiplier(
    strategy: str,
    side: str,
    regime: str,
    hl_cfg: Optional[Mapping[str, Any]] = None,
) -> float:
    """Return a fixed-paper notional multiplier for one strategy/side/regime lane.

    Fixed paper sizing intentionally ignores the broad adaptive multiplier stack.
    This narrow overlay lets explicitly configured probation lanes use less
    exposure without changing the deterministic notional of validated lanes.
    """
    lane_cfg = (hl_cfg or {}).get("lane_notional_multipliers") or {}
    if not isinstance(lane_cfg, Mapping):
        return 1.0
    strategy_cfg = lane_cfg.get(str(strategy or "").strip().lower()) or {}
    if not isinstance(strategy_cfg, Mapping):
        return 1.0
    regime_cfg = strategy_cfg.get(str(regime or "").strip().lower()) or {}
    if not isinstance(regime_cfg, Mapping):
        return 1.0
    raw = regime_cfg.get(str(side or "").strip().lower())
    if raw is None:
        return 1.0
    try:
        return max(0.05, min(1.0, float(raw)))
    except (TypeError, ValueError):
        return 1.0


def _is_executable_perp_trade(trade: Mapping[str, Any]) -> bool:
    meta = trade.get("metadata") or {}
    if not isinstance(meta, dict):
        meta = {}
    shadow_raw = meta.get("shadow_trade")
    if shadow_raw is True or str(shadow_raw).lower() in {"1", "true", "yes"}:
        return False
    if str(meta.get("accounting_excluded") or "").lower() in {"1", "true", "yes"}:
        return False
    return str(trade.get("status") or "").upper() == "CLOSED"


def executable_size_requalification_passes(
    strategy: str,
    side: str,
    closed_trades: Iterable[Dict[str, Any]],
    hl_cfg: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str]:
    """Gate size boosts until executable paper stats meet the requalification bar."""
    cfg = ((hl_cfg or {}).get("executable_size_requalification") or {})
    enabled = cfg.get("enabled", True)
    if enabled is False or str(enabled).lower() in {"0", "false", "no", "off"}:
        return True, "requal_disabled"

    normalized_strategy = str(strategy or "").strip().lower()
    normalized_side = str(side or "").strip().lower()
    if not normalized_strategy or normalized_side not in {"long", "short"}:
        return False, "requal_missing_strategy_side"

    try:
        min_closed = max(1, int(cfg.get("min_closed_trades", 30) or 30))
        min_span_days = max(1.0, float(cfg.get("min_span_days", 14) or 14))
        min_profit_factor = float(cfg.get("min_profit_factor", 1.25) or 1.25)
        min_realized = float(cfg.get("min_realized_pnl_usd", 0.0) or 0.0)
    except (TypeError, ValueError):
        return False, "requal_invalid_config"

    rows: List[Dict[str, Any]] = []
    for trade in closed_trades or []:
        if not _is_executable_perp_trade(trade):
            continue
        trade_strategy = str(
            trade.get("source_strategy") or trade.get("strategy") or ""
        ).strip().lower()
        trade_side = str(
            trade.get("position_side") or trade.get("source_signal") or ""
        ).strip().lower()
        if trade_side not in {"long", "short"}:
            trade_side = normalize_perp_entry_signal(trade_side) or trade_side
        if trade_strategy != normalized_strategy or trade_side != normalized_side:
            continue
        exit_dt = _parse_dt(trade.get("exit_time") or trade.get("updated_at"))
        if exit_dt is None:
            continue
        try:
            pnl = float(trade.get("realized_pnl") or 0.0)
        except (TypeError, ValueError):
            pnl = 0.0
        rows.append({"pnl": pnl, "exit_time": exit_dt})

    if len(rows) < min_closed:
        return False, f"requal_closed_{len(rows)}_lt_{min_closed}"

    exit_times = [row["exit_time"] for row in rows if row.get("exit_time") is not None]
    if not exit_times:
        return False, "requal_missing_exit_times"
    span_days = (max(exit_times) - min(exit_times)).total_seconds() / 86400.0
    if span_days < min_span_days:
        return False, f"requal_span_{span_days:.1f}d_lt_{min_span_days:.1f}d"

    realized = sum(row["pnl"] for row in rows)
    gross_profit = sum(row["pnl"] for row in rows if row["pnl"] > 0)
    gross_loss = abs(sum(row["pnl"] for row in rows if row["pnl"] < 0))
    if realized < min_realized:
        return False, f"requal_pnl_{realized:.2f}_lt_{min_realized:.2f}"
    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    elif gross_profit > 0:
        profit_factor = float("inf")
    else:
        profit_factor = 0.0
    if profit_factor < min_profit_factor:
        return False, f"requal_pf_{profit_factor:.2f}_lt_{min_profit_factor:.2f}"
    return True, "requal_pass"


def consensus_executable_denylist(hl_cfg: Optional[Dict[str, Any]] = None) -> set[str]:
    """Strategies that must never win the consensus fallback selection path."""
    raw = (hl_cfg or {}).get("consensus_executable_denylist")
    if raw is None:
        return {str(name).strip().lower() for name in DEFAULT_CONSENSUS_EXECUTABLE_DENYLIST}
    if not isinstance(raw, (list, tuple, set)):
        return set()
    return {
        str(name or "").strip().lower()
        for name in raw
        if str(name or "").strip()
    }


def select_mirrored_signal(
    signals_data: Dict[str, Any],
    hl_cfg: Optional[Dict[str, Any]] = None,
    *,
    coin: Optional[str] = None,
    market_regime: Optional[str] = None,
    promoted_cohorts: Optional[Iterable[Mapping[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Pick the best actionable long/short entry intent from a strategy-service payload.

    Consensus long/short gets priority for direction. The executable confidence
    uses the strongest matching strategy because consensus confidence can be
    diluted when most other strategies are correctly holding.
    """
    if not isinstance(signals_data, dict):
        return None

    strategies = signals_data.get("strategies") or {}
    if not isinstance(strategies, dict):
        strategies = {}

    denied_consensus = consensus_executable_denylist(hl_cfg)

    def best_strategy_for(
        side: str,
        *,
        require_runtime_eligibility: bool = False,
    ) -> Dict[str, Any]:
        candidates = []
        for name, data in strategies.items():
            if not isinstance(data, dict):
                continue
            strategy_key = str(name or "").strip().lower()
            if require_runtime_eligibility and strategy_key in denied_consensus:
                continue
            if normalize_perp_entry_signal(data.get("signal", "")) != side:
                continue
            if require_runtime_eligibility and hl_cfg is not None:
                candidate = {
                    "strategy": str(name),
                    "signal": side,
                    "confidence": float(data.get("confidence", 0) or 0),
                    "strength": float(data.get("strength", 0) or 0),
                    "details": data,
                }
                if hyperliquid_min_edge_gate(candidate, hl_cfg).get("blocked"):
                    continue
                specialist_gate = specialist_entry_gate(candidate, hl_cfg)
                if specialist_gate.get("isSpecialist") and not specialist_gate.get("allowed"):
                    continue
            candidates.append(
                (
                    float(data.get("confidence", 0) or 0),
                    float(data.get("strength", 0) or 0),
                    str(name),
                    data,
                )
            )
        if not candidates:
            return {}
        conf, strength, name, data = sorted(candidates, reverse=True)[0]
        return {
            "strategy": name,
            "signal": side,
            "confidence": conf,
            "strength": strength,
            "details": data,
        }

    def strongest_opposite_for(side: str) -> Dict[str, Any]:
        opposite = "short" if side == "long" else "long"
        return best_strategy_for(opposite)

    consensus = signals_data.get("consensus") or {}

    standalone_candidates: List[Dict[str, Any]] = []
    for strategy_name in PRIORITY_STANDALONE_ENTRY_STRATEGIES:
        data = strategies.get(strategy_name) or {}
        if not isinstance(data, dict):
            continue
        side = normalize_perp_entry_signal(data.get("signal", ""))
        if side not in {"long", "short"}:
            continue
        defaults = DEFAULT_STANDALONE_STRATEGY_GATES.get(strategy_name) or {}
        conf = float(data.get("confidence", 0) or 0)
        strength = float(data.get("strength", 0) or 0)
        if (
            conf < float(defaults.get("min_confidence", 0) or 0)
            or strength < float(defaults.get("min_strength", 0) or 0)
        ):
            continue
        selected: Dict[str, Any] = {
            "strategy": strategy_name,
            "signal": side,
            "confidence": conf,
            "strength": strength,
            "consensus_confidence": float(consensus.get("confidence", 0) or 0),
            "consensus_agreement": float(consensus.get("agreement", 0) or 0),
            "details": data,
            "standalone_priority": True,
            "market_regime": str(
                market_regime or signals_data.get("market_regime") or ""
            ).strip().lower(),
        }
        # Do not let a fixed strategy-list order monopolize execution. Rank every
        # complete standalone setup by signal quality, and when runtime config is
        # available discard candidates that are certain to fail the downstream
        # edge/specialist gates. All gates still run again before an order opens.
        if hl_cfg is not None:
            regime_gate = hyperliquid_regime_direction_gate(
                side,
                selected["market_regime"],
                conf,
                strength,
                hl_cfg,
                strategy=strategy_name,
            )
            if regime_gate.get("blocked"):
                continue
            edge_gate = hyperliquid_min_edge_gate(selected, hl_cfg)
            if edge_gate.get("blocked"):
                continue
            specialist_gate = specialist_entry_gate(selected, hl_cfg)
            if specialist_gate.get("isSpecialist") and not specialist_gate.get("allowed"):
                continue
        expected_move = _expected_move_pct_from_signal(selected)
        expected_quality = min(max(float(expected_move or 0.0), 0.0), 2.0) / 2.0
        # Per-strategy edge bias so cross_strategy_selection favours the playbooks
        # the shadow book proves have edge instead of just the loudest signal.
        # See trading.hyperliquid_perps.cross_strategy_selection_bias.
        selection_bias = 0.0
        if hl_cfg is not None:
            raw_bias = hl_cfg.get("cross_strategy_selection_bias")
            if isinstance(raw_bias, dict):
                try:
                    selection_bias = float(raw_bias.get(strategy_name, 0.0) or 0.0)
                except (TypeError, ValueError):
                    selection_bias = 0.0
        if selection_bias:
            selected["selection_bias"] = selection_bias
        promo_boost = promoted_cohort_selection_boost(
            selected,
            coin=str(coin or ""),
            market_regime=str(
                market_regime or signals_data.get("market_regime") or ""
            ),
            promoted_cohorts=promoted_cohorts,
            hl_cfg=hl_cfg,
        )
        if promo_boost:
            selected["promoted_cohort_boost"] = promo_boost
        selected["selection_score"] = round(
            conf * 0.50
            + strength * 0.30
            + expected_quality * 0.20
            + selection_bias
            + promo_boost,
            6,
        )
        if expected_move is not None:
            selected["expected_move_pct"] = expected_move
        opposite = strongest_opposite_for(side)
        if opposite:
            selected["opposite_strategy"] = opposite.get("strategy")
            selected["opposite_confidence"] = float(opposite.get("confidence", 0) or 0)
            selected["opposite_strength"] = float(opposite.get("strength", 0) or 0)
        standalone_candidates.append(selected)

    if standalone_candidates:
        return max(
            standalone_candidates,
            key=lambda item: (
                float(item.get("selection_score") or 0.0),
                float(item.get("expected_move_pct") or 0.0),
                float(item.get("confidence") or 0.0),
                float(item.get("strength") or 0.0),
                str(item.get("strategy") or ""),
            ),
        )

    c_signal = normalize_perp_entry_signal(consensus.get("signal", ""))
    if c_signal in {"long", "short"}:
        best = best_strategy_for(c_signal, require_runtime_eligibility=True)
        if not best:
            return None
        consensus_confidence = float(consensus.get("confidence", 0) or 0)
        best_confidence = float(best.get("confidence", 0) or 0)
        selected = {
            "strategy": best.get("strategy") or "consensus",
            "signal": c_signal,
            "confidence": max(consensus_confidence, best_confidence),
            "strength": float(best.get("strength", 0) or consensus.get("strength", 0) or 0),
            "consensus_confidence": consensus_confidence,
            "consensus_agreement": float(consensus.get("agreement", 0) or 0),
            "details": best.get("details") or {},
        }
        opposite = strongest_opposite_for(c_signal)
        if opposite:
            selected["opposite_strategy"] = opposite.get("strategy")
            selected["opposite_confidence"] = float(opposite.get("confidence", 0) or 0)
            selected["opposite_strength"] = float(opposite.get("strength", 0) or 0)
        return selected

    # Do not fall back to "loudest individual strategy wins" when consensus is
    # HOLD. That path trades unlike the manual workflow: a non-priority strategy
    # can open a position just because it has the highest raw confidence while
    # the selected regime/consensus says no trade. Only explicit standalone
    # playbooks above are allowed to bypass consensus.
    return None


def selected_mirrored_signal_metadata(
    mirrored: Mapping[str, Any],
) -> Dict[str, Any]:
    """Audit-friendly metadata for the exact signal selected for perp entry."""
    if not isinstance(mirrored, Mapping):
        return {}
    details = mirrored.get("details") or {}
    if not isinstance(details, Mapping):
        details = {}
    state = details.get("state") or {}
    if not isinstance(state, Mapping):
        state = {}
    indicators = state.get("indicators") or {}
    if not isinstance(indicators, Mapping):
        indicators = {}

    reason = (
        state.get("entry_reason")
        or details.get("entry_reason")
        or indicators.get("entry_reason_detail")
        or ""
    )
    metadata = {
        "strategy": mirrored.get("strategy"),
        "signal": mirrored.get("signal"),
        "confidence": mirrored.get("confidence"),
        "strength": mirrored.get("strength"),
        "consensus_confidence": mirrored.get("consensus_confidence"),
        "consensus_agreement": mirrored.get("consensus_agreement"),
    }
    if mirrored.get("standalone_priority") is not None:
        metadata["standalone_priority"] = bool(mirrored.get("standalone_priority"))
    if reason:
        metadata["reason"] = reason
    for key in ("opposite_strategy", "opposite_confidence", "opposite_strength"):
        if mirrored.get(key) is not None:
            metadata[key] = mirrored.get(key)
    setup_risk = setup_risk_metadata_from_signal(dict(mirrored))
    if setup_risk:
        metadata["setup_risk"] = setup_risk
    return metadata


SETUP_RISK_KEYS = (
    "stop_hint",
    "target_hint",
    "stop_pct",
    "target_pct",
    "reward_risk",
    "entry_price",
    "breakeven_trigger_swing_high",
    "partial_profit_sma_extension_pct",
    "measured_move",
    "entry_reason",
    "setup",
    "pattern_type",
)


def _setup_distance_pct(entry_price: float, price_hint: float) -> float:
    """Absolute entry-to-level distance in percent (works for long and short)."""
    if entry_price <= 0 or price_hint <= 0:
        return 0.0
    return abs(price_hint - entry_price) / entry_price * 100.0


def _normalize_published_setup_pct(raw: float) -> float:
    """Engines publish decimal fractions (<0.1); exits expect percent."""
    if raw <= 0:
        return 0.0
    if raw < 0.1:
        return raw * 100.0
    return raw


def resolve_setup_target_pct(
    setup: Mapping[str, Any],
    entry_price: float = 0.0,
    min_target_pct: float = 0.0,
) -> float:
    """Resolve setup take-profit distance in percent for exit checks."""
    if not isinstance(setup, dict):
        return 0.0
    entry_px = _safe_float(setup.get("entry_price"), entry_price)
    target_hint = _safe_float(setup.get("target_hint"), 0.0)
    if target_hint > 0 and entry_px > 0:
        resolved = _setup_distance_pct(entry_px, target_hint)
    else:
        resolved = _normalize_published_setup_pct(_safe_float(setup.get("target_pct"), 0.0))
    floor = _safe_float(min_target_pct, 0.0)
    if floor > 0 and resolved > 0:
        resolved = max(resolved, floor)
    return resolved


def resolve_setup_stop_pct(
    setup: Mapping[str, Any],
    entry_price: float = 0.0,
) -> float:
    """Resolve setup stop distance in percent for exit checks."""
    if not isinstance(setup, dict):
        return 0.0
    entry_px = _safe_float(setup.get("entry_price"), entry_price)
    stop_hint = _safe_float(setup.get("stop_hint"), 0.0)
    if stop_hint > 0 and entry_px > 0:
        return _setup_distance_pct(entry_px, stop_hint)
    return _normalize_published_setup_pct(_safe_float(setup.get("stop_pct"), 0.0))


def setup_risk_metadata_from_signal(signal: Dict[str, Any]) -> Dict[str, Any]:
    """Extract setup-aware stop/target metadata from a strategy signal."""
    if not isinstance(signal, dict):
        return {}
    meta: Dict[str, Any] = {}
    for source in _indicator_sources(signal):
        for key in SETUP_RISK_KEYS:
            if key not in source:
                continue
            value = source.get(key)
            if value is None:
                continue
            meta[key] = value
    if not meta:
        strat_data = signal.get("strategy_data") or {}
        if isinstance(strat_data, dict):
            state = strat_data.get("state") or strat_data
            indicators = state.get("indicators") if isinstance(state, dict) else {}
            if isinstance(indicators, dict):
                for key in SETUP_RISK_KEYS:
                    if key in indicators and indicators.get(key) is not None:
                        meta[key] = indicators.get(key)
    if not meta:
        return {}
    entry_price = _safe_float(meta.get("entry_price"), 0.0)
    target_hint = _safe_float(meta.get("target_hint"), 0.0)
    stop_hint = _safe_float(meta.get("stop_hint"), 0.0)
    if entry_price > 0 and stop_hint > 0:
        meta["stop_pct"] = _setup_distance_pct(entry_price, stop_hint)
    else:
        stop_pct = _safe_float(meta.get("stop_pct"), 0.0)
        if stop_pct > 0:
            meta["stop_pct"] = _normalize_published_setup_pct(stop_pct)
    if entry_price > 0 and target_hint > 0:
        meta["target_pct"] = _setup_distance_pct(entry_price, target_hint)
    else:
        target_pct = _safe_float(meta.get("target_pct"), 0.0)
        if target_pct > 0:
            meta["target_pct"] = _normalize_published_setup_pct(target_pct)
    if not meta.get("setup"):
        strategy_name = str(
            signal.get("strategy")
            or signal.get("source_strategy")
            or meta.get("strategy")
            or ""
        ).strip().lower()
        if strategy_name:
            meta["setup"] = strategy_name
    return meta


def encode_setup_risk_entry_reason(base_reason: str, setup_risk: Dict[str, Any]) -> str:
    """Persist setup risk on spot trades via entry_reason suffix."""
    reason = str(base_reason or "").strip()
    if not setup_risk:
        return reason
    try:
        import json

        payload = json.dumps(setup_risk, separators=(",", ":"), sort_keys=True)
    except (TypeError, ValueError):
        return reason
    marker = f" [setup:{payload}]"
    if marker in reason:
        return reason
    return f"{reason}{marker}" if reason else marker.strip()


def parse_setup_risk_from_entry_reason(entry_reason: str) -> Dict[str, Any]:
    text = str(entry_reason or "")
    start = text.rfind(" [setup:")
    if start < 0:
        return {}
    blob = text[start + len(" [setup:") :]
    if blob.endswith("]"):
        blob = blob[:-1]
    try:
        import json

        parsed = json.loads(blob)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def setup_risk_from_trade_metadata(trade: Mapping[str, Any]) -> Dict[str, Any]:
    """Read persisted setup risk from a paper/spot trade record."""
    if not isinstance(trade, Mapping):
        return {}
    metadata = trade.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    setup = metadata.get("setup_risk")
    if isinstance(setup, dict) and setup:
        return dict(setup)
    hl_selected = metadata.get("hl_selected") or {}
    if isinstance(hl_selected, dict):
        nested = hl_selected.get("setup_risk")
        if isinstance(nested, dict) and nested:
            return dict(nested)
    parsed = parse_setup_risk_from_entry_reason(str(trade.get("entry_reason") or ""))
    return parsed if isinstance(parsed, dict) else {}


def paper_strategy_allowlist_block(
    strategy: str,
    hl_cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Block real paper entries when paper_strategy_allowlist is non-empty and strategy is absent.

    Empty / missing allowlist means no restriction (all enabled strategies may paper-enter).
    Shadow evaluation is unaffected — callers should only invoke this on real paper opens.
    """
    raw = (hl_cfg or {}).get("paper_strategy_allowlist")
    if raw is None:
        return {"blocked": False, "reason": "paper_allowlist_unset"}
    if not isinstance(raw, (list, tuple)):
        return {"blocked": False, "reason": "paper_allowlist_invalid"}
    allow = [str(s).strip().lower() for s in raw if str(s).strip()]
    if not allow:
        return {"blocked": False, "reason": "paper_allowlist_empty"}
    key = str(strategy or "").strip().lower()
    if key in allow:
        return {"blocked": False, "reason": "paper_allowlist_ok", "allowlist": allow}
    return {
        "blocked": True,
        "reason": "paper_allowlist",
        "message": f"Strategy {strategy or 'unknown'} not on paper allowlist",
        "allowlist": allow,
    }


def shadow_promotion_sample_thresholds(
    strategy: str,
    promotion_cfg: Optional[Mapping[str, Any]] = None,
) -> Dict[str, int]:
    """Resolve min_closed / min_episodes, with optional per-strategy overrides.

    Phase C swing lanes (e.g. donchian_atr_pullback) can require ≥20 episodes
    while other strategies keep the global 8-episode floor.
    """
    cfg = promotion_cfg or {}
    min_closed = int(cfg.get("min_closed", 8) or 8)
    min_episodes = int(cfg.get("min_episodes", min_closed) or min_closed)
    overrides = cfg.get("strategy_overrides") or {}
    if isinstance(overrides, Mapping):
        strat_cfg = overrides.get(str(strategy or "").strip().lower()) or {}
        if isinstance(strat_cfg, Mapping):
            if strat_cfg.get("min_closed") is not None:
                try:
                    min_closed = int(strat_cfg.get("min_closed") or min_closed)
                except (TypeError, ValueError):
                    pass
            if strat_cfg.get("min_episodes") is not None:
                try:
                    min_episodes = int(strat_cfg.get("min_episodes") or min_episodes)
                except (TypeError, ValueError):
                    pass
    return {"min_closed": max(1, min_closed), "min_episodes": max(1, min_episodes)}


def strategy_risk_per_trade_pct(
    strategy: str,
    hl_cfg: Optional[Dict[str, Any]] = None,
) -> float:
    """Per-strategy risk budget override for risk-based notional sizing."""
    key = str(strategy or "").strip().lower()
    overrides = ((hl_cfg or {}).get("strategy_risk_overrides") or {})
    if isinstance(overrides, dict):
        strat_cfg = overrides.get(key) or {}
        if isinstance(strat_cfg, dict) and strat_cfg.get("risk_per_trade_pct") is not None:
            return _safe_float(strat_cfg.get("risk_per_trade_pct"), 0.0075)
    risk_cfg = ((hl_cfg or {}).get("risk_based_sizing") or {})
    return _safe_float(risk_cfg.get("risk_per_trade_pct", 0.0075), 0.0075)


def strategy_max_notional(
    strategy: str,
    hl_cfg: Optional[Dict[str, Any]] = None,
) -> float:
    """Per-strategy notional cap override."""
    key = str(strategy or "").strip().lower()
    overrides = ((hl_cfg or {}).get("strategy_notional_overrides") or {})
    if isinstance(overrides, dict):
        strat_cfg = overrides.get(key) or {}
        if isinstance(strat_cfg, dict) and strat_cfg.get("max_notional_per_trade") is not None:
            return _safe_float(strat_cfg.get("max_notional_per_trade"), 200.0)
    return _safe_float((hl_cfg or {}).get("max_notional_per_trade", 200.0), 200.0)


def strategy_min_notional(
    strategy: str,
    hl_cfg: Optional[Dict[str, Any]] = None,
) -> float:
    """Per-strategy notional floor for skipping fee-dominated paper entries."""
    key = str(strategy or "").strip().lower()
    overrides = ((hl_cfg or {}).get("strategy_notional_overrides") or {})
    if isinstance(overrides, dict):
        strat_cfg = overrides.get(key) or {}
        if isinstance(strat_cfg, dict) and strat_cfg.get("min_notional_per_trade") is not None:
            return _safe_float(strat_cfg.get("min_notional_per_trade"), 0.0)
    return _safe_float((hl_cfg or {}).get("min_notional_per_trade", 0.0), 0.0)


def dynamic_min_notional(
    signal: Dict[str, Any],
    hl_cfg: Optional[Dict[str, Any]] = None,
    *,
    size_multiplier: float = 1.0,
) -> Dict[str, Any]:
    """Return a fee/edge-aware notional floor that follows adaptive sizing.

    The old global floor treated a reduced-risk trade like a full-size trade.
    This floor instead targets a small absolute net profit at the signal's
    expected edge, while preserving an exchange/order floor and optional
    per-strategy values.  Every strategy value scales down with the final
    position-size multiplier; the exchange floor does not.
    """
    cfg = (hl_cfg or {}).get("dynamic_notional_gate") or {}
    enabled = cfg.get("enabled", False)
    if enabled is False or str(enabled).lower() in {"0", "false", "no", "off"}:
        legacy = strategy_min_notional(str((signal or {}).get("strategy") or ""), hl_cfg)
        return {
            "enabled": False,
            "minNotional": max(0.0, legacy),
            "reason": "legacy_min_notional",
        }

    strategy = str((signal or {}).get("strategy") or "").strip().lower()
    overrides = cfg.get("strategy_overrides") or {}
    strategy_cfg = overrides.get(strategy) or {} if isinstance(overrides, dict) else {}
    if not isinstance(strategy_cfg, dict):
        strategy_cfg = {}

    multiplier = max(0.0, float(size_multiplier or 0.0))
    exchange_floor = max(
        0.0,
        _safe_float(cfg.get("exchange_min_notional_usd", 10.0), 10.0),
    )
    base_floor = max(
        0.0,
        _safe_float(
            strategy_cfg.get(
                "minimum_viable_notional_usd",
                cfg.get("minimum_viable_notional_usd", 25.0),
            ),
            25.0,
        ),
    )
    target_profit = max(
        0.0,
        _safe_float(
            strategy_cfg.get(
                "min_expected_net_profit_usd",
                cfg.get("min_expected_net_profit_usd", 0.25),
            ),
            0.25,
        ),
    )
    fallback_floor = max(
        0.0,
        _safe_float(
            strategy_cfg.get(
                "fallback_min_notional_usd",
                cfg.get("fallback_min_notional_usd", base_floor),
            ),
            base_floor,
        ),
    )
    max_floor = max(
        exchange_floor,
        _safe_float(cfg.get("max_dynamic_min_notional_usd", 200.0), 200.0),
    )

    edge = hyperliquid_min_edge_gate(signal, hl_cfg)
    expected_pct = edge.get("expectedMovePct")
    cost_pct = max(0.0, _safe_float(edge.get("estimatedCostPct"), 0.0))
    scaled_base = base_floor * multiplier
    scaled_target_profit = target_profit * multiplier
    if expected_pct is None:
        required = fallback_floor * multiplier
        reason = "dynamic_min_notional_fallback"
        net_edge_pct = None
    else:
        net_edge_pct = max(0.0, _safe_float(expected_pct, 0.0) - cost_pct)
        required = (
            scaled_target_profit / (net_edge_pct / 100.0)
            if scaled_target_profit > 0 and net_edge_pct > 0
            else scaled_base
        )
        reason = "dynamic_min_notional_fee_edge"

    floor = max(exchange_floor, scaled_base, required)
    floor = min(floor, max_floor)
    return {
        "enabled": True,
        "minNotional": floor,
        "reason": reason,
        "strategy": strategy,
        "sizeMultiplier": multiplier,
        "expectedMovePct": expected_pct,
        "estimatedCostPct": cost_pct,
        "netEdgePct": net_edge_pct,
        "targetNetProfitUsd": scaled_target_profit,
    }


def adaptive_perp_leverage(
    signal: Dict[str, Any],
    hl_cfg: Optional[Dict[str, Any]] = None,
) -> float:
    """Choose 1x-5x leverage from setup stop distance and strategy limits.

    Leverage changes collateral usage only.  The caller must continue sizing
    notional from the risk budget so higher leverage cannot increase loss at
    the setup stop.
    """
    cfg = (hl_cfg or {}).get("adaptive_leverage") or {}
    default = _safe_float(
        cfg.get("default", (hl_cfg or {}).get("default_leverage", 2.0)),
        2.0,
    )
    enabled = cfg.get("enabled", False)
    if enabled is False or str(enabled).lower() in {"0", "false", "no", "off"}:
        return max(1.0, min(5.0, default))

    strategy = str((signal or {}).get("strategy") or "").strip().lower()
    overrides = cfg.get("strategy_overrides") or {}
    strategy_cfg = overrides.get(strategy) or {} if isinstance(overrides, dict) else {}
    if not isinstance(strategy_cfg, dict):
        strategy_cfg = {}
    minimum = max(1.0, _safe_float(strategy_cfg.get("min", cfg.get("min", 1.0)), 1.0))
    maximum = min(5.0, _safe_float(strategy_cfg.get("max", cfg.get("max", 5.0)), 5.0))
    if maximum < minimum:
        maximum = minimum
    selected = _safe_float(strategy_cfg.get("default", default), default)
    stop_pct = stop_distance_pct_from_signal(signal, hl_cfg)
    tight_stop = _safe_float(cfg.get("tight_stop_max_pct", 0.60), 0.60)
    medium_stop = _safe_float(cfg.get("medium_stop_max_pct", 1.25), 1.25)
    if stop_pct <= tight_stop:
        selected = maximum
    elif stop_pct <= medium_stop:
        selected = min(maximum, selected + 1.0)
    elif stop_pct > _safe_float(cfg.get("wide_stop_min_pct", 2.0), 2.0):
        selected = minimum
    return max(minimum, min(maximum, selected))


def strategy_sizing_tier_multiplier(
    strategy: str,
    trading_cfg: Optional[Dict[str, Any]] = None,
) -> float:
    """Relative sizing tier — sma_reclaim_bull_flag is configured as the top tier."""
    key = str(strategy or "").strip().lower()
    tiers = ((trading_cfg or {}).get("strategy_sizing_tiers") or {})
    if not isinstance(tiers, dict):
        return 1.0
    multipliers = tiers.get("multipliers") or {}
    if isinstance(multipliers, dict) and key in multipliers:
        return max(0.0, min(1.5, _safe_float(multipliers.get(key), 1.0)))
    ordered = [str(item or "").strip().lower() for item in (tiers.get("ordered") or [])]
    default_mult = _safe_float(tiers.get("default_multiplier", 0.70), 0.70)
    if key in ordered:
        rank = ordered.index(key)
        return max(0.0, min(1.5, 1.0 - rank * 0.15))
    return max(0.0, min(1.5, default_mult))


def strategy_min_size_multiplier(
    strategy: str,
    trading_cfg: Optional[Dict[str, Any]] = None,
) -> Optional[float]:
    """Optional floor so adaptive loss haircuts cannot starve priority strategies."""
    key = str(strategy or "").strip().lower()
    tiers = ((trading_cfg or {}).get("strategy_sizing_tiers") or {})
    if isinstance(tiers, dict):
        floor_key = f"{key}_min_multiplier"
        if tiers.get(floor_key) is not None:
            return max(0.0, min(1.5, _safe_float(tiers.get(floor_key), 1.0)))
    adaptive = ((trading_cfg or {}).get("adaptive_position_sizing") or {})
    mins = (adaptive.get("strategy_min_multipliers") or {}) if isinstance(adaptive, dict) else {}
    if isinstance(mins, dict) and key in mins:
        return max(0.0, min(1.5, _safe_float(mins.get(key), 1.0)))
    return None


@dataclass(frozen=True)
class PaperPerpExitConfig:
    """Exit rules for HL paper perps — mirrors spot trading.trailing_stop / profit_protection."""

    use_spot_exit_rules: bool = True
    fixed_stop_loss_enabled: bool = True
    stop_loss_pct: float = 1.5
    take_profit_pct: float = 0.0
    max_holding_minutes: int = 240
    # Phase 3 (2026-05-27): hard cap once a salvage trail is engaged. 0 disables salvage.
    max_holding_minutes_hard: int = 360
    overall_take_profit_pct: float = 4.5

    trailing_enabled: bool = True
    trailing_activation_decimal: float = 0.0050
    trailing_step_decimal: float = 0.0020
    tightened_step_decimal: float = 0.0015
    dynamic_tightening_enabled: bool = True
    tighten_profit_threshold_decimal: float = 0.0050
    breakeven_floor_decimal: float = 0.0035
    min_trigger_distance_decimal: float = 0.0035

    profit_protection_enabled: bool = True
    profit_protection_activation_decimal: float = 0.0035
    fee_rate_per_side: float = 0.001
    profit_protection_fee_buffer: float = 0.0015
    effective_profit_floor_decimal: float = 0.0035
    early_profit_locks: Dict[str, Any] = field(default_factory=dict)

    # Phase 3 (2026-05-27): ATR-based stop loss override.
    # When enabled and trade metadata carries `entry_atr_pct`, the effective
    # stop loss = clamp(atr_pct * stop_loss_atr_mult, min_pct, max_pct).
    # Falls back to fixed stop_loss_pct when ATR is unavailable.
    stop_loss_atr_enabled: bool = False
    stop_loss_atr_mult: float = 1.8
    stop_loss_atr_min_pct: float = 0.9
    stop_loss_atr_max_pct: float = 3.0
    # Phase 3 (2026-05-27): per-coin stop loss override map (coin → stop pct).
    # Highest priority — used regardless of ATR availability.
    per_coin_stop_overrides: Dict[str, float] = field(default_factory=dict)

    # Ported from spot stagnant_loser (2026-05-28): pre-empt full SL on never-armed chop.
    stagnant_loser_enabled: bool = True
    stagnant_loser: Dict[str, Any] = field(default_factory=dict)

    use_setup_stops: bool = False
    use_setup_targets: bool = False
    breakeven_on_swing_high: bool = False
    partial_profit_pct: float = 0.0
    partial_profit_sma_extension_pct: float = 0.05

    dollar_loss_cap_enabled: bool = False
    dollar_loss_soft_pct: float = 0.0
    dollar_loss_hard_pct: float = 0.0
    dollar_loss_hard_pct_by_strategy: Dict[str, float] = field(default_factory=dict)
    dollar_loss_cap_default_usd: float = 0.0
    dollar_loss_cap_by_strategy: Dict[str, float] = field(default_factory=dict)
    dollar_loss_recovery_soft_usd: float = 0.0
    dollar_loss_recovery_minutes: float = 0.0
    dollar_loss_suppress_percentage_stop: bool = True

    time_decay_exit_enabled: bool = False
    time_decay_min_age_minutes: float = 0.0
    time_decay_max_loss_usd: float = 0.0
    time_decay_min_age_by_strategy: Dict[str, float] = field(default_factory=dict)


@dataclass
class PaperPerpExitResult:
    exit_reason: Optional[str]
    metadata: Dict[str, Any]
    exit_price: Optional[float] = None


def paper_perp_exit_config_from_yaml(
    hl_cfg: Dict[str, Any],
    trading_cfg: Dict[str, Any],
    strategy_name: str = "",
) -> PaperPerpExitConfig:
    """Build exit config from hyperliquid_perps + global trading sections."""
    hl_cfg = hl_cfg or {}
    trading_cfg = trading_cfg or {}
    strategy_key = str(strategy_name or "").strip().lower()
    selected_profile: Dict[str, Any] = {}
    for profile in (hl_cfg.get("exit_profiles") or {}).values():
        if not isinstance(profile, dict):
            continue
        strategies = {
            str(item or "").strip().lower() for item in (profile.get("strategies") or [])
        }
        if strategy_key and strategy_key in strategies:
            selected_profile = profile
            break
    if selected_profile:
        merged = dict(hl_cfg)
        for key, value in selected_profile.items():
            if key == "strategies":
                continue
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = {**merged[key], **value}
            else:
                merged[key] = value
        hl_cfg = merged
    # Perp-specific overrides win over global spot trailing / profit protection.
    trailing = hl_cfg.get("trailing_stop") or trading_cfg.get("trailing_stop") or {}
    pp = hl_cfg.get("profit_protection") or trading_cfg.get("profit_protection") or {}
    stagnant_raw = hl_cfg.get("stagnant_loser") or trading_cfg.get("stagnant_loser") or {}
    stagnant_loser = dict(stagnant_raw) if isinstance(stagnant_raw, dict) else {}

    overall_dec = float(trading_cfg.get("overall_profit_take_exit_pct", 0.045) or 0.0)
    overall_pct = overall_dec * 100.0 if overall_dec > 0 else 0.0

    stop_loss_pct = float(hl_cfg.get("stop_loss_pct", 1.5) or 1.5)
    spot_sl = trading_cfg.get("stop_loss_percentage")
    if spot_sl is not None and "stop_loss_pct" not in selected_profile:
        try:
            stop_loss_pct = abs(float(spot_sl) * 100.0)
        except (TypeError, ValueError):
            pass

    take_profit_pct = float(hl_cfg.get("take_profit_pct", 0) or 0)
    use_spot = bool(hl_cfg.get("use_spot_exit_rules", True))

    def _dec(key: str, default: float) -> float:
        try:
            return float(trailing.get(key, default) or default)
        except (TypeError, ValueError):
            return default

    step = _dec("step_percentage", 0.0020)
    tightened = _dec("tightened_step_percentage", step)
    if tightened <= 0:
        tightened = step

    try:
        pp_activation = float(pp.get("activation_threshold", 0.0035) or 0.0035)
    except (TypeError, ValueError):
        pp_activation = 0.0035

    try:
        fee_rate_per_side = float(hl_cfg.get("fee_rate_per_side", 0.001) or 0.001)
    except (TypeError, ValueError):
        fee_rate_per_side = 0.001
    try:
        fee_buffer = float(hl_cfg.get("profit_protection_fee_buffer", 0.0015) or 0.0015)
    except (TypeError, ValueError):
        fee_buffer = 0.0015
    fee_floor = max(0.0, (fee_rate_per_side * 2.0) + fee_buffer)
    # Lock floor honors guaranteed_min_profit / break_even_plus, then fee floor.
    configured_lock_floor = resolve_profit_lock_floor_decimal(
        trailing,
        pp,
        default_floor=0.0035,
    )
    breakeven_floor = max(configured_lock_floor, fee_floor)
    min_trigger_distance = max(_dec("min_trigger_distance_percentage", 0.0035), fee_floor)
    trailing_activation = max(_dec("activation_threshold", 0.0050), fee_floor)
    pp_activation = max(pp_activation, fee_floor)

    if strategy_key in {
        "rsi_stoch_reversal_15m",
        "rsi_stoch_reversal_5m",
        "rsi_stoch_reversal_1m",
    }:
        risk_cfg = (
            trading_cfg.get(f"{strategy_key}_risk")
            or trading_cfg.get("rsi_stoch_reversal_15m_risk")
            or {}
        )
        try:
            pp_activation = max(
                float(risk_cfg.get("profit_activation_threshold", pp_activation) or pp_activation),
                fee_floor,
            )
        except (TypeError, ValueError):
            pass
        try:
            trailing_activation = max(
                float(risk_cfg.get("trailing_activation_threshold", trailing_activation) or trailing_activation),
                fee_floor,
            )
        except (TypeError, ValueError):
            pass
    elif strategy_key == "macd_momentum":
        risk_cfg = trading_cfg.get("macd_continuation_risk") or {}
        try:
            pp_activation = max(
                float(risk_cfg.get("profit_activation_threshold", pp_activation) or pp_activation),
                fee_floor,
            )
        except (TypeError, ValueError):
            pass
        try:
            trailing_activation = max(
                float(risk_cfg.get("trailing_activation_threshold", trailing_activation) or trailing_activation),
                fee_floor,
            )
        except (TypeError, ValueError):
            pass

    atr_cfg = hl_cfg.get("stop_loss_atr") or {}
    atr_enabled = bool(atr_cfg.get("enabled", False))
    try:
        atr_mult = float(atr_cfg.get("mult", 1.8) or 1.8)
    except (TypeError, ValueError):
        atr_mult = 1.8
    try:
        atr_min_pct = float(atr_cfg.get("min_pct", 0.9) or 0.9)
    except (TypeError, ValueError):
        atr_min_pct = 0.9
    try:
        atr_max_pct = float(atr_cfg.get("max_pct", 3.0) or 3.0)
    except (TypeError, ValueError):
        atr_max_pct = 3.0

    raw_overrides = hl_cfg.get("per_coin_stop_overrides") or {}
    overrides: Dict[str, float] = {}
    if isinstance(raw_overrides, dict):
        for coin, pct in raw_overrides.items():
            try:
                overrides[str(coin).strip().upper()] = float(pct)
            except (TypeError, ValueError):
                continue

    try:
        max_hold_hard = int(hl_cfg.get("max_holding_minutes_hard", 360) or 0)
    except (TypeError, ValueError):
        max_hold_hard = 360

    stagnant_enabled = hl_cfg.get("stagnant_loser_enabled")
    if stagnant_enabled is None:
        stagnant_enabled = True
    else:
        stagnant_enabled = bool(stagnant_enabled)

    dollar_cap_cfg = hl_cfg.get("dollar_loss_cap") or {}
    if not isinstance(dollar_cap_cfg, dict):
        dollar_cap_cfg = {}
    raw_strategy_caps = dollar_cap_cfg.get("strategy_max_loss_usd") or {}
    dollar_cap_by_strategy: Dict[str, float] = {}
    if isinstance(raw_strategy_caps, dict):
        for strategy, value in raw_strategy_caps.items():
            cap = _safe_float(value, 0.0)
            if cap > 0:
                dollar_cap_by_strategy[str(strategy or "").strip().lower()] = cap
    raw_strategy_pct_caps = dollar_cap_cfg.get("strategy_hard_loss_pct") or {}
    dollar_hard_pct_by_strategy: Dict[str, float] = {}
    if isinstance(raw_strategy_pct_caps, dict):
        for strategy, value in raw_strategy_pct_caps.items():
            cap = _safe_float(value, 0.0)
            if cap > 0:
                dollar_hard_pct_by_strategy[str(strategy or "").strip().lower()] = cap

    time_decay_cfg = hl_cfg.get("time_decay_exit") or {}
    if not isinstance(time_decay_cfg, dict):
        time_decay_cfg = {}
    raw_time_decay_ages = time_decay_cfg.get("strategy_min_age_minutes") or {}
    time_decay_min_age_by_strategy: Dict[str, float] = {}
    if isinstance(raw_time_decay_ages, dict):
        for strategy, value in raw_time_decay_ages.items():
            minutes = _safe_float(value, 0.0)
            if minutes > 0:
                time_decay_min_age_by_strategy[str(strategy or "").strip().lower()] = minutes

    return PaperPerpExitConfig(
        use_spot_exit_rules=use_spot,
        fixed_stop_loss_enabled=bool(hl_cfg.get("fixed_stop_loss_enabled", True)),
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct if not use_spot else 0.0,
        max_holding_minutes=int(hl_cfg.get("max_holding_minutes", 240) or 240),
        max_holding_minutes_hard=max_hold_hard,
        overall_take_profit_pct=overall_pct,
        trailing_enabled=is_feature_enabled(trailing, default=True),
        trailing_activation_decimal=trailing_activation,
        trailing_step_decimal=step,
        tightened_step_decimal=tightened,
        dynamic_tightening_enabled=bool(trailing.get("dynamic_tightening_enabled", True)),
        tighten_profit_threshold_decimal=_dec("tighten_profit_threshold", 0.0050),
        breakeven_floor_decimal=breakeven_floor,
        min_trigger_distance_decimal=min_trigger_distance,
        profit_protection_enabled=is_feature_enabled(pp, default=True),
        profit_protection_activation_decimal=pp_activation,
        fee_rate_per_side=fee_rate_per_side,
        profit_protection_fee_buffer=fee_buffer,
        effective_profit_floor_decimal=fee_floor,
        early_profit_locks=dict(pp.get("early_profit_locks") or {}),
        stop_loss_atr_enabled=atr_enabled,
        stop_loss_atr_mult=atr_mult,
        stop_loss_atr_min_pct=atr_min_pct,
        stop_loss_atr_max_pct=atr_max_pct,
        per_coin_stop_overrides=overrides,
        stagnant_loser_enabled=stagnant_enabled,
        stagnant_loser=stagnant_loser,
        use_setup_stops=bool(hl_cfg.get("use_setup_stops", False)),
        use_setup_targets=bool(hl_cfg.get("use_setup_targets", False)),
        breakeven_on_swing_high=bool(hl_cfg.get("breakeven_on_swing_high", False)),
        partial_profit_pct=_safe_float(hl_cfg.get("partial_profit_pct", 0.0), 0.0),
        partial_profit_sma_extension_pct=_safe_float(
            hl_cfg.get("partial_profit_sma_extension_pct", 0.05),
            0.05,
        ),
        dollar_loss_cap_enabled=bool(dollar_cap_cfg.get("enabled", False)),
        dollar_loss_cap_default_usd=_safe_float(
            dollar_cap_cfg.get("default_max_loss_usd", 0.0),
            0.0,
        ),
        dollar_loss_soft_pct=_safe_float(
            dollar_cap_cfg.get("soft_loss_pct", 0.0),
            0.0,
        ),
        dollar_loss_hard_pct=_safe_float(
            dollar_cap_cfg.get("hard_loss_pct", 0.0),
            0.0,
        ),
        dollar_loss_hard_pct_by_strategy=dollar_hard_pct_by_strategy,
        dollar_loss_cap_by_strategy=dollar_cap_by_strategy,
        dollar_loss_recovery_soft_usd=_safe_float(
            dollar_cap_cfg.get("soft_loss_usd", 0.0),
            0.0,
        ),
        dollar_loss_recovery_minutes=_safe_float(
            dollar_cap_cfg.get("max_recovery_minutes", 0.0),
            0.0,
        ),
        dollar_loss_suppress_percentage_stop=bool(
            dollar_cap_cfg.get("suppress_percentage_stop", True)
        ),
        time_decay_exit_enabled=bool(time_decay_cfg.get("enabled", False)),
        time_decay_min_age_minutes=_safe_float(
            time_decay_cfg.get("min_age_minutes", 0.0),
            0.0,
        ),
        time_decay_max_loss_usd=_safe_float(
            time_decay_cfg.get("max_loss_usd", 0.0),
            0.0,
        ),
        time_decay_min_age_by_strategy=time_decay_min_age_by_strategy,
    )


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _strategy_family(strategy: str) -> str:
    return HYPERLIQUID_STRATEGY_FAMILIES.get(str(strategy or "").strip().lower(), "standalone")


def hyperliquid_standalone_entry_gate(
    signal: Dict[str, Any],
    hl_cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    General standalone gate for HL-native strategy playbooks.

    These strategies are heterogeneous; HOLD from unrelated playbooks means
    "not my setup" rather than a veto. This gate lets configured complete
    playbooks bypass global all-strategy agreement while retaining their own
    quality thresholds and a strong opposite-signal safety check.
    """
    strategy = str((signal or {}).get("strategy") or "").strip().lower()
    side = normalize_perp_entry_signal((signal or {}).get("signal"))
    defaults = DEFAULT_STANDALONE_STRATEGY_GATES.get(strategy)
    if defaults is None:
        return {
            "isStandalone": False,
            "allowed": False,
            "bypassConsensus": False,
            "reason": "strategy_not_standalone",
            "family": _strategy_family(strategy),
            "sizeMultiplier": None,
        }

    root = (hl_cfg or {}).get("standalone_strategy_gates") or {}
    global_cfg = root.get("global") or {}
    strategy_cfg = root.get(strategy) or {}
    enabled = strategy_cfg.get("enabled", global_cfg.get("enabled", True))
    if enabled is False or str(enabled).lower() in {"0", "false", "no", "off"}:
        return {
            "isStandalone": True,
            "allowed": False,
            "bypassConsensus": False,
            "reason": "standalone_gate_disabled",
            "family": _strategy_family(strategy),
            "sizeMultiplier": None,
        }

    side_conf_key = f"min_confidence_{side}" if side in {"long", "short"} else None
    if side_conf_key and side_conf_key in strategy_cfg:
        min_conf = _safe_float(strategy_cfg[side_conf_key], defaults["min_confidence"])
    else:
        min_conf = _safe_float(
            strategy_cfg.get("min_confidence", global_cfg.get("min_confidence", defaults["min_confidence"])),
            defaults["min_confidence"],
        )
    min_strength = _safe_float(
        strategy_cfg.get("min_strength", global_cfg.get("min_strength", defaults["min_strength"])),
        defaults["min_strength"],
    )
    size_mult_raw = strategy_cfg.get("size_multiplier", defaults.get("size_multiplier"))
    size_mult = None if size_mult_raw is None else max(0.0, min(1.0, _safe_float(size_mult_raw, 1.0)))

    opposite_cfg = global_cfg.get("strong_opposition_block") or {}
    block_opposite = opposite_cfg.get("enabled", True)
    opposite_conf_threshold = _safe_float(opposite_cfg.get("min_confidence", 0.85), 0.85)
    opposite_strength_threshold = _safe_float(opposite_cfg.get("min_strength", 0.65), 0.65)

    conf = _safe_float((signal or {}).get("confidence"), 0.0)
    strength = _safe_float((signal or {}).get("strength"), 0.0)
    opposite_conf = _safe_float((signal or {}).get("opposite_confidence"), 0.0)
    opposite_strength = _safe_float((signal or {}).get("opposite_strength"), 0.0)

    failures = []
    if side not in {"long", "short"}:
        failures.append("not_directional")
    if conf < min_conf:
        failures.append(f"confidence_{conf:.2f}_lt_{min_conf:.2f}")
    if strength < min_strength:
        failures.append(f"strength_{strength:.2f}_lt_{min_strength:.2f}")
    if (
        block_opposite is not False
        and opposite_conf >= opposite_conf_threshold
        and opposite_strength >= opposite_strength_threshold
    ):
        failures.append(
            f"opposite_{opposite_conf:.2f}_{opposite_strength:.2f}_gte_"
            f"{opposite_conf_threshold:.2f}_{opposite_strength_threshold:.2f}"
        )

    return {
        "isStandalone": True,
        "allowed": not failures,
        "bypassConsensus": not failures,
        "reason": ",".join(failures) if failures else "standalone_gate_pass",
        "family": _strategy_family(strategy),
        "sizeMultiplier": size_mult,
    }


def sma_reclaim_bull_flag_specialist_gate(
    signal: Dict[str, Any],
    hl_cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Dedicated long-only gate for SMA reclaim bull flag entries."""
    strategy = str((signal or {}).get("strategy") or "").strip().lower()
    side = normalize_perp_entry_signal((signal or {}).get("signal"))
    if strategy != "sma_reclaim_bull_flag":
        return {
            "isSpecialist": False,
            "allowed": False,
            "bypassConsensus": False,
            "reason": "not_sma_reclaim_bull_flag",
            "sizeMultiplier": None,
        }

    gates = (((hl_cfg or {}).get("specialist_strategy_gates") or {}).get(strategy) or {})
    enabled = gates.get("enabled", True)
    if enabled is False or str(enabled).lower() in {"0", "false", "no", "off"}:
        return {
            "isSpecialist": True,
            "allowed": False,
            "bypassConsensus": False,
            "reason": "specialist_gate_disabled",
            "sizeMultiplier": None,
        }

    min_conf = _safe_float(gates.get("min_confidence", 0.85), 0.85)
    min_strength = _safe_float(gates.get("min_strength", 0.70), 0.70)
    min_rr = _safe_float(gates.get("min_reward_risk", 1.8), 1.8)
    max_stop_pct = _safe_float(gates.get("max_stop_pct", 0.03), 0.03)
    size_mult = _safe_float(gates.get("size_multiplier", 0.35), 0.35)

    conf = _safe_float((signal or {}).get("confidence"), 0.0)
    strength = _safe_float((signal or {}).get("strength"), 0.0)
    details = (signal or {}).get("details") or {}
    state = details.get("state") or {}
    indicators = details.get("indicators") or state.get("indicators") or {}
    rr = _safe_float(indicators.get("reward_risk"), 0.0)
    stop_pct = _safe_float(indicators.get("stop_pct"), 999.0)
    invalidation = str(indicators.get("invalidation_reason") or "").strip().lower()
    setup = str(indicators.get("setup") or "").strip().lower()

    failures = []
    if side != "long":
        failures.append("not_long")
    if conf < min_conf:
        failures.append(f"confidence_{conf:.2f}_lt_{min_conf:.2f}")
    if strength < min_strength:
        failures.append(f"strength_{strength:.2f}_lt_{min_strength:.2f}")
    if rr < min_rr:
        failures.append(f"rr_{rr:.2f}_lt_{min_rr:.2f}")
    if stop_pct > max_stop_pct:
        failures.append(f"stop_pct_{stop_pct:.4f}_gt_{max_stop_pct:.4f}")
    if invalidation not in {"", "none"}:
        failures.append(f"invalidation_{invalidation}")
    if setup and setup != "sma_reclaim_bull_flag":
        failures.append(f"setup_{setup}")

    return {
        "isSpecialist": True,
        "allowed": not failures,
        "bypassConsensus": bool(gates.get("bypass_consensus", True)) and not failures,
        "reason": ",".join(failures) if failures else "specialist_gate_pass",
        "sizeMultiplier": max(0.0, min(1.0, size_mult)),
    }


def supply_demand_3step_specialist_gate(
    signal: Dict[str, Any],
    hl_cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Dedicated gate for supply/demand 3-step entries (long or short)."""
    strategy = str((signal or {}).get("strategy") or "").strip().lower()
    side = normalize_perp_entry_signal((signal or {}).get("signal"))
    if strategy != "supply_demand_3step":
        return {
            "isSpecialist": False,
            "allowed": False,
            "bypassConsensus": False,
            "reason": "not_supply_demand_3step",
            "sizeMultiplier": None,
        }

    gates = (((hl_cfg or {}).get("specialist_strategy_gates") or {}).get(strategy) or {})
    enabled = gates.get("enabled", True)
    if enabled is False or str(enabled).lower() in {"0", "false", "no", "off"}:
        return {
            "isSpecialist": True,
            "allowed": False,
            "bypassConsensus": False,
            "reason": "specialist_gate_disabled",
            "sizeMultiplier": None,
        }

    min_conf = _safe_float(gates.get("min_confidence", 0.70), 0.70)
    min_strength = _safe_float(gates.get("min_strength", 0.65), 0.65)
    min_rr = _safe_float(gates.get("min_reward_risk", 2.5), 2.5)
    size_mult = _safe_float(gates.get("size_multiplier", 0.40), 0.40)
    side_mults = gates.get("size_multiplier_by_side") or {}
    if isinstance(side_mults, dict) and side in {"long", "short"}:
        raw_side_mult = side_mults.get(side)
        if raw_side_mult is not None:
            size_mult = _safe_float(raw_side_mult, size_mult)

    conf = _safe_float((signal or {}).get("confidence"), 0.0)
    strength = _safe_float((signal or {}).get("strength"), 0.0)
    details = (signal or {}).get("details") or {}
    state = details.get("state") or {}
    indicators = details.get("indicators") or state.get("indicators") or {}
    rr = _safe_float(indicators.get("reward_risk"), 0.0)
    setup = str(indicators.get("setup") or "").strip().lower()

    failures = []
    if side not in {"long", "short"}:
        failures.append("not_directional")
    if conf < min_conf:
        failures.append(f"confidence_{conf:.2f}_lt_{min_conf:.2f}")
    if strength < min_strength:
        failures.append(f"strength_{strength:.2f}_lt_{min_strength:.2f}")
    if rr < min_rr:
        failures.append(f"rr_{rr:.2f}_lt_{min_rr:.2f}")
    if not indicators.get("step1_pass"):
        failures.append("step1_fail")
    if not indicators.get("step2_pass"):
        failures.append("step2_fail")
    if not indicators.get("step3_pass"):
        failures.append("step3_fail")
    if setup and setup != "supply_demand_3step":
        failures.append(f"setup_{setup}")

    return {
        "isSpecialist": True,
        "allowed": not failures,
        "bypassConsensus": bool(gates.get("bypass_consensus", True)) and not failures,
        "reason": ",".join(failures) if failures else "specialist_gate_pass",
        "sizeMultiplier": max(0.0, min(1.0, size_mult)),
    }


def dual_sma_daytrade_specialist_gate(
    signal: Dict[str, Any],
    hl_cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Dedicated gate for dual-SMA daytrade entries (long or short)."""
    strategy = str((signal or {}).get("strategy") or "").strip().lower()
    side = normalize_perp_entry_signal((signal or {}).get("signal"))
    if strategy != "dual_sma_daytrade":
        return {
            "isSpecialist": False,
            "allowed": False,
            "bypassConsensus": False,
            "reason": "not_dual_sma_daytrade",
            "sizeMultiplier": None,
        }

    gates = (((hl_cfg or {}).get("specialist_strategy_gates") or {}).get(strategy) or {})
    enabled = gates.get("enabled", True)
    if enabled is False or str(enabled).lower() in {"0", "false", "no", "off"}:
        return {
            "isSpecialist": True,
            "allowed": False,
            "bypassConsensus": False,
            "reason": "specialist_gate_disabled",
            "sizeMultiplier": None,
        }

    min_conf = _safe_float(gates.get("min_confidence", 0.70), 0.70)
    min_strength = _safe_float(gates.get("min_strength", 0.65), 0.65)
    min_rr = _safe_float(gates.get("min_reward_risk", 1.8), 1.8)
    size_mult = _safe_float(gates.get("size_multiplier", 0.50), 0.50)

    conf = _safe_float((signal or {}).get("confidence"), 0.0)
    strength = _safe_float((signal or {}).get("strength"), 0.0)
    details = (signal or {}).get("details") or {}
    state = details.get("state") or {}
    indicators = details.get("indicators") or state.get("indicators") or {}
    rr = _safe_float(indicators.get("reward_risk"), 0.0)
    setup = str(indicators.get("setup") or "").strip().lower()

    failures = []
    if side not in {"long", "short"}:
        failures.append("not_directional")
    if conf < min_conf:
        failures.append(f"confidence_{conf:.2f}_lt_{min_conf:.2f}")
    if strength < min_strength:
        failures.append(f"strength_{strength:.2f}_lt_{min_strength:.2f}")
    if rr < min_rr:
        failures.append(f"rr_{rr:.2f}_lt_{min_rr:.2f}")
    if not indicators.get("daily_pass"):
        failures.append("daily_fail")
    if not indicators.get("confirm_15m_pass"):
        failures.append("confirm_15m_fail")
    if not indicators.get("entry_5m_pass"):
        failures.append("entry_5m_fail")
    if setup and setup != "dual_sma_daytrade":
        failures.append(f"setup_{setup}")

    return {
        "isSpecialist": True,
        "allowed": not failures,
        "bypassConsensus": bool(gates.get("bypass_consensus", True)) and not failures,
        "reason": ",".join(failures) if failures else "specialist_gate_pass",
        "sizeMultiplier": max(0.0, min(1.0, size_mult)),
    }


def arc_daytrade_specialist_gate(
    signal: Dict[str, Any],
    hl_cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Dedicated gate for ARC daytrade entries (long or short)."""
    strategy = str((signal or {}).get("strategy") or "").strip().lower()
    side = normalize_perp_entry_signal((signal or {}).get("signal"))
    if strategy != "arc_daytrade":
        return {
            "isSpecialist": False,
            "allowed": False,
            "bypassConsensus": False,
            "reason": "not_arc_daytrade",
            "sizeMultiplier": None,
        }

    gates = (((hl_cfg or {}).get("specialist_strategy_gates") or {}).get(strategy) or {})
    enabled = gates.get("enabled", True)
    if enabled is False or str(enabled).lower() in {"0", "false", "no", "off"}:
        return {
            "isSpecialist": True,
            "allowed": False,
            "bypassConsensus": False,
            "reason": "specialist_gate_disabled",
            "sizeMultiplier": None,
        }

    min_conf = _safe_float(gates.get("min_confidence", 0.70), 0.70)
    min_strength = _safe_float(gates.get("min_strength", 0.65), 0.65)
    min_rr = _safe_float(gates.get("min_reward_risk", 1.2), 1.2)
    size_mult = _safe_float(gates.get("size_multiplier", 0.45), 0.45)

    conf = _safe_float((signal or {}).get("confidence"), 0.0)
    strength = _safe_float((signal or {}).get("strength"), 0.0)
    details = (signal or {}).get("details") or {}
    state = details.get("state") or {}
    indicators = details.get("indicators") or state.get("indicators") or {}
    rr = _safe_float(indicators.get("reward_risk"), 0.0)
    setup = str(indicators.get("setup") or "").strip().lower()

    failures = []
    if side not in {"long", "short"}:
        failures.append("not_directional")
    if conf < min_conf:
        failures.append(f"confidence_{conf:.2f}_lt_{min_conf:.2f}")
    if strength < min_strength:
        failures.append(f"strength_{strength:.2f}_lt_{min_strength:.2f}")
    if rr < min_rr:
        failures.append(f"rr_{rr:.2f}_lt_{min_rr:.2f}")
    if not indicators.get("area_pass"):
        failures.append("area_fail")
    if not indicators.get("range_pass"):
        failures.append("range_fail")
    if not indicators.get("candle_pass"):
        failures.append("candle_fail")
    if setup and setup != "arc_daytrade":
        failures.append(f"setup_{setup}")

    return {
        "isSpecialist": True,
        "allowed": not failures,
        "bypassConsensus": bool(gates.get("bypass_consensus", True)) and not failures,
        "reason": ",".join(failures) if failures else "specialist_gate_pass",
        "sizeMultiplier": max(0.0, min(1.0, size_mult)),
    }


def ema50_breakout_pullback_specialist_gate(
    signal: Dict[str, Any],
    hl_cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Dedicated gate for EMA50 breakout-pullback entries (long or short)."""
    strategy = str((signal or {}).get("strategy") or "").strip().lower()
    side = normalize_perp_entry_signal((signal or {}).get("signal"))
    if strategy != "ema50_breakout_pullback":
        return {
            "isSpecialist": False,
            "allowed": False,
            "bypassConsensus": False,
            "reason": "not_ema50_breakout_pullback",
            "sizeMultiplier": None,
        }

    gates = (((hl_cfg or {}).get("specialist_strategy_gates") or {}).get(strategy) or {})
    enabled = gates.get("enabled", True)
    if enabled is False or str(enabled).lower() in {"0", "false", "no", "off"}:
        return {
            "isSpecialist": True,
            "allowed": False,
            "bypassConsensus": False,
            "reason": "specialist_gate_disabled",
            "sizeMultiplier": None,
        }

    min_conf = _safe_float(gates.get("min_confidence", 0.70), 0.70)
    min_strength = _safe_float(gates.get("min_strength", 0.65), 0.65)
    min_rr = _safe_float(gates.get("min_reward_risk", 2.0), 2.0)
    size_mult = _safe_float(gates.get("size_multiplier", 0.40), 0.40)

    conf = _safe_float((signal or {}).get("confidence"), 0.0)
    strength = _safe_float((signal or {}).get("strength"), 0.0)
    details = (signal or {}).get("details") or {}
    state = details.get("state") or {}
    indicators = details.get("indicators") or state.get("indicators") or {}
    rr = _safe_float(indicators.get("reward_risk"), 0.0)
    setup = str(indicators.get("setup") or "").strip().lower()

    failures = []
    if side not in {"long", "short"}:
        failures.append("not_directional")
    if conf < min_conf:
        failures.append(f"confidence_{conf:.2f}_lt_{min_conf:.2f}")
    if strength < min_strength:
        failures.append(f"strength_{strength:.2f}_lt_{min_strength:.2f}")
    if rr < min_rr:
        failures.append(f"rr_{rr:.2f}_lt_{min_rr:.2f}")
    if not indicators.get("breakout_pass"):
        failures.append("breakout_fail")
    if not indicators.get("pullback_pass"):
        failures.append("pullback_fail")
    if not indicators.get("trigger_pass"):
        failures.append("trigger_fail")
    if setup and setup != "ema50_breakout_pullback":
        failures.append(f"setup_{setup}")

    return {
        "isSpecialist": True,
        "allowed": not failures,
        "bypassConsensus": bool(gates.get("bypass_consensus", True)) and not failures,
        "reason": ",".join(failures) if failures else "specialist_gate_pass",
        "sizeMultiplier": max(0.0, min(1.0, size_mult)),
    }


def orb_5m_scalp_specialist_gate(
    signal: Dict[str, Any],
    hl_cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Dedicated gate for ORB 5m scalp entries (long or short)."""
    strategy = str((signal or {}).get("strategy") or "").strip().lower()
    side = normalize_perp_entry_signal((signal or {}).get("signal"))
    if strategy != "orb_5m_scalp":
        return {
            "isSpecialist": False,
            "allowed": False,
            "bypassConsensus": False,
            "reason": "not_orb_5m_scalp",
            "sizeMultiplier": None,
        }

    gates = (((hl_cfg or {}).get("specialist_strategy_gates") or {}).get(strategy) or {})
    enabled = gates.get("enabled", True)
    if enabled is False or str(enabled).lower() in {"0", "false", "no", "off"}:
        return {
            "isSpecialist": True,
            "allowed": False,
            "bypassConsensus": False,
            "reason": "specialist_gate_disabled",
            "sizeMultiplier": None,
        }

    min_conf = _safe_float(gates.get("min_confidence", 0.70), 0.70)
    min_strength = _safe_float(gates.get("min_strength", 0.65), 0.65)
    min_rr = _safe_float(gates.get("min_reward_risk", 2.0), 2.0)
    size_mult = _safe_float(gates.get("size_multiplier", 0.45), 0.45)

    conf = _safe_float((signal or {}).get("confidence"), 0.0)
    strength = _safe_float((signal or {}).get("strength"), 0.0)
    details = (signal or {}).get("details") or {}
    state = details.get("state") or {}
    indicators = details.get("indicators") or state.get("indicators") or {}
    rr = _safe_float(indicators.get("reward_risk"), 0.0)
    setup = str(indicators.get("setup") or "").strip().lower()
    session_state = str(indicators.get("session_state") or "").strip().lower()

    failures = []
    if side not in {"long", "short"}:
        failures.append("not_directional")
    if conf < min_conf:
        failures.append(f"confidence_{conf:.2f}_lt_{min_conf:.2f}")
    if strength < min_strength:
        failures.append(f"strength_{strength:.2f}_lt_{min_strength:.2f}")
    if rr < min_rr:
        failures.append(f"rr_{rr:.2f}_lt_{min_rr:.2f}")
    if not indicators.get("breakout_valid"):
        failures.append("breakout_fail")
    if not indicators.get("retest_valid"):
        failures.append("retest_fail")
    if session_state != "signal":
        failures.append(f"session_state_{session_state or 'unknown'}")
    if setup and setup != "orb_5m_scalp":
        failures.append(f"setup_{setup}")

    return {
        "isSpecialist": True,
        "allowed": not failures,
        "bypassConsensus": bool(gates.get("bypass_consensus", True)) and not failures,
        "reason": ",".join(failures) if failures else "specialist_gate_pass",
        "sizeMultiplier": max(0.0, min(1.0, size_mult)),
    }


def specialist_entry_gate(
    signal: Dict[str, Any],
    hl_cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return the first matching specialist gate for the selected signal."""
    for gate_fn in (
        sma_reclaim_bull_flag_specialist_gate,
        supply_demand_3step_specialist_gate,
        dual_sma_daytrade_specialist_gate,
        arc_daytrade_specialist_gate,
        ema50_breakout_pullback_specialist_gate,
        orb_5m_scalp_specialist_gate,
    ):
        result = gate_fn(signal, hl_cfg)
        if result.get("isSpecialist"):
            return result
    return {
        "isSpecialist": False,
        "allowed": False,
        "bypassConsensus": False,
        "reason": "not_specialist_strategy",
        "sizeMultiplier": None,
    }


def eligible_shadow_strategy_signals(
    signals_data: Dict[str, Any],
    hl_cfg: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Return every directional strategy signal that passes its own entry gates.

    Shadow eligibility deliberately excludes portfolio gates such as available
    balance, daily halts, open-position limits, and cross-strategy consensus.
    This provides an unbiased counterfactual sample for each strategy while the
    executable portfolio remains risk constrained.
    """
    shadow_cfg = ((hl_cfg or {}).get("shadow_strategy_evaluation") or {})
    enabled = shadow_cfg.get("enabled", False)
    if enabled is False or str(enabled).lower() in {"0", "false", "no", "off"}:
        return []

    strategies = (signals_data or {}).get("strategies") or {}
    if not isinstance(strategies, dict):
        return []
    allowlist = {
        str(value or "").strip().lower()
        for value in (shadow_cfg.get("strategies") or [])
        if str(value or "").strip()
    }

    directional: List[Dict[str, Any]] = []
    for strategy_name, data in strategies.items():
        if not isinstance(data, dict):
            continue
        strategy_key = str(strategy_name or "").strip().lower()
        if allowlist and strategy_key not in allowlist:
            continue
        side = normalize_perp_entry_signal(data.get("signal"))
        if side not in {"long", "short"}:
            continue
        directional.append(
            {
                "strategy": strategy_key,
                "signal": side,
                "confidence": _safe_float(data.get("confidence"), 0.0),
                "strength": _safe_float(data.get("strength"), 0.0),
                "details": data,
                "market_regime": str(
                    (signals_data or {}).get("market_regime") or ""
                ).strip().lower(),
            }
        )

    eligible: List[Dict[str, Any]] = []
    for candidate in directional:
        specialist = specialist_entry_gate(candidate, hl_cfg)
        if specialist.get("isSpecialist"):
            if not specialist.get("allowed"):
                continue
            gate_name = "specialist"
            gate_reason = specialist.get("reason")
        else:
            standalone = hyperliquid_standalone_entry_gate(candidate, hl_cfg)
            if standalone.get("isStandalone") and not standalone.get("allowed"):
                continue
            # Strategies without an additional orchestrator gate have already
            # passed their engine-owned setup rules by emitting long/short.
            gate_name = "standalone" if standalone.get("isStandalone") else "engine"
            gate_reason = (
                standalone.get("reason")
                if standalone.get("isStandalone")
                else "engine_directional_signal"
            )

        # Edge and cross-strategy opposition are portfolio gates, not properties
        # of this strategy's setup. Persist their outcome for later comparison,
        # but do not censor the counterfactual sample with them.
        edge = hyperliquid_min_edge_gate(candidate, hl_cfg)
        candidate["shadow_gate"] = gate_name
        candidate["shadow_gate_reason"] = gate_reason
        candidate["shadow_edge_reason"] = edge.get("reason")
        candidate["shadow_edge_passed"] = not bool(edge.get("blocked"))
        candidate["expected_move_pct"] = edge.get("expectedMovePct")
        eligible.append(candidate)

    return eligible


def paper_perp_position_size_multiplier(
    signal: Dict[str, Any],
    hl_cfg: Optional[Dict[str, Any]] = None,
) -> float:
    """Return weak/normal/strong paper sizing multiplier from signal metadata."""
    sizing = ((hl_cfg or {}).get("position_sizing") or {})
    enabled = sizing.get("enabled", True)
    if enabled is False or str(enabled).lower() in {"0", "false", "no", "off"}:
        return 1.0

    weak_mult = _safe_float(sizing.get("weak_multiplier", 0.35), 0.35)
    normal_mult = _safe_float(sizing.get("normal_multiplier", 0.70), 0.70)
    strong_mult = _safe_float(sizing.get("strong_multiplier", 1.00), 1.00)
    normal_conf = _safe_float(sizing.get("normal_confidence", 0.62), 0.62)
    strong_conf = _safe_float(sizing.get("strong_confidence", 0.72), 0.72)
    normal_strength = _safe_float(sizing.get("normal_strength", 0.60), 0.60)
    strong_strength = _safe_float(sizing.get("strong_strength", 0.68), 0.68)
    normal_agreement = _safe_float(sizing.get("normal_agreement", 60.0), 60.0)
    strong_agreement = _safe_float(sizing.get("strong_agreement", 65.0), 65.0)
    normal_consensus_conf = _safe_float(
        sizing.get("normal_consensus_confidence", normal_conf),
        normal_conf,
    )
    strong_consensus_conf = _safe_float(
        sizing.get("strong_consensus_confidence", normal_conf),
        normal_conf,
    )

    conf = _safe_float((signal or {}).get("confidence"), 0.0)
    strength = _safe_float((signal or {}).get("strength"), 0.0)
    consensus_conf = _safe_float((signal or {}).get("consensus_confidence"), 0.0)
    agreement = _safe_float((signal or {}).get("consensus_agreement"), 0.0)

    strong = (
        conf >= strong_conf
        and strength >= strong_strength
        and (agreement >= strong_agreement or consensus_conf >= strong_consensus_conf)
    )
    normal = (
        (conf >= normal_conf or strength >= normal_strength)
        and (agreement >= normal_agreement or consensus_conf >= normal_consensus_conf)
    )
    selected = strong_mult if strong else normal_mult if normal else weak_mult
    return max(0.0, min(1.0, selected))


def _metadata_dict(trade: Mapping[str, Any]) -> Dict[str, Any]:
    metadata = trade.get("metadata") or {}
    return metadata if isinstance(metadata, dict) else {}


def _trade_exit_time(trade: Mapping[str, Any]) -> Optional[datetime]:
    return _parse_dt(trade.get("exit_time") or trade.get("updated_at"))


def _exit_bucket_from_reason(reason: Any) -> str:
    raw = str(reason or "").strip()
    lowered = raw.lower()
    if "trailing_stop" in lowered:
        return "trailing_stop"
    if "overall_take_profit" in lowered:
        return "overall_take_profit"
    if "profit_protection" in lowered:
        return "profit_protection"
    if "max_holding_time_flat" in lowered:
        return "max_hold_flat"
    if "max_holding_time" in lowered:
        return "max_hold_hard"
    if "stagnant_loser_fast_fail" in lowered:
        return "paper_stagnant_loser_fast_fail"
    if "stagnant_loser_divergence" in lowered:
        return "paper_stagnant_loser_divergence"
    if "stop_loss" in lowered:
        return "paper_stop_loss"
    return raw or "unknown"


def _profit_factor(gross_profit: float, gross_loss: float) -> Optional[float]:
    if gross_loss > 0:
        return gross_profit / gross_loss
    if gross_profit > 0:
        return float("inf")
    return None


def _group_closed_paper_trades(
    closed_trades: Iterable[Mapping[str, Any]],
    *,
    now: Optional[datetime],
    lookback_hours: float,
) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    now_dt = now or datetime.utcnow()
    if now_dt.tzinfo:
        now_dt = now_dt.replace(tzinfo=None)
    cutoff = now_dt - timedelta(hours=max(1.0, float(lookback_hours or 168.0)))
    rows: List[Dict[str, Any]] = []
    strategy_side: Dict[str, Dict[str, Any]] = {}
    regime_side: Dict[str, Dict[str, Any]] = {}
    exit_bucket: Dict[str, Dict[str, Any]] = {}

    def _empty(key: str) -> Dict[str, Any]:
        return {
            "key": key,
            "closed": 0,
            "realized": 0.0,
            "fees": 0.0,
            "funding": 0.0,
            "gross_before_fees": 0.0,
            "notional": 0.0,
            "wins": 0,
            "losses": 0,
            "gross_profit": 0.0,
            "gross_loss": 0.0,
        }

    def _add(group: Dict[str, Dict[str, Any]], key: str, row: Dict[str, Any]) -> None:
        bucket = group.setdefault(key, _empty(key))
        bucket["closed"] += 1
        bucket["realized"] += row["realized"]
        bucket["fees"] += row["fees"]
        bucket["funding"] += row["funding"]
        bucket["gross_before_fees"] += row["gross_before_fees"]
        bucket["notional"] += row["notional"]
        if row["realized"] > 0:
            bucket["wins"] += 1
            bucket["gross_profit"] += row["realized"]
        elif row["realized"] < 0:
            bucket["losses"] += 1
            bucket["gross_loss"] += abs(row["realized"])

    for trade in closed_trades or []:
        if str(trade.get("status") or "CLOSED").upper() != "CLOSED":
            continue
        metadata = _metadata_dict(trade)
        if str(metadata.get("accounting_excluded") or "false").lower() == "true":
            continue
        exit_time = _trade_exit_time(trade)
        if exit_time is None or exit_time < cutoff:
            continue
        strategy = str(trade.get("source_strategy") or trade.get("strategy") or "unknown").strip().lower()
        side = str(trade.get("position_side") or trade.get("source_signal") or "").strip().lower()
        if side not in {"long", "short"}:
            side = normalize_perp_entry_signal(side) or "unknown"
        regime = str(metadata.get("market_regime") or "unknown").strip().lower()
        try:
            realized = float(trade.get("realized_pnl") or 0.0)
            fees = float(trade.get("fees") or 0.0)
            funding = float(trade.get("funding") or 0.0)
            notional = float(trade.get("notional_size") or 0.0)
        except (TypeError, ValueError):
            continue
        row = {
            "strategy": strategy,
            "side": side,
            "regime": regime,
            "realized": realized,
            "fees": fees,
            "funding": funding,
            "gross_before_fees": realized + fees + funding,
            "notional": notional,
            "exit_bucket": _exit_bucket_from_reason(trade.get("exit_reason")),
        }
        rows.append(row)
        _add(strategy_side, f"{strategy}:{side}", row)
        _add(regime_side, f"{regime}:{side}", row)
        _add(exit_bucket, row["exit_bucket"], row)

    for group in (strategy_side, regime_side, exit_bucket):
        for bucket in group.values():
            bucket["profit_factor"] = _profit_factor(
                float(bucket["gross_profit"]),
                float(bucket["gross_loss"]),
            )
            bucket["win_rate"] = (
                float(bucket["wins"]) / float(bucket["closed"])
                if bucket["closed"]
                else None
            )
            gross = float(bucket["gross_before_fees"])
            bucket["fee_drag"] = float(bucket["fees"]) / abs(gross) if gross else None
            bucket["net_edge"] = (
                float(bucket["realized"]) / float(bucket["notional"])
                if float(bucket["notional"]) > 0
                else None
            )
            bucket["gross_edge"] = (
                gross / float(bucket["notional"])
                if float(bucket["notional"]) > 0
                else None
            )

    return rows, strategy_side, regime_side, exit_bucket


def build_hyperliquid_adaptive_pnl_control(
    closed_trades: Iterable[Mapping[str, Any]],
    hl_cfg: Optional[Dict[str, Any]] = None,
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Build a runtime control overlay from recent paper-perp results.

    This is intentionally bounded and reversible: it does not rewrite config.yaml.
    It returns gate, sizing, and exit-profile adjustments that the orchestrator can
    apply for the current cycle.
    """
    cfg = (hl_cfg or {}).get("adaptive_pnl_control") or {}
    enabled = cfg.get("enabled", False)
    if enabled is False or str(enabled).lower() in {"0", "false", "no", "off"}:
        return {"enabled": False, "decisions": [], "entrySizing": {}, "blockedRegimeSides": {}, "exitProfiles": {}}

    hl_cfg = hl_cfg or {}
    lookback_hours = _safe_float(cfg.get("lookback_hours", 168.0), 168.0)
    min_reduce_trades = int(_safe_float(cfg.get("min_reduce_trades", 10), 10))
    min_block_trades = int(_safe_float(cfg.get("min_block_trades", 15), 15))
    min_scale_trades = int(_safe_float(cfg.get("min_scale_trades", 30), 30))
    recent_window_hours = _safe_float(cfg.get("recent_window_hours", 6.0), 6.0)
    min_recent_reduce_trades = int(_safe_float(cfg.get("min_recent_reduce_trades", 3), 3))
    min_recent_block_trades = int(_safe_float(cfg.get("min_recent_block_trades", 3), 3))
    recent_release_hold_hours = _safe_float(cfg.get("recent_release_hold_hours", 12.0), 12.0)
    probation_multiplier = max(0.05, min(1.0, _safe_float(cfg.get("probation_size_multiplier", 0.35), 0.35)))
    recent_probation_multiplier = max(
        0.05,
        min(1.0, _safe_float(cfg.get("recent_probation_size_multiplier", probation_multiplier), probation_multiplier)),
    )
    scale_up_multiplier = max(1.0, min(1.5, _safe_float(cfg.get("scale_up_multiplier", 1.25), 1.25)))
    max_fee_drag_for_scale = _safe_float(cfg.get("max_fee_drag_for_scale", 0.60), 0.60)
    min_pf_for_scale = _safe_float(cfg.get("min_profit_factor_for_scale", 1.25), 1.25)
    min_net_edge_for_scale = _safe_float(cfg.get("min_net_edge_for_scale", 0.0015), 0.0015)
    min_gross_edge_for_scale = _safe_float(cfg.get("min_gross_edge_for_scale", 0.0040), 0.0040)

    rows, strategy_side, regime_side, exit_buckets = _group_closed_paper_trades(
        closed_trades,
        now=now,
        lookback_hours=lookback_hours,
    )
    recent_strategy_side: Dict[str, Dict[str, Any]] = {}
    recent_regime_side: Dict[str, Dict[str, Any]] = {}
    if recent_window_hours > 0:
        _, recent_strategy_side, recent_regime_side, _ = _group_closed_paper_trades(
            closed_trades,
            now=now,
            lookback_hours=recent_window_hours,
        )
    decisions: List[Dict[str, Any]] = []
    entry_sizing: Dict[str, float] = {}
    blocked_regime_sides: Dict[str, List[str]] = {}
    exit_profiles: Dict[str, Dict[str, Any]] = {}

    def _finite_pf(value: Any) -> float:
        if value == float("inf"):
            return 999.0
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _round_or_none(value: Any, digits: int = 4) -> Optional[float]:
        if value is None:
            return None
        try:
            return round(float(value), digits)
        except (TypeError, ValueError, OverflowError):
            return None

    def _perf_evidence(
        perf: Mapping[str, Any],
        *,
        evidence_lookback_hours: Optional[float] = None,
    ) -> Dict[str, Any]:
        pf_raw = perf.get("profit_factor")
        pf = _finite_pf(pf_raw)
        return {
            "lookbackHours": evidence_lookback_hours if evidence_lookback_hours is not None else lookback_hours,
            "closed": int(perf.get("closed") or 0),
            "realized": _round_or_none(perf.get("realized")),
            "grossBeforeFees": _round_or_none(perf.get("gross_before_fees")),
            "fees": _round_or_none(perf.get("fees")),
            "feeDragPct": (
                _round_or_none(float(perf["fee_drag"]) * 100.0, 2)
                if perf.get("fee_drag") is not None
                else None
            ),
            "profitFactor": None if pf_raw is None else _round_or_none(pf),
            "winRatePct": (
                _round_or_none(float(perf["win_rate"]) * 100.0, 2)
                if perf.get("win_rate") is not None
                else None
            ),
            "netEdgePct": (
                _round_or_none(float(perf["net_edge"]) * 100.0, 4)
                if perf.get("net_edge") is not None
                else None
            ),
            "grossEdgePct": (
                _round_or_none(float(perf["gross_edge"]) * 100.0, 4)
                if perf.get("gross_edge") is not None
                else None
            ),
        }

    def _entry_decision(
        *,
        action: str,
        decision_type: str,
        target_type: str,
        target: str,
        side: str,
        multiplier: float,
        perf: Mapping[str, Any],
        situation: str,
        intended_effect: str,
    ) -> Dict[str, Any]:
        return {
            "decisionKey": f"{decision_type}:{target}:{side}",
            "type": decision_type,
            "action": action,
            "targetType": target_type,
            "target": target,
            "side": side,
            "key": f"{target}:{side}",
            "situation": situation,
            "configPath": f"runtime.hyperliquid_perps.adaptive_pnl_control.entrySizing.{target}:{side}",
            "oldValue": 1.0,
            "newValue": round(float(multiplier), 4),
            "intendedEffect": intended_effect,
            "evidence": _perf_evidence(perf),
        }

    def _block_regime_decision(
        *,
        regime: str,
        side: str,
        perf: Mapping[str, Any],
        decision_type: str = "block_regime_side",
        evidence_lookback_hours: Optional[float] = None,
        situation: Optional[str] = None,
        intended_effect: Optional[str] = None,
    ) -> Dict[str, Any]:
        return {
            "decisionKey": f"{decision_type}:{regime}:{side}",
            "type": decision_type,
            "action": "block",
            "targetType": "regime_side",
            "target": regime,
            "side": side,
            "key": f"{regime}:{side}",
            "situation": situation or (
                f"{regime} {side} entries show negative gross/net rolling PnL "
                "after fees."
            ),
            "configPath": f"runtime.hyperliquid_perps.blocked_regime_sides.{regime}",
            "oldValue": sorted(list((hl_cfg.get("blocked_regime_sides") or {}).get(regime) or [])),
            "newValue": sorted(set(list((hl_cfg.get("blocked_regime_sides") or {}).get(regime) or []) + [side])),
            "intendedEffect": intended_effect or "Stop opening this weak regime/side until the rolling evidence improves.",
            "evidence": _perf_evidence(perf, evidence_lookback_hours=evidence_lookback_hours),
        }

    for key, perf in sorted(strategy_side.items()):
        strategy, side = key.rsplit(":", 1)
        closed = int(perf["closed"])
        realized = float(perf["realized"])
        gross = float(perf["gross_before_fees"])
        pf = _finite_pf(perf.get("profit_factor"))
        fee_drag = perf.get("fee_drag")
        net_edge = perf.get("net_edge")
        gross_edge = perf.get("gross_edge")
        if closed >= min_scale_trades and realized > 0 and pf >= min_pf_for_scale:
            if (
                fee_drag is not None
                and fee_drag <= max_fee_drag_for_scale
                and net_edge is not None
                and net_edge >= min_net_edge_for_scale
                and gross_edge is not None
                and gross_edge >= min_gross_edge_for_scale
            ):
                entry_sizing[key] = scale_up_multiplier
                decisions.append(_entry_decision(
                    action="scale_up",
                    decision_type="scale_up_strategy_side",
                    target_type="strategy_side",
                    target=strategy,
                    side=side,
                    multiplier=scale_up_multiplier,
                    perf=perf,
                    situation=(
                        f"{strategy} {side} has positive net PnL, profit factor "
                        f">= {min_pf_for_scale:.2f}, sufficient edge, and acceptable fee drag."
                    ),
                    intended_effect="Increase allocation to a fee-adjusted winner while the rolling edge holds.",
                ))
                continue
        if closed >= min_reduce_trades and (realized < 0 or gross < 0 or pf < 1.0):
            entry_sizing[key] = min(entry_sizing.get(key, 1.0), probation_multiplier)
            decisions.append(_entry_decision(
                action="reduce",
                decision_type="reduce_strategy_side",
                target_type="strategy_side",
                target=strategy,
                side=side,
                multiplier=probation_multiplier,
                perf=perf,
                situation=(
                    f"{strategy} {side} is underperforming in the rolling window "
                    "through negative net/gross PnL or profit factor below 1.00."
                ),
                intended_effect="Reduce fee exposure and loss velocity while keeping the strategy available for recovery evidence.",
            ))

    for key, perf in sorted(recent_strategy_side.items()):
        strategy, side = key.rsplit(":", 1)
        closed = int(perf["closed"])
        realized = float(perf["realized"])
        gross = float(perf["gross_before_fees"])
        pf = _finite_pf(perf.get("profit_factor"))
        if closed >= min_recent_reduce_trades and (realized < 0 or gross < 0 or pf < 1.0):
            entry_sizing[key] = min(entry_sizing.get(key, 1.0), recent_probation_multiplier)
            decisions.append({
                **_entry_decision(
                    action="reduce",
                    decision_type="reduce_recent_strategy_side",
                    target_type="strategy_side",
                    target=strategy,
                    side=side,
                    multiplier=recent_probation_multiplier,
                    perf=perf,
                    situation=(
                        f"{strategy} {side} is deteriorating in the last "
                        f"{recent_window_hours:g}h through negative net/gross PnL or profit factor below 1.00."
                    ),
                    intended_effect=(
                        "React faster to current loss clusters without disabling the strategy; "
                        "size returns automatically when the recent window improves."
                    ),
                ),
                "evidence": _perf_evidence(perf, evidence_lookback_hours=recent_window_hours),
            })

    for key, perf in sorted(recent_regime_side.items()):
        regime, side = key.rsplit(":", 1)
        closed = int(perf["closed"])
        realized = float(perf["realized"])
        gross = float(perf["gross_before_fees"])
        pf = _finite_pf(perf.get("profit_factor"))
        if closed >= min_recent_block_trades and (gross < 0 or (realized < 0 and pf < 0.85)):
            sides = blocked_regime_sides.setdefault(regime, [])
            if side not in sides:
                sides.append(side)
            decisions.append(_block_regime_decision(
                regime=regime,
                side=side,
                perf=perf,
                decision_type="block_recent_regime_side",
                evidence_lookback_hours=recent_window_hours,
                situation=(
                    f"{regime} {side} entries are deteriorating in the last "
                    f"{recent_window_hours:g}h through negative gross/net PnL."
                ),
                intended_effect=(
                    "Stop the current loss cluster completely; unblock automatically "
                    "after the recent evidence clears and the hold window expires."
                ),
            ))

    for key, perf in sorted(regime_side.items()):
        regime, side = key.rsplit(":", 1)
        closed = int(perf["closed"])
        realized = float(perf["realized"])
        gross = float(perf["gross_before_fees"])
        pf = _finite_pf(perf.get("profit_factor"))
        fee_drag = perf.get("fee_drag")
        net_edge = perf.get("net_edge")
        gross_edge = perf.get("gross_edge")
        if closed >= min_scale_trades and realized > 0 and pf >= min_pf_for_scale:
            if (
                fee_drag is not None
                and fee_drag <= max_fee_drag_for_scale
                and net_edge is not None
                and net_edge >= min_net_edge_for_scale
                and gross_edge is not None
                and gross_edge >= min_gross_edge_for_scale
            ):
                entry_sizing[key] = max(entry_sizing.get(key, 1.0), scale_up_multiplier)
                decisions.append(_entry_decision(
                    action="scale_up",
                    decision_type="scale_up_regime_side",
                    target_type="regime_side",
                    target=regime,
                    side=side,
                    multiplier=scale_up_multiplier,
                    perf=perf,
                    situation=(
                        f"{regime} {side} entries have positive fee-adjusted edge "
                        f"and profit factor >= {min_pf_for_scale:.2f}."
                    ),
                    intended_effect="Increase allocation in the regime/side that is currently carrying positive edge.",
                ))
                continue
        if closed >= min_block_trades and (gross < 0 or (realized < 0 and pf < 0.75)):
            sides = blocked_regime_sides.setdefault(regime, [])
            if side not in sides:
                sides.append(side)
            decisions.append(_block_regime_decision(regime=regime, side=side, perf=perf))

    stop_loss_loss = abs(float((exit_buckets.get("paper_stop_loss") or {}).get("realized") or 0.0))
    fast_fail_loss = abs(float((exit_buckets.get("paper_stagnant_loser_fast_fail") or {}).get("realized") or 0.0))
    max_hold_loss = abs(min(0.0, float((exit_buckets.get("max_hold_flat") or {}).get("realized") or 0.0)))
    trailing_gain = max(0.0, float((exit_buckets.get("trailing_stop") or {}).get("realized") or 0.0))
    loss_drag = stop_loss_loss + fast_fail_loss + max_hold_loss
    if len(rows) >= min_reduce_trades and loss_drag > 0 and loss_drag >= max(10.0, trailing_gain * 0.75):
        exit_profiles["rsi_stoch_reversal_5m"] = {
            "strategies": ["rsi_stoch_reversal_5m"],
            "max_holding_minutes": 180,
            "max_holding_minutes_hard": 240,
            "stop_loss_pct": 0.9,
            "stagnant_loser": {
                "min_age_minutes": 15,
                "peak_cap_pct": 0.45,
                "loss_trigger_pct": -0.55,
                "fast_fail_peak_pct": 0.12,
                "fast_fail_min_age_minutes": 5,
                "fast_fail_loss_pct": -0.30,
            },
            "trailing_stop": {
                "step_percentage": 0.0015,
                "tightened_step_percentage": 0.0010,
                "tighten_profit_threshold": 0.0045,
            },
        }
        decisions.append({
            "decisionKey": "tighten_loss_and_trailing_exits:rsi_stoch_reversal_5m",
            "type": "tighten_loss_and_trailing_exits",
            "action": "tighten_exits",
            "targetType": "strategy",
            "strategy": "rsi_stoch_reversal_5m",
            "target": "rsi_stoch_reversal_5m",
            "situation": (
                "Stop-loss and fast-fail loss drag is dominating trailing-stop gains "
                "in the rolling paper-perp exit mix."
            ),
            "configPath": "runtime.hyperliquid_perps.exit_profiles.rsi_stoch_reversal_5m",
            "oldValue": {
                "stop_loss_pct": hl_cfg.get("stop_loss_pct"),
                "stagnant_loser": deepcopy(hl_cfg.get("stagnant_loser") or {}),
                "trailing_stop": deepcopy(hl_cfg.get("trailing_stop") or {}),
            },
            "newValue": deepcopy(exit_profiles["rsi_stoch_reversal_5m"]),
            "intendedEffect": "Cut stagnant losers earlier and tighten trailing giveback after profit is available.",
            "evidence": {
                "lookbackHours": lookback_hours,
                "sampleClosed": len(rows),
                "stopLossLoss": round(stop_loss_loss, 4),
                "fastFailLoss": round(fast_fail_loss, 4),
                "maxHoldLoss": round(max_hold_loss, 4),
                "trailingGain": round(trailing_gain, 4),
            },
        })

    return {
        "enabled": True,
        "lookbackHours": lookback_hours,
        "sampleClosed": len(rows),
        "decisions": decisions,
        "entrySizing": entry_sizing,
        "blockedRegimeSides": blocked_regime_sides,
        "exitProfiles": exit_profiles,
        "recentReleaseHoldHours": recent_release_hold_hours,
    }


def apply_hyperliquid_adaptive_pnl_control(
    hl_cfg: Optional[Dict[str, Any]],
    control: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Return a Hyperliquid config copy with adaptive runtime controls merged in."""
    merged = deepcopy(hl_cfg or {})
    if not control or not control.get("enabled"):
        return merged

    blocked = control.get("blockedRegimeSides") or {}
    if blocked:
        current = deepcopy(merged.get("blocked_regime_sides") or {})
        for regime, sides in blocked.items():
            existing = {
                str(value or "").strip().lower()
                for value in (current.get(regime) or [])
            }
            for side in sides or []:
                normalized_side = str(side or "").strip().lower()
                if normalized_side:
                    existing.add(normalized_side)
            current[regime] = sorted(existing)
        merged["blocked_regime_sides"] = current

    exit_profiles = control.get("exitProfiles") or {}
    if exit_profiles:
        current_profiles = deepcopy(merged.get("exit_profiles") or {})
        for profile_name, profile in exit_profiles.items():
            base = deepcopy(current_profiles.get(profile_name) or {})
            for key, value in (profile or {}).items():
                if isinstance(value, dict) and isinstance(base.get(key), dict):
                    base[key] = {**base[key], **value}
                else:
                    base[key] = deepcopy(value)
            current_profiles[profile_name] = base
        merged["exit_profiles"] = current_profiles

    merged["_adaptive_pnl_control"] = {
        "entrySizing": deepcopy(control.get("entrySizing") or {}),
        "decisions": deepcopy(control.get("decisions") or []),
        "sampleClosed": control.get("sampleClosed", 0),
        "lookbackHours": control.get("lookbackHours"),
    }
    return merged


def hyperliquid_adaptive_entry_sizing_multiplier(
    signal: Mapping[str, Any],
    regime: str,
    hl_cfg: Optional[Dict[str, Any]] = None,
) -> float:
    """Return runtime adaptive size multiplier for a selected strategy/side/regime."""
    controls = ((hl_cfg or {}).get("_adaptive_pnl_control") or {}).get("entrySizing") or {}
    if not controls:
        return 1.0
    strategy = str((signal or {}).get("strategy") or "").strip().lower()
    side = normalize_perp_entry_signal((signal or {}).get("signal"))
    regime_key = str(regime or "").strip().lower()
    multipliers = []
    if strategy and side:
        multipliers.append(controls.get(f"{strategy}:{side}"))
    if regime_key and side:
        multipliers.append(controls.get(f"{regime_key}:{side}"))
    parsed = []
    for value in multipliers:
        if value is None:
            continue
        try:
            parsed.append(float(value))
        except (TypeError, ValueError):
            continue
    if not parsed:
        return 1.0
    if any(value < 1.0 for value in parsed):
        return max(0.0, min(1.0, min(parsed)))
    return max(1.0, min(1.5, max(parsed)))


def _parse_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    except Exception:
        return None


def hyperliquid_strategy_side_performance(
    strategy: str,
    side: str,
    closed_trades: Iterable[Dict[str, Any]],
    *,
    lookback_trades: int = 12,
) -> Dict[str, Any]:
    """Summarize recent closed paper performance for one strategy direction."""
    normalized_strategy = str(strategy or "").strip().lower()
    normalized_side = str(side or "").strip().lower()
    try:
        lookback = max(1, int(float(lookback_trades or 12)))
    except (TypeError, ValueError):
        lookback = 12
    if not normalized_strategy or normalized_side not in {"long", "short"}:
        return {
            "strategy": normalized_strategy,
            "side": normalized_side,
            "closedCount": 0,
            "wins": 0,
            "losses": 0,
            "consecutiveLosses": 0,
            "realizedPnl": 0.0,
            "grossProfit": 0.0,
            "grossLoss": 0.0,
            "profitFactor": None,
            "winRate": None,
            "latestExitTime": None,
            "latestPnl": None,
            "lookbackTrades": lookback,
        }

    rows = []
    for trade in closed_trades or []:
        trade_strategy = str(
            trade.get("source_strategy") or trade.get("strategy") or ""
        ).strip().lower()
        raw_side = trade.get("position_side") or trade.get("source_signal") or ""
        trade_side = str(raw_side or "").strip().lower()
        if trade_side not in {"long", "short"}:
            trade_side = normalize_perp_entry_signal(raw_side) or trade_side
        if trade_strategy != normalized_strategy or trade_side != normalized_side:
            continue
        try:
            rpnl = float(trade.get("realized_pnl") or 0.0)
        except (TypeError, ValueError):
            continue
        exit_time = _parse_dt(trade.get("exit_time") or trade.get("updated_at"))
        rows.append({"pnl": rpnl, "exit_time": exit_time})

    rows.sort(key=lambda row: row["exit_time"] or datetime.min, reverse=True)
    recent = rows[:lookback]

    wins = sum(1 for row in recent if row["pnl"] > 0)
    losses = sum(1 for row in recent if row["pnl"] < 0)
    realized = sum(row["pnl"] for row in recent)
    gross_profit = sum(row["pnl"] for row in recent if row["pnl"] > 0)
    gross_loss = abs(sum(row["pnl"] for row in recent if row["pnl"] < 0))
    consecutive_losses = 0
    for row in recent:
        if row["pnl"] < 0:
            consecutive_losses += 1
            continue
        break

    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    elif gross_profit > 0:
        profit_factor = float("inf")
    else:
        profit_factor = None

    latest = recent[0] if recent else None
    latest_time = latest.get("exit_time") if latest else None
    return {
        "strategy": normalized_strategy,
        "side": normalized_side,
        "closedCount": len(recent),
        "wins": wins,
        "losses": losses,
        "consecutiveLosses": consecutive_losses,
        "realizedPnl": round(realized, 6),
        "grossProfit": round(gross_profit, 6),
        "grossLoss": round(gross_loss, 6),
        "profitFactor": None if profit_factor is None else round(profit_factor, 6),
        "winRate": None if not recent else round(wins / len(recent), 6),
        "latestExitTime": latest_time.isoformat() + "+00:00" if latest_time else None,
        "latestPnl": None if latest is None else round(float(latest["pnl"]), 6),
        "lookbackTrades": lookback,
    }


def hyperliquid_coin_entry_block(
    coin: str,
    open_trades: Iterable[Dict[str, Any]],
    closed_trades: Iterable[Dict[str, Any]],
    *,
    now: Optional[datetime] = None,
    realized_block_hours: float = 4.0,
) -> Dict[str, Any]:
    """Return dashboard/entry block metadata for the coin, or entryBlocked=False."""
    normalized = pair_to_hyperliquid_coin(coin)
    now_dt = now or datetime.utcnow()
    block_hours = max(0.0, float(realized_block_hours or 0.0))

    for trade in open_trades or []:
        trade_coin = pair_to_hyperliquid_coin(
            trade.get("coin") or trade.get("pair") or trade.get("source_pair") or ""
        )
        if trade_coin != normalized:
            continue
        try:
            upnl = float(trade.get("unrealized_pnl") or 0.0)
        except (TypeError, ValueError):
            upnl = 0.0
        if upnl < 0:
            return {
                "entryBlocked": True,
                "entryBlockReason": "open_unrealized_negative",
                "entryBlockUntil": None,
                "entryBlockMessage": f"open paper position is underwater (${upnl:.2f})",
            }

    latest_loss_time: Optional[datetime] = None
    latest_loss_pnl = 0.0
    for trade in closed_trades or []:
        trade_coin = pair_to_hyperliquid_coin(
            trade.get("coin") or trade.get("pair") or trade.get("source_pair") or ""
        )
        if trade_coin != normalized:
            continue
        try:
            rpnl = float(trade.get("realized_pnl") or 0.0)
        except (TypeError, ValueError):
            continue
        if rpnl >= 0:
            continue
        exit_time = _parse_dt(trade.get("exit_time") or trade.get("updated_at"))
        if exit_time is None:
            continue
        if latest_loss_time is None or exit_time > latest_loss_time:
            latest_loss_time = exit_time
            latest_loss_pnl = rpnl

    if latest_loss_time and block_hours > 0:
        until_dt = latest_loss_time + timedelta(hours=block_hours)
        if until_dt > now_dt:
            return {
                "entryBlocked": True,
                "entryBlockReason": "recent_negative_realized",
                "entryBlockUntil": until_dt.isoformat() + "+00:00",
                "entryBlockMessage": (
                    f"realized loss ${latest_loss_pnl:.2f}; cooldown until {until_dt.isoformat()} UTC"
                ),
            }

    return {
        "entryBlocked": False,
        "entryBlockReason": None,
        "entryBlockUntil": None,
        "entryBlockMessage": "",
    }


def hyperliquid_coin_side_entry_block(
    coin: str,
    side: str,
    open_trades: Iterable[Dict[str, Any]],
    closed_trades: Iterable[Dict[str, Any]],
    *,
    now: Optional[datetime] = None,
    realized_block_hours: float = 4.0,
) -> Dict[str, Any]:
    """Return entry block metadata for a single Hyperliquid coin+side."""
    normalized_coin = pair_to_hyperliquid_coin(coin)
    normalized_side = str(side or "").strip().lower()
    if not normalized_coin or normalized_side not in {"long", "short"}:
        return {
            "entryBlocked": False,
            "entryBlockReason": None,
            "entryBlockUntil": None,
            "entryBlockMessage": "",
            "entryBlockSide": normalized_side or None,
        }

    now_dt = now or datetime.utcnow()
    block_hours = max(0.0, float(realized_block_hours or 0.0))

    for trade in open_trades or []:
        trade_coin = pair_to_hyperliquid_coin(
            trade.get("coin") or trade.get("pair") or trade.get("source_pair") or ""
        )
        if trade_coin != normalized_coin:
            continue
        trade_side = str(
            trade.get("position_side")
            or position_sides_from_signal(trade.get("source_signal") or "")
            or ""
        ).strip().lower()
        if trade_side != normalized_side:
            continue
        try:
            upnl = float(trade.get("unrealized_pnl") or 0.0)
        except (TypeError, ValueError):
            upnl = 0.0
        if upnl < 0:
            return {
                "entryBlocked": True,
                "entryBlockReason": "open_unrealized_negative",
                "entryBlockUntil": None,
                "entryBlockMessage": (
                    f"open {normalized_side} paper position is underwater (${upnl:.2f})"
                ),
                "entryBlockSide": normalized_side,
            }

    latest_loss_time: Optional[datetime] = None
    latest_loss_pnl = 0.0
    for trade in closed_trades or []:
        trade_coin = pair_to_hyperliquid_coin(
            trade.get("coin") or trade.get("pair") or trade.get("source_pair") or ""
        )
        if trade_coin != normalized_coin:
            continue
        trade_side = str(
            trade.get("position_side")
            or position_sides_from_signal(trade.get("source_signal") or "")
            or ""
        ).strip().lower()
        if trade_side != normalized_side:
            continue
        try:
            rpnl = float(trade.get("realized_pnl") or 0.0)
        except (TypeError, ValueError):
            continue
        if rpnl >= 0:
            continue
        exit_time = _parse_dt(trade.get("exit_time") or trade.get("updated_at"))
        if exit_time is None:
            continue
        if latest_loss_time is None or exit_time > latest_loss_time:
            latest_loss_time = exit_time
            latest_loss_pnl = rpnl

    if latest_loss_time and block_hours > 0:
        until_dt = latest_loss_time + timedelta(hours=block_hours)
        if until_dt > now_dt:
            return {
                "entryBlocked": True,
                "entryBlockReason": "recent_negative_realized",
                "entryBlockUntil": until_dt.isoformat() + "+00:00",
                "entryBlockMessage": (
                    f"{normalized_side} realized loss ${latest_loss_pnl:.2f}; "
                    f"cooldown until {until_dt.isoformat()} UTC"
                ),
                "entryBlockSide": normalized_side,
            }

    return {
        "entryBlocked": False,
        "entryBlockReason": None,
        "entryBlockUntil": None,
        "entryBlockMessage": "",
        "entryBlockSide": normalized_side,
    }


def hyperliquid_strategy_side_entry_block(
    strategy: str,
    side: str,
    closed_trades: Iterable[Dict[str, Any]],
    *,
    now: Optional[datetime] = None,
    realized_block_hours: float = 4.0,
) -> Dict[str, Any]:
    """Block a strategy direction after its latest realized paper loss."""
    normalized_strategy = str(strategy or "").strip().lower()
    normalized_side = str(side or "").strip().lower()
    if not normalized_strategy or normalized_side not in {"long", "short"}:
        return {
            "entryBlocked": False,
            "entryBlockReason": None,
            "entryBlockUntil": None,
            "entryBlockMessage": "",
        }

    now_dt = now or datetime.utcnow()
    block_hours = max(0.0, float(realized_block_hours or 0.0))
    latest_loss_time: Optional[datetime] = None
    latest_loss_pnl = 0.0
    latest_loss_coin = ""

    for trade in closed_trades or []:
        trade_strategy = str(
            trade.get("source_strategy") or trade.get("strategy") or ""
        ).strip().lower()
        trade_side = str(
            trade.get("position_side") or trade.get("source_signal") or ""
        ).strip().lower()
        if trade_strategy != normalized_strategy or trade_side != normalized_side:
            continue
        try:
            rpnl = float(trade.get("realized_pnl") or 0.0)
        except (TypeError, ValueError):
            continue
        if rpnl >= 0:
            continue
        exit_time = _parse_dt(trade.get("exit_time") or trade.get("updated_at"))
        if exit_time is None:
            continue
        if latest_loss_time is None or exit_time > latest_loss_time:
            latest_loss_time = exit_time
            latest_loss_pnl = rpnl
            latest_loss_coin = pair_to_hyperliquid_coin(
                trade.get("coin") or trade.get("pair") or trade.get("source_pair") or ""
            )

    if latest_loss_time and block_hours > 0:
        until_dt = latest_loss_time + timedelta(hours=block_hours)
        if until_dt > now_dt:
            return {
                "entryBlocked": True,
                "entryBlockReason": "recent_strategy_side_negative_realized_12h",
                "entryBlockUntil": until_dt.isoformat() + "+00:00",
                "entryBlockMessage": (
                    f"{normalized_strategy} {normalized_side} realized loss "
                    f"${latest_loss_pnl:.2f} on {latest_loss_coin or 'perps'}; "
                    f"cooldown until {until_dt.isoformat()} UTC"
                ),
            }

    return {
        "entryBlocked": False,
        "entryBlockReason": None,
        "entryBlockUntil": None,
        "entryBlockMessage": "",
    }


def hyperliquid_strategy_open_position_limit_block(
    strategy: str,
    open_trades: Iterable[Dict[str, Any]],
    hl_cfg: Optional[Dict[str, Any]] = None,
    *,
    pending_open_count: int = 0,
) -> Dict[str, Any]:
    """Block a strategy when it has reached its configured open-position cap."""
    normalized_strategy = str(strategy or "").strip().lower()
    if not normalized_strategy:
        return {
            "entryBlocked": False,
            "entryBlockReason": None,
            "entryBlockMessage": "",
            "openCount": 0,
            "maxOpen": None,
        }

    raw_limits = ((hl_cfg or {}).get("strategy_open_position_limits") or {})
    if not isinstance(raw_limits, dict):
        raw_limits = {}
    raw_limit = raw_limits.get(normalized_strategy)
    if raw_limit is None:
        raw_limit = raw_limits.get(strategy)
    try:
        max_open = int(raw_limit)
    except (TypeError, ValueError):
        max_open = 0
    if max_open <= 0:
        return {
            "entryBlocked": False,
            "entryBlockReason": None,
            "entryBlockMessage": "",
            "openCount": 0,
            "maxOpen": None,
        }

    open_count = max(0, int(pending_open_count or 0))
    for trade in open_trades or []:
        trade_strategy = str(
            trade.get("source_strategy") or trade.get("strategy") or ""
        ).strip().lower()
        if trade_strategy == normalized_strategy:
            open_count += 1

    blocked = open_count >= max_open
    return {
        "entryBlocked": blocked,
        "entryBlockReason": "strategy_open_position_limit" if blocked else None,
        "entryBlockMessage": (
            f"{normalized_strategy} open-position cap reached: {open_count}/{max_open}"
            if blocked
            else ""
        ),
        "openCount": open_count,
        "maxOpen": max_open,
    }


def hyperliquid_strategy_coin_loss_streak_entry_block(
    coin: str,
    strategy: str,
    closed_trades: Iterable[Dict[str, Any]],
    *,
    now: Optional[datetime] = None,
    consecutive_losses: int = 2,
    cooldown_hours: float = 12.0,
) -> Dict[str, Any]:
    """Block a strategy on a coin after its latest consecutive loss streak."""
    normalized_coin = pair_to_hyperliquid_coin(coin)
    normalized_strategy = str(strategy or "").strip().lower()
    threshold = max(1, int(consecutive_losses or 1))
    hours = max(0.0, float(cooldown_hours or 0.0))
    if not normalized_coin or not normalized_strategy or hours <= 0:
        return {"entryBlocked": False, "entryBlockReason": None, "entryBlockMessage": ""}

    matching = []
    for trade in closed_trades or []:
        trade_coin = pair_to_hyperliquid_coin(
            trade.get("coin") or trade.get("pair") or trade.get("source_pair") or ""
        )
        trade_strategy = str(
            trade.get("source_strategy") or trade.get("strategy") or ""
        ).strip().lower()
        exit_time = _parse_dt(trade.get("exit_time") or trade.get("updated_at"))
        if trade_coin != normalized_coin or trade_strategy != normalized_strategy or exit_time is None:
            continue
        try:
            pnl = float(trade.get("realized_pnl") or 0.0)
        except (TypeError, ValueError):
            continue
        matching.append((exit_time, pnl))
    matching.sort(key=lambda item: item[0], reverse=True)

    streak = 0
    latest_exit = None
    for exit_time, pnl in matching:
        if pnl >= 0:
            break
        streak += 1
        if latest_exit is None:
            latest_exit = exit_time
    now_dt = now or datetime.utcnow()
    if streak >= threshold and latest_exit is not None:
        until_dt = latest_exit + timedelta(hours=hours)
        if until_dt > now_dt:
            return {
                "entryBlocked": True,
                "entryBlockReason": "strategy_coin_consecutive_losses",
                "entryBlockUntil": until_dt.isoformat() + "+00:00",
                "entryBlockMessage": (
                    f"{normalized_strategy} has {streak} consecutive losses on "
                    f"{normalized_coin}; cooldown until {until_dt.isoformat()} UTC"
                ),
                "consecutiveLosses": streak,
            }
    return {
        "entryBlocked": False,
        "entryBlockReason": None,
        "entryBlockUntil": None,
        "entryBlockMessage": "",
        "consecutiveLosses": streak,
    }


def hyperliquid_strategy_pair_stop_cooldown_block(
    coin: str,
    strategy: str,
    side: str,
    closed_trades: Iterable[Dict[str, Any]],
    *,
    now: Optional[datetime] = None,
    cooldown_hours: float = 6.0,
    stop_keywords: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Block same strategy+coin+side re-entry after a recent stop-like loss."""
    normalized_coin = pair_to_hyperliquid_coin(coin)
    normalized_strategy = str(strategy or "").strip().lower()
    normalized_side = str(side or "").strip().lower()
    hours = max(0.0, float(cooldown_hours or 0.0))
    if not normalized_coin or not normalized_strategy or hours <= 0:
        return {"entryBlocked": False, "entryBlockReason": None, "entryBlockMessage": ""}

    keywords = [
        str(item or "").strip().lower()
        for item in (
            stop_keywords
            or [
                "stop_loss",
                "loss_cap",
                "loss_recovery_expired",
                "stagnant_loser",
                "no_mfe",
            ]
        )
        if str(item or "").strip()
    ]
    latest_exit: Optional[datetime] = None
    latest_reason = ""
    for trade in closed_trades or []:
        trade_coin = pair_to_hyperliquid_coin(
            trade.get("coin") or trade.get("pair") or trade.get("source_pair") or ""
        )
        trade_strategy = str(
            trade.get("source_strategy") or trade.get("strategy") or ""
        ).strip().lower()
        trade_side = str(trade.get("side") or trade.get("position_side") or "").strip().lower()
        if (
            trade_coin != normalized_coin
            or trade_strategy != normalized_strategy
            or (normalized_side and trade_side and trade_side != normalized_side)
        ):
            continue
        try:
            pnl = float(trade.get("realized_pnl") or 0.0)
        except (TypeError, ValueError):
            continue
        if pnl >= 0:
            continue
        reason = str(trade.get("exit_reason") or trade.get("close_reason") or "").lower()
        if keywords and not any(keyword in reason for keyword in keywords):
            continue
        exit_time = _parse_dt(trade.get("exit_time") or trade.get("updated_at"))
        if exit_time is None:
            continue
        if latest_exit is None or exit_time > latest_exit:
            latest_exit = exit_time
            latest_reason = reason

    now_dt = now or datetime.utcnow()
    if latest_exit is not None:
        until_dt = latest_exit + timedelta(hours=hours)
        if until_dt > now_dt:
            return {
                "entryBlocked": True,
                "entryBlockReason": "strategy_pair_stop_cooldown",
                "entryBlockUntil": until_dt.isoformat() + "+00:00",
                "entryBlockMessage": (
                    f"{normalized_strategy} {normalized_coin} {normalized_side or 'side'} "
                    f"stop cooldown until {until_dt.isoformat()} UTC "
                    f"(latest={latest_reason or 'stop_like_loss'})"
                ),
            }

    return {
        "entryBlocked": False,
        "entryBlockReason": None,
        "entryBlockUntil": None,
        "entryBlockMessage": "",
    }


def _peak_pct(side: str, entry_price: float, extreme_price: float) -> float:
    if entry_price <= 0 or extreme_price <= 0:
        return 0.0
    if side == "short":
        return ((entry_price - extreme_price) / entry_price) * 100.0
    return ((extreme_price - entry_price) / entry_price) * 100.0


def _update_extreme_price(
    side: str, entry_price: float, current_price: float, metadata: Dict[str, Any]
) -> float:
    highest = max(float(metadata.get("highest_price") or entry_price), current_price)
    lowest = min(float(metadata.get("lowest_price") or entry_price), current_price)
    metadata["highest_price"] = highest
    metadata["lowest_price"] = lowest
    if side == "short":
        return lowest
    return highest


def _active_trail_step_decimal(cfg: PaperPerpExitConfig, peak_pct: float) -> float:
    if (
        cfg.dynamic_tightening_enabled
        and peak_pct >= cfg.tighten_profit_threshold_decimal * 100.0
    ):
        return cfg.tightened_step_decimal
    return cfg.trailing_step_decimal


def _trail_trigger_price(
    side: str,
    entry_price: float,
    extreme_price: float,
    step_decimal: float,
    cfg: PaperPerpExitConfig,
) -> float:
    """Mirror orchestrator new-trailing-stop trigger price (in-memory, no exchange order)."""
    if side == "short":
        calculated = extreme_price * (1.0 + step_decimal)
        floor_cap = entry_price * (1.0 - cfg.breakeven_floor_decimal)
        trigger = min(calculated, floor_cap)
        min_trigger = entry_price * (1.0 - cfg.min_trigger_distance_decimal)
        if trigger > min_trigger:
            trigger = min_trigger
        return trigger

    calculated = extreme_price * (1.0 - step_decimal)
    floor_price = entry_price * (1.0 + cfg.breakeven_floor_decimal)
    trigger = max(calculated, floor_price)
    min_trigger = entry_price * (1.0 + cfg.min_trigger_distance_decimal)
    if trigger < min_trigger:
        trigger = min_trigger
    return trigger


def _is_better_trigger(side: str, new_trigger: float, old_trigger: float) -> bool:
    if old_trigger <= 0:
        return True
    if side == "short":
        return new_trigger < old_trigger
    return new_trigger > old_trigger


def _max_holding_exit(
    trade: Dict[str, Any],
    max_holding_minutes: int,
    now: Optional[datetime] = None,
) -> Optional[str]:
    """Legacy helper retained for unit-test compatibility. Returns plain reason."""
    if max_holding_minutes <= 0:
        return None
    elapsed = _elapsed_minutes_since_entry(trade, now=now)
    if elapsed is None:
        return None
    if elapsed >= max_holding_minutes:
        return "paper_max_holding_time"
    return None


def _elapsed_minutes_since_entry(
    trade: Dict[str, Any],
    now: Optional[datetime] = None,
) -> Optional[float]:
    raw_entry = trade.get("entry_time")
    try:
        entry_time = (
            raw_entry
            if isinstance(raw_entry, datetime)
            else datetime.fromisoformat(str(raw_entry).replace("Z", "+00:00")).replace(tzinfo=None)
        )
        if isinstance(entry_time, datetime) and entry_time.tzinfo is not None:
            entry_time = entry_time.replace(tzinfo=None)
        now_dt = now or datetime.utcnow()
        if now_dt.tzinfo:
            now_dt = now_dt.replace(tzinfo=None)
        return (now_dt - entry_time).total_seconds() / 60.0
    except Exception:
        return None


def _atr_pct_from_trade(trade: Dict[str, Any]) -> Optional[float]:
    """Read entry-time ATR (as percent of entry price) from trade metadata if available."""
    metadata = trade.get("metadata") or {}
    for key in ("entry_atr_pct", "atr_pct", "stop_loss_atr_pct"):
        raw = metadata.get(key)
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


def perp_entry_atr_metadata(
    mirrored_signal: Dict[str, Any],
    entry_price: float,
) -> Dict[str, Any]:
    """
    Extract ATR-as-percent-of-entry-price from a mirrored signal payload.

    Looks under details.indicators / details.state.indicators / details
    for any of: atr_pct, atr_percent, atr_percentage, atr (absolute price).
    Absolute ATR values are converted to percent using entry_price.
    Returns {"entry_atr_pct": float} on success or {} otherwise — callers
    can splat into the new trade's metadata.
    """
    if not isinstance(mirrored_signal, dict):
        return {}
    details = mirrored_signal.get("details") or {}
    if not isinstance(details, dict):
        return {}

    candidates: List[Mapping[str, Any]] = []
    indicators = details.get("indicators")
    if isinstance(indicators, dict):
        candidates.append(indicators)
    state = details.get("state")
    if isinstance(state, dict):
        nested = state.get("indicators")
        if isinstance(nested, dict):
            candidates.append(nested)
    candidates.append(details)

    pct_keys = ("atr_pct", "atr_percent", "atr_percentage")
    abs_keys = ("atr", "atr_value", "ATR")

    for source in candidates:
        for key in pct_keys:
            raw = source.get(key)
            try:
                value = float(raw) if raw is not None else 0.0
            except (TypeError, ValueError):
                value = 0.0
            if value > 0:
                if value < 1.0:
                    value *= 100.0
                return {"entry_atr_pct": value}

    if entry_price and entry_price > 0:
        for source in candidates:
            for key in abs_keys:
                raw = source.get(key)
                try:
                    value = float(raw) if raw is not None else 0.0
                except (TypeError, ValueError):
                    value = 0.0
                if value > 0:
                    pct = (value / entry_price) * 100.0
                    if pct > 0:
                        return {"entry_atr_pct": pct}
    return {}


def _setup_stop_pct_from_trade(trade: Dict[str, Any]) -> Optional[float]:
    setup = setup_risk_from_trade_metadata(trade)
    entry_price = _safe_float(trade.get("entry_price"), 0.0)
    val = resolve_setup_stop_pct(setup, entry_price)
    if val <= 0:
        return None
    return val


def _effective_stop_pct(
    trade: Dict[str, Any],
    cfg: PaperPerpExitConfig,
) -> float:
    """Resolve the effective stop loss percentage for this trade.

    Priority: setup metadata stop > per-coin override > ATR-derived > fixed cfg.stop_loss_pct.
    """
    strategy_key = str(trade.get("source_strategy") or trade.get("strategy") or "").strip().lower()
    setup_stop = _setup_stop_pct_from_trade(trade)
    if setup_stop is not None and (
        cfg.use_setup_stops
        or strategy_key in {
            "sma_reclaim_bull_flag",
            "supply_demand_3step",
            "dual_sma_daytrade",
            "arc_daytrade",
            "ema50_breakout_pullback",
        }
    ):
        return setup_stop

    coin = str(trade.get("coin") or "").strip().upper()
    if coin and coin in cfg.per_coin_stop_overrides:
        return float(cfg.per_coin_stop_overrides[coin])

    if cfg.stop_loss_atr_enabled:
        atr_pct = _atr_pct_from_trade(trade)
        if atr_pct is not None and atr_pct > 0:
            derived = atr_pct * cfg.stop_loss_atr_mult
            lo = max(0.0, cfg.stop_loss_atr_min_pct)
            hi = max(lo, cfg.stop_loss_atr_max_pct)
            return float(max(lo, min(hi, derived)))

    return float(cfg.stop_loss_pct)


def _max_holding_decision(
    trade: Dict[str, Any],
    pct: float,
    cfg: PaperPerpExitConfig,
    metadata: Dict[str, Any],
    now: Optional[datetime] = None,
) -> Tuple[Optional[str], bool]:
    """Decide on max-holding-time exits with breakeven + salvage trail.

    Returns (exit_reason_or_none, metadata_changed).
    Behavior:
      - If position has been open >= max_holding_minutes:
          * pct >= -fee_floor → exit "paper_max_holding_time_flat".
          * Else → engage salvage mode (metadata flag) and stay open.
      - In salvage mode:
          * pct >= 0 → exit "paper_max_holding_time_be" (price recovered).
          * elapsed >= max_holding_minutes_hard → "paper_max_holding_time_hard".
      - Stop loss and trailing logic continue to apply outside this helper.
    """
    if cfg.max_holding_minutes <= 0:
        return None, False

    elapsed = _elapsed_minutes_since_entry(trade, now=now)
    if elapsed is None:
        return None, False

    fee_floor_pct = cfg.effective_profit_floor_decimal * 100.0
    in_salvage = bool(metadata.get("salvage_mode"))
    changed = False

    if in_salvage:
        if pct >= 0:
            return "paper_max_holding_time_be", False
        if (
            cfg.max_holding_minutes_hard > 0
            and elapsed >= cfg.max_holding_minutes_hard
        ):
            return "paper_max_holding_time_hard", False
        return None, False

    if elapsed >= cfg.max_holding_minutes:
        if pct >= -fee_floor_pct:
            return "paper_max_holding_time_flat", False
        if cfg.max_holding_minutes_hard > cfg.max_holding_minutes:
            metadata["salvage_mode"] = True
            metadata["salvage_engaged_at_pct"] = pct
            changed = True
            return None, changed
        return "paper_max_holding_time", False

    return None, changed


def _stagnant_loser_decision(
    trade: Dict[str, Any],
    side: str,
    entry_price: float,
    pct: float,
    peak_pct: float,
    cfg: PaperPerpExitConfig,
    metadata: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> Optional[str]:
    """Pre-empt full stop-loss on trades that never arm profit protection (spot parity)."""
    if not cfg.stagnant_loser_enabled or entry_price <= 0:
        return None

    meta = metadata or {}
    if meta.get("salvage_mode"):
        return None

    age_minutes = _elapsed_minutes_since_entry(trade, now=now)
    if age_minutes is None:
        return None
    # Let max-hold salvage handle underwater positions past the soft cap.
    if cfg.max_holding_minutes > 0 and age_minutes >= cfg.max_holding_minutes:
        return None

    sl = cfg.stagnant_loser or {}
    try:
        min_age_minutes = float(sl.get("min_age_minutes", 25.0) or 25.0)
    except (TypeError, ValueError):
        min_age_minutes = 25.0
    try:
        base_peak_cap_pct = float(sl.get("peak_cap_pct", 0.55) or 0.55)
    except (TypeError, ValueError):
        base_peak_cap_pct = 0.55

    pp_act_dec = cfg.profit_protection_activation_decimal
    if pp_act_dec > 0:
        activation_peak_cap = (pp_act_dec * 100.0) * 0.92
        base_peak_cap_pct = min(base_peak_cap_pct, activation_peak_cap)

    try:
        base_loss_trigger_pct = float(sl.get("loss_trigger_pct", -0.65) or -0.65)
    except (TypeError, ValueError):
        base_loss_trigger_pct = -0.65
    try:
        volatility_ref_pct = float(sl.get("volatility_reference_pct", 0.8) or 0.8)
    except (TypeError, ValueError):
        volatility_ref_pct = 0.8
    try:
        peak_cap_slope = float(sl.get("peak_cap_slope", 0.35) or 0.35)
    except (TypeError, ValueError):
        peak_cap_slope = 0.35
    try:
        loss_trigger_slope = float(sl.get("loss_trigger_slope", 0.45) or 0.45)
    except (TypeError, ValueError):
        loss_trigger_slope = 0.45
    try:
        min_age_floor = float(sl.get("min_age_floor_minutes", 20.0) or 20.0)
    except (TypeError, ValueError):
        min_age_floor = 20.0
    try:
        min_age_ceiling = float(sl.get("min_age_ceiling_minutes", 45.0) or 45.0)
    except (TypeError, ValueError):
        min_age_ceiling = 45.0

    vol_factor = abs(float(pct)) / max(0.1, volatility_ref_pct)
    dynamic_age_minutes = min_age_minutes / max(0.75, vol_factor)
    dynamic_age_minutes = max(min_age_floor, min(min_age_ceiling, dynamic_age_minutes))
    dynamic_peak_cap_pct = base_peak_cap_pct + (max(0.0, vol_factor - 1.0) * peak_cap_slope)
    dynamic_loss_trigger_pct = base_loss_trigger_pct - (
        max(0.0, vol_factor - 1.0) * loss_trigger_slope
    )

    try:
        fast_fail_peak = float(sl.get("fast_fail_peak_pct", 0.15) or 0.15)
    except (TypeError, ValueError):
        fast_fail_peak = 0.15
    try:
        fast_fail_age = float(sl.get("fast_fail_min_age_minutes", 10.0) or 10.0)
    except (TypeError, ValueError):
        fast_fail_age = 10.0
    try:
        fast_fail_loss = float(sl.get("fast_fail_loss_pct", -0.40) or -0.40)
    except (TypeError, ValueError):
        fast_fail_loss = -0.40

    no_mfe_enabled = sl.get("no_mfe_fast_fail_enabled", True)
    no_mfe_enabled = no_mfe_enabled is not False and str(no_mfe_enabled).lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    try:
        no_mfe_peak = float(sl.get("no_mfe_peak_pct", 0.03) or 0.03)
    except (TypeError, ValueError):
        no_mfe_peak = 0.03
    try:
        no_mfe_age = float(sl.get("no_mfe_min_age_minutes", fast_fail_age) or fast_fail_age)
    except (TypeError, ValueError):
        no_mfe_age = fast_fail_age
    try:
        no_mfe_loss = float(sl.get("no_mfe_loss_pct", fast_fail_loss) or fast_fail_loss)
    except (TypeError, ValueError):
        no_mfe_loss = fast_fail_loss
    no_mfe_fast_fail = (
        no_mfe_enabled
        and age_minutes >= no_mfe_age
        and peak_pct <= no_mfe_peak
        and pct <= no_mfe_loss
    )
    fast_fail = (
        age_minutes >= fast_fail_age
        and peak_pct <= fast_fail_peak
        and pct <= fast_fail_loss
    )
    stagnant_standard = (
        age_minutes >= dynamic_age_minutes
        and peak_pct <= dynamic_peak_cap_pct
        and pct <= dynamic_loss_trigger_pct
    )
    if not no_mfe_fast_fail and not fast_fail and not stagnant_standard:
        return None

    tag = "no_mfe_fast_fail" if no_mfe_fast_fail else "fast_fail" if fast_fail else "divergence"
    return (
        f"paper_stagnant_loser_{tag}@{pct:.2f}%"
        f"_peak{peak_pct:.2f}%_age{age_minutes:.0f}m"
    )


def _estimated_perp_net_pnl_usd(
    trade: Dict[str, Any],
    side: str,
    entry_price: float,
    current_price: float,
    cfg: PaperPerpExitConfig,
) -> Optional[float]:
    """Estimate current paper perp PnL including recorded costs and exit fee."""
    size = _safe_float(trade.get("position_size"), 0.0)
    if size <= 0:
        notional = _safe_float(trade.get("notional_size"), 0.0)
        if entry_price > 0 and notional > 0:
            size = notional / entry_price
    if size <= 0:
        return None
    if side == "short":
        gross = (entry_price - current_price) * size
    else:
        gross = (current_price - entry_price) * size
    recorded_costs = abs(_safe_float(trade.get("fees"), 0.0)) + abs(
        _safe_float(trade.get("funding"), 0.0)
    )
    exit_fee_estimate = abs(current_price * size * cfg.fee_rate_per_side)
    return gross - recorded_costs - exit_fee_estimate


def evaluate_paper_perp_exit(
    trade: Dict[str, Any],
    current_price: float,
    cfg: PaperPerpExitConfig,
    now: Optional[datetime] = None,
) -> PaperPerpExitResult:
    """
    Paper perp exit evaluation using the same trailing / profit-protection model as spot.

    State is persisted in trade metadata (highest_price, trail_stop_trigger, etc.).
    """
    metadata = dict(trade.get("metadata") or {})
    if current_price <= 0:
        return PaperPerpExitResult(None, metadata)

    entry_price = float(trade.get("entry_price") or 0.0)
    side = str(trade.get("position_side") or "long").lower()
    if entry_price <= 0:
        return PaperPerpExitResult(None, metadata)

    extreme = _update_extreme_price(side, entry_price, current_price, metadata)
    pct = pnl_percentage(side, entry_price, current_price)
    peak_pct = _peak_pct(side, entry_price, extreme)
    age_minutes = _elapsed_minutes_since_entry(trade, now=now)
    strategy_key = str(
        trade.get("source_strategy") or trade.get("strategy") or ""
    ).strip().lower()
    net_pnl_usd = _estimated_perp_net_pnl_usd(
        trade, side, entry_price, current_price, cfg
    )
    suppress_percentage_stop_for_recovery = False

    if cfg.dollar_loss_cap_enabled and net_pnl_usd is not None:
        max_loss_usd = cfg.dollar_loss_cap_by_strategy.get(
            strategy_key, cfg.dollar_loss_cap_default_usd
        )
        soft_loss_usd = cfg.dollar_loss_recovery_soft_usd
        if soft_loss_usd <= 0 and max_loss_usd > 0:
            soft_loss_usd = max_loss_usd
        hard_loss_pct = cfg.dollar_loss_hard_pct_by_strategy.get(
            strategy_key, cfg.dollar_loss_hard_pct
        )
        soft_loss_pct = cfg.dollar_loss_soft_pct
        estimated_loss_usd = -net_pnl_usd
        estimated_loss_pct = -pct
        hard_cap_hit = (
            (hard_loss_pct > 0 and estimated_loss_pct >= hard_loss_pct)
            or (max_loss_usd > 0 and estimated_loss_usd >= max_loss_usd)
        )
        soft_cap_hit = (
            soft_loss_pct > 0 and estimated_loss_pct >= soft_loss_pct
        ) or (
            soft_loss_pct <= 0 and soft_loss_usd > 0 and estimated_loss_usd >= soft_loss_usd
        )
        if hard_cap_hit:
            cap_label = (
                f"{hard_loss_pct:.2f}%"
                if hard_loss_pct > 0 and estimated_loss_pct >= hard_loss_pct
                else f"${max_loss_usd:.2f}"
            )
            return PaperPerpExitResult(
                f"paper_loss_cap_{cap_label}@pnl${net_pnl_usd:.2f}_{pct:.2f}%",
                metadata,
            )
        if (
            soft_cap_hit
            and cfg.dollar_loss_recovery_minutes > 0
            and age_minutes is not None
            and age_minutes < cfg.dollar_loss_recovery_minutes
        ):
            suppress_percentage_stop_for_recovery = cfg.dollar_loss_suppress_percentage_stop
            metadata["dollar_loss_recovery"] = {
                "soft_loss_usd": soft_loss_usd,
                "soft_loss_pct": soft_loss_pct,
                "max_loss_usd": max_loss_usd,
                "hard_loss_pct": hard_loss_pct,
                "age_minutes": round(age_minutes, 2),
                "estimated_pnl_usd": round(net_pnl_usd, 4),
                "estimated_loss_pct": round(estimated_loss_pct, 4),
            }
        elif (
            soft_cap_hit
            and cfg.dollar_loss_recovery_minutes > 0
            and age_minutes is not None
            and age_minutes >= cfg.dollar_loss_recovery_minutes
        ):
            cap_label = (
                f"{hard_loss_pct:.2f}%"
                if hard_loss_pct > 0
                else f"${max_loss_usd:.2f}"
            )
            return PaperPerpExitResult(
                f"paper_loss_recovery_expired_{cap_label}@pnl${net_pnl_usd:.2f}_{pct:.2f}%",
                metadata,
            )

    if (
        cfg.time_decay_exit_enabled
        and net_pnl_usd is not None
        and age_minutes is not None
        and cfg.time_decay_max_loss_usd > 0
    ):
        min_age = cfg.time_decay_min_age_by_strategy.get(
            strategy_key, cfg.time_decay_min_age_minutes
        )
        if min_age > 0 and age_minutes >= min_age and -net_pnl_usd >= cfg.time_decay_max_loss_usd:
            return PaperPerpExitResult(
                f"paper_time_decay_loss_exit_${cfg.time_decay_max_loss_usd:.2f}@pnl${net_pnl_usd:.2f}_age{age_minutes:.0f}m",
                metadata,
            )

    effective_stop_pct = _effective_stop_pct(trade, cfg)
    if (
        cfg.fixed_stop_loss_enabled
        and not suppress_percentage_stop_for_recovery
        and effective_stop_pct > 0
        and pct <= -abs(effective_stop_pct)
    ):
        return PaperPerpExitResult("paper_stop_loss", metadata)

    setup = setup_risk_from_trade_metadata(trade)
    if cfg.breakeven_on_swing_high and side == "long":
        swing_high = _safe_float(setup.get("breakeven_trigger_swing_high"), 0.0)
        if (
            swing_high > entry_price
            and current_price >= swing_high
            and not metadata.get("setup_breakeven_armed")
        ):
            trigger_px = entry_price * (1.0 + cfg.breakeven_floor_decimal)
            metadata["trail_stop_trigger"] = trigger_px
            metadata["setup_breakeven_armed"] = True
            metadata["profit_protection"] = metadata.get("profit_protection") or "setup_breakeven"

    holding_exit, _ = _max_holding_decision(trade, pct, cfg, metadata, now=now)
    if holding_exit:
        return PaperPerpExitResult(holding_exit, metadata)

    if not cfg.use_spot_exit_rules:
        if cfg.take_profit_pct > 0 and pct >= cfg.take_profit_pct:
            return PaperPerpExitResult("paper_take_profit", metadata)
        return PaperPerpExitResult(None, metadata)

    stagnant_exit = _stagnant_loser_decision(
        trade, side, entry_price, pct, peak_pct, cfg, metadata=metadata, now=now
    )
    if stagnant_exit:
        return PaperPerpExitResult(stagnant_exit, metadata)

    if cfg.use_setup_targets:
        target_pct = resolve_setup_target_pct(setup, entry_price)
        if (
            cfg.partial_profit_pct > 0
            and target_pct > 0
            and not metadata.get("setup_partial_taken")
            and pct >= target_pct * cfg.partial_profit_pct
        ):
            metadata["setup_partial_taken"] = True
            return PaperPerpExitResult(
                f"paper_setup_partial_profit@{pct:.2f}%",
                metadata,
            )
        if target_pct > 0 and pct >= target_pct:
            return PaperPerpExitResult(
                f"paper_setup_target@{pct:.2f}%",
                metadata,
            )

    if cfg.overall_take_profit_pct > 0 and pct >= cfg.overall_take_profit_pct:
        return PaperPerpExitResult(
            f"paper_overall_take_profit_{cfg.overall_take_profit_pct:.2f}%@{pct:.2f}%",
            metadata,
        )

    trail_active = str(metadata.get("trail_stop") or "").lower() == "active"
    pp_status = metadata.get("profit_protection")
    trailing_activation_pct = cfg.trailing_activation_decimal * 100.0
    pp_activation_pct = cfg.profit_protection_activation_decimal * 100.0

    tiered_lock = evaluate_tiered_profit_lock(
        config=cfg.early_profit_locks,
        peak_pct=peak_pct,
        entry_price=entry_price,
        current_price=current_price,
        existing_trigger=metadata.get("trail_stop_trigger"),
        side=side,
    )
    if tiered_lock.action == "late_exit":
        metadata["early_profit_lock_tier"] = tiered_lock.tier_index
        metadata["early_profit_lock_floor_pct"] = tiered_lock.floor_decimal * 100.0
        return PaperPerpExitResult(
            (
                f"paper_early_profit_lock_late_breach_tier{tiered_lock.tier_index}"
                f"@{pct:.2f}%_peak{peak_pct:.2f}%"
                f"_floor{tiered_lock.floor_price:.6f}"
                "|profit_protection_breach"
            ),
            metadata,
            tiered_lock.floor_price,
        )
    if tiered_lock.action == "raise":
        metadata["trail_stop_trigger"] = merge_trail_trigger_for_side(
            metadata.get("trail_stop_trigger"),
            tiered_lock.floor_price,
            side=side,
        )
        metadata["profit_protection"] = "profit_guaranteed"
        metadata["profit_protection_trigger"] = pct
        metadata["early_profit_lock_tier"] = tiered_lock.tier_index
        metadata["early_profit_lock_activation_pct"] = (
            tiered_lock.activation_decimal * 100.0
        )
        metadata["early_profit_lock_floor_pct"] = tiered_lock.floor_decimal * 100.0
        metadata["early_profit_lock_peak_pct"] = peak_pct
        pp_status = "profit_guaranteed"

    # Port spot can_arm / late-arm semantics: milestones may upgrade; never leave
    # profit_guaranteed when mark is already through the floor.
    arm_decision = evaluate_profit_protection_arm(
        status=pp_status,
        peak_pct=peak_pct,
        activation_pct=pp_activation_pct,
        entry_price=entry_price,
        current_price=current_price,
        floor_decimal=cfg.breakeven_floor_decimal,
        trailing_active=trail_active,
        enabled=cfg.profit_protection_enabled,
        side=side,
    )
    if arm_decision.action == "late_exit":
        return PaperPerpExitResult(
            f"paper_{format_late_arm_exit_reason(pnl_percentage=pct, floor_price=arm_decision.floor_price, current_price=current_price)}",
            metadata,
            arm_decision.floor_price,
        )
    if arm_decision.action == "arm":
        metadata["trail_stop_trigger"] = merge_trail_trigger_for_side(
            metadata.get("trail_stop_trigger"),
            arm_decision.floor_price,
            side=side,
        )
        metadata["profit_protection"] = "profit_guaranteed"
        metadata["profit_protection_trigger"] = pct

    # Armed floor breach is always executable (no LOSS/NET stranding).
    if (
        cfg.profit_protection_enabled
        and should_breach_exit_for_status(metadata.get("profit_protection"))
        and not trail_active
    ):
        pp_trigger = float(metadata.get("trail_stop_trigger") or 0.0)
        if pp_trigger > 0:
            breached = (
                (side == "long" and current_price <= pp_trigger)
                or (side == "short" and current_price >= pp_trigger)
            )
            if breached:
                reason = format_breach_exit_reason(
                    metadata.get("profit_protection"),
                    pnl_percentage=pct,
                    trigger_price=pp_trigger,
                    current_price=current_price,
                )
                return PaperPerpExitResult(
                    f"paper_{reason}",
                    metadata,
                    pp_trigger,
                )

    if cfg.trailing_enabled:
        step_decimal = _active_trail_step_decimal(cfg, peak_pct)
        min_peak_pct_for_trail = (cfg.breakeven_floor_decimal + step_decimal) * 100.0

        if trail_active:
            new_trigger = _trail_trigger_price(side, entry_price, extreme, step_decimal, cfg)
            old_trigger = float(metadata.get("trail_stop_trigger") or 0.0)
            if _is_better_trigger(side, new_trigger, old_trigger):
                metadata["trail_stop_trigger"] = new_trigger
            trigger_px = float(metadata.get("trail_stop_trigger") or 0.0)
            if trigger_px > 0:
                if side == "long" and current_price <= trigger_px:
                    return PaperPerpExitResult(
                        f"paper_trailing_stop_trigger_${trigger_px:.4f}@{pct:.2f}%",
                        metadata,
                        trigger_px,
                    )
                if side == "short" and current_price >= trigger_px:
                    return PaperPerpExitResult(
                        f"paper_trailing_stop_trigger_${trigger_px:.4f}@{pct:.2f}%",
                        metadata,
                        trigger_px,
                    )
        elif pct >= trailing_activation_pct and extreme > 0:
            if peak_pct >= min_peak_pct_for_trail:
                if side == "long" and extreme <= entry_price:
                    pass
                elif side == "short" and extreme >= entry_price:
                    pass
                else:
                    trigger_px = _trail_trigger_price(
                        side, entry_price, extreme, step_decimal, cfg
                    )
                    metadata["trail_stop"] = "active"
                    metadata["trail_stop_trigger"] = trigger_px
                    metadata["profit_protection"] = "trailing"
                    trail_active = True

    if cfg.take_profit_pct > 0 and pct >= cfg.take_profit_pct and not trail_active:
        return PaperPerpExitResult("paper_take_profit", metadata)

    return PaperPerpExitResult(None, metadata)


def should_close_paper_perp(
    trade: Dict[str, Any],
    current_price: float,
    *,
    stop_loss_pct: float,
    take_profit_pct: float,
    max_holding_minutes: int,
    now: Optional[datetime] = None,
) -> Optional[str]:
    """Return an exit reason when a paper position should close (fixed TP/SL fallback)."""
    cfg = PaperPerpExitConfig(
        use_spot_exit_rules=False,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        max_holding_minutes=max_holding_minutes,
        trailing_enabled=False,
        profit_protection_enabled=False,
    )
    result = evaluate_paper_perp_exit(trade, current_price, cfg, now=now)
    return result.exit_reason


def filter_allowed_coin(coin: str, allowed_symbols: Iterable[str]) -> bool:
    allowed = {str(x).upper().strip() for x in (allowed_symbols or []) if str(x).strip()}
    return not allowed or str(coin or "").upper().strip() in allowed


def hyperliquid_signal_prefetch_settings(
    hl_cfg: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Resolve orchestrator HL signal prefetch knobs from hyperliquid_perps config."""
    raw = (hl_cfg or {}).get("signal_prefetch") or {}
    if not isinstance(raw, Mapping):
        raw = {}
    return {
        "timeout_seconds": max(
            5.0,
            min(float(raw.get("timeout_seconds", 20.0) or 20.0), 45.0),
        ),
        "retries": max(0, min(int(raw.get("retries", 2) or 2), 4)),
        "retry_delay_seconds": max(
            0.0,
            min(float(raw.get("retry_delay_seconds", 0.35) or 0.35), 2.0),
        ),
        "inline_fallback": bool(raw.get("inline_fallback", True)),
        "max_prefetch_seconds": max(
            10.0,
            min(float(raw.get("max_prefetch_seconds", 30.0) or 30.0), 60.0),
        ),
        "entry_evaluation_reserve_seconds": max(
            5.0,
            min(float(raw.get("entry_evaluation_reserve_seconds", 12.0) or 12.0), 30.0),
        ),
    }


def hyperliquid_signal_prefetch_health(stats: Mapping[str, Any]) -> str:
    """Classify last perp entry signal prefetch outcome: ok | degraded | failed | unknown."""
    if stats.get("skipped"):
        return "ok"
    requested = int(stats.get("requested") or 0)
    if requested <= 0:
        return "unknown"
    scanned_missed = int(stats.get("scanned_missed") or 0)
    if scanned_missed == 0:
        return "ok"
    ratio = (requested - scanned_missed) / float(requested)
    if ratio >= 0.9:
        return "degraded"
    return "failed"


def _hyperliquid_signals_url(
    *,
    coin_key: str,
    signal_source: str,
    strategy_service_url: str,
    mirror_exchanges: Iterable[str],
    pair_selections: Mapping[str, Any],
) -> Tuple[Optional[str], str]:
    if str(signal_source or "").lower() == "hyperliquid_strategies":
        return (
            f"{strategy_service_url.rstrip('/')}/api/v1/signals/hyperliquid/{coin_key}",
            "hyperliquid_strategies",
        )
    spot_ex, spot_pair = find_mirror_spot_pair(
        coin_key, mirror_exchanges, dict(pair_selections or {})
    )
    if not spot_ex or not spot_pair:
        return None, "no_mirror_pair"
    strategy_pair = str(spot_pair).replace("/", "")
    return (
        f"{strategy_service_url.rstrip('/')}/api/v1/signals/{spot_ex}/{strategy_pair}",
        "mirror_spot",
    )


async def fetch_hyperliquid_entry_signal_payload(
    client: Any,
    *,
    coin_key: str,
    signal_source: str,
    strategy_service_url: str,
    mirror_exchanges: Iterable[str],
    pair_selections: Mapping[str, Any],
    timeout_seconds: float = 20.0,
    retries: int = 2,
    retry_delay_seconds: float = 0.35,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """Fetch one coin's strategy payload. Returns (payload, reason)."""
    signals_url, source_kind = _hyperliquid_signals_url(
        coin_key=coin_key,
        signal_source=signal_source,
        strategy_service_url=strategy_service_url,
        mirror_exchanges=mirror_exchanges,
        pair_selections=pair_selections,
    )
    if not signals_url:
        return None, source_kind

    attempts = max(1, int(retries or 0) + 1)
    last_reason = "unknown"
    for attempt in range(1, attempts + 1):
        try:
            signals_resp = await client.get(signals_url, timeout=float(timeout_seconds))
            if signals_resp.status_code == 200:
                payload = signals_resp.json()
                if isinstance(payload, dict) and payload:
                    return payload, "ok"
                last_reason = "empty_payload"
            else:
                last_reason = f"http_{signals_resp.status_code}"
        except Exception as exc:
            last_reason = f"error:{type(exc).__name__}"
            logger.warning(
                "[HyperliquidPaper] Signal fetch failed for %s (%s) attempt %s/%s: %s",
                coin_key,
                source_kind,
                attempt,
                attempts,
                exc,
            )
        if attempt < attempts and retry_delay_seconds > 0:
            await asyncio.sleep(float(retry_delay_seconds))
    return None, last_reason


async def prefetch_hyperliquid_entry_signals(
    client: Any,
    *,
    coins: Sequence[str],
    signal_source: str,
    strategy_service_url: str,
    mirror_exchanges: Iterable[str],
    pair_selections: Mapping[str, Any],
    hl_cfg: Optional[Mapping[str, Any]] = None,
    deadline: Optional[float] = None,
    coin_filter_set: Optional[set] = None,
    concurrency: int = 8,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    """Parallel prefetch with retry pass for misses."""
    settings = hyperliquid_signal_prefetch_settings(hl_cfg)
    prefetch_conc = max(1, min(int(concurrency or 8), 16))
    prefetch_sem = asyncio.Semaphore(prefetch_conc)
    prefetched: Dict[str, Dict[str, Any]] = {}
    failure_reasons: Dict[str, str] = {}

    if deadline is not None:
        remaining = max(0.0, deadline - time.monotonic())
        reserve = float(settings["entry_evaluation_reserve_seconds"])
        budget = min(settings["max_prefetch_seconds"], max(0.0, remaining - reserve))
    else:
        budget = settings["max_prefetch_seconds"]
    if budget <= 0.5:
        logger.warning(
            "[HyperliquidPaper] Signal prefetch budget exhausted (%.2fs left, reserve %.0fs)",
            max(0.0, (deadline - time.monotonic()) if deadline else 0.0),
            settings["entry_evaluation_reserve_seconds"],
        )
        return prefetched, {
            "requested": len(coins or []),
            "ok": 0,
            "missed": len(coins or []),
            "failure_reasons": {"__deadline__": "prefetch_budget_exhausted"},
        }

    async def _fetch_one(coin_raw: str) -> Tuple[str, Optional[Dict[str, Any]], str]:
        coin_key = pair_to_hyperliquid_coin(str(coin_raw))
        if coin_filter_set is not None and coin_key not in coin_filter_set:
            return coin_key, None, "coin_filter"
        if deadline is not None:
            reserve = float(settings["entry_evaluation_reserve_seconds"])
            if time.monotonic() >= deadline - reserve:
                return coin_key, None, "deadline"
        async with prefetch_sem:
            if deadline is not None:
                reserve = float(settings["entry_evaluation_reserve_seconds"])
                remaining = deadline - time.monotonic() - reserve
                if remaining <= 0.5:
                    return coin_key, None, "deadline"
                req_timeout = min(
                    float(settings["timeout_seconds"]),
                    remaining,
                )
            else:
                req_timeout = float(settings["timeout_seconds"])
            payload, reason = await fetch_hyperliquid_entry_signal_payload(
                client,
                coin_key=coin_key,
                signal_source=signal_source,
                strategy_service_url=strategy_service_url,
                mirror_exchanges=mirror_exchanges,
                pair_selections=pair_selections,
                timeout_seconds=req_timeout,
                retries=settings["retries"],
                retry_delay_seconds=settings["retry_delay_seconds"],
            )
            return coin_key, payload, reason

    async def _run_pass(target_coins: Sequence[str]) -> None:
        results = await asyncio.gather(
            *(_fetch_one(c) for c in target_coins),
            return_exceptions=True,
        )
        for item in results:
            if not isinstance(item, tuple) or len(item) != 3:
                continue
            coin_key, payload, reason = item
            if isinstance(payload, dict):
                prefetched[coin_key] = payload
                failure_reasons.pop(coin_key, None)
            elif coin_key not in prefetched:
                failure_reasons[coin_key] = str(reason or "miss")

    await _run_pass(coins)

    missed_keys = [
        pair_to_hyperliquid_coin(str(c))
        for c in (coins or [])
        if pair_to_hyperliquid_coin(str(c)) not in prefetched
        and (coin_filter_set is None or pair_to_hyperliquid_coin(str(c)) in coin_filter_set)
    ]
    if missed_keys:
        if deadline is not None:
            retry_budget = max(
                0.0,
                min(15.0, deadline - time.monotonic() - settings["entry_evaluation_reserve_seconds"]),
            )
        else:
            retry_budget = min(15.0, settings["max_prefetch_seconds"] * 0.4)
        if retry_budget > 1.0:
            await _run_pass(missed_keys)

    stats = {
        "requested": len(coins or []),
        "ok": len(prefetched),
        "missed": max(0, len(coins or []) - len(prefetched)),
        "failure_reasons": dict(failure_reasons),
    }
    return prefetched, stats


def find_mirror_spot_pair(
    coin: str,
    mirror_exchanges: Iterable[str],
    pair_selections: Dict[str, Any],
) -> tuple[Optional[str], Optional[str]]:
    """Pick a spot pair on mirror exchanges to fetch strategy signals for an HL coin."""
    target = str(coin or "").upper().strip()
    if not target:
        return None, None
    for exchange_name in mirror_exchanges:
        for pair in pair_selections.get(exchange_name) or []:
            if pair_to_hyperliquid_coin(str(pair)) == target:
                return str(exchange_name), str(pair)
    return None, None


def hyperliquid_shadow_promotion_requirement(
    coin: str,
    signal: Optional[Mapping[str, Any]],
    regime: str,
    hl_cfg: Optional[Dict[str, Any]],
    promoted_cohorts_by_coin: Optional[Mapping[str, List[Mapping[str, Any]]]] = None,
) -> Dict[str, Any]:
    """Require coin-specific shadow proof for configured risky cohorts."""
    promotion_cfg = (hl_cfg or {}).get("shadow_cohort_promotion") or {}
    requirements = promotion_cfg.get("require_promotion_for") or []
    if not isinstance(requirements, list) or not requirements:
        return {"blocked": False, "reason": "shadow_promotion_not_required"}

    strategy = str((signal or {}).get("strategy") or "").strip().lower()
    side = position_sides_from_signal((signal or {}).get("signal"))
    regime_key = str(regime or "").strip().lower()
    if not strategy or side not in {"long", "short"}:
        return {"blocked": False, "reason": "shadow_promotion_no_directional_signal"}

    matched_requirement = False
    for raw in requirements:
        if not isinstance(raw, Mapping):
            continue
        req_strategy = str(raw.get("strategy") or "").strip().lower()
        req_side = str(raw.get("side") or "").strip().lower()
        req_regimes = {
            str(value or "").strip().lower()
            for value in (raw.get("regimes") or [])
            if str(value or "").strip()
        }
        if req_strategy and req_strategy != strategy:
            continue
        if req_side and req_side != side:
            continue
        if req_regimes and regime_key not in req_regimes:
            continue
        matched_requirement = True
        break

    if not matched_requirement:
        return {"blocked": False, "reason": "shadow_promotion_not_required"}

    coin_key = pair_to_hyperliquid_coin(str(coin or ""))
    promoted_map = promoted_cohorts_by_coin or {}
    promoted = promoted_map.get(coin_key) or []
    if not promoted:
        promoted = next(
            (
                cohorts
                for raw_coin, cohorts in promoted_map.items()
                if pair_to_hyperliquid_coin(str(raw_coin or "")).lower()
                == coin_key.lower()
            ),
            [],
        )
    for cohort in promoted:
        if (
            str(cohort.get("strategy") or "").strip().lower() == strategy
            and str(cohort.get("side") or "").strip().lower() == side
            and str(cohort.get("regime") or "").strip().lower() == regime_key
        ):
            return {
                "blocked": False,
                "reason": "shadow_promoted_cohort_match",
                "cohort": dict(cohort),
            }

    return {
        "blocked": True,
        "reason": f"shadow_promotion_required_{strategy}_{side}_{regime_key or 'unknown'}",
        "message": (
            f"{strategy} {side} {regime_key or 'unknown'} requires a profitable "
            f"coin-specific shadow cohort before executable entry"
        ),
    }


def hyperliquid_coin_strategy_entry_deny(
    coin: str,
    signal: Optional[Mapping[str, Any]],
    regime: str,
    hl_cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Hard deny specific coin/strategy/side/regime combinations before entry."""
    denies = (hl_cfg or {}).get("coin_strategy_entry_denies") or []
    if not isinstance(denies, list) or not denies:
        return {"blocked": False, "reason": "coin_strategy_deny_not_configured"}

    strategy = str((signal or {}).get("strategy") or "").strip().lower()
    side = position_sides_from_signal((signal or {}).get("signal"))
    regime_key = str(regime or "").strip().lower()
    coin_key = pair_to_hyperliquid_coin(str(coin or ""))
    if not coin_key or not strategy or side not in {"long", "short"}:
        return {"blocked": False, "reason": "coin_strategy_deny_incomplete_signal"}

    for raw in denies:
        if not isinstance(raw, Mapping):
            continue
        deny_coin = pair_to_hyperliquid_coin(str(raw.get("coin") or ""))
        if deny_coin and deny_coin.lower() != coin_key.lower():
            continue
        deny_strategy = str(raw.get("strategy") or "").strip().lower()
        if deny_strategy and deny_strategy != strategy:
            continue
        deny_sides = {
            str(value or "").strip().lower()
            for value in (raw.get("sides") or [])
            if str(value or "").strip()
        }
        if deny_sides and side not in deny_sides:
            continue
        deny_regimes = {
            str(value or "").strip().lower()
            for value in (raw.get("regimes") or [])
            if str(value or "").strip()
        }
        if deny_regimes and regime_key not in deny_regimes:
            continue
        return {
            "blocked": True,
            "reason": (
                f"coin_strategy_entry_denied_{coin_key}_{strategy}_{side}_"
                f"{regime_key or 'any_regime'}"
            ),
            "message": (
                f"{coin_key} {strategy} {side} "
                f"{regime_key or 'any regime'} is on the executable denylist"
            ),
        }

    return {"blocked": False, "reason": "coin_strategy_deny_clear"}


# ---------------------------------------------------------------------------
# Change 3: Counter-trend regime direction gate
# ---------------------------------------------------------------------------

_COUNTER_TREND_BLOCKS: Dict[str, str] = {
    "trending_up": "short",
    "trending_down": "long",
}

_COUNTER_TREND_OVERRIDE_MIN_CONFIDENCE = 0.90
_COUNTER_TREND_OVERRIDE_MIN_STRENGTH = 0.80
_COUNTER_TREND_OVERRIDE_SIZE_MULTIPLIER = 0.5


def hyperliquid_regime_direction_gate(
    signal_side: str,
    regime: str,
    confidence: float,
    strength: float,
    hl_cfg: Optional[Dict[str, Any]] = None,
    strategy: str = "",
) -> Dict[str, Any]:
    """
    Block entries that go against the dominant trend direction.

    Short entries are blocked in trending_up regimes and long entries in
    trending_down regimes unless the signal has exceptionally high conviction
    (confidence >= 0.90 AND strength >= 0.80), in which case entry is allowed
    at half size.
    """
    side = str(signal_side or "").lower()
    regime_key = str(regime or "").lower()
    strategy_key = str(strategy or "").strip().lower()
    blocked_strategy_sides = (hl_cfg or {}).get("blocked_strategy_sides") or {}
    if isinstance(blocked_strategy_sides, Mapping) and strategy_key:
        configured_sides = {
            str(value or "").strip().lower()
            for value in (blocked_strategy_sides.get(strategy_key) or [])
        }
        if side in configured_sides:
            return {
                "blocked": True,
                "reason": f"configured_strategy_side_block_{strategy_key}_{side}",
                "sizeMultiplier": None,
            }
    strategy_blocks = (hl_cfg or {}).get("strategy_regime_side_blocks") or {}
    if isinstance(strategy_blocks, dict) and strategy_key:
        raw_strategy_cfg = strategy_blocks.get(strategy_key) or strategy_blocks.get(strategy) or {}
        if isinstance(raw_strategy_cfg, dict):
            blocked_sides = {
                str(value or "").strip().lower()
                for value in (raw_strategy_cfg.get(regime_key) or [])
            }
            if side in blocked_sides:
                return {
                    "blocked": True,
                    "reason": f"configured_strategy_regime_side_block_{strategy_key}_{regime_key}_{side}",
                    "sizeMultiplier": None,
                }

    blocked_regime_sides = (hl_cfg or {}).get("blocked_regime_sides") or {}
    configured_blocked_sides = {
        str(value or "").strip().lower()
        for value in (blocked_regime_sides.get(regime_key) or [])
    }
    if (
        side in configured_blocked_sides
        and strategy_key not in adaptive_regime_side_exit_exempt_strategies(hl_cfg)
    ):
        return {
            "blocked": True,
            "reason": f"configured_regime_side_block_{regime_key}_{side}",
            "sizeMultiplier": None,
        }

    allowed_lanes = (hl_cfg or {}).get("counter_trend_allowed_lanes") or []
    if isinstance(allowed_lanes, list) and strategy_key:
        for raw in allowed_lanes:
            if not isinstance(raw, Mapping):
                continue
            lane_strategy = str(raw.get("strategy") or "").strip().lower()
            lane_side = str(raw.get("side") or "").strip().lower()
            lane_regimes = {
                str(value or "").strip().lower()
                for value in (raw.get("regimes") or [])
                if str(value or "").strip()
            }
            if lane_strategy != strategy_key:
                continue
            if lane_side and lane_side != side:
                continue
            if lane_regimes and regime_key not in lane_regimes:
                continue
            return {
                "blocked": False,
                "reason": "counter_trend_allowed_lane",
                "sizeMultiplier": None,
            }

    blocked_side = _COUNTER_TREND_BLOCKS.get(regime_key)

    if blocked_side is None or side != blocked_side:
        return {
            "blocked": False,
            "reason": "regime_direction_ok",
            "sizeMultiplier": None,
        }

    if (
        confidence >= _COUNTER_TREND_OVERRIDE_MIN_CONFIDENCE
        and strength >= _COUNTER_TREND_OVERRIDE_MIN_STRENGTH
    ):
        logger.info(
            "[HL regime gate] counter-trend override: %s %s in %s "
            "(conf=%.2f str=%.2f) — allowed at %.0f%% size",
            side, "entry", regime_key, confidence, strength,
            _COUNTER_TREND_OVERRIDE_SIZE_MULTIPLIER * 100,
        )
        return {
            "blocked": False,
            "reason": "counter_trend_override_high_conviction",
            "sizeMultiplier": _COUNTER_TREND_OVERRIDE_SIZE_MULTIPLIER,
        }

    return {
        "blocked": True,
        "reason": f"counter_trend_blocked_{side}_in_{regime_key}",
        "sizeMultiplier": None,
    }


# ---------------------------------------------------------------------------
# Phase 7 (2026-05-27): PnL-weighted strategy sizing tier
#
# Multiplier applied to the per-trade position size based on the strategy's
# rolling realized PnL across closed paper trades within a lookback window.
# Strategies that lose money are put on probation (smaller size), strategies
# that earn money are full size. This is independent of the standalone gate
# size multipliers — the final size multiplier is min(gate, pnl_tier).
# ---------------------------------------------------------------------------


def _parse_paper_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.replace(tzinfo=None) if dt.tzinfo else dt
    except Exception:
        return None


def hyperliquid_strategy_pnl_multiplier(
    strategy: str,
    closed_trades: Iterable[Dict[str, Any]],
    *,
    lookback_hours: float = 168.0,
    strong_pnl_threshold: float = 5.0,
    normal_pnl_threshold: float = 0.0,
    strong_multiplier: float = 1.0,
    normal_multiplier: float = 0.7,
    probation_multiplier: float = 0.4,
    min_sample: int = 3,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Return a size multiplier tier based on rolling realized PnL.

    Tiers (default):
      lookback PnL >= +$5  → 1.00x  (strong)
      lookback PnL >= 0    → 0.70x  (normal)
      lookback PnL <  0    → 0.40x  (probation)

    Strategies with fewer than ``min_sample`` trades in the lookback window
    are treated as normal (no penalty, no boost). Returns a structured dict
    so callers can log the rationale.
    """
    normalized = str(strategy or "").strip().lower()
    if not normalized:
        return {
            "multiplier": normal_multiplier,
            "tier": "normal",
            "reason": "strategy_unknown",
            "lookback_pnl": 0.0,
            "lookback_trades": 0,
        }

    now_dt = now or datetime.utcnow()
    if now_dt.tzinfo:
        now_dt = now_dt.replace(tzinfo=None)
    cutoff = now_dt - timedelta(hours=max(0.0, float(lookback_hours)))

    total_pnl = 0.0
    sample = 0
    for trade in closed_trades or []:
        if not isinstance(trade, dict):
            continue
        strat = str(
            trade.get("source_strategy") or trade.get("strategy") or ""
        ).strip().lower()
        if strat != normalized:
            continue
        exit_dt = _parse_paper_dt(trade.get("exit_time") or trade.get("updated_at"))
        if exit_dt is None:
            continue
        if exit_dt < cutoff:
            continue
        try:
            total_pnl += float(trade.get("realized_pnl") or 0.0)
        except (TypeError, ValueError):
            continue
        sample += 1

    if sample < max(0, int(min_sample)):
        return {
            "multiplier": normal_multiplier,
            "tier": "normal_unsampled",
            "reason": f"sample_{sample}_lt_min_{min_sample}",
            "lookback_pnl": total_pnl,
            "lookback_trades": sample,
        }

    if total_pnl >= strong_pnl_threshold:
        return {
            "multiplier": strong_multiplier,
            "tier": "strong",
            "reason": f"pnl_{total_pnl:.2f}_gte_{strong_pnl_threshold}",
            "lookback_pnl": total_pnl,
            "lookback_trades": sample,
        }
    if total_pnl >= normal_pnl_threshold:
        return {
            "multiplier": normal_multiplier,
            "tier": "normal",
            "reason": f"pnl_{total_pnl:.2f}_gte_{normal_pnl_threshold}",
            "lookback_pnl": total_pnl,
            "lookback_trades": sample,
        }
    return {
        "multiplier": probation_multiplier,
        "tier": "probation",
        "reason": f"pnl_{total_pnl:.2f}_lt_{normal_pnl_threshold}",
        "lookback_pnl": total_pnl,
        "lookback_trades": sample,
    }


# ---------------------------------------------------------------------------
# Phase 6 (2026-05-27): Fee-aware minimum-edge gate
#
# Round-trip taker fees on Hyperliquid at the paper-engine's default rate
# (0.001 per side -> 0.002 round trip = 0.2%) mean any entry whose expected
# move is less than ~2x fees is structurally negative EV. Observed avg fees
# on the 166-trade sample were $0.43/trade (~0.21% on $200 notional).
#
# This gate computes an expected_move_pct from the signal payload (or a
# conservative proxy from confidence + stop/target) and rejects entries
# where expected move < max(min_edge_pct, fee_round_trip * edge_multiplier).
# ---------------------------------------------------------------------------


def _expected_move_pct_from_signal(signal: Dict[str, Any]) -> Optional[float]:
    """
    Read expected_move_pct from a signal, falling back to TP/SL geometry.

    Priority:
      1. signal.expected_move_pct
      2. signal.details.indicators.expected_move_pct
      3. (take_profit_pct - stop_loss_pct * (1 - confidence)) derived
         from indicators or strategy parameters.

    Decimal forms (0.012) are normalized to percent (1.2).
    Returns None when nothing usable is available.
    """
    if not isinstance(signal, dict):
        return None

    def _normalize(raw: Any) -> Optional[float]:
        """Accept percent OR explicit decimal (<0.1) form. Values in [0.1, 50]
        are treated as already in percent so "0.55" stays as 0.55%."""
        if raw is None:
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        if value <= 0:
            return None
        if value < 0.1:
            value *= 100.0
        return value

    direct = _normalize(signal.get("expected_move_pct"))
    if direct is not None:
        return direct

    indicators = _extract_indicators(signal)
    indicator = _normalize(indicators.get("expected_move_pct"))
    if indicator is not None:
        return indicator

    details = signal.get("details") or {}
    candidates: List[Mapping[str, Any]] = []
    if isinstance(details, dict):
        for key in ("indicators", "parameters"):
            data = details.get(key)
            if isinstance(data, dict):
                candidates.append(data)
        candidates.append(details)
    if not candidates:
        return None

    def _first_float(*keys: str) -> Optional[float]:
        for source in candidates:
            for key in keys:
                if key in source:
                    parsed = _normalize(source.get(key))
                    if parsed is not None:
                        return parsed
        return None

    take_profit_pct = _first_float("take_profit_pct", "tp_pct", "target_pct")
    stop_loss_pct = _first_float("stop_loss_pct", "sl_pct", "stop_pct")
    if take_profit_pct is None or stop_loss_pct is None:
        return None

    try:
        confidence = float(signal.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    expected = take_profit_pct - stop_loss_pct * (1.0 - confidence)
    if expected <= 0:
        return None
    return expected


def hyperliquid_min_edge_gate(
    signal: Dict[str, Any],
    hl_cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Reject entries whose expected move (percent) is too small relative to
    the round-trip fee load. Soft when expected_move_pct can't be derived.
    """
    edge_cfg = ((hl_cfg or {}).get("min_edge_gate") or {})
    enabled = edge_cfg.get("enabled", True)
    if enabled is False or str(enabled).lower() in {"0", "false", "no", "off"}:
        return {
            "blocked": False,
            "reason": "min_edge_disabled",
            "expectedMovePct": None,
        }

    min_edge_pct = _safe_float(edge_cfg.get("min_edge_pct", 0.40), 0.40)
    edge_multiplier = _safe_float(edge_cfg.get("edge_multiplier", 2.0), 2.0)
    fee_rate_per_side = _safe_float(
        (hl_cfg or {}).get("fee_rate_per_side", 0.001), 0.001
    )
    fee_round_trip_pct = fee_rate_per_side * 2.0 * 100.0
    slippage_round_trip_pct = max(
        0.0,
        _safe_float(edge_cfg.get("estimated_round_trip_slippage_pct", 0.0), 0.0),
    )
    total_cost_pct = fee_round_trip_pct + slippage_round_trip_pct
    threshold_pct = max(min_edge_pct, total_cost_pct * edge_multiplier)

    strategy_name = str(signal.get("strategy") or "").strip().lower()
    side = position_sides_from_signal(signal.get("signal"))
    regime = str(signal.get("market_regime") or "").strip().lower()
    evidence_exempt_lanes = edge_cfg.get("evidence_exempt_lanes") or []
    if isinstance(evidence_exempt_lanes, list):
        for raw in evidence_exempt_lanes:
            if not isinstance(raw, Mapping):
                continue
            lane_strategy = str(raw.get("strategy") or "").strip().lower()
            lane_side = str(raw.get("side") or "").strip().lower()
            lane_regimes = {
                str(value or "").strip().lower()
                for value in (raw.get("regimes") or [])
                if str(value or "").strip()
            }
            if lane_strategy and lane_strategy != strategy_name:
                continue
            if lane_side and lane_side != side:
                continue
            if lane_regimes and regime not in lane_regimes:
                continue
            return {
                "blocked": False,
                "reason": "min_edge_evidence_exempt_lane",
                "expectedMovePct": _expected_move_pct_from_signal(signal),
                "thresholdPct": threshold_pct,
                "estimatedCostPct": total_cost_pct,
                "evidenceExempt": True,
            }

    expected = _expected_move_pct_from_signal(signal)
    if expected is None:
        require_expected_move = edge_cfg.get("require_expected_move", False)
        allow_missing = {
            str(value or "").strip().lower()
            for value in (edge_cfg.get("allow_missing_expected_move_strategies") or [])
        }
        if strategy_name in allow_missing:
            return {
                "blocked": False,
                "reason": "min_edge_missing_allowed_for_strategy",
                "expectedMovePct": None,
                "thresholdPct": threshold_pct,
                "estimatedCostPct": total_cost_pct,
            }
        if require_expected_move is True or str(require_expected_move).lower() in {
            "1", "true", "yes", "on"
        }:
            return {
                "blocked": True,
                "reason": "min_edge_blocked_expected_move_missing",
                "expectedMovePct": None,
                "thresholdPct": threshold_pct,
                "estimatedCostPct": total_cost_pct,
            }
        return {
            "blocked": False,
            "reason": "min_edge_no_data",
            "expectedMovePct": None,
            "thresholdPct": threshold_pct,
            "estimatedCostPct": total_cost_pct,
        }

    if expected < threshold_pct:
        return {
            "blocked": True,
            "reason": (
                f"min_edge_blocked_{expected:.2f}pct_lt_{threshold_pct:.2f}pct"
            ),
            "expectedMovePct": expected,
            "thresholdPct": threshold_pct,
            "estimatedCostPct": total_cost_pct,
        }

    return {
        "blocked": False,
        "reason": "min_edge_pass",
        "expectedMovePct": expected,
        "thresholdPct": threshold_pct,
        "estimatedCostPct": total_cost_pct,
    }


# ---------------------------------------------------------------------------
# Phase 5 (2026-05-27): Trend-chase guard
#
# Lifetime PnL by regime × side from the 166-trade sample showed:
#   trending_up   × long  = -$51.59 (44 trades, 63.6% WR) — top-chasing
#   trending_down × short = -$21.53 (20 trades, 45% WR)   — same pattern
#
# The Change 3 counter-trend gate addresses the opposite case (shorts in
# trending_up and longs in trending_down). This new gate keeps *with-trend*
# entries but requires either a pullback context or a non-extended RSI so we
# don't chase tops/bottoms. Strategies that do not expose pullback / RSI
# indicators are passthrough (no behavior change).
# ---------------------------------------------------------------------------


_TREND_CHASE_REGIMES: Dict[str, str] = {
    "trending_up": "long",
    "trending_down": "short",
}


def _extract_indicators(signal: Dict[str, Any]) -> Dict[str, Any]:
    details = (signal or {}).get("details") or {}
    if not isinstance(details, dict):
        return {}
    indicators = details.get("indicators")
    if isinstance(indicators, dict):
        return indicators
    state = details.get("state")
    if isinstance(state, dict):
        nested = state.get("indicators")
        if isinstance(nested, dict):
            return nested
    return {}


_TREND_CHASE_UNPROVEN_SIZE_MULTIPLIER = 0.5


def hyperliquid_trend_chase_gate(
    signal: Dict[str, Any],
    regime: str,
    *,
    min_pullback_pct: float = 0.6,
    long_rsi_max: float = 60.0,
    short_rsi_min: float = 40.0,
) -> Dict[str, Any]:
    """
    Block with-trend entries that are chasing an extended move.

    Activates only when:
      - regime is trending_up and signal side is long, OR
      - regime is trending_down and signal side is short.

    Within an active branch we allow the entry when either:
      - the signal indicators expose a meaningful pullback context
        (`pullback_depth_pct >= min_pullback_pct` -- whether stored as a
        decimal 0.006 or percent 0.6), OR
      - the latest RSI(14) is not in the chase zone (<= 60 for longs,
        >= 40 for shorts).

    When neither indicator is available the gate is permissive (no block).
    This keeps strategies that do not yet emit RSI/pullback fields
    unaffected while letting the strategies that do (pullback_long_scalping,
    vwma_hull, swing_hull_rsi_ema, supertrend) benefit immediately.
    """
    side = normalize_perp_entry_signal((signal or {}).get("signal"))
    regime_key = str(regime or "").lower()
    expected_side = _TREND_CHASE_REGIMES.get(regime_key)

    if expected_side is None or side != expected_side:
        return {
            "blocked": False,
            "reason": "trend_chase_inactive",
            "passthrough": True,
        }

    indicators = _extract_indicators(signal)
    pullback_raw = indicators.get("pullback_depth_pct")
    rsi_raw = indicators.get("rsi_14")
    if rsi_raw is None:
        rsi_raw = indicators.get("rsi")

    pullback_pct = None
    if pullback_raw is not None:
        try:
            pullback_pct = float(pullback_raw)
        except (TypeError, ValueError):
            pullback_pct = None
        else:
            if 0 < pullback_pct < 1:
                pullback_pct *= 100.0

    rsi_value = None
    if rsi_raw is not None:
        try:
            rsi_value = float(rsi_raw)
        except (TypeError, ValueError):
            rsi_value = None

    if pullback_pct is None and rsi_value is None:
        # Phase C (2026-05-29): with-trend entries in an active chase regime that
        # cannot prove a pullback / non-extended RSI are the top lifetime leak
        # (trending_up/long -$51.78). Keep them tradeable but at half size
        # instead of full pass so we stop top/bottom chasing at full notional.
        return {
            "blocked": False,
            "reason": "trend_chase_no_indicators",
            "passthrough": True,
            "sizeMultiplier": _TREND_CHASE_UNPROVEN_SIZE_MULTIPLIER,
        }

    pullback_ok = pullback_pct is not None and pullback_pct >= min_pullback_pct
    if side == "long":
        rsi_ok = rsi_value is not None and rsi_value <= long_rsi_max
    else:
        rsi_ok = rsi_value is not None and rsi_value >= short_rsi_min

    if pullback_ok or rsi_ok:
        return {
            "blocked": False,
            "reason": "trend_chase_pass",
            "passthrough": False,
        }

    return {
        "blocked": True,
        "reason": (
            f"trend_chase_blocked_{side}_in_{regime_key}_"
            f"rsi_{rsi_value if rsi_value is not None else 'na'}_"
            f"pullback_{pullback_pct if pullback_pct is not None else 'na'}"
        ),
        "passthrough": False,
    }


# ---------------------------------------------------------------------------
# Risk-based sizing helpers (used by orchestrator HL entry path)
# ---------------------------------------------------------------------------


def _indicator_sources(signal: Dict[str, Any]) -> List[Mapping[str, Any]]:
    sources: List[Mapping[str, Any]] = []
    if not isinstance(signal, dict):
        return sources
    details = signal.get("details") or {}
    if isinstance(details, dict):
        state = details.get("state") or {}
        if isinstance(state, dict):
            indicators = state.get("indicators") or {}
            if isinstance(indicators, dict):
                sources.append(indicators)
    for key in ("indicators", "state"):
        block = signal.get(key)
        if isinstance(block, dict):
            if key == "state":
                ind = block.get("indicators")
                if isinstance(ind, dict):
                    sources.append(ind)
            else:
                sources.append(block)
    return sources


def _first_pct_from_signal(signal: Dict[str, Any], *keys: str) -> Optional[float]:
    for source in _indicator_sources(signal):
        for key in keys:
            raw = source.get(key)
            if raw is None:
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if value <= 0:
                continue
            if value < 0.1:
                value *= 100.0
            return value
    return None


def stop_distance_pct_from_signal(
    signal: Dict[str, Any],
    hl_cfg: Optional[Dict[str, Any]] = None,
) -> float:
    """Effective stop distance in percent for risk-based sizing."""
    stop = _first_pct_from_signal(signal, "stop_loss_pct", "sl_pct", "stop_pct")
    if stop is not None:
        return stop
    return float((hl_cfg or {}).get("stop_loss_pct", 1.5) or 1.5)


def signal_position_size_multiplier(signal: Dict[str, Any]) -> Optional[float]:
    """Optional per-signal size hint from strategy indicators."""
    for source in _indicator_sources(signal):
        for key in ("position_size_multiplier", "size_multiplier"):
            if key not in source:
                continue
            try:
                value = float(source.get(key))
            except (TypeError, ValueError):
                continue
            if 0.0 < value <= 2.0:
                return value
    return None


def hyperliquid_risk_based_notional(
    account_equity: float,
    stop_distance_pct: float,
    hl_cfg: Optional[Dict[str, Any]] = None,
    *,
    size_multiplier: float = 1.0,
    strategy: str = "",
) -> Optional[float]:
    """
    Risk-% position sizing: notional = (equity × risk%) / (stop_distance / 100).
    Returns None when disabled; caller falls back to fixed caps.
    """
    risk_cfg = ((hl_cfg or {}).get("risk_based_sizing") or {})
    enabled = risk_cfg.get("enabled", False)
    if enabled is False or str(enabled).lower() in {"0", "false", "no", "off"}:
        return None
    if account_equity <= 0 or stop_distance_pct <= 0:
        return None
    mult = max(0.0, min(1.0, float(size_multiplier or 1.0)))
    risk_pct = strategy_risk_per_trade_pct(strategy, hl_cfg) if strategy else _safe_float(
        risk_cfg.get("risk_per_trade_pct", 0.0075),
        0.0075,
    )
    max_cap = strategy_max_notional(strategy, hl_cfg) if strategy else _safe_float(
        (hl_cfg or {}).get("max_notional_per_trade", 200.0),
        200.0,
    )
    risk_usd = account_equity * risk_pct * mult
    notional = risk_usd / (stop_distance_pct / 100.0)
    return min(notional, max_cap * mult)


def hyperliquid_daily_loss_halt(
    closed_trades: List[Dict[str, Any]],
    account_equity: float,
    hl_cfg: Optional[Dict[str, Any]] = None,
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Block new entries when today's realized PnL breaches the daily loss budget."""
    halt_cfg = ((hl_cfg or {}).get("daily_loss_halt") or {})
    enabled = halt_cfg.get("enabled", True)
    if enabled is False or str(enabled).lower() in {"0", "false", "no", "off"}:
        return {"blocked": False, "reason": "daily_loss_halt_disabled"}
    max_pct = _safe_float(
        halt_cfg.get(
            "max_daily_loss_pct",
            ((hl_cfg or {}).get("max_daily_loss_pct", 0.03)),
        ),
        0.03,
    )
    if max_pct <= 0 or account_equity <= 0:
        return {"blocked": False, "reason": "daily_loss_halt_disabled"}
    now_dt = now or datetime.utcnow()
    if now_dt.tzinfo is not None:
        now_dt = now_dt.replace(tzinfo=None)
    today = now_dt.date()
    daily_pnl = 0.0
    for row in closed_trades or []:
        if str(row.get("status") or "").upper() != "CLOSED":
            continue
        ts = _parse_dt(row.get("exit_time")) or _parse_dt(row.get("entry_time"))
        if not ts:
            continue
        if ts.tzinfo is not None:
            ts = ts.replace(tzinfo=None)
        if ts.date() != today:
            continue
        daily_pnl += float(row.get("realized_pnl") or 0.0)
    limit = -account_equity * max_pct
    if daily_pnl <= limit:
        return {
            "blocked": True,
            "reason": "daily_loss_halt",
            "dailyPnl": daily_pnl,
            "limitUsd": limit,
            "maxDailyLossPct": max_pct,
        }
    return {
        "blocked": False,
        "reason": "daily_loss_ok",
        "dailyPnl": daily_pnl,
        "limitUsd": limit,
        "maxDailyLossPct": max_pct,
    }


def hyperliquid_daily_profit_target_halt(
    closed_trades: List[Dict[str, Any]],
    hl_cfg: Optional[Dict[str, Any]] = None,
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Block new entries once today's realized PnL reaches the configured target."""
    target_cfg = ((hl_cfg or {}).get("daily_profit_target") or {})
    enabled = target_cfg.get("enabled", False)
    if enabled is False or str(enabled).lower() in {"0", "false", "no", "off"}:
        return {"blocked": False, "reason": "daily_profit_target_disabled"}
    target_usd = _safe_float(target_cfg.get("target_usd", 0.0), 0.0)
    if target_usd <= 0:
        return {"blocked": False, "reason": "daily_profit_target_disabled"}
    now_dt = now or datetime.utcnow()
    if now_dt.tzinfo is not None:
        now_dt = now_dt.replace(tzinfo=None)
    today = now_dt.date()
    daily_pnl = 0.0
    for row in closed_trades or []:
        if str(row.get("status") or "").upper() != "CLOSED":
            continue
        ts = _parse_dt(row.get("exit_time")) or _parse_dt(row.get("entry_time"))
        if not ts:
            continue
        if ts.tzinfo is not None:
            ts = ts.replace(tzinfo=None)
        if ts.date() != today:
            continue
        daily_pnl += float(row.get("realized_pnl") or 0.0)
    if daily_pnl >= target_usd:
        return {
            "blocked": True,
            "reason": "daily_profit_target",
            "dailyPnl": daily_pnl,
            "targetUsd": target_usd,
        }
    return {
        "blocked": False,
        "reason": "daily_profit_target_not_reached",
        "dailyPnl": daily_pnl,
        "targetUsd": target_usd,
    }


# ---------------------------------------------------------------------------
# Change 5b: Per-coin re-entry cooldown (any exit, not just losses)
# ---------------------------------------------------------------------------


def hyperliquid_reentry_cooldown_check(
    coin: str,
    side: str,
    closed_trades: Iterable[Dict[str, Any]],
    cooldown_minutes: int = 30,
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Block re-entry on the same coin+side within ``cooldown_minutes`` of any
    prior exit.  This is distinct from the 12h post-loss block — it applies
    after profitable exits too, preventing rapid re-entry churn.
    """
    normalized_coin = pair_to_hyperliquid_coin(coin)
    normalized_side = str(side or "").lower()
    if cooldown_minutes <= 0 or not normalized_coin or normalized_side not in {"long", "short"}:
        return {"blocked": False, "reason": "cooldown_disabled"}

    now_dt = now or datetime.utcnow()
    cutoff = now_dt - timedelta(minutes=cooldown_minutes)

    for trade in closed_trades or []:
        trade_coin = pair_to_hyperliquid_coin(
            trade.get("coin") or trade.get("pair") or trade.get("source_pair") or ""
        )
        if trade_coin != normalized_coin:
            continue
        trade_side = str(
            trade.get("position_side") or trade.get("source_signal") or ""
        ).lower()
        if trade_side != normalized_side:
            continue
        exit_time = _parse_dt(trade.get("exit_time") or trade.get("updated_at"))
        if exit_time is None:
            continue
        if exit_time >= cutoff:
            until = exit_time + timedelta(minutes=cooldown_minutes)
            return {
                "blocked": True,
                "reason": (
                    f"reentry_cooldown_{normalized_coin}_{normalized_side}_"
                    f"until_{until.strftime('%H:%M')}"
                ),
                "until": until.isoformat() + "+00:00",
            }

    return {"blocked": False, "reason": "no_recent_exit"}


# ---------------------------------------------------------------------------
# Change 6: Session-aware position sizing
# ---------------------------------------------------------------------------


def _hour_in_windows(utc_hour: int, windows: List[Dict[str, Any]]) -> bool:
    for window in windows or []:
        try:
            start = int(window.get("start_utc", -1))
            end = int(window.get("end_utc", -1))
        except (TypeError, ValueError):
            continue
        if start < 0 or end < 0:
            continue
        if start <= end:
            if start <= utc_hour < end:
                return True
        else:
            if utc_hour >= start or utc_hour < end:
                return True
    return False


def is_caution_window(
    utc_hour: int,
    hl_cfg: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, float]:
    """
    Check if the current UTC hour falls in a configured caution window.

    Returns ``(is_caution, multiplier)``.  When not in a caution window the
    multiplier is 1.0.
    """
    session_cfg = ((hl_cfg or {}).get("session_sizing") or {})
    if not session_cfg.get("enabled", False):
        return False, 1.0

    caution_mult = _safe_float(session_cfg.get("caution_multiplier", 0.5), 0.5)
    windows: List[Dict[str, Any]] = session_cfg.get("caution_windows") or []

    if _hour_in_windows(utc_hour, windows):
        return True, caution_mult

    return False, 1.0


def is_block_window(
    utc_hour: int,
    hl_cfg: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Phase 4 (2026-05-27): hard-skip windows for hours that lifetime PnL shows
    as systematically losing (e.g. 13 UTC US chop, 21 UTC US-close vacuum).

    Block windows are gated by ``session_sizing.block_windows_enabled`` AND
    ``session_sizing.enabled``. Returns True iff utc_hour falls inside any
    configured window. Default off to allow gradual rollout.
    """
    session_cfg = ((hl_cfg or {}).get("session_sizing") or {})
    if not session_cfg.get("enabled", False):
        return False
    if not session_cfg.get("block_windows_enabled", False):
        return False
    windows: List[Dict[str, Any]] = session_cfg.get("block_windows") or []
    return _hour_in_windows(utc_hour, windows)


def is_block_window_strategy_exempt(
    strategy: str,
    hl_cfg: Optional[Dict[str, Any]] = None,
) -> bool:
    """Return True when a strategy is explicitly exempt from hard session blocks."""
    session_cfg = ((hl_cfg or {}).get("session_sizing") or {})
    raw = session_cfg.get("block_window_exempt_strategies") or []
    exempt = {
        str(item).strip().lower()
        for item in raw
        if str(item).strip()
    }
    return str(strategy or "").strip().lower() in exempt


def is_caution_window_strategy_exempt(
    strategy: str,
    hl_cfg: Optional[Dict[str, Any]] = None,
) -> bool:
    """Return True when a strategy skips session caution sizing haircuts."""
    session_cfg = ((hl_cfg or {}).get("session_sizing") or {})
    raw = session_cfg.get("caution_window_exempt_strategies") or []
    exempt = {
        str(item).strip().lower()
        for item in raw
        if str(item).strip()
    }
    return str(strategy or "").strip().lower() in exempt
