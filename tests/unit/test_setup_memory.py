from datetime import datetime, timezone

import pytest

from core.setup_memory import (
    detect_permanent_setup_outcomes,
    evaluate_setup_memory,
    setup_identity_from_signal,
)
from core.strategy_trade_evidence import encode_strategy_evidence_entry_reason


def _config(mode="blocking", **overrides):
    setup_memory = {
        "enabled": True,
        "mode": mode,
        "include_shadow": True,
        "min_real_samples": 2,
        "min_shadow_episodes": 2,
        "recent_loss_cooldown_hours": 0,
        "exact_setup_loss_streak_block": 2,
        "block_expectancy_below": 0.0,
        "block_profit_factor_below": 0.8,
        "caution_profit_factor_below": 1.1,
        "size_down_multiplier": 0.5,
    }
    setup_memory.update(overrides)
    return {"trading": {"setup_memory": setup_memory}}


def _signal(**overrides):
    signal = {
        "strategy": "sma_reclaim_bull_flag",
        "strategy_version": "v1",
        "signal": "long",
        "coin": "ETH",
        "market_regime": "trending_up",
        "why": "pullback reclaimed sma",
    }
    signal.update(overrides)
    return signal


def _perp_trade(pnl, *, coin="ETH", strategy="sma_reclaim_bull_flag", side="long", why="pullback reclaimed sma", shadow=False):
    return {
        "trade_id": f"t-{pnl}-{shadow}",
        "status": "CLOSED",
        "coin": coin,
        "source_strategy": strategy,
        "position_side": side,
        "entry_time": "2026-07-10T10:00:00+00:00",
        "exit_time": "2026-07-10T11:00:00+00:00",
        "realized_pnl": pnl,
        "exit_reason": "paper_stop_loss",
        "metadata": {
            "strategy_key": strategy,
            "strategy_version": "v1",
            "strategy_config_hash": "unknown",
            "market_regime": "trending_up",
            "rationale": {"why": why, "status": "entered"},
            "shadow_trade": shadow,
        },
    }


def _spot_trade(pnl, *, pair="ETH/USDC", strategy="sma_reclaim_bull_flag"):
    return {
        "trade_id": f"s-{pnl}",
        "status": "CLOSED",
        "pair": pair,
        "exchange": "binance",
        "strategy": strategy,
        "entry_time": "2026-07-10T10:00:00+00:00",
        "exit_time": "2026-07-10T11:00:00+00:00",
        "entry_price": 100.0,
        "exit_price": 99.0,
        "position_size": 1.0,
        "realized_pnl": pnl,
        "entry_reason": "strategy signal",
        "exit_reason": "stop_loss",
    }


def _spot_trade_with_evidence(
    pnl,
    *,
    pair="ETH/USDC",
    strategy="rsi_oversold_checklist",
    regime="sideways",
):
    evidence = {
        "strategy_key": strategy,
        "strategy_version": "config-test",
        "strategy_config_hash": "cfg-test",
        "timeframe_bundle": ["15m", "1h"],
        "signal_candle_timestamp": "2026-07-10T09:45:00+00:00",
        "market_regime": regime,
        "rationale": {"why": "rsi checklist recovery band", "status": "entered"},
    }
    trade = _spot_trade(pnl, pair=pair, strategy=strategy)
    trade["entry_reason"] = encode_strategy_evidence_entry_reason(
        f"Queue-based {strategy} strategy signal [stable_regime={regime}]",
        evidence,
    )
    return trade


def test_exact_perp_setup_loss_streak_blocks():
    decision = evaluate_setup_memory(
        _signal(),
        market_type="perps",
        real_closed_trades=[_perp_trade(-3.0), _perp_trade(-2.0)],
        config=_config(),
        now=datetime(2026, 7, 11, tzinfo=timezone.utc),
    )

    assert decision.action == "block"
    assert decision.match_level == "exact"
    assert decision.loss_count == 2
    assert "loss streak" in decision.reason


def test_advisory_mode_allows_but_reports_would_block():
    decision = evaluate_setup_memory(
        _signal(),
        market_type="perps",
        real_closed_trades=[_perp_trade(-3.0), _perp_trade(-2.0)],
        config=_config(mode="advisory"),
        now=datetime(2026, 7, 11, tzinfo=timezone.utc),
    )

    assert decision.action == "allow"
    assert "advisory: would block" in decision.reason


