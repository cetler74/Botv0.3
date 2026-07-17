"""Tests for HL paper live-readiness gate with validation-manifest requirement."""

from datetime import datetime, timedelta, timezone

from core.hyperliquid_live_readiness import evaluate_live_readiness


def _closed_trade(pnl: float, hours_ago: float = 1.0, strategy: str = "rsi_stoch_reversal_15m") -> dict:
    exit_t = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return {
        "source_strategy": strategy,
        "status": "CLOSED",
        "realized_pnl": pnl,
        "exit_time": exit_t.isoformat(),
        "entry_time": (exit_t - timedelta(minutes=30)).isoformat(),
    }


def test_readiness_fails_with_insufficient_trades():
    promo = {
        "min_closed_trades": 30,
        "lookback_hours": 168,
        "min_realized_pnl_usd": 25.0,
        "min_profit_factor": 1.2,
        "min_win_rate": 0.52,
        "max_consecutive_losses": 4,
        "require_positive_last_24h": True,
        "require_validation_manifest": False,
    }
    rows = [_closed_trade(5.0) for _ in range(5)]
    result = evaluate_live_readiness(rows, promo)
    assert result["ready"] is False
    assert any("closed_trades" in r for r in result["reasons"])


def test_readiness_passes_with_strong_sample_when_manifest_optional():
    promo = {
        "min_closed_trades": 10,
        "lookback_hours": 168,
        "min_realized_pnl_usd": 10.0,
        "min_profit_factor": 1.0,
        "min_win_rate": 0.5,
        "max_consecutive_losses": 5,
        "require_positive_last_24h": True,
        "require_validation_manifest": False,
    }
    rows = [_closed_trade(3.0) for _ in range(8)] + [_closed_trade(-1.0) for _ in range(2)]
    result = evaluate_live_readiness(rows, promo)
    assert result["ready"] is True
    assert result["reasons"] == []


def test_readiness_blocked_when_manifest_gates_fail():
    promo = {
        "min_closed_trades": 10,
        "lookback_hours": 168,
        "min_realized_pnl_usd": 10.0,
        "min_profit_factor": 1.0,
        "min_win_rate": 0.5,
        "max_consecutive_losses": 5,
        "require_positive_last_24h": True,
        "require_validation_manifest": True,
    }
    rows = [_closed_trade(3.0) for _ in range(8)] + [_closed_trade(-1.0) for _ in range(2)]
    result = evaluate_live_readiness(
        rows,
        promo,
        validation_manifest={
            "approved_strategies": [],
            "rejected_strategies": ["donchian_atr_pullback"],
            "target_gates": {"all_passed": False},
            "promotion_performed": False,
        },
    )
    assert result["ready"] is False
    assert any("validation_manifest" in r for r in result["reasons"])


def test_readiness_requires_strategy_on_approved_list():
    promo = {
        "min_closed_trades": 10,
        "lookback_hours": 168,
        "min_realized_pnl_usd": 10.0,
        "min_profit_factor": 1.0,
        "min_win_rate": 0.5,
        "max_consecutive_losses": 5,
        "require_positive_last_24h": True,
        "require_validation_manifest": True,
    }
    rows = [
        _closed_trade(3.0, strategy="rsi_stoch_reversal_15m") for _ in range(8)
    ] + [_closed_trade(-1.0, strategy="rsi_stoch_reversal_15m") for _ in range(2)]
    result = evaluate_live_readiness(
        rows,
        promo,
        strategy="rsi_stoch_reversal_15m",
        validation_manifest={
            "approved_strategies": ["rsi_stoch_reversal_15m"],
            "rejected_strategies": [],
            "target_gates": {"all_passed": True},
            "promotion_performed": False,
        },
    )
    assert result["ready"] is True


def test_readiness_defaults_to_the_15m_promotion_strategy():
    promo = {
        "min_closed_trades": 1,
        "lookback_hours": 168,
        "min_realized_pnl_usd": 0.0,
        "min_profit_factor": 0.0,
        "min_win_rate": 0.0,
        "max_consecutive_losses": 1,
        "require_positive_last_24h": False,
        "require_validation_manifest": False,
    }

    result = evaluate_live_readiness(
        [_closed_trade(1.0, strategy="rsi_stoch_reversal_15m")],
        promo,
    )

    assert result["metrics"]["strategy"] == "rsi_stoch_reversal_15m"
    assert result["metrics"]["closed_trades"] == 1
