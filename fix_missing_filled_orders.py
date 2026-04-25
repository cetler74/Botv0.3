#!/usr/bin/env python3
"""
EMERGENCY: Fix missing filled orders for 1INCH/USD on cryptocom
These orders were filled on the exchange but never recorded as trades due to verification timeout
"""
import httpx
import asyncio
import uuid
from datetime import datetime

# Missing filled orders data from user
MISSING_ORDERS = [
    {"exchange_order_id": "6530219584133556707", "fill_time": "2025-08-21T12:28:17+00:00", "fill_price": 0.25225, "qty": 395.5},
    {"exchange_order_id": "6530219584133488714", "fill_time": "2025-08-21T12:27:16+00:00", "fill_price": 0.25234, "qty": 395.5},
    {"exchange_order_id": "6530219584133495654", "fill_time": "2025-08-21T12:26:17+00:00", "fill_price": 0.25265, "qty": 396.0},
    {"exchange_order_id": "6530219584133506169", "fill_time": "2025-08-21T12:25:22+00:00", "fill_price": 0.25304, "qty": 396.0},
    {"exchange_order_id": "6530219584133509172", "fill_time": "2025-08-21T12:24:20+00:00", "fill_price": 0.25266, "qty": 396.0},
    {"exchange_order_id": "6530219584133515652", "fill_time": "2025-08-21T12:23:15+00:00", "fill_price": 0.25244, "qty": 396.0},
    {"exchange_order_id": "6530219584133522184", "fill_time": "2025-08-21T12:22:15+00:00", "fill_price": 0.25284, "qty": 396.0},
    {"exchange_order_id": "6530219584133524924", "fill_time": "2025-08-21T12:21:17+00:00", "fill_price": 0.25264, "qty": 396.0},
    {"exchange_order_id": "6530219584133536053", "fill_time": "2025-08-21T12:20:21+00:00", "fill_price": 0.25225, "qty": 396.0},
]

DATABASE_URL = "http://localhost:8002"

async def fix_missing_filled_orders():
    """Fix the missing filled orders by updating database and creating trades"""
    async with httpx.AsyncClient(timeout=30.0) as client:
        
        for order_data in MISSING_ORDERS:
            exchange_order_id = order_data["exchange_order_id"]
            
            print(f"\n🔧 Processing order {exchange_order_id}...")
            
            # 1. Get the order from database
            try:
                response = await client.get(f"{DATABASE_URL}/api/v1/orders")
                response.raise_for_status()
                orders = response.json().get("orders", [])
                
                order = None
                for o in orders:
                    if o.get("exchange_order_id") == exchange_order_id:
                        order = o
                        break
                
                if not order:
                    print(f"❌ Order {exchange_order_id} not found in database")
                    continue
                    
                print(f"✅ Found order in database: {order['order_id']}")
                
                # 2. Update order status to FILLED
                update_data = {
                    "status": "FILLED",
                    "filled_amount": order_data["qty"],
                    "filled_price": order_data["fill_price"], 
                    "filled_at": order_data["fill_time"]
                }
                
                response = await client.put(f"{DATABASE_URL}/api/v1/orders/{order['order_id']}", json=update_data)
                response.raise_for_status()
                print(f"✅ Updated order status to FILLED")
                
                # 3. Create trade record
                trade_id = str(uuid.uuid4())
                notional_value = order_data["qty"] * order_data["fill_price"]
                
                trade_data = {
                    "trade_id": trade_id,
                    "pair": "1INCH/USD",
                    "entry_price": order_data["fill_price"],
                    "entry_id": exchange_order_id,
                    "entry_time": order_data["fill_time"],
                    "status": "OPEN",
                    "exchange": "cryptocom",
                    "entry_reason": "recovery_missing_fill",
                    "position_size": order_data["qty"],
                    "strategy": "heikin_ashi",
                    "notional_value": notional_value,
                    "current_price": order_data["fill_price"],
                    "highest_price": order_data["fill_price"],
                }
                
                response = await client.post(f"{DATABASE_URL}/api/v1/trades", json=trade_data)
                response.raise_for_status()
                print(f"✅ Created trade record: {trade_id}")
                
                # 4. Update order to link to trade
                response = await client.put(f"{DATABASE_URL}/api/v1/orders/{order['order_id']}", 
                                          json={"trade_id": trade_id})
                response.raise_for_status()
                print(f"✅ Linked order to trade")
                
            except Exception as e:
                print(f"❌ Error processing order {exchange_order_id}: {e}")
                
        print(f"\n🎉 Recovery complete! Processed {len(MISSING_ORDERS)} orders")

if __name__ == "__main__":
    asyncio.run(fix_missing_filled_orders())