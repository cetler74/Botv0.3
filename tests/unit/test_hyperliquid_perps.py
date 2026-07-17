import asyncio
import os
import sys
import time
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
    adaptive_blocked_regime_side_exit_reason,
    apply_hyperliquid_adaptive_pnl_control,
    build_hyperliquid_adaptive_pnl_control,
    calculate_perp_pnl,
    disabled_strategy_side_exit_reason,
    eligible_shadow_strategy_signals,
    evaluate_paper_perp_exit,
    hyperliquid_coin_entry_block,
    hyperliquid_coin_side_entry_block,
    hyperliquid_adaptive_entry_sizing_multiplier,
    hyperliquid_min_edge_gate,
    hyperliquid_daily_profit_target_halt,
    hyperliquid_coin_strategy_entry_deny,
    hyperliquid_reentry_cooldown_check,
    hyperliquid_regime_direction_gate,
    hyperliquid_shadow_promotion_requirement,
    hyperliquid_signal_prefetch_health,
    hyperliquid_signal_prefetch_settings,
    fetch_hyperliquid_entry_signal_payload,
    prefetch_hyperliquid_entry_signals,
    hyperliquid_standalone_entry_gate,
    hyperliquid_strategy_pnl_multiplier,
    hyperliquid_strategy_side_performance,
    hyperliquid_strategy_side_entry_block,
    hyperliquid_strategy_coin_loss_streak_entry_block,
    hyperliquid_strategy_pair_stop_cooldown_block,
    hyperliquid_strategy_open_position_limit_block,
    hyperliquid_trend_chase_gate,
    merge_active_trades_with_paper_perps,
    is_block_window,
    is_block_window_strategy_exempt,
    is_caution_window,
    paper_perp_exit_config_from_yaml,
    paper_perp_position_size_multiplier,
    perp_entry_atr_metadata,
    perp_lane_notional_multiplier,
    perp_side_fee,
    pair_to_hyperliquid_coin,
    pnl_percentage,
    position_sides_from_signal,
    portfolio_control_exit_reason,
    promoted_cohort_selection_boost,
    executable_size_requalification_passes,
    select_mirrored_signal,
    setup_risk_metadata_from_signal,
    ta_evidence_from_signal,
    sma_reclaim_bull_flag_specialist_gate,
    supply_demand_3step_specialist_gate,
    dual_sma_daytrade_specialist_gate,
    specialist_entry_gate,
    strategy_max_notional,
    strategy_min_size_multiplier,
    strategy_risk_per_trade_pct,
    hyperliquid_risk_based_notional,
    dynamic_min_notional,
    adaptive_perp_leverage,
    should_close_paper_perp,
    encode_setup_risk_entry_reason,
    parse_setup_risk_from_entry_reason,
    setup_risk_from_trade_metadata,
)


def test_ta_evidence_from_signal_is_bounded_and_finite():
    evidence = ta_evidence_from_signal(
        {
            "details": {
                "ta_evidence": {
                    "bar_closed": True,
                    "timeframes": ["1h", "15m", "5m"],
                    "bar_times": {"1h": "2026-07-14T10:00:00+00:00"},
                    "inputs": {
                        "rsi": 48.0,
                        "invalid": float("nan"),
                        **{f"v{index}": float(index) for index in range(20)},
                    },
                }
            }
        }
    )

    assert evidence["bar_closed"] is True
    assert evidence["timeframes"] == ["1h", "15m"]
    assert "invalid" not in evidence["inputs"]
    assert len(evidence["inputs"]) == 16


def test_active_trade_rows_include_db_backed_paper_perps():
    spot_rows = [
        {
            "trade_id": "spot-1",
            "exchange": "binance",
            "pair": "ETH/USDC",
            "status": "OPEN",
        }
    ]
    paper_perps = [
        {
            "trade_id": "perp-1",
            "coin": "SOL",
            "source_strategy": "supply_demand_3step",
            "position_side": "short",
            "status": "OPEN",
        }
    ]

    rows = merge_active_trades_with_paper_perps(spot_rows, paper_perps)

    assert len(rows) == 2
    perp = rows[1]
    assert perp["trade_id"] == "perp-1"
    assert perp["exchange"] == "hyperliquid"
    assert perp["pair"] == "SOL/USD-PERP"
    assert perp["asset_class"] == "perp"
    assert perp["source"] == "hyperliquid_paper_perp"


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


def test_shadow_trade_bypasses_executable_portfolio_control_exits():
    trade = {
        "source_strategy": "orb_5m_scalp",
        "position_side": "long",
        "metadata": {
            "shadow_trade": True,
            "market_regime": "sideways",
        },
    }
    root_config = {
        "strategies_hyperliquid": {
            "orb_5m_scalp": {"enabled": False},
        }
    }
    hl_cfg = {
        "_adaptive_pnl_control": {
            "decisions": [
                {
                    "action": "block",
                    "targetType": "regime_side",
                    "target": "sideways",
                    "side": "long",
                }
            ]
        }
    }

    assert portfolio_control_exit_reason(trade, root_config, hl_cfg) is None


def test_executable_trade_still_receives_disabled_strategy_exit():
    trade = {
        "source_strategy": "orb_5m_scalp",
        "position_side": "long",
        "metadata": {"market_regime": "sideways"},
    }
    root_config = {
        "strategies_hyperliquid": {
            "orb_5m_scalp": {"enabled": False},
        }
    }

    assert portfolio_control_exit_reason(trade, root_config, {}) == (
        "paper_disabled_strategy_orb_5m_scalp"
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


def test_disabled_strategy_side_exit_reason_closes_legacy_disabled_short():
    cfg = {
        "strategies_hyperliquid": {
            "vwma_hull": {
                "enabled": True,
                "parameters": {"allow_long": True, "allow_short": False},
            }
        }
    }

    assert disabled_strategy_side_exit_reason(
        {"source_strategy": "vwma_hull", "position_side": "short"}, cfg
    ) == "paper_disabled_side_vwma_hull_short"
    assert disabled_strategy_side_exit_reason(
        {"source_strategy": "vwma_hull", "position_side": "long"}, cfg
    ) is None


def test_adaptive_blocked_regime_side_exit_reason_closes_matching_open_position():
    trade = {
        "source_strategy": "rsi_stoch_reversal_5m",
        "position_side": "short",
        "metadata": {"market_regime": "reversal_zone"},
    }
    cfg = {
        "_adaptive_pnl_control": {
            "decisions": [
                {
                    "type": "block_recent_regime_side",
                    "action": "block",
                    "targetType": "regime_side",
                    "target": "reversal_zone",
                    "side": "short",
                }
            ]
        }
    }

    assert adaptive_blocked_regime_side_exit_reason(
        trade, cfg
    ) == "paper_block_recent_regime_side_reversal_zone_short"
    assert adaptive_blocked_regime_side_exit_reason(
        {**trade, "position_side": "long"}, cfg
    ) is None


def test_hyperliquid_perps_use_centralized_exit_rules():
    config_path = Path(ROOT) / "config" / "config.yaml"
    cfg = yaml.safe_load(config_path.read_text())
    hl_cfg = cfg["trading"]["hyperliquid_perps"]
    assert hl_cfg["use_strategy_exits"] is False
    assert hl_cfg["use_spot_exit_rules"] is True
    assert hl_cfg["fixed_stop_loss_enabled"] is True
    assert hl_cfg["profit_protection_fee_buffer"] == pytest.approx(0.0015)
    assert hl_cfg["max_margin_per_trade"] == pytest.approx(250.0)
    assert hl_cfg["max_notional_per_trade"] == pytest.approx(500.0)
    assert hl_cfg["max_open_positions"] == 15
    assert hl_cfg["strategy_open_position_limits"]["ema50_breakout_pullback"] == 4
    assert hl_cfg["strategy_open_position_limits"]["supply_demand_3step"] == 2
    assert hl_cfg["shadow_cohort_promotion"]["require_promotion_for"] == [
        {
            "strategy": "supply_demand_3step",
            "side": "long",
            "regimes": ["trending_up", "breakout", "high_volatility"],
        },
        {
            "strategy": "supertrend",
            "side": "short",
            "regimes": ["trending_down", "high_volatility"],
        },
        {
            "strategy": "supply_demand_3step",
            "side": "short",
            "regimes": ["trending_down", "high_volatility"],
        },
        {
            "strategy": "rsi_stoch_reversal_15m",
            "side": "long",
            "regimes": [
                "reversal_zone",
                "sideways",
                "breakout",
            ],
        },
        {
            "strategy": "rsi_stoch_reversal_15m",
            "side": "short",
            "regimes": ["trending_up"],
        },
    ]
    assert "sideways" in cfg["strategies_hyperliquid"]["supply_demand_3step"]["parameters"]["blocked_regimes"]
    assert hl_cfg["strategy_regime_side_blocks"]["supply_demand_3step"]["sideways"] == [
        "long",
        "short",
    ]
    assert hl_cfg["dollar_loss_cap"]["strategy_hard_loss_pct"]["supply_demand_3step"] == pytest.approx(0.40)
    assert hl_cfg["dollar_loss_cap"]["strategy_max_loss_usd"]["supply_demand_3step"] == pytest.approx(3.0)
    assert hl_cfg["strategy_coin_loss_streak_cooldowns"]["supply_demand_3step"]["consecutive_losses"] == 1
    assert hl_cfg["min_edge_gate"]["min_edge_pct"] == pytest.approx(0.65)
    assert hl_cfg["daily_profit_target"]["target_usd"] == pytest.approx(20.0)
    assert hl_cfg["shadow_cohort_promotion"]["use_episode_metrics"] is True
    assert hl_cfg["shadow_cohort_promotion"]["min_episodes"] == 8
    assert hl_cfg["cross_strategy_selection_bias"] == {}
    assert hl_cfg["lane_notional_multipliers"] == {}
    assert hl_cfg["consensus_executable_denylist"] == [
        "rsi_stoch_reversal_15m",
        "rsi_stoch_reversal_5m",
        "rsi_stoch_reversal_1m",
    ]
    assert hl_cfg["shadow_strategy_evaluation"][
        "single_open_per_strategy_coin_side"
    ] is True
    assert (
        cfg["strategies_hyperliquid"]["ema50_breakout_pullback"]["parameters"][
            "allow_short"
        ]
        is False
    )
    assert cfg["trading"]["standalone_entry_quality"]["supply_demand_3step"][
        "min_confidence"
    ] == pytest.approx(0.74)
    assert cfg["trading"]["standalone_entry_quality"]["arc_daytrade"][
        "min_confidence"
    ] == pytest.approx(0.74)


def test_perp_side_fee():
    assert perp_side_fee(50.0, 0.001) == pytest.approx(0.05)
    assert perp_side_fee(0.0, 0.001) == 0.0


def test_perp_lane_notional_multiplier_scopes_probation_to_exact_lane():
    cfg = {
        "lane_notional_multipliers": {
            "rsi_stoch_reversal_5m": {"high_volatility": {"long": 0.35}}
        }
    }
    assert perp_lane_notional_multiplier(
        "rsi_stoch_reversal_5m", "long", "high_volatility", cfg
    ) == pytest.approx(0.35)
    assert perp_lane_notional_multiplier(
        "rsi_stoch_reversal_5m", "short", "high_volatility", cfg
    ) == pytest.approx(1.0)
    assert perp_lane_notional_multiplier(
        "supply_demand_3step", "short", "sideways", cfg
    ) == pytest.approx(1.0)


def test_hyperliquid_strategy_open_position_limit_block_counts_pending():
    open_trades = [
        {"source_strategy": "ema50_breakout_pullback", "coin": "BTC"},
        {"source_strategy": "ema50_breakout_pullback", "coin": "ETH"},
        {"source_strategy": "rsi_stoch_reversal_5m", "coin": "SOL"},
    ]
    cfg = {"strategy_open_position_limits": {"ema50_breakout_pullback": 3}}

    allowed = hyperliquid_strategy_open_position_limit_block(
        "ema50_breakout_pullback",
        open_trades,
        cfg,
    )
    assert allowed["entryBlocked"] is False
    assert allowed["openCount"] == 2

    blocked = hyperliquid_strategy_open_position_limit_block(
        "ema50_breakout_pullback",
        open_trades,
        cfg,
        pending_open_count=1,
    )
    assert blocked["entryBlocked"] is True
    assert blocked["entryBlockReason"] == "strategy_open_position_limit"
    assert blocked["openCount"] == 3


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


def test_select_mirrored_signal_does_not_use_generic_individual_when_consensus_hold():
    payload = {
        "consensus": {"signal": "hold", "confidence": 0.2, "agreement": 40},
        "strategies": {
            "weak_long": {"signal": "long", "confidence": 0.5, "strength": 0.6},
            "strong_short": {"signal": "short", "confidence": 0.8, "strength": 0.7},
        },
    }
    selected = select_mirrored_signal(payload)
    assert selected is None


def test_select_mirrored_signal_rsi_stoch_competes_as_standalone_lane():
    payload = {
        "consensus": {"signal": "long", "confidence": 0.8, "agreement": 70},
        "strategies": {
            "macd_momentum": {"signal": "long", "confidence": 0.8, "strength": 0.7},
            "rsi_stoch_reversal_15m": {
                "signal": "short",
                "confidence": 0.72,
                "strength": 0.7,
            },
        },
    }

    selected = select_mirrored_signal(payload)

    assert selected["signal"] == "short"
    assert selected["strategy"] == "rsi_stoch_reversal_15m"


def test_select_mirrored_signal_ranks_all_standalone_candidates_by_quality():
    payload = {
        "consensus": {"signal": "long", "confidence": 0.1, "agreement": 10},
        "strategies": {
            "rsi_stoch_reversal_15m": {
                "signal": "long",
                "confidence": 0.70,
                "strength": 0.65,
            },
            "orb_5m_scalp": {
                "signal": "long",
                "confidence": 0.78,
                "strength": 0.75,
                "state": {"indicators": {"expected_move_pct": 1.2}},
            },
        },
    }

    selected = select_mirrored_signal(payload)

    assert selected["strategy"] == "rsi_stoch_reversal_15m"
    assert selected["selection_score"] > 0


def test_select_mirrored_signal_skips_candidate_that_will_fail_edge_gate():
    payload = {
        "consensus": {"signal": "long", "confidence": 0.1, "agreement": 10},
        "strategies": {
            "vwma_hull": {
                "signal": "long",
                "confidence": 0.90,
                "strength": 0.80,
                "state": {"indicators": {"expected_move_pct": 0.50}},
            },
            "orb_5m_scalp": {
                "signal": "long",
                "confidence": 0.76,
                "strength": 0.70,
                "state": {
                    "indicators": {
                        "expected_move_pct": 1.0,
                        "reward_risk": 2.0,
                        "breakout_valid": True,
                        "retest_valid": True,
                        "session_state": "signal",
                        "setup": "orb_5m_scalp",
                    }
                },
            },
        },
    }
    cfg = {
        "min_edge_gate": {
            "enabled": True,
            "min_edge_pct": 0.80,
            "require_expected_move": True,
        }
    }

    selected = select_mirrored_signal(payload, cfg)

    assert selected["strategy"] == "orb_5m_scalp"


def test_select_mirrored_signal_can_execute_fee_aware_supply_demand_setup():
    payload = {
        "consensus": {"signal": "hold", "confidence": 0.1, "agreement": 10},
        "strategies": {
            "rsi_stoch_reversal_5m": {
                "signal": "long",
                "confidence": 0.72,
                "strength": 0.70,
                "state": {"indicators": {"expected_move_pct": 0.90}},
            },
            "supply_demand_3step": {
                "signal": "long",
                "confidence": 0.74,
                "strength": 0.72,
                "state": {
                    "indicators": {
                        "setup": "supply_demand_3step",
                        "step1_pass": True,
                        "step2_pass": True,
                        "step3_pass": True,
                        "reward_risk": 4.0,
                        "expected_move_pct": 1.40,
                    }
                },
            },
        },
    }
    cfg = {
        "min_edge_gate": {
            "enabled": True,
            "min_edge_pct": 0.80,
            "require_expected_move": True,
        },
        "specialist_strategy_gates": {
            "supply_demand_3step": {
                "enabled": True,
                "min_confidence": 0.74,
                "min_strength": 0.65,
                "min_reward_risk": 2.5,
                "size_multiplier": 0.40,
            }
        },
    }

    selected = select_mirrored_signal(payload, cfg)

    assert selected["strategy"] == "supply_demand_3step"
    assert selected["expected_move_pct"] == pytest.approx(1.40)
    assert hyperliquid_min_edge_gate(selected, cfg)["blocked"] is False


def test_shadow_strategy_signals_returns_every_independently_eligible_strategy():
    payload = {
        "strategies": {
            "macd_momentum": {
                "signal": "long",
                "confidence": 0.75,
                "strength": 0.70,
                "state": {"indicators": {"expected_move_pct": 1.1}},
            },
            "rsi_stoch_reversal_5m": {
                "signal": "long",
                "confidence": 0.74,
                "strength": 0.70,
                "state": {"indicators": {"expected_move_pct": 1.0}},
            },
            "supertrend": {
                "signal": "long",
                "confidence": 0.90,
                "strength": 0.80,
                "state": {"indicators": {"expected_move_pct": 0.4}},
            },
            "arc_daytrade": {"signal": "hold", "confidence": 0.0, "strength": 0.0},
        }
    }
    cfg = {
        "shadow_strategy_evaluation": {"enabled": True},
        "min_edge_gate": {
            "enabled": True,
            "min_edge_pct": 0.8,
            "require_expected_move": True,
        },
    }

    eligible = eligible_shadow_strategy_signals(payload, cfg)

    assert {row["strategy"] for row in eligible} == {
        "supertrend",
        "macd_momentum",
        "rsi_stoch_reversal_5m",
    }
    low_edge = next(row for row in eligible if row["strategy"] == "supertrend")
    assert low_edge["shadow_edge_passed"] is False
    assert low_edge["shadow_gate"] == "engine"
    assert low_edge["shadow_gate_reason"] == "engine_directional_signal"


def test_shadow_strategy_signals_disabled_by_default():
    payload = {
        "strategies": {
            "macd_momentum": {
                "signal": "long",
                "confidence": 0.9,
                "strength": 0.8,
            }
        }
    }
    assert eligible_shadow_strategy_signals(payload, {}) == []


def test_shadow_strategy_signals_keep_multiple_specialists_independently():
    payload = {
        "strategies": {
            "supply_demand_3step": {
                "signal": "long",
                "confidence": 0.78,
                "strength": 0.72,
                "state": {
                    "indicators": {
                        "setup": "supply_demand_3step",
                        "step1_pass": True,
                        "step2_pass": True,
                        "step3_pass": True,
                        "reward_risk": 2.8,
                    }
                },
            },
            "dual_sma_daytrade": {
                "signal": "short",
                "confidence": 0.77,
                "strength": 0.70,
                "state": {
                    "indicators": {
                        "setup": "dual_sma_daytrade",
                        "daily_pass": True,
                        "confirm_15m_pass": True,
                        "entry_5m_pass": True,
                        "reward_risk": 2.0,
                    }
                },
            },
        }
    }
    eligible = eligible_shadow_strategy_signals(
        payload,
        {"shadow_strategy_evaluation": {"enabled": True}},
    )

    assert {(row["strategy"], row["signal"]) for row in eligible} == {
        ("supply_demand_3step", "long"),
        ("dual_sma_daytrade", "short"),
    }
    assert all(row["shadow_gate"] == "specialist" for row in eligible)


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
                "size_multiplier": 1.0,
            }
        }
    }

    gate = sma_reclaim_bull_flag_specialist_gate(signal, cfg)

    assert gate["isSpecialist"] is True
    assert gate["allowed"] is True
    assert gate["bypassConsensus"] is True
    assert gate["sizeMultiplier"] == pytest.approx(1.0)


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


