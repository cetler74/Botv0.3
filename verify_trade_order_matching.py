#!/usr/bin/env python3
"""
Verify trade IDs and entry/exit IDs match exchange order IDs
"""

import asyncio
import httpx
import json
from datetime import datetime

async def verify_trade_order_matching():
    """Verify that database trade IDs match exchange order IDs"""
    
    async with httpx.AsyncClient() as client:
        try:
            print("🔍 VERIFYING TRADE ORDER ID MATCHING")
            print("=" * 60)
            
            # 1. Get all trades from database
            print("1. DATABASE TRADES:")
            trades_response = await client.get("http://localhost:8002/api/v1/trades")
            if trades_response.status_code != 200:
                print(f"❌ Failed to get trades: {trades_response.status_code}")
                return
            
            trades_data = trades_response.json()
            all_trades = trades_data.get('trades', [])
            
            print(f"📋 Found {len(all_trades)} total trades in database")
            
            # Group trades by exchange
            trades_by_exchange = {}
            for trade in all_trades:
                exchange = trade.get('exchange', 'unknown')
                if exchange not in trades_by_exchange:
                    trades_by_exchange[exchange] = []
                trades_by_exchange[exchange].append(trade)
            
            # 2. Check each exchange
            for exchange, trades in trades_by_exchange.items():
                print(f"\n2. EXCHANGE: {exchange.upper()}")
                print("-" * 40)
                
                # Get exchange orders
                try:
                    orders_response = await client.get(f"http://localhost:8003/api/v1/orders/{exchange}")
                    if orders_response.status_code != 200:
                        print(f"❌ Failed to get orders for {exchange}: {orders_response.status_code}")
                        continue
                    
                    orders_data = orders_response.json()
                    exchange_orders = orders_data.get('orders', [])
                    
                    print(f"📊 Exchange orders: {len(exchange_orders)}")
                    print(f"📊 Database trades: {len(trades)}")
                    
                    # Create lookup for exchange orders
                    exchange_order_ids = set()
                    for order in exchange_orders:
                        order_id = order.get('id') or order.get('order_id')
                        if order_id:
                            exchange_order_ids.add(str(order_id))
                    
                    print(f"📊 Unique exchange order IDs: {len(exchange_order_ids)}")
                    
                    # Check database trades against exchange orders
                    matched_trades = 0
                    unmatched_trades = 0
                    missing_orders = []
                    
                    for trade in trades:
                        entry_id = trade.get('entry_id')
                        exit_id = trade.get('exit_id')
                        
                        entry_matched = entry_id in exchange_order_ids if entry_id else False
                        exit_matched = exit_id in exchange_order_ids if exit_id else False
                        
                        if entry_matched and (not exit_id or exit_matched):
                            matched_trades += 1
                        else:
                            unmatched_trades += 1
                            missing_orders.append({
                                'trade_id': trade['trade_id'],
                                'pair': trade['pair'],
                                'entry_id': entry_id,
                                'exit_id': exit_id,
                                'status': trade['status'],
                                'entry_matched': entry_matched,
                                'exit_matched': exit_matched
                            })
                    
                    print(f"✅ Matched trades: {matched_trades}")
                    print(f"❌ Unmatched trades: {unmatched_trades}")
                    
                    if unmatched_trades > 0:
                        print(f"\n🔍 UNMATCHED TRADES:")
                        for trade in missing_orders[:10]:  # Show first 10
                            print(f"   Trade: {trade['trade_id'][:8]}...")
                            print(f"   Pair: {trade['pair']}")
                            print(f"   Entry ID: {trade['entry_id']} ({'✅' if trade['entry_matched'] else '❌'})")
                            print(f"   Exit ID: {trade['exit_id']} ({'✅' if trade['exit_matched'] else '❌'})")
                            print(f"   Status: {trade['status']}")
                            print("-" * 30)
                        
                        if len(missing_orders) > 10:
                            print(f"   ... and {len(missing_orders) - 10} more")
                    
                    # Check for orphaned exchange orders (orders without corresponding trades)
                    trade_entry_ids = set()
                    trade_exit_ids = set()
                    
                    for trade in trades:
                        if trade.get('entry_id'):
                            trade_entry_ids.add(str(trade['entry_id']))
                        if trade.get('exit_id'):
                            trade_exit_ids.add(str(trade['exit_id']))
                    
                    orphaned_orders = exchange_order_ids - trade_entry_ids - trade_exit_ids
                    
                    if orphaned_orders:
                        print(f"\n⚠️  ORPHANED EXCHANGE ORDERS ({len(orphaned_orders)}):")
                        for order_id in list(orphaned_orders)[:5]:  # Show first 5
                            print(f"   Order ID: {order_id}")
                        if len(orphaned_orders) > 5:
                            print(f"   ... and {len(orphaned_orders) - 5} more")
                    
                except Exception as e:
                    print(f"❌ Error checking {exchange}: {e}")
            
            # 3. Specific check for A2Z trades
            print(f"\n3. A2Z TRADE ANALYSIS:")
            print("-" * 40)
            
            a2z_trades = [t for t in all_trades if t['pair'] == 'A2Z/USD']
            print(f"📋 A2Z trades in database: {len(a2z_trades)}")
            
            for trade in a2z_trades:
                print(f"   Trade ID: {trade['trade_id'][:8]}...")
                print(f"   Entry ID: {trade.get('entry_id', 'None')}")
                print(f"   Exit ID: {trade.get('exit_id', 'None')}")
                print(f"   Status: {trade['status']}")
                print(f"   Position: {trade['position_size']}")
                print("-" * 20)
            
            # 4. Check recent orders from exchange
            print(f"\n4. RECENT EXCHANGE ORDERS:")
            print("-" * 40)
            
            try:
                recent_orders_response = await client.get("http://localhost:8003/api/v1/orders/cryptocom?limit=20")
                if recent_orders_response.status_code == 200:
                    recent_orders = recent_orders_response.json().get('orders', [])
                    
                    print(f"📊 Recent orders from exchange: {len(recent_orders)}")
                    
                    for order in recent_orders[:10]:  # Show first 10
                        print(f"   Order ID: {order.get('id', 'N/A')}")
                        print(f"   Symbol: {order.get('symbol', 'N/A')}")
                        print(f"   Side: {order.get('side', 'N/A')}")
                        print(f"   Status: {order.get('status', 'N/A')}")
                        print(f"   Amount: {order.get('amount', 'N/A')}")
                        print("-" * 15)
                else:
                    print(f"❌ Failed to get recent orders: {recent_orders_response.status_code}")
                    
            except Exception as e:
                print(f"❌ Error getting recent orders: {e}")
            
        except Exception as e:
            print(f"❌ Error: {e}")
            import traceback
            print(f"Full traceback: {traceback.format_exc()}")

if __name__ == "__main__":
    asyncio.run(verify_trade_order_matching())
