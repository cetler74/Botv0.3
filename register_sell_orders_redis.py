#!/usr/bin/env python3
"""
Register sell orders in Redis for WebSocket tracking
This script manually registers the sell orders that were placed before the Redis registration fix
"""

import asyncio
import redis.asyncio as redis
import time
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Redis connection
REDIS_URL = "redis://localhost:6379"

# Sell orders that were filled on the exchange but not registered in Redis
SELL_ORDERS = [
    {
        "order_id": "1210470476",
        "exchange": "binance", 
        "symbol": "ADAUSDC",
        "side": "sell",
        "order_type": "limit",
        "amount": 49.7625,
        "price": 0.82811718,
        "trade_id": "c22c988c-3288-4dc0-aae8-f09019ee3352"  # From the trade record
    },
    {
        "order_id": "1210469505", 
        "exchange": "binance",
        "symbol": "ADAUSDC", 
        "side": "sell",
        "order_type": "limit",
        "amount": 100.0,
        "price": 0.82741725,
        "trade_id": "c22c988c-3288-4dc0-aae8-f09019ee3352"
    },
    {
        "order_id": "1210468816",
        "exchange": "binance", 
        "symbol": "ADAUSDC",
        "side": "sell", 
        "order_type": "limit",
        "amount": 100.0,
        "price": 0.82671732,
        "trade_id": "c22c988c-3288-4dc0-aae8-f09019ee3352"
    }
]

async def register_sell_orders():
    """Register sell orders in Redis for WebSocket tracking"""
    try:
        # Connect to Redis
        redis_client = redis.from_url(REDIS_URL)
        
        for order_data in SELL_ORDERS:
            order_id = order_data["order_id"]
            
            # Create order hash with all tracking data
            order_hash = {
                "order_id": str(order_id),
                "exchange": order_data["exchange"],
                "symbol": order_data["symbol"],
                "side": order_data["side"],
                "order_type": order_data["order_type"],
                "amount": str(order_data["amount"]),
                "price": str(order_data["price"]),
                "status": "pending",  # Will be updated to filled when WebSocket report comes in
                "created_at": str(time.time()),
                "updated_at": str(time.time()),
                "exchange_order_id": str(order_id),
                "client_order_id": "",
                "trade_id": order_data["trade_id"]
            }
            
            pipe = redis_client.pipeline()
            
            # 1. Store order state in Redis Hash
            pipe.hmset(f"orders:{order_id}", order_hash)
            pipe.expire(f"orders:{order_id}", 86400)  # 24-hour TTL
            
            # 2. Add to pending orders index
            pipe.zadd("orders_by_status:pending", {order_id: time.time()})
            
            # 3. Add to exchange-based index
            pipe.hset(f"exchange_orders:{order_data['exchange']}", order_id, "pending")
            
            # Execute all operations
            await pipe.execute()
            
            logger.info(f"✅ Registered sell order {order_id} in Redis for WebSocket tracking")
            
        logger.info(f"✅ Successfully registered {len(SELL_ORDERS)} sell orders in Redis")
        
    except Exception as e:
        logger.error(f"❌ Error registering sell orders in Redis: {e}")
        raise
    finally:
        if 'redis_client' in locals():
            await redis_client.close()

if __name__ == "__main__":
    asyncio.run(register_sell_orders())