def test_setup_risk_metadata_from_signal_extracts_stop_and_target():
    signal = {
        "strategy": "sma_reclaim_bull_flag",
        "details": {
            "state": {
                "indicators": {
                    "setup": "sma_reclaim_bull_flag",
                    "entry_price": 100.0,
                    "stop_hint": 98.0,
                    "target_hint": 104.0,
                    "stop_pct": 0.02,
                    "reward_risk": 2.0,
                    "breakeven_trigger_swing_high": 101.5,
                }
            }
        },
    }
    meta = setup_risk_metadata_from_signal(signal)
    assert meta["stop_pct"] == pytest.approx(2.0)
    assert meta["target_pct"] == pytest.approx(4.0)


def test_setup_risk_metadata_prefers_hints_over_decimal_target_pct():
    """EMA50-style engines publish decimal target_pct; exits must use hint geometry."""
    signal = {
        "strategy": "ema50_breakout_pullback",
        "details": {
            "state": {
                "indicators": {
                    "setup": "ema50_breakout_pullback",
                    "entry_price": 392.24,
                    "stop_hint": 398.12,
                    "target_hint": 368.58,
                    "stop_pct": 0.015,
                    "target_pct": 0.0603,
                    "reward_risk": 2.0,
                }
            }
        },
    }
    meta = setup_risk_metadata_from_signal(signal)
    assert meta["stop_pct"] == pytest.approx(1.5, rel=0.01)
    assert meta["target_pct"] == pytest.approx(6.03, rel=0.01)


def test_paper_perp_exit_ema50_short_waits_for_real_setup_target():
    cfg = PaperPerpExitConfig(
        use_setup_stops=True,
        use_setup_targets=True,
        fixed_stop_loss_enabled=True,
        stop_loss_pct=1.5,
        max_holding_minutes=2880,
    )
    trade = {
        "entry_price": 392.24,
        "position_side": "short",
        "source_strategy": "ema50_breakout_pullback",
        "entry_time": datetime.utcnow().isoformat(),
        "metadata": {
            "setup_risk": {
                "entry_price": 392.24,
                "stop_hint": 398.12,
                "target_hint": 368.58,
                "stop_pct": 0.015,
                "target_pct": 0.0603,
            }
        },
    }
    # +0.06% favorable move for a short — must NOT hit a 6% target.
    tiny_win = evaluate_paper_perp_exit(trade, 392.00, cfg)
    assert tiny_win.exit_reason is None
    # ~6.1% favorable move should trigger the real 2R target.
    full_target = evaluate_paper_perp_exit(trade, 368.50, cfg)
    assert full_target.exit_reason == "paper_setup_target@6.05%"


def test_strategy_priority_sizing_overrides():
    hl_cfg = {
        "risk_based_sizing": {"enabled": True, "risk_per_trade_pct": 0.0075},
        "strategy_risk_overrides": {"sma_reclaim_bull_flag": {"risk_per_trade_pct": 0.012}},
        "strategy_notional_overrides": {"sma_reclaim_bull_flag": {"max_notional_per_trade": 300.0}},
        "max_notional_per_trade": 200.0,
    }
    assert strategy_risk_per_trade_pct("sma_reclaim_bull_flag", hl_cfg) == pytest.approx(0.012)
    assert strategy_risk_per_trade_pct("rsi_stoch_reversal_5m", hl_cfg) == pytest.approx(0.0075)
    assert strategy_max_notional("sma_reclaim_bull_flag", hl_cfg) == pytest.approx(300.0)
    assert strategy_max_notional("rsi_stoch_reversal_5m", hl_cfg) == pytest.approx(200.0)
    trading_cfg = {
        "strategy_sizing_tiers": {
            "ordered": ["sma_reclaim_bull_flag"],
            "multipliers": {"sma_reclaim_bull_flag": 1.0},
            "sma_reclaim_bull_flag_min_multiplier": 1.0,
        },
        "adaptive_position_sizing": {
            "strategy_min_multipliers": {"sma_reclaim_bull_flag": 0.85},
        },
    }
    assert strategy_min_size_multiplier("sma_reclaim_bull_flag", trading_cfg) == pytest.approx(1.0)
    sma_notional = hyperliquid_risk_based_notional(
        10000.0,
        2.0,
        hl_cfg,
        size_multiplier=1.0,
        strategy="sma_reclaim_bull_flag",
    )
    rsi_notional = hyperliquid_risk_based_notional(
        10000.0,
        2.0,
        hl_cfg,
        size_multiplier=1.0,
        strategy="rsi_stoch_reversal_5m",
    )
    assert sma_notional > rsi_notional


def test_paper_perp_exit_uses_setup_stop_and_target():
    cfg = PaperPerpExitConfig(
        use_setup_stops=True,
        use_setup_targets=True,
        fixed_stop_loss_enabled=True,
        stop_loss_pct=1.5,
        max_holding_minutes=240,
    )
    trade = {
        "entry_price": 100.0,
        "position_side": "long",
        "source_strategy": "sma_reclaim_bull_flag",
        "entry_time": datetime.utcnow().isoformat(),
        "metadata": {
            "setup_risk": {
                "stop_pct": 1.0,
                "target_pct": 3.0,
                "entry_price": 100.0,
            }
        },
    }
    stop_result = evaluate_paper_perp_exit(trade, 98.9, cfg)
    assert stop_result.exit_reason == "paper_stop_loss"
    target_result = evaluate_paper_perp_exit(trade, 103.1, cfg)
    assert target_result.exit_reason == "paper_setup_target@3.10%"


def test_setup_risk_entry_reason_roundtrip():
    setup = {"stop_pct": 2.0, "target_hint": 105.0}
    encoded = encode_setup_risk_entry_reason("Queue-based signal", setup)
    parsed = parse_setup_risk_from_entry_reason(encoded)
    assert parsed["stop_pct"] == 2.0
    trade = {"entry_reason": encoded, "metadata": {}}
    assert setup_risk_from_trade_metadata(trade)["stop_pct"] == 2.0


