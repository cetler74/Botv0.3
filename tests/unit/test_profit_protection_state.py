"""Unit tests for spot profit-protection / milestone state helpers."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ORCH = ROOT / "services" / "orchestrator-service"
if str(ORCH) not in sys.path:
    sys.path.insert(0, str(ORCH))

import profit_protection_state as pps  # noqa: E402
import spot_exit_config as sec  # noqa: E402


def test_setup_partial_does_not_block_arming():
    assert pps.can_arm_profit_protection("setup_partial") is True
    assert pps.can_arm_profit_protection("target_progress") is True
    assert pps.can_arm_profit_protection("inactive") is True
    assert pps.can_arm_profit_protection(None) is True
    assert pps.can_arm_profit_protection("profit_guaranteed") is False
    assert pps.can_arm_profit_protection("setup_breakeven") is False


def test_locked_and_milestone_classification():
    assert pps.is_profit_protection_locked("profit_guaranteed")
    assert pps.is_setup_milestone("setup_partial")
    assert pps.is_setup_milestone("target_progress")
    assert pps.should_breach_exit_for_status("target_progress")
    assert pps.should_breach_exit_for_status("setup_breakeven")
    assert not pps.should_breach_exit_for_status("inactive")


def test_near_miss_detection():
    assert pps.is_profit_protection_near_miss(
        peak_pct=1.39,
        current_pnl_pct=0.33,
        activation_pct=1.50,
        status="target_progress",
    )
    assert not pps.is_profit_protection_near_miss(
        peak_pct=1.39,
        current_pnl_pct=0.33,
        activation_pct=1.50,
        status="profit_guaranteed",
    )
    assert not pps.is_profit_protection_near_miss(
        peak_pct=1.00,
        current_pnl_pct=0.20,
        activation_pct=1.50,
        status="inactive",
    )


def test_milestone_floor_and_merge_trigger():
    assert abs(pps.milestone_floor_price(1.0, floor_pct=0.001) - 1.001) < 1e-9
    assert pps.merge_trail_trigger(1.0, 1.002) == 1.002
    assert pps.merge_trail_trigger(1.01, 1.002) == 1.01


def test_tiered_profit_lock_raises_highest_earned_floor():
    cfg = {
        "enabled": True,
        "tiers": [
            {"activation": 0.0055, "floor": 0.0015},
            {"activation": 0.0085, "floor": 0.0035},
            {"activation": 0.0120, "floor": 0.0070},
        ],
    }
    decision = pps.evaluate_tiered_profit_lock(
        config=cfg,
        peak_pct=0.90,
        entry_price=100.0,
        current_price=100.80,
        existing_trigger=100.15,
    )
    assert decision.action == "raise"
    assert decision.tier_index == 1
    assert decision.floor_price == pytest.approx(100.35)


def test_tiered_profit_lock_late_exits_after_giveback():
    decision = pps.evaluate_tiered_profit_lock(
        config={
            "enabled": True,
            "tiers": [{"activation": 0.0055, "floor": 0.0015}],
        },
        peak_pct=0.60,
        entry_price=100.0,
        current_price=100.10,
    )
    assert decision.action == "late_exit"
    assert decision.floor_price == pytest.approx(100.15)


def test_tiered_profit_lock_honors_fee_aware_minimum_floor():
    decision = pps.evaluate_tiered_profit_lock(
        config={
            "enabled": True,
            "tiers": [{"activation": 0.0055, "floor": 0.0015}],
        },
        peak_pct=0.60,
        entry_price=100.0,
        current_price=100.50,
        minimum_floor_decimal=0.0035,
    )
    assert decision.action == "raise"
    assert decision.floor_price == pytest.approx(100.35)


def test_ema20_exit_profile_arms_earlier_than_global():
    trading_cfg = {
        "trailing_stop": {"activation_threshold": 0.0180},
        "profit_protection": {"activation_threshold": 0.0150},
        "exit_profiles": {
            "ema20_ma50_spot_1h": {
                "strategies": ["ema20_ma50_spot_1h"],
                "trailing_stop": {"activation_threshold": 0.0135},
                "profit_protection": {"activation_threshold": 0.0120},
            }
        },
    }
    rules = sec.spot_strategy_exit_rules_from_trading_config(
        trading_cfg, "ema20_ma50_spot_1h"
    )
    assert rules.profile_name == "ema20_ma50_spot_1h"
    assert rules.profit_protection["activation_threshold"] == 0.0120
    assert rules.trailing_stop["activation_threshold"] == 0.0135


def test_feature_enabled_honors_false():
    assert pps.is_feature_enabled({"enabled": False}, default=True) is False
    assert pps.is_feature_enabled({"enabled": True}, default=False) is True
    assert pps.is_feature_enabled({}, default=True) is True
    assert pps.is_feature_enabled(None, default=True) is True


def test_resolve_profit_lock_floor_uses_max_of_knobs():
    floor = pps.resolve_profit_lock_floor_decimal(
        {"breakeven_floor_percentage": 0.008},
        {"guaranteed_min_profit": 0.012, "break_even_plus": 0.010},
    )
    assert abs(floor - 0.012) < 1e-9

    floor2 = pps.resolve_profit_lock_floor_decimal(
        {"breakeven_floor_percentage": 0.010},
        {"guaranteed_min_profit": 0.010, "break_even_plus": 0.010},
    )
    assert abs(floor2 - 0.010) < 1e-9


def test_late_arm_exits_instead_of_zombie_state():
    decision = pps.evaluate_profit_protection_arm(
        status="target_progress",
        peak_pct=1.30,
        activation_pct=1.20,
        entry_price=100.0,
        current_price=100.50,  # below floor at +1.0%
        floor_decimal=0.010,
        trailing_active=False,
        enabled=True,
        side="long",
    )
    assert decision.action == "late_exit"
    assert decision.reason == pps.EXIT_REASON_PROFIT_PROTECTION_LATE_BREACH
    assert abs(decision.floor_price - 101.0) < 1e-9


def test_arm_when_mark_still_above_floor():
    decision = pps.evaluate_profit_protection_arm(
        status="setup_partial",
        peak_pct=1.30,
        activation_pct=1.20,
        entry_price=100.0,
        current_price=101.50,
        floor_decimal=0.010,
        trailing_active=False,
        enabled=True,
        side="long",
    )
    assert decision.action == "arm"
    assert abs(decision.floor_price - 101.0) < 1e-9


def test_skip_when_profit_protection_disabled():
    decision = pps.evaluate_profit_protection_arm(
        status="inactive",
        peak_pct=2.0,
        activation_pct=1.0,
        entry_price=100.0,
        current_price=102.0,
        floor_decimal=0.01,
        enabled=False,
    )
    assert decision.action == "skip"
    assert decision.reason == "profit_protection_disabled"


def test_distinct_breach_exit_reasons_include_legacy_alias():
    reason = pps.format_breach_exit_reason(
        "profit_guaranteed",
        pnl_percentage=-0.20,
        trigger_price=101.0,
        current_price=100.5,
    )
    assert "profit_guaranteed_breach" in reason
    assert "profit_protection_breach" in reason

    milestone_reason = pps.format_breach_exit_reason(
        "target_progress",
        pnl_percentage=0.05,
        trigger_price=100.1,
        current_price=100.05,
    )
    assert "target_progress_breach" in milestone_reason
    assert "profit_protection_breach" in milestone_reason


def test_may_not_reset_locked_or_milestone_for_trail():
    assert pps.may_reset_profit_protection_for_trail("profit_guaranteed") is False
    assert pps.may_reset_profit_protection_for_trail("setup_breakeven") is False
    assert pps.may_reset_profit_protection_for_trail("target_progress") is False
    assert pps.may_reset_profit_protection_for_trail("inactive") is True
    assert pps.may_reset_profit_protection_for_trail(None) is True


def test_near_miss_metadata_payload():
    payload = pps.near_miss_metadata_payload(
        peak_pct=1.39,
        current_pnl_pct=0.33,
        activation_pct=1.50,
        status="target_progress",
    )
    assert payload["profit_protection_near_miss"] is True
    assert payload["profit_protection_near_miss_peak_pct"] == 1.39
    assert payload["profit_protection_near_miss_status"] == "target_progress"


def test_weekly_fibonacci_profile_disables_pp_and_trail():
    trading_cfg = {
        "trailing_stop": {"enabled": True, "activation_threshold": 0.0180},
        "profit_protection": {"enabled": True, "activation_threshold": 0.0150},
        "exit_profiles": {
            "weekly_fibonacci_spot": {
                "strategies": ["weekly_fibonacci_spot"],
                "trailing_stop": {"enabled": False},
                "profit_protection": {"enabled": False},
            }
        },
    }
    rules = sec.spot_strategy_exit_rules_from_trading_config(
        trading_cfg, "weekly_fibonacci_spot"
    )
    assert pps.is_feature_enabled(rules.trailing_stop, default=True) is False
    assert pps.is_feature_enabled(rules.profit_protection, default=True) is False
