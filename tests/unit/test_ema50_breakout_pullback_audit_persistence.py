"""Audit payload contract for EMA50 breakout-pullback."""

from __future__ import annotations

from strategy.playbooks.ema50_breakout_pullback_audit import build_ema50_breakout_pullback_audit_payload


def test_audit_payload_from_strategy_result():
    strategy_result = {
        "signal": "buy",
        "timestamp": "2026-06-07T12:00:00",
        "state": {
            "indicators": {
                "setup": "ema50_breakout_pullback",
                "setup_state": "triggered",
                "direction": "long",
                "ema50_side": "above",
                "breakout_pass": True,
                "pullback_pass": True,
                "trigger_pass": True,
                "breakout_reason": "breakout_close_above_ema50",
                "pullback_reason": "pullback_2_bearish",
                "trigger_reason": "body_close_above_swing_high",
                "swing_level": 105.5,
                "ema50_value": 104.0,
                "entry_price": 106.0,
                "stop_hint": 103.0,
                "target_hint": 112.0,
                "reward_risk": 2.0,
                "entry_reason": "EMA50 Breakout Pullback LONG (4h): test",
                "primary_timeframe": "4h",
                "candle_ts": "2026-06-07T08:00:00",
            }
        },
    }
    payload = build_ema50_breakout_pullback_audit_payload("binance", "BTC/USDT", strategy_result)
    assert payload is not None
    assert payload["venue"] == "binance"
    assert payload["symbol"] == "BTC/USDT"
    assert payload["setup_state"] == "triggered"
    assert payload["breakout_pass"] is True
    assert payload["trigger_pass"] is True
    assert payload["reward_risk"] == 2.0
