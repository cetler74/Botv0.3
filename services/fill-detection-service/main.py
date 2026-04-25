#!/usr/bin/env python3
"""
Fill Detection Service - Redis Streams for Real-time Fill Processing
Monitors order fills and creates trade records with zero latency
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
import httpx
import redis.asyncio as redis
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
from contextlib import asynccontextmanager
from websocket_consumer import WebSocketEventConsumer
from cryptocom_websocket_consumer import CryptocomWebSocketConsumer
from bybit_websocket_consumer import BybitWebSocketConsumer

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Redis Keys
REDIS_FILL_STREAM = "trading:fills:stream"
REDIS_TRADE_QUEUE = "trading:trades:pending"
REDIS_ORDER_STATUS = "trading:orders:status:{order_id}"
REDIS_CONSUMER_GROUP = "fill_processors"
REDIS_CONSUMER_ID = f"fill_consumer_{uuid.uuid4().hex[:8]}"

# Service URLs
DATABASE_SERVICE_URL = "http://database-service:8002"
EXCHANGE_SERVICE_URL = "http://exchange-service:8003"

# Global Redis connection
redis_client: Optional[redis.Redis] = None

# WebSocket connections for real-time updates
websocket_connections: List[WebSocket] = []

# Global WebSocket event consumer
websocket_consumer: Optional[WebSocketEventConsumer] = None
cryptocom_websocket_consumer: Optional[CryptocomWebSocketConsumer] = None
bybit_websocket_consumer: Optional[BybitWebSocketConsumer] = None

# Global fallback state tracker
fallback_active: Dict[str, bool] = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan management"""
    global redis_client, websocket_consumer, cryptocom_websocket_consumer, bybit_websocket_consumer
    
    # Startup
    redis_client = redis.Redis(host='redis', port=6379, decode_responses=True)
    
    try:
        await redis_client.ping()
        logger.info("✅ Connected to Redis successfully")
        
        # Create consumer group for fill stream
        try:
            await redis_client.xgroup_create(REDIS_FILL_STREAM, REDIS_CONSUMER_GROUP, id='0', mkstream=True)
            logger.info(f"✅ Created consumer group: {REDIS_CONSUMER_GROUP}")
        except redis.RedisError as e:
            if "BUSYGROUP" in str(e):
                logger.info(f"📋 Consumer group {REDIS_CONSUMER_GROUP} already exists")
            else:
                logger.error(f"❌ Failed to create consumer group: {e}")
        
        # Initialize WebSocket consumers
        websocket_consumer = WebSocketEventConsumer(redis_client)
        cryptocom_websocket_consumer = CryptocomWebSocketConsumer()
        bybit_websocket_consumer = BybitWebSocketConsumer()
        
        # Initialize Crypto.com consumer with Redis connection
        if await cryptocom_websocket_consumer.initialize():
            logger.info("✅ Crypto.com WebSocket consumer initialized")
        else:
            logger.warning("⚠️ Failed to initialize Crypto.com WebSocket consumer")
        
        # Initialize Bybit consumer with Redis connection
        if await bybit_websocket_consumer.initialize():
            logger.info("✅ Bybit WebSocket consumer initialized")
        else:
            logger.warning("⚠️ Failed to initialize Bybit WebSocket consumer")
            
        logger.info("✅ WebSocket consumers initialized")
                
    except Exception as e:
        logger.error(f"❌ Failed to connect to Redis: {e}")
        raise
    
    # Start background workers
    asyncio.create_task(fill_stream_processor())
    asyncio.create_task(trade_creator_worker())
    asyncio.create_task(exchange_monitor_worker())
    asyncio.create_task(pending_trade_recovery_worker())  # NEW: Automatic recovery worker
    
    yield
    
    # Shutdown
    if cryptocom_websocket_consumer:
        await cryptocom_websocket_consumer.cleanup()
    if bybit_websocket_consumer:
        await bybit_websocket_consumer.cleanup()
    if redis_client:
        await redis_client.close()

