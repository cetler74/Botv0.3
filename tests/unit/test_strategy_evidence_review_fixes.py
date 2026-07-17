"""Regression tests for blocking strategy-evidence review findings."""

from __future__ import annotations

import importlib.util
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from psycopg2.extras import Json

from core.perp_paper_pnl_report import build_paper_pnl_report
from core.shadow_episode_summary import (
    independent_closed_episode_rows,
    shadow_promotion_cohorts_from_trades,
)
from core.spot_pnl_report import build_spot_pnl_report
from core.strategy_trade_evidence import build_strategy_entry_evidence


ROOT = Path(__file__).resolve().parents[2]
_DATABASE_MODULE = None


@pytest.mark.parametrize(
    "candidate",
    [
        {
            "strategy": "portfolio_trend",
            "timestamp": "2026-07-09T18:15:00-04:00",
            "details": {"state": {"entry_reason": "aligned"}},
        },
        {
            "strategy": "portfolio_trend",
            "details": {
                "timestamp": "2026-07-09T18:15:00-04:00",
                "state": {"entry_reason": "aligned"},
            },
        },
    ],
)
def test_runtime_strategy_candidate_timestamp_is_extracted_as_utc(candidate):
    """Match strategy-service row and orchestrator selected-candidate shapes."""
    evidence = build_strategy_entry_evidence(candidate)

    assert evidence["signal_candle_timestamp"] == "2026-07-09T22:15:00+00:00"


def test_shadow_promotion_keeps_strategy_versions_and_hashes_separate():
    rows = []
    for index, (version, config_hash, pnl) in enumerate(
        [("v1", "hash-one", 2.0), ("v2", "hash-two", -1.0)]
    ):
        rows.append(
            {
                "source_strategy": "portfolio_trend",
                "coin": "ETH",
                "position_side": "long",
                "status": "CLOSED",
                "entry_time": f"2026-07-0{index + 1}T10:00:00+00:00",
                "exit_time": f"2026-07-0{index + 1}T11:00:00+00:00",
                "realized_pnl": pnl,
                "metadata": {
                    "strategy_version": version,
                    "strategy_config_hash": config_hash,
                    "market_regime": "trending_up",
                },
            }
        )

    cohorts = shadow_promotion_cohorts_from_trades(rows)

    assert len(cohorts) == 2
    assert ("ETH", "portfolio_trend", "long", "trending_up") not in cohorts
    assert {row["strategy_version"] for row in cohorts.values()} == {"v1", "v2"}
    assert {row["strategy_config_hash"] for row in cohorts.values()} == {
        "hash-one",
        "hash-two",
    }
    assert sorted(row["realized"] for row in cohorts.values()) == [-1.0, 2.0]

    reversed_cohorts = shadow_promotion_cohorts_from_trades(list(reversed(rows)))
    assert cohorts == reversed_cohorts
    assert set(cohorts) == {
        ("ETH", "portfolio_trend@v1#hash-one", "long", "trending_up"),
        ("ETH", "portfolio_trend@v2#hash-two", "long", "trending_up"),
    }


def test_shadow_promotion_uses_legacy_key_for_single_identity():
    row = {
        "source_strategy": "portfolio_trend",
        "coin": "ETH",
        "position_side": "long",
        "status": "CLOSED",
        "entry_time": "2026-07-01T10:00:00+00:00",
        "exit_time": "2026-07-01T11:00:00+00:00",
        "realized_pnl": 2.0,
        "metadata": {
            "strategy_version": "v1",
            "strategy_config_hash": "hash-one",
            "market_regime": "trending_up",
        },
    }

    cohorts = shadow_promotion_cohorts_from_trades([row])

    assert list(cohorts) == [
        ("ETH", "portfolio_trend", "long", "trending_up")
    ]


def test_shadow_promotion_does_not_collide_on_shared_hash_prefix():
    rows = []
    for index, config_hash in enumerate(
        ("same-prefix-aaaaaaaa", "same-prefix-bbbbbbbb")
    ):
        rows.append(
            {
                "source_strategy": "portfolio_trend",
                "coin": "ETH",
                "position_side": "long",
                "status": "CLOSED",
                "entry_time": f"2026-07-0{index + 1}T10:00:00Z",
                "exit_time": f"2026-07-0{index + 1}T11:00:00Z",
                "realized_pnl": 1.0,
                "metadata": {
                    "strategy_version": "v1",
                    "strategy_config_hash": config_hash,
                    "market_regime": "trending_up",
                },
            }
        )

    cohorts = shadow_promotion_cohorts_from_trades(rows)

    assert len(cohorts) == 2
    assert {row["strategy_config_hash"] for row in cohorts.values()} == {
        "same-prefix-aaaaaaaa",
        "same-prefix-bbbbbbbb",
    }


