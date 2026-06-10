"""
Hyperliquid perp entry-cycle snapshots for the portfolio dashboard watchlist.

Written by orchestrator after each coin is evaluated in the perp entry cycle;
read by web-dashboard when building portfolio-intelligence watchlist rows.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

SNAPSHOT_ENTRY_CONFIDENCE = 0.3
HL_CYCLE_REDIS_PREFIX = "cycle:hl:"
HL_CYCLE_TTL_SECONDS = 120


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_hl_coin(coin: str) -> str:
    raw = str(coin or "").strip().upper()
    if "/" in raw:
        raw = raw.split("/", 1)[0]
    if ":" in raw:
        raw = raw.split(":", 1)[1]
    for suffix in ("USDC", "USDT", "USD"):
        if raw.endswith(suffix) and len(raw) > len(suffix):
            return raw[: -len(suffix)]
    return raw


def hl_cycle_redis_key(coin: str) -> str:
    return f"{HL_CYCLE_REDIS_PREFIX}{normalize_hl_coin(coin)}"


def _normalize_perp_signal(signal: Any) -> str:
    raw = str(signal or "hold").strip().lower()
    if raw in {"buy", "long"}:
        return "long"
    if raw in {"sell", "short"}:
        return "short"
    return "hold"


def _strategy_hold_reason(name: str, sr: Mapping[str, Any]) -> str:
    """Human-readable HOLD reason for a strategy result row."""
    indicators = ((sr.get("state") or {}).get("indicators") or {})
    if not isinstance(indicators, dict):
        indicators = {}
    skip = indicators.get("skip_reason") or sr.get("skip_reason")
    if skip:
        setup = indicators.get("setup") or name
        return f"HOLD — {setup}: {skip}"
    entry_reason = (
        (sr.get("state") or {}).get("entry_reason")
        or indicators.get("entry_reason")
        or indicators.get("entry_reason_detail")
        or ""
    )
    if entry_reason:
        return str(entry_reason)
    return "HOLD — no directional entry from this strategy."


def _strategy_outcome(sig: str, conf: float) -> str:
    if sig == "long" and conf >= SNAPSHOT_ENTRY_CONFIDENCE:
        return "long_signal"
    if sig == "short" and conf >= SNAPSHOT_ENTRY_CONFIDENCE:
        return "short_signal"
    if sig in {"long", "short"}:
        return f"weak_{sig}"
    return "hold"


def build_hl_strategy_rows(results: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Format HL strategy-service payload into dashboard-friendly strategy rows."""
    strategies = results.get("strategies") or {}
    if not isinstance(strategies, dict):
        return []
    rows: List[Dict[str, Any]] = []
    for name, sr in sorted(strategies.items()):
        if not isinstance(sr, dict):
            continue
        if "error" in sr:
            rows.append(
                {
                    "name": name,
                    "signal": None,
                    "confidence": 0.0,
                    "strength": 0.0,
                    "outcome": "failed",
                    "reason": f"Strategy error: {sr['error']}",
                }
            )
            continue
        sig = _normalize_perp_signal(sr.get("signal"))
        conf = _safe_float(sr.get("confidence"))
        strength = _safe_float(sr.get("strength"))
        outcome = _strategy_outcome(sig, conf)
        if sig in {"long", "short"} and conf >= SNAPSHOT_ENTRY_CONFIDENCE:
            indicators = ((sr.get("state") or {}).get("indicators") or {})
            reason = (
                str(indicators.get("entry_reason") or "")
                if isinstance(indicators, dict)
                else ""
            ) or f"{sig.upper()} at or above confidence bar ({conf:.2f} ≥ {SNAPSHOT_ENTRY_CONFIDENCE})."
        elif sig in {"long", "short"}:
            reason = (
                f"{sig.upper()} below entry-confidence bar ({conf:.2f} < {SNAPSHOT_ENTRY_CONFIDENCE})."
            )
        else:
            reason = _strategy_hold_reason(name, sr)
        rows.append(
            {
                "name": name,
                "signal": sig,
                "confidence": round(conf, 4),
                "strength": round(strength, 4),
                "outcome": outcome,
                "reason": reason,
            }
        )
    return rows


