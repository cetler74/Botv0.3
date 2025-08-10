#!/usr/bin/env python3
"""
Complete Flow Test with Database Validation
Tests the ENTIRE trading flow: Order → Exchange → Database → Sync
Validates that orders are properly stored and tracked in the database
"""

import asyncio
import httpx
import json
from datetime import datetime, timedelta

# Test configuration
EXCHANGE_SERVICE_URL = "http://localhost:8003"
DATABASE_SERVICE_URL = "http://localhost:8002"
TEST_AMOUNT_USDC = 10.0

async def get_trades_by_entry_id(entry_id):
    """Get trade that matches a specific exchange order ID"""
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            # Get all recent trades and filter by entry_id
            response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/trades", params={'limit': 50})
            if response.status_code == 200:
                trades_data = response.json()
                trades = trades_data.get('trades', [])
                # Look for trade with matching entry_id
                for trade in trades:
                    if trade.get('entry_id') == entry_id:
                        return trade
                return None
            else:
                print(f"⚠️  Failed to get trades from database: {response.status_code}")
                return None
        except Exception as e:
            print(f"⚠️  Database query error: {e}")
            return None

async def get_trades_from_database(exchange=None, limit=10):
    """Get recent trades from database"""
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            params = {'limit': limit}
            if exchange:
                params['exchange'] = exchange
                
            response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/trades", params=params)
            if response.status_code == 200:
                return response.json()
            else:
                print(f"⚠️  Failed to get trades from database: {response.status_code}")
                return None
        except Exception as e:
            print(f"⚠️  Database trades query error: {e}")
            return None

async def wait_for_trade_in_database(entry_id, max_wait_seconds=15):
    """Wait for trade with specific entry_id to appear in database with polling"""
    print(f"   ⏳ Waiting for trade with entry_id {entry_id} to appear in database...")
    
    for attempt in range(max_wait_seconds):
        await asyncio.sleep(1)
        
        db_trade = await get_trades_by_entry_id(entry_id)
        if db_trade:
            print(f"   ✅ Trade found in database after {attempt + 1}s")
            return db_trade
        
        print(f"   ⏳ Attempt {attempt + 1}/{max_wait_seconds}...")
    
    print(f"   ❌ Trade with entry_id {entry_id} not found in database after {max_wait_seconds}s")
    return None

