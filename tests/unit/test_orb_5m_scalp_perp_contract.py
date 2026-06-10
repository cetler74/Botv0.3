"""Contract tests for ORB 5m scalp HL registration and specialist gate."""

from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ORCH = os.path.join(ROOT, "services", "orchestrator-service")
if ORCH not in sys.path:
    sys.path.insert(0, ORCH)

from hyperliquid_perps import orb_5m_scalp_specialist_gate  # noqa: E402

from strategy.hyperliquid.mapping import HYPERLIQUID_STRATEGY_MAPPING


def test_mapping_registers_orb_5m_scalp():
    assert "orb_5m_scalp" in HYPERLIQUID_STRATEGY_MAPPING
    module, cls = HYPERLIQUID_STRATEGY_MAPPING["orb_5m_scalp"]
    assert module == "strategy.hyperliquid.orb_5m_scalp_perp"
    assert cls == "Orb5mScalpPerpStrategy"


def test_perp_strategy_is_instantiable():
    from strategy.hyperliquid.orb_5m_scalp_perp import Orb5mScalpPerpStrategy

    strat = Orb5mScalpPerpStrategy(
        {"parameters": {"entry_timeframe": "5m"}},
        None,
        None,
    )
    assert strat.STRATEGY_NAME == "ORB 5m Scalp Perp"


def test_specialist_gate_passes_valid_signal():
    signal = {
        "strategy": "orb_5m_scalp",
        "signal": "long",
        "confidence": 0.75,
        "strength": 0.70,
        "details": {
            "indicators": {
                "setup": "orb_5m_scalp",
                "session_state": "signal",
                "breakout_valid": True,
                "retest_valid": True,
                "reward_risk": 2.0,
            }
        },
    }
    result = orb_5m_scalp_specialist_gate(signal, {"specialist_strategy_gates": {"orb_5m_scalp": {}}})
    assert result["isSpecialist"] is True
    assert result["allowed"] is True


def test_specialist_gate_rejects_waiting_retest():
    signal = {
        "strategy": "orb_5m_scalp",
        "signal": "hold",
        "confidence": 0.75,
        "strength": 0.70,
        "details": {
            "indicators": {
                "setup": "orb_5m_scalp",
                "session_state": "waiting_retest",
                "breakout_valid": True,
                "retest_valid": False,
                "reward_risk": 2.0,
            }
        },
    }
    result = orb_5m_scalp_specialist_gate(signal, {"specialist_strategy_gates": {"orb_5m_scalp": {}}})
    assert result["isSpecialist"] is True
    assert result["allowed"] is False
