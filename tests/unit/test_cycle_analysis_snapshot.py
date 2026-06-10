"""Tests for HL perp cycle analysis snapshot helpers."""

import json
from unittest.mock import AsyncMock

import pytest

from core.cycle_analysis_snapshot import (
    SNAPSHOT_ENTRY_CONFIDENCE,
    build_entry_decision,
    build_hl_best_signal,
    build_hl_cycle_analysis,
    build_hl_strategy_rows,
    merge_hl_cycle_analysis_into_watchlist,
    merge_hl_cycle_snapshot,
    normalize_hl_coin,
    parse_hl_cycle_snapshot,
    read_hl_cycle_snapshots,
    write_hl_cycle_snapshot,
)


def _sample_signals_data():
    return {
        "stable_regime": "sideways",
        "consensus": {
            "signal": "hold",
            "confidence": 0.42,
            "agreement": 38.0,
            "participating_strategies": 12,
        },
        "strategies": {
            "macd_momentum": {
                "signal": "hold",
                "confidence": 0.58,
                "strength": 0.41,
                "state": {"indicators": {"skip_reason": "MACD rule not satisfied"}},
            },
            "rsi_stoch_reversal_5m": {
                "signal": "long",
                "confidence": 0.71,
                "strength": 0.68,
                "state": {
                    "indicators": {"entry_reason": "RSI+Stoch cross from oversold"},
                },
            },
            "vwma_hull": {
                "signal": "hold",
                "confidence": 0.55,
                "strength": 0.22,
                "state": {"indicators": {"skip_reason": "close below vwma_hull"}},
            },
        },
    }


def test_normalize_hl_coin():
    assert normalize_hl_coin("wld/usdc:usdc") == "WLD"
    assert normalize_hl_coin("ETH") == "ETH"


def test_build_hl_strategy_rows_formats_hold_and_long():
    rows = build_hl_strategy_rows(_sample_signals_data())
    by_name = {r["name"]: r for r in rows}
    assert by_name["macd_momentum"]["signal"] == "hold"
    assert "MACD rule not satisfied" in by_name["macd_momentum"]["reason"]
    assert by_name["rsi_stoch_reversal_5m"]["signal"] == "long"
    assert by_name["rsi_stoch_reversal_5m"]["confidence"] == 0.71
    assert by_name["rsi_stoch_reversal_5m"]["outcome"] == "long_signal"


def test_build_hl_cycle_analysis_includes_regime_and_best_signal():
    analysis = build_hl_cycle_analysis(_sample_signals_data())
    assert analysis["regime"] == "sideways"
    assert analysis["consensus"]["signal"] == "hold"
    assert analysis["consensus"]["agreement"] == 38.0
    assert analysis["bestSignal"]["strategy"] == "rsi_stoch_reversal_5m"
    assert analysis["bestSignal"]["side"] == "long"


def test_build_hl_best_signal_prefers_mirrored():
    strategies = build_hl_strategy_rows(_sample_signals_data())
    mirrored = {
        "strategy": "macd_momentum",
        "signal": "short",
        "confidence": 0.66,
        "strength": 0.5,
    }
    best = build_hl_best_signal(mirrored, strategies)
    assert best["strategy"] == "macd_momentum"
    assert best["side"] == "short"


def test_build_entry_decision_gate_chain():
    decision = build_entry_decision(
        outcome="skipped",
        primary_reason="edge_gate",
        message="expected move 0.48% < 0.60%",
        gate_chain=[
            {"step": "confidence", "passed": True, "message": ""},
            {"step": "edge_gate", "passed": False, "message": "expected move 0.48% < 0.60%"},
        ],
    )
    assert decision["outcome"] == "skipped"
    assert len(decision["gateChain"]) == 2
    assert decision["gateChain"][1]["passed"] is False


def test_merge_hl_cycle_snapshot_uses_mirrored_best_signal():
    mirrored = {
        "strategy": "rsi_stoch_reversal_5m",
        "signal": "long",
        "confidence": 0.71,
        "strength": 0.68,
    }
    snap = merge_hl_cycle_snapshot(
        coin="WLD",
        cycle_count=1842,
        signals_data=_sample_signals_data(),
        mirrored=mirrored,
        entry_decision=build_entry_decision(
            outcome="skipped",
            primary_reason="edge_gate",
            message="expected move 0.48% < 0.60%",
        ),
    )
    assert snap["coin"] == "WLD"
    assert snap["cycleCount"] == 1842
    assert snap["bestSignal"]["strategy"] == "rsi_stoch_reversal_5m"
    assert snap["entryDecision"]["primaryReason"] == "edge_gate"


def test_merge_hl_cycle_analysis_into_watchlist_attaches_by_coin():
    watchlist = [
        {"coin": "WLD", "status": "under_analysis"},
        {"coin": "ETH", "status": "open"},
    ]
    snapshots = {
        "WLD": merge_hl_cycle_snapshot(
            coin="WLD",
            signals_data=_sample_signals_data(),
            entry_decision=build_entry_decision(
                outcome="skipped",
                primary_reason="confidence",
                message="0.61 < 0.64",
            ),
        )
    }
    merged = merge_hl_cycle_analysis_into_watchlist(watchlist, snapshots)
    wld = next(r for r in merged if r["coin"] == "WLD")
    eth = next(r for r in merged if r["coin"] == "ETH")
    assert wld["cycleAnalysis"]["regime"] == "sideways"
    assert wld["cycleAnalysis"]["entryDecision"]["outcome"] == "skipped"
    assert eth["cycleAnalysis"]["entryDecision"]["outcome"] == "not_scanned"


def test_weak_signal_outcome_below_confidence_bar():
    data = {
        "strategies": {
            "test_strat": {
                "signal": "long",
                "confidence": SNAPSHOT_ENTRY_CONFIDENCE - 0.05,
                "strength": 0.2,
            }
        }
    }
    rows = build_hl_strategy_rows(data)
    assert rows[0]["outcome"] == "weak_long"
    assert "below entry-confidence bar" in rows[0]["reason"]


def test_parse_hl_cycle_snapshot_roundtrip():
    snap = {"coin": "SOL", "regime": "trending"}
    raw = json.dumps(snap)
    assert parse_hl_cycle_snapshot(raw) == snap
    assert parse_hl_cycle_snapshot(snap) == snap


@pytest.mark.asyncio
async def test_write_and_read_hl_cycle_snapshots():
    redis = AsyncMock()
    redis.set = AsyncMock(return_value=True)
    redis.mget = AsyncMock(
        return_value=[
            json.dumps({"coin": "WLD", "regime": "sideways"}),
            None,
        ]
    )
    snap = {"coin": "WLD", "regime": "sideways"}
    ok = await write_hl_cycle_snapshot(redis, "WLD", snap)
    assert ok is True
    redis.set.assert_awaited_once()
    out = await read_hl_cycle_snapshots(redis, ["WLD", "ETH"])
    assert "WLD" in out
    assert out["WLD"]["regime"] == "sideways"
    assert "ETH" not in out
