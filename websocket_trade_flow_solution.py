#!/usr/bin/env python3
"""
WEBSOCKET-BASED TRADE CREATION FLOW SOLUTION

Implements the correct flow using WebSocket for real-time order tracking:
1. Place limit order → Store as PENDING
2. WebSocket monitors order fills in real-time 
3. ONLY create OPEN trade when WebSocket confirms fill
4. Much faster than polling APIs

This fixes the fundamental issue where trades are created before orders are filled.
"""
import asyncio
import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional

# This would be the improved architecture
WEBSOCKET_FLOW_ARCHITECTURE = """
🔄 WEBSOCKET-BASED TRADE CREATION FLOW
=======================================

PHASE 1: ORDER PLACEMENT
------------------------
[Orchestrator] → Place limit order on exchange
              → Get exchange_order_id
              → Store in PENDING_ORDERS table (NOT trades)
              → Subscribe to WebSocket for this order_id

PHASE 2: REAL-TIME MONITORING  
-----------------------------
[WebSocket] → Listens for order updates in real-time
           → Receives fill notifications instantly
           → Much faster than API polling
           → Handles partial fills correctly

PHASE 3: TRADE CREATION
-----------------------
[WebSocket Handler] → Order filled event received
                   → Validate fill data
                   → Create OPEN trade in trades table
                   → Remove from PENDING_ORDERS table
                   → Notify all services

PHASE 4: EXIT MONITORING
------------------------
[Strategy] → Determines exit signal
          → WebSocket monitors exit order same way
          → Close trade when exit confirmed filled
"""

class WebSocketTradeFlowManager:
    """
    Manages the correct trade creation flow using WebSocket monitoring
    """
    
    def __init__(self):
        self.pending_orders: Dict[str, Dict] = {}
        self.websocket_subscriptions: Dict[str, str] = {}
        
    async def place_order_and_track(self, order_data: Dict[str, Any]) -> Optional[str]:
        """
        Phase 1: Place order and set up WebSocket tracking
        Returns pending_order_id for tracking
        """
        print(f"📤 PHASE 1: Placing order for {order_data['pair']} on {order_data['exchange']}")
        
        # 1. Place limit order on exchange
        exchange_order = await self._place_limit_order(order_data)
        if not exchange_order or not exchange_order.get('id'):
            print(f"❌ Failed to place order")
            return None
            
        exchange_order_id = exchange_order['id']
        
        # 2. Store in PENDING_ORDERS table (NOT trades table)
        pending_order = {
            'order_id': str(uuid.uuid4()),
            'exchange_order_id': exchange_order_id,
            'exchange': order_data['exchange'],
            'pair': order_data['pair'], 
            'side': 'buy',
            'amount': order_data['amount'],
            'price': order_data['price'],
            'strategy': order_data.get('strategy'),
            'entry_reason': order_data.get('reason'),
            'status': 'pending',
            'created_at': datetime.utcnow().isoformat()
        }
        
        await self._store_pending_order(pending_order)
        
        # 3. Subscribe to WebSocket for this specific order
        await self._subscribe_to_order_updates(exchange_order_id, order_data['exchange'])
        
        print(f"✅ Order {exchange_order_id} placed and WebSocket tracking started")
        return pending_order['order_id']
    
    async def handle_websocket_fill_event(self, fill_event: Dict[str, Any]):
        """
        Phase 2 & 3: Handle WebSocket fill notification
        ONLY create trade when WebSocket confirms fill
        """
        exchange_order_id = fill_event.get('exchange_order_id')
        if not exchange_order_id:
            return
            
        print(f"⚡ WEBSOCKET FILL EVENT: {exchange_order_id}")
        
        # Get pending order details
        pending_order = await self._get_pending_order(exchange_order_id)
        if not pending_order:
            print(f"⚠️ No pending order found for {exchange_order_id}")
            return
            
        # Validate fill data
        fill_price = fill_event.get('price', 0)
        fill_amount = fill_event.get('filled_amount', 0)
        fill_status = fill_event.get('status', '').lower()
        
        if fill_status != 'filled' or fill_amount <= 0:
            print(f"📊 Partial/incomplete fill: status={fill_status}, amount={fill_amount}")
            await self._update_pending_order_status(exchange_order_id, 'partially_filled')
            return
            
        print(f"✅ CONFIRMED FILL: {fill_amount} @ ${fill_price}")
        
        # NOW create the OPEN trade (only when actually filled)
        trade_data = {
            'trade_id': str(uuid.uuid4()),
            'pair': pending_order['pair'],
            'exchange': pending_order['exchange'],
            'entry_price': fill_price,
            'position_size': fill_amount,
            'entry_id': exchange_order_id,
            'status': 'OPEN',  # NOW it's safe to mark as OPEN
            'strategy': pending_order['strategy'],
            'entry_reason': pending_order['entry_reason'],
            'entry_time': datetime.utcnow().isoformat(),
            'entry_fee_amount': fill_event.get('fee_amount', 0),
            'entry_fee_currency': fill_event.get('fee_currency'),
        }
        
        success = await self._create_open_trade(trade_data)
        if success:
            # Clean up pending order
            await self._remove_pending_order(exchange_order_id)
            print(f"🎯 TRADE CREATED: {trade_data['trade_id'][:8]} - {trade_data['pair']}")
        else:
            print(f"❌ Failed to create trade for filled order {exchange_order_id}")
    
    async def _place_limit_order(self, order_data: Dict) -> Optional[Dict]:
        """Place limit order on exchange"""
        # This would call the exchange service
        print(f"   Placing {order_data['amount']} {order_data['pair']} @ ${order_data['price']}")
        return {'id': f"mock_order_{datetime.now().timestamp()}", 'status': 'open'}
    
    async def _store_pending_order(self, pending_order: Dict):
        """Store order in PENDING_ORDERS table"""
        print(f"   Stored in PENDING_ORDERS: {pending_order['exchange_order_id']}")
        self.pending_orders[pending_order['exchange_order_id']] = pending_order
    
    async def _subscribe_to_order_updates(self, order_id: str, exchange: str):
        """Subscribe to WebSocket updates for specific order"""
        print(f"   WebSocket subscription: {exchange} order {order_id}")
        self.websocket_subscriptions[order_id] = exchange
    
    async def _get_pending_order(self, exchange_order_id: str) -> Optional[Dict]:
        """Get pending order by exchange_order_id"""
        return self.pending_orders.get(exchange_order_id)
    
    async def _update_pending_order_status(self, exchange_order_id: str, status: str):
        """Update pending order status"""
        if exchange_order_id in self.pending_orders:
            self.pending_orders[exchange_order_id]['status'] = status
            print(f"   Updated pending order status: {status}")
    
    async def _create_open_trade(self, trade_data: Dict) -> bool:
        """Create OPEN trade in trades table"""
        print(f"   Creating OPEN trade: {trade_data['trade_id'][:8]}")
        return True  # Would call database service
    
    async def _remove_pending_order(self, exchange_order_id: str):
        """Remove order from PENDING_ORDERS table"""
        if exchange_order_id in self.pending_orders:
            del self.pending_orders[exchange_order_id]
        if exchange_order_id in self.websocket_subscriptions:
            del self.websocket_subscriptions[exchange_order_id]
        print(f"   Cleaned up pending order: {exchange_order_id}")