app = FastAPI(
    title="Fill Detection Service", 
    description="Real-time fill detection with Redis Streams",
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

class FillDetectionManager:
    """Manages fill detection and trade creation"""
    
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        
    async def process_fill_event(self, event: Dict[str, Any]) -> bool:
        """Process a fill event from Redis Stream"""
        try:
            event_type = event.get("event_type")
            
            if event_type == "order_acknowledged":
                return await self.handle_order_acknowledged(event)
            elif event_type == "order_filled":
                return await self.handle_order_filled(event)
            elif event_type == "websocket_fill":
                return await self.handle_websocket_fill(event)
            else:
                logger.warning(f"⚠️ Unknown event type: {event_type}")
                return True
                
        except Exception as e:
            logger.error(f"❌ Error processing fill event: {e}")
            return False
    
    async def handle_order_acknowledged(self, event: Dict[str, Any]) -> bool:
        """Handle order acknowledged event"""
        order_id = event.get("order_id")
        exchange_order_id = event.get("exchange_order_id")
        
        logger.info(f"📝 Order acknowledged: {order_id} -> {exchange_order_id}")
        
        # Start monitoring this order for fills
        monitoring_task = asyncio.create_task(
            self.monitor_order_fills(event)
        )
        
        return True
    
    async def handle_order_filled(self, event: Dict[str, Any]) -> bool:
        """Handle order filled event"""
        order_id = event.get("order_id")
        exchange_order_id = event.get("exchange_order_id")
        
        logger.info(f"✅ Order filled: {order_id} -> {exchange_order_id}")
        
        # Create trade record
        await self.create_trade_record(event)
        
        # Notify WebSocket clients
        await self.broadcast_fill_notification(event)
        
        return True
    
    async def handle_websocket_fill(self, event: Dict[str, Any]) -> bool:
        """Handle real-time WebSocket fill notification"""
        logger.info(f"⚡ WebSocket fill: {event.get('order_id')}")
        
        # Create trade record immediately
        await self.create_trade_record(event)
        
        return True
    
    async def monitor_order_fills(self, order_event: Dict[str, Any]):
        """Monitor specific order for fills via exchange API"""
        exchange = order_event.get("exchange")
        exchange_order_id = order_event.get("exchange_order_id")
        order_id = order_event.get("order_id")
        
        max_attempts = 12  # 60 seconds total (5s * 12)
        attempt = 0
        
        while attempt < max_attempts:
            try:
                fill_data = await self.check_order_status(exchange, exchange_order_id)
                
                if fill_data and fill_data.get("status") == "filled":
                    # Emit fill event
                    fill_event = {
                        "event_type": "order_filled",
                        "order_id": order_id,
                        "exchange_order_id": exchange_order_id,
                        "exchange": exchange,
                        "symbol": order_event.get("symbol"),
                        "side": order_event.get("side"),
                        "amount": order_event.get("amount"),
                        "filled_amount": fill_data.get("filled"),
                        "avg_price": fill_data.get("average"),
                        "fee_amount": fill_data.get("fee_amount", 0),
                        "fee_currency": fill_data.get("fee_currency"),
                        "trade_id": order_event.get("trade_id"),
                        "timestamp": datetime.utcnow().isoformat()
                    }
                    
                    # DEBUG: Log fill event fee data
                    logger.info(f"🐛 [DEBUG] Fill event created with fee_amount={fill_event['fee_amount']}, fee_currency={fill_event['fee_currency']} for order {exchange_order_id}")
                    
                    await self.redis.xadd(REDIS_FILL_STREAM, fill_event)
                    logger.info(f"📡 Fill event emitted for {order_id}")
                    return
                
                attempt += 1
                await asyncio.sleep(5)
                
            except Exception as e:
                logger.error(f"❌ Error monitoring order {order_id}: {e}")
                attempt += 1
                await asyncio.sleep(5)
        
        logger.warning(f"⏱️ Order {order_id} monitoring timeout after 60 seconds")
    
    async def check_order_status(self, exchange: str, order_id: str) -> Optional[Dict[str, Any]]:
        """Check order status via exchange service - FIXED VERSION"""
        logger.info(f"🔍 [DEBUG] check_order_status called for {exchange} order {order_id}")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # First try specific order endpoint (works for both open and closed orders)
                response = await client.get(
                    f"{EXCHANGE_SERVICE_URL}/api/v1/trading/order/{exchange}/{order_id}"
                )
                
                if response.status_code == 200:
                    order_data = response.json().get("order", {})
                    if order_data:
                        # Map exchange status to standardized status
                        exchange_status = order_data.get("status", "").lower()
                        ccxt_status = order_data.get("info", {}).get("status", "").upper()
                        
                        # Determine if order is filled based on both fields
                        is_filled = (exchange_status == "closed" and 
                                   order_data.get("filled", 0) > 0) or ccxt_status == "FILLED"
                        
                        # Extract fee information - be exchange-specific
                        fee_info = order_data.get("fee", {})
                        info_data = order_data.get("info", {})
                        
                        # Get exchange from the request context (we need to know which exchange this is)
                        exchange_name = exchange.lower()
                        
                        # Check if account has zero fee rates (mainly for CRO)
                        maker_fee_rate = info_data.get("maker_fee_rate", 0)
                        taker_fee_rate = info_data.get("taker_fee_rate", 0)
                        
                        # Convert to float if they're strings, handle None
                        try:
                            maker_fee_rate = float(maker_fee_rate) if maker_fee_rate is not None else 0
                            taker_fee_rate = float(taker_fee_rate) if taker_fee_rate is not None else 0
                        except (ValueError, TypeError):
                            maker_fee_rate = taker_fee_rate = 0
                        
                        # Universal fee extraction for ALL exchanges and tokens
                        fee_amount = fee_info.get("cost", 0) or info_data.get("cumulative_fee", 0)
                        fee_currency = fee_info.get("currency") or info_data.get("fee_instrument_name")
                        
                        # Convert string fee amounts to float
                        if isinstance(fee_amount, str):
                            try:
                                fee_amount = float(fee_amount)
                            except (ValueError, TypeError):
                                fee_amount = 0
                        
                        # Ensure fee_amount is properly converted to float
                        fee_amount = float(fee_amount) if fee_amount else 0.0
                        
                        # DEBUG: Log fee extraction details
                        logger.info(f"🐛 [DEBUG] EXCHANGE={exchange_name} Fee extraction for order {order_id}: fee_amount={fee_amount}, fee_currency={fee_currency}")
                        logger.info(f"🐛 [DEBUG] maker_fee_rate={maker_fee_rate}, taker_fee_rate={taker_fee_rate}")
                        logger.info(f"🐛 [DEBUG] Raw fee_info={fee_info}")
                        logger.info(f"🐛 [DEBUG] Raw info_data fees: cumulative_fee={info_data.get('cumulative_fee')}, fee_instrument_name={info_data.get('fee_instrument_name')}")
                        
                        return {
                            "status": "filled" if is_filled else "open",
                            "filled": order_data.get("filled", 0),
                            "remaining": order_data.get("remaining", 0),
                            "average": order_data.get("average"),
                            "fee_amount": float(fee_amount) if fee_amount else 0.0,
                            "fee_currency": fee_currency,
                            "info": order_data.get("info", {})
                        }
                
                # Fallback: check in open orders (for pending orders)
                response = await client.get(
                    f"{EXCHANGE_SERVICE_URL}/api/v1/trading/orders/{exchange}"
                )
                
                if response.status_code == 200:
                    orders = response.json().get("orders", [])
                    for order in orders:
                        if order.get("id") == order_id:
                            return {
                                "status": "open",
                                "filled": order.get("filled", 0),
                                "remaining": order.get("remaining", 0),
                                "average": order.get("average"),
                                "info": order
                            }
                            
        except Exception as e:
            logger.error(f"❌ Failed to check order status: {e}")
            
        return None
    
    def get_default_fee_currency(self, exchange: str, symbol: str) -> str:
        """Get default fee currency for exchanges that don't provide fee data"""
        exchange = exchange.lower()
        
        # Extract base and quote currencies from symbol (e.g., "BTC/USDC" -> base="BTC", quote="USDC")
        if "/" in symbol:
            base_currency, quote_currency = symbol.split("/")
        else:
            base_currency, quote_currency = "BTC", "USDT"  # Default fallback
            
        # Exchange-specific fee currency logic
        if exchange == "binance":
            # Binance typically uses quote currency for trading fees (USDC, USDT, etc.)
            # Only use BNB if specifically trading BNB pairs or if BNB fee discount is enabled
            if base_currency == "BNB" or quote_currency == "BNB":
                return "BNB"
            else:
                # For most trades, Binance charges fees in the quote currency
                return quote_currency
        elif exchange == "bybit":
            return quote_currency  # Bybit typically uses quote currency
        elif exchange == "cryptocom":
            return "CRO"
        else:
            return quote_currency  # Generic fallback
    
    def get_default_fee_amount(self, exchange: str, symbol: str) -> float:
        """Get estimated fee amount when exchange doesn't provide actual fees"""
        exchange = exchange.lower()
        
        # Extract base and quote currencies for currency-appropriate fee amounts
        if "/" in symbol:
            base_currency, quote_currency = symbol.split("/")
        else:
            base_currency, quote_currency = "BTC", "USDT"
        
        # Exchange-specific fee amounts based on typical trading volume ($200)
        if exchange == "binance":
            # Binance typical fee rate: 0.1% for taker orders
            if quote_currency in ["USDC", "USDT", "USD"]:
                return 0.20  # $0.20 for $200 trade (0.1% fee)
            elif base_currency == "BNB" or quote_currency == "BNB":
                return 0.0002  # BNB amount for $200 trade
            else:
                return 0.20  # Default to quote currency equivalent
        elif exchange == "bybit": 
            return 0.2     # ~$0.20 in USDT 
        elif exchange == "cryptocom":
            return 0.0     # 0% fees for this account
        else:
            return 0.1     # Generic small fee
    
    async def create_trade_record(self, fill_event: Dict[str, Any]):
        """Create or update trade record based on order side"""
        try:
            order_side = fill_event.get("side", "").lower()
            exchange = fill_event.get("exchange")
            symbol = fill_event.get("symbol")
            exchange_order_id = fill_event.get("exchange_order_id")
            
            # CRITICAL: Enrich fill_event with actual fee data from exchange
            if exchange_order_id and exchange:
                logger.info(f"🔄 [DEBUG] Fetching fee data for {exchange} order {exchange_order_id}")
                fee_data = await self.check_order_status(exchange, exchange_order_id)
                if fee_data:
                    # Add fee information to fill_event
                    fill_event["fee_amount"] = fee_data.get("fee_amount", 0.0)
                    fill_event["fee_currency"] = fee_data.get("fee_currency")
                    logger.info(f"✅ [DEBUG] Enriched fill_event with fee_amount={fill_event['fee_amount']}, fee_currency={fill_event['fee_currency']}")
                else:
                    # Fallback: Set reasonable defaults when exchange doesn't provide fee data
                    logger.warning(f"⚠️ Could not fetch fee data for {exchange} order {exchange_order_id}, using defaults")
                    default_currency = self.get_default_fee_currency(exchange, symbol)
                    default_amount = self.get_default_fee_amount(exchange, symbol)
                    
                    fill_event["fee_amount"] = default_amount
                    fill_event["fee_currency"] = default_currency
                    logger.info(f"✅ [DEBUG] Applied fallback fee: {default_amount} {default_currency}")
            
            if order_side == "buy":
                await self.handle_buy_order(fill_event)
            elif order_side == "sell":
                await self.handle_sell_order(fill_event)
            else:
                logger.error(f"❌ Unknown order side: {order_side}")
                
        except Exception as e:
            logger.error(f"❌ Error creating trade record: {e}")
    
    async def handle_buy_order(self, fill_event: Dict[str, Any]):
        """Handle buy order - update existing PENDING trade to OPEN position"""
        trade_id = fill_event.get("trade_id")
        
        # DEBUG: Log the entire fill_event to see what data is available
        logger.info(f"🔍 [DEBUG] handle_buy_order received fill_event: {fill_event}")
        
        # Validate trade_id
        try:
            uuid.UUID(trade_id)
        except (ValueError, TypeError):
            logger.error(f"❌ Invalid trade_id in fill event: {trade_id}")
            return
        
        # Update existing PENDING trade to OPEN status
        # Use filled_amount (actual exchange execution) not amount (original order)
        actual_filled_amount = fill_event.get("filled_amount", fill_event.get("amount", 0))
        actual_avg_price = fill_event.get("avg_price", 0)
        
        # Extract fee information
        fee_amount = fill_event.get("fee_amount", 0)
        fee_currency = fill_event.get("fee_currency")
        
        # DEBUG: Log fee data in buy handler
        logger.info(f"🐛 [DEBUG] handle_buy_order fee extraction: fee_amount={fee_amount}, fee_currency={fee_currency} for trade {trade_id}")
        
        update_data = {
            "status": "OPEN",  # Buy order creates OPEN position to be closed by sell
            "entry_price": float(actual_avg_price),
            "entry_id": fill_event.get("exchange_order_id"),
            "entry_time": fill_event.get("timestamp"),
            "position_size": float(actual_filled_amount),
            "entry_fee_amount": float(fee_amount) if fee_amount else 0.0,
            "entry_fee_currency": fee_currency,
            "fees": float(fee_amount) if fee_amount else 0.0,  # Update legacy fees field
            "updated_at": datetime.utcnow().isoformat()
        }
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Try to update existing trade first
            response = await client.put(
                f"{DATABASE_SERVICE_URL}/api/v1/trades/{trade_id}",
                json=update_data
            )
            
            if response.status_code == 200:
                logger.info(f"✅ Buy trade updated to OPEN: {trade_id}")
            elif response.status_code == 404:
                # Fallback: create new trade if not found (shouldn't happen in normal flow)
                logger.warning(f"⚠️ PENDING trade not found, creating new OPEN trade: {trade_id}")
                trade_data = {
                    "trade_id": trade_id,
                    "pair": fill_event.get("symbol"),
                    "entry_price": float(actual_avg_price),
                    "status": "OPEN",
                    "entry_id": fill_event.get("exchange_order_id"),
                    "entry_time": fill_event.get("timestamp"),
                    "exchange": fill_event.get("exchange"),
                    "entry_reason": f"Redis queue processed order - {fill_event.get('trade_id', 'unknown')}",
                    "position_size": float(actual_filled_amount),
                    "entry_fee_amount": float(fee_amount) if fee_amount else 0.0,
                    "entry_fee_currency": fee_currency,
                    "fees": float(fee_amount) if fee_amount else 0.0,
                    "strategy": "redis_queue",
                    "created_at": datetime.utcnow().isoformat(),
                    "updated_at": datetime.utcnow().isoformat()
                }
                
                create_response = await client.post(
                    f"{DATABASE_SERVICE_URL}/api/v1/trades",
                    json=trade_data
                )
                
                if create_response.status_code in [200, 201]:
                    logger.info(f"✅ Buy trade record created (OPEN): {trade_id}")
                else:
                    logger.error(f"❌ Failed to create buy trade: {create_response.status_code} - {create_response.text}")
            else:
                logger.error(f"❌ Failed to update buy trade: {response.status_code} - {response.text}")
    
    async def handle_sell_order(self, fill_event: Dict[str, Any]):
        """Handle sell order - find and close existing open position"""
        exchange = fill_event.get("exchange")
        symbol = fill_event.get("symbol")
        filled_amount = float(fill_event.get("filled_amount", 0))
        
        try:
            # Find existing open position for this exchange/symbol
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Get open trades for this exchange/symbol
                response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/trades/open")
                
                if response.status_code == 200:
                    trades = response.json().get("trades", [])
                    
                    # Find matching open position
                    matching_trade = None
                    for trade in trades:
                        if (trade.get("exchange") == exchange and 
                            trade.get("pair") == symbol and 
                            trade.get("status") == "OPEN"):
                            matching_trade = trade
                            break
                    
                    if matching_trade:
                        # Close the existing position
                        trade_id = matching_trade["trade_id"]
                        exit_price = float(fill_event.get("avg_price", 0))
                        
                        # Extract exit fee information
                        exit_fee_amount = fill_event.get("fee_amount", 0)
                        exit_fee_currency = fill_event.get("fee_currency")
                        entry_fee_amount = float(matching_trade.get("entry_fee_amount", 0))
                        
                        # Calculate PnL (subtract total fees)
                        entry_price = float(matching_trade.get("entry_price", 0))
                        position_size = float(matching_trade.get("position_size", 0))
                        gross_pnl = (exit_price - entry_price) * position_size
                        total_fees = entry_fee_amount + (float(exit_fee_amount) if exit_fee_amount else 0)
                        net_pnl = gross_pnl - total_fees  # Net PnL after fees
                        
                        update_data = {
                            "status": "CLOSED",
                            "exit_price": exit_price,
                            "exit_id": fill_event.get("exchange_order_id"),
                            "exit_time": fill_event.get("timestamp"),
                            "exit_reason": f"Redis queue sell order - {fill_event.get('trade_id', 'unknown')}",
                            "exit_fee_amount": float(exit_fee_amount) if exit_fee_amount else 0.0,
                            "exit_fee_currency": exit_fee_currency,
                            "fees": total_fees,  # Update legacy fees field with total fees
                            "realized_pnl": net_pnl,  # Store net PnL (after fees)
                            "updated_at": datetime.utcnow().isoformat()
                        }
                        
                        # Update the existing trade
                        update_response = await client.put(
                            f"{DATABASE_SERVICE_URL}/api/v1/trades/{trade_id}",
                            json=update_data
                        )
                        
                        if update_response.status_code == 200:
                            logger.info(f"✅ Sell order closed existing position: {trade_id} (PnL: {pnl:.4f})")
                        else:
                            logger.error(f"❌ Failed to update trade: {update_response.status_code} - {update_response.text}")
                    
                    else:
                        logger.warning(f"⚠️ No open position found to close for sell order on {exchange} {symbol}")
                        # Create a new sell trade record as fallback
                        await self.create_standalone_sell_trade(fill_event)
                        
                else:
                    logger.error(f"❌ Failed to get open trades: {response.status_code}")
                    
        except Exception as e:
            logger.error(f"❌ Error handling sell order: {e}")
    
    async def create_standalone_sell_trade(self, fill_event: Dict[str, Any]):
        """Create standalone sell trade when no matching position found"""
        trade_id = str(uuid.uuid4())
        logger.info(f"🔄 Generated new UUID for standalone sell trade: {trade_id}")
        
        # Extract fee information for standalone sell
        fee_amount = fill_event.get("fee_amount", 0)
        fee_currency = fill_event.get("fee_currency")
        
        trade_data = {
            "trade_id": trade_id,
            "pair": fill_event.get("symbol"),
            "entry_price": float(fill_event.get("avg_price", 0)),
            "status": "CLOSED",  # Sell order is completed
            "entry_id": fill_event.get("exchange_order_id"),
            "entry_time": fill_event.get("timestamp"),
            "exchange": fill_event.get("exchange"),
            "entry_reason": f"Standalone sell order - {fill_event.get('trade_id', 'unknown')}",
            "position_size": -float(fill_event.get("filled_amount", 0)),  # Negative for sell
            "entry_fee_amount": float(fee_amount) if fee_amount else 0.0,
            "entry_fee_currency": fee_currency,
            "fees": float(fee_amount) if fee_amount else 0.0,
            "strategy": "redis_queue",
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat()
        }
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{DATABASE_SERVICE_URL}/api/v1/trades",
                json=trade_data
            )
            
            if response.status_code in [200, 201]:
                logger.info(f"✅ Standalone sell trade created: {trade_id}")
            else:
                logger.error(f"❌ Failed to create standalone sell trade: {response.status_code} - {response.text}")
    
    async def broadcast_fill_notification(self, fill_event: Dict[str, Any]):
        """Broadcast fill notification to WebSocket clients"""
        if not websocket_connections:
            return
            
        notification = {
            "type": "order_filled",
            "data": fill_event,
            "timestamp": datetime.utcnow().isoformat()
        }
        
        # Send to all connected WebSocket clients
        disconnected = []
        for ws in websocket_connections:
            try:
                await ws.send_json(notification)
            except:
                disconnected.append(ws)
        
        # Clean up disconnected clients
        for ws in disconnected:
            websocket_connections.remove(ws)
        
        if disconnected:
            logger.info(f"🧹 Cleaned up {len(disconnected)} disconnected WebSocket clients")

