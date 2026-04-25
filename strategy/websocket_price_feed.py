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
        
        # HTTP client for API calls
        self.http_client = httpx.AsyncClient(timeout=5.0)
        
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
        self.stale_threshold = 10.0  # Mark data stale after 10 seconds
        
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
        """Get current price for a symbol (WebSocket preferred, REST fallback)"""
        try:
            # Try WebSocket data first
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
            
            # WebSocket data unavailable/stale, use REST fallback
            self.metrics['fallback_requests'] += 1
            
            response = await self.http_client.get(
                f"{self.exchange_url}/api/v1/market/ticker/{exchange}/{symbol}"
            )
            response.raise_for_status()
            
            ticker_data = response.json()
            price = float(ticker_data.get('last', 0))
            if price > 0:
                logger.debug(f"📈 REST fallback price: {exchange} {symbol} = ${price:.4f}")
                return price
                
        except Exception as e:
            logger.error(f"❌ Error getting current price for {exchange} {symbol}: {e}")
            self.metrics['websocket_errors'] += 1
        
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