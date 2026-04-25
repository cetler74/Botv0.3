#!/usr/bin/env python3
"""
Simple test to verify basic pair selection without complex analysis
"""

import asyncio
import httpx
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

async def test_simple_selection():
    """Test simple pair selection using config values"""
    
    print("🚀 Testing Simple Pair Selection")
    print("=" * 50)
    
    try:
        # Test config service
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Get exchange configs
            response = await client.get('http://localhost:8001/api/v1/config/all')
            if response.status_code == 200:
                config_data = response.json()
                exchanges = config_data.get('exchanges', {})
                
                print("✅ Exchange configurations:")
                for exchange, config in exchanges.items():
                    max_pairs = config.get('max_pairs', 0)
                    base_currency = config.get('base_currency', 'USDC')
                    print(f"  {exchange.upper()}: {max_pairs} pairs, base: {base_currency}")
                
                # Test database current state
                print("\n📊 Current database selections:")
                for exchange in exchanges.keys():
                    db_response = await client.get(f'http://localhost:8002/api/v1/pairs/{exchange}')
                    if db_response.status_code == 200:
                        db_data = db_response.json()
                        current_pairs = db_data.get('pairs', [])
                        print(f"  {exchange.upper()}: {len(current_pairs)} pairs")
                    else:
                        print(f"  {exchange.upper()}: Error getting pairs")
                
                print("\n✅ Configuration test completed successfully!")
                
            else:
                print(f"❌ Error getting config: {response.status_code}")
                
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_simple_selection())
