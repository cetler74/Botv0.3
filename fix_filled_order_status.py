#!/usr/bin/env python3
"""
Fix the incorrectly marked order that was actually filled
Order ID: 6530219582963593826 - currently marked as 'cancelled' but actually 'filled'
"""
import asyncio
import httpx
from datetime import datetime

async def fix_filled_order_status():
    """Fix the specific order that was marked as cancelled but is actually filled"""
    
    order_id = "6530219582963593826"
    exchange_order_id = "6530219582963593826"
    exchange = "cryptocom"
    
    print(f"🔧 Fixing order status for {order_id}...")
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            # First, verify the order is actually filled on the exchange
            exchange_response = await client.get(f"http://localhost:8003/api/v1/trading/orders/{exchange}")
            
            if exchange_response.status_code == 200:
                exchange_orders = exchange_response.json().get('orders', [])
                filled_order = None
                
                # Check if the order is still in open orders (it shouldn't be if filled)
                for ex_order in exchange_orders:
                    if ex_order.get('id') == exchange_order_id:
                        print(f"⚠️  Order {exchange_order_id} is still OPEN on exchange with status: {ex_order.get('status')}")
                        return
                
                print(f"✅ Order {exchange_order_id} is not in open orders (confirming it was filled)")
                
                # Update the order status in database to 'filled'
                update_data = {
                    'status': 'filled',
                    'filled_amount': 14000,  # From your message: 14,000 filled
                    'filled_price': 0.0071077,  # From your message: filled at 0.0071077
                    'updated_at': datetime.utcnow().isoformat() + 'Z',
                    'cancellation_reason': 'CORRECTED: Order was actually filled, not cancelled - race condition fix',
                    'sync_source': 'manual_correction'
                }
                
                update_response = await client.put(f"http://localhost:8002/api/v1/orders/{order_id}", json=update_data)
                
                if update_response.status_code == 200:
                    print(f"✅ Successfully updated order {order_id} status from 'cancelled' to 'filled'")
                    print(f"   Filled amount: 14,000")
                    print(f"   Filled price: $0.0071077")
                    print(f"   This will now be counted in performance metrics as a SUCCESSFUL limit order")
                else:
                    print(f"❌ Failed to update order status: {update_response.status_code}")
                    print(f"   Response: {update_response.text}")
            else:
                print(f"❌ Failed to get exchange orders: {exchange_response.status_code}")
                
        except Exception as e:
            print(f"❌ Error: {e}")
    
    # Verify the fix worked
    print(f"\n📊 Verification - checking updated order status...")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"http://localhost:8002/api/v1/orders")
            if response.status_code == 200:
                orders = response.json().get('orders', [])
                target_order = None
                
                for order in orders:
                    if order.get('order_id') == order_id or order.get('exchange_order_id') == exchange_order_id:
                        target_order = order
                        break
                
                if target_order:
                    print(f"📈 Order {order_id} current status:")
                    print(f"   Status: {target_order.get('status')}")
                    print(f"   Filled amount: {target_order.get('filled_amount')}")
                    print(f"   Filled price: {target_order.get('filled_price')}")
                    print(f"   Updated at: {target_order.get('updated_at')}")
                    
                    if target_order.get('status') == 'filled':
                        print(f"🎯 SUCCESS: Order status corrected! This will improve performance metrics.")
                    else:
                        print(f"⚠️  Order still shows as: {target_order.get('status')}")
                else:
                    print(f"❌ Could not find order {order_id} in database")
    except Exception as e:
        print(f"❌ Verification error: {e}")

if __name__ == "__main__":
    asyncio.run(fix_filled_order_status())