def demonstrate_flow():
    """Demonstrate the WebSocket-based flow"""
    print("🚀 WEBSOCKET-BASED TRADE FLOW DEMONSTRATION")
    print("=" * 60)
    print(WEBSOCKET_FLOW_ARCHITECTURE)
    print()
    print("🧪 SIMULATION:")
    print("-" * 20)
    
    # Example flow
    manager = WebSocketTradeFlowManager()
    
    print("\n1. 📤 PLACE ORDER:")
    order_data = {
        'exchange': 'binance',
        'pair': 'NEO/USDC',
        'amount': 28.5,
        'price': 7.15,
        'strategy': 'heikin_ashi',
        'reason': 'WebSocket-based entry signal'
    }
    
    # This would be async in real implementation
    print(f"   Order: {order_data['amount']} {order_data['pair']} @ ${order_data['price']}")
    print(f"   Exchange: {order_data['exchange']}")
    print(f"   Strategy: {order_data['strategy']}")
    print(f"   ✅ Order placed, WebSocket tracking started")
    print(f"   📋 Stored in PENDING_ORDERS table")
    print(f"   ⏳ Waiting for WebSocket fill confirmation...")
    
    print("\n2. ⚡ WEBSOCKET FILL EVENT:")
    fill_event = {
        'exchange_order_id': '145999999',
        'status': 'filled',
        'price': 7.151,
        'filled_amount': 28.5,
        'fee_amount': 0.0002,
        'fee_currency': 'BNB'
    }
    
    print(f"   🔔 WebSocket notification received:")
    print(f"   Order: {fill_event['exchange_order_id']}")
    print(f"   Status: {fill_event['status']}")
    print(f"   Filled: {fill_event['filled_amount']} @ ${fill_event['price']}")
    print(f"   Fee: {fill_event['fee_amount']} {fill_event['fee_currency']}")
    
    print("\n3. ✅ CREATE OPEN TRADE:")
    print(f"   🎯 NOW creating OPEN trade (order confirmed filled)")
    print(f"   Trade ID: abc12345")
    print(f"   Status: OPEN")
    print(f"   Entry Price: ${fill_event['price']}")
    print(f"   Position Size: {fill_event['filled_amount']}")
    print(f"   Entry Fee: {fill_event['fee_amount']} {fill_event['fee_currency']}")
    print(f"   📋 Removed from PENDING_ORDERS table")
    
    print("\n🎯 BENEFITS OF WEBSOCKET APPROACH:")
    print("   ✅ Real-time fill detection (sub-second)")
    print("   ✅ No polling delays or missed fills") 
    print("   ✅ Accurate fill prices and fees")
    print("   ✅ Proper order lifecycle tracking")
    print("   ✅ Eliminates database/exchange mismatches")

if __name__ == "__main__":
    demonstrate_flow()
    
    print("\n" + "="*60)
    print("🔧 IMPLEMENTATION STEPS:")
    print("1. Apply PENDING_ORDERS schema to database")
    print("2. Modify orchestrator to use pending orders")
    print("3. Enhance WebSocket handlers for order tracking")
    print("4. Update fill-detection to use WebSocket events")
    print("5. Test end-to-end with small orders")
    print("6. Gradually migrate existing system")