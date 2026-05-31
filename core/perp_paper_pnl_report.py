"""Hyperliquid paper-perps PnL aggregation (shared by CLI and API)."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional


def parse_ts(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def exit_bucket(reason: str) -> str:
    if not reason:
        return "unknown"
    if "paper_stop_loss" in reason:
        return "paper_stop_loss"
    if "max_holding_time_flat" in reason:
        return "max_hold_flat"
    if "max_holding_time_be" in reason:
        return "max_hold_be"
    if "max_holding_time_hard" in reason:
        return "max_hold_hard"
    if reason.startswith("paper_max_holding_time"):
        return "max_hold_legacy"
    if reason.startswith("paper_trailing_stop_trigger"):
        return "trailing_stop"
    if reason.startswith("paper_profit_protection_breach"):
        return "profit_protection"
    if reason.startswith("paper_overall_take_profit"):
        return "overall_take_profit"
    if reason.startswith("paper_take_profit"):
        return "take_profit"
    return reason.split("@")[0][:40]


def profit_factor(wins_sum: float, losses_sum: float) -> Optional[float]:
    if losses_sum >= 0:
        return None
    return wins_sum / abs(losses_sum) if abs(losses_sum) > 0 else None


def hold_minutes(trade: Dict[str, Any]) -> Optional[float]:
    entry = parse_ts(trade.get("entry_time"))
    exit_dt = parse_ts(trade.get("exit_time"))
    if not entry or not exit_dt:
        return None
    return max(0.0, (exit_dt - entry).total_seconds() / 60.0)


def filter_rows_by_hours(
    rows: List[Dict[str, Any]], hours: float
) -> List[Dict[str, Any]]:
    if hours <= 0:
        return rows
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    return [
        r
        for r in rows
        if (parse_ts(r.get("entry_time")) or datetime.now(timezone.utc)) >= cutoff
    ]


def aggregate_trades(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    closed = [r for r in rows if str(r.get("status") or "").upper() == "CLOSED"]
    open_count = len(rows) - len(closed)
    realized = sum(float(r.get("realized_pnl") or 0.0) for r in closed)
    fees = sum(float(r.get("fees") or 0.0) for r in closed)
    funding = sum(float(r.get("funding") or 0.0) for r in closed)
    wins = [r for r in closed if float(r.get("realized_pnl") or 0.0) > 0]
    losses = [r for r in closed if float(r.get("realized_pnl") or 0.0) < 0]
    wins_sum = sum(float(r.get("realized_pnl") or 0.0) for r in wins)
    losses_sum = sum(float(r.get("realized_pnl") or 0.0) for r in losses)
    win_holds = [m for m in (hold_minutes(r) for r in wins) if m is not None]
    loss_holds = [m for m in (hold_minutes(r) for r in losses) if m is not None]
    pf = profit_factor(wins_sum, losses_sum)
    return {
        "closed": len(closed),
        "open": open_count,
        "realized": realized,
        "fees": fees,
        "funding": funding,
        "wins": len(wins),
        "losses": len(losses),
        "winRate": (100.0 * len(wins) / len(closed)) if closed else 0.0,
        "avgWin": (wins_sum / len(wins)) if wins else 0.0,
        "avgLoss": (losses_sum / len(losses)) if losses else 0.0,
        "profitFactor": pf,
        "avgWinHoldMin": (sum(win_holds) / len(win_holds)) if win_holds else 0.0,
        "avgLossHoldMin": (sum(loss_holds) / len(loss_holds)) if loss_holds else 0.0,
    }


def _group_rows(
    rows: List[Dict[str, Any]], key: Callable[[Dict[str, Any]], str]
) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        out[key(r)].append(r)
    return out


def group_breakdown(
    rows: List[Dict[str, Any]],
    key: Callable[[Dict[str, Any]], str],
    *,
    sort_by_pnl: bool = True,
    max_rows: int = 50,
) -> List[Dict[str, Any]]:
    groups = _group_rows(rows, key)
    items: List[Dict[str, Any]] = []
    for label, group in groups.items():
        closed = [
            r for r in group if str(r.get("status") or "").upper() == "CLOSED"
        ]
        wins = [r for r in closed if float(r.get("realized_pnl") or 0.0) > 0]
        losses = [r for r in closed if float(r.get("realized_pnl") or 0.0) < 0]
        pnl = sum(float(r.get("realized_pnl") or 0.0) for r in closed)
        wins_sum = sum(float(r.get("realized_pnl") or 0.0) for r in wins)
        losses_sum = sum(float(r.get("realized_pnl") or 0.0) for r in losses)
        items.append(
            {
                "label": label,
                "nClosed": len(closed),
                "nOpen": len(group) - len(closed),
                "pnl": pnl,
                "winRate": (100.0 * len(wins) / len(closed)) if closed else 0.0,
                "avg": (pnl / len(closed)) if closed else 0.0,
                "profitFactor": profit_factor(wins_sum, losses_sum),
            }
        )
    items.sort(key=lambda x: (x["pnl"] if sort_by_pnl else x["label"]))
    return items[:max_rows]


def _trade_metadata(trade: Dict[str, Any]) -> Dict[str, Any]:
    raw = trade.get("metadata") or {}
    if isinstance(raw, str):
        import json

        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return {}
    return raw if isinstance(raw, dict) else {}


def build_paper_pnl_report(
    rows: List[Dict[str, Any]],
    *,
    hours: float = 0.0,
    limit: int = 2000,
) -> Dict[str, Any]:
    """Build JSON-serializable report from raw trade rows."""
    filtered = filter_rows_by_hours(rows, hours)
    entries = [
        parse_ts(r.get("entry_time"))
        for r in filtered
        if r.get("entry_time")
    ]
    window_start = min(entries).isoformat() if entries else None
    window_end = max(entries).isoformat() if entries else None

    return {
        "hours": hours,
        "limit": limit,
        "tradeCount": len(filtered),
        "windowStart": window_start,
        "windowEnd": window_end,
        "aggregate": aggregate_trades(filtered),
        "breakdowns": {
            "strategy": group_breakdown(
                filtered,
                lambda r: (r.get("source_strategy") or "unknown").strip().lower(),
            ),
            "positionSide": group_breakdown(
                filtered,
                lambda r: (r.get("position_side") or "unknown").strip().lower(),
            ),
            "regimeSide": group_breakdown(
                filtered,
                lambda r: (
                    f"{(_trade_metadata(r).get('market_regime') or 'unknown')}"
                    f"/{(r.get('position_side') or 'unknown')}"
                ).lower(),
            ),
            "exitBucket": group_breakdown(
                filtered,
                lambda r: exit_bucket(r.get("exit_reason") or ""),
            ),
            "utcHour": group_breakdown(
                filtered,
                lambda r: (
                    f"{parse_ts(r.get('entry_time')).astimezone(timezone.utc).hour:02d}"
                    if parse_ts(r.get("entry_time"))
                    else "??"
                ),
                sort_by_pnl=False,
            ),
            "coin": group_breakdown(
                filtered,
                lambda r: (r.get("coin") or "unknown").strip().upper(),
                max_rows=40,
            ),
        },
    }
