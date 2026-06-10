from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, List, Optional


def _decision_key(record: Dict[str, Any]) -> str:
    return str(record.get("decisionKey") or record.get("decision_key") or "").strip()


def _with_live_apply_status(record: Dict[str, Any]) -> Dict[str, Any]:
    enriched = dict(record)
    enriched.setdefault("applyStatus", "applied_live")
    enriched.setdefault("reloadRequired", False)
    enriched.setdefault("appliedAt", enriched.get("lastSeenAt") or enriched.get("liveSince"))
    enriched.setdefault("reloadVerifiedAt", datetime.utcnow().isoformat() + "Z")
    return enriched


def merge_persisted_adaptive_history(
    control: Dict[str, Any],
    postgres_records: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Attach durable adaptive audit history to a runtime control payload.

    Postgres records are authoritative when present. Without them, runtime
    decisions are still marked as live because they were applied to the current
    in-memory paper-perp cycle, not to a pending config-file reload.
    """
    merged = deepcopy(control or {})
    records = [dict(row) for row in (postgres_records or []) if _decision_key(dict(row))]

    if records:
        active = [
            row
            for row in records
            if str(row.get("status") or "").lower() == "active"
        ]
        merged["history"] = records
        merged["historyCount"] = len(records)
        merged["activeDecisionKeys"] = sorted(_decision_key(row) for row in active)
        merged["decisions"] = active
        merged["historySource"] = "postgres"
        return merged

    runtime_decisions = [
        _with_live_apply_status(row)
        for row in (merged.get("decisions") or [])
        if _decision_key(row)
    ]
    runtime_history = [
        _with_live_apply_status(row)
        for row in (merged.get("history") or runtime_decisions)
        if _decision_key(row)
    ]
    active = [
        row
        for row in runtime_history
        if str(row.get("status") or "active").lower() == "active"
    ]
    merged["decisions"] = runtime_decisions
    merged["history"] = runtime_history
    merged["historyCount"] = len(runtime_history)
    merged["activeDecisionKeys"] = sorted(_decision_key(row) for row in active)
    merged["historySource"] = "runtime"
    return merged
