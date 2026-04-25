#!/usr/bin/env python3
"""
Fix Missing 1INCH/USD Trades Recovery Script
Specifically addresses the two missing 1INCH trades from 2025-08-21
"""

import asyncio
import httpx
import logging
from datetime import datetime, timedelta
import json

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Service URLs
DATABASE_SERVICE_URL = "http://localhost:8002"
EXCHANGE_SERVICE_URL = "http://localhost:8003" 
ORCHESTRATOR_SERVICE_URL = "http://localhost:8005"

# Your specific missing trades
MISSING_TRADES = [
    {
        "order_id": "6530...2184",
        "partial_order_id": "65302184",
        "timestamp": "2025-08-21T12:22:15Z",
        "symbol": "1INCH/USD",
        "side": "buy",
        "amount": 396.0,
        "price": 0.25284,
        "value": 100.12464,
        "status": "filled"
    },
    {
        "order_id": "6530...4924", 
        "partial_order_id": "65304924",
        "timestamp": "2025-08-21T12:21:17Z",
        "symbol": "1INCH/USD",
        "side": "buy",
        "amount": 396.0,
        "price": 0.25264,
        "value": 100.04544,
        "status": "filled"
    }
]

async def check_database_for_trades():
    """Check which trades exist in database"""
    logger.info("🔍 Checking database for existing 1INCH/USD trades...")
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Check recent trades in database
            response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/trades")
            
            if response.status_code == 200:
                trades = response.json().get('trades', [])
                
                # Filter for 1INCH trades around that time
                oneinch_trades = [
                    t for t in trades 
                    if t.get('pair') == '1INCH/USD' and 
                    t.get('entry_time', '').startswith('2025-08-21')
                ]
                
                logger.info(f"Found {len(oneinch_trades)} 1INCH/USD trades in database for 2025-08-21:")
                for trade in oneinch_trades:
                    logger.info(f"  - Trade ID: {trade.get('trade_id', 'N/A')}")
                    logger.info(f"    Entry Time: {trade.get('entry_time', 'N/A')}")
                    logger.info(f"    Amount: {trade.get('position_size', 'N/A')}")
                    logger.info(f"    Price: {trade.get('entry_price', 'N/A')}")
                    logger.info(f"    Status: {trade.get('status', 'N/A')}")
                    logger.info("  ---")
                
                return oneinch_trades
                
    except Exception as e:
        logger.error(f"Error checking database: {e}")
        return []

async def check_exchange_for_orders():
    """Check exchange for the actual order history"""
    logger.info("🔍 Checking exchange for 1INCH/USD order history...")
    
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            # Get order history from exchange service
            response = await client.get(f"{EXCHANGE_SERVICE_URL}/api/v1/trading/orders/cryptocom")
            
            if response.status_code == 200:
                orders = response.json().get('orders', [])
                
                # Filter for 1INCH orders on 2025-08-21
                oneinch_orders = [
                    o for o in orders
                    if '1INCH' in str(o.get('symbol', '')) and
                    str(o.get('timestamp', '')).startswith('2025-08-21')
                ]
                
                logger.info(f"Found {len(oneinch_orders)} 1INCH orders on exchange for 2025-08-21:")
                for order in oneinch_orders:
                    logger.info(f"  - Order ID: {order.get('id', 'N/A')}")
                    logger.info(f"    Symbol: {order.get('symbol', 'N/A')}")
                    logger.info(f"    Timestamp: {order.get('timestamp', 'N/A')}")
                    logger.info(f"    Amount: {order.get('amount', 'N/A')}")
                    logger.info(f"    Price: {order.get('price', 'N/A')} / Avg: {order.get('average', 'N/A')}")
                    logger.info(f"    Filled: {order.get('filled', 'N/A')}")
                    logger.info(f"    Status: {order.get('status', 'N/A')}")
                    logger.info("  ---")
                    
                return oneinch_orders
                
    except Exception as e:
        logger.error(f"Error checking exchange: {e}")
        return []

