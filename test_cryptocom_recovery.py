#!/usr/bin/env python3
"""
Test Crypto.com buy order recovery and trade creation
"""

import asyncio
import httpx
import uuid
from datetime import datetime, timezone

DATABASE_SERVICE_URL = "http://localhost:8002"

# The filled ADA orders from Crypto.com
FILLED_ADA_ORDERS = [
    {"order_id": "6530219584115525520", "symbol": "ADA/USD", "filled_amount": 114.0, "filled_price": 0.8768, "exchange": "cryptocom"},
    {"order_id": "6530219584115525624", "symbol": "ADA/USD", "filled_amount": 114.0, "filled_price": 0.8768, "exchange": "cryptocom"},
]

async def update_order_to_filled(order_info):
    """Update order status to FILLED"""
    order_id = order_info["order_id"]
    try:
        update_data = {
            "status": "FILLED",
            "filled_amount": order_info["filled_amount"],
            "filled_price": order_info["filled_price"],
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "filled_at": datetime.now(timezone.utc).isoformat()
        }
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.put(f"{DATABASE_SERVICE_URL}/api/v1/orders/{order_id}", json=update_data)
            if response.status_code == 200:
                print(f"✅ Updated order {order_id} to FILLED")
                return True
            else:
                print(f"❌ Failed to update order {order_id}: {response.status_code}")
                return False
                
    except Exception as e:
        print(f"❌ Error updating order {order_id}: {e}")
        return False

async def create_trade_for_order(order_info):
    """Create trade record for filled order"""
    order_id = order_info["order_id"]
    try:
        trade_id = str(uuid.uuid4())
        notional_value = order_info["filled_amount"] * order_info["filled_price"]
        
        trade_data = {
            "trade_id": trade_id,
            "pair": order_info["symbol"],
            "entry_price": order_info["filled_price"],
            "status": "OPEN",
            "entry_id": order_id,
            "entry_time": datetime.now(timezone.utc).isoformat(),
            "exchange": order_info["exchange"],
            "entry_reason": "test_cryptocom_buy_signal",
            "position_size": order_info["filled_amount"],
            "strategy": "test_heikin_ashi",
            "notional_value": notional_value,
            "current_price": order_info["filled_price"]
        }
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(f"{DATABASE_SERVICE_URL}/api/v1/trades", json=trade_data)
            if response.status_code == 200:
                result = response.json()
                if result.get("status") == "duplicate_suppressed":
                    print(f"⚠️ Trade for order {order_id} already exists")
                    return True
                else:
                    print(f"✅ Created trade {trade_id} for order {order_id} - ${notional_value:.2f}")
                    return True
            else:
                print(f"❌ Failed to create trade for order {order_id}: {response.status_code}")
                return False
                
    except Exception as e:
        print(f"❌ Error creating trade for order {order_id}: {e}")
        return False

async def test_cryptocom_recovery():
    """Test Crypto.com order recovery"""
    print("🚨 TESTING: Crypto.com buy order recovery and trade creation")
    print("=" * 60)
    
    updated_orders = 0
    created_trades = 0
    
    for order_info in FILLED_ADA_ORDERS:
        order_id = order_info["order_id"]
        symbol = order_info["symbol"]
        value = order_info["filled_amount"] * order_info["filled_price"]
        
        print(f"\n📋 Processing {order_id} ({symbol}) - ${value:.2f}")
        
        # Update order to FILLED
        if await update_order_to_filled(order_info):
            updated_orders += 1
            
            # Create trade record
            if await create_trade_for_order(order_info):
                created_trades += 1
    
    print(f"\n📊 CRYPTO.COM TEST COMPLETE:")
    print(f"   ✅ Orders updated: {updated_orders}")
    print(f"   ✅ Trades created: {created_trades}")
    print(f"   💰 Total value: ${sum(o['filled_amount'] * o['filled_price'] for o in FILLED_ADA_ORDERS):.2f}")

if __name__ == "__main__":
    asyncio.run(test_cryptocom_recovery())