def build_hl_consensus_summary(results: Mapping[str, Any]) -> Dict[str, Any]:
    consensus = results.get("consensus") or {}
    if not isinstance(consensus, dict):
        consensus = {}
    signal = _normalize_perp_signal(consensus.get("signal"))
    return {
        "signal": signal,
        "confidence": round(_safe_float(consensus.get("confidence")), 4),
        "agreement": round(_safe_float(consensus.get("agreement")), 2),
        "participatingStrategies": int(consensus.get("participating_strategies") or 0),
    }


def build_hl_best_signal(
    mirrored: Optional[Mapping[str, Any]],
    strategies: Sequence[Mapping[str, Any]],
) -> Optional[Dict[str, Any]]:
    if mirrored and mirrored.get("strategy"):
        side = _normalize_perp_signal(mirrored.get("signal"))
        if side in {"long", "short"}:
            return {
                "strategy": str(mirrored.get("strategy") or ""),
                "side": side,
                "confidence": round(_safe_float(mirrored.get("confidence")), 4),
                "strength": round(_safe_float(mirrored.get("strength")), 4),
            }
    best: Optional[Dict[str, Any]] = None
    for row in strategies:
        side = _normalize_perp_signal(row.get("signal"))
        if side not in {"long", "short"}:
            continue
        conf = _safe_float(row.get("confidence"))
        if best is None or conf > _safe_float(best.get("confidence")):
            best = {
                "strategy": str(row.get("name") or ""),
                "side": side,
                "confidence": round(conf, 4),
                "strength": round(_safe_float(row.get("strength")), 4),
            }
    return best