# Global fill manager
fill_manager: Optional[FillDetectionManager] = None

async def fill_stream_processor():
    """Background worker to process fill events from Redis Stream"""
    global fill_manager, redis_client
    
    if not redis_client:
        logger.error("❌ Redis client not initialized")
        return
        
    fill_manager = FillDetectionManager(redis_client)
    
    logger.info("🚀 Fill stream processor started")
    
    while True:
        try:
            # Read from stream with consumer group
            messages = await redis_client.xreadgroup(
                REDIS_CONSUMER_GROUP,
                REDIS_CONSUMER_ID,
                {REDIS_FILL_STREAM: '>'},
                count=10,
                block=5000  # 5 second timeout
            )
            
            for stream, msgs in messages:
                for msg_id, fields in msgs:
                    try:
                        success = await fill_manager.process_fill_event(fields)
                        
                        if success:
                            # Acknowledge message
                            await redis_client.xack(REDIS_FILL_STREAM, REDIS_CONSUMER_GROUP, msg_id)
                        else:
                            logger.error(f"❌ Failed to process message {msg_id}")
                            
                    except Exception as e:
                        logger.error(f"❌ Error processing message {msg_id}: {e}")
                        
        except Exception as e:
            logger.error(f"❌ Stream processor error: {e}")
            await asyncio.sleep(5)

