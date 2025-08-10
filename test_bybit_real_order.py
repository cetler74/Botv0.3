#!/usr/bin/env python3
"""
Real Bybit Order Test - Tests actual order placement with minimal amount
WARNING: This will place a REAL order on Bybit with real money!
"""

import asyncio
import httpx
import json
from datetime import datetime

# Test configuration - VERY SMALL AMOUNT
EXCHANGE_SERVICE_URL = "http://localhost:8003"
TEST_ORDER = {
    "exchange": "bybit",
    "symbol": "BTC/USDC", 
    "order_type": "market",
    "side": "buy",
    "amount": 1.0,  # MINIMAL amount - $1 USDC only
    "price": None
}

async def test_real_bybit_order():
    """Test actual Bybit order placement with minimal amount"""
    print("🚨 REAL BYBIT ORDER TEST")
    print("⚠️  WARNING: This will place a REAL order!")
    print(f"💰 Amount: ${TEST_ORDER['amount']} USDC (minimal test)")
    print("=" * 50)
    
    # Get user confirmation
    confirmation = input("⚠️  Continue with REAL order test? Type 'YES' to proceed: ")
    if confirmation != 'YES':
        print("❌ Test cancelled by user")
        return False
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            # 1. Check balance first
            print("\n1️⃣ Checking Bybit balance...")
            response = await client.get(f"{EXCHANGE_SERVICE_URL}/api/v1/account/balance/bybit")
            if response.status_code != 200:
                print(f"   ❌ Failed to get balance: {response.status_code}")
                return False
                
            balance = response.json()
            usdc_free = balance.get('free', {}).get('USDC', 0) or 0
            print(f"   💰 Available USDC: {usdc_free:.2f}")
            
            if usdc_free < TEST_ORDER["amount"]:
                print(f"   ❌ Insufficient balance: need ${TEST_ORDER['amount']}, have ${usdc_free:.2f}")
                return False
            
            # 2. Get current price
            print("\n2️⃣ Getting current BTC price...")
            response = await client.get(f"{EXCHANGE_SERVICE_URL}/api/v1/market/ticker/bybit/BTCUSDC")
            if response.status_code != 200:
                print(f"   ❌ Failed to get ticker: {response.status_code}")
                return False
                
            ticker = response.json()
            current_price = ticker.get('last', 0)
            btc_amount = TEST_ORDER["amount"] / current_price if current_price > 0 else 0
            print(f"   📊 BTC/USDC: ${current_price:,.2f}")
            print(f"   🪙 Will buy: {btc_amount:.8f} BTC")
            
            # 3. Place the actual order
            print(f"\n3️⃣ Placing REAL order...")
            print(f"   🚨 PLACING LIVE ORDER FOR ${TEST_ORDER['amount']} USDC")
            
            start_time = datetime.now()
            response = await client.post(f"{EXCHANGE_SERVICE_URL}/api/v1/trading/order", json=TEST_ORDER)
            end_time = datetime.now()
            response_time = (end_time - start_time).total_seconds()
            
            print(f"   ⏱️  Response time: {response_time:.2f}s")
            print(f"   📊 Status code: {response.status_code}")
            
            if response.status_code == 200:
                result = response.json()
                print(f"   🎉 ORDER SUCCESSFUL!")
                print(f"   📋 Order ID: {result.get('id', 'N/A')}")
                print(f"   💰 Amount: {result.get('amount', 'N/A')}")
                print(f"   📈 Status: {result.get('status', 'N/A')}")
                print(f"   🔄 Sync Status: {result.get('sync_status', 'N/A')}")
                
                # 4. Verify order in balance
                print(f"\n4️⃣ Verifying balance after order...")
                await asyncio.sleep(2)  # Wait for order to process
                
                response = await client.get(f"{EXCHANGE_SERVICE_URL}/api/v1/account/balance/bybit")
                if response.status_code == 200:
                    new_balance = response.json()
                    new_usdc_free = new_balance.get('free', {}).get('USDC', 0) or 0
                    usdc_change = usdc_free - new_usdc_free
                    print(f"   💰 New USDC balance: {new_usdc_free:.2f}")
                    print(f"   📉 USDC used: {usdc_change:.2f}")
                    
                    # Check if we got BTC
                    btc_balance = new_balance.get('BTC', {}).get('total', 0) or 0
                    if btc_balance > 0:
                        print(f"   🪙 BTC received: {btc_balance:.8f}")
                    else:
                        print(f"   ⚠️  BTC balance not visible yet (may take time to appear)")
                
                return True
                
            else:
                error_text = response.text
                print(f"   ❌ ORDER FAILED!")
                print(f"   🔴 Status: {response.status_code}")
                print(f"   📄 Error: {error_text}")
                
                # Check if it's the old error
                if "createMarketBuyOrderRequiresPrice" in error_text:
                    print(f"   🚨 BYBIT PARAMETER ERROR STILL EXISTS!")
                    print(f"   🔧 The fix may not be working correctly")
                elif "insufficient" in error_text.lower():
                    print(f"   💰 Balance/insufficient funds error")
                else:
                    print(f"   ❓ Unknown error type")
                
                return False
                
        except Exception as e:
            print(f"   ❌ Test failed with exception: {e}")
            return False

async def main():
    """Run the real order test"""
    print(f"🧪 Bybit Real Order Test")
    print(f"⏰ Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    success = await test_real_bybit_order()
    
    print("\n" + "=" * 50)
    if success:
        print("🎉 REAL ORDER TEST PASSED!")
        print("✅ Bybit order placement is working correctly")
        print("🚀 The fix has been validated with real money")
    else:
        print("❌ REAL ORDER TEST FAILED!")
        print("🔧 There are still issues with Bybit order placement")
        print("📋 Check the error logs above")
    
    print(f"⏰ Completed at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

if __name__ == "__main__":
    asyncio.run(main())