def test_broad_weak_memory_sizes_down_instead_of_blocking():
    decision = evaluate_setup_memory(
        _signal(coin="SOL"),
        market_type="perps",
        real_closed_trades=[_perp_trade(-3.0), _perp_trade(1.0), _perp_trade(-2.0)],
        config=_config(exact_setup_loss_streak_block=0),
        now=datetime(2026, 7, 11, tzinfo=timezone.utc),
    )

    assert decision.action == "size_down"
    assert decision.size_multiplier == pytest.approx(0.5)
    assert decision.match_level == "strategy_side_regime"


def test_spot_memory_matches_missing_side_as_long():
    decision = evaluate_setup_memory(
        {
            "strategy": "sma_reclaim_bull_flag",
            "signal": "buy",
            "pair": "ETH/USDC",
            "market_regime": "unknown",
        },
        market_type="spot",
        real_closed_trades=[_spot_trade(-2.0), _spot_trade(-1.0)],
        config=_config(),
        now=datetime(2026, 7, 11, tzinfo=timezone.utc),
    )

    assert decision.action in {"block", "size_down"}
    assert decision.matched_count == 2


def test_spot_memory_uses_market_specific_sample_floor():
    decision = evaluate_setup_memory(
        {
            "strategy": "rsi_oversold_checklist",
            "signal": "buy",
            "pair": "ETH/USDC",
            "market_regime": "unknown",
        },
        market_type="spot",
        real_closed_trades=[
            _spot_trade(-2.0, strategy="rsi_oversold_checklist"),
            _spot_trade(-1.0, strategy="rsi_oversold_checklist"),
        ],
        config=_config(
            min_real_samples=5,
            exact_setup_loss_streak_block=0,
            spot={"enabled": True, "min_real_samples": 2, "include_shadow": False},
        ),
        now=datetime(2026, 7, 11, tzinfo=timezone.utc),
    )

    assert decision.action == "block"
    assert decision.matched_count == 2
    assert "negative expectancy" in decision.reason


def test_spot_memory_can_block_all_loss_broad_cohort():
    decision = evaluate_setup_memory(
        {
            "strategy": "rsi_oversold_checklist",
            "signal": "buy",
            "pair": "SOL/USDC",
            "market_regime": "sideways",
        },
        market_type="spot",
        real_closed_trades=[
            _spot_trade(-2.0, pair="ETH/USDC", strategy="rsi_oversold_checklist"),
            _spot_trade(-1.0, pair="BNB/USDC", strategy="rsi_oversold_checklist"),
        ],
        config=_config(
            min_real_samples=5,
            exact_setup_loss_streak_block=0,
            spot={
                "enabled": True,
                "min_real_samples": 2,
                "include_shadow": False,
                "block_all_loss_cohorts": True,
            },
        ),
        now=datetime(2026, 7, 11, tzinfo=timezone.utc),
    )

    assert decision.action == "block"
    assert decision.match_level == "strategy_side"
    assert "all-loss cohort" in decision.reason


def test_spot_memory_uses_encoded_regime_from_closed_result():
    decision = evaluate_setup_memory(
        {
            "strategy": "rsi_oversold_checklist",
            "strategy_version": "config-test",
            "strategy_config_hash": "cfg-test",
            "signal": "buy",
            "pair": "ETH/USDC",
            "market_regime": "sideways",
            "why": "rsi checklist recovery band",
        },
        market_type="spot",
        real_closed_trades=[
            _spot_trade_with_evidence(-2.0, regime="sideways"),
            _spot_trade_with_evidence(-1.0, regime="sideways"),
        ],
        config=_config(
            min_real_samples=5,
            exact_setup_loss_streak_block=0,
            spot={
                "enabled": True,
                "min_real_samples": 2,
                "include_shadow": False,
                "block_all_loss_cohorts": True,
            },
        ),
        strategy_config={"version": "config-test"},
        now=datetime(2026, 7, 11, tzinfo=timezone.utc),
    )

    assert decision.action == "block"
    assert decision.match_level == "exact"
    assert "all-loss cohort" in decision.reason


