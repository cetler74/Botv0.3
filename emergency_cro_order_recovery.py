#!/usr/bin/env python3
"""
EMERGENCY: CRO/USD Order Recovery Script
Critical issue: 2 filled limit orders on Crypto.com not recorded in database

Orders to recover:
1. CRO/USD Buy 0.30907 x 668 = $206.46 (6530...1735) - Filled at 23:22:51
2. CRO/USD Buy 0.30831 x 668 = $205.95 (6530...9663) - Filled at 23:22:48
"""

import asyncio
import aiohttp
import json
from datetime import datetime, timedelta
import logging
import uuid

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Missing order data from exchanges - CRITICAL SYSTEM FAILURE
MISSING_ORDERS = [
    # CRO/USD orders on Crypto.com (2025-08-30)
    {
        "exchange": "cryptocom",
        "exchange_order_id": "6530...1735",  # Partial ID from exchange
        "pair": "CRO/USD",
        "side": "buy",
        "type": "limit",
        "quantity": 668.0,
        "price": 0.30907,
        "filled_price": 0.30898,  # Actual fill price
        "notional_value": 206.45876,  # USD value
        "timestamp": "2025-08-30 23:22:51",
        "status": "filled"
    },
    {
        "exchange": "cryptocom",
        "exchange_order_id": "6530...9663",  # Partial ID from exchange  
        "pair": "CRO/USD",
        "side": "buy", 
        "type": "limit",
        "quantity": 668.0,
        "price": 0.30831,
        "filled_price": 0.30831,  # Same as limit price
        "notional_value": 205.95108,  # USD value
        "timestamp": "2025-08-30 23:22:48", 
        "status": "filled"
    },
    # MANA/USDC order on Bybit (2025-08-31) - ALSO MISSING!
    {
        "exchange": "bybit",
        "exchange_order_id": "28544256",  # Bybit order ID
        "pair": "MANA/USDC", 
        "side": "buy",
        "type": "limit", 
        "quantity": 260.97,  # MANA quantity
        "price": 0.2956,  # USDC per MANA
        "filled_price": 0.2956,  # Same as limit
        "notional_value": 77.142732,  # USDC value
        "timestamp": "2025-08-31 01:51:23",
        "status": "filled",
        "fees_paid": 0.19857,  # Total fees in MANA from 8 fills
        "partial_fills": 8  # Filled in 8 partial executions
    }
]

DATABASE_API_URL = "http://localhost:8002"

async def create_emergency_order_record(session, order_data):
    """Create emergency order record in database"""
    
    # Generate unique IDs for the missing orders
    order_id = f"emergency_recovery_{int(datetime.now().timestamp())}"
    trade_id = str(uuid.uuid4())
    
    # Convert timestamp to ISO format
    created_at = datetime.strptime(order_data["timestamp"], "%Y-%m-%d %H:%M:%S")
    
    # Use actual exchange from order data
    exchange = order_data.get("exchange", "cryptocom")
    
    # Calculate fees if available
    fees = order_data.get("fees_paid", 0.0)
    
    order_record = {
        "order_id": order_id,
        "trade_id": trade_id,
        "exchange": exchange,
        "symbol": order_data["pair"],
        "order_type": order_data["type"], 
        "side": order_data["side"],
        "amount": order_data["quantity"],
        "price": order_data["price"],
        "filled_amount": order_data["quantity"],  # Fully filled
        "filled_price": order_data["filled_price"],
        "status": "FILLED",
        "fees": fees,
        "created_at": created_at.isoformat() + "+00:00",
        "filled_at": created_at.isoformat() + "+00:00",
        "exchange_order_id": f"emergency_{order_data['exchange_order_id']}",
        "error_message": f"EMERGENCY_RECOVERY: {exchange} order not initially recorded in system"
    }
    
    logger.info(f"🚨 EMERGENCY: Recording missing order {order_data['exchange_order_id']} on {exchange}")
    logger.info(f"   Pair: {order_data['pair']} | Side: {order_data['side']}")  
    logger.info(f"   Amount: {order_data['quantity']} @ ${order_data['filled_price']}")
    logger.info(f"   Value: ${order_data['notional_value']:.2f} | Fees: {fees}")
    
    # Log partial fills info if available
    if "partial_fills" in order_data:
        logger.info(f"   Filled in {order_data['partial_fills']} partial executions")
    
    try:
        async with session.post(
            f"{DATABASE_API_URL}/api/v1/orders", 
            json=order_record,
            headers={"Content-Type": "application/json"}
        ) as response:
            if response.status == 200:
                result = await response.json()
                logger.info(f"✅ RECOVERY: Successfully recorded order {order_record['order_id']}")
                return result
            else:
                error_text = await response.text()
                logger.error(f"❌ RECOVERY: Failed to record order - {response.status}: {error_text}")
                return None
                
    except Exception as e:
        logger.error(f"❌ RECOVERY: Exception recording order: {e}")
        return None

