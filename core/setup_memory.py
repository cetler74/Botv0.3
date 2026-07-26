"""Shared setup-memory evaluation for spot and perpetual entries."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

try:
    from core.perp_paper_pnl_report import exit_bucket
    from core.shadow_episode_summary import independent_closed_episode_rows
    from core.strategy_trade_evidence import (
        build_strategy_entry_evidence,
        normalized_closed_trade_evidence,
    )
except ImportError:  # pragma: no cover - service-local imports
    from perp_paper_pnl_report import exit_bucket
    from shadow_episode_summary import independent_closed_episode_rows
    from strategy_trade_evidence import (
        build_strategy_entry_evidence,
        normalized_closed_trade_evidence,
    )


SETUP_MEMORY_DEFAULTS: Dict[str, Any] = {
    "enabled": False,
    "mode": "advisory",
    "include_shadow": True,
    "lookback_days": 90,
    "min_real_samples": 5,
    "min_shadow_episodes": 10,
    "recent_loss_cooldown_hours": 4,
    "exact_setup_loss_streak_block": 2,
    "block_expectancy_below": 0.0,
    "block_profit_factor_below": 0.8,
    "caution_profit_factor_below": 1.1,
    "caution_win_rate_below": 0.45,
    "block_all_loss_cohorts": False,
    "broad_all_loss_min_samples": 0,
    "block_negative_expectancy_broad": False,
    "size_down_multiplier": 0.5,
    "closed_trade_fetch_limit": 2000,
    "permanent": {
        "enabled": True,
        "exact_loss_streak": 3,
        "exact_win_streak": 3,
        "coin_regime_loss_streak": 4,
        "coin_regime_win_streak": 4,
        "size_up_multiplier": 1.25,
        "include_shadow": True,
    },
}

PERMANENT_DEFAULTS: Dict[str, Any] = {
    "enabled": True,
    "exact_loss_streak": 3,
    "exact_win_streak": 3,
    "coin_regime_loss_streak": 4,
    "coin_regime_win_streak": 4,
    "size_up_multiplier": 1.25,
    "include_shadow": True,
}


@dataclass(frozen=True)
class SetupMemoryDecision:
    action: str
    reason: str
    enabled: bool
    mode: str
    market_type: str
    setup_fingerprint: str
    size_multiplier: float = 1.0
    matched_count: int = 0
    loss_count: int = 0
    win_count: int = 0
    win_rate: Optional[float] = None
    expectancy: Optional[float] = None
    profit_factor: Optional[float] = None
    latest_loss_at: Optional[str] = None
    match_level: str = "none"
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    permanent: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "reason": self.reason,
            "enabled": self.enabled,
            "mode": self.mode,
            "marketType": self.market_type,
            "setupFingerprint": self.setup_fingerprint,
            "sizeMultiplier": self.size_multiplier,
            "matchedCount": self.matched_count,
            "lossCount": self.loss_count,
            "winCount": self.win_count,
            "winRate": self.win_rate,
            "expectancy": self.expectancy,
            "profitFactor": self.profit_factor,
            "latestLossAt": self.latest_loss_at,
            "matchLevel": self.match_level,
            "evidence": self.evidence,
            "permanent": self.permanent,
        }


def setup_memory_config(
    root_config: Optional[Mapping[str, Any]],
    market_type: str,
) -> Dict[str, Any]:
    """Merge global setup-memory config with optional per-market overrides."""
    trading_cfg = (root_config or {}).get("trading") if isinstance(root_config, Mapping) else {}
    if not isinstance(trading_cfg, Mapping):
        trading_cfg = {}
    raw = trading_cfg.get("setup_memory") or {}
    if not isinstance(raw, Mapping):
        raw = {}
    cfg = dict(SETUP_MEMORY_DEFAULTS)
    cfg["permanent"] = dict(PERMANENT_DEFAULTS)
    for key, value in raw.items():
        if key not in {"spot", "perps"}:
            if key == "permanent" and isinstance(value, Mapping):
                merged_perm = dict(PERMANENT_DEFAULTS)
                merged_perm.update(dict(value))
                cfg["permanent"] = merged_perm
            else:
                cfg[key] = value
    market_key = "perps" if str(market_type).lower() in {"perp", "perps"} else "spot"
    override = raw.get(market_key) or {}
    if isinstance(override, Mapping):
        for key, value in override.items():
            if key == "permanent" and isinstance(value, Mapping):
                merged_perm = dict(cfg.get("permanent") or PERMANENT_DEFAULTS)
                merged_perm.update(dict(value))
                cfg["permanent"] = merged_perm
            else:
                cfg[key] = value
    return cfg


def permanent_memory_config(cfg: Mapping[str, Any]) -> Dict[str, Any]:
    raw = cfg.get("permanent") if isinstance(cfg, Mapping) else None
    out = dict(PERMANENT_DEFAULTS)
    if isinstance(raw, Mapping):
        out.update(dict(raw))
    return out


def _enabled(value: Any) -> bool:
    return value is True or str(value).strip().lower() in {"1", "true", "yes", "on"}


def _parse_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value).strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed):
        return default
    return parsed


def _side_from_signal(signal: Mapping[str, Any]) -> str:
    raw = str(signal.get("signal") or signal.get("side") or signal.get("position_side") or "").lower()
    if raw in {"buy", "long"}:
        return "long"
    if raw in {"sell", "short"}:
        return "short"
    return raw or "unknown"


def _coin_from_signal(signal: Mapping[str, Any], market_type: str) -> str:
    for key in ("coin", "pair", "source_pair", "symbol"):
        value = signal.get(key)
        if value:
            text = str(value).strip()
            if market_type == "perps":
                return text.split("/", 1)[0].upper()
            return text
    return "unknown"


def _regime_from_signal(signal: Mapping[str, Any], market_regime: str = "") -> str:
    if market_regime:
        return str(market_regime).strip().lower()
    details = signal.get("details") if isinstance(signal.get("details"), Mapping) else {}
    state = details.get("state") if isinstance(details.get("state"), Mapping) else {}
    indicators = state.get("indicators") if isinstance(state.get("indicators"), Mapping) else {}
    return str(
        signal.get("market_regime")
        or signal.get("stable_regime")
        or indicators.get("market_regime")
        or "unknown"
    ).strip().lower()


def _fingerprint(identity: Mapping[str, Any]) -> str:
    parts = [
        identity.get("marketType"),
        identity.get("strategyKey"),
        identity.get("strategyVersion"),
        identity.get("configHash"),
        identity.get("side"),
        identity.get("coin"),
        identity.get("regime"),
        identity.get("why"),
    ]
    return "|".join(str(part or "unknown") for part in parts)


def setup_identity_from_signal(
    signal: Mapping[str, Any],
    *,
    market_type: str,
    strategy_config: Optional[Mapping[str, Any]] = None,
    market_regime: str = "",
) -> Dict[str, Any]:
    evidence = signal.get("entry_evidence")
    if not isinstance(evidence, Mapping):
        evidence = build_strategy_entry_evidence(signal, strategy_config=strategy_config)
    rationale = evidence.get("rationale") if isinstance(evidence.get("rationale"), Mapping) else {}
    identity = {
        "marketType": "perps" if str(market_type).lower() in {"perp", "perps"} else "spot",
        "strategyKey": str(evidence.get("strategy_key") or signal.get("strategy") or "unknown").lower(),
        "strategyVersion": str(evidence.get("strategy_version") or "unversioned"),
        "configHash": str(evidence.get("strategy_config_hash") or "unknown"),
        "timeframeBundle": evidence.get("timeframe_bundle") or "unknown",
        "side": _side_from_signal(signal),
        "coin": _coin_from_signal(signal, market_type),
        "regime": _regime_from_signal(
            signal,
            market_regime or str(evidence.get("market_regime") or ""),
        ),
        "why": str(rationale.get("why") or signal.get("reason") or "unknown"),
    }
    identity["setupFingerprint"] = _fingerprint(identity)
    return identity


def _spot_exit_bucket(trade: Mapping[str, Any]) -> str:
    reason = str(trade.get("exit_reason") or "").lower()
    if "stop" in reason or "loss" in reason:
        return "stop_loss"
    if "profit" in reason or "target" in reason:
        return "take_profit"
    if "trail" in reason:
        return "trailing_stop"
    return "other"


def normalize_memory_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    market_type: str,
) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for row in rows or []:
        if str(row.get("status") or "").upper() != "CLOSED":
            continue
        if market_type == "perps" and not (row.get("coin") or row.get("position_side")):
            continue
        bucket = (
            exit_bucket(str(row.get("exit_reason") or ""))
            if market_type == "perps"
            else _spot_exit_bucket(row)
        )
        try:
            item = normalized_closed_trade_evidence(row, market_type=market_type, exit_bucket_value=bucket)
        except Exception:
            continue
        if market_type == "spot" and str(item.get("side") or "").lower() == "unknown":
            item["side"] = "long"
        item["tradeId"] = str(row.get("trade_id") or row.get("id") or "")
        item["source"] = "shadow" if _is_shadow_row(row) else "real"
        normalized.append(item)
    return normalized


def _is_shadow_row(row: Mapping[str, Any]) -> bool:
    meta = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
    raw = meta.get("shadow_trade") if isinstance(meta, Mapping) else None
    return raw is True or str(raw).strip().lower() in {"1", "true", "yes"}


def _row_match_level(row: Mapping[str, Any], identity: Mapping[str, Any]) -> str:
    if str(row.get("side") or "").lower() != str(identity.get("side") or "").lower():
        return "none"
    if str(row.get("strategyKey") or "").lower() != str(identity.get("strategyKey") or "").lower():
        return "none"
    same_version = str(row.get("strategyVersion") or "") == str(identity.get("strategyVersion") or "")
    same_config = str(row.get("configHash") or "") == str(identity.get("configHash") or "")
    same_coin = str(row.get("coin") or "").upper() == str(identity.get("coin") or "").upper()
    same_regime = str(row.get("regime") or "").lower() == str(identity.get("regime") or "").lower()
    same_why = str(row.get("why") or "") == str(identity.get("why") or "")
    if same_version and same_config and same_coin and same_regime and same_why:
        return "exact"
    if same_coin and same_regime:
        return "strategy_side_coin_regime"
    if same_regime:
        return "strategy_side_regime"
    return "strategy_side"


def _stats(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    pnl_values = [_safe_float(row.get("realizedPnl")) for row in rows]
    wins = [v for v in pnl_values if v > 0]
    losses = [v for v in pnl_values if v < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    latest_loss_at = None
    for row in rows:
        if _safe_float(row.get("realizedPnl")) >= 0:
            continue
        ts = _parse_dt(row.get("windowTimestamp"))
        if ts and (latest_loss_at is None or ts > latest_loss_at):
            latest_loss_at = ts
    return {
        "count": len(rows),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (len(wins) / len(rows)) if rows else None,
        "expectancy": (sum(pnl_values) / len(rows)) if rows else None,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else (None if gross_win <= 0 else float("inf")),
        "latest_loss_at": latest_loss_at,
    }


def _loss_streak(rows: Sequence[Mapping[str, Any]]) -> int:
    ordered = sorted(
        rows,
        key=lambda row: _parse_dt(row.get("windowTimestamp")) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    streak = 0
    for row in ordered:
        pnl = _safe_float(row.get("realizedPnl"))
        if pnl < 0:
            streak += 1
            continue
        break
    return streak


def _win_streak(rows: Sequence[Mapping[str, Any]]) -> int:
    ordered = sorted(
        rows,
        key=lambda row: _parse_dt(row.get("windowTimestamp")) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    streak = 0
    for row in ordered:
        pnl = _safe_float(row.get("realizedPnl"))
        if pnl > 0:
            streak += 1
            continue
        break
    return streak


def _ordered_rows(rows: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        [dict(row) for row in rows],
        key=lambda row: _parse_dt(row.get("windowTimestamp")) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )


def _exact_group_key(row: Mapping[str, Any], market_type: str) -> Tuple[str, ...]:
    return (
        market_type,
        str(row.get("strategyKey") or "").lower(),
        str(row.get("strategyVersion") or ""),
        str(row.get("configHash") or ""),
        str(row.get("side") or "").lower(),
        str(row.get("coin") or "").upper(),
        str(row.get("regime") or "").lower(),
        str(row.get("why") or ""),
    )


def _coin_regime_group_key(row: Mapping[str, Any], market_type: str) -> Tuple[str, ...]:
    return (
        market_type,
        str(row.get("strategyKey") or "").lower(),
        str(row.get("side") or "").lower(),
        str(row.get("coin") or "").upper(),
        str(row.get("regime") or "").lower(),
    )


def _fingerprint_from_parts(
    *,
    market_type: str,
    strategy_key: str,
    strategy_version: str,
    config_hash: str,
    side: str,
    coin: str,
    regime: str,
    why: str,
) -> str:
    return _fingerprint(
        {
            "marketType": market_type,
            "strategyKey": strategy_key,
            "strategyVersion": strategy_version,
            "configHash": config_hash,
            "side": side,
            "coin": coin,
            "regime": regime,
            "why": why,
        }
    )


def _cohort_fingerprint(match_level: str, row: Mapping[str, Any], market_type: str) -> str:
    strategy_key = str(row.get("strategyKey") or "unknown").lower()
    side = str(row.get("side") or "unknown").lower()
    coin = str(row.get("coin") or "unknown").upper()
    regime = str(row.get("regime") or "unknown").lower()
    if match_level == "exact":
        return _fingerprint_from_parts(
            market_type=market_type,
            strategy_key=strategy_key,
            strategy_version=str(row.get("strategyVersion") or "unversioned"),
            config_hash=str(row.get("configHash") or "unknown"),
            side=side,
            coin=coin,
            regime=regime,
            why=str(row.get("why") or "unknown"),
        )
    # Broader permanent key intentionally omits version/hash/why; those are
    # stored on the record for supersede checks against the live signal.
    return "|".join(
        [
            market_type,
            strategy_key,
            side,
            coin,
            regime,
            "cohort",
        ]
    )


def _evidence_slice(rows: Sequence[Mapping[str, Any]], limit: int = 20) -> List[Dict[str, Any]]:
    return [
        {
            "tradeId": row.get("tradeId"),
            "source": row.get("source"),
            "realizedPnl": row.get("realizedPnl"),
            "windowTimestamp": row.get("windowTimestamp"),
            "exitBucket": row.get("exitBucket"),
            "why": row.get("why"),
        }
        for row in rows[:limit]
    ]


def _source_mix(rows: Sequence[Mapping[str, Any]]) -> str:
    sources = {str(row.get("source") or "real") for row in rows}
    if sources == {"real"}:
        return "real"
    if sources == {"shadow"}:
        return "shadow"
    return "mixed"


def _permanent_record(
    *,
    fingerprint: str,
    match_level: str,
    outcome: str,
    market_type: str,
    rows: Sequence[Mapping[str, Any]],
    streak_count: int,
) -> Dict[str, Any]:
    head = rows[0] if rows else {}
    return {
        "fingerprint": fingerprint,
        "matchLevel": match_level,
        "outcome": outcome,
        "marketType": market_type,
        "strategyKey": str(head.get("strategyKey") or "unknown").lower(),
        "strategyVersion": str(head.get("strategyVersion") or "unversioned"),
        "configHash": str(head.get("configHash") or "unknown"),
        "side": str(head.get("side") or "unknown").lower(),
        "coin": str(head.get("coin") or "unknown").upper()
        if market_type == "perps"
        else str(head.get("coin") or "unknown"),
        "regime": str(head.get("regime") or "unknown").lower(),
        "why": str(head.get("why") or "unknown"),
        "streakCount": int(streak_count),
        "sourceMix": _source_mix(rows),
        "evidence": _evidence_slice(rows),
        "status": "active",
    }


def detect_permanent_setup_outcomes(
    *,
    market_type: str,
    real_closed_trades: Iterable[Mapping[str, Any]] = (),
    shadow_closed_trades: Iterable[Mapping[str, Any]] = (),
    config: Optional[Mapping[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Scan closed real + shadow history for permanent win/loss streaks."""
    cfg = setup_memory_config(config, market_type)
    perm = permanent_memory_config(cfg)
    if not _enabled(cfg.get("enabled")) or not _enabled(perm.get("enabled")):
        return []

    market = "perps" if str(market_type).lower() in {"perp", "perps"} else "spot"
    real_rows = normalize_memory_rows(real_closed_trades, market_type=market)
    include_shadow = _enabled(perm.get("include_shadow")) and _enabled(cfg.get("include_shadow"))
    shadow_rows = (
        normalize_memory_rows(
            independent_closed_episode_rows(list(shadow_closed_trades or [])),
            market_type=market,
        )
        if include_shadow
        else []
    )
    all_rows = real_rows + shadow_rows
    if not all_rows:
        return []

    exact_groups: Dict[Tuple[str, ...], List[Dict[str, Any]]] = {}
    cohort_groups: Dict[Tuple[str, ...], List[Dict[str, Any]]] = {}
    for row in all_rows:
        exact_groups.setdefault(_exact_group_key(row, market), []).append(row)
        cohort_groups.setdefault(_coin_regime_group_key(row, market), []).append(row)

    detected: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

    def _consider(match_level: str, rows: Sequence[Mapping[str, Any]], loss_need: int, win_need: int) -> None:
        ordered = _ordered_rows(rows)
        if not ordered:
            return
        real_count = sum(1 for row in ordered if row.get("source") == "real")
        shadow_count = sum(1 for row in ordered if row.get("source") == "shadow")
        # Shadow-only cohorts still need a minimum episode floor.
        if real_count == 0 and shadow_count < int(cfg.get("min_shadow_episodes") or 0):
            return
        loss_streak = _loss_streak(ordered)
        win_streak = _win_streak(ordered)
        fingerprint = _cohort_fingerprint(match_level, ordered[0], market)
        if loss_need > 0 and loss_streak >= loss_need:
            key = (fingerprint, "block", match_level)
            detected[key] = _permanent_record(
                fingerprint=fingerprint,
                match_level=match_level,
                outcome="block",
                market_type=market,
                rows=ordered,
                streak_count=loss_streak,
            )
        if win_need > 0 and win_streak >= win_need:
            key = (fingerprint, "promote", match_level)
            # Block beats promote for the same fingerprint+level when both fire.
            if (fingerprint, "block", match_level) in detected:
                return
            detected[key] = _permanent_record(
                fingerprint=fingerprint,
                match_level=match_level,
                outcome="promote",
                market_type=market,
                rows=ordered,
                streak_count=win_streak,
            )

    for rows in exact_groups.values():
        _consider(
            "exact",
            rows,
            int(perm.get("exact_loss_streak") or 0),
            int(perm.get("exact_win_streak") or 0),
        )
    for rows in cohort_groups.values():
        _consider(
            "strategy_side_coin_regime",
            rows,
            int(perm.get("coin_regime_loss_streak") or 0),
            int(perm.get("coin_regime_win_streak") or 0),
        )
    return list(detected.values())


