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
from urllib.parse import unquote
from fastapi import FastAPI, HTTPException
import uvicorn

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Embedded so Docker image only needs main.py — matches scripts/create_all_trading_bot_tables.sql additions.
_PRICE_FEED_DDL_STATEMENTS: list[str] = [
    "CREATE SCHEMA IF NOT EXISTS trading",
    """
    ALTER TABLE trading.trades
    ADD COLUMN IF NOT EXISTS trailing_stop_history JSONB DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS price_updates_count INTEGER DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_price_update TIMESTAMP WITH TIME ZONE,
    ADD COLUMN IF NOT EXISTS websocket_price_source BOOLEAN DEFAULT FALSE
    """.strip(),
    """
    CREATE TABLE IF NOT EXISTS trading.real_time_prices (
        id SERIAL PRIMARY KEY,
        exchange VARCHAR(50) NOT NULL,
        pair VARCHAR(20) NOT NULL,
        price DECIMAL(20, 8) NOT NULL,
        bid DECIMAL(20, 8),
        ask DECIMAL(20, 8),
        volume_24h DECIMAL(20, 8),
        price_change_24h DECIMAL(10, 6),
        timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
        source VARCHAR(20) DEFAULT 'websocket',
        sequence_id BIGINT,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
    )
    """.strip(),
    "CREATE INDEX IF NOT EXISTS idx_real_time_prices_exchange_pair ON trading.real_time_prices(exchange, pair)",
    "CREATE INDEX IF NOT EXISTS idx_real_time_prices_timestamp ON trading.real_time_prices(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_real_time_prices_sequence ON trading.real_time_prices(sequence_id)",
    """
    CREATE TABLE IF NOT EXISTS trading.websocket_status (
        id SERIAL PRIMARY KEY,
        exchange VARCHAR(50) NOT NULL UNIQUE,
        status VARCHAR(20) NOT NULL,
        last_message_time TIMESTAMP WITH TIME ZONE,
        connection_start_time TIMESTAMP WITH TIME ZONE,
        reconnection_count INTEGER DEFAULT 0,
        error_message TEXT,
        latency_ms INTEGER,
        message_count BIGINT DEFAULT 0,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
    )
    """.strip(),
    "CREATE INDEX IF NOT EXISTS idx_websocket_status_exchange ON trading.websocket_status(exchange)",
    "CREATE INDEX IF NOT EXISTS idx_websocket_status_updated ON trading.websocket_status(updated_at)",
    """
    CREATE TABLE IF NOT EXISTS trading.trailing_stops (
        id SERIAL PRIMARY KEY,
        trade_id UUID NOT NULL REFERENCES trading.trades(trade_id) ON DELETE CASCADE,
        exchange VARCHAR(50) NOT NULL,
        pair VARCHAR(20) NOT NULL,
        trailing_enabled BOOLEAN DEFAULT FALSE,
        trailing_trigger_percentage DECIMAL(10, 6),
        trailing_step_percentage DECIMAL(10, 6),
        max_trail_distance_percentage DECIMAL(10, 6),
        is_active BOOLEAN DEFAULT FALSE,
        current_stop_price DECIMAL(20, 8),
        highest_price_seen DECIMAL(20, 8),
        lowest_price_seen DECIMAL(20, 8),
        entry_price DECIMAL(20, 8) NOT NULL,
        current_price DECIMAL(20, 8),
        position_side VARCHAR(10) DEFAULT 'long',
        profit_protection_enabled BOOLEAN DEFAULT FALSE,
        profit_lock_percentage DECIMAL(10, 6),
        profit_protection_active BOOLEAN DEFAULT FALSE,
        last_price_update TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        last_adjustment TIMESTAMP WITH TIME ZONE,
        adjustment_count INTEGER DEFAULT 0,
        triggered_at TIMESTAMP WITH TIME ZONE,
        recovery_data JSONB,
        websocket_connected BOOLEAN DEFAULT TRUE,
        price_source VARCHAR(50) DEFAULT 'websocket',
        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
    )
    """.strip(),
    "CREATE INDEX IF NOT EXISTS idx_trailing_stops_trade_id ON trading.trailing_stops(trade_id)",
    "CREATE INDEX IF NOT EXISTS idx_trailing_stops_exchange_pair ON trading.trailing_stops(exchange, pair)",
    "CREATE INDEX IF NOT EXISTS idx_trailing_stops_active ON trading.trailing_stops(is_active)",
    "CREATE INDEX IF NOT EXISTS idx_trailing_stops_updated ON trading.trailing_stops(updated_at)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_trailing_stops_trade_unique ON trading.trailing_stops(trade_id)",
    """
    CREATE OR REPLACE FUNCTION trading.get_latest_price(
        exchange_name VARCHAR(50),
        pair_name VARCHAR(20)
    ) RETURNS DECIMAL(20, 8) AS $$
    DECLARE
        latest_price DECIMAL(20, 8);
    BEGIN
        SELECT price INTO latest_price
        FROM trading.real_time_prices
        WHERE exchange = exchange_name AND pair = pair_name
        ORDER BY timestamp DESC, sequence_id DESC NULLS LAST
        LIMIT 1;
        RETURN COALESCE(latest_price, 0);
    END;
    $$ LANGUAGE plpgsql
    """.strip(),
    """
    CREATE OR REPLACE FUNCTION trading.update_trailing_stop(
        trade_uuid UUID,
        current_market_price DECIMAL(20, 8)
    ) RETURNS BOOLEAN AS $$
    DECLARE
        trailing_record RECORD;
        new_stop_price DECIMAL(20, 8);
        new_highest_price DECIMAL(20, 8);
        price_improved BOOLEAN := FALSE;
        adjustment_made BOOLEAN := FALSE;
    BEGIN
        SELECT * INTO trailing_record
        FROM trading.trailing_stops
        WHERE trade_id = trade_uuid;
        IF NOT FOUND OR NOT trailing_record.trailing_enabled THEN
            RETURN FALSE;
        END IF;
        UPDATE trading.trailing_stops
        SET current_price = current_market_price,
            last_price_update = CURRENT_TIMESTAMP
        WHERE trade_id = trade_uuid;
        IF trailing_record.position_side = 'long' THEN
            IF current_market_price > trailing_record.highest_price_seen THEN
                new_highest_price := current_market_price;
                price_improved := TRUE;
                new_stop_price := current_market_price * (1 - trailing_record.trailing_step_percentage / 100);
                IF new_stop_price > trailing_record.current_stop_price THEN
                    adjustment_made := TRUE;
                END IF;
            END IF;
        END IF;
        IF adjustment_made THEN
            UPDATE trading.trailing_stops
            SET current_stop_price = new_stop_price,
                highest_price_seen = COALESCE(new_highest_price, highest_price_seen),
                last_adjustment = CURRENT_TIMESTAMP,
                adjustment_count = adjustment_count + 1
            WHERE trade_id = trade_uuid;
            UPDATE trading.trades
            SET trail_stop_trigger = new_stop_price,
                highest_price = COALESCE(new_highest_price, highest_price),
                price_updates_count = price_updates_count + 1,
                last_price_update = CURRENT_TIMESTAMP
            WHERE trade_id = trade_uuid;
        END IF;
        RETURN adjustment_made;
    END;
    $$ LANGUAGE plpgsql
    """.strip(),
    """
    CREATE OR REPLACE FUNCTION trading.should_trigger_trailing_stop(
        trade_uuid UUID,
        current_market_price DECIMAL(20, 8)
    ) RETURNS BOOLEAN AS $$
    DECLARE
        trailing_record RECORD;
    BEGIN
        SELECT * INTO trailing_record
        FROM trading.trailing_stops
        WHERE trade_id = trade_uuid AND is_active = TRUE;
        IF NOT FOUND THEN
            RETURN FALSE;
        END IF;
        IF trailing_record.position_side = 'long' THEN
            RETURN current_market_price <= trailing_record.current_stop_price;
        END IF;
        IF trailing_record.position_side = 'short' THEN
            RETURN current_market_price >= trailing_record.current_stop_price;
        END IF;
        RETURN FALSE;
    END;
    $$ LANGUAGE plpgsql
    """.strip(),
    """
    CREATE OR REPLACE VIEW trading.active_trailing_stops AS
    SELECT
        ts.*,
        t.position_size,
        t.strategy,
        t.status AS trade_status,
        rtp.price AS latest_market_price,
        rtp.timestamp AS latest_price_time,
        ws.status AS websocket_status,
        CASE
            WHEN ts.position_side = 'long' THEN
                ROUND(((ts.current_price - ts.entry_price) / ts.entry_price * 100)::numeric, 4)
            ELSE
                ROUND(((ts.entry_price - ts.current_price) / ts.entry_price * 100)::numeric, 4)
        END AS current_pnl_percentage
    FROM trading.trailing_stops ts
    JOIN trading.trades t ON ts.trade_id = t.trade_id
    LEFT JOIN trading.real_time_prices rtp ON (ts.exchange = rtp.exchange AND ts.pair = rtp.pair)
    LEFT JOIN trading.websocket_status ws ON ts.exchange = ws.exchange
    WHERE ts.is_active = TRUE AND t.status = 'OPEN'
    """.strip(),
]


