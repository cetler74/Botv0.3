from datetime import datetime, timedelta

from core.ema50_entry_block_reason import (
    enrich_ema50_audit_entry_blocks,
    resolve_ema50_entry_block_reason,
)


def _row(**kwargs):
    base = {
        "symbol": "TON",
        "signal": "short",
        "setup_state": "triggered",
        "direction": "short",
        "invalidation_reason": "none",
    }
    base.update(kwargs)
    return base


def test_setup_incomplete_for_waiting_breakout():
    block = resolve_ema50_entry_block_reason(
        _row(signal="hold", setup_state="waiting_breakout", direction="none"),
        global_entry_block={"entryBlocked": False},
    )
    assert block["reason"] == "setup_incomplete"
    assert block["detail"] == "setup:waiting_breakout"


def test_regime_invalid_uses_invalidation_reason():
    block = resolve_ema50_entry_block_reason(
        _row(
            signal="hold",
            setup_state="invalid",
            invalidation_reason="regime_blocked:low_volatility",
        ),
        global_entry_block={"entryBlocked": False},
    )
    assert block["reason"] == "setup_incomplete"
    assert block["detail"] == "regime_blocked:low_volatility"


def test_recent_negative_realized_blocks_short():
    now = datetime(2026, 6, 7, 22, 0, 0)
    closed = [
        {
            "coin": "TON",
            "position_side": "short",
            "realized_pnl": -1.68,
            "exit_time": (now - timedelta(hours=1)).isoformat() + "+00:00",
            "status": "CLOSED",
        }
    ]
    block = resolve_ema50_entry_block_reason(
        _row(),
        global_entry_block={"entryBlocked": False},
        open_trades=[],
        closed_trades=closed,
        now=now,
    )
    assert block["reason"] == "recent_negative_realized"
    assert "cooldown" in block["detail"].lower()


def test_position_already_open_when_not_underwater():
    open_trades = [
        {
            "coin": "LIT",
            "position_side": "short",
            "unrealized_pnl": 0.25,
            "status": "OPEN",
        }
    ]
    block = resolve_ema50_entry_block_reason(
        _row(symbol="LIT"),
        global_entry_block={"entryBlocked": False},
        open_trades=open_trades,
        closed_trades=[],
    )
    assert block["reason"] == "position_already_open"


def test_enrich_attaches_fields_to_audit_payload():
    payload = enrich_ema50_audit_entry_blocks(
        {"rows": [_row(signal="hold", setup_state="waiting_breakout")]},
        global_entry_block={"entryBlocked": False},
    )
    row = payload["rows"][0]
    assert row["entry_block_reason"] == "setup_incomplete"
    assert "entry_block_detail" in row
    assert payload["summary"]["totalEvaluations"] == 1
