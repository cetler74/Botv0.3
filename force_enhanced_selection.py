#!/usr/bin/env python3
"""
Force enhanced pair selection and update database
"""

import asyncio
import httpx
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.enhanced_pair_selector import EnhancedPairSelector

class SimpleConfigManager:
    def __init__(self):
        self.config_service_url = 'http://localhost:8001'
    
    async def get_exchange_config(self, exchange_name):
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(f'{self.config_service_url}/api/v1/config/exchanges/{exchange_name}')
                if response.status_code == 200:
                    return response.json()
                return {}
            except Exception as e:
                print(f"Error getting config for {exchange_name}: {e}")
                return {}
    
    async def get_exchange_list(self):
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(f'{self.config_service_url}/api/v1/config/exchanges/list')
                if response.status_code == 200:
                    return response.json().get('exchanges', [])
                return []
            except Exception as e:
                print(f"Error getting exchange list: {e}")
                return []
    
    async def get_pair_selector_config(self):
        """Get pair selector configuration from config service"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(f'{self.config_service_url}/api/v1/config/all')
                if response.status_code == 200:
                    config_data = response.json()
                    return config_data.get('pair_selector', {})
                return {}
            except Exception as e:
                print(f"Error getting pair selector config: {e}")
                return {}

async def force_enhanced_selection():
    """Force enhanced pair selection and update database"""
    print('🚀 Forcing Enhanced Pair Selection...')
    print('=' * 60)
    
    try:
        config_manager = SimpleConfigManager()
        selector = EnhancedPairSelector(config_manager)
        
        # Load configuration from service
        await selector._load_config_from_service()
        
        exchanges = await config_manager.get_exchange_list()
        print(f"Found exchanges: {exchanges}")
        
        for exchange in exchanges:
            print(f'\n=== {exchange.upper()} ===')
            try:
                exchange_config = await config_manager.get_exchange_config(exchange)
                base_pair = exchange_config.get('base_currency', 'USDC')
                num_pairs = exchange_config.get('max_pairs', 20)
                
                print(f'Base currency: {base_pair}')
                print(f'Max pairs requested: {num_pairs}')
                
                # Get current pairs from database
                async with httpx.AsyncClient(timeout=30.0) as client:
                    current_response = await client.get(f'http://localhost:8002/api/v1/pairs/{exchange}')
                    if current_response.status_code == 200:
                        current_data = current_response.json()
                        current_pairs = current_data.get('pairs', [])
                        print(f'Current pairs in database: {len(current_pairs)}')
                        print(f'Current pairs: {current_pairs[:5]}...' if len(current_pairs) > 5 else f'Current pairs: {current_pairs}')
                
                # Use enhanced selector
                print(f'🎯 Running enhanced pair selection...')
                selected_pairs_with_scores = await selector.select_top_scalping_pairs(exchange, base_pair, num_pairs)
                
                if selected_pairs_with_scores:
                    selected_pairs = [pair[0] for pair in selected_pairs_with_scores]
                    scores = [pair[1] for pair in selected_pairs_with_scores]
                    print(f'✅ Enhanced selector found {len(selected_pairs)} pairs')
                    print(f'Enhanced pairs: {selected_pairs[:5]}...' if len(selected_pairs) > 5 else f'Enhanced pairs: {selected_pairs}')
                    print(f'Scores: {scores[:5]}...' if len(scores) > 5 else f'Scores: {scores}')
                    
                    # Update database with new pairs
                    async with httpx.AsyncClient(timeout=30.0) as client:
                        response = await client.post(
                            f'http://localhost:8002/api/v1/pairs/{exchange}',
                            json=selected_pairs
                        )
                        if response.status_code == 200:
                            print(f'✅ Successfully updated database for {exchange} with enhanced selection')
                        else:
                            print(f'❌ Failed to update database for {exchange}: {response.status_code}')
                else:
                    print(f'❌ Enhanced selector found no suitable pairs for {exchange}')
                    
            except Exception as e:
                print(f'❌ Error processing {exchange}: {e}')
                import traceback
                traceback.print_exc()
                
    except Exception as e:
        print(f'❌ An error occurred: {e}')
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(force_enhanced_selection())
