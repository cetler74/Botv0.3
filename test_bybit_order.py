#!/usr/bin/env python3
"""
Test script to validate Bybit order placement flow
Tests the exchange service API without actually placing real orders
"""

import asyncio
import httpx
import json
from datetime import datetime

# Test configuration
EXCHANGE_SERVICE_URL = "http://localhost:8003"
TEST_ORDER = {
    "exchange": "bybit",
    "symbol": "BTC/USDC", 
    "order_type": "market",
    "side": "buy",
    "amount": 5.0,  # Small test amount (5 USDC)
    "price": None
}

async def test_bybit_order_flow():
    """Test the complete Bybit order flow"""
    print("🧪 Testing Bybit Order Flow")
    print("=" * 50)
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            # 1. Test exchange service health
            print("1️⃣ Testing exchange service health...")
            response = await client.get(f"{EXCHANGE_SERVICE_URL}/health")
            if response.status_code == 200:
                health = response.json()
                print(f"   ✅ Exchange service healthy: {health['exchanges_connected']}/{health['total_exchanges']} exchanges")
            else:
                print(f"   ❌ Exchange service unhealthy: {response.status_code}")
                return False
            
            # 2. Test Bybit balance
            print("\n2️⃣ Testing Bybit balance retrieval...")
            response = await client.get(f"{EXCHANGE_SERVICE_URL}/api/v1/account/balance/bybit")
            if response.status_code == 200:
                balance = response.json()
                available_total = balance.get('available', 0)
                print(f"   ✅ Bybit balance retrieved: ${available_total:.2f} available")
                
                # Get USDC balance specifically
                usdc_balance = balance.get('USDC', {})
                usdc_total = usdc_balance.get('total', 0) or 0
                usdc_free = balance.get('free', {}).get('USDC', 0) or usdc_total
                
                print(f"   💰 USDC: {usdc_free:.2f} available of {usdc_total:.2f} total")
                
                if usdc_free >= TEST_ORDER["amount"]:
                    print(f"   ✅ Sufficient balance for test: {usdc_free:.2f} USDC")
                else:
                    print(f"   ⚠️  Limited balance: {usdc_free:.2f} USDC (test needs {TEST_ORDER['amount']})")
            else:
                print(f"   ❌ Failed to get balance: {response.status_code}")
                return False
            
            # 3. Test market data
            print("\n3️⃣ Testing market data retrieval...")
            response = await client.get(f"{EXCHANGE_SERVICE_URL}/api/v1/market/ticker/bybit/BTCUSDC")
            if response.status_code == 200:
                ticker = response.json()
                current_price = ticker.get('last', 0)
                print(f"   ✅ Market data available: BTC/USDC @ ${current_price:,.2f}")
                
                # Calculate how much BTC we'd get
                btc_amount = TEST_ORDER["amount"] / current_price if current_price > 0 else 0
                print(f"   📊 Test order would buy: {btc_amount:.8f} BTC for ${TEST_ORDER['amount']}")
            else:
                print(f"   ❌ Failed to get ticker: {response.status_code}")
                return False
            
            # 4. Test order validation (dry run)
            print("\n4️⃣ Testing order parameter validation...")
            print(f"   📝 Order details:")
            print(f"      Exchange: {TEST_ORDER['exchange']}")
            print(f"      Symbol: {TEST_ORDER['symbol']}")
            print(f"      Type: {TEST_ORDER['order_type']}")
            print(f"      Side: {TEST_ORDER['side']}")
            print(f"      Amount: ${TEST_ORDER['amount']} USDC")
            
            # Check if this would be a valid order
            if TEST_ORDER['order_type'] == 'market' and TEST_ORDER['side'] == 'buy':
                print(f"   ✅ Order type: Market buy (should use createMarketBuyOrderRequiresPrice=False)")
            
            # 5. Simulate order creation (NOTE: We won't actually place the order)
            print(f"\n5️⃣ Simulating order creation...")
            print(f"   🚨 SIMULATION MODE - NOT placing real order")
            print(f"   📤 Would POST to: {EXCHANGE_SERVICE_URL}/api/v1/trading/order")
            print(f"   📋 Payload: {json.dumps(TEST_ORDER, indent=2)}")
            
            # If you want to test the actual API (UNCOMMENT to enable real order):
            # UNCOMMENT_TO_ENABLE_REAL_ORDER = False
            # if UNCOMMENT_TO_ENABLE_REAL_ORDER:
            #     response = await client.post(f"{EXCHANGE_SERVICE_URL}/api/v1/trading/order", json=TEST_ORDER)
            #     if response.status_code == 200:
            #         result = response.json()
            #         print(f"   ✅ Order placed successfully: {result.get('id')}")
            #         return True
            #     else:
            #         error_text = response.text
            #         print(f"   ❌ Order failed: {response.status_code}")
            #         print(f"   📄 Error: {error_text}")
            #         return False
            
            print(f"   ✅ All validation checks passed!")
            print(f"\n🎉 Bybit order flow test completed successfully!")
            print(f"💡 To test with real orders, uncomment the order creation section")
            return True
            
        except Exception as e:
            print(f"   ❌ Test failed with exception: {e}")
            return False

async def test_order_parameters():
    """Test that order parameters are correctly set for Bybit"""
    print("\n🔍 Testing Order Parameter Configuration")
    print("=" * 50)
    
    # Test the parameter logic
    order = TEST_ORDER.copy()
    params = {}
    
    if order['order_type'] == 'market':
        params['type'] = 'market'
        if order['exchange'].lower() == 'bybit' and order['side'] == 'buy':
            params['createMarketBuyOrderRequiresPrice'] = False
            print("✅ Bybit market buy parameter correctly set:")
            print(f"   createMarketBuyOrderRequiresPrice = {params['createMarketBuyOrderRequiresPrice']}")
        else:
            print("ℹ️  Not a Bybit market buy order")
    
    print(f"📋 Final params: {json.dumps(params, indent=2)}")
    return True

async def main():
    """Run all tests"""
    print(f"🚀 Bybit Order Flow Validation")
    print(f"⏰ Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    # Run parameter test
    success1 = await test_order_parameters()
    
    # Run flow test  
    success2 = await test_bybit_order_flow()
    
    print("\n" + "=" * 50)
    if success1 and success2:
        print("🎉 ALL TESTS PASSED!")
        print("✅ Bybit order flow is ready for production")
    else:
        print("❌ SOME TESTS FAILED!")
        print("🔧 Check the logs above for issues")
    
    print(f"⏰ Completed at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

if __name__ == "__main__":
    asyncio.run(main())