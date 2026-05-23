import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ORCH = os.path.join(ROOT, "services", "orchestrator-service")
if ORCH not in sys.path:
    sys.path.insert(0, ORCH)

from hyperliquid_balance import parse_clearinghouse_state  # noqa: E402


def test_parse_clearinghouse_state_account_value_and_withdrawable():
    payload = {
        "marginSummary": {"accountValue": "12500.5", "totalMarginUsed": "2500.25"},
        "withdrawable": "10000.25",
        "assetPositions": [
            {"position": {"unrealizedPnl": "120.5"}},
            {"position": {"unrealizedPnl": "-20.5"}},
        ],
    }
    parsed = parse_clearinghouse_state(payload)
    assert parsed["balance"] == 12500.5
    assert parsed["available_balance"] == 10000.25
    assert parsed["margin_used"] == 2500.25
    assert parsed["unrealized_pnl"] == 100.0


def test_parse_clearinghouse_derives_available_from_equity_minus_margin():
    payload = {
        "marginSummary": {"accountValue": "1000", "totalMarginUsed": "200"},
    }
    parsed = parse_clearinghouse_state(payload)
    assert parsed["available_balance"] == 800.0
