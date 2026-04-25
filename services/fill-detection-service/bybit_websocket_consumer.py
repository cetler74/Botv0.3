"""
Bybit WebSocket Consumer for Fill Detection Service - Version 2.6.0
Consumes and processes Bybit WebSocket events for order fill detection
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
import redis.asyncio as redis
import httpx

logger = logging.getLogger(__name__)

class BybitWebSocketConsumer:
    """
    Consumes Bybit WebSocket events and processes them for fill detection
    
    Features:
    - Event transformation to standardized format
    - Redis stream publishing for existing pipeline integration
    - Fill detection and notification
    - Metrics and performance tracking
    - Integration with existing fill detection system
    """
    
    def __init__(self, redis_url: str = "redis://redis:6379"):
        self.redis_url = redis_url
        self.redis_client = None
        
        # Metrics tracking
        self.metrics = {
            "events_received": 0,
            "order_events": 0,
            "execution_events": 0,
            "position_events": 0,
            "wallet_events": 0,
            "fills_detected": 0,
            "partial_fills_detected": 0,
            "events_published": 0,
            "processing_errors": 0,
            "last_processed_time": None
        }
        
        logger.info("🏢 Bybit WebSocket Consumer initialized")
    
    async def initialize(self) -> bool:
        """Initialize the consumer with Redis connection"""
        try:
            self.redis_client = await redis.from_url(self.redis_url)
            
            # Test Redis connection
            await self.redis_client.ping()
            
            logger.info("✅ Bybit WebSocket Consumer connected to Redis")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to initialize Bybit WebSocket Consumer: {e}")
            return False
    
    async def process_order_event(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process Bybit order event and detect fills
        
        Args:
            event_data: Raw Bybit order event data
            
        Returns:
            Standardized event data for fill detection
        """
        try:
            self.metrics["events_received"] += 1
            self.metrics["order_events"] += 1
            self.metrics["last_processed_time"] = datetime.utcnow().isoformat()
            
            # Extract order information
            order_id = event_data.get('orderId', '')
            order_link_id = event_data.get('orderLinkId', '')
            symbol = event_data.get('symbol', '')
            side = event_data.get('side', '')
            order_type = event_data.get('orderType', '')
            price = float(event_data.get('price', 0))
            qty = float(event_data.get('qty', 0))
            cum_exec_qty = float(event_data.get('cumExecQty', 0))
            cum_exec_fee = float(event_data.get('cumExecFee', 0))
            avg_price = float(event_data.get('avgPrice', 0))
            order_status = event_data.get('orderStatus', '')
            last_exec_price = float(event_data.get('lastExecPrice', 0))
            last_exec_qty = float(event_data.get('lastExecQty', 0))
            exec_time = event_data.get('execTime', '')
            
            # Standardize event format
            standardized_event = {
                "event_type": "bybit_order_event",
                "exchange": "bybit",
                "order_id": order_id,
                "order_link_id": order_link_id,
                "symbol": symbol,
                "side": side.lower(),
                "order_type": order_type,
                "price": price,
                "quantity": qty,
                "filled_quantity": cum_exec_qty,
                "avg_price": avg_price,
                "fee_amount": cum_exec_fee,
                "fee_currency": "USDT",  # Bybit typically uses USDT for fees
                "order_status": order_status,
                "last_exec_price": last_exec_price,
                "last_exec_qty": last_exec_qty,
                "exec_time": exec_time,
                "timestamp": datetime.utcnow().isoformat(),
                "source": "bybit_websocket",
                "raw_data": event_data
            }
            
            # Detect fills
            if order_status == "Filled":
                self.metrics["fills_detected"] += 1
                standardized_event["fill_detected"] = True
                standardized_event["fill_type"] = "complete"
                logger.info(f"🎯 Complete fill detected - Bybit {symbol}: {cum_exec_qty}")
                
            elif order_status == "PartiallyFilled":
                self.metrics["partial_fills_detected"] += 1
                standardized_event["fill_detected"] = True
                standardized_event["fill_type"] = "partial"
                logger.info(f"🎯 Partial fill detected - Bybit {symbol}: {cum_exec_qty}/{qty}")
            
            # Publish to Redis stream for existing pipeline integration
            await self._publish_to_redis_stream(standardized_event)
            
            logger.debug(f"📋 Processed Bybit order event: {order_id} ({order_status})")
            return standardized_event
            
        except Exception as e:
            logger.error(f"❌ Error processing Bybit order event: {e}")
            self.metrics["processing_errors"] += 1
            raise
    
    async def process_execution_event(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process Bybit execution event
        
        Args:
            event_data: Raw Bybit execution event data
            
        Returns:
            Standardized event data for fill detection
        """
        try:
            self.metrics["events_received"] += 1
            self.metrics["execution_events"] += 1
            self.metrics["last_processed_time"] = datetime.utcnow().isoformat()
            
            # Extract execution information
            symbol = event_data.get('symbol', '')
            side = event_data.get('side', '')
            order_id = event_data.get('orderId', '')
            exec_id = event_data.get('execId', '')
            order_link_id = event_data.get('orderLinkId', '')
            price = float(event_data.get('price', 0))
            qty = float(event_data.get('qty', 0))
            exec_fee = float(event_data.get('execFee', 0))
            exec_time = event_data.get('execTime', '')
            
            # Standardize event format
            standardized_event = {
                "event_type": "bybit_execution_event",
                "exchange": "bybit",
                "symbol": symbol,
                "side": side.lower(),
                "order_id": order_id,
                "execution_id": exec_id,
                "order_link_id": order_link_id,
                "price": price,
                "quantity": qty,
                "fee_amount": exec_fee,
                "fee_currency": "USDT",
                "exec_time": exec_time,
                "timestamp": datetime.utcnow().isoformat(),
                "source": "bybit_websocket",
                "raw_data": event_data
            }
            
            # Publish to Redis stream for existing pipeline integration
            await self._publish_to_redis_stream(standardized_event)
            
            logger.debug(f"⚡ Processed Bybit execution event: {exec_id} for order {order_id}")
            return standardized_event
            
        except Exception as e:
            logger.error(f"❌ Error processing Bybit execution event: {e}")
            self.metrics["processing_errors"] += 1
            raise
    
    async def process_position_event(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process Bybit position event
        
        Args:
            event_data: Raw Bybit position event data
            
        Returns:
            Standardized event data
        """
        try:
            self.metrics["events_received"] += 1
            self.metrics["position_events"] += 1
            self.metrics["last_processed_time"] = datetime.utcnow().isoformat()
            
            # Extract position information
            symbol = event_data.get('symbol', '')
            side = event_data.get('side', '')
            size = float(event_data.get('size', 0))
            avg_price = float(event_data.get('avgPrice', 0))
            unrealized_pnl = float(event_data.get('unrealizedPnl', 0))
            mark_price = float(event_data.get('markPrice', 0))
            position_value = float(event_data.get('positionValue', 0))
            
            # Standardize event format
            standardized_event = {
                "event_type": "bybit_position_event",
                "exchange": "bybit",
                "symbol": symbol,
                "side": side.lower(),
                "size": size,
                "avg_price": avg_price,
                "unrealized_pnl": unrealized_pnl,
                "mark_price": mark_price,
                "position_value": position_value,
                "timestamp": datetime.utcnow().isoformat(),
                "source": "bybit_websocket",
                "raw_data": event_data
            }
            
            # Publish to Redis stream for existing pipeline integration
            await self._publish_to_redis_stream(standardized_event)
            
            logger.debug(f"📈 Processed Bybit position event: {symbol} - {size}")
            return standardized_event
            
        except Exception as e:
            logger.error(f"❌ Error processing Bybit position event: {e}")
            self.metrics["processing_errors"] += 1
            raise
    
    async def process_wallet_event(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process Bybit wallet event
        
        Args:
            event_data: Raw Bybit wallet event data
            
        Returns:
            Standardized event data
        """
        try:
            self.metrics["events_received"] += 1
            self.metrics["wallet_events"] += 1
            self.metrics["last_processed_time"] = datetime.utcnow().isoformat()
            
            # Extract wallet information
            currency = event_data.get('currency', '')
            wallet_balance = float(event_data.get('walletBalance', 0))
            available_balance = float(event_data.get('availableBalance', 0))
            
            # Standardize event format
            standardized_event = {
                "event_type": "bybit_wallet_event",
                "exchange": "bybit",
                "currency": currency,
                "wallet_balance": wallet_balance,
                "available_balance": available_balance,
                "timestamp": datetime.utcnow().isoformat(),
                "source": "bybit_websocket",
                "raw_data": event_data
            }
            
            # Publish to Redis stream for existing pipeline integration
            await self._publish_to_redis_stream(standardized_event)
            
            logger.debug(f"💰 Processed Bybit wallet event: {currency} - {wallet_balance}")
            return standardized_event
            
        except Exception as e:
            logger.error(f"❌ Error processing Bybit wallet event: {e}")
            self.metrics["processing_errors"] += 1
            raise
    
    async def _publish_to_redis_stream(self, event_data: Dict[str, Any]) -> bool:
        """
        Publish event to Redis stream for processing by existing pipeline
        
        Args:
            event_data: Standardized event data
            
        Returns:
            True if published successfully, False otherwise
        """
        try:
            if not self.redis_client:
                logger.error("❌ Redis client not initialized")
                return False
            
            # Publish to fill stream
            stream_data = {
                "event_type": "bybit_websocket",
                "exchange": "bybit",
                "data": json.dumps(event_data),
                "timestamp": datetime.utcnow().isoformat()
            }
            
            await self.redis_client.xadd("trading:fills:stream", stream_data)
            self.metrics["events_published"] += 1
            
            logger.debug(f"📤 Published Bybit event to Redis stream: {event_data.get('event_type')}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Error publishing to Redis stream: {e}")
            return False
    
    async def handle_bybit_event(self, event_data: Dict[str, Any]) -> bool:
        """
        Handle incoming Bybit WebSocket event
        
        Args:
            event_data: Raw Bybit WebSocket event
            
        Returns:
            True if processed successfully, False otherwise
        """
        try:
            topic = event_data.get("topic")
            data = event_data.get("data", [])
            
            if not topic:
                logger.warning(f"⚠️ No topic in Bybit event: {event_data}")
                return False
            
            # Process events based on topic
            if topic == "order":
                for order in data:
                    await self.process_order_event(order)
            elif topic == "execution":
                for execution in data:
                    await self.process_execution_event(execution)
            elif topic == "position":
                for position in data:
                    await self.process_position_event(position)
            elif topic == "wallet":
                for wallet in data:
                    await self.process_wallet_event(wallet)
            else:
                logger.debug(f"📨 Unhandled Bybit topic: {topic}")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Error handling Bybit event: {e}")
            self.metrics["processing_errors"] += 1
            return False
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get consumer metrics"""
        return self.metrics.copy()
    
    def reset_metrics(self):
        """Reset consumer metrics"""
        self.metrics = {
            "events_received": 0,
            "order_events": 0,
            "execution_events": 0,
            "position_events": 0,
            "wallet_events": 0,
            "fills_detected": 0,
            "partial_fills_detected": 0,
            "events_published": 0,
            "processing_errors": 0,
            "last_processed_time": None
        }
        logger.info("📊 Bybit WebSocket Consumer metrics reset")
    
    async def cleanup(self):
        """Cleanup resources"""
        try:
            if self.redis_client:
                await self.redis_client.close()
                logger.info("✅ Bybit WebSocket Consumer Redis connection closed")
        except Exception as e:
            logger.error(f"❌ Error during Bybit WebSocket Consumer cleanup: {e}")
