#!/usr/bin/env python3
"""
Diagnose Order Fill Recording Issue - Root Cause Analysis

This script will identify the specific points where order fills are failing to be recorded
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

async def analyze_order_lifecycle():
    """Analyze the complete order lifecycle to find recording gaps"""
    logger.info("🔍 ANALYZING ORDER FILL RECORDING PIPELINE")
    logger.info("=" * 60)
    
    issues_found = []
    
    # 1. Check database event handling
    logger.info("1. Testing Database Event API...")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Test events endpoint with proper structure
            event_data = {
                "event_type": "OrderFilled",
                "aggregate_id": "test-order-123",
                "payload": {
                    "local_order_id": "test-order-123",
                    "exchange_order_id": "exchange-123",
                    "exchange": "cryptocom",
                    "symbol": "1INCH/USD",
                    "side": "buy",
                    "quantity": 100.0,
                    "price": 0.25,
                    "timestamp": datetime.utcnow().isoformat()
                }
            }
            
            response = await client.post(f"{DATABASE_SERVICE_URL}/api/v1/events", json=event_data)
            
            if response.status_code == 200:
                logger.info("✅ Database events API working")
            else:
                logger.error(f"❌ Database events API failed: {response.status_code} - {response.text}")
                issues_found.append("Database events API broken")
                
    except Exception as e:
        logger.error(f"❌ Database events API error: {e}")
        issues_found.append(f"Database events API exception: {e}")
    
    # 2. Check order fill detection in orchestrator
    logger.info("\n2. Checking Orchestrator Fill Detection...")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Check if orchestrator is properly detecting fills
            response = await client.get(f"{ORCHESTRATOR_SERVICE_URL}/api/v1/orders/tracking")
            
            if response.status_code == 200:
                tracking_data = response.json()
                pending_orders = tracking_data.get('pending_orders', {})
                filled_orders = tracking_data.get('filled_orders', {})
                
                logger.info(f"📊 Pending orders: {len(pending_orders)}")
                logger.info(f"📊 Filled orders: {len(filled_orders)}")
                
                # Check for orders that might be stuck in pending
                for order_id, order_data in pending_orders.items():
                    created_at = datetime.fromisoformat(order_data.get('created_at', '').replace('Z', '+00:00'))
                    age_minutes = (datetime.utcnow().replace(tzinfo=None) - created_at.replace(tzinfo=None)).total_seconds() / 60
                    
                    if age_minutes > 30:  # Orders older than 30 minutes
                        logger.warning(f"⚠️ Stuck pending order: {order_id} (age: {age_minutes:.1f}m)")
                        issues_found.append(f"Stuck pending order: {order_id}")
                
            else:
                logger.error(f"❌ Orchestrator tracking API failed: {response.status_code}")
                issues_found.append("Orchestrator tracking API failed")
                
    except Exception as e:
        logger.error(f"❌ Orchestrator tracking error: {e}")
        issues_found.append(f"Orchestrator tracking exception: {e}")
    
    # 3. Check order-sync service health
    logger.info("\n3. Checking Order-Sync Service...")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"http://localhost:8008/health")
            
            if response.status_code == 200:
                health = response.json()
                reconciler_metrics = health.get('reconciler_v2', {}).get('metrics', {})
                
                logger.info("📊 Order-Sync Metrics:")
                logger.info(f"  - Reconciliations run: {reconciler_metrics.get('reconciliations_run', 0)}")
                logger.info(f"  - Mismatches found: {reconciler_metrics.get('mismatches_found', 0)}")
                logger.info(f"  - Corrective events: {reconciler_metrics.get('corrective_events_generated', 0)}")
                logger.info(f"  - Orphaned orders: {reconciler_metrics.get('orphaned_orders_imported', 0)}")
                logger.info(f"  - Alerts raised: {reconciler_metrics.get('alerts_raised', 0)}")
                
                # Check for concerning metrics
                if reconciler_metrics.get('alerts_raised', 0) > 100:
                    issues_found.append(f"High alert count: {reconciler_metrics.get('alerts_raised')}")
                    
            else:
                logger.error(f"❌ Order-sync health check failed: {response.status_code}")
                issues_found.append("Order-sync service unhealthy")
                
    except Exception as e:
        logger.error(f"❌ Order-sync health error: {e}")
        issues_found.append(f"Order-sync exception: {e}")
    
    # 4. Check for recent order creation vs fill events
    logger.info("\n4. Analyzing Recent Order vs Fill Event Patterns...")
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Get recent orders from database
            orders_response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/orders?limit=50")
            
            if orders_response.status_code == 200:
                orders = orders_response.json().get('orders', [])
                
                # Count orders by status
                status_counts = {}
                recent_orders = []
                
                for order in orders:
                    status = order.get('status', 'unknown')
                    status_counts[status] = status_counts.get(status, 0) + 1
                    
                    # Check for recent orders
                    if order.get('created_at'):
                        created_at = datetime.fromisoformat(order['created_at'].replace('Z', '+00:00'))
                        if (datetime.utcnow().replace(tzinfo=None) - created_at.replace(tzinfo=None)).total_seconds() < 3600:  # Last hour
                            recent_orders.append(order)
                
                logger.info("📊 Order Status Distribution:")
                for status, count in status_counts.items():
                    logger.info(f"  - {status}: {count}")
                
                logger.info(f"📊 Recent orders (last hour): {len(recent_orders)}")
                
                # Check for orders that should have filled but haven't
                acknowledged_not_filled = [o for o in recent_orders if o.get('status') == 'ACKNOWLEDGED']
                if acknowledged_not_filled:
                    logger.warning(f"⚠️ {len(acknowledged_not_filled)} orders stuck in ACKNOWLEDGED state")
                    issues_found.append(f"{len(acknowledged_not_filled)} orders not progressing from ACKNOWLEDGED")
                
            else:
                logger.error(f"❌ Database orders API failed: {response.status_code}")
                issues_found.append("Database orders API failed")
                
    except Exception as e:
        logger.error(f"❌ Database orders analysis error: {e}")
        issues_found.append(f"Database orders exception: {e}")
    
    # 5. Summary and recommendations
    logger.info("\n" + "=" * 60)
    logger.info("🔍 DIAGNOSIS SUMMARY")
    logger.info("=" * 60)
    
    if issues_found:
        logger.error(f"❌ FOUND {len(issues_found)} ISSUES:")
        for i, issue in enumerate(issues_found, 1):
            logger.error(f"  {i}. {issue}")
        
        logger.info("\n🛠️ RECOMMENDED FIXES:")
        
        if any("Database events" in issue for issue in issues_found):
            logger.info("  1. Fix database events API - check event schema validation")
        
        if any("Stuck pending" in issue for issue in issues_found):
            logger.info("  2. Implement order timeout and cleanup mechanism")
        
        if any("ACKNOWLEDGED" in issue for issue in issues_found):
            logger.info("  3. Add fill verification after order acknowledgment")
        
        if any("Order-sync" in issue for issue in issues_found):
            logger.info("  4. Restart and monitor order-sync service")
            
        logger.info("  5. Implement real-time fill detection instead of polling")
        logger.info("  6. Add atomic order-fill-record transactions")
        
    else:
        logger.info("✅ No obvious issues found - may need deeper investigation")
    
    return issues_found

async def propose_fixes():
    """Propose specific code fixes for the identified issues"""
    logger.info("\n🛠️ PROPOSING IMMEDIATE FIXES:")
    logger.info("=" * 60)
    
    fixes = [
        {
            "priority": "CRITICAL",
            "component": "Database Events API",
            "issue": "500 errors preventing order sync corrections",
            "fix": "Validate event schema and fix aggregate_id requirement",
            "implementation": "Update database service event validation"
        },
        {
            "priority": "HIGH", 
            "component": "Orchestrator Fill Detection",
            "issue": "Orders acknowledged but fills not detected",
            "fix": "Add immediate post-acknowledgment fill verification",
            "implementation": "Implement WebSocket-based fill monitoring"
        },
        {
            "priority": "HIGH",
            "component": "Order Lifecycle Management", 
            "issue": "No atomic order-fill-record transactions",
            "fix": "Ensure every exchange fill creates database record",
            "implementation": "Add transaction rollback on fill recording failure"
        },
        {
            "priority": "MEDIUM",
            "component": "Order-Sync Recovery",
            "issue": "Manual reconciliation required",
            "fix": "Implement automatic recovery for missed fills",
            "implementation": "Add continuous background fill verification"
        }
    ]
    
    for fix in fixes:
        logger.info(f"🔧 [{fix['priority']}] {fix['component']}")
        logger.info(f"   Issue: {fix['issue']}")
        logger.info(f"   Fix: {fix['fix']}")
        logger.info(f"   Implementation: {fix['implementation']}\n")
    
    return fixes

async def main():
    """Main diagnostic process"""
    logger.info("🚨 CRITICAL ISSUE ANALYSIS: Order Fill Recording Failures")
    logger.info("This diagnostic will identify why orders execute on exchange but don't record in database")
    
    issues = await analyze_order_lifecycle()
    fixes = await propose_fixes()
    
    logger.info("=" * 60)
    logger.info("📋 NEXT STEPS:")
    logger.info("1. Implement the CRITICAL fixes first")
    logger.info("2. Add comprehensive logging to order fill pipeline") 
    logger.info("3. Implement automated testing for order lifecycle")
    logger.info("4. Set up alerts for order recording failures")
    logger.info("=" * 60)

if __name__ == "__main__":
    asyncio.run(main())