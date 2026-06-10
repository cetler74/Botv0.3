"""Aggregate ARC audit rows for dashboards."""

from __future__ import annotations

from typing import Any, Dict, List


def _pct(n: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round((n / total) * 100.0, 2)


def build_arc_audit_summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(rows or [])
    area = sum(1 for r in rows if r.get("area_pass"))
    rng = sum(1 for r in rows if r.get("range_pass"))
    candle = sum(1 for r in rows if r.get("candle_pass"))
    by_zone: Dict[str, int] = {}
    by_geometry: Dict[str, int] = {}
    by_signal: Dict[str, int] = {}
    by_state: Dict[str, int] = {}
    for row in rows or []:
        zone = str(row.get("zone") or "unknown")
        by_zone[zone] = by_zone.get(zone, 0) + 1
        geom = str(row.get("range_geometry_tag") or "unknown")
        by_geometry[geom] = by_geometry.get(geom, 0) + 1
        sig = str(row.get("signal") or "hold")
        by_signal[sig] = by_signal.get(sig, 0) + 1
        state = str(row.get("setup_state") or "unknown")
        by_state[state] = by_state.get(state, 0) + 1
    return {
        "totalEvaluations": total,
        "gatePassRates": {
            "area": _pct(area, total),
            "range": _pct(rng, total),
            "candle": _pct(candle, total),
        },
        "gatePassCounts": {
            "area": area,
            "range": rng,
            "candle": candle,
        },
        "byZone": by_zone,
        "byGeometry": by_geometry,
        "bySignal": by_signal,
        "bySetupState": by_state,
        "recent": (rows or [])[:50],
    }
