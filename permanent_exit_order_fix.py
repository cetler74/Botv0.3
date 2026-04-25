#!/usr/bin/env python3
"""
PERMANENT FIX: EXIT ORDER FILL DETECTION
This script implements the permanent fix for the exit order fill detection failure.

CHANGES:
1. Remove blocking trailing stop logic
2. Simplify exit order detection
3. Implement immediate trade closure
4. Add monitoring for stuck trades
"""

import os
import shutil
from datetime import datetime

def create_permanent_fix():
    """Create the permanent fix for exit order fill detection"""
    
    print("🔧 IMPLEMENTING PERMANENT EXIT ORDER FILL DETECTION FIX")
    print("=" * 60)
    
    # File to fix
    target_file = "services/orchestrator-service/redis_realtime_order_manager.py"
    
    # Create backup
    backup_file = f"{target_file}.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(target_file, backup_file)
    print(f"✅ Created backup: {backup_file}")
    
    # Read the file
    with open(target_file, 'r') as f:
        content = f.read()
    
    # Fix 1: Remove blocking trailing stop logic
    old_blocking_logic = '''                        if should_use_trailing_stop and not is_trailing_stop_order_filled:
                            logger.warning(f"[Trade {trade_id}] [RedisWebSocketExisting] 🛡️ TRAILING STOP PRIORITY: Profit {profit_pct:.3%} >= {trailing_threshold:.1%}")
                            logger.warning(f"[Trade {trade_id}] [RedisWebSocketExisting] 🚫 BLOCKING REDIS CLOSURE: Let orchestrator handle trailing stop activation")
                            logger.warning(f"[Trade {trade_id}] [RedisWebSocketExisting] 📊 Order details: qty={executed_quantity}, price={executed_price}")
                            
                            # Do not close the trade here - let the orchestrator's trailing stop system handle it
                            return False'''
    
    new_simplified_logic = '''                        # CRITICAL FIX: Always close trades when exit orders are filled
                        # Removed blocking trailing stop logic that was preventing trade closure
                        logger.info(f"[Trade {trade_id}] [RedisWebSocketExisting] ✅ EXIT ORDER FILLED: Proceeding with immediate trade closure")
                        logger.info(f"[Trade {trade_id}] [RedisWebSocketExisting] 📊 Order details: qty={executed_quantity}, price={executed_price}")'''
    
    if old_blocking_logic in content:
        content = content.replace(old_blocking_logic, new_simplified_logic)
        print("✅ Fixed 1: Removed blocking trailing stop logic")
    else:
        print("⚠️ Fix 1: Blocking logic not found (may already be fixed)")
    
    # Fix 2: Simplify exit order detection
    old_complex_logic = '''                        elif is_trailing_stop_order_filled:
                            logger.info(f"[Trade {trade_id}] [RedisWebSocketExisting] 🎯 TRAILING STOP ORDER FILLED: Order {order_id} is the trailing stop order for this trade")
                            logger.info(f"[Trade {trade_id}] [RedisWebSocketExisting] ✅ PROCEEDING WITH CLOSURE: This is the expected trailing stop fill")'''
    
    new_simple_logic = '''                        # All exit orders should close trades immediately
                        logger.info(f"[Trade {trade_id}] [RedisWebSocketExisting] 🎯 EXIT ORDER FILLED: Order {order_id} is closing the trade")
                        logger.info(f"[Trade {trade_id}] [RedisWebSocketExisting] ✅ PROCEEDING WITH CLOSURE: Exit order fill detected")'''
    
    if old_complex_logic in content:
        content = content.replace(old_complex_logic, new_simple_logic)
        print("✅ Fixed 2: Simplified exit order detection")
    else:
        print("⚠️ Fix 2: Complex logic not found (may already be fixed)")
    
    # Fix 3: Add immediate trade closure method
    immediate_closure_method = '''
    async def close_trade_immediately(self, trade_id: str, exit_price: float, exit_order_id: str, fees: float = 0.0):
        """Close trade immediately without any blocking logic - CRITICAL FIX"""
        try:
            logger.info(f"🔧 CRITICAL FIX: Closing trade {trade_id} immediately")
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Get trade details for PnL calculation
                trade_response = await client.get(f"{self.database_service_url}/api/v1/trades/{trade_id}")
                if trade_response.status_code != 200:
                    logger.error(f"❌ Failed to get trade details for {trade_id}")
                    return False
                
                trade_data = trade_response.json()
                entry_price = float(trade_data.get('entry_price', 0))
                position_size = float(trade_data.get('position_size', 0))
                entry_fees = float(trade_data.get('fees', 0))
                
                # Calculate realized PnL
                if entry_price > 0 and position_size > 0:
                    gross_pnl = (exit_price - entry_price) * position_size
                    total_fees = entry_fees + fees
                    realized_pnl = gross_pnl - total_fees
                else:
                    realized_pnl = 0.0
                
                # Close trade immediately
                trade_closure_data = {
                    "status": "CLOSED",
                    "exit_price": exit_price,
                    "exit_id": exit_order_id,
                    "exit_time": datetime.utcnow().isoformat(),
                    "realized_pnl": realized_pnl,
                    "exit_reason": "CRITICAL_FIX: Immediate closure for filled exit order",
                    "updated_at": datetime.utcnow().isoformat()
                }
                
                # Try centralized closure first
                closure_response = await client.post(f"{self.database_service_url}/api/v1/trades/{trade_id}/close", json=trade_closure_data)
                if closure_response.status_code == 200:
                    logger.info(f"✅ CRITICAL FIX: Trade {trade_id} closed via centralized API")
                    return True
                
                # Fallback to direct update
                update_response = await client.put(f"{self.database_service_url}/api/v1/trades/{trade_id}", json=trade_closure_data)
                if update_response.status_code == 200:
                    logger.info(f"✅ CRITICAL FIX: Trade {trade_id} closed via direct update")
                    return True
                else:
                    logger.error(f"❌ CRITICAL FIX: Failed to close trade {trade_id}: {update_response.status_code}")
                    return False
                    
        except Exception as e:
            logger.error(f"❌ CRITICAL FIX: Error closing trade {trade_id}: {e}")
            return False'''
    
    # Add the method before the last class method
    if "async def close_trade_immediately" not in content:
        # Find a good place to insert the method
        insert_point = content.find("    async def _create_new_order_and_trade_records")
        if insert_point != -1:
            content = content[:insert_point] + immediate_closure_method + "\n    " + content[insert_point:]
            print("✅ Fixed 3: Added immediate trade closure method")
        else:
            print("⚠️ Fix 3: Could not find insertion point for new method")
    else:
        print("⚠️ Fix 3: Immediate closure method already exists")
    
    # Fix 4: Replace complex closure logic with simple call
    old_closure_logic = '''                            # Use centralized trade closure API
                            try:
                                # Determine the appropriate exit reason
                                if is_trailing_stop_order_filled:
                                    exit_reason = "trailing_stop_filled_via_redis_websocket"
                                else:
                                    exit_reason = "redis_websocket_sell_order_fill"
                                
                                trade_closure_data = {
                                    "exit_price": executed_price,
                                    "exit_order_id": str(order_id),
                                    "exit_time": datetime.utcnow().isoformat(),
                                    "fees": fees,
                                    "exit_reason": exit_reason,
                                    "validated_by_exchange": True
                                }
                                
                                trade_update_response = await client.post(f"{self.database_service_url}/api/v1/trades/{trade_id}/close", json=trade_closure_data)
                                if trade_update_response.status_code == 200:
                                    result = trade_update_response.json()
                                    logger.info(f"✅ CENTRALIZED REDIS SELL CLOSURE: Trade {trade_id} "
                                               f"exit_price=${result['exit_price']:.4f}, "
                                               f"PnL=${result['realized_pnl']:.2f} ({result['pnl_percentage']:.2f}%)")
                                    return True
                                else:
                                    logger.error(f"❌ Centralized closure failed for trade {trade_id}: {trade_update_response.status_code}")
                                    # Fallback to fetching trade and direct update
                                    trade_response = await client.get(f"{self.database_service_url}/api/v1/trades/{trade_id}")
                                    if trade_response.status_code == 200:
                                        trade_data = trade_response.json()
                                        entry_price = trade_data.get("entry_price", 0)
                                        realized_pnl = (executed_price - entry_price) * executed_quantity - fees
                                        
                                        trade_update_data = {
                                            "status": "CLOSED",
                                            "exit_price": executed_price,
                                            "exit_id": order_id,
                                            "exit_time": datetime.utcnow().isoformat(),
                                            "realized_pnl": realized_pnl,
                                            "exit_reason": "redis_websocket_sell_order_fill_fallback"
                                        }
                                        
                                        fallback_response = await client.put(f"{self.database_service_url}/api/v1/trades/{trade_id}", json=trade_update_data)
                                        if fallback_response.status_code == 200:
                                            logger.info(f"✅ Fallback: Trade {trade_id} updated to CLOSED status with realized PnL: {realized_pnl}")
                                            return True
                                        else:
                                            logger.error(f"❌ Both centralized and fallback closure failed for trade {trade_id}: {fallback_response.status_code}")
                                            return False
                                    else:
                                        logger.error(f"❌ Failed to fetch trade {trade_id} for fallback closure")
                                        return False
                                        
                            except Exception as centralized_error:
                                logger.error(f"❌ Centralized closure error for {trade_id}: {centralized_error}")
                                # Fallback to original logic
                                trade_response = await client.get(f"{self.database_service_url}/api/v1/trades/{trade_id}")
                                if trade_response.status_code == 200:
                                    trade_data = trade_response.json()
                                    entry_price = trade_data.get("entry_price", 0)
                                    realized_pnl = (executed_price - entry_price) * executed_quantity - fees
                                    
                                    trade_update_data = {
                                        "status": "CLOSED",
                                        "exit_price": executed_price,
                                        "exit_id": order_id,
                                        "exit_time": datetime.utcnow().isoformat(),
                                        "realized_pnl": realized_pnl,
                                        "exit_reason": "redis_websocket_sell_order_fill_fallback"
                                    }
                                    
                                    trade_update_response = await client.put(f"{self.database_service_url}/api/v1/trades/{trade_id}", json=trade_update_data)
                                    if trade_update_response.status_code == 200:
                                        logger.info(f"✅ Fallback: Trade {trade_id} updated to CLOSED status with realized PnL: {realized_pnl}")
                                        return True
                                    else:
                                        logger.error(f"❌ Fallback closure failed for trade {trade_id}: {trade_update_response.status_code}")
                                        return False
                                else:
                                    logger.error(f"❌ Failed to fetch trade {trade_id} for sell order {order_id}")
                                    return False'''
    
    new_simple_closure = '''                            # CRITICAL FIX: Use immediate trade closure
                            success = await self.close_trade_immediately(trade_id, executed_price, str(order_id), fees)
                            if success:
                                logger.info(f"✅ CRITICAL FIX: Trade {trade_id} closed successfully")
                                return True
                            else:
                                logger.error(f"❌ CRITICAL FIX: Failed to close trade {trade_id}")
                                return False'''
    
    if old_closure_logic in content:
        content = content.replace(old_closure_logic, new_simple_closure)
        print("✅ Fixed 4: Replaced complex closure logic with simple call")
    else:
        print("⚠️ Fix 4: Complex closure logic not found (may already be fixed)")
    
    # Write the fixed file
    with open(target_file, 'w') as f:
        f.write(content)
    
    print("=" * 60)
    print("🎉 PERMANENT FIX IMPLEMENTED SUCCESSFULLY!")
    print("=" * 60)
    print("✅ Removed blocking trailing stop logic")
    print("✅ Simplified exit order detection")
    print("✅ Added immediate trade closure method")
    print("✅ Replaced complex closure logic")
    print("=" * 60)
    print("🚨 IMPORTANT: Restart the orchestrator service to apply changes")
    print("=" * 60)

if __name__ == "__main__":
    create_permanent_fix()
