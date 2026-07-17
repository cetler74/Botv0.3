import asyncio

import numpy as np
import pandas as pd

from strategy.weekly_fibonacci_spot_strategy import WeeklyFibonacciSpotStrategy


def _frames():
    closes = np.linspace(80.0, 108.0, 240)
    hourly = pd.DataFrame(
        {
            "open": closes - 0.3,
            "high": closes + 0.8,
            "low": closes - 0.8,
            "close": closes,
            "volume": np.full(240, 1000.0),
        }
    )
    hourly.loc[hourly.index[-168:], "low"] = np.linspace(101.0, 106.0, 168)
    hourly.loc[hourly.index[-168:], "high"] = np.linspace(106.0, 110.0, 168)

    trigger_closes = np.concatenate([np.linspace(108.0, 102.5, 40), np.linspace(102.5, 104.0, 20)])
    trigger = pd.DataFrame(
        {
            "open": trigger_closes - 0.15,
            "high": trigger_closes + 0.25,
            "low": trigger_closes - 0.25,
            "close": trigger_closes,
            "volume": np.full(60, 1000.0),
        }
    )
    trigger.loc[trigger.index[-1], "volume"] = 1800.0
    return {"1h": hourly, "15m": trigger}


def test_weekly_fibonacci_buy_publishes_structural_risk():
    strategy = WeeklyFibonacciSpotStrategy(
        {
            "parameters": {
                "max_stop_pct": 4.0,
                "min_volume_ratio": 1.0,
                "rsi_min": 30.0,
                "rsi_max": 70.0,
            }
        },
        None,
        None,
    )
    signal, confidence, strength = asyncio.run(
        strategy.generate_signal(_frames(), pair="BTC/USDC")
    )
    indicators = strategy.state.indicators

    assert signal == "buy"
    assert confidence >= 0.85
    assert strength >= 0.80
    assert indicators["target_pct"] >= 3.0
    assert 0 < indicators["stop_pct"] <= 4.0
    assert indicators["reward_risk"] >= 2.0
    assert indicators["stop_hint"] < indicators["entry_price"] < indicators["target_hint"]
    assert indicators["range_timeframe"] == "1h"
    assert indicators["range_bars"] == 168
    assert indicators["confirmation_timeframe"] == "15m"
    assert "168 closed 1h bars" in indicators["entry_reason"]
    assert "15m confirmation" in indicators["entry_reason"]


def test_weekly_fibonacci_rejects_price_outside_entry_zone():
    frames = _frames()
    frames["15m"].loc[frames["15m"].index[-1], ["open", "close", "high", "low"]] = [111, 112, 112.2, 110.8]
    strategy = WeeklyFibonacciSpotStrategy({"parameters": {"max_stop_pct": 20.0}}, None, None)

    signal, _, _ = asyncio.run(strategy.generate_signal(frames, pair="BTC/USDC"))

    assert signal == "hold"
    assert strategy.state.indicators["in_entry_zone"] is False


def test_weekly_fibonacci_requires_full_seven_day_hourly_range():
    frames = _frames()
    frames["1h"] = frames["1h"].iloc[-167:]
    strategy = WeeklyFibonacciSpotStrategy({}, None, None)

    signal, _, _ = asyncio.run(strategy.generate_signal(frames, pair="BTC/USDC"))

    assert signal == "hold"