def _permanent_record_matches_identity(
    record: Mapping[str, Any],
    identity: Mapping[str, Any],
) -> bool:
    if str(record.get("status") or "active").lower() not in {"active", ""}:
        return False
    if str(record.get("marketType") or record.get("market_type") or "").lower() != str(
        identity.get("marketType") or ""
    ).lower():
        return False
    if str(record.get("strategyKey") or record.get("strategy_key") or "").lower() != str(
        identity.get("strategyKey") or ""
    ).lower():
        return False
    if str(record.get("side") or "").lower() != str(identity.get("side") or "").lower():
        return False
    record_coin = str(record.get("coin") or "")
    identity_coin = str(identity.get("coin") or "")
    if identity.get("marketType") == "perps":
        if record_coin.upper() != identity_coin.upper():
            return False
    elif record_coin != identity_coin:
        return False

    match_level = str(record.get("matchLevel") or record.get("match_level") or "")
    # Version/hash mismatch invalidates the permanent record for live signals.
    if str(record.get("strategyVersion") or record.get("strategy_version") or "") != str(
        identity.get("strategyVersion") or ""
    ):
        return False
    if str(record.get("configHash") or record.get("config_hash") or "") != str(
        identity.get("configHash") or ""
    ):
        return False

    if match_level == "exact":
        return str(record.get("fingerprint") or "") == str(identity.get("setupFingerprint") or "")
    if match_level == "strategy_side_coin_regime":
        return str(record.get("regime") or "").lower() == str(identity.get("regime") or "").lower()
    return False


