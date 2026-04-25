#!/usr/bin/env python3
"""
Force Order Book Selection
Ensures the orchestrator uses order book-based selections by updating the database
"""

import asyncio
import httpx
import sys
import os

async def force_order_book_selections():
    """
    Force the orchestrator to use order book-based selections
    """
    print("🎯 Forcing Order Book-Based Pair Selections")
    print("=" * 60)
    
    # Order book-based selections we created earlier
    order_book_selections = {
        'binance': ['VET/USDC', 'SHIB/USDC', 'SUI/USDC', 'XRP/USDC', 'XLM/USDC'],
        'bybit': ['ARB/USDC', 'DOGE/USDC', 'SHIB/USDC', 'USDE/USDC', 'ADA/USDC'],
        'cryptocom': ['USDT/USD', 'ADA/USD', 'XLM/USD', 'DOGE/USD', 'HBAR/USD']
    }
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        for exchange_name, pairs in order_book_selections.items():
            print(f"📊 Updating {exchange_name.upper()} with order book-based selections...")
            
            try:
                # Update the database with order book-based selections
                response = await client.post(
                    f"http://database-service:8002/api/v1/pairs/{exchange_name}",
                    json=pairs
                )
                
                if response.status_code in [200, 201]:
                    print(f"✅ Successfully updated {exchange_name} with {len(pairs)} order book-based pairs")
                    print(f"   Pairs: {pairs}")
                else:
                    print(f"❌ Failed to update {exchange_name}: {response.status_code}")
                    print(f"   Response: {response.text}")
                    
            except Exception as e:
                print(f"❌ Error updating {exchange_name}: {e}")
    
    print("\n🎯 Order Book-Based Selections Applied!")
    print("The orchestrator will now use these selections when it restarts.")

async def verify_selections():
    """
    Verify that the order book-based selections are in the database
    """
    print("\n🔍 Verifying Order Book-Based Selections")
    print("=" * 60)
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        for exchange_name in ['binance', 'bybit', 'cryptocom']:
            try:
                response = await client.get(f"http://database-service:8002/api/v1/pairs/{exchange_name}")
                
                if response.status_code == 200:
                    pairs = response.json().get('pairs', [])
                    print(f"✅ {exchange_name.upper()}: {len(pairs)} pairs")
                    print(f"   Pairs: {pairs[:5]}...")
                else:
                    print(f"❌ Failed to get pairs for {exchange_name}: {response.status_code}")
                    
            except Exception as e:
                print(f"❌ Error getting pairs for {exchange_name}: {e}")

async def main():
    """Main function"""
    await force_order_book_selections()
    await verify_selections()
    
    print("\n🚀 Next Steps:")
    print("1. The order book-based selections are now in the database")
    print("2. Restart the orchestrator to use these selections")
    print("3. The orchestrator will load these pairs instead of generating new ones")

if __name__ == "__main__":
    asyncio.run(main())
