from core.dashboard_audit_venues import (
    SPOT_AUDIT_VENUES,
    filter_audit_payload_by_venues,
    merge_venue_audit_payloads,
)
from core.ema50_breakout_pullback_audit_summary import build_ema50_breakout_pullback_audit_summary


def _row(venue: str, symbol: str, log_ts: str, setup_state: str = "waiting_breakout"):
    return {
        "venue": venue,
        "symbol": symbol,
        "log_ts": log_ts,
        "setup_state": setup_state,
        "signal": "hold",
        "breakout_pass": False,
        "pullback_pass": False,
        "trigger_pass": False,
    }


def test_merge_venue_audit_payloads_excludes_hyperliquid():
    payloads = [
        {"rows": [_row("binance", "BTC/USDC", "2026-06-07T12:00:00Z")]},
        {"rows": [_row("hyperliquid", "BTC", "2026-06-07T12:00:00Z", "triggered")]},
        {"rows": [_row("bybit", "TON/USDC", "2026-06-07T11:00:00Z")]},
    ]
    merged = merge_venue_audit_payloads(
        [p for i, p in enumerate(payloads) if i != 1],
        build_ema50_breakout_pullback_audit_summary,
    )
    venues = {r["venue"] for r in merged["rows"]}
    assert venues == {"binance", "bybit"}
    assert merged["summary"]["totalEvaluations"] == 2
    assert "hyperliquid" not in venues


def test_filter_audit_payload_by_venues_rebuilds_summary():
    payload = {
        "rows": [
            _row("binance", "DOT/USDC", "2026-06-07T10:00:00Z"),
            _row("hyperliquid", "LIT", "2026-06-07T10:00:00Z", "triggered"),
            _row("cryptocom", "BTC/USD", "2026-06-07T09:00:00Z"),
        ]
    }
    filtered = filter_audit_payload_by_venues(
        payload,
        SPOT_AUDIT_VENUES,
        build_ema50_breakout_pullback_audit_summary,
    )
    assert filtered["count"] == 2
    assert {r["venue"] for r in filtered["rows"]} == {"binance", "cryptocom"}
    assert filtered["summary"]["totalEvaluations"] == 2