def build_hl_cycle_analysis(results: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Analysis portion of a cycle snapshot (no entry decision)."""
    if not results:
        return {
            "regime": None,
            "consensus": {"signal": "hold", "confidence": 0.0, "agreement": 0.0, "participatingStrategies": 0},
            "strategies": [],
            "bestSignal": None,
        }
    strategies = build_hl_strategy_rows(results)
    regime = (
        results.get("stable_regime")
        or results.get("market_regime")
        or results.get("detected_regime")
    )
    return {
        "regime": str(regime) if regime else None,
        "consensus": build_hl_consensus_summary(results),
        "strategies": strategies,
        "bestSignal": build_hl_best_signal(None, strategies),
    }


def build_entry_decision(
    *,
    outcome: str,
    primary_reason: str,
    message: str = "",
    gate_chain: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    return {
        "outcome": outcome,
        "primaryReason": primary_reason,
        "message": message,
        "gateChain": list(gate_chain or []),
    }


def merge_hl_cycle_snapshot(
    *,
    coin: str,
    cycle_at: Optional[str] = None,
    cycle_count: int = 0,
    signals_data: Optional[Mapping[str, Any]] = None,
    mirrored: Optional[Mapping[str, Any]] = None,
    entry_decision: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    analysis = build_hl_cycle_analysis(signals_data or {})
    if mirrored:
        analysis["bestSignal"] = build_hl_best_signal(
            mirrored, analysis.get("strategies") or []
        )
    return {
        "coin": normalize_hl_coin(coin),
        "cycleAt": cycle_at or (datetime.utcnow().isoformat() + "Z"),
        "cycleCount": int(cycle_count or 0),
        "regime": analysis.get("regime"),
        "consensus": analysis.get("consensus") or {},
        "strategies": analysis.get("strategies") or [],
        "bestSignal": analysis.get("bestSignal"),
        "entryDecision": dict(entry_decision or build_entry_decision(
            outcome="not_scanned",
            primary_reason="not_scanned",
            message="Not evaluated this cycle",
        )),
    }


def merge_hl_cycle_analysis_into_watchlist(
    watchlist: Iterable[Dict[str, Any]],
    snapshots_by_coin: Mapping[str, Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """Attach cycleAnalysis to each watchlist row keyed by coin."""
    out: List[Dict[str, Any]] = []
    for row in watchlist:
        merged = dict(row)
        coin = normalize_hl_coin(str(row.get("coin") or ""))
        snap = snapshots_by_coin.get(coin)
        if snap:
            merged["cycleAnalysis"] = snap
        else:
            merged["cycleAnalysis"] = merge_hl_cycle_snapshot(
                coin=coin,
                entry_decision=build_entry_decision(
                    outcome="not_scanned",
                    primary_reason="not_scanned",
                    message="No cycle snapshot available",
                ),
            )
        out.append(merged)
    return out


def serialize_hl_cycle_snapshot(snapshot: Mapping[str, Any]) -> str:
    return json.dumps(snapshot, default=str, separators=(",", ":"))


def parse_hl_cycle_snapshot(raw: Any) -> Optional[Dict[str, Any]]:
    if raw is None:
        return None
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    try:
        parsed = json.loads(str(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


async def write_hl_cycle_snapshot(
    redis_client: Any,
    coin: str,
    snapshot: Mapping[str, Any],
    *,
    ttl_seconds: int = HL_CYCLE_TTL_SECONDS,
) -> bool:
    if redis_client is None:
        return False
    key = hl_cycle_redis_key(coin)
    try:
        payload = serialize_hl_cycle_snapshot(snapshot)
        await redis_client.set(key, payload, ex=max(15, int(ttl_seconds)))
        return True
    except Exception:
        return False


async def read_hl_cycle_snapshots(
    redis_client: Any,
    coins: Sequence[str],
) -> Dict[str, Dict[str, Any]]:
    if redis_client is None or not coins:
        return {}
    keys = [hl_cycle_redis_key(c) for c in coins]
    normalized = [normalize_hl_coin(c) for c in coins]
    try:
        values = await redis_client.mget(keys)
    except Exception:
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for coin, raw in zip(normalized, values or []):
        parsed = parse_hl_cycle_snapshot(raw)
        if parsed:
            out[coin] = parsed
    return out


async def create_redis_client():
    url = os.getenv("REDIS_URL", "redis://redis:6379")
    try:
        import redis.asyncio as redis

        return redis.from_url(url, decode_responses=True)
    except Exception:
        return None


class HlCoinCycleRecorder:
    """Collect gate checks for one HL coin and persist a cycle snapshot to Redis."""

    def __init__(
        self,
        *,
        coin: str,
        cycle_count: int,
        redis_client: Any,
    ) -> None:
        self.coin = normalize_hl_coin(coin)
        self.cycle_count = int(cycle_count or 0)
        self.redis_client = redis_client
        self.signals_data: Dict[str, Any] = {}
        self.mirrored: Optional[Dict[str, Any]] = None
        self.gate_chain: List[Dict[str, Any]] = []

    def set_signals(self, signals_data: Optional[Mapping[str, Any]]) -> None:
        self.signals_data = dict(signals_data or {})

    def set_mirrored(self, mirrored: Optional[Mapping[str, Any]]) -> None:
        self.mirrored = dict(mirrored) if mirrored else None

    def add_gate(self, step: str, passed: bool, message: str = "") -> None:
        self.gate_chain.append(
            {
                "step": str(step),
                "passed": bool(passed),
                "message": str(message or ""),
            }
        )

    async def finish(
        self,
        *,
        outcome: str,
        primary_reason: str,
        message: str = "",
    ) -> None:
        snapshot = merge_hl_cycle_snapshot(
            coin=self.coin,
            cycle_count=self.cycle_count,
            signals_data=self.signals_data or None,
            mirrored=self.mirrored,
            entry_decision=build_entry_decision(
                outcome=outcome,
                primary_reason=primary_reason,
                message=message,
                gate_chain=self.gate_chain,
            ),
        )
        await write_hl_cycle_snapshot(self.redis_client, self.coin, snapshot)
