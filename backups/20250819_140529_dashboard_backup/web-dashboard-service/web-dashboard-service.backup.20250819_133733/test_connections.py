#!/usr/bin/env python3
import asyncio
import httpx

async def test_connections():
    services = {
        "database": "http://trading-bot-database:8002",
        "orchestrator": "http://trading-bot-orchestrator:8005", 
        "config": "http://trading-bot-config:8001",
        "exchange": "http://trading-bot-exchange:8003"
    }
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        for name, url in services.items():
            try:
                print(f"Testing {name}: {url}")
                response = await client.get(f"{url}/health")
                print(f"  ✅ {name}: {response.status_code}")
            except Exception as e:
                print(f"  ❌ {name}: {e}")

if __name__ == "__main__":
    asyncio.run(test_connections()) 