#!/usr/bin/env python3
"""
Orchestrator Order Book Integration Patch
Replaces the legacy CCXT pair selector with order book-based selection
"""

import asyncio
import httpx
import sys
import os
sys.path.append('/app')

from core.order_book_analyzer import OrderBookAnalyzer
from core.dynamic_blacklist_manager import blacklist_manager

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

async def generate_order_book_based_pairs(exchange_name: str, max_pairs: int, base_currency: str, database_service_url: str):
    """
    Generate pairs using order book analysis instead of legacy CCXT selector
    """
    try:
        print(f"🎯 Generating order book-based pairs for {exchange_name} (max: {max_pairs}, base: {base_currency})")
        
        # Initialize order book analyzer
        config_manager = SimpleConfigManager()
        analyzer = OrderBookAnalyzer(config_manager)
        
        # Get active blacklisted pairs
        blacklisted_pairs = await blacklist_manager.get_active_blacklist()
        print(f"[Blacklist] Active blacklisted pairs for {exchange_name}: {blacklisted_pairs}")
        
        # Get candidate pairs from exchange
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                # Get all available pairs from exchange service
                response = await client.get(f"http://exchange-service:8003/api/v1/markets/{exchange_name}")
                if response.status_code == 200:
                    markets_data = response.json()
                    all_pairs = list(markets_data.keys())
                    print(f"📊 Found {len(all_pairs)} total pairs for {exchange_name}")
                else:
                    print(f"❌ Failed to get markets for {exchange_name}: {response.status_code}")
                    return []
            except Exception as e:
                print(f"❌ Error getting markets for {exchange_name}: {e}")
                return []
        
        # Filter pairs by base currency and exclude blacklisted
        candidate_pairs = []
        for pair in all_pairs:
            if pair.endswith(f"/{base_currency}") and pair not in blacklisted_pairs:
                candidate_pairs.append(pair)
        
        print(f"📋 {len(candidate_pairs)} candidate pairs after filtering for {base_currency}")
        
        if len(candidate_pairs) == 0:
            print(f"❌ No candidate pairs found for {exchange_name}")
            return []
        
        # Analyze order book for each candidate pair
        analyzed_pairs = []
        for pair in candidate_pairs[:50]:  # Limit to top 50 for performance
            try:
                metrics = await analyzer.analyze_order_book(exchange_name, pair)
                if metrics and metrics.is_suitable_for_scalping:
                    analyzed_pairs.append((pair, metrics.scalping_suitability_score))
                    print(f"✅ {pair}: {metrics.scalping_suitability_score:.1f}/100 (spread: {metrics.spread_percentage:.3f}%)")
                else:
                    print(f"❌ {pair}: Not suitable for scalping")
            except Exception as e:
                print(f"⚠️ Error analyzing {pair}: {e}")
                continue
        
        # Sort by scalping suitability score and take top pairs
        analyzed_pairs.sort(key=lambda x: x[1], reverse=True)
        selected_pairs = [pair for pair, score in analyzed_pairs[:max_pairs]]
        
        print(f"🎯 Selected {len(selected_pairs)} order book-optimized pairs for {exchange_name}")
        
        # Special handling for crypto.com - ensure CRO/USD is included
        if exchange_name.lower() == "cryptocom":
            if "CRO/USD" not in selected_pairs and "CRO/USD" not in blacklisted_pairs:
                if len(selected_pairs) >= max_pairs:
                    selected_pairs = selected_pairs[:-1]  # Remove last to make room
                selected_pairs.insert(0, "CRO/USD")
                print(f"✅ Ensured CRO/USD is included for cryptocom")
            elif "CRO/USD" in selected_pairs:
                # Move CRO/USD to first position
                selected_pairs.remove("CRO/USD")
                selected_pairs.insert(0, "CRO/USD")
                print(f"✅ Moved CRO/USD to first position for cryptocom")
        
        return selected_pairs
        
    except Exception as e:
        print(f"❌ Error generating order book-based pairs for {exchange_name}: {e}")
        return []

async def test_order_book_pair_generation():
    """Test the order book-based pair generation"""
    print("🧪 Testing Order Book-Based Pair Generation")
    print("=" * 60)
    
    # Test each exchange
    exchanges = [
        ("binance", 5, "USDC"),
        ("bybit", 5, "USDC"), 
        ("cryptocom", 5, "USD")
    ]
    
    for exchange_name, max_pairs, base_currency in exchanges:
        print(f"\n📊 Testing {exchange_name.upper()}...")
        selected_pairs = await generate_order_book_based_pairs(
            exchange_name, max_pairs, base_currency, "http://database-service:8002"
        )
        print(f"✅ {exchange_name}: {selected_pairs}")

if __name__ == "__main__":
    asyncio.run(test_order_book_pair_generation())
