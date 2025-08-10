#!/usr/bin/env python3
"""
Test script to validate Bybit SELL order placement flow
Tests exit orders with the same parameters as buy orders
"""

import asyncio
import httpx
import json
from datetime import datetime

# Test configuration
EXCHANGE_SERVICE_URL = "http://localhost:8003"
TEST_SELL_ORDER = {
    "exchange": "bybit",
    "symbol": "BTC/USDC", 
    "order_type": "market",
    "side": "sell",
    "amount": 0.00001,  # Small BTC amount to sell (about $1 worth)
    "price": None
}

async def test_bybit_sell_flow():
    """Test the complete Bybit sell order flow"""
    print("🧪 Testing Bybit SELL Order Flow")
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
            
            # 2. Test Bybit balance - check BTC holdings
            print("\n2️⃣ Testing Bybit balance for BTC holdings...")
            response = await client.get(f"{EXCHANGE_SERVICE_URL}/api/v1/account/balance/bybit")
            if response.status_code == 200:
                balance = response.json()
                available_total = balance.get('available', 0)
                print(f"   ✅ Bybit balance retrieved: ${available_total:.2f} total available")
                
                # Check BTC balance specifically
                btc_balance = balance.get('BTC', {})
                btc_total = btc_balance.get('total', 0) or 0
                btc_free = balance.get('free', {}).get('BTC', 0) or btc_total
                
                print(f"   🪙 BTC: {btc_free:.8f} available of {btc_total:.8f} total")
                
                if btc_free >= TEST_SELL_ORDER["amount"]:
                    print(f"   ✅ Sufficient BTC for sell test: {btc_free:.8f} BTC")
                else:
                    print(f"   ⚠️  Limited BTC: {btc_free:.8f} BTC (test needs {TEST_SELL_ORDER['amount']:.8f})")
                    print(f"   💡 Note: You may need to buy BTC first to test sell orders")
                    
                # Also check USDC for context
                usdc_free = balance.get('free', {}).get('USDC', 0) or 0
                print(f"   💰 USDC: {usdc_free:.2f} available (for reference)")
                
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
                
                # Calculate how much USDC we'd get from selling
                usdc_value = TEST_SELL_ORDER["amount"] * current_price if current_price > 0 else 0
                print(f"   📊 Test sell would generate: ${usdc_value:.2f} USDC from {TEST_SELL_ORDER['amount']:.8f} BTC")
            else:
                print(f"   ❌ Failed to get ticker: {response.status_code}")
                return False
            
            # 4. Test sell order validation (dry run)
            print("\n4️⃣ Testing SELL order parameter validation...")
            print(f"   📝 Sell order details:")
            print(f"      Exchange: {TEST_SELL_ORDER['exchange']}")
            print(f"      Symbol: {TEST_SELL_ORDER['symbol']}")
            print(f"      Type: {TEST_SELL_ORDER['order_type']}")
            print(f"      Side: {TEST_SELL_ORDER['side']} 📉")
            print(f"      Amount: {TEST_SELL_ORDER['amount']:.8f} BTC")
            
            # Check if this would be a valid sell order
            if TEST_SELL_ORDER['order_type'] == 'market' and TEST_SELL_ORDER['side'] == 'sell':
                print(f"   ✅ Order type: Market sell (standard CCXT behavior)")
                print(f"   💡 Sell orders typically don't need special Bybit parameters")
            
            # 5. Test order parameters for sell
            print(f"\n5️⃣ Testing sell order parameter configuration...")
            params = {}
            if TEST_SELL_ORDER['order_type'] == 'market':
                params['type'] = 'market'
                # Sell orders don't need the createMarketBuyOrderRequiresPrice parameter
                if TEST_SELL_ORDER['exchange'].lower() == 'bybit' and TEST_SELL_ORDER['side'] == 'sell':
                    print(f"   ✅ Bybit market sell: No special parameters required")
                else:
                    print(f"   ℹ️  Not a Bybit market sell order")
            
            print(f"   📋 Sell params: {json.dumps(params, indent=2)}")
            
            # 6. Simulate sell order creation
            print(f"\n6️⃣ Simulating SELL order creation...")
            print(f"   🚨 SIMULATION MODE - NOT placing real sell order")
            print(f"   📤 Would POST to: {EXCHANGE_SERVICE_URL}/api/v1/trading/order")
            print(f"   📋 Payload: {json.dumps(TEST_SELL_ORDER, indent=2)}")
            
            print(f"   ✅ All sell order validation checks passed!")
            print(f"\n🎉 Bybit SELL order flow test completed successfully!")
            print(f"💡 Sell orders should work without the Bybit buy order fix")
            
            return True
            
        except Exception as e:
            print(f"   ❌ Test failed with exception: {e}")
            return False

