"""Contract checks for counterfactual Hyperliquid opportunity recording."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_shadow_recorder_uses_setup_fingerprint_and_open_episode_key():
    source = (ROOT / "services/orchestrator-service/main.py").read_text()
    recorder = source[
        source.index("async def _record_hyperliquid_shadow_trades") :
        source.index("async def _finalize_hyperliquid_shadow_dispositions")
    ]
    assert "if fingerprint in shadow_fingerprints" in recorder
    assert "open_key in shadow_open_keys" in recorder
    assert "shadow_open_keys.add(open_key)" in recorder
    assert "single_open_per_strategy_coin_side" in recorder
    assert 'strategy == "supply_demand_3step" and retest_zone' in source
    assert '"shadow_signal_fingerprint": fingerprint' in recorder


def test_shadow_records_store_real_disposition_and_gate_trace():
    source = (ROOT / "services/orchestrator-service/main.py").read_text()
    assert '"real_execution_status": "pending"' in source
    assert '"downstream_block_reason": None' in source
    assert '"downstream_gate_trace": []' in source
    assert '"shadow_exit_policy_version": 2' in source
    assert '"shadow_portfolio_exits_excluded": True' in source
    assert 'execution_status = "executed" if executed else "blocked"' in source
    assert 'execution_status = "not_selected"' in source
    assert "recorder.set_finish_callback(_finish_shadow_dispositions)" in source


def test_runtime_blocked_coin_path_creates_shadow_positions():
    source = (ROOT / "services/orchestrator-service/main.py").read_text()
    blocked_path = source[
        source.index("async def _record_hyperliquid_blocked_signal_diagnostics") :
        source.index("async def _refresh_hyperliquid_pair_selections")
    ]
    assert "await self._record_hyperliquid_shadow_trades(" in blocked_path
    assert "apply_outcome_to_all=True" in blocked_path


def test_shadow_summary_groups_by_execution_disposition():
    source = (ROOT / "services/database-service/main.py").read_text()
    summary = source[
        source.index('app.get("/api/v1/perps/paper-shadow-summary")') :
        source.index('app.post("/api/v1/perps/adaptive-pnl-decisions/sync")')
    ]
    assert "real_execution_status" in summary
    assert "downstream_block_reason" in summary
    assert "shadow_exit_policy_version" in summary
    assert "COUNT(*) AS opportunity_count" in summary
    assert "enrich_shadow_summary_cohorts" in summary
    assert '"episode_reporting": True' in summary
