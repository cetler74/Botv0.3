import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ORCH = os.path.join(ROOT, "services", "orchestrator-service")
if ORCH not in sys.path:
    sys.path.insert(0, ORCH)

from hyperliquid_perps import (  # noqa: E402
    PaperPerpExitConfig,
    calculate_perp_pnl,
    evaluate_paper_perp_exit,
    hyperliquid_coin_entry_block,
    hyperliquid_reentry_cooldown_check,
    hyperliquid_regime_direction_gate,
    hyperliquid_standalone_entry_gate,
    hyperliquid_strategy_side_performance,
    hyperliquid_strategy_side_entry_block,
    is_caution_window,
    paper_perp_exit_config_from_yaml,
    paper_perp_position_size_multiplier,
    perp_entry_atr_metadata,
    perp_side_fee,
    pair_to_hyperliquid_coin,
    pnl_percentage,
    position_sides_from_signal,
    select_mirrored_signal,
    sma_reclaim_bull_flag_specialist_gate,
    should_close_paper_perp,
)


def _spot_like_exit_cfg() -> PaperPerpExitConfig:
    return paper_perp_exit_config_from_yaml(
        {"use_spot_exit_rules": True, "stop_loss_pct": 1.5, "max_holding_minutes": 240},
        {
            "stop_loss_percentage": 0.015,
            "overall_profit_take_exit_pct": 0.045,
            "trailing_stop": {
                "enabled": True,
                "activation_threshold": 0.0035,
                "step_percentage": 0.0025,
                "tightened_step_percentage": 0.0020,
                "dynamic_tightening_enabled": True,
                "tighten_profit_threshold": 0.0035,
                "breakeven_floor_percentage": 0.0035,
                "min_trigger_distance_percentage": 0.0035,
            },
            "profit_protection": {
                "enabled": True,
                "activation_threshold": 0.0035,
            },
        },
    )


def test_pair_to_hyperliquid_coin():
    assert pair_to_hyperliquid_coin("NEAR/USDC") == "NEAR"
    assert pair_to_hyperliquid_coin("BTCUSD") == "BTC"
    assert pair_to_hyperliquid_coin("ETHUSDT") == "ETH"


def test_position_sides_from_signal():
    assert position_sides_from_signal("long") == "long"
    assert position_sides_from_signal("short") == "short"
    assert position_sides_from_signal("buy") == "long"
    assert position_sides_from_signal("sell") == "short"
    assert position_sides_from_signal("hold") is None


def test_hyperliquid_perps_use_centralized_exit_rules():
    config_path = Path(ROOT) / "config" / "config.yaml"
    cfg = yaml.safe_load(config_path.read_text())
    hl_cfg = cfg["trading"]["hyperliquid_perps"]
    assert hl_cfg["use_strategy_exits"] is False
    assert hl_cfg["use_spot_exit_rules"] is True
    assert hl_cfg["fixed_stop_loss_enabled"] is True
    assert hl_cfg["profit_protection_fee_buffer"] == pytest.approx(0.0015)
    assert hl_cfg["max_margin_per_trade"] == pytest.approx(100.0)
    assert hl_cfg["max_notional_per_trade"] == pytest.approx(200.0)
    assert hl_cfg["max_open_positions"] == 6


def test_perp_side_fee():
    assert perp_side_fee(50.0, 0.001) == pytest.approx(0.05)
    assert perp_side_fee(0.0, 0.001) == 0.0


def test_side_aware_pnl():
    assert calculate_perp_pnl("long", 100, 110, 2) == 20
    assert calculate_perp_pnl("short", 100, 90, 2) == 20
    assert calculate_perp_pnl("short", 100, 110, 2, fees=1) == -21
    assert pnl_percentage("long", 100, 110) == 10
    assert pnl_percentage("short", 100, 90) == 10


def test_select_mirrored_signal_uses_consensus_direction_and_best_strategy_confidence():
    payload = {
        "consensus": {"signal": "short", "confidence": 0.7, "agreement": 60},
        "strategies": {
            "macd": {"signal": "short", "confidence": 0.8, "strength": 0.6},
            "rsi": {"signal": "long", "confidence": 0.9, "strength": 0.4},
        },
    }
    selected = select_mirrored_signal(payload)
    assert selected["signal"] == "short"
    assert selected["strategy"] == "macd"
    assert selected["confidence"] == 0.8
    assert selected["consensus_confidence"] == 0.7


def test_select_mirrored_signal_does_not_dilute_standalone_entry_confidence():
    payload = {
        "consensus": {"signal": "long", "confidence": 0.0529, "agreement": 7.14},
        "strategies": {
            "breakout_retest_long": {
                "signal": "long",
                "confidence": 0.74,
                "strength": 0.75,
            },
            "macd": {"signal": "hold", "confidence": 0.0, "strength": 0.0},
            "rsi": {"signal": "hold", "confidence": 0.0, "strength": 0.0},
        },
    }

    selected = select_mirrored_signal(payload)

    assert selected["signal"] == "long"
    assert selected["strategy"] == "breakout_retest_long"
    assert selected["confidence"] == 0.74
    assert selected["consensus_confidence"] == pytest.approx(0.0529)
    assert selected["consensus_agreement"] == pytest.approx(7.14)


