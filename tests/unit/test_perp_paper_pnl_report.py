"""Tests for paper-perp PnL window filtering (exit-time for realized)."""

from datetime import datetime, timedelta, timezone

from core.perp_paper_pnl_report import build_paper_pnl_report, filter_rows_by_hours


def _ts(hours_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


def test_last_24h_includes_closed_by_exit_time_not_entry_only():
    """Closed trades that exited within 24h count even if opened earlier."""
    rows = [
        {
            "status": "CLOSED",
            "realized_pnl": 1.0,
            "entry_time": _ts(30),  # opened 30h ago
            "exit_time": _ts(2),  # closed 2h ago
            "source_strategy": "rsi_stoch_reversal_5m",
            "position_side": "long",
            "coin": "ETH",
        },
        {
            "status": "CLOSED",
            "realized_pnl": 0.5,
            "entry_time": _ts(2),
            "exit_time": _ts(1),
            "source_strategy": "vwma_hull",
            "position_side": "short",
            "coin": "BNB",
        },
        {
            "status": "CLOSED",
            "realized_pnl": 9.0,
            "entry_time": _ts(48),
  # old trade fully outside window
            "exit_time": _ts(30),
            "source_strategy": "vwma_hull",
            "position_side": "long",
            "coin": "SOL",
        },
    ]
    report = build_paper_pnl_report(rows, hours=24, limit=100)
    assert report["aggregate"]["closed"] == 2
    assert report["aggregate"]["realized"] == 1.5


def test_filter_rows_by_hours_uses_entry_for_open():
    rows = [
        {"status": "OPEN", "entry_time": _ts(2)},
        {"status": "OPEN", "entry_time": _ts(30)},
    ]
    filtered = filter_rows_by_hours(rows, 24)
    assert len(filtered) == 1


def test_accounting_excluded_rows_do_not_count_in_report():
    rows = [
        {
            "status": "CLOSED",
            "realized_pnl": -10.0,
            "entry_time": _ts(4),
            "exit_time": _ts(1),
            "source_strategy": "rsi_stoch_reversal_5m",
            "position_side": "short",
            "coin": "WLD",
            "metadata": {"accounting_excluded": True},
        },
        {
            "status": "CLOSED",
            "realized_pnl": 2.0,
            "entry_time": _ts(4),
            "exit_time": _ts(1),
            "source_strategy": "rsi_stoch_reversal_5m",
            "position_side": "long",
            "coin": "XMR",
        },
    ]

    report = build_paper_pnl_report(rows, hours=24, limit=100)

    assert report["tradeCount"] == 1
    assert report["aggregate"]["closed"] == 1
    assert report["aggregate"]["realized"] == 2.0


def test_report_exposes_gross_before_fees_and_fee_drag():
    rows = [
        {
            "status": "CLOSED",
            "realized_pnl": -1.0,
            "fees": 3.0,
            "funding": 0.0,
            "entry_time": _ts(2),
            "exit_time": _ts(1),
            "source_strategy": "rsi_stoch_reversal_5m",
            "position_side": "long",
            "coin": "ETH",
        }
    ]

    aggregate = build_paper_pnl_report(rows, hours=24, limit=100)["aggregate"]

    assert aggregate["grossBeforeFees"] == 2.0
    assert aggregate["feeDragPct"] == 150.0
