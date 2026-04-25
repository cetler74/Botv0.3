#!/usr/bin/env python3
"""
Test script to evaluate the enhanced pair selector performance
"""

import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.enhanced_pair_selector import EnhancedPairSelector
from core.config_manager import ConfigManager

async def test_enhanced_pair_selector():
    """Test the enhanced pair selector and compare with current selections"""
    
    print("🚀 Testing Enhanced Pair Selector")
    print("=" * 50)
    
    try:
        # Initialize config manager
        config_manager = ConfigManager()
        
        # Initialize enhanced pair selector
        selector = EnhancedPairSelector(config_manager)
        
        # Test each exchange
        exchanges = ['binance', 'bybit', 'cryptocom']
        
        for exchange in exchanges:
            print(f"\n📊 Testing {exchange.upper()}")
            print("-" * 30)
            
            try:
                # Get exchange configuration
                exchange_config = config_manager.get_exchange_config(exchange)
                base_pair = exchange_config.get('base_currency', 'USDC')
                max_pairs = exchange_config.get('max_pairs', 15)
                
                print(f"Base currency: {base_pair}")
                print(f"Max pairs requested: {max_pairs}")
                
                # Test enhanced selection
                print(f"\n🔍 Running enhanced pair selection...")
                selected_pairs = await selector.select_top_scalping_pairs(
                    exchange, base_pair, max_pairs
                )
                
                if selected_pairs:
                    print(f"✅ Selected {len(selected_pairs)} pairs:")
                    print(f"{'Rank':<4} {'Pair':<12} {'Score':<8} {'Analysis'}")
                    print("-" * 50)
                    
                    for i, (pair, score) in enumerate(selected_pairs, 1):
                        # Determine quality based on score
                        if score >= 0.8:
                            quality = "🟢 Excellent"
                        elif score >= 0.6:
                            quality = "🟡 Good"
                        elif score >= 0.4:
                            quality = "🟠 Fair"
                        else:
                            quality = "🔴 Poor"
                        
                        print(f"{i:<4} {pair:<12} {score:<8.3f} {quality}")
                else:
                    print("❌ No pairs selected - all pairs failed scalping criteria")
                    
            except Exception as e:
                print(f"❌ Error testing {exchange}: {e}")
                import traceback
                traceback.print_exc()
        
        print(f"\n🎯 Enhanced Pair Selector Test Complete!")
        print("=" * 50)
        
    except Exception as e:
        print(f"❌ Error initializing test: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_enhanced_pair_selector())