def test_select_mirrored_signal_uses_best_individual_when_consensus_hold():
    payload = {
        "consensus": {"signal": "hold", "confidence": 0.2, "agreement": 40},
        "strategies": {
            "weak_long": {"signal": "long", "confidence": 0.5, "strength": 0.6},
            "strong_short": {"signal": "short", "confidence": 0.8, "strength": 0.7},
        },
    }
    selected = select_mirrored_signal(payload)
    assert selected["signal"] == "short"
    assert selected["strategy"] == "strong_short"


def test_paper_perp_position_size_multiplier_moderate_profile():
    cfg = {
        "position_sizing": {
            "enabled": True,
            "weak_multiplier": 0.35,
            "normal_multiplier": 0.70,
            "strong_multiplier": 1.0,
            "normal_confidence": 0.62,
            "strong_confidence": 0.72,
            "normal_strength": 0.60,
            "strong_strength": 0.68,
            "normal_agreement": 60,
            "strong_agreement": 65,
        }
    }
    assert paper_perp_position_size_multiplier({"confidence": 0.56, "strength": 0.50}, cfg) == pytest.approx(0.35)
    assert paper_perp_position_size_multiplier(
        {"confidence": 0.64, "strength": 0.50, "consensus_agreement": 7.14},
        cfg,
    ) == pytest.approx(0.35)
    assert paper_perp_position_size_multiplier(
        {"confidence": 0.64, "strength": 0.50, "consensus_agreement": 60},
        cfg,
    ) == pytest.approx(0.70)
    assert paper_perp_position_size_multiplier(
        {"confidence": 0.74, "strength": 0.70, "consensus_agreement": 66},
        cfg,
    ) == pytest.approx(1.0)


def test_sma_reclaim_bull_flag_specialist_gate_bypasses_consensus_when_setup_passes():
    signal = {
        "strategy": "sma_reclaim_bull_flag",
        "signal": "long",
        "confidence": 0.88,
        "strength": 0.74,
        "consensus_agreement": 6.67,
        "details": {
            "state": {
                "indicators": {
                    "setup": "sma_reclaim_bull_flag",
                    "invalidation_reason": "none",
                    "reward_risk": 2.1,
                    "stop_pct": 0.018,
                }
            }
        },
    }
    cfg = {
        "specialist_strategy_gates": {
            "sma_reclaim_bull_flag": {
                "enabled": True,
                "bypass_consensus": True,
                "min_confidence": 0.85,
                "min_strength": 0.70,
                "min_reward_risk": 1.8,
                "max_stop_pct": 0.03,
                "size_multiplier": 0.35,
            }
        }
    }

    gate = sma_reclaim_bull_flag_specialist_gate(signal, cfg)

    assert gate["isSpecialist"] is True
    assert gate["allowed"] is True
    assert gate["bypassConsensus"] is True
    assert gate["sizeMultiplier"] == pytest.approx(0.35)


def test_sma_reclaim_bull_flag_specialist_gate_requires_own_risk_metadata():
    signal = {
        "strategy": "sma_reclaim_bull_flag",
        "signal": "long",
        "confidence": 0.88,
        "strength": 0.74,
        "details": {
            "state": {
                "indicators": {
                    "setup": "sma_reclaim_bull_flag",
                    "invalidation_reason": "none",
                    "reward_risk": 1.2,
                    "stop_pct": 0.045,
                }
            }
        },
    }

    gate = sma_reclaim_bull_flag_specialist_gate(signal, {})

    assert gate["isSpecialist"] is True
    assert gate["allowed"] is False
    assert gate["bypassConsensus"] is False
    assert "rr_1.20_lt_1.80" in gate["reason"]
    assert "stop_pct_0.0450_gt_0.0300" in gate["reason"]


def test_hyperliquid_standalone_gate_allows_heterogeneous_strategy_without_global_consensus():
    signal = {
        "strategy": "vwma_hull",
        "signal": "short",
        "confidence": 1.0,
        "strength": 0.48,
        "consensus_agreement": 6.67,
        "consensus_confidence": 0.07,
    }

    gate = hyperliquid_standalone_entry_gate(signal, {})

    assert gate["isStandalone"] is True
    assert gate["allowed"] is True
    assert gate["bypassConsensus"] is True
    assert gate["family"] == "trend_momentum"
    assert gate["reason"] == "standalone_gate_pass"


def test_hyperliquid_standalone_gate_blocks_strong_opposite_signal():
    signal = {
        "strategy": "breakout_retest_long",
        "signal": "long",
        "confidence": 0.74,
        "strength": 0.75,
        "opposite_strategy": "heikin_ashi",
        "opposite_confidence": 0.90,
        "opposite_strength": 0.80,
    }

    gate = hyperliquid_standalone_entry_gate(signal, {})

    assert gate["isStandalone"] is True
    assert gate["allowed"] is False
    assert gate["bypassConsensus"] is False
    assert "opposite_0.90_0.80" in gate["reason"]


