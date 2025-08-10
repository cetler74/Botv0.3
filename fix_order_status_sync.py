#!/usr/bin/env python3
"""
Fix order status synchronization - update database to match exchange reality
"""
import asyncio
import httpx
from datetime import datetime

async def fix_order_status_sync():
    """Fix the 13 orders that should be pending but are marked as cancelled/failed"""
    
    print("🔧 Fixing order status synchronization between database and exchange...")
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        
        # Get current open orders from CryptoCom exchange
        try:
            exchange_response = await client.get("http://localhost:8003/api/v1/trading/orders/cryptocom")
            if exchange_response.status_code != 200:
                print(f"❌ Failed to get CryptoCom orders: {exchange_response.status_code}")
                return
            
            exchange_orders = exchange_response.json().get('orders', [])
            open_order_ids = set(order.get('id') for order in exchange_orders)
            print(f"📊 Found {len(open_order_ids)} OPEN orders on CryptoCom exchange")
            
            # Get all database orders for CryptoCom
            db_response = await client.get("http://localhost:8002/api/v1/orders")
            if db_response.status_code != 200:
                print(f"❌ Failed to get database orders: {db_response.status_code}")
                return
                
            db_orders = db_response.json().get('orders', [])
            cryptocom_db_orders = [order for order in db_orders if order.get('exchange') == 'cryptocom']
            print(f"💾 Found {len(cryptocom_db_orders)} CryptoCom orders in database")
            
            # Find orders that are OPEN on exchange but not PENDING in database
            fixed_count = 0
            
            for db_order in cryptocom_db_orders:
                order_id = db_order.get('order_id')
                exchange_order_id = db_order.get('exchange_order_id')
                current_status = db_order.get('status')
                
                # Check if this order is open on exchange
                if (order_id in open_order_ids or exchange_order_id in open_order_ids) and current_status != 'pending':
                    print(f"🔄 Fixing order {order_id}: {current_status} -> pending (OPEN on exchange)")
                    
                    update_data = {
                        'status': 'pending',
                        'updated_at': datetime.utcnow().isoformat() + 'Z',
                        'sync_source': 'status_fix_script'
                    }
                    
                    try:
                        update_response = await client.put(f"http://localhost:8002/api/v1/orders/{order_id}", json=update_data)
                        if update_response.status_code == 200:
                            print(f"✅ Fixed order {order_id} status to pending")
                            fixed_count += 1
                        else:
                            print(f"❌ Failed to update order {order_id}: {update_response.status_code}")
                    except Exception as e:
                        print(f"❌ Error updating order {order_id}: {e}")
                        
                elif order_id in open_order_ids or exchange_order_id in open_order_ids:
                    print(f"✅ Order {order_id} already has correct status: {current_status}")
            
            print(f"\n✅ Fixed {fixed_count} order statuses")
            
        except Exception as e:
            print(f"❌ Error: {e}")
    
    # Verify the fix
    print(f"\n📊 Verification - checking synchronized order counts...")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Check database orders after fix
            response = await client.get("http://localhost:8002/api/v1/orders")
            if response.status_code == 200:
                orders = response.json().get('orders', [])
                cryptocom_orders = [o for o in orders if o.get('exchange') == 'cryptocom']
                
                # Count by status
                status_counts = {}
                for order in cryptocom_orders:
                    status = order.get('status', 'unknown')
                    status_counts[status] = status_counts.get(status, 0) + 1
                    
                pending_count = status_counts.get('pending', 0)
                
                print(f"📈 CryptoCom database orders after fix:")
                print(f"   Total orders: {len(cryptocom_orders)}")
                print(f"   Status distribution: {status_counts}")
                
                # Check exchange orders
                exchange_response = await client.get("http://localhost:8003/api/v1/trading/orders/cryptocom")
                if exchange_response.status_code == 200:
                    exchange_orders = exchange_response.json().get('orders', [])
                    exchange_count = len(exchange_orders)
                    
                    print(f"📊 CryptoCom exchange: {exchange_count} OPEN orders")
                    
                    if pending_count == exchange_count:
                        print("🎯 SUCCESS: Database pending orders now match exchange open orders!")
                    else:
                        print(f"⚠️  MISMATCH: Database has {pending_count} pending, exchange has {exchange_count} open")
                        
                        # Show the mismatch details
                        print("\n🔍 Detailed analysis:")
                        db_pending_ids = set()
                        for order in cryptocom_orders:
                            if order.get('status') == 'pending':
                                db_pending_ids.add(order.get('exchange_order_id') or order.get('order_id'))
                        
                        exchange_open_ids = set(order.get('id') for order in exchange_orders)
                        
                        missing_in_db = exchange_open_ids - db_pending_ids
                        extra_in_db = db_pending_ids - exchange_open_ids
                        
                        if missing_in_db:
                            print(f"   Orders OPEN on exchange but NOT pending in DB: {len(missing_in_db)}")
                            for order_id in list(missing_in_db)[:5]:  # Show first 5
                                print(f"     - {order_id}")
                        
                        if extra_in_db:
                            print(f"   Orders PENDING in DB but NOT open on exchange: {len(extra_in_db)}")
                            for order_id in list(extra_in_db)[:5]:  # Show first 5
                                print(f"     - {order_id}")
                    
    except Exception as e:
        print(f"❌ Verification error: {e}")

if __name__ == "__main__":
    asyncio.run(fix_order_status_sync())