"""Aggregate dual-SMA audit rows for dashboards."""

from __future__ import annotations

from typing import Any, Dict, List


def _pct(n: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round((n / total) * 100.0, 2)


def build_dual_sma_audit_summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(rows or [])
    daily = sum(1 for r in rows if r.get("daily_pass"))
    confirm = sum(1 for r in rows if r.get("confirm_15m_pass"))
    entry = sum(1 for r in rows if r.get("entry_5m_pass"))
    precision = sum(1 for r in rows if r.get("precision_pass"))
    by_bias: Dict[str, int] = {}
    by_trend: Dict[str, int] = {}
    by_signal: Dict[str, int] = {}
    for row in rows or []:
        bias = str(row.get("daily_bias") or "unknown")
        by_bias[bias] = by_bias.get(bias, 0) + 1
        trend = str(row.get("trend_15m") or "unknown")
        by_trend[trend] = by_trend.get(trend, 0) + 1
        sig = str(row.get("signal") or "hold")
        by_signal[sig] = by_signal.get(sig, 0) + 1
    return {
        "totalEvaluations": total,
        "gatePassRates": {
            "daily": _pct(daily, total),
            "confirm15m": _pct(confirm, total),
            "entry5m": _pct(entry, total),
            "precision": _pct(precision, total),
        },
        "gatePassCounts": {
            "daily": daily,
            "confirm15m": confirm,
            "entry5m": entry,
            "precision": precision,
        },
        "byDailyBias": by_bias,
        "byTrend15m": by_trend,
        "bySignal": by_signal,
        "recent": (rows or [])[:50],
    }
