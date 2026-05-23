"""
Hyperliquid clearinghouse balance parsing (read-only).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import httpx

HYPERLIQUID_INFO_URL = "https://api.hyperliquid.xyz/info"


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_clearinghouse_state(payload: Dict[str, Any]) -> Dict[str, float]:
    """Map Hyperliquid clearinghouseState to internal balance fields."""
    margin = payload.get("marginSummary") or {}
    cross = payload.get("crossMarginSummary") or {}
    account_value = _safe_float(
        margin.get("accountValue") or cross.get("accountValue") or payload.get("accountValue")
    )
    total_margin_used = _safe_float(
        margin.get("totalMarginUsed") or cross.get("totalMarginUsed") or payload.get("totalMarginUsed")
    )
    withdrawable = _safe_float(payload.get("withdrawable"))
    if withdrawable <= 0 and account_value > 0:
        withdrawable = max(0.0, account_value - total_margin_used)

    unrealized = 0.0
    for key in ("assetPositions", "crossAssetPositions"):
        positions = payload.get(key) or []
        if not isinstance(positions, list):
            continue
        for row in positions:
            if not isinstance(row, dict):
                continue
            pos = row.get("position") if isinstance(row.get("position"), dict) else row
            unrealized += _safe_float((pos or {}).get("unrealizedPnl"))

    return {
        "balance": account_value,
        "available_balance": withdrawable,
        "margin_used": total_margin_used,
        "unrealized_pnl": unrealized,
        "total_pnl": unrealized,
        "daily_pnl": 0.0,
    }


async def fetch_hyperliquid_wallet_balance(
    wallet_address: str,
    client: Optional[httpx.AsyncClient] = None,
) -> Dict[str, float]:
    """Fetch live HL wallet equity via clearinghouseState (read-only)."""
    user = str(wallet_address or "").strip()
    if not user:
        raise ValueError("wallet_address is required for live Hyperliquid balance")

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=15.0)
    try:
        response = await client.post(
            HYPERLIQUID_INFO_URL,
            json={"type": "clearinghouseState", "user": user},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("unexpected clearinghouseState payload")
        return parse_clearinghouse_state(payload)
    finally:
        if owns_client:
            await client.aclose()
