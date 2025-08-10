#!/usr/bin/env python3
"""
Transversal Momentum Filter Design
Cross-pair momentum analysis to prevent buying into downtrends or at peaks
"""

import asyncio
import httpx
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, Any, Tuple, Optional

class MomentumFilter:
    """
    Transversal momentum filter for trade entry decisions
    Analyzes 24-hour price bands and trend direction
    """
    
    def __init__(self, exchange_service_url: str = "http://localhost:8003"):
        self.exchange_service_url = exchange_service_url
        
        # Band percentiles for 24h analysis
        self.lower_band_percentile = 20  # 20th percentile = lower support
        self.upper_band_percentile = 95  # 95th percentile = upper resistance
        self.middle_band_lower = 40      # 40th percentile = middle range start
        self.middle_band_upper = 80      # 80th percentile = middle range end
        
        # Trend analysis parameters
        self.trend_short_hours = 6       # Short-term trend (6h)
        self.trend_long_hours = 24       # Long-term trend (24h)
        self.min_trend_strength = 0.01   # 1% minimum trend strength
        
    async def should_allow_entry(self, exchange: str, pair: str) -> Tuple[bool, str]:
        """
        Determine if entry should be allowed based on momentum analysis
        
        Returns:
            (allow_entry: bool, reason: str)
        """
        try:
            # Get 24-hour price data
            price_data = await self._get_price_data(exchange, pair, hours=24)
            if not price_data or len(price_data) < 12:
                return True, "Insufficient data - allowing entry"
            
            # Calculate price bands
            prices = [candle['close'] for candle in price_data]
            current_price = prices[-1]
            
            lower_band = np.percentile(prices, self.lower_band_percentile)
            upper_band = np.percentile(prices, self.upper_band_percentile)
            middle_lower = np.percentile(prices, self.middle_band_lower)
            middle_upper = np.percentile(prices, self.middle_band_upper)
            
            # Determine price position
            if current_price <= lower_band:
                position = "lower_band"
            elif current_price >= upper_band:
                position = "upper_band"
            elif middle_lower <= current_price <= middle_upper:
                position = "middle_band"
            elif current_price < middle_lower:
                position = "lower_middle"
            else:
                position = "upper_middle"
            
            # Calculate trend direction
            trend_direction, trend_strength = await self._calculate_trend(price_data)
            
            # Apply momentum filter logic
            return self._apply_filter_logic(position, trend_direction, trend_strength, 
                                          current_price, lower_band, upper_band)
            
        except Exception as e:
            # On error, allow entry (fail-safe)
            return True, f"Filter error: {e} - allowing entry"
    
    async def _get_price_data(self, exchange: str, pair: str, hours: int = 48) -> Optional[list]:
        """Get OHLCV data for the specified time period"""
        try:
            # Calculate required candles (assuming 1h timeframe)
            limit = min(hours + 5, 100)  # Add buffer, cap at 100
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{self.exchange_service_url}/api/v1/market/ohlcv/{exchange}/{pair}",
                    params={"timeframe": "1h", "limit": limit}
                )
                
                if response.status_code == 200:
                    return response.json()
                else:
                    return None
                    
        except Exception as e:
            print(f"Error fetching price data for {pair} on {exchange}: {e}")
            return None
    
    async def _calculate_trend(self, price_data: list) -> Tuple[str, float]:
        """
        Calculate trend direction and strength
        
        Returns:
            (direction: 'up'/'down'/'neutral', strength: float)
        """
        if len(price_data) < 12:
            return 'neutral', 0.0
            
        prices = [candle['close'] for candle in price_data]
        current_price = prices[-1]
        
        # Short-term trend (6h)
        short_price = prices[-6] if len(prices) >= 6 else prices[0]
        short_trend = (current_price - short_price) / short_price
        
        # Long-term trend (24h) 
        long_price = prices[0]
        long_trend = (current_price - long_price) / long_price
        
        # Weighted trend strength (favor short-term)
        trend_strength = (short_trend * 0.7) + (long_trend * 0.3)
        
        # Determine direction
        if abs(trend_strength) < self.min_trend_strength:
            direction = 'neutral'
        elif trend_strength > 0:
            direction = 'up'
        else:
            direction = 'down'
            
        return direction, abs(trend_strength)
    
    def _apply_filter_logic(self, position: str, trend_direction: str, trend_strength: float,
                           current_price: float, lower_band: float, upper_band: float) -> Tuple[bool, str]:
        """
        Apply momentum filter logic
        
        Filter Rules:
        - Lower band + upward trend = ALLOW (potential reversal)
        - Upper band + any trend = BLOCK (avoid buying peaks)  
        - Lower band + downward trend = BLOCK (avoid falling knife)
        - Middle band = ALLOW (neutral zone, use strategy logic)
        """
        
        band_pct = ((current_price - lower_band) / (upper_band - lower_band)) * 100
        
        if position == "lower_band":
            if trend_direction == 'up' and trend_strength >= self.min_trend_strength:
                return True, f"ALLOW: Lower band reversal - price at {band_pct:.1f}% of 24h range, uptrend {trend_strength:.2%}"
            else:
                return False, f"BLOCK: Lower band falling knife - price at {band_pct:.1f}% of 24h range, trend {trend_direction} {trend_strength:.2%}"
                
        elif position == "upper_band":
            return False, f"BLOCK: Upper band peak - price at {band_pct:.1f}% of 24h range, avoid buying highs"
            
        elif position in ["middle_band", "lower_middle"]:
            return True, f"ALLOW: Middle range - price at {band_pct:.1f}% of 24h range, neutral zone"
            
        elif position == "upper_middle":
            if trend_direction == 'down':
                return False, f"BLOCK: Upper-middle downtrend - price at {band_pct:.1f}% of 24h range, trend {trend_direction} {trend_strength:.2%}"
            else:
                return True, f"ALLOW: Upper-middle stable - price at {band_pct:.1f}% of 24h range, trend {trend_direction}"
        
        # Default: allow
        return True, f"ALLOW: Default case - price at {band_pct:.1f}% of 24h range"

# Example usage
async def test_momentum_filter():
    """Test the momentum filter on CRO/USD"""
    filter = MomentumFilter()
    
    # Test current CRO/USD conditions
    allow_entry, reason = await filter.should_allow_entry("cryptocom", "CROUSD")
    
    print("Momentum Filter Test Results:")
    print("=" * 50)
    print(f"Pair: CRO/USD (Crypto.com)")
    print(f"Entry Decision: {'✅ ALLOW' if allow_entry else '❌ BLOCK'}")
    print(f"Reason: {reason}")
    print()
    
    # Test on other pairs for comparison
    test_pairs = [
        ("bybit", "BTCUSDC"),
        ("bybit", "ETHUSDC"), 
        ("binance", "XRPUSDC")
    ]
    
    for exchange, pair in test_pairs:
        try:
            allow, reason = await filter.should_allow_entry(exchange, pair)
            print(f"{pair} ({exchange}): {'✅ ALLOW' if allow else '❌ BLOCK'} - {reason}")
        except Exception as e:
            print(f"{pair} ({exchange}): Error - {e}")

if __name__ == "__main__":
    asyncio.run(test_momentum_filter())