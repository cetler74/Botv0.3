"""
Redis fast-signal cache for low-latency standalone strategy entries.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

STRATEGY_KEY = "rsi_stoch_reversal_5m"
FAST_SIGNAL_PREFIX = f"trading:fast_signal:{STRATEGY_KEY}"
DEFAULT_TTL_SECONDS = 45
HOLD_TTL_SECONDS = 15


def redis_key(venue: str, symbol: str) -> str:
    v = str(venue or "").strip().lower()
    s = str(symbol or "").strip().upper().replace("/", "")
    return f"{FAST_SIGNAL_PREFIX}:{v}:{s}"


def _normalize_payload(
    signal: str,
    confidence: float,
    strength: float,
    indicators: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    ind = indicators or {}
    return {
        "signal": str(signal or "hold").lower(),
        "confidence": str(confidence),
        "strength": str(strength),
        "side_intent": str(ind.get("side_intent") or ""),
        "rsi": str(ind.get("rsi") or ""),
        "stoch_rsi_k": str(ind.get("stoch_rsi_k") or ""),
        "stoch_rsi_d": str(ind.get("stoch_rsi_d") or ""),
        "entry_reason": str(ind.get("entry_reason") or ind.get("skip_reason") or ""),
        "bar_close_time": str(ind.get("bar_close_time") or ""),
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "strategy": STRATEGY_KEY,
        "indicators_json": json.dumps(ind, default=str),
    }


def _float_indicator(ind: Dict[str, Any], key: str) -> Optional[float]:
    raw = ind.get(key)
    if raw is None or raw == "":
        return None
    try:
        val = float(raw)
        if val != val:
            return None
        return val
    except (TypeError, ValueError):
        return None


def validate_rsi_stoch_actionable(
    payload: Dict[str, Any],
    *,
    allow_short: bool = False,
    params: Optional[Any] = None,
    max_bar_age_seconds: float = 360.0,
) -> Tuple[bool, str]:
    """
    Confirm payload indicators satisfy rsi_stoch entry rules (not just signal side).
    """
    from strategy.playbooks.rsi_stoch_reversal_5m_engine import EngineParams

    p = params if params is not None else EngineParams()
    if isinstance(params, dict):
        from strategy.playbooks.rsi_stoch_reversal_5m_engine import params_from_config

        p = params_from_config({"parameters": params})

    sig = str(payload.get("signal") or "").lower()
    side = normalize_perp_side(sig)
    if not side:
        return False, "not_actionable_signal"

    ind = payload.get("indicators") or {}
    if not isinstance(ind, dict):
        return False, "missing_indicators"

    if ind.get("skip_reason") and not ind.get("entry_reason"):
        return False, f"skip_reason:{ind.get('skip_reason')}"

    entry_reason = str(ind.get("entry_reason") or "").strip()
    if not entry_reason:
        return False, "missing_entry_reason"

    rsi = _float_indicator(ind, "rsi")
    k = _float_indicator(ind, "stoch_rsi_k")
    d = _float_indicator(ind, "stoch_rsi_d")
    if rsi is None or k is None or d is None:
        return False, "nan_or_missing_indicators"

    bar_time = ind.get("bar_close_time")
    if bar_time:
        age = signal_age_seconds({"analyzed_at": str(bar_time)})
        if age is not None and age > max_bar_age_seconds:
            return False, f"stale_bar_close_time age={age:.0f}s"

    stoch_os = float(p.stoch_oversold)
    stoch_ob = float(p.stoch_overbought)
    rsi_os = float(p.rsi_oversold)

    if side == "long":
        if not (rsi < rsi_os and k < stoch_os and d < stoch_os and k > d):
            return False, "long_rules_failed"
        return True, "long_ok"

    if side == "short":
        if not allow_short:
            return False, "short_not_allowed"
        if not (k > stoch_ob and d > stoch_ob and d > k):
            return False, "short_rules_failed"
        return True, "short_ok"

    return False, "unknown_side"


async def clear_fast_signal(redis_client: Any, venue: str, symbol: str) -> bool:
    if redis_client is None:
        return False
    key = redis_key(venue, symbol)
    try:
        await redis_client.delete(key)
        return True
    except Exception as exc:
        logger.debug("[FastSignal] clear failed %s: %s", key, exc)
        return False


async def publish_fast_signal(
    redis_client: Any,
    venue: str,
    symbol: str,
    signal: str,
    confidence: float,
    strength: float,
    indicators: Optional[Dict[str, Any]] = None,
    *,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    allow_short: bool = False,
    params: Optional[Any] = None,
) -> bool:
    if redis_client is None:
        return False
    key = redis_key(venue, symbol)
    sig_lc = str(signal or "hold").lower()
    if sig_lc in {"hold", ""}:
        return True

    payload = _normalize_payload(signal, confidence, strength, indicators)
    try:
        await redis_client.hset(key, mapping=payload)
        await redis_client.expire(key, max(15, int(ttl_seconds)))
        if sig_lc in {"buy", "long", "short", "sell"}:
            logger.warning(
                "[FastSignal] %s %s %s conf=%s str=%s",
                venue,
                symbol,
                sig_lc,
                payload["confidence"],
                payload["strength"],
            )
        return True
    except Exception as exc:
        logger.debug("[FastSignal] publish failed %s: %s", key, exc)
        return False


async def read_fast_signal(
    redis_client: Any,
    venue: str,
    symbol: str,
) -> Optional[Dict[str, Any]]:
    if redis_client is None:
        return None
    key = redis_key(venue, symbol)
    try:
        raw = await redis_client.hgetall(key)
        if not raw:
            return None
        decoded: Dict[str, Any] = {}
        for k, v in raw.items():
            key_s = k.decode() if isinstance(k, bytes) else str(k)
            val_s = v.decode() if isinstance(v, bytes) else str(v)
            decoded[key_s] = val_s
        try:
            decoded["confidence"] = float(decoded.get("confidence") or 0)
            decoded["strength"] = float(decoded.get("strength") or 0)
        except (TypeError, ValueError):
            decoded["confidence"] = 0.0
            decoded["strength"] = 0.0
        ind_raw = decoded.get("indicators_json")
        if ind_raw:
            try:
                decoded["indicators"] = json.loads(ind_raw)
            except json.JSONDecodeError:
                decoded["indicators"] = {}
        return decoded
    except Exception as exc:
        logger.debug("[FastSignal] read failed %s: %s", key, exc)
        return None


def merge_rsi_stoch_spot_buy_into_signals(
    signals_data: Dict[str, Any],
    fast_payload: Dict[str, Any],
) -> bool:
    """
    Overlay a Redis fast-lane BUY into strategy-service snapshot so spot
    standalone entry sees rsi_stoch_reversal_5m without waiting on full consensus.
    """
    if str(fast_payload.get("signal", "")).lower() != "buy":
        return False
    ind = fast_payload.get("indicators") or {}
    if not isinstance(ind, dict):
        ind = {}
    strategies = signals_data.setdefault("strategies", {})
    if not isinstance(strategies, dict):
        return False
    strategies[STRATEGY_KEY] = {
        "signal": "buy",
        "confidence": float(fast_payload.get("confidence") or 0),
        "strength": float(fast_payload.get("strength") or 0),
        "market_regime": ind.get("market_regime") or signals_data.get("market_regime"),
        "timestamp": fast_payload.get("analyzed_at"),
        "state": {"indicators": ind},
    }
    return True


def mirrored_perp_signal_from_fast_payload(
    fast_payload: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Build orchestrator mirrored-signal dict from Redis rsi_stoch perp payload."""
    sig = str(fast_payload.get("signal", "")).lower()
    if sig == "buy":
        side = "long"
    elif sig == "sell":
        side = "short"
    elif sig in {"long", "short"}:
        side = sig
    else:
        return None
    strategy_name = str(fast_payload.get("strategy") or STRATEGY_KEY)
    conf = float(fast_payload.get("confidence") or 0)
    strength = float(fast_payload.get("strength") or 0)
    ind = fast_payload.get("indicators") or {}
    if not isinstance(ind, dict):
        ind = {}
    return {
        "strategy": strategy_name,
        "signal": side,
        "confidence": conf,
        "strength": strength,
        "consensus_confidence": conf,
        "consensus_agreement": 100.0,
        "details": {"state": {"indicators": ind}},
    }


