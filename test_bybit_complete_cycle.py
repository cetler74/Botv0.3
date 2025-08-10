#!/usr/bin/env python3
"""
Complete Bybit Trading Cycle Test
Tests BUY order followed by SELL order to validate complete trading flow
WARNING: This will place REAL orders with real money!
"""

import asyncio
import httpx
import json
from datetime import datetime

# Test configuration
EXCHANGE_SERVICE_URL = "http://localhost:8003"
TEST_AMOUNT_USDC = 2.0  # $2 USDC for complete cycle test

async def test_complete_trading_cycle():
    """Test complete buy -> sell trading cycle"""
    print("🔄 COMPLETE BYBIT TRADING CYCLE TEST")
    print("⚠️  WARNING: This will place REAL BUY and SELL orders!")
    print(f"💰 Test amount: ${TEST_AMOUNT_USDC} USDC")
    print("=" * 60)
    
    # Get user confirmation
    print("📋 This test will:")
    print("   1️⃣ Buy BTC with USDC (market buy order)")
    print("   2️⃣ Wait for order to settle")
    print("   3️⃣ Sell the BTC back to USDC (market sell order)")
    print("   4️⃣ Verify complete cycle")
    print()
    confirmation = input("⚠️  Continue with COMPLETE CYCLE test? Type 'YES' to proceed: ")
    if confirmation != 'YES':
        print("❌ Complete cycle test cancelled by user")
        return False
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            # STEP 1: Get initial balances
            print("\n📊 STEP 1: Getting initial balances...")
            response = await client.get(f"{EXCHANGE_SERVICE_URL}/api/v1/account/balance/bybit")
            if response.status_code != 200:
                print(f"❌ Failed to get initial balance: {response.status_code}")
                return False
            
            initial_balance = response.json()
            initial_usdc = initial_balance.get('free', {}).get('USDC', 0) or 0
            initial_btc = initial_balance.get('free', {}).get('BTC', 0) or 0
            
            print(f"   💰 Initial USDC: {initial_usdc:.2f}")
            print(f"   🪙 Initial BTC: {initial_btc:.8f}")
            
            if initial_usdc < TEST_AMOUNT_USDC:
                print(f"❌ Insufficient USDC: need ${TEST_AMOUNT_USDC}, have ${initial_usdc:.2f}")
                return False
            
            # STEP 2: Place BUY order
            print(f"\n🛒 STEP 2: Placing BUY order...")
            buy_order = {
                "exchange": "bybit",
                "symbol": "BTC/USDC",
                "order_type": "market",
                "side": "buy",
                "amount": TEST_AMOUNT_USDC,
                "price": None
            }
            
            print(f"   📤 Buying ${TEST_AMOUNT_USDC} worth of BTC...")
            buy_start = datetime.now()
            response = await client.post(f"{EXCHANGE_SERVICE_URL}/api/v1/trading/order", json=buy_order)
            buy_end = datetime.now()
            
            print(f"   ⏱️  Buy response time: {(buy_end - buy_start).total_seconds():.2f}s")
            print(f"   📊 Buy status: {response.status_code}")
            
            if response.status_code != 200:
                error_text = response.text
                print(f"   ❌ BUY ORDER FAILED!")
                print(f"   📄 Error: {error_text}")
                
                # Check if it's the Bybit parameter error
                if "createMarketBuyOrderRequiresPrice" in error_text:
                    print(f"   🚨 BYBIT BUY PARAMETER ERROR - FIX NOT WORKING!")
                    return False
                else:
                    print(f"   ❓ Other buy error")
                    return False
            
            buy_result = response.json()
            print(f"   ✅ BUY ORDER SUCCESSFUL!")
            print(f"   📋 Buy Order ID: {buy_result.get('id', 'N/A')}")
            
            # STEP 3: Wait and check balances after buy
            print(f"\n⏳ STEP 3: Waiting for buy order to settle...")
            await asyncio.sleep(3)  # Wait for order to process
            
            response = await client.get(f"{EXCHANGE_SERVICE_URL}/api/v1/account/balance/bybit")
            if response.status_code != 200:
                print(f"❌ Failed to get balance after buy")
                return False
            
            after_buy_balance = response.json()
            after_buy_usdc = after_buy_balance.get('free', {}).get('USDC', 0) or 0
            after_buy_btc = after_buy_balance.get('free', {}).get('BTC', 0) or 0
            
            usdc_spent = initial_usdc - after_buy_usdc
            btc_received = after_buy_btc - initial_btc
            
            print(f"   💰 USDC after buy: {after_buy_usdc:.2f} (spent: {usdc_spent:.2f})")
            print(f"   🪙 BTC after buy: {after_buy_btc:.8f} (received: {btc_received:.8f})")
            
            if btc_received <= 0:
                print(f"   ❌ No BTC received from buy order!")
                return False
            
            print(f"   ✅ Buy order completed successfully!")
            
            # STEP 4: Place SELL order
            print(f"\n💸 STEP 4: Placing SELL order...")
            
            # Sell the BTC we just bought (use slightly less to account for fees)
            btc_to_sell = btc_received * 0.99  # Use 99% to account for potential fees
            
            sell_order = {
                "exchange": "bybit",
                "symbol": "BTC/USDC",
                "order_type": "market", 
                "side": "sell",
                "amount": btc_to_sell,
                "price": None
            }
            
            print(f"   📤 Selling {btc_to_sell:.8f} BTC...")
            sell_start = datetime.now()
            response = await client.post(f"{EXCHANGE_SERVICE_URL}/api/v1/trading/order", json=sell_order)
            sell_end = datetime.now()
            
            print(f"   ⏱️  Sell response time: {(sell_end - sell_start).total_seconds():.2f}s")
            print(f"   📊 Sell status: {response.status_code}")
            
            if response.status_code != 200:
                error_text = response.text
                print(f"   ❌ SELL ORDER FAILED!")
                print(f"   📄 Error: {error_text}")
                print(f"   ⚠️  BTC is still in account: {after_buy_btc:.8f}")
                return False
            
            sell_result = response.json()
            print(f"   ✅ SELL ORDER SUCCESSFUL!")
            print(f"   📋 Sell Order ID: {sell_result.get('id', 'N/A')}")
            
            # STEP 5: Check final balances
            print(f"\n📊 STEP 5: Checking final balances...")
            await asyncio.sleep(3)  # Wait for sell order to process
            
            response = await client.get(f"{EXCHANGE_SERVICE_URL}/api/v1/account/balance/bybit")
            if response.status_code != 200:
                print(f"❌ Failed to get final balance")
                return False
            
            final_balance = response.json()
            final_usdc = final_balance.get('free', {}).get('USDC', 0) or 0
            final_btc = final_balance.get('free', {}).get('BTC', 0) or 0
            
            # Calculate complete cycle results
            total_usdc_change = final_usdc - initial_usdc
            total_btc_change = final_btc - initial_btc
            
            print(f"   💰 Final USDC: {final_usdc:.2f}")
            print(f"   🪙 Final BTC: {final_btc:.8f}")
            print()
            print(f"📊 COMPLETE CYCLE RESULTS:")
            print(f"   💵 USDC change: {total_usdc_change:+.2f} (fees & slippage)")
            print(f"   🪙 BTC change: {total_btc_change:+.8f}")
            print()
            
            # Analyze results
            if abs(total_btc_change) < 0.00001:  # Minimal BTC change (good)
                print(f"   ✅ BTC balance restored (minimal residual)")
            else:
                print(f"   ⚠️  BTC balance change: {total_btc_change:.8f}")
            
            if total_usdc_change > -1.0:  # Less than $1 lost to fees
                print(f"   ✅ USDC loss reasonable (trading fees & slippage)")
            else:
                print(f"   ⚠️  Large USDC loss: ${abs(total_usdc_change):.2f}")
            
            # Success criteria
            buy_success = buy_result.get('id') is not None
            sell_success = sell_result.get('id') is not None
            cycle_complete = abs(total_btc_change) < 0.0001  # Very small BTC remainder
            
            if buy_success and sell_success and cycle_complete:
                print(f"\n🎉 COMPLETE TRADING CYCLE SUCCESSFUL!")
                print(f"   ✅ Buy order: {buy_result.get('id')}")
                print(f"   ✅ Sell order: {sell_result.get('id')}")
                print(f"   ✅ Cycle completed with expected fees")
                return True
            else:
                print(f"\n⚠️  Partial success:")
                print(f"   Buy: {'✅' if buy_success else '❌'}")
                print(f"   Sell: {'✅' if sell_success else '❌'}")
                print(f"   Cycle: {'✅' if cycle_complete else '❌'}")
                return False
                
        except Exception as e:
            print(f"❌ Complete cycle test failed with exception: {e}")
            return False

async def main():
    """Run the complete trading cycle test"""
    print(f"🧪 Bybit Complete Trading Cycle Test")
    print(f"⏰ Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    success = await test_complete_trading_cycle()
    
    print("\n" + "=" * 60)
    if success:
        print("🎉 COMPLETE TRADING CYCLE TEST PASSED!")
        print("✅ Both buy and sell orders work correctly")
        print("✅ Bybit buy parameter fix is working")
        print("✅ Complete trading flow validated")
        print("🚀 System ready for production trading")
    else:
        print("❌ COMPLETE TRADING CYCLE TEST FAILED!")
        print("🔧 Issues detected in the trading flow")
        print("📋 Check the detailed logs above")
    
    print(f"⏰ Completed at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

if __name__ == "__main__":
    asyncio.run(main())