"""Pure Hyperliquid paper balance math (shared by database-service and tests)."""

from __future__ import annotations

from typing import Dict


def compute_hyperliquid_balance_amounts(
    starting_balance_usd: float,
    total_realized_pnl: float,
    unrealized_pnl: float,
    margin_used: float,
    daily_realized_pnl: float = 0.0,
) -> Dict[str, float]:
    starting = float(starting_balance_usd)
    total_pnl = float(total_realized_pnl)
    unrealized = float(unrealized_pnl)
    margin = float(margin_used)
    equity = starting + total_pnl + unrealized
    available = max(0.0, starting + total_pnl - margin)
    return {
        "balance": equity,
        "available_balance": available,
        "equity": equity,
        "margin_used": margin,
        "starting_balance": starting,
        "total_pnl": total_pnl,
        "daily_pnl": float(daily_realized_pnl),
        "unrealized_pnl": unrealized,
        "balance_source": "paper_derived",
    }
