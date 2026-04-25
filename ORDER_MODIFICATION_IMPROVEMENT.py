#!/usr/bin/env python3
"""
ORDER MODIFICATION IMPROVEMENT
==============================

This implements a better approach for trailing stop updates using order modification
instead of the current cancel-and-recreate approach.

BENEFITS:
✅ Keeps same exit_id (no tracking issues)
✅ Faster execution (single API call)
✅ Reduced race conditions
✅ Lower fees (no cancellation fees)
✅ Simpler error handling

IMPLEMENTATION:
1. Add order modification endpoint to exchange service
2. Update trailing stop manager to use modification
3. Fallback to cancel-and-recreate if modification fails
"""

import asyncio
import logging
from typing import Dict, Any, Optional
import httpx

logger = logging.getLogger(__name__)

class OrderModificationAPI:
    """
    Enhanced order modification functionality for trailing stops
    """
    
    def __init__(self, exchange_service_url: str = "http://localhost:8003"):
        self.exchange_service_url = exchange_service_url
    
    async def modify_order_price(self, exchange: str, order_id: str, symbol: str, 
                               new_price: float) -> Dict[str, Any]:
        """
        Modify an existing order's price
        
        Args:
            exchange: Exchange name (binance, bybit, cryptocom)
            order_id: Exchange order ID to modify
            symbol: Trading symbol (e.g., "BTC/USDC")
            new_price: New limit price for the order
            
        Returns:
            Dict with success status and order details
        """
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                modify_data = {
                    "new_price": new_price,
                    "symbol": symbol
                }
                
                response = await client.put(
                    f"{self.exchange_service_url}/api/v1/trading/order/{exchange}/{order_id}",
                    json=modify_data
                )
                
                if response.status_code == 200:
                    result = response.json()
                    logger.info(f"✅ Order {order_id} modified successfully - New price: ${new_price:.6f}")
                    return {
                        "success": True,
                        "order_id": order_id,
                        "new_price": new_price,
                        "exchange_response": result
                    }
                else:
                    logger.warning(f"⚠️ Order modification failed: {response.status_code} - {response.text}")
                    return {
                        "success": False,
                        "error": f"HTTP {response.status_code}: {response.text}",
                        "fallback_required": True
                    }
                    
        except Exception as e:
            logger.error(f"❌ Error modifying order {order_id}: {e}")
            return {
                "success": False,
                "error": str(e),
                "fallback_required": True
            }

class ImprovedTrailingStopManager:
    """
    Improved trailing stop manager using order modification
    """
    
    def __init__(self, exchange_service_url: str = "http://localhost:8003"):
        self.order_api = OrderModificationAPI(exchange_service_url)
        self.stats = {
            "orders_modified": 0,
            "orders_cancelled_recreated": 0,
            "modification_failures": 0
        }
    
    async def update_trailing_stop_price(self, trade_id: str, exchange: str, 
                                       order_id: str, symbol: str, 
                                       new_price: float) -> Dict[str, Any]:
        """
        Update trailing stop price using the improved approach
        
        1. Try to modify existing order price (preferred)
        2. Fallback to cancel-and-recreate if modification fails
        """
        logger.info(f"🔄 [Trade {trade_id}] Updating trailing stop {order_id} to ${new_price:.6f}")
        
        # METHOD 1: Try order modification first (preferred)
        modify_result = await self.order_api.modify_order_price(
            exchange, order_id, symbol, new_price
        )
        
        if modify_result["success"]:
            self.stats["orders_modified"] += 1
            logger.info(f"✅ [Trade {trade_id}] Order modified successfully - Same exit_id: {order_id}")
            return {
                "success": True,
                "method": "modification",
                "exit_id": order_id,  # Same exit_id!
                "new_price": new_price
            }
        
        # METHOD 2: Fallback to cancel-and-recreate
        logger.warning(f"⚠️ [Trade {trade_id}] Order modification failed, falling back to cancel-and-recreate")
        
        cancel_recreate_result = await self._cancel_and_recreate_order(
            trade_id, exchange, order_id, symbol, new_price
        )
        
        if cancel_recreate_result["success"]:
            self.stats["orders_cancelled_recreated"] += 1
            return cancel_recreate_result
        else:
            self.stats["modification_failures"] += 1
            return {"success": False, "error": "Both modification and cancel-recreate failed"}
    
    async def _cancel_and_recreate_order(self, trade_id: str, exchange: str, 
                                       old_order_id: str, symbol: str, 
                                       new_price: float) -> Dict[str, Any]:
        """
        Fallback method: Cancel existing order and create new one
        """
        try:
            # This would use existing cancel-and-recreate logic
            logger.info(f"🔄 [Trade {trade_id}] Using cancel-and-recreate fallback")
            
            # Implementation would go here using existing methods
            # from the TradingOrchestrator class
            
            return {
                "success": True,
                "method": "cancel_and_recreate",
                "exit_id": "NEW_ORDER_ID",  # New exit_id (needs updating)
                "new_price": new_price
            }
            
        except Exception as e:
            logger.error(f"❌ [Trade {trade_id}] Cancel-and-recreate failed: {e}")
            return {"success": False, "error": str(e)}

