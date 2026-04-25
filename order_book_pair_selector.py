#!/usr/bin/env python3
"""
Order Book-Based Pair Selector
Selects pairs based on order book quality for scalping
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

class OrderBookPairSelector:
    """Pair selector based on order book quality"""
    
    def __init__(self, config_manager):
        self.config_manager = config_manager
        self.order_book_analyzer = OrderBookAnalyzer(config_manager)
    
    async def get_candidate_pairs(self, exchange_name: str, base_currency: str) -> list:
        """Get candidate pairs from exchange"""
        try:
            import ccxt.async_support as ccxt_async
            
            exchange_config = await self.config_manager.get_exchange_config(exchange_name)
            exchange_class = getattr(ccxt_async, exchange_name.lower())
            exchange = exchange_class({
                'apiKey': exchange_config.get('api_key', ''),
                'secret': exchange_config.get('secret', ''),
                'enableRateLimit': True,
                'sandbox': exchange_config.get('sandbox', False)
            })
            
            await exchange.load_markets()
            
            # Filter pairs by base currency and basic criteria
            candidate_pairs = []
            for symbol, market in exchange.markets.items():
                if (market.get('active') and
                    market.get('quote') == base_currency and
                    market.get('type') == 'spot' and
                    not market.get('info', {}).get('isSpotTradingAllowed', True) == False):
                    candidate_pairs.append(symbol)
            
            await exchange.close()
            return candidate_pairs
            
        except Exception as e:
            print(f"Error getting candidate pairs for {exchange_name}: {e}")
            return []
    
    async def select_pairs_by_order_book_quality(self, exchange_name: str, base_currency: str, max_pairs: int) -> list:
        """Select pairs based on order book quality"""
        print(f"🔍 Selecting {max_pairs} pairs for {exchange_name.upper()} based on order book quality...")
        
        # Get candidate pairs
        candidate_pairs = await self.get_candidate_pairs(exchange_name, base_currency)
        print(f"Found {len(candidate_pairs)} candidate pairs")
        
        if not candidate_pairs:
            print(f"❌ No candidate pairs found for {exchange_name}")
            return []
        
        # Analyze order book quality for each pair
        print(f"📊 Analyzing order book quality for {len(candidate_pairs)} pairs...")
        
        pair_scores = []
        analyzed_count = 0
        
        for symbol in candidate_pairs:
            try:
                print(f"  Analyzing {symbol}... ({analyzed_count + 1}/{len(candidate_pairs)})")
                
                metrics = await self.order_book_analyzer.analyze_order_book(exchange_name, symbol)
                
                if metrics and metrics.is_suitable_for_scalping:
                    pair_scores.append((symbol, metrics.scalping_suitability_score))
                    print(f"    ✅ Score: {metrics.scalping_suitability_score:.1f}/100")
                elif metrics:
                    print(f"    ⚠️  Score: {metrics.scalping_suitability_score:.1f}/100 (not suitable)")
                else:
                    print(f"    ❌ Failed to analyze")
                
                analyzed_count += 1
                
                # Add small delay to avoid rate limiting
                await asyncio.sleep(0.1)
                
            except Exception as e:
                print(f"    ❌ Error analyzing {symbol}: {e}")
                analyzed_count += 1
                continue
        
        # Sort by score (highest first)
        pair_scores.sort(key=lambda x: x[1], reverse=True)
        
        # Select top pairs
        selected_pairs = [pair[0] for pair in pair_scores[:max_pairs]]
        
        print(f"✅ Selected {len(selected_pairs)} pairs based on order book quality:")
        for i, (symbol, score) in enumerate(pair_scores[:max_pairs]):
            print(f"  {i+1:2d}. {symbol:<15} - Score: {score:5.1f}/100")
        
        return selected_pairs
    
    async def update_database_with_order_book_selection(self, exchange_name: str, selected_pairs: list):
        """Update database with order book-based selection"""
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
    """Main function to run order book-based pair selection"""
    print("🚀 Order Book-Based Pair Selection")
    print("=" * 60)
    
    try:
        config_manager = SimpleConfigManager()
        selector = OrderBookPairSelector(config_manager)
        
        # Get exchanges from config
        exchanges = await config_manager.get_exchange_list()
        print(f"Found exchanges: {exchanges}")
        
        for exchange in exchanges:
            print(f"\n{'='*20} {exchange.upper()} {'='*20}")
            
            try:
                # Get exchange configuration
                exchange_config = await config_manager.get_exchange_config(exchange)
                base_currency = exchange_config.get('base_currency', 'USDC')
                max_pairs = exchange_config.get('max_pairs', 20)
                
                print(f"Base currency: {base_currency}")
                print(f"Max pairs: {max_pairs}")
                
                # Select pairs based on order book quality
                selected_pairs = await selector.select_pairs_by_order_book_quality(
                    exchange, base_currency, max_pairs
                )
                
                if selected_pairs:
                    # Update database
                    success = await selector.update_database_with_order_book_selection(
                        exchange, selected_pairs
                    )
                    
                    if success:
                        print(f"🎯 Successfully updated {exchange.upper()} with {len(selected_pairs)} order book-optimized pairs")
                    else:
                        print(f"❌ Failed to update database for {exchange.upper()}")
                else:
                    print(f"❌ No suitable pairs found for {exchange.upper()}")
                
            except Exception as e:
                print(f"❌ Error processing {exchange}: {e}")
        
        print(f"\n🎉 Order Book-Based Pair Selection Complete!")
        
    except Exception as e:
        print(f"❌ Main error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
