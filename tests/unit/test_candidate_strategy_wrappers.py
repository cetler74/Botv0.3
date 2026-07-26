"""Wrapper contracts for Donchian/ATR and VWAP/RSI candidate strategies."""

from unittest.mock import patch

import pandas as pd
import pytest

from strategy.donchian_atr_pullback_strategy import DonchianAtrPullbackStrategy
from strategy.hyperliquid.donchian_atr_pullback_perp import DonchianAtrPullbackPerpStrategy
from strategy.hyperliquid.mapping import HYPERLIQUID_STRATEGY_MAPPING
from strategy.hyperliquid.vwap_rsi_mean_reversion_perp import (
    VwapRsiMeanReversionPerpStrategy,
)
from strategy.playbooks.donchian_atr_pullback_engine import EngineResult as DonchianResult
from strategy.playbooks.vwap_rsi_mean_reversion_engine import EngineResult as VwapResult
from strategy.vwap_rsi_mean_reversion_strategy import VwapRsiMeanReversionStrategy


@pytest.mark.asyncio
async def test_donchian_spot_wrapper_never_allows_short():
    strategy = DonchianAtrPullbackStrategy(
        config={"parameters": {}},
        exchange=None,
        database=None,
    )
    await strategy.initialize("BTC/USDC")
    captured = {}

    def capture(market_data, params, **kwargs):
        captured["allow_short"] = kwargs["allow_short"]
        captured["keys"] = set(market_data)
        return DonchianResult(
            "buy",
            0.74,
            0.72,
            {
                "entry_reason": "1h uptrend; 15m Donchian reclaim",
                "stop_hint": 99.0,
                "target_hint": 105.0,
            },
        )

    with patch(
        "strategy.donchian_atr_pullback_strategy.evaluate_donchian_atr_pullback",
        side_effect=capture,
    ):
        signal, _, _ = await strategy.generate_signal(
            {"1h": pd.DataFrame(), "15m": pd.DataFrame()},
            pair="BTC/USDC",
        )

    assert signal == "buy"
    assert captured["allow_short"] is False
    assert captured["keys"] == {"1h", "15m"}


@pytest.mark.asyncio
async def test_vwap_spot_wrapper_never_allows_short():
    strategy = VwapRsiMeanReversionStrategy(
        config={"parameters": {}},
        exchange=None,
        database=None,
    )
    await strategy.initialize("ETH/USDC")
    captured = {}

    def capture(market_data, params, **kwargs):
        captured["allow_short"] = kwargs["allow_short"]
        return VwapResult(
            "buy",
            0.72,
            0.70,
            {
                "entry_reason": "1h range; VWAP excursion with RSI reclaim",
                "stop_hint": 98.0,
                "target_hint": 101.0,
            },
        )

    with patch(
        "strategy.vwap_rsi_mean_reversion_strategy.evaluate_vwap_rsi_mean_reversion",
        side_effect=capture,
    ):
        signal, _, _ = await strategy.generate_signal(
            {"1h": pd.DataFrame(), "15m": pd.DataFrame()},
            pair="ETH/USDC",
        )

    assert signal == "buy"
    assert captured["allow_short"] is False


def test_perp_wrappers_default_to_1h_bias_and_15m_entry():
    don = DonchianAtrPullbackPerpStrategy(
        config={"parameters": {"allow_long": True, "allow_short": True}},
        exchange=None,
        database=None,
    )
    vwap = VwapRsiMeanReversionPerpStrategy(
        config={"parameters": {"allow_long": True, "allow_short": True}},
        exchange=None,
        database=None,
    )
    assert don._engine_params.bias_timeframe == "1h"
    assert don._engine_params.entry_timeframe == "15m"
    assert vwap._engine_params.bias_timeframe == "1h"
    assert vwap._engine_params.entry_timeframe == "15m"


def test_candidate_strategies_are_registered_in_hl_mapping():
    assert "donchian_atr_pullback" in HYPERLIQUID_STRATEGY_MAPPING
    assert "daily_box_break_retest" in HYPERLIQUID_STRATEGY_MAPPING
    assert "vwap_rsi_mean_reversion" in HYPERLIQUID_STRATEGY_MAPPING
