"""
Redis-Enhanced Realtime Order Manager
Implements the Redis-based order tracking system for realtime WebSocket fill detection
Based on Redis-Enhanced-Trading-Architecture.md
"""

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Dict, Any, Optional, List
import redis.asyncio as redis
import httpx

logger = logging.getLogger(__name__)

class RedisRealtimeOrderManager:
    """Redis-enhanced realtime order management for WebSocket fill detection"""
    
    def __init__(self, redis_url: str = "redis://redis:6379", database_service_url: str = "http://database-service:8002"):
        self.redis_url = redis_url
        self.database_service_url = database_service_url
        self.redis_client: Optional[redis.Redis] = None
        
    async def initialize(self):
        """Initialize Redis connection and consumer groups"""
        try:
            self.redis_client = redis.Redis.from_url(self.redis_url, decode_responses=True)
            await self.redis_client.ping()
            
            # Create consumer groups for order event processing
            try:
                await self.redis_client.xgroup_create("order_events", "order_processors", id="0", mkstream=True)
            except redis.ResponseError:
                # Group already exists
                pass
                
            try:
                await self.redis_client.xgroup_create("fill_events", "fill_processors", id="0", mkstream=True)
            except redis.ResponseError:
                # Group already exists
                pass
                
            logger.info("✅ Redis Realtime Order Manager initialized")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to initialize Redis Realtime Order Manager: {e}")
            return False
    
    async def close(self):
        """Close Redis connection"""
        if self.redis_client:
            await self.redis_client.close()
    
    async def register_order_for_tracking(self, exchange: str, order_id: str, order_data: Dict[str, Any]) -> bool:
        """Register an order in Redis for realtime tracking"""
        try:
            if not self.redis_client:
                return False
                
            # Create order hash with all tracking data
            order_hash = {
                "order_id": str(order_id),
                "exchange": exchange,
                "symbol": order_data.get("symbol", ""),
                "side": order_data.get("side", ""),
                "order_type": order_data.get("order_type", order_data.get("type", "")),
                "amount": str(order_data.get("amount", 0)),
                "price": str(order_data.get("price", 0)),
                "status": "pending",
                "created_at": str(time.time()),
                "updated_at": str(time.time()),
                "exchange_order_id": str(order_id),
                "client_order_id": order_data.get("client_order_id", ""),
                "trade_id": order_data.get("trade_id", str(uuid.uuid4()))
            }
            
            pipe = self.redis_client.pipeline()
            
            # 1. Store order state in Redis Hash (dual format for compatibility)
            client_order_id = order_data.get("client_order_id", "")
            if client_order_id:
                # Primary format for fill detection system
                pipe.hmset(f"order:{client_order_id}", order_hash)
                pipe.expire(f"order:{client_order_id}", 86400)  # 24-hour TTL
                
                # Secondary format for WebSocket events (legacy compatibility)
                pipe.hmset(f"orders:{order_id}", order_hash)
                pipe.expire(f"orders:{order_id}", 86400)  # 24-hour TTL
            else:
                # Fallback to exchange order ID if no client order ID
                pipe.hmset(f"order:{order_id}", order_hash)
                pipe.expire(f"order:{order_id}", 86400)  # 24-hour TTL
            
            # 2. Add to pending orders index
            pipe.zadd("orders_by_status:pending", {order_id: time.time()})
            
            # 3. Add to exchange-based index
            pipe.hset(f"exchange_orders:{exchange}", order_id, "pending")
            
            # 4. Add to tracked orders set (for fill detection)
            if client_order_id:
                pipe.sadd("tracked_orders", client_order_id)
                pipe.sadd(f"tracked_orders_{exchange}", client_order_id)
                # Add to symbol-specific tracking
                symbol = order_data.get("symbol", "").replace("/", "_")
                if symbol:
                    pipe.sadd(f"tracked_orders_{symbol}", client_order_id)
            
            # 5. Add order creation event to stream
            pipe.xadd("order_events", {
                "action": "order_created",
                "order_id": order_id,
                "exchange": exchange,
                "symbol": order_hash["symbol"],
                "status": "pending",
                "timestamp": str(time.time()),
                "priority": "NORMAL"
            })
            
            await pipe.execute()
            
            logger.info(f"✅ Registered order {order_id} for realtime tracking on {exchange}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to register order {order_id} for tracking: {e}")
            return False
    
    async def process_order_fill_callback(self, exchange: str, order_id: str, fill_data: Dict[str, Any]) -> bool:
        """Process WebSocket order fill callback and update Redis state"""
        try:
            if not self.redis_client:
                return False
                
            logger.info(f"🔄 Processing order fill callback for {order_id} on {exchange}")
            
            # Get current order state from Redis
            order_data = await self.redis_client.hmget(f"orders:{order_id}",
                "order_id", "exchange", "symbol", "side", "order_type", "amount", "price", "trade_id", "status", "strategy", "entry_reason"
            )
            
            if not all(order_data):
                logger.warning(f"⚠️ Order {order_id} not found in Redis - may be a direct exchange order")
                # Handle direct exchange orders by creating minimal tracking
                await self._handle_untracked_order_fill(exchange, order_id, fill_data)
                return True
            
            # Extract fill information
            status = fill_data.get("status", fill_data.get("order_status", "")).upper()
            executed_quantity = float(fill_data.get("executed_quantity", 0) or fill_data.get("cumulative_quantity", 0) or fill_data.get("filled_quantity", 0))
            executed_price = float(fill_data.get("executed_price", 0) or fill_data.get("avg_price", 0) or fill_data.get("price", 0))
            fees = float(fill_data.get("fee_amount", 0) or fill_data.get("commission_amount", 0) or 0)
            
            if status in ["FILLED", "filled"]:
                await self._process_filled_order(exchange, order_id, order_data, fill_data, executed_quantity, executed_price, fees)
            elif status in ["PARTIALLY_FILLED", "partially_filled"]:
                await self._process_partial_fill(exchange, order_id, order_data, fill_data, executed_quantity, executed_price, fees)
            else:
                logger.debug(f"📋 Order {order_id} status update: {status}")
                await self._update_order_status(order_id, status)
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to process order fill callback for {order_id}: {e}")
            return False
    
    async def _process_filled_order(self, exchange: str, order_id: str, order_data: List[str], fill_data: Dict[str, Any], executed_quantity: float, executed_price: float, fees: float):
        """Process a completely filled order"""
        try:
            logger.info(f"💰 Processing filled order {order_id}: {executed_quantity} @ {executed_price}")
            
            pipe = self.redis_client.pipeline()
            
            # Update order state to filled
            pipe.hmset(f"orders:{order_id}", {
                "status": "FILLED",
                "filled_amount": str(executed_quantity),
                "filled_price": str(executed_price),
                "fees": str(fees),
                "filled_at": str(time.time()),
                "updated_at": str(time.time())
            })
            
            # Remove from pending orders, add to filled
            pipe.zrem("orders_by_status:pending", order_id)
            pipe.zadd("orders_by_status:filled", {order_id: time.time()})
            
            # Update exchange index
            pipe.hset(f"exchange_orders:{exchange}", order_id, "filled")
            
            # Add fill event to stream for reliable processing
            pipe.xadd("fill_events", {
                "action": "order_filled",
                "order_id": order_id,
                "exchange": exchange,
                "symbol": order_data[2],  # symbol from order_data
                "side": order_data[3],    # side from order_data
                "executed_quantity": str(executed_quantity),
                "executed_price": str(executed_price),
                "fees": str(fees),
                "timestamp": str(time.time()),
                "priority": "HIGH"
            })
            
            await pipe.execute()
            
            # Create database record for filled order
            await self._create_database_record_from_fill(exchange, order_id, order_data, executed_quantity, executed_price, fees)
            
            logger.info(f"✅ Successfully processed filled order {order_id}")
            
        except Exception as e:
            logger.error(f"❌ Failed to process filled order {order_id}: {e}")
    
    async def _process_partial_fill(self, exchange: str, order_id: str, order_data: List[str], fill_data: Dict[str, Any], executed_quantity: float, executed_price: float, fees: float):
        """Process a partially filled order"""
        try:
            logger.info(f"📊 Processing partial fill for order {order_id}: {executed_quantity} @ {executed_price}")
            
            pipe = self.redis_client.pipeline()
            
            # Update order state
            pipe.hmset(f"orders:{order_id}", {
                "status": "PARTIALLY_FILLED",
                "partially_filled_amount": str(executed_quantity),
                "avg_filled_price": str(executed_price),
                "fees": str(fees),
                "updated_at": str(time.time())
            })
            
            # Add partial fill event
            pipe.xadd("order_events", {
                "action": "order_partially_filled",
                "order_id": order_id,
                "exchange": exchange,
                "executed_quantity": str(executed_quantity),
                "executed_price": str(executed_price),
                "timestamp": str(time.time()),
                "priority": "NORMAL"
            })
            
            await pipe.execute()
            
        except Exception as e:
            logger.error(f"❌ Failed to process partial fill for {order_id}: {e}")
    
    def _extract_trade_id_from_client_order_id(self, client_order_id: str) -> Optional[str]:
        """Extract trade_id from orchestrator-generated client_order_id"""
        try:
            # Format: oms{trade_prefix}M{timestamp}{random} or oms{trade_prefix}L{timestamp}{random}
            if not client_order_id or not client_order_id.startswith("oms"):
                return None
                
            # Extract the 8-character trade prefix after "oms"
            if len(client_order_id) < 12:  # oms + 8 chars + at least 1 more
                return None
                
            trade_prefix = client_order_id[3:11]  # Characters 3-10 (8 chars)
            
            # Reconstruct full trade_id by searching database for matching prefix
            return trade_prefix
            
        except Exception as e:
            logger.debug(f"Failed to extract trade_id from client_order_id {client_order_id}: {e}")
            return None

    async def _find_trade_by_prefix(self, trade_prefix: str, exchange: str, side: str) -> Optional[Dict[str, Any]]:
        """Find existing trade by trade_id prefix"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Get all open trades for this exchange
                response = await client.get(f"{self.database_service_url}/api/v1/trades/open")
                if response.status_code == 200:
                    open_trades = response.json().get("trades", [])
                    
                    # Find trade with matching prefix
                    for trade in open_trades:
                        trade_id = trade.get("trade_id", "")
                        trade_exchange = trade.get("exchange", "")
                        
                        # Check if trade_id starts with our prefix and matches exchange
                        if (trade_id.replace('-', '')[:8] == trade_prefix and 
                            trade_exchange == exchange):
                            
                            # For sell orders, this should be the original buy trade to close
                            if side.lower() == "sell":
                                return trade
                                
                    return None
                else:
                    logger.error(f"Failed to fetch open trades: {response.status_code}")
                    return None
                    
        except Exception as e:
            logger.error(f"Error finding trade by prefix {trade_prefix}: {e}")
            return None

    async def _close_existing_trade(self, existing_trade: Dict[str, Any], exit_order_id: str, exit_price: float, exit_quantity: float, exit_fees: float) -> bool:
        """Close an existing trade with proper PnL calculation"""
        try:
            trade_id = existing_trade["trade_id"]
            entry_price = existing_trade["entry_price"]
            position_size = existing_trade["position_size"]
            entry_fees = existing_trade.get("fees", 0.0)
            
            # Calculate realized PnL: (exit_price - entry_price) * position_size - total_fees
            price_diff = exit_price - entry_price
            realized_pnl = (price_diff * position_size) - (entry_fees + exit_fees)
            
            logger.info(f"💰 Calculating PnL for trade {trade_id}: "
                       f"({exit_price} - {entry_price}) * {position_size} - fees = ${realized_pnl:.2f}")
            
            # Use centralized trade closure API
            trade_closure_data = {
                "exit_price": exit_price,
                "exit_order_id": exit_order_id,
                "exit_time": datetime.utcnow().isoformat(),
                "fees": exit_fees,
                "exit_reason": "redis_websocket_fill_detection",
                "validated_by_exchange": True
            }
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                try:
                    # Use centralized closure endpoint
                    trade_response = await client.post(f"{self.database_service_url}/api/v1/trades/{trade_id}/close", json=trade_closure_data)
                    if trade_response.status_code == 200:
                        result = trade_response.json()
                        logger.info(f"✅ CENTRALIZED REDIS CLOSURE: Trade {trade_id} closed with "
                                   f"exit_price=${result['exit_price']:.4f}, "
                                   f"PnL=${result['realized_pnl']:.2f} ({result['pnl_percentage']:.2f}%)")
                    else:
                        logger.error(f"❌ Centralized closure failed for trade {trade_id}: {trade_response.status_code}")
                        # Fallback to direct update
                        trade_update_data = {
                            "status": "CLOSED",
                            "exit_price": exit_price,
                            "exit_id": exit_order_id,
                            "exit_time": datetime.utcnow().isoformat(),
                            "realized_pnl": realized_pnl,
                            "exit_reason": "redis_websocket_fill_detection_fallback"
                        }
                        
                        fallback_response = await client.put(f"{self.database_service_url}/api/v1/trades/{trade_id}", json=trade_update_data)
                        if fallback_response.status_code != 200:
                            logger.error(f"❌ Both centralized and fallback closure failed for trade {trade_id}: {fallback_response.status_code}")
                            
                except Exception as centralized_error:
                    logger.error(f"❌ Centralized closure error for {trade_id}: {centralized_error}")
                    # Fallback to direct update
                    trade_update_data = {
                        "status": "CLOSED",
                        "exit_price": exit_price,
                        "exit_id": exit_order_id,
                        "exit_time": datetime.utcnow().isoformat(),
                        "realized_pnl": realized_pnl,
                        "exit_reason": "redis_websocket_fill_detection_fallback"
                    }
                    
                    trade_response = await client.put(f"{self.database_service_url}/api/v1/trades/{trade_id}", json=trade_update_data)
                    if trade_response.status_code != 200:
                        logger.error(f"❌ Fallback closure failed for trade {trade_id}: {trade_response.status_code}")
                    return False
                
                # Create the exit order record
                exit_order_data = {
                    "order_id": str(exit_order_id),
                    "trade_id": trade_id,
                    "exchange": existing_trade["exchange"],
                    "symbol": existing_trade["pair"],  # Use pair from trade
                    "order_type": "market",  # Assume market order for WebSocket fills
                    "side": "sell",
                    "amount": exit_quantity,
                    "price": exit_price,
                    "filled_amount": exit_quantity,
                    "filled_price": exit_price,
                    "status": "FILLED",
                    "fees": exit_fees,
                    "fee_rate": 0.0,
                    "exchange_order_id": str(exit_order_id),
                    "client_order_id": "",
                    "error_message": "REDIS_WEBSOCKET_FILL_DETECTION: Exit order filled via Redis-enhanced WebSocket system"
                }
                
                order_response = await client.post(f"{self.database_service_url}/api/v1/orders", json=exit_order_data)
                if order_response.status_code != 200:
                    logger.error(f"❌ Failed to create exit order record for {exit_order_id}: {order_response.status_code}")
                    # Trade was closed but order record failed - log but don't fail
                
                logger.info(f"✅ Successfully closed trade {trade_id}: "
                          f"Entry ${entry_price:.2f} → Exit ${exit_price:.2f} = "
                          f"${realized_pnl:.2f} realized PnL")
                return True
                
        except Exception as e:
            logger.error(f"❌ Error closing existing trade {existing_trade.get('trade_id')}: {e}")
            return False

    async def _handle_untracked_order_fill(self, exchange: str, order_id: str, fill_data: Dict[str, Any]):
        """Handle fills for orders not tracked in Redis (direct exchange orders)"""
        try:
            logger.info(f"🔍 Handling untracked order fill: {order_id} on {exchange}")
            
            # Extract basic info from fill_data
            symbol = fill_data.get("symbol", "UNKNOWN")
            side = fill_data.get("side", "unknown")
            executed_quantity = float(fill_data.get("executed_quantity", 0) or fill_data.get("cumulative_quantity", 0) or 0)
            executed_price = float(fill_data.get("executed_price", 0) or fill_data.get("avg_price", 0) or 0)
            fees = float(fill_data.get("fee_amount", 0) or fill_data.get("commission_amount", 0) or 0)
            client_order_id = fill_data.get("client_order_id", "")
            
            # 🔥 CRITICAL FIX: Check if this is a real orchestrator order
            trade_prefix = None
            existing_trade = None
            
            if client_order_id:
                trade_prefix = self._extract_trade_id_from_client_order_id(client_order_id)
                if trade_prefix and side.lower() == "sell":
                    # This might be a sell order closing an existing trade
                    existing_trade = await self._find_trade_by_prefix(trade_prefix, exchange, side)
                    
                    if existing_trade:
                        logger.info(f"🎯 Found existing trade to close: {existing_trade['trade_id']} (prefix: {trade_prefix})")
                        
                        # 🛡️ CRITICAL PRIORITY CHECK: Trailing stop FIRST, profit protection only if trailing stop not active
                        trade_id = existing_trade['trade_id']
                        pair = existing_trade.get('pair', '')
                        entry_price = float(existing_trade.get('entry_price', 0))
                        
                        # Calculate current profit
                        if entry_price > 0:
                            profit_pct = (executed_price - entry_price) / entry_price
                            trailing_threshold = 0.003  # 0.3% - current config value
                            
                            # Check if this trade should be managed by trailing stop system
                            should_use_trailing_stop = (
                                profit_pct >= trailing_threshold and  # Above activation threshold
                                existing_trade.get('exit_id') is None and  # No trailing stop active yet
                                executed_quantity >= 5.0  # Significant order size (not micro-order)
                            )
                            
                            if should_use_trailing_stop:
                                logger.warning(f"[Trade {trade_id}] [RedisWebSocket] 🛡️ TRAILING STOP PRIORITY: Profit {profit_pct:.3%} >= {trailing_threshold:.1%}")
                                logger.warning(f"[Trade {trade_id}] [RedisWebSocket] 🚫 BLOCKING REDIS CLOSURE: Let orchestrator handle trailing stop activation")
                                logger.warning(f"[Trade {trade_id}] [RedisWebSocket] 📊 Order details: qty={executed_quantity}, price={executed_price}")
                                
                                # Do not close the trade here - let the orchestrator's trailing stop system handle it
                                return False
                        
                        return await self._close_existing_trade(existing_trade, order_id, executed_price, executed_quantity, fees)
            
            logger.info(f"💡 No existing trade found - creating new trade record for order {order_id}")
            
            # 🔥 CRITICAL FIX: Use embedded trade_id from client_order_id if available
            if trade_prefix and client_order_id.startswith("oms"):
                # Reconstruct full trade_id using the embedded prefix from orchestrator
                reconstructed_trade_id = f"{trade_prefix}-0000-0000-0000-{trade_prefix}0000"
                logger.info(f"🔧 Using reconstructed trade_id: {reconstructed_trade_id} from client_order_id prefix")
                trade_id_to_use = reconstructed_trade_id
            else:
                # Generate new random UUID for non-orchestrator orders
                trade_id_to_use = str(uuid.uuid4())
            
            # Create minimal Redis tracking
            order_hash = {
                "order_id": str(order_id),
                "exchange": exchange,
                "symbol": symbol,
                "side": side.lower(),
                "order_type": "unknown",
                "amount": str(executed_quantity),
                "price": str(executed_price),
                "status": "FILLED",
                "filled_amount": str(executed_quantity),
                "filled_price": str(executed_price),
                "fees": str(fees),
                "created_at": str(time.time()),
                "filled_at": str(time.time()),
                "updated_at": str(time.time()),
                "exchange_order_id": str(order_id),
                "trade_id": trade_id_to_use
            }
            
            pipe = self.redis_client.pipeline()
            
            # Store minimal order state
            pipe.hmset(f"orders:{order_id}", order_hash)
            pipe.expire(f"orders:{order_id}", 86400)
            
            # Add to filled orders index
            pipe.zadd("orders_by_status:filled", {order_id: time.time()})
            pipe.hset(f"exchange_orders:{exchange}", order_id, "filled")
            
            # Add fill event
            pipe.xadd("fill_events", {
                "action": "untracked_order_filled",
                "order_id": order_id,
                "exchange": exchange,
                "symbol": symbol,
                "side": side,
                "executed_quantity": str(executed_quantity),
                "executed_price": str(executed_price),
                "fees": str(fees),
                "timestamp": str(time.time()),
                "priority": "HIGH"
            })
            
            await pipe.execute()
            
            # Create database record
            order_data = [str(order_id), exchange, symbol, side.lower(), "unknown", str(executed_quantity), str(executed_price), trade_id_to_use, "FILLED"]
            await self._create_database_record_from_fill(exchange, order_id, order_data, executed_quantity, executed_price, fees)
            
            logger.info(f"✅ Successfully handled untracked order fill: {order_id}")
            
        except Exception as e:
            logger.error(f"❌ Failed to handle untracked order fill {order_id}: {e}")
    
    async def _create_database_record_from_fill(self, exchange: str, order_id: str, order_data: List[str], executed_quantity: float, executed_price: float, fees: float):
        """Create or update database record from Redis order data after fill"""
        try:
            # Format symbol properly
            symbol = order_data[2]  # symbol from Redis data
            if not "/" in symbol and symbol.upper() != "UNKNOWN":
                # Add proper formatting for common pairs
                if symbol.endswith("USDC"):
                    symbol = symbol.replace("USDC", "/USDC")
                elif symbol.endswith("USDT"):
                    symbol = symbol.replace("USDT", "/USDT")
                elif symbol.endswith("USD"):
                    symbol = symbol.replace("USD", "/USD")
            
            # Check if order already exists in database
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Check for existing order
                existing_order_response = await client.get(f"{self.database_service_url}/api/v1/orders")
                if existing_order_response.status_code == 200:
                    existing_orders = existing_order_response.json().get("orders", [])
                    existing_order = next((o for o in existing_orders if o.get("order_id") == str(order_id)), None)
                    
                    if existing_order:
                        # Update existing order with correct values from WebSocket
                        logger.info(f"🔄 Updating existing order {order_id} with WebSocket data: {executed_quantity} @ {executed_price}")
                        
                        update_order_data = {
                            "filled_amount": executed_quantity,
                            "filled_price": executed_price,
                            "price": executed_price,
                            "amount": executed_quantity,
                            "status": "FILLED",
                            "fees": fees,
                            "error_message": "REDIS_WEBSOCKET_FILL_DETECTION: Order updated with correct WebSocket execution data"
                        }
                        
                        order_update_response = await client.put(f"{self.database_service_url}/api/v1/orders/{order_id}", json=update_order_data)
                        if order_update_response.status_code != 200:
                            logger.error(f"❌ Failed to update order record for {order_id}: {order_update_response.status_code}")
                            return False
                        
                        # Check if this is a sell order (exit order)
                        if existing_order.get("side") == "sell" and existing_order.get("trade_id"):
                            # This is a sell order - update the trade to CLOSED
                            trade_id = existing_order["trade_id"]
                            logger.info(f"🔄 Processing sell order {order_id} - updating trade {trade_id} to CLOSED")
                            
                            # 🛡️ CRITICAL PRIORITY CHECK: Trailing stop FIRST, profit protection only if trailing stop not active
                            try:
                                trade_response = await client.get(f"{self.database_service_url}/api/v1/trades/{trade_id}")
                                if trade_response.status_code == 200:
                                    trade_data = trade_response.json()
                                    entry_price = float(trade_data.get('entry_price', 0))
                                    exit_id = trade_data.get('exit_id')
                                    
                                    # Calculate current profit
                                    if entry_price > 0:
                                        profit_pct = (executed_price - entry_price) / entry_price
                                        trailing_threshold = 0.003  # 0.3% - current config value
                                        
                                        # Check if this trade should be managed by trailing stop system
                                        should_use_trailing_stop = (
                                            profit_pct >= trailing_threshold and  # Above activation threshold
                                            exit_id is None and  # No trailing stop active yet
                                            executed_quantity >= 5.0  # Significant order size (not micro-order)
                                        )
                                        
                                        # 🚨 CRITICAL FIX: Check if this is a trailing stop order that was already filled
                                        # If the order_id matches the trade's exit_id, this IS the trailing stop order being filled
                                        is_trailing_stop_order_filled = (str(order_id) == str(exit_id))
                                        
                                        if should_use_trailing_stop and not is_trailing_stop_order_filled:
                                            logger.warning(f"[Trade {trade_id}] [RedisWebSocketExisting] 🛡️ TRAILING STOP PRIORITY: Profit {profit_pct:.3%} >= {trailing_threshold:.1%}")
                                            logger.warning(f"[Trade {trade_id}] [RedisWebSocketExisting] 🚫 BLOCKING REDIS CLOSURE: Let orchestrator handle trailing stop activation")
                                            logger.warning(f"[Trade {trade_id}] [RedisWebSocketExisting] 📊 Order details: qty={executed_quantity}, price={executed_price}")
                                            
                                            # Do not close the trade here - let the orchestrator's trailing stop system handle it
                                            return False
                                        elif is_trailing_stop_order_filled:
                                            logger.info(f"[Trade {trade_id}] [RedisWebSocketExisting] 🎯 TRAILING STOP ORDER FILLED: Order {order_id} is the trailing stop order for this trade")
                                            logger.info(f"[Trade {trade_id}] [RedisWebSocketExisting] ✅ PROCEEDING WITH CLOSURE: This is the expected trailing stop fill")
                            except Exception as priority_check_error:
                                logger.warning(f"[Trade {trade_id}] [RedisWebSocketExisting] ⚠️ Priority check failed: {priority_check_error}, proceeding with closure")
                            
                            # CRITICAL FIX: Use immediate trade closure
                            success = await self.close_trade_immediately(trade_id, executed_price, str(order_id), fees)
                            if success:
                                logger.info(f"✅ CRITICAL FIX: Trade {trade_id} closed successfully")
                                return True
                            else:
                                logger.error(f"❌ CRITICAL FIX: Failed to close trade {trade_id}")
                                return False
                        else:
                            # This is a buy order - check for existing trade and update it
                            existing_trade_response = await client.get(f"{self.database_service_url}/api/v1/trades")
                            if existing_trade_response.status_code == 200:
                                existing_trades = existing_trade_response.json().get("trades", [])
                                existing_trade = next((t for t in existing_trades if t.get("entry_id") == str(order_id)), None)
                                
                                if existing_trade:
                                    # Update existing trade with correct values
                                    logger.info(f"🔄 Updating existing trade for order {order_id} with correct WebSocket data")
                                    
                                    update_trade_data = {
                                        "entry_price": executed_price,
                                        "position_size": executed_quantity,
                                        "fees": fees,
                                        "highest_price": executed_price,
                                        "entry_reason": "REDIS_WEBSOCKET_FILL_DETECTION: Order updated with correct WebSocket execution data"
                                    }
                                    
                                    trade_update_response = await client.put(f"{self.database_service_url}/api/v1/trades/{existing_trade['id']}", json=update_trade_data)
                                    if trade_update_response.status_code == 200:
                                        logger.info(f"✅ Database records updated for filled order: {order_id} (order + trade)")
                                        return True
                                    else:
                                        logger.error(f"❌ Failed to update trade record for {order_id}: {trade_update_response.status_code}")
                                        return False
                                else:
                                    # Create new trade record if none exists
                                    logger.info(f"📝 Creating new trade record for existing order {order_id}")
                                    return await self._create_new_trade_record(client, order_id, symbol, exchange, executed_quantity, executed_price, fees)
                            else:
                                logger.error(f"❌ Failed to fetch existing trades for {order_id}")
                                return False
                    else:
                        # Create new order and trade records
                        logger.info(f"📝 Creating new database records for order {order_id}")
                        return await self._create_new_order_and_trade_records(client, order_id, order_data, symbol, exchange, executed_quantity, executed_price, fees)
                else:
                    logger.error(f"❌ Failed to fetch existing orders for {order_id}")
                    return False
                    
        except Exception as e:
            logger.error(f"❌ Error creating/updating database records for order {order_id}: {e}")
            return False
    

    async def close_trade_immediately(self, trade_id: str, exit_price: float, exit_order_id: str, fees: float = 0.0):
        """Close trade immediately without any blocking logic - CRITICAL FIX"""
        try:
            logger.info(f"🔧 CRITICAL FIX: Closing trade {trade_id} immediately")
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Get trade details for PnL calculation
                trade_response = await client.get(f"{self.database_service_url}/api/v1/trades/{trade_id}")
                if trade_response.status_code != 200:
                    logger.error(f"❌ Failed to get trade details for {trade_id}")
                    return False
                
                trade_data = trade_response.json()
                entry_price = float(trade_data.get('entry_price', 0))
                position_size = float(trade_data.get('position_size', 0))
                entry_fees = float(trade_data.get('fees', 0))
                
                # Calculate realized PnL
                if entry_price > 0 and position_size > 0:
                    gross_pnl = (exit_price - entry_price) * position_size
                    total_fees = entry_fees + fees
                    realized_pnl = gross_pnl - total_fees
                else:
                    realized_pnl = 0.0
                
                # Close trade immediately
                trade_closure_data = {
                    "status": "CLOSED",
                    "exit_price": exit_price,
                    "exit_id": exit_order_id,
                    "exit_time": datetime.utcnow().isoformat(),
                    "realized_pnl": realized_pnl,
                    "exit_reason": "CRITICAL_FIX: Immediate closure for filled exit order",
                    "updated_at": datetime.utcnow().isoformat()
                }
                
                # Try centralized closure first
                closure_response = await client.post(f"{self.database_service_url}/api/v1/trades/{trade_id}/close", json=trade_closure_data)
                if closure_response.status_code == 200:
                    logger.info(f"✅ CRITICAL FIX: Trade {trade_id} closed via centralized API")
                    return True
                
                # Fallback to direct update
                update_response = await client.put(f"{self.database_service_url}/api/v1/trades/{trade_id}", json=trade_closure_data)
                if update_response.status_code == 200:
                    logger.info(f"✅ CRITICAL FIX: Trade {trade_id} closed via direct update")
                    return True
                else:
                    logger.error(f"❌ CRITICAL FIX: Failed to close trade {trade_id}: {update_response.status_code}")
                    return False
                    
        except Exception as e:
            logger.error(f"❌ CRITICAL FIX: Error closing trade {trade_id}: {e}")
            return False
    
    async def _create_new_order_and_trade_records(self, client, order_id: str, order_data: List[str], symbol: str, exchange: str, executed_quantity: float, executed_price: float, fees: float):
        """Create new order and trade records"""
        try:
            # Create order record
            db_order_data = {
                "order_id": str(order_id),
                "trade_id": order_data[7],  # trade_id from Redis data
                "exchange": exchange,
                "symbol": symbol,
                "order_type": "market" if order_data[4] in ["market", "unknown"] else "limit",
                "side": order_data[3],  # side from Redis data
                "amount": float(order_data[5]),  # amount from Redis data
                "price": executed_price,
                "filled_amount": executed_quantity,
                "filled_price": executed_price,
                "status": "FILLED",
                "fees": fees,
                "fee_rate": 0.0,
                "exchange_order_id": str(order_id),
                "client_order_id": "",
                "error_message": "REDIS_WEBSOCKET_FILL_DETECTION: Order filled detected via Redis-enhanced WebSocket system"
            }
            
            # Create trade record for exit cycle
            trade_id = order_data[7]  # Use the same trade_id
            db_trade_data = {
                "trade_id": trade_id,
                "pair": symbol,
                "exchange": exchange,
                "entry_price": executed_price,
                "exit_price": None,
                "status": "OPEN",  # Mark as OPEN for exit cycle
                "entry_id": str(order_id),
                "exit_id": None,
                "entry_time": datetime.utcnow().isoformat(),
                "exit_time": None,
                "unrealized_pnl": 0.0,
                "realized_pnl": 0.0,
                "highest_price": executed_price,  # Initialize with entry price
                "profit_protection": "inactive",
                "profit_protection_trigger": None,
                "trail_stop": "inactive",
                "trail_stop_trigger": None,
                "entry_reason": order_data[10] or "REDIS_WEBSOCKET_FILL_DETECTION: Order filled detected via Redis-enhanced WebSocket system",
                "exit_reason": None,
                "position_size": executed_quantity,
                "fees": fees,
                "strategy": order_data[9] or "websocket_detection"
            }
            
            # Create order record
            order_response = await client.post(f"{self.database_service_url}/api/v1/orders", json=db_order_data)
            if order_response.status_code != 200:
                logger.error(f"❌ Failed to create order record for {order_id}: {order_response.status_code}")
                return False
            
            # Create trade record
            trade_response = await client.post(f"{self.database_service_url}/api/v1/trades", json=db_trade_data)
            if trade_response.status_code == 200:
                logger.info(f"✅ Database records created for filled order: {order_id} (order + trade)")
                return True
            else:
                logger.error(f"❌ Failed to create trade record for {order_id}: {trade_response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Error creating new database records for order {order_id}: {e}")
            return False
    
    async def _create_new_trade_record(self, client, order_id: str, symbol: str, exchange: str, executed_quantity: float, executed_price: float, fees: float):
        """Create new trade record for existing order"""
        try:
            trade_id = str(uuid.uuid4())  # Generate new trade_id
            db_trade_data = {
                "trade_id": trade_id,
                "pair": symbol,
                "exchange": exchange,
                "entry_price": executed_price,
                "exit_price": None,
                "status": "OPEN",  # Mark as OPEN for exit cycle
                "entry_id": str(order_id),
                "exit_id": None,
                "entry_time": datetime.utcnow().isoformat(),
                "exit_time": None,
                "unrealized_pnl": 0.0,
                "realized_pnl": 0.0,
                "highest_price": executed_price,  # Initialize with entry price
                "profit_protection": "inactive",
                "profit_protection_trigger": None,
                "trail_stop": "inactive",
                "trail_stop_trigger": None,
                "entry_reason": order_data[10] or "REDIS_WEBSOCKET_FILL_DETECTION: Order filled detected via Redis-enhanced WebSocket system",
                "exit_reason": None,
                "position_size": executed_quantity,
                "fees": fees,
                "strategy": order_data[9] or "websocket_detection"
            }
            
            # Create trade record
            trade_response = await client.post(f"{self.database_service_url}/api/v1/trades", json=db_trade_data)
            if trade_response.status_code == 200:
                logger.info(f"✅ Trade record created for existing order: {order_id}")
                return True
            else:
                logger.error(f"❌ Failed to create trade record for {order_id}: {trade_response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Error creating new trade record for order {order_id}: {e}")
            return False
    
    async def _update_order_status(self, order_id: str, status: str):
        """Update order status in Redis (dual format for compatibility)"""
        try:
            # Update both key formats for compatibility
            status_update = {
                "status": status,
                "updated_at": str(time.time())
            }
            
            # First, get the client_order_id from the legacy format
            client_order_id = None
            try:
                order_data = await self.redis_client.hgetall(f"orders:{order_id}")
                client_order_id = order_data.get("client_order_id")
            except:
                pass  # Continue if legacy format doesn't exist
            
            # Now update both formats in a pipeline
            pipe = self.redis_client.pipeline()
            
            # Update legacy format
            pipe.hmset(f"orders:{order_id}", status_update)
            
            # Update client_order_id format if it exists
            if client_order_id:
                pipe.hmset(f"order:{client_order_id}", status_update)
            
            # Add status update event
            pipe.xadd("order_events", {
                "action": "order_status_update",
                "order_id": order_id,
                "status": status,
                "timestamp": str(time.time()),
                "priority": "LOW"
            })
            
            await pipe.execute()
            
        except Exception as e:
            logger.error(f"❌ Failed to update order status for {order_id}: {e}")
    
    async def get_order_state(self, order_id: str) -> Optional[Dict[str, Any]]:
        """Get complete order state from Redis"""
        try:
            if not self.redis_client:
                return None
                
            order_data = await self.redis_client.hgetall(f"orders:{order_id}")
            return order_data if order_data else None
            
        except Exception as e:
            logger.error(f"❌ Failed to get order state for {order_id}: {e}")
            return None
    
    async def get_pending_orders(self, exchange: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all pending orders, optionally filtered by exchange"""
        try:
            if not self.redis_client:
                return []
                
            # Get pending order IDs
            pending_order_ids = await self.redis_client.zrange("orders_by_status:pending", 0, -1)
            
            if not pending_order_ids:
                return []
            
            orders = []
            for order_id in pending_order_ids:
                order_data = await self.redis_client.hgetall(f"orders:{order_id}")
                if order_data and (not exchange or order_data.get("exchange") == exchange):
                    orders.append(order_data)
            
            return orders
            
        except Exception as e:
            logger.error(f"❌ Failed to get pending orders: {e}")
            return []
    
    async def cleanup_expired_orders(self):
        """Clean up expired orders from indexes"""
        try:
            if not self.redis_client:
                return
                
            current_time = time.time()
            cutoff_time = current_time - 86400  # 24 hours ago
            
            # Clean up old pending orders
            await self.redis_client.zremrangebyscore("orders_by_status:pending", 0, cutoff_time)
            await self.redis_client.zremrangebyscore("orders_by_status:filled", 0, cutoff_time - 3600)  # Keep filled for 1 hour extra
            
            logger.debug("🧹 Cleaned up expired orders from Redis indexes")
            
        except Exception as e:
            logger.error(f"❌ Failed to cleanup expired orders: {e}")

# Global instance
redis_realtime_manager = RedisRealtimeOrderManager()