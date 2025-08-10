#!/usr/bin/env python3
"""
Test the enhanced trade validation system
"""

import asyncio
import httpx
import json

async def test_enhanced_validation():
    """Test the enhanced validation system"""
    
    async with httpx.AsyncClient() as client:
        try:
            print("🔍 Testing enhanced trade validation system...")
            
            # Test 1: Validate trades for Crypto.com by exchange IDs
            print("\n1. Running comprehensive validation for Crypto.com...")
            validation_data = {"exchange": "cryptocom"}
            
            validation_response = await client.post(
                "http://localhost:8002/api/v1/trades/validate-by-exchange-ids",
                json=validation_data
            )
            
            if validation_response.status_code == 200:
                result = validation_response.json()
                print(f"✅ Validation successful:")
                print(f"   - Total trades checked: {result['total_trades_checked']}")
                print(f"   - Validated trades: {result['validated_trades']}")
                print(f"   - Missing entry IDs: {len(result['missing_entry_ids'])}")
                print(f"   - Missing exit IDs: {len(result['missing_exit_ids'])}")
                print(f"   - Trades needing validation: {len(result['trades_needing_validation'])}")
                
                if result['missing_entry_ids']:
                    print(f"\n⚠️ Trades missing entry IDs:")
                    for trade in result['missing_entry_ids']:
                        print(f"   - {trade['trade_id']}: {trade['pair']} ({trade['status']})")
                
                if result['missing_exit_ids']:
                    print(f"\n⚠️ Closed trades missing exit IDs:")
                    for trade in result['missing_exit_ids']:
                        print(f"   - {trade['trade_id']}: {trade['pair']} (entry_id: {trade['entry_id']})")
                
                # Show sample of trades needing validation
                if result['trades_needing_validation']:
                    print(f"\n📋 Sample trades with exchange IDs (first 5):")
                    for trade in result['trades_needing_validation'][:5]:
                        print(f"   - {trade['trade_id']}: {trade['pair']} (entry: {trade['entry_id']}, exit: {trade['exit_id']}, status: {trade['status']})")
                        
            else:
                print(f"❌ Validation failed: {validation_response.status_code} - {validation_response.text}")
            
            # Test 2: Run a sample comprehensive sync to test the enhanced validation logic
            print("\n2. Testing enhanced sync validation with sample data...")
            
            # Create test sync data based on known orders
            test_sync_data = {
                "exchange": "cryptocom",
                "recent_trades": [
                    {
                        "id": "6530219581761276035",  # Known ACX entry order
                        "symbol": "ACX/USD",
                        "side": "buy",
                        "amount": 275.81,
                        "price": 0.18059,
                        "timestamp": 1721808997000,
                        "datetime": "2024-07-24T10:36:37Z"
                    }
                ],
                "current_balance": {},
                "open_orders": [],
                "sync_type": "validation_test"
            }
            
            sync_response = await client.post(
                "http://localhost:8002/api/v1/trades/comprehensive-sync",
                json=test_sync_data
            )
            
            if sync_response.status_code == 200:
                result = sync_response.json()
                print(f"✅ Enhanced sync test successful:")
                print(f"   - Processed trades: {result['processed_trades']}")
                print(f"   - Created trades: {result['created_trades']}")
                print(f"   - Closed trades: {result['closed_trades']}")
                print(f"   - Validated trades: {result.get('validated_trades', 0)}")
                print(f"   - Corrected trades: {result.get('corrected_trades', 0)}")
            else:
                print(f"❌ Enhanced sync test failed: {sync_response.status_code} - {sync_response.text}")
                
        except Exception as e:
            print(f"❌ Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_enhanced_validation())