async def force_order_sync():
    """Force a full order synchronization"""
    logger.info("🔄 Forcing order synchronization...")
    
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            # Trigger reconciliation for Crypto.com
            response = await client.post(f"http://localhost:8008/api/v1/sync/reconcile/cryptocom")
            
            if response.status_code == 200:
                result = response.json()
                logger.info(f"✅ Reconciliation completed: {result}")
                return result
            else:
                logger.error(f"❌ Reconciliation failed: {response.status_code} - {response.text}")
                
    except Exception as e:
        logger.error(f"Error forcing sync: {e}")
        
    return None

async def create_missing_trade_records():
    """Create missing trade records based on exchange data"""
    logger.info("🛠️ Creating missing trade records...")
    
    # Get actual exchange data first
    exchange_orders = await check_exchange_for_orders()
    
    for missing_trade in MISSING_TRADES:
        logger.info(f"Processing missing trade for order {missing_trade['partial_order_id']}...")
        
        # Find matching exchange order
        matching_order = None
        for order in exchange_orders:
            if missing_trade['partial_order_id'] in str(order.get('id', '')):
                matching_order = order
                break
                
        if not matching_order:
            logger.warning(f"⚠️ Could not find exchange order for {missing_trade['partial_order_id']}")
            continue
            
        # Create trade record
        import uuid
        trade_id = str(uuid.uuid4())
        
        trade_data = {
            'trade_id': trade_id,
            'pair': '1INCH/USD',
            'exchange': 'cryptocom',
            'status': 'CLOSED',  # Since these are filled orders
            'position_size': float(matching_order.get('filled', missing_trade['amount'])),
            'entry_price': float(matching_order.get('average') or matching_order.get('price', missing_trade['price'])),
            'entry_time': matching_order.get('timestamp', missing_trade['timestamp']),
            'entry_id': matching_order.get('id'),
            'exit_time': matching_order.get('timestamp'),  # Assuming immediate fill for market orders
            'exit_price': float(matching_order.get('average') or matching_order.get('price', missing_trade['price'])),
            'realized_pnl': 0.0,  # No PnL for immediate fills
            'fees': matching_order.get('fee', {}).get('cost', 0) if matching_order.get('fee') else 0,
            'strategy': 'recovery',
            'entry_reason': f'Recovery script - Missing trade from exchange order {matching_order.get("id")}',
            'exit_reason': 'Immediate market order fill',
            'created_at': datetime.utcnow().isoformat(),
            'updated_at': datetime.utcnow().isoformat()
        }
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(f"{DATABASE_SERVICE_URL}/api/v1/trades", json=trade_data)
                
                if response.status_code in [200, 201]:
                    logger.info(f"✅ Created trade record: {trade_id}")
                else:
                    logger.error(f"❌ Failed to create trade: {response.status_code} - {response.text}")
                    
        except Exception as e:
            logger.error(f"Error creating trade record: {e}")

async def main():
    """Main recovery process"""
    logger.info("🚀 Starting 1INCH/USD Trade Recovery Process")
    logger.info("=" * 60)
    
    # Step 1: Check current database state
    db_trades = await check_database_for_trades()
    
    # Step 2: Check exchange state  
    exchange_orders = await check_exchange_for_orders()
    
    # Step 3: Force synchronization
    sync_result = await force_order_sync()
    
    # Step 4: Check again after sync
    logger.info("\n🔄 Checking database again after sync...")
    db_trades_after = await check_database_for_trades()
    
    # Step 5: Create missing records if still missing
    if len(db_trades_after) < len(exchange_orders):
        logger.info(f"\n⚠️ Still missing trades: DB has {len(db_trades_after)}, Exchange has {len(exchange_orders)}")
        await create_missing_trade_records()
        
        # Final check
        logger.info("\n🔍 Final database check...")
        final_trades = await check_database_for_trades()
        logger.info(f"✅ Final result: {len(final_trades)} trades in database")
    else:
        logger.info("✅ All trades properly synchronized!")
    
    logger.info("=" * 60)
    logger.info("🏁 Recovery process completed")

if __name__ == "__main__":
    asyncio.run(main())