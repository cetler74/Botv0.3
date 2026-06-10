"""Unit tests for EMA50 breakout-pullback engine."""

from __future__ import annotations

import pandas as pd
import pytest

pytest.importorskip("pandas_ta")

from strategy.playbooks.ema50_breakout_pullback_engine import (
    EngineParams,
    evaluate_ema50_breakout_pullback,
)


def _synthetic_long_trigger_df() -> pd.DataFrame:
    """Build 4h bars: below EMA leg, breakout, leg highs, 2 bearish pullback, trigger."""
    n = 90
    rows = []
    price = 100.0
    for i in range(n):
        if i < 50:
            o = price
            c = price - 0.2
            h = o + 0.1
            l = c - 0.1
        elif i == 50:
            o = price
            c = price + 2.0
            h = c + 0.5
            l = o - 0.1
        elif 51 <= i <= 53:
            o = price
            c = price + 0.8
            h = c + 0.6
            l = o - 0.05
        elif i in (54, 55):
            o = price + 0.8
            c = o - 0.5
            h = o + 0.1
            l = c - 0.1
        elif i == 56:
            swing_high = max(r["high"] for r in rows[50:54])
            o = price
            c = swing_high + 0.5
            h = c + 0.2
            l = o - 0.1
        else:
            o = price
            c = price + 0.05
            h = c + 0.1
            l = o - 0.05
        rows.append({"open": o, "high": h, "low": l, "close": c, "volume": 1000})
        price = c
    idx = pd.date_range("2025-01-01", periods=n, freq="4h")
    return pd.DataFrame(rows, index=idx)


def test_hold_on_insufficient_candles():
    params = EngineParams(min_candles=200)
    df = _synthetic_long_trigger_df().iloc[:30]
    result = evaluate_ema50_breakout_pullback({"4h": df}, params)
    assert result.signal == "hold"
    assert "insufficient" in result.invalidation_reason


def test_hold_when_regime_blocked():
    params = EngineParams(blocked_regimes=["sideways"])
    df = _synthetic_long_trigger_df()
    result = evaluate_ema50_breakout_pullback({"4h": df}, params, market_regime="sideways")
    assert result.signal == "hold"
    assert "regime_blocked" in result.invalidation_reason


def test_long_trigger_emits_buy_with_setup_fields():
    df = _synthetic_long_trigger_df()
    params = EngineParams(min_candles=80, max_candle_size_mult=100.0)
    result = evaluate_ema50_breakout_pullback({"4h": df}, params, allow_short=False)
    ind = result.indicators
    assert ind.get("setup") == "ema50_breakout_pullback"
    if result.signal == "buy":
        assert ind.get("breakout_pass") is True
        assert ind.get("pullback_pass") is True
        assert ind.get("trigger_pass") is True
        assert ind.get("reward_risk") == 2.0
        assert ind.get("stop_hint") is not None
        assert ind.get("target_hint") is not None
        assert ind.get("stop_pct") is not None and ind.get("stop_pct") > 0
        assert ind.get("target_pct") is not None and ind.get("target_pct") > 0
        assert ind.get("expected_move_pct") is not None and ind.get("expected_move_pct") > 0
        assert "EMA50 Breakout Pullback LONG" in (ind.get("entry_reason") or "")


def _min_edge_cfg():
    return {
        "fee_rate_per_side": 0.001,
        "min_edge_gate": {
            "enabled": True,
            "min_edge_pct": 0.80,
            "edge_multiplier": 2.0,
            "require_expected_move": True,
        },
    }


def _load_min_edge_gate():
    import os
    import sys

    orch = os.path.join(os.path.dirname(__file__), "..", "..", "services", "orchestrator-service")
    if orch not in sys.path:
        sys.path.insert(0, orch)
    from hyperliquid_perps import hyperliquid_min_edge_gate  # noqa: E402

    return hyperliquid_min_edge_gate


@pytest.mark.parametrize(
    ("side", "entry", "stop", "target", "confidence"),
    [
        ("short", 0.069469, 0.075910, 0.056587, 0.74),
        ("long", 0.069469, 0.063000, 0.082351, 0.74),
    ],
)
def test_triggered_signal_publishes_expected_move_pct_for_min_edge_gate(
    side, entry, stop, target, confidence
):
    """Large 2R setups must publish percent-form expected_move_pct for both sides."""
    risk = abs(entry - stop)
    reward = abs(target - entry)
    stop_pct = risk / entry
    target_pct = reward / entry
    assert stop_pct > 0
    assert target_pct > 0
    assert target_pct / stop_pct == pytest.approx(2.0, rel=0.01)

    stop_pct_percent = stop_pct * 100.0
    target_pct_percent = target_pct * 100.0
    expected_move_pct = target_pct_percent - stop_pct_percent * (1.0 - confidence)
    hyperliquid_min_edge_gate = _load_min_edge_gate()
    signal = {
        "strategy": "ema50_breakout_pullback",
        "signal": side,
        "confidence": confidence,
        "details": {
            "state": {
                "indicators": {
                    "stop_pct": stop_pct,
                    "target_pct": target_pct,
                    "expected_move_pct": expected_move_pct,
                    "entry_price": entry,
                    "stop_hint": stop,
                    "target_hint": target,
                }
            }
        },
    }
    result = hyperliquid_min_edge_gate(signal, _min_edge_cfg())
    assert result["blocked"] is False
    assert result["reason"] == "min_edge_pass"
    assert result["expectedMovePct"] > 0.80


def test_oversized_candle_veto():
    df = _synthetic_long_trigger_df()
    params = EngineParams(min_candles=80, max_candle_size_mult=0.01)
    result = evaluate_ema50_breakout_pullback({"4h": df}, params)
    if result.indicators.get("setup_state") == "triggered":
        assert result.signal == "hold"
        assert "oversized_entry_candle" in result.invalidation_reason
