#!/usr/bin/env python3
"""
Order Queue Service - Redis-based Order Processing
Handles order lifecycle with Redis queues for reliability and performance
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from urllib.parse import quote
import httpx
import redis.asyncio as redis
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import os
from contextlib import asynccontextmanager

CONFIG_SERVICE_URL = os.getenv("CONFIG_SERVICE_URL", "http://config-service:8001")
PRICE_FEED_SERVICE_URL = os.getenv("PRICE_FEED_SERVICE_URL", "http://price-feed-service:8007")
EXCHANGE_SERVICE_URL = os.getenv("EXCHANGE_SERVICE_URL", "http://exchange-service:8003")
SIM_MODE_CACHE_SECONDS = 15

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Redis Keys
REDIS_ORDER_QUEUE = "trading:orders:pending"
REDIS_ORDER_STATUS = "trading:orders:status:{order_id}"  
REDIS_FILL_STREAM = "trading:fills:stream"
REDIS_WORKER_HEARTBEAT = "trading:workers:heartbeat"
REDIS_CIRCUIT_BREAKER = "trading:circuit_breaker:{exchange}"

# Data Models
class OrderRequest(BaseModel):
    signal: Dict[str, Any]
    trade_id: str
    exchange: str
    symbol: str
    side: str
    amount: float
    strategy: str
    entry_reason: str

class OrderStatus(BaseModel):
    order_id: str
    status: str  # QUEUED, PROCESSING, ACKNOWLEDGED, FILLED, FAILED
    exchange_order_id: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime

# Global Redis connection
redis_client: Optional[redis.Redis] = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan management"""
    global redis_client
    
    # Startup
    redis_client = redis.Redis(host='redis', port=6379, decode_responses=True)
    
    # Test connection
    try:
        await redis_client.ping()
        logger.info("✅ Connected to Redis successfully")
    except Exception as e:
        logger.error(f"❌ Failed to connect to Redis: {e}")
        raise
    
    # Start background workers
    asyncio.create_task(order_processor_worker())
    asyncio.create_task(heartbeat_worker())
    
    yield
    
    # Shutdown
    if redis_client:
        await redis_client.close()