def test_hyperliquid_strategy_side_performance_tracks_recent_closed_results():
    closed = [
        {
            "source_strategy": "vwma_hull",
            "position_side": "short",
            "realized_pnl": -2.0,
            "exit_time": "2026-05-25T12:00:00+00:00",
        },
        {
            "source_strategy": "vwma_hull",
            "position_side": "short",
            "realized_pnl": -3.0,
            "exit_time": "2026-05-25T11:00:00+00:00",
        },
        {
            "source_strategy": "vwma_hull",
            "position_side": "short",
            "realized_pnl": 5.0,
            "exit_time": "2026-05-25T10:00:00+00:00",
        },
        {
            "source_strategy": "vwma_hull",
            "position_side": "long",
            "realized_pnl": 99.0,
            "exit_time": "2026-05-25T13:00:00+00:00",
        },
        {
            "source_strategy": "heikin_ashi",
            "position_side": "short",
            "realized_pnl": 99.0,
            "exit_time": "2026-05-25T13:30:00+00:00",
        },
    ]

    perf = hyperliquid_strategy_side_performance("vwma_hull", "short", closed)

    assert perf["closedCount"] == 3
    assert perf["wins"] == 1
    assert perf["losses"] == 2
    assert perf["consecutiveLosses"] == 2
    assert perf["realizedPnl"] == pytest.approx(0.0)
    assert perf["grossProfit"] == pytest.approx(5.0)
    assert perf["grossLoss"] == pytest.approx(5.0)
    assert perf["profitFactor"] == pytest.approx(1.0)
    assert perf["winRate"] == pytest.approx(1 / 3)
    assert perf["latestPnl"] == pytest.approx(-2.0)
    assert perf["latestExitTime"] == "2026-05-25T12:00:00+00:00"


def test_hyperliquid_strategy_side_performance_respects_lookback():
    closed = [
        {
            "source_strategy": "breakout_retest_long",
            "source_signal": "buy",
            "realized_pnl": -1.0,
            "exit_time": "2026-05-25T12:00:00+00:00",
        },
        {
            "source_strategy": "breakout_retest_long",
            "source_signal": "buy",
            "realized_pnl": -2.0,
            "exit_time": "2026-05-25T11:00:00+00:00",
        },
        {
            "source_strategy": "breakout_retest_long",
            "source_signal": "buy",
            "realized_pnl": 10.0,
            "exit_time": "2026-05-25T10:00:00+00:00",
        },
    ]

    perf = hyperliquid_strategy_side_performance(
        "breakout_retest_long",
        "long",
        closed,
        lookback_trades=2,
    )

    assert perf["closedCount"] == 2
    assert perf["wins"] == 0
    assert perf["losses"] == 2
    assert perf["consecutiveLosses"] == 2
    assert perf["realizedPnl"] == pytest.approx(-3.0)


def test_paper_perp_position_size_multiplier_can_be_disabled():
    assert paper_perp_position_size_multiplier(
        {"confidence": 0.56, "strength": 0.50},
        {"position_sizing": {"enabled": False}},
    ) == pytest.approx(1.0)


def test_hyperliquid_strategy_side_entry_block_after_recent_loss():
    now = datetime(2026, 5, 25, 12, 0, 0)
    closed = [
        {
            "coin": "WLD",
            "source_strategy": "breakout_retest_long",
            "position_side": "long",
            "realized_pnl": -12.5,
            "exit_time": "2026-05-25T08:00:00+00:00",
        },
        {
            "coin": "ETH",
            "source_strategy": "breakout_retest_long",
            "position_side": "short",
            "realized_pnl": -3.0,
            "exit_time": "2026-05-25T09:00:00+00:00",
        },
    ]

    block = hyperliquid_strategy_side_entry_block(
        "breakout_retest_long",
        "long",
        closed,
        now=now,
        realized_block_hours=12,
    )

    assert block["entryBlocked"] is True
    assert block["entryBlockReason"] == "recent_strategy_side_negative_realized_12h"
    assert "breakout_retest_long long realized loss" in block["entryBlockMessage"]

    short_block = hyperliquid_strategy_side_entry_block(
        "breakout_retest_long",
        "short",
        closed,
        now=now,
        realized_block_hours=12,
    )
    assert short_block["entryBlocked"] is True

    other_strategy = hyperliquid_strategy_side_entry_block(
        "swing_hull_rsi_ema",
        "long",
        closed,
        now=now,
        realized_block_hours=12,
    )
    assert other_strategy["entryBlocked"] is False


def test_should_close_paper_perp():
    trade = {
        "entry_price": 100,
        "position_side": "short",
        "entry_time": (datetime.utcnow() - timedelta(minutes=5)).isoformat(),
    }
    assert should_close_paper_perp(
        trade,
        97,
        stop_loss_pct=1.5,
        take_profit_pct=2.5,
        max_holding_minutes=240,
    ) == "paper_take_profit"
    assert should_close_paper_perp(
        trade,
        102,
        stop_loss_pct=1.5,
        take_profit_pct=2.5,
        max_holding_minutes=240,
    ) == "paper_stop_loss"


