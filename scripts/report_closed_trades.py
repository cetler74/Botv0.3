#!/usr/bin/env python3
"""Closed-trade PnL report by strategy, entry regime, and exit reason.

Usage:
  python3 scripts/report_closed_trades.py
  python3 scripts/report_closed_trades.py --hours 24
  python3 scripts/report_closed_trades.py --hours 168 --database-url http://localhost:8002
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional


def _parse_ts(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _entry_regime(trade: Dict[str, Any]) -> str:
    er = trade.get("entry_reason") or ""
    m = re.search(r"stable_regime=([^,\]]+)", er)
    return m.group(1).strip() if m else "unknown"


def _exit_bucket(reason: str) -> str:
    if not reason:
        return "unknown"
    if reason.startswith("stagnant"):
        return "stagnant_loser"
    if reason.startswith("stop_loss"):
        return "stop_loss"
    if reason.startswith("profit_protection"):
        return "profit_protection"
    if reason.startswith("trailing_stop"):
        return "trailing_stop"
    if reason.startswith("overall_take"):
        return "take_profit"
    if "max_hold" in reason:
        return "max_hold"
    return reason.split("@")[0][:40]


def fetch_trades(base_url: str, limit: int = 300) -> List[Dict[str, Any]]:
    url = f"{base_url.rstrip('/')}/api/v1/trades?status=CLOSED&limit={limit}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        data = json.load(resp)
    trades = data.get("trades", data if isinstance(data, list) else [])
    return trades if isinstance(trades, list) else []


def main() -> int:
    parser = argparse.ArgumentParser(description="Report closed trades by strategy")
    parser.add_argument("--hours", type=float, default=24.0, help="Lookback window (default 24)")
    parser.add_argument(
        "--database-url",
        default="http://localhost:8002",
        help="database-service base URL",
    )
    parser.add_argument("--limit", type=int, default=300, help="Max trades to fetch")
    args = parser.parse_args()

    cutoff = datetime.now(timezone.utc) - timedelta(hours=args.hours)
    all_trades = fetch_trades(args.database_url, args.limit)
    trades: List[Dict[str, Any]] = []
    for t in all_trades:
        dt = _parse_ts(t.get("exit_time") or t.get("entry_time"))
        if dt and dt >= cutoff:
            trades.append(t)

    print(f"Closed trades in last {args.hours:g}h: {len(trades)}")
    if not trades:
        return 0

    by_strategy: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"n": 0, "w": 0, "pnl": 0.0, "wins": [], "losses": [], "exits": defaultdict(int)}
    )
    total_pnl = 0.0
    for t in trades:
        strat = (t.get("strategy") or "unknown").strip()
        rp = float(t.get("realized_pnl") or 0)
        total_pnl += rp
        bucket = by_strategy[strat]
        bucket["n"] += 1
        bucket["pnl"] += rp
        bucket["exits"][_exit_bucket(t.get("exit_reason") or "")] += 1
        row = (
            t.get("pair"),
            t.get("exchange"),
            round(rp, 2),
            _entry_regime(t),
            (t.get("exit_time") or "")[:16],
            (t.get("exit_reason") or "")[:55],
        )
        if rp > 0:
            bucket["w"] += 1
            bucket["wins"].append(row)
        elif rp < 0:
            bucket["losses"].append(row)

    print(f"Total realized PnL: {total_pnl:+.2f}\n")
    for strat, v in sorted(by_strategy.items(), key=lambda x: x[1]["pnl"]):
        wr = 100.0 * v["w"] / v["n"] if v["n"] else 0.0
        stagnant = v["exits"].get("stagnant_loser", 0)
        print(f"## {strat}")
        print(f"  trades={v['n']}  win%={wr:.0f}  pnl={v['pnl']:+.2f}  stagnant_loser={stagnant}/{v['n']}")
        wins = [float(x[2]) for x in v["wins"]]
        losses = [float(x[2]) for x in v["losses"]]
        if wins:
            print(f"  avg_win={sum(wins)/len(wins):+.2f}")
        if losses:
            print(f"  avg_loss={sum(losses)/len(losses):+.2f}")
        print("  exit_buckets:", dict(v["exits"]))
        for label, rows in (("wins", v["wins"]), ("losses", v["losses"])):
            if rows:
                print(f"  {label}:")
                for r in rows:
                    print(f"    {r}")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
