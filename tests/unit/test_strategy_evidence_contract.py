"""Regression coverage for comparable strategy evidence contracts."""

from pathlib import Path

from core.perp_paper_pnl_report import build_paper_pnl_report
from core.shadow_episode_summary import (
    episode_stats_by_cohort,
    independent_closed_episode_rows,
    shadow_summary_totals,
)
from core.spot_pnl_report import build_spot_pnl_report
from core.strategy_trade_evidence import (
    build_strategy_entry_evidence,
    encode_strategy_evidence_entry_reason,
    freeze_perp_close_evidence,
    parse_strategy_evidence_entry_reason,
)


def test_entry_evidence_is_deterministic_and_round_trips_through_spot_reason():
    signal = {
        "strategy": "portfolio_trend",
        "strategy_version": "2026.07",
        "stable_regime": "trending_up",
        "timeframe_bundle": {"trend": "1h", "entry": "15m"},
        "details": {
            "state": {
                "indicators": {
                    "signal_candle_ts": "2026-07-09T18:15:00Z",
                    "entry_reason_detail": "1h trend aligned; 15m pullback confirmed",
                    "rsi": 48.2,
                }
            }
        },
    }
    config = {"entry": {"rsi_min": 45}, "timeframes": ["1h", "15m"]}

    first = build_strategy_entry_evidence(signal, strategy_config=config)
    second = build_strategy_entry_evidence(signal, strategy_config=config)

    assert first == second
    assert first["strategy_key"] == "portfolio_trend"
    assert first["strategy_version"] == "2026.07"
    assert first["strategy_config_hash"] != "unknown"
    assert first["timeframe_bundle"] == {"trend": "1h", "entry": "15m"}
    assert first["signal_candle_timestamp"] == "2026-07-09T18:15:00+00:00"
    assert first["market_regime"] == "trending_up"
    assert first["rationale"]["why"] == "1h trend aligned; 15m pullback confirmed"
    assert first["rationale"]["status"] == "entered"

    encoded = encode_strategy_evidence_entry_reason("strategy signal", first)
    assert parse_strategy_evidence_entry_reason(encoded) == first


def test_entry_evidence_uses_named_timeframes_and_version_from_config():
    evidence = build_strategy_entry_evidence(
        {"strategy": "portfolio_trend", "why": "aligned"},
        strategy_config={
            "version": "v4",
            "parameters": {
                "trend_timeframe": "1h",
                "entry_timeframe": "15m",
            },
        },
    )

    assert evidence["strategy_version"] == "v4"
    assert evidence["timeframe_bundle"] == {"trend": "1h", "entry": "15m"}


def test_new_entry_without_declared_version_gets_deterministic_config_version():
    config = {"parameters": {"trend_timeframe": "1h", "entry_timeframe": "15m"}}

    evidence = build_strategy_entry_evidence(
        {"strategy": "portfolio_trend", "why": "aligned"},
        strategy_config=config,
    )

    assert evidence["strategy_version"].startswith("config-")
    assert evidence["strategy_version"] == (
        f"config-{evidence['strategy_config_hash'][:12]}"
    )


def test_perp_report_exposes_closed_versioned_evidence_and_preserves_costs():
    rows = [
        {
            "status": "CLOSED",
            "venue": "hyperliquid",
            "coin": "ETH",
            "source_strategy": "portfolio_trend",
            "position_side": "long",
            "entry_time": "2026-07-09T18:00:00+00:00",
            "exit_time": "2026-07-09T20:00:00+00:00",
            "realized_pnl": 4.0,
            "fees": 1.0,
            "funding": 0.25,
            "exit_reason": "paper_take_profit@1.2",
            "metadata": {
                "strategy_key": "portfolio_trend",
                "strategy_version": "v2",
                "strategy_config_hash": "cfg-v2",
                "timeframe_bundle": {"trend": "1h", "entry": "15m"},
                "signal_candle_timestamp": "2026-07-09T17:45:00+00:00",
                "market_regime": "trending_up",
                "rationale": {"why": "trend and pullback aligned", "status": "entered"},
                "mfe_pct": 2.5,
                "mae_pct": -0.4,
                "exit_policy_snapshot": {"version": "exit-v3"},
            },
        },
        {
            "status": "CLOSED",
            "coin": "BTC",
            "source_strategy": "legacy",
            "position_side": "short",
            "entry_time": "2026-07-09T18:00:00+00:00",
            "exit_time": "2026-07-09T20:00:00+00:00",
            "realized_pnl": 99.0,
            "metadata": {"accounting_excluded": True},
        },
    ]

    report = build_paper_pnl_report(rows, hours=0)
    evidence = report["normalizedEvidence"]

    assert len(evidence) == 1
    assert evidence[0]["strategyVersion"] == "v2"
    assert evidence[0]["venue"] == "hyperliquid"
    assert evidence[0]["exitBucket"] == "take_profit"
    assert evidence[0]["costs"] == {"fees": 1.0, "funding": 0.25, "total": 1.25}
    assert evidence[0]["why"] == "trend and pullback aligned"
    assert evidence[0]["status"] == "closed"
    assert report["breakdowns"]["strategyVersion"][0]["label"] == "portfolio_trend@v2"


