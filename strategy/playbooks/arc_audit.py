"""Build and publish ARC daytrade audit rows."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

import httpx


def build_arc_audit_payload(
    venue: str,
    symbol: str,
    strategy_result: Dict[str, Any],
    *,
    source: str = "strategy-service",
) -> Optional[Dict[str, Any]]:
    indicators = ((strategy_result or {}).get("state") or {}).get("indicators") or {}
    if not isinstance(indicators, dict) or indicators.get("setup") != "arc_daytrade":
        return None
    if indicators.get("area_pass") is None and not indicators.get("area_reason"):
        return None
    record = {
        "signal_candle": indicators.get("signal_candle") or {},
        "gap_info": indicators.get("gap_info") or {},
        "side_intent": indicators.get("side_intent"),
        "skip_reason": indicators.get("skip_reason"),
    }
    candle_ts = None
    sig = indicators.get("signal_candle") or {}
    if isinstance(sig, dict):
        candle_ts = sig.get("timestamp")
    return {
        "log_ts": (strategy_result or {}).get("timestamp") or datetime.utcnow().isoformat(),
        "venue": venue,
        "symbol": symbol,
        "asset_class": indicators.get("asset_class"),
        "box_high": indicators.get("box_high"),
        "box_low": indicators.get("box_low"),
        "swing_high": indicators.get("swing_high"),
        "swing_low": indicators.get("swing_low"),
        "range_size": indicators.get("range_size"),
        "range_pct_move": indicators.get("range_pct_move"),
        "range_geometry_tag": indicators.get("range_geometry_tag"),
        "zone": indicators.get("zone"),
        "zone_level": indicators.get("zone_level"),
        "area_pass": indicators.get("area_pass"),
        "range_pass": indicators.get("range_pass"),
        "candle_pass": indicators.get("candle_pass"),
        "area_reason": indicators.get("area_reason"),
        "range_reason": indicators.get("range_reason"),
        "candle_reason": indicators.get("candle_reason"),
        "signal": (strategy_result or {}).get("signal") or "hold",
        "setup_state": indicators.get("setup_state"),
        "entry_price": indicators.get("entry_price"),
        "stop_hint": indicators.get("stop_hint"),
        "target_hint": indicators.get("target_hint"),
        "target_50": indicators.get("target_50"),
        "target_100": indicators.get("target_100"),
        "reward_risk": indicators.get("reward_risk"),
        "invalidation_reason": indicators.get("invalidation_reason"),
        "entry_reason": indicators.get("entry_reason"),
        "record": record,
        "candle_ts": candle_ts,
        "source": source,
    }


async def persist_arc_audit(
    database_service_url: str,
    venue: str,
    symbol: str,
    strategy_result: Dict[str, Any],
    *,
    source: str = "strategy-service",
) -> None:
    payload = build_arc_audit_payload(venue, symbol, strategy_result, source=source)
    if not payload:
        return
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            await client.post(
                f"{database_service_url.rstrip('/')}/api/v1/analytics/arc-analysis-logs",
                json=payload,
            )
    except Exception:
        pass
