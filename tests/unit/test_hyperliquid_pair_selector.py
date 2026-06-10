import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ORCH = os.path.join(ROOT, "services", "orchestrator-service")
if ORCH not in sys.path:
    sys.path.insert(0, ORCH)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from hyperliquid_pair_selector import (  # noqa: E402
    _merge_selector_config,
    impact_spread_pct,
    select_top_hyperliquid_perps,
)
from core.pair_filters import DEFAULT_STABLECOIN_BASES  # noqa: E402


def test_impact_spread_pct_from_impact_prices():
    ctx = {"midPx": 100.0, "impactPxs": ["99.95", "100.05"]}
    spread = impact_spread_pct(ctx)
    assert spread == pytest.approx(0.1, rel=1e-3)


def test_impact_spread_pct_missing_data_returns_high():
    assert impact_spread_pct({}) >= 999.0


def test_merge_selector_config_uses_global_volume_when_hl_omits_min_vol():
    hl_cfg = {"pair_selector": {"max_pairs": 20}}
    global_sel = {"selection_criteria": {"min_volume_24h": 2_500_000, "max_spread_percentage": 0.4}}
    merged = _merge_selector_config(hl_cfg, global_sel)
    assert merged["min_day_notional_volume"] == 2_500_000
    assert merged["max_impact_spread_pct"] == 0.4
    assert merged["max_pairs"] == 20
    assert merged["use_open_interest_filter"] is False


@pytest.mark.asyncio
async def test_select_top_hyperliquid_perps_filters_and_ranks(monkeypatch):
    universe = [
        {"name": "BTC"},
        {"name": "ETH"},
        {"name": "LOW"},
    ]
    ctxs = [
        {"dayNtlVlm": "50000000", "openInterest": "200000", "midPx": "100", "impactPxs": ["99.99", "100.01"]},
        {"dayNtlVlm": "10000000", "openInterest": "100000", "midPx": "50", "impactPxs": ["49.99", "50.01"]},
        {"dayNtlVlm": "100", "openInterest": "10", "midPx": "1", "impactPxs": ["0.99", "1.01"]},
    ]

    async def fake_fetch(_client=None, *, dexes=None):
        return universe, ctxs

    monkeypatch.setattr(
        "hyperliquid_pair_selector.fetch_hyperliquid_meta_and_ctxs",
        fake_fetch,
    )

    hl_cfg = {
        "pair_selector": {
            "enabled": True,
            "max_pairs": 2,
            "min_day_notional_volume": 1_000_000,
            "min_open_interest": 50_000,
            "max_impact_spread_pct": 0.5,
            "exclude_stablecoin_bases": True,
        },
    }
    result = await select_top_hyperliquid_perps(hl_cfg, {}, stable_bases=DEFAULT_STABLECOIN_BASES)
    assert result["selected_coins"] == ["BTC", "ETH"]
    assert result["selector"] == "hyperliquid_metaAndAssetCtxs"


@pytest.mark.asyncio
async def test_select_top_does_not_filter_high_volume_btc_by_base_open_interest(monkeypatch):
    universe = [{"name": "BTC"}, {"name": "ETH"}]
    ctxs = [
        {
            "dayNtlVlm": "7000000000",
            "openInterest": "33700",
            "midPx": "61084.5",
            "impactPxs": ["61084.0", "61085.0"],
        },
        {
            "dayNtlVlm": "2000000000",
            "openInterest": "700000",
            "midPx": "2000",
            "impactPxs": ["1999.9", "2000.1"],
        },
    ]

    async def fake_fetch(_client=None, *, dexes=None):
        return universe, ctxs

    monkeypatch.setattr(
        "hyperliquid_pair_selector.fetch_hyperliquid_meta_and_ctxs",
        fake_fetch,
    )

    hl_cfg = {
        "pair_selector": {
            "enabled": True,
            "max_pairs": 2,
            "min_day_notional_volume": 1_000_000,
            "min_open_interest": 50_000,
            "max_impact_spread_pct": 0.5,
            "exclude_stablecoin_bases": False,
        },
    }
    result = await select_top_hyperliquid_perps(hl_cfg, {}, stable_bases=frozenset())

    assert result["selected_coins"] == ["BTC", "ETH"]
    assert result["criteria"]["use_open_interest_filter"] is False