def match_permanent_setup_memory(
    identity: Mapping[str, Any],
    permanent_records: Iterable[Mapping[str, Any]],
    *,
    size_up_multiplier: float = 1.25,
) -> Optional[SetupMemoryDecision]:
    """Return a permanent block/promote decision if an active record matches."""
    matched: List[Mapping[str, Any]] = [
        record
        for record in (permanent_records or [])
        if _permanent_record_matches_identity(record, identity)
    ]
    if not matched:
        return None

    blocks = [r for r in matched if str(r.get("outcome") or "").lower() == "block"]
    promotes = [r for r in matched if str(r.get("outcome") or "").lower() == "promote"]
    chosen = None
    action = "allow"
    if blocks:
        # Prefer exact blocks over broader cohort blocks.
        blocks_sorted = sorted(
            blocks,
            key=lambda r: 0 if str(r.get("matchLevel") or r.get("match_level")) == "exact" else 1,
        )
        chosen = blocks_sorted[0]
        action = "block"
    elif promotes:
        promotes_sorted = sorted(
            promotes,
            key=lambda r: 0 if str(r.get("matchLevel") or r.get("match_level")) == "exact" else 1,
        )
        chosen = promotes_sorted[0]
        action = "size_up"
    if chosen is None:
        return None

    match_level = str(chosen.get("matchLevel") or chosen.get("match_level") or "exact")
    streak = int(chosen.get("streakCount") or chosen.get("streak_count") or 0)
    reason = (
        f"permanent {match_level} setup {chosen.get('outcome')} "
        f"(streak={streak})"
    )
    size_multiplier = 1.0
    if action == "size_up":
        size_multiplier = max(1.0, _safe_float(size_up_multiplier, 1.25))
    evidence = chosen.get("evidence") if isinstance(chosen.get("evidence"), list) else []
    return SetupMemoryDecision(
        action=action,
        reason=reason,
        enabled=True,
        mode="blocking",
        market_type=str(identity.get("marketType") or "perps"),
        setup_fingerprint=str(identity.get("setupFingerprint") or ""),
        size_multiplier=size_multiplier,
        matched_count=streak,
        loss_count=streak if action == "block" else 0,
        win_count=streak if action == "size_up" else 0,
        match_level=match_level,
        evidence=list(evidence)[:20],
        permanent=True,
    )


