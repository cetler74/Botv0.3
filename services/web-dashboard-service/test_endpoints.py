#!/usr/bin/env python3
import asyncio
import httpx
import json

async def test_endpoints():
    endpoints = [
        ("database", "http://trading-bot-database:8002/api/v1/trades?limit=10000"),
        ("orchestrator", "http://trading-bot-orchestrator:8005/api/v1/trading/status"),
        ("config", "http://trading-bot-config:8001/api/v1/config/all"),
        ("exchange", "http://trading-bot-exchange:8003/api/v1/account/balance/binance")
    ]
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        for name, url in endpoints.items():
            try:
                print(f"Testing {name}: {url}")
                response = await client.get(url)
                print(f"  ✅ {name}: {response.status_code}")
                if response.status_code == 200:
                    data = response.json()
                    if name == "database":
                        print(f"    Trades: {len(data.get('trades', []))}")
                    elif name == "orchestrator":
                        print(f"    Cycle count: {data.get('cycle_count', 'N/A')}")
                    elif name == "config":
                        print(f"    Config keys: {list(data.keys())}")
                    elif name == "exchange":
                        print(f"    Balance data: {list(data.keys()) if isinstance(data, dict) else 'N/A'}")
            except Exception as e:
                print(f"  ❌ {name}: {e}")

if __name__ == "__main__":
    asyncio.run(test_endpoints()) 