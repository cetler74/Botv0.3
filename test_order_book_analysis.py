#!/usr/bin/env python3
"""
Test Order Book Analysis for Pair Selection
"""

import asyncio
import httpx
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.order_book_analyzer import OrderBookAnalyzer

class SimpleConfigManager:
    def __init__(self):
        self.config_service_url = 'http://localhost:8001'
    
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

async def test_order_book_analysis():
    """Test order book analysis for major pairs"""
    
    print("🔍 Testing Order Book Analysis for Scalping")
    print("=" * 60)
    
    try:
        config_manager = SimpleConfigManager()
        analyzer = OrderBookAnalyzer(config_manager)
        
        # Test major pairs on Binance
        test_pairs = [
            "BTC/USDC",
            "ETH/USDC", 
            "BNB/USDC",
            "XRP/USDC",
            "SOL/USDC"
        ]
        
        print(f"Analyzing {len(test_pairs)} major pairs on Binance...")
        print()
        
        for symbol in test_pairs:
            print(f"📊 Analyzing {symbol}...")
            
            try:
                metrics = await analyzer.analyze_order_book("binance", symbol)
                
                if metrics:
                    print(f"  ✅ Spread: {metrics.spread_percentage:.3f}%")
                    print(f"  📈 Depth at 0.1%: {metrics.depth_at_0_1pct:,.0f}")
                    print(f"  📊 Order Book Levels: {metrics.depth_levels}")
                    print(f"  ⚖️  Imbalance: {metrics.order_book_imbalance:.2f}")
                    print(f"  💰 Slippage ($1000): {metrics.slippage_1000_usd:.3f}%")
                    print(f"  🎯 Scalping Score: {metrics.scalping_suitability_score:.1f}/100")
                    print(f"  ✅ Suitable: {'Yes' if metrics.is_suitable_for_scalping else 'No'}")
                    
                    if metrics.suitability_reasons:
                        print(f"  💡 Reasons: {', '.join(metrics.suitability_reasons[:2])}")
                    if metrics.risk_factors:
                        print(f"  ⚠️  Risks: {', '.join(metrics.risk_factors[:2])}")
                else:
                    print(f"  ❌ Failed to analyze {symbol}")
                
                print()
                
            except Exception as e:
                print(f"  ❌ Error analyzing {symbol}: {e}")
                print()
        
        print("🎯 Order Book Analysis Complete!")
        
    except Exception as e:
        print(f"❌ Test failed: {e}")

if __name__ == "__main__":
    asyncio.run(test_order_book_analysis())