def test_paper_perp_exit_profile_applies_to_sma_reclaim_bull_flag():
    cfg = paper_perp_exit_config_from_yaml(
        {
            "exit_profiles": {
                "sma_reclaim_bull_flag": {
                    "strategies": ["sma_reclaim_bull_flag"],
                    "use_setup_stops": True,
                    "use_setup_targets": True,
                    "breakeven_on_swing_high": True,
                    "partial_profit_pct": 0.5,
                    "max_holding_minutes": 240,
                }
            }
        },
        {},
        strategy_name="sma_reclaim_bull_flag",
    )
    assert cfg.use_setup_stops is True
    assert cfg.use_setup_targets is True
    assert cfg.breakeven_on_swing_high is True
    assert cfg.partial_profit_pct == pytest.approx(0.5)


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


def test_rsi_stoch_standalone_bypasses_low_consensus_agreement():
    """XMR-style case: rsi_stoch long at 0.72 conf must not require 50% global agreement."""
    signal = {
        "strategy": "rsi_stoch_reversal_5m",
        "signal": "long",
        "confidence": 0.72,
        "strength": 0.70,
        "consensus_agreement": 7.7,
        "consensus_confidence": 0.06,
    }
    cfg = {
        "standalone_strategy_gates": {
            "global": {"enabled": True},
            "rsi_stoch_reversal_5m": {
                "enabled": True,
                "min_confidence": 0.70,
                "min_strength": 0.65,
            },
        }
    }

    gate = hyperliquid_standalone_entry_gate(signal, cfg)

    assert gate["isStandalone"] is True
    assert gate["allowed"] is True
    assert gate["bypassConsensus"] is True
    assert gate["family"] == "reversal_reclaim"


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


# ---------------------------------------------------------------------------
# Adaptive paper-perp PnL control
# ---------------------------------------------------------------------------


def _adaptive_cfg(**overrides):
    cfg = {
        "enabled": True,
        "lookback_hours": 168,
        "min_reduce_trades": 3,
        "min_block_trades": 3,
        "min_scale_trades": 3,
        "min_recent_block_trades": 3,
        "recent_release_hold_hours": 12,
        "probation_size_multiplier": 0.35,
        "scale_up_multiplier": 1.25,
        "min_profit_factor_for_scale": 1.25,
        "min_net_edge_for_scale": 0.0015,
        "min_gross_edge_for_scale": 0.0040,
        "max_fee_drag_for_scale": 0.60,
    }
    cfg.update(overrides)
    return {"adaptive_pnl_control": cfg}


def _adaptive_trade(
    *,
    strategy="rsi_stoch_reversal_1m",
    side="long",
    regime="reversal_zone",
    pnl=-1.0,
    fees=0.1,
    funding=0.0,
    notional=100.0,
    exit_reason="paper_stop_loss",
    hours_ago=1,
):
    return {
        "status": "CLOSED",
        "source_strategy": strategy,
        "position_side": side,
        "realized_pnl": pnl,
        "fees": fees,
        "funding": funding,
        "notional_size": notional,
        "exit_reason": exit_reason,
        "exit_time": (datetime.utcnow() - timedelta(hours=hours_ago)).isoformat() + "Z",
        "metadata": {"market_regime": regime},
    }


def test_adaptive_pnl_control_reduces_bad_strategy_side_and_releases_when_improved():
    bad_trades = [
        _adaptive_trade(strategy="rsi_stoch_reversal_1m", side="long", pnl=-1.0, fees=0.1)
        for _ in range(3)
    ]
    control = build_hyperliquid_adaptive_pnl_control(bad_trades, _adaptive_cfg())

    assert control["entrySizing"]["rsi_stoch_reversal_1m:long"] == pytest.approx(0.35)
    decision = control["decisions"][0]
    assert decision["type"] == "reduce_strategy_side"
    assert decision["configPath"].endswith("entrySizing.rsi_stoch_reversal_1m:long")
    assert "underperforming" in decision["situation"]

    improved_trades = [
        _adaptive_trade(strategy="rsi_stoch_reversal_1m", side="long", pnl=1.0, fees=0.05, notional=100.0)
        for _ in range(3)
    ]
    improved = build_hyperliquid_adaptive_pnl_control(improved_trades, _adaptive_cfg())

    assert improved["entrySizing"]["rsi_stoch_reversal_1m:long"] == pytest.approx(1.25)
    assert all(d["type"] != "reduce_strategy_side" for d in improved["decisions"])


def test_adaptive_pnl_control_blocks_bad_regime_side_and_unblocks_when_improved():
    bad_trades = [
        _adaptive_trade(strategy="vwma_hull", side="short", regime="high_volatility", pnl=-1.0, fees=0.1)
        for _ in range(3)
    ]
    control = build_hyperliquid_adaptive_pnl_control(bad_trades, _adaptive_cfg())

    assert control["blockedRegimeSides"]["high_volatility"] == ["short"]
    assert any(d["type"] == "block_regime_side" for d in control["decisions"])

    improved_trades = [
        _adaptive_trade(strategy="vwma_hull", side="short", regime="high_volatility", pnl=1.0, fees=0.05, notional=100.0)
        for _ in range(3)
    ]
    improved = build_hyperliquid_adaptive_pnl_control(improved_trades, _adaptive_cfg())

    assert improved["blockedRegimeSides"] == {}
    assert all(d["type"] != "block_regime_side" for d in improved["decisions"])


def test_adaptive_pnl_control_scales_up_fee_adjusted_winner():
    trades = [
        _adaptive_trade(strategy="rsi_stoch_reversal_5m", side="long", regime="high_volatility", pnl=1.0, fees=0.05, notional=100.0)
        for _ in range(3)
    ]
    control = build_hyperliquid_adaptive_pnl_control(trades, _adaptive_cfg())

    assert control["entrySizing"]["rsi_stoch_reversal_5m:long"] == pytest.approx(1.25)
    assert control["entrySizing"]["high_volatility:long"] == pytest.approx(1.25)
    applied = apply_hyperliquid_adaptive_pnl_control(_adaptive_cfg(), control)
    multiplier = hyperliquid_adaptive_entry_sizing_multiplier(
        {"strategy": "rsi_stoch_reversal_5m", "signal": "long"},
        "high_volatility",
        applied,
    )
    assert multiplier == pytest.approx(1.25)


def test_adaptive_pnl_control_tightens_exits_when_loss_drag_dominates():
    trades = [
        _adaptive_trade(strategy="rsi_stoch_reversal_5m", pnl=-4.0, exit_reason="paper_stop_loss")
        for _ in range(2)
    ] + [
        _adaptive_trade(strategy="rsi_stoch_reversal_5m", pnl=-3.0, exit_reason="paper_stagnant_loser_fast_fail")
        for _ in range(2)
    ] + [
        _adaptive_trade(strategy="rsi_stoch_reversal_5m", pnl=1.0, exit_reason="trailing_stop")
        for _ in range(2)
    ]
    cfg = _adaptive_cfg(min_reduce_trades=3)
    control = build_hyperliquid_adaptive_pnl_control(trades, cfg)

    profile = control["exitProfiles"]["rsi_stoch_reversal_5m"]
    assert profile["stop_loss_pct"] == pytest.approx(0.9)
    assert profile["max_holding_minutes"] == 180
    assert profile["max_holding_minutes_hard"] == 240
    assert profile["trailing_stop"]["step_percentage"] == pytest.approx(0.0015)
    assert any(d["type"] == "tighten_loss_and_trailing_exits" for d in control["decisions"])

    applied = apply_hyperliquid_adaptive_pnl_control(cfg, control)
    applied_profile = applied["exit_profiles"]["rsi_stoch_reversal_5m"]
    assert applied_profile["stagnant_loser"]["fast_fail_loss_pct"] == pytest.approx(-0.30)
    assert applied_profile["max_holding_minutes"] == 180


def test_adaptive_pnl_control_reduces_recent_deterioration_before_long_window_turns_negative():
    trades = [
        _adaptive_trade(
            strategy="rsi_stoch_reversal_5m",
            side="long",
            pnl=1.0,
            fees=0.05,
            notional=100.0,
            hours_ago=24,
        )
        for _ in range(8)
    ] + [
        _adaptive_trade(
            strategy="rsi_stoch_reversal_5m",
            side="long",
            pnl=-0.9,
            fees=0.1,
            notional=100.0,
            hours_ago=1,
        )
        for _ in range(3)
    ]
    cfg = _adaptive_cfg(
        min_reduce_trades=10,
        min_scale_trades=30,
        recent_window_hours=6,
        min_recent_reduce_trades=3,
        recent_probation_size_multiplier=0.35,
    )

    control = build_hyperliquid_adaptive_pnl_control(trades, cfg)

    assert control["entrySizing"]["rsi_stoch_reversal_5m:long"] == pytest.approx(0.35)
    assert control["recentReleaseHoldHours"] == pytest.approx(12)
    decision = next(d for d in control["decisions"] if d["type"] == "reduce_recent_strategy_side")
    assert decision["evidence"]["lookbackHours"] == 6
    assert "last 6h" in decision["situation"]


def test_adaptive_pnl_control_blocks_recent_bad_regime_side_before_long_window_turns_negative():
    trades = [
        _adaptive_trade(
            strategy="rsi_stoch_reversal_5m",
            side="short",
            regime="reversal_zone",
            pnl=1.0,
            fees=0.05,
            notional=100.0,
            hours_ago=24,
        )
        for _ in range(12)
    ] + [
        _adaptive_trade(
            strategy="rsi_stoch_reversal_5m",
            side="short",
            regime="reversal_zone",
            pnl=-1.2,
            fees=0.12,
            notional=100.0,
            hours_ago=1,
        )
        for _ in range(3)
    ]
    cfg = _adaptive_cfg(
        min_block_trades=30,
        recent_window_hours=6,
        min_recent_block_trades=3,
    )

    control = build_hyperliquid_adaptive_pnl_control(trades, cfg)

    assert control["blockedRegimeSides"]["reversal_zone"] == ["short"]
    decision = next(d for d in control["decisions"] if d["type"] == "block_recent_regime_side")
    assert decision["decisionKey"] == "block_recent_regime_side:reversal_zone:short"
    assert decision["evidence"]["lookbackHours"] == 6
    assert "last 6h" in decision["situation"]


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


def test_hyperliquid_strategy_coin_loss_streak_blocks_only_matching_strategy_coin():
    now = datetime(2026, 6, 5, 12, 0, 0)
    closed = [
        {
            "coin": "WLD",
            "source_strategy": "rsi_stoch_reversal_1m",
            "realized_pnl": -0.5,
            "exit_time": "2026-06-05T10:00:00+00:00",
        },
        {
            "coin": "WLD",
            "source_strategy": "rsi_stoch_reversal_1m",
            "realized_pnl": -0.4,
            "exit_time": "2026-06-05T09:00:00+00:00",
        },
    ]
    block = hyperliquid_strategy_coin_loss_streak_entry_block(
        "WLD", "rsi_stoch_reversal_1m", closed, now=now
    )
    assert block["entryBlocked"] is True
    assert block["consecutiveLosses"] == 2
    assert hyperliquid_strategy_coin_loss_streak_entry_block(
        "ETH", "rsi_stoch_reversal_1m", closed, now=now
    )["entryBlocked"] is False


def test_hyperliquid_strategy_pair_stop_cooldown_blocks_same_strategy_coin_side():
    now = datetime(2026, 6, 15, 12, 0, 0)
    closed = [
        {
            "coin": "MSTR",
            "source_strategy": "ema50_breakout_pullback",
            "side": "long",
            "realized_pnl": -1.75,
            "exit_reason": "paper_stop_loss@-1.10%",
            "exit_time": "2026-06-15T09:30:00+00:00",
        }
    ]
    block = hyperliquid_strategy_pair_stop_cooldown_block(
        "MSTR",
        "ema50_breakout_pullback",
        "long",
        closed,
        now=now,
        cooldown_hours=6,
    )
    assert block["entryBlocked"] is True
    assert block["entryBlockReason"] == "strategy_pair_stop_cooldown"
    assert hyperliquid_strategy_pair_stop_cooldown_block(
        "MSTR",
        "ema50_breakout_pullback",
        "short",
        closed,
        now=now,
        cooldown_hours=6,
    )["entryBlocked"] is False


def test_hyperliquid_strategy_pair_stop_cooldown_ignores_non_stop_winners():
    now = datetime(2026, 6, 15, 12, 0, 0)
    closed = [
        {
            "coin": "MSTR",
            "source_strategy": "ema50_breakout_pullback",
            "side": "long",
            "realized_pnl": 1.25,
            "exit_reason": "paper_trailing_stop@1.20%",
            "exit_time": "2026-06-15T10:00:00+00:00",
        },
        {
            "coin": "MSTR",
            "source_strategy": "ema50_breakout_pullback",
            "side": "long",
            "realized_pnl": -1.25,
            "exit_reason": "paper_max_holding_time",
            "exit_time": "2026-06-15T10:30:00+00:00",
        },
    ]
    block = hyperliquid_strategy_pair_stop_cooldown_block(
        "MSTR",
        "ema50_breakout_pullback",
        "long",
        closed,
        now=now,
        cooldown_hours=6,
    )
    assert block["entryBlocked"] is False