@pytest.mark.asyncio
async def test_select_top_can_enable_open_interest_filter_explicitly(monkeypatch):
    universe = [{"name": "BTC"}, {"name": "ETH"}]
    ctxs = [
        {"dayNtlVlm": "7000000000", "openInterest": "33700", "midPx": "61084.5", "impactPxs": ["61084.0", "61085.0"]},
        {"dayNtlVlm": "2000000000", "openInterest": "700000", "midPx": "2000", "impactPxs": ["1999.9", "2000.1"]},
    ]

    async def fake_fetch(_client=None, *, dexes=None):
        return universe, ctxs

    monkeypatch.setattr(
        "hyperliquid_pair_selector.fetch_hyperliquid_meta_and_ctxs",
        fake_fetch,
    )

    hl_cfg = {
        "pair_selector": {
            "enabled": True,
            "max_pairs": 2,
            "min_day_notional_volume": 1_000_000,
            "min_open_interest": 50_000,
            "use_open_interest_filter": True,
            "max_impact_spread_pct": 0.5,
            "exclude_stablecoin_bases": False,
        },
    }
    result = await select_top_hyperliquid_perps(hl_cfg, {}, stable_bases=frozenset())

    assert result["selected_coins"] == ["ETH"]
    assert result["criteria"]["use_open_interest_filter"] is True


@pytest.mark.asyncio
async def test_select_top_excludes_stablecoins(monkeypatch):
    universe = [{"name": "BTC"}, {"name": "USDC"}, {"name": "USDT"}]
    ctxs = [
        {"dayNtlVlm": "50000000", "openInterest": "200000", "midPx": "100", "impactPxs": ["99.99", "100.01"]},
        {"dayNtlVlm": "50000000", "openInterest": "200000", "midPx": "1", "impactPxs": ["0.99", "1.01"]},
        {"dayNtlVlm": "50000000", "openInterest": "200000", "midPx": "1", "impactPxs": ["0.99", "1.01"]},
    ]

    async def fake_fetch(_client=None, *, dexes=None):
        return universe, ctxs

    monkeypatch.setattr(
        "hyperliquid_pair_selector.fetch_hyperliquid_meta_and_ctxs",
        fake_fetch,
    )

    hl_cfg = {"pair_selector": {"enabled": True, "max_pairs": 20, "exclude_stablecoin_bases": True}}
    result = await select_top_hyperliquid_perps(hl_cfg, {}, stable_bases=DEFAULT_STABLECOIN_BASES)
    assert result["selected_coins"] == ["BTC"]


@pytest.mark.asyncio
async def test_select_top_caps_at_max_pairs(monkeypatch):
    universe = [{"name": f"COIN{i}"} for i in range(25)]
    ctxs = [
        {
            "dayNtlVlm": str(10_000_000 - i * 100_000),
            "openInterest": "100000",
            "midPx": "10",
            "impactPxs": ["9.99", "10.01"],
        }
        for i in range(25)
    ]

    async def fake_fetch(_client=None, *, dexes=None):
        return universe, ctxs

    monkeypatch.setattr(
        "hyperliquid_pair_selector.fetch_hyperliquid_meta_and_ctxs",
        fake_fetch,
    )

    hl_cfg = {"pair_selector": {"enabled": True, "max_pairs": 20, "exclude_stablecoin_bases": False}}
    result = await select_top_hyperliquid_perps(hl_cfg, {}, stable_bases=frozenset())
    assert len(result["selected_coins"]) == 20
    assert result["selected_coins"][0] == "COIN0"


