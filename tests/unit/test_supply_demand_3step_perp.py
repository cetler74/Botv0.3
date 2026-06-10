"""Unit tests for supply/demand 3-step perp wrapper."""

from __future__ import annotations

import pytest

import strategy.hyperliquid.supply_demand_3step_perp as perp_module
from strategy.hyperliquid.supply_demand_3step_perp import SupplyDemand3StepPerpStrategy
from strategy.playbooks.supply_demand_3step_engine import EngineResult


@pytest.mark.asyncio
async def test_perp_long_signal_sets_risk_metadata(monkeypatch):
    fake_result = EngineResult(
        "buy",
        0.74,
        0.72,
        {
            "setup": "supply_demand_3step",
            "stop_hint": 98.5,
            "target_hint": 105.0,
            "reward_risk": 2.8,
            "entry_price": 100.0,
            "entry_reason": "Supply/Demand 3-Step LONG test",
        },
        "none",
    )

    monkeypatch.setattr(perp_module, "evaluate_supply_demand_3step", lambda *a, **k: fake_result)

    strat = SupplyDemand3StepPerpStrategy(
        {"parameters": {"allow_long": True, "allow_short": True}},
        None,
        None,
    )
    await strat.initialize("WLD")
    signal, conf, strength = await strat.generate_signal({}, pair="WLD")
    assert signal == "long"
    assert conf == 0.74
    assert strat.state.indicators["reward_risk"] == 2.8


@pytest.mark.asyncio
async def test_perp_short_signal(monkeypatch):
    fake_result = EngineResult(
        "sell",
        0.74,
        0.72,
        {
            "setup": "supply_demand_3step",
            "stop_hint": 102.0,
            "target_hint": 95.0,
            "reward_risk": 3.0,
            "entry_price": 100.0,
            "entry_reason": "Supply/Demand 3-Step SHORT test",
        },
        "none",
    )

    monkeypatch.setattr(perp_module, "evaluate_supply_demand_3step", lambda *a, **k: fake_result)

    strat = SupplyDemand3StepPerpStrategy(
        {"parameters": {"allow_long": True, "allow_short": True}},
        None,
        None,
    )
    await strat.initialize("WLD")
    signal, _, _ = await strat.generate_signal({}, pair="WLD")
    assert signal == "short"


@pytest.mark.asyncio
async def test_perp_respects_short_disabled(monkeypatch):
    fake_result = EngineResult(
        "sell",
        0.74,
        0.72,
        {"setup": "supply_demand_3step", "entry_reason": "short setup"},
        "none",
    )

    monkeypatch.setattr(perp_module, "evaluate_supply_demand_3step", lambda *a, **k: fake_result)

    strat = SupplyDemand3StepPerpStrategy(
        {"parameters": {"allow_short": False}},
        None,
        None,
    )
    await strat.initialize("WLD")
    signal, conf, strength = await strat.generate_signal({}, pair="WLD")
    assert signal == "hold"
    assert conf == 0.0


@pytest.mark.asyncio
async def test_spot_wrapper_is_long_only(monkeypatch):
    import strategy.supply_demand_3step_strategy as spot_module
    from strategy.supply_demand_3step_strategy import SupplyDemand3StepStrategy

    fake_short = EngineResult(
        "sell",
        0.74,
        0.72,
        {"setup": "supply_demand_3step", "entry_reason": "short setup"},
        "none",
    )
    monkeypatch.setattr(spot_module, "evaluate_supply_demand_3step", lambda *a, **k: fake_short)

    strat = SupplyDemand3StepStrategy({"parameters": {"allow_short": True}}, None, None)
    await strat.initialize("BTC/USDC")
    signal, conf, strength = await strat.generate_signal({}, pair="BTC/USDC")

    assert signal == "hold"
    assert conf == 0.0
    assert strength == 0.0