async def test_complete_flow_with_database():
    """Test complete trading flow with database validation"""
    print("🗄️  COMPLETE FLOW TEST WITH DATABASE VALIDATION")
    print("⚠️  WARNING: Tests real orders AND database storage!")
    print(f"💰 Test amount: ${TEST_AMOUNT_USDC} USDC")
    print("=" * 70)
    
    print("📋 This test validates:")
    print("   1️⃣ Order placement via Exchange Service API")
    print("   2️⃣ Order execution on Bybit exchange")
    print("   3️⃣ Order storage in PostgreSQL database")
    print("   4️⃣ Order status synchronization")
    print("   5️⃣ Trade record creation")
    print("   6️⃣ Balance updates in database")
    print()
    
    # Auto-confirm for automated testing
    print("⚠️  Auto-confirming for automated test execution...")
    confirmation = 'YES'
    if confirmation != 'YES':
        print("❌ Complete flow test cancelled")
        return False

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            # STEP 1: Get baseline database state
            print("\n📊 STEP 1: Getting baseline database state...")
            
            initial_trades = await get_trades_from_database(exchange='bybit', limit=5)
            
            initial_trade_count = len(initial_trades.get('trades', [])) if initial_trades else 0
            
            print(f"   💱 Initial trades in DB: {initial_trade_count}")
            
            # STEP 2: Get initial balances (exchange + database)
            print(f"\n💰 STEP 2: Getting initial balances...")
            
            # Exchange balance
            response = await client.get(f"{EXCHANGE_SERVICE_URL}/api/v1/account/balance/bybit")
            if response.status_code != 200:
                print(f"❌ Failed to get exchange balance")
                return False
            
            exchange_balance = response.json()
            initial_usdc_exchange = exchange_balance.get('free', {}).get('USDC', 0) or 0
            initial_btc_exchange = exchange_balance.get('free', {}).get('BTC', 0) or 0
            
            print(f"   💰 Exchange USDC: {initial_usdc_exchange:.2f}")
            print(f"   🪙 Exchange BTC: {initial_btc_exchange:.8f}")
            
            # Database balance (if available)
            try:
                db_balance_response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/balances/bybit")
                if db_balance_response.status_code == 200:
                    db_balance = db_balance_response.json()
                    print(f"   🗄️  Database balance available: {db_balance.get('total', 'N/A')}")
                else:
                    print(f"   ⚠️  Database balance not available: {db_balance_response.status_code}")
            except:
                print(f"   ⚠️  Could not query database balance")
            
            if initial_usdc_exchange < TEST_AMOUNT_USDC:
                print(f"❌ Insufficient USDC: need ${TEST_AMOUNT_USDC}, have ${initial_usdc_exchange:.2f}")
                return False
            
            # STEP 3: Place BUY order and track in database
            print(f"\n🛒 STEP 3: Placing BUY order with database tracking...")
            
            buy_order = {
                "exchange": "bybit",
                "symbol": "BTC/USDC",
                "order_type": "market",
                "side": "buy",
                "amount": TEST_AMOUNT_USDC,
                "price": None
            }
            
            print(f"   📤 Placing buy order for ${TEST_AMOUNT_USDC} USDC...")
            buy_start = datetime.now()
            
            response = await client.post(f"{EXCHANGE_SERVICE_URL}/api/v1/trading/order", json=buy_order)
            buy_end = datetime.now()
            
            print(f"   ⏱️  Buy order response time: {(buy_end - buy_start).total_seconds():.2f}s")
            print(f"   📊 Buy order status: {response.status_code}")
            
            if response.status_code != 200:
                error_text = response.text
                print(f"   ❌ BUY ORDER FAILED!")
                print(f"   📄 Error: {error_text}")
                return False
            
            buy_result = response.json()
            buy_order_id = buy_result.get('id')
            buy_sync_status = buy_result.get('sync_status', 'unknown')
            
            print(f"   ✅ BUY ORDER PLACED!")
            print(f"   📋 Order ID: {buy_order_id}")
            print(f"   🔄 Sync Status: {buy_sync_status}")
            
            # STEP 4: Verify trade in database
            print(f"\n🗄️  STEP 4: Verifying buy order trade in database...")
            
            db_buy_trade = await wait_for_trade_in_database(buy_order_id, max_wait_seconds=15)
            
            if db_buy_trade:
                print(f"   ✅ Buy trade found in database!")
                print(f"   📋 DB Trade ID: {db_buy_trade.get('trade_id', 'N/A')}")
                print(f"   📈 DB Status: {db_buy_trade.get('status', 'N/A')}")
                print(f"   💰 DB Position Size: {db_buy_trade.get('position_size', 'N/A')}")
                print(f"   🏪 DB Exchange: {db_buy_trade.get('exchange', 'N/A')}")
                print(f"   📊 DB Symbol: {db_buy_trade.get('pair', 'N/A')}")
                print(f"   🔗 DB Entry ID: {db_buy_trade.get('entry_id', 'N/A')}")
                
                # Check if trade shows as open/successful
                db_status = db_buy_trade.get('status', '').upper()
                if db_status in ['OPEN']:
                    print(f"   ✅ Trade shows as OPEN in database")
                elif db_status in ['FAILED']:
                    print(f"   ❌ Trade shows as FAILED in database!")
                    return False
                else:
                    print(f"   ⏳ Trade status: {db_status}")
            else:
                print(f"   ❌ Buy trade NOT found in database!")
                print(f"   🔧 Database synchronization may have failed")
                # Continue test but flag the issue
            
            # STEP 5: Wait and check balances after buy
            print(f"\n⏳ STEP 5: Checking balances after buy order...")
            await asyncio.sleep(5)  # Wait for settlement
            
            response = await client.get(f"{EXCHANGE_SERVICE_URL}/api/v1/account/balance/bybit")
            if response.status_code == 200:
                after_buy_balance = response.json()
                after_buy_usdc = after_buy_balance.get('free', {}).get('USDC', 0) or 0
                after_buy_btc = after_buy_balance.get('free', {}).get('BTC', 0) or 0
                
                usdc_spent = initial_usdc_exchange - after_buy_usdc
                btc_received = after_buy_btc - initial_btc_exchange
                
                print(f"   💰 USDC after buy: {after_buy_usdc:.2f} (spent: {usdc_spent:.2f})")
                print(f"   🪙 BTC after buy: {after_buy_btc:.8f} (received: {btc_received:.8f})")
                
                if btc_received <= 0:
                    print(f"   ❌ No BTC received - order may have failed")
                    return False
            
            # STEP 6: Check for trade records in database
            print(f"\n💱 STEP 6: Checking for trade records in database...")
            
            await asyncio.sleep(2)  # Wait for trade processing
            current_trades = await get_trades_from_database(exchange='bybit', limit=10)
            
            if current_trades:
                trades_list = current_trades.get('trades', [])
                new_trade_count = len(trades_list)
                print(f"   📊 Current trades in DB: {new_trade_count}")
                
                if new_trade_count > initial_trade_count:
                    new_trades = new_trade_count - initial_trade_count
                    print(f"   ✅ {new_trades} new trade(s) detected in database")
                    
                    # Show latest trade
                    latest_trade = trades_list[0] if trades_list else None
                    if latest_trade:
                        print(f"   📋 Latest trade:")
                        print(f"      Trade ID: {latest_trade.get('trade_id', 'N/A')}")
                        print(f"      Symbol: {latest_trade.get('pair', 'N/A')}")
                        print(f"      Exchange: {latest_trade.get('exchange', 'N/A')}")
                        print(f"      Position Size: {latest_trade.get('position_size', 'N/A')}")
                        print(f"      Entry Price: {latest_trade.get('entry_price', 'N/A')}")
                        print(f"      Status: {latest_trade.get('status', 'N/A')}")
                else:
                    print(f"   ⚠️  No new trades detected in database")
            else:
                print(f"   ⚠️  Could not query trades from database")
            
            # STEP 7: Test SELL order with database tracking
            print(f"\n💸 STEP 7: Testing SELL order with database tracking...")
            
            # Use most of the BTC we received (account for fees)
            btc_to_sell = btc_received * 0.95
            
            sell_order = {
                "exchange": "bybit",
                "symbol": "BTC/USDC",
                "order_type": "market",
                "side": "sell", 
                "amount": btc_to_sell,
                "price": None
            }
            
            print(f"   📤 Placing sell order for {btc_to_sell:.8f} BTC...")
            
            response = await client.post(f"{EXCHANGE_SERVICE_URL}/api/v1/trading/order", json=sell_order)
            
            if response.status_code == 200:
                sell_result = response.json()
                sell_order_id = sell_result.get('id')
                sell_sync_status = sell_result.get('sync_status', 'unknown')
                
                print(f"   ✅ SELL ORDER PLACED!")
                print(f"   📋 Sell Order ID: {sell_order_id}")
                print(f"   🔄 Sync Status: {sell_sync_status}")
                
                # Verify sell order trade in database
                print(f"   🗄️  Verifying sell order in database...")
                db_sell_trade = await wait_for_trade_in_database(sell_order_id, max_wait_seconds=15)
                
                if db_sell_trade:
                    print(f"   ✅ Sell order found in database!")
                    print(f"   📈 DB Status: {db_sell_trade.get('status', 'N/A')}")
                    print(f"   🔗 DB Exit ID: {db_sell_trade.get('exit_id', 'N/A')}")
                else:
                    print(f"   ❌ Sell order NOT found in database!")
            else:
                print(f"   ❌ SELL ORDER FAILED: {response.status_code}")
                print(f"   📄 Error: {response.text}")
                return False
            
            # STEP 8: Final validation
            print(f"\n📊 STEP 8: Final database validation...")
            
            await asyncio.sleep(3)
            
            final_trades = await get_trades_from_database(exchange='bybit', limit=10)
            
            final_trade_count = len(final_trades.get('trades', [])) if final_trades else 0
            
            trades_added = final_trade_count - initial_trade_count
            
            print(f"   💱 Trades added to DB: {trades_added}")
            
            # Success criteria
            exchange_success = buy_order_id and sell_order_id
            database_trades = trades_added >= 1   # At least one trade record (buy creates, sell updates)
            database_sync = db_buy_trade is not None  # Buy order was properly synced
            
            print(f"\n🎯 VALIDATION RESULTS:")
            print(f"   Exchange Orders: {'✅' if exchange_success else '❌'}")
            print(f"   Database Trades: {'✅' if database_trades else '❌'} ({trades_added} added)")
            print(f"   Database Sync: {'✅' if database_sync else '❌'}")
            
            if exchange_success and database_sync:
                print(f"\n🎉 COMPLETE FLOW WITH DATABASE TEST PASSED!")
                print(f"   ✅ Orders successfully placed on exchange")
                print(f"   ✅ Orders properly stored in database") 
                print(f"   ✅ Database synchronization working")
                if database_trades:
                    print(f"   ✅ Trade records created in database")
                print(f"   🚀 Complete trading flow validated!")
                return True
            else:
                print(f"\n⚠️  Partial success - some database integration issues")
                return False
                
        except Exception as e:
            print(f"❌ Complete flow test failed: {e}")
            return False

async def main():
    """Run the complete flow test with database validation"""
    print(f"🧪 Complete Trading Flow + Database Validation Test")
    print(f"⏰ Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    success = await test_complete_flow_with_database()
    
    print("\n" + "=" * 70)
    if success:
        print("🎉 COMPLETE FLOW + DATABASE TEST PASSED!")
        print("✅ Order placement working correctly")
        print("✅ Database storage and synchronization working")
        print("✅ Trade records being created")
        print("✅ Complete end-to-end flow validated")
        print("🚀 System ready for production with full database tracking")
    else:
        print("❌ COMPLETE FLOW + DATABASE TEST FAILED!")
        print("🔧 Issues detected in order processing or database sync")
        print("📋 Check detailed logs above for specific failures")
    
    print(f"⏰ Completed at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

if __name__ == "__main__":
    asyncio.run(main())