# Initialize FastAPI
app = FastAPI(
    title="Order Queue Service",
    description="Redis-based order processing with fault tolerance",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class OrderQueueManager:
    """Manages Redis-based order queue operations"""
    
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.worker_id = f"worker_{uuid.uuid4().hex[:8]}"
        self.config_service_url = CONFIG_SERVICE_URL
        self._simulation_mode: Optional[bool] = None
        self._simulation_mode_at: Optional[datetime] = None

    async def simulation_mode_enabled(self) -> bool:
        """True when config trading.mode is simulation — no live exchange orders."""
        now = datetime.utcnow()
        if (
            self._simulation_mode is not None
            and self._simulation_mode_at is not None
            and (now - self._simulation_mode_at).total_seconds() < SIM_MODE_CACHE_SECONDS
        ):
            return self._simulation_mode
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{self.config_service_url}/api/v1/config/mode")
                if r.status_code == 200:
                    self._simulation_mode = bool(r.json().get("is_simulation", False))
                else:
                    self._simulation_mode = False
        except Exception as e:
            logger.warning(f"Could not read trading mode from config-service: {e} — assuming live")
            self._simulation_mode = False
        self._simulation_mode_at = now
        return self._simulation_mode

    async def enqueue_order(self, order_request: OrderRequest) -> str:
        """Add order to processing queue"""
        order_id = str(uuid.uuid4())
        
        order_data = {
            "order_id": order_id,
            "signal": order_request.signal,
            "trade_id": order_request.trade_id,
            "exchange": order_request.exchange,
            "symbol": order_request.symbol,
            "side": order_request.side,
            "amount": order_request.amount,
            "strategy": order_request.strategy,
            "entry_reason": order_request.entry_reason,
            "created_at": datetime.utcnow().isoformat(),
            "worker_id": self.worker_id
        }
        
        # Add to queue
        await self.redis.rpush(REDIS_ORDER_QUEUE, json.dumps(order_data))
        
        # Set initial status
        await self.set_order_status(order_id, "QUEUED")
        
        logger.info(f"📋 Order {order_id} queued for {order_request.exchange} {order_request.symbol}")
        return order_id
    
    async def set_order_status(self, order_id: str, status: str, **kwargs):
        """Update order status in Redis cache"""
        status_data = {
            "order_id": order_id,
            "status": status,
            "updated_at": datetime.utcnow().isoformat(),
            **kwargs
        }
        
        status_key = REDIS_ORDER_STATUS.format(order_id=order_id)
        await self.redis.hset(status_key, mapping=status_data)
        await self.redis.expire(status_key, 3600)  # 1 hour TTL
        
        logger.info(f"📊 Order {order_id} status: {status}")
    
    async def get_order_status(self, order_id: str) -> Optional[Dict[str, Any]]:
        """Get order status from Redis cache"""
        status_key = REDIS_ORDER_STATUS.format(order_id=order_id)
        status_data = await self.redis.hgetall(status_key)
        
        if not status_data:
            return None
            
        return dict(status_data)
    
    async def process_next_order(self) -> bool:
        """Process next order from queue"""
        try:
            # Get order from queue (blocking pop with timeout)
            result = await self.redis.blpop(REDIS_ORDER_QUEUE, timeout=5)
            if not result:
                return False  # No orders to process
                
            queue_name, order_data = result
            order = json.loads(order_data)
            order_id = order["order_id"]
            
            logger.info(f"🔄 Processing order {order_id}")
            await self.set_order_status(order_id, "PROCESSING")

            simulation = await self.simulation_mode_enabled()
            
            # Live only: circuit breaker protects real exchange
            if not simulation and await self.is_circuit_breaker_open(order["exchange"]):
                await self.set_order_status(order_id, "FAILED", error_message="Circuit breaker open")
                # Update database trade to FAILED status
                await self.update_trade_status_failed(order.get("trade_id"), "Circuit breaker protection activated")
                return True
            
            # Process order
            success = await self.execute_order(order)
            
            if success:
                await self.set_order_status(order_id, "ACKNOWLEDGED", exchange_order_id=success)
                # Update database trade to OPEN status for successful orders
                await self.update_trade_status_success(
                    order.get("trade_id"), success, order, simulation=simulation
                )
            else:
                await self.set_order_status(order_id, "FAILED", error_message="Order execution failed")
                # Update database trade to FAILED status
                await self.update_trade_status_failed(order.get("trade_id"), "Order execution failed - insufficient balance or other exchange error")
                if not simulation:
                    await self.increment_circuit_breaker_failures(order["exchange"])
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Error processing order: {e}")
            return False
    
    async def _execute_simulated_order(self, order: Dict[str, Any]) -> Optional[str]:
        """Simulate a filled limit order at current market (no exchange API)."""
        try:
            current_price = await self.get_current_price(order["exchange"], order["symbol"])
            if not current_price:
                logger.error(f"❌ [SIMULATION] No price for {order['symbol']} on {order['exchange']}")
                return None
            adjusted_amount = float(order["amount"])
            limit_price = self.calculate_limit_price(current_price, order["side"])
            # Sticky on the queue payload so trade update + balance debit use the same fill (orchestrator uses price-feed; REST ticker alone often fails).
            order["_sim_entry_fill_price"] = float(limit_price)
            sim_id = f"sim_{uuid.uuid4().hex[:24]}"
            order_data = {
                "id": sim_id,
                "status": "closed",
                "average": limit_price,
                "price": limit_price,
                "filled": adjusted_amount,
                "amount": adjusted_amount,
                "cost": float(adjusted_amount) * float(limit_price),
                "fees": [],
                "trades": [],
            }
            order_with = order.copy()
            order_with["amount"] = adjusted_amount
            await self.emit_fill_event(order_with, sim_id, order_data, limit_price)
            logger.info(
                f"✅ [SIMULATION] Simulated fill {sim_id} {order['side']} {adjusted_amount} {order['symbol']} @ {limit_price}"
            )
            return sim_id
        except Exception as e:
            logger.error(f"❌ [SIMULATION] Simulated order error: {e}")
            return None

    async def execute_order(self, order: Dict[str, Any]) -> Optional[str]:
        """Execute order via exchange service"""
        try:
            if await self.simulation_mode_enabled():
                logger.info(f"🧪 [SIMULATION] Skipping live exchange for order {order.get('order_id')}")
                return await self._execute_simulated_order(order)

            # Get current market price for limit order calculation
            current_price = await self.get_current_price(order["exchange"], order["symbol"])
            if not current_price:
                logger.error(f"❌ Unable to get current price for {order['symbol']} on {order['exchange']}")
                return None
            
            # Check and adjust amount based on available balance for sell orders
            adjusted_amount = order["amount"]
            if order["side"] == "sell":
                available_balance = await self.get_available_balance(order["exchange"], order["symbol"])
                if available_balance is not None:
                    if adjusted_amount > available_balance:
                        logger.info(f"📊 Adjusting sell amount from {adjusted_amount} to available balance {available_balance}")
                        adjusted_amount = available_balance
                    elif available_balance < 0.001:  # Minimum trade amount
                        logger.error(f"❌ Insufficient balance for sell: {available_balance}")
                        return None
                else:
                    logger.warning(f"⚠️ Could not verify balance for {order['symbol']} on {order['exchange']}")
            
            # Calculate limit order price with spread
            limit_price = self.calculate_limit_price(current_price, order["side"])
            
            # Convert exchange name for API compatibility
            api_exchange = order["exchange"].replace('crypto.com', 'cryptocom')
            
            # Convert symbol format for order placement
            order_symbol = order["symbol"]
            if api_exchange == 'cryptocom':
                # Crypto.com uses USD instead of USDC for many pairs
                if order_symbol == 'XLM/USDC':
                    order_symbol = 'XLM/USD'
                else:
                    # General case - convert /USDC to /USD
                    order_symbol = order_symbol.replace('/USDC', '/USD')
            
            # Prepare order data for exchange with limit order
            exchange_order = {
                "exchange": api_exchange,
                "symbol": order_symbol,
                "order_type": "limit",  # Use limit orders for better execution
                "side": order["side"],
                "amount": adjusted_amount,  # Use balance-adjusted amount
                "price": limit_price,
                "client_order_id": f"queue_{order['order_id'][:8]}"
            }
            
            # CRITICAL: Log exact order parameters being sent to exchange
            logger.info(f"🔍 SENDING ORDER TO {api_exchange.upper()}: {exchange_order}")
            logger.info(f"🔍 ORDER DETAILS - Original amount: {order.get('amount', 'N/A')}, Adjusted amount: {adjusted_amount}, Total cost: {adjusted_amount * limit_price:.2f} USDC")
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{EXCHANGE_SERVICE_URL}/api/v1/trading/order",
                    json=exchange_order
                )
                
                if response.status_code in [200, 201]:
                    result = response.json()
                    # Get order data from the response
                    order_data = result.get("order", {})
                    exchange_order_id = order_data.get("id")
                    
                    if exchange_order_id:
                        logger.info(f"✅ Exchange order succeeded: {exchange_order_id} - Status: {order_data.get('status', 'unknown')}")
                        
                        # Emit fill event to Redis Stream with complete order data
                        # Update order with adjusted amount for accurate event emission
                        order_with_adjusted_amount = order.copy()
                        order_with_adjusted_amount["amount"] = adjusted_amount
                        await self.emit_fill_event(order_with_adjusted_amount, exchange_order_id, order_data, limit_price)
                        return exchange_order_id
                    else:
                        logger.error(f"❌ No order ID in response: {result}")
                        return None
                
                logger.error(f"❌ Exchange order failed: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"❌ Order execution error: {e}")
            return None
    
    async def get_current_price(self, exchange: str, symbol: str) -> Optional[float]:
        """Spot price: ticker-live → price-feed (subscribe+GET) → REST ticker (aligns with orchestrator)."""
        api_exchange = exchange.replace('crypto.com', 'cryptocom')
        # Symbol as in pair e.g. BTC/USDC for live + feed paths
        exchange_symbol = symbol if '/' in symbol else symbol

        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                live = await client.get(
                    f"{EXCHANGE_SERVICE_URL}/api/v1/market/ticker-live/{api_exchange}/{exchange_symbol}",
                    params={"stale_threshold_seconds": 5},
                )
                if live.status_code == 200:
                    data = live.json()
                    p = data.get('last') or data.get('close') or data.get('price')
                    if p and float(p) > 0:
                        logger.info(f"📊 Price (ticker-live) {symbol} on {exchange}: {p}")
                        return float(p)
        except Exception as e:
            logger.debug(f"ticker-live failed for {symbol}: {e}")

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(
                    f"{PRICE_FEED_SERVICE_URL}/api/v1/pairs/subscribe",
                    json={"exchange": exchange, "pair": symbol},
                )
        except Exception:
            pass

        try:
            encoded = quote(symbol, safe='')
            async with httpx.AsyncClient(timeout=8.0) as client:
                r = await client.get(
                    f"{PRICE_FEED_SERVICE_URL}/api/v1/price/{api_exchange}/{encoded}"
                )
                if r.status_code == 200:
                    data = r.json()
                    p = data.get('price')
                    if p and float(p) > 0:
                        logger.info(
                            f"📊 Price (price-feed) {symbol} on {exchange}: {p} "
                            f"(cache_hit={data.get('cache_hit')})"
                        )
                        return float(p)
        except Exception as e:
            logger.debug(f"price-feed failed for {symbol}: {e}")

        try:
            if api_exchange == 'cryptocom':
                if symbol == 'XLM/USDC':
                    ticker_symbol = 'XLMUSD'
                else:
                    ticker_symbol = symbol.replace('/USDC', 'USD').replace('/', '')
            else:
                ticker_symbol = symbol.replace('/', '')

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{EXCHANGE_SERVICE_URL}/api/v1/market/ticker/{api_exchange}/{ticker_symbol}"
                )

                if response.status_code == 200:
                    ticker = response.json()
                    price = ticker.get('last') or ticker.get('close') or ticker.get('price')
                    if price:
                        logger.info(f"📊 Price (REST ticker) {symbol} on {exchange}: {price}")
                        return float(price)

                logger.warning(
                    f"⚠️ REST ticker failed for {symbol} on {exchange}: {response.status_code}"
                )
                return None

        except Exception as e:
            logger.error(f"❌ Error getting current price: {e}")
            return None
    
    async def get_available_balance(self, exchange: str, symbol: str) -> Optional[float]:
        """Get available balance for the base currency of the symbol"""
        try:
            # Extract base currency from symbol (e.g., XLM from XLM/USDC)
            base_currency = symbol.split('/')[0]
            
            # Convert exchange name for API compatibility
            api_exchange = exchange.replace('crypto.com', 'cryptocom')
            
            exchange_service_url = "http://exchange-service:8003"
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    f"{exchange_service_url}/api/v1/account/balance/{api_exchange}"
                )
                
                if response.status_code == 200:
                    balance_data = response.json()
                    
                    # The exchange service returns the balance structure directly
                    # Extract free balance for the base currency
                    free_balances = balance_data.get('free', {})
                    free_balance = free_balances.get(base_currency, 0.0) or 0.0
                    
                    logger.info(f"💰 Available {base_currency} balance on {exchange}: {free_balance}")
                    return float(free_balance)
                else:
                    logger.error(f"❌ Failed to get balance: {response.status_code}")
                    return None
                    
        except Exception as e:
            logger.error(f"❌ Error getting available balance: {e}")
            return None
    
    def calculate_limit_price(self, current_price: float, side: str) -> float:
        """Calculate limit order price with appropriate spread for fast fills"""
        try:
            # Use a slightly more aggressive spread for faster fills
            # while still maintaining limit order benefits
            aggressive_spread = 0.0002  # 0.02% spread for faster execution
            
            if side.lower() == "buy":
                # For buy orders, place slightly above current price for quick fill
                # This ensures we get filled quickly but still better than pure market order
                limit_price = current_price * (1 + aggressive_spread)
                logger.info(f"📈 Buy limit price: {limit_price} (current: {current_price}, spread: +{aggressive_spread*100:.3f}%)")
            else:  # sell
                # For sell orders, place slightly below current price for quick fill
                limit_price = current_price * (1 - aggressive_spread)
                logger.info(f"📉 Sell limit price: {limit_price} (current: {current_price}, spread: -{aggressive_spread*100:.3f}%)")
            
            return round(limit_price, 8)  # Round to 8 decimal places for precision
            
        except Exception as e:
            logger.error(f"❌ Error calculating limit price: {e}")
            # Fallback to current price if calculation fails
            return current_price
    
    async def emit_fill_event(self, order: Dict[str, Any], exchange_order_id: str, order_data: Dict[str, Any] = None, limit_price: float = None):
        """Emit fill notification to Redis Stream with complete order data"""
        fill_event = {
            "event_type": "order_filled",
            "order_id": order["order_id"],
            "exchange_order_id": exchange_order_id,
            "exchange": order["exchange"],
            "symbol": order["symbol"],
            "side": order["side"],
            "amount": order["amount"],  # Original order amount for reference
            "trade_id": order["trade_id"],
            "timestamp": datetime.utcnow().isoformat()
        }
        
        # Add exchange order data if available
        if order_data:
            # Get average price with multiple fallback options
            avg_price = (
                order_data.get("average") or 
                order_data.get("price") or 
                order_data.get("lastPrice") or 
                limit_price or 
                0.0  # Final fallback to prevent None
            )
            
            # Get filled amount with fallback
            filled_amount = (
                order_data.get("filled") or 
                order_data.get("amount") or 
                order.get("amount") or
                0.0  # Final fallback to prevent None
            )
            
            fill_event.update({
                "avg_price": float(avg_price) if avg_price is not None else 0.0,
                "filled_amount": float(filled_amount) if filled_amount is not None else 0.0,
                "order_status": order_data.get("status") or "unknown",
                "cost": float(order_data.get("cost")) if order_data.get("cost") is not None else 0.0,
                "fees": json.dumps(order_data.get("fees", [])),
                "trades": json.dumps(order_data.get("trades", []))
            })
            logger.info(f"📊 Fill details: filled={fill_event.get('filled_amount')}, avg_price={fill_event.get('avg_price')}")
        
        # Ensure all values are serializable
        for key, value in fill_event.items():
            if value is None:
                fill_event[key] = ""
        
        await self.redis.xadd(REDIS_FILL_STREAM, fill_event)
        logger.info(f"📡 Complete fill event emitted for order {order['order_id']}")
    
    async def is_circuit_breaker_open(self, exchange: str) -> bool:
        """Check if circuit breaker is open for exchange"""
        breaker_key = REDIS_CIRCUIT_BREAKER.format(exchange=exchange)
        failures = await self.redis.get(breaker_key)
        
        if failures and int(failures) >= 3:  # Open after 3 failures
            logger.warning(f"⚡ Circuit breaker OPEN for {exchange}")
            return True
        return False
    
    async def increment_circuit_breaker_failures(self, exchange: str):
        """Increment circuit breaker failure count"""
        breaker_key = REDIS_CIRCUIT_BREAKER.format(exchange=exchange)
        await self.redis.incr(breaker_key)
        await self.redis.expire(breaker_key, 300)  # Reset after 5 minutes

    async def update_trade_status_failed(self, trade_id: str, failure_reason: str):
        """Update database trade status to FAILED when order fails"""
        if not trade_id:
            logger.warning("⚠️ No trade_id provided for failed order update")
            return
            
        try:
            database_service_url = "http://database-service:8002"
            
            update_data = {
                "status": "FAILED",
                "exit_reason": failure_reason,
                "updated_at": datetime.utcnow().isoformat()
            }
            
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.put(
                    f"{database_service_url}/api/v1/trades/{trade_id}",
                    json=update_data
                )
                
                if response.status_code == 200:
                    logger.info(f"✅ Trade {trade_id} updated to FAILED status: {failure_reason}")
                else:
                    logger.error(f"❌ Failed to update trade {trade_id} to FAILED: {response.status_code} - {response.text}")
                    
        except Exception as e:
            logger.error(f"❌ Error updating trade {trade_id} to FAILED: {e}")

    async def _debit_simulation_balance_for_buy(
        self,
        exchange: str,
        amount: float,
        reference_price: float,
        *,
        exact_fill_price: Optional[float] = None,
    ) -> None:
        """After a simulated buy fill, reduce DB USDC so portfolio/dashboard match spend."""
        if amount <= 0:
            return
        if exact_fill_price is not None and float(exact_fill_price) > 0:
            cost = float(amount) * float(exact_fill_price)
        elif reference_price > 0:
            fill_price = self.calculate_limit_price(reference_price, "buy")
            cost = float(amount) * float(fill_price)
        else:
            return
        database_service_url = "http://database-service:8002"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get(f"{database_service_url}/api/v1/balances/{exchange}")
                if r.status_code != 200:
                    logger.warning(
                        f"🧪 [SIM BALANCE] Skip debit: GET /balances/{exchange} -> {r.status_code}"
                    )
                    return
                row = r.json()
                bal = float(row.get("balance") or 0)
                avail = float(row.get("available_balance") or 0)
                total_pnl = float(row.get("total_pnl") or 0)
                daily_pnl = float(row.get("daily_pnl") or 0)
                new_bal = max(0.0, bal - cost)
                new_avail = max(0.0, avail - cost)
                payload = {
                    "exchange": exchange,
                    "balance": new_bal,
                    "available_balance": new_avail,
                    "total_pnl": total_pnl,
                    "daily_pnl": daily_pnl,
                    "timestamp": datetime.utcnow().isoformat(),
                }
                r2 = await client.put(
                    f"{database_service_url}/api/v1/balances/{exchange}",
                    json=payload,
                )
                if r2.status_code == 200:
                    logger.info(
                        f"🧪 [SIM BALANCE] Debited ${cost:.4f} on {exchange} "
                        f"(available {avail:.4f} -> {new_avail:.4f})"
                    )
                else:
                    logger.error(
                        f"🧪 [SIM BALANCE] PUT balance failed {r2.status_code}: {r2.text}"
                    )
        except Exception as e:
            logger.error(f"🧪 [SIM BALANCE] Debit error for {exchange}: {e}")

    async def _credit_simulation_balance_for_sell(
        self,
        exchange: str,
        amount: float,
        reference_price: float,
        *,
        exact_fill_price: Optional[float] = None,
    ) -> None:
        """After a simulated sell fill, increase DB quote balance (proceeds)."""
        if amount <= 0:
            return
        if exact_fill_price is not None and float(exact_fill_price) > 0:
            proceeds = float(amount) * float(exact_fill_price)
        elif reference_price > 0:
            fill_price = self.calculate_limit_price(reference_price, "sell")
            proceeds = float(amount) * float(fill_price)
        else:
            return
        database_service_url = "http://database-service:8002"
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.get(f"{database_service_url}/api/v1/balances/{exchange}")
                if r.status_code != 200:
                    logger.warning(
                        f"🧪 [SIM BALANCE] Skip credit: GET /balances/{exchange} -> {r.status_code}"
                    )
                    return
                row = r.json()
                bal = float(row.get("balance") or 0)
                avail = float(row.get("available_balance") or 0)
                total_pnl = float(row.get("total_pnl") or 0)
                daily_pnl = float(row.get("daily_pnl") or 0)
                new_bal = max(0.0, bal + proceeds)
                new_avail = max(0.0, avail + proceeds)
                payload = {
                    "exchange": exchange,
                    "balance": new_bal,
                    "available_balance": new_avail,
                    "total_pnl": total_pnl,
                    "daily_pnl": daily_pnl,
                    "timestamp": datetime.utcnow().isoformat(),
                }
                r2 = await client.put(
                    f"{database_service_url}/api/v1/balances/{exchange}",
                    json=payload,
                )
                if r2.status_code == 200:
                    logger.info(
                        f"🧪 [SIM BALANCE] Credited ${proceeds:.4f} on {exchange} "
                        f"(available {avail:.4f} -> {new_avail:.4f})"
                    )
                else:
                    logger.error(
                        f"🧪 [SIM BALANCE] PUT balance (credit) failed {r2.status_code}: {r2.text}"
                    )
        except Exception as e:
            logger.error(f"🧪 [SIM BALANCE] Credit error for {exchange}: {e}")

    async def update_trade_status_success(
        self,
        trade_id: str,
        exchange_order_id: str,
        order: Dict[str, Any],
        simulation: bool = False,
    ):
        """Update database trade status to OPEN when order succeeds"""
        if not trade_id:
            logger.warning("⚠️ No trade_id provided for successful order update")
            return
            
        try:
            database_service_url = "http://database-service:8002"
            
            sim_fill_f = 0.0
            raw_sf = order.get("_sim_entry_fill_price")
            if raw_sf is not None:
                try:
                    sim_fill_f = float(raw_sf)
                except (TypeError, ValueError):
                    sim_fill_f = 0.0

            fetched: Optional[float] = None
            if sim_fill_f <= 0:
                fetched = await self.get_current_price(order["exchange"], order["symbol"])
            entry_price_for_db = sim_fill_f if sim_fill_f > 0 else float(fetched or 0)

            if entry_price_for_db <= 0:
                logger.warning(
                    f"⚠️ No entry price for {order['symbol']} on {order['exchange']} "
                    f"(sim_fill={sim_fill_f!r}, fetched={fetched!r})"
                )

            update_data = {
                "status": "OPEN",
                "entry_id": exchange_order_id,
                "entry_price": float(entry_price_for_db),
                "position_size": float(order["amount"]),
                "entry_time": datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat()
            }
            
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.put(
                    f"{database_service_url}/api/v1/trades/{trade_id}",
                    json=update_data
                )
                
                if response.status_code == 200:
                    logger.info(f"✅ Trade {trade_id} updated to OPEN status with order {exchange_order_id}")
                    if simulation and entry_price_for_db > 0:
                        side = str(order.get("side", "")).lower()
                        ref = float(fetched or 0)
                        if side == "buy":
                            await self._debit_simulation_balance_for_buy(
                                order["exchange"],
                                float(order["amount"]),
                                ref,
                                exact_fill_price=sim_fill_f if sim_fill_f > 0 else None,
                            )
                        elif side == "sell":
                            await self._credit_simulation_balance_for_sell(
                                order["exchange"],
                                float(order["amount"]),
                                ref,
                                exact_fill_price=sim_fill_f if sim_fill_f > 0 else None,
                            )
                else:
                    logger.error(f"❌ Failed to update trade {trade_id} to OPEN: {response.status_code} - {response.text}")
                    
        except Exception as e:
            logger.error(f"❌ Error updating trade {trade_id} to OPEN: {e}")

