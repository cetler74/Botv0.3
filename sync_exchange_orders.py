#!/usr/bin/env python3
"""
Critical Order Synchronization Script
Synchronizes successful exchange orders back to the database
"""
import asyncio
import httpx
import json
from datetime import datetime
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def sync_exchange_orders():
    """Synchronize all exchange orders with the database"""
    
    logger.info("🔄 Starting critical order synchronization...")
    
    exchanges = ['cryptocom', 'binance', 'bybit']
    total_synced = 0
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        
        for exchange in exchanges:
            logger.info(f"\n📊 Processing {exchange} exchange...")
            
            try:
                # Get all orders from exchange (both open and filled)
                exchange_response = await client.get(f"http://localhost:8003/api/v1/trading/orders/{exchange}")
                if exchange_response.status_code != 200:
                    logger.error(f"❌ Failed to get {exchange} orders: {exchange_response.status_code}")
                    continue
                
                exchange_orders = exchange_response.json().get('orders', [])
                logger.info(f"📈 Found {len(exchange_orders)} orders on {exchange}")
                
                # Also get filled orders (if exchange supports it)
                try:
                    # Try to get trading history
                    history_response = await client.get(f"http://localhost:8003/api/v1/trading/history/{exchange}")
                    if history_response.status_code == 200:
                        filled_orders = history_response.json().get('orders', [])
                        logger.info(f"📜 Found {len(filled_orders)} filled orders in {exchange} history")
                        exchange_orders.extend(filled_orders)
                except Exception as e:
                    logger.warning(f"⚠️  Could not get {exchange} history: {e}")
                
                # Get existing database orders for this exchange
                db_response = await client.get(f"http://localhost:8002/api/v1/orders?exchange={exchange}")
                if db_response.status_code != 200:
                    logger.error(f"❌ Failed to get database orders for {exchange}")
                    continue
                
                db_orders = db_response.json().get('orders', [])
                db_order_ids = set(order.get('exchange_order_id') or order.get('order_id') for order in db_orders)
                logger.info(f"💾 Found {len(db_orders)} orders in database for {exchange}")
                
                synced_count = 0
                
                # Process each exchange order
                for ex_order in exchange_orders:
                    exchange_order_id = ex_order.get('id')
                    if not exchange_order_id:
                        continue
                        
                    # Check if this order is already in database
                    if exchange_order_id in db_order_ids:
                        # Update existing order status if needed
                        existing_order = next((o for o in db_orders if 
                                             (o.get('exchange_order_id') == exchange_order_id or 
                                              o.get('order_id') == exchange_order_id)), None)
                        
                        if existing_order:
                            ex_status = ex_order.get('status', 'unknown')
                            db_status = existing_order.get('status', 'unknown')
                            
                            # Map exchange status to database status
                            status_mapping = {
                                'open': 'pending',
                                'filled': 'filled',
                                'closed': 'filled', 
                                'canceled': 'cancelled',
                                'cancelled': 'cancelled',
                                'expired': 'failed',
                                'rejected': 'failed'
                            }
                            
                            mapped_status = status_mapping.get(ex_status.lower(), ex_status)
                            
                            if mapped_status != db_status and mapped_status in ['filled', 'cancelled', 'failed']:
                                logger.info(f"🔄 Updating order {exchange_order_id}: {db_status} -> {mapped_status}")
                                
                                update_data = {
                                    'status': mapped_status,
                                    'filled_amount': ex_order.get('filled', ex_order.get('amount', 0)),
                                    'filled_price': ex_order.get('average', ex_order.get('price')),
                                    'updated_at': datetime.utcnow().isoformat() + 'Z'
                                }
                                
                                # Add fees if available
                                if 'fee' in ex_order and ex_order['fee']:
                                    update_data['fees'] = ex_order['fee'].get('cost', 0)
                                    update_data['fee_rate'] = ex_order['fee'].get('rate', 0)
                                
                                # Update the order
                                update_response = await client.put(
                                    f"http://localhost:8002/api/v1/orders/{existing_order['order_id']}", 
                                    json=update_data
                                )
                                
                                if update_response.status_code == 200:
                                    logger.info(f"✅ Updated order {exchange_order_id}")
                                    synced_count += 1
                                else:
                                    logger.error(f"❌ Failed to update order {exchange_order_id}: {update_response.status_code}")
                        continue
                    
                    # This is a new order not in database - add it with unique ID
                    logger.info(f"🆕 Adding missing order {exchange_order_id} ({ex_order.get('symbol')}) to database")
                    
                    # Map exchange order to database format
                    ex_status = ex_order.get('status', 'unknown')
                    status_mapping = {
                        'open': 'pending',
                        'filled': 'filled',
                        'closed': 'filled',
                        'canceled': 'cancelled', 
                        'cancelled': 'cancelled',
                        'expired': 'failed',
                        'rejected': 'failed'
                    }
                    
                    mapped_status = status_mapping.get(ex_status.lower(), 'pending')
                    
                    # Use exchange_order_id as order_id to match for cancellation logic
                    # For filled orders from exchange history, we need exact ID matching
                    order_data = {
                        'order_id': exchange_order_id,
                        'exchange_order_id': exchange_order_id,
                        'exchange': exchange,
                        'symbol': ex_order.get('symbol', 'UNKNOWN'),
                        'order_type': ex_order.get('type', 'limit').lower(),
                        'side': ex_order.get('side', 'buy').lower(),
                        'amount': float(ex_order.get('amount', 0)),
                        'price': float(ex_order.get('price', 0)) if ex_order.get('price') else None,
                        'filled_amount': float(ex_order.get('filled', 0)),
                        'filled_price': float(ex_order.get('average', 0)) if ex_order.get('average') else None,
                        'status': mapped_status,
                        'created_at': datetime.fromtimestamp(ex_order.get('timestamp', 0) / 1000).isoformat() + 'Z' if ex_order.get('timestamp') else datetime.utcnow().isoformat() + 'Z'
                    }
                    
                    # Add fees if available
                    if 'fee' in ex_order and ex_order['fee']:
                        order_data['fees'] = ex_order['fee'].get('cost', 0)
                        order_data['fee_rate'] = ex_order['fee'].get('rate', 0)
                    
                    # Add to database with unique ID
                    add_response = await client.post("http://localhost:8002/api/v1/orders", json=order_data)
                    if add_response.status_code == 200:
                        logger.info(f"✅ Added order {unique_order_id} -> {exchange_order_id} ({mapped_status})")
                        synced_count += 1
                    else:
                        logger.error(f"❌ Failed to add order {unique_order_id}: {add_response.status_code}")
                        if add_response.status_code in [422, 500]:
                            logger.error(f"   Error details: {add_response.text}")
                
                logger.info(f"🎯 {exchange}: Synced {synced_count} orders")
                total_synced += synced_count
                
            except Exception as e:
                logger.error(f"❌ Error processing {exchange}: {e}")
    
    logger.info(f"\n✅ Order synchronization completed!")
    logger.info(f"🎯 Total orders synchronized: {total_synced}")
    
    # Verify the sync worked
    logger.info(f"\n📊 Verification - checking updated order counts...")
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.get("http://localhost:8002/api/v1/orders")
            if response.status_code == 200:
                orders = response.json().get('orders', [])
                limit_orders = [o for o in orders if o.get('order_type') == 'limit']
                market_orders = [o for o in orders if o.get('order_type') == 'market']
                filled_orders = [o for o in orders if o.get('status') == 'filled']
                
                logger.info(f"📈 Database now contains:")
                logger.info(f"   Total orders: {len(orders)}")
                logger.info(f"   Limit orders: {len(limit_orders)}")
                logger.info(f"   Market orders: {len(market_orders)}")
                logger.info(f"   Filled orders: {len(filled_orders)}")
                
                if limit_orders:
                    filled_limits = [o for o in limit_orders if o.get('status') == 'filled']
                    success_rate = len(filled_limits) / len(limit_orders) * 100
                    logger.info(f"   Limit order success rate: {success_rate:.1f}%")
                    
        except Exception as e:
            logger.error(f"❌ Verification error: {e}")

if __name__ == "__main__":
    asyncio.run(sync_exchange_orders())