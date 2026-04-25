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
        
        # Initialize trailing stop manager with this integration
        self.trailing_manager = TrailingStopManager(
            config=config,
            database_service=self,
            exchange_service=self
        )
        
        # Connect price feed to trailing manager
        self.price_feed.add_price_callback(self._handle_price_update)
        
        logger.info("🔗 OrderLifecycleManager initialized with service integrations")
    
    async def start(self):
        """Start the order lifecycle management system"""
        logger.info("🚀 Starting Order Lifecycle Management")
        
        # Start WebSocket price feed first
        await asyncio.create_task(self.price_feed.start())
        
        # Start the trailing stop manager
        await self.trailing_manager.start_monitoring()
    
    async def stop(self):
        """Stop the order lifecycle management system"""
        logger.info("🛑 Stopping Order Lifecycle Management")
        
        # Stop trailing stop manager
        await self.trailing_manager.stop_monitoring()
        
        # Stop WebSocket price feed
        await self.price_feed.stop()
        
        # Close HTTP clients
        await self.http_client.aclose()
        await self.http_client_fast.aclose()
    
    # Database Service Integration Methods
    
    async def get_open_trades_without_exit(self) -> List[Dict[str, Any]]:
        """Get all OPEN trades that don't have an exit_id (no active trailing stop)"""
        try:
            response = await self.http_client_fast.get(f"{self.database_url}/api/v1/trades/open")
            response.raise_for_status()
            
            trades = response.json()
            
            # Filter for trades without exit_id
            open_trades_no_exit = []
            for trade in trades:
                if trade.get('status') == 'OPEN' and not trade.get('exit_id'):
                    # Convert to object with attributes for easier access
                    trade_obj = type('Trade', (), trade)
                    trade_obj.id = trade['trade_id']  # Map trade_id to id for compatibility
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
    
    async def close_trade(self, trade_id: str, exit_price: float, exit_time: datetime, 
                         realized_pnl: float, exit_reason: str) -> bool:
        """Close a trade with final details"""
        try:
            update_data = {
                "status": "CLOSED",
                "exit_price": exit_price,
                "exit_time": exit_time.isoformat() if isinstance(exit_time, datetime) else exit_time,
                "realized_pnl": realized_pnl,
                "exit_reason": exit_reason,
                "updated_at": datetime.utcnow().isoformat()
            }
            
            response = await self.http_client.put(
                f"{self.database_url}/api/v1/trades/{trade_id}",
                json=update_data
            )
            response.raise_for_status()
            
            logger.info(f"✅ Closed trade {trade_id} - Price: ${exit_price:.4f}, PnL: ${realized_pnl:.2f}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Error closing trade {trade_id}: {e}")
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
        """Get ticker data from REST API (fallback)"""
        try:
            response = await self.http_client_fast.get(
                f"{self.exchange_url}/api/v1/market/ticker/{exchange}/{symbol}"
            )
            response.raise_for_status()
            
            ticker_data = response.json()
            logger.debug(f"📈 REST price for {exchange} {symbol}: ${ticker_data.get('last', 0):.4f}")
            return ticker_data
            
        except Exception as e:
            logger.error(f"❌ Error getting ticker for {exchange} {symbol}: {e}")
            return None
    
    async def create_limit_sell_order(self, exchange: str, symbol: str, 
                                    quantity: float, price: float) -> Optional[Dict[str, Any]]:
        """Create a limit sell order on the exchange"""
        try:
            order_data = {
                "symbol": symbol,
                "side": "sell",
                "type": "limit",
                "amount": quantity,
                "price": price
            }
            
            response = await self.http_client.post(
                f"{self.exchange_url}/api/v1/orders/{exchange}",
                json=order_data
            )
            response.raise_for_status()
            
            order_result = response.json()
            
            # Extract order ID and normalize response
            order_id = order_result.get('id') or order_result.get('order_id')
            
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
    
    async def cancel_order(self, exchange: str, order_id: str) -> Optional[Dict[str, Any]]:
        """Cancel an existing order"""
        try:
            response = await self.http_client.delete(
                f"{self.exchange_url}/api/v1/orders/{exchange}/{order_id}"
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
    
    async def get_order_status(self, exchange: str, order_id: str) -> Optional[Dict[str, Any]]:
        """Get current status of an order"""
        try:
            response = await self.http_client_fast.get(
                f"{self.exchange_url}/api/v1/orders/{exchange}/{order_id}/status"
            )
            response.raise_for_status()
            
            status_data = response.json()
            
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