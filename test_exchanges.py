#!/usr/bin/env python3
"""
Test script to verify exchange data retrieval and strategy functionality
"""

import asyncio
import httpx
import json
from typing import Dict, List, Any

# Service URLs
EXCHANGE_SERVICE_URL = "http://localhost:8003"
STRATEGY_SERVICE_URL = "http://localhost:8004"
CONFIG_SERVICE_URL = "http://localhost:8001"

async def test_exchange_configuration():
    """Test exchange configuration loading"""
    print("=== Testing Exchange Configuration ===")
    
    async with httpx.AsyncClient() as client:
        # Get exchange configuration
        response = await client.get(f"{CONFIG_SERVICE_URL}/api/v1/config/exchanges")
        if response.status_code == 200:
            config = response.json()
            print("✓ Exchange configuration loaded successfully")
            for exchange, settings in config.items():
                base_currency = settings.get('base_currency', 'Unknown')
                print(f"  - {exchange}: {base_currency}")
        else:
            print(f"✗ Failed to load exchange configuration: {response.status_code}")
            return False
    
    return True

async def test_exchange_pairs():
    """Test trading pairs for each exchange"""
    print("\n=== Testing Trading Pairs ===")
    
    async with httpx.AsyncClient() as client:
        exchanges = ['binance', 'bybit', 'cryptocom']
        
        for exchange in exchanges:
            print(f"\nTesting {exchange}:")
            
            # Get pairs without specifying base currency (should use config)
            response = await client.get(f"{EXCHANGE_SERVICE_URL}/api/v1/market/pairs/{exchange}")
            if response.status_code == 200:
                data = response.json()
                pairs = data.get('pairs', [])
                total = data.get('total', 0)
                print(f"  ✓ Found {total} pairs")
                
                # Show first 5 pairs as examples
                if pairs:
                    print(f"  Sample pairs: {pairs[:5]}")
                    
                    # Test OHLCV for first pair
                    if pairs:
                        # Find a simple pair without colons for testing
                        test_pair = None
                        for pair in pairs[:10]:  # Check first 10 pairs
                            if ':' not in pair:
                                test_pair = pair
                                break
                        
                        if not test_pair:
                            test_pair = pairs[0]  # Fallback to first pair
                        
                        print(f"  Testing OHLCV for {test_pair}:")
                        
                        # Convert pair format for OHLCV endpoint (remove slashes and colons)
                        ohlcv_symbol = test_pair.replace('/', '').replace(':', '')
                        
                        ohlcv_response = await client.get(
                            f"{EXCHANGE_SERVICE_URL}/api/v1/market/ohlcv/{exchange}/{ohlcv_symbol}",
                            params={'timeframe': '1h', 'limit': 3}
                        )
                        
                        if ohlcv_response.status_code == 200:
                            ohlcv_data = ohlcv_response.json()
                            print(f"    ✓ OHLCV data retrieved: {len(ohlcv_data)} candles")
                            if ohlcv_data:
                                latest = ohlcv_data[0]
                                print(f"    Latest candle: {latest.get('timestamp')} - Close: {latest.get('close')}")
                        else:
                            print(f"    ✗ OHLCV failed: {ohlcv_response.status_code}")
                else:
                    print(f"  ✗ No pairs found")
            else:
                print(f"  ✗ Failed to get pairs: {response.status_code}")

async def test_strategy_analysis():
    """Test strategy analysis for each exchange"""
    print("\n=== Testing Strategy Analysis ===")
    
    async with httpx.AsyncClient() as client:
        # Test pairs for each exchange
        test_pairs = {
            'binance': 'BTCUSDC',
            'bybit': 'BTCUSDC', 
            'cryptocom': 'BTCUSD'  # Strategy service expects no slashes
        }
        
        for exchange, pair in test_pairs.items():
            print(f"\nTesting strategy analysis for {exchange} - {pair}:")
            
            # Test consensus signals
            response = await client.get(f"{STRATEGY_SERVICE_URL}/api/v1/signals/consensus/{exchange}/{pair}")
            
            if response.status_code == 200:
                data = response.json()
                consensus = data.get('consensus_signal', 'unknown')
                agreement = data.get('agreement_percentage', 0)
                strategies = data.get('participating_strategies', 0)
                
                print(f"  ✓ Strategy analysis successful")
                print(f"    Consensus: {consensus}")
                print(f"    Agreement: {agreement}%")
                print(f"    Participating strategies: {strategies}")
                
                # Show individual strategy signals
                signals = data.get('signals', [])
                if signals:
                    print(f"    Strategy signals:")
                    for signal in signals:
                        strategy_name = signal.get('strategy_name', 'unknown')
                        signal_type = signal.get('signal', 'unknown')
                        confidence = signal.get('confidence', 0)
                        print(f"      - {strategy_name}: {signal_type} (confidence: {confidence})")
            else:
                print(f"  ✗ Strategy analysis failed: {response.status_code}")
                if response.status_code == 404:
                    print(f"    Error: {response.json().get('detail', 'Unknown error')}")

async def test_exchange_health():
    """Test exchange health status"""
    print("\n=== Testing Exchange Health ===")
    
    async with httpx.AsyncClient() as client:
        exchanges = ['binance', 'bybit', 'cryptocom']
        
        for exchange in exchanges:
            print(f"\nTesting {exchange} health:")
            
            response = await client.get(f"{EXCHANGE_SERVICE_URL}/api/v1/exchanges/{exchange}/health")
            if response.status_code == 200:
                health = response.json()
                status = health.get('status', 'unknown')
                response_time = health.get('response_time', 0)
                error_count = health.get('error_count', 0)
                
                print(f"  Status: {status}")
                print(f"  Response time: {response_time:.3f}s")
                print(f"  Error count: {error_count}")
                
                if status == 'healthy':
                    print(f"  ✓ {exchange} is healthy")
                else:
                    print(f"  ⚠ {exchange} has issues")
            else:
                print(f"  ✗ Failed to get health: {response.status_code}")

async def main():
    """Run all tests"""
    print("Starting Exchange and Strategy Tests")
    print("=" * 50)
    
    try:
        # Test exchange configuration
        if not await test_exchange_configuration():
            print("Configuration test failed, stopping tests")
            return
        
        # Test trading pairs and OHLCV data
        await test_exchange_pairs()
        
        # Test strategy analysis
        await test_strategy_analysis()
        
        # Test exchange health
        await test_exchange_health()
        
        print("\n" + "=" * 50)
        print("All tests completed!")
        
    except Exception as e:
        print(f"Test failed with error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main()) 