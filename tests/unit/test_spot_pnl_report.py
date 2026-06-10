"""Tests for spot trading PnL report aggregation."""

from datetime import datetime, timedelta, timezone

from core.spot_pnl_report import build_spot_pnl_report, normalize_spot_trade_row


def _ts(hours_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


def test_normalize_spot_trade_row_maps_strategy_and_pair():
    row = normalize_spot_trade_row({
        "status": "CLOSED",
        "exchange": "binance",
        "pair": "ETHUSDC",
        "strategy": "macd_momentum",
        "realized_pnl": 1.25,
        "fees": 0.5,
        "entry_time": _ts(3),
        "exit_time": _ts(1),
        "exit_reason": "take_profit",
    })

    assert row["source_strategy"] == "macd_momentum"
    assert row["pair"] == "ETH/USDC"
    assert row["exchange"] == "binance"
    assert row["position_side"] == "long"
    assert row["funding"] == 0.0


def test_build_spot_pnl_report_24h_window_and_breakdowns():
    open_trades = [{
        "status": "OPEN",
        "exchange": "bybit",
        "pair": "BTC/USDC",
        "strategy": "swing_trend",
        "entry_time": _ts(2),
        "unrealized_pnl": 0.5,
    }]
    closed_trades = [
        {
            "status": "CLOSED",
            "exchange": "binance",
            "pair": "ETH/USDC",
            "strategy": "macd_momentum",
            "realized_pnl": 2.0,
            "fees": 0.4,
            "entry_time": _ts(4),
            "exit_time": _ts(1),
            "exit_reason": "take_profit",
        },
        {
            "status": "CLOSED",
            "exchange": "binance",
            "pair": "SOL/USDC",
            "strategy": "macd_momentum",
            "realized_pnl": -1.0,
            "fees": 0.2,
            "entry_time": _ts(30),
            "exit_time": _ts(25),
            "exit_reason": "stop_loss",
        },
    ]

    report = build_spot_pnl_report(open_trades, closed_trades, hours=24, limit=100)

    assert report["marketType"] == "spot"
    assert report["tradeCount"] == 2
    assert report["aggregate"]["closed"] == 1
    assert report["aggregate"]["open"] == 1
    assert report["aggregate"]["realized"] == 2.0
    assert "strategy" in report["breakdowns"]
    assert "exchange" in report["breakdowns"]
    assert "pair" in report["breakdowns"]
    assert report["breakdowns"]["exchange"][0]["label"] == "binance"
