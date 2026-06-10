"""Wrapper contract tests for Dual-SMA spot/perp strategy adapters."""

from __future__ import annotations

import pytest

import strategy.dual_sma_daytrade_strategy as spot_module
import strategy.hyperliquid.dual_sma_daytrade_perp as perp_module
from strategy.dual_sma_daytrade_strategy import DualSmaDaytradeStrategy
from strategy.hyperliquid.dual_sma_daytrade_perp import DualSmaDaytradePerpStrategy
from strategy.playbooks.dual_sma_daytrade_engine import EngineResult


@pytest.mark.asyncio
async def test_spot_wrapper_maps_buy_and_blocks_sell(monkeypatch):
    fake_buy = EngineResult(
        "buy",
        0.74,
        0.72,
        {
            "setup": "dual_sma_daytrade",
            "stop_hint": 98.0,
            "target_hint": 104.0,
            "reward_risk": 2.0,
        },
        "none",
    )
    monkeypatch.setattr(spot_module, "evaluate_dual_sma_daytrade", lambda *a, **k: fake_buy)

    strat = DualSmaDaytradeStrategy({"parameters": {}}, None, None)
    await strat.initialize("BTC/USDC")
    signal, conf, strength = await strat.generate_signal({}, pair="BTC/USDC")

    assert signal == "buy"
    assert conf == 0.74
    assert strength == 0.72
    assert strat.state.stop_loss == 98.0
    assert strat.state.take_profit == 104.0

    fake_sell = EngineResult("sell", 0.74, 0.72, {"setup": "dual_sma_daytrade"}, "none")
    monkeypatch.setattr(spot_module, "evaluate_dual_sma_daytrade", lambda *a, **k: fake_sell)

    signal, conf, strength = await strat.generate_signal({}, pair="BTC/USDC")

    assert signal == "hold"
    assert conf == 0.0
    assert strength == 0.0


@pytest.mark.asyncio
async def test_perp_wrapper_maps_long_and_short(monkeypatch):
    fake_buy = EngineResult(
        "buy",
        0.74,
        0.72,
        {"setup": "dual_sma_daytrade", "stop_hint": 98.0, "target_hint": 104.0},
        "none",
    )
    monkeypatch.setattr(perp_module, "evaluate_dual_sma_daytrade", lambda *a, **k: fake_buy)

    strat = DualSmaDaytradePerpStrategy(
        {"parameters": {"allow_long": True, "allow_short": True}},
        None,
        None,
    )
    await strat.initialize("BTC")
    signal, conf, strength = await strat.generate_signal({}, pair="BTC")

    assert signal == "long"
    assert conf == 0.74
    assert strength == 0.72

    fake_sell = EngineResult(
        "sell",
        0.74,
        0.72,
        {"setup": "dual_sma_daytrade", "stop_hint": 102.0, "target_hint": 96.0},
        "none",
    )
    monkeypatch.setattr(perp_module, "evaluate_dual_sma_daytrade", lambda *a, **k: fake_sell)

    signal, conf, strength = await strat.generate_signal({}, pair="BTC")

    assert signal == "short"
    assert conf == 0.74
    assert strength == 0.72