def _safe_float(val, default=None):
    if val is None:
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _extract_price_from_ccxt_ticker(data: dict) -> float:
    """CCXT / exchange-service may return last=null with close or info.lastPrice set (common on Bybit)."""
    for key in ("last", "close", "average", "markPrice"):
        v = data.get(key)
        px = _safe_float(v)
        if px is not None and px > 0:
            return px
    bid = _safe_float(data.get("bid"))
    ask = _safe_float(data.get("ask"))
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    info = data.get("info")
    if isinstance(info, dict):
        for key in ("lastPrice", "markPrice", "indexPrice", "c"):
            v = info.get(key)
            px = _safe_float(v)
            if px is not None and px > 0:
                return px
    return 0.0


def _extract_volume_from_ccxt_ticker(data: dict) -> float:
    v = data.get("baseVolume")
    if v is None:
        v = data.get("volume")
    if v is None and isinstance(data.get("info"), dict):
        v = data["info"].get("volume24h") or data["info"].get("turnover24h")
    return _safe_float(v, 0.0) or 0.0


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
        # Default config - will be loaded from config service
        self.config = {
            'enable_websocket_prices': True,  # Default to enabled, load from config
            'stale_price_threshold_seconds': 30,
            'rest_poll_interval_seconds': 1,
            'ws': {
                'max_concurrent_subscriptions_per_exchange': 50,
                'subscribe_only_for_open_trades': False  # Subscribe to all selected pairs
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

            await self._ensure_price_feed_schema()
            
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

    async def _ensure_price_feed_schema(self):
        """Create tables/functions used by this service (survives missed initdb migrations)."""
        try:
            async with self.db_pool.acquire() as conn:
                for stmt in _PRICE_FEED_DDL_STATEMENTS:
                    await conn.execute(stmt)
            logger.info("✅ Price-feed database schema verified (real_time_prices, trailing_stops, websocket_status)")
        except Exception as e:
            logger.error(f"❌ Failed to ensure price-feed schema: {e}")
            raise

    
    async def _load_active_pairs(self):
        """Load active trading pairs based on configuration and trading activity.

        WS-FIX (ticker-live 204 root cause): the previous implementation either
        used a hard-coded list of 15 pairs per exchange, or, when
        ``subscribe_only_for_open_trades=true``, only the open-trade set. The
        dynamic pair selector picks pairs OUTSIDE the hard-coded list every 15
        minutes, so any open trade in a non-pre-listed pair (e.g. bybit/TRX/USDC
        or binance/BLUR/USDC) never got a WS subscription → ``ticker-live``
        returned 204 forever and the trail-stop / stop-loss reactions ran on
        the slower REST/OHLCV fallback path.

        New behaviour: ALWAYS include open-trade pairs and recent-trade pairs
        (last 24h) in ``active_pairs``, regardless of the strategy flag. The
        hard-coded list is now an *additional* baseline used only when the
        strategy is "all-selected".
        """
        try:
            logger.info(f"🔧 DEBUG: _load_active_pairs called, current config: {self.config}")

            self.active_pairs.clear()

            subscribe_only_for_open_trades = self.config.get('ws', {}).get('subscribe_only_for_open_trades', False)
            logger.info(f"🔧 DEBUG: subscribe_only_for_open_trades = {subscribe_only_for_open_trades}")

            # ALWAYS load open-trade pairs first — these are the ones the
            # orchestrator's exit cycle is actively polling ticker-live for.
            # Skipping them for any reason guarantees stale stops.
            await self._merge_open_trade_pairs()
            await self._merge_recent_trade_pairs(hours=24)

            if not subscribe_only_for_open_trades:
                logger.info("📊 Adding hard-coded baseline of selected pairs to active monitoring")

                # Baseline pairs to keep WS warm for likely-to-be-traded symbols.
                # NOTE: this is just a hint — the open-trade merge above is
                # the source of truth for what MUST be subscribed.
                baseline_pairs = {
                    'binance': [
                        'BNB/USDC', 'BTC/USDC', 'ETH/USDC', 'XRP/USDC', 'XLM/USDC',
                        'LINK/USDC', 'LTC/USDC', 'TRX/USDC', 'ADA/USDC', 'NEO/USDC',
                        'ATOM/USDC', 'ETC/USDC', 'ALGO/USDC', 'DOGE/USDC', 'ONT/USDC'
                    ],
                    'cryptocom': [
                        'CRO/USD', '1INCH/USD', 'AAVE/USD', 'ACH/USD', 'ACT/USD',
                        'ADA/USD', 'AIXBT/USD', 'AKT/USD', 'ALGO/USD',
                        'ALICE/USD', 'ANIME/USD', 'ANKR/USD', 'APE/USD', 'API3/USD'
                    ],
                    'bybit': [
                        'ETH/USDC', 'BTC/USDC', 'XLM/USDC', 'SOL/USDC', 'XRP/USDC',
                        'LTC/USDC', 'MANA/USDC', 'SAND/USDC', 'DOT/USDC', 'LUNC/USDC',
                        'DOGE/USDC', 'AVAX/USDC', 'ADA/USDC', 'OP/USDC', 'APEX/USDC'
                    ]
                }

                for exchange, pairs in baseline_pairs.items():
                    if exchange not in self.active_pairs:
                        self.active_pairs[exchange] = set()
                    self.active_pairs[exchange].update(pairs)

            for exchange, pairs in self.active_pairs.items():
                logger.info(f"   📡 {exchange.upper()}: {len(pairs)} active pairs - {sorted(pairs)}")

            if not any(self.active_pairs.values()):
                logger.info("📊 No trading pairs found, will subscribe dynamically based on requests")

        except Exception as e:
            logger.error(f"❌ Failed to load active pairs: {e}")

    async def _merge_open_trade_pairs(self):
        """Merge currently-OPEN trade pairs into ``active_pairs`` (idempotent)."""
        try:
            async with self.db_pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT DISTINCT exchange, pair
                    FROM trading.trades
                    WHERE status = 'OPEN'
                    """
                )
            added = []
            for row in rows:
                exchange = row['exchange']
                pair = row['pair']
                if exchange not in self.active_pairs:
                    self.active_pairs[exchange] = set()
                if pair not in self.active_pairs[exchange]:
                    added.append(f"{exchange}/{pair}")
                self.active_pairs[exchange].add(pair)
            if added:
                logger.info(f"📊 Merged {len(added)} open-trade pair(s) into active_pairs: {added}")
            elif rows:
                logger.debug(f"📊 Open trades already present in active_pairs ({len(rows)} pairs)")
        except Exception as e:
            logger.warning(f"⚠️ Failed to merge open-trade pairs: {e}")

    async def _merge_recent_trade_pairs(self, hours: int = 24):
        """Merge recently-traded pairs (last N hours) into ``active_pairs``."""
        try:
            async with self.db_pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT DISTINCT exchange, pair
                    FROM trading.trades
                    WHERE created_at >= NOW() - ($1 || ' hours')::interval
                    ORDER BY exchange, pair
                    """,
                    str(hours),
                )
            for row in rows:
                exchange = row['exchange']
                pair = row['pair']
                if exchange not in self.active_pairs:
                    self.active_pairs[exchange] = set()
                self.active_pairs[exchange].add(pair)
        except Exception as e:
            logger.debug(f"_merge_recent_trade_pairs skipped: {e}")
    
    async def _load_open_trades_pairs(self):
        """Fallback method to load pairs from open trades only"""
        try:
            async with self.db_pool.acquire() as conn:
                open_trades_query = """
                    SELECT DISTINCT exchange, pair 
                    FROM trading.trades 
                    WHERE status = 'OPEN'
                """
                open_trades = await conn.fetch(open_trades_query)
                
                for row in open_trades:
                    exchange = row['exchange']
                    pair = row['pair']
                    
                    if exchange not in self.active_pairs:
                        self.active_pairs[exchange] = set()
                    self.active_pairs[exchange].add(pair)
                
                if open_trades:
                    logger.info(f"📊 Fallback: Loaded pairs from open trades: {dict((k, list(v)) for k, v in self.active_pairs.items())}")
        except Exception as e:
            logger.error(f"❌ Error loading open trades pairs: {e}")
    
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
                # Track which pairs got WebSocket updates to avoid REST API override
                ws_updated_pairs = set()
                
                # Prefer WS live price if enabled
                if self.config.get('enable_websocket_prices'):
                    ws_updated_pairs = await self._update_prices_from_ws_if_available()
                
                # Fallback/refresh via REST only for pairs that didn't get WebSocket data
                await self._fetch_all_prices(exclude_pairs=ws_updated_pairs)
                
                # Update trailing stops based on new prices
                await self._process_trailing_stop_updates()
                
                # Sleep for a short interval (adjust based on needs)
                await asyncio.sleep(self.config.get('rest_poll_interval_seconds', 1))
                
            except Exception as e:
                logger.error(f"❌ Error in price monitoring loop: {e}")
                await asyncio.sleep(5.0)  # Longer sleep on errors
    
    async def _fetch_all_prices(self, exclude_pairs=None):
        """Fetch current prices for all active trading pairs, excluding specified pairs"""
        if exclude_pairs is None:
            exclude_pairs = set()
            
        tasks = []
        
        for exchange_name, pairs in self.active_pairs.items():
            for pair in pairs:
                pair_key = f"{exchange_name}:{pair}"
                # Skip pairs that were successfully updated via WebSocket
                if pair_key not in exclude_pairs:
                    tasks.append(self._fetch_price(exchange_name, pair))
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _load_realtime_config(self):
        """Load realtime configuration from config service"""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{CONFIG_SERVICE_URL}/api/v1/config/realtime")
                if response.status_code == 200:
                    realtime_config = response.json()
                    logger.info(f"🔧 Raw config received: {realtime_config}")
                    
                    # Update config with values from config service
                    self.config['enable_websocket_prices'] = realtime_config.get('enable_websocket_prices', True)
                    self.config['stale_price_threshold_seconds'] = realtime_config.get('stale_price_threshold_seconds', 30)
                    self.config['rest_poll_interval_seconds'] = realtime_config.get('rest_poll_interval_seconds', 1)
                    
                    # Update WebSocket specific config
                    ws_config = realtime_config.get('ws', {})
                    self.config['ws']['max_concurrent_subscriptions_per_exchange'] = ws_config.get('max_concurrent_subscriptions_per_exchange', 50)
                    self.config['ws']['subscribe_only_for_open_trades'] = ws_config.get('subscribe_only_for_open_trades', False)
                    
                    logger.info(f"✅ Loaded realtime config: WebSocket={self.config['enable_websocket_prices']}, OpenTradesOnly={self.config['ws']['subscribe_only_for_open_trades']}")
                else:
                    logger.warning(f"Failed to load realtime config from config service: {response.status_code}")
        except Exception as e:
            logger.warning(f"Error loading realtime config, using defaults: {e}")
            


    async def _update_prices_from_ws_if_available(self):
        """Update prices from WebSocket and return set of pairs that were successfully updated"""
        updated_pairs = set()
        if not self.config.get('enable_websocket_prices'):
            return updated_pairs
            
        try:
            stale = self.config.get('stale_price_threshold_seconds', 30)
            async with httpx.AsyncClient(timeout=3.0) as client:
                for exchange_name, pairs in self.active_pairs.items():
                    for pair in pairs:
                        # Use correct symbol format for each exchange
                        if exchange_name == 'cryptocom':
                            symbol = pair  # Keep slash format for Crypto.com: ACH/USD
                        else:
                            symbol = pair.replace('/', '')  # Remove slash for others: ETHUSDC
                        url = f"{EXCHANGE_SERVICE_URL}/api/v1/market/ticker-live/{exchange_name}/{symbol}"
                        r = await client.get(url, params={'stale_threshold_seconds': stale})
                        if r.status_code == 200:
                            data = r.json()
                            price = _extract_price_from_ccxt_ticker(data)
                            bid = data.get('bid')
                            ask = data.get('ask')
                            if price > 0:
                                await self._store_price_update(
                                    exchange_name,
                                    pair,
                                    price,
                                    _safe_float(bid) if bid is not None else None,
                                    _safe_float(ask) if ask is not None else None,
                                    None,
                                    'websocket',
                                )
                                cache_key = f"{exchange_name}:{pair}"
                                self.price_cache[cache_key] = {
                                    'price': price,
                                    'bid': _safe_float(bid) if bid is not None else None,
                                    'ask': _safe_float(ask) if ask is not None else None,
                                    'timestamp': datetime.utcnow(),
                                    'source': 'websocket'
                                }
                                # Track this pair as successfully updated via WebSocket
                                updated_pairs.add(f"{exchange_name}:{pair}")
        except Exception as e:
            logger.debug(f"WS price update skipped: {e}")
            
        return updated_pairs
    
    async def _fetch_price(self, exchange_name: str, pair: str):
        """Fetch price for a specific exchange/pair"""
        try:
            pair = unquote(pair)
            # Match orchestrator: exchange-service ticker may await slow CCXT/upstream.
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=8.0, read=25.0, write=25.0, pool=10.0)
            ) as client:
                # Use correct symbol format for each exchange
                if exchange_name == 'cryptocom':
                    symbol = pair  # Keep slash format for Crypto.com: ACH/USD
                else:
                    symbol = pair.replace('/', '')  # Remove slash for others: ETHUSDC
                
                response = await client.get(
                    f"{EXCHANGE_SERVICE_URL}/api/v1/market/ticker/{exchange_name}/{symbol}"
                )
                
                if response.status_code == 200:
                    data = response.json()
                    price = _extract_price_from_ccxt_ticker(data)
                    bid_raw = data.get("bid")
                    ask_raw = data.get("ask")
                    bid = _safe_float(bid_raw) if bid_raw is not None else None
                    ask = _safe_float(ask_raw) if ask_raw is not None else None
                    volume = _extract_volume_from_ccxt_ticker(data)
                    
                    if price > 0:
                        await self._store_price_update(
                            exchange_name, pair, price,
                            bid, ask,
                            volume,
                            'rest_api',
                        )
                        
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
                snippet = (response.text or "")[:200].replace("\n", " ")
                logger.warning(
                    "price_feed _fetch_price non-200 exchange=%s pair=%s symbol=%s http=%s body=%s",
                    exchange_name,
                    pair,
                    symbol,
                    response.status_code,
                    snippet,
                )

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
        """Dedicated loop for trailing stop management.

        WS-FIX: lowered cadence from 60s → 15s. The orchestrator's exit cycle
        runs every 30s and reacts to ticker-live for trail-stop hits, so the
        subscription set must converge faster than the exit cycle itself when
        a new trade opens on a non-baseline pair (e.g. dynamically-selected).
        Cleanup of old price rows still runs once per minute.
        """
        logger.info("🚀 Starting trailing stop update loop (ws-sync every 15s, cleanup every 60s)")

        cleanup_counter = 0
        while not self.shutdown_event.is_set():
            try:
                await self._load_active_pairs()
                await self._sync_ws_subscriptions()

                cleanup_counter += 1
                if cleanup_counter >= 4:  # every ~60s when sleeping 15s
                    await self._cleanup_old_prices()
                    cleanup_counter = 0

                await asyncio.sleep(15.0)

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
        """Subscribe to live ticker streams for every pair the bot is actively trading.

        WS-FIX: previously gated entirely by ``enable_websocket_prices`` — when
        that flag was false (the current production setting), NO subscriptions
        were pushed to exchange-service even though the orchestrator's exit
        cycle was hammering ``/api/v1/market/ticker-live/...`` for every open
        trade and getting 204s. We now always sync subscriptions for the
        actively-traded set so trail-stops / stop-losses run on real WS prices.
        """
        try:
            max_per_ex = self.config.get('ws', {}).get('max_concurrent_subscriptions_per_exchange', 50)
            async with httpx.AsyncClient(timeout=10.0) as client:
                for exchange_name, pairs in self.active_pairs.items():
                    # Use correct symbol format for each exchange
                    if exchange_name == 'cryptocom':
                        symbols = list(pairs)[:max_per_ex]  # Keep slash format for Crypto.com
                    else:
                        symbols = [p.replace('/', '') for p in list(pairs)][:max_per_ex]  # Remove slash for others
                    if not symbols:
                        continue
                    try:
                        resp = await client.post(
                            f"{EXCHANGE_SERVICE_URL}/api/v1/websocket/{exchange_name}/subscribe",
                            json={'symbols': symbols},
                        )
                        if resp.status_code == 200:
                            logger.info(
                                f"📡 Synced WS subscriptions for {exchange_name}: {len(symbols)} symbol(s)"
                            )
                        elif resp.status_code == 503:
                            logger.warning(
                                f"⚠️ {exchange_name} WS not connected — subscription deferred "
                                f"({len(symbols)} symbol(s) queued for next sync)"
                            )
                        else:
                            logger.warning(
                                f"⚠️ Subscription call returned {resp.status_code} for {exchange_name}: {resp.text}"
                            )
                    except Exception as sub_e:
                        logger.warning(f"⚠️ Subscription error for {exchange_name}: {sub_e}")
        except Exception as e:
            logger.warning(f"⚠️ _sync_ws_subscriptions failed: {e}")
    
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