# Global order manager
order_manager: Optional[OrderQueueManager] = None

async def order_processor_worker():
    """Background worker to process orders from queue"""
    global order_manager, redis_client
    
    if not redis_client:
        logger.error("❌ Redis client not initialized")
        return
        
    order_manager = OrderQueueManager(redis_client)
    
    logger.info("🚀 Order processor worker started")
    
    while True:
        try:
            processed = await order_manager.process_next_order()
            if not processed:
                await asyncio.sleep(1)  # Brief pause when no orders
        except Exception as e:
            logger.error(f"❌ Worker error: {e}")
            await asyncio.sleep(5)

async def heartbeat_worker():
    """Worker heartbeat for monitoring"""
    global redis_client
    
    while True:
        try:
            if redis_client:
                await redis_client.setex(
                    f"{REDIS_WORKER_HEARTBEAT}:order_processor", 
                    30, 
                    datetime.utcnow().isoformat()
                )
        except Exception as e:
            logger.error(f"❌ Heartbeat error: {e}")
        
        await asyncio.sleep(10)

# API Endpoints
@app.post("/api/v1/orders/enqueue")
async def enqueue_order_endpoint(order_request: OrderRequest):
    """Enqueue order for processing"""
    if not order_manager:
        raise HTTPException(status_code=503, detail="Order manager not initialized")
    
    try:
        order_id = await order_manager.enqueue_order(order_request)
        return {
            "order_id": order_id,
            "status": "queued",
            "message": "Order queued for processing"
        }
    except Exception as e:
        logger.error(f"❌ Failed to enqueue order: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/orders/{order_id}/status")
async def get_order_status_endpoint(order_id: str):
    """Get order status"""
    if not order_manager:
        raise HTTPException(status_code=503, detail="Order manager not initialized")
    
    status = await order_manager.get_order_status(order_id)
    if not status:
        raise HTTPException(status_code=404, detail="Order not found")
    
    return status

@app.get("/api/v1/queue/stats")
async def get_queue_stats():
    """Get queue statistics"""
    if not redis_client:
        raise HTTPException(status_code=503, detail="Redis not available")
    
    try:
        queue_length = await redis_client.llen(REDIS_ORDER_QUEUE)
        stream_length = await redis_client.xlen(REDIS_FILL_STREAM)
        
        return {
            "orders_pending": queue_length,
            "fill_events": stream_length,
            "worker_active": True
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    if not redis_client:
        raise HTTPException(status_code=503, detail="Redis not connected")
    
    try:
        await redis_client.ping()
        return {"status": "healthy", "service": "order-queue-service"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Redis unhealthy: {e}")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8007)