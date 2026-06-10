"""Build and publish EMA50 breakout-pullback audit rows."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

import httpx


def build_ema50_breakout_pullback_audit_payload(
    venue: str,
    symbol: str,
    strategy_result: Dict[str, Any],
    *,
    source: str = "strategy-service",
) -> Optional[Dict[str, Any]]:
    indicators = ((strategy_result or {}).get("state") or {}).get("indicators") or {}
    if not isinstance(indicators, dict) or indicators.get("setup") != "ema50_breakout_pullback":
        return None
    if indicators.get("setup_state") is None and not indicators.get("breakout_reason"):
        return None
    record = {
        "predominant_pct": indicators.get("predominant_pct"),
        "pullback_count": indicators.get("pullback_count"),
        "entry_candle_range": indicators.get("entry_candle_range"),
        "avg_candle_range": indicators.get("avg_candle_range"),
        "chandelier_period": indicators.get("chandelier_period"),
        "chandelier_atr_mult": indicators.get("chandelier_atr_mult"),
        "skip_reason": indicators.get("skip_reason"),
    }
    candle_ts = indicators.get("candle_ts")
    return {
        "log_ts": (strategy_result or {}).get("timestamp") or datetime.utcnow().isoformat(),
        "venue": venue,
        "symbol": symbol,
        "asset_class": indicators.get("asset_class"),
        "timeframe": indicators.get("primary_timeframe") or "4h",
        "setup_state": indicators.get("setup_state"),
        "direction": indicators.get("direction"),
        "ema50_side": indicators.get("ema50_side"),
        "signal": (strategy_result or {}).get("signal") or "hold",
        "breakout_pass": indicators.get("breakout_pass"),
        "pullback_pass": indicators.get("pullback_pass"),
        "trigger_pass": indicators.get("trigger_pass"),
        "breakout_reason": indicators.get("breakout_reason"),
        "pullback_reason": indicators.get("pullback_reason"),
        "trigger_reason": indicators.get("trigger_reason"),
        "swing_level": indicators.get("swing_level"),
        "ema50_value": indicators.get("ema50_value"),
        "entry_reason": indicators.get("entry_reason"),
        "reward_risk": indicators.get("reward_risk"),
        "entry_price": indicators.get("entry_price"),
        "stop_hint": indicators.get("stop_hint"),
        "target_hint": indicators.get("target_hint"),
        "invalidation_reason": indicators.get("invalidation_reason"),
        "record": record,
        "candle_ts": candle_ts,
        "source": source,
    }


async def persist_ema50_breakout_pullback_audit(
    database_service_url: str,
    venue: str,
    symbol: str,
    strategy_result: Dict[str, Any],
    *,
    source: str = "strategy-service",
) -> None:
    payload = build_ema50_breakout_pullback_audit_payload(venue, symbol, strategy_result, source=source)
    if not payload:
        return
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                f"{database_service_url.rstrip('/')}/api/v1/analytics/ema50-breakout-pullback-analysis-logs",
                json=payload,
            )
    except Exception:
        pass
