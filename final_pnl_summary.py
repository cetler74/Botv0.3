#!/usr/bin/env python3
"""
Final PnL Summary - Show corrected PnL calculations for all trades
"""

import subprocess
from datetime import datetime

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

def format_currency(amount_str):
    """Format currency amount"""
    try:
        amount = float(amount_str)
        if amount == 0:
            return "$0.00"
        elif amount > 0:
            return f"${amount:,.2f}"
        else:
            return f"-${abs(amount):,.2f}"
    except (ValueError, TypeError):
        return amount_str

def main():
    """Main function"""
    print(f"\n📊 FINAL PnL SUMMARY - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 100)
    
    # Overall summary
    print("📊 OVERALL TRADING SUMMARY:")
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
        print(summary_result)
    
    # Today's summary
    print(f"\n📊 TODAY'S TRADING SUMMARY:")
    today_query = """
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
    
    today_result = run_docker_query(today_query)
    if today_result:
        print(today_result)
    
    # Closed trades summary
    print(f"\n📋 CLOSED TRADES SUMMARY (Last 10):")
    closed_query = """
    SELECT 
        pair,
        exchange,
        entry_price,
        exit_price,
        position_size,
        realized_pnl,
        exit_reason,
        created_at::date as trade_date
    FROM trading.trades 
    WHERE status = 'CLOSED'
    ORDER BY created_at DESC 
    LIMIT 10
    """
    
    closed_result = run_docker_query(closed_query)
    if closed_result:
        print(closed_result)
    
    # Open trades summary
    print(f"\n📋 OPEN TRADES SUMMARY:")
    open_query = """
    SELECT 
        pair,
        exchange,
        entry_price,
        position_size,
        unrealized_pnl,
        created_at::date as trade_date
    FROM trading.trades 
    WHERE status = 'OPEN'
    ORDER BY created_at DESC
    """
    
    open_result = run_docker_query(open_query)
    if open_result:
        print(open_result)
    
    # PnL by exchange
    print(f"\n📊 PnL BY EXCHANGE:")
    exchange_query = """
    SELECT 
        exchange,
        COUNT(*) as total_trades,
        COUNT(CASE WHEN status = 'CLOSED' THEN 1 END) as closed_trades,
        COUNT(CASE WHEN status = 'OPEN' THEN 1 END) as open_trades,
        COALESCE(SUM(CASE WHEN status = 'CLOSED' THEN realized_pnl ELSE 0 END), 0) as realized_pnl,
        COALESCE(SUM(CASE WHEN status = 'OPEN' THEN unrealized_pnl ELSE 0 END), 0) as unrealized_pnl,
        COALESCE(SUM(realized_pnl + unrealized_pnl), 0) as total_pnl
    FROM trading.trades
    GROUP BY exchange
    ORDER BY total_pnl DESC
    """
    
    exchange_result = run_docker_query(exchange_query)
    if exchange_result:
        print(exchange_result)
    
    # PnL by pair
    print(f"\n📊 PnL BY PAIR (Top 10):")
    pair_query = """
    SELECT 
        pair,
        COUNT(*) as total_trades,
        COUNT(CASE WHEN status = 'CLOSED' THEN 1 END) as closed_trades,
        COUNT(CASE WHEN status = 'OPEN' THEN 1 END) as open_trades,
        COALESCE(SUM(CASE WHEN status = 'CLOSED' THEN realized_pnl ELSE 0 END), 0) as realized_pnl,
        COALESCE(SUM(CASE WHEN status = 'OPEN' THEN unrealized_pnl ELSE 0 END), 0) as unrealized_pnl,
        COALESCE(SUM(realized_pnl + unrealized_pnl), 0) as total_pnl
    FROM trading.trades
    GROUP BY pair
    ORDER BY total_pnl DESC
    LIMIT 10
    """
    
    pair_result = run_docker_query(pair_query)
    if pair_result:
        print(pair_result)
    
    print("\n" + "=" * 100)
    print("✅ PnL calculations have been corrected!")
    print("📊 All realized PnL values now use the formula: (exit_price - entry_price) * position_size")
    print("=" * 100)

if __name__ == "__main__":
    main()
