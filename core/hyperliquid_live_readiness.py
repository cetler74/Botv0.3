"""Paper profitability gate before manual Hyperliquid live promotion."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

try:
    from perp_paper_pnl_report import build_paper_pnl_report, parse_ts
except ImportError:
    from core.perp_paper_pnl_report import build_paper_pnl_report, parse_ts


def _consecutive_losses(closed: List[Dict[str, Any]]) -> int:
    streak = 0
    for row in sorted(
        closed,
        key=lambda r: parse_ts(r.get("exit_time")) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    ):
        pnl = float(row.get("realized_pnl") or 0.0)
        if pnl < 0:
            streak += 1
        else:
            break
    return streak


def evaluate_live_readiness(
    rows: List[Dict[str, Any]],
    promotion_cfg: Dict[str, Any],
    *,
    strategy: str = "rsi_stoch_reversal_5m",
) -> Dict[str, Any]:
    """
    Return {ready, reasons, metrics} for rsi_stoch paper trades vs promotion thresholds.
    """
    cfg = promotion_cfg or {}
    lookback_hours = float(cfg.get("lookback_hours", 168) or 168)
    min_closed = int(cfg.get("min_closed_trades", 30) or 30)
    min_pnl = float(cfg.get("min_realized_pnl_usd", 25.0) or 25.0)
    min_pf = float(cfg.get("min_profit_factor", 1.2) or 1.2)
    min_wr = float(cfg.get("min_win_rate", 0.52) or 0.52)
    max_consec = int(cfg.get("max_consecutive_losses", 4) or 4)
    require_24h = bool(cfg.get("require_positive_last_24h", True))

    strat_lc = str(strategy or "rsi_stoch_reversal_5m").strip().lower()
    filtered = [
        r
        for r in rows
        if str(r.get("source_strategy") or "").strip().lower() == strat_lc
    ]
    report = build_paper_pnl_report(filtered, hours=lookback_hours)
    agg = report.get("aggregate") or {}
    closed = [
        r
        for r in filtered
        if str(r.get("status") or "").upper() == "CLOSED"
        and (parse_ts(r.get("exit_time")) or datetime.min.replace(tzinfo=timezone.utc))
        >= datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    ]

    reasons: List[str] = []
    n_closed = int(agg.get("closed") or 0)
    realized = float(agg.get("realized") or 0.0)
    wr = float(agg.get("winRate") or 0.0) / 100.0
    pf = agg.get("profitFactor")
    pf_val = float(pf) if pf is not None else None
    consec = _consecutive_losses(closed)

    if n_closed < min_closed:
        reasons.append(f"closed_trades {n_closed} < {min_closed}")
    if realized < min_pnl:
        reasons.append(f"realized_pnl ${realized:.2f} < ${min_pnl:.2f}")
    if pf_val is None or pf_val < min_pf:
        reasons.append(f"profit_factor {pf_val} < {min_pf}")
    if wr < min_wr:
        reasons.append(f"win_rate {wr:.1%} < {min_wr:.1%}")
    if consec > max_consec:
        reasons.append(f"consecutive_losses {consec} > {max_consec}")

    pnl_24h = 0.0
    if require_24h:
        cutoff_24 = datetime.now(timezone.utc) - timedelta(hours=24)
        closed_24 = [
            r
            for r in closed
            if (parse_ts(r.get("exit_time")) or datetime.min.replace(tzinfo=timezone.utc))
            >= cutoff_24
        ]
        pnl_24h = sum(float(r.get("realized_pnl") or 0.0) for r in closed_24)
        if pnl_24h <= 0:
            reasons.append(f"last_24h_pnl ${pnl_24h:.2f} not positive")

    metrics = {
        "strategy": strat_lc,
        "lookback_hours": lookback_hours,
        "closed_trades": n_closed,
        "realized_pnl_usd": realized,
        "win_rate": wr,
        "profit_factor": pf_val,
        "consecutive_losses": consec,
        "last_24h_realized_pnl_usd": pnl_24h,
    }
    return {
        "ready": len(reasons) == 0,
        "reasons": reasons,
        "metrics": metrics,
        "report": report,
    }
