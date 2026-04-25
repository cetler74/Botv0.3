#!/usr/bin/env python3
"""
Test Order Book Selection in Docker Container
"""

import asyncio
import httpx
import sys
import os
sys.path.append('/app')

from core.order_book_analyzer import OrderBookAnalyzer

class SimpleConfigManager:
    def __init__(self):
        self.config_service_url = 'http://config-service:8001'
    
    async def get_exchange_config(self, exchange_name: str):
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(f'{self.config_service_url}/api/v1/config/all')
                if response.status_code == 200:
                    config_data = response.json()
                    exchanges = config_data.get('exchanges', {})
                    return exchanges.get(exchange_name, {})
                return {}
            except Exception as e:
                print(f"Error getting config for {exchange_name}: {e}")
                return {}

async def test_docker_order_book():
    """Test order book analysis in Docker container"""
    
    print("🐳 Testing Order Book Analysis in Docker Container")
    print("=" * 60)
    
    try:
        config_manager = SimpleConfigManager()
        analyzer = OrderBookAnalyzer(config_manager)
        
        # Test a few pairs
        test_pairs = [
            ("binance", "VET/USDC"),
            ("bybit", "ARB/USDC"),
            ("cryptocom", "USDT/USD")
        ]
        
        for exchange, symbol in test_pairs:
            print(f"📊 Testing {symbol} on {exchange.upper()}...")
            
            try:
                metrics = await analyzer.analyze_order_book(exchange, symbol)
                
                if metrics:
                    print(f"  ✅ Success! Score: {metrics.scalping_suitability_score:.1f}/100")
                    print(f"  📈 Spread: {metrics.spread_percentage:.3f}%")
                    print(f"  📊 Depth at 0.1%: {metrics.depth_at_0_1pct:,.0f}")
                    print(f"  🎯 Suitable: {'Yes' if metrics.is_suitable_for_scalping else 'No'}")
                else:
                    print(f"  ❌ Failed to analyze")
                
            except Exception as e:
                print(f"  ❌ Error: {e}")
            
            print()
        
        print("🎯 Docker Order Book Test Complete!")
        
    except Exception as e:
        print(f"❌ Test failed: {e}")

if __name__ == "__main__":
    asyncio.run(test_docker_order_book())
