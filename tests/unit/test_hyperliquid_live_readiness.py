"""Tests for HL rsi_stoch paper live-readiness gate."""

from datetime import datetime, timedelta, timezone

from core.hyperliquid_live_readiness import evaluate_live_readiness


def _closed_trade(pnl: float, hours_ago: float = 1.0) -> dict:
    exit_t = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return {
        "source_strategy": "rsi_stoch_reversal_5m",
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
    }
    rows = [_closed_trade(5.0) for _ in range(5)]
    result = evaluate_live_readiness(rows, promo)
    assert result["ready"] is False
    assert any("closed_trades" in r for r in result["reasons"])


def test_readiness_passes_with_strong_sample():
    promo = {
        "min_closed_trades": 10,
        "lookback_hours": 168,
        "min_realized_pnl_usd": 10.0,
        "min_profit_factor": 1.0,
        "min_win_rate": 0.5,
        "max_consecutive_losses": 5,
        "require_positive_last_24h": True,
    }
    rows = [_closed_trade(3.0) for _ in range(8)] + [_closed_trade(-1.0) for _ in range(2)]
    result = evaluate_live_readiness(rows, promo)
    assert result["ready"] is True
    assert result["reasons"] == []
