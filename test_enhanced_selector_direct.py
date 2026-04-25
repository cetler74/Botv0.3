#!/usr/bin/env python3
"""
Direct test of enhanced pair selector using config service
"""

import asyncio
import httpx
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.enhanced_pair_selector import EnhancedPairSelector

class SimpleConfigManager:
    """Simple config manager that uses the config service"""
    
    def __init__(self):
        self.config_service_url = 'http://localhost:8001'
        self.config_data = {}
        
    async def get_exchange_config(self, exchange_name: str):
        """Get exchange configuration from service"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(f"{self.config_service_url}/api/v1/config/exchanges/{exchange_name}")
                if response.status_code == 200:
                    return response.json()
                else:
                    print(f"Error getting config for {exchange_name}: {response.status_code}")
                    return {}
            except Exception as e:
                print(f"Exception getting config for {exchange_name}: {e}")
                return {}
    
    async def get_exchange_list(self):
        """Get list of exchanges from service"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(f"{self.config_service_url}/api/v1/config/exchanges/list")
                if response.status_code == 200:
                    return response.json().get('exchanges', [])
                else:
                    print(f"Error getting exchange list: {response.status_code}")
                    return []
            except Exception as e:
                print(f"Exception getting exchange list: {e}")
                return []
    
    def get_pair_selector_config(self):
        """Get pair selection configuration"""
        return {
            'update_interval_minutes': 15,
            'scoring_weights': {
                'liquidity_score': 0.3,
                'spread_tightness': 0.25,
                'volatility_suitability': 0.2,
                'performance_metrics': 0.15,
                'volume_24h': 0.1
            },
            'selection_criteria': {
                'min_volume_1h': 100000,
                'min_volume_15m': 25000,
                'min_volume_5m': 10000,
                'min_order_book_depth': 50000,
                'max_spread_percentage': 0.5,
                'max_spread_1h_avg': 0.3,
                'min_spread_consistency': 0.7,
                'min_volatility_15m': 0.5,
                'max_volatility_15m': 3.0,
                'optimal_volatility_range': [1.0, 2.5],
                'min_volatility_consistency': 0.6,
                'max_slippage_threshold': 0.1,
                'min_execution_success_rate': 0.95,
                'max_execution_time': 2.0,
                'min_win_rate': 0.6,
                'min_profit_factor': 1.2,
                'max_drawdown': 0.15
            }
        }

async def test_enhanced_selector():
    """Test the enhanced pair selector directly"""
    
    print("🚀 Testing Enhanced Pair Selector Directly")
    print("=" * 60)
    
    try:
        # Initialize simple config manager
        config_manager = SimpleConfigManager()
        
        # Initialize enhanced pair selector
        selector = EnhancedPairSelector(config_manager)
        
        # Test each exchange
        exchanges = await config_manager.get_exchange_list()
        print(f"Found exchanges: {exchanges}")
        
        for exchange in exchanges:
            print(f'\n=== Testing {exchange.upper()} ===')
            try:
                # Get exchange config
                exchange_config = await config_manager.get_exchange_config(exchange)
                base_pair = exchange_config.get('base_currency', 'USDC')
                num_pairs = exchange_config.get('max_pairs', 15)
                
                print(f'Base currency: {base_pair}')
                print(f'Max pairs requested: {num_pairs}')
                
                # Test enhanced pair selector
                selected_pairs_with_scores = await selector.select_top_scalping_pairs(
                    exchange, base_pair, num_pairs
                )
                
                if selected_pairs_with_scores:
                    selected_pairs = [pair[0] for pair in selected_pairs_with_scores]
                    scores = [pair[1] for pair in selected_pairs_with_scores]
                    print(f"✅ Enhanced selector found {len(selected_pairs)} pairs:")
                    for pair, score in zip(selected_pairs, scores):
                        print(f"  - {pair}: {score:.3f}")
                else:
                    print(f"❌ Enhanced selector found no suitable pairs for {exchange}")
                    
            except Exception as e:
                print(f"❌ Error testing {exchange}: {e}")
                import traceback
                traceback.print_exc()
                
    except Exception as e:
        print(f"❌ An error occurred during testing: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_enhanced_selector())