def test_signal_identity_uses_entry_evidence_regime_fallback():
    decision = evaluate_setup_memory(
        {
            "strategy": "rsi_oversold_checklist",
            "strategy_version": "config-test",
            "strategy_config_hash": "cfg-test",
            "signal": "buy",
            "pair": "ETH/USDC",
            "why": "rsi checklist recovery band",
            "entry_evidence": {
                "strategy_key": "rsi_oversold_checklist",
                "strategy_version": "config-test",
                "strategy_config_hash": "cfg-test",
                "timeframe_bundle": ["15m", "1h"],
                "signal_candle_timestamp": "2026-07-10T09:45:00+00:00",
                "market_regime": "sideways",
                "rationale": {"why": "rsi checklist recovery band", "status": "entered"},
            },
        },
        market_type="spot",
        real_closed_trades=[
            _spot_trade_with_evidence(-2.0, regime="sideways"),
            _spot_trade_with_evidence(-1.0, regime="sideways"),
        ],
        config=_config(
            exact_setup_loss_streak_block=0,
            spot={
                "enabled": True,
                "min_real_samples": 2,
                "include_shadow": False,
                "block_all_loss_cohorts": True,
            },
        ),
        strategy_config={"version": "config-test"},
        now=datetime(2026, 7, 11, tzinfo=timezone.utc),
    )

    assert decision.action == "block"
    assert decision.match_level == "exact"


def test_spot_broad_all_loss_floor_prevents_two_trade_freeze():
    decision = evaluate_setup_memory(
        {
            "strategy": "rsi_oversold_checklist",
            "signal": "buy",
            "pair": "SOL/USDC",
            "market_regime": "sideways",
        },
        market_type="spot",
        real_closed_trades=[
            _spot_trade(-2.0, pair="ETH/USDC", strategy="rsi_oversold_checklist"),
            _spot_trade(-1.0, pair="BNB/USDC", strategy="rsi_oversold_checklist"),
        ],
        config=_config(
            exact_setup_loss_streak_block=0,
            spot={
                "enabled": True,
                "min_real_samples": 2,
                "include_shadow": False,
                "block_all_loss_cohorts": True,
                "broad_all_loss_min_samples": 4,
            },
        ),
        now=datetime(2026, 7, 11, tzinfo=timezone.utc),
    )

    assert decision.action == "size_down"
    assert decision.match_level == "strategy_side"
    assert "negative expectancy" in decision.reason


def test_spot_can_block_broad_negative_expectancy_when_configured():
    decision = evaluate_setup_memory(
        {
            "strategy": "rsi_oversold_checklist",
            "signal": "buy",
            "pair": "SOL/USDC",
            "market_regime": "sideways",
        },
        market_type="spot",
        real_closed_trades=[
            _spot_trade(-2.0, pair="ETH/USDC", strategy="rsi_oversold_checklist"),
            _spot_trade(-1.0, pair="BNB/USDC", strategy="rsi_oversold_checklist"),
            _spot_trade(-1.0, pair="XRP/USDC", strategy="rsi_oversold_checklist"),
            _spot_trade(0.2, pair="ADA/USDC", strategy="rsi_oversold_checklist"),
        ],
        config=_config(
            exact_setup_loss_streak_block=0,
            spot={
                "enabled": True,
                "min_real_samples": 2,
                "include_shadow": False,
                "block_all_loss_cohorts": True,
                "broad_all_loss_min_samples": 4,
                "block_negative_expectancy_broad": True,
            },
        ),
        now=datetime(2026, 7, 11, tzinfo=timezone.utc),
    )

    assert decision.action == "block"
    assert decision.match_level == "strategy_side"
    assert "negative expectancy" in decision.reason


def test_perp_memory_does_not_consume_spot_rows():
    decision = evaluate_setup_memory(
        _signal(),
        market_type="perps",
        real_closed_trades=[_spot_trade(-2.0), _spot_trade(-1.0)],
        config=_config(),
        now=datetime(2026, 7, 11, tzinfo=timezone.utc),
    )

    assert decision.action == "allow"
    assert decision.matched_count == 0


def test_blocking_memory_blocks_when_required_history_is_unavailable():
    decision = evaluate_setup_memory(
        _signal(),
        market_type="perps",
        config=_config(),
        history_available=False,
    )

    assert decision.action == "block"
    assert "history unavailable" in decision.reason


def test_shadow_memory_counts_overlapping_trades_as_one_episode():
    first = _perp_trade(-3.0, shadow=True)
    second = _perp_trade(-2.0, shadow=True)
    second["trade_id"] = "overlapping-shadow"
    second["entry_time"] = "2026-07-10T10:30:00+00:00"
    second["exit_time"] = "2026-07-10T11:30:00+00:00"

    decision = evaluate_setup_memory(
        _signal(),
        market_type="perps",
        shadow_closed_trades=[first, second],
        config=_config(
            min_shadow_episodes=2,
            exact_setup_loss_streak_block=0,
            block_all_loss_cohorts=True,
        ),
        now=datetime(2026, 7, 11, tzinfo=timezone.utc),
    )

    assert decision.action == "allow"
    assert decision.matched_count == 0
    assert "source sample floor" in decision.reason