def test_legacy_perp_evidence_is_retained_as_unversioned_unknown():
    report = build_paper_pnl_report(
        [
            {
                "status": "CLOSED",
                "source_strategy": "legacy_alpha",
                "position_side": "long",
                "coin": "SOL",
                "entry_time": "2026-07-09T18:00:00+00:00",
                "exit_time": "2026-07-09T19:00:00+00:00",
                "realized_pnl": -1.0,
            }
        ],
        hours=0,
    )

    evidence = report["normalizedEvidence"][0]
    assert evidence["strategyKey"] == "legacy_alpha"
    assert evidence["strategyVersion"] == "unversioned"
    assert evidence["configHash"] == "unknown"
    assert evidence["why"] == "unknown"
    assert evidence["status"] == "closed"


def test_spot_evidence_reports_mfe_and_explicitly_unavailable_mae():
    entry_evidence = {
        "strategy_key": "portfolio_trend",
        "strategy_version": "v3",
        "strategy_config_hash": "cfg-v3",
        "timeframe_bundle": {"trend": "1h", "entry": "15m"},
        "signal_candle_timestamp": "2026-07-09T17:45:00+00:00",
        "rationale": {"why": "15m close confirmed 1h bias", "status": "entered"},
    }
    reason = encode_strategy_evidence_entry_reason("portfolio signal", entry_evidence)
    report = build_spot_pnl_report(
        [],
        [
            {
                "status": "CLOSED",
                "exchange": "binance",
                "pair": "ETHUSDC",
                "strategy": "portfolio_trend",
                "entry_price": 100.0,
                "highest_price": 106.0,
                "realized_pnl": 3.0,
                "entry_fee_amount": 0.2,
                "exit_fee_amount": 0.3,
                "entry_time": "2026-07-09T18:00:00+00:00",
                "exit_time": "2026-07-09T20:00:00+00:00",
                "exit_reason": "take_profit",
                "entry_reason": reason,
                "market_regime": "trending_up",
            }
        ],
        hours=0,
    )

    evidence = report["normalizedEvidence"][0]
    assert evidence["strategyVersion"] == "v3"
    assert evidence["venue"] == "binance"
    assert evidence["coin"] == "ETH/USDC"
    assert evidence["mfePct"] == 6.0
    assert evidence["maePct"] is None
    assert evidence["maeStatus"] == "unavailable"
    assert evidence["costs"]["total"] == 0.5
    assert evidence["why"] == "15m close confirmed 1h bias"


def test_shadow_episode_dedup_keeps_overlapping_strategy_versions_separate():
    def row(version: str):
        return {
            "source_strategy": "portfolio_trend",
            "coin": "ETH",
            "position_side": "long",
            "status": "CLOSED",
            "entry_time": "2026-07-09T18:00:00+00:00",
            "exit_time": "2026-07-09T19:00:00+00:00",
            "realized_pnl": 1.0,
            "metadata": {"strategy_version": version},
        }

    rows = [row("v1"), row("v2")]
    assert len(independent_closed_episode_rows(rows)) == 2
    assert len(episode_stats_by_cohort(rows)) == 2


def test_shadow_totals_expose_episode_deduplicated_normalized_evidence():
    base = {
        "source_strategy": "portfolio_trend",
        "coin": "ETH",
        "position_side": "long",
        "status": "CLOSED",
        "entry_time": "2026-07-09T18:00:00+00:00",
        "exit_time": "2026-07-09T19:00:00+00:00",
        "realized_pnl": 1.0,
        "fees": 0.2,
        "exit_reason": "paper_take_profit@1.0",
        "metadata": {
            "strategy_version": "v2",
            "market_regime": "trending_up",
            "rationale": {"why": "shadow setup qualified", "status": "entered"},
        },
    }
    duplicate = {
        **base,
        "entry_time": "2026-07-09T18:15:00+00:00",
        "exit_time": "2026-07-09T19:15:00+00:00",
    }

    totals = shadow_summary_totals(
        [{"closed_count": 2, "realized_pnl": 2.0}],
        [base, duplicate],
    )

    assert len(totals["normalizedEvidence"]) == 1
    assert totals["normalizedEvidence"][0]["strategyVersion"] == "v2"
    assert totals["normalizedEvidence"][0]["why"] == "shadow setup qualified"


def test_shadow_database_transport_selects_version_and_exit_evidence_fields():
    source = Path("services/database-service/main.py").read_text()
    shadow_query = source[source.index('@app.get("/api/v1/perps/paper-shadow-summary")') :]
    shadow_query = shadow_query[: shadow_query.index('@app.post("/api/v1/perps/adaptive-pnl-decisions/sync")')]

    assert "AS strategy_version" in shadow_query
    assert "strategy_version," in shadow_query
    assert "funding," in shadow_query
    assert "exit_reason," in shadow_query
    assert "venue," in shadow_query


def test_perp_close_evidence_freezes_excursions_and_exit_policy():
    metadata = {
        "highest_price": 110.0,
        "lowest_price": 96.0,
        "strategy_version": "v2",
    }
    frozen = freeze_perp_close_evidence(
        metadata,
        side="long",
        entry_price=100.0,
        exit_policy={"version": "exit-v3", "stop_loss_pct": 2.0},
    )

    assert frozen["mfe_pct"] == 10.0
    assert frozen["mae_pct"] == -4.0
    assert frozen["excursion_status"] == "frozen"
    assert frozen["exit_policy_snapshot"] == {
        "version": "exit-v3",
        "stop_loss_pct": 2.0,
    }
    assert metadata == {
        "highest_price": 110.0,
        "lowest_price": 96.0,
        "strategy_version": "v2",
    }
