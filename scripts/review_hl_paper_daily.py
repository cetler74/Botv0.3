#!/usr/bin/env python3
"""Daily Botv0.3 Hyperliquid paper-perp trade review.

Checks live paper trades against profit-plan acceptance criteria and
prints a concise status report. Intended for cron / automation on the
host where docker compose and database-service are running.

Exit codes:
  0 — all checks passed (or only informational warnings)
  1 — one or more acceptance criteria failed
  2 — database-service unreachable (stack down or wrong --database-url)

Usage:
  python3 scripts/review_hl_paper_daily.py
  python3 scripts/review_hl_paper_daily.py --database-url http://localhost:8002
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

# Reuse report helpers when available (same directory).
try:
    from report_hl_paper_pnl import (  # type: ignore
        _aggregate,
        _exit_bucket,
        _fetch_trades,
        _parse_ts,
        _profit_factor,
    )
except ImportError:
    # Allow running as `python3 scripts/review_hl_paper_daily.py` from repo root.
    import os
    import importlib.util

    _here = os.path.dirname(os.path.abspath(__file__))
    _spec = importlib.util.spec_from_file_location(
        "report_hl_paper_pnl",
        os.path.join(_here, "report_hl_paper_pnl.py"),
    )
    _mod = importlib.util.module_from_spec(_spec)
    assert _spec and _spec.loader
    _spec.loader.exec_module(_mod)
    _aggregate = _mod._aggregate
    _exit_bucket = _mod._exit_bucket
    _fetch_trades = _mod._fetch_trades
    _parse_ts = _mod._parse_ts
    _profit_factor = _mod._profit_factor


def _health_ok(base_url: str) -> bool:
    try:
        with urllib.request.urlopen(
            f"{base_url.rstrip('/')}/health", timeout=8
        ) as resp:
            return resp.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _filter_since(
    rows: List[Dict[str, Any]], hours: float, *, field: str = "entry_time"
) -> List[Dict[str, Any]]:
    if hours <= 0:
        return rows
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    out: List[Dict[str, Any]] = []
    for r in rows:
        ts = _parse_ts(r.get(field)) or _parse_ts(r.get("exit_time"))
        if ts is None or ts >= cutoff:
            out.append(r)
    return out


def _closed_in_window(
    rows: List[Dict[str, Any]], hours: float
) -> List[Dict[str, Any]]:
    """Closed trades whose exit_time falls within the lookback."""
    if hours <= 0:
        return [r for r in rows if str(r.get("status", "")).upper() == "CLOSED"]
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    out: List[Dict[str, Any]] = []
    for r in rows:
        if str(r.get("status", "")).upper() != "CLOSED":
            continue
        exit_dt = _parse_ts(r.get("exit_time"))
        if exit_dt is None or exit_dt >= cutoff:
            out.append(r)
    return out


def _daily_realized(closed: List[Dict[str, Any]]) -> Dict[str, float]:
    """UTC date string -> sum realized_pnl for exits that day."""
    by_day: Dict[str, float] = defaultdict(float)
    for r in closed:
        exit_dt = _parse_ts(r.get("exit_time"))
        if not exit_dt:
            continue
        day = exit_dt.astimezone(timezone.utc).strftime("%Y-%m-%d")
        by_day[day] += float(r.get("realized_pnl") or 0.0)
    return dict(by_day)


def _max_hold_family_pnl(closed: List[Dict[str, Any]]) -> Tuple[float, int]:
    buckets = {
        "max_hold_flat",
        "max_hold_be",
        "max_hold_hard",
        "max_hold_legacy",
    }
    total = 0.0
    count = 0
    for r in closed:
        bucket = _exit_bucket(r.get("exit_reason") or "")
        if bucket in buckets:
            total += float(r.get("realized_pnl") or 0.0)
            count += 1
    return total, count


def _strategy_pf_7d(
    closed_7d: List[Dict[str, Any]], min_trades: int = 3
) -> Dict[str, Optional[float]]:
    by_strat: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in closed_7d:
        name = (r.get("source_strategy") or "unknown").strip().lower()
        by_strat[name].append(r)
    out: Dict[str, Optional[float]] = {}
    for name, group in by_strat.items():
        if len(group) < min_trades:
            out[name] = None
            continue
        wins_sum = sum(
            float(r.get("realized_pnl") or 0.0)
            for r in group
            if float(r.get("realized_pnl") or 0.0) > 0
        )
        losses_sum = sum(
            float(r.get("realized_pnl") or 0.0)
            for r in group
            if float(r.get("realized_pnl") or 0.0) < 0
        )
        out[name] = _profit_factor(wins_sum, losses_sum)
    return out


def _fee_ratio(closed: List[Dict[str, Any]]) -> Optional[float]:
    gross_wins = sum(
        float(r.get("realized_pnl") or 0.0)
        for r in closed
        if float(r.get("realized_pnl") or 0.0) > 0
    )
    fees = sum(float(r.get("fees") or 0.0) for r in closed)
    if gross_wins <= 0:
        return None
    return fees / gross_wins


def run_review(base_url: str, limit: int = 3000) -> int:
    if not _health_ok(base_url):
        print(
            f"ERROR: database-service not reachable at {base_url} "
            "(start stack: docker compose up -d)"
        )
        return 2

    closed_all = _fetch_trades(base_url, limit, status="CLOSED")
    open_all = _fetch_trades(base_url, limit, status="OPEN")
    closed_24h = _closed_in_window(closed_all, 24)
    closed_96h = _closed_in_window(closed_all, 96)
    closed_7d = _closed_in_window(closed_all, 168)

    agg_24h = _aggregate(closed_24h)
    agg_7d = _aggregate(closed_7d)
    max_hold_pnl, max_hold_n = _max_hold_family_pnl(closed_96h)
    fee_ratio = _fee_ratio(closed_7d)
    daily = _daily_realized(closed_all)
    sorted_days = sorted(daily.keys(), reverse=True)
    consecutive_profitable = 0
    for day in sorted_days:
        if daily[day] > 0:
            consecutive_profitable += 1
        else:
            break

    failures: List[str] = []
    warnings: List[str] = []

    print("# Botv0.3 HL Paper Daily Review")
    print(f"  database_url={base_url}")
    print(f"  as_of_utc={datetime.now(timezone.utc).isoformat()}")
    print(
        f"  closed_24h={len(closed_24h)}  closed_7d={len(closed_7d)}  "
        f"open={len(open_all)}"
    )
    print(
        f"  realized_24h=${agg_24h['realized']:+.2f}  "
        f"realized_7d=${agg_7d['realized']:+.2f}  "
        f"WR_7d={agg_7d['win_rate']:.1f}%  PF_7d={_fmt_pf(agg_7d['profit_factor'])}"
    )

    # Acceptance: 4 consecutive profitable UTC days (spec)
    if consecutive_profitable < 4:
        failures.append(
            f"consecutive_profitable_days={consecutive_profitable} (need >= 4)"
        )
    else:
        print(f"  OK consecutive_profitable_days={consecutive_profitable}")

    # Acceptance: max-hold family >= -$5 over 96h (was -$79 lifetime pre-fix)
    if max_hold_pnl < -5.0:
        failures.append(
            f"max_hold_family_96h=${max_hold_pnl:+.2f} on {max_hold_n} trades "
            f"(need >= -$5)"
        )
    else:
        print(
            f"  OK max_hold_family_96h=${max_hold_pnl:+.2f} ({max_hold_n} trades)"
        )

    # Acceptance: fees <= 35% of gross wins (7d)
    if fee_ratio is None:
        warnings.append("fee_ratio_7d=n/a (no gross wins in 7d)")
    elif fee_ratio > 0.35:
        failures.append(
            f"fee_ratio_7d={fee_ratio * 100:.1f}% of gross wins (need <= 35%)"
        )
    else:
        print(f"  OK fee_ratio_7d={fee_ratio * 100:.1f}% of gross wins")

    # Whitelist strategies PF >= 1.2 over 7d
    whitelist = (
        "swing_hull_rsi_ema",
        "small_size_momentum_scalp",
        "vwma_hull",
    )
    pf_map = _strategy_pf_7d(closed_7d)
    print("\n## Whitelist strategy PF (7d)")
    for name in whitelist:
        pf = pf_map.get(name)
        pf_s = _fmt_pf(pf)
        n = len(
            [
                r
                for r in closed_7d
                if (r.get("source_strategy") or "").strip().lower() == name
            ]
        )
        print(f"  {name}: trades={n} PF={pf_s}")
        if n >= 3 and pf is not None and pf < 1.2:
            failures.append(f"whitelist_pf_{name}={pf:.2f} (need >= 1.2)")

    # breakout_retest should not appear on new closed trades (disabled 2026-05-29)
    br_recent = [
        r
        for r in closed_7d
        if (r.get("source_strategy") or "").strip().lower() == "breakout_retest_long"
    ]
    if br_recent:
        warnings.append(
            f"breakout_retest_long still has {len(br_recent)} closed trade(s) in 7d "
            "(expected 0 new entries after disable)"
        )
    else:
        print("  OK no breakout_retest_long closes in 7d")

    # Top exit buckets 7d
    by_exit: Dict[str, float] = defaultdict(float)
    for r in closed_7d:
        by_exit[_exit_bucket(r.get("exit_reason") or "")] += float(
            r.get("realized_pnl") or 0.0
        )
    print("\n## Exit buckets (7d PnL)")
    for bucket, pnl in sorted(by_exit.items(), key=lambda x: x[1]):
        print(f"  {bucket}: ${pnl:+.2f}")

    # Worst strategies 7d
    by_strat: Dict[str, float] = defaultdict(float)
    for r in closed_7d:
        by_strat[(r.get("source_strategy") or "unknown").strip().lower()] += float(
            r.get("realized_pnl") or 0.0
        )
    print("\n## Strategy PnL (7d, worst first)")
    for name, pnl in sorted(by_strat.items(), key=lambda x: x[1])[:8]:
        print(f"  {name}: ${pnl:+.2f}")

    if warnings:
        print("\n## Warnings")
        for w in warnings:
            print(f"  - {w}")

    if failures:
        print("\n## FAILED acceptance criteria")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("\n## Result: PASS (all acceptance criteria met)")
    return 0


def _fmt_pf(pf: Optional[float]) -> str:
    return f"{pf:.2f}" if pf is not None else "n/a"


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily HL paper trade review")
    parser.add_argument(
        "--database-url",
        default="http://localhost:8002",
        help="database-service base URL",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=3000,
        help="Max trades to fetch per status",
    )
    args = parser.parse_args()
    return run_review(args.database_url, args.limit)


if __name__ == "__main__":
    sys.exit(main())