def test_paper_perp_exit_config_uses_spot_trailing_not_fixed_tp():
    cfg = _spot_like_exit_cfg()
    assert cfg.use_spot_exit_rules is True
    assert cfg.fixed_stop_loss_enabled is True
    assert cfg.take_profit_pct == 0.0
    assert cfg.trailing_activation_decimal == pytest.approx(0.0035)
    assert cfg.effective_profit_floor_decimal == pytest.approx(0.0035)


def test_paper_perp_stop_loss_takes_precedence_over_max_hold():
    cfg = PaperPerpExitConfig(
        use_spot_exit_rules=True,
        fixed_stop_loss_enabled=True,
        stop_loss_pct=1.5,
        max_holding_minutes=1,
    )
    trade = {
        "entry_price": 100.0,
        "position_side": "long",
        "entry_time": (datetime.utcnow() - timedelta(minutes=5)).isoformat(),
        "metadata": {},
    }

    result = evaluate_paper_perp_exit(trade, 98.0, cfg)

    assert result.exit_reason == "paper_stop_loss"


def test_profit_protection_floor_uses_round_trip_fees_plus_buffer():
    cfg = paper_perp_exit_config_from_yaml(
        {
            "use_spot_exit_rules": True,
            "fee_rate_per_side": 0.001,
            "profit_protection_fee_buffer": 0.0015,
        },
        {
            "trailing_stop": {
                "enabled": True,
                "activation_threshold": 0.001,
                "breakeven_floor_percentage": 0.001,
                "min_trigger_distance_percentage": 0.001,
            },
            "profit_protection": {"enabled": True, "activation_threshold": 0.001},
        },
    )

    assert cfg.effective_profit_floor_decimal == pytest.approx(0.0035)
    assert cfg.breakeven_floor_decimal == pytest.approx(0.0035)
    assert cfg.profit_protection_activation_decimal == pytest.approx(0.0035)


def test_low_gross_profit_pullback_does_not_profit_protect_into_net_loss():
    cfg = paper_perp_exit_config_from_yaml(
        {
            "use_spot_exit_rules": True,
            "fee_rate_per_side": 0.001,
            "profit_protection_fee_buffer": 0.0015,
        },
        {
            "trailing_stop": {
                "enabled": True,
                "activation_threshold": 0.001,
                "breakeven_floor_percentage": 0.001,
                "min_trigger_distance_percentage": 0.001,
            },
            "profit_protection": {"enabled": True, "activation_threshold": 0.001},
        },
    )
    trade = {
        "entry_price": 100.0,
        "position_side": "long",
        "entry_time": (datetime.utcnow() - timedelta(minutes=5)).isoformat(),
        "metadata": {},
    }

    peaked = evaluate_paper_perp_exit(trade, 100.2, cfg)
    pulled_back = evaluate_paper_perp_exit({**trade, "metadata": peaked.metadata}, 100.05, cfg)

    assert peaked.exit_reason is None
    assert peaked.metadata.get("profit_protection") is None
    assert pulled_back.exit_reason is None


def test_paper_perp_can_disable_fixed_stop_loss_while_keeping_trailing():
    cfg = paper_perp_exit_config_from_yaml(
        {
            "use_spot_exit_rules": True,
            "fixed_stop_loss_enabled": False,
            "stop_loss_pct": 1.5,
            "max_holding_minutes": 240,
        },
        {
            "stop_loss_percentage": 0.015,
            "trailing_stop": {"enabled": True, "activation_threshold": 0.0035},
            "profit_protection": {"enabled": True, "activation_threshold": 0.0035},
        },
    )
    trade = {
        "entry_price": 100.0,
        "position_side": "long",
        "entry_time": (datetime.utcnow() - timedelta(minutes=5)).isoformat(),
        "metadata": {},
    }

    result = evaluate_paper_perp_exit(trade, 98.0, cfg)

    assert cfg.stop_loss_pct == pytest.approx(1.5)
    assert result.exit_reason is None


def test_paper_perp_disabled_fixed_stop_loss_applies_to_short_positions():
    cfg = paper_perp_exit_config_from_yaml(
        {
            "use_spot_exit_rules": True,
            "fixed_stop_loss_enabled": False,
            "stop_loss_pct": 1.5,
            "max_holding_minutes": 240,
        },
        {
            "stop_loss_percentage": 0.015,
            "trailing_stop": {"enabled": True, "activation_threshold": 0.0035},
            "profit_protection": {"enabled": True, "activation_threshold": 0.0035},
        },
    )
    trade = {
        "entry_price": 100.0,
        "position_side": "short",
        "entry_time": (datetime.utcnow() - timedelta(minutes=5)).isoformat(),
        "metadata": {},
    }

    result = evaluate_paper_perp_exit(trade, 102.0, cfg)

    assert result.exit_reason is None


def test_hyperliquid_coin_entry_block_open_negative_unrealized():
    block = hyperliquid_coin_entry_block(
        "WLD",
        [{"coin": "WLD", "unrealized_pnl": -0.01}],
        [],
        now=datetime(2026, 5, 24, 12, 0, 0),
    )

    assert block["entryBlocked"] is True
    assert block["entryBlockReason"] == "open_unrealized_negative"


