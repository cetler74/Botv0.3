import os
import sys
from datetime import datetime, timedelta

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ORCH = os.path.join(ROOT, "services", "orchestrator-service")
if ORCH not in sys.path:
    sys.path.insert(0, ORCH)

from hyperliquid_perps import (  # noqa: E402
    calculate_perp_pnl,
    perp_side_fee,
    pair_to_hyperliquid_coin,
    pnl_percentage,
    position_sides_from_signal,
    select_mirrored_signal,
    should_close_paper_perp,
)


def test_pair_to_hyperliquid_coin():
    assert pair_to_hyperliquid_coin("NEAR/USDC") == "NEAR"
    assert pair_to_hyperliquid_coin("BTCUSD") == "BTC"
    assert pair_to_hyperliquid_coin("ETHUSDT") == "ETH"


def test_position_sides_from_signal():
    assert position_sides_from_signal("buy") == "long"
    assert position_sides_from_signal("sell") == "short"
    assert position_sides_from_signal("hold") is None


def test_perp_side_fee():
    assert perp_side_fee(50.0, 0.001) == pytest.approx(0.05)
    assert perp_side_fee(0.0, 0.001) == 0.0


def test_side_aware_pnl():
    assert calculate_perp_pnl("long", 100, 110, 2) == 20
    assert calculate_perp_pnl("short", 100, 90, 2) == 20
    assert calculate_perp_pnl("short", 100, 110, 2, fees=1) == -21
    assert pnl_percentage("long", 100, 110) == 10
    assert pnl_percentage("short", 100, 90) == 10


def test_select_mirrored_signal_prefers_consensus_sell_for_short():
    payload = {
        "consensus": {"signal": "sell", "confidence": 0.7, "agreement": 60},
        "strategies": {
            "macd": {"signal": "sell", "confidence": 0.8, "strength": 0.6},
            "rsi": {"signal": "buy", "confidence": 0.9, "strength": 0.4},
        },
    }
    selected = select_mirrored_signal(payload)
    assert selected["signal"] == "sell"
    assert selected["strategy"] == "macd"
    assert selected["confidence"] == 0.7


def test_select_mirrored_signal_uses_best_individual_when_consensus_hold():
    payload = {
        "consensus": {"signal": "hold", "confidence": 0.2, "agreement": 40},
        "strategies": {
            "weak_buy": {"signal": "buy", "confidence": 0.5, "strength": 0.6},
            "strong_sell": {"signal": "sell", "confidence": 0.8, "strength": 0.7},
        },
    }
    selected = select_mirrored_signal(payload)
    assert selected["signal"] == "sell"
    assert selected["strategy"] == "strong_sell"


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

