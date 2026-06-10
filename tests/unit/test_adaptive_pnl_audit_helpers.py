import sys
from pathlib import Path


SERVICE_DIR = Path(__file__).resolve().parents[2] / "services" / "orchestrator-service"
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from adaptive_pnl_audit import merge_persisted_adaptive_history


def test_merge_persisted_adaptive_history_prefers_postgres_records():
    control = {
        "status": "not_evaluated",
        "decisions": [],
        "history": [{"decisionKey": "json-old", "status": "active"}],
    }
    records = [
        {
            "decisionKey": "db-active",
            "status": "active",
            "liveSince": "2026-06-06T09:00:00Z",
            "applyStatus": "applied_live",
        },
        {
            "decisionKey": "db-released",
            "status": "released",
            "liveSince": "2026-06-06T08:00:00Z",
            "releasedAt": "2026-06-06T09:10:00Z",
            "applyStatus": "applied_live",
        },
    ]

    merged = merge_persisted_adaptive_history(control, records)

    assert [row["decisionKey"] for row in merged["history"]] == ["db-active", "db-released"]
    assert merged["historyCount"] == 2
    assert merged["activeDecisionKeys"] == ["db-active"]
    assert merged["decisions"] == [records[0]]
    assert merged["historySource"] == "postgres"


def test_merge_persisted_adaptive_history_marks_runtime_decisions_live():
    control = {
        "status": "active",
        "decisions": [
            {
                "decisionKey": "runtime-reduce",
                "status": "active",
                "liveSince": "2026-06-06T09:00:00Z",
            }
        ],
    }

    merged = merge_persisted_adaptive_history(control, [])

    assert merged["decisions"][0]["applyStatus"] == "applied_live"
    assert merged["decisions"][0]["reloadRequired"] is False
    assert merged["historySource"] == "runtime"


def test_merge_persisted_adaptive_history_preserves_pending_cycle_status():
    control = {
        "status": "active",
        "decisions": [
            {
                "decisionKey": "runtime-reduce",
                "status": "active",
                "liveSince": "2026-06-06T09:00:00Z",
                "applyStatus": "pending_cycle",
                "appliedAt": None,
                "reloadRequired": False,
            }
        ],
    }

    merged = merge_persisted_adaptive_history(control, [])

    assert merged["decisions"][0]["applyStatus"] == "pending_cycle"
    assert merged["decisions"][0]["appliedAt"] is None
    assert merged["historySource"] == "runtime"