def signals_data_from_fast_perp_payload(
    fast_payload: Dict[str, Any],
    coin: str,
) -> Dict[str, Any]:
    """Minimal HL signals_data envelope for fast-entry guard chain."""
    mirrored = mirrored_perp_signal_from_fast_payload(fast_payload)
    if not mirrored:
        return {}
    strategy_name = mirrored["strategy"]
    ind = (mirrored.get("details") or {}).get("state", {}).get("indicators") or {}
    return {
        "coin": str(coin).upper(),
        "market_regime": str(
            ind.get("market_regime") or fast_payload.get("market_regime") or ""
        ).lower(),
        "strategies": {
            strategy_name: {
                "signal": mirrored["signal"],
                "confidence": mirrored["confidence"],
                "strength": mirrored["strength"],
            }
        },
    }


def normalize_perp_side(signal: str) -> Optional[str]:
    sig = str(signal or "").lower()
    if sig in {"buy", "long"}:
        return "long"
    if sig in {"sell", "short"}:
        return "short"
    return None


def fast_payload_from_hl_strategy_signal(live: Dict[str, Any]) -> Dict[str, Any]:
    """Build a fast-entry payload from strategy-service HL single-strategy response."""
    ind = ((live.get("state") or {}).get("indicators") or {})
    if not isinstance(ind, dict):
        ind = {}
    return {
        "signal": str(live.get("signal") or "hold").lower(),
        "confidence": float(live.get("confidence") or 0),
        "strength": float(live.get("strength") or 0),
        "indicators": ind,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "strategy": str(live.get("strategy") or STRATEGY_KEY),
    }


def signal_age_seconds(payload: Dict[str, Any]) -> Optional[float]:
    analyzed = payload.get("analyzed_at")
    if not analyzed:
        return None
    try:
        ts = datetime.fromisoformat(str(analyzed).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds()
    except (TypeError, ValueError):
        return None


async def create_redis_client():
    url = os.getenv("REDIS_URL", "redis://redis:6379")
    try:
        import redis.asyncio as redis

        return redis.from_url(url, decode_responses=False)
    except Exception as exc:
        logger.warning("[FastSignal] Redis unavailable: %s", exc)
        return None
