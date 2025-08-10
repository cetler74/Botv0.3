import asyncio
import time
import httpx
from datetime import datetime
import logging
import uuid

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATABASE_SERVICE_URL = "http://database-service:8002"
EXCHANGE_SERVICE_URL = "http://exchange-service:8003"

async def get_filled_orders(exchange: str, symbol: str, hours_back: int = 1):
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{EXCHANGE_SERVICE_URL}/api/v1/trading/filled-orders/{exchange}",
                params={"symbol": symbol}
            )
            if response.status_code == 200:
                orders = response.json().get('orders', [])
                return [o for o in orders if o.get('status') == 'closed']
    except Exception as e:
        logger.error(f"Failed to fetch orders: {e}")
    return []

async def check_db_for_order(order_id: str):
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{DATABASE_SERVICE_URL}/api/v1/trades/by-order/{order_id}"
            )
            return response.status_code == 200
    except Exception as e:
        logger.error(f"Failed to check DB for order {order_id}: {e}")
        return False

async def create_missing_trade(order: dict):
    trade_data = {
        "trade_id": str(uuid.uuid4()),
        "entry_id": order['id'],
        "pair": order['symbol'],
        "exchange": order.get('exchange', ''),
        "status": "OPEN",
        "position_size": order.get('filled', 0),
        "entry_price": order.get('price', 0),
        "entry_time": order.get('timestamp'),
        "entry_reason": "Reconciled from exchange",
        "created_at": order.get('timestamp'),
        "strategy": "reconciled_orders"
    }
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{DATABASE_SERVICE_URL}/api/v1/trades",
                json=trade_data
            )
            if response.status_code in [200, 201]:
                logger.info(f"✅ Created missing trade for order {order['id']}")
            else:
                logger.error(f"Failed to create trade: {response.text}")
    except Exception as e:
        logger.error(f"Error creating trade: {e}")

async def reconcile_orders(exchange: str, symbol: str):
    logger.info(f"🔍 Starting reconciliation for {exchange} {symbol}")
    filled_orders = await get_filled_orders(exchange, symbol)
    logger.info(f"Found {len(filled_orders)} filled orders on exchange")
    for order in filled_orders:
        exists = await check_db_for_order(order['id'])
        if not exists:
            logger.warning(f"⚠️ Missing order in DB: {order['id']}")
            await create_missing_trade(order)

if __name__ == "__main__":
    asyncio.run(reconcile_orders("cryptocom", "AAVE/USD")) 