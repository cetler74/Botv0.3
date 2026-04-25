"""
Enhanced WebSocket Price Feed Integration for Trailing Stops

This module provides enhanced WebSocket price monitoring that serves both
trailing stop management and dashboard real-time updates. It integrates 
with the existing exchange service WebSocket infrastructure and provides
optimized price feeds for the trailing stop system.

Key Features:
- Real-time WebSocket price monitoring for active trades
- Automatic subscription management for traded symbols
- Price change detection and alerts
- Dashboard integration for real-time UI updates
- Fallback mechanisms for WebSocket failures
- Performance optimization for high-frequency updates

Author: Claude Code
Created: 2025-08-30
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Set, Callable
import httpx
import json
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

# Calls hit exchange-service, which may call CCXT / public APIs; 5s read caused
# constant ReadTimeout under load or when upstream Binance is slow (see logs).
_EXCHANGE_HTTP_TIMEOUT = httpx.Timeout(
    connect=8.0, read=25.0, write=25.0, pool=10.0
)


class PriceFeedStatus(Enum):
    """Status of price feed connection"""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"
    STALE = "stale"

@dataclass
class PriceUpdate:
    """Real-time price update data"""
    exchange: str
    symbol: str
    price: float
    bid: Optional[float] = None
    ask: Optional[float] = None
    timestamp: datetime = None
    source: str = "websocket"
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.utcnow()

@dataclass 
class SubscriptionInfo:
    """Symbol subscription tracking"""
    exchange: str
    symbol: str
    trade_ids: Set[str]
    last_price: Optional[float] = None
    last_update: Optional[datetime] = None
    update_count: int = 0
    
class WebSocketPriceFeed:
    """
    Enhanced WebSocket price feed manager for trailing stops and dashboard
    
    This class manages real-time price subscriptions and provides optimized
    price updates for both trailing stop management and dashboard UI needs.
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize WebSocket price feed
        
        Args:
            config: Configuration dictionary
        """
        self.config = config
        
        # Service endpoints
        self.exchange_url = "http://exchange-service:8003"
        
        # HTTP client for API calls (ticker-live + REST ticker + health)
        self.http_client = httpx.AsyncClient(timeout=_EXCHANGE_HTTP_TIMEOUT)
        
        # Subscription management
        self.subscriptions: Dict[str, SubscriptionInfo] = {}  # "exchange:symbol" -> SubscriptionInfo
        self.active_trades: Dict[str, Dict[str, str]] = {}    # trade_id -> {exchange, symbol}
        
        # Price callbacks and monitoring
        self.price_callbacks: List[Callable[[PriceUpdate], None]] = []
        self.dashboard_callbacks: List[Callable[[PriceUpdate], None]] = []
        
        # Status tracking
        self.status = PriceFeedStatus.DISCONNECTED
        self.last_health_check = None
        self.monitoring_active = False
        
        # Performance metrics
        self.metrics = {
            'price_updates_received': 0,
            'price_updates_processed': 0,
            'websocket_errors': 0,
            'fallback_requests': 0,
            'active_subscriptions': 0
        }
        
        # Configuration
        self.poll_interval = 1.0  # Check for new trades every second
        self.health_check_interval = 30.0  # Health check every 30 seconds
        # WS-FIX: bumped from 10s → 45s. Many low-volume pairs (e.g. cryptocom
        # ACT/USD, AKT/USD) only print ticks every 15-30s on the WS feed even
        # when the subscription is healthy. A 10s freshness window forced the
        # orchestrator to treat those as "stale" and fall back to REST/OHLCV
        # every poll, generating constant 204s. 45s is loose enough to cover
        # the typical low-vol cadence while still catching a truly dead feed
        # (the dynamic_threshold method below can tighten further per-symbol).
        self.stale_threshold = 45.0
        
        logger.info("🎯 WebSocketPriceFeed initialized")
    
    async def start(self):
        """Start WebSocket price feed monitoring"""
        if self.monitoring_active:
            logger.warning("WebSocket price feed already active")
            return
            
        self.monitoring_active = True
        self.status = PriceFeedStatus.CONNECTING
        
        logger.info("🚀 Starting WebSocket price feed monitoring")
        
        try:
            # Start monitoring tasks
            await asyncio.gather(
                self._price_monitoring_loop(),
                self._health_monitoring_loop(),
                self._subscription_management_loop()
            )
        except Exception as e:
            logger.error(f"❌ Critical error in price feed monitoring: {e}")
            self.status = PriceFeedStatus.ERROR
            self.monitoring_active = False
            raise
    
    async def stop(self):
        """Stop WebSocket price feed monitoring"""
        logger.info("🛑 Stopping WebSocket price feed monitoring")
        self.monitoring_active = False
        self.status = PriceFeedStatus.DISCONNECTED
        
        # Clear subscriptions
        self.subscriptions.clear()
        self.active_trades.clear()
        
        # Close HTTP client
        await self.http_client.aclose()
    
    async def subscribe_to_trade(self, trade_id: str, exchange: str, symbol: str):
        """Subscribe to price updates for a specific trade"""
        subscription_key = f"{exchange}:{symbol}"
        
        if subscription_key not in self.subscriptions:
            self.subscriptions[subscription_key] = SubscriptionInfo(
                exchange=exchange,
                symbol=symbol,
                trade_ids={trade_id}
            )
            logger.info(f"📡 New price subscription: {exchange} {symbol}")
        else:
            self.subscriptions[subscription_key].trade_ids.add(trade_id)
            logger.debug(f"➕ Added trade {trade_id} to existing subscription: {exchange} {symbol}")
        
        # Track trade mapping
        self.active_trades[trade_id] = {"exchange": exchange, "symbol": symbol}
        
        # Update metrics
        self.metrics['active_subscriptions'] = len(self.subscriptions)
        
        # Get initial price
        await self._fetch_initial_price(exchange, symbol)
    
    async def unsubscribe_from_trade(self, trade_id: str):
        """Unsubscribe from price updates for a completed trade"""
        if trade_id not in self.active_trades:
            return
            
        trade_info = self.active_trades[trade_id]
        subscription_key = f"{trade_info['exchange']}:{trade_info['symbol']}"
        
        if subscription_key in self.subscriptions:
            subscription = self.subscriptions[subscription_key]
            subscription.trade_ids.discard(trade_id)
            
            # Remove subscription if no trades remain
            if not subscription.trade_ids:
                del self.subscriptions[subscription_key]
                logger.info(f"🗑️ Removed price subscription: {trade_info['exchange']} {trade_info['symbol']}")
            else:
                logger.debug(f"➖ Removed trade {trade_id} from subscription: {trade_info['exchange']} {trade_info['symbol']}")
        
        # Remove trade mapping
        del self.active_trades[trade_id]
        
        # Update metrics
        self.metrics['active_subscriptions'] = len(self.subscriptions)
    
    def add_price_callback(self, callback: Callable[[PriceUpdate], None]):
        """Add callback for price updates (for trailing stop manager)"""
        self.price_callbacks.append(callback)
        logger.debug(f"➕ Added price callback (total: {len(self.price_callbacks)})")
    
    def add_dashboard_callback(self, callback: Callable[[PriceUpdate], None]):
        """Add callback for dashboard updates (for UI real-time updates)"""
        self.dashboard_callbacks.append(callback)
        logger.debug(f"➕ Added dashboard callback (total: {len(self.dashboard_callbacks)})")
    
    async def get_current_price(self, exchange: str, symbol: str) -> Optional[float]:
        """Get current price for a symbol.

        PnL-FIX v8: hardened price chain with Redis fallback and rate-limited
        404 logging. New order:
          1. ticker-live (in-memory WS cache, fresh)
          2. Redis ``ticker:live:{exchange}|{normalized_symbol}`` (WS mirror)
          3. REST ticker (only if pair is not in the unsupported-set)
        404s from REST (e.g. bybit/TRX/USDC where the pair isn't listed
        spot) are logged ONCE at WARNING then suppressed — they previously
        spammed two ERROR lines + stacktrace every 2 seconds.
        """
        # Step 1: in-memory WS cache via exchange-service ticker-live
        try:
            response = await self.http_client.get(
                f"{self.exchange_url}/api/v1/market/ticker-live/{exchange}/{symbol}",
                params={"stale_threshold_seconds": int(self.stale_threshold)}
            )
            if response.status_code == 200:
                ticker_data = response.json()
                price = float(ticker_data.get('last', 0))
                if price > 0:
                    logger.debug(f"📡 WebSocket price: {exchange} {symbol} = ${price:.4f}")
                    return price
        except Exception as e:
            logger.debug(f"ticker-live miss for {exchange} {symbol}: {e}")

        # Step 2: Redis fallback (WS mirror written by exchange-service)
        try:
            redis_price = await self._get_redis_ticker_last(exchange, symbol)
            if redis_price and redis_price > 0:
                logger.debug(f"📦 Redis price: {exchange} {symbol} = ${redis_price:.6f}")
                self.metrics['fallback_requests'] += 1
                return redis_price
        except Exception as e:
            logger.debug(f"Redis ticker miss for {exchange} {symbol}: {e}")

        # Step 3: REST fallback — skip if this pair previously 404'd
        unsupported = getattr(self, '_unsupported_pairs', None)
        if unsupported is None:
            self._unsupported_pairs = set()
            unsupported = self._unsupported_pairs
        pair_key = (exchange, symbol)
        if pair_key in unsupported:
            return None

        self.metrics['fallback_requests'] += 1
        try:
            response = await self.http_client.get(
                f"{self.exchange_url}/api/v1/market/ticker/{exchange}/{symbol}"
            )
            if response.status_code == 404:
                unsupported.add(pair_key)
                logger.warning(
                    "⚠️ Pair %s/%s not listed on exchange (REST 404) — suppressing "
                    "further price-fetch attempts. Verify the trade was created "
                    "on the correct exchange/quote (e.g. USDT vs USDC).",
                    exchange, symbol,
                )
                return None
            response.raise_for_status()
            ticker_data = response.json()
            price = float(ticker_data.get('last', 0))
            if price > 0:
                logger.debug(f"📈 REST fallback price: {exchange} {symbol} = ${price:.4f}")
                return price
        except Exception as e:
            base = type(e).__name__
            msg = str(e).strip()
            detail = f"{base}: {msg}" if msg else base
            if isinstance(e, httpx.HTTPStatusError) and e.response is not None:
                r = e.response
                body = (r.text or "")[:350].replace("\n", " ")
                detail = f"{detail} | http={r.status_code} | body_prefix={body}"
            elif isinstance(
                e,
                (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.WriteTimeout, httpx.PoolTimeout),
            ):
                detail = (
                    f"{detail} | orchestrator→exchange-service "
                    f"read_timeout_s={_EXCHANGE_HTTP_TIMEOUT.read}"
                )
            logger.warning(
                "⚠️ REST ticker fetch failed for %s %s: %s",
                exchange,
                symbol,
                detail,
            )
            self.metrics['websocket_errors'] += 1

        return None

    async def _get_redis_ticker_last(self, exchange: str, symbol: str) -> Optional[float]:
        """Read ``last`` price from Redis ``ticker:live:{exchange}|{sym}`` mirror.

        Mirror is written by services/exchange-service/main.py whenever a
        ticker arrives via WS or REST poll. This gives the trail manager a
        usable price even when the exchange-service in-memory cache is cold.
        Symbol normalization matches ``_normalize_symbol_key`` in
        exchange-service/main.py.
        """
        rcl = await self._get_redis_client()
        if rcl is None:
            return None
        try:
            if exchange.lower() == 'cryptocom':
                normalized = symbol.replace('/', '_') if '/' in symbol else symbol
            else:
                normalized = ''.join(ch for ch in str(symbol) if ch.isalnum())
            rk = f"ticker:live:{exchange}|{normalized}"
            raw = await rcl.hget(rk, 'last')
            if raw is None:
                return None
            value = float(raw)
            return value if value > 0 else None
        except Exception:
            return None

    async def _get_redis_client(self):
        """Lazy async Redis client for ticker-live mirror reads."""
        existing = getattr(self, '_redis_client', None)
        if existing is not None:
            return existing
        if getattr(self, '_redis_disabled', False):
            return None
        try:
            import os
            url = os.getenv('REDIS_URL', '').strip() or 'redis://trading-bot-redis:6379/0'
            try:
                import redis.asyncio as aioredis
            except ImportError:
                from redis import asyncio as aioredis
            client = aioredis.from_url(url, decode_responses=True)
            await client.ping()
            self._redis_client = client
            logger.info("✅ WebSocketPriceFeed Redis fallback client ready (ticker:live:* mirror)")
            return client
        except Exception as e:
            self._redis_disabled = True
            logger.warning(f"WebSocketPriceFeed Redis fallback disabled: {e}")
            return None
    
    async def _price_monitoring_loop(self):
        """Main price monitoring loop"""
        logger.info("🔄 Price monitoring loop started")
        
        while self.monitoring_active:
            try:
                # Process all active subscriptions
                for subscription_key, subscription in list(self.subscriptions.items()):
                    await self._process_subscription(subscription)
                
                # Update status
                if self.subscriptions:
                    self.status = PriceFeedStatus.CONNECTED
                else:
                    self.status = PriceFeedStatus.DISCONNECTED
                
                await asyncio.sleep(self.poll_interval)
                
            except Exception as e:
                logger.error(f"❌ Error in price monitoring loop: {e}")
                self.metrics['websocket_errors'] += 1
                await asyncio.sleep(self.poll_interval)
    
    async def _process_subscription(self, subscription: SubscriptionInfo):
        """Process price updates for a subscription"""
        try:
            # Get current price
            current_price = await self.get_current_price(subscription.exchange, subscription.symbol)
            
            if current_price is None:
                return
            
            # Check for price change
            price_changed = (
                subscription.last_price is None or 
                abs(current_price - subscription.last_price) > 0.0001
            )
            
            if price_changed or self._should_send_periodic_update(subscription):
                # Create price update
                price_update = PriceUpdate(
                    exchange=subscription.exchange,
                    symbol=subscription.symbol,
                    price=current_price,
                    timestamp=datetime.utcnow(),
                    source="websocket" if not self.metrics['fallback_requests'] else "rest"
                )
                
                # Update subscription tracking
                subscription.last_price = current_price
                subscription.last_update = datetime.utcnow()
                subscription.update_count += 1
                
                # Send to callbacks
                await self._notify_callbacks(price_update)
                
                # Update metrics
                self.metrics['price_updates_received'] += 1
                self.metrics['price_updates_processed'] += 1
                
                if price_changed:
                    logger.debug(f"📊 Price update: {subscription.exchange} {subscription.symbol} = ${current_price:.4f}")
        
        except Exception as e:
            logger.error(f"❌ Error processing subscription {subscription.exchange}:{subscription.symbol}: {e}")
            self.metrics['websocket_errors'] += 1
    
    def _should_send_periodic_update(self, subscription: SubscriptionInfo) -> bool:
        """Check if we should send periodic update even without price change"""
        if not subscription.last_update:
            return True
            
        # Send periodic updates every 30 seconds for dashboard
        time_since_update = (datetime.utcnow() - subscription.last_update).total_seconds()
        return time_since_update > 30.0
    
    async def _notify_callbacks(self, price_update: PriceUpdate):
        """Notify all registered callbacks of price update"""
        try:
            # Notify trailing stop callbacks (synchronous for performance)
            for callback in self.price_callbacks:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(price_update)
                    else:
                        callback(price_update)
                except Exception as e:
                    logger.error(f"❌ Error in price callback: {e}")
            
            # Notify dashboard callbacks (can be async)
            for callback in self.dashboard_callbacks:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(price_update)
                    else:
                        callback(price_update)
                except Exception as e:
                    logger.error(f"❌ Error in dashboard callback: {e}")
                    
        except Exception as e:
            logger.error(f"❌ Error notifying callbacks: {e}")
    
    async def _health_monitoring_loop(self):
        """Monitor WebSocket health and connection status"""
        logger.info("❤️ Health monitoring loop started")
        
        while self.monitoring_active:
            try:
                await self._perform_health_check()
                await asyncio.sleep(self.health_check_interval)
                
            except Exception as e:
                logger.error(f"❌ Error in health monitoring: {e}")
                await asyncio.sleep(self.health_check_interval)
    
    async def _perform_health_check(self):
        """Perform WebSocket health check"""
        try:
            # Check WebSocket status via API
            response = await self.http_client.get(
                f"{self.exchange_url}/api/v1/websocket/monitor/status"
            )
            response.raise_for_status()
            
            status_data = response.json()
            connected_exchanges = status_data.get('summary', {}).get('connected_exchanges', 0)
            total_exchanges = status_data.get('summary', {}).get('total_exchanges', 0)
            
            self.last_health_check = datetime.utcnow()
            
            if connected_exchanges >= total_exchanges * 0.5:  # At least 50% connected
                if self.status == PriceFeedStatus.ERROR:
                    self.status = PriceFeedStatus.CONNECTED
                    logger.info("✅ WebSocket health recovered")
            else:
                self.status = PriceFeedStatus.ERROR
                logger.warning(f"⚠️ WebSocket health degraded: {connected_exchanges}/{total_exchanges} exchanges connected")
            
            logger.debug(f"❤️ Health check: {connected_exchanges}/{total_exchanges} exchanges connected")
            
        except Exception as e:
            logger.error(f"❌ Health check failed: {e}")
            self.status = PriceFeedStatus.ERROR
    
    async def _subscription_management_loop(self):
        """Manage subscriptions and cleanup stale ones"""
        logger.info("🔧 Subscription management loop started")
        
        while self.monitoring_active:
            try:
                await self._cleanup_stale_subscriptions()
                await asyncio.sleep(60)  # Check every minute
                
            except Exception as e:
                logger.error(f"❌ Error in subscription management: {e}")
                await asyncio.sleep(60)
    
    async def _cleanup_stale_subscriptions(self):
        """Clean up subscriptions that haven't been updated recently"""
        current_time = datetime.utcnow()
        stale_subscriptions = []
        
        for subscription_key, subscription in self.subscriptions.items():
            if subscription.last_update:
                age = (current_time - subscription.last_update).total_seconds()
                if age > 300:  # 5 minutes without update
                    stale_subscriptions.append(subscription_key)
        
        for subscription_key in stale_subscriptions:
            subscription = self.subscriptions[subscription_key]
            logger.warning(f"🧹 Cleaning up stale subscription: {subscription.exchange} {subscription.symbol}")
            
            # Remove associated trades
            for trade_id in list(subscription.trade_ids):
                if trade_id in self.active_trades:
                    del self.active_trades[trade_id]
            
            del self.subscriptions[subscription_key]
        
        if stale_subscriptions:
            self.metrics['active_subscriptions'] = len(self.subscriptions)
    
    async def _fetch_initial_price(self, exchange: str, symbol: str):
        """Fetch initial price for a new subscription"""
        try:
            price = await self.get_current_price(exchange, symbol)
            if price:
                subscription_key = f"{exchange}:{symbol}"
                if subscription_key in self.subscriptions:
                    self.subscriptions[subscription_key].last_price = price
                    self.subscriptions[subscription_key].last_update = datetime.utcnow()
                    logger.debug(f"💰 Initial price: {exchange} {symbol} = ${price:.4f}")
        
        except Exception as e:
            logger.error(f"❌ Error fetching initial price for {exchange} {symbol}: {e}")
    
    # Public interface methods
    
    def get_subscription_count(self) -> int:
        """Get number of active subscriptions"""
        return len(self.subscriptions)
    
    def get_active_trades_count(self) -> int:
        """Get number of active trades being monitored"""
        return len(self.active_trades)
    
    def get_status(self) -> Dict[str, Any]:
        """Get current status and metrics"""
        return {
            'status': self.status.value,
            'active_subscriptions': len(self.subscriptions),
            'active_trades': len(self.active_trades),
            'last_health_check': self.last_health_check.isoformat() if self.last_health_check else None,
            'metrics': self.metrics.copy(),
            'subscriptions': {
                key: {
                    'exchange': sub.exchange,
                    'symbol': sub.symbol,
                    'trade_count': len(sub.trade_ids),
                    'last_price': sub.last_price,
                    'last_update': sub.last_update.isoformat() if sub.last_update else None,
                    'update_count': sub.update_count
                }
                for key, sub in self.subscriptions.items()
            }
        }
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get performance metrics"""
        return self.metrics.copy()
    
    async def force_price_update(self, exchange: str, symbol: str) -> Optional[float]:
        """Force immediate price update for a symbol"""
        price = await self.get_current_price(exchange, symbol)
        
        if price:
            price_update = PriceUpdate(
                exchange=exchange,
                symbol=symbol,
                price=price,
                timestamp=datetime.utcnow(),
                source="forced"
            )
            
            await self._notify_callbacks(price_update)
            logger.info(f"🔄 Forced price update: {exchange} {symbol} = ${price:.4f}")
        
        return price
    
    def __repr__(self):
        """String representation"""
        return f"WebSocketPriceFeed(subscriptions={len(self.subscriptions)}, trades={len(self.active_trades)}, status={self.status.value})"


# Factory function for easy initialization
def create_websocket_price_feed(config: Dict[str, Any]) -> WebSocketPriceFeed:
    """
    Create WebSocket price feed instance
    
    Args:
        config: Configuration dictionary
        
    Returns:
        WebSocketPriceFeed instance
    """
    
    return WebSocketPriceFeed(config)