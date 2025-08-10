#!/usr/bin/env python3
"""
Close ACS Trades Script
=======================

This script closes all remaining ACS/USD trades since ACS is not a valid symbol on Crypto.com.
"""

import asyncio
import httpx
import json
from datetime import datetime, timezone

async def close_acs_trades():
    """Close all ACS trades"""
    
    # Get open trades
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get("http://localhost:8002/api/v1/trades/open")
        data = response.json()
        open_trades = data.get('trades', [])
    
    # Find ACS trades
    acs_trades = [t for t in open_trades if 'ACS' in t.get('pair', '')]
    print(f"Found {len(acs_trades)} ACS trades to close")
    
    if not acs_trades:
        print("No ACS trades found")
        return
    
    # Close each ACS trade
    closed_count = 0
    async with httpx.AsyncClient(timeout=10.0) as client:
        for trade in acs_trades:
            trade_id = trade.get('trade_id')
            pair = trade.get('pair')
            position_size = trade.get('position_size')
            
            print(f"Closing trade {trade_id}: {pair} (size: {position_size})")
            
            try:
                response = await client.put(
                    f"http://localhost:8002/api/v1/trades/{trade_id}",
                    json={
                        "status": "CLOSED",
                        "exit_reason": "token_delisted_exchange",
                        "exit_price": 0.0,
                        "exit_time": datetime.now(timezone.utc).isoformat(),
                        "realized_pnl": -100.0
                    }
                )
                
                if response.status_code == 200:
                    print(f"✅ Closed trade {trade_id}")
                    closed_count += 1
                else:
                    print(f"❌ Failed to close trade {trade_id}: {response.status_code}")
                    
            except Exception as e:
                print(f"❌ Error closing trade {trade_id}: {e}")
    
    print(f"\n✅ Successfully closed {closed_count}/{len(acs_trades)} ACS trades")

if __name__ == "__main__":
    asyncio.run(close_acs_trades())
