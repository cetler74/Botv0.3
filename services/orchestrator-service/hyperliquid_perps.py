"""
Hyperliquid perpetual paper-trading helpers.

This module intentionally has no live-order path. It mirrors existing strategy
signals into isolated paper positions so spot trading remains untouched.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from strategy.hyperliquid.consensus import normalize_perp_entry_signal

logger = logging.getLogger(__name__)


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
}


def pair_to_hyperliquid_coin(pair: str) -> str:
    """Convert BTC/USDC or BTCUSD-style symbols to Hyperliquid perp coin names."""
    raw = str(pair or "").upper().strip()
    if "/" in raw:
        return raw.split("/", 1)[0]
    for suffix in ("USDC", "USDT", "USD"):
        if raw.endswith(suffix):
            return raw[: -len(suffix)]
    return raw


def position_sides_from_signal(signal: str) -> Optional[str]:
    sig = normalize_perp_entry_signal(signal)
    if sig == "long":
        return "long"
    if sig == "short":
        return "short"
    return None


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


def select_mirrored_signal(signals_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
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

    def best_strategy_for(side: str) -> Dict[str, Any]:
        candidates = []
        for name, data in strategies.items():
            if not isinstance(data, dict):
                continue
            if normalize_perp_entry_signal(data.get("signal", "")) != side:
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
    c_signal = normalize_perp_entry_signal(consensus.get("signal", ""))
    if c_signal in {"long", "short"}:
        best = best_strategy_for(c_signal)
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

    candidates = []
    for side in ("long", "short"):
        best = best_strategy_for(side)
        if best:
            candidates.append(best)
    if not candidates:
        return None
    best = sorted(
        candidates,
        key=lambda row: (float(row.get("confidence", 0)), float(row.get("strength", 0))),
        reverse=True,
    )[0]
    best["consensus_confidence"] = float(consensus.get("confidence", 0) or 0)
    best["consensus_agreement"] = float(consensus.get("agreement", 0) or 0)
    opposite = strongest_opposite_for(str(best.get("signal") or ""))
    if opposite:
        best["opposite_strategy"] = opposite.get("strategy")
        best["opposite_confidence"] = float(opposite.get("confidence", 0) or 0)
        best["opposite_strength"] = float(opposite.get("strength", 0) or 0)
    return best


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
    trailing_activation_decimal: float = 0.0075
    trailing_step_decimal: float = 0.0050
    tightened_step_decimal: float = 0.0030
    dynamic_tightening_enabled: bool = True
    tighten_profit_threshold_decimal: float = 0.0150
    breakeven_floor_decimal: float = 0.0050
    min_trigger_distance_decimal: float = 0.0050

    profit_protection_enabled: bool = True
    profit_protection_activation_decimal: float = 0.0050
    fee_rate_per_side: float = 0.001
    profit_protection_fee_buffer: float = 0.0015
    effective_profit_floor_decimal: float = 0.0035

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


@dataclass
class PaperPerpExitResult:
    exit_reason: Optional[str]
    metadata: Dict[str, Any]


def paper_perp_exit_config_from_yaml(
    hl_cfg: Dict[str, Any],
    trading_cfg: Dict[str, Any],
) -> PaperPerpExitConfig:
    """Build exit config from hyperliquid_perps + global trading sections."""
    hl_cfg = hl_cfg or {}
    trading_cfg = trading_cfg or {}
    trailing = trading_cfg.get("trailing_stop") or {}
    pp = trading_cfg.get("profit_protection") or {}

    overall_dec = float(trading_cfg.get("overall_profit_take_exit_pct", 0.045) or 0.0)
    overall_pct = overall_dec * 100.0 if overall_dec > 0 else 0.0

    stop_loss_pct = float(hl_cfg.get("stop_loss_pct", 1.5) or 1.5)
    spot_sl = trading_cfg.get("stop_loss_percentage")
    if spot_sl is not None:
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

    step = _dec("step_percentage", 0.0050)
    tightened = _dec("tightened_step_percentage", step)
    if tightened <= 0:
        tightened = step

    try:
        pp_activation = float(pp.get("activation_threshold", 0.0050) or 0.0050)
    except (TypeError, ValueError):
        pp_activation = 0.0050

    try:
        fee_rate_per_side = float(hl_cfg.get("fee_rate_per_side", 0.001) or 0.001)
    except (TypeError, ValueError):
        fee_rate_per_side = 0.001
    try:
        fee_buffer = float(hl_cfg.get("profit_protection_fee_buffer", 0.0015) or 0.0015)
    except (TypeError, ValueError):
        fee_buffer = 0.0015
    fee_floor = max(0.0, (fee_rate_per_side * 2.0) + fee_buffer)
    breakeven_floor = max(_dec("breakeven_floor_percentage", 0.0050), fee_floor)
    min_trigger_distance = max(_dec("min_trigger_distance_percentage", 0.0050), fee_floor)
    trailing_activation = max(_dec("activation_threshold", 0.0075), fee_floor)
    pp_activation = max(pp_activation, fee_floor)

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

    return PaperPerpExitConfig(
        use_spot_exit_rules=use_spot,
        fixed_stop_loss_enabled=bool(hl_cfg.get("fixed_stop_loss_enabled", True)),
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct if not use_spot else 0.0,
        max_holding_minutes=int(hl_cfg.get("max_holding_minutes", 240) or 240),
        max_holding_minutes_hard=max_hold_hard,
        overall_take_profit_pct=overall_pct,
        trailing_enabled=bool(trailing.get("enabled", True)),
        trailing_activation_decimal=trailing_activation,
        trailing_step_decimal=step,
        tightened_step_decimal=tightened,
        dynamic_tightening_enabled=bool(trailing.get("dynamic_tightening_enabled", True)),
        tighten_profit_threshold_decimal=_dec("tighten_profit_threshold", 0.0150),
        breakeven_floor_decimal=breakeven_floor,
        min_trigger_distance_decimal=min_trigger_distance,
        profit_protection_enabled=bool(pp.get("enabled", True)),
        profit_protection_activation_decimal=pp_activation,
        fee_rate_per_side=fee_rate_per_side,
        profit_protection_fee_buffer=fee_buffer,
        effective_profit_floor_decimal=fee_floor,
        stop_loss_atr_enabled=atr_enabled,
        stop_loss_atr_mult=atr_mult,
        stop_loss_atr_min_pct=atr_min_pct,
        stop_loss_atr_max_pct=atr_max_pct,
        per_coin_stop_overrides=overrides,
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
    realized_block_hours: float = 12.0,
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
                "entryBlockReason": "recent_negative_realized_12h",
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


def hyperliquid_strategy_side_entry_block(
    strategy: str,
    side: str,
    closed_trades: Iterable[Dict[str, Any]],
    *,
    now: Optional[datetime] = None,
    realized_block_hours: float = 12.0,
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


def _peak_pct(side: str, entry_price: float, extreme_price: float) -> float:
    if entry_price <= 0 or extreme_price <= 0:
        return 0.0
    if side == "short":
        return ((entry_price - extreme_price) / entry_price) * 100.0
    return ((extreme_price - entry_price) / entry_price) * 100.0


def _update_extreme_price(
    side: str, entry_price: float, current_price: float, metadata: Dict[str, Any]
) -> float:
    if side == "short":
        key = "lowest_price"
        extreme = float(metadata.get(key) or entry_price)
        if current_price < extreme:
            extreme = current_price
    else:
        key = "highest_price"
        extreme = float(metadata.get(key) or entry_price)
        if current_price > extreme:
            extreme = current_price
    metadata[key] = extreme
    return extreme


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


def _effective_stop_pct(
    trade: Dict[str, Any],
    cfg: PaperPerpExitConfig,
) -> float:
    """Resolve the effective stop loss percentage for this trade.

    Priority: per-coin override > ATR-derived > fixed cfg.stop_loss_pct.
    """
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

    effective_stop_pct = _effective_stop_pct(trade, cfg)
    if (
        cfg.fixed_stop_loss_enabled
        and effective_stop_pct > 0
        and pct <= -abs(effective_stop_pct)
    ):
        return PaperPerpExitResult("paper_stop_loss", metadata)

    holding_exit, _ = _max_holding_decision(trade, pct, cfg, metadata, now=now)
    if holding_exit:
        return PaperPerpExitResult(holding_exit, metadata)

    if not cfg.use_spot_exit_rules:
        if cfg.take_profit_pct > 0 and pct >= cfg.take_profit_pct:
            return PaperPerpExitResult("paper_take_profit", metadata)
        return PaperPerpExitResult(None, metadata)

    if cfg.overall_take_profit_pct > 0 and pct >= cfg.overall_take_profit_pct:
        return PaperPerpExitResult(
            f"paper_overall_take_profit_{cfg.overall_take_profit_pct:.2f}%@{pct:.2f}%",
            metadata,
        )

    trail_active = str(metadata.get("trail_stop") or "").lower() == "active"
    pp_status = metadata.get("profit_protection")
    trailing_activation_pct = cfg.trailing_activation_decimal * 100.0
    pp_activation_pct = cfg.profit_protection_activation_decimal * 100.0
    profit_guarantee_pct = cfg.breakeven_floor_decimal * 100.0

    if (
        cfg.profit_protection_enabled
        and peak_pct >= pp_activation_pct
        and not pp_status
        and not trail_active
    ):
        if side == "short":
            trigger_px = entry_price * (1.0 - cfg.breakeven_floor_decimal)
        else:
            trigger_px = entry_price * (1.0 + cfg.breakeven_floor_decimal)
        metadata["trail_stop_trigger"] = trigger_px
        metadata["profit_protection"] = "profit_guaranteed"
        metadata["profit_protection_trigger"] = pct

    if (
        cfg.profit_protection_enabled
        and metadata.get("profit_protection") == "profit_guaranteed"
        and not trail_active
    ):
        pp_trigger = float(metadata.get("trail_stop_trigger") or 0.0)
        if pp_trigger > 0:
            if side == "long" and current_price > entry_price and current_price <= pp_trigger:
                return PaperPerpExitResult(
                    f"paper_profit_protection_breach@{pct:.2f}%",
                    metadata,
                )
            if side == "short" and current_price < entry_price and current_price >= pp_trigger:
                return PaperPerpExitResult(
                    f"paper_profit_protection_breach@{pct:.2f}%",
                    metadata,
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
                    )
                if side == "short" and current_price >= trigger_px:
                    return PaperPerpExitResult(
                        f"paper_trailing_stop_trigger_${trigger_px:.4f}@{pct:.2f}%",
                        metadata,
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
    threshold_pct = max(min_edge_pct, fee_round_trip_pct * edge_multiplier)

    expected = _expected_move_pct_from_signal(signal)
    if expected is None:
        return {
            "blocked": False,
            "reason": "min_edge_no_data",
            "expectedMovePct": None,
            "thresholdPct": threshold_pct,
        }

    if expected < threshold_pct:
        return {
            "blocked": True,
            "reason": (
                f"min_edge_blocked_{expected:.2f}pct_lt_{threshold_pct:.2f}pct"
            ),
            "expectedMovePct": expected,
            "thresholdPct": threshold_pct,
        }

    return {
        "blocked": False,
        "reason": "min_edge_pass",
        "expectedMovePct": expected,
        "thresholdPct": threshold_pct,
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
        return {
            "blocked": False,
            "reason": "trend_chase_no_indicators",
            "passthrough": True,
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
