"""Unit tests for HL paper balance derivation."""

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.hyperliquid_ledger import compute_hyperliquid_balance_amounts


def test_empty_book_equals_starting():
    v = compute_hyperliquid_balance_amounts(5000.0, 0.0, 0.0, 0.0)
    assert v["equity"] == 5000.0
    assert v["available_balance"] == 5000.0
    assert v["margin_used"] == 0.0


def test_open_position_locks_margin_but_unrealized_does_not_change_balance():
    v = compute_hyperliquid_balance_amounts(
        starting_balance_usd=5000.0,
        total_realized_pnl=0.0,
        unrealized_pnl=3.0,
        margin_used=25.0,
    )
    assert v["equity"] == 5000.0
    assert v["balance"] == 5000.0
    assert v["available_balance"] == 4975.0
    assert v["unrealized_pnl"] == 3.0


def test_closed_trade_releases_margin_and_adds_realized():
    v = compute_hyperliquid_balance_amounts(
        starting_balance_usd=5000.0,
        total_realized_pnl=10.0,
        unrealized_pnl=0.0,
        margin_used=0.0,
    )
    assert v["equity"] == 5010.0
    assert v["available_balance"] == 5010.0


def test_losing_close_reduces_available():
    v = compute_hyperliquid_balance_amounts(
        starting_balance_usd=5000.0,
        total_realized_pnl=-15.0,
        unrealized_pnl=0.0,
        margin_used=0.0,
    )
    assert v["available_balance"] == 4985.0
