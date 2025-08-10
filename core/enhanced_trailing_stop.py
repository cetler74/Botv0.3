"""
Enhanced Trailing Stop Manager
Integrates with the new database schema and price feed service for robust trailing stop management
"""

import asyncio
import httpx
import logging
from decimal import Decimal
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)

class EnhancedTrailingStopManager:
    """Enhanced trailing stop manager with database persistence and real-time price feeds"""
    
    def __init__(self, database_service_url: str, price_feed_service_url: str = None):
        self.database_service_url = database_service_url
        self.price_feed_service_url = price_feed_service_url or "http://price-feed-service:8007"
        self.client = httpx.AsyncClient(timeout=30.0)
    
    async def initialize_trailing_stop(self, trade_data: Dict[str, Any], config: Dict[str, Any]) -> bool:
        """Initialize a new trailing stop record for a trade"""
        try:
            trade_id = trade_data.get('trade_id')
            if not trade_id:
                logger.error("No trade_id provided for trailing stop initialization")
                return False
            
            # Extract configuration values
            trading_config = config.get('trading', {})
            trailing_config = trading_config.get('trailing_stop', {})
            
            trailing_data = {
                'trade_id': trade_id,
                'exchange': trade_data.get('exchange'),
                'pair': trade_data.get('pair'),
                'trailing_enabled': trailing_config.get('enabled', True),
                'trailing_trigger_percentage': float(trailing_config.get('trigger_percentage', 0.035)),  # 3.5%
                'trailing_step_percentage': float(trailing_config.get('step_percentage', 0.005)),  # 0.5%
                'max_trail_distance_percentage': float(trailing_config.get('max_trail_distance', 0.025)),  # 2.5%
                'entry_price': float(trade_data.get('entry_price', 0)),
                'current_price': float(trade_data.get('entry_price', 0)),
                'highest_price_seen': float(trade_data.get('entry_price', 0)),
                'position_side': 'long',  # Assuming long positions for now
                'profit_protection_enabled': trading_config.get('profit_protection', {}).get('enabled', True),
                'profit_lock_percentage': float(trading_config.get('profit_protection', {}).get('lock_percentage', 0.025))
            }
            
            # Create trailing stop record in database
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.database_service_url}/api/v1/trailing-stops",
                    json=trailing_data
                )
                
                if response.status_code == 201:
                    logger.info(f"✅ Initialized trailing stop for trade {trade_id}")
                    return True
                else:
                    logger.error(f"❌ Failed to initialize trailing stop: {response.status_code} - {response.text}")
                    return False
                    
        except Exception as e:
            logger.error(f"❌ Error initializing trailing stop for trade {trade_data.get('trade_id')}: {e}")
            return False
    
    async def update_trailing_stop_price(self, trade_id: str, current_price: float) -> Tuple[bool, Optional[str]]:
        """Update trailing stop based on current price and check if it should trigger"""
        try:
            # Call database function to update trailing stop
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.put(
                    f"{self.database_service_url}/api/v1/trailing-stops/{trade_id}/update",
                    json={'current_price': current_price}
                )
                
                if response.status_code == 200:
                    data = response.json()
                    adjustment_made = data.get('adjustment_made', False)
                    should_trigger = data.get('should_trigger', False)
                    trigger_reason = data.get('trigger_reason')
                    
                    if adjustment_made:
                        logger.info(f"📈 Trailing stop adjusted for trade {trade_id} at ${current_price:.8f}")
                    
                    if should_trigger:
                        logger.warning(f"🚨 Trailing stop triggered for trade {trade_id} at ${current_price:.8f}")
                        return True, trigger_reason
                    
                    return False, None
                    
                else:
                    logger.error(f"❌ Failed to update trailing stop: {response.status_code}")
                    return False, None
                    
        except Exception as e:
            logger.error(f"❌ Error updating trailing stop for trade {trade_id}: {e}")
            return False, None
    
    async def activate_trailing_stop(self, trade_id: str) -> bool:
        """Activate trailing stop when profit threshold is reached"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.put(
                    f"{self.database_service_url}/api/v1/trailing-stops/{trade_id}/activate"
                )
                
                if response.status_code == 200:
                    logger.info(f"✅ Activated trailing stop for trade {trade_id}")
                    return True
                else:
                    logger.error(f"❌ Failed to activate trailing stop: {response.status_code}")
                    return False
                    
        except Exception as e:
            logger.error(f"❌ Error activating trailing stop for trade {trade_id}: {e}")
            return False
    
    async def get_trailing_stop_status(self, trade_id: str) -> Optional[Dict[str, Any]]:
        """Get current trailing stop status for a trade"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{self.database_service_url}/api/v1/trailing-stops/{trade_id}"
                )
                
                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 404:
                    return None
                else:
                    logger.error(f"❌ Failed to get trailing stop status: {response.status_code}")
                    return None
                    
        except Exception as e:
            logger.error(f"❌ Error getting trailing stop status for trade {trade_id}: {e}")
            return None
    
    async def get_current_price_from_feed(self, exchange: str, pair: str) -> Optional[float]:
        """Get current price from the price feed service"""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{self.price_feed_service_url}/api/v1/price/{exchange}/{pair}"
                )
                
                if response.status_code == 200:
                    data = response.json()
                    return float(data.get('price', 0))
                else:
                    return None
                    
        except Exception as e:
            logger.debug(f"Price feed service not available, falling back to REST API: {e}")
            return None
    
    async def check_all_trailing_stops(self) -> List[Dict[str, Any]]:
        """Check all active trailing stops and return any that should trigger"""
        triggered_stops = []
        
        try:
            # Get all active trailing stops from price feed service
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(f"{self.price_feed_service_url}/trailing-stops")
                
                if response.status_code == 200:
                    data = response.json()
                    trailing_stops = data.get('trailing_stops', [])
                    
                    for ts in trailing_stops:
                        if ts.get('is_active') and ts.get('trade_status') == 'OPEN':
                            trade_id = ts.get('trade_id')
                            current_price = ts.get('latest_market_price')
                            
                            if current_price:
                                should_trigger, reason = await self.update_trailing_stop_price(
                                    trade_id, float(current_price)
                                )
                                
                                if should_trigger:
                                    triggered_stops.append({
                                        'trade_id': trade_id,
                                        'exchange': ts.get('exchange'),
                                        'pair': ts.get('pair'),
                                        'trigger_price': current_price,
                                        'reason': reason
                                    })
                
        except Exception as e:
            logger.error(f"❌ Error checking trailing stops: {e}")
        
        return triggered_stops
    
    async def deactivate_trailing_stop(self, trade_id: str) -> bool:
        """Deactivate trailing stop when trade is closed"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.put(
                    f"{self.database_service_url}/api/v1/trailing-stops/{trade_id}/deactivate"
                )
                
                if response.status_code == 200:
                    logger.info(f"✅ Deactivated trailing stop for trade {trade_id}")
                    return True
                else:
                    logger.error(f"❌ Failed to deactivate trailing stop: {response.status_code}")
                    return False
                    
        except Exception as e:
            logger.error(f"❌ Error deactivating trailing stop for trade {trade_id}: {e}")
            return False
    
    async def get_trailing_stop_analytics(self) -> Dict[str, Any]:
        """Get analytics about trailing stop performance"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(f"{self.price_feed_service_url}/status")
                
                if response.status_code == 200:
                    return response.json()
                else:
                    return {}
                    
        except Exception as e:
            logger.error(f"❌ Error getting trailing stop analytics: {e}")
            return {}
    
    async def cleanup(self):
        """Cleanup resources"""
        if hasattr(self, 'client'):
            await self.client.aclose()