async def trade_creator_worker():
    """Worker to create trade records from filled orders"""
    logger.info("🚀 Trade creator worker started")
    
    while True:
        try:
            # Process trade creation queue if needed
            await asyncio.sleep(10)
        except Exception as e:
            logger.error(f"❌ Trade creator error: {e}")
            await asyncio.sleep(5)

async def exchange_monitor_worker():
    """Worker to monitor exchanges and provide fallback to REST API when WebSocket disconnects"""
    logger.info("🚀 Exchange monitor worker started with fallback capabilities")
    
    # Track WebSocket health for different exchanges
    websocket_health = {
        'binance': {'healthy': True, 'last_check': datetime.utcnow()},
        'bybit': {'healthy': True, 'last_check': datetime.utcnow()},
        'cryptocom': {'healthy': True, 'last_check': datetime.utcnow()}
    }
    
    global fallback_active
    
    while True:
        try:
            # Check WebSocket health status every 30 seconds
            await asyncio.sleep(30)
            
            # Check each exchange WebSocket health
            for exchange in ['binance', 'bybit', 'cryptocom']:
                try:
                    # Check if exchange WebSocket is healthy via exchange service
                    is_healthy = await check_websocket_health(exchange)
                    prev_healthy = websocket_health[exchange]['healthy']
                    
                    websocket_health[exchange]['healthy'] = is_healthy
                    websocket_health[exchange]['last_check'] = datetime.utcnow()
                    
                    # Detect state changes
                    if prev_healthy and not is_healthy:
                        # WebSocket disconnected - activate fallback
                        logger.warning(f"⚠️ {exchange} WebSocket disconnected, activating REST API fallback")
                        fallback_active[exchange] = True
                        asyncio.create_task(rest_api_fallback_worker(exchange))
                        
                    elif not prev_healthy and is_healthy:
                        # WebSocket reconnected - deactivate fallback
                        logger.info(f"✅ {exchange} WebSocket reconnected, deactivating REST API fallback")
                        fallback_active[exchange] = False
                    
                except Exception as e:
                    logger.error(f"❌ Error checking {exchange} WebSocket health: {e}")
                    
        except Exception as e:
            logger.error(f"❌ Exchange monitor error: {e}")
            await asyncio.sleep(5)

