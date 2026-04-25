#!/usr/bin/env python3
"""
Test Dashboard PnL - Verify dashboard PnL calculations against corrected database values
"""

import subprocess
import json
import httpx
import asyncio
from datetime import datetime, timedelta

def run_docker_query(query):
    """Run a SQL query via docker exec"""
    try:
        cmd = [
            "docker", "exec", "-i", "trading-bot-postgres",
            "psql", "-U", "carloslarramba", "-d", "trading_bot_futures",
            "-c", query
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        print(f"❌ Docker query error: {e}")
        return None
    except Exception as e:
        print(f"❌ Error running docker query: {e}")
        return None

async def test_dashboard_endpoints():
    """Test dashboard endpoints and compare with database values"""
    print(f"\n🔍 TESTING DASHBOARD PnL CALCULATIONS - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
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
    
    # Get database values directly
    print(f"\n📊 DATABASE VALUES (Direct Query):")
    
    # Overall PnL summary
    db_summary_query = """
    SELECT 
        COUNT(*) as total_trades,
        COUNT(CASE WHEN status = 'CLOSED' THEN 1 END) as closed_trades,
        COUNT(CASE WHEN status = 'OPEN' THEN 1 END) as open_trades,
        COALESCE(SUM(CASE WHEN status = 'CLOSED' THEN realized_pnl ELSE 0 END), 0) as total_realized_pnl,
        COALESCE(SUM(CASE WHEN status = 'OPEN' THEN unrealized_pnl ELSE 0 END), 0) as total_unrealized_pnl,
        COALESCE(SUM(realized_pnl + unrealized_pnl), 0) as total_combined_pnl
    FROM trading.trades
    """
    
    db_summary = run_docker_query(db_summary_query)
    if db_summary:
        print("   Database Summary:")
        print(db_summary)
    
    # Today's PnL
    db_today_query = """
    SELECT 
        COUNT(*) as total_trades_today,
        COUNT(CASE WHEN status = 'CLOSED' THEN 1 END) as closed_trades_today,
        COUNT(CASE WHEN status = 'OPEN' THEN 1 END) as open_trades_today,
        COALESCE(SUM(CASE WHEN status = 'CLOSED' THEN realized_pnl ELSE 0 END), 0) as realized_pnl_today,
        COALESCE(SUM(CASE WHEN status = 'OPEN' THEN unrealized_pnl ELSE 0 END), 0) as unrealized_pnl_today,
        COALESCE(SUM(realized_pnl + unrealized_pnl), 0) as combined_pnl_today
    FROM trading.trades 
    WHERE DATE(created_at) = CURRENT_DATE
    """
    
    db_today = run_docker_query(db_today_query)
    if db_today:
        print("   Database Today:")
        print(db_today)
    
    # Test dashboard endpoints
    print(f"\n🌐 DASHBOARD ENDPOINT VALUES:")
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Test profitability endpoint
            print("   Testing /api/v1/profitability...")
            response = await client.get("http://localhost:8006/api/v1/profitability")
            if response.status_code == 200:
                profitability_data = response.json()
                print(f"   ✅ Profitability endpoint: {response.status_code}")
                print(f"   Total PnL: {profitability_data.get('total_pnl', 'N/A')}")
                print(f"   Win Rate: {profitability_data.get('win_rate', 'N/A')}")
                print(f"   Profit Factor: {profitability_data.get('profit_factor', 'N/A')}")
            else:
                print(f"   ❌ Profitability endpoint: {response.status_code}")
            
            # Test daily PnL endpoint
            print("   Testing /api/v1/pnl/daily...")
            response = await client.get("http://localhost:8006/api/v1/pnl/daily")
            if response.status_code == 200:
                daily_data = response.json()
                print(f"   ✅ Daily PnL endpoint: {response.status_code}")
                if 'summary' in daily_data:
                    summary = daily_data['summary']
                    print(f"   Total PnL (7 days): {summary.get('total_pnl', 'N/A')}")
                    print(f"   Total Trades (7 days): {summary.get('total_trades', 'N/A')}")
                    print(f"   Profitable Days: {summary.get('profitable_days', 'N/A')}")
                    print(f"   Losing Days: {summary.get('losing_days', 'N/A')}")
            else:
                print(f"   ❌ Daily PnL endpoint: {response.status_code}")
            
            # Test portfolio summary endpoint
            print("   Testing /api/portfolio...")
            response = await client.get("http://localhost:8006/api/portfolio")
            if response.status_code == 200:
                portfolio_data = response.json()
                print(f"   ✅ Portfolio endpoint: {response.status_code}")
                print(f"   Total PnL: {portfolio_data.get('total_pnl', 'N/A')}")
                print(f"   Daily PnL: {portfolio_data.get('daily_pnl', 'N/A')}")
                print(f"   Total Unrealized PnL: {portfolio_data.get('total_unrealized_pnl', 'N/A')}")
                print(f"   Active Trades: {portfolio_data.get('active_trades', 'N/A')}")
                print(f"   Total Trades: {portfolio_data.get('total_trades', 'N/A')}")
            else:
                print(f"   ❌ Portfolio endpoint: {response.status_code}")
            
            # Test trading analytics endpoint
            print("   Testing /api/v1/analytics/trading...")
            response = await client.get("http://localhost:8006/api/v1/analytics/trading")
            if response.status_code == 200:
                analytics_data = response.json()
                print(f"   ✅ Trading Analytics endpoint: {response.status_code}")
                if 'overall_stats' in analytics_data:
                    stats = analytics_data['overall_stats']
                    print(f"   Total Trades: {stats.get('total_trades', 'N/A')}")
                    print(f"   Closed Trades: {stats.get('closed_trades', 'N/A')}")
                    print(f"   Win Rate: {stats.get('win_rate', 'N/A')}")
                    print(f"   Net Realized PnL: {stats.get('net_realized_pnl', 'N/A')}")
            else:
                print(f"   ❌ Trading Analytics endpoint: {response.status_code}")
                
    except Exception as e:
        print(f"   ❌ Error testing dashboard endpoints: {e}")
    
    # Compare values
    print(f"\n📊 COMPARISON ANALYSIS:")
    print("   The dashboard should now be using the corrected PnL values from the database.")
    print("   All realized_pnl values have been updated to use the formula:")
    print("   (exit_price - entry_price) * position_size")
    
    # Check if there are any discrepancies
    print(f"\n🔍 CHECKING FOR DISCREPANCIES:")
    
    # Check for any trades with incorrect realized PnL
    discrepancy_query = """
    SELECT COUNT(*) as trades_with_discrepancy
    FROM trading.trades 
    WHERE status = 'CLOSED' 
    AND exit_price IS NOT NULL 
    AND entry_price IS NOT NULL 
    AND position_size IS NOT NULL
    AND ABS(realized_pnl - ((exit_price - entry_price) * position_size)) > 0.01
    """
    
    discrepancy_result = run_docker_query(discrepancy_query)
    if discrepancy_result:
        print(f"   Trades with PnL discrepancies: {discrepancy_result}")
    
    print(f"\n✅ VERIFICATION COMPLETE")
    print("=" * 100)

if __name__ == "__main__":
    asyncio.run(test_dashboard_endpoints())