def test_paper_perp_exit_profile_applies_to_rsi_stoch_1m():
    cfg = paper_perp_exit_config_from_yaml(
        {
            "stop_loss_pct": 1.5,
            "max_holding_minutes": 240,
            "max_holding_minutes_hard": 360,
            "exit_profiles": {
                "rsi_1m": {
                    "strategies": ["rsi_stoch_reversal_1m"],
                    "stop_loss_pct": 0.9,
                    "max_holding_minutes": 30,
                    "max_holding_minutes_hard": 45,
                    "stagnant_loser": {"min_age_minutes": 8},
                }
            },
        },
        {"stop_loss_percentage": 0.015},
        strategy_name="rsi_stoch_reversal_1m",
    )
    assert cfg.stop_loss_pct == pytest.approx(0.9)
    assert cfg.max_holding_minutes == 30
    assert cfg.max_holding_minutes_hard == 45
    assert cfg.stagnant_loser["min_age_minutes"] == 8


def test_paper_perp_exit_profile_disables_stagnant_loser_for_arc_daytrade():
    cfg = paper_perp_exit_config_from_yaml(
        {
            "stagnant_loser_enabled": True,
            "exit_profiles": {
                "arc_daytrade": {
                    "strategies": ["arc_daytrade"],
                    "use_setup_stops": True,
                    "use_setup_targets": False,
                    "max_holding_minutes": 180,
                    "stagnant_loser_enabled": False,
                }
            },
        },
        {"stagnant_loser": {"min_age_minutes": 30}},
        strategy_name="arc_daytrade",
    )
    assert cfg.use_setup_stops is True
    assert cfg.use_setup_targets is False
    assert cfg.stagnant_loser_enabled is False


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
    assert block["entryBlockReason"] == "recent_negative_realized"
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


def test_hyperliquid_coin_side_entry_block_open_negative_long_only_blocks_long():
    block_long = hyperliquid_coin_side_entry_block(
        "WLD",
        "long",
        [{"coin": "WLD", "position_side": "long", "unrealized_pnl": -0.01}],
        [],
        now=datetime(2026, 5, 24, 12, 0, 0),
    )
    block_short = hyperliquid_coin_side_entry_block(
        "WLD",
        "short",
        [{"coin": "WLD", "position_side": "long", "unrealized_pnl": -0.01}],
        [],
        now=datetime(2026, 5, 24, 12, 0, 0),
    )

    assert block_long["entryBlocked"] is True
    assert block_long["entryBlockReason"] == "open_unrealized_negative"
    assert block_long["entryBlockSide"] == "long"
    assert block_short["entryBlocked"] is False


def test_hyperliquid_coin_side_entry_block_open_negative_short_only_blocks_short():
    block_long = hyperliquid_coin_side_entry_block(
        "WLD",
        "long",
        [{"coin": "WLD", "position_side": "short", "unrealized_pnl": -0.01}],
        [],
        now=datetime(2026, 5, 24, 12, 0, 0),
    )
    block_short = hyperliquid_coin_side_entry_block(
        "WLD",
        "short",
        [{"coin": "WLD", "position_side": "short", "unrealized_pnl": -0.01}],
        [],
        now=datetime(2026, 5, 24, 12, 0, 0),
    )

    assert block_long["entryBlocked"] is False
    assert block_short["entryBlocked"] is True
    assert block_short["entryBlockReason"] == "open_unrealized_negative"
    assert block_short["entryBlockSide"] == "short"


def test_hyperliquid_coin_side_entry_block_recent_long_loss_allows_short():
    now = datetime(2026, 5, 24, 12, 0, 0)
    closed = [
        {
            "coin": "WLD",
            "position_side": "long",
            "realized_pnl": -1.0,
            "exit_time": (now - timedelta(hours=2)).isoformat(),
        }
    ]

    block_long = hyperliquid_coin_side_entry_block(
        "WLD",
        "long",
        [],
        closed,
        now=now,
        realized_block_hours=4,
    )
    block_short = hyperliquid_coin_side_entry_block(
        "WLD",
        "short",
        [],
        closed,
        now=now,
        realized_block_hours=4,
    )

    assert block_long["entryBlocked"] is True
    assert block_long["entryBlockReason"] == "recent_negative_realized"
    assert block_long["entryBlockSide"] == "long"
    assert block_short["entryBlocked"] is False


def test_hyperliquid_coin_side_entry_block_recent_short_loss_allows_long():
    now = datetime(2026, 5, 24, 12, 0, 0)
    closed = [
        {
            "coin": "WLD",
            "position_side": "short",
            "realized_pnl": -1.0,
            "exit_time": (now - timedelta(hours=2)).isoformat(),
        }
    ]

    block_long = hyperliquid_coin_side_entry_block(
        "WLD",
        "long",
        [],
        closed,
        now=now,
        realized_block_hours=4,
    )
    block_short = hyperliquid_coin_side_entry_block(
        "WLD",
        "short",
        [],
        closed,
        now=now,
        realized_block_hours=4,
    )

    assert block_long["entryBlocked"] is False
    assert block_short["entryBlocked"] is True
    assert block_short["entryBlockReason"] == "recent_negative_realized"
    assert block_short["entryBlockSide"] == "short"


def test_hyperliquid_coin_side_entry_block_expires_after_four_hours():
    now = datetime(2026, 5, 24, 12, 0, 0)
    block = hyperliquid_coin_side_entry_block(
        "WLD",
        "long",
        [],
        [
            {
                "coin": "WLD",
                "position_side": "long",
                "realized_pnl": -1.0,
                "exit_time": (now - timedelta(hours=5)).isoformat(),
            }
        ],
        now=now,
        realized_block_hours=4,
    )

    assert block["entryBlocked"] is False


