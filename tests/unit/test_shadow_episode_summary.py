"""Unit tests for shadow independent-episode aggregation."""

from core.shadow_episode_summary import (
    enrich_shadow_summary_cohorts,
    independent_closed_episode_rows,
    shadow_summary_totals,
)


def _shadow_row(
    *,
    strategy: str,
    coin: str,
    side: str,
    entry: str,
    exit_time: str,
    pnl: float,
    regime: str = "trending_up",
    execution_status: str = "not_selected",
    block_reason: str = "cross_strategy_selection",
):
    return {
        "source_strategy": strategy,
        "coin": coin,
        "position_side": side,
        "status": "CLOSED",
        "entry_time": entry,
        "exit_time": exit_time,
        "realized_pnl": pnl,
        "fees": 0.2,
        "metadata": {
            "market_regime": regime,
            "shadow_edge_passed": "true",
            "real_execution_status": execution_status,
            "downstream_block_reason": block_reason,
            "shadow_exit_policy_version": "2",
        },
    }


def test_independent_episodes_collapse_overlapping_rescans():
    rows = [
        _shadow_row(
            strategy="supply_demand_3step",
            coin="XYZ:MSTR",
            side="long",
            entry="2026-06-21T10:00:00+00:00",
            exit_time="2026-06-21T11:00:00+00:00",
            pnl=2.0,
        ),
        _shadow_row(
            strategy="supply_demand_3step",
            coin="XYZ:MSTR",
            side="long",
            entry="2026-06-21T10:30:00+00:00",
            exit_time="2026-06-21T11:30:00+00:00",
            pnl=2.5,
        ),
        _shadow_row(
            strategy="supply_demand_3step",
            coin="XYZ:MSTR",
            side="long",
            entry="2026-06-21T12:30:00+00:00",
            exit_time="2026-06-21T13:00:00+00:00",
            pnl=-1.0,
        ),
    ]
    episodes = independent_closed_episode_rows(rows)
    assert len(episodes) == 2
    assert float(episodes[0]["realized_pnl"]) == 2.0
    assert float(episodes[1]["realized_pnl"]) == -1.0


def test_enrich_shadow_summary_adds_episode_fields():
    trades = [
        _shadow_row(
            strategy="supply_demand_3step",
            coin="XYZ:MSTR",
            side="long",
            entry="2026-06-21T10:00:00+00:00",
            exit_time="2026-06-21T11:00:00+00:00",
            pnl=2.0,
        ),
        _shadow_row(
            strategy="supply_demand_3step",
            coin="XYZ:MSTR",
            side="long",
            entry="2026-06-21T10:20:00+00:00",
            exit_time="2026-06-21T11:20:00+00:00",
            pnl=2.5,
        ),
    ]
    cohorts = [
        {
            "source_strategy": "supply_demand_3step",
            "position_side": "long",
            "market_regime": "trending_up",
            "edge_gate_passed": "true",
            "real_execution_status": "not_selected",
            "downstream_block_reason": "cross_strategy_selection",
            "shadow_exit_policy_version": "2",
            "closed_count": 2,
            "realized_pnl": 4.5,
        }
    ]
    enriched = enrich_shadow_summary_cohorts(cohorts, trades)
    assert enriched[0]["closed_count"] == 2
    assert enriched[0]["episode_closed_count"] == 1
    assert enriched[0]["episode_realized_pnl"] == 2.0


def test_edge_gate_normalization_matches_sql_cohort_keys():
    row = _shadow_row(
        strategy="supply_demand_3step",
        coin="XYZ:MSTR",
        side="long",
        entry="2026-06-21T10:00:00+00:00",
        exit_time="2026-06-21T11:00:00+00:00",
        pnl=1.0,
    )
    row["metadata"]["shadow_edge_passed"] = True
    cohort = {
        "source_strategy": "supply_demand_3step",
        "position_side": "long",
        "market_regime": "trending_up",
        "edge_gate_passed": "true",
        "real_execution_status": "not_selected",
        "downstream_block_reason": "cross_strategy_selection",
        "shadow_exit_policy_version": "2",
        "closed_count": 1,
        "realized_pnl": 1.0,
    }
    enriched = enrich_shadow_summary_cohorts([cohort], [row])
    assert enriched[0]["episode_closed_count"] == 1


def test_shadow_summary_totals_reports_inflation_ratio():
    trades = [
        _shadow_row(
            strategy="supply_demand_3step",
            coin="XYZ:MSTR",
            side="long",
            entry="2026-06-21T10:00:00+00:00",
            exit_time="2026-06-21T11:00:00+00:00",
            pnl=1.0,
        ),
        _shadow_row(
            strategy="supply_demand_3step",
            coin="XYZ:MSTR",
            side="long",
            entry="2026-06-21T10:15:00+00:00",
            exit_time="2026-06-21T11:15:00+00:00",
            pnl=1.0,
        ),
    ]
    cohorts = [{"closed_count": 2, "realized_pnl": 2.0}]
    totals = shadow_summary_totals(cohorts, trades)
    assert totals["raw"]["closed_count"] == 2
    assert totals["episode"]["closed_count"] == 1
    assert totals["episode_inflation_ratio"] == 2.0
