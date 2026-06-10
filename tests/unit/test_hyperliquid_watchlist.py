"""Tests for HL portfolio watchlist builder."""

from datetime import datetime, timedelta

from core.hyperliquid_watchlist import (
    build_hyperliquid_watchlist,
    coin_entry_block,
    coin_side_entry_block,
    global_entry_block,
)


def test_watchlist_includes_excluded_open_position_not_in_selector():
    hl_cfg = {
        "enabled": True,
        "block_after_negative_realized_hours": 4.0,
        "pair_selector": {"blacklisted_coins": []},
    }
    open_trades = [
        {
            "coin": "NEAR",
            "unrealized_pnl": -0.12,
            "position_side": "short",
            "trade_id": "t1",
        }
    ]
    rows, _ = build_hyperliquid_watchlist(
        hl_cfg,
        {"NEAR": 2.63, "ETH": 1900.0},
        open_trades,
        closed_trades=[],
        hl_selected_pairs=["ETH/USD-PERP"],
        liquid_candidate_coins=["ETH", "NEAR", "SOL"],
    )
    coins = {r["coin"] for r in rows}
    assert "NEAR" in coins
    assert "ETH" in coins
    near = next(r for r in rows if r["coin"] == "NEAR")
    assert near["hasOpenPosition"] is True
    assert near["status"] == "open"
    assert near["excludedFromSelector"] is False
    assert near["entryBlocked"] is True
    assert near["entryBlockReason"] == "open_unrealized_negative"
    assert near["blockedSides"] == ["short"]
    assert near["statusDetail"] == "short_blocked"


def test_watchlist_shows_blacklisted_coin():
    hl_cfg = {
        "enabled": True,
        "pair_selector": {"blacklisted_coins": ["DOGE"]},
    }
    rows, _ = build_hyperliquid_watchlist(
        hl_cfg,
        {"DOGE": 0.1},
        [],
        hl_selected_pairs=[],
    )
    doge = next(r for r in rows if r["coin"] == "DOGE")
    assert doge["isBlacklisted"] is True
    assert doge["status"] == "blocked"
    assert doge["entryBlockReason"] == "blacklisted"


def test_global_halt_expands_to_all_liquid_candidates():
    hl_cfg = {
        "enabled": True,
        "max_open_positions": 1,
        "pair_selector": {"blacklisted_coins": []},
    }
    open_trades = [{"coin": "ETH", "unrealized_pnl": 1.0}]
    rows, gblock = build_hyperliquid_watchlist(
        hl_cfg,
        {"ETH": 1.0, "NEAR": 2.0, "SOL": 100.0},
        open_trades,
        hl_selected_pairs=["ETH/USD-PERP"],
        trading_status={"status": "running"},
        liquid_candidate_coins=["ETH", "NEAR", "SOL"],
    )
    assert gblock["entryBlockReason"] == "max_open_positions"
    coins = {r["coin"] for r in rows}
    assert coins == {"ETH", "NEAR", "SOL"}
    for row in rows:
        assert row["globalBlockApplied"] is True
        assert row["entryBlockReason"] == "max_open_positions"


def test_recent_loss_cooldown_block():
    now = datetime.utcnow()
    closed = [
        {
            "coin": "SOL",
            "status": "CLOSED",
            "realized_pnl": -1.0,
            "exit_time": (now - timedelta(hours=1)).isoformat() + "+00:00",
        }
    ]
    block = coin_entry_block("SOL", [], closed, realized_block_hours=4.0, now=now)
    assert block["entryBlocked"] is True
    assert block["entryBlockReason"] == "recent_negative_realized"


