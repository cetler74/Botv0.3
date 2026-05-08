"""
Order Lifecycle Management Integration

This module provides the integration layer between the TrailingStopManager
and the existing database and exchange services. It handles all order
lifecycle operations including creation, tracking, updates, and fills.

Key Features:
- Database service integration for trade management
- Exchange service integration for order operations  
- Real-time price monitoring via WebSocket
- Complete order status tracking and synchronization
- Error handling and recovery mechanisms

Author: Claude Code
Created: 2025-08-30
"""

import asyncio
import logging
from datetime import datetime
from typing import Dict, Any, Optional, List
import httpx
from dataclasses import asdict

from trailing_stop_manager import TrailingStopManager, TrailingStopData, TrailingStopState
from websocket_price_feed import WebSocketPriceFeed, PriceUpdate

logger = logging.getLogger(__name__)

class OrderLifecycleManager:
    """
    Integration manager for order lifecycle operations
    
    Provides the bridge between TrailingStopManager and existing services:
    - Database Service (Port 8002) - Trade data and order tracking
    - Exchange Service (Port 8003) - Market data and order execution
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the order lifecycle manager
        
        Args:
            config: Configuration dictionary
        """
        self.config = config
        
        # Service endpoints
        self.database_url = "http://database-service:8002"
        self.exchange_url = "http://exchange-service:8003"
        
        # HTTP clients with appropriate timeouts
        self.http_client = httpx.AsyncClient(timeout=30.0)
        self.http_client_fast = httpx.AsyncClient(timeout=5.0)  # For quick operations
        
        # Initialize WebSocket price feed
        self.price_feed = WebSocketPriceFeed(config)
        
        # PnL-FIX v8 (Q3): pass price_feed into the trail manager so it can
        # subscribe directly to the WS callback and ratchet the trail on
        # every tick (event-driven), instead of relying solely on its
        # internal polling loop.
        self.trailing_manager = TrailingStopManager(
            config=config,
            database_service=self,
            exchange_service=self,
            price_feed=self.price_feed,
        )
        
        # Connect price feed to lifecycle manager (debug/monitoring only —
        # the trail manager registers its own callback in start_monitoring).
        self.price_feed.add_price_callback(self._handle_price_update)
        
        logger.info("🔗 OrderLifecycleManager initialized with service integrations")
    
    async def start(self):
        """Start the order lifecycle management system.

        PnL-FIX v8 (Q3): the previous implementation did
        ``await asyncio.create_task(self.price_feed.start())`` followed by
        ``await self.trailing_manager.start_monitoring()``. Because
        ``price_feed.start()`` runs an unbounded loop, the await on its
        task never returned and ``trailing_manager.start_monitoring()`` was
        **never reached** — so the dedicated trail-monitoring loop never
        ran (the trail-arming you saw in production was happening only via
        the per-cycle exit checks). This now spawns BOTH as background
        tasks so each can run concurrently for the lifetime of the service.
        """
        logger.info("🚀 Starting Order Lifecycle Management")

        # Spawn both as background tasks (each runs an unbounded loop).
        self._price_feed_task = asyncio.create_task(self.price_feed.start())
        self._trailing_task = asyncio.create_task(self.trailing_manager.start_monitoring())

        logger.info(
            "✅ Order Lifecycle Management running — price_feed + trailing_manager "
            "spawned as background tasks (event-driven trail callback registered)"
        )
    
    async def stop(self):
        """Stop the order lifecycle management system"""
        logger.info("🛑 Stopping Order Lifecycle Management")
        
        # Stop trailing stop manager
        await self.trailing_manager.stop_monitoring()
        
        # Stop WebSocket price feed
        await self.price_feed.stop()

        # Cancel background tasks if still running.
        for attr in ("_trailing_task", "_price_feed_task"):
            task = getattr(self, attr, None)
            if task and not task.done():
                task.cancel()
        
        # Close HTTP clients
        await self.http_client.aclose()
        await self.http_client_fast.aclose()
    
    # Database Service Integration Methods
    
    async def get_open_trades_without_exit(self) -> List[Any]:
        """Get all OPEN trades that don't have an exit_id (no active trailing stop).

        PnL-FIX v8: hardened response parsing — the database service returns
        ``{"trades": [...]}`` (the previous code iterated the dict's keys
        which are strings, producing ``'str' object has no attribute 'get'``
        once the trail loop actually started running). Accept both list and
        dict-wrapped shapes for safety.
        """
        try:
            response = await self.http_client_fast.get(f"{self.database_url}/api/v1/trades/open")
            response.raise_for_status()
            payload = response.json()

            if isinstance(payload, dict):
                trades = payload.get("trades") or payload.get("data") or []
            elif isinstance(payload, list):
                trades = payload
            else:
                logger.warning(
                    "Unexpected /trades/open payload type %s — treating as empty",
                    type(payload).__name__,
                )
                trades = []

            open_trades_no_exit: List[Any] = []
            for trade in trades:
                if not isinstance(trade, dict):
                    continue
                if trade.get('status') != 'OPEN' or trade.get('exit_id'):
                    continue
                # Build a lightweight object so callers can use attribute
                # access (trade.id, trade.exchange, ...). ``type(...)`` was
                # being misused: 3rd arg must be a dict of class members,
                # not the trade row itself. We just set attributes.
                trade_obj = type('Trade', (), {})()
                for k, v in trade.items():
                    try:
                        setattr(trade_obj, k, v)
                    except Exception:
                        pass
                trade_obj.id = trade.get('trade_id') or trade.get('id')
                open_trades_no_exit.append(trade_obj)

            logger.debug(f"📊 Found {len(open_trades_no_exit)} OPEN trades without exit_id")
            return open_trades_no_exit

        except Exception as e:
            logger.error(f"❌ Error getting open trades: {e}")
            return []
    
    async def update_trade_exit_id(self, trade_id: str, exit_id: str) -> bool:
        """Update trade with exit order ID"""
        try:
            update_data = {
                "exit_id": exit_id,
                "updated_at": datetime.utcnow().isoformat()
            }
            
            response = await self.http_client.put(
                f"{self.database_url}/api/v1/trades/{trade_id}",
                json=update_data
            )
            response.raise_for_status()
            
            logger.info(f"✅ Updated trade {trade_id} with exit_id: {exit_id}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Error updating trade {trade_id} exit_id: {e}")
            return False

    async def update_trade(self, trade_id: str, update_data: Dict[str, Any]) -> bool:
        """Generic trade update compatibility method for TrailingStopManager."""
        try:
            payload = dict(update_data or {})
            payload.setdefault("updated_at", datetime.utcnow().isoformat())
            response = await self.http_client.put(
                f"{self.database_url}/api/v1/trades/{trade_id}",
                json=payload,
            )
            response.raise_for_status()
            logger.info(f"✅ Updated trade {trade_id} with fields: {list(payload.keys())}")
            return True
        except Exception as e:
            logger.error(f"❌ Error updating trade {trade_id}: {e}")
            return False
    
    async def close_trade(self, trade_id: str, exit_price: float, exit_time: datetime, 
                         realized_pnl: float = None, exit_reason: str = "orchestrator_closure", 
                         exit_order_id: str = None, fees: float = 0.0, 
                         validated_by_exchange: bool = False) -> bool:
        """Close a trade using centralized trade closure service - REFACTORED"""
        try:
            # Prepare data for centralized closure API
            closure_data = {
                "exit_price": exit_price,
                "exit_reason": exit_reason,
                "exit_time": exit_time.isoformat() if isinstance(exit_time, datetime) else exit_time,
                "fees": fees,
                "validated_by_exchange": validated_by_exchange
            }
            
            # Add exit_order_id if provided
            if exit_order_id:
                closure_data["exit_order_id"] = exit_order_id
            
            # Use new centralized closure API endpoint
            response = await self.http_client.post(
                f"{self.database_url}/api/v1/trades/{trade_id}/close",
                json=closure_data
            )
            response.raise_for_status()
            
            # Get the response data
            result = response.json()
            if result.get('success'):
                logger.info(f"✅ CENTRALIZED CLOSURE: Trade {trade_id} - "
                           f"Price: ${result['exit_price']:.4f}, "
                           f"PnL: ${result['realized_pnl']:.2f} ({result['pnl_percentage']:.2f}%)")
                return True
            else:
                logger.error(f"❌ Centralized closure failed for {trade_id}: {result.get('message', 'unknown error')}")
                return False
            
        except Exception as e:
            logger.error(f"❌ Error in centralized trade closure {trade_id}: {e}")
            # Fallback to old method if centralized service fails
            try:
                logger.warning(f"🔄 Falling back to direct update for trade {trade_id}")
                update_data = {
                    "status": "CLOSED",
                    "exit_price": exit_price,
                    "exit_time": exit_time.isoformat() if isinstance(exit_time, datetime) else exit_time,
                    "realized_pnl": realized_pnl if realized_pnl is not None else 0.0,
                    "exit_reason": f"{exit_reason}_fallback",
                    "updated_at": datetime.utcnow().isoformat()
                }
                
                response = await self.http_client.put(
                    f"{self.database_url}/api/v1/trades/{trade_id}",
                    json=update_data
                )
                response.raise_for_status()
                
                logger.info(f"✅ Fallback closure: Trade {trade_id} - Price: ${exit_price:.4f}")
                return True
                
            except Exception as fallback_error:
                logger.error(f"❌ Both centralized and fallback closure failed for {trade_id}: {fallback_error}")
                return False
    
    # Exchange Service Integration Methods
    
    async def get_ticker_live(self, exchange: str, symbol: str) -> Optional[Dict[str, Any]]:
        """Get real-time ticker data from WebSocket price feed (enhanced)"""
        try:
            # Use enhanced WebSocket price feed
            price = await self.price_feed.get_current_price(exchange, symbol)
            
            if price:
                return {
                    'symbol': symbol,
                    'last': price,
                    'timestamp': datetime.utcnow().isoformat(),
                    'source': 'websocket_enhanced',
                    'stale': False
                }
            
            # Fallback to direct REST call if price feed fails
            return await self.get_ticker(exchange, symbol)
            
        except Exception as e:
            logger.debug(f"Enhanced WebSocket ticker failed for {exchange} {symbol}: {e}")
            return await self.get_ticker(exchange, symbol)
    
    async def get_ticker(self, exchange: str, symbol: str) -> Optional[Dict[str, Any]]:
        """Get ticker data — uses the hardened price chain (WS cache → Redis →
        REST) from WebSocketPriceFeed instead of a raw REST call. This avoids
        the per-2s ERROR spam when a pair isn't listed on the exchange (404)
        because that case is now WARNING-once + suppressed in the price feed.
        """
        try:
            price = await self.price_feed.get_current_price(exchange, symbol)
            if price and price > 0:
                return {
                    'symbol': symbol,
                    'last': price,
                    'timestamp': datetime.utcnow().isoformat(),
                    'source': 'price_feed_chain',
                    'stale': False,
                }
            return None
        except Exception as e:
            logger.warning(f"⚠️ get_ticker fallback failed for {exchange} {symbol}: {e}")
            return None
    
    async def get_balance(self, exchange: str) -> Dict[str, Any]:
        """Fetch account balance for an exchange.

        FIX (trailing-stop balance check): ``TrailingStopManager`` calls
        ``self.exchange_service.get_balance(...)`` to size the limit-sell to
        the actually-available base asset (preventing over-sells). Previously
        this method did not exist on ``OrderLifecycleManager``, raising
        ``AttributeError`` and forcing fallback to the theoretical
        ``position_size`` (which can fail on Binance with ``insufficient
        balance``).

        Calls ``GET /api/v1/account/balance/{exchange}`` on exchange-service
        and returns the wrapped shape ``{success, data}`` that
        ``trailing_stop_manager._create_limit_sell_order`` expects.
        """
        try:
            response = await self.http_client.get(
                f"{self.exchange_url}/api/v1/account/balance/{exchange}"
            )
            response.raise_for_status()
            return {"success": True, "data": response.json()}
        except httpx.HTTPStatusError as e:
            logger.warning(
                f"⚠️ get_balance HTTP {e.response.status_code} for {exchange}: {e.response.text[:200]}"
            )
            return {"success": False, "error": f"HTTP {e.response.status_code}"}
        except Exception as e:
            logger.error(f"❌ Error fetching balance for {exchange}: {e}")
            return {"success": False, "error": str(e)}

    async def create_limit_sell_order(self, exchange: str, symbol: str, 
                                    quantity: float, price: float) -> Optional[Dict[str, Any]]:
        """Create a limit sell order on the exchange.

        FIX (trailing-stop 404): exchange-service exposes the create-order
        endpoint at ``POST /api/v1/trading/order`` with the exchange in the
        body (``OrderRequest`` model: ``exchange, symbol, order_type, side,
        amount, price``). The previous URL ``/api/v1/orders/{exchange}`` and
        the body keys ``type``/``amount`` (with ``exchange`` missing entirely)
        produced 404 on every trailing-stop activation, leaving in-profit
        positions unprotected on the exchange.
        """
        try:
            order_data = {
                "exchange": exchange,
                "symbol": symbol,
                "order_type": "limit",  # exchange-service expects ``order_type`` (not ``type``)
                "side": "sell",
                "amount": quantity,
                "price": price,
            }

            response = await self.http_client.post(
                f"{self.exchange_url}/api/v1/trading/order",
                json=order_data,
            )
            response.raise_for_status()

            order_result = response.json()

            # Extract order ID and normalize response. Some endpoints return
            # the ccxt order dict directly; others wrap it under ``order``.
            if isinstance(order_result, dict) and 'order' in order_result and isinstance(order_result['order'], dict):
                order_payload = order_result['order']
            else:
                order_payload = order_result

            order_id = (
                order_payload.get('id')
                or order_payload.get('order_id')
                or (order_result.get('id') if isinstance(order_result, dict) else None)
            )
            
            if order_id:
                logger.info(f"📋 Created limit sell order on {exchange}: {order_id} - {quantity} {symbol} @ ${price:.4f}")
                return {
                    'success': True,
                    'order_id': order_id,
                    'price': price,
                    'quantity': quantity,
                    'raw_response': order_result
                }
            else:
                logger.error(f"❌ No order ID in response: {order_result}")
                return {'success': False, 'error': 'No order ID returned'}
            
        except Exception as e:
            logger.error(f"❌ Error creating limit sell order on {exchange}: {e}")
            return {'success': False, 'error': str(e)}
    
    async def cancel_order(self, exchange: str, order_id: str,
                           symbol: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Cancel an existing order.

        FIX (trailing-stop 404): correct URL is
        ``DELETE /api/v1/trading/order/{exchange}/{order_id}``. Some exchanges
        (e.g. Crypto.com) require ``symbol`` to cancel — pass it as a query
        param when known.
        """
        try:
            params = {"symbol": symbol} if symbol else None
            response = await self.http_client.delete(
                f"{self.exchange_url}/api/v1/trading/order/{exchange}/{order_id}",
                params=params,
            )
            response.raise_for_status()
            
            cancel_result = response.json()
            
            logger.info(f"🗑️ Cancelled order {order_id} on {exchange}")
            return {
                'success': True,
                'order_id': order_id,
                'raw_response': cancel_result
            }
            
        except Exception as e:
            logger.error(f"❌ Error cancelling order {order_id} on {exchange}: {e}")
            return {'success': False, 'error': str(e)}
    
    async def get_order_status(self, exchange: str, order_id: str,
                               symbol: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Get current status of an order.

        FIX (trailing-stop 404): correct URL is
        ``GET /api/v1/trading/order/{exchange}/{order_id}`` (no ``/status``
        suffix); the response is wrapped under ``{"order": <ccxt_order>}``
        so we unwrap before normalising.
        """
        try:
            params = {"symbol": symbol} if symbol else None
            response = await self.http_client_fast.get(
                f"{self.exchange_url}/api/v1/trading/order/{exchange}/{order_id}",
                params=params,
            )
            response.raise_for_status()

            payload = response.json()
            status_data = payload.get('order', payload) if isinstance(payload, dict) else {}
            
            # Normalize status across exchanges
            status = status_data.get('status', '').lower()
            
            # Map exchange-specific statuses to normalized values
            if status in ['filled', 'closed']:
                normalized_state = 'FILLED'
                fill_price = float(status_data.get('average', 0) or status_data.get('price', 0))
                fill_time = status_data.get('timestamp') or status_data.get('datetime')
                
                if not fill_time:
                    fill_time = datetime.utcnow().isoformat()
                
                return {
                    'state': normalized_state,
                    'fill_price': fill_price,
                    'fill_time': fill_time,
                    'raw_response': status_data
                }
            
            elif status in ['open', 'new', 'pending']:
                return {
                    'state': 'OPEN',
                    'raw_response': status_data
                }
            
            elif status in ['cancelled', 'canceled']:
                return {
                    'state': 'CANCELLED',
                    'raw_response': status_data
                }
            
            else:
                logger.warning(f"⚠️ Unknown order status '{status}' for {order_id}")
                return {
                    'state': 'UNKNOWN',
                    'raw_response': status_data
                }
            
        except Exception as e:
            logger.error(f"❌ Error getting order status for {order_id} on {exchange}: {e}")
            return None
    
    # Price update handling
    
    async def _handle_price_update(self, price_update: PriceUpdate):
        """Handle price updates from WebSocket feed"""
        try:
            # This callback is used by the trailing stop manager for real-time price monitoring
            # The price feed automatically manages subscriptions for active trades
            logger.debug(f"📈 Price update: {price_update.exchange} {price_update.symbol} = ${price_update.price:.4f}")
            
            # The trailing stop manager will process this through its normal monitoring cycle
            # This provides the real-time price data needed for dashboard updates too
            
        except Exception as e:
            logger.error(f"❌ Error handling price update: {e}")
    
    async def subscribe_trade_price_monitoring(self, trade_id: str, exchange: str, symbol: str):
        """Subscribe to real-time price monitoring for a trade"""
        try:
            await self.price_feed.subscribe_to_trade(trade_id, exchange, symbol)
            logger.info(f"📡 Subscribed to price monitoring: {trade_id} - {exchange} {symbol}")
            
        except Exception as e:
            logger.error(f"❌ Error subscribing to price monitoring for trade {trade_id}: {e}")
    
    async def unsubscribe_trade_price_monitoring(self, trade_id: str):
        """Unsubscribe from price monitoring when trade completes"""
        try:
            await self.price_feed.unsubscribe_from_trade(trade_id)
            logger.info(f"🚫 Unsubscribed from price monitoring: {trade_id}")
            
        except Exception as e:
            logger.error(f"❌ Error unsubscribing from price monitoring for trade {trade_id}: {e}")
    
    def add_dashboard_price_callback(self, callback):
        """Add callback for dashboard price updates"""
        self.price_feed.add_dashboard_callback(callback)
        logger.info("📈 Added dashboard price callback")
    
    # Public interface methods for TrailingStopManager
    
    def get_trailing_manager(self) -> TrailingStopManager:
        """Get the underlying trailing stop manager"""
        return self.trailing_manager
    
    def get_active_stops_count(self) -> int:
        """Get number of active trailing stops"""
        return self.trailing_manager.get_active_stops_count()
    
    def get_stop_data(self, trade_id: str) -> Optional[TrailingStopData]:
        """Get trailing stop data for a specific trade"""
        return self.trailing_manager.get_stop_data(trade_id)
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get comprehensive statistics"""
        base_stats = self.trailing_manager.get_statistics()
        price_feed_stats = self.price_feed.get_status()
        
        # Add integration-specific statistics
        integration_stats = {
            'database_url': self.database_url,
            'exchange_url': self.exchange_url,
            'integration_active': True,
            'websocket_price_feed': price_feed_stats
        }
        
        return {**base_stats, 'integration': integration_stats}
    
    async def force_cleanup_trade(self, trade_id: str) -> bool:
        """Force cleanup of a specific trade (emergency use)"""
        return await self.trailing_manager.force_cleanup_trade(trade_id)
    
    def __repr__(self):
        """String representation"""
        active_count = self.get_active_stops_count()
        return f"OrderLifecycleManager(active_stops={active_count}, price_feed_status={self.price_feed.get_status()['status']}, database={self.database_url}, exchange={self.exchange_url})"


# Factory function for easy initialization
async def create_order_lifecycle_manager(config: Dict[str, Any]) -> OrderLifecycleManager:
    """
    Factory function to create and initialize OrderLifecycleManager
    
    Args:
        config: Configuration dictionary
        
    Returns:
        Initialized OrderLifecycleManager instance
    """
    
    manager = OrderLifecycleManager(config)
    
    # Perform any additional async initialization here if needed
    logger.info("🏗️ OrderLifecycleManager created and ready")
    
    return manager


# Integration helper functions
def create_order_lifecycle_integration(config: Dict[str, Any]) -> OrderLifecycleManager:
    """Create order lifecycle integration (alias for compatibility)"""
    return create_trailing_stop_integration(config)

def create_trailing_stop_integration(config: Dict[str, Any]) -> OrderLifecycleManager:
    """
    Create a trailing stop integration with the existing system
    
    This is the main entry point for integrating trailing stops
    into the existing trading bot architecture.
    
    Args:
        config: Configuration dictionary from config.yaml
        
    Returns:
        OrderLifecycleManager ready for integration
        
    Example:
        # In orchestrator or main service
        from strategy.order_lifecycle_manager import create_trailing_stop_integration
        
        trailing_integration = create_trailing_stop_integration(config)
        await trailing_integration.start()
    """
    
    return OrderLifecycleManager(config)