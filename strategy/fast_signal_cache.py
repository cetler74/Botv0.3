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

STRATEGY_KEY = "rsi_stoch_reversal_15m"
ORB_STRATEGY_KEY = "orb_5m_scalp"
RSI_STOCH_REVERSAL_STRATEGIES = frozenset(
    {"rsi_stoch_reversal_15m"}
)
DAYTRADE_FAST_STRATEGIES = frozenset(
    {
        "dual_sma_daytrade",
        "supply_demand_3step",
    }
)
DEFAULT_TTL_SECONDS = 45
ORB_FAST_TTL_SECONDS = 960
DAYTRADE_FAST_TTL_SECONDS = 45
HOLD_TTL_SECONDS = 15
RSI_STOCH_BAR_MAX_AGE_SECONDS = 960


def _normalize_hyperliquid_symbol(symbol: str) -> str:
    s = str(symbol or "").strip().replace("/", "")
    if not s:
        return ""
    if ":" in s:
        dex, base = s.split(":", 1)
        return f"{dex.strip().lower()}:{base.strip().upper()}"
    return s.upper()


def redis_key(venue: str, symbol: str, strategy_key: str = STRATEGY_KEY) -> str:
    v = str(venue or "").strip().lower()
    s = _normalize_hyperliquid_symbol(symbol) if v == "hyperliquid" else str(symbol or "").strip().upper().replace("/", "")
    strat = str(strategy_key or STRATEGY_KEY).strip().lower()
    return f"trading:fast_signal:{strat}:{v}:{s}"


def _normalize_payload(
    signal: str,
    confidence: float,
    strength: float,
    indicators: Optional[Dict[str, Any]] = None,
    *,
    strategy_key: str = STRATEGY_KEY,
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
        "strategy": str(strategy_key or STRATEGY_KEY).strip().lower(),
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
    max_bar_age_seconds: float = RSI_STOCH_BAR_MAX_AGE_SECONDS,
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
    rsi_ob = float(p.rsi_overbought)

    if side == "long":
        if not (rsi < rsi_os and k < stoch_os and d < stoch_os and k >= d):
            return False, "long_rules_failed"
        return True, "long_ok"

    if side == "short":
        if not allow_short:
            return False, "short_not_allowed"
        if not (rsi > rsi_ob and k > stoch_ob and d > stoch_ob and d >= k):
            return False, "short_rules_failed"
        return True, "short_ok"

    return False, "unknown_side"


def validate_generic_fast_actionable(
    payload: Dict[str, Any],
    *,
    allow_short: bool = False,
    min_confidence: float = 0.0,
    min_strength: float = 0.0,
    max_signal_age_seconds: float = 60.0,
    require_entry_reason: bool = True,
) -> Tuple[bool, str]:
    """Confirm a generic fast payload still has an actionable entry snapshot."""
    sig = str(payload.get("signal") or "").lower()
    if sig == "buy":
        side = "long"
    elif sig == "sell":
        side = "short"
    else:
        side = normalize_perp_side(sig)
    if not side:
        return False, "not_actionable_signal"

    ind = payload.get("indicators") or {}
    if not isinstance(ind, dict):
        return False, "missing_indicators"

    skip = str(ind.get("skip_reason") or "").strip().lower()
    if skip:
        return False, f"skip_reason:{skip}"

    if require_entry_reason and not str(ind.get("entry_reason") or "").strip():
        return False, "missing_entry_reason"

    try:
        conf = float(payload.get("confidence") or 0)
        strength = float(payload.get("strength") or 0)
    except (TypeError, ValueError):
        return False, "invalid_confidence_or_strength"

    if conf < float(min_confidence):
        return False, f"confidence_{conf:.2f}_lt_{min_confidence:.2f}"
    if strength < float(min_strength):
        return False, f"strength_{strength:.2f}_lt_{min_strength:.2f}"

    if side == "short" and not allow_short:
        return False, "short_not_allowed"

    age = signal_age_seconds(payload)
    if age is not None and age > max_signal_age_seconds:
        return False, f"stale_fast_signal age={age:.0f}s"

    return True, f"{side}_ok"


def validate_orb_actionable(
    payload: Dict[str, Any],
    *,
    allow_short: bool = True,
    min_reward_risk: float = 2.0,
    max_signal_age_seconds: float = 960.0,
) -> Tuple[bool, str]:
    """Confirm ORB fast payload still has a valid breakout+retest entry."""
    sig = str(payload.get("signal") or "").lower()
    side = normalize_perp_side(sig)
    if not side:
        return False, "not_actionable_signal"

    ind = payload.get("indicators") or {}
    if not isinstance(ind, dict):
        return False, "missing_indicators"

    skip = str(ind.get("skip_reason") or "").strip().lower()
    if skip in {"session_entry_taken", "regime_blocked"} or skip.startswith("regime_blocked:"):
        return False, f"skip_reason:{skip or 'blocked'}"

    session_state = str(ind.get("session_state") or "").strip().lower()
    if session_state != "signal":
        return False, f"session_state_{session_state or 'unknown'}"

    if not ind.get("breakout_valid") or not ind.get("retest_valid"):
        return False, "breakout_or_retest_invalid"

    direction = str(ind.get("direction") or "").strip().lower()
    if direction and direction != side:
        return False, f"direction_mismatch_{direction}_vs_{side}"

    entry_reason = str(ind.get("entry_reason") or "").strip()
    if not entry_reason:
        return False, "missing_entry_reason"

    try:
        rr = float(ind.get("reward_risk") or 0)
    except (TypeError, ValueError):
        rr = 0.0
    if rr < float(min_reward_risk):
        return False, f"reward_risk_{rr:.2f}_lt_{min_reward_risk:.2f}"

    if side == "short" and not allow_short:
        return False, "short_not_allowed"

    age = signal_age_seconds(payload)
    if age is not None and age > max_signal_age_seconds:
        return False, f"stale_fast_signal age={age:.0f}s"

    return True, f"{side}_ok"


async def clear_fast_signal(
    redis_client: Any,
    venue: str,
    symbol: str,
    *,
    strategy_key: str = STRATEGY_KEY,
) -> bool:
    if redis_client is None:
        return False
    key = redis_key(venue, symbol, strategy_key=strategy_key)
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
    strategy_key: str = STRATEGY_KEY,
) -> bool:
    if redis_client is None:
        return False
    key = redis_key(venue, symbol, strategy_key=strategy_key)
    sig_lc = str(signal or "hold").lower()
    if sig_lc in {"hold", ""}:
        return True

    payload = _normalize_payload(
        signal,
        confidence,
        strength,
        indicators,
        strategy_key=strategy_key,
    )
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
    *,
    strategy_key: str = STRATEGY_KEY,
) -> Optional[Dict[str, Any]]:
    if redis_client is None:
        return None
    key = redis_key(venue, symbol, strategy_key=strategy_key)
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


