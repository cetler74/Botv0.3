#!/usr/bin/env python3
"""
Real-Time Price Feed Service
Consumes WebSocket price feeds and maintains trailing stop levels with redundant persistence
"""

import asyncio
import asyncpg
import httpx
import json
import logging
import os
import websockets
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, Any, List, Optional, Set
import signal
import sys
from fastapi import FastAPI, HTTPException
import uvicorn

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://carloslarramba:mypassword@postgres:5432/trading_bot_futures')
EXCHANGE_SERVICE_URL = os.getenv('EXCHANGE_SERVICE_URL', 'http://exchange-service:8003')
CONFIG_SERVICE_URL = os.getenv('CONFIG_SERVICE_URL', 'http://config-service:8001')

class PriceFeedManager:
    """Manages real-time price feeds from multiple exchanges via WebSocket"""
    
    def __init__(self):
        self.db_pool = None
        self.websocket_handlers = {}
        self.active_pairs = {}  # Track which pairs we need prices for
        self.price_cache = {}  # Local price cache for fast access
        self.shutdown_event = asyncio.Event()
        self.tasks = []
        self.config = {
            'enable_websocket_prices': False,
            'stale_price_threshold_seconds': 30,
            'rest_poll_interval_seconds': 1,
            'ws': {
                'max_concurrent_subscriptions_per_exchange': 50,
                'subscribe_only_for_open_trades': True
            }
        }
        
    async def initialize(self):
        """Initialize database connection and WebSocket handlers"""
        try:
            # Initialize database pool
            self.db_pool = await asyncpg.create_pool(
                DATABASE_URL,
                min_size=2,
                max_size=10,
                command_timeout=30
            )
            logger.info("✅ Database pool initialized")
            
            # Load configuration and initialize exchanges
            await self._load_realtime_config()
            await self._load_active_pairs()
            await self._initialize_websocket_handlers()
            await self._sync_ws_subscriptions()
            
            # Start background tasks
            self.tasks.append(asyncio.create_task(self._price_monitoring_loop()))
            self.tasks.append(asyncio.create_task(self._trailing_stop_update_loop()))
            self.tasks.append(asyncio.create_task(self._websocket_health_check_loop()))
            
            logger.info("✅ Price Feed Manager initialized successfully")
            
        except Exception as e:
            logger.error(f"❌ Failed to initialize Price Feed Manager: {e}")
            raise
    
    async def _load_active_pairs(self):
        """Load active trading pairs from open trades"""
        try:
            async with self.db_pool.acquire() as conn:
                # Get all open trades to determine which pairs we need to track
                query = """
                    SELECT DISTINCT exchange, pair 
                    FROM trading.trades 
                    WHERE status = 'OPEN'
                """
                rows = await conn.fetch(query)
                
                for row in rows:
                    exchange = row['exchange']
                    pair = row['pair']
                    
                    if exchange not in self.active_pairs:
                        self.active_pairs[exchange] = set()
                    self.active_pairs[exchange].add(pair)
                
                logger.info(f"📊 Loaded active pairs: {dict(self.active_pairs)}")
                
        except Exception as e:
            logger.error(f"❌ Failed to load active pairs: {e}")
    
    async def _initialize_websocket_handlers(self):
        """Initialize WebSocket connections to exchanges"""
        try:
            for exchange_name in self.active_pairs.keys():
                await self._setup_exchange_websocket(exchange_name)
                
        except Exception as e:
            logger.error(f"❌ Failed to initialize WebSocket handlers: {e}")
    
    async def _setup_exchange_websocket(self, exchange_name: str):
        """Setup WebSocket connection for a specific exchange"""
        try:
            # Update WebSocket status in database
            await self._update_websocket_status(exchange_name, 'connecting')
            
            # Check if exchange service WebSocket is already connected
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{EXCHANGE_SERVICE_URL}/api/v1/websocket/{exchange_name}/status")
                
                if response.status_code == 200:
                    data = response.json()
                    if data.get('connected', False):
                        logger.info(f"✅ {exchange_name} WebSocket already connected via exchange service")
                        await self._update_websocket_status(exchange_name, 'connected')
                        return True
                
                # If not connected, log but don't fail - we'll use REST API fallback
                logger.warning(f"⚠️ {exchange_name} WebSocket not available, will use REST API fallback")
                await self._update_websocket_status(exchange_name, 'fallback')
                return False
                
        except Exception as e:
            logger.error(f"❌ Failed to setup WebSocket for {exchange_name}: {e}")
            await self._update_websocket_status(exchange_name, 'error', str(e))
            return False
    
    async def _update_websocket_status(self, exchange: str, status: str, error_message: str = None):
        """Update WebSocket status in database"""
        try:
            async with self.db_pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO trading.websocket_status (exchange, status, error_message, updated_at)
                    VALUES ($1, $2, $3, CURRENT_TIMESTAMP)
                    ON CONFLICT (exchange) DO UPDATE SET
                        status = EXCLUDED.status,
                        error_message = EXCLUDED.error_message,
                        updated_at = CURRENT_TIMESTAMP,
                        message_count = websocket_status.message_count + 1
                """, exchange, status, error_message)
                
        except Exception as e:
            logger.error(f"❌ Failed to update WebSocket status for {exchange}: {e}")
    
    async def _price_monitoring_loop(self):
        """Main loop for price monitoring and updates"""
        logger.info("🚀 Starting price monitoring loop")
        
        while not self.shutdown_event.is_set():
            try:
                # Prefer WS live price if enabled; else use REST fetch loop
                await self._update_prices_from_ws_if_available()
                # Fallback/refresh via REST
                await self._fetch_all_prices()
                
                # Update trailing stops based on new prices
                await self._process_trailing_stop_updates()
                
                # Sleep for a short interval (adjust based on needs)
                await asyncio.sleep(self.config.get('rest_poll_interval_seconds', 1))
                
            except Exception as e:
                logger.error(f"❌ Error in price monitoring loop: {e}")
                await asyncio.sleep(5.0)  # Longer sleep on errors
    
    async def _fetch_all_prices(self):
        """Fetch current prices for all active trading pairs"""
        tasks = []
        
        for exchange_name, pairs in self.active_pairs.items():
            for pair in pairs:
                tasks.append(self._fetch_price(exchange_name, pair))
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _load_realtime_config(self):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{CONFIG_SERVICE_URL}/api/v1/config/realtime")
                if resp.status_code == 200:
                    self.config = resp.json()
        except Exception as e:
            logger.warning(f"Realtime config not available, using defaults: {e}")

    async def _update_prices_from_ws_if_available(self):
        if not self.config.get('enable_websocket_prices'):
            return
        try:
            stale = self.config.get('stale_price_threshold_seconds', 30)
            async with httpx.AsyncClient(timeout=3.0) as client:
                for exchange_name, pairs in self.active_pairs.items():
                    for pair in pairs:
                        symbol = pair.replace('/', '')
                        url = f"{EXCHANGE_SERVICE_URL}/api/v1/market/ticker-live/{exchange_name}/{symbol}"
                        r = await client.get(url, params={'stale_threshold_seconds': stale})
                        if r.status_code == 200:
                            data = r.json()
                            price = float(data.get('last', 0) or 0)
                            bid = data.get('bid'); ask = data.get('ask')
                            if price > 0:
                                await self._store_price_update(exchange_name, pair, price, bid if bid is not None else None, ask if ask is not None else None, None, 'websocket')
                                cache_key = f"{exchange_name}:{pair}"
                                self.price_cache[cache_key] = {
                                    'price': price,
                                    'bid': float(bid) if bid else None,
                                    'ask': float(ask) if ask else None,
                                    'timestamp': datetime.utcnow(),
                                    'source': 'websocket'
                                }
        except Exception as e:
            logger.debug(f"WS price update skipped: {e}")
    
    async def _fetch_price(self, exchange_name: str, pair: str):
        """Fetch price for a specific exchange/pair"""
        try:
            # Try to get price from exchange service
            async with httpx.AsyncClient(timeout=5.0) as client:
                # Convert pair format if needed
                symbol = pair.replace('/', '')  # Simple conversion for now
                
                response = await client.get(
                    f"{EXCHANGE_SERVICE_URL}/api/v1/market/ticker/{exchange_name}/{symbol}"
                )
                
                if response.status_code == 200:
                    data = response.json()
                    price = float(data.get('last', 0))
                    bid = float(data.get('bid', 0))
                    ask = float(data.get('ask', 0))
                    volume = float(data.get('baseVolume', 0))
                    
                    if price > 0:
                        await self._store_price_update(exchange_name, pair, price, bid, ask, volume, 'rest_api')
                        
                        # Update local cache
                        cache_key = f"{exchange_name}:{pair}"
                        self.price_cache[cache_key] = {
                            'price': price,
                            'bid': bid,
                            'ask': ask,
                            'timestamp': datetime.utcnow(),
                            'source': 'rest_api'
                        }
                        
                        return price
                
        except Exception as e:
            logger.error(f"❌ Failed to fetch price for {exchange_name}/{pair}: {e}")
            return None
    
    async def _store_price_update(self, exchange: str, pair: str, price: float, bid: float = None, ask: float = None, volume: float = None, source: str = 'websocket'):
        """Store price update in database"""
        try:
            async with self.db_pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO trading.real_time_prices 
                    (exchange, pair, price, bid, ask, volume_24h, timestamp, source)
                    VALUES ($1, $2, $3, $4, $5, $6, CURRENT_TIMESTAMP, $7)
                """, exchange, pair, Decimal(str(price)), 
                Decimal(str(bid)) if bid else None,
                Decimal(str(ask)) if ask else None,
                Decimal(str(volume)) if volume else None, source)
                
        except Exception as e:
            logger.error(f"❌ Failed to store price update: {e}")
    
    async def _process_trailing_stop_updates(self):
        """Process trailing stop updates based on current prices"""
        try:
            async with self.db_pool.acquire() as conn:
                # Get all active trailing stops
                query = """
                    SELECT ts.*, t.trade_id, t.pair, t.exchange
                    FROM trading.trailing_stops ts
                    JOIN trading.trades t ON ts.trade_id = t.trade_id
                    WHERE ts.is_active = TRUE AND t.status = 'OPEN'
                """
                trailing_stops = await conn.fetch(query)
                
                for ts in trailing_stops:
                    exchange = ts['exchange']
                    pair = ts['pair']
                    trade_id = ts['trade_id']
                    
                    # Get latest price from cache or database
                    current_price = await self._get_latest_price(exchange, pair)
                    
                    if current_price and current_price > 0:
                        # Update trailing stop using the database function
                        adjustment_made = await conn.fetchval("""
                            SELECT trading.update_trailing_stop($1, $2)
                        """, trade_id, Decimal(str(current_price)))
                        
                        if adjustment_made:
                            logger.info(f"📈 Updated trailing stop for {trade_id}: {exchange}/{pair} @ ${current_price:.8f}")
                        
                        # Check if trailing stop should trigger
                        should_trigger = await conn.fetchval("""
                            SELECT trading.should_trigger_trailing_stop($1, $2)
                        """, trade_id, Decimal(str(current_price)))
                        
                        if should_trigger:
                            logger.warning(f"🚨 Trailing stop TRIGGERED for {trade_id}: {exchange}/{pair} @ ${current_price:.8f}")
                            # Here you could send a signal to the orchestrator or create an alert
                            await self._create_trailing_stop_alert(trade_id, exchange, pair, current_price)
                            
        except Exception as e:
            logger.error(f"❌ Error processing trailing stop updates: {e}")
    
    async def _get_latest_price(self, exchange: str, pair: str) -> Optional[float]:
        """Get latest price from cache or database"""
        try:
            # First check cache
            cache_key = f"{exchange}:{pair}"
            if cache_key in self.price_cache:
                cached = self.price_cache[cache_key]
                # Use cached price if it's recent (within last 30 seconds)
                if (datetime.utcnow() - cached['timestamp']).total_seconds() < 30:
                    return cached['price']
            
            # Fallback to database
            async with self.db_pool.acquire() as conn:
                price = await conn.fetchval("""
                    SELECT trading.get_latest_price($1, $2)
                """, exchange, pair)
                
                return float(price) if price else None
                
        except Exception as e:
            logger.error(f"❌ Failed to get latest price for {exchange}/{pair}: {e}")
            return None
    
    async def _create_trailing_stop_alert(self, trade_id, exchange: str, pair: str, current_price: float):
        """Create alert when trailing stop is triggered"""
        try:
            async with self.db_pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO trading.alerts (level, category, message, details, exchange)
                    VALUES ('WARNING', 'TRADE', $1, $2, $3)
                """, 
                f"Trailing stop triggered for trade {trade_id}",
                json.dumps({
                    'trade_id': str(trade_id),
                    'exchange': exchange,
                    'pair': pair,
                    'trigger_price': current_price,
                    'timestamp': datetime.utcnow().isoformat()
                }),
                exchange)
                
        except Exception as e:
            logger.error(f"❌ Failed to create trailing stop alert: {e}")
    
    async def _trailing_stop_update_loop(self):
        """Dedicated loop for trailing stop management"""
        logger.info("🚀 Starting trailing stop update loop")
        
        while not self.shutdown_event.is_set():
            try:
                # Refresh active pairs periodically
                await self._load_active_pairs()
                await self._sync_ws_subscriptions()
                
                # Clean up old price data (keep last 1000 entries per pair)
                await self._cleanup_old_prices()
                
                await asyncio.sleep(60.0)  # Run every minute
                
            except Exception as e:
                logger.error(f"❌ Error in trailing stop update loop: {e}")
                await asyncio.sleep(30.0)
    
    async def _cleanup_old_prices(self):
        """Clean up old price data to prevent database bloat"""
        try:
            async with self.db_pool.acquire() as conn:
                # Keep only last 1000 price records per exchange/pair
                await conn.execute("""
                    DELETE FROM trading.real_time_prices 
                    WHERE id NOT IN (
                        SELECT id FROM (
                            SELECT id, 
                                   ROW_NUMBER() OVER (PARTITION BY exchange, pair ORDER BY timestamp DESC) as rn
                            FROM trading.real_time_prices
                        ) ranked
                        WHERE rn <= 1000
                    )
                """)
                
        except Exception as e:
            logger.error(f"❌ Failed to cleanup old prices: {e}")

    async def _sync_ws_subscriptions(self):
        """Subscribe/unsubscribe to live ticker streams based on active pairs and config."""
        try:
            if not self.config.get('enable_websocket_prices'):
                return
            max_per_ex = self.config.get('ws', {}).get('max_concurrent_subscriptions_per_exchange', 50)
            async with httpx.AsyncClient(timeout=10.0) as client:
                for exchange_name, pairs in self.active_pairs.items():
                    symbols = [p.replace('/', '') for p in list(pairs)][:max_per_ex]
                    if not symbols:
                        continue
                    try:
                        resp = await client.post(f"{EXCHANGE_SERVICE_URL}/api/v1/websocket/{exchange_name}/subscribe", json={'symbols': symbols})
                        if resp.status_code != 200:
                            logger.debug(f"Subscription call response {resp.status_code} for {exchange_name}: {resp.text}")
                    except Exception as sub_e:
                        logger.debug(f"Subscription error for {exchange_name}: {sub_e}")
        except Exception as e:
            logger.debug(f"_sync_ws_subscriptions skipped: {e}")
    
    async def _websocket_health_check_loop(self):
        """Monitor WebSocket health and reconnect if needed"""
        logger.info("🚀 Starting WebSocket health check loop")
        
        while not self.shutdown_event.is_set():
            try:
                for exchange_name in self.active_pairs.keys():
                    await self._check_websocket_health(exchange_name)
                
                await asyncio.sleep(30.0)  # Check every 30 seconds
                
            except Exception as e:
                logger.error(f"❌ Error in WebSocket health check loop: {e}")
                await asyncio.sleep(60.0)
    
    async def _check_websocket_health(self, exchange_name: str):
        """Check WebSocket health for specific exchange"""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{EXCHANGE_SERVICE_URL}/api/v1/websocket/{exchange_name}/status")
                
                if response.status_code == 200:
                    data = response.json()
                    is_connected = data.get('connected', False)
                    
                    # Update database status
                    status = 'connected' if is_connected else 'disconnected'
                    await self._update_websocket_status(exchange_name, status)
                    
                    if not is_connected:
                        logger.warning(f"⚠️ {exchange_name} WebSocket disconnected, using REST API fallback")
                        
                else:
                    await self._update_websocket_status(exchange_name, 'error', f"HTTP {response.status_code}")
                    
        except Exception as e:
            logger.error(f"❌ Failed to check WebSocket health for {exchange_name}: {e}")
            await self._update_websocket_status(exchange_name, 'error', str(e))
    
    async def shutdown(self):
        """Shutdown the price feed manager"""
        logger.info("🛑 Shutting down Price Feed Manager...")
        
        self.shutdown_event.set()
        
        # Cancel all tasks
        for task in self.tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        
        # Close database pool
        if self.db_pool:
            await self.db_pool.close()
        
        logger.info("✅ Price Feed Manager shut down complete")

# Global manager instance
price_feed_manager = PriceFeedManager()

# FastAPI app for health checks and API endpoints
app = FastAPI(title="Price Feed Service", version="1.0.0")

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    try:
        if not price_feed_manager.db_pool:
            return {"status": "unhealthy", "reason": "Database not connected"}
        
        # Test database connection
        async with price_feed_manager.db_pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        
        return {
            "status": "healthy",
            "timestamp": datetime.utcnow().isoformat(),
            "active_pairs": dict(price_feed_manager.active_pairs),
            "price_cache_size": len(price_feed_manager.price_cache),
            "service": "price-feed-service"
        }
    except Exception as e:
        return {"status": "unhealthy", "reason": str(e)}

@app.get("/status")
async def get_status():
    """Get detailed service status"""
    try:
        websocket_status = {}
        # Skip websocket status for now to debug
        # if price_feed_manager.db_pool:
        #     async with price_feed_manager.db_pool.acquire() as conn:
        #         rows = await conn.fetch("SELECT * FROM trading.websocket_status")
        #         for row in rows:
        #             websocket_status[row['exchange']] = {
        #                 'status': row['status'],
        #                 'last_message_time': row['last_message_time'].isoformat() if row['last_message_time'] else None,
        #                 'reconnection_count': row['reconnection_count'],
        #                 'message_count': row['message_count']
        #             }
        
        # Format price cache for external consumption
        formatted_cache = {}
        cache_size = len(price_feed_manager.price_cache)
        logger.info(f"🔧 Formatting cache: size={cache_size}")
        
        try:
            for cache_key, cache_data in price_feed_manager.price_cache.items():
                logger.info(f"🔧 Processing cache key: {cache_key}")
                timestamp = cache_data.get('timestamp', datetime.utcnow())
                # Ensure timestamp is properly formatted
                if hasattr(timestamp, 'isoformat'):
                    timestamp_str = timestamp.isoformat()
                else:
                    timestamp_str = str(timestamp)
                    
                formatted_cache[cache_key] = {
                    'price': cache_data.get('price', 0),
                    'bid': cache_data.get('bid'),
                    'ask': cache_data.get('ask'),
                    'timestamp': timestamp_str,
                    'source': cache_data.get('source', 'unknown')
                }
            logger.info(f"🔧 Formatted cache complete: {len(formatted_cache)} items")
        except Exception as cache_e:
            logger.error(f"Error formatting price cache: {cache_e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            formatted_cache = {"error": f"cache_formatting_failed: {str(cache_e)}"}
        
        return {
            "service": "price-feed-service",
            "status": "running",
            "timestamp": datetime.utcnow().isoformat(),
            "active_pairs": dict(price_feed_manager.active_pairs),
            "price_cache_size": len(price_feed_manager.price_cache),
            "price_cache": formatted_cache if formatted_cache else {"debug": "empty_formatted_cache"},
            "websocket_status": websocket_status,
            "running_tasks": len(price_feed_manager.tasks)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/trailing-stops")
async def get_active_trailing_stops():
    """Get all active trailing stops"""
    try:
        if not price_feed_manager.db_pool:
            raise HTTPException(status_code=503, detail="Database not available")
        
        async with price_feed_manager.db_pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM trading.active_trailing_stops")
            
            trailing_stops = []
            for row in rows:
                trailing_stops.append(dict(row))
            
            return {
                "trailing_stops": trailing_stops,
                "count": len(trailing_stops),
                "timestamp": datetime.utcnow().isoformat()
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/price/{exchange}/{pair}")
async def get_current_price(exchange: str, pair: str):
    """Get current price for a specific exchange/pair from cache"""
    try:
        # Try different cache key formats to match what's actually stored
        possible_keys = [
            f"{exchange}:{pair}",
            f"{exchange.lower()}:{pair.upper()}",
            f"{exchange.upper()}:{pair.lower()}",
            f"{exchange.upper()}:{pair.upper()}"
        ]
        
        # Try to get from local cache first
        for cache_key in possible_keys:
            if cache_key in price_feed_manager.price_cache:
                cached = price_feed_manager.price_cache[cache_key]
                # Use cached price if it's recent (within last 30 seconds)
                try:
                    cache_age = (datetime.utcnow() - cached['timestamp']).total_seconds()
                    if cache_age < 30:
                        return {
                            "exchange": exchange,
                            "pair": pair,
                            "price": cached['price'],
                            "bid": cached.get('bid'),
                            "ask": cached.get('ask'),
                            "timestamp": cached['timestamp'].isoformat(),
                            "source": cached['source'],
                            "cache_hit": True,
                            "cache_age_seconds": cache_age
                        }
                except Exception as ts_e:
                    logger.debug(f"Timestamp error for {cache_key}: {ts_e}")
                    continue
        
        # If not in cache or stale, try to get latest from database
        if price_feed_manager.db_pool:
            try:
                async with price_feed_manager.db_pool.acquire() as conn:
                    row = await conn.fetchrow("""
                        SELECT price, bid, ask, timestamp, source
                        FROM trading.real_time_prices
                        WHERE exchange = $1 AND pair = $2
                        ORDER BY timestamp DESC
                        LIMIT 1
                    """, exchange, pair)
                    
                    if row and row['price']:
                        return {
                            "exchange": exchange,
                            "pair": pair,
                            "price": float(row['price']),
                            "bid": float(row['bid']) if row['bid'] else None,
                            "ask": float(row['ask']) if row['ask'] else None,
                            "timestamp": row['timestamp'].isoformat(),
                            "source": row['source'],
                            "cache_hit": False
                        }
            except Exception as db_e:
                logger.debug(f"Database query failed for {exchange}/{pair}: {db_e}")
        
        # If still no price, fetch fresh data
        current_price = await price_feed_manager._fetch_price(exchange, pair)
        
        if current_price and current_price > 0:
            return {
                "exchange": exchange,
                "pair": pair,
                "price": current_price,
                "timestamp": datetime.utcnow().isoformat(),
                "source": "fresh_fetch",
                "cache_hit": False
            }
        
        raise HTTPException(status_code=404, detail=f"No price available for {exchange}/{pair}")
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error getting price: {str(e)}")

@app.get("/api/v1/cache/debug")
async def debug_cache():
    """Debug endpoint to see what's actually in the cache"""
    try:
        cache_debug = {}
        for key, value in price_feed_manager.price_cache.items():
            cache_debug[key] = {
                "price": value.get('price'),
                "timestamp": str(value.get('timestamp')),
                "source": value.get('source'),
                "type": str(type(value.get('timestamp')))
            }
        
        return {
            "cache_keys": list(price_feed_manager.price_cache.keys()),
            "cache_details": cache_debug,
            "active_pairs": dict(price_feed_manager.active_pairs)
        }
    except Exception as e:
        return {"error": str(e), "cache_size": len(price_feed_manager.price_cache)}

async def signal_handler(signum):
    """Handle shutdown signals"""
    logger.info(f"Received signal {signum}, initiating shutdown...")
    await price_feed_manager.shutdown()
    sys.exit(0)

@app.on_event("startup")
async def startup_event():
    """Initialize the price feed manager when FastAPI starts"""
    logger.info("🚀 Starting Price Feed Service...")
    await price_feed_manager.initialize()
    logger.info("✅ Price Feed Service started successfully")

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup when FastAPI shuts down"""
    await price_feed_manager.shutdown()

async def main():
    """Main entry point"""
    # Setup signal handlers
    loop = asyncio.get_event_loop()
    for sig in [signal.SIGINT, signal.SIGTERM]:
        loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(signal_handler(s)))
    
    # Start the FastAPI server
    config = uvicorn.Config(
        app, 
        host="0.0.0.0", 
        port=8007, 
        log_level="info",
        loop="asyncio"
    )
    server = uvicorn.Server(config)
    await server.serve()

if __name__ == "__main__":
    asyncio.run(main())