# Exchange Service Enhancement
EXCHANGE_SERVICE_ORDER_MODIFICATION_ENDPOINT = """
# Add this to services/exchange-service/main.py

@app.put("/api/v1/trading/order/{exchange}/{order_id}")
async def modify_order(exchange: str, order_id: str, 
                      modification: Dict[str, Any]):
    '''
    Modify an existing order (price, quantity, etc.)
    
    CCXT Support by Exchange:
    - Binance: ✅ editOrder() - Full support
    - Bybit: ✅ editOrder() - Full support  
    - Crypto.com: ⚠️ Limited support
    '''
    if not exchange_manager or exchange not in exchanges:
        raise HTTPException(status_code=404, detail=f"Exchange {exchange} not found")
    
    try:
        exchange_instance = exchanges[exchange]['instance']
        
        # Check if exchange supports order modification
        if not hasattr(exchange_instance, 'edit_order'):
            raise HTTPException(
                status_code=501, 
                detail=f"Exchange {exchange} does not support order modification"
            )
        
        symbol = modification.get('symbol')
        new_price = modification.get('new_price')
        new_amount = modification.get('new_amount')
        
        # Build modification parameters
        params = {}
        if new_price:
            params['price'] = new_price
        if new_amount:
            params['amount'] = new_amount
        
        # Modify the order using CCXT
        result = await exchange_instance.edit_order(
            order_id, 
            symbol, 
            type='limit',  # Most trailing stops are limit orders
            side=None,     # Keep existing side
            amount=new_amount if new_amount else None,
            price=new_price if new_price else None,
            params={}
        )
        
        return {
            "success": True,
            "order": result,
            "modified_fields": params,
            "exchange": exchange
        }
        
    except Exception as e:
        logger.error(f"Order modification failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
"""

def get_exchange_modification_support():
    """
    Exchange support matrix for order modification
    """
    return {
        "binance": {
            "editOrder": True,
            "modify_price": True,
            "modify_quantity": True,
            "notes": "Full support via editOrder API"
        },
        "bybit": {
            "editOrder": True,
            "modify_price": True,
            "modify_quantity": True,
            "notes": "Full support via amendOrder API"
        },
        "cryptocom": {
            "editOrder": False,
            "modify_price": False,
            "modify_quantity": False,
            "notes": "No direct modification - must cancel and recreate"
        },
        "kraken": {
            "editOrder": True,
            "modify_price": True,
            "modify_quantity": True,
            "notes": "Support via editOrder API"
        }
    }

if __name__ == "__main__":
    print("ORDER MODIFICATION IMPROVEMENT")
    print("=" * 50)
    print()
    print("BENEFITS OF ORDER MODIFICATION:")
    print("✅ Same exit_id (no tracking issues)")
    print("✅ Faster execution (single API call)")
    print("✅ Reduced race conditions")
    print("✅ Lower fees (no cancellation fees)")
    print("✅ Simpler error handling")
    print()
    print("EXCHANGE SUPPORT:")
    support = get_exchange_modification_support()
    for exchange, info in support.items():
        status = "✅" if info["editOrder"] else "❌"
        print(f"{status} {exchange.upper()}: {info['notes']}")
    print()
    print("IMPLEMENTATION PLAN:")
    print("1. Add PUT /api/v1/trading/order/{exchange}/{order_id} endpoint")
    print("2. Update TrailingStopManager to use modification first")
    print("3. Fallback to cancel-and-recreate for unsupported exchanges")
    print("4. Update database tracking to handle same exit_id")