def test_coin_side_entry_block_recent_long_loss_allows_short():
    now = datetime.utcnow()
    closed = [
        {
            "coin": "SOL",
            "status": "CLOSED",
            "position_side": "long",
            "realized_pnl": -1.0,
            "exit_time": (now - timedelta(hours=1)).isoformat() + "+00:00",
        }
    ]

    long_block = coin_side_entry_block("SOL", "long", [], closed, realized_block_hours=4.0, now=now)
    short_block = coin_side_entry_block("SOL", "short", [], closed, realized_block_hours=4.0, now=now)

    assert long_block["entryBlocked"] is True
    assert long_block["entryBlockReason"] == "recent_negative_realized"
    assert short_block["entryBlocked"] is False


def test_watchlist_side_only_block_stays_under_analysis():
    now = datetime.utcnow()
    hl_cfg = {
        "enabled": True,
        "block_after_negative_realized_hours": 4.0,
        "pair_selector": {"blacklisted_coins": []},
    }
    rows, _ = build_hyperliquid_watchlist(
        hl_cfg,
        {"SOL": 100.0},
        [],
        closed_trades=[
            {
                "coin": "SOL",
                "status": "CLOSED",
                "position_side": "long",
                "realized_pnl": -1.0,
                "exit_time": (now - timedelta(hours=1)).isoformat() + "+00:00",
            }
        ],
        hl_selected_pairs=["SOL/USD-PERP"],
    )
    sol = next(r for r in rows if r["coin"] == "SOL")

    assert sol["status"] == "under_analysis"
    assert sol["statusDetail"] == "long_blocked"
    assert sol["blockedSides"] == ["long"]
    assert sol["entryBlockBySide"]["long"]["entryBlocked"] is True
    assert sol["entryBlockBySide"]["short"]["entryBlocked"] is False


def test_watchlist_both_side_block_is_blocked():
    now = datetime.utcnow()
    hl_cfg = {
        "enabled": True,
        "block_after_negative_realized_hours": 4.0,
        "pair_selector": {"blacklisted_coins": []},
    }
    rows, _ = build_hyperliquid_watchlist(
        hl_cfg,
        {"SOL": 100.0},
        [],
        closed_trades=[
            {
                "coin": "SOL",
                "status": "CLOSED",
                "position_side": "long",
                "realized_pnl": -1.0,
                "exit_time": (now - timedelta(hours=1)).isoformat() + "+00:00",
            },
            {
                "coin": "SOL",
                "status": "CLOSED",
                "position_side": "short",
                "realized_pnl": -1.0,
                "exit_time": (now - timedelta(hours=1)).isoformat() + "+00:00",
            },
        ],
        hl_selected_pairs=["SOL/USD-PERP"],
    )
    sol = next(r for r in rows if r["coin"] == "SOL")

    assert sol["status"] == "blocked"
    assert sol["statusDetail"] == "both_blocked"
    assert sol["blockedSides"] == ["long", "short"]


def test_watchlist_marks_hip3_tradfi_and_native_spx_crypto():
    hl_cfg = {
        "enabled": True,
        "pair_selector": {
            "tradfi": {
                "enabled": True,
                "dexes": ["xyz"],
                "categories": {"indices": ["SP500"]},
            }
        },
    }
    rows, _ = build_hyperliquid_watchlist(
        hl_cfg,
        {"SPX": 0.33, "xyz:SP500": 7600.0},
        [],
        hl_selected_pairs=["SPX/USD-PERP", "xyz:SP500/USD-PERP"],
    )

    spx = next(r for r in rows if r["coin"] == "SPX")
    sp500 = next(r for r in rows if r["coin"] == "xyz:SP500")
    assert spx["assetClass"] == "crypto"
    assert spx["assetCategory"] == "crypto"
    assert sp500["assetClass"] == "tradfi"
    assert sp500["assetCategory"] == "indices"
    assert sp500["displayCoin"] == "SP500"
    assert sp500["displayPair"] == "SP500/USD-PERP"


def test_trading_stopped_global_block():
    g = global_entry_block(
        {"enabled": True},
        trading_status={"status": "stopped"},
    )
    assert g["entryBlocked"] is True
    assert g["entryBlockReason"] == "trading_stopped"