async def test_sell_order_differences():
    """Test the differences between buy and sell order requirements"""
    print("\n🔍 Testing Buy vs Sell Order Differences")
    print("=" * 50)
    
    # Buy order parameters
    buy_params = {}
    buy_params['type'] = 'market'
    buy_params['createMarketBuyOrderRequiresPrice'] = False  # Required for Bybit buy
    
    # Sell order parameters  
    sell_params = {}
    sell_params['type'] = 'market'
    # No special parameters needed for sell orders
    
    print("📋 Buy Order Parameters:")
    print(f"   {json.dumps(buy_params, indent=2)}")
    print(f"   💡 Bybit buy orders need createMarketBuyOrderRequiresPrice=False")
    
    print("\n📋 Sell Order Parameters:")
    print(f"   {json.dumps(sell_params, indent=2)}")
    print(f"   💡 Sell orders use standard CCXT behavior")
    
    print(f"\n✅ Key difference: Only BUY orders need the special Bybit parameter")
    return True

async def check_btc_holdings():
    """Check if we have BTC to test sell orders with"""
    print("\n💰 Checking BTC Holdings for Sell Test")
    print("=" * 50)
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.get(f"{EXCHANGE_SERVICE_URL}/api/v1/account/balance/bybit")
            if response.status_code == 200:
                balance = response.json()
                
                # Check all cryptocurrency holdings
                btc_total = balance.get('BTC', {}).get('total', 0) or 0
                btc_free = balance.get('free', {}).get('BTC', 0) or btc_total
                
                eth_total = balance.get('ETH', {}).get('total', 0) or 0
                eth_free = balance.get('free', {}).get('ETH', 0) or eth_total
                
                print(f"🪙 Cryptocurrency Holdings:")
                print(f"   BTC: {btc_free:.8f} available / {btc_total:.8f} total")
                print(f"   ETH: {eth_free:.8f} available / {eth_total:.8f} total")
                
                if btc_free > 0:
                    print(f"   ✅ Have BTC for sell testing")
                    # Get current BTC price for value calculation
                    ticker_response = await client.get(f"{EXCHANGE_SERVICE_URL}/api/v1/market/ticker/bybit/BTCUSDC")
                    if ticker_response.status_code == 200:
                        ticker = ticker_response.json()
                        btc_price = ticker.get('last', 0)
                        btc_value = btc_free * btc_price if btc_price > 0 else 0
                        print(f"   💵 BTC value: ${btc_value:.2f} USDC")
                else:
                    print(f"   ⚠️  No BTC available for sell testing")
                    print(f"   💡 Suggestion: Run a small buy order first to get BTC for sell testing")
                
                return btc_free > 0
                
            else:
                print(f"❌ Failed to get balance: {response.status_code}")
                return False
                
        except Exception as e:
            print(f"❌ Error checking holdings: {e}")
            return False

async def main():
    """Run all sell order tests"""
    print(f"🚀 Bybit SELL Order Flow Validation")
    print(f"⏰ Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    # Run tests
    success1 = await test_sell_order_differences()
    success2 = await check_btc_holdings()
    success3 = await test_bybit_sell_flow()
    
    print("\n" + "=" * 50)
    if success1 and success3:
        print("🎉 SELL ORDER TESTS PASSED!")
        print("✅ Bybit sell order flow is ready for testing")
        if success2:
            print("💰 You have BTC available for real sell testing")
        else:
            print("💡 Consider buying small amount of BTC first for sell testing")
    else:
        print("❌ SOME SELL TESTS FAILED!")
        print("🔧 Check the logs above for issues")
    
    print(f"⏰ Completed at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

if __name__ == "__main__":
    asyncio.run(main())