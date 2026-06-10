"""Merge and filter strategy audit payloads by trading venue for dashboards."""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Sequence

SPOT_AUDIT_VENUES = ("binance", "bybit", "cryptocom")
HL_AUDIT_VENUE = "hyperliquid"


def _audit_row_key(row: Dict[str, Any]) -> tuple:
    return (
        str(row.get("venue") or "").lower(),
        str(row.get("symbol") or "").upper(),
        str(row.get("candle_ts") or ""),
        str(row.get("source") or ""),
    )


def merge_venue_audit_payloads(
    payloads: Sequence[Dict[str, Any]],
    summary_builder: Callable[[List[Dict[str, Any]]], Dict[str, Any]],
    *,
    max_rows: int | None = None,
) -> Dict[str, Any]:
    """Combine per-venue audit fetches, dedupe, sort newest-first, rebuild summary."""
    rows: List[Dict[str, Any]] = []
    seen: set[tuple] = set()
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        for row in payload.get("rows") or []:
            if not isinstance(row, dict):
                continue
            key = _audit_row_key(row)
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
    rows.sort(key=lambda r: str(r.get("log_ts") or ""), reverse=True)
    if max_rows is not None and max_rows > 0:
        rows = rows[:max_rows]
    summary = summary_builder(rows)
    return {"rows": rows, "count": len(rows), "summary": summary}


def filter_audit_payload_by_venues(
    payload: Dict[str, Any],
    venues: Iterable[str],
    summary_builder: Callable[[List[Dict[str, Any]]], Dict[str, Any]],
    *,
    max_rows: int | None = None,
) -> Dict[str, Any]:
    """Keep only rows whose venue is in *venues* and rebuild summary."""
    allowed = {str(v).lower() for v in venues}
    source_rows = payload.get("rows") if isinstance(payload, dict) else []
    filtered = [
        row
        for row in (source_rows or [])
        if isinstance(row, dict) and str(row.get("venue") or "").lower() in allowed
    ]
    filtered.sort(key=lambda r: str(r.get("log_ts") or ""), reverse=True)
    if max_rows is not None and max_rows > 0:
        filtered = filtered[:max_rows]
    summary = summary_builder(filtered)
    return {"rows": filtered, "count": len(filtered), "summary": summary}
