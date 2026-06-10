"""Aggregate supply/demand step audit rows for dashboards."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _pct(n: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round((n / total) * 100.0, 2)


def build_supply_demand_audit_summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(rows or [])
    step1 = sum(1 for r in rows if r.get("step1_pass"))
    step2 = sum(1 for r in rows if r.get("step2_pass"))
    step3 = sum(1 for r in rows if r.get("step3_pass"))
    by_trend: Dict[str, int] = {}
    by_asset: Dict[str, int] = {}
    by_signal: Dict[str, int] = {}
    for row in rows or []:
        trend = str(row.get("trend_direction") or "unknown")
        by_trend[trend] = by_trend.get(trend, 0) + 1
        asset = str(row.get("asset_class") or "unknown")
        by_asset[asset] = by_asset.get(asset, 0) + 1
        sig = str(row.get("signal") or "hold")
        by_signal[sig] = by_signal.get(sig, 0) + 1
    return {
        "totalEvaluations": total,
        "stepPassRates": {
            "step1": _pct(step1, total),
            "step2": _pct(step2, total),
            "step3": _pct(step3, total),
        },
        "stepPassCounts": {"step1": step1, "step2": step2, "step3": step3},
        "byTrendDirection": by_trend,
        "byAssetClass": by_asset,
        "bySignal": by_signal,
        "recent": (rows or [])[:50],
    }
