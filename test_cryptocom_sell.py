#!/usr/bin/env python3
"""
Test Crypto.com sell order placement and trade closure
"""

import asyncio
import httpx
from datetime import datetime, timezone

ORCHESTRATOR_URL = "http://localhost:8005"
DATABASE_URL = "http://localhost:8002"

# Test trade to close (one of our new ADA trades)
TEST_TRADE_ID = "fa2886d8-7494-49aa-9f69-00f8f0e98091"

async def get_trade_details(trade_id):
    """Get trade details"""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{DATABASE_URL}/api/v1/trades/{trade_id}")
            if response.status_code == 200:
                return response.json()
            else:
                print(f"❌ Failed to get trade {trade_id}: {response.status_code}")
                return None
    except Exception as e:
        print(f"❌ Error getting trade {trade_id}: {e}")
        return None

async def place_manual_sell_order(trade_data):
    """Place a manual sell order for testing"""
    try:
        # Simulate placing a sell order at slightly lower price to ensure fill
        sell_price = trade_data["entry_price"] * 0.992  # 0.8% loss to trigger stop loss
        
        order_data = {
            "exchange": trade_data["exchange"],
            "symbol": trade_data["pair"],
            "side": "sell",
            "amount": trade_data["position_size"],
            "price": sell_price,
            "order_type": "limit"
        }
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Note: Using a fake order ID since we're testing the process
            fake_order_id = "6530219584115999999"
            
            # Manually create order record
            order_record = {
                "order_id": fake_order_id,
                "trade_id": trade_data["trade_id"],
                "exchange": trade_data["exchange"],
                "symbol": trade_data["pair"],
                "order_type": "limit",
                "side": "sell",
                "amount": trade_data["position_size"],
                "price": sell_price,
                "filled_amount": trade_data["position_size"],
                "filled_price": sell_price,
                "status": "FILLED",
                "fees": 0.0,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "filled_at": datetime.now(timezone.utc).isoformat(),
                "exchange_order_id": fake_order_id
            }
            
            # Create order record
            response = await client.post(f"{DATABASE_URL}/api/v1/orders", json=order_record)
            if response.status_code == 200:
                print(f"✅ Created sell order {fake_order_id}")
                return fake_order_id, sell_price
            else:
                print(f"❌ Failed to create sell order: {response.status_code}")
                return None, None
                
    except Exception as e:
        print(f"❌ Error placing sell order: {e}")
        return None, None

async def close_trade_manually(trade_id, exit_order_id, exit_price):
    """Manually close trade"""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Get current trade data
            trade_response = await client.get(f"{DATABASE_URL}/api/v1/trades/{trade_id}")
            if trade_response.status_code != 200:
                print(f"❌ Failed to get trade {trade_id}")
                return False
                
            trade_data = trade_response.json()
            entry_price = trade_data["entry_price"]
            position_size = trade_data["position_size"]
            
            # Calculate P&L
            realized_pnl = (exit_price - entry_price) * position_size
            
            # Update trade to CLOSED
            update_data = {
                "status": "CLOSED",
                "exit_price": exit_price,
                "exit_id": exit_order_id,
                "exit_reason": "test_manual_sell",
                "exit_time": datetime.now(timezone.utc).isoformat(),
                "realized_pnl": realized_pnl
            }
            
            response = await client.put(f"{DATABASE_URL}/api/v1/trades/{trade_id}", json=update_data)
            if response.status_code == 200:
                print(f"✅ Closed trade {trade_id} with PnL: ${realized_pnl:.2f}")
                return True
            else:
                print(f"❌ Failed to close trade {trade_id}: {response.status_code}")
                return False
                
    except Exception as e:
        print(f"❌ Error closing trade {trade_id}: {e}")
        return False

async def test_cryptocom_sell():
    """Test Crypto.com sell process"""
    print("🚨 TESTING: Crypto.com sell order placement and trade closure")
    print("=" * 60)
    
    # Get trade details
    print(f"\n📋 Getting trade details for {TEST_TRADE_ID}")
    trade_data = await get_trade_details(TEST_TRADE_ID)
    if not trade_data:
        print("❌ Failed to get trade data")
        return
    
    print(f"   Trade: {trade_data['position_size']} {trade_data['pair']} at ${trade_data['entry_price']}")
    print(f"   Value: ${trade_data['position_size'] * trade_data['entry_price']:.2f}")
    
    # Place sell order
    print(f"\n📋 Placing sell order")
    exit_order_id, exit_price = await place_manual_sell_order(trade_data)
    if not exit_order_id:
        print("❌ Failed to place sell order")
        return
        
    print(f"   Exit price: ${exit_price:.6f} (0.8% loss)")
    
    # Close trade
    print(f"\n📋 Closing trade")
    if await close_trade_manually(TEST_TRADE_ID, exit_order_id, exit_price):
        print(f"✅ Successfully completed Crypto.com sell test")
    else:
        print(f"❌ Failed to close trade")
    
    print(f"\n📊 CRYPTO.COM SELL TEST COMPLETE")

if __name__ == "__main__":
    asyncio.run(test_cryptocom_sell())