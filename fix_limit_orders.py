#!/usr/bin/env python3
"""
Comprehensive fix for limit order tracking and pricing issues
"""
import asyncio
import httpx
import json
from datetime import datetime, timedelta

async def fix_limit_order_issues():
    """Fix limit order tracking, pricing, and synchronization issues"""
    
    print("🔧 Starting comprehensive limit order fixes...")
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        
        # Step 1: Synchronize existing open orders from exchange to database
        print("\n📊 Step 1: Synchronizing open orders from exchange...")
        
        exchanges = ['cryptocom', 'binance', 'bybit']
        for exchange in exchanges:
            try:
                # Get open orders from exchange
                exchange_orders_response = await client.get(f"http://localhost:8003/api/v1/trading/orders/{exchange}")
                if exchange_orders_response.status_code == 200:
                    exchange_orders = exchange_orders_response.json().get('orders', [])
                    print(f"📈 {exchange}: Found {len(exchange_orders)} open orders on exchange")
                    
                    # Check each exchange order against database
                    for order in exchange_orders:
                        order_id = order.get('id')
                        symbol = order.get('symbol', '').replace('/', '')  # Convert ACS/USD -> ACSUSD for search
                        
                        # Check if this order exists in database
                        db_check_response = await client.get(f"http://localhost:8002/api/v1/orders?exchange_order_id={order_id}")
                        if db_check_response.status_code == 200:
                            db_orders = db_check_response.json().get('orders', [])
                            
                            if not db_orders:
                                print(f"🔍 Missing in DB: {order_id} ({order.get('symbol')}) - adding...")
                                
                                # Add missing order to database
                                order_data = {
                                    'order_id': order_id,
                                    'exchange_order_id': order_id,
                                    'exchange': exchange,
                                    'symbol': order.get('symbol'),
                                    'order_type': order.get('type', 'limit'),
                                    'side': order.get('side'),
                                    'amount': order.get('amount', 0),
                                    'price': order.get('price'),
                                    'filled_amount': order.get('filled', 0),
                                    'status': 'pending',  # Will be updated by status sync
                                    'created_at': datetime.fromtimestamp(order.get('timestamp', 0) / 1000).isoformat() if order.get('timestamp') else datetime.utcnow().isoformat()
                                }
                                
                                add_response = await client.post("http://localhost:8002/api/v1/orders", json=order_data)
                                if add_response.status_code == 200:
                                    print(f"✅ Added missing order {order_id} to database")
                                else:
                                    print(f"❌ Failed to add order {order_id}: {add_response.status_code}")
                            else:
                                print(f"✅ Order {order_id} already tracked in database")
                else:
                    print(f"❌ Failed to get {exchange} orders: {exchange_orders_response.status_code}")
                    
            except Exception as e:
                print(f"❌ Error processing {exchange}: {e}")
        
        # Step 2: Check limit order pricing logic
        print(f"\n💰 Step 2: Analyzing current limit order pricing...")
        
        # Get current market data for analysis
        try:
            ticker_response = await client.get("http://localhost:8003/api/v1/market/ticker/cryptocom/ACSUSD")
            orderbook_response = await client.get("http://localhost:8003/api/v1/market/orderbook/cryptocom/ACSUSD?limit=5")
            
            if ticker_response.status_code == 200 and orderbook_response.status_code == 200:
                ticker = ticker_response.json()
                orderbook = orderbook_response.json()
                
                current_price = ticker.get('last', 0)
                best_bid = orderbook.get('bids', [[0]])[0][0] if orderbook.get('bids') else 0
                best_ask = orderbook.get('asks', [[0]])[0][0] if orderbook.get('asks') else 0
                spread_pct = ((best_ask - best_bid) / best_bid * 100) if best_bid > 0 else 0
                
                print(f"📊 ACS/USD Market Analysis:")
                print(f"   Current Price: ${current_price:.8f}")
                print(f"   Best Bid: ${best_bid:.8f}")
                print(f"   Best Ask: ${best_ask:.8f}")
                print(f"   Spread: {spread_pct:.4f}%")
                
                # Calculate what our current logic would do
                current_spread = 0.0005  # 0.05% as per current logic
                our_buy_price = current_price * (1 - current_spread)
                our_sell_price = current_price * (1 + current_spread)
                
                print(f"\n🧮 Current Algorithm Analysis:")
                print(f"   Our BUY limit: ${our_buy_price:.8f} (vs best bid ${best_bid:.8f})")
                print(f"   Our SELL limit: ${our_sell_price:.8f} (vs best ask ${best_ask:.8f})")
                
                if our_buy_price < best_bid:
                    print(f"   ⚠️  BUY price too low - will not fill quickly")
                if our_sell_price > best_ask:
                    print(f"   ⚠️  SELL price too high - will not fill quickly")
                
                # Recommend better pricing
                print(f"\n💡 Recommended Pricing Strategy:")
                recommended_buy = best_bid + (best_bid * 0.0001)  # Slightly above best bid
                recommended_sell = best_ask - (best_ask * 0.0001)  # Slightly below best ask
                print(f"   Better BUY limit: ${recommended_buy:.8f}")
                print(f"   Better SELL limit: ${recommended_sell:.8f}")
                
        except Exception as e:
            print(f"❌ Error analyzing pricing: {e}")
        
        # Step 3: Clean up failed/stale limit orders
        print(f"\n🧹 Step 3: Cleaning up failed limit orders...")
        
        try:
            # Get all pending limit orders from database that are older than 1 hour
            one_hour_ago = datetime.utcnow() - timedelta(hours=1)
            db_orders_response = await client.get("http://localhost:8002/api/v1/orders?order_type=limit&status=pending")
            
            if db_orders_response.status_code == 200:
                db_orders = db_orders_response.json().get('orders', [])
                stale_orders = []
                
                for order in db_orders:
                    created_at = datetime.fromisoformat(order.get('created_at', '').replace('Z', '+00:00').replace('+00:00', ''))
                    if created_at < one_hour_ago:
                        stale_orders.append(order)
                
                print(f"🗑️  Found {len(stale_orders)} stale limit orders (>1 hour old)")
                
                # Mark stale orders as failed if they're not on the exchange
                for order in stale_orders[:10]:  # Limit to first 10
                    order_id = order.get('order_id')
                    exchange = order.get('exchange')
                    
                    # Check if still exists on exchange
                    exchange_orders_response = await client.get(f"http://localhost:8003/api/v1/trading/orders/{exchange}")
                    if exchange_orders_response.status_code == 200:
                        exchange_orders = exchange_orders_response.json().get('orders', [])
                        exchange_order_ids = [o.get('id') for o in exchange_orders]
                        
                        if order_id not in exchange_order_ids:
                            # Order not on exchange, mark as failed
                            update_data = {
                                'status': 'failed',
                                'error_message': 'STALE_CLEANUP: Order not found on exchange after 1 hour'
                            }
                            
                            update_response = await client.put(f"http://localhost:8002/api/v1/orders/{order_id}", json=update_data)
                            if update_response.status_code == 200:
                                print(f"🗑️  Marked stale order {order_id} as failed")
                            
        except Exception as e:
            print(f"❌ Error cleaning up stale orders: {e}")
    
    print(f"\n✅ Limit order fixes completed!")
    print(f"\n📋 Summary of fixes needed in orchestrator code:")
    print(f"1. 🎯 Improve limit order pricing to be more competitive")
    print(f"2. 🔗 Store actual exchange_order_id in database")
    print(f"3. 🔄 Add periodic order status synchronization")
    print(f"4. 📊 Add orderbook-based pricing validation")
    print(f"5. ⏰ Add timeout handling for unfilled limit orders")

if __name__ == "__main__":
    asyncio.run(fix_limit_order_issues())