#!/usr/bin/env python3
"""
Batch update existing PENDING orders to FILLED status and create missing trades
"""

import asyncio
import httpx
import uuid
from datetime import datetime, timezone

DATABASE_SERVICE_URL = "http://localhost:8002"

# Orders we know are FILLED based on exchange data
FILLED_ORDERS = [
    {"order_id": "141242031", "symbol": "NEO/USDC", "filled_amount": 23.77, "filled_price": 6.31, "exchange": "binance"},
    {"order_id": "141243277", "symbol": "NEO/USDC", "filled_amount": 23.7, "filled_price": 6.32, "exchange": "binance"},
    {"order_id": "141243829", "symbol": "NEO/USDC", "filled_amount": 23.58, "filled_price": 6.36, "exchange": "binance"},
    {"order_id": "141252234", "symbol": "NEO/USDC", "filled_amount": 22.9, "filled_price": 6.55, "exchange": "binance"},
    {"order_id": "496762342", "symbol": "LINK/USDC", "filled_amount": 6.17, "filled_price": 26.07, "exchange": "binance"},
    {"order_id": "6530219584096551927", "symbol": "1INCH/USD", "filled_amount": 427.5, "filled_price": 0.25699, "exchange": "cryptocom"},
    {"order_id": "6530219584096611243", "symbol": "1INCH/USD", "filled_amount": 388.9, "filled_price": 0.25691, "exchange": "cryptocom"},
    {"order_id": "6530219584096681743", "symbol": "1INCH/USD", "filled_amount": 388.9, "filled_price": 0.2568, "exchange": "cryptocom"},
    {"order_id": "6530219584096754348", "symbol": "1INCH/USD", "filled_amount": 388.9, "filled_price": 0.2568, "exchange": "cryptocom"},
    {"order_id": "6530219584096889061", "symbol": "1INCH/USD", "filled_amount": 388.9, "filled_price": 0.2568, "exchange": "cryptocom"},
    {"order_id": "6530219584096941072", "symbol": "1INCH/USD", "filled_amount": 388.9, "filled_price": 0.25689, "exchange": "cryptocom"},
    {"order_id": "6530219584097005764", "symbol": "1INCH/USD", "filled_amount": 388.9, "filled_price": 0.2569, "exchange": "cryptocom"},
    {"order_id": "6530219584097070408", "symbol": "1INCH/USD", "filled_amount": 388.9, "filled_price": 0.25713, "exchange": "cryptocom"},
    {"order_id": "6530219584097125730", "symbol": "1INCH/USD", "filled_amount": 388.9, "filled_price": 0.25712, "exchange": "cryptocom"},
    {"order_id": "6530219584097188967", "symbol": "1INCH/USD", "filled_amount": 388.9, "filled_price": 0.2574, "exchange": "cryptocom"},
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
            "entry_reason": "emergency_recovery_batch_update",
            "position_size": order_info["filled_amount"],
            "strategy": "recovery_batch",
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

async def batch_recovery():
    """Run batch recovery for all orders"""
    print("🚨 BATCH RECOVERY: Updating PENDING orders to FILLED")
    print("=" * 50)
    
    updated_orders = 0
    created_trades = 0
    
    for order_info in FILLED_ORDERS:
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
    
    print(f"\n📊 BATCH RECOVERY COMPLETE:")
    print(f"   ✅ Orders updated: {updated_orders}")
    print(f"   ✅ Trades created: {created_trades}")
    print(f"   💰 Total recovered value: ${sum(o['filled_amount'] * o['filled_price'] for o in FILLED_ORDERS):.2f}")

if __name__ == "__main__":
    asyncio.run(batch_recovery())