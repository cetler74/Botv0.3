#!/usr/bin/env python3
"""
CRITICAL FIX: ADA/USDC Order Tracking Issue
The following order was filled on Binance but still shows as OPEN in the database:

Exchange Data:
- Time: 2025-08-29 11:31:12
- Pair: ADA/USDC
- Type: Limit Sell
- Price: 0.8249
- Quantity: 250.7
- Status: Filled
- Value: 206.80243 USDC

Database Record:
- Trade ID: 08227eff...
- Status: OPEN (should be CLOSED)
- Exit Price: $0.8246 (trailing stop)
- Position Size: 251.000000

This script will fix the synchronization issue and close the trade properly.
"""

import asyncio
import httpx
import logging
from datetime import datetime
from typing import Dict, Any, Optional

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Service URLs
DATABASE_SERVICE_URL = "http://localhost:8002"
EXCHANGE_SERVICE_URL = "http://localhost:8003"

# ADA/USDC Order Details from Exchange
ADA_ORDER_DETAILS = {
    "exchange_order_id": "1205895767",  # From database record
    "pair": "ADA/USDC",
    "exchange": "binance",
    "fill_time": "2025-08-29 11:31:12",
    "fill_price": 0.8249,
    "fill_quantity": 250.7,
    "fill_value": 206.80243,
    "order_type": "LIMIT",
    "side": "SELL",
    "status": "FILLED"
}

async def check_database_connection():
    """Check if database service is available"""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{DATABASE_SERVICE_URL}/health")
            if response.status_code == 200:
                logger.info("✅ Database service is healthy")
                return True
            else:
                logger.error(f"❌ Database service returned {response.status_code}")
                return False
    except Exception as e:
        logger.error(f"❌ Cannot connect to database service: {e}")
        return False

async def find_ada_trade() -> Optional[Dict[str, Any]]:
    """Find the specific ADA/USDC trade in the database"""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Search for ADA/USDC trades on Binance
            response = await client.get(
                f"{DATABASE_SERVICE_URL}/api/v1/trades",
                params={
                    "pair": "ADA/USDC",
                    "exchange": "binance",
                    "limit": 50
                }
            )
            
            if response.status_code != 200:
                logger.error(f"❌ Failed to get trades: {response.status_code}")
                return None
            
            trades = response.json().get('trades', [])
            logger.info(f"📊 Found {len(trades)} ADA/USDC trades on Binance")
            
            # Look for the specific trade that should be closed
            for trade in trades:
                trade_id = trade.get('trade_id', '')
                status = trade.get('status', '')
                entry_id = trade.get('entry_id', '')
                position_size = trade.get('position_size', 0)
                
                # Match based on trade ID pattern and position size
                if (trade_id.startswith('08227eff') or 
                    abs(position_size - 251.0) < 0.1 or  # Close to 251.0
                    entry_id == ADA_ORDER_DETAILS["exchange_order_id"]):
                    
                    logger.info(f"🎯 Found matching trade:")
                    logger.info(f"   Trade ID: {trade_id}")
                    logger.info(f"   Status: {status}")
                    logger.info(f"   Entry ID: {entry_id}")
                    logger.info(f"   Position Size: {position_size}")
                    logger.info(f"   Entry Price: {trade.get('entry_price')}")
                    logger.info(f"   Exit Price: {trade.get('exit_price')}")
                    logger.info(f"   Strategy: {trade.get('strategy')}")
                    
                    return trade
            
            logger.warning("⚠️  Could not find the specific ADA/USDC trade")
            return None
            
    except Exception as e:
        logger.error(f"❌ Error finding ADA trade: {e}")
        return None

async def close_ada_trade(trade: Dict[str, Any]) -> bool:
    """Close the ADA/USDC trade with the correct exit information"""
    try:
        trade_id = trade['trade_id']
        
        # Parse the fill time
        fill_time = datetime.strptime(ADA_ORDER_DETAILS["fill_time"], "%Y-%m-%d %H:%M:%S")
        
        # Calculate fees (estimate based on typical Binance fees)
        fill_value = ADA_ORDER_DETAILS["fill_value"]
        estimated_fee = fill_value * 0.001  # 0.1% fee estimate
        
        # Calculate realized PnL
        entry_price = float(trade.get('entry_price', 0))
        exit_price = ADA_ORDER_DETAILS["fill_price"]
        position_size = float(trade.get('position_size', 0))
        
        if entry_price > 0:
            realized_pnl = (exit_price - entry_price) * position_size - estimated_fee
        else:
            realized_pnl = 0.0
        
        # Prepare update data
        update_data = {
            "status": "CLOSED",
            "exit_price": exit_price,
            "exit_time": fill_time.isoformat(),
            "exit_id": ADA_ORDER_DETAILS["exchange_order_id"],
            "exit_reason": f"Limit sell order filled at {exit_price} (exchange confirmed)",
            "realized_pnl": realized_pnl,
            "fees": estimated_fee,
            "updated_at": datetime.utcnow().isoformat()
        }
        
        logger.info(f"🔄 Updating trade {trade_id[:8]}...")
        logger.info(f"   Status: {trade.get('status')} → CLOSED")
        logger.info(f"   Exit Price: {exit_price}")
        logger.info(f"   Exit Time: {fill_time}")
        logger.info(f"   Realized PnL: ${realized_pnl:.2f}")
        logger.info(f"   Fees: ${estimated_fee:.4f}")
        
        # Update the trade
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.put(
                f"{DATABASE_SERVICE_URL}/api/v1/trades/{trade_id}",
                json=update_data
            )
            
            if response.status_code == 200:
                logger.info(f"✅ Successfully closed trade {trade_id[:8]}")
                return True
            else:
                logger.error(f"❌ Failed to close trade: {response.status_code} - {response.text}")
                return False
                
    except Exception as e:
        logger.error(f"❌ Error closing ADA trade: {e}")
        return False

