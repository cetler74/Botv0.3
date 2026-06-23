"""Contract tests for Hyperliquid mids rate-limit protection."""

from __future__ import annotations

from pathlib import Path


def test_hyperliquid_mids_fetch_is_cached_and_coalesced():
    source = (
        Path(__file__).resolve().parents[2]
        / "services/orchestrator-service/main.py"
    ).read_text(encoding="utf-8")
    assert "_hyperliquid_mids_cache" in source
    assert "_hyperliquid_mids_fetch_task" in source
    assert "_hyperliquid_mids_min_interval_seconds" in source
    assert "_fetch_hyperliquid_mids_uncached" in source
    assert "_hyperliquid_mids_ws_loop" in source
    assert "_ensure_hyperliquid_mids_ws_task" in source
    assert "_hyperliquid_mids_status" in source
    assert '"method": "subscribe"' in source
    assert '"type": "allMids"' in source
    assert "websocketConnected" in source
    assert "websocket-first cache and REST fallback" in source
    assert "Joining in-flight Hyperliquid mids fetch" in source
    assert "Falling back to cached Hyperliquid mids" in source
    assert "Hyperliquid mids rate-limited; using cached mids if available" in source
    assert source.count('"https://api.hyperliquid.xyz/info"') == 1
