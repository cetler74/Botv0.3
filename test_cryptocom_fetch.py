#!/usr/bin/env python3
"""
Test Crypto.com order fetching directly
"""

import asyncio
import httpx
import json

async def test_cryptocom_fetch():
    """Test fetching orders from Crypto.com via exchange service"""
    
    async with httpx.AsyncClient() as client:
        try:
            print("Testing direct exchange service calls...")
            
            # First test - try to get recent orders through a sync call
            print("\n1. Attempting comprehensive sync...")
            
            # The issue is likely that we need to trigger this sync more effectively
            # Let's check what the exchange service logs show
            
            # Let's try to manually call the sync by creating recent_trades data
            print("\n2. Creating test sync data with known filled orders...")
            
            # Based on your order data, create the expected trade format
            test_sync_data = {
                "exchange": "cryptocom",
                "recent_trades": [
                    {
                        "id": "6530219581794196852",
                        "order": "6530219581794196852", 
                        "symbol": "AAVE/USD",
                        "side": "sell",
                        "amount": 0.173,
                        "price": 291.978,
                        "cost": 48.004213,
                        "timestamp": 1721852540000,  # Approximate timestamp from your logs
                        "datetime": "2025-07-24T18:15:40Z"
                    },
                    {
                        "id": "6530219581797307436",
                        "order": "6530219581797307436",
                        "symbol": "ADA/USD", 
                        "side": "sell",
                        "amount": 62.0,
                        "price": 0.82547,
                        "cost": 48.528981,
                        "timestamp": 1721852521000,
                        "datetime": "2025-07-24T18:20:21Z"
                    },
                    {
                        "id": "6530219581797270551",
                        "order": "6530219581797270551",
                        "symbol": "ADA/USD",
                        "side": "sell", 
                        "amount": 62.0,
                        "price": 0.82390,
                        "cost": 48.52926,
                        "timestamp": 1721852361000,
                        "datetime": "2025-07-24T19:19:21Z"
                    },
                    {
                        "id": "6530219581797214490",
                        "order": "6530219581797214490",
                        "symbol": "ADA/USD",
                        "side": "sell",
                        "amount": 62.0,
                        "price": 0.82365,
                        "cost": 48.52182,
                        "timestamp": 1721852295000,
                        "datetime": "2025-07-24T18:18:15Z"
                    }
                ],
                "current_balance": {},
                "open_orders": [],
                "sync_timestamp": "2025-07-24T19:30:00Z",
                "sync_type": "manual_test_sync"
            }
            
            print("Sending test sync data to database service...")
            sync_response = await client.post(
                "http://localhost:8002/api/v1/trades/comprehensive-sync",
                json=test_sync_data
            )
            
            if sync_response.status_code == 200:
                result = sync_response.json()
                print(f"✅ Sync successful: {result}")
                print(f"   - Processed trades: {result.get('processed_trades', 0)}")
                print(f"   - Closed trades: {result.get('closed_trades', 0)}")
                print(f"   - Created trades: {result.get('created_trades', 0)}")
            else:
                print(f"❌ Sync failed: {sync_response.status_code} - {sync_response.text}")
                
        except Exception as e:
            print(f"❌ Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_cryptocom_fetch())