def test_shadow_episodes_normalize_mixed_timestamps_to_utc():
    def row(entry, exit_time, pnl):
        return {
            "source_strategy": "portfolio_trend",
            "coin": "ETH",
            "position_side": "long",
            "status": "CLOSED",
            "entry_time": entry,
            "exit_time": exit_time,
            "realized_pnl": pnl,
        }

    rows = [
        row("2026-07-09T10:00:00", "2026-07-09T11:00:00Z", 1.0),
        row("2026-07-09T12:30:00+02:00", "2026-07-09T13:30:00+02:00", 2.0),
        row("2026-07-09T12:00:00Z", "2026-07-09T13:00:00", 3.0),
    ]

    episodes = independent_closed_episode_rows(rows)

    assert [row["realized_pnl"] for row in episodes] == [1.0, 3.0]


def test_shadow_promotion_cutoff_normalizes_naive_and_offset_timestamps():
    rows = [
        {
            "source_strategy": "portfolio_trend",
            "coin": "ETH",
            "position_side": "long",
            "status": "CLOSED",
            "entry_time": "2026-07-09T09:00:00",
            "exit_time": "2026-07-09T10:00:00",
            "realized_pnl": 1.0,
        },
        {
            "source_strategy": "portfolio_trend",
            "coin": "SOL",
            "position_side": "long",
            "status": "CLOSED",
            "entry_time": "2026-07-09T12:00:00+02:00",
            "exit_time": "2026-07-09T13:00:00+02:00",
            "realized_pnl": 2.0,
        },
    ]

    cohorts = shadow_promotion_cohorts_from_trades(
        rows,
        cutoff=datetime(2026, 7, 9, 10, 30, tzinfo=timezone.utc),
    )

    assert {row["coin"] for row in cohorts.values()} == {"SOL"}


def test_perp_report_all_time_window_is_normalized_to_utc():
    report = build_paper_pnl_report(
        [
            {
                "status": "CLOSED",
                "source_strategy": "portfolio_trend",
                "position_side": "long",
                "coin": "ETH",
                "entry_time": "2026-07-09T18:00:00-04:00",
                "exit_time": "2026-07-09T20:00:00-04:00",
                "realized_pnl": 1.0,
            }
        ],
        hours=0,
    )

    assert report["windowStart"] == "2026-07-10T00:00:00+00:00"
    assert report["windowEnd"] == "2026-07-10T00:00:00+00:00"
    assert report["normalizedEvidence"][0]["windowTimestamp"] == (
        "2026-07-10T00:00:00+00:00"
    )


def test_spot_report_all_time_window_is_normalized_to_utc():
    report = build_spot_pnl_report(
        [],
        [
            {
                "status": "CLOSED",
                "exchange": "binance",
                "pair": "ETHUSDC",
                "strategy": "portfolio_trend",
                "entry_time": "2026-07-09T18:00:00+05:30",
                "exit_time": "2026-07-09T20:00:00+05:30",
                "realized_pnl": 1.0,
            }
        ],
        hours=0,
    )

    assert report["windowStart"] == "2026-07-09T14:30:00+00:00"
    assert report["windowEnd"] == "2026-07-09T14:30:00+00:00"
    assert report["normalizedEvidence"][0]["windowTimestamp"] == (
        "2026-07-09T14:30:00+00:00"
    )


