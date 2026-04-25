#!/usr/bin/env python3
"""
URGENT FIX: Database Events API UUID Validation Issue
The database expects UUID format for correlation_id but receives strings
"""

import asyncio
import httpx
import logging
import uuid

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATABASE_SERVICE_URL = "http://localhost:8002"

async def test_with_proper_uuid():
    """Test events API with proper UUID format"""
    logger.info("🧪 Testing with proper UUID format...")
    
    test_event = {
        "aggregate_id": str(uuid.uuid4()),  # Proper UUID
        "aggregate_type": "order",
        "event_type": "OrderFilled",
        "payload": {
            "local_order_id": str(uuid.uuid4()),
            "exchange_order_id": "6530219584046147761",
            "exchange": "cryptocom", 
            "symbol": "1INCH/USD",
            "side": "buy",
            "quantity": 396.0,
            "price": 0.25284,
            "timestamp": "2025-08-21T12:22:15Z"
        },
        "correlation_id": str(uuid.uuid4()),  # Proper UUID
        "causation_id": str(uuid.uuid4())     # Proper UUID
    }
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(f"{DATABASE_SERVICE_URL}/api/v1/events", json=test_event)
            
            if response.status_code == 200:
                logger.info("✅ Events API working with proper UUID format!")
                result = response.json()
                logger.info(f"📋 Event ID created: {result.get('event_id')}")
                return True
            else:
                logger.error(f"❌ Still failing: {response.status_code}")
                logger.error(f"📋 Response: {response.text}")
                return False
                
    except Exception as e:
        logger.error(f"❌ Test error: {e}")
        return False

async def fix_order_sync_service():
    """The order-sync service needs to generate proper UUIDs"""
    logger.info("🔧 Analyzing order-sync service UUID generation...")
    
    # Check if order-sync service is generating proper UUIDs
    # The fix would be in the emit_corrective_event function
    
    logger.info("📋 Required fix in order-sync service:")
    logger.info("   - correlation_id should be str(uuid.uuid4())")
    logger.info("   - causation_id should be str(uuid.uuid4())")
    logger.info("   - aggregate_id should be proper UUID format")
    
    return True

async def main():
    """Main fix process"""
    logger.info("🚨 CRITICAL FIX: Database Events API UUID Validation")
    logger.info("=" * 60)
    
    # Test with proper UUIDs
    uuid_test_passed = await test_with_proper_uuid()
    
    if uuid_test_passed:
        logger.info("✅ SOLUTION CONFIRMED: Events API works with proper UUIDs")
        logger.info("\n🛠️ REQUIRED FIX:")
        logger.info("Update order-sync-service to generate proper UUIDs for:")
        logger.info("  - correlation_id")
        logger.info("  - causation_id") 
        logger.info("  - aggregate_id")
        
        await fix_order_sync_service()
        
    else:
        logger.error("❌ Additional issues remain in Events API")
    
    logger.info("=" * 60)

if __name__ == "__main__":
    asyncio.run(main())