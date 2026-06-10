"""Fast signal Redis cache helpers."""

import pytest

from strategy.fast_signal_cache import (
    fast_payload_from_hl_strategy_signal,
    merge_rsi_stoch_spot_buy_into_signals,
    mirrored_perp_signal_from_fast_payload,
    normalize_perp_side,
    redis_key,
    signal_age_seconds,
)


def test_redis_key_format():
    assert redis_key("binance", "BTCUSDC") == (
        "trading:fast_signal:rsi_stoch_reversal_5m:binance:BTCUSDC"
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
    rsi = signals_data["strategies"]["rsi_stoch_reversal_5m"]
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
        "strategy": "rsi_stoch_reversal_5m",
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
