import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ORCH = os.path.join(ROOT, "services", "orchestrator-service")
if ORCH not in sys.path:
    sys.path.insert(0, ORCH)

from hyperliquid_pair_selector import (  # noqa: E402
    impact_spread_pct,
    select_top_hyperliquid_perps,
)


def test_impact_spread_pct_from_impact_prices():
    ctx = {"midPx": 100.0, "impactPxs": ["99.95", "100.05"]}
    spread = impact_spread_pct(ctx)
    assert spread == pytest.approx(0.1, rel=1e-3)


def test_impact_spread_pct_missing_data_returns_high():
    assert impact_spread_pct({}) >= 999.0


@pytest.mark.asyncio
async def test_select_top_hyperliquid_perps_filters_and_ranks(monkeypatch):
    universe = [
        {"name": "BTC"},
        {"name": "ETH"},
        {"name": "LOW"},
    ]
    ctxs = [
        {"dayNtlVlm": "50000000", "openInterest": "200000", "midPx": "100", "impactPxs": ["100.01", "99.99"]},
        {"dayNtlVlm": "10000000", "openInterest": "100000", "midPx": "50", "impactPxs": ["50.01", "49.99"]},
        {"dayNtlVlm": "100", "openInterest": "10", "midPx": "1", "impactPxs": ["1.01", "0.99"]},
    ]

    async def fake_fetch(_client=None):
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
        },
        "allowed_symbols": [],
    }
    result = await select_top_hyperliquid_perps(hl_cfg, {})
    assert result["selected_coins"] == ["BTC", "ETH"]
    assert result["selector"] == "hyperliquid_metaAndAssetCtxs"


@pytest.mark.asyncio
async def test_select_top_respects_allowed_symbols_cap(monkeypatch):
    universe = [{"name": "BTC"}, {"name": "ETH"}]
    ctxs = [
        {"dayNtlVlm": "50000000", "openInterest": "200000", "midPx": "100", "impactPxs": ["100.01", "99.99"]},
        {"dayNtlVlm": "40000000", "openInterest": "150000", "midPx": "50", "impactPxs": ["50.01", "49.99"]},
    ]

    async def fake_fetch(_client=None):
        return universe, ctxs

    monkeypatch.setattr(
        "hyperliquid_pair_selector.fetch_hyperliquid_meta_and_ctxs",
        fake_fetch,
    )

    hl_cfg = {
        "pair_selector": {"enabled": True, "max_pairs": 5},
        "allowed_symbols": ["ETH"],
    }
    result = await select_top_hyperliquid_perps(hl_cfg, {})
    assert result["selected_coins"] == ["ETH"]
