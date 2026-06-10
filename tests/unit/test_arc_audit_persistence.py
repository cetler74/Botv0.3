"""Unit tests for ARC audit helpers."""

from __future__ import annotations

from strategy.playbooks.arc_audit import build_arc_audit_payload
from core.arc_audit_summary import build_arc_audit_summary


def test_build_arc_audit_payload():
    payload = build_arc_audit_payload(
        "binance",
        "BTC/USDC",
        {
            "signal": "hold",
            "timestamp": "2025-06-09T12:00:00Z",
            "state": {
                "indicators": {
                    "setup": "arc_daytrade",
                    "area_pass": True,
                    "range_pass": False,
                    "candle_pass": False,
                    "zone": "buy",
                    "box_high": 110,
                    "box_low": 95,
                }
            },
        },
    )
    assert payload is not None
    assert payload["venue"] == "binance"
    assert payload["area_pass"] is True


def test_build_arc_audit_payload_skips_wrong_setup():
    assert build_arc_audit_payload("binance", "BTC/USDC", {"state": {"indicators": {"setup": "other"}}}) is None


def test_build_arc_audit_summary():
    rows = [
        {"area_pass": True, "range_pass": True, "candle_pass": False, "zone": "buy", "signal": "hold"},
        {"area_pass": True, "range_pass": False, "candle_pass": False, "zone": "sell", "signal": "hold"},
    ]
    summary = build_arc_audit_summary(rows)
    assert summary["totalEvaluations"] == 2
    assert summary["gatePassRates"]["area"] == 100.0
    assert summary["gatePassRates"]["range"] == 50.0
