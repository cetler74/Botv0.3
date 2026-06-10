"""Contract tests for spot entry cycle scheduling changes."""

from __future__ import annotations

from pathlib import Path


def test_trading_loop_runs_entry_before_hyperliquid_perps():
    source = (
        Path(__file__).resolve().parents[2]
        / "services/orchestrator-service/main.py"
    ).read_text(encoding="utf-8")
    entry_idx = source.index("self._run_entry_cycle(deadline=spot_entry_deadline)")
    hl_idx = source.index('await self._run_hyperliquid_perps_cycle(deadline=perp_deadline)')
    assert entry_idx < hl_idx


def test_trading_loop_caps_perp_cycle():
    source = (
        Path(__file__).resolve().parents[2]
        / "services/orchestrator-service/main.py"
    ).read_text(encoding="utf-8")
    assert "_loop_perp_cycle_max_seconds" in source
    assert "perp_deadline = min(full_deadline, hl_start + self._loop_perp_cycle_max_seconds)" in source


def test_entry_cycle_prioritizes_audit_buy_pairs():
    source = (
        Path(__file__).resolve().parents[2]
        / "services/orchestrator-service/main.py"
    ).read_text(encoding="utf-8")
    assert "fetch_priority_entry_pairs" in source
    assert "order_pairs_by_priority" in source
    assert "spot_entry_pair_priority" in source


def test_config_trading_manager_entry_budget():
    import yaml

    cfg = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / "config/config.yaml").read_text(
            encoding="utf-8"
        )
    )
    tm = cfg.get("trading_manager") or {}
    assert tm.get("max_cycle_duration", 0) >= 180
    assert tm.get("entry_loop_reserve_seconds", 0) >= 60
    assert tm.get("perp_cycle_max_seconds", 0) == 30
    assert tm.get("spot_entry_pair_timeout_seconds", 0) == 12
    assert (tm.get("spot_entry_pair_priority") or {}).get("enabled") is True
    assert (tm.get("spot_entry_pair_priority") or {}).get("cache_ttl_seconds", 0) >= 60
