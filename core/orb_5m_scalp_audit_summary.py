"""Aggregate ORB 5m scalp audit rows for dashboards."""

from __future__ import annotations

from typing import Any, Dict, List


def _pct(n: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round((n / total) * 100.0, 2)


def build_orb_5m_scalp_audit_summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(rows or [])
    breakout = sum(1 for r in rows if r.get("breakout_valid"))
    retest = sum(1 for r in rows if r.get("retest_valid"))

    setup_state_counts: Dict[str, int] = {}
    by_direction: Dict[str, int] = {}
    by_signal: Dict[str, int] = {}
    rejection_breakdown: Dict[str, int] = {}
    rr_values: List[float] = []

    for row in rows or []:
        state = str(row.get("setup_state") or "unknown")
        setup_state_counts[state] = setup_state_counts.get(state, 0) + 1
        direction = str(row.get("direction") or "none")
        by_direction[direction] = by_direction.get(direction, 0) + 1
        sig = str(row.get("signal") or "hold")
        by_signal[sig] = by_signal.get(sig, 0) + 1
        rej = str(row.get("rejection_reason") or "")
        if rej:
            rejection_breakdown[rej] = rejection_breakdown.get(rej, 0) + 1
        sig_l = sig.lower()
        if sig_l in {"buy", "sell", "long", "short"}:
            try:
                rr_values.append(float(row.get("reward_risk") or 0))
            except (TypeError, ValueError):
                pass

    setup_state_rates = {k: _pct(v, total) for k, v in setup_state_counts.items()}
    avg_rr = round(sum(rr_values) / len(rr_values), 2) if rr_values else 0.0

    return {
        "totalEvaluations": total,
        "setupStateCounts": setup_state_counts,
        "setupStateRates": setup_state_rates,
        "gatePassRates": {
            "breakout": _pct(breakout, total),
            "retest": _pct(retest, total),
        },
        "gatePassCounts": {
            "breakout": breakout,
            "retest": retest,
        },
        "byDirection": by_direction,
        "bySignal": by_signal,
        "rejectionBreakdown": rejection_breakdown,
        "signalRewardRisk": avg_rr,
        "recent": (rows or [])[:50],
    }
