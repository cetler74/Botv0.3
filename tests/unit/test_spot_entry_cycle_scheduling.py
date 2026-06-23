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
    # The runtime fix uses a longer full-loop wall clock, but keeps spot entry
    # bounded separately so it cannot starve perps or hang indefinitely.
    assert tm.get("max_cycle_duration", 999) <= 180
    assert tm.get("entry_loop_reserve_seconds", 999) <= 35
    assert tm.get("perp_cycle_max_seconds", 0) <= 45
    assert tm.get("spot_cycle_max_seconds", 999) <= 110
    assert tm.get("spot_entry_pair_timeout_seconds", 999) <= 45
    assert tm.get("spot_entry_check_concurrency", 0) == 2
    assert tm.get("perp_entry_check_concurrency", 0) >= 2
    assert (tm.get("spot_entry_pair_priority") or {}).get("enabled") is True
    assert (tm.get("spot_entry_pair_priority") or {}).get("cache_ttl_seconds", 0) >= 60


def test_config_service_exposes_spot_cycle_bucket():
    source = (
        Path(__file__).resolve().parents[2]
        / "services/config-service/main.py"
    ).read_text(encoding="utf-8")
    assert '"spot_cycle_max_seconds"' in source


def test_entry_cycle_uses_parallel_pair_gather():
    source = (
        Path(__file__).resolve().parents[2]
        / "services/orchestrator-service/main.py"
    ).read_text(encoding="utf-8")
    assert "_loop_spot_entry_check_concurrency" in source
    assert "asyncio.Semaphore(entry_conc)" in source
    assert "asyncio.gather(" in source
    assert "_loop_perp_entry_check_concurrency" in source
    assert "Prefetching entry signals" in source


def test_pair_analysis_is_venue_scoped_but_execution_is_serialized():
    source = (
        Path(__file__).resolve().parents[2]
        / "services/orchestrator-service/main.py"
    ).read_text(encoding="utf-8")
    assert 'key = f"{str(exchange_name or \'\').strip().lower()}:{cluster}"' in source
    assert "async with self._spot_entry_execution_lock:" in source
    assert "await self._release_pair_entry_reservation(exchange_name, pair)" in source


def test_strategy_signal_fetches_are_fail_fast():
    source = (
        Path(__file__).resolve().parents[2]
        / "services/orchestrator-service/main.py"
    ).read_text(encoding="utf-8")
    assert "signal_attempts = 1" in source
    assert "signal_timeout = max(1.0, min(self._loop_spot_entry_pair_timeout_seconds, 8.0))" in source
    assert "timeout=90.0" not in source
    assert "signal_timeout = 180.0" not in source


def test_fast_entry_uses_redis_envelope_without_aggregate_fetch():
    source = (
        Path(__file__).resolve().parents[2]
        / "services/orchestrator-service/main.py"
    ).read_text(encoding="utf-8")
    assert "scan_iter(" in source
    assert 'match="trading:fast_signal:*"' in source
    assert "single_strategy_timeout_seconds" in source
    assert "active_hl_keys" in source
    assert "await self._fetch_hyperliquid_mids(allow_stale=True) if active_hl_keys else {}" in source
    assert "spot_signals_data_from_fast_payload" in source
    assert "[FastEntry] Spot using Redis" in source
    fast_branch = source[
        source.index("if fast_lane_payload:")
        : source.index("else:", source.index("if fast_lane_payload:"))
    ]
    assert "/api/v1/signals/{exchange_name}/{strategy_pair}" not in fast_branch


def test_maintenance_tasks_are_timeboxed():
    source = (
        Path(__file__).resolve().parents[2]
        / "services/orchestrator-service/main.py"
    ).read_text(encoding="utf-8")
    assert "async def run_with_budget" in source
    assert "asyncio.wait_for(task_factory()" in source
    assert "Maintenance timed out during" in source
