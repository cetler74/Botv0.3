#!/usr/bin/env python3
"""
Fix Missing Crypto.com Trades Utility
Record the 6 successful CRO/USD trades that were filled on Crypto.com but never recorded in database
"""

import asyncio
import httpx
import uuid
from datetime import datetime

DATABASE_SERVICE_URL = "http://localhost:8002"

# The 6 successful CRO/USD trades from exchange logs (most recent first)
MISSING_TRADES = [
    {
        'order_id': '6530219580564414053',
        'pair': 'CRO/USD',
        'exchange': 'cryptocom', 
        'filled_amount': 372.0,
        'filled_price': 0.13420,
        'timestamp': '2025-07-26 10:59:09 UTC',
        'value': 52.41852
    },
    {
        'order_id': '6530219579897906090', 
        'pair': 'CRO/USD',
        'exchange': 'cryptocom',
        'filled_amount': 371.0,
        'filled_price': 0.13443,
        'timestamp': '2025-07-26 10:57:23 UTC',
        'value': 52.36665
    },
    {
        'order_id': '6530219579181881696',
        'pair': 'CRO/USD', 
        'exchange': 'cryptocom',
        'filled_amount': 372.0,
        'filled_price': 0.13436,
        'timestamp': '2025-07-26 10:54:22 UTC',
        'value': 52.47804
    },
    {
        'order_id': '6530219578549372264',
        'pair': 'CRO/USD',
        'exchange': 'cryptocom',
        'filled_amount': 372.0, 
        'filled_price': 0.13422,
        'timestamp': '2025-07-26 10:52:36 UTC',
        'value': 52.42596
    },
    {
        'order_id': '6530219577654304145',
        'pair': 'CRO/USD',
        'exchange': 'cryptocom',
        'filled_amount': 371.0,
        'filled_price': 0.13477,
        'timestamp': '2025-07-26 10:49:33 UTC', 
        'value': 52.4965
    },
    {
        'order_id': '6530219581931676949',
        'pair': 'CRO/USD',
        'exchange': 'cryptocom',
        'filled_amount': 370.75485689,
        'filled_price': 0.13487,
        'timestamp': '2025-07-26 10:47:44 UTC',
        'value': 52.3846
    }
]

async def record_missing_trades():
    """Record the missing CRO/USD trades in the database"""
    print("🔧 RECORDING MISSING CRYPTO.COM TRADES")
    print("=" * 50)
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            recorded_count = 0
            
            for trade in MISSING_TRADES:
                print(f"📝 Recording trade {trade['order_id'][-8:]}...")
                print(f"   Amount: {trade['filled_amount']} CRO at ${trade['filled_price']:.5f}")
                print(f"   Value: ${trade['value']:.2f} | Time: {trade['timestamp']}")
                
                # Convert timestamp to ISO format
                # Convert from "2025-07-26 10:59:09 UTC" to datetime
                dt = datetime.fromisoformat(trade['timestamp'].replace(' UTC', '+00:00'))
                
                # Create trade record
                trade_id = str(uuid.uuid4())
                trade_data = {
                    'trade_id': trade_id,
                    'pair': trade['pair'],
                    'exchange': trade['exchange'],
                    'status': 'OPEN',  # Record as OPEN since they just filled
                    'position_size': trade['filled_amount'],
                    'entry_price': trade['filled_price'],
                    'entry_time': dt.isoformat(),
                    'entry_id': trade['order_id'],
                    'fees': 0.0,  # Estimate fees later
                    'strategy': 'vwma_hull',  # Based on orchestrator logs showing this strategy
                    'entry_reason': 'vwma_hull strategy signal: buy (retroactive recording)',
                    'created_at': dt.isoformat(),
                    'updated_at': datetime.utcnow().isoformat()
                }
                
                # Record in database
                response = await client.post(f"{DATABASE_SERVICE_URL}/api/v1/trades", json=trade_data)
                
                if response.status_code in [200, 201]:
                    print(f"   ✅ Recorded as trade {trade_id[:8]}...")
                    recorded_count += 1
                else:
                    print(f"   ❌ Failed to record: {response.status_code} - {response.text}")
            
            print(f"\\n🎉 MISSING TRADES RECORDING COMPLETE: {recorded_count}/{len(MISSING_TRADES)} trades recorded")
            
        except Exception as e:
            print(f"❌ Error recording missing trades: {e}")

async def main():
    print("🔄 This will record 6 missing CRO/USD trades that were filled on Crypto.com")
    print("but never recorded in the database due to the float(None) error.")
    print()
    
    confirmation = input("Type 'YES' to proceed: ")
    if confirmation != 'YES':
        print("❌ Operation cancelled")
        return
    
    await record_missing_trades()

if __name__ == "__main__":
    asyncio.run(main())