async def check_websocket_health(exchange: str) -> bool:
    """Check if WebSocket is healthy for a specific exchange"""
    try:
        if exchange == 'binance':
            # Check Binance WebSocket integration status
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{EXCHANGE_SERVICE_URL}/api/v1/websocket/binance/health")
                return response.status_code == 200
        elif exchange == 'cryptocom':
            # Check Crypto.com WebSocket integration status
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{EXCHANGE_SERVICE_URL}/api/v1/websocket/cryptocom/health")
                return response.status_code == 200
        else:
            # For other exchanges, assume healthy for now (can be extended)
            return True
            
    except Exception as e:
        logger.error(f"❌ Error checking {exchange} WebSocket health: {e}")
        return False

async def rest_api_fallback_worker(exchange: str):
    """DEPRECATED: REST API fallback removed - Exchange Service handles all REST API polling
    
    This function has been removed as part of the consolidation effort.
    Fill Detection Service now focuses solely on WebSocket-based real-time fill detection.
    Exchange Service provides reliable REST API polling as the primary fallback mechanism.
    """
    logger.info(f"🔄 REST API fallback disabled for {exchange} - Exchange Service handles REST polling")
    
    # Mark fallback as inactive immediately
    fallback_active[exchange] = False
    
    # Log the consolidation change
    logger.info(f"✅ CONSOLIDATION: Fill Detection Service no longer duplicates REST polling for {exchange}")
    logger.info(f"✅ CONSOLIDATION: Exchange Service provides comprehensive REST API order monitoring")

