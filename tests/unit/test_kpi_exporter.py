"""Unit tests for KPI exporter perp report field mapping."""

from core.perp_paper_pnl_report import build_paper_pnl_report


def test_build_paper_pnl_report_has_aggregate_and_strategy_breakdown():
    rows = [
        {
            "status": "CLOSED",
            "realized_pnl": 10.0,
            "entry_time": "2026-05-29T12:00:00+00:00",
            "exit_time": "2026-05-29T13:00:00+00:00",
            "source_strategy": "macd_momentum",
            "position_side": "long",
            "coin": "BTC",
        },
        {
            "status": "CLOSED",
            "realized_pnl": -2.0,
            "entry_time": "2026-05-29T14:00:00+00:00",
            "exit_time": "2026-05-29T15:00:00+00:00",
            "source_strategy": "macd_momentum",
            "position_side": "short",
            "coin": "ETH",
        },
    ]
    report = build_paper_pnl_report(rows, hours=0, limit=100)
    assert report["aggregate"]["closed"] == 2
    assert report["aggregate"]["realized"] == 8.0
    strategies = report["breakdowns"]["strategy"]
    assert any(s["label"] == "macd_momentum" for s in strategies)
