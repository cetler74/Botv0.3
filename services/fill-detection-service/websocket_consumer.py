#!/usr/bin/env python3
"""
WebSocket Consumer for Fill Detection Service - Version 2.5.0
Handles real-time WebSocket events from exchange services
"""

import asyncio
import logging
from typing import Dict, Any, Optional
from datetime import datetime
import redis.asyncio as redis

logger = logging.getLogger(__name__)

# Redis Keys
REDIS_FILL_STREAM = "trading:fills:stream"

class WebSocketEventConsumer:
    """
    Consumes WebSocket events from exchange services and processes them
    Integrates with the existing Redis-based fill detection system
    """
    
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.metrics = {
            'events_received': 0,
            'execution_events': 0,
            'order_events': 0,
            'processing_errors': 0,
            'stream_emits': 0
        }
        
        logger.info("🔌 WebSocket Event Consumer initialized")
    
    async def process_execution_event(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process execution events from WebSocket stream"""
        try:
            self.metrics['events_received'] += 1
            event_type = event_data.get('event_type')
            
            logger.info(f"⚡ Processing WebSocket event: {event_type} for {event_data.get('symbol', 'unknown')}")
            
            if event_type == 'order_filled':
                self.metrics['execution_events'] += 1
                return await self._handle_order_filled_event(event_data)
            elif event_type == 'order_created':
                self.metrics['order_events'] += 1
                return await self._handle_order_created_event(event_data)
            elif event_type in ['order_cancelled', 'order_rejected', 'order_expired']:
                self.metrics['order_events'] += 1
                return await self._handle_order_status_event(event_data)
            else:
                logger.warning(f"⚠️ Unknown WebSocket event type: {event_type}")
                return {"status": "ignored", "reason": f"Unknown event type: {event_type}"}
                
        except Exception as e:
            logger.error(f"❌ Error processing WebSocket event: {e}")
            self.metrics['processing_errors'] += 1
            return {"status": "error", "error": str(e)}
    
    async def _handle_order_filled_event(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle order filled events from WebSocket"""
        try:
            # Transform WebSocket event to fill stream format
            fill_event = {
                "event_type": "websocket_fill",
                "order_id": event_data.get('client_order_id', event_data.get('order_id')),
                "exchange_order_id": event_data.get('order_id'),
                "exchange": event_data.get('exchange', 'binance'),
                "symbol": event_data.get('symbol'),
                "side": event_data.get('side'),
                "amount": event_data.get('cumulative_quantity', event_data.get('executed_quantity', 0)),
                "filled_amount": event_data.get('cumulative_quantity', event_data.get('executed_quantity', 0)),
                "avg_price": event_data.get('executed_price', 0),
                "fee_amount": event_data.get('fee_amount', 0),
                "fee_currency": event_data.get('fee_currency'),
                "trade_id": event_data.get('trade_id'),
                "timestamp": event_data.get('timestamp', datetime.utcnow().isoformat()),
                
                # WebSocket-specific metadata
                "source": "websocket",
                "is_full_fill": event_data.get('is_full_fill', event_data.get('order_status') == 'filled'),
                "execution_id": event_data.get('execution_id'),
                "transaction_time": event_data.get('transaction_time'),
                "cumulative_quote_quantity": event_data.get('cumulative_quote_quantity', 0),
                "is_maker": event_data.get('is_maker', False)
            }
            
            # DEBUG: Log the transformed event
            logger.info(f"🔄 [DEBUG] WebSocket fill event transformed: order_id={fill_event['order_id']}, "
                       f"fee_amount={fill_event['fee_amount']}, fee_currency={fill_event['fee_currency']}")
            
            # Emit to Redis stream for processing by existing pipeline
            message_id = await self.redis.xadd(REDIS_FILL_STREAM, fill_event)
            self.metrics['stream_emits'] += 1
            
            logger.info(f"✅ WebSocket fill event emitted to stream: {message_id}")
            
            return {
                "status": "processed",
                "message_id": message_id,
                "event_type": "websocket_fill"
            }
            
        except Exception as e:
            logger.error(f"❌ Error handling WebSocket fill event: {e}")
            return {"status": "error", "error": str(e)}
    
    async def _handle_order_created_event(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle new order creation events"""
        try:
            # Transform to acknowledgment event for monitoring
            ack_event = {
                "event_type": "order_acknowledged",
                "order_id": event_data.get('client_order_id', event_data.get('order_id')),
                "exchange_order_id": event_data.get('order_id'),
                "exchange": event_data.get('exchange', 'binance'),
                "symbol": event_data.get('symbol'),
                "side": event_data.get('side'),
                "amount": event_data.get('quantity', 0),
                "price": event_data.get('price', 0),
                "timestamp": event_data.get('timestamp', datetime.utcnow().isoformat()),
                "source": "websocket"
            }
            
            # Emit to Redis stream
            message_id = await self.redis.xadd(REDIS_FILL_STREAM, ack_event)
            self.metrics['stream_emits'] += 1
            
            logger.info(f"📝 Order acknowledgment event emitted: {message_id}")
            
            return {
                "status": "processed", 
                "message_id": message_id,
                "event_type": "order_acknowledged"
            }
            
        except Exception as e:
            logger.error(f"❌ Error handling order created event: {e}")
            return {"status": "error", "error": str(e)}
    
    async def _handle_order_status_event(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle order status change events (cancelled, rejected, expired)"""
        try:
            status_event = {
                "event_type": event_data.get('event_type', 'order_status_change'),
                "order_id": event_data.get('client_order_id', event_data.get('order_id')),
                "exchange_order_id": event_data.get('order_id'),
                "exchange": event_data.get('exchange', 'binance'),
                "symbol": event_data.get('symbol'),
                "status": event_data.get('status'),
                "filled_quantity": event_data.get('filled_quantity', 0),
                "remaining_quantity": event_data.get('remaining_quantity', 0),
                "timestamp": event_data.get('timestamp', datetime.utcnow().isoformat()),
                "source": "websocket"
            }
            
            # Emit to Redis stream for logging/monitoring
            message_id = await self.redis.xadd(REDIS_FILL_STREAM, status_event)
            self.metrics['stream_emits'] += 1
            
            logger.info(f"📊 Order status event emitted: {event_data.get('event_type')} - {message_id}")
            
            return {
                "status": "processed",
                "message_id": message_id,
                "event_type": event_data.get('event_type')
            }
            
        except Exception as e:
            logger.error(f"❌ Error handling order status event: {e}")
            return {"status": "error", "error": str(e)}
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get consumer metrics"""
        return {
            **self.metrics,
            'processing_rate': self.metrics['events_received'] / max(1, self.metrics.get('uptime_seconds', 1))
        }
    
    async def health_check(self) -> bool:
        """Check if WebSocket consumer is healthy"""
        try:
            await self.redis.ping()
            return True
        except Exception as e:
            logger.error(f"❌ WebSocket consumer health check failed: {e}")
            return False