"""Versioned 15m RSI/Stoch reversal wrapper contracts."""

from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

from strategy.hyperliquid.rsi_stoch_reversal_15m_perp import (
    RsiStochReversal15mPerpStrategy,
)
from strategy.playbooks.rsi_stoch_reversal_5m_engine import EngineResult
from strategy.playbooks.rsi_stoch_reversal_5m_engine import (
    EngineParams,
    evaluate_rsi_stoch_reversal_5m,
)
from strategy.rsi_stoch_reversal_15m_strategy import RsiStochReversal15mStrategy


@pytest.mark.asyncio
async def test_spot_15m_wrapper_uses_1h_bias_and_never_allows_short():
    strategy = RsiStochReversal15mStrategy(
        config={"parameters": {}},
        exchange=None,
        database=None,
    )
    await strategy.initialize("BTC/USDC")
    captured = {}

    def capture(market_data, params, **kwargs):
        captured["keys"] = set(market_data)
        captured["entry_timeframe"] = params.entry_timeframe
        captured["confirmation_timeframe"] = params.confirmation_timeframe
        captured["require_confirmation"] = params.require_confirmation
        captured["allow_short"] = kwargs["allow_short"]
        return EngineResult(
            "buy",
            0.72,
            0.70,
            {"entry_reason": "15m reversal aligned with 1h bullish bias"},
        )

    with patch(
        "strategy.rsi_stoch_reversal_5m_strategy.evaluate_rsi_stoch_reversal_5m",
        side_effect=capture,
    ):
        strategy.log_condition_outcome = AsyncMock()
        signal, _, _ = await strategy.generate_signal(
            {"1h": pd.DataFrame(), "15m": pd.DataFrame()},
            pair="BTC/USDC",
        )

    assert signal == "buy"
    assert captured == {
        "keys": {"1h", "15m"},
        "entry_timeframe": "15m",
        "confirmation_timeframe": "1h",
        "require_confirmation": True,
        "allow_short": False,
    }


@pytest.mark.asyncio
async def test_perp_15m_wrapper_defaults_to_1h_bias_and_15m_trigger():
    strategy = RsiStochReversal15mPerpStrategy(
        config={"parameters": {"allow_long": True, "allow_short": True}},
        exchange=None,
        database=None,
    )
    await strategy.initialize("BTC")

    assert strategy._engine_params.entry_timeframe == "15m"
    assert strategy._engine_params.confirmation_timeframe == "1h"
    assert strategy._engine_params.require_confirmation is True


def test_15m_engine_reason_describes_1h_bias():
    entry = pd.DataFrame(
        {
            "open": [99.0] * 120,
            "high": [101.0] * 120,
            "low": [98.0] * 120,
            "close": [100.0] * 120,
            "volume": [1000.0] * 120,
        }
    )
    bias = entry.copy()
    bias.loc[bias.index[-1], ["open", "close"]] = [100.0, 102.0]
    params = EngineParams(
        entry_timeframe="15m",
        confirmation_timeframe="1h",
        require_confirmation=True,
    )
    entry_rsi = pd.Series([25.0] * 120)
    bias_rsi = pd.Series([55.0] * 119 + [60.0])

    with patch(
        "strategy.playbooks.rsi_stoch_reversal_5m_engine._rsi_series",
        side_effect=[entry_rsi, bias_rsi],
    ), patch(
        "strategy.playbooks.rsi_stoch_reversal_5m_engine.compute_stoch_rsi",
        return_value=(pd.Series([22.0] * 120), pd.Series([18.0] * 120)),
    ):
        result = evaluate_rsi_stoch_reversal_5m(
            {"15m": entry, "1h": bias},
            params,
        )

    assert result.signal == "buy"
    assert "1h bias" in result.indicators["entry_reason"]
