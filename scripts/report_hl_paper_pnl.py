#!/usr/bin/env python3
"""Hyperliquid paper-perps PnL report (multi-dimensional).

Prints the same tables that drove the 2026-05-27 profit-plan analysis:
  - Aggregate (closed/open, realized PnL, fees, win rate, profit factor,
    avg win/loss, hold-time asymmetry)
  - By strategy
  - By side
  - By regime x side
  - By exit-reason bucket
  - By UTC hour
  - By coin (top losers/winners)

Pulls from database-service (default http://localhost:8002) so it works
both on the host and inside the docker network when invoked with the
appropriate --database-url.

Usage:
  python3 scripts/report_hl_paper_pnl.py
  python3 scripts/report_hl_paper_pnl.py --hours 24
  python3 scripts/report_hl_paper_pnl.py --hours 168 --limit 1000
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Helpers


def _parse_ts(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def _exit_bucket(reason: str) -> str:
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


def _fetch_trades(base_url: str, limit: int, status: str = "CLOSED") -> List[Dict[str, Any]]:
    """Page through /api/v1/perps/paper-trades and return up to ``limit`` rows."""
    out: List[Dict[str, Any]] = []
    page_size = min(limit, 1000)
    offset = 0
    while len(out) < limit:
        params = {"status": status, "limit": page_size, "offset": offset}
        url = (
            base_url.rstrip("/")
            + "/api/v1/perps/paper-trades?"
            + urllib.parse.urlencode(params)
        )
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = json.load(resp)
        chunk = data.get("trades", []) if isinstance(data, dict) else []
        if not chunk:
            break
        out.extend(chunk)
        if len(chunk) < page_size:
            break
        offset += page_size
    return out[:limit]


def _profit_factor(wins_sum: float, losses_sum: float) -> Optional[float]:
    if losses_sum >= 0:
        return None
    return wins_sum / abs(losses_sum) if abs(losses_sum) > 0 else None


def _fmt_pf(pf: Optional[float]) -> str:
    return f"{pf:.2f}" if pf is not None else "n/a"


def _hold_minutes(trade: Dict[str, Any]) -> Optional[float]:
    entry = _parse_ts(trade.get("entry_time"))
    exit_dt = _parse_ts(trade.get("exit_time"))
    if not entry or not exit_dt:
        return None
    return max(0.0, (exit_dt - entry).total_seconds() / 60.0)


# ---------------------------------------------------------------------------
# Aggregations


def _aggregate(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    closed = [r for r in rows if str(r.get("status") or "").upper() == "CLOSED"]
    realized = sum(float(r.get("realized_pnl") or 0.0) for r in closed)
    fees = sum(float(r.get("fees") or 0.0) for r in closed)
    funding = sum(float(r.get("funding") or 0.0) for r in closed)
    wins = [r for r in closed if float(r.get("realized_pnl") or 0.0) > 0]
    losses = [r for r in closed if float(r.get("realized_pnl") or 0.0) < 0]
    wins_sum = sum(float(r.get("realized_pnl") or 0.0) for r in wins)
    losses_sum = sum(float(r.get("realized_pnl") or 0.0) for r in losses)
    win_holds = [m for m in (_hold_minutes(r) for r in wins) if m is not None]
    loss_holds = [m for m in (_hold_minutes(r) for r in losses) if m is not None]
    return {
        "closed": len(closed),
        "realized": realized,
        "fees": fees,
        "funding": funding,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": (100.0 * len(wins) / len(closed)) if closed else 0.0,
        "avg_win": (wins_sum / len(wins)) if wins else 0.0,
        "avg_loss": (losses_sum / len(losses)) if losses else 0.0,
        "profit_factor": _profit_factor(wins_sum, losses_sum),
        "avg_win_hold_min": (sum(win_holds) / len(win_holds)) if win_holds else 0.0,
        "avg_loss_hold_min": (sum(loss_holds) / len(loss_holds)) if loss_holds else 0.0,
    }


def _group_rows(rows: List[Dict[str, Any]], key) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in rows:
        out[key(r)].append(r)
    return out


def _print_grouped(
    title: str,
    rows: List[Dict[str, Any]],
    key,
    sort_by_pnl: bool = True,
    show_max: int = 50,
) -> None:
    groups = _group_rows(rows, key)
    items = []
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
                "n_closed": len(closed),
                "n_open": len(group) - len(closed),
                "pnl": pnl,
                "wr": (100.0 * len(wins) / len(closed)) if closed else 0.0,
                "avg": (pnl / len(closed)) if closed else 0.0,
                "pf": _profit_factor(wins_sum, losses_sum),
            }
        )
    items.sort(key=lambda x: (x["pnl"] if sort_by_pnl else x["label"]))
    print(f"\n## {title}")
    header = (
        f"  {'label':<28}  {'closed':>6}  {'open':>4}  {'pnl':>10}  "
        f"{'avg':>8}  {'wr%':>6}  {'pf':>6}"
    )
    print(header)
    print(f"  {'-' * 26:<28}  {'-' * 6:>6}  {'-' * 4:>4}  {'-' * 10:>10}  "
          f"{'-' * 8:>8}  {'-' * 6:>6}  {'-' * 6:>6}")
    for item in items[:show_max]:
        print(
            f"  {str(item['label'])[:28]:<28}  {item['n_closed']:>6}  "
            f"{item['n_open']:>4}  {item['pnl']:>+10.2f}  {item['avg']:>+8.3f}  "
            f"{item['wr']:>6.1f}  {_fmt_pf(item['pf']):>6}"
        )


# ---------------------------------------------------------------------------
# Main


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Hyperliquid paper-perps PnL report"
    )
    parser.add_argument(
        "--database-url",
        default="http://localhost:8002",
        help="database-service base URL",
    )
    parser.add_argument(
        "--hours",
        type=float,
        default=0.0,
        help="Optional lookback window (0 = all time)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=2000,
        help="Maximum number of trades to fetch (each status)",
    )
    args = parser.parse_args()

    closed = _fetch_trades(args.database_url, args.limit, status="CLOSED")
    open_rows = _fetch_trades(args.database_url, args.limit, status="OPEN")
    rows = closed + open_rows

    if args.hours > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=args.hours)
        rows = [
            r for r in rows
            if (
                _parse_ts(r.get("entry_time"))
                or datetime.now(timezone.utc)
            ) >= cutoff
        ]

    if not rows:
        print("No paper trades found.")
        return 0

    agg = _aggregate(rows)
    first_entry = min(
        (_parse_ts(r.get("entry_time")) for r in rows if r.get("entry_time")),
        default=None,
    )
    last_entry = max(
        (_parse_ts(r.get("entry_time")) for r in rows if r.get("entry_time")),
        default=None,
    )

    print("# Hyperliquid Paper Perps PnL Report")
    print(
        f"  closed={agg['closed']}  open={len(open_rows)}  "
        f"realized=${agg['realized']:+.2f}  fees=${agg['fees']:.2f}  "
        f"funding=${agg['funding']:+.2f}"
    )
    print(
        f"  WR={agg['win_rate']:.1f}%  avg_win=${agg['avg_win']:+.2f}  "
        f"avg_loss=${agg['avg_loss']:+.2f}  PF={_fmt_pf(agg['profit_factor'])}"
    )
    print(
        f"  avg_win_hold={agg['avg_win_hold_min']:.1f}min  "
        f"avg_loss_hold={agg['avg_loss_hold_min']:.1f}min"
    )
    if first_entry and last_entry:
        print(f"  window: {first_entry.isoformat()} → {last_entry.isoformat()}")

    _print_grouped(
        "By strategy",
        rows,
        lambda r: (r.get("source_strategy") or "unknown").strip().lower(),
    )
    _print_grouped(
        "By position side",
        rows,
        lambda r: (r.get("position_side") or "unknown").strip().lower(),
    )
    _print_grouped(
        "By regime × side",
        rows,
        lambda r: (
            f"{((r.get('metadata') or {}).get('market_regime') or 'unknown')}"
            f"/{(r.get('position_side') or 'unknown')}"
        ).lower(),
    )
    _print_grouped(
        "By exit-reason bucket",
        rows,
        lambda r: _exit_bucket(r.get("exit_reason") or ""),
    )
    _print_grouped(
        "By UTC hour (entry)",
        rows,
        lambda r: (
            f"{_parse_ts(r.get('entry_time')).astimezone(timezone.utc).hour:02d}"
            if _parse_ts(r.get("entry_time"))
            else "??"
        ),
        sort_by_pnl=False,
    )
    _print_grouped(
        "By coin",
        rows,
        lambda r: (r.get("coin") or "unknown").strip().upper(),
        show_max=40,
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
