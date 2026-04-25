#!/usr/bin/env python3
"""
Simple Order Book-Based Pair Selector
Focuses on major pairs and handles API errors gracefully
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
    
    async def get_exchange_list(self):
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(f'{self.config_service_url}/api/v1/config/all')
                if response.status_code == 200:
                    config_data = response.json()
                    exchanges = config_data.get('exchanges', {})
                    return list(exchanges.keys())
                return []
            except Exception as e:
                print(f"Error getting exchange list: {e}")
                return []

class SimpleOrderBookSelector:
    """Simple order book-based pair selector focusing on major pairs"""
    
    def __init__(self, config_manager):
        self.config_manager = config_manager
        self.order_book_analyzer = OrderBookAnalyzer(config_manager)
        
        # Define major pairs to focus on (most liquid and stable)
        self.major_pairs = {
            'binance': [
                'BTC/USDC', 'ETH/USDC', 'BNB/USDC', 'XRP/USDC', 'SOL/USDC',
                'ADA/USDC', 'DOGE/USDC', 'TRX/USDC', 'LINK/USDC', 'LTC/USDC',
                'BCH/USDC', 'AVAX/USDC', 'DOT/USDC', 'MATIC/USDC', 'UNI/USDC',
                'ATOM/USDC', 'FIL/USDC', 'XLM/USDC', 'VET/USDC', 'ICP/USDC',
                'SHIB/USDC', 'NEAR/USDC', 'APT/USDC', 'OP/USDC', 'ARB/USDC',
                'SUI/USDC', 'PEPE/USDC', 'WLD/USDC', 'TIA/USDC', 'INJ/USDC'
            ],
            'bybit': [
                'BTC/USDC', 'ETH/USDC', 'SOL/USDC', 'XRP/USDC', 'USDE/USDC',
                'SUI/USDC', 'ARB/USDC', 'ADA/USDC', 'DOGE/USDC', 'AVAX/USDC',
                'ENA/USDC', 'WLD/USDC', 'LINK/USDC', 'LTC/USDC', 'TOWNS/USDC',
                'TIA/USDC', 'MNT/USDC', 'XLM/USDC', 'EIGEN/USDC', 'SHIB/USDC'
            ],
            'cryptocom': [
                'BTC/USD', 'ETH/USD', 'SOL/USD', 'XRP/USD', 'CRO/USD',
                'DOGE/USD', 'LINK/USD', 'ADA/USD', 'ENA/USD', 'USDT/USD',
                'HBAR/USD', 'WLFI/USD', 'SHIB/USD', 'BCH/USD', 'LTC/USD',
                'SUI/USD', 'DOT/USD', 'MANEKI/USD', 'AVAX/USD', 'XLM/USD'
            ]
        }
    
    async def analyze_major_pairs(self, exchange_name: str) -> list:
        """Analyze order book quality for major pairs only"""
        print(f"🔍 Analyzing major pairs for {exchange_name.upper()}...")
        
        major_pairs = self.major_pairs.get(exchange_name.lower(), [])
        if not major_pairs:
            print(f"❌ No major pairs defined for {exchange_name}")
            return []
        
        print(f"📊 Analyzing {len(major_pairs)} major pairs...")
        
        pair_scores = []
        analyzed_count = 0
        
        for symbol in major_pairs:
            try:
                print(f"  Analyzing {symbol}... ({analyzed_count + 1}/{len(major_pairs)})")
                
                metrics = await self.order_book_analyzer.analyze_order_book(exchange_name, symbol)
                
                if metrics:
                    pair_scores.append((symbol, metrics.scalping_suitability_score))
                    print(f"    ✅ Score: {metrics.scalping_suitability_score:.1f}/100")
                    
                    if metrics.is_suitable_for_scalping:
                        print(f"    🎯 Suitable for scalping!")
                    else:
                        print(f"    ⚠️  Not suitable for scalping")
                else:
                    print(f"    ❌ Failed to analyze")
                
                analyzed_count += 1
                
                # Add delay to avoid rate limiting
                await asyncio.sleep(0.2)
                
            except Exception as e:
                print(f"    ❌ Error analyzing {symbol}: {e}")
                analyzed_count += 1
                continue
        
        # Sort by score (highest first)
        pair_scores.sort(key=lambda x: x[1], reverse=True)
        
        return pair_scores
    
    async def select_top_pairs(self, exchange_name: str, max_pairs: int) -> list:
        """Select top pairs based on order book quality"""
        pair_scores = await self.analyze_major_pairs(exchange_name)
        
        if not pair_scores:
            print(f"❌ No pairs analyzed for {exchange_name}")
            return []
        
        # Select top pairs
        selected_pairs = [pair[0] for pair in pair_scores[:max_pairs]]
        
        print(f"✅ Selected {len(selected_pairs)} pairs based on order book quality:")
        for i, (symbol, score) in enumerate(pair_scores[:max_pairs]):
            print(f"  {i+1:2d}. {symbol:<15} - Score: {score:5.1f}/100")
        
        return selected_pairs
    
    async def update_database(self, exchange_name: str, selected_pairs: list):
        """Update database with selected pairs"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f'http://localhost:8002/api/v1/pairs/{exchange_name}',
                    json=selected_pairs
                )
                
                if response.status_code == 200:
                    print(f"✅ Database updated with {len(selected_pairs)} pairs for {exchange_name}")
                    return True
                else:
                    print(f"❌ Failed to update database: {response.status_code}")
                    return False
                    
        except Exception as e:
            print(f"❌ Error updating database: {e}")
            return False

async def main():
    """Main function to run simple order book-based pair selection"""
    print("🚀 Simple Order Book-Based Pair Selection")
    print("=" * 60)
    
    try:
        config_manager = SimpleConfigManager()
        selector = SimpleOrderBookSelector(config_manager)
        
        # Get exchanges from config
        exchanges = await config_manager.get_exchange_list()
        print(f"Found exchanges: {exchanges}")
        
        for exchange in exchanges:
            print(f"\n{'='*20} {exchange.upper()} {'='*20}")
            
            try:
                # Get exchange configuration
                exchange_config = await config_manager.get_exchange_config(exchange)
                max_pairs = exchange_config.get('max_pairs', 20)
                
                print(f"Max pairs: {max_pairs}")
                
                # Select pairs based on order book quality
                selected_pairs = await selector.select_top_pairs(exchange, max_pairs)
                
                if selected_pairs:
                    # Update database
                    success = await selector.update_database(exchange, selected_pairs)
                    
                    if success:
                        print(f"🎯 Successfully updated {exchange.upper()} with {len(selected_pairs)} order book-optimized pairs")
                    else:
                        print(f"❌ Failed to update database for {exchange.upper()}")
                else:
                    print(f"❌ No suitable pairs found for {exchange.upper()}")
                
            except Exception as e:
                print(f"❌ Error processing {exchange}: {e}")
        
        print(f"\n🎉 Simple Order Book-Based Pair Selection Complete!")
        
    except Exception as e:
        print(f"❌ Main error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