def merge_fast_spot_signal_into_signals(
    signals_data: Dict[str, Any],
    fast_payload: Dict[str, Any],
    *,
    strategy_key: Optional[str] = None,
) -> bool:
    """
    Overlay a Redis fast-lane BUY into strategy-service snapshot so spot
    standalone entry sees the strategy without waiting on full consensus.
    """
    sig = str(fast_payload.get("signal", "")).lower()
    if sig not in {"buy", "long"}:
        return False
    strat_name = str(strategy_key or fast_payload.get("strategy") or STRATEGY_KEY).strip().lower()
    if not strat_name:
        return False
    ind = fast_payload.get("indicators") or {}
    if not isinstance(ind, dict):
        ind = {}
    strategies = signals_data.setdefault("strategies", {})
    if not isinstance(strategies, dict):
        return False
    strategies[strat_name] = {
        "signal": "buy",
        "confidence": float(fast_payload.get("confidence") or 0),
        "strength": float(fast_payload.get("strength") or 0),
        "market_regime": ind.get("market_regime") or signals_data.get("market_regime"),
        "timestamp": fast_payload.get("analyzed_at"),
        "state": {"indicators": ind},
    }
    return True


def spot_signals_data_from_fast_payload(
    fast_payload: Dict[str, Any],
    exchange: str,
    pair: str,
    *,
    strategy_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a minimal spot signal envelope from a validated Redis fast payload."""
    strat_name = str(strategy_key or fast_payload.get("strategy") or STRATEGY_KEY).strip().lower()
    ind = fast_payload.get("indicators") or {}
    if not isinstance(ind, dict):
        ind = {}
    sig = str(fast_payload.get("signal") or "").strip().lower()
    if sig == "long":
        sig = "buy"
    elif sig == "short":
        sig = "sell"
    conf = float(fast_payload.get("confidence") or 0)
    strength = float(fast_payload.get("strength") or 0)
    analyzed_at = fast_payload.get("analyzed_at") or datetime.now(timezone.utc).isoformat()
    regime = str(
        ind.get("stable_regime")
        or ind.get("market_regime")
        or fast_payload.get("stable_regime")
        or fast_payload.get("market_regime")
        or "unknown"
    ).lower()
    strategy_data = {
        "signal": sig,
        "confidence": conf,
        "strength": strength,
        "market_regime": regime,
        "timestamp": analyzed_at,
        "state": {"indicators": ind},
    }
    return {
        "exchange": str(exchange or "").lower(),
        "pair": str(pair or ""),
        "timestamp": analyzed_at,
        "market_regime": regime,
        "stable_regime": regime,
        "consensus": {
            "signal": "hold",
            "confidence": 0.0,
            "agreement": 0.0,
            "stable_regime": regime,
            "primary_override": False,
            "sell_veto_max": 0.0,
        },
        "strategies": {strat_name: strategy_data},
        "fast_signal": {
            "source": "redis",
            "strategy": strat_name,
            "analyzed_at": analyzed_at,
        },
    }


def merge_rsi_stoch_spot_buy_into_signals(
    signals_data: Dict[str, Any],
    fast_payload: Dict[str, Any],
) -> bool:
    """Backward-compatible RSI/Stoch spot fast-lane merge."""
    return merge_fast_spot_signal_into_signals(
        signals_data,
        fast_payload,
        strategy_key=STRATEGY_KEY,
    )


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
        "coin": _normalize_hyperliquid_symbol(coin),
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
