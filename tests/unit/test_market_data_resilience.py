import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
EXCHANGE = os.path.join(ROOT, "services", "exchange-service")
if EXCHANGE not in sys.path:
    sys.path.insert(0, EXCHANGE)

from core.hyperliquid_market import HyperliquidCandleCache
from cryptocom_connection_manager import CryptocomConnectionManager
from cryptocom_market_websocket import CryptocomMarketWebSocket


@pytest.mark.asyncio
async def test_hyperliquid_cache_coalesces_concurrent_requests():
    cache = HyperliquidCandleCache(min_request_interval_seconds=0)
    fetcher = AsyncMock(
        return_value=[
            {
                "timestamp": 1,
                "open": 1.0,
                "high": 2.0,
                "low": 0.5,
                "close": 1.5,
                "volume": 10.0,
            }
        ]
    )

    results = await asyncio.gather(
        *(cache.get("ETH", "1h", 100, fetcher=fetcher) for _ in range(8))
    )

    assert fetcher.await_count == 1
    assert all(rows[0]["close"] == 1.5 for rows in results)


@pytest.mark.asyncio
async def test_cryptocom_market_responds_to_server_heartbeat():
    stream = CryptocomMarketWebSocket()
    stream.websocket = AsyncMock()

    await stream._process_message(
        json.dumps({"id": 42, "method": "public/heartbeat"})
    )

    stream.websocket.send.assert_awaited_once()
    sent = json.loads(stream.websocket.send.await_args.args[0])
    assert sent == {"id": 42, "method": "public/respond-heartbeat"}


def test_cryptocom_connection_error_counter_does_not_recurse():
    manager = CryptocomConnectionManager("wss://example.invalid")

    manager._record_error()

    assert manager.connection_metrics["total_errors"] == 1


@pytest.mark.asyncio
async def test_cryptocom_user_stream_responds_to_server_heartbeat():
    manager = CryptocomConnectionManager("wss://example.invalid")
    manager.send_message = AsyncMock(return_value=True)

    handled = await manager._respond_to_server_heartbeat(
        {"id": 84, "method": "public/heartbeat"}
    )

    assert handled is True
    manager.send_message.assert_awaited_once_with(
        {"id": 84, "method": "public/respond-heartbeat"}
    )