async def create_emergency_trade_records(session):
    """Create corresponding trade records for the missing orders"""
    
    # Since these are buy orders, we need to create open trades
    for i, order_data in enumerate(MISSING_ORDERS):
        trade_id = str(uuid.uuid4())
        entry_time = datetime.strptime(order_data["timestamp"], "%Y-%m-%d %H:%M:%S")
        
        # Use actual exchange from order data
        exchange = order_data.get("exchange", "cryptocom")
        
        trade_record = {
            "trade_id": trade_id,
            "pair": order_data["pair"],
            "entry_price": order_data["filled_price"],
            "status": "OPEN",
            "entry_id": f"emergency_{order_data['exchange_order_id']}",
            "entry_time": entry_time.isoformat() + "+00:00",
            "exchange": exchange,
            "entry_reason": f"EMERGENCY_RECOVERY: Missing {order_data['pair']} order recovery on {exchange}",
            "position_size": order_data["quantity"],
            "strategy": "emergency_recovery",
            "current_price": order_data["filled_price"],  # Will be updated by price feeds
            "symbol": order_data["pair"],
            "notional_value": order_data["notional_value"],
            "fees": order_data.get("fees_paid", 0.0)
        }
        
        logger.info(f"🚨 EMERGENCY: Creating trade record for missing order {i+1}/{len(MISSING_ORDERS)}")
        logger.info(f"   Trade ID: {trade_id}")
        logger.info(f"   Entry: {order_data['quantity']} {order_data['pair'].split('/')[0]} @ ${order_data['filled_price']} on {exchange}")
        
        try:
            async with session.post(
                f"{DATABASE_API_URL}/api/v1/trades",
                json=trade_record,
                headers={"Content-Type": "application/json"}
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    logger.info(f"✅ RECOVERY: Successfully created trade {trade_id}")
                else:
                    error_text = await response.text() 
                    logger.error(f"❌ RECOVERY: Failed to create trade - {response.status}: {error_text}")
                    
        except Exception as e:
            logger.error(f"❌ RECOVERY: Exception creating trade: {e}")

async def verify_recovery():
    """Verify that the recovery was successful"""
    
    async with aiohttp.ClientSession() as session:
        try:
            # Check for recovered orders by pair
            pairs_to_check = ["CRO/USD", "MANA/USDC"]
            for pair in pairs_to_check:
                async with session.get(f"{DATABASE_API_URL}/api/v1/orders?pair={pair}&limit=10") as response:
                    if response.status == 200:
                        data = await response.json()
                        orders = len(data.get("orders", []))
                        logger.info(f"📊 VERIFICATION: Found {orders} {pair} orders in database")
                        
                        expected = 2 if pair == "CRO/USD" else 1
                        if orders >= expected:
                            logger.info(f"✅ VERIFICATION: {pair} orders successfully recovered")
                        else:
                            logger.warning(f"⚠️ VERIFICATION: Expected {expected}+ {pair} orders, recovery may be incomplete")
                            
                # Check for trades  
                async with session.get(f"{DATABASE_API_URL}/api/v1/trades?pair={pair}&limit=10") as response:
                    if response.status == 200:
                        data = await response.json()
                        trades = len(data.get("trades", []))
                        logger.info(f"📊 VERIFICATION: Found {trades} {pair} trades in database")
                        
                        expected = 2 if pair == "CRO/USD" else 1
                        if trades >= expected:
                            logger.info(f"✅ VERIFICATION: {pair} trades successfully recovered")
                        else:
                            logger.warning(f"⚠️ VERIFICATION: Expected {expected}+ {pair} trades, recovery may be incomplete")
                        
        except Exception as e:
            logger.error(f"❌ VERIFICATION: Failed to verify recovery: {e}")

async def main():
    """Main emergency recovery function"""
    
    logger.info("🚨 STARTING EMERGENCY CRO/USD ORDER RECOVERY")
    logger.info("=" * 60)
    logger.info(f"Missing Orders: {len(MISSING_ORDERS)}")
    logger.info(f"Total Value: ${sum(order['notional_value'] for order in MISSING_ORDERS):.2f}")
    logger.info("=" * 60)
    
    async with aiohttp.ClientSession() as session:
        
        # Step 1: Create emergency order records
        logger.info("🔄 STEP 1: Recording missing orders in database...")
        order_results = []
        
        for i, order_data in enumerate(MISSING_ORDERS):
            logger.info(f"📝 Processing missing order {i+1}/{len(MISSING_ORDERS)}")
            result = await create_emergency_order_record(session, order_data)
            order_results.append(result)
            await asyncio.sleep(0.1)  # Brief delay between requests
            
        # Step 2: Create trade records for open positions
        logger.info("🔄 STEP 2: Creating trade records for CRO positions...")
        await create_emergency_trade_records(session)
        await asyncio.sleep(0.5)
        
        # Step 3: Verify recovery
        logger.info("🔄 STEP 3: Verifying emergency recovery...")
        await verify_recovery()
        
    logger.info("=" * 60)
    logger.info("🎯 EMERGENCY RECOVERY COMPLETED")
    logger.info("⚠️  NEXT STEPS:")
    logger.info("   1. Verify orders appear in web dashboard")
    logger.info("   2. Check CRO/USD positions are tracked")
    logger.info("   3. Update fees with actual exchange data")
    logger.info("   4. Monitor position management")
    logger.info("=" * 60)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("❌ RECOVERY: Emergency recovery interrupted")
    except Exception as e:
        logger.error(f"❌ RECOVERY: Emergency recovery failed: {e}")