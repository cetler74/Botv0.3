import ast
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


def test_perp_entry_cycle_keeps_shared_http_client_open_until_finally():
    tree = ast.parse(ORCH_PATH.read_text())
    entry_cycle = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "_run_hyperliquid_strategy_entries"
    )

    creates_shared_client = any(
        isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "client" for target in node.targets)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr == "AsyncClient"
        for node in ast.walk(entry_cycle)
    )
    closes_shared_client_in_finally = any(
        isinstance(statement, ast.Await)
        and isinstance(statement.value, ast.Call)
        and isinstance(statement.value.func, ast.Attribute)
        and isinstance(statement.value.func.value, ast.Name)
        and statement.value.func.value.id == "client"
        and statement.value.func.attr == "aclose"
        for try_node in ast.walk(entry_cycle)
        if isinstance(try_node, ast.Try)
        for statement in ast.walk(ast.Module(body=try_node.finalbody, type_ignores=[]))
    )

    assert creates_shared_client
    assert closes_shared_client_in_finally