def test_hyperliquid_coin_side_entry_block_positive_pnl_does_not_block():
    now = datetime(2026, 5, 24, 12, 0, 0)
    block = hyperliquid_coin_side_entry_block(
        "WLD",
        "long",
        [{"coin": "WLD", "position_side": "long", "unrealized_pnl": 0.01}],
        [
            {
                "coin": "WLD",
                "position_side": "long",
                "realized_pnl": 1.0,
                "exit_time": (now - timedelta(hours=1)).isoformat(),
            }
        ],
        now=now,
        realized_block_hours=4,
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
# Change 2: Fee-aware profit protection / trailing defaults
# ---------------------------------------------------------------------------


def test_default_exit_config_uses_fee_floor_trailing_params():
    cfg = PaperPerpExitConfig()
    assert cfg.profit_protection_activation_decimal == pytest.approx(0.0035)
    assert cfg.trailing_activation_decimal == pytest.approx(0.0050)
    assert cfg.trailing_step_decimal == pytest.approx(0.0020)
    assert cfg.tightened_step_decimal == pytest.approx(0.0015)
    assert cfg.tighten_profit_threshold_decimal == pytest.approx(0.0050)
    assert cfg.breakeven_floor_decimal == pytest.approx(0.0035)
    assert cfg.min_trigger_distance_decimal == pytest.approx(0.0035)


def test_default_trailing_does_not_arm_below_half_percent():
    """With activation at 0.50%, a +0.49% move should NOT activate the trail."""
    trade = {
        "entry_price": 100.0,
        "position_side": "long",
        "entry_time": (datetime.utcnow() - timedelta(minutes=5)).isoformat(),
        "metadata": {},
    }
    cfg = PaperPerpExitConfig()
    result = evaluate_paper_perp_exit(trade, 100.49, cfg)
    assert result.exit_reason is None
    assert result.metadata.get("trail_stop") != "active"


def test_default_trailing_arms_at_half_percent():
    """With activation at 0.50%, a +0.50% move should activate the trail."""
    trade = {
        "entry_price": 100.0,
        "position_side": "long",
        "entry_time": (datetime.utcnow() - timedelta(minutes=5)).isoformat(),
        "metadata": {},
    }
    cfg = PaperPerpExitConfig()
    result = evaluate_paper_perp_exit(trade, 100.50, cfg)
    assert result.exit_reason is None
    assert result.metadata.get("trail_stop") == "active"


def test_active_trailing_long_fills_at_trigger_when_price_gaps_through():
    """Paper trailing protection must not realize worse than its armed trigger."""
    cfg = PaperPerpExitConfig()
    entry_price = 107.90
    size = 120.0 / entry_price
    trigger_price = 108.327265
    trade = {
        "entry_price": entry_price,
        "position_side": "long",
        "entry_time": datetime.utcnow().isoformat(),
        "fees": perp_side_fee(120.0, cfg.fee_rate_per_side),
        "metadata": {
            "highest_price": 108.49,
            "trail_stop": "active",
            "trail_stop_trigger": trigger_price,
            "profit_protection": "trailing",
        },
    }

    result = evaluate_paper_perp_exit(trade, 108.00, cfg)
    assert result.exit_reason is not None
    assert "paper_trailing_stop_trigger" in result.exit_reason
    assert result.exit_price == pytest.approx(trigger_price)

    exit_fee = perp_side_fee(result.exit_price * size, cfg.fee_rate_per_side)
    realized = calculate_perp_pnl(
        "long",
        entry_price,
        result.exit_price,
        size,
        trade["fees"] + exit_fee,
    )
    assert realized > 0


def test_profit_protection_breach_fills_at_floor_when_price_gaps_through():
    cfg = PaperPerpExitConfig()
    floor_px = 100.0 * (1.0 + cfg.breakeven_floor_decimal)
    trade = {
        "entry_price": 100.0,
        "position_side": "long",
        "entry_time": datetime.utcnow().isoformat(),
        "metadata": {
            "highest_price": 100.6,
            "profit_protection": "profit_guaranteed",
            "trail_stop_trigger": floor_px,
        },
    }

    result = evaluate_paper_perp_exit(trade, 100.10, cfg)
    assert result.exit_reason is not None
    assert "profit_protection_breach" in result.exit_reason
    assert result.exit_price == pytest.approx(floor_px)


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


def test_regime_direction_gate_honors_configured_regime_side_block():
    gate = hyperliquid_regime_direction_gate(
        "long",
        "reversal_zone",
        0.90,
        0.90,
        {"blocked_regime_sides": {"reversal_zone": ["long"]}},
    )

    assert gate["blocked"] is True
    assert gate["reason"] == "configured_regime_side_block_reversal_zone_long"


def test_regime_direction_gate_high_conviction_override():
    gate = hyperliquid_regime_direction_gate("short", "trending_up", 0.92, 0.85)
    assert gate["blocked"] is False
    assert gate["reason"] == "counter_trend_override_high_conviction"


def test_regime_direction_gate_allows_configured_counter_trend_lane():
    gate = hyperliquid_regime_direction_gate(
        "short",
        "trending_up",
        0.74,
        0.65,
        {
            "counter_trend_allowed_lanes": [
                {
                    "strategy": "arc_daytrade",
                    "side": "short",
                    "regimes": ["trending_up", "breakout"],
                }
            ]
        },
        strategy="arc_daytrade",
    )
    assert gate["blocked"] is False
    assert gate["reason"] == "counter_trend_allowed_lane"


def test_regime_direction_gate_allows_vwma_short_in_trending_down():
    gate = hyperliquid_regime_direction_gate(
        "short",
        "trending_down",
        0.90,
        0.85,
        {
            "strategy_regime_side_blocks": {
                "vwma_hull": {
                    "trending_up": ["short"],
                    "sideways": ["short"],
                }
            }
        },
        strategy="vwma_hull",
    )
    assert gate["blocked"] is False
    assert gate["reason"] == "regime_direction_ok"


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
# Phase 4 (2026-05-27): Retuned caution windows + optional block windows
# ---------------------------------------------------------------------------


def _phase4_session_cfg(**overrides):
    base = {
        "session_sizing": {
            "enabled": True,
            "caution_multiplier": 0.5,
            "caution_windows": [
                {"start_utc": 2, "end_utc": 5},
                {"start_utc": 13, "end_utc": 15},
                {"start_utc": 19, "end_utc": 22},
            ],
            "block_windows_enabled": True,
            "block_windows": [
                {"start_utc": 13, "end_utc": 14},
                {"start_utc": 21, "end_utc": 22},
            ],
        }
    }
    base["session_sizing"].update(overrides)
    return base


def test_phase4_caution_windows_cover_worst_hours():
    cfg = _phase4_session_cfg()
    for hour in (2, 3, 4, 13, 14, 19, 20, 21):
        caution, mult = is_caution_window(hour, cfg)
        assert caution is True
        assert mult == pytest.approx(0.5)


def test_phase4_caution_windows_exclude_best_hours():
    cfg = _phase4_session_cfg()
    for hour in (0, 8, 16, 23):
        caution, mult = is_caution_window(hour, cfg)
        assert caution is False
        assert mult == pytest.approx(1.0)


def test_phase4_block_windows_hard_skip_when_enabled():
    cfg = _phase4_session_cfg()
    assert is_block_window(13, cfg) is True
    assert is_block_window(21, cfg) is True
    assert is_block_window(20, cfg) is False


def test_phase4_block_windows_off_by_flag():
    cfg = _phase4_session_cfg(block_windows_enabled=False)
    assert is_block_window(13, cfg) is False
    assert is_block_window(21, cfg) is False


def test_phase4_block_windows_off_when_session_sizing_disabled():
    cfg = _phase4_session_cfg(enabled=False)
    assert is_block_window(13, cfg) is False


def test_phase4_block_window_strategy_exemption_is_explicit():
    cfg = _phase4_session_cfg(
        block_window_exempt_strategies=["rsi_stoch_reversal_5m"]
    )
    assert is_block_window(13, cfg) is True
    assert is_block_window_strategy_exempt("rsi_stoch_reversal_5m", cfg) is True
    assert is_block_window_strategy_exempt("vwma_hull", cfg) is False


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


def test_perp_trailing_override_prefers_hyperliquid_perps_section():
    cfg = paper_perp_exit_config_from_yaml(
        {
            "use_spot_exit_rules": True,
            "trailing_stop": {
                "enabled": True,
                "activation_threshold": 0.0075,
                "step_percentage": 0.0050,
                "tightened_step_percentage": 0.0030,
                "dynamic_tightening_enabled": True,
                "tighten_profit_threshold": 0.0150,
                "breakeven_floor_percentage": 0.0050,
                "min_trigger_distance_percentage": 0.0050,
            },
            "profit_protection": {"enabled": True, "activation_threshold": 0.0075},
        },
        {
            "trailing_stop": {
                "enabled": True,
                "activation_threshold": 0.0035,
                "step_percentage": 0.0025,
                "breakeven_floor_percentage": 0.0035,
            },
            "profit_protection": {"enabled": True, "activation_threshold": 0.0035},
        },
    )
    assert cfg.trailing_activation_decimal == pytest.approx(0.0075)
    assert cfg.trailing_step_decimal == pytest.approx(0.0050)
    assert cfg.breakeven_floor_decimal == pytest.approx(0.0050)
    assert cfg.profit_protection_activation_decimal == pytest.approx(0.0075)


def test_paper_perp_exit_config_accepts_strategy_name_overrides():
    cfg = paper_perp_exit_config_from_yaml(
        {
            "use_spot_exit_rules": True,
            "fee_rate_per_side": 0.001,
            "profit_protection_fee_buffer": 0.0015,
            "trailing_stop": {
                "enabled": True,
                "activation_threshold": 0.0075,
                "breakeven_floor_percentage": 0.0035,
                "min_trigger_distance_percentage": 0.0035,
            },
            "profit_protection": {"enabled": True, "activation_threshold": 0.0075},
        },
        {
            "rsi_stoch_reversal_5m_risk": {
                "profit_activation_threshold": 0.0035,
                "trailing_activation_threshold": 0.0050,
            },
        },
        strategy_name="rsi_stoch_reversal_5m",
    )

    assert cfg.profit_protection_activation_decimal == pytest.approx(0.0035)
    assert cfg.trailing_activation_decimal == pytest.approx(0.0050)


def test_stagnant_loser_fast_fail_long():
    now = datetime(2026, 5, 28, 12, 0, 0)
    cfg = PaperPerpExitConfig(
        use_spot_exit_rules=True,
        fixed_stop_loss_enabled=False,
        stagnant_loser_enabled=True,
        stagnant_loser={
            "fast_fail_min_age_minutes": 10,
            "fast_fail_peak_pct": 0.15,
            "fast_fail_loss_pct": -0.40,
        },
        profit_protection_enabled=False,
        trailing_enabled=False,
    )
    trade = {
        "entry_price": 100.0,
        "position_side": "long",
        "entry_time": (now - timedelta(minutes=15)).isoformat(),
        "metadata": {"highest_price": 100.1},
    }
    result = evaluate_paper_perp_exit(trade, 99.5, cfg, now=now)
    assert result.exit_reason is not None
    assert "paper_stagnant_loser_fast_fail" in result.exit_reason


def test_stagnant_loser_fast_fail_short():
    now = datetime(2026, 5, 28, 12, 0, 0)
    cfg = PaperPerpExitConfig(
        use_spot_exit_rules=True,
        fixed_stop_loss_enabled=False,
        stagnant_loser_enabled=True,
        stagnant_loser={
            "fast_fail_min_age_minutes": 10,
            "fast_fail_peak_pct": 0.15,
            "fast_fail_loss_pct": -0.40,
        },
        profit_protection_enabled=False,
        trailing_enabled=False,
    )
    trade = {
        "entry_price": 100.0,
        "position_side": "short",
        "entry_time": (now - timedelta(minutes=15)).isoformat(),
        "metadata": {"lowest_price": 99.9},
    }
    result = evaluate_paper_perp_exit(trade, 100.5, cfg, now=now)
    assert result.exit_reason is not None
    assert "paper_stagnant_loser_fast_fail" in result.exit_reason


def test_stagnant_loser_no_mfe_fast_fail_long():
    now = datetime(2026, 5, 28, 12, 0, 0)
    cfg = PaperPerpExitConfig(
        use_spot_exit_rules=True,
        fixed_stop_loss_enabled=False,
        stagnant_loser_enabled=True,
        stagnant_loser={
            "no_mfe_fast_fail_enabled": True,
            "no_mfe_min_age_minutes": 10,
            "no_mfe_peak_pct": 0.03,
            "no_mfe_loss_pct": -0.40,
            "fast_fail_min_age_minutes": 10,
            "fast_fail_peak_pct": 0.15,
            "fast_fail_loss_pct": -0.40,
        },
        profit_protection_enabled=False,
        trailing_enabled=False,
    )
    trade = {
        "entry_price": 100.0,
        "position_side": "long",
        "entry_time": (now - timedelta(minutes=15)).isoformat(),
        "metadata": {},
    }
    result = evaluate_paper_perp_exit(trade, 99.5, cfg, now=now)
    assert result.exit_reason is not None
    assert "paper_stagnant_loser_no_mfe_fast_fail" in result.exit_reason


def test_stagnant_loser_disabled_skips_exit():
    now = datetime(2026, 5, 28, 12, 0, 0)
    cfg = PaperPerpExitConfig(
        use_spot_exit_rules=True,
        fixed_stop_loss_enabled=False,
        stagnant_loser_enabled=False,
        stagnant_loser={
            "fast_fail_min_age_minutes": 10,
            "fast_fail_peak_pct": 0.15,
            "fast_fail_loss_pct": -0.40,
        },
        profit_protection_enabled=False,
        trailing_enabled=False,
    )
    trade = {
        "entry_price": 100.0,
        "position_side": "long",
        "entry_time": (now - timedelta(minutes=15)).isoformat(),
        "metadata": {},
    }
    result = evaluate_paper_perp_exit(trade, 99.5, cfg, now=now)
    assert result.exit_reason is None


def test_profit_protection_breach_fills_floor_below_entry_long():
    cfg = _spot_like_exit_cfg()
    trigger = 100.35
    trade = {
        "entry_price": 100.0,
        "position_side": "long",
        "entry_time": datetime.utcnow().isoformat(),
        "metadata": {
            "highest_price": 100.5,
            "profit_protection": "profit_guaranteed",
            "trail_stop_trigger": trigger,
        },
    }
    result = evaluate_paper_perp_exit(trade, 99.8, cfg)
    assert result.exit_reason is not None
    assert "profit_protection_breach" in result.exit_reason
    assert result.exit_price == pytest.approx(trigger)


def test_profit_protection_breach_fills_floor_short():
    """ENA-like: armed PP exits at the floor even if polling observes a worse price."""
    cfg = _spot_like_exit_cfg()
    trigger = 0.087331 * (1.0 - cfg.breakeven_floor_decimal)
    trade = {
        "entry_price": 0.087331,
        "position_side": "short",
        "entry_time": datetime.utcnow().isoformat(),
        "metadata": {
            "lowest_price": 0.087025,
            "profit_protection": "profit_guaranteed",
            "trail_stop_trigger": trigger,
        },
    }
    result = evaluate_paper_perp_exit(trade, 0.087135, cfg)
    assert result.exit_reason is not None
    assert "profit_protection_breach" in result.exit_reason
    assert result.exit_price == pytest.approx(trigger)


def test_profit_protection_arms_before_trailing_activation():
    cfg = paper_perp_exit_config_from_yaml(
        {
            "use_spot_exit_rules": True,
            "trailing_stop": {
                "enabled": True,
                "activation_threshold": 0.0075,
                "step_percentage": 0.0050,
                "breakeven_floor_percentage": 0.0050,
            },
            "profit_protection": {"enabled": True, "activation_threshold": 0.0035},
        },
        {},
    )
    trade = {
        "entry_price": 100.0,
        "position_side": "short",
        "entry_time": datetime.utcnow().isoformat(),
        "metadata": {},
    }
    # Peak ~0.40% — above PP threshold but below trailing activation (0.75%).
    result = evaluate_paper_perp_exit(trade, 99.60, cfg)
    assert result.metadata.get("profit_protection") == "profit_guaranteed"
    assert result.metadata.get("trail_stop") != "active"
    assert float(result.metadata.get("trail_stop_trigger") or 0) == pytest.approx(
        100.0 * (1.0 - cfg.breakeven_floor_decimal)
    )


def test_wld_long_profit_protection_arms_at_configured_threshold_before_trail():
    cfg = paper_perp_exit_config_from_yaml(
        {
            "use_spot_exit_rules": True,
            "fee_rate_per_side": 0.001,
            "profit_protection_fee_buffer": 0.0015,
            "trailing_stop": {
                "enabled": True,
                "activation_threshold": 0.0050,
                "step_percentage": 0.0020,
                "tightened_step_percentage": 0.0015,
                "dynamic_tightening_enabled": True,
                "tighten_profit_threshold": 0.0050,
                "breakeven_floor_percentage": 0.0035,
                "min_trigger_distance_percentage": 0.0035,
            },
            "profit_protection": {"enabled": True, "activation_threshold": 0.0035},
        },
        {},
    )
    trade = {
        "coin": "WLD",
        "entry_price": 0.49179,
        "position_side": "long",
        "entry_time": datetime.utcnow().isoformat(),
        "metadata": {},
    }

    result = evaluate_paper_perp_exit(trade, 0.49381, cfg)

    assert result.exit_reason is None
    assert result.metadata.get("profit_protection") == "profit_guaranteed"
    assert result.metadata.get("trail_stop") != "active"
    assert result.metadata.get("trail_stop_trigger") == pytest.approx(
        0.49179 * (1.0 + cfg.breakeven_floor_decimal)
    )


def test_profit_protection_breach_exits_at_floor_short():
    cfg = _spot_like_exit_cfg()
    floor_px = 100.0 * (1.0 - cfg.breakeven_floor_decimal)
    trade = {
        "entry_price": 100.0,
        "position_side": "short",
        "entry_time": datetime.utcnow().isoformat(),
        "metadata": {
            "lowest_price": 99.0,
            "profit_protection": "profit_guaranteed",
            "trail_stop_trigger": floor_px,
        },
    }
    result = evaluate_paper_perp_exit(trade, floor_px, cfg)
    assert result.exit_reason is not None
    assert "profit_protection_breach" in result.exit_reason


def test_config_yaml_carries_perp_trailing_and_stagnant_flags():
    config_path = Path(ROOT) / "config" / "config.yaml"
    cfg = yaml.safe_load(config_path.read_text())
    hl = cfg["trading"]["hyperliquid_perps"]
    assert hl["profit_protection"]["activation_threshold"] == pytest.approx(0.0100)
    assert hl["trailing_stop"]["activation_threshold"] == pytest.approx(0.0100)
    assert hl["trailing_stop"]["breakeven_floor_percentage"] == pytest.approx(0.0080)
    assert hl["stagnant_loser_enabled"] is True
    assert hl["structural_exits"]["enabled"] is True
    assert "vwma_hull" in hl["structural_exits"]["strategies"]


# ---------------------------------------------------------------------------
# Phase 5 (2026-05-27): Trend-chase guard
# ---------------------------------------------------------------------------


def _chase_signal(side, *, rsi=None, pullback=None, strategy="vwma_hull"):
    indicators = {}
    if rsi is not None:
        indicators["rsi_14"] = rsi
    if pullback is not None:
        indicators["pullback_depth_pct"] = pullback
    return {
        "strategy": strategy,
        "signal": side,
        "details": {"indicators": indicators},
    }


def test_trend_chase_inactive_for_counter_trend():
    sig = _chase_signal("short", rsi=80)
    result = hyperliquid_trend_chase_gate(sig, "trending_up")
    assert result["blocked"] is False
    assert result["passthrough"] is True


def test_trend_chase_inactive_outside_trend_regime():
    sig = _chase_signal("long", rsi=80)
    result = hyperliquid_trend_chase_gate(sig, "sideways")
    assert result["blocked"] is False
    assert result["passthrough"] is True


def test_trend_chase_passthrough_when_indicators_missing():
    sig = _chase_signal("long")
    result = hyperliquid_trend_chase_gate(sig, "trending_up")
    assert result["blocked"] is False
    assert result["passthrough"] is True
    assert result["reason"] == "trend_chase_no_indicators"
    # Phase C (2026-05-29): unproven with-trend entries get half size.
    assert result["sizeMultiplier"] == pytest.approx(0.5)


def test_trend_chase_unproven_short_gets_half_size():
    sig = _chase_signal("short")
    result = hyperliquid_trend_chase_gate(sig, "trending_down")
    assert result["blocked"] is False
    assert result["sizeMultiplier"] == pytest.approx(0.5)


def test_trend_chase_inactive_has_no_size_penalty():
    sig = _chase_signal("long")
    result = hyperliquid_trend_chase_gate(sig, "sideways")
    assert result["blocked"] is False
    assert result.get("sizeMultiplier") is None


def test_trend_chase_blocks_long_with_overbought_rsi_no_pullback():
    sig = _chase_signal("long", rsi=72)
    result = hyperliquid_trend_chase_gate(sig, "trending_up")
    assert result["blocked"] is True
    assert "trend_chase_blocked_long_in_trending_up" in result["reason"]


def test_trend_chase_blocks_short_with_oversold_rsi_no_pullback():
    sig = _chase_signal("short", rsi=28)
    result = hyperliquid_trend_chase_gate(sig, "trending_down")
    assert result["blocked"] is True
    assert "trend_chase_blocked_short_in_trending_down" in result["reason"]


def test_trend_chase_allows_long_with_pullback_context():
    sig = _chase_signal("long", rsi=72, pullback=0.9)
    result = hyperliquid_trend_chase_gate(sig, "trending_up")
    assert result["blocked"] is False
    assert result["reason"] == "trend_chase_pass"


def test_trend_chase_allows_long_with_decimal_pullback():
    sig = _chase_signal("long", rsi=72, pullback=0.012)
    result = hyperliquid_trend_chase_gate(sig, "trending_up")
    assert result["blocked"] is False


def test_trend_chase_allows_long_with_neutral_rsi():
    sig = _chase_signal("long", rsi=55)
    result = hyperliquid_trend_chase_gate(sig, "trending_up")
    assert result["blocked"] is False


def test_trend_chase_allows_short_with_neutral_rsi():
    sig = _chase_signal("short", rsi=45)
    result = hyperliquid_trend_chase_gate(sig, "trending_down")
    assert result["blocked"] is False


def test_trend_chase_reads_state_indicators_fallback():
    sig = {
        "strategy": "supertrend",
        "signal": "long",
        "details": {"state": {"indicators": {"rsi_14": 72}}},
    }
    result = hyperliquid_trend_chase_gate(sig, "trending_up")
    assert result["blocked"] is True


# ---------------------------------------------------------------------------
# Phase 6 (2026-05-27): Fee-aware minimum-edge gate
# ---------------------------------------------------------------------------


def _edge_cfg(**overrides):
    cfg = {
        "fee_rate_per_side": 0.001,
        "min_edge_gate": {
            "enabled": True,
            "min_edge_pct": 0.40,
            "edge_multiplier": 2.0,
        },
    }
    cfg["min_edge_gate"].update(overrides)
    return cfg


def test_min_edge_gate_disabled_flag():
    cfg = _edge_cfg(enabled=False)
    result = hyperliquid_min_edge_gate({"expected_move_pct": 0.1}, cfg)
    assert result["blocked"] is False
    assert result["reason"] == "min_edge_disabled"


def test_min_edge_gate_passes_when_no_data():
    cfg = _edge_cfg()
    result = hyperliquid_min_edge_gate({"signal": "long"}, cfg)
    assert result["blocked"] is False
    assert result["reason"] == "min_edge_no_data"


def test_min_edge_gate_blocks_missing_expected_move_when_required():
    cfg = _edge_cfg(require_expected_move=True)
    result = hyperliquid_min_edge_gate({"signal": "long"}, cfg)

    assert result["blocked"] is True
    assert result["reason"] == "min_edge_blocked_expected_move_missing"


def test_min_edge_gate_can_allow_missing_expected_move_for_named_strategy():
    cfg = _edge_cfg(
        require_expected_move=True,
        allow_missing_expected_move_strategies=["rsi_stoch_reversal_1m"],
    )
    result = hyperliquid_min_edge_gate(
        {"signal": "long", "strategy": "rsi_stoch_reversal_1m"},
        cfg,
    )

    assert result["blocked"] is False
    assert result["reason"] == "min_edge_missing_allowed_for_strategy"


def test_min_edge_gate_uses_direct_expected_move():
    cfg = _edge_cfg()
    result = hyperliquid_min_edge_gate(
        {"signal": "long", "expected_move_pct": 0.55}, cfg,
    )
    assert result["blocked"] is False
    assert result["expectedMovePct"] == pytest.approx(0.55)


def test_min_edge_gate_blocks_below_threshold():
    cfg = _edge_cfg()
    result = hyperliquid_min_edge_gate(
        {"signal": "long", "expected_move_pct": 0.25}, cfg,
    )
    assert result["blocked"] is True
    assert "min_edge_blocked" in result["reason"]


def test_min_edge_gate_allows_configured_evidence_lane():
    cfg = _edge_cfg()
    cfg["min_edge_gate"]["evidence_exempt_lanes"] = [
        {
            "strategy": "arc_daytrade",
            "side": "short",
            "regimes": ["breakout", "high_volatility"],
        }
    ]
    result = hyperliquid_min_edge_gate(
        {
            "strategy": "arc_daytrade",
            "signal": "short",
            "market_regime": "breakout",
            "expected_move_pct": 0.10,
        },
        cfg,
    )
    assert result["blocked"] is False
    assert result["reason"] == "min_edge_evidence_exempt_lane"


def test_min_edge_gate_keeps_arc_trending_up_short_blocked():
    cfg = _edge_cfg()
    cfg["min_edge_gate"]["evidence_exempt_lanes"] = [
        {
            "strategy": "arc_daytrade",
            "side": "short",
            "regimes": ["breakout", "high_volatility"],
        }
    ]
    result = hyperliquid_min_edge_gate(
        {
            "strategy": "arc_daytrade",
            "signal": "short",
            "market_regime": "trending_up",
            "expected_move_pct": 0.10,
        },
        cfg,
    )
    assert result["blocked"] is True


def test_min_edge_gate_reads_indicator_field():
    cfg = _edge_cfg()
    result = hyperliquid_min_edge_gate(
        {
            "signal": "long",
            "details": {"indicators": {"expected_move_pct": 0.65}},
        },
        cfg,
    )
    assert result["blocked"] is False
    assert result["expectedMovePct"] == pytest.approx(0.65)


def test_promoted_cohort_selection_boost_matches_coin_strategy_side_regime():
    cfg = {
        "shadow_cohort_promotion": {"selection_boost": 0.35},
    }
    boost = promoted_cohort_selection_boost(
        {"strategy": "ema50_breakout_pullback", "signal": "long"},
        coin="xyz:NATGAS",
        market_regime="sideways",
        promoted_cohorts=[
            {
                "coin": "xyz:NATGAS",
                "strategy": "ema50_breakout_pullback",
                "side": "long",
                "regime": "sideways",
            }
        ],
        hl_cfg=cfg,
    )
    assert boost == pytest.approx(0.35)


def test_promoted_cohort_selection_boost_requires_regime_match():
    cfg = {"shadow_cohort_promotion": {"selection_boost": 0.35}}
    boost = promoted_cohort_selection_boost(
        {"strategy": "ema50_breakout_pullback", "signal": "long"},
        coin="xyz:NATGAS",
        market_regime="trending_up",
        promoted_cohorts=[
            {
                "coin": "xyz:NATGAS",
                "strategy": "ema50_breakout_pullback",
                "side": "long",
                "regime": "sideways",
            }
        ],
        hl_cfg=cfg,
    )
    assert boost == 0.0


def test_select_mirrored_signal_does_not_promote_disabled_ema50():
    payload = {
        "market_regime": "sideways",
        "consensus": {"signal": "hold", "confidence": 0.1, "agreement": 10},
        "strategies": {
            "ema50_breakout_pullback": {
                "signal": "long",
                "confidence": 0.74,
                "strength": 0.70,
                "state": {
                    "indicators": {
                        "expected_move_pct": 1.10,
                        "reward_risk": 2.5,
                        "breakout_pass": True,
                        "pullback_pass": True,
                        "trigger_pass": True,
                        "setup": "ema50_breakout_pullback",
                    }
                },
            },
        },
    }
    cfg = {
        "min_edge_gate": {"enabled": True, "min_edge_pct": 0.40, "edge_multiplier": 2.0},
        "shadow_cohort_promotion": {"selection_boost": 0.35},
        "specialist_strategy_gates": {
            "ema50_breakout_pullback": {
                "enabled": True,
                "min_confidence": 0.74,
                "min_strength": 0.65,
                "min_reward_risk": 2.0,
            }
        },
    }
    promoted = [
        {
            "coin": "xyz:NATGAS",
            "strategy": "ema50_breakout_pullback",
            "side": "long",
            "regime": "sideways",
        }
    ]
    selected = select_mirrored_signal(
        payload,
        cfg,
        coin="xyz:NATGAS",
        market_regime="sideways",
        promoted_cohorts=promoted,
    )
    assert selected is None


def test_executable_size_requalification_blocks_until_sample_met():
    cfg = {
        "executable_size_requalification": {
            "enabled": True,
            "min_closed_trades": 3,
            "min_span_days": 7,
            "min_profit_factor": 1.25,
            "min_realized_pnl_usd": 0.0,
        }
    }
    trades = [
        {
            "status": "CLOSED",
            "source_strategy": "ema50_breakout_pullback",
            "position_side": "long",
            "realized_pnl": 1.0,
            "exit_time": "2026-06-20T10:00:00+00:00",
            "metadata": {},
        }
    ]
    ok, reason = executable_size_requalification_passes(
        "ema50_breakout_pullback",
        "long",
        trades,
        cfg,
    )
    assert ok is False
    assert "requal_closed_1_lt_3" in reason


def test_executable_size_requalification_passes_with_enough_edge():
    cfg = {
        "executable_size_requalification": {
            "enabled": True,
            "min_closed_trades": 2,
            "min_span_days": 1,
            "min_profit_factor": 1.25,
            "min_realized_pnl_usd": 0.0,
        }
    }
    trades = [
        {
            "status": "CLOSED",
            "source_strategy": "ema50_breakout_pullback",
            "position_side": "long",
            "realized_pnl": 2.0,
            "exit_time": "2026-06-20T10:00:00+00:00",
            "metadata": {},
        },
        {
            "status": "CLOSED",
            "source_strategy": "ema50_breakout_pullback",
            "position_side": "long",
            "realized_pnl": 1.0,
            "exit_time": "2026-06-22T10:00:00+00:00",
            "metadata": {},
        },
    ]
    ok, reason = executable_size_requalification_passes(
        "ema50_breakout_pullback",
        "long",
        trades,
        cfg,
    )
    assert ok is True
    assert reason == "requal_pass"

def test_min_edge_gate_normalizes_decimal_pct():
    """Values < 0.1 are interpreted as decimal form (0.002 -> 0.2%)."""
    cfg = _edge_cfg()
    result = hyperliquid_min_edge_gate(
        {"signal": "long", "expected_move_pct": 0.002}, cfg,
    )
    assert result["expectedMovePct"] == pytest.approx(0.2)
    assert result["blocked"] is True
    assert result["expectedMovePct"] < result["thresholdPct"]

    permissive = hyperliquid_min_edge_gate(
        {"signal": "long", "expected_move_pct": 0.006}, cfg,
    )
    assert permissive["expectedMovePct"] == pytest.approx(0.6)
    assert permissive["blocked"] is False


def test_min_edge_gate_derives_from_tp_sl_and_confidence():
    cfg = _edge_cfg()
    signal = {
        "signal": "long",
        "confidence": 0.7,
        "details": {
            "indicators": {
                "take_profit_pct": 2.0,
                "stop_loss_pct": 1.5,
            }
        },
    }
    result = hyperliquid_min_edge_gate(signal, cfg)
    assert result["blocked"] is False
    expected = 2.0 - 1.5 * (1.0 - 0.7)
    assert result["expectedMovePct"] == pytest.approx(expected)


def test_min_edge_gate_threshold_follows_fee_rate():
    cfg = _edge_cfg()
    cfg["fee_rate_per_side"] = 0.0025
    result = hyperliquid_min_edge_gate(
        {"signal": "long", "expected_move_pct": 0.90}, cfg,
    )
    assert result["blocked"] is True
    assert result["thresholdPct"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Phase 7 (2026-05-27): PnL-weighted strategy sizing tier
# ---------------------------------------------------------------------------


def _closed_trade(strategy, pnl, hours_ago):
    return {
        "source_strategy": strategy,
        "realized_pnl": pnl,
        "exit_time": (
            datetime.utcnow() - timedelta(hours=hours_ago)
        ).isoformat(),
    }


def test_pnl_tier_strong_when_rolling_pnl_above_threshold():
    trades = [
        _closed_trade("swing_hull_rsi_ema", 4.0, 5),
        _closed_trade("swing_hull_rsi_ema", 3.0, 10),
        _closed_trade("swing_hull_rsi_ema", -1.0, 20),
    ]
    result = hyperliquid_strategy_pnl_multiplier(
        "swing_hull_rsi_ema", trades,
        lookback_hours=168, strong_pnl_threshold=5.0, min_sample=3,
    )
    assert result["tier"] == "strong"
    assert result["multiplier"] == pytest.approx(1.0)
    assert result["lookback_trades"] == 3
    assert result["lookback_pnl"] == pytest.approx(6.0)


def test_pnl_tier_normal_when_above_breakeven_but_below_strong():
    trades = [
        _closed_trade("small_size_momentum_scalp", 1.0, 5),
        _closed_trade("small_size_momentum_scalp", 0.5, 10),
        _closed_trade("small_size_momentum_scalp", -0.2, 20),
    ]
    result = hyperliquid_strategy_pnl_multiplier(
        "small_size_momentum_scalp", trades, min_sample=3,
    )
    assert result["tier"] == "normal"
    assert result["multiplier"] == pytest.approx(0.7)


def test_pnl_tier_probation_when_underwater():
    trades = [
        _closed_trade("breakout_retest_long", -2.0, 5),
        _closed_trade("breakout_retest_long", -1.5, 10),
        _closed_trade("breakout_retest_long", 0.5, 20),
    ]
    result = hyperliquid_strategy_pnl_multiplier(
        "breakout_retest_long", trades, min_sample=3,
    )
    assert result["tier"] == "probation"
    assert result["multiplier"] == pytest.approx(0.4)
    assert result["lookback_pnl"] < 0


def test_pnl_tier_normal_when_under_min_sample():
    trades = [_closed_trade("supertrend", 0.7, 5)]
    result = hyperliquid_strategy_pnl_multiplier(
        "supertrend", trades, min_sample=3,
    )
    assert result["tier"] == "normal_unsampled"
    assert result["multiplier"] == pytest.approx(0.7)


def test_pnl_tier_ignores_old_trades_outside_lookback():
    trades = [
        _closed_trade("vwma_hull", -10.0, 200),
        _closed_trade("vwma_hull", 6.0, 5),
        _closed_trade("vwma_hull", 4.0, 10),
        _closed_trade("vwma_hull", 1.0, 20),
    ]
    result = hyperliquid_strategy_pnl_multiplier(
        "vwma_hull", trades, lookback_hours=168, min_sample=3,
    )
    assert result["tier"] == "strong"
    assert result["lookback_trades"] == 3
    assert result["lookback_pnl"] == pytest.approx(11.0)


def test_pnl_tier_filters_by_strategy_name():
    trades = [
        _closed_trade("swing_hull_rsi_ema", 5.0, 5),
        _closed_trade("vwma_hull", -10.0, 5),
    ]
    result = hyperliquid_strategy_pnl_multiplier(
        "vwma_hull", trades, min_sample=1,
    )
    assert result["tier"] == "probation"
    assert result["lookback_pnl"] == pytest.approx(-10.0)


def test_pnl_tier_empty_strategy_name():
    result = hyperliquid_strategy_pnl_multiplier(
        "", [], min_sample=1,
    )
    assert result["tier"] == "normal"
    assert result["reason"] == "strategy_unknown"


def test_hyperliquid_daily_profit_target_halts_after_target():
    now = datetime(2026, 6, 16, 18, 0, 0)
    rows = [
        {
            "status": "CLOSED",
            "realized_pnl": 15.0,
            "exit_time": "2026-06-16T08:00:00+00:00",
        },
        {
            "status": "CLOSED",
            "realized_pnl": 12.0,
            "exit_time": "2026-06-16T12:00:00+00:00",
        },
        {
            "status": "CLOSED",
            "realized_pnl": -50.0,
            "exit_time": "2026-06-15T12:00:00+00:00",
        },
    ]
    result = hyperliquid_daily_profit_target_halt(
        rows,
        {"daily_profit_target": {"enabled": True, "target_usd": 25.0}},
        now=now,
    )
    assert result["blocked"] is True
    assert result["reason"] == "daily_profit_target"
    assert result["dailyPnl"] == pytest.approx(27.0)


def test_hyperliquid_daily_profit_target_allows_before_target():
    now = datetime(2026, 6, 16, 18, 0, 0)
    rows = [
        {
            "status": "CLOSED",
            "realized_pnl": 24.99,
            "exit_time": "2026-06-16T12:00:00+00:00",
        },
    ]
    result = hyperliquid_daily_profit_target_halt(
        rows,
        {"daily_profit_target": {"enabled": True, "target_usd": 25.0}},
        now=now,
    )
    assert result["blocked"] is False
    assert result["reason"] == "daily_profit_target_not_reached"
    assert result["dailyPnl"] == pytest.approx(24.99)


def test_hyperliquid_regime_direction_gate_blocks_strategy_specific_loser():
    result = hyperliquid_regime_direction_gate(
        "long",
        "high_volatility",
        0.74,
        0.72,
        {
            "strategy_regime_side_blocks": {
                "ema50_breakout_pullback": {"high_volatility": ["long"]}
            }
        },
        strategy="ema50_breakout_pullback",
    )
    assert result["blocked"] is True
    assert result["reason"] == (
        "configured_strategy_regime_side_block_"
        "ema50_breakout_pullback_high_volatility_long"
    )


def test_hyperliquid_regime_direction_gate_keeps_other_strategy_same_regime():
    result = hyperliquid_regime_direction_gate(
        "long",
        "high_volatility",
        0.72,
        0.70,
        {
            "strategy_regime_side_blocks": {
                "ema50_breakout_pullback": {"high_volatility": ["long"]}
            }
        },
        strategy="rsi_stoch_reversal_5m",
    )
    assert result["blocked"] is False


def test_supply_demand_sideways_blocked_by_strategy_regime_side_blocks():
    cfg = {
        "strategy_regime_side_blocks": {
            "supply_demand_3step": {
                "sideways": ["long", "short"],
            }
        }
    }
    short = hyperliquid_regime_direction_gate(
        "short", "sideways", 0.80, 0.80, cfg, strategy="supply_demand_3step"
    )
    long = hyperliquid_regime_direction_gate(
        "long", "sideways", 0.80, 0.80, cfg, strategy="supply_demand_3step"
    )
    assert short["blocked"] is True
    assert long["blocked"] is True


def test_dollar_loss_cap_honors_usd_backstop_alongside_pct():
    from datetime import datetime, timezone

    trade = {
        "position_side": "short",
        "entry_price": 100.0,
        "position_size": 10.0,
        "notional_size": 1000.0,
        "leverage": 1.0,
        "source_strategy": "supply_demand_3step",
        "entry_time": datetime.now(timezone.utc).isoformat(),
        "fees": 0.0,
        "funding": 0.0,
        "metadata": {},
    }
    cfg = paper_perp_exit_config_from_yaml(
        {
            "fixed_stop_loss_enabled": False,
            "use_spot_exit_rules": False,
            "fee_rate_per_side": 0.0,
            "dollar_loss_cap": {
                "enabled": True,
                "soft_loss_pct": 0.0,
                "hard_loss_pct": 0.0,
                "strategy_hard_loss_pct": {"supply_demand_3step": 0.40},
                "strategy_max_loss_usd": {"supply_demand_3step": 3.0},
            },
            "trailing_stop": {"enabled": False},
            "profit_protection": {"enabled": False},
            "stagnant_loser_enabled": False,
            "time_decay_exit": {"enabled": False},
        },
        {},
        strategy_name="supply_demand_3step",
    )
    # Short adverse +0.35% on $1000 notional ≈ $3.50 loss → USD backstop fires
    # before the 0.40% hard pct.
    result = evaluate_paper_perp_exit(trade, 100.35, cfg)
    assert result.exit_reason
    assert "paper_loss_cap_$3.00" in result.exit_reason


def test_shadow_promotion_requirement_does_not_block_unconfigured_short():
    """Shorts without a matching require_promotion_for lane pass without promotion."""
    cfg = {
        "shadow_cohort_promotion": {
            "require_promotion_for": [
                {
                    "strategy": "arc_daytrade",
                    "side": "short",
                    "regimes": ["trending_up", "breakout", "high_volatility"],
                },
            ]
        }
    }
    result = hyperliquid_shadow_promotion_requirement(
        "BTC",
        {"strategy": "swing_hull_rsi_ema", "signal": "short"},
        "trending_down",
        cfg,
        {},
    )
    assert result["blocked"] is False
    assert result["reason"] == "shadow_promotion_not_required"


def test_shadow_promotion_requirement_blocks_arc_short_without_promoted_cohort():
    cfg = {
        "shadow_cohort_promotion": {
            "require_promotion_for": [
                {
                    "strategy": "arc_daytrade",
                    "side": "short",
                    "regimes": ["trending_up", "breakout", "high_volatility"],
                },
            ]
        }
    }
    result = hyperliquid_shadow_promotion_requirement(
        "BTC",
        {"strategy": "arc_daytrade", "signal": "short"},
        "trending_up",
        cfg,
        {},
    )
    assert result["blocked"] is True
    assert result["reason"] == "shadow_promotion_required_arc_daytrade_short_trending_up"


def test_shadow_promotion_requirement_allows_promoted_short():
    cfg = {
        "shadow_cohort_promotion": {
            "require_promotion_for": [
                {
                    "strategy": "arc_daytrade",
                    "side": "short",
                    "regimes": ["trending_up", "breakout", "high_volatility"],
                },
            ]
        }
    }
    cohort = {
        "strategy": "arc_daytrade",
        "side": "short",
        "regime": "trending_up",
        "closed": 24,
        "win_rate": 0.54,
        "realized": 12.73,
    }
    result = hyperliquid_shadow_promotion_requirement(
        "XYZ:BB",
        {"strategy": "arc_daytrade", "signal": "short"},
        "trending_up",
        cfg,
        {"XYZ:BB": [cohort]},
    )
    assert result["blocked"] is False
    assert result["reason"] == "shadow_promoted_cohort_match"


def test_shadow_promotion_requirement_blocks_unpromoted_risky_cohort():
    cfg = {
        "shadow_cohort_promotion": {
            "require_promotion_for": [
                {
                    "strategy": "supply_demand_3step",
                    "side": "long",
                    "regimes": ["trending_up"],
                }
            ]
        }
    }
    result = hyperliquid_shadow_promotion_requirement(
        "FARTCOIN",
        {"strategy": "supply_demand_3step", "signal": "long"},
        "trending_up",
        cfg,
        {
            "XYZ:MSTR": [
                {
                    "strategy": "supply_demand_3step",
                    "side": "long",
                    "regime": "trending_up",
                    "closed": 48,
                    "win_rate": 0.896,
                    "realized": 98.35,
                }
            ]
        },
    )

    assert result["blocked"] is True
    assert result["reason"] == (
        "shadow_promotion_required_supply_demand_3step_long_trending_up"
    )


def test_shadow_promotion_requirement_allows_matching_promoted_coin():
    cfg = {
        "shadow_cohort_promotion": {
            "require_promotion_for": [
                {
                    "strategy": "supply_demand_3step",
                    "side": "long",
                    "regimes": ["trending_up"],
                }
            ]
        }
    }
    cohort = {
        "strategy": "supply_demand_3step",
        "side": "long",
        "regime": "trending_up",
        "closed": 48,
        "win_rate": 0.896,
        "realized": 98.35,
    }
    result = hyperliquid_shadow_promotion_requirement(
        "xyz:MSTR",
        {"strategy": "supply_demand_3step", "signal": "long"},
        "trending_up",
        cfg,
        {"XYZ:MSTR": [cohort]},
    )

    assert result["blocked"] is False
    assert result["reason"] == "shadow_promoted_cohort_match"
    assert result["cohort"] == cohort


def test_shadow_promotion_requirement_ignores_unconfigured_strategy():
    result = hyperliquid_shadow_promotion_requirement(
        "XPL",
        {"strategy": "swing_hull_rsi_ema", "signal": "long"},
        "trending_up",
        {
            "shadow_cohort_promotion": {
                "require_promotion_for": [
                    {
                        "strategy": "supply_demand_3step",
                        "side": "long",
                        "regimes": ["trending_up"],
                    }
                ]
            }
        },
        {},
    )

    assert result["blocked"] is False
    assert result["reason"] == "shadow_promotion_not_required"


def test_dual_sma_specialist_gate_passes_valid_long():
    signal = {
        "strategy": "dual_sma_daytrade",
        "signal": "long",
        "confidence": 0.76,
        "strength": 0.70,
        "details": {
            "indicators": {
                "setup": "dual_sma_daytrade",
                "daily_pass": True,
                "confirm_15m_pass": True,
                "entry_5m_pass": True,
                "reward_risk": 2.1,
            }
        },
    }
    gate = dual_sma_daytrade_specialist_gate(signal, {"specialist_strategy_gates": {"dual_sma_daytrade": {"bypass_consensus": True}}})
    assert gate["isSpecialist"] is True
    assert gate["allowed"] is True
    assert gate["bypassConsensus"] is True


def test_supply_demand_specialist_gate_passes_valid_short():
    signal = {
        "strategy": "supply_demand_3step",
        "signal": "short",
        "confidence": 0.76,
        "strength": 0.70,
        "details": {
            "indicators": {
                "setup": "supply_demand_3step",
                "step1_pass": True,
                "step2_pass": True,
                "step3_pass": True,
                "reward_risk": 2.8,
            }
        },
    }
    gate = supply_demand_3step_specialist_gate(
        signal,
        {"specialist_strategy_gates": {"supply_demand_3step": {"bypass_consensus": True}}},
    )
    assert gate["isSpecialist"] is True
    assert gate["allowed"] is True
    assert gate["bypassConsensus"] is True


def test_supply_demand_specialist_gate_blocks_missing_step2():
    signal = {
        "strategy": "supply_demand_3step",
        "signal": "long",
        "confidence": 0.76,
        "strength": 0.70,
        "details": {
            "indicators": {
                "setup": "supply_demand_3step",
                "step1_pass": True,
                "step2_pass": False,
                "step3_pass": True,
                "reward_risk": 3.0,
            }
        },
    }
    gate = supply_demand_3step_specialist_gate(signal, {})
    assert gate["allowed"] is False
    assert "step2_fail" in gate["reason"]


def test_dual_sma_specialist_gate_blocks_low_rr():
    signal = {
        "strategy": "dual_sma_daytrade",
        "signal": "long",
        "confidence": 0.76,
        "strength": 0.70,
        "details": {
            "indicators": {
                "setup": "dual_sma_daytrade",
                "daily_pass": True,
                "confirm_15m_pass": True,
                "entry_5m_pass": True,
                "reward_risk": 1.0,
            }
        },
    }
    gate = dual_sma_daytrade_specialist_gate(signal, {})
    assert gate["allowed"] is False
    assert "rr_" in gate["reason"]


def test_specialist_entry_gate_selects_dual_sma():
    signal = {
        "strategy": "dual_sma_daytrade",
        "signal": "long",
        "confidence": 0.76,
        "strength": 0.70,
        "details": {
            "indicators": {
                "setup": "dual_sma_daytrade",
                "daily_pass": True,
                "confirm_15m_pass": True,
                "entry_5m_pass": True,
                "reward_risk": 2.0,
            }
        },
    }
    gate = specialist_entry_gate(signal, {"specialist_strategy_gates": {"dual_sma_daytrade": {}}})
    assert gate["isSpecialist"] is True
    assert gate["allowed"] is True


def test_select_mirrored_signal_selects_rsi_stoch_as_standalone_before_consensus():
    payload = {
        "consensus": {"signal": "long", "confidence": 0.8, "agreement": 70},
        "strategies": {
            "rsi_stoch_reversal_15m": {
                "signal": "long",
                "confidence": 0.90,
                "strength": 0.85,
            },
            "macd_momentum": {
                "signal": "long",
                "confidence": 0.72,
                "strength": 0.70,
            },
        },
    }
    selected = select_mirrored_signal(payload, {})
    assert selected["strategy"] == "rsi_stoch_reversal_15m"
    assert selected["standalone_priority"] is True


def test_coin_strategy_entry_deny_blocks_fartcoin_supply_demand_long():
    result = hyperliquid_coin_strategy_entry_deny(
        "FARTCOIN",
        {"strategy": "supply_demand_3step", "signal": "long"},
        "trending_up",
        {
            "coin_strategy_entry_denies": [
                {
                    "coin": "FARTCOIN",
                    "strategy": "supply_demand_3step",
                    "sides": ["long"],
                }
            ]
        },
    )
    assert result["blocked"] is True
    assert "FARTCOIN" in result["reason"]


def test_coin_strategy_entry_deny_allows_supply_demand_short():
    result = hyperliquid_coin_strategy_entry_deny(
        "FARTCOIN",
        {"strategy": "supply_demand_3step", "signal": "short"},
        "trending_down",
        {
            "coin_strategy_entry_denies": [
                {
                    "coin": "FARTCOIN",
                    "strategy": "supply_demand_3step",
                    "sides": ["long"],
                }
            ]
        },
    )
    assert result["blocked"] is False


def test_strategy_entry_deny_without_coin_is_global():
    result = hyperliquid_coin_strategy_entry_deny(
        "SOL",
        {"strategy": "breakout_retest_long", "signal": "long"},
        "trending_up",
        {"coin_strategy_entry_denies": [{"strategy": "breakout_retest_long"}]},
    )
    assert result["blocked"] is True


def test_min_edge_gate_includes_fees_slippage_and_three_x_cost_buffer():
    cfg = {
        "fee_rate_per_side": 0.0001,
        "min_edge_gate": {
            "enabled": True,
            "min_edge_pct": 0.0,
            "edge_multiplier": 3.0,
            "estimated_round_trip_slippage_pct": 0.10,
            "require_expected_move": True,
        },
    }
    result = hyperliquid_min_edge_gate({"expected_move_pct": 0.35}, cfg)
    assert result["estimatedCostPct"] == pytest.approx(0.12)
    assert result["thresholdPct"] == pytest.approx(0.36)
    assert result["blocked"] is True


def test_dynamic_min_notional_scales_strategy_values_with_adaptive_size():
    cfg = {
        "fee_rate_per_side": 0.0001,
        "min_edge_gate": {
            "enabled": True,
            "min_edge_pct": 0.0,
            "edge_multiplier": 1.0,
            "estimated_round_trip_slippage_pct": 0.10,
        },
        "dynamic_notional_gate": {
            "enabled": True,
            "exchange_min_notional_usd": 10.0,
            "minimum_viable_notional_usd": 40.0,
            "min_expected_net_profit_usd": 0.25,
            "max_dynamic_min_notional_usd": 200.0,
            "strategy_overrides": {
                "supply_demand_3step": {
                    "minimum_viable_notional_usd": 50.0,
                    "min_expected_net_profit_usd": 0.25,
                }
            },
        },
    }
    result = dynamic_min_notional(
        {
            "strategy": "supply_demand_3step",
            "expected_move_pct": 0.85,
        },
        cfg,
        size_multiplier=0.35,
    )

    # Costs are 0.12%, leaving 0.73% expected net edge. The scaled $0.0875
    # profit target needs ~$11.99 notional, so the scaled strategy floor wins.
    assert result["netEdgePct"] == pytest.approx(0.73)
    assert result["targetNetProfitUsd"] == pytest.approx(0.0875)
    assert result["minNotional"] == pytest.approx(17.5)
    assert 96.45 >= result["minNotional"]


def test_dynamic_min_notional_raises_floor_when_net_edge_is_thin():
    cfg = {
        "fee_rate_per_side": 0.0001,
        "min_edge_gate": {
            "enabled": True,
            "min_edge_pct": 0.0,
            "edge_multiplier": 1.0,
            "estimated_round_trip_slippage_pct": 0.10,
        },
        "dynamic_notional_gate": {
            "enabled": True,
            "exchange_min_notional_usd": 10.0,
            "minimum_viable_notional_usd": 25.0,
            "min_expected_net_profit_usd": 0.25,
            "max_dynamic_min_notional_usd": 200.0,
        },
    }
    result = dynamic_min_notional(
        {"strategy": "test", "expected_move_pct": 0.22},
        cfg,
        size_multiplier=1.0,
    )

    # 0.22% expected move - 0.12% costs = 0.10% net edge; earning $0.25
    # would need $250, capped at the configured $200 maximum floor.
    assert result["netEdgePct"] == pytest.approx(0.10)
    assert result["minNotional"] == pytest.approx(200.0)


def test_adaptive_perp_leverage_uses_stop_distance_and_strategy_cap():
    cfg = {
        "default_leverage": 2.0,
        "adaptive_leverage": {
            "enabled": True,
            "min": 1.0,
            "max": 5.0,
            "tight_stop_max_pct": 0.60,
            "medium_stop_max_pct": 1.25,
            "wide_stop_min_pct": 2.0,
            "strategy_overrides": {
                "supply_demand_3step": {"min": 1.0, "max": 3.0, "default": 2.0},
                "rsi_stoch_reversal_5m": {"min": 1.0, "max": 5.0, "default": 3.0},
            },
        },
    }
    supply = {
        "strategy": "supply_demand_3step",
        "details": {"state": {"indicators": {"stop_loss_pct": 0.50}}},
    }
    rsi = {
        "strategy": "rsi_stoch_reversal_5m",
        "details": {"state": {"indicators": {"stop_loss_pct": 0.50}}},
    }
    wide = {
        "strategy": "rsi_stoch_reversal_5m",
        "details": {"state": {"indicators": {"stop_loss_pct": 2.50}}},
    }

    assert adaptive_perp_leverage(supply, cfg) == pytest.approx(3.0)
    assert adaptive_perp_leverage(rsi, cfg) == pytest.approx(5.0)
    assert adaptive_perp_leverage(wide, cfg) == pytest.approx(1.0)


def test_shadow_promotion_requirement_blocks_rsi_stoch_without_promoted_cohort():
    result = hyperliquid_shadow_promotion_requirement(
        "WLD",
        {"strategy": "rsi_stoch_reversal_5m", "signal": "long"},
        "trending_up",
        {
            "shadow_cohort_promotion": {
                "require_promotion_for": [
                    {"strategy": "rsi_stoch_reversal_5m", "side": "long"},
                ]
            }
        },
        {},
    )
    assert result["blocked"] is True


def test_supply_demand_specialist_gate_uses_side_specific_size_multiplier():
    signal = {
        "strategy": "supply_demand_3step",
        "signal": "short",
        "confidence": 0.76,
        "strength": 0.70,
        "details": {
            "indicators": {
                "setup": "supply_demand_3step",
                "step1_pass": True,
                "step2_pass": True,
                "step3_pass": True,
                "reward_risk": 2.8,
            }
        },
    }
    gate = supply_demand_3step_specialist_gate(
        signal,
        {
            "specialist_strategy_gates": {
                "supply_demand_3step": {
                    "size_multiplier": 0.75,
                    "size_multiplier_by_side": {"short": 1.0, "long": 0.75},
                }
            }
        },
    )
    assert gate["allowed"] is True
    assert gate["sizeMultiplier"] == pytest.approx(1.0)


def test_hyperliquid_signal_prefetch_settings_reads_config_block():
    settings = hyperliquid_signal_prefetch_settings(
        {
            "signal_prefetch": {
                "timeout_seconds": 22,
                "retries": 2,
                "max_prefetch_seconds": 28,
                "entry_evaluation_reserve_seconds": 12,
            }
        }
    )
    assert settings["timeout_seconds"] == pytest.approx(22.0)
    assert settings["retries"] == 2
    assert settings["max_prefetch_seconds"] == pytest.approx(28.0)
    assert settings["entry_evaluation_reserve_seconds"] == pytest.approx(12.0)


def test_hyperliquid_signal_prefetch_health_classifies_outcomes():
    assert hyperliquid_signal_prefetch_health({"requested": 30, "scanned_missed": 0}) == "ok"
    assert hyperliquid_signal_prefetch_health({"skipped": True}) == "ok"
    assert hyperliquid_signal_prefetch_health({"requested": 30, "scanned_missed": 2}) == "degraded"
    assert hyperliquid_signal_prefetch_health({"requested": 30, "scanned_missed": 10}) == "failed"
    assert hyperliquid_signal_prefetch_health({}) == "unknown"


@pytest.mark.asyncio
async def test_fetch_hyperliquid_entry_signal_payload_retries_then_succeeds():
    calls = {"n": 0}

    class _Resp:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    class _Client:
        async def get(self, url, timeout=20.0):
            calls["n"] += 1
            if calls["n"] == 1:
                raise TimeoutError("slow")
            return _Resp(200, {"coin": "BTC", "strategies": {}})

    payload, reason = await fetch_hyperliquid_entry_signal_payload(
        _Client(),
        coin_key="BTC",
        signal_source="hyperliquid_strategies",
        strategy_service_url="http://strategy-service:8004",
        mirror_exchanges=[],
        pair_selections={},
        timeout_seconds=5.0,
        retries=1,
        retry_delay_seconds=0.0,
    )
    assert reason == "ok"
    assert payload["coin"] == "BTC"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_prefetch_hyperliquid_entry_signals_respects_entry_reserve():
    class _Resp:
        status_code = 200

        def json(self):
            return {"coin": "BTC", "strategies": {}}

    class _Client:
        async def get(self, url, timeout=20.0):
            await asyncio.sleep(0.05)
            return _Resp()

    deadline = time.monotonic() + 0.2
    prefetched, stats = await prefetch_hyperliquid_entry_signals(
        _Client(),
        coins=["BTC"],
        signal_source="hyperliquid_strategies",
        strategy_service_url="http://strategy-service:8004",
        mirror_exchanges=[],
        pair_selections={},
        hl_cfg={"signal_prefetch": {"entry_evaluation_reserve_seconds": 15}},
        deadline=deadline,
        concurrency=1,
    )
    assert stats["requested"] == 1
    assert stats["failure_reasons"].get("__deadline__") == "prefetch_budget_exhausted"
    assert prefetched == {}
