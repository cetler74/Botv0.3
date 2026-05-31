"""Spot wrapper tests for rsi_stoch_reversal_5m."""

from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

from strategy.playbooks.rsi_stoch_reversal_5m_engine import EngineResult
from strategy.rsi_stoch_reversal_5m_strategy import RsiStochReversal5mStrategy


@pytest.mark.asyncio
async def test_spot_wrapper_emits_buy_only():
    strat = RsiStochReversal5mStrategy(
        config={"parameters": {"entry_timeframe": "5m"}},
        exchange=None,
        database=None,
    )
    await strat.initialize("BTC/USDC")
    buy_result = EngineResult(
        signal="buy",
        confidence=0.72,
        strength=0.70,
        indicators={"setup": "rsi_stoch_reversal_5m", "rsi": 28.0},
    )
    with patch(
        "strategy.rsi_stoch_reversal_5m_strategy.evaluate_rsi_stoch_reversal_5m",
        return_value=buy_result,
    ):
        strat.log_condition_outcome = AsyncMock()
        sig, conf, strength = await strat.generate_signal(
            {"5m": pd.DataFrame()}, pair="BTC/USDC"
        )
    assert sig == "buy"
    assert conf == pytest.approx(0.72)


@pytest.mark.asyncio
async def test_spot_wrapper_uses_engine_long_rules():
    strat = RsiStochReversal5mStrategy(
        config={
            "parameters": {
                "rsi_oversold": 30,
                "stoch_oversold": 30,
                "entry_timeframe": "5m",
            }
        },
        exchange=None,
        database=None,
    )
    await strat.initialize("BTC/USDC")
    captured = {}

    def _capture_eval(market_data, params, **kwargs):
        captured["allow_short"] = kwargs.get("allow_short")
        captured["keys"] = list(market_data.keys()) if isinstance(market_data, dict) else None
        return EngineResult(signal="hold", confidence=0.0, strength=0.0, indicators={})

    with patch(
        "strategy.rsi_stoch_reversal_5m_strategy.evaluate_rsi_stoch_reversal_5m",
        side_effect=_capture_eval,
    ):
        strat.log_condition_outcome = AsyncMock()
        await strat.generate_signal({"5m": pd.DataFrame()}, pair="BTC/USDC")
    assert captured["allow_short"] is False
    assert captured["keys"] == ["5m"]


@pytest.mark.asyncio
async def test_spot_wrapper_never_emits_sell():
    strat = RsiStochReversal5mStrategy(
        config={"parameters": {}},
        exchange=None,
        database=None,
    )
    await strat.initialize("BTC/USDC")
    sell_result = EngineResult(
        signal="sell",
        confidence=0.72,
        strength=0.70,
        indicators={},
    )
    with patch(
        "strategy.rsi_stoch_reversal_5m_strategy.evaluate_rsi_stoch_reversal_5m",
        return_value=sell_result,
    ):
        strat.log_condition_outcome = AsyncMock()
        sig, conf, strength = await strat.generate_signal(
            {"5m": pd.DataFrame()}, pair="BTC/USDC"
        )
    assert sig == "hold"
    assert conf == 0.0
