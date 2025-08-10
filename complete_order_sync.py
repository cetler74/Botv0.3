#!/usr/bin/env python3
"""
Complete order synchronization - ensure all exchange orders are in database
"""
import asyncio
import httpx
from datetime import datetime

async def complete_order_sync():
    """Complete synchronization of all exchange orders to database"""
    
    print("🔧 Performing complete order synchronization...")
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        
        # Get all open orders from CryptoCom exchange
        try:
            exchange_response = await client.get("http://localhost:8003/api/v1/trading/orders/cryptocom")
            if exchange_response.status_code != 200:
                print(f"❌ Failed to get CryptoCom orders: {exchange_response.status_code}")
                return
            
            exchange_orders = exchange_response.json().get('orders', [])
            print(f"📊 Found {len(exchange_orders)} OPEN orders on CryptoCom exchange")
            
            # Get all database orders for CryptoCom
            db_response = await client.get("http://localhost:8002/api/v1/orders")
            if db_response.status_code != 200:
                print(f"❌ Failed to get database orders: {db_response.status_code}")
                return
                
            db_orders = db_response.json().get('orders', [])
            cryptocom_db_orders = [order for order in db_orders if order.get('exchange') == 'cryptocom']
            
            # Create maps of existing database orders
            db_by_order_id = {order.get('order_id'): order for order in cryptocom_db_orders}
            db_by_exchange_id = {order.get('exchange_order_id'): order for order in cryptocom_db_orders if order.get('exchange_order_id')}
            
            print(f"💾 Found {len(cryptocom_db_orders)} CryptoCom orders in database")
            print(f"   - {len(db_by_order_id)} indexed by order_id")
            print(f"   - {len(db_by_exchange_id)} indexed by exchange_order_id")
            
            updated_count = 0
            added_count = 0
            
            for exchange_order in exchange_orders:
                exchange_id = exchange_order.get('id')
                symbol = exchange_order.get('symbol', 'UNKNOWN')
                
                print(f"\n🔍 Processing exchange order {exchange_id} ({symbol})")
                
                # Check if this order exists in database by any identifier
                db_order = None
                if exchange_id in db_by_order_id:
                    db_order = db_by_order_id[exchange_id]
                    print(f"   Found in DB by order_id")
                elif exchange_id in db_by_exchange_id:
                    db_order = db_by_exchange_id[exchange_id]
                    print(f"   Found in DB by exchange_order_id")
                
                if db_order:
                    # Order exists, check if status needs updating
                    current_status = db_order.get('status')
                    if current_status != 'pending':
                        print(f"   📝 Updating status: {current_status} -> pending")
                        
                        update_data = {
                            'status': 'pending',
                            'exchange_order_id': exchange_id,  # Ensure exchange_order_id is set
                            'updated_at': datetime.utcnow().isoformat() + 'Z'
                        }
                        
                        try:
                            update_response = await client.put(
                                f"http://localhost:8002/api/v1/orders/{db_order['order_id']}", 
                                json=update_data
                            )
                            if update_response.status_code == 200:
                                print(f"   ✅ Updated order status to pending")
                                updated_count += 1
                            else:
                                print(f"   ❌ Failed to update: {update_response.status_code}")
                        except Exception as e:
                            print(f"   ❌ Update error: {e}")
                    else:
                        print(f"   ✅ Status already correct: {current_status}")
                else:
                    # Order doesn't exist, add it
                    print(f"   🆕 Order not found in database - adding...")
                    
                    order_data = {
                        'order_id': f"sync_{exchange_id}",  # Use unique prefix to avoid conflicts
                        'exchange_order_id': exchange_id,
                        'exchange': 'cryptocom',
                        'symbol': symbol,
                        'order_type': exchange_order.get('type', 'limit').lower(),
                        'side': exchange_order.get('side', 'buy').lower(),
                        'amount': float(exchange_order.get('amount', 0)),
                        'price': float(exchange_order.get('price', 0)) if exchange_order.get('price') else None,
                        'filled_amount': float(exchange_order.get('filled', 0)),
                        'status': 'pending',
                        'created_at': datetime.fromtimestamp(exchange_order.get('timestamp', 0) / 1000).isoformat() + 'Z' if exchange_order.get('timestamp') else datetime.utcnow().isoformat() + 'Z',
                        'updated_at': datetime.utcnow().isoformat() + 'Z'
                    }
                    
                    try:
                        add_response = await client.post("http://localhost:8002/api/v1/orders", json=order_data)
                        if add_response.status_code == 200:
                            print(f"   ✅ Added new order to database")
                            added_count += 1
                        else:
                            print(f"   ❌ Failed to add: {add_response.status_code} - {add_response.text}")
                    except Exception as e:
                        print(f"   ❌ Add error: {e}")
            
            print(f"\n🎯 Synchronization completed:")
            print(f"   📝 Updated {updated_count} existing orders")
            print(f"   🆕 Added {added_count} missing orders")
            
        except Exception as e:
            print(f"❌ Error: {e}")
    
    # Final verification
    print(f"\n📊 Final verification...")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Check database after sync
            response = await client.get("http://localhost:8002/api/v1/orders")
            if response.status_code == 200:
                orders = response.json().get('orders', [])
                cryptocom_orders = [o for o in orders if o.get('exchange') == 'cryptocom']
                pending_orders = [o for o in cryptocom_orders if o.get('status') == 'pending']
                
                print(f"📈 Database after sync:")
                print(f"   Total CryptoCom orders: {len(cryptocom_orders)}")
                print(f"   Pending orders: {len(pending_orders)}")
                
                # Check exchange
                exchange_response = await client.get("http://localhost:8003/api/v1/trading/orders/cryptocom")
                if exchange_response.status_code == 200:
                    exchange_orders = exchange_response.json().get('orders', [])
                    print(f"📊 Exchange OPEN orders: {len(exchange_orders)}")
                    
                    if len(pending_orders) == len(exchange_orders):
                        print("🎯 ✅ SUCCESS: Perfect synchronization achieved!")
                        print("   Database pending orders = Exchange open orders")
                        
                        # Update performance analytics
                        limit_orders = [o for o in cryptocom_orders if o.get('order_type') == 'limit']
                        filled_orders = [o for o in cryptocom_orders if o.get('status') == 'filled']
                        
                        print(f"\n📊 Updated Performance Metrics:")
                        print(f"   Total limit orders: {len(limit_orders)}")
                        print(f"   Filled orders: {len(filled_orders)}")
                        if limit_orders:
                            success_rate = len(filled_orders) / len(limit_orders) * 100
                            print(f"   Success rate: {success_rate:.1f}%")
                        
                    else:
                        print(f"⚠️  Still mismatched: DB={len(pending_orders)}, Exchange={len(exchange_orders)}")
                    
    except Exception as e:
        print(f"❌ Verification error: {e}")

if __name__ == "__main__":
    asyncio.run(complete_order_sync())