def _load_database_service_module():
    global _DATABASE_MODULE
    if _DATABASE_MODULE is not None:
        return _DATABASE_MODULE
    service_dir = ROOT / "services/database-service"
    for import_dir in (service_dir, ROOT / "core"):
        import_path = str(import_dir)
        if import_path not in sys.path:
            sys.path.insert(0, import_path)
    stubbed_prometheus = "prometheus_client" not in sys.modules and importlib.util.find_spec(
        "prometheus_client"
    ) is None
    if stubbed_prometheus:
        prometheus = types.ModuleType("prometheus_client")
        prometheus.Counter = MagicMock
        prometheus.Histogram = MagicMock
        prometheus.Gauge = MagicMock
        prometheus.generate_latest = MagicMock(return_value=b"")
        prometheus.CONTENT_TYPE_LATEST = "text/plain"
        prometheus.CollectorRegistry = MagicMock
        sys.modules["prometheus_client"] = prometheus
    spec = importlib.util.spec_from_file_location(
        "database_service_strategy_evidence_test",
        service_dir / "main.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    finally:
        if stubbed_prometheus:
            sys.modules.pop("prometheus_client", None)
    _DATABASE_MODULE = module
    return module


class _FakeDatabaseManager:
    def __init__(self, existing):
        self.existing = existing
        self.select_calls = []
        self.update_calls = []

    async def execute_single_query(self, query, params=()):
        self.select_calls.append((query, params))
        return self.existing

    async def execute_query(self, query, params=()):
        self.update_calls.append((query, params))
        return []


def _json_param(params):
    return next(value.adapted for value in params if isinstance(value, Json))


@pytest.mark.asyncio
async def test_database_paper_close_path_freezes_existing_evidence():
    module = _load_database_service_module()
    fake = _FakeDatabaseManager(
        {
            "entry_price": 100.0,
            "position_side": "long",
            "metadata": {
                "highest_price": 108.0,
                "lowest_price": 97.0,
                "exit_policy_snapshot": {"version": "exit-v3"},
            },
        }
    )
    module.db_manager = fake

    await module.update_perp_paper_trade(
        "paper-1",
        {"status": "CLOSED", "exit_reason": "paper_take_profit"},
    )

    assert fake.select_calls
    persisted = _json_param(fake.update_calls[0][1])
    assert persisted["mfe_pct"] == 8.0
    assert persisted["mae_pct"] == -3.0
    assert persisted["excursion_status"] == "frozen"
    assert persisted["exit_policy_snapshot"] == {"version": "exit-v3"}


@pytest.mark.asyncio
async def test_database_live_close_path_marks_unavailable_without_invention():
    module = _load_database_service_module()
    fake = _FakeDatabaseManager(
        {
            "entry_price": 100.0,
            "position_side": "short",
            "metadata": {},
        }
    )
    module.db_manager = fake

    await module.update_perp_live_trade(
        "live-1",
        {
            "status": "CLOSED",
            "metadata_patch": {"hl_close_result": {"status": "filled"}},
        },
    )

    assert fake.select_calls
    persisted = _json_param(fake.update_calls[0][1])
    assert persisted["hl_close_result"] == {"status": "filled"}
    assert persisted["mfe_pct"] is None
    assert persisted["mae_pct"] is None
    assert persisted["excursion_status"] == "unavailable"
    assert persisted["exit_policy_status"] == "unavailable"
    assert "exit_policy_snapshot" not in persisted


@pytest.mark.asyncio
async def test_repeated_close_preserves_stored_frozen_evidence():
    module = _load_database_service_module()
    stored_metadata = {
        "highest_price": 110.0,
        "lowest_price": 95.0,
        "mfe_pct": 10.0,
        "mae_pct": -5.0,
        "excursion_status": "frozen",
        "exit_policy_snapshot": {"version": "trusted-v1"},
        "exit_policy_status": "frozen",
        "close_evidence_status": "complete",
    }
    fake = _FakeDatabaseManager(
        {
            "status": "CLOSED",
            "entry_price": 100.0,
            "position_side": "long",
            "metadata": stored_metadata,
        }
    )
    module.db_manager = fake

    await module.update_perp_paper_trade(
        "paper-closed",
        {
            "status": "CLOSED",
            "metadata": {
                "highest_price": 999.0,
                "lowest_price": 1.0,
                "mfe_pct": 899.0,
                "mae_pct": -99.0,
                "excursion_status": "unavailable",
                "exit_policy_snapshot": {"version": "crafted"},
                "audit_note": "second close observed",
            },
        },
    )

    persisted = _json_param(fake.update_calls[0][1])
    for key, value in stored_metadata.items():
        assert persisted[key] == value
    assert persisted["audit_note"] == "second close observed"


@pytest.mark.asyncio
async def test_first_close_uses_stored_extrema_before_incoming_overrides():
    module = _load_database_service_module()
    fake = _FakeDatabaseManager(
        {
            "status": "OPEN",
            "entry_price": 100.0,
            "position_side": "long",
            "metadata": {
                "highest_price": 108.0,
                "lowest_price": 97.0,
            },
        }
    )
    module.db_manager = fake

    await module.update_perp_paper_trade(
        "paper-first-close",
        {
            "status": "CLOSED",
            "metadata": {
                "highest_price": 500.0,
                "lowest_price": 2.0,
                "exit_policy_snapshot": {"version": "first-close-policy"},
            },
        },
    )

    persisted = _json_param(fake.update_calls[0][1])
    assert persisted["highest_price"] == 108.0
    assert persisted["lowest_price"] == 97.0
    assert persisted["mfe_pct"] == 8.0
    assert persisted["mae_pct"] == -3.0
    assert persisted["exit_policy_snapshot"] == {
        "version": "first-close-policy"
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("entry_price", "highest_price", "lowest_price", "expected_reason"),
    [
        ("not-a-number", 108.0, 97.0, "invalid_entry_price"),
        (100.0, "bad-high", 97.0, "invalid_extreme_price"),
        (100.0, 108.0, "bad-low", "invalid_extreme_price"),
    ],
)
async def test_malformed_close_numbers_persist_unavailable_evidence(
    entry_price,
    highest_price,
    lowest_price,
    expected_reason,
):
    module = _load_database_service_module()
    fake = _FakeDatabaseManager(
        {
            "status": "OPEN",
            "entry_price": entry_price,
            "position_side": "long",
            "metadata": {
                "highest_price": highest_price,
                "lowest_price": lowest_price,
            },
        }
    )
    module.db_manager = fake

    await module.update_perp_paper_trade(
        "paper-malformed",
        {"status": "CLOSED"},
    )

    persisted = _json_param(fake.update_calls[0][1])
    assert persisted["mfe_pct"] is None
    assert persisted["mae_pct"] is None
    assert persisted["excursion_status"] == "unavailable"
    assert persisted["excursion_unavailable_reason"] == expected_reason
    assert persisted["close_evidence_status"] == "unavailable"
