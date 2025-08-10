#!/usr/bin/env python3
"""
Import missing orders from Crypto.com exchange data into the database
This script manually imports orders that were placed but not recorded due to sync issues
"""

import asyncio
import httpx
import uuid
from datetime import datetime
from typing import List, Dict, Any

# Database service URL
DATABASE_SERVICE_URL = "http://localhost:8002"

# Order data from Crypto.com exchange
MISSING_ORDERS = [
    {
        "time": "2025-07-23 15:09:55",
        "utc_time": "2025-07-23 14:09:55 UTC",
        "instrument": "AAVE/USD",
        "type": "Market",
        "side": "Buy",
        "price": None,  # Market order
        "quantity": 0.171,
        "completed": 0.171,
        "remaining": 0.000,
        "average_price": 291.064,
        "order_value": 52.250076,
        "order_id": "6530219581692826242",
        "status": "Filled"
    },
    {
        "time": "2025-07-23 15:08:31",
        "utc_time": "2025-07-23 14:08:31 UTC",
        "instrument": "AAVE/USD",
        "type": "Market",
        "side": "Buy",
        "price": None,
        "quantity": 0.171,
        "completed": 0.171,
        "remaining": 0.000,
        "average_price": 291.751,
        "order_value": 52.364988,
        "order_id": "6530219581692817007",
        "status": "Filled"
    },
    {
        "time": "2025-07-23 15:07:01",
        "utc_time": "2025-07-23 14:07:01 UTC",
        "instrument": "AAVE/USD",
        "type": "Market",
        "side": "Buy",
        "price": None,
        "quantity": 0.171,
        "completed": 0.171,
        "remaining": 0.000,
        "average_price": 292.156,
        "order_value": 52.442109,
        "order_id": "6530219581692806090",
        "status": "Filled"
    },
    {
        "time": "2025-07-23 15:04:52",
        "utc_time": "2025-07-23 14:04:52 UTC",
        "instrument": "1INCH/USD",
        "type": "Market",
        "side": "Buy",
        "price": None,
        "quantity": 169.0,
        "completed": 169.0,
        "remaining": 0.0,
        "average_price": 0.29577,
        "order_value": 52.48295,
        "order_id": "6530219581692767925",
        "status": "Filled"
    },
    {
        "time": "2025-07-23 15:03:12",
        "utc_time": "2025-07-23 14:03:12 UTC",
        "instrument": "1INCH/USD",
        "type": "Market",
        "side": "Buy",
        "price": None,
        "quantity": 169.0,
        "completed": 169.0,
        "remaining": 0.0,
        "average_price": 0.29551,
        "order_value": 52.43732,
        "order_id": "6530219581692733298",
        "status": "Filled"
    },
    {
        "time": "2025-07-23 15:00:00",
        "utc_time": "2025-07-23 14:00:00 UTC",
        "instrument": "AAVE/USD",
        "type": "Market",
        "side": "Buy",
        "price": None,
        "quantity": 0.172,
        "completed": 0.172,
        "remaining": 0.000,
        "average_price": 289.784,
        "order_value": 52.302620,
        "order_id": "6530219581692698018",
        "status": "Filled"
    }
]

