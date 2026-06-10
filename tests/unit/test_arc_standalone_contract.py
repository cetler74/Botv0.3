"""Standalone registration contract for ARC daytrade."""

from __future__ import annotations

import re
from pathlib import Path

from strategy.hyperliquid.mapping import HYPERLIQUID_STRATEGY_MAPPING


def test_arc_daytrade_in_hl_mapping():
    assert "arc_daytrade" in HYPERLIQUID_STRATEGY_MAPPING


def test_orchestrator_lists_arc_standalone():
    source = (
        Path(__file__).resolve().parents[2]
        / "services/orchestrator-service/hyperliquid_perps.py"
    ).read_text(encoding="utf-8")
    assert '"arc_daytrade"' in source
    assert "PRIORITY_STANDALONE_ENTRY_STRATEGIES" in source
    assert "arc_daytrade_specialist_gate" in source


def test_orchestrator_main_lists_arc_setup_standalone():
    """Spot entries must use setup_standalone_names like dual_sma / supply_demand."""
    main_path = (
        Path(__file__).resolve().parents[2]
        / "services/orchestrator-service/main.py"
    )
    source = main_path.read_text(encoding="utf-8")
    setup_match = re.search(
        r"setup_standalone_names\s*=\s*\((.*?)\)",
        source,
        re.DOTALL,
    )
    assert setup_match is not None
    setup_block = setup_match.group(1)
    assert '"arc_daytrade"' in setup_block
    assert '"dual_sma_daytrade"' in setup_block
    assert '"supply_demand_3step"' in setup_block

    excluded_blocks = re.findall(
        r"consensus_excluded_strategies\s*=\s*\{(.*?)\}",
        source,
        re.DOTALL,
    )
    assert len(excluded_blocks) >= 2
    for block in excluded_blocks:
        assert '"arc_daytrade"' in block


def test_strategy_service_lists_arc_standalone():
    source = (
        Path(__file__).resolve().parents[2] / "services/strategy-service/main.py"
    ).read_text(encoding="utf-8")
    assert '"arc_daytrade"' in source
    assert "standalone_name" in source or "standalone_buy_strategies" in source


def test_orchestrator_main_lists_arc_setup_stop_and_risk_sizing():
    """Spot exits and risk sizing must honor John Wick setup stops for ARC."""
    source = (
        Path(__file__).resolve().parents[2]
        / "services/orchestrator-service/main.py"
    ).read_text(encoding="utf-8")
    setup_stop_match = re.search(
        r"current_stop_loss = -abs\(stop_val\)\s*\n\s*logger\.info\(",
        source,
    )
    assert setup_stop_match is not None
    setup_stop_block = source[
        max(0, setup_stop_match.start() - 400) : setup_stop_match.start()
    ]
    assert '"arc_daytrade"' in setup_stop_block

    risk_match = re.search(
        r"if strategy_name in \{(.*?)\} and stop_pct > 0:",
        source,
        re.DOTALL,
    )
    assert risk_match is not None
    assert '"arc_daytrade"' in risk_match.group(1)


def test_hl_perps_lists_arc_setup_stop_strategies():
    source = (
        Path(__file__).resolve().parents[2]
        / "services/orchestrator-service/hyperliquid_perps.py"
    ).read_text(encoding="utf-8")
    stop_match = re.search(
        r"def _effective_stop_pct\(.*?return float\(cfg\.stop_loss_pct\)",
        source,
        re.DOTALL,
    )
    assert stop_match is not None
    assert '"arc_daytrade"' in stop_match.group(0)


def test_config_disables_stagnant_loser_for_arc_daytrade():
    import yaml

    config_path = Path(__file__).resolve().parents[2] / "config/config.yaml"
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    trading = cfg.get("trading") or {}
    stagnant = trading.get("stagnant_loser") or {}
    disabled = {str(s).strip().lower() for s in (stagnant.get("disabled_strategies") or [])}
    assert "arc_daytrade" in disabled

    spot_profiles = trading.get("exit_profiles") or {}
    arc_spot = spot_profiles.get("arc_daytrade") or {}
    assert arc_spot.get("stagnant_loser_enabled") is False
    assert arc_spot.get("use_setup_targets") is False
    assert float((arc_spot.get("trailing_stop") or {}).get("activation_threshold")) <= 0.0100
    assert float((arc_spot.get("profit_protection") or {}).get("activation_threshold")) <= 0.0100

    hl_profiles = (
        ((cfg.get("trading") or {}).get("hyperliquid_perps") or {}).get("exit_profiles") or {}
    )
    arc_profile = hl_profiles.get("arc_daytrade") or {}
    assert arc_profile.get("stagnant_loser_enabled") is False
    assert arc_profile.get("use_setup_stops") is True


def test_orchestrator_main_uses_spot_exit_profiles():
    source = (
        Path(__file__).resolve().parents[2]
        / "services/orchestrator-service/main.py"
    ).read_text(encoding="utf-8")
    assert "spot_strategy_exit_rules_from_trading_config" in source
    assert "[SpotExitProfile]" in source


def test_orchestrator_main_skips_stagnant_loser_for_disabled_strategies():
    source = (
        Path(__file__).resolve().parents[2]
        / "services/orchestrator-service/main.py"
    ).read_text(encoding="utf-8")
    assert "is_stagnant_loser_disabled_for_strategy" in source
    assert "not stagnant_disabled" in source
