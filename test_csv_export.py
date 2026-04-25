#!/usr/bin/env python3
"""
Test CSV Export - Verify the CSV export functionality works correctly
"""

import httpx
import asyncio
from datetime import datetime

async def test_csv_export():
    """Test the CSV export endpoint"""
    print(f"\n🔍 TESTING CSV EXPORT FUNCTIONALITY - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 100)
    
    # Check if dashboard service is running
    print("🔍 Checking if dashboard service is running...")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get("http://localhost:8006/health")
            if response.status_code == 200:
                print("✅ Dashboard service is running")
            else:
                print("❌ Dashboard service is not responding")
                return
    except Exception as e:
        print(f"❌ Dashboard service is not accessible: {e}")
        return
    
    # Test CSV export endpoint
    print(f"\n📊 TESTING CSV EXPORT ENDPOINT:")
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Test basic export
            print("   Testing basic CSV export...")
            response = await client.get("http://localhost:8006/api/v1/trades/export/csv")
            
            if response.status_code == 200:
                data = response.json()
                print(f"   ✅ CSV Export endpoint: {response.status_code}")
                print(f"   Status: {data.get('status', 'N/A')}")
                print(f"   Filename: {data.get('filename', 'N/A')}")
                print(f"   Trade Count: {data.get('trade_count', 'N/A')}")
                print(f"   CSV Content Length: {len(data.get('csv_content', ''))} characters")
                
                # Check if CSV content is valid
                csv_content = data.get('csv_content', '')
                if csv_content:
                    lines = csv_content.strip().split('\n')
                    print(f"   CSV Lines: {len(lines)}")
                    if len(lines) > 1:
                        print(f"   Headers: {lines[0]}")
                        print(f"   Sample Data Row: {lines[1] if len(lines) > 1 else 'No data'}")
                    else:
                        print("   ⚠️  CSV contains only headers (no data)")
                else:
                    print("   ❌ No CSV content received")
                
            else:
                print(f"   ❌ CSV Export endpoint: {response.status_code}")
                print(f"   Error: {response.text}")
            
            # Test export with filters
            print(f"\n   Testing CSV export with filters...")
            params = {
                'status': 'CLOSED',
                'limit': 100
            }
            response = await client.get("http://localhost:8006/api/v1/trades/export/csv", params=params)
            
            if response.status_code == 200:
                data = response.json()
                print(f"   ✅ Filtered CSV Export: {response.status_code}")
                print(f"   Trade Count: {data.get('trade_count', 'N/A')}")
                
                # Check if we got closed trades
                csv_content = data.get('csv_content', '')
                if csv_content:
                    lines = csv_content.strip().split('\n')
                    if len(lines) > 1:
                        # Check if the data contains CLOSED status
                        closed_trades = [line for line in lines[1:] if 'CLOSED' in line]
                        print(f"   Closed Trades in Export: {len(closed_trades)}")
                    else:
                        print("   ⚠️  No data in filtered export")
                else:
                    print("   ❌ No CSV content in filtered export")
            else:
                print(f"   ❌ Filtered CSV Export: {response.status_code}")
                print(f"   Error: {response.text}")
                
    except Exception as e:
        print(f"   ❌ Error testing CSV export: {e}")
    
    print(f"\n✅ CSV EXPORT TESTING COMPLETE")
    print("=" * 100)

if __name__ == "__main__":
    asyncio.run(test_csv_export())
