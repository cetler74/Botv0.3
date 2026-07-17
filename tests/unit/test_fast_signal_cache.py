"""Fast signal Redis cache helpers."""

import pytest

from strategy.fast_signal_cache import (
    DAYTRADE_FAST_STRATEGIES,
    fast_payload_from_hl_strategy_signal,
    merge_fast_spot_signal_into_signals,
    merge_rsi_stoch_spot_buy_into_signals,
    mirrored_perp_signal_from_fast_payload,
    normalize_perp_side,
    redis_key,
    signal_age_seconds,
    spot_signals_data_from_fast_payload,
    validate_generic_fast_actionable,
    validate_rsi_stoch_actionable,
    validate_orb_actionable,
)


def test_redis_key_format():
    assert redis_key("binance", "BTCUSDC") == (
        "trading:fast_signal:rsi_stoch_reversal_15m:binance:BTCUSDC"
    )
    assert redis_key(
        "hyperliquid",
        "BTC",
        strategy_key="rsi_stoch_reversal_1m",
    ) == "trading:fast_signal:rsi_stoch_reversal_1m:hyperliquid:BTC"


def test_signal_age_seconds_parses_iso():
    payload = {"analyzed_at": "2024-06-01T12:00:00+00:00"}
    age = signal_age_seconds(payload)
    assert age is not None
    assert age >= 0


def test_merge_rsi_stoch_spot_buy_into_signals():
    signals_data = {"strategies": {"macd_momentum": {"signal": "hold"}}}
    fast = {
        "signal": "buy",
        "confidence": 0.72,
        "strength": 0.7,
        "indicators": {"rsi": 28.0, "stoch_rsi_k": 35.0, "stoch_rsi_d": 20.0},
    }
    assert merge_rsi_stoch_spot_buy_into_signals(signals_data, fast)
    rsi = signals_data["strategies"]["rsi_stoch_reversal_15m"]
    assert rsi["signal"] == "buy"
    assert rsi["confidence"] == 0.72


def test_mirrored_perp_signal_from_fast_payload_long():
    fast = {"signal": "long", "confidence": 0.72, "strength": 0.65}
    mirrored = mirrored_perp_signal_from_fast_payload(fast)
    assert mirrored is not None
    assert mirrored["signal"] == "long"
    assert mirrored["consensus_agreement"] == 100.0


def test_normalize_perp_side_maps_buy_sell():
    assert normalize_perp_side("buy") == "long"
    assert normalize_perp_side("sell") == "short"
    assert normalize_perp_side("hold") is None


def test_fast_payload_from_hl_strategy_signal():
    live = {
        "signal": "short",
        "confidence": 0.72,
        "strength": 0.7,
        "strategy": "rsi_stoch_reversal_15m",
        "state": {
            "indicators": {
                "stoch_rsi_k": 85.0,
                "stoch_rsi_d": 90.0,
                "bar_close_time": "2026-05-31T11:15:00+00:00",
            }
        },
    }
    payload = fast_payload_from_hl_strategy_signal(live)
    assert payload["signal"] == "short"
    assert payload["indicators"]["stoch_rsi_k"] == 85.0


def test_validate_orb_actionable_accepts_grace_window_signal():
    from datetime import datetime, timezone

    payload = {
        "signal": "long",
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "indicators": {
            "session_state": "signal",
            "direction": "long",
            "breakout_valid": True,
            "retest_valid": True,
            "reward_risk": 2.0,
            "entry_reason": "ORB 5m LONG: body breakout",
        },
    }
    ok, reason = validate_orb_actionable(payload, max_signal_age_seconds=3600.0)
    assert ok is True
    assert reason == "long_ok"


def test_merge_fast_spot_signal_into_signals_generic_strategy():
    signals_data = {"strategies": {}}
    fast = {
        "signal": "buy",
        "strategy": "ema50_breakout_pullback",
        "confidence": 0.76,
        "strength": 0.68,
        "indicators": {"entry_reason": "EMA50 pullback reclaim"},
    }
    assert merge_fast_spot_signal_into_signals(
        signals_data, fast, strategy_key="ema50_breakout_pullback"
    )
    assert signals_data["strategies"]["ema50_breakout_pullback"]["signal"] == "buy"


def test_validate_generic_fast_actionable_requires_entry_reason():
    from datetime import datetime, timezone

    payload = {
        "signal": "long",
        "confidence": 0.8,
        "strength": 0.7,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "indicators": {"entry_reason": "Dual SMA pullback"},
    }
    ok, reason = validate_generic_fast_actionable(payload, min_confidence=0.7)
    assert ok is True
    assert reason == "long_ok"


def test_validate_rsi_stoch_uses_separate_closed_bar_age_budget():
    from datetime import datetime, timedelta, timezone

    payload = {
        "signal": "buy",
        "confidence": 0.72,
        "strength": 0.70,
        "analyzed_at": datetime.now(timezone.utc).isoformat(),
        "indicators": {
            "entry_reason": "RSI+StochRSI long",
            "rsi": 20.0,
            "stoch_rsi_k": 0.0,
            "stoch_rsi_d": 0.0,
            "bar_close_time": (datetime.now(timezone.utc) - timedelta(minutes=4)).isoformat(),
        },
    }
    ok, reason = validate_rsi_stoch_actionable(
        payload,
        params={"rsi_oversold": 30, "stoch_oversold": 30},
        max_bar_age_seconds=360,
    )
    assert ok is True
    assert reason == "long_ok"


def test_spot_signals_data_from_fast_payload_builds_standalone_envelope():
    fast = {
        "signal": "buy",
        "strategy": "rsi_stoch_reversal_15m",
        "confidence": 0.72,
        "strength": 0.70,
        "analyzed_at": "2026-06-13T17:13:58+00:00",
        "indicators": {
            "entry_reason": "RSI+StochRSI long",
            "market_regime": "reversal_zone",
        },
    }
    signals = spot_signals_data_from_fast_payload(fast, "bybit", "MOVE/USDC")
    assert signals["consensus"]["signal"] == "hold"
    assert signals["market_regime"] == "reversal_zone"
    assert signals["strategies"]["rsi_stoch_reversal_15m"]["signal"] == "buy"
    assert signals["fast_signal"]["source"] == "redis"


def test_daytrade_fast_strategies_include_standalone_playbooks():
    assert "supply_demand_3step" in DAYTRADE_FAST_STRATEGIES
    assert "dual_sma_daytrade" in DAYTRADE_FAST_STRATEGIES
    assert "ema50_breakout_pullback" not in DAYTRADE_FAST_STRATEGIES
    assert "arc_daytrade" not in DAYTRADE_FAST_STRATEGIES