def evaluate_setup_memory(
    signal: Mapping[str, Any],
    *,
    market_type: str,
    real_closed_trades: Iterable[Mapping[str, Any]] = (),
    shadow_closed_trades: Iterable[Mapping[str, Any]] = (),
    config: Optional[Mapping[str, Any]] = None,
    strategy_config: Optional[Mapping[str, Any]] = None,
    market_regime: str = "",
    now: Optional[datetime] = None,
    history_available: bool = True,
    permanent_records: Iterable[Mapping[str, Any]] = (),
) -> SetupMemoryDecision:
    cfg = setup_memory_config(config, market_type)
    mode = str(cfg.get("mode") or "advisory").strip().lower()
    identity = setup_identity_from_signal(
        signal,
        market_type=market_type,
        strategy_config=strategy_config,
        market_regime=market_regime,
    )
    if not _enabled(cfg.get("enabled")):
        return SetupMemoryDecision(
            action="allow",
            reason="setup memory disabled",
            enabled=False,
            mode=mode,
            market_type=identity["marketType"],
            setup_fingerprint=identity["setupFingerprint"],
        )

    perm_cfg = permanent_memory_config(cfg)
    if _enabled(perm_cfg.get("enabled")):
        permanent_decision = match_permanent_setup_memory(
            identity,
            permanent_records,
            size_up_multiplier=_safe_float(perm_cfg.get("size_up_multiplier"), 1.25),
        )
        if permanent_decision is not None:
            action = permanent_decision.action
            reason = permanent_decision.reason
            effective_action = action
            if mode == "advisory" and action == "block":
                effective_action = "allow"
                reason = f"advisory: would block; {reason}"
            elif mode == "size_down" and action == "block":
                effective_action = "size_down"
                reason = f"size_down mode: would block; {reason}"
            size_multiplier = permanent_decision.size_multiplier
            if effective_action == "size_down":
                size_multiplier = max(
                    0.0, min(1.0, _safe_float(cfg.get("size_down_multiplier"), 0.5))
                )
            elif effective_action == "size_up":
                size_multiplier = max(
                    1.0, _safe_float(perm_cfg.get("size_up_multiplier"), 1.25)
                )
            elif effective_action == "allow":
                size_multiplier = 1.0
            return SetupMemoryDecision(
                action=effective_action,
                reason=reason,
                enabled=True,
                mode=mode,
                market_type=identity["marketType"],
                setup_fingerprint=identity["setupFingerprint"],
                size_multiplier=size_multiplier,
                matched_count=permanent_decision.matched_count,
                loss_count=permanent_decision.loss_count,
                win_count=permanent_decision.win_count,
                match_level=permanent_decision.match_level,
                evidence=permanent_decision.evidence,
                permanent=True,
            )

    if not history_available:
        action = "block" if mode == "blocking" else "allow"
        reason = (
            "setup memory history unavailable; blocking entry"
            if action == "block"
            else "setup memory history unavailable; enforcement disabled outside blocking mode"
        )
        return SetupMemoryDecision(
            action=action,
            reason=reason,
            enabled=True,
            mode=mode,
            market_type=identity["marketType"],
            setup_fingerprint=identity["setupFingerprint"],
        )

    now_dt = now or datetime.now(timezone.utc)
    lookback_days = max(0.0, _safe_float(cfg.get("lookback_days"), 90.0))
    cutoff = now_dt - timedelta(days=lookback_days) if lookback_days > 0 else None
    real_rows = normalize_memory_rows(real_closed_trades, market_type=identity["marketType"])
    shadow_rows = (
        normalize_memory_rows(
            independent_closed_episode_rows(list(shadow_closed_trades or [])),
            market_type=identity["marketType"],
        )
        if _enabled(cfg.get("include_shadow"))
        else []
    )

    matched: List[Tuple[str, Dict[str, Any]]] = []
    for row in real_rows + shadow_rows:
        ts = _parse_dt(row.get("windowTimestamp"))
        if cutoff:
            if ts is None or ts < cutoff:
                continue
        level = _row_match_level(row, identity)
        if level != "none":
            matched.append((level, row))

    priority = ["exact", "strategy_side_coin_regime", "strategy_side_regime", "strategy_side"]
    chosen_level = "none"
    chosen_rows: List[Dict[str, Any]] = []
    for level in priority:
        rows = [row for match_level, row in matched if match_level == level]
        eligible_rows: List[Dict[str, Any]] = []
        real_matches = [row for row in rows if row.get("source") == "real"]
        shadow_matches = [row for row in rows if row.get("source") == "shadow"]
        if len(real_matches) >= int(cfg.get("min_real_samples") or 0):
            eligible_rows.extend(real_matches)
        if len(shadow_matches) >= int(cfg.get("min_shadow_episodes") or 0):
            eligible_rows.extend(shadow_matches)
        if eligible_rows:
            chosen_level = level
            chosen_rows = eligible_rows
            break

    if not chosen_rows:
        return SetupMemoryDecision(
            action="allow",
            reason="no matching closed setup memory meets its source sample floor",
            enabled=True,
            mode=mode,
            market_type=identity["marketType"],
            setup_fingerprint=identity["setupFingerprint"],
        )

    stats = _stats(chosen_rows)
    latest_loss_at = stats["latest_loss_at"]
    sample_floor = int(
        cfg.get("min_shadow_episodes" if all(row.get("source") == "shadow" for row in chosen_rows) else "min_real_samples")
        or 0
    )
    action = "allow"
    reason = f"{chosen_level} setup memory clear"
    recent_loss_hours = _safe_float(cfg.get("recent_loss_cooldown_hours"), 0.0)
    if latest_loss_at and recent_loss_hours > 0:
        until = latest_loss_at + timedelta(hours=recent_loss_hours)
        if until > now_dt and chosen_level in {"exact", "strategy_side_coin_regime"}:
            action = "block"
            reason = f"recent {chosen_level} setup loss; cooldown until {until.isoformat()}"
    if action == "allow" and chosen_level == "exact":
        streak_limit = int(cfg.get("exact_setup_loss_streak_block") or 0)
        if streak_limit > 0 and _loss_streak(chosen_rows) >= streak_limit:
            action = "block"
            reason = f"exact setup loss streak >= {streak_limit}"
    if action == "allow" and stats["count"] >= sample_floor:
        all_loss_floor = sample_floor
        if chosen_level in {"strategy_side_regime", "strategy_side"}:
            all_loss_floor = int(cfg.get("broad_all_loss_min_samples") or sample_floor)
        if (
            _enabled(cfg.get("block_all_loss_cohorts"))
            and stats["count"] >= all_loss_floor
            and stats["losses"] >= all_loss_floor
            and stats["wins"] == 0
        ):
            action = "block"
            reason = f"{chosen_level} setup all-loss cohort ({stats['losses']}/{stats['count']})"
        pf = stats["profit_factor"]
        expectancy = stats["expectancy"]
        if action == "allow" and expectancy is not None and expectancy < _safe_float(cfg.get("block_expectancy_below"), 0.0):
            if pf is not None and pf < _safe_float(cfg.get("block_profit_factor_below"), 0.8):
                broad_block = _enabled(cfg.get("block_negative_expectancy_broad")) and chosen_level in {
                    "strategy_side_regime",
                    "strategy_side",
                }
                action = "block" if chosen_level in {"exact", "strategy_side_coin_regime"} or broad_block else "size_down"
                reason = (
                    f"{chosen_level} setup negative expectancy {expectancy:.2f} "
                    f"and profit factor {pf:.2f}"
                )
        if action == "allow" and pf is not None and pf < _safe_float(cfg.get("caution_profit_factor_below"), 1.1):
            action = "size_down" if chosen_level in {"exact", "strategy_side_coin_regime"} else "caution"
            reason = f"{chosen_level} setup weak profit factor {pf:.2f}"
        win_rate = stats["win_rate"]
        if action == "allow" and win_rate is not None and win_rate < _safe_float(cfg.get("caution_win_rate_below"), 0.45):
            action = "caution"
            reason = f"{chosen_level} setup low win rate {win_rate:.1%}"
    elif action == "allow":
        reason = f"{chosen_level} setup memory sample {stats['count']} below floor {sample_floor}"

    effective_action = action
    if mode == "advisory" and action in {"block", "size_down"}:
        effective_action = "allow"
        reason = f"advisory: would {action}; {reason}"
    elif mode == "size_down" and action == "block":
        effective_action = "size_down"
        reason = f"size_down mode: would block; {reason}"

    size_multiplier = 1.0
    if effective_action == "size_down":
        size_multiplier = max(0.0, min(1.0, _safe_float(cfg.get("size_down_multiplier"), 0.5)))

    evidence = _evidence_slice(chosen_rows)
    return SetupMemoryDecision(
        action=effective_action,
        reason=reason,
        enabled=True,
        mode=mode,
        market_type=identity["marketType"],
        setup_fingerprint=identity["setupFingerprint"],
        size_multiplier=size_multiplier,
        matched_count=int(stats["count"]),
        loss_count=int(stats["losses"]),
        win_count=int(stats["wins"]),
        win_rate=stats["win_rate"],
        expectancy=stats["expectancy"],
        profit_factor=stats["profit_factor"],
        latest_loss_at=latest_loss_at.isoformat() if latest_loss_at else None,
        match_level=chosen_level,
        evidence=evidence,
        permanent=False,
    )