@pytest.mark.asyncio
async def test_select_top_replaces_runtime_excluded_coins(monkeypatch):
    universe = [{"name": "BTC"}, {"name": "ETH"}, {"name": "SOL"}]
    ctxs = [
        {"dayNtlVlm": "50000000", "openInterest": "200000", "midPx": "100", "impactPxs": ["99.99", "100.01"]},
        {"dayNtlVlm": "40000000", "openInterest": "200000", "midPx": "50", "impactPxs": ["49.99", "50.01"]},
        {"dayNtlVlm": "30000000", "openInterest": "200000", "midPx": "40", "impactPxs": ["39.99", "40.01"]},
    ]

    async def fake_fetch(_client=None, *, dexes=None):
        return universe, ctxs

    monkeypatch.setattr(
        "hyperliquid_pair_selector.fetch_hyperliquid_meta_and_ctxs",
        fake_fetch,
    )

    hl_cfg = {
        "pair_selector": {
            "enabled": True,
            "max_pairs": 2,
            "min_day_notional_volume": 1_000_000,
            "min_open_interest": 50_000,
            "max_impact_spread_pct": 0.5,
            "exclude_stablecoin_bases": False,
        },
    }
    result = await select_top_hyperliquid_perps(
        hl_cfg,
        {},
        stable_bases=frozenset(),
        excluded_coins={"BTC"},
    )

    assert result["selected_coins"] == ["ETH", "SOL"]
    assert result["criteria"]["runtime_excluded_coins"] == ["BTC"]


@pytest.mark.asyncio
async def test_select_top_adds_tradfi_within_total_cap(monkeypatch):
    universe = [{"name": "BTC"}, {"name": "ETH"}, {"name": "SPX"}, {"name": "xyz:SP500"}]
    ctxs = [
        {"dayNtlVlm": "50000000", "openInterest": "200000", "midPx": "100", "impactPxs": ["99.99", "100.01"]},
        {"dayNtlVlm": "40000000", "openInterest": "200000", "midPx": "50", "impactPxs": ["49.99", "50.01"]},
        {"dayNtlVlm": "2000000", "openInterest": "100000", "midPx": "0.33", "impactPxs": ["0.329", "0.331"]},
        {"dayNtlVlm": "2000000", "openInterest": "100000", "midPx": "7600", "impactPxs": ["7599", "7601"]},
    ]

    async def fake_fetch(_client=None, *, dexes=None):
        return universe, ctxs

    monkeypatch.setattr(
        "hyperliquid_pair_selector.fetch_hyperliquid_meta_and_ctxs",
        fake_fetch,
    )

    hl_cfg = {
        "pair_selector": {
            "enabled": True,
            "max_pairs": 2,
            "min_day_notional_volume": 1_000_000,
            "min_open_interest": 50_000,
            "max_impact_spread_pct": 0.5,
            "exclude_stablecoin_bases": False,
            "tradfi": {
                "enabled": True,
                "dexes": ["xyz"],
                "categories": {"indices": ["SP500"]},
                "max_pairs": 2,
                "min_day_notional_volume": 1_000_000,
                "min_open_interest": 50_000,
                "max_impact_spread_pct": 1.0,
            },
        },
    }
    result = await select_top_hyperliquid_perps(hl_cfg, {}, stable_bases=frozenset())

    assert result["selected_coins"] == ["BTC", "xyz:SP500"]
    assert result["candidates"][0]["assetClass"] == "crypto"
    assert result["candidates"][1]["assetClass"] == "tradfi"
    assert result["candidates"][1]["assetCategory"] == "indices"
    assert result["candidates"][1]["displayCoin"] == "SP500"
    assert result["candidates"][1]["displayPair"] == "SP500/USD-PERP"
    assert result["criteria"]["tradfi"]["selected_coins"] == ["xyz:SP500"]
    assert result["criteria"]["tradfi"]["selected_display_coins"] == ["SP500"]


