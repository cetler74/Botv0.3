"""Standalone wiring contract for EMA50 breakout-pullback."""

from __future__ import annotations

import pathlib


def test_hyperliquid_perps_source_lists_ema50_standalone():
    perps_path = (
        pathlib.Path(__file__).resolve().parents[2]
        / "services/orchestrator-service/hyperliquid_perps.py"
    )
    source = perps_path.read_text(encoding="utf-8")
    assert '"ema50_breakout_pullback"' in source
    assert "PRIORITY_STANDALONE_ENTRY_STRATEGIES" in source
    assert "ema50_breakout_pullback_specialist_gate" in source


def test_orchestrator_main_source_lists_ema50_setup_standalone():
    main_path = (
        pathlib.Path(__file__).resolve().parents[2]
        / "services/orchestrator-service/main.py"
    )
    source = main_path.read_text(encoding="utf-8")
    assert '"ema50_breakout_pullback"' in source
    assert "setup_standalone_names" in source
