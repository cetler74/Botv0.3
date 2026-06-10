"""Build and publish dual-SMA daytrade audit rows."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

import httpx


def build_dual_sma_audit_payload(
    venue: str,
    symbol: str,
    strategy_result: Dict[str, Any],
    *,
    source: str = "strategy-service",
) -> Optional[Dict[str, Any]]:
    indicators = ((strategy_result or {}).get("state") or {}).get("indicators") or {}
    if not isinstance(indicators, dict) or indicators.get("setup") != "dual_sma_daytrade":
        return None
    if indicators.get("daily_pass") is None and not indicators.get("daily_reason"):
        return None
    record = {
        "validated_swings_15m": indicators.get("validated_swings_15m") or [],
        "validated_swings_5m": indicators.get("validated_swings_5m") or [],
        "skip_reason": indicators.get("skip_reason"),
        "mean_reversion_tag": indicators.get("mean_reversion_tag"),
        "side_intent": indicators.get("side_intent"),
    }
    candle_ts = None
    swings = indicators.get("validated_swings_5m") or indicators.get("validated_swings_15m") or []
    if swings and isinstance(swings[-1], dict):
        candle_ts = swings[-1].get("timestamp")
    return {
        "log_ts": (strategy_result or {}).get("timestamp") or datetime.utcnow().isoformat(),
        "venue": venue,
        "symbol": symbol,
        "asset_class": indicators.get("asset_class"),
        "daily_bias": indicators.get("daily_bias"),
        "trend_15m": indicators.get("trend_15m"),
        "entry_signal_5m": indicators.get("entry_signal_5m"),
        "signal": (strategy_result or {}).get("signal") or "hold",
        "sma20_slope": indicators.get("sma20_slope"),
        "price_vs_sma20_pct": indicators.get("price_vs_sma20_pct"),
        "price_vs_sma200_pct": indicators.get("price_vs_sma200_pct"),
        "extension_distance_pct": indicators.get("extension_distance_pct"),
        "daily_gap_vs_sma200_pct": indicators.get("daily_gap_vs_sma200_pct"),
        "sma200_flat": indicators.get("sma200_flat"),
        "squeeze_detected": indicators.get("squeeze_detected"),
        "daily_pass": indicators.get("daily_pass"),
        "confirm_15m_pass": indicators.get("confirm_15m_pass"),
        "entry_5m_pass": indicators.get("entry_5m_pass"),
        "precision_pass": indicators.get("precision_pass"),
        "daily_reason": indicators.get("daily_reason"),
        "confirm_15m_reason": indicators.get("confirm_15m_reason"),
        "entry_5m_reason": indicators.get("entry_5m_reason"),
        "precision_reason": indicators.get("precision_reason"),
        "entry_reason": indicators.get("entry_reason"),
        "reward_risk": indicators.get("reward_risk"),
        "entry_price": indicators.get("entry_price"),
        "stop_hint": indicators.get("stop_hint"),
        "target_hint": indicators.get("target_hint"),
        "record": record,
        "candle_ts": candle_ts,
        "source": source,
    }


async def persist_dual_sma_audit(
    database_service_url: str,
    venue: str,
    symbol: str,
    strategy_result: Dict[str, Any],
    *,
    source: str = "strategy-service",
) -> None:
    payload = build_dual_sma_audit_payload(venue, symbol, strategy_result, source=source)
    if not payload:
        return
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            await client.post(
                f"{database_service_url.rstrip('/')}/api/v1/analytics/dual-sma-analysis-logs",
                json=payload,
            )
    except Exception:
        pass