@pytest.mark.asyncio
async def test_select_top_tradfi_counts_against_max_pairs(monkeypatch):
    universe = [{"name": "BTC"}, {"name": "ETH"}, {"name": "xyz:SP500"}, {"name": "xyz:CL"}]
    ctxs = [
        {"dayNtlVlm": "50000000", "openInterest": "200000", "midPx": "100", "impactPxs": ["99.99", "100.01"]},
        {"dayNtlVlm": "40000000", "openInterest": "200000", "midPx": "50", "impactPxs": ["49.99", "50.01"]},
        {"dayNtlVlm": "3000000", "openInterest": "100000", "midPx": "7600", "impactPxs": ["7599", "7601"]},
        {"dayNtlVlm": "2000000", "openInterest": "100000", "midPx": "96", "impactPxs": ["95.9", "96.1"]},
    ]

    async def fake_fetch(_client=None, *, dexes=None):
        return universe, ctxs

    monkeypatch.setattr(
        "hyperliquid_pair_selector.fetch_hyperliquid_meta_and_ctxs",
        fake_fetch,
    )

    hl_cfg = {
        "pair_selector": {
            "enabled": True,
            "max_pairs": 2,
            "exclude_stablecoin_bases": False,
            "tradfi": {
                "enabled": True,
                "dexes": ["xyz"],
                "categories": {"indices": ["SP500"], "commodities": ["CL"]},
                "max_pairs": 2,
            },
        },
    }
    result = await select_top_hyperliquid_perps(hl_cfg, {}, stable_bases=frozenset())

    assert len(result["selected_coins"]) == 2
    assert result["selected_coins"] == ["xyz:SP500", "xyz:CL"]


@pytest.mark.asyncio
async def test_select_top_tradfi_disabled_removes_tradfi_symbols(monkeypatch):
    universe = [{"name": "BTC"}, {"name": "xyz:SP500"}]
    ctxs = [
        {"dayNtlVlm": "50000000", "openInterest": "200000", "midPx": "100", "impactPxs": ["99.99", "100.01"]},
        {"dayNtlVlm": "2000000", "openInterest": "100000", "midPx": "0.33", "impactPxs": ["0.329", "0.331"]},
    ]

    async def fake_fetch(_client=None, *, dexes=None):
        return universe, ctxs

    monkeypatch.setattr(
        "hyperliquid_pair_selector.fetch_hyperliquid_meta_and_ctxs",
        fake_fetch,
    )

    hl_cfg = {
        "pair_selector": {
            "enabled": True,
            "max_pairs": 20,
            "exclude_stablecoin_bases": False,
            "tradfi": {"enabled": False, "dexes": ["xyz"], "categories": {"indices": ["SP500"]}},
        },
    }
    result = await select_top_hyperliquid_perps(hl_cfg, {}, stable_bases=frozenset())

    assert result["selected_coins"] == ["BTC"]
    assert result["criteria"]["tradfi"]["selected_coins"] == []


@pytest.mark.asyncio
async def test_select_top_missing_tradfi_symbol_is_skipped(monkeypatch):
    universe = [{"name": "BTC"}, {"name": "ETH"}]
    ctxs = [
        {"dayNtlVlm": "50000000", "openInterest": "200000", "midPx": "100", "impactPxs": ["99.99", "100.01"]},
        {"dayNtlVlm": "40000000", "openInterest": "200000", "midPx": "50", "impactPxs": ["49.99", "50.01"]},
    ]

    async def fake_fetch(_client=None, *, dexes=None):
        return universe, ctxs

    monkeypatch.setattr(
        "hyperliquid_pair_selector.fetch_hyperliquid_meta_and_ctxs",
        fake_fetch,
    )

    hl_cfg = {
        "pair_selector": {
            "enabled": True,
            "max_pairs": 2,
            "exclude_stablecoin_bases": False,
            "tradfi": {
                "enabled": True,
                "dexes": ["xyz"],
                "categories": {"indices": ["SP500"]},
                "max_pairs": 2,
            },
        },
    }
    result = await select_top_hyperliquid_perps(hl_cfg, {}, stable_bases=frozenset())

    assert result["selected_coins"] == ["BTC", "ETH"]
    assert result["criteria"]["tradfi"]["selected_coins"] == []


@pytest.mark.asyncio
async def test_select_top_empty_api_returns_no_coins(monkeypatch):
    async def fake_fetch(_client=None, *, dexes=None):
        return [], []

    monkeypatch.setattr(
        "hyperliquid_pair_selector.fetch_hyperliquid_meta_and_ctxs",
        fake_fetch,
    )

    result = await select_top_hyperliquid_perps({"pair_selector": {"enabled": True}}, {})
    assert result["selected_coins"] == []
    assert result["selector"] == "fallback_empty"
