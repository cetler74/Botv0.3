"""Unit tests for per-strategy spot exit profile resolution."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ORCH = ROOT / "services" / "orchestrator-service"
if str(ORCH) not in sys.path:
    sys.path.insert(0, str(ORCH))

import spot_exit_config as sec  # noqa: E402


def test_arc_daytrade_profile_maximizes_profit_with_early_pp_and_wide_trail():
    trading_cfg = {
        "trailing_stop": {
            "activation_threshold": 0.0180,
            "breakeven_floor_percentage": 0.0140,
            "step_percentage": 0.0040,
            "tighten_profit_threshold": 0.0120,
        },
        "profit_protection": {
            "enabled": True,
            "activation_threshold": 0.0150,
        },
        "exit_profiles": {
            "arc_daytrade": {
                "strategies": ["arc_daytrade"],
                "stagnant_loser_enabled": False,
                "trailing_stop": {
                    "activation_threshold": 0.0100,
                    "breakeven_floor_percentage": 0.0070,
                    "step_percentage": 0.0040,
                    "tighten_profit_threshold": 0.0150,
                    "tightened_step_percentage": 0.0025,
                },
                "profit_protection": {
                    "activation_threshold": 0.0080,
                },
            }
        },
    }

    rules = sec.spot_strategy_exit_rules_from_trading_config(trading_cfg, "arc_daytrade")

    assert rules.profile_name == "arc_daytrade"
    # PP arms before trail so chop peaks lock a fee+ floor.
    assert rules.profit_protection["activation_threshold"] == 0.0080
    assert rules.trailing_stop["activation_threshold"] == 0.0100
    assert rules.trailing_stop["step_percentage"] == 0.0040
    assert rules.trailing_stop["tighten_profit_threshold"] == 0.0150
    assert rules.trailing_stop["breakeven_floor_percentage"] == 0.0070
    assert rules.stagnant_loser_enabled is False


def test_unknown_strategy_uses_global_defaults():
    trading_cfg = {
        "trailing_stop": {"activation_threshold": 0.0180},
        "profit_protection": {"activation_threshold": 0.0150},
        "exit_profiles": {
            "arc_daytrade": {
                "strategies": ["arc_daytrade"],
                "trailing_stop": {"activation_threshold": 0.0100},
            }
        },
    }

    rules = sec.spot_strategy_exit_rules_from_trading_config(
        trading_cfg, "ema50_breakout_pullback"
    )

    assert rules.profile_name == ""
    assert rules.trailing_stop["activation_threshold"] == 0.0180
    assert rules.profit_protection["activation_threshold"] == 0.0150
    assert rules.stagnant_loser_enabled is None


def test_stagnant_loser_disabled_via_profile_or_global_list():
    trading_cfg = {
        "stagnant_loser": {"disabled_strategies": ["arc_daytrade"]},
        "exit_profiles": {},
    }
    assert sec.is_stagnant_loser_disabled_for_strategy(trading_cfg, "arc_daytrade") is True

    trading_cfg = {
        "stagnant_loser": {"disabled_strategies": []},
        "exit_profiles": {
            "arc_daytrade": {
                "strategies": ["arc_daytrade"],
                "stagnant_loser_enabled": False,
            }
        },
    }
    assert sec.is_stagnant_loser_disabled_for_strategy(trading_cfg, "arc_daytrade") is True
    assert sec.is_stagnant_loser_disabled_for_strategy(trading_cfg, "dual_sma_daytrade") is False