async def verify_trade_closure(trade_id: str) -> bool:
    """Verify that the trade was properly closed"""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/trades/{trade_id}")
            
            if response.status_code == 200:
                trade = response.json()
                status = trade.get('status')
                
                if status == 'CLOSED':
                    logger.info(f"✅ Verification successful: Trade {trade_id[:8]} is now CLOSED")
                    logger.info(f"   Exit Price: {trade.get('exit_price')}")
                    logger.info(f"   Realized PnL: ${trade.get('realized_pnl', 0):.2f}")
                    return True
                else:
                    logger.error(f"❌ Trade {trade_id[:8]} still shows status: {status}")
                    return False
            else:
                logger.error(f"❌ Failed to verify trade: {response.status_code}")
                return False
                
    except Exception as e:
        logger.error(f"❌ Error verifying trade closure: {e}")
        return False

async def sync_with_exchange():
    """Sync the order status with the exchange to ensure consistency"""
    try:
        logger.info("🔄 Syncing with Binance exchange...")
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Get current orders from Binance
            response = await client.get(f"{EXCHANGE_SERVICE_URL}/api/v1/trading/orders/binance")
            
            if response.status_code == 200:
                orders = response.json().get('orders', [])
                ada_orders = [o for o in orders if o.get('symbol') == 'ADA/USDC']
                
                logger.info(f"📊 Found {len(ada_orders)} ADA/USDC orders on Binance")
                
                for order in ada_orders:
                    order_id = order.get('id')
                    status = order.get('status')
                    logger.info(f"   Order {order_id}: {status}")
                
                # Check if our specific order is still showing as open
                specific_order = next((o for o in ada_orders if o.get('id') == ADA_ORDER_DETAILS["exchange_order_id"]), None)
                
                if specific_order:
                    if specific_order.get('status') == 'FILLED':
                        logger.info("✅ Exchange confirms order is FILLED - database sync is correct")
                    else:
                        logger.warning(f"⚠️  Exchange shows order status: {specific_order.get('status')}")
                else:
                    logger.info("✅ Order not found in open orders (expected if filled)")
                    
            else:
                logger.warning(f"⚠️  Could not sync with exchange: {response.status_code}")
                
    except Exception as e:
        logger.error(f"❌ Error syncing with exchange: {e}")

async def main():
    """Main function to fix the ADA/USDC order tracking issue"""
    logger.info("🚨 CRITICAL FIX: ADA/USDC Order Tracking Issue")
    logger.info("=" * 60)
    logger.info(f"Exchange Order: {ADA_ORDER_DETAILS['exchange_order_id']}")
    logger.info(f"Fill Time: {ADA_ORDER_DETAILS['fill_time']}")
    logger.info(f"Fill Price: ${ADA_ORDER_DETAILS['fill_price']}")
    logger.info(f"Fill Quantity: {ADA_ORDER_DETAILS['fill_quantity']}")
    logger.info("=" * 60)
    
    # Check database connection
    if not await check_database_connection():
        logger.error("❌ Cannot proceed without database connection")
        return
    
    # Find the specific trade
    trade = await find_ada_trade()
    if not trade:
        logger.error("❌ Could not find the ADA/USDC trade to fix")
        return
    
    # Close the trade
    if await close_ada_trade(trade):
        # Verify the closure
        if await verify_trade_closure(trade['trade_id']):
            logger.info("✅ ADA/USDC order tracking issue FIXED successfully!")
            
            # Sync with exchange to ensure consistency
            await sync_with_exchange()
            
            logger.info("🎉 Critical fix completed!")
        else:
            logger.error("❌ Trade closure verification failed")
    else:
        logger.error("❌ Failed to close the ADA/USDC trade")

if __name__ == "__main__":
    asyncio.run(main())
