import sys
import types
from datetime import datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for path in (
    REPO_ROOT / "services" / "database-service",
    REPO_ROOT / "core",
):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

if "prometheus_client" not in sys.modules:
    prometheus_stub = types.ModuleType("prometheus_client")

    class _Metric:
        def __init__(self, *args, **kwargs):
            pass

        def labels(self, *args, **kwargs):
            return self

        def inc(self, *args, **kwargs):
            pass

        def observe(self, *args, **kwargs):
            pass

        def set(self, *args, **kwargs):
            pass

    prometheus_stub.Counter = _Metric
    prometheus_stub.Histogram = _Metric
    prometheus_stub.Gauge = _Metric
    prometheus_stub.CollectorRegistry = lambda *args, **kwargs: object()
    prometheus_stub.generate_latest = lambda *args, **kwargs: b""
    prometheus_stub.CONTENT_TYPE_LATEST = "text/plain"
    sys.modules["prometheus_client"] = prometheus_stub

from services.database_service import main as db_service


class FakeDbManager:
    def __init__(self):
        self.queries = []
        self.single_queries = []

    async def execute_query(self, query, params=None):
        self.queries.append((query, params))
        return []

    async def execute_single_query(self, query, params=None):
        self.single_queries.append((query, params))
        return None


@pytest.mark.asyncio
async def test_adaptive_pnl_decision_table_includes_apply_reload_columns():
    manager = FakeDbManager()

    await db_service.ensure_hyperliquid_adaptive_pnl_decisions_table(manager)

    schema_sql = "\n".join(query for query, _ in manager.queries)
    assert "decision_type TEXT" in schema_sql
    assert "apply_status TEXT" in schema_sql
    assert "applied_at TIMESTAMPTZ" in schema_sql
    assert "applied_to_cycle BIGINT" in schema_sql
    assert "reload_required BOOLEAN" in schema_sql
    assert "reload_verified_at TIMESTAMPTZ" in schema_sql
    assert "idx_hl_adaptive_pnl_decision_key" in schema_sql


@pytest.mark.asyncio
async def test_adaptive_pnl_decision_sync_persists_apply_reload_state(monkeypatch):
    manager = FakeDbManager()
    impl = sys.modules["services.database_service._impl"]
    monkeypatch.setattr(impl, "db_manager", manager)

    payload = db_service.AdaptivePnlDecisionSync(
        records=[
            {
                "decisionKey": "rsi_stoch_reversal_1m:long:reduce",
                "status": "active",
                "type": "reduce_strategy_side",
                "targetType": "strategy_side",
                "target": "rsi_stoch_reversal_1m",
                "side": "long",
                "configPath": "runtime.entrySizing.rsi_stoch_reversal_1m:long",
                "oldValue": 1.0,
                "newValue": 0.35,
                "situation": "High fee drag",
                "intendedEffect": "Reduce exposure until evidence improves",
                "evidence": {"closed": 12},
                "currentOutcome": {"closed": 2, "realized": 1.25},
                "liveSince": "2026-06-06T09:00:00Z",
                "applyStatus": "applied_live",
                "appliedAt": "2026-06-06T09:01:00Z",
                "appliedToCycle": 42,
                "reloadRequired": False,
                "reloadVerifiedAt": "2026-06-06T09:02:00Z",
            }
        ],
        synced_at=datetime(2026, 6, 6, 9, 3, 0),
    )

    result = await db_service.sync_hyperliquid_adaptive_pnl_decisions(payload)

    assert result["synced"] == 1
    query, params = manager.queries[0]
    assert "decision_type" in query
    assert "apply_status" in query
    assert "applied_at" in query
    assert "applied_to_cycle" in query
    assert "reload_required" in query
    assert "reload_verified_at" in query
    assert "reduce_strategy_side" in params
    assert "applied_live" in params
    assert 42 in params
    assert query.count("%s") == len(params)


@pytest.mark.asyncio
async def test_adaptive_pnl_decision_sync_releases_missing_active_records(monkeypatch):
    manager = FakeDbManager()
    impl = sys.modules["services.database_service._impl"]
    monkeypatch.setattr(impl, "db_manager", manager)

    payload = db_service.AdaptivePnlDecisionSync(
        records=[],
        active_decision_keys=[],
        synced_at=datetime(2026, 6, 6, 10, 0, 0),
    )

    result = await db_service.sync_hyperliquid_adaptive_pnl_decisions(payload)

    assert result["synced"] == 0
    assert any("WHERE status = 'active'" in query for query, _ in manager.queries)
    assert any("No active adaptive decisions remain" in query for query, _ in manager.queries)