def test_mixed_memory_ignores_sources_below_their_independent_floors():
    real = _perp_trade(-3.0)
    shadows = []
    for index in range(4):
        shadow = _perp_trade(-2.0 - index, shadow=True)
        shadow["trade_id"] = f"shadow-{index}"
        shadow["entry_time"] = f"2026-07-10T{10 + index:02d}:00:00+00:00"
        shadow["exit_time"] = f"2026-07-10T{11 + index:02d}:00:00+00:00"
        shadows.append(shadow)

    decision = evaluate_setup_memory(
        _signal(),
        market_type="perps",
        real_closed_trades=[real],
        shadow_closed_trades=shadows,
        config=_config(
            min_real_samples=5,
            min_shadow_episodes=10,
            exact_setup_loss_streak_block=0,
            block_all_loss_cohorts=True,
        ),
        now=datetime(2026, 7, 11, tzinfo=timezone.utc),
    )

    assert decision.action == "allow"
    assert decision.matched_count == 0


def test_lookback_excludes_undated_legacy_rows():
    undated = _perp_trade(-3.0)
    undated["entry_time"] = ""
    undated["exit_time"] = ""

    decision = evaluate_setup_memory(
        _signal(),
        market_type="perps",
        real_closed_trades=[undated],
        config=_config(
            min_real_samples=1,
            exact_setup_loss_streak_block=0,
            block_all_loss_cohorts=True,
        ),
        now=datetime(2026, 7, 11, tzinfo=timezone.utc),
    )

    assert decision.action == "allow"
    assert decision.matched_count == 0


def _loss_trades(count=3):
    trades = []
    for index in range(count):
        trade = _perp_trade(-1.0 - index)
        trade["trade_id"] = f"loss-{index}"
        trade["entry_time"] = f"2026-07-10T{10 + index:02d}:00:00+00:00"
        trade["exit_time"] = f"2026-07-10T{11 + index:02d}:00:00+00:00"
        trades.append(trade)
    return trades


def _win_trades(count=3):
    trades = []
    for index in range(count):
        trade = _perp_trade(1.0 + index)
        trade["trade_id"] = f"win-{index}"
        trade["entry_time"] = f"2026-07-10T{10 + index:02d}:00:00+00:00"
        trade["exit_time"] = f"2026-07-10T{11 + index:02d}:00:00+00:00"
        trades.append(trade)
    return trades


def test_detect_permanent_exact_loss_streak():
    detected = detect_permanent_setup_outcomes(
        market_type="perps",
        real_closed_trades=_loss_trades(3),
        config=_config(
            permanent={
                "enabled": True,
                "exact_loss_streak": 3,
                "exact_win_streak": 3,
                "coin_regime_loss_streak": 99,
                "coin_regime_win_streak": 99,
            }
        ),
    )

    blocks = [row for row in detected if row["outcome"] == "block"]
    assert blocks
    assert blocks[0]["matchLevel"] == "exact"
    assert blocks[0]["streakCount"] >= 3


def test_permanent_exact_loss_blocks_even_outside_lookback():
    identity = setup_identity_from_signal(_signal(), market_type="perps")
    permanent = [
        {
            "fingerprint": identity["setupFingerprint"],
            "matchLevel": "exact",
            "outcome": "block",
            "marketType": "perps",
            "strategyKey": identity["strategyKey"],
            "strategyVersion": identity["strategyVersion"],
            "configHash": identity["configHash"],
            "side": identity["side"],
            "coin": identity["coin"],
            "regime": identity["regime"],
            "why": identity["why"],
            "streakCount": 3,
            "status": "active",
            "evidence": [],
        }
    ]

    decision = evaluate_setup_memory(
        _signal(),
        market_type="perps",
        real_closed_trades=[],
        config=_config(
            recent_loss_cooldown_hours=0,
            exact_setup_loss_streak_block=0,
            permanent={"enabled": True, "size_up_multiplier": 1.25},
        ),
        permanent_records=permanent,
        now=datetime(2026, 7, 11, tzinfo=timezone.utc),
    )

    assert decision.action == "block"
    assert decision.permanent is True
    assert "permanent" in decision.reason


