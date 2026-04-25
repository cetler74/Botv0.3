#!/usr/bin/env python3
"""
Quick recovery script for Binance missing order fills
"""

import asyncio
import httpx
import json
from datetime import datetime

DATABASE_SERVICE_URL = "http://localhost:8002"
EXCHANGE_SERVICE_URL = "http://localhost:8003"

async def recover_binance_orders():
    """Quick recovery for Binance orders"""
    print("🔍 Recovering Binance missing fills...")
    
    # Get all PENDING Binance orders
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/orders?status=PENDING")
        if response.status_code != 200:
            print("❌ Failed to get orders from database")
            return
        
        all_orders = response.json().get('orders', [])
        binance_orders = [order for order in all_orders if order.get('exchange') == 'binance']
        
        print(f"Found {len(binance_orders)} PENDING Binance orders")
        
        recovered = 0
        errors = 0
        
        for db_order in binance_orders:  # Process all orders
            order_id = db_order.get('exchange_order_id')
            symbol = db_order.get('symbol')
            
            print(f"\n📋 Checking order {order_id} ({symbol})")
            
            try:
                # Check order on exchange using corrected endpoint
                response = await client.get(f"{EXCHANGE_SERVICE_URL}/api/v1/trading/orders/history/binance?symbol={symbol}")
                
                if response.status_code == 200:
                    orders = response.json().get('orders', [])
                    
                    # Find our order
                    exchange_order = None
                    for order in orders:
                        if order.get('id') == order_id:
                            exchange_order = order
                            break
                    
                    if exchange_order:
                        status = exchange_order.get('status', '').lower()
                        filled = float(exchange_order.get('filled', 0))
                        
                        print(f"   Found on exchange: status={status}, filled={filled}")
                        
                        if status in ['filled', 'closed'] and filled > 0:
                            # Update database
                            update_data = {
                                'status': 'FILLED',
                                'filled_amount': filled,
                                'filled_price': float(exchange_order.get('average', 0)),
                                'updated_at': datetime.utcnow().isoformat() + 'Z'
                            }
                            
                            update_response = await client.put(
                                f"{DATABASE_SERVICE_URL}/api/v1/orders/{order_id}",
                                json=update_data
                            )
                            
                            if update_response.status_code == 200:
                                print(f"   ✅ Recovered: {filled} at ${exchange_order.get('average')}")
                                recovered += 1
                            else:
                                print(f"   ❌ Failed to update database")
                                errors += 1
                        else:
                            print(f"   Order not filled on exchange")
                    else:
                        print(f"   Order not found in exchange history")
                else:
                    print(f"   ❌ Failed to get exchange history: {response.status_code}")
                    errors += 1
                    
            except Exception as e:
                print(f"   ❌ Error: {e}")
                errors += 1
        
        print(f"\n📊 Results: {recovered} recovered, {errors} errors")

if __name__ == "__main__":
    asyncio.run(recover_binance_orders())