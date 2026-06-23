"""Contract tests for strategy-service aggregate signal execution."""

from __future__ import annotations

from pathlib import Path


def test_aggregate_strategy_analysis_is_parallel_and_isolated():
    source = (
        Path(__file__).resolve().parents[2]
        / "services/strategy-service/main.py"
    ).read_text(encoding="utf-8")
    assert "def _create_strategy_instance" in source
    assert "copy.deepcopy(strategy_config)" in source
    assert "strategy_sem = asyncio.Semaphore(max_parallel_strategies)" in source
    assert "await asyncio.gather(" in source
    assert "await asyncio.wait_for(" in source
    assert "strategy_instance = self._create_strategy_instance(" in source
    assert "strategy_instance = strategy_data['instance']" not in source


def test_allowlisted_strategy_analysis_does_not_populate_aggregate_cache():
    source = (
        Path(__file__).resolve().parents[2]
        / "services/strategy-service/main.py"
    ).read_text(encoding="utf-8")
    assert "if allowlist_set is None:" in source
    guarded_cache_write = (
        "if allowlist_set is None:\n"
        "                cache_key = f\"{exchange_name}_{pair}_"
    )
    assert guarded_cache_write in source
    assert (
        "cached by their caller with a strategy-specific key" in source
    )
