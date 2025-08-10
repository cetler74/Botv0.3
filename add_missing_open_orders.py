#!/usr/bin/env python3
"""
Add missing open orders from exchange to database
These are the 13 OPEN limit orders on CryptoCom that aren't tracked in database
"""
import asyncio
import httpx
import json
from datetime import datetime

async def add_missing_open_orders():
    """Add the 13 missing open orders from CryptoCom exchange"""
    
    print("🔧 Adding missing open orders from CryptoCom exchange to database...")
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        
        # Get current open orders from exchange
        try:
            exchange_response = await client.get("http://localhost:8003/api/v1/trading/orders/cryptocom")
            if exchange_response.status_code != 200:
                print(f"❌ Failed to get CryptoCom orders: {exchange_response.status_code}")
                return
            
            exchange_orders = exchange_response.json().get('orders', [])
            print(f"📊 Found {len(exchange_orders)} open orders on CryptoCom exchange")
            
            # Get existing database orders
            db_response = await client.get("http://localhost:8002/api/v1/orders")
            if db_response.status_code != 200:
                print(f"❌ Failed to get database orders: {db_response.status_code}")
                return
                
            db_orders = db_response.json().get('orders', [])
            existing_exchange_order_ids = set(
                order.get('exchange_order_id') for order in db_orders 
                if order.get('exchange_order_id') and order.get('exchange') == 'cryptocom'
            )
            
            print(f"💾 Found {len(existing_exchange_order_ids)} existing CryptoCom orders in database")
            
            added_count = 0
            
            for exchange_order in exchange_orders:
                exchange_order_id = exchange_order.get('id')
                
                if exchange_order_id not in existing_exchange_order_ids:
                    print(f"🆕 Adding missing order {exchange_order_id} ({exchange_order.get('symbol')})")
                    
                    # Create order data for database
                    order_data = {
                        'order_id': exchange_order_id,  # Use exchange order ID as primary key
                        'exchange_order_id': exchange_order_id,
                        'exchange': 'cryptocom',
                        'symbol': exchange_order.get('symbol', 'UNKNOWN'),
                        'order_type': exchange_order.get('type', 'limit').lower(),
                        'side': exchange_order.get('side', 'buy').lower(), 
                        'amount': float(exchange_order.get('amount', 0)),
                        'price': float(exchange_order.get('price', 0)) if exchange_order.get('price') else None,
                        'filled_amount': float(exchange_order.get('filled', 0)),
                        'filled_price': float(exchange_order.get('average', 0)) if exchange_order.get('average') else None,
                        'status': 'pending',  # These are open orders
                        'created_at': datetime.fromtimestamp(exchange_order.get('timestamp', 0) / 1000).isoformat() + 'Z' if exchange_order.get('timestamp') else datetime.utcnow().isoformat() + 'Z',
                        'updated_at': datetime.utcnow().isoformat() + 'Z'
                    }
                    
                    # Add fees if available
                    if 'fee' in exchange_order and exchange_order['fee']:
                        order_data['fees'] = exchange_order['fee'].get('cost', 0)
                        order_data['fee_rate'] = exchange_order['fee'].get('rate', 0)
                    
                    # Add to database
                    try:
                        add_response = await client.post("http://localhost:8002/api/v1/orders", json=order_data)
                        if add_response.status_code == 200:
                            print(f"✅ Added missing order {exchange_order_id}")
                            added_count += 1
                        else:
                            print(f"❌ Failed to add order {exchange_order_id}: {add_response.status_code}")
                            print(f"   Error: {add_response.text}")
                    except Exception as e:
                        print(f"❌ Exception adding order {exchange_order_id}: {e}")
                else:
                    print(f"⏭️  Order {exchange_order_id} already exists in database")
            
            print(f"\n✅ Added {added_count} missing open orders to database")
            
        except Exception as e:
            print(f"❌ Error: {e}")
    
    # Verify the fix worked
    print(f"\n📊 Verification - checking updated order counts...")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Check database orders
            response = await client.get("http://localhost:8002/api/v1/orders")
            if response.status_code == 200:
                orders = response.json().get('orders', [])
                cryptocom_orders = [o for o in orders if o.get('exchange') == 'cryptocom']
                pending_orders = [o for o in cryptocom_orders if o.get('status') == 'pending']
                
                print(f"📈 CryptoCom orders in database:")
                print(f"   Total CryptoCom orders: {len(cryptocom_orders)}")
                print(f"   Pending orders: {len(pending_orders)}")
                
                # Status distribution
                status_counts = {}
                for order in cryptocom_orders:
                    status = order.get('status', 'unknown')
                    status_counts[status] = status_counts.get(status, 0) + 1
                    
                print(f"   Status distribution: {status_counts}")
                
            # Check exchange orders
            exchange_response = await client.get("http://localhost:8003/api/v1/trading/orders/cryptocom")
            if exchange_response.status_code == 200:
                exchange_orders = exchange_response.json().get('orders', [])
                print(f"📊 CryptoCom exchange has {len(exchange_orders)} open orders")
                
                if len(pending_orders) == len(exchange_orders):
                    print("✅ SUCCESS: Database and exchange order counts now match!")
                else:
                    print(f"⚠️  WARNING: Still mismatch - DB has {len(pending_orders)}, exchange has {len(exchange_orders)}")
                    
    except Exception as e:
        print(f"❌ Verification error: {e}")

if __name__ == "__main__":
    asyncio.run(add_missing_open_orders())