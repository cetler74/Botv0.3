"""
Crypto.com WebSocket Consumer for Fill Detection Service - Version 2.6.0
Consumes and processes Crypto.com WebSocket events for order fill detection
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
import redis.asyncio as redis
import httpx

logger = logging.getLogger(__name__)

class CryptocomWebSocketConsumer:
    """
    Consumes Crypto.com WebSocket events and processes them for fill detection
    
    Features:
    - Event transformation to standardized format
    - Redis stream publishing for existing pipeline integration
    - Fill detection and notification
    - Metrics and performance tracking
    """
    
    def __init__(self, redis_url: str = "redis://redis:6379"):
        self.redis_url = redis_url
        self.redis_client = None
        
        # Metrics tracking
        self.metrics = {
            "events_received": 0,
            "order_events": 0,
            "trade_events": 0,
            "balance_events": 0,
            "fills_detected": 0,
            "partial_fills_detected": 0,
            "events_published": 0,
            "processing_errors": 0,
            "last_processed_time": None
        }
        
        logger.info("🏢 Crypto.com WebSocket Consumer initialized")
    
    async def initialize(self) -> bool:
        """Initialize the consumer with Redis connection"""
        try:
            self.redis_client = await redis.from_url(self.redis_url)
            
            # Test Redis connection
            await self.redis_client.ping()
            
            logger.info("✅ Crypto.com WebSocket Consumer connected to Redis")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to initialize Crypto.com WebSocket Consumer: {e}")
            return False
    
    async def process_order_event(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process Crypto.com order events from WebSocket stream
        
        Args:
            event_data: Raw order event from Crypto.com WebSocket
            
        Returns:
            Processed event in standardized format
        """
        try:
            self.metrics["events_received"] += 1
            self.metrics["order_events"] += 1
            self.metrics["last_processed_time"] = datetime.utcnow().isoformat()
            
            # Extract order details
            order_id = event_data.get("order_id")
            status = event_data.get("status", "").upper()
            symbol = event_data.get("symbol", "").replace("_", "/")  # BTC_USDC -> BTC/USDC
            side = event_data.get("side", "").upper()
            quantity = float(event_data.get("quantity", 0))
            filled_quantity = float(event_data.get("filled_quantity", 0))
            remaining_quantity = float(event_data.get("remaining_quantity", 0))
            price = float(event_data.get("price", 0))
            
            # Transform to standardized format
            standardized_event = {
                "exchange": "cryptocom",
                "event_type": "order_update",
                "order_id": order_id,
                "symbol": symbol,
                "side": side,
                "status": status,
                "quantity": quantity,
                "filled_quantity": filled_quantity,
                "remaining_quantity": remaining_quantity,
                "price": price,
                "timestamp": datetime.utcnow().isoformat(),
                "raw_data": event_data
            }
            
            # Detect fills
            if status == "FILLED":
                self.metrics["fills_detected"] += 1
                standardized_event["fill_detected"] = True
                standardized_event["fill_type"] = "complete"
                logger.info(f"🎯 Complete fill detected - Crypto.com {symbol}: {filled_quantity}")
                
            elif status == "PARTIALLY_FILLED":
                self.metrics["partial_fills_detected"] += 1
                standardized_event["fill_detected"] = True
                standardized_event["fill_type"] = "partial"
                logger.info(f"🎯 Partial fill detected - Crypto.com {symbol}: {filled_quantity}/{quantity}")
            
            # Publish to Redis stream for existing pipeline integration
            await self._publish_to_redis_stream(standardized_event)
            
            logger.debug(f"📋 Processed Crypto.com order event: {order_id} ({status})")
            return standardized_event
            
        except Exception as e:
            logger.error(f"❌ Error processing Crypto.com order event: {e}")
            self.metrics["processing_errors"] += 1
            raise
    
    async def process_trade_event(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process Crypto.com trade events from WebSocket stream
        
        Args:
            event_data: Raw trade event from Crypto.com WebSocket
            
        Returns:
            Processed event in standardized format
        """
        try:
            self.metrics["events_received"] += 1
            self.metrics["trade_events"] += 1
            self.metrics["last_processed_time"] = datetime.utcnow().isoformat()
            
            # Extract trade details
            trade_id = event_data.get("trade_id")
            order_id = event_data.get("order_id")
            symbol = event_data.get("symbol", "").replace("_", "/")  # BTC_USDC -> BTC/USDC
            side = event_data.get("side", "").upper()
            quantity = float(event_data.get("quantity", 0))
            price = float(event_data.get("price", 0))
            fee = float(event_data.get("fee", 0))
            fee_currency = event_data.get("fee_currency", "")
            trade_time = event_data.get("trade_time", 0)
            
            # Transform to standardized format
            standardized_event = {
                "exchange": "cryptocom",
                "event_type": "trade_execution",
                "trade_id": trade_id,
                "order_id": order_id,
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "price": price,
                "fee": fee,
                "fee_currency": fee_currency,
                "notional_value": quantity * price,
                "trade_time": datetime.fromtimestamp(trade_time / 1000).isoformat() if trade_time else None,
                "timestamp": datetime.utcnow().isoformat(),
                "raw_data": event_data,
                "fill_detected": True,  # Trade events always indicate fills
                "fill_type": "execution"
            }
            
            # Trade executions always indicate fills
            logger.info(f"💰 Trade execution detected - Crypto.com {symbol}: {quantity} @ {price}")
            
            # Publish to Redis stream
            await self._publish_to_redis_stream(standardized_event)
            
            logger.debug(f"💰 Processed Crypto.com trade event: {trade_id}")
            return standardized_event
            
        except Exception as e:
            logger.error(f"❌ Error processing Crypto.com trade event: {e}")
            self.metrics["processing_errors"] += 1
            raise
    
    async def process_balance_event(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process Crypto.com balance events from WebSocket stream
        
        Args:
            event_data: Raw balance event from Crypto.com WebSocket
            
        Returns:
            Processed event in standardized format
        """
        try:
            self.metrics["events_received"] += 1
            self.metrics["balance_events"] += 1
            self.metrics["last_processed_time"] = datetime.utcnow().isoformat()
            
            # Extract balance details
            currency = event_data.get("currency", "")
            balance = float(event_data.get("balance", 0))
            available = float(event_data.get("available", 0))
            frozen = float(event_data.get("frozen", 0))
            update_time = event_data.get("update_time", 0)
            
            # Transform to standardized format
            standardized_event = {
                "exchange": "cryptocom",
                "event_type": "balance_update",
                "currency": currency,
                "balance": balance,
                "available": available,
                "frozen": frozen,
                "update_time": datetime.fromtimestamp(update_time / 1000).isoformat() if update_time else None,
                "timestamp": datetime.utcnow().isoformat(),
                "raw_data": event_data
            }
            
            # Publish to Redis stream
            await self._publish_to_redis_stream(standardized_event)
            
            logger.debug(f"💳 Processed Crypto.com balance event: {currency}")
            return standardized_event
            
        except Exception as e:
            logger.error(f"❌ Error processing Crypto.com balance event: {e}")
            self.metrics["processing_errors"] += 1
            raise
    
    async def _publish_to_redis_stream(self, event_data: Dict[str, Any]):
        """
        Publish event to Redis stream for integration with existing pipeline
        
        Args:
            event_data: Processed event data to publish
        """
        try:
            if not self.redis_client:
                logger.warning("⚠️ Redis client not initialized, skipping stream publish")
                return
            
            # Determine stream name based on event type
            event_type = event_data.get("event_type", "unknown")
            stream_name = f"cryptocom:{event_type}:events"
            
            # Convert event data to string values for Redis
            redis_data = {}
            for key, value in event_data.items():
                if isinstance(value, dict):
                    redis_data[key] = json.dumps(value)
                else:
                    redis_data[key] = str(value)
            
            # Publish to Redis stream
            message_id = await self.redis_client.xadd(stream_name, redis_data)
            
            self.metrics["events_published"] += 1
            logger.debug(f"📤 Published to Redis stream {stream_name}: {message_id}")
            
        except Exception as e:
            logger.error(f"❌ Error publishing to Redis stream: {e}")
            # Don't raise exception to avoid breaking event processing
    
    async def process_execution_event(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Main entry point for processing execution events from exchange service
        
        Args:
            event_data: Event data from Crypto.com WebSocket integration
            
        Returns:
            Processed event data
        """
        try:
            event_type = event_data.get("event_type")
            
            if event_type == "order_update":
                return await self.process_order_event(event_data.get("data", {}))
            elif event_type == "trade_execution":
                return await self.process_trade_event(event_data.get("data", {}))
            elif event_type == "balance_update":
                return await self.process_balance_event(event_data.get("data", {}))
            else:
                logger.warning(f"⚠️ Unknown event type: {event_type}")
                return {}
                
        except Exception as e:
            logger.error(f"❌ Error processing execution event: {e}")
            self.metrics["processing_errors"] += 1
            raise
    
    async def get_fill_status(self, order_id: str) -> Optional[Dict[str, Any]]:
        """
        Get fill status for a specific order by querying recent Redis streams
        
        Args:
            order_id: Order ID to check
            
        Returns:
            Fill status information or None if not found
        """
        try:
            if not self.redis_client:
                logger.warning("⚠️ Redis client not initialized")
                return None
            
            # Search recent order events for the order ID
            streams = ["cryptocom:order_update:events", "cryptocom:trade_execution:events"]
            
            for stream_name in streams:
                try:
                    # Get recent messages from stream
                    messages = await self.redis_client.xrevrange(stream_name, count=100)
                    
                    for message_id, fields in messages:
                        if fields.get("order_id") == order_id:
                            return {
                                "order_id": order_id,
                                "found_in_stream": stream_name,
                                "message_id": message_id,
                                "status": fields.get("status"),
                                "filled_quantity": fields.get("filled_quantity"),
                                "fill_detected": fields.get("fill_detected") == "True",
                                "timestamp": fields.get("timestamp")
                            }
                except Exception as e:
                    logger.warning(f"⚠️ Error checking stream {stream_name}: {e}")
                    continue
            
            return None
            
        except Exception as e:
            logger.error(f"❌ Error getting fill status: {e}")
            return None
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get consumer metrics"""
        return {
            **self.metrics,
            "redis_connected": self.redis_client is not None
        }
    
    def get_status(self) -> Dict[str, Any]:
        """Get consumer status"""
        return {
            "initialized": self.redis_client is not None,
            "redis_connected": self.redis_client is not None,
            "events_processed": self.metrics["events_received"],
            "fills_detected": self.metrics["fills_detected"] + self.metrics["partial_fills_detected"],
            "last_processed": self.metrics["last_processed_time"],
            "processing_healthy": self.metrics["processing_errors"] < 10
        }
    
    async def cleanup(self):
        """Cleanup resources"""
        try:
            if self.redis_client:
                await self.redis_client.aclose()
                logger.info("✅ Crypto.com WebSocket Consumer cleaned up")
        except Exception as e:
            logger.error(f"❌ Error during cleanup: {e}")