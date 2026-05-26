"""Long/short aware consensus for Hyperliquid perp strategies."""

from __future__ import annotations

from typing import Any, Dict, List


def normalize_perp_entry_signal(signal: str) -> str:
    """
    Canonical perpetual entry intent.

    Strategies may still return legacy spot-style values internally while being
    ported, but Hyperliquid perps must expose entry direction as long/short.
    A sell is an exit/reduce action, not a short-entry name.
    """
    sig = str(signal or "").lower().strip()
    if sig in {"long", "buy"}:
        return "long"
    if sig in {"short", "sell"}:
        return "short"
    return "hold"


def calculate_hyperliquid_consensus(
    strategy_results: Dict[str, Any],
    *,
    min_agreement: float = 50.0,
) -> Dict[str, Any]:
    """
    Weighted vote: long/short entry intent. HOLD does not dilute directional confidence.
    """
    long_weight = 0.0
    short_weight = 0.0
    long_conf = 0.0
    short_conf = 0.0
    hold_count = 0
    participating = 0

    breakdown: List[Dict[str, Any]] = []

    for name, result in strategy_results.items():
        if not isinstance(result, dict) or "error" in result:
            continue
        signal = normalize_perp_entry_signal(result.get("signal", "hold"))
        confidence = float(result.get("confidence", 0) or 0)
        strength = float(result.get("strength", 0) or 0)
        participating += 1
        breakdown.append(
            {
                "strategy": name,
                "signal": signal,
                "confidence": confidence,
                "strength": strength,
            }
        )
        if signal == "long":
            long_weight += 1.0
            long_conf += confidence
        elif signal == "short":
            short_weight += 1.0
            short_conf += confidence
        else:
            hold_count += 1

    if participating == 0:
        return {
            "signal": "hold",
            "confidence": 0.0,
            "strength": 0.0,
            "agreement": 0.0,
            "participating_strategies": 0,
            "breakdown": [],
        }

    long_pct = (long_weight / participating) * 100.0
    short_pct = (short_weight / participating) * 100.0
    avg_long = (long_conf / long_weight) if long_weight > 0 else 0.0
    avg_short = (short_conf / short_weight) if short_weight > 0 else 0.0

    signal = "hold"
    confidence = 0.0
    strength = 0.0
    agreement = 0.0

    if long_pct >= min_agreement and long_pct > short_pct:
        signal = "long"
        confidence = avg_long
        agreement = long_pct
        strength = long_pct / 100.0
    elif short_pct >= min_agreement and short_pct > long_pct:
        signal = "short"
        confidence = avg_short
        agreement = short_pct
        strength = short_pct / 100.0
    elif long_pct > short_pct and long_weight > 0:
        signal = "long"
        confidence = avg_long * (long_pct / 100.0)
        agreement = long_pct
        strength = long_pct / 100.0
    elif short_pct > long_pct and short_weight > 0:
        signal = "short"
        confidence = avg_short * (short_pct / 100.0)
        agreement = short_pct
        strength = short_pct / 100.0

    return {
        "signal": signal,
        "confidence": round(confidence, 4),
        "strength": round(strength, 4),
        "agreement": round(agreement, 2),
        "participating_strategies": participating,
        "long_votes": int(long_weight),
        "short_votes": int(short_weight),
        "hold_votes": hold_count,
        "breakdown": breakdown,
    }


def select_recommended_strategy(
    strategy_results: Dict[str, Any],
    consensus_signal: str,
) -> Dict[str, Any]:
    """Best individual strategy matching consensus direction."""
    side = normalize_perp_entry_signal(consensus_signal)
    if side not in ("long", "short"):
        return {}
    best = None
    for name, data in strategy_results.items():
        if not isinstance(data, dict) or normalize_perp_entry_signal(data.get("signal")) != side:
            continue
        conf = float(data.get("confidence", 0) or 0)
        if best is None or conf > best[0]:
            best = (conf, name, data)
    if not best:
        return {}
    _, name, data = best
    return {
        "strategy": name,
        "signal": side,
        "side": side,
        "confidence": float(data.get("confidence", 0) or 0),
        "strength": float(data.get("strength", 0) or 0),
        "reason": (data.get("state") or {}).get("indicators", {}).get("entry_reason", ""),
    }