def test_hyperliquid_coin_entry_block_recent_negative_realized():
    now = datetime(2026, 5, 24, 12, 0, 0)
    block = hyperliquid_coin_entry_block(
        "WLD/USD-PERP",
        [],
        [{"coin": "WLD", "realized_pnl": -1.0, "exit_time": (now - timedelta(hours=2)).isoformat()}],
        now=now,
        realized_block_hours=12,
    )

    assert block["entryBlocked"] is True
    assert block["entryBlockReason"] == "recent_negative_realized_12h"
    assert block["entryBlockUntil"]


def test_hyperliquid_coin_entry_block_expired_negative_realized_allows_entry():
    now = datetime(2026, 5, 24, 12, 0, 0)
    block = hyperliquid_coin_entry_block(
        "WLD",
        [],
        [{"coin": "WLD", "realized_pnl": -1.0, "exit_time": (now - timedelta(hours=13)).isoformat()}],
        now=now,
        realized_block_hours=12,
    )

    assert block["entryBlocked"] is False


def test_spot_trailing_long_does_not_fixed_tp_at_3pct():
    """NEAR-like move (+3.4%) should stay open until trail pulls back, not fixed TP."""
    trade = {
        "entry_price": 2.32085,
        "position_side": "long",
        "entry_time": (datetime.utcnow() - timedelta(minutes=5)).isoformat(),
        "metadata": {},
    }
    cfg = _spot_like_exit_cfg()
    result = evaluate_paper_perp_exit(trade, 2.40145, cfg)
    assert result.exit_reason is None
    assert result.metadata.get("trail_stop") == "active"
    assert float(result.metadata.get("trail_stop_trigger") or 0) > 0


def test_spot_trailing_long_exits_on_pullback():
    trade = {
        "entry_price": 100.0,
        "position_side": "long",
        "entry_time": (datetime.utcnow() - timedelta(minutes=5)).isoformat(),
        "metadata": {},
    }
    cfg = _spot_like_exit_cfg()
    armed = evaluate_paper_perp_exit(trade, 100.7, cfg)
    assert armed.exit_reason is None
    assert armed.metadata.get("trail_stop") == "active"
    trigger = float(armed.metadata["trail_stop_trigger"])
    exited = evaluate_paper_perp_exit(
        {**trade, "metadata": armed.metadata},
        trigger - 0.01,
        cfg,
    )
    assert exited.exit_reason
    assert "trailing_stop" in exited.exit_reason


def test_spot_trailing_short_exits_on_bounce():
    trade = {
        "entry_price": 100.0,
        "position_side": "short",
        "entry_time": (datetime.utcnow() - timedelta(minutes=5)).isoformat(),
        "metadata": {},
    }
    cfg = _spot_like_exit_cfg()
    armed = evaluate_paper_perp_exit(trade, 99.3, cfg)
    assert armed.metadata.get("trail_stop") == "active"
    trigger = float(armed.metadata["trail_stop_trigger"])
    exited = evaluate_paper_perp_exit(
        {**trade, "metadata": armed.metadata},
        trigger + 0.01,
        cfg,
    )
    assert exited.exit_reason
    assert "trailing_stop" in exited.exit_reason


# ---------------------------------------------------------------------------
# Change 2: Wider trailing stop defaults
# ---------------------------------------------------------------------------


def test_default_exit_config_uses_widened_trailing_params():
    cfg = PaperPerpExitConfig()
    assert cfg.trailing_activation_decimal == pytest.approx(0.0075)
    assert cfg.trailing_step_decimal == pytest.approx(0.0050)
    assert cfg.tightened_step_decimal == pytest.approx(0.0030)
    assert cfg.tighten_profit_threshold_decimal == pytest.approx(0.0150)
    assert cfg.breakeven_floor_decimal == pytest.approx(0.0050)
    assert cfg.min_trigger_distance_decimal == pytest.approx(0.0050)
    assert cfg.profit_protection_activation_decimal == pytest.approx(0.0050)


def test_widened_trailing_does_not_arm_at_half_percent():
    """With activation at 0.75%, a +0.50% move should NOT activate the trail."""
    trade = {
        "entry_price": 100.0,
        "position_side": "long",
        "entry_time": (datetime.utcnow() - timedelta(minutes=5)).isoformat(),
        "metadata": {},
    }
    cfg = PaperPerpExitConfig()
    result = evaluate_paper_perp_exit(trade, 100.50, cfg)
    assert result.exit_reason is None
    assert result.metadata.get("trail_stop") != "active"


def test_widened_trailing_arms_at_one_percent():
    """With activation at 0.75%, a +1.0% move should activate the trail."""
    trade = {
        "entry_price": 100.0,
        "position_side": "long",
        "entry_time": (datetime.utcnow() - timedelta(minutes=5)).isoformat(),
        "metadata": {},
    }
    cfg = PaperPerpExitConfig()
    result = evaluate_paper_perp_exit(trade, 101.0, cfg)
    assert result.exit_reason is None
    assert result.metadata.get("trail_stop") == "active"


# ---------------------------------------------------------------------------
# Change 3: Counter-trend regime direction gate
# ---------------------------------------------------------------------------


