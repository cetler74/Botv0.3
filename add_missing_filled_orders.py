#!/usr/bin/env python3
"""
Add missing filled orders that the user mentioned to fix performance analytics
"""
import asyncio
import httpx
import json
from datetime import datetime

async def add_missing_filled_orders():
    """Add the filled orders the user mentioned that are missing from database"""
    
    # Orders the user mentioned as filled on the exchange
    filled_orders = [
        {
            'exchange_order_id': '6530219673',  # A2Z/USD Limit Buy filled
            'symbol': 'A2Z/USD',
            'order_type': 'limit',
            'side': 'buy',
            'amount': 14600.0,
            'price': 0.0068025,
            'filled_amount': 14600.0,
            'filled_price': 0.0067979,
            'status': 'filled',
            'created_at': '2025-08-07T20:08:56Z',
            'exchange': 'cryptocom'
        },
        {
            'exchange_order_id': '6530212580',  # A2Z/USD Limit Buy filled  
            'symbol': 'A2Z/USD',
            'order_type': 'limit',
            'side': 'buy',
            'amount': 14700.0,
            'price': 0.0067561,
            'filled_amount': 14700.0,
            'filled_price': 0.0067561,
            'status': 'filled',
            'created_at': '2025-08-07T20:07:55Z',
            'exchange': 'cryptocom'
        },
        {
            'exchange_order_id': '6530211002',  # A2Z/USD Limit Buy filled
            'symbol': 'A2Z/USD', 
            'order_type': 'limit',
            'side': 'buy',
            'amount': 15000.0,
            'price': 0.0066378,
            'filled_amount': 15000.0,
            'filled_price': 0.0066378,
            'status': 'filled',
            'created_at': '2025-08-07T20:06:59Z',
            'exchange': 'cryptocom'
        },
        {
            'exchange_order_id': '6530195348',  # A2Z/USD Market Sell filled
            'symbol': 'A2Z/USD',
            'order_type': 'market', 
            'side': 'sell',
            'amount': 19900.0,
            'filled_amount': 19900.0,
            'filled_price': 0.0054758,
            'status': 'filled',
            'created_at': '2025-08-07T18:17:04Z',
            'exchange': 'cryptocom'
        },
        {
            'exchange_order_id': '6530190946',  # ACX/USD Limit Buy filled
            'symbol': 'ACX/USD',
            'order_type': 'limit',
            'side': 'buy', 
            'amount': 610.0,
            'price': 0.16364,
            'filled_amount': 610.0,
            'filled_price': 0.16141,
            'status': 'filled',
            'created_at': '2025-08-07T17:48:17Z',
            'exchange': 'cryptocom'
        }
    ]
    
    print(f"🔧 Adding {len(filled_orders)} missing filled orders to database...")
    
    added_count = 0
    async with httpx.AsyncClient(timeout=30.0) as client:
        
        for order in filled_orders:
            try:
                # Check if this specific filled order already exists
                check_response = await client.get(f"http://localhost:8002/api/v1/orders")
                if check_response.status_code == 200:
                    existing_orders = check_response.json().get('orders', [])
                    # Check if we already have this specific filled order added
                    existing_filled = [o for o in existing_orders if 
                                     o.get('order_id', '').startswith('filled_') and 
                                     order['exchange_order_id'] in o.get('order_id', '')]
                    if existing_filled:
                        print(f"⏭️  Filled order for {order['exchange_order_id']} already exists, skipping")
                        continue
                
                # Add the missing order
                order_data = {
                    'order_id': f"filled_{order['exchange_order_id']}",  # Unique ID to avoid conflicts
                    'exchange_order_id': order['exchange_order_id'],
                    'exchange': order['exchange'],
                    'symbol': order['symbol'],
                    'order_type': order['order_type'],
                    'side': order['side'],
                    'amount': order['amount'],
                    'price': order.get('price'),
                    'filled_amount': order['filled_amount'],
                    'filled_price': order['filled_price'],
                    'status': order['status'],
                    'created_at': order['created_at'],
                    'updated_at': datetime.utcnow().isoformat() + 'Z'
                }
                
                response = await client.post("http://localhost:8002/api/v1/orders", json=order_data)
                if response.status_code == 200:
                    print(f"✅ Added filled order {order['exchange_order_id']} ({order['symbol']} {order['order_type']})")
                    added_count += 1
                else:
                    print(f"❌ Failed to add {order['exchange_order_id']}: {response.status_code}")
                    print(f"   Error: {response.text}")
                    
            except Exception as e:
                print(f"❌ Error adding order {order['exchange_order_id']}: {e}")
    
    print(f"\n✅ Added {added_count} filled orders to database")
    
    # Verify the fix worked
    print(f"\n📊 Checking updated performance metrics...")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get("http://localhost:8002/api/v1/orders")
            if response.status_code == 200:
                orders = response.json().get('orders', [])
                limit_orders = [o for o in orders if o.get('order_type') == 'limit']
                market_orders = [o for o in orders if o.get('order_type') == 'market']
                filled_orders = [o for o in orders if o.get('status') == 'filled']
                
                filled_limits = [o for o in limit_orders if o.get('status') == 'filled']
                filled_markets = [o for o in market_orders if o.get('status') == 'filled']
                
                limit_success_rate = (len(filled_limits) / len(limit_orders) * 100) if limit_orders else 0
                market_success_rate = (len(filled_markets) / len(market_orders) * 100) if market_orders else 0
                
                print(f"📈 Updated performance metrics:")
                print(f"   Total orders: {len(orders)}")
                print(f"   Limit orders: {len(limit_orders)} (success: {len(filled_limits)}, rate: {limit_success_rate:.1f}%)")
                print(f"   Market orders: {len(market_orders)} (success: {len(filled_markets)}, rate: {market_success_rate:.1f}%)")
                print(f"   Total filled: {len(filled_orders)}")
                
    except Exception as e:
        print(f"❌ Verification error: {e}")

if __name__ == "__main__":
    asyncio.run(add_missing_filled_orders())