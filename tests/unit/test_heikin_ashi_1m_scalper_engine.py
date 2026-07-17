from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from strategy.hyperliquid.heikin_ashi_1m_scalper_perp import HeikinAshi1mScalperPerpStrategy
from strategy.hyperliquid.mapping import HYPERLIQUID_STRATEGY_MAPPING
from strategy.playbooks import heikin_ashi_1m_scalper_engine as engine


def _ohlcv(n=140, trend=0.001):
    idx = pd.date_range("2026-07-13 13:00:00", periods=n, freq="1min", tz="UTC")
    close = 100.0 * (1.0 + np.linspace(0, trend * n, n))
    return pd.DataFrame(
        {
            "open": close * 0.9995,
            "high": close * 1.004,
            "low": close * 0.996,
            "close": close,
            "volume": np.full(n, 1000.0),
        },
        index=idx,
    )


def _mock_ha(df, side="long"):
    out = pd.DataFrame(index=df.index)
    out["ha_open"] = df["close"].astype(float)
    out["ha_close"] = df["close"].astype(float)
    out["ha_high"] = out["ha_close"] + 1.0
    out["ha_low"] = out["ha_close"] - 1.0

    if side == "long":
        # Two bearish flat-top pullback candles immediately before the doji.
        for offset in (-3, -2):
            out.iloc[offset, out.columns.get_loc("ha_open")] = 121.0
            out.iloc[offset, out.columns.get_loc("ha_close")] = 120.0
            out.iloc[offset, out.columns.get_loc("ha_high")] = 121.0
            out.iloc[offset, out.columns.get_loc("ha_low")] = 119.0
    else:
        # Two bullish flat-bottom pullback candles immediately before the doji.
        for offset in (-3, -2):
            out.iloc[offset, out.columns.get_loc("ha_open")] = 80.0
            out.iloc[offset, out.columns.get_loc("ha_close")] = 81.0
            out.iloc[offset, out.columns.get_loc("ha_high")] = 82.0
            out.iloc[offset, out.columns.get_loc("ha_low")] = 80.0

    # Last closed bar is a high-range doji.
    out.iloc[-1, out.columns.get_loc("ha_open")] = 100.0
    out.iloc[-1, out.columns.get_loc("ha_close")] = 100.1
    out.iloc[-1, out.columns.get_loc("ha_high")] = 102.0
    out.iloc[-1, out.columns.get_loc("ha_low")] = 98.0
    out["ha_range"] = out["ha_high"] - out["ha_low"]
    out["ha_body"] = (out["ha_close"] - out["ha_open"]).abs()
    out["ha_upper_wick"] = out["ha_high"] - out[["ha_open", "ha_close"]].max(axis=1)
    out["ha_lower_wick"] = out[["ha_open", "ha_close"]].min(axis=1) - out["ha_low"]
    return out


def test_compute_heikin_ashi_formula():
    df = pd.DataFrame(
        {
            "open": [10.0, 12.0],
            "high": [13.0, 14.0],
            "low": [9.0, 11.0],
            "close": [12.0, 13.0],
        }
    )
    ha = engine.compute_heikin_ashi(df)

    assert ha["ha_close"].iloc[0] == pytest.approx(11.0)
    assert ha["ha_open"].iloc[0] == pytest.approx(11.0)
    assert ha["ha_close"].iloc[1] == pytest.approx(12.5)
    assert ha["ha_open"].iloc[1] == pytest.approx(11.0)
    assert ha["ha_high"].iloc[1] == pytest.approx(14.0)
    assert ha["ha_low"].iloc[1] == pytest.approx(11.0)


def test_engine_emits_long_after_flat_top_pullback_and_high_range_doji(monkeypatch):
    df = _ohlcv(trend=0.0015)
    monkeypatch.setattr(engine, "compute_heikin_ashi", lambda frame: _mock_ha(frame, "long"))
    params = engine.EngineParams(block_outside_session=False, ema_chop_buffer_pct=0.0)

    result = engine.evaluate_heikin_ashi_1m_scalper({"1m": df}, params)

    assert result.signal == "buy"
    assert result.confidence == pytest.approx(params.buy_confidence)
    assert result.indicators["direction"] == "long"
    assert result.indicators["pullback_count"] == 2
    assert result.indicators["stop_hint"] < result.indicators["entry_price"]
    assert result.indicators["target_hint"] > result.indicators["entry_price"]


def test_engine_emits_short_after_flat_bottom_pullback_and_high_range_doji(monkeypatch):
    df = _ohlcv(trend=-0.0015)
    monkeypatch.setattr(engine, "compute_heikin_ashi", lambda frame: _mock_ha(frame, "short"))
    params = engine.EngineParams(block_outside_session=False, ema_chop_buffer_pct=0.0)

    result = engine.evaluate_heikin_ashi_1m_scalper({"1m": df}, params)

    assert result.signal == "sell"
    assert result.confidence == pytest.approx(params.sell_confidence)
    assert result.indicators["direction"] == "short"
    assert result.indicators["pullback_count"] == 2
    assert result.indicators["stop_hint"] > result.indicators["entry_price"]
    assert result.indicators["target_hint"] < result.indicators["entry_price"]


def test_engine_blocks_outside_session(monkeypatch):
    df = _ohlcv()
    monkeypatch.setattr(engine, "compute_heikin_ashi", lambda frame: _mock_ha(frame, "long"))
    params = engine.EngineParams(block_outside_session=True)

    result = engine.evaluate_heikin_ashi_1m_scalper(
        {"1m": df},
        params,
        now=datetime(2026, 7, 13, 18, 30),
    )

    assert result.signal == "hold"
    assert result.invalidation_reason == "outside_session"


@pytest.mark.asyncio
async def test_hyperliquid_wrapper_maps_to_perp_long_and_sets_risk(monkeypatch):
    df = _ohlcv(trend=0.0015)
    monkeypatch.setattr(engine, "compute_heikin_ashi", lambda frame: _mock_ha(frame, "long"))
    strat = HeikinAshi1mScalperPerpStrategy(
        config={"parameters": {"block_outside_session": False, "ema_chop_buffer_pct": 0.0}},
        exchange=None,
        database=None,
    )
    await strat.initialize("BTC")

    signal, confidence, strength = await strat.generate_signal({"1m": df}, pair="BTC")

    assert signal == "long"
    assert confidence > 0
    assert strength > 0
    assert strat.state.stop_loss == strat.state.indicators["stop_hint"]
    assert strat.state.take_profit == strat.state.indicators["target_hint"]


def test_heikin_ashi_1m_scalper_registered():
    assert HYPERLIQUID_STRATEGY_MAPPING["heikin_ashi_1m_scalper"] == (
        "strategy.hyperliquid.heikin_ashi_1m_scalper_perp",
        "HeikinAshi1mScalperPerpStrategy",
    )
