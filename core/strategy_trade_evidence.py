"""Deterministic strategy-entry metadata and normalized closed-trade evidence."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional


ENTRY_EVIDENCE_MARKER = " [strategy_evidence:"
PERP_CLOSE_PROTECTED_METADATA_KEYS = {
    "highest_price",
    "lowest_price",
    "mfe_pct",
    "mae_pct",
    "excursion_status",
    "excursion_unavailable_reason",
    "exit_policy_snapshot",
    "exit_policy_status",
    "close_evidence_status",
}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def metadata_dict(trade: Mapping[str, Any]) -> Dict[str, Any]:
    raw = trade.get("metadata") or {}
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
    return dict(raw) if isinstance(raw, Mapping) else {}


def _first(*values: Any, default: Any = None) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return default


def _utc_timestamp(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _canonical_json(value: Any) -> str:
    if is_dataclass(value):
        value = asdict(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _config_hash(signal: Mapping[str, Any], strategy_config: Optional[Mapping[str, Any]]) -> str:
    supplied = _first(
        signal.get("strategy_config_hash"),
        signal.get("config_hash"),
        _mapping(signal.get("metadata")).get("strategy_config_hash"),
    )
    if supplied:
        return str(supplied)
    if not strategy_config:
        return "unknown"
    return hashlib.sha256(_canonical_json(strategy_config).encode("utf-8")).hexdigest()


def _timeframe_bundle(
    signal: Mapping[str, Any],
    strategy_config: Optional[Mapping[str, Any]] = None,
) -> Any:
    details = _mapping(signal.get("details"))
    state = _mapping(details.get("state"))
    indicators = _mapping(state.get("indicators"))
    explicit = _first(
        signal.get("timeframe_bundle"),
        signal.get("timeframes"),
        details.get("timeframe_bundle"),
        state.get("timeframe_bundle"),
        indicators.get("timeframe_bundle"),
    )
    if explicit is not None:
        if isinstance(explicit, Mapping):
            return dict(explicit)
        if isinstance(explicit, (list, tuple)):
            return list(explicit)
        return [str(explicit)]
    config = _mapping(strategy_config)
    parameters = _mapping(config.get("parameters"))
    configured = _first(
        config.get("timeframe_bundle"),
        config.get("target_timeframes"),
        parameters.get("timeframe_bundle"),
        parameters.get("target_timeframes"),
    )
    if configured is not None:
        if isinstance(configured, Mapping):
            return dict(configured)
        if isinstance(configured, (list, tuple)):
            return list(configured)
        return [str(configured)]
    bundle: Dict[str, Any] = {}
    for key in (
        "macro_timeframe",
        "trend_timeframe",
        "structure_timeframe",
        "signal_timeframe",
        "confirmation_timeframe",
        "entry_timeframe",
        "execution_timeframe",
        "primary_timeframe",
    ):
        value = _first(
            signal.get(key),
            details.get(key),
            state.get(key),
            indicators.get(key),
            parameters.get(key),
            config.get(key),
        )
        if value is not None:
            bundle[key.removesuffix("_timeframe")] = value
    return bundle or "unknown"


def build_strategy_entry_evidence(
    signal: Mapping[str, Any],
    *,
    strategy_config: Optional[Mapping[str, Any]] = None,
    status: str = "entered",
) -> Dict[str, Any]:
    """Build stable, JSON-safe entry evidence from a strategy signal."""
    signal = _mapping(signal)
    details = _mapping(signal.get("details"))
    state = _mapping(details.get("state"))
    indicators = _mapping(state.get("indicators"))
    strategy_key = str(
        _first(signal.get("strategy_key"), signal.get("strategy"), default="unknown")
    ).strip().lower() or "unknown"
    config_hash = _config_hash(signal, strategy_config)
    declared_version = _first(
        signal.get("strategy_version"),
        signal.get("version"),
        details.get("strategy_version"),
        state.get("strategy_version"),
        _mapping(strategy_config).get("strategy_version"),
        _mapping(strategy_config).get("version"),
    )
    strategy_version = (
        str(declared_version).strip()
        if declared_version is not None
        else f"config-{config_hash[:12]}"
        if config_hash != "unknown"
        else "unversioned"
    ) or "unversioned"
    why = str(
        _first(
            _mapping(signal.get("rationale")).get("why"),
            signal.get("why"),
            state.get("entry_reason"),
            details.get("entry_reason"),
            indicators.get("entry_reason_detail"),
            indicators.get("entry_reason"),
            default="unknown",
        )
    ).strip() or "unknown"
    candle_timestamp = _utc_timestamp(
        _first(
            signal.get("signal_candle_timestamp"),
            signal.get("signal_candle_ts"),
            indicators.get("signal_candle_ts"),
            indicators.get("bar_close_time"),
            indicators.get("bar_timestamp"),
            signal.get("timestamp"),
            details.get("timestamp"),
            details.get("candle_ts"),
            state.get("timestamp"),
        )
    )
    rationale = {"why": why, "status": str(status or "unknown").strip().lower()}
    supplied_details = _mapping(_mapping(signal.get("rationale")).get("details"))
    if supplied_details:
        rationale["details"] = dict(supplied_details)
    market_regime = str(
        _first(
            signal.get("market_regime"),
            signal.get("stable_regime"),
            state.get("market_regime"),
            indicators.get("market_regime"),
            default="unknown",
        )
    ).strip().lower() or "unknown"
    return {
        "strategy_key": strategy_key,
        "strategy_version": strategy_version,
        "strategy_config_hash": config_hash,
        "timeframe_bundle": _timeframe_bundle(signal, strategy_config),
        "signal_candle_timestamp": candle_timestamp or "unknown",
        "market_regime": market_regime,
        "rationale": rationale,
    }


def encode_strategy_evidence_entry_reason(
    base_reason: str, evidence: Mapping[str, Any]
) -> str:
    """Append evidence to the existing structured spot entry-reason string."""
    reason = str(base_reason or "").strip()
    if not evidence:
        return reason
    payload = _canonical_json(dict(evidence))
    marker = f"{ENTRY_EVIDENCE_MARKER}{payload}]"
    return f"{reason}{marker}" if reason else marker.strip()


def parse_strategy_evidence_entry_reason(entry_reason: str) -> Dict[str, Any]:
    text = str(entry_reason or "")
    start = text.rfind(ENTRY_EVIDENCE_MARKER)
    if start < 0:
        return {}
    decoder = json.JSONDecoder()
    try:
        parsed, _ = decoder.raw_decode(text[start + len(ENTRY_EVIDENCE_MARKER) :])
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def freeze_perp_close_evidence(
    metadata: Mapping[str, Any],
    *,
    side: str,
    entry_price: Any,
    exit_policy: Any = None,
) -> Dict[str, Any]:
    """Return a metadata copy with immutable close-time excursion evidence."""
    frozen = dict(metadata or {})
    highest_raw = frozen.get("highest_price")
    lowest_raw = frozen.get("lowest_price")
    already_frozen = (
        frozen.get("excursion_status") == "frozen"
        and frozen.get("mfe_pct") is not None
        and frozen.get("mae_pct") is not None
    )
    if not already_frozen:
        try:
            entry = float(entry_price)
            if not math.isfinite(entry) or entry <= 0:
                raise ValueError
        except (TypeError, ValueError, OverflowError):
            entry = None
            unavailable_reason = "invalid_entry_price"
        else:
            unavailable_reason = ""
        if entry is not None and (
            highest_raw is None or lowest_raw is None
        ):
            unavailable_reason = "missing_extreme_price"
        if entry is not None and not unavailable_reason:
            try:
                highest = float(highest_raw)
                lowest = float(lowest_raw)
                if not math.isfinite(highest) or not math.isfinite(lowest):
                    raise ValueError
            except (TypeError, ValueError, OverflowError):
                unavailable_reason = "invalid_extreme_price"
        if entry is not None and not unavailable_reason:
            highest = max(entry, highest)
            lowest = min(entry, lowest)
            if str(side or "").lower() == "short":
                mfe = ((entry - lowest) / entry) * 100.0
                mae = -((highest - entry) / entry) * 100.0
            else:
                mfe = ((highest - entry) / entry) * 100.0
                mae = -((entry - lowest) / entry) * 100.0
            frozen["mfe_pct"] = round(mfe, 10)
            frozen["mae_pct"] = round(mae, 10)
            frozen["excursion_status"] = "frozen"
            frozen.pop("excursion_unavailable_reason", None)
        else:
            frozen["mfe_pct"] = None
            frozen["mae_pct"] = None
            frozen["excursion_status"] = "unavailable"
            frozen["excursion_unavailable_reason"] = unavailable_reason
    if exit_policy is None:
        exit_policy = frozen.get("exit_policy_snapshot")
    if exit_policy:
        if is_dataclass(exit_policy):
            exit_policy = asdict(exit_policy)
        frozen["exit_policy_snapshot"] = json.loads(_canonical_json(exit_policy))
        frozen["exit_policy_status"] = "frozen"
    else:
        frozen.pop("exit_policy_snapshot", None)
        frozen["exit_policy_status"] = "unavailable"
    excursion_available = frozen.get("excursion_status") == "frozen"
    policy_available = frozen.get("exit_policy_status") == "frozen"
    frozen["close_evidence_status"] = (
        "complete"
        if excursion_available and policy_available
        else "partial"
        if excursion_available or policy_available
        else "unavailable"
    )
    return frozen


def normalize_perp_close_update(
    existing_trade: Mapping[str, Any],
    update_data: Mapping[str, Any],
    *,
    metadata_field: str,
) -> Dict[str, Any]:
    """Normalize a CLOSED persistence payload using only ledger-backed evidence."""
    normalized = dict(update_data or {})
    if str(normalized.get("status") or "").upper() != "CLOSED":
        return normalized
    existing_metadata = metadata_dict(existing_trade)
    was_closed = str(existing_trade.get("status") or "").upper() == "CLOSED"
    incoming_metadata = normalized.get(metadata_field) or {}
    if isinstance(incoming_metadata, str):
        try:
            incoming_metadata = json.loads(incoming_metadata)
        except (TypeError, ValueError, json.JSONDecodeError):
            incoming_metadata = {}
    merged_metadata = dict(existing_metadata)
    if isinstance(incoming_metadata, Mapping):
        for key, value in incoming_metadata.items():
            if key not in PERP_CLOSE_PROTECTED_METADATA_KEYS:
                merged_metadata[key] = value
        if not was_closed:
            for key in ("highest_price", "lowest_price", "exit_policy_snapshot"):
                if key not in merged_metadata and key in incoming_metadata:
                    merged_metadata[key] = incoming_metadata[key]
    normalized[metadata_field] = freeze_perp_close_evidence(
        merged_metadata,
        side=str(existing_trade.get("position_side") or "unknown"),
        entry_price=existing_trade.get("entry_price"),
        exit_policy=merged_metadata.get("exit_policy_snapshot"),
    )
    return normalized


def normalized_closed_trade_evidence(
    trade: Mapping[str, Any],
    *,
    market_type: str,
    exit_bucket_value: str,
) -> Dict[str, Any]:
    """Normalize one closed ledger row without removing source-specific fields."""
    metadata = metadata_dict(trade)
    entry_evidence = _mapping(metadata.get("entry_evidence"))
    if not entry_evidence and market_type == "spot":
        entry_evidence = parse_strategy_evidence_entry_reason(
            str(trade.get("entry_reason") or "")
        )
    rationale = _mapping(
        _first(
            metadata.get("rationale"),
            entry_evidence.get("rationale"),
            default={},
        )
    )
    strategy_key = str(
        _first(
            metadata.get("strategy_key"),
            entry_evidence.get("strategy_key"),
            trade.get("source_strategy"),
            trade.get("strategy"),
            default="unknown",
        )
    ).strip().lower() or "unknown"
    strategy_version = str(
        _first(
            metadata.get("strategy_version"),
            entry_evidence.get("strategy_version"),
            default="unversioned",
        )
    ).strip() or "unversioned"
    fees = float(trade.get("fees") or 0.0)
    funding = float(trade.get("funding") or 0.0)
    mfe = metadata.get("mfe_pct")
    mae = metadata.get("mae_pct")
    mae_status = "available" if mae is not None else "unavailable"
    if market_type == "spot":
        entry = float(trade.get("entry_price") or 0.0)
        highest = float(trade.get("highest_price") or 0.0)
        mfe = ((highest - entry) / entry) * 100.0 if entry > 0 and highest > 0 else None
        mae = None
        mae_status = "unavailable"
    status = str(trade.get("status") or "unknown").strip().lower() or "unknown"
    window_timestamp = _utc_timestamp(
        _first(trade.get("exit_time"), trade.get("entry_time"))
    )
    venue = str(
        _first(
            trade.get("venue"),
            trade.get("exchange"),
            trade.get("source_exchange"),
            default="unknown",
        )
    ).strip().lower() or "unknown"
    coin = str(
        _first(trade.get("coin"), trade.get("pair"), default="unknown")
    ).strip() or "unknown"
    return {
        "strategyKey": strategy_key,
        "strategyVersion": strategy_version,
        "configHash": str(
            _first(
                metadata.get("strategy_config_hash"),
                entry_evidence.get("strategy_config_hash"),
                default="unknown",
            )
        ),
        "timeframeBundle": _first(
            metadata.get("timeframe_bundle"),
            entry_evidence.get("timeframe_bundle"),
            default="unknown",
        ),
        "signalCandleTimestamp": _utc_timestamp(
            _first(
                metadata.get("signal_candle_timestamp"),
                entry_evidence.get("signal_candle_timestamp"),
            )
        )
        or "unknown",
        "venue": venue,
        "coin": coin,
        "side": str(
            _first(trade.get("position_side"), trade.get("side"), default="unknown")
        ).lower(),
        "regime": str(
            _first(
                metadata.get("market_regime"),
                metadata.get("stable_regime"),
                entry_evidence.get("market_regime"),
                entry_evidence.get("stable_regime"),
                trade.get("market_regime"),
                default="unknown",
            )
        ).lower(),
        "exitBucket": exit_bucket_value,
        "windowTimestamp": window_timestamp or "unknown",
        "status": status,
        "why": str(_first(rationale.get("why"), default="unknown")),
        "rationale": dict(rationale) if rationale else {"why": "unknown", "status": status},
        "realizedPnl": float(trade.get("realized_pnl") or 0.0),
        "costs": {
            "fees": fees,
            "funding": funding,
            "total": fees + funding,
        },
        "mfePct": mfe,
        "maePct": mae,
        "maeStatus": mae_status,
        "exitPolicySnapshot": metadata.get("exit_policy_snapshot") or {},
    }