async def check_database_connection():
    """Check if database service is available"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{DATABASE_SERVICE_URL}/health")
            if response.status_code == 200:
                print("✓ Database service is healthy")
                return True
            else:
                print(f"✗ Database service returned {response.status_code}")
                return False
    except Exception as e:
        print(f"✗ Cannot connect to database service: {e}")
        return False

async def check_existing_order(order_id: str) -> bool:
    """Check if order already exists in database"""
    try:
        async with httpx.AsyncClient() as client:
            # Check if trade with entry_id exists
            response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/trades")
            if response.status_code == 200:
                trades = response.json().get('trades', [])
                for trade in trades:
                    if trade.get('entry_id') == order_id:
                        return True
        return False
    except Exception as e:
        print(f"Error checking existing order {order_id}: {e}")
        return False

async def create_trade_from_order(order: Dict[str, Any]) -> bool:
    """Create a trade record from order data"""
    try:
        # Parse UTC time
        order_time = datetime.fromisoformat(order['utc_time'].replace(' UTC', ''))
        
        # Generate unique trade ID
        trade_id = str(uuid.uuid4())
        
        # Convert instrument format (AAVE/USD -> AAVEUSD for consistency)
        symbol = order['instrument'].replace('/', '')
        
        # Create trade data with entry_id included
        trade_data = {
            "trade_id": trade_id,
            "pair": symbol,
            "exchange": "cryptocom",
            "entry_price": float(order['average_price']),
            "exit_price": None,
            "status": "OPEN",  # Start as OPEN so it can be monitored for exit
            "position_size": float(order['completed']),
            "strategy": "exchange_import",
            "entry_time": order_time.isoformat(),
            "exit_time": None,
            "pnl": None,
            "entry_reason": f"imported_order_{order['order_id']}",
            "exit_reason": None,
            "fees": None,
            "unrealized_pnl": None,
            "realized_pnl": None,
            "highest_price": float(order['average_price']),  # Initialize with entry price
            "profit_protection": None,
            "profit_protection_trigger": None,
            "trail_stop": None,
            "trail_stop_trigger": None,
            "current_price": float(order['average_price']),
            "entry_id": order['order_id']  # Include entry_id for proper tracking
        }
        
        # Send to database service
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{DATABASE_SERVICE_URL}/api/v1/trades", json=trade_data)
            
            if response.status_code == 200:
                result = response.json()
                print(f"✓ Created trade {trade_id} for order {order['order_id']} ({symbol}) with entry_id")
                return True
            else:
                error_text = response.text
                print(f"✗ Failed to create trade for order {order['order_id']}: {response.status_code} - {error_text}")
                return False
                
    except Exception as e:
        print(f"✗ Error creating trade from order {order.get('order_id', 'unknown')}: {e}")
        return False

async def verify_import():
    """Verify that orders were imported correctly"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/trades/open?exchange=cryptocom")
            if response.status_code == 200:
                trades = response.json().get('trades', [])
                imported_trades = [t for t in trades if t.get('entry_reason', '').startswith('imported_order_')]
                print(f"\n✓ Found {len(imported_trades)} imported trades in database")
                
                for trade in imported_trades:
                    print(f"  - {trade['pair']}: {trade['position_size']} @ {trade['entry_price']} (ID: {trade['trade_id']})")
                
                return len(imported_trades)
            else:
                print(f"✗ Failed to verify import: {response.status_code}")
                return 0
    except Exception as e:
        print(f"✗ Error verifying import: {e}")
        return 0

async def main():
    """Main import function"""
    print("🔄 Starting import of missing Crypto.com orders...")
    
    # Check database connection
    if not await check_database_connection():
        print("❌ Cannot proceed without database connection")
        return
    
    print(f"\n📋 Processing {len(MISSING_ORDERS)} orders...")
    
    created_count = 0
    skipped_count = 0
    failed_count = 0
    
    for order in MISSING_ORDERS:
        order_id = order['order_id']
        symbol = order['instrument']
        
        print(f"\n🔍 Processing order {order_id} ({symbol})...")
        
        # Check if already exists
        if await check_existing_order(order_id):
            print(f"⏭️  Order {order_id} already exists, skipping")
            skipped_count += 1
            continue
        
        # Create trade from order
        if await create_trade_from_order(order):
            created_count += 1
        else:
            failed_count += 1
    
    print(f"\n📊 Import Summary:")
    print(f"  ✅ Created: {created_count}")
    print(f"  ⏭️  Skipped: {skipped_count}")  
    print(f"  ❌ Failed: {failed_count}")
    
    # Verify import
    if created_count > 0:
        print(f"\n🔍 Verifying import...")
        verified_count = await verify_import()
        if verified_count == created_count:
            print(f"✅ All {created_count} orders successfully imported and verified!")
        else:
            print(f"⚠️  Expected {created_count} but found {verified_count} in database")
    
    print(f"\n✅ Import complete! These orders are now available for exit cycle monitoring.")

if __name__ == "__main__":
    asyncio.run(main())