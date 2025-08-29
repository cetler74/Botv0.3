"""
Redis Order Manager - Queue-based order processing for Orchestrator
Replaces direct database order processing with Redis queues
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime
from typing import Dict, Any, Optional
import httpx
import redis.asyncio as redis

logger = logging.getLogger(__name__)

class RedisOrderManager:
    """Manages order processing via Redis queues"""
    
    def __init__(self, redis_url: str = "redis://redis:6379"):
        self.redis_url = redis_url
        self.redis_client: Optional[redis.Redis] = None
        self.order_queue_service_url = "http://order-queue-service:8007"
        self.fill_detection_service_url = "http://fill-detection-service:8008"
        
    async def initialize(self):
        """Initialize Redis connection"""
        try:
            self.redis_client = redis.Redis.from_url(self.redis_url, decode_responses=True)
            await self.redis_client.ping()
            logger.info("✅ Redis Order Manager initialized")
        except Exception as e:
            logger.error(f"❌ Failed to initialize Redis Order Manager: {e}")
            raise
    
    async def close(self):
        """Close Redis connection"""
        if self.redis_client:
            await self.redis_client.close()
    
    async def execute_trade_entry_async(
        self,
        signal: Dict[str, Any],
        exchange_name: str,
        pair: str,
        sanitized_position_size: float,
        trade_id: str,
        strategy_name: str
    ) -> bool:
        """
        Execute trade entry via Redis queue system
        Replaces the synchronous order processing with async queue-based processing
        """
        try:
            logger.info(f"🚀 Executing async trade entry: {pair} on {exchange_name}")
            
            # Emit trade lifecycle event - trade creation
            await self.emit_trade_lifecycle_event(
                trade_id, 
                "trade_created", 
                {
                    "exchange": exchange_name,
                    "symbol": pair,
                    "strategy": strategy_name,
                    "amount": sanitized_position_size,
                    "signal_confidence": signal.get('confidence', 0)
                }
            )
            
            # Create order request for queue
            order_request = {
                "signal": signal,
                "trade_id": trade_id,
                "exchange": exchange_name,
                "symbol": pair,
                "side": "buy" if signal.get("signal") == "buy" else "sell",
                "amount": sanitized_position_size,
                "strategy": strategy_name,
                "entry_reason": f"{strategy_name} strategy signal: {signal.get('signal', 'buy')} (confidence: {signal.get('confidence', 0):.2f})"
            }
            
            # Submit to order queue
            order_id = await self.submit_order_to_queue(order_request)
            
            if order_id:
                logger.info(f"✅ Order {order_id} submitted to queue for {pair} on {exchange_name}")
                
                # Emit order state change event
                await self.emit_order_state_change(
                    order_id, 
                    "created", 
                    "queued", 
                    {"trade_id": trade_id, "exchange": exchange_name, "symbol": pair}
                )
                
                # Emit trade lifecycle event - order queued
                await self.emit_trade_lifecycle_event(
                    trade_id, 
                    "order_queued", 
                    {"order_id": order_id, "queue_position": "pending"}
                )
                
                # Monitor order progress (non-blocking)
                asyncio.create_task(self.monitor_order_progress(order_id, trade_id))
                
                return True
            else:
                logger.error(f"❌ Failed to submit order to queue for {pair} on {exchange_name}")
                
                # Emit failure events
                await self.emit_trade_lifecycle_event(
                    trade_id, 
                    "order_failed", 
                    {"error": "Failed to submit to queue"}
                )
                
                return False
                
        except Exception as e:
            logger.error(f"❌ Error in async trade entry: {e}")
            
            # Emit error event
            await self.emit_trade_lifecycle_event(
                trade_id, 
                "error", 
                {"error": str(e), "stage": "order_creation"}
            )
            
            return False
    
    async def submit_order_to_queue(self, order_request: Dict[str, Any]) -> Optional[str]:
        """Submit order to Redis processing queue"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.order_queue_service_url}/api/v1/orders/enqueue",
                    json=order_request
                )
                
                if response.status_code in [200, 201]:
                    result = response.json()
                    order_id = result.get("order_id")
                    logger.info(f"📋 Order {order_id} queued successfully")
                    return order_id
                else:
                    logger.error(f"❌ Queue submission failed: {response.status_code} - {response.text}")
                    return None
                    
        except Exception as e:
            logger.error(f"❌ Error submitting order to queue: {e}")
            return None
    
    async def monitor_order_progress(self, order_id: str, trade_id: str):
        """Monitor order progress via Redis (non-blocking)"""
        try:
            max_wait_time = 120  # 2 minutes total
            check_interval = 5   # Check every 5 seconds
            checks = 0
            max_checks = max_wait_time // check_interval
            last_status = "queued"
            
            while checks < max_checks:
                status = await self.get_order_status(order_id)
                
                if not status:
                    logger.warning(f"⚠️ No status found for order {order_id}")
                    await self.emit_trade_lifecycle_event(
                        trade_id, 
                        "monitoring_lost", 
                        {"order_id": order_id, "checks_completed": checks}
                    )
                    break
                
                current_status = status.get("status")
                logger.info(f"📊 Order {order_id} status: {current_status}")
                
                # Emit state change if status changed
                if current_status != last_status:
                    await self.emit_order_state_change(
                        order_id, 
                        last_status, 
                        current_status, 
                        {"trade_id": trade_id, "check_number": checks}
                    )
                    
                    # Emit corresponding trade lifecycle event
                    if current_status == "PROCESSING":
                        await self.emit_trade_lifecycle_event(
                            trade_id, 
                            "order_processing", 
                            {"order_id": order_id}
                        )
                    elif current_status == "ACKNOWLEDGED":
                        await self.emit_trade_lifecycle_event(
                            trade_id, 
                            "order_acknowledged", 
                            {"order_id": order_id, "exchange_order_id": status.get("exchange_order_id")}
                        )
                    elif current_status == "FILLED":
                        await self.emit_trade_lifecycle_event(
                            trade_id, 
                            "order_filled", 
                            {"order_id": order_id, "exchange_order_id": status.get("exchange_order_id")}
                        )
                    
                    last_status = current_status
                
                if current_status in ["FILLED", "ACKNOWLEDGED"]:
                    logger.info(f"✅ Order {order_id} completed successfully")
                    await self.emit_trade_lifecycle_event(
                        trade_id, 
                        "order_completed", 
                        {
                            "order_id": order_id, 
                            "final_status": current_status,
                            "monitoring_duration_seconds": checks * check_interval
                        }
                    )
                    break
                elif current_status == "FAILED":
                    error_msg = status.get('error_message', 'Unknown error')
                    logger.error(f"❌ Order {order_id} failed: {error_msg}")
                    await self.emit_trade_lifecycle_event(
                        trade_id, 
                        "order_failed", 
                        {
                            "order_id": order_id, 
                            "error_message": error_msg,
                            "monitoring_duration_seconds": checks * check_interval
                        }
                    )
                    break
                
                checks += 1
                await asyncio.sleep(check_interval)
            
            if checks >= max_checks:
                logger.warning(f"⏱️ Order {order_id} monitoring timed out after {max_wait_time}s")
                await self.emit_trade_lifecycle_event(
                    trade_id, 
                    "monitoring_timeout", 
                    {
                        "order_id": order_id, 
                        "last_status": last_status,
                        "timeout_duration_seconds": max_wait_time
                    }
                )
                
        except Exception as e:
            logger.error(f"❌ Error monitoring order {order_id}: {e}")
            await self.emit_trade_lifecycle_event(
                trade_id, 
                "monitoring_error", 
                {"order_id": order_id, "error": str(e)}
            )
    
    async def get_order_status(self, order_id: str) -> Optional[Dict[str, Any]]:
        """Get order status from queue service"""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{self.order_queue_service_url}/api/v1/orders/{order_id}/status"
                )
                
                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 404:
                    return None
                else:
                    logger.error(f"❌ Status check failed: {response.status_code}")
                    return None
                    
        except Exception as e:
            logger.error(f"❌ Error getting order status: {e}")
            return None
    
    async def get_queue_statistics(self) -> Dict[str, Any]:
        """Get queue performance statistics"""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{self.order_queue_service_url}/api/v1/queue/stats"
                )
                
                if response.status_code == 200:
                    return response.json()
                else:
                    return {"error": f"Failed to get stats: {response.status_code}"}
                    
        except Exception as e:
            logger.error(f"❌ Error getting queue stats: {e}")
            return {"error": str(e)}
    
    async def emit_fill_event(self, fill_data: Dict[str, Any]) -> bool:
        """Emit a fill event to the fill detection service"""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{self.fill_detection_service_url}/api/v1/fills/emit",
                    json=fill_data
                )
                
                if response.status_code in [200, 201]:
                    logger.info(f"📡 Fill event emitted successfully")
                    return True
                else:
                    logger.error(f"❌ Fill emission failed: {response.status_code}")
                    return False
                    
        except Exception as e:
            logger.error(f"❌ Error emitting fill event: {e}")
            return False
    
    async def emit_order_state_change(
        self, 
        order_id: str, 
        old_state: str, 
        new_state: str, 
        metadata: Dict[str, Any] = None
    ) -> bool:
        """Emit order state change event to Redis stream"""
        try:
            if not self.redis_client:
                return False
                
            state_change_event = {
                "event_type": "order_state_change",
                "order_id": order_id,
                "old_state": old_state,
                "new_state": new_state,
                "timestamp": datetime.utcnow().isoformat(),
                "metadata": json.dumps(metadata or {})
            }
            
            await self.redis_client.xadd("trading:order_state:stream", state_change_event)
            logger.info(f"📡 State change event: {order_id} {old_state} -> {new_state}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Error emitting state change event: {e}")
            return False
    
    async def emit_trade_lifecycle_event(
        self, 
        trade_id: str, 
        event_type: str, 
        data: Dict[str, Any] = None
    ) -> bool:
        """Emit trade lifecycle events for real-time monitoring"""
        try:
            if not self.redis_client:
                return False
                
            lifecycle_event = {
                "event_type": "trade_lifecycle",
                "trade_id": trade_id,
                "lifecycle_event": event_type,  # created, filled, partial_fill, cancelled, closed
                "timestamp": datetime.utcnow().isoformat(),
                "data": json.dumps(data or {})
            }
            
            await self.redis_client.xadd("trading:trade_lifecycle:stream", lifecycle_event)
            logger.info(f"📡 Trade lifecycle event: {trade_id} - {event_type}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Error emitting trade lifecycle event: {e}")
            return False
    
    async def create_pending_trade_record(
        self,
        trade_id: str,
        signal: Dict[str, Any],
        exchange_name: str,
        pair: str,
        position_size: float,
        strategy_name: str
    ) -> bool:
        """Create a PENDING trade record before order execution"""
        try:
            # Create PENDING trade first (for immediate tracking)
            trade_data = {
                "trade_id": trade_id,
                "pair": pair,
                "expected_entry_price": signal.get("current_price", 0),
                "status": "PENDING", 
                "entry_time": datetime.utcnow().isoformat(),
                "exchange": exchange_name,
                "entry_reason": f"Queue-based {strategy_name} strategy signal",
                "position_size": position_size,
                "strategy": strategy_name
            }
            
            database_service_url = "http://database-service:8002"
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{database_service_url}/api/v1/trades",
                    json=trade_data
                )
                
                if response.status_code in [200, 201]:
                    logger.info(f"✅ PENDING trade record created: {trade_id}")
                    return True
                else:
                    logger.error(f"❌ Failed to create PENDING trade: {response.status_code}")
                    return False
                    
        except Exception as e:
            logger.error(f"❌ Error creating PENDING trade: {e}")
            return False
    
    async def is_healthy(self) -> bool:
        """Check if Redis order management system is healthy"""
        try:
            if not self.redis_client:
                return False
                
            # Check Redis connectivity
            await self.redis_client.ping()
            
            # Check queue service health
            async with httpx.AsyncClient(timeout=5.0) as client:
                queue_response = await client.get(f"{self.order_queue_service_url}/health")
                fill_response = await client.get(f"{self.fill_detection_service_url}/health")
                
                return (queue_response.status_code == 200 and 
                       fill_response.status_code == 200)
                
        except Exception as e:
            logger.error(f"❌ Health check failed: {e}")
            return False