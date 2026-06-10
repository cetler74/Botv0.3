"""Spot trading PnL aggregation for dashboard (rolling 24h window)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

try:
    from perp_paper_pnl_report import (
        aggregate_trades,
        exit_bucket,
        filter_rows_by_hours,
        group_breakdown,
        trade_window_timestamp,
    )
except ImportError:  # pragma: no cover - local dev imports
    from core.perp_paper_pnl_report import (
        aggregate_trades,
        exit_bucket,
        filter_rows_by_hours,
        group_breakdown,
        trade_window_timestamp,
    )


def _spot_quote_for_exchange(exchange: str) -> str:
    return "USD" if str(exchange or "").lower() == "cryptocom" else "USDC"


def _display_pair(raw: str, exchange: str = "") -> str:
    pair = str(raw or "").strip().upper()
    if not pair:
        return ""
    if "/" in pair:
        return pair
    quote = _spot_quote_for_exchange(exchange)
    for suffix in ("USDC", "USDT", "USD"):
        if pair.endswith(suffix) and len(pair) > len(suffix):
            return f"{pair[:-len(suffix)]}/{suffix}"
    return f"{pair}/{quote}" if exchange else pair


def normalize_spot_trade_row(trade: Dict[str, Any]) -> Dict[str, Any]:
    exchange = str(trade.get("exchange") or trade.get("exchange_name") or "unknown").strip().lower()
    pair = _display_pair(
        str(trade.get("pair") or trade.get("symbol") or trade.get("trading_pair") or ""),
        exchange,
    )
    fees = float(trade.get("fees") or 0.0)
    if not fees:
        fees = float(trade.get("entry_fee_amount") or 0.0) + float(trade.get("exit_fee_amount") or 0.0)
    return {
        "status": trade.get("status"),
        "realized_pnl": trade.get("realized_pnl") if trade.get("realized_pnl") is not None else trade.get("pnl"),
        "fees": fees,
        "funding": 0.0,
        "entry_time": trade.get("entry_time") or trade.get("entryTime"),
        "exit_time": trade.get("exit_time") or trade.get("exitTime"),
        "source_strategy": str(trade.get("strategy") or trade.get("source_strategy") or "unknown").strip().lower(),
        "position_side": "long",
        "coin": pair or "unknown",
        "exit_reason": trade.get("exit_reason") or trade.get("exitReason") or "",
        "metadata": trade.get("metadata") or {},
        "exchange": exchange,
        "pair": pair or "unknown",
    }


def build_spot_pnl_report(
    open_trades: List[Dict[str, Any]],
    closed_trades: List[Dict[str, Any]],
    *,
    hours: float = 24.0,
    limit: int = 2000,
) -> Dict[str, Any]:
    """Build JSON-serializable spot PnL report from open and closed trade rows."""
    rows = [normalize_spot_trade_row(t) for t in (open_trades or []) + (closed_trades or [])]
    if limit > 0:
        rows = rows[:limit]
    filtered = filter_rows_by_hours(rows, hours)
    now = datetime.now(timezone.utc)
    if hours > 0:
        window_start = (now - timedelta(hours=hours)).isoformat()
        window_end = now.isoformat()
    else:
        stamps = [trade_window_timestamp(r) for r in filtered]
        stamps = [t for t in stamps if t is not None]
        window_start = min(stamps).isoformat() if stamps else None
        window_end = max(stamps).isoformat() if stamps else None

    return {
        "hours": hours,
        "limit": limit,
        "marketType": "spot",
        "tradeCount": len(filtered),
        "windowStart": window_start,
        "windowEnd": window_end,
        "aggregate": aggregate_trades(filtered),
        "breakdowns": {
            "strategy": group_breakdown(
                filtered,
                lambda r: str(r.get("source_strategy") or "unknown").strip().lower(),
            ),
            "exchange": group_breakdown(
                filtered,
                lambda r: str(r.get("exchange") or "unknown").strip().lower(),
                sort_by_pnl=False,
            ),
            "exitBucket": group_breakdown(
                filtered,
                lambda r: exit_bucket(str(r.get("exit_reason") or "")),
            ),
            "utcHour": group_breakdown(
                filtered,
                lambda r: (
                    f"{trade_window_timestamp(r).astimezone(timezone.utc).hour:02d}"
                    if trade_window_timestamp(r)
                    else "??"
                ),
                sort_by_pnl=False,
            ),
            "pair": group_breakdown(
                filtered,
                lambda r: str(r.get("pair") or "unknown").strip().upper(),
                max_rows=50,
            ),
        },
    }
