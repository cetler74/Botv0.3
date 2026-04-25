#!/usr/bin/env python3
"""
Test Price Integration Module

This module provides integration with the test price service to allow
price overrides during testing without affecting production flows.

Author: Claude Code
Created: 2025-01-27
"""

import asyncio
import logging
import os
from typing import Optional, Dict, Any
import httpx

logger = logging.getLogger(__name__)

class TestPriceIntegration:
    """Integration with test price service for price overrides"""
    
    def __init__(self):
        self.test_price_service_url = os.getenv("TEST_PRICE_SERVICE_URL", "http://test-price-service:8008")
        self.enabled = os.getenv("TEST_MODE_ENABLED", "false").lower() == "true"
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._cache_ttl = 30  # Cache for 30 seconds
        
    async def get_test_price(self, exchange: str, pair: str) -> Optional[float]:
        """Get test price override if available"""
        if not self.enabled:
            return None
            
        try:
            # Check cache first
            cache_key = f"{exchange}:{pair}"
            if cache_key in self._cache:
                cached_data = self._cache[cache_key]
                if cached_data.get('timestamp', 0) + self._cache_ttl > asyncio.get_event_loop().time():
                    return cached_data.get('price')
            
            # Fetch from test price service
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(
                    f"{self.test_price_service_url}/api/v1/prices/test/{exchange}/{pair}"
                )
                
                if response.status_code == 200:
                    data = response.json()
                    test_price = data.get('test_price')
                    
                    if test_price:
                        # Cache the result
                        self._cache[cache_key] = {
                            'price': test_price,
                            'timestamp': asyncio.get_event_loop().time()
                        }
                        
                        logger.info(f"🧪 Using test price: {exchange}/{pair} = ${test_price:.6f}")
                        return test_price
                        
        except Exception as e:
            logger.debug(f"Test price service unavailable: {e}")
            
        return None
    
    async def is_test_mode_active(self) -> bool:
        """Check if test mode is active"""
        if not self.enabled:
            return False
            
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.test_price_service_url}/api/v1/test-mode/status")
                
                if response.status_code == 200:
                    data = response.json()
                    return data.get('test_mode_status') == 'enabled'
                    
        except Exception as e:
            logger.debug(f"Could not check test mode status: {e}")
            
        return False
    
    def clear_cache(self):
        """Clear the price cache"""
        self._cache.clear()
        logger.debug("🧹 Cleared test price cache")

# Global instance
test_price_integration = TestPriceIntegration()
