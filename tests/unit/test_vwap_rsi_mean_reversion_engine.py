"""VWAP/RSI mean-reversion engine contracts for 1h range + 15m reclaim."""

from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pandas as pd

from strategy.playbooks.vwap_rsi_mean_reversion_engine import (
    EngineParams,
    evaluate_vwap_rsi_mean_reversion,
)


def _range_ohlcv(n: int, mid: float = 100.0, amp: float = 1.5) -> pd.DataFrame:
    idx = np.arange(n)
    closes = mid + amp * np.sin(idx / 6.0)
    return pd.DataFrame(
        {
            "open": closes - 0.1,
            "high": closes + 0.6,
            "low": closes - 0.6,
            "close": closes,
            "volume": np.full(n, 1200.0),
        }
    )


def test_long_mean_reversion_after_vwap_excursion_and_rsi_reclaim():
    hourly = _range_ohlcv(80, mid=100.0, amp=0.4)
    trigger = _range_ohlcv(80, mid=100.0, amp=0.5)
    # Downside excursion then a reclaim candle that closes back through VWAP.
    trigger.loc[trigger.index[-6:-2], ["low", "close", "volume"]] = [
        [96.0, 96.5, 2000.0],
        [95.5, 96.0, 2200.0],
        [95.8, 96.2, 1800.0],
        [96.5, 97.0, 1600.0],
    ]
    trigger.loc[trigger.index[-1], ["open", "high", "low", "close", "volume"]] = [
        98.0,
        101.5,
        97.8,
        101.0,
        2500.0,
    ]
    rsi = pd.Series([50.0] * 79 + [46.0])
    rsi.iloc[-2] = 32.0

    with patch(
        "strategy.playbooks.vwap_rsi_mean_reversion_engine._rsi_series",
        return_value=rsi,
    ):
        result = evaluate_vwap_rsi_mean_reversion(
            {"1h": hourly, "15m": trigger},
            EngineParams(allow_short=False, rsi_oversold=40.0, rsi_reclaim=45.0),
            market_regime="sideways",
            allow_short=False,
        )

    assert result.signal == "buy"
    assert result.indicators["stop_hint"] < result.indicators["entry_price"]
    assert result.indicators["target_hint"] > result.indicators["entry_price"]
    assert "1h range" in result.indicators["entry_reason"]
    assert "VWAP" in result.indicators["entry_reason"]
    assert "RSI reclaim" in result.indicators["entry_reason"]


def test_short_mean_reversion_when_shorts_allowed():
    hourly = _range_ohlcv(80, mid=100.0, amp=0.4)
    trigger = _range_ohlcv(80, mid=100.0, amp=0.5)
    trigger.loc[trigger.index[-6:-2], ["high", "close", "volume"]] = [
        [104.0, 103.5, 2000.0],
        [104.5, 104.0, 2200.0],
        [104.2, 103.8, 1800.0],
        [103.5, 103.0, 1600.0],
    ]
    trigger.loc[trigger.index[-1], ["open", "high", "low", "close", "volume"]] = [
        102.0,
        102.2,
        98.5,
        99.0,
        2500.0,
    ]
    rsi = pd.Series([50.0] * 79 + [54.0])
    rsi.iloc[-2] = 72.0

    with patch(
        "strategy.playbooks.vwap_rsi_mean_reversion_engine._rsi_series",
        return_value=rsi,
    ):
        result = evaluate_vwap_rsi_mean_reversion(
            {"1h": hourly, "15m": trigger},
            EngineParams(allow_short=True, rsi_overbought=60.0, rsi_release=55.0),
            market_regime="sideways",
            allow_short=True,
        )

    assert result.signal == "sell"
    assert result.indicators["stop_hint"] > result.indicators["entry_price"]
    assert "1h range" in result.indicators["entry_reason"]


def test_rejects_strong_1h_trend():
    trend = pd.DataFrame(
        {
            "open": np.arange(80) + 90.0,
            "high": np.arange(80) + 90.5,
            "low": np.arange(80) + 89.5,
            "close": np.arange(80) + 90.2,
            "volume": np.full(80, 1000.0),
        }
    )
    result = evaluate_vwap_rsi_mean_reversion(
        {"1h": trend, "15m": trend.copy()},
        EngineParams(),
        market_regime="trending_up",
        allow_short=True,
    )
    assert result.signal == "hold"
    assert result.invalidation_reason
