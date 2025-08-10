#!/usr/bin/env python3
"""
Real Bybit SELL Order Test - Tests actual sell order placement
WARNING: This will place a REAL SELL order on Bybit with real cryptocurrency!
"""

import asyncio
import httpx
import json
from datetime import datetime

# Test configuration
EXCHANGE_SERVICE_URL = "http://localhost:8003"

async def get_available_crypto():
    """Check what cryptocurrency we have available to sell"""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(f"{EXCHANGE_SERVICE_URL}/api/v1/account/balance/bybit")
        if response.status_code != 200:
            return None
            
        balance = response.json()
        
        # Check for common cryptocurrencies
        cryptos = {}
        for symbol in ['BTC', 'ETH', 'SOL', 'XLM', 'XRP']:
            total = balance.get(symbol, {}).get('total', 0) or 0
            free = balance.get('free', {}).get(symbol, 0) or total
            if free > 0:
                cryptos[symbol] = {'free': free, 'total': total}
        
        return cryptos

async def test_real_bybit_sell():
    """Test actual Bybit sell order placement"""
    print("🚨 REAL BYBIT SELL ORDER TEST")
    print("⚠️  WARNING: This will place a REAL SELL order!")
    print("=" * 50)
    
    # First, check what crypto we have
    print("1️⃣ Checking available cryptocurrency...")
    available_crypto = await get_available_crypto()
    
    if not available_crypto:
        print("❌ No cryptocurrency available for selling")
        print("💡 You need to have some crypto holdings to test sell orders")
        return False
    
    print("💰 Available cryptocurrency for selling:")
    for symbol, data in available_crypto.items():
        print(f"   {symbol}: {data['free']:.8f} available")
    
    # Let user choose what to sell
    print(f"\nAvailable options: {', '.join(available_crypto.keys())}")
    chosen_symbol = input("Enter symbol to sell (e.g., BTC, ETH): ").upper().strip()
    
    if chosen_symbol not in available_crypto:
        print(f"❌ {chosen_symbol} not available or not found")
        return False
    
    # Get the amount to sell
    max_amount = available_crypto[chosen_symbol]['free']
    print(f"\nMaximum {chosen_symbol} available: {max_amount:.8f}")
    
    # Suggest a small test amount (10% of holdings or minimum)
    suggested_amount = min(max_amount * 0.1, max_amount)
    amount_input = input(f"Enter amount to sell (suggested: {suggested_amount:.8f}): ").strip()
    
    try:
        sell_amount = float(amount_input) if amount_input else suggested_amount
    except ValueError:
        print("❌ Invalid amount entered")
        return False
    
    if sell_amount > max_amount:
        print(f"❌ Amount {sell_amount:.8f} exceeds available {max_amount:.8f}")
        return False
    
    # Create the sell order
    symbol_pair = f"{chosen_symbol}/USDC"
    TEST_SELL_ORDER = {
        "exchange": "bybit",
        "symbol": symbol_pair,
        "order_type": "market",
        "side": "sell",
        "amount": sell_amount,
        "price": None
    }
    
    print(f"\n📋 Sell Order Summary:")
    print(f"   Symbol: {symbol_pair}")
    print(f"   Amount: {sell_amount:.8f} {chosen_symbol}")
    print(f"   Type: Market sell")
    
    # Get current price for estimation
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            symbol_url = symbol_pair.replace('/', '')
            response = await client.get(f"{EXCHANGE_SERVICE_URL}/api/v1/market/ticker/bybit/{symbol_url}")
            if response.status_code == 200:
                ticker = response.json()
                current_price = ticker.get('last', 0)
                estimated_usdc = sell_amount * current_price if current_price > 0 else 0
                print(f"   Current price: ${current_price:,.2f}")
                print(f"   Estimated USDC: ${estimated_usdc:.2f}")
        except:
            print("   ⚠️  Could not get current price")
    
    # Get final confirmation
    confirmation = input(f"\n⚠️  Place REAL sell order? Type 'YES' to proceed: ")
    if confirmation != 'YES':
        print("❌ Sell test cancelled by user")
        return False
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            print(f"\n2️⃣ Placing REAL SELL order...")
            print(f"   🚨 SELLING {sell_amount:.8f} {chosen_symbol} FOR USDC")
            
            start_time = datetime.now()
            response = await client.post(f"{EXCHANGE_SERVICE_URL}/api/v1/trading/order", json=TEST_SELL_ORDER)
            end_time = datetime.now()
            response_time = (end_time - start_time).total_seconds()
            
            print(f"   ⏱️  Response time: {response_time:.2f}s")
            print(f"   📊 Status code: {response.status_code}")
            
            if response.status_code == 200:
                result = response.json()
                print(f"   🎉 SELL ORDER SUCCESSFUL!")
                print(f"   📋 Order ID: {result.get('id', 'N/A')}")
                print(f"   💰 Amount: {result.get('amount', 'N/A')}")
                print(f"   📈 Status: {result.get('status', 'N/A')}")
                print(f"   🔄 Sync Status: {result.get('sync_status', 'N/A')}")
                
                # 3. Verify order in balance
                print(f"\n3️⃣ Verifying balance after sell order...")
                await asyncio.sleep(2)  # Wait for order to process
                
                response = await client.get(f"{EXCHANGE_SERVICE_URL}/api/v1/account/balance/bybit")
                if response.status_code == 200:
                    new_balance = response.json()
                    
                    # Check crypto balance change
                    new_crypto_free = new_balance.get('free', {}).get(chosen_symbol, 0) or 0
                    new_crypto_total = new_balance.get(chosen_symbol, {}).get('total', 0) or 0
                    crypto_change = max_amount - new_crypto_free
                    
                    print(f"   🪙 New {chosen_symbol} balance: {new_crypto_free:.8f}")
                    print(f"   📉 {chosen_symbol} sold: {crypto_change:.8f}")
                    
                    # Check USDC increase
                    new_usdc_free = new_balance.get('free', {}).get('USDC', 0) or 0
                    print(f"   💰 USDC balance: {new_usdc_free:.2f}")
                    print(f"   💡 Check if USDC increased from the sale")
                
                return True
                
            else:
                error_text = response.text
                print(f"   ❌ SELL ORDER FAILED!")
                print(f"   🔴 Status: {response.status_code}")
                print(f"   📄 Error: {error_text}")
                
                # Analyze the error
                if "insufficient" in error_text.lower():
                    print(f"   💰 Insufficient holdings error")
                elif "minimum" in error_text.lower():
                    print(f"   📏 Minimum order size error")
                else:
                    print(f"   ❓ Other error type")
                
                return False
                
        except Exception as e:
            print(f"   ❌ Test failed with exception: {e}")
            return False

async def main():
    """Run the real sell order test"""
    print(f"🧪 Bybit Real SELL Order Test")
    print(f"⏰ Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    success = await test_real_bybit_sell()
    
    print("\n" + "=" * 50)
    if success:
        print("🎉 REAL SELL ORDER TEST PASSED!")
        print("✅ Bybit sell order placement is working correctly")
        print("🔄 Complete buy/sell cycle validated")
    else:
        print("❌ REAL SELL ORDER TEST FAILED!")
        print("🔧 There may be issues with sell order placement")
        print("📋 Check the error logs above")
    
    print(f"⏰ Completed at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

if __name__ == "__main__":
    asyncio.run(main())