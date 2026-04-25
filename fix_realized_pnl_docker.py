#!/usr/bin/env python3
"""
Fix Realized PnL - Update realized PnL values for closed trades using docker exec
"""

import subprocess
import json
from datetime import datetime

def run_docker_query(query):
    """Run a SQL query via docker exec"""
    try:
        # Escape the query for shell
        escaped_query = query.replace("'", "''")
        
        # Run the query via docker exec
        cmd = [
            "docker", "exec", "-i", "trading-bot-postgres",
            "psql", "-U", "carloslarramba", "-d", "trading_bot_futures",
            "-c", escaped_query
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        print(f"❌ Docker query error: {e}")
        print(f"   Error output: {e.stderr}")
        return None
    except Exception as e:
        print(f"❌ Error running docker query: {e}")
        return None

def format_currency(amount):
    """Format currency amount"""
    if amount == 0:
        return "$0.00"
    elif amount > 0:
        return f"${amount:,.2f}"
    else:
        return f"-${abs(amount):,.2f}"

def main():
    """Main function"""
    print(f"\n🔧 FIXING REALIZED PnL VALUES - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 100)
    
    # Check if docker container is running
    print("🔍 Checking Docker container...")
    try:
        result = subprocess.run(["docker", "ps", "--filter", "name=trading-bot-postgres", "--format", "{{.Names}}"], 
                              capture_output=True, text=True, check=True)
        if "trading-bot-postgres" not in result.stdout:
            print("❌ trading-bot-postgres container is not running")
            return
        print("✅ trading-bot-postgres container is running")
    except Exception as e:
        print(f"❌ Error checking Docker container: {e}")
        return
    
    # Get count of closed trades with incorrect realized PnL
    print(f"\n📊 Analyzing closed trades...")
    count_query = """
    SELECT COUNT(*) as total_closed_trades,
           COUNT(CASE WHEN realized_pnl = 0 THEN 1 END) as trades_with_zero_pnl
    FROM trading.trades 
    WHERE status = 'CLOSED'
    """
    
    count_result = run_docker_query(count_query)
    if count_result:
        print(f"   Query result:")
        print(count_result)
    
    # Get sample of trades that need fixing
    print(f"\n📋 SAMPLE TRADES THAT NEED FIXING:")
    sample_query = """
    SELECT trade_id, pair, exchange, entry_price, exit_price, position_size, realized_pnl,
           (exit_price - entry_price) * position_size as calculated_pnl
    FROM trading.trades 
    WHERE status = 'CLOSED' AND realized_pnl = 0
    ORDER BY created_at DESC 
    LIMIT 10
    """
    
    sample_result = run_docker_query(sample_query)
    if sample_result:
        print(sample_result)
    
    # Calculate total realized PnL that should be
    print(f"\n📊 CALCULATING CORRECT REALIZED PnL:")
    total_query = """
    SELECT 
        COUNT(*) as total_closed_trades,
        COALESCE(SUM((exit_price - entry_price) * position_size), 0) as total_realized_pnl,
        COALESCE(SUM(realized_pnl), 0) as current_realized_pnl,
        COALESCE(SUM((exit_price - entry_price) * position_size) - SUM(realized_pnl), 0) as difference
    FROM trading.trades 
    WHERE status = 'CLOSED' AND exit_price IS NOT NULL AND entry_price IS NOT NULL AND position_size IS NOT NULL
    """
    
    total_result = run_docker_query(total_query)
    if total_result:
        print(f"   Current totals:")
        print(total_result)
    
    # Update realized PnL for all closed trades
    print(f"\n🔧 UPDATING REALIZED PnL VALUES...")
    update_query = """
    UPDATE trading.trades 
    SET realized_pnl = (exit_price - entry_price) * position_size,
        updated_at = NOW()
    WHERE status = 'CLOSED' 
    AND exit_price IS NOT NULL 
    AND entry_price IS NOT NULL 
    AND position_size IS NOT NULL
    """
    
    update_result = run_docker_query(update_query)
    if update_result:
        print(f"   Update result:")
        print(update_result)
    
    # Verify the fix
    print(f"\n✅ VERIFYING THE FIX:")
    verify_query = """
    SELECT 
        COUNT(*) as total_closed_trades,
        COALESCE(SUM(realized_pnl), 0) as total_realized_pnl,
        COALESCE(SUM((exit_price - entry_price) * position_size), 0) as calculated_total
    FROM trading.trades 
    WHERE status = 'CLOSED' AND exit_price IS NOT NULL AND entry_price IS NOT NULL AND position_size IS NOT NULL
    """
    
    verify_result = run_docker_query(verify_query)
    if verify_result:
        print(f"   Verification result:")
        print(verify_result)
    
    # Show updated sample
    print(f"\n📋 UPDATED SAMPLE TRADES:")
    updated_sample_query = """
    SELECT trade_id, pair, exchange, entry_price, exit_price, position_size, realized_pnl,
           (exit_price - entry_price) * position_size as calculated_pnl
    FROM trading.trades 
    WHERE status = 'CLOSED'
    ORDER BY created_at DESC 
    LIMIT 5
    """
    
    updated_sample_result = run_docker_query(updated_sample_query)
    if updated_sample_result:
        print(updated_sample_result)
    
    # Show overall PnL summary
    print(f"\n📊 OVERALL PnL SUMMARY:")
    summary_query = """
    SELECT 
        COUNT(*) as total_trades,
        COUNT(CASE WHEN status = 'CLOSED' THEN 1 END) as closed_trades,
        COUNT(CASE WHEN status = 'OPEN' THEN 1 END) as open_trades,
        COALESCE(SUM(CASE WHEN status = 'CLOSED' THEN realized_pnl ELSE 0 END), 0) as total_realized_pnl,
        COALESCE(SUM(CASE WHEN status = 'OPEN' THEN unrealized_pnl ELSE 0 END), 0) as total_unrealized_pnl,
        COALESCE(SUM(realized_pnl + unrealized_pnl), 0) as total_combined_pnl
    FROM trading.trades
    """
    
    summary_result = run_docker_query(summary_query)
    if summary_result:
        print(f"   Final summary:")
        print(summary_result)
    
    print("\n" + "=" * 100)

if __name__ == "__main__":
    main()
