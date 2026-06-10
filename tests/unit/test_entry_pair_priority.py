"""Tests for spot entry pair prioritization."""

from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ORCH = os.path.join(ROOT, "services", "orchestrator-service")
if ORCH not in sys.path:
    sys.path.insert(0, ORCH)

from entry_pair_priority import (  # noqa: E402
    fetch_priority_entry_pairs,
    order_pairs_by_priority,
)


def test_order_pairs_by_priority_stable():
    pairs = ["SOL/USDC", "AAVE/USDC", "BTC/USDC", "NEAR/USDC"]
    ordered = order_pairs_by_priority(pairs, {"AAVE/USDC", "NEAR/USDC"})
    assert ordered[:2] == ["AAVE/USDC", "NEAR/USDC"]
    assert ordered[2:] == ["SOL/USDC", "BTC/USDC"]


def test_order_pairs_by_priority_empty():
    pairs = ["SOL/USDC", "BTC/USDC"]
    assert order_pairs_by_priority(pairs, set()) == pairs


@pytest.mark.asyncio
async def test_fetch_priority_entry_pairs_filters_buy(monkeypatch):
    import entry_pair_priority as mod

    class FakeResp:
        status_code = 200

        def json(self):
            return {
                "rows": [
                    {"symbol": "AAVE/USDC", "signal": "buy"},
                    {"symbol": "SOL/USDC", "signal": "hold"},
                ]
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, params=None):
            return FakeResp()

    monkeypatch.setattr(mod.httpx, "AsyncClient", lambda timeout=8.0: FakeClient())
    result = await fetch_priority_entry_pairs(
        "http://database:8002",
        "binance",
        {"enabled": True, "audit_endpoints": ["ema50-breakout-pullback-analysis-logs"]},
    )
    assert result == {"AAVE/USDC"}