async def emit_rest_fill_event(exchange: str, order_data: Dict[str, Any]):
    """DEPRECATED: REST fill event emission removed - handled by Exchange Service
    
    This function has been removed to eliminate duplication.
    Exchange Service now handles all REST API-based fill detection and event emission.
    """
    logger.info(f"🔄 REST fill event emission deprecated for {exchange}")
    logger.info(f"✅ CONSOLIDATION: Exchange Service handles REST API fill events")

# API Endpoints
@app.post("/api/v1/fills/emit")
async def emit_fill_event(fill_data: Dict[str, Any]):
    """Manually emit a fill event (for testing/integration)"""
    if not redis_client:
        raise HTTPException(status_code=503, detail="Redis not available")
    
    try:
        fill_event = {
            "event_type": "order_filled",
            "timestamp": datetime.utcnow().isoformat(),
            **fill_data
        }
        
        message_id = await redis_client.xadd(REDIS_FILL_STREAM, fill_event)
        return {"message_id": message_id, "status": "emitted"}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/fills/stream/info")
async def get_stream_info():
    """Get fill stream information"""
    if not redis_client:
        raise HTTPException(status_code=503, detail="Redis not available")
    
    try:
        stream_info = await redis_client.xinfo_stream(REDIS_FILL_STREAM)
        return {
            "stream_length": stream_info.get("length", 0),
            "consumer_group": REDIS_CONSUMER_GROUP,
            "consumer_id": REDIS_CONSUMER_ID
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.websocket("/ws/fills")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time fill notifications"""
    await websocket.accept()
    websocket_connections.append(websocket)
    logger.info(f"🔌 WebSocket connected: {len(websocket_connections)} total")
    
    try:
        while True:
            # Keep connection alive and listen for client messages
            await websocket.receive_text()
    except WebSocketDisconnect:
        websocket_connections.remove(websocket)
        logger.info(f"🔌 WebSocket disconnected: {len(websocket_connections)} remaining")

@app.post("/api/v1/events/execution")
async def handle_execution_event(event_data: Dict[str, Any]):
    """Handle execution events from WebSocket streams (exchange service)"""
    if not websocket_consumer:
        raise HTTPException(status_code=503, detail="WebSocket consumer not initialized")
    
    try:
        result = await websocket_consumer.process_execution_event(event_data)
        return result
    except Exception as e:
        logger.error(f"❌ Error processing execution event: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/events/execution/cryptocom")
async def handle_cryptocom_execution_event(event_data: Dict[str, Any]):
    """Handle Crypto.com execution events from WebSocket streams"""
    if not cryptocom_websocket_consumer:
        raise HTTPException(status_code=503, detail="Crypto.com WebSocket consumer not initialized")
    
    try:
        result = await cryptocom_websocket_consumer.process_execution_event(event_data)
        return result
    except Exception as e:
        logger.error(f"❌ Error processing Crypto.com execution event: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/events/bybit")
async def handle_bybit_event(event_data: Dict[str, Any]):
    """Handle Bybit events from WebSocket streams"""
    if not bybit_websocket_consumer:
        raise HTTPException(status_code=503, detail="Bybit WebSocket consumer not initialized")
    
    try:
        result = await bybit_websocket_consumer.handle_bybit_event(event_data)
        return {"status": "success" if result else "error", "processed": result}
    except Exception as e:
        logger.error(f"❌ Error processing Bybit event: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/events/bybit/order")
async def handle_bybit_order_event(event_data: Dict[str, Any]):
    """Handle Bybit order events from WebSocket streams"""
    if not bybit_websocket_consumer:
        raise HTTPException(status_code=503, detail="Bybit WebSocket consumer not initialized")
    
    try:
        result = await bybit_websocket_consumer.process_order_event(event_data)
        return result
    except Exception as e:
        logger.error(f"❌ Error processing Bybit order event: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/events/bybit/execution")
async def handle_bybit_execution_event(event_data: Dict[str, Any]):
    """Handle Bybit execution events from WebSocket streams"""
    if not bybit_websocket_consumer:
        raise HTTPException(status_code=503, detail="Bybit WebSocket consumer not initialized")
    
    try:
        result = await bybit_websocket_consumer.process_execution_event(event_data)
        return result
    except Exception as e:
        logger.error(f"❌ Error processing Bybit execution event: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/websocket/status")
async def get_websocket_status():
    """Get WebSocket consumer status and metrics"""
    if not websocket_consumer:
        return {"status": "not_initialized", "healthy": False}
    
    try:
        is_healthy = await websocket_consumer.health_check()
        metrics = websocket_consumer.get_metrics()
        
        return {
            "status": "active" if is_healthy else "unhealthy",
            "healthy": is_healthy,
            "metrics": metrics
        }
    except Exception as e:
        return {
            "status": "error",
            "healthy": False,
            "error": str(e)
        }

@app.get("/api/v1/websocket/cryptocom/status")
async def get_cryptocom_websocket_status():
    """Get Crypto.com WebSocket consumer status and metrics"""
    if not cryptocom_websocket_consumer:
        return {"status": "not_initialized", "healthy": False}
    
    try:
        status = cryptocom_websocket_consumer.get_status()
        metrics = cryptocom_websocket_consumer.get_metrics()
        
        return {
            "status": status,
            "metrics": metrics
        }
    except Exception as e:
        return {
            "status": "error",
            "healthy": False,
            "error": str(e)
        }

@app.get("/api/v1/websocket/bybit/status")
async def get_bybit_websocket_status():
    """Get Bybit WebSocket consumer status and metrics"""
    if not bybit_websocket_consumer:
        return {"status": "not_initialized", "healthy": False}
    
    try:
        metrics = bybit_websocket_consumer.get_metrics()
        
        return {
            "status": "active",
            "healthy": True,
            "metrics": metrics
        }
    except Exception as e:
        return {
            "status": "error",
            "healthy": False,
            "error": str(e)
        }

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    if not redis_client:
        raise HTTPException(status_code=503, detail="Redis not connected")
    
    try:
        await redis_client.ping()
        
        # Also check WebSocket consumer health
        websocket_healthy = True
        cryptocom_healthy = True
        bybit_healthy = True
        
        if websocket_consumer:
            websocket_healthy = await websocket_consumer.health_check()
            
        if cryptocom_websocket_consumer:
            cryptocom_status = cryptocom_websocket_consumer.get_status()
            cryptocom_healthy = cryptocom_status.get('processing_healthy', True)
            
        if bybit_websocket_consumer:
            bybit_metrics = bybit_websocket_consumer.get_metrics()
            bybit_healthy = bybit_metrics.get('processing_errors', 0) < 10  # Consider healthy if less than 10 errors
        
        return {
            "status": "healthy", 
            "service": "fill-detection-service",
            "websocket_consumer": websocket_healthy,
            "cryptocom_consumer": cryptocom_healthy
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Service unhealthy: {e}")

async def pending_trade_recovery_worker():
    """Background worker to recover PENDING trades that were filled but not updated"""
    logger.info("🔄 Pending trade recovery worker started")
    
    while True:
        try:
            # Run recovery every 60 seconds
            await asyncio.sleep(60)
            
            # Get PENDING trades older than 5 minutes
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/trades")
                
                if response.status_code == 200:
                    trades_response = response.json()
                    # Handle different response formats
                    all_trades = trades_response if isinstance(trades_response, list) else trades_response.get("trades", [])
                    pending_trades = [t for t in all_trades if t.get("status") == "PENDING"]
                    
                    for trade in pending_trades:
                        trade_id = trade.get("trade_id")
                        created_at = trade.get("created_at")
                        
                        # Check if trade is older than 5 minutes (should have been processed)
                        try:
                            from datetime import datetime, timezone
                            trade_time = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                            age_minutes = (datetime.now(timezone.utc) - trade_time).total_seconds() / 60
                            
                            if age_minutes > 5:  # Trade is stale
                                # Try to find fill event in Redis stream
                                fill_event = await find_fill_event_for_trade(trade_id)
                                if fill_event:
                                    logger.info(f"🔄 Recovering stale PENDING trade: {trade_id}")
                                    # Process with our fixed logic
                                    manager = FillDetectionManager(redis_client)
                                    await manager.handle_buy_order(fill_event)
                                    
                        except Exception as e:
                            logger.error(f"❌ Error processing recovery for {trade_id}: {e}")
                            
        except Exception as e:
            logger.error(f"❌ Recovery worker error: {e}")
            await asyncio.sleep(30)  # Wait longer on error

async def find_fill_event_for_trade(trade_id: str) -> Optional[Dict[str, Any]]:
    """Find fill event in Redis stream for a specific trade_id"""
    try:
        # Search through Redis stream for matching trade_id
        streams = await redis_client.xrange(REDIS_FILL_STREAM, count=100)
        
        for stream_id, event_data in streams:
            if event_data.get("trade_id") == trade_id:
                return event_data
                
    except Exception as e:
        logger.error(f"❌ Error finding fill event for {trade_id}: {e}")
        
    return None

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8008)