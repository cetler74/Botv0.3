"""Unit tests for supply/demand audit helpers."""

from __future__ import annotations

from strategy.playbooks.supply_demand_audit import build_supply_demand_audit_payload
from core.supply_demand_audit_summary import build_supply_demand_audit_summary


def test_build_audit_payload_from_strategy_result():
    payload = build_supply_demand_audit_payload(
        "binance",
        "BTC/USDC",
        {
            "signal": "hold",
            "timestamp": "2025-06-07T12:00:00Z",
            "state": {
                "indicators": {
                    "setup": "supply_demand_3step",
                    "step1_pass": True,
                    "step2_pass": False,
                    "step3_pass": False,
                    "step1_reason": "uptrend",
                    "trend_direction": "uptrend",
                    "timeframe_mode": "multi",
                    "structure_timeframe": "1h",
                    "entry_timeframe": "15m",
                }
            },
        },
    )
    assert payload is not None
    assert payload["venue"] == "binance"
    assert payload["step1_pass"] is True
    assert payload["step2_pass"] is False


def test_build_audit_payload_skips_non_setup():
    assert build_supply_demand_audit_payload("binance", "BTC/USDC", {"state": {"indicators": {}}}) is None


def test_build_audit_summary_pass_rates():
    rows = [
        {"step1_pass": True, "step2_pass": True, "step3_pass": False, "trend_direction": "uptrend", "signal": "hold", "asset_class": "crypto"},
        {"step1_pass": True, "step2_pass": False, "step3_pass": False, "trend_direction": "uptrend", "signal": "hold", "asset_class": "crypto"},
    ]
    summary = build_supply_demand_audit_summary(rows)
    assert summary["totalEvaluations"] == 2
    assert summary["stepPassRates"]["step1"] == 100.0
    assert summary["stepPassRates"]["step2"] == 50.0
    assert summary["stepPassCounts"]["step3"] == 0
