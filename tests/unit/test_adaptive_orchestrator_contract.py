from pathlib import Path


ORCH_PATH = Path("services/orchestrator-service/main.py")


def test_orchestrator_exposes_adaptive_reevaluate_endpoint():
    text = ORCH_PATH.read_text()

    assert '"/api/v1/perps/adaptive-pnl-control/reevaluate"' in text
    assert "_hyperliquid_adaptive_runtime_cfg" in text
    assert 'apply_status="pending_cycle"' in text


def test_orchestrator_holds_recent_decisions_before_release():
    text = ORCH_PATH.read_text()

    assert "recentReleaseHoldHours" in text
    assert "reduce_recent_strategy_side" in text
    assert "block_recent_regime_side" in text
    assert "_recent_decision_still_in_hold" in text
    assert "control.setdefault(\"entrySizing\", {})" in text
    assert "control.setdefault(\"blockedRegimeSides\", {})" in text