def test_permanent_exact_win_sizes_up():
    identity = setup_identity_from_signal(_signal(), market_type="perps")
    permanent = [
        {
            "fingerprint": identity["setupFingerprint"],
            "matchLevel": "exact",
            "outcome": "promote",
            "marketType": "perps",
            "strategyKey": identity["strategyKey"],
            "strategyVersion": identity["strategyVersion"],
            "configHash": identity["configHash"],
            "side": identity["side"],
            "coin": identity["coin"],
            "regime": identity["regime"],
            "why": identity["why"],
            "streakCount": 3,
            "status": "active",
            "evidence": [],
        }
    ]

    decision = evaluate_setup_memory(
        _signal(),
        market_type="perps",
        real_closed_trades=[],
        config=_config(
            permanent={"enabled": True, "size_up_multiplier": 1.25},
        ),
        permanent_records=permanent,
        now=datetime(2026, 7, 11, tzinfo=timezone.utc),
    )

    assert decision.action == "size_up"
    assert decision.permanent is True
    assert decision.size_multiplier == pytest.approx(1.25)


def test_permanent_block_beats_promote_on_same_fingerprint():
    identity = setup_identity_from_signal(_signal(), market_type="perps")
    permanent = [
        {
            "fingerprint": identity["setupFingerprint"],
            "matchLevel": "exact",
            "outcome": "promote",
            "marketType": "perps",
            "strategyKey": identity["strategyKey"],
            "strategyVersion": identity["strategyVersion"],
            "configHash": identity["configHash"],
            "side": identity["side"],
            "coin": identity["coin"],
            "regime": identity["regime"],
            "why": identity["why"],
            "streakCount": 3,
            "status": "active",
        },
        {
            "fingerprint": identity["setupFingerprint"],
            "matchLevel": "exact",
            "outcome": "block",
            "marketType": "perps",
            "strategyKey": identity["strategyKey"],
            "strategyVersion": identity["strategyVersion"],
            "configHash": identity["configHash"],
            "side": identity["side"],
            "coin": identity["coin"],
            "regime": identity["regime"],
            "why": identity["why"],
            "streakCount": 3,
            "status": "active",
        },
    ]

    decision = evaluate_setup_memory(
        _signal(),
        market_type="perps",
        config=_config(permanent={"enabled": True}),
        permanent_records=permanent,
    )

    assert decision.action == "block"
    assert decision.permanent is True


def test_permanent_version_mismatch_is_ignored():
    identity = setup_identity_from_signal(_signal(), market_type="perps")
    permanent = [
        {
            "fingerprint": identity["setupFingerprint"],
            "matchLevel": "exact",
            "outcome": "block",
            "marketType": "perps",
            "strategyKey": identity["strategyKey"],
            "strategyVersion": "old-version",
            "configHash": identity["configHash"],
            "side": identity["side"],
            "coin": identity["coin"],
            "regime": identity["regime"],
            "why": identity["why"],
            "streakCount": 3,
            "status": "active",
        }
    ]

    decision = evaluate_setup_memory(
        _signal(),
        market_type="perps",
        real_closed_trades=[],
        config=_config(permanent={"enabled": True}),
        permanent_records=permanent,
    )

    assert decision.action == "allow"
    assert decision.permanent is False


def test_detect_permanent_shadow_win_streak_with_episode_floor():
    shadows = []
    for index in range(10):
        trade = _perp_trade(1.5 + index, shadow=True)
        trade["trade_id"] = f"shadow-win-{index}"
        day = 10 + index
        trade["entry_time"] = f"2026-07-{day:02d}T10:00:00+00:00"
        trade["exit_time"] = f"2026-07-{day:02d}T11:00:00+00:00"
        shadows.append(trade)

    detected = detect_permanent_setup_outcomes(
        market_type="perps",
        shadow_closed_trades=shadows,
        config=_config(
            min_shadow_episodes=10,
            permanent={
                "enabled": True,
                "include_shadow": True,
                "exact_loss_streak": 99,
                "exact_win_streak": 3,
                "coin_regime_loss_streak": 99,
                "coin_regime_win_streak": 99,
            },
        ),
    )

    promotes = [row for row in detected if row["outcome"] == "promote"]
    assert promotes
    assert promotes[0]["sourceMix"] == "shadow"
