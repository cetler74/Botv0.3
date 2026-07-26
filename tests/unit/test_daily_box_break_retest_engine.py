"""Unit tests for daily box break + retest engine."""

from __future__ import annotations

import numpy as np
import pandas as pd

from strategy.playbooks.daily_box_break_retest_engine import (
    EngineParams,
    evaluate_daily_box_break_retest,
)


def _ohlcv(closes, *, high_off=0.4, low_off=0.4):
    closes = np.asarray(closes, dtype=float)
    idx = pd.date_range("2026-07-01", periods=len(closes), freq="h", tz="UTC")
    return pd.DataFrame(
        {
            "open": closes - 0.1,
            "high": closes + high_off,
            "low": closes - low_off,
            "close": closes,
            "volume": np.full(len(closes), 1000.0),
        },
        index=idx,
    )


def _daily_box_frames():
    # Previous day high/low ≈ 110 / 100 (mid 105). Current day climbs through 110.
    daily_closes = np.concatenate([np.linspace(90, 105, 5), [105.0, 112.0]])
    daily = _ohlcv(daily_closes)
    # Force previous completed day (iloc[-2]) to known box.
    daily.iloc[-2, daily.columns.get_loc("high")] = 110.0
    daily.iloc[-2, daily.columns.get_loc("low")] = 100.0
    daily.iloc[-2, daily.columns.get_loc("close")] = 108.0
    # Resample-like daily index
    daily.index = pd.date_range("2026-07-01", periods=len(daily), freq="D", tz="UTC")

    # 4h uptrend above EMA
    bias_closes = np.linspace(100, 115, 50)
    bias = _ohlcv(bias_closes)
    bias.index = pd.date_range("2026-06-20", periods=len(bias), freq="4h", tz="UTC")

    # 1h: break above 110, retest ~110, confirm close > 110
    entry_closes = np.linspace(104, 109.5, 45)
    entry = _ohlcv(entry_closes, high_off=0.5, low_off=0.5)
    # break bar
    entry.iloc[-4, entry.columns.get_loc("close")] = 111.2
    entry.iloc[-4, entry.columns.get_loc("high")] = 111.5
    entry.iloc[-4, entry.columns.get_loc("low")] = 109.8
    # retest bar
    entry.iloc[-3, entry.columns.get_loc("close")] = 110.4
    entry.iloc[-3, entry.columns.get_loc("high")] = 110.8
    entry.iloc[-3, entry.columns.get_loc("low")] = 109.95
    # filler
    entry.iloc[-2, entry.columns.get_loc("close")] = 110.6
    entry.iloc[-2, entry.columns.get_loc("high")] = 110.9
    entry.iloc[-2, entry.columns.get_loc("low")] = 110.2
    # confirmation
    entry.iloc[-1, entry.columns.get_loc("close")] = 111.8
    entry.iloc[-1, entry.columns.get_loc("high")] = 112.0
    entry.iloc[-1, entry.columns.get_loc("low")] = 110.7
    entry.index = pd.date_range("2026-07-10", periods=len(entry), freq="h", tz="UTC")

    return {"1d": daily, "4h": bias, "1h": entry}


def test_daily_box_long_break_retest_buy():
    params = EngineParams(
        min_bias_candles=20,
        min_entry_candles=20,
        min_daily_candles=3,
        min_stop_pct=0.002,
        max_stop_pct=0.08,
        min_reward_risk=2.0,
        retest_tolerance_pct=0.01,
        max_box_range_pct=0.12,
    )
    result = evaluate_daily_box_break_retest(
        _daily_box_frames(), params, market_regime="trending_up"
    )
    assert result.signal == "buy"
    assert result.confidence >= 0.78
    ind = result.indicators
    assert ind["setup"] == "daily_box_break_retest"
    assert ind["box_high"] == 110.0
    assert ind["box_low"] == 100.0
    assert ind["entry_price"] > ind["box_high"]
    assert ind["stop_hint"] < ind["entry_price"] < ind["target_hint"]
    assert ind["reward_risk"] >= 2.0
    assert "Daily box long" in ind["entry_reason"]


def test_daily_box_blocks_sideways_regime():
    params = EngineParams(min_bias_candles=20, min_entry_candles=20)
    result = evaluate_daily_box_break_retest(
        _daily_box_frames(), params, market_regime="sideways"
    )
    assert result.signal == "hold"
    assert result.indicators["skip_reason"] == "blocked_regime"


def test_daily_box_rejects_without_break():
    frames = _daily_box_frames()
    # Keep all 1h closes below box high.
    frames["1h"]["close"] = np.linspace(100, 109.0, len(frames["1h"]))
    frames["1h"]["high"] = frames["1h"]["close"] + 0.2
    frames["1h"]["low"] = frames["1h"]["close"] - 0.2
    params = EngineParams(
        min_bias_candles=20,
        min_entry_candles=20,
        max_box_range_pct=0.12,
    )
    result = evaluate_daily_box_break_retest(frames, params, market_regime="trending_up")
    assert result.signal == "hold"
    assert result.indicators["skip_reason"] in {
        "no_break_retest_confirm",
        "no_trend_bias",
        "no_break",
    }
