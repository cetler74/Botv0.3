"""Build and publish ORB 5m scalp audit rows."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

import httpx


def build_orb_5m_scalp_audit_payload(
    venue: str,
    symbol: str,
    strategy_result: Dict[str, Any],
    *,
    source: str = "strategy-service",
) -> Optional[Dict[str, Any]]:
    indicators = ((strategy_result or {}).get("state") or {}).get("indicators") or {}
    if not isinstance(indicators, dict) or indicators.get("setup") != "orb_5m_scalp":
        return None
    if indicators.get("session_state") is None and not indicators.get("or_high"):
        return None
    record = {
        "or_candle_ts": indicators.get("or_candle_ts"),
        "breakout_candle_ts": indicators.get("breakout_candle_ts"),
        "retest_candle_ts": indicators.get("retest_candle_ts"),
        "session_date": indicators.get("session_date"),
        "skip_reason": indicators.get("skip_reason"),
    }
    candle_ts = indicators.get("candle_ts")
    return {
        "log_ts": (strategy_result or {}).get("timestamp") or datetime.utcnow().isoformat(),
        "venue": venue,
        "symbol": symbol,
        "asset_class": indicators.get("asset_class"),
        "timeframe": indicators.get("entry_timeframe") or "5m",
        "setup_state": indicators.get("session_state"),
        "direction": indicators.get("direction"),
        "signal": (strategy_result or {}).get("signal") or "hold",
        "or_high": indicators.get("or_high"),
        "or_low": indicators.get("or_low"),
        "or_mid": indicators.get("or_mid"),
        "breakout_valid": indicators.get("breakout_valid"),
        "retest_valid": indicators.get("retest_valid"),
        "breakout_reason": indicators.get("breakout_reason"),
        "retest_reason": indicators.get("retest_reason"),
        "rejection_reason": indicators.get("rejection_reason"),
        "entry_price": indicators.get("entry_price"),
        "stop_hint": indicators.get("stop_hint"),
        "target_hint": indicators.get("target_hint"),
        "reward_risk": indicators.get("reward_risk"),
        "entry_reason": indicators.get("entry_reason"),
        "invalidation_reason": indicators.get("invalidation_reason"),
        "record": record,
        "candle_ts": candle_ts,
        "source": source,
    }


async def persist_orb_5m_scalp_audit(
    database_service_url: str,
    venue: str,
    symbol: str,
    strategy_result: Dict[str, Any],
    *,
    source: str = "strategy-service",
) -> None:
    payload = build_orb_5m_scalp_audit_payload(venue, symbol, strategy_result, source=source)
    if not payload:
        return
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                f"{database_service_url.rstrip('/')}/api/v1/analytics/orb-5m-scalp-analysis-logs",
                json=payload,
            )
    except Exception:
        pass
