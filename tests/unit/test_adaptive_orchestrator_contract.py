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
    # Regime-block newValue is a side list; hold path must not float() it.
    assert 'decision_type in {' in text
    assert '"scale_up_regime_side"' in text
    assert "except (TypeError, ValueError):" in text


def test_adaptive_pnl_control_endpoint_omits_static_note():
    text = ORCH_PATH.read_text()
    endpoint = text.split('async def get_hyperliquid_adaptive_pnl_control():', 1)[1]
    endpoint = endpoint.split("async def post_hyperliquid_adaptive_pnl_control_reevaluate():", 1)[0]
    assert '"note"' not in endpoint
    assert '"adaptiveConfig"' in endpoint