def test_regime_direction_gate_blocks_short_in_trending_up():
    gate = hyperliquid_regime_direction_gate("short", "trending_up", 0.75, 0.60)
    assert gate["blocked"] is True
    assert "counter_trend_blocked" in gate["reason"]


def test_regime_direction_gate_blocks_long_in_trending_down():
    gate = hyperliquid_regime_direction_gate("long", "trending_down", 0.75, 0.60)
    assert gate["blocked"] is True
    assert "counter_trend_blocked" in gate["reason"]


def test_regime_direction_gate_allows_long_in_trending_up():
    gate = hyperliquid_regime_direction_gate("long", "trending_up", 0.75, 0.60)
    assert gate["blocked"] is False


def test_regime_direction_gate_allows_any_in_sideways():
    for side in ("long", "short"):
        gate = hyperliquid_regime_direction_gate(side, "sideways", 0.50, 0.50)
        assert gate["blocked"] is False


def test_regime_direction_gate_high_conviction_override():
    gate = hyperliquid_regime_direction_gate("short", "trending_up", 0.92, 0.85)
    assert gate["blocked"] is False
    assert gate["reason"] == "counter_trend_override_high_conviction"
    assert gate["sizeMultiplier"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Change 4: Per-side standalone gate for VWMA Hull
# ---------------------------------------------------------------------------


def test_standalone_gate_uses_per_side_min_confidence_short():
    signal = {
        "strategy": "vwma_hull",
        "signal": "short",
        "confidence": 0.80,
        "strength": 0.50,
    }
    cfg = {
        "standalone_strategy_gates": {
            "global": {"enabled": True},
            "vwma_hull": {
                "enabled": True,
                "min_confidence": 0.70,
                "min_confidence_short": 0.85,
                "min_strength": 0.20,
            },
        }
    }
    gate = hyperliquid_standalone_entry_gate(signal, cfg)
    assert gate["isStandalone"] is True
    assert gate["allowed"] is False
    assert "confidence_0.80_lt_0.85" in gate["reason"]


def test_standalone_gate_uses_per_side_confidence_long_uses_default():
    signal = {
        "strategy": "vwma_hull",
        "signal": "long",
        "confidence": 0.72,
        "strength": 0.50,
    }
    cfg = {
        "standalone_strategy_gates": {
            "global": {"enabled": True},
            "vwma_hull": {
                "enabled": True,
                "min_confidence": 0.70,
                "min_confidence_short": 0.85,
                "min_strength": 0.20,
            },
        }
    }
    gate = hyperliquid_standalone_entry_gate(signal, cfg)
    assert gate["isStandalone"] is True
    assert gate["allowed"] is True


# ---------------------------------------------------------------------------
# Change 5: Re-entry cooldown
# ---------------------------------------------------------------------------


def test_reentry_cooldown_blocks_within_window():
    now = datetime(2026, 5, 26, 14, 0, 0)
    closed_trades = [
        {
            "coin": "WLD",
            "position_side": "long",
            "realized_pnl": 1.50,
            "exit_time": (now - timedelta(minutes=15)).isoformat(),
        }
    ]
    result = hyperliquid_reentry_cooldown_check(
        "WLD", "long", closed_trades, cooldown_minutes=30, now=now,
    )
    assert result["blocked"] is True
    assert "reentry_cooldown" in result["reason"]


def test_reentry_cooldown_allows_after_window():
    now = datetime(2026, 5, 26, 14, 0, 0)
    closed_trades = [
        {
            "coin": "WLD",
            "position_side": "long",
            "realized_pnl": 1.50,
            "exit_time": (now - timedelta(minutes=45)).isoformat(),
        }
    ]
    result = hyperliquid_reentry_cooldown_check(
        "WLD", "long", closed_trades, cooldown_minutes=30, now=now,
    )
    assert result["blocked"] is False


def test_reentry_cooldown_does_not_cross_sides():
    now = datetime(2026, 5, 26, 14, 0, 0)
    closed_trades = [
        {
            "coin": "WLD",
            "position_side": "short",
            "realized_pnl": -0.50,
            "exit_time": (now - timedelta(minutes=5)).isoformat(),
        }
    ]
    result = hyperliquid_reentry_cooldown_check(
        "WLD", "long", closed_trades, cooldown_minutes=30, now=now,
    )
    assert result["blocked"] is False


# ---------------------------------------------------------------------------
# Change 6: Session-aware position sizing
# ---------------------------------------------------------------------------


def test_is_caution_window_inside():
    cfg = {
        "session_sizing": {
            "enabled": True,
            "caution_multiplier": 0.5,
            "caution_windows": [{"start_utc": 10, "end_utc": 12}],
        }
    }
    is_caution, mult = is_caution_window(10, cfg)
    assert is_caution is True
    assert mult == pytest.approx(0.5)

    is_caution2, _ = is_caution_window(11, cfg)
    assert is_caution2 is True


def test_is_caution_window_outside():
    cfg = {
        "session_sizing": {
            "enabled": True,
            "caution_multiplier": 0.5,
            "caution_windows": [{"start_utc": 10, "end_utc": 12}],
        }
    }
    is_caution, mult = is_caution_window(12, cfg)
    assert is_caution is False
    assert mult == pytest.approx(1.0)


def test_is_caution_window_disabled():
    cfg = {
        "session_sizing": {
            "enabled": False,
            "caution_multiplier": 0.5,
            "caution_windows": [{"start_utc": 10, "end_utc": 12}],
        }
    }
    is_caution, mult = is_caution_window(11, cfg)
    assert is_caution is False
    assert mult == pytest.approx(1.0)


def test_is_caution_window_wrapping():
    """A window from 22:00 to 02:00 UTC should wrap around midnight."""
    cfg = {
        "session_sizing": {
            "enabled": True,
            "caution_multiplier": 0.4,
            "caution_windows": [{"start_utc": 22, "end_utc": 2}],
        }
    }
    is_caution, mult = is_caution_window(23, cfg)
    assert is_caution is True
    assert mult == pytest.approx(0.4)

    is_caution2, _ = is_caution_window(1, cfg)
    assert is_caution2 is True

    is_caution3, _ = is_caution_window(3, cfg)
    assert is_caution3 is False


# ---------------------------------------------------------------------------
# Phase 3 (2026-05-27): Exit logic rework
#   - Breakeven max-hold exit + salvage trail
#   - ATR-based stop loss with fixed-pct fallback
#   - Per-coin stop overrides
# ---------------------------------------------------------------------------


def _phase3_exit_cfg(**overrides) -> PaperPerpExitConfig:
    defaults = dict(
        use_spot_exit_rules=True,
        fixed_stop_loss_enabled=True,
        stop_loss_pct=1.5,
        max_holding_minutes=10,
        max_holding_minutes_hard=30,
        profit_protection_enabled=False,
        trailing_enabled=False,
        effective_profit_floor_decimal=0.0035,
    )
    defaults.update(overrides)
    return PaperPerpExitConfig(**defaults)


def test_max_hold_flat_exit_near_breakeven():
    """Above the fee floor at the soft cap → flat exit at breakeven."""
    cfg = _phase3_exit_cfg()
    entry = datetime.utcnow() - timedelta(minutes=12)
    trade = {
        "entry_price": 100.0,
        "position_side": "long",
        "entry_time": entry.isoformat(),
        "metadata": {},
    }
    result = evaluate_paper_perp_exit(trade, 100.05, cfg)
    assert result.exit_reason == "paper_max_holding_time_flat"


def test_max_hold_engages_salvage_when_underwater():
    """Below the fee floor at the soft cap → salvage mode flag set, no exit."""
    cfg = _phase3_exit_cfg()
    entry = datetime.utcnow() - timedelta(minutes=12)
    trade = {
        "entry_price": 100.0,
        "position_side": "long",
        "entry_time": entry.isoformat(),
        "metadata": {},
    }
    result = evaluate_paper_perp_exit(trade, 99.0, cfg)
    assert result.exit_reason is None
    assert result.metadata.get("salvage_mode") is True


def test_salvage_exits_on_breakeven_recovery():
    cfg = _phase3_exit_cfg()
    entry = datetime.utcnow() - timedelta(minutes=15)
    trade = {
        "entry_price": 100.0,
        "position_side": "long",
        "entry_time": entry.isoformat(),
        "metadata": {"salvage_mode": True},
    }
    result = evaluate_paper_perp_exit(trade, 100.10, cfg)
    assert result.exit_reason == "paper_max_holding_time_be"


def test_salvage_short_exits_on_breakeven_recovery():
    cfg = _phase3_exit_cfg()
    entry = datetime.utcnow() - timedelta(minutes=15)
    trade = {
        "entry_price": 100.0,
        "position_side": "short",
        "entry_time": entry.isoformat(),
        "metadata": {"salvage_mode": True},
    }
    result = evaluate_paper_perp_exit(trade, 99.90, cfg)
    assert result.exit_reason == "paper_max_holding_time_be"


def test_salvage_hard_cap_exits():
    cfg = _phase3_exit_cfg()
    entry = datetime.utcnow() - timedelta(minutes=40)
    trade = {
        "entry_price": 100.0,
        "position_side": "long",
        "entry_time": entry.isoformat(),
        "metadata": {"salvage_mode": True},
    }
    result = evaluate_paper_perp_exit(trade, 99.20, cfg)
    assert result.exit_reason == "paper_max_holding_time_hard"


def test_salvage_stays_open_when_still_underwater_before_hard_cap():
    cfg = _phase3_exit_cfg()
    entry = datetime.utcnow() - timedelta(minutes=20)
    trade = {
        "entry_price": 100.0,
        "position_side": "long",
        "entry_time": entry.isoformat(),
        "metadata": {"salvage_mode": True},
    }
    result = evaluate_paper_perp_exit(trade, 99.40, cfg)
    assert result.exit_reason is None
    assert result.metadata.get("salvage_mode") is True


def test_per_coin_stop_override_fires_before_fixed_stop():
    cfg = _phase3_exit_cfg(
        stop_loss_pct=1.5,
        per_coin_stop_overrides={"WLD": 1.0},
    )
    entry = datetime.utcnow() - timedelta(minutes=1)
    trade = {
        "coin": "WLD",
        "entry_price": 100.0,
        "position_side": "long",
        "entry_time": entry.isoformat(),
        "metadata": {},
    }
    result = evaluate_paper_perp_exit(trade, 98.95, cfg)
    assert result.exit_reason == "paper_stop_loss"


def test_per_coin_stop_override_does_not_fire_for_other_coins():
    cfg = _phase3_exit_cfg(
        stop_loss_pct=1.5,
        per_coin_stop_overrides={"WLD": 1.0},
    )
    entry = datetime.utcnow() - timedelta(minutes=1)
    trade = {
        "coin": "BTC",
        "entry_price": 100.0,
        "position_side": "long",
        "entry_time": entry.isoformat(),
        "metadata": {},
    }
    result = evaluate_paper_perp_exit(trade, 98.95, cfg)
    assert result.exit_reason is None


def test_atr_stop_used_when_metadata_present():
    cfg = _phase3_exit_cfg(
        stop_loss_atr_enabled=True,
        stop_loss_atr_mult=1.8,
        stop_loss_atr_min_pct=0.9,
        stop_loss_atr_max_pct=3.0,
    )
    entry = datetime.utcnow() - timedelta(minutes=1)
    trade = {
        "entry_price": 100.0,
        "position_side": "long",
        "entry_time": entry.isoformat(),
        "metadata": {"entry_atr_pct": 1.0},
    }
    result = evaluate_paper_perp_exit(trade, 98.10, cfg)
    assert result.exit_reason == "paper_stop_loss"


def test_atr_stop_falls_back_when_metadata_missing():
    cfg = _phase3_exit_cfg(
        stop_loss_pct=1.5,
        stop_loss_atr_enabled=True,
    )
    entry = datetime.utcnow() - timedelta(minutes=1)
    trade = {
        "entry_price": 100.0,
        "position_side": "long",
        "entry_time": entry.isoformat(),
        "metadata": {},
    }
    result = evaluate_paper_perp_exit(trade, 99.00, cfg)
    assert result.exit_reason is None
    result_below = evaluate_paper_perp_exit(trade, 98.40, cfg)
    assert result_below.exit_reason == "paper_stop_loss"


def test_atr_stop_clamped_to_min_pct():
    cfg = _phase3_exit_cfg(
        stop_loss_atr_enabled=True,
        stop_loss_atr_mult=1.8,
        stop_loss_atr_min_pct=1.0,
        stop_loss_atr_max_pct=3.0,
    )
    entry = datetime.utcnow() - timedelta(minutes=1)
    trade = {
        "entry_price": 100.0,
        "position_side": "long",
        "entry_time": entry.isoformat(),
        "metadata": {"entry_atr_pct": 0.1},
    }
    result_safe = evaluate_paper_perp_exit(trade, 99.50, cfg)
    assert result_safe.exit_reason is None
    result_stop = evaluate_paper_perp_exit(trade, 98.95, cfg)
    assert result_stop.exit_reason == "paper_stop_loss"


def test_perp_entry_atr_metadata_extracts_indicator_pct():
    md = perp_entry_atr_metadata(
        {"details": {"indicators": {"atr_pct": 1.4}}},
        entry_price=100.0,
    )
    assert md.get("entry_atr_pct") == pytest.approx(1.4)


def test_perp_entry_atr_metadata_converts_absolute_value():
    md = perp_entry_atr_metadata(
        {"details": {"indicators": {"atr": 1.5}}},
        entry_price=100.0,
    )
    assert md.get("entry_atr_pct") == pytest.approx(1.5)


def test_perp_entry_atr_metadata_normalizes_decimal_pct():
    md = perp_entry_atr_metadata(
        {"details": {"indicators": {"atr_pct": 0.012}}},
        entry_price=100.0,
    )
    assert md.get("entry_atr_pct") == pytest.approx(1.2)


def test_perp_entry_atr_metadata_returns_empty_when_missing():
    assert perp_entry_atr_metadata({"details": {}}, entry_price=100.0) == {}
    assert perp_entry_atr_metadata({}, entry_price=100.0) == {}
    assert perp_entry_atr_metadata(None, entry_price=100.0) == {}


def test_config_yaml_carries_phase3_overrides():
    cfg = paper_perp_exit_config_from_yaml(
        {
            "use_spot_exit_rules": True,
            "max_holding_minutes": 240,
            "max_holding_minutes_hard": 360,
            "stop_loss_atr": {
                "enabled": True,
                "mult": 1.8,
                "min_pct": 0.9,
                "max_pct": 3.0,
            },
            "per_coin_stop_overrides": {"WLD": 1.2, "ondo": 1.2},
        },
        {
            "stop_loss_percentage": 0.015,
            "trailing_stop": {"enabled": True, "activation_threshold": 0.0075},
            "profit_protection": {"enabled": True, "activation_threshold": 0.005},
        },
    )
    assert cfg.max_holding_minutes_hard == 360
    assert cfg.stop_loss_atr_enabled is True
    assert cfg.stop_loss_atr_mult == pytest.approx(1.8)
    assert cfg.per_coin_stop_overrides == {"WLD": 1.2, "ONDO": 1.2}
