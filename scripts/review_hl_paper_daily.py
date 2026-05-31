#!/usr/bin/env python3
"""Daily Hyperliquid paper-perp trade review vs profit-plan acceptance criteria.

Designed for the Botv0.3 cron automation. Pulls closed/open paper trades from
database-service, compares post-change windows (24h / 96h / 7d) against the
targets in docs/superpowers/specs/2026-05-27-perp-profit-plan-design.md, and
prints actionable recommendations.

Usage:
  python3 scripts/review_hl_paper_daily.py
  python3 scripts/review_hl_paper_daily.py --database-url http://localhost:8002
  python3 scripts/review_hl_paper_daily.py --hours 24 --json
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

# Reuse report helpers
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from report_hl_paper_pnl import (  # noqa: E402
    _aggregate,
    _exit_bucket,
    _fetch_trades,
    _hold_minutes,
    _parse_ts,
    _print_grouped,
    _profit_factor,
)

DEPRECATED_STRATEGIES = {
    "breakout_retest_long",
    "heikin_ashi",
    "engulfing_multi_tf",
}

WHITELIST_CANDIDATES = {
    "swing_hull_rsi_ema",
    "small_size_momentum_scalp",
}

LEASHED_STRATEGIES = {
    "pullback_long_scalping",
    "vwap_bounce_scalping",
}

MAX_HOLD_BUCKETS = {
    "max_hold_flat",
    "max_hold_be",
    "max_hold_hard",
    "max_hold_legacy",
}


def _filter_since(rows: List[Dict[str, Any]], hours: float) -> List[Dict[str, Any]]:
    if hours <= 0:
        return rows
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    out: List[Dict[str, Any]] = []
    for row in rows:
        ts = _parse_ts(row.get("exit_time")) or _parse_ts(row.get("entry_time"))
        if ts and ts >= cutoff:
            out.append(row)
    return out


def _closed_since(rows: List[Dict[str, Any]], hours: float) -> List[Dict[str, Any]]:
    closed = [r for r in rows if str(r.get("status") or "").upper() == "CLOSED"]
    if hours <= 0:
        return closed
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    out: List[Dict[str, Any]] = []
    for row in closed:
        ts = _parse_ts(row.get("exit_time")) or _parse_ts(row.get("entry_time"))
        if ts and ts >= cutoff:
            out.append(row)
    return out


def _daily_realized(closed: List[Dict[str, Any]]) -> List[Tuple[str, float, int]]:
    by_day: Dict[str, Dict[str, float]] = defaultdict(lambda: {"pnl": 0.0, "n": 0})
    for row in closed:
        ts = _parse_ts(row.get("exit_time")) or _parse_ts(row.get("entry_time"))
        if not ts:
            continue
        day = ts.astimezone(timezone.utc).strftime("%Y-%m-%d")
        by_day[day]["pnl"] += float(row.get("realized_pnl") or 0.0)
        by_day[day]["n"] += 1
    return sorted(
        [(day, vals["pnl"], int(vals["n"])) for day, vals in by_day.items()],
        key=lambda x: x[0],
    )


def _consecutive_profitable_days(daily: List[Tuple[str, float, int]]) -> int:
    streak = 0
    for _, pnl, n in reversed(daily):
        if n == 0:
            continue
        if pnl > 0:
            streak += 1
        else:
            break
    return streak


def _strategy_pf(closed: List[Dict[str, Any]], strategy: str) -> Optional[float]:
    rows = [
        r
        for r in closed
        if (r.get("source_strategy") or "").strip().lower() == strategy
    ]
    wins = sum(float(r.get("realized_pnl") or 0.0) for r in rows if float(r.get("realized_pnl") or 0.0) > 0)
    losses = sum(float(r.get("realized_pnl") or 0.0) for r in rows if float(r.get("realized_pnl") or 0.0) < 0)
    return _profit_factor(wins, losses)


def _max_hold_pnl(closed: List[Dict[str, Any]]) -> Tuple[float, int]:
    total = 0.0
    count = 0
    for row in closed:
        bucket = _exit_bucket(row.get("exit_reason") or "")
        if bucket in MAX_HOLD_BUCKETS:
            total += float(row.get("realized_pnl") or 0.0)
            count += 1
    return total, count


def _fee_ratio(closed: List[Dict[str, Any]]) -> Optional[float]:
    gross = sum(
        float(r.get("realized_pnl") or 0.0)
        for r in closed
        if float(r.get("realized_pnl") or 0.0) > 0
    )
    fees = sum(float(r.get("fees") or 0.0) for r in closed)
    if gross <= 0:
        return None
    return 100.0 * fees / gross


def _check_deprecated_activity(closed: List[Dict[str, Any]], hours: float) -> List[str]:
    issues: List[str] = []
    recent = _closed_since(closed, hours)
    for strat in DEPRECATED_STRATEGIES:
        hits = [
            r
            for r in recent
            if (r.get("source_strategy") or "").strip().lower() == strat
        ]
        if hits:
            pnl = sum(float(r.get("realized_pnl") or 0.0) for r in hits)
            issues.append(
                f"Deprecated strategy `{strat}` still traded {len(hits)}× "
                f"in last {hours:.0f}h (PnL ${pnl:+.2f}) — config/apply_config may be stale."
            )
    return issues


def _recommendations(
    closed_all: List[Dict[str, Any]],
    closed_24h: List[Dict[str, Any]],
    closed_96h: List[Dict[str, Any]],
    closed_7d: List[Dict[str, Any]],
    open_rows: List[Dict[str, Any]],
) -> List[str]:
    recs: List[str] = []
    daily = _daily_realized(closed_all)
    streak = _consecutive_profitable_days(daily)
    if streak < 4:
        recs.append(
            f"Acceptance: only {streak}/4 consecutive profitable days — "
            "keep paper-only; verify exit rework (Phase 3) is reducing max-hold dumps."
        )

    mh_pnl, mh_n = _max_hold_pnl(closed_96h)
    if mh_pnl < -5.0:
        recs.append(
            f"Max-hold exit family lost ${mh_pnl:.2f} over 96h ({mh_n} trades); "
            "target is ≥ -$5. Review ATR stops and salvage trail metadata."
        )

    fee_pct = _fee_ratio(closed_7d)
    if fee_pct is not None and fee_pct > 35.0:
        recs.append(
            f"Fees are {fee_pct:.0f}% of gross wins (7d) — target ≤35%. "
            "Min-edge gate (0.50%) or block windows may need tightening."
        )

    for strat in WHITELIST_CANDIDATES:
        pf = _strategy_pf(closed_7d, strat)
        if pf is not None and pf < 1.2:
            recs.append(
                f"Whitelist candidate `{strat}` 7d PF={pf:.2f} (<1.2) — "
                "do not promote to live yet."
            )

    for strat in LEASHED_STRATEGIES:
        pf = _strategy_pf(closed_7d, strat)
        rows = [
            r
            for r in closed_7d
            if (r.get("source_strategy") or "").strip().lower() == strat
        ]
        if len(rows) >= 5 and pf is not None and pf < 1.0:
            recs.append(
                f"Leashed strategy `{strat}` still PF={pf:.2f} on {len(rows)} "
                "7d closes — consider disabling or raising min_confidence further."
            )

    stop_loss = [
        r for r in closed_7d if _exit_bucket(r.get("exit_reason") or "") == "paper_stop_loss"
    ]
    if stop_loss:
        stop_pnl = sum(float(r.get("realized_pnl") or 0.0) for r in stop_loss)
        stop_wr = sum(1 for r in stop_loss if float(r.get("realized_pnl") or 0.0) > 0) / len(stop_loss)
        if stop_pnl < -20 and stop_wr == 0:
            recs.append(
                f"Stop-loss bucket: {len(stop_loss)} trades, ${stop_pnl:.2f}, "
                "0% WR in 7d — check min_edge_gate and per-coin stop overrides."
            )

    agg_24 = _aggregate(closed_24h + open_rows)
    if agg_24["closed"] >= 3 and agg_24["avg_win"] > 0 and agg_24["avg_loss"] < 0:
        ratio = abs(agg_24["avg_loss"]) / agg_24["avg_win"]
        if ratio > 2.0:
            recs.append(
                f"24h loss/win asymmetry {ratio:.1f}× "
                f"(avg win ${agg_24['avg_win']:+.2f} vs avg loss ${agg_24['avg_loss']:+.2f}) — "
                "trailing step widening (Phase C) may need more time or further tuning."
            )

    recs.extend(_check_deprecated_activity(closed_all, 168.0))

    if not recs:
        recs.append("All tracked acceptance checks passed on current sample.")
    return recs


def _status_line(label: str, ok: bool, detail: str) -> str:
    mark = "PASS" if ok else "FAIL"
    return f"  [{mark}] {label}: {detail}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily HL paper-perp trade review")
    parser.add_argument("--database-url", default="http://localhost:8002")
    parser.add_argument("--limit", type=int, default=3000)
    parser.add_argument("--hours", type=float, default=24.0, help="Primary focus window")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        closed = _fetch_trades(args.database_url, args.limit, status="CLOSED")
        open_rows = _fetch_trades(args.database_url, args.limit, status="OPEN")
    except urllib.error.URLError as exc:
        msg = (
            f"Cannot reach database-service at {args.database_url}: {exc}\n"
            "Start the stack (docker compose up) and retry, or pass --database-url."
        )
        print(msg, file=sys.stderr)
        return 2

    if not closed and not open_rows:
        print("No paper trades found.")
        return 0

    closed_24h = _closed_since(closed, 24)
    closed_96h = _closed_since(closed, 96)
    closed_7d = _closed_since(closed, 168)
    focus = _closed_since(closed, args.hours)

    agg_focus = _aggregate(focus + open_rows)
    agg_7d = _aggregate(closed_7d)
    daily = _daily_realized(closed)
    streak = _consecutive_profitable_days(daily)
    mh_pnl, mh_n = _max_hold_pnl(closed_96h)
    fee_pct = _fee_ratio(closed_7d)
    recs = _recommendations(closed, closed_24h, closed_96h, closed_7d, open_rows)

    checks = [
        _status_line(
            "Consecutive profitable days",
            streak >= 4,
            f"{streak}/4 (latest day "
            f"{daily[-1][0] if daily else 'n/a'}: "
            f"${daily[-1][1]:+.2f})" if daily else "no daily data",
        ),
        _status_line(
            "Max-hold family 96h PnL",
            mh_pnl >= -5.0,
            f"${mh_pnl:+.2f} across {mh_n} exits (target ≥ -$5)",
        ),
        _status_line(
            "Fee load vs gross wins (7d)",
            fee_pct is None or fee_pct <= 35.0,
            f"{fee_pct:.0f}% of gross" if fee_pct is not None else "no gross wins in 7d",
        ),
        _status_line(
            f"Focus window ({args.hours:.0f}h) realized PnL",
            agg_focus["realized"] > 0 or agg_focus["closed"] == 0,
            f"${agg_focus['realized']:+.2f} on {agg_focus['closed']} closes, "
            f"WR {agg_focus['win_rate']:.1f}%, PF "
            f"{agg_focus['profit_factor'] if agg_focus['profit_factor'] is not None else 'n/a'}",
        ),
    ]

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "focus_hours": args.hours,
        "open_positions": len(open_rows),
        "focus": agg_focus,
        "last_7d": agg_7d,
        "consecutive_profitable_days": streak,
        "max_hold_96h_pnl": mh_pnl,
        "max_hold_96h_count": mh_n,
        "fee_pct_of_gross_7d": fee_pct,
        "checks": checks,
        "recommendations": recs,
    }

    if args.json:
        print(json.dumps(payload, indent=2, default=str))
        return 0 if all("PASS" in c for c in checks[:3]) else 1

    print("# Botv0.3 Hyperliquid Paper Daily Review")
    print(f"  generated: {payload['generated_at']}")
    print(f"  open positions: {len(open_rows)}")
    print(
        f"  focus ({args.hours:.0f}h): closed={agg_focus['closed']} "
        f"realized=${agg_focus['realized']:+.2f} WR={agg_focus['win_rate']:.1f}% "
        f"PF={agg_focus['profit_factor'] if agg_focus['profit_factor'] is not None else 'n/a'}"
    )
    print(
        f"  7d: closed={agg_7d['closed']} realized=${agg_7d['realized']:+.2f} "
        f"avg_win=${agg_7d['avg_win']:+.2f} avg_loss=${agg_7d['avg_loss']:+.2f} "
        f"avg_win_hold={agg_7d['avg_win_hold_min']:.0f}m "
        f"avg_loss_hold={agg_7d['avg_loss_hold_min']:.0f}m"
    )
    print("\n## Acceptance checks")
    for line in checks:
        print(line)
    print("\n## Recommendations")
    for rec in recs:
        print(f"  - {rec}")
    _print_grouped(
        f"By strategy (last {args.hours:.0f}h)",
        focus + [r for r in open_rows if _parse_ts(r.get("entry_time"))],
        lambda r: (r.get("source_strategy") or "unknown").strip().lower(),
    )
    _print_grouped(
        "By exit-reason bucket (7d)",
        closed_7d,
        lambda r: _exit_bucket(r.get("exit_reason") or ""),
    )
    return 0 if all("PASS" in c for c in checks[:3]) else 1


if __name__ == "__main__":
    sys.exit(main())