@app.post("/api/v1/pairs/subscribe")
async def subscribe_to_pair(request: dict):
    """Subscribe to price updates for a specific trading pair"""
    try:
        exchange = request.get('exchange')
        pair = request.get('pair')
        
        if not exchange or not pair:
            return {"status": "error", "message": "Exchange and pair are required"}
        
        # Add pair to active monitoring
        if exchange not in price_feed_manager.active_pairs:
            price_feed_manager.active_pairs[exchange] = set()
        
        was_new = pair not in price_feed_manager.active_pairs[exchange]
        price_feed_manager.active_pairs[exchange].add(pair)
        
        if was_new:
            # Subscribe to WebSocket for this pair
            await price_feed_manager._sync_ws_subscriptions()
            logger.info(f"📡 Dynamically subscribed to {exchange}/{pair}")
        
        return {
            "status": "subscribed",
            "exchange": exchange,
            "pair": pair,
            "was_new": was_new
        }
        
    except Exception as e:
        logger.error(f"❌ Failed to subscribe to pair: {e}")
        return {"status": "error", "message": str(e)}

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

@app.get("/api/v1/price/{exchange}/{pair:path}")
async def get_current_price(exchange: str, pair: str):
    """Get current price for a specific exchange/pair from cache"""
    try:
        # Normalize pair (orchestrator calls with quote(); path may still contain %2F)
        decoded_pair = unquote(pair)
        exchange_key = exchange.lower()

        # Try different cache key formats to match what's actually stored
        possible_keys = [
            f"{exchange_key}:{decoded_pair}",
            f"{exchange}:{decoded_pair}",
            f"{exchange.lower()}:{decoded_pair.upper()}",
            f"{exchange.upper()}:{decoded_pair.lower()}",
            f"{exchange.upper()}:{decoded_pair.upper()}",
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
                            "pair": decoded_pair,
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
                        WHERE LOWER(exchange) = LOWER($1) AND pair = $2
                        ORDER BY timestamp DESC
                        LIMIT 1
                    """, exchange, decoded_pair)
                    
                    if row and row['price']:
                        return {
                            "exchange": exchange,
                            "pair": decoded_pair,
                            "price": float(row['price']),
                            "bid": float(row['bid']) if row['bid'] else None,
                            "ask": float(row['ask']) if row['ask'] else None,
                            "timestamp": row['timestamp'].isoformat(),
                            "source": row['source'],
                            "cache_hit": False
                        }
            except Exception as db_e:
                logger.debug(f"Database query failed for {exchange}/{decoded_pair}: {db_e}")
        
        # If still no price, fetch fresh data
        current_price = await price_feed_manager._fetch_price(exchange, decoded_pair)
        
        if current_price and current_price > 0:
            return {
                "exchange": exchange,
                "pair": decoded_pair,
                "price": current_price,
                "timestamp": datetime.utcnow().isoformat(),
                "source": "fresh_fetch",
                "cache_hit": False
            }
        
        raise HTTPException(
            status_code=404,
            detail=f"No price available for {exchange}/{decoded_pair} (cache/DB empty and exchange ticker fetch failed)",
        )
        
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