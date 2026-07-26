import asyncio

import numpy as np
import pandas as pd

from strategy.ema20_ma50_spot_1h_strategy import Ema20Ma50Spot1hStrategy


def _frames_for_buy():
    close = np.linspace(100.0, 130.0, 90)
    close[-4] = 126.4
    close[-3] = 125.8
    close[-2] = 125.4
    close[-1] = 127.2
    frame = pd.DataFrame(
        {
            "open": close - 0.35,
            "high": close + 0.7,
            "low": close - 0.7,
            "close": close,
            "volume": np.full(len(close), 1000.0),
        }
    )
    frame.loc[frame.index[-1], "open"] = 126.2
    return {"1h": frame}


def _strategy(**params):
    config = {
        "parameters": {
            "min_candles": 60,
            "min_stop_pct": 0.002,
            "max_stop_pct": 0.08,
            "min_ma_gap_pct": 0.0001,
            **params,
        }
    }
    strategy = Ema20Ma50Spot1hStrategy(config, None, None)
    strategy.state.market_regime = "trending_up"
    return strategy


def test_ema20_ma50_spot_1h_buy_publishes_setup_risk():
    strategy = _strategy()

    signal, confidence, strength = asyncio.run(
        strategy.generate_signal(_frames_for_buy(), pair="BTC/USDC")
    )
    indicators = strategy.state.indicators

    assert signal == "buy"
    assert confidence >= 0.78
    assert strength >= 0.76
    assert indicators["setup"] == "ema20_ma50_spot_1h"
    assert indicators["entry_price"] > indicators["ema20"] > indicators["ma50"]
    assert indicators["stop_hint"] < indicators["entry_price"] < indicators["target_hint"]
    assert indicators["reward_risk"] >= 2.0
    assert indicators["stop_pct"] > 0
    assert "EMA20/MA50 1h long" in indicators["entry_reason"]


def test_ema20_ma50_spot_1h_rejects_extended_price():
    strategy = _strategy(max_extension_pct=0.001)

    signal, _, _ = asyncio.run(strategy.generate_signal(_frames_for_buy(), pair="BTC/USDC"))

    assert signal == "hold"
    assert strategy.state.indicators["skip_reason"] == "price_too_extended"


def test_ema20_ma50_spot_1h_blocks_downtrend_regime():
    strategy = _strategy()
    strategy.state.market_regime = "trending_down"

    signal, _, _ = asyncio.run(strategy.generate_signal(_frames_for_buy(), pair="BTC/USDC"))

    assert signal == "hold"
    assert strategy.state.indicators["skip_reason"] == "blocked_regime"
