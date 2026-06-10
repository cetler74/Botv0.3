"""Contract tests for strategy expectancy probation exemption."""

from __future__ import annotations

from pathlib import Path


def test_orchestrator_supports_probation_exempt_strategies():
    source = (
        Path(__file__).resolve().parents[2]
        / "services/orchestrator-service/main.py"
    ).read_text(encoding="utf-8")
    assert "probation_exempt_strategies" in source
    assert "probation_exempt_fresh_sample" in source


def test_config_declares_probation_exempt_arc_and_supply_demand():
    import yaml

    cfg = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / "config/config.yaml").read_text(
            encoding="utf-8"
        )
    )
    guard = (cfg.get("trading") or {}).get("strategy_expectancy_guard") or {}
    exempt = guard.get("probation_exempt_strategies") or []
    assert "arc_daytrade" in exempt
    assert "supply_demand_3step" in exempt
