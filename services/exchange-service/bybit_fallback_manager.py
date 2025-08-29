"""
Bybit Fallback Manager - Version 2.6.0
Manages fallback to REST API when Bybit WebSocket is unavailable
"""

import asyncio
import logging
import time
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
import httpx

logger = logging.getLogger(__name__)

class BybitFallbackManager:
    """
    Manages fallback to Bybit REST API when WebSocket is unavailable
    
    Features:
    - REST API polling for order status
    - Execution history retrieval
    - Position reconciliation
    - Automatic recovery when WebSocket restored
    - Rate limiting and error handling
    """
    
    def __init__(self, api_key: str, api_secret: str, base_url: str = "https://api.bybit.com"):
        """
        Initialize Bybit fallback manager
        
        Args:
            api_key: Bybit API key
            api_secret: Bybit API secret
            base_url: Bybit API base URL
        """
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url
        
        # Fallback state
        self.fallback_active = False
        self.last_poll_time = None
        self.poll_interval = 30  # seconds
        self.max_poll_attempts = 3
        
        # Rate limiting
        self.rate_limit_delay = 1  # seconds
        self.last_request_time = 0
        
        # Metrics
        self.metrics = {
            "fallback_activations": 0,
            "rest_api_calls": 0,
            "orders_polled": 0,
            "executions_retrieved": 0,
            "positions_reconciled": 0,
            "polling_errors": 0,
            "last_fallback_time": None
        }
        
        logger.info("🔄 Bybit Fallback Manager initialized")
    
    async def activate_fallback(self) -> bool:
        """
        Activate REST API fallback mode
        
        Returns:
            True if activated successfully, False otherwise
        """
        try:
            self.fallback_active = True
            self.metrics["fallback_activations"] += 1
            self.metrics["last_fallback_time"] = datetime.now(timezone.utc).isoformat()
            
            logger.warning("🔄 Bybit WebSocket unavailable - activating REST API fallback")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to activate fallback: {e}")
            return False
    
    async def deactivate_fallback(self) -> bool:
        """
        Deactivate REST API fallback mode
        
        Returns:
            True if deactivated successfully, False otherwise
        """
        try:
            self.fallback_active = False
            logger.info("✅ Bybit WebSocket restored - deactivating REST API fallback")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to deactivate fallback: {e}")
            return False
    
    async def poll_orders(self, symbol: str = None) -> List[Dict[str, Any]]:
        """
        Poll orders via REST API
        
        Args:
            symbol: Optional symbol to filter orders
            
        Returns:
            List of order data
        """
        try:
            await self._rate_limit()
            
            # Build API endpoint
            endpoint = "/v5/order/realtime"
            params = {"category": "spot"}
            if symbol:
                params["symbol"] = symbol
            
            # Make API request
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}{endpoint}",
                    params=params,
                    headers=self._get_auth_headers("GET", endpoint, params),
                    timeout=10.0
                )
                
                if response.status_code == 200:
                    data = response.json()
                    orders = data.get("result", {}).get("list", [])
                    
                    self.metrics["rest_api_calls"] += 1
                    self.metrics["orders_polled"] += len(orders)
                    
                    logger.info(f"📋 Polled {len(orders)} orders from Bybit REST API")
                    return orders
                else:
                    logger.error(f"❌ REST API error: {response.status_code} - {response.text}")
                    self.metrics["polling_errors"] += 1
                    return []
                    
        except Exception as e:
            logger.error(f"❌ Error polling orders: {e}")
            self.metrics["polling_errors"] += 1
            return []
    
    async def get_executions(self, symbol: str = None, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Get recent executions via REST API
        
        Args:
            symbol: Optional symbol to filter executions
            limit: Number of executions to retrieve
            
        Returns:
            List of execution data
        """
        try:
            await self._rate_limit()
            
            # Build API endpoint
            endpoint = "/v5/execution/list"
            params = {"category": "spot", "limit": limit}
            if symbol:
                params["symbol"] = symbol
            
            # Make API request
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}{endpoint}",
                    params=params,
                    headers=self._get_auth_headers("GET", endpoint, params),
                    timeout=10.0
                )
                
                if response.status_code == 200:
                    data = response.json()
                    executions = data.get("result", {}).get("list", [])
                    
                    self.metrics["rest_api_calls"] += 1
                    self.metrics["executions_retrieved"] += len(executions)
                    
                    logger.info(f"⚡ Retrieved {len(executions)} executions from Bybit REST API")
                    return executions
                else:
                    logger.error(f"❌ REST API error: {response.status_code} - {response.text}")
                    self.metrics["polling_errors"] += 1
                    return []
                    
        except Exception as e:
            logger.error(f"❌ Error getting executions: {e}")
            self.metrics["polling_errors"] += 1
            return []
    
    async def get_positions(self, symbol: str = None) -> List[Dict[str, Any]]:
        """
        Get current positions via REST API
        
        Args:
            symbol: Optional symbol to filter positions
            
        Returns:
            List of position data
        """
        try:
            await self._rate_limit()
            
            # Build API endpoint
            endpoint = "/v5/position/list"
            params = {"category": "spot"}
            if symbol:
                params["symbol"] = symbol
            
            # Make API request
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}{endpoint}",
                    params=params,
                    headers=self._get_auth_headers("GET", endpoint, params),
                    timeout=10.0
                )
                
                if response.status_code == 200:
                    data = response.json()
                    positions = data.get("result", {}).get("list", [])
                    
                    self.metrics["rest_api_calls"] += 1
                    self.metrics["positions_reconciled"] += len(positions)
                    
                    logger.info(f"📈 Retrieved {len(positions)} positions from Bybit REST API")
                    return positions
                else:
                    logger.error(f"❌ REST API error: {response.status_code} - {response.text}")
                    self.metrics["polling_errors"] += 1
                    return []
                    
        except Exception as e:
            logger.error(f"❌ Error getting positions: {e}")
            self.metrics["polling_errors"] += 1
            return []
    
    async def get_wallet_balance(self, account_type: str = "UNIFIED") -> Dict[str, Any]:
        """
        Get wallet balance via REST API
        
        Args:
            account_type: Account type (UNIFIED, CONTRACT, SPOT)
            
        Returns:
            Wallet balance data
        """
        try:
            await self._rate_limit()
            
            # Build API endpoint
            endpoint = "/v5/account/wallet-balance"
            params = {"accountType": account_type}
            
            # Make API request
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}{endpoint}",
                    params=params,
                    headers=self._get_auth_headers("GET", endpoint, params),
                    timeout=10.0
                )
                
                if response.status_code == 200:
                    data = response.json()
                    balance = data.get("result", {})
                    
                    self.metrics["rest_api_calls"] += 1
                    
                    logger.info(f"💰 Retrieved wallet balance from Bybit REST API")
                    return balance
                else:
                    logger.error(f"❌ REST API error: {response.status_code} - {response.text}")
                    self.metrics["polling_errors"] += 1
                    return {}
                    
        except Exception as e:
            logger.error(f"❌ Error getting wallet balance: {e}")
            self.metrics["polling_errors"] += 1
            return {}
    
    async def process_order_via_rest(self, order_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process order data from REST API and convert to WebSocket event format
        
        Args:
            order_data: Order data from REST API
            
        Returns:
            Standardized event data
        """
        try:
            # Convert REST API response to WebSocket event format
            event_data = {
                "topic": "order",
                "type": "snapshot",
                "ts": int(time.time() * 1000),
                "data": [{
                    "orderId": order_data.get("orderId", ""),
                    "orderLinkId": order_data.get("orderLinkId", ""),
                    "symbol": order_data.get("symbol", ""),
                    "side": order_data.get("side", ""),
                    "orderType": order_data.get("orderType", ""),
                    "price": order_data.get("price", "0"),
                    "qty": order_data.get("qty", "0"),
                    "cumExecQty": order_data.get("cumExecQty", "0"),
                    "cumExecFee": order_data.get("cumExecFee", "0"),
                    "avgPrice": order_data.get("avgPrice", "0"),
                    "orderStatus": order_data.get("orderStatus", ""),
                    "lastExecPrice": order_data.get("lastExecPrice", "0"),
                    "lastExecQty": order_data.get("lastExecQty", "0"),
                    "execTime": order_data.get("execTime", "")
                }]
            }
            
            logger.debug(f"🔄 Converted REST order to WebSocket format: {order_data.get('orderId')}")
            return event_data
            
        except Exception as e:
            logger.error(f"❌ Error converting REST order to WebSocket format: {e}")
            return {}
    
    async def process_execution_via_rest(self, execution_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process execution data from REST API and convert to WebSocket event format
        
        Args:
            execution_data: Execution data from REST API
            
        Returns:
            Standardized event data
        """
        try:
            # Convert REST API response to WebSocket event format
            event_data = {
                "topic": "execution",
                "type": "snapshot",
                "ts": int(time.time() * 1000),
                "data": [{
                    "symbol": execution_data.get("symbol", ""),
                    "side": execution_data.get("side", ""),
                    "orderId": execution_data.get("orderId", ""),
                    "execId": execution_data.get("execId", ""),
                    "orderLinkId": execution_data.get("orderLinkId", ""),
                    "price": execution_data.get("price", "0"),
                    "qty": execution_data.get("qty", "0"),
                    "execFee": execution_data.get("execFee", "0"),
                    "execTime": execution_data.get("execTime", "")
                }]
            }
            
            logger.debug(f"🔄 Converted REST execution to WebSocket format: {execution_data.get('execId')}")
            return event_data
            
        except Exception as e:
            logger.error(f"❌ Error converting REST execution to WebSocket format: {e}")
            return {}
    
    async def _rate_limit(self):
        """Apply rate limiting for API requests"""
        current_time = time.time()
        time_since_last = current_time - self.last_request_time
        
        if time_since_last < self.rate_limit_delay:
            await asyncio.sleep(self.rate_limit_delay - time_since_last)
        
        self.last_request_time = time.time()
    
    def _get_auth_headers(self, method: str, endpoint: str, params: Dict[str, Any]) -> Dict[str, str]:
        """
        Generate authenticated headers for REST API requests
        
        Args:
            method: HTTP method
            endpoint: API endpoint
            params: Query parameters
            
        Returns:
            Headers with authentication
        """
        # This is a simplified version - in production, you'd implement proper HMAC-SHA256 signing
        return {
            "X-BAPI-API-KEY": self.api_key,
            "X-BAPI-TIMESTAMP": str(int(time.time() * 1000)),
            "Content-Type": "application/json"
        }
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get fallback manager metrics"""
        return self.metrics.copy()
    
    def reset_metrics(self):
        """Reset fallback manager metrics"""
        self.metrics = {
            "fallback_activations": 0,
            "rest_api_calls": 0,
            "orders_polled": 0,
            "executions_retrieved": 0,
            "positions_reconciled": 0,
            "polling_errors": 0,
            "last_fallback_time": None
        }
        logger.info("📊 Bybit Fallback Manager metrics reset")
    
    def is_fallback_active(self) -> bool:
        """Check if fallback mode is active"""
        return self.fallback_active
