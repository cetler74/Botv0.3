"""Unit tests for dual-SMA audit helpers."""

from __future__ import annotations

from strategy.playbooks.dual_sma_audit import build_dual_sma_audit_payload
from core.dual_sma_audit_summary import build_dual_sma_audit_summary


def test_build_audit_payload_from_strategy_result():
    payload = build_dual_sma_audit_payload(
        "hyperliquid",
        "BTC",
        {
            "signal": "hold",
            "timestamp": "2025-06-08T12:00:00Z",
            "state": {
                "indicators": {
                    "setup": "dual_sma_daytrade",
                    "daily_pass": True,
                    "confirm_15m_pass": False,
                    "entry_5m_pass": False,
                    "daily_bias": "bullish",
                    "trend_15m": "uptrend",
                    "entry_signal_5m": "hold",
                    "daily_reason": "bullish gap",
                }
            },
        },
    )
    assert payload is not None
    assert payload["venue"] == "hyperliquid"
    assert payload["daily_pass"] is True
    assert payload["confirm_15m_pass"] is False


def test_build_audit_payload_skips_non_setup():
    assert build_dual_sma_audit_payload("binance", "BTC/USDC", {"state": {"indicators": {}}}) is None


def test_build_audit_summary_gate_rates():
    rows = [
        {
            "daily_pass": True,
            "confirm_15m_pass": True,
            "entry_5m_pass": False,
            "precision_pass": True,
            "daily_bias": "bullish",
            "trend_15m": "uptrend",
            "signal": "hold",
        },
        {
            "daily_pass": True,
            "confirm_15m_pass": False,
            "entry_5m_pass": False,
            "precision_pass": False,
            "daily_bias": "bearish",
            "trend_15m": "flat",
            "signal": "hold",
        },
    ]
    summary = build_dual_sma_audit_summary(rows)
    assert summary["totalEvaluations"] == 2
    assert summary["gatePassRates"]["daily"] == 100.0
    assert summary["gatePassRates"]["confirm15m"] == 50.0
    assert summary["gatePassCounts"]["entry5m"] == 0
