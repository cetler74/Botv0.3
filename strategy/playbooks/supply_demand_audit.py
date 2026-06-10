"""Build and publish supply/demand 3-step audit rows."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

import httpx


def build_supply_demand_audit_payload(
    venue: str,
    symbol: str,
    strategy_result: Dict[str, Any],
    *,
    source: str = "strategy-service",
) -> Optional[Dict[str, Any]]:
    indicators = ((strategy_result or {}).get("state") or {}).get("indicators") or {}
    if not isinstance(indicators, dict) or indicators.get("setup") != "supply_demand_3step":
        return None
    if indicators.get("step1_pass") is None and not indicators.get("step1_reason"):
        return None
    record = {
        "validated_swings": indicators.get("validated_swings") or [],
        "active_zones": indicators.get("active_zones") or [],
        "retest_zone": indicators.get("retest_zone"),
        "skip_reason": indicators.get("skip_reason"),
    }
    candle_ts = None
    swings = indicators.get("validated_swings") or []
    if swings and isinstance(swings[-1], dict):
        candle_ts = swings[-1].get("timestamp")
    return {
        "log_ts": (strategy_result or {}).get("timestamp") or datetime.utcnow().isoformat(),
        "venue": venue,
        "symbol": symbol,
        "asset_class": indicators.get("asset_class"),
        "timeframe_mode": indicators.get("timeframe_mode"),
        "structure_timeframe": indicators.get("structure_timeframe"),
        "entry_timeframe": indicators.get("entry_timeframe"),
        "trend_direction": indicators.get("trend_direction"),
        "signal": (strategy_result or {}).get("signal") or "hold",
        "step1_pass": indicators.get("step1_pass"),
        "step2_pass": indicators.get("step2_pass"),
        "step3_pass": indicators.get("step3_pass"),
        "step1_reason": indicators.get("step1_reason"),
        "step2_reason": indicators.get("step2_reason"),
        "step3_reason": indicators.get("step3_reason"),
        "entry_reason": indicators.get("entry_reason"),
        "reward_risk": indicators.get("reward_risk"),
        "entry_price": indicators.get("entry_price"),
        "stop_hint": indicators.get("stop_hint"),
        "target_hint": indicators.get("target_hint"),
        "record": record,
        "candle_ts": candle_ts,
        "source": source,
    }


async def persist_supply_demand_audit(
    database_service_url: str,
    venue: str,
    symbol: str,
    strategy_result: Dict[str, Any],
    *,
    source: str = "strategy-service",
) -> None:
    payload = build_supply_demand_audit_payload(venue, symbol, strategy_result, source=source)
    if not payload:
        return
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            await client.post(
                f"{database_service_url.rstrip('/')}/api/v1/analytics/supply-demand-analysis-logs",
                json=payload,
            )
    except Exception:
        pass
