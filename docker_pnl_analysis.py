#!/usr/bin/env python3
"""
Docker PnL Analysis - Connect to PostgreSQL via Docker and analyze trades
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
            "-t", "-A", "-F", ",", "-c", escaped_query
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

def run_docker_query_json(query):
    """Run a SQL query via docker exec and return JSON format"""
    try:
        # Escape the query for shell
        escaped_query = query.replace("'", "''")
        
        # Run the query via docker exec with JSON output
        cmd = [
            "docker", "exec", "-i", "trading-bot-postgres",
            "psql", "-U", "carloslarramba", "-d", "trading_bot_futures",
            "-t", "-A", "-F", ",", "-c", escaped_query
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        
        # Parse the CSV output
        lines = result.stdout.strip().split('\n')
        if not lines or lines[0] == '':
            return []
        
        # Get headers from first line
        headers = lines[0].split(',')
        
        # Parse data rows
        data = []
        for line in lines[1:]:
            if line.strip():
                values = line.split(',')
                row = {}
                for i, header in enumerate(headers):
                    if i < len(values):
                        row[header.strip()] = values[i].strip()
                data.append(row)
        
        return data
    except subprocess.CalledProcessError as e:
        print(f"❌ Docker query error: {e}")
        print(f"   Error output: {e.stderr}")
        return []
    except Exception as e:
        print(f"❌ Error running docker query: {e}")
        return []

def format_currency(amount_str):
    """Format currency amount"""
    if not amount_str or amount_str == 'NULL' or amount_str == '':
        return "NULL"
    
    try:
        amount = float(amount_str)
        if amount == 0:
            return "$0.00"
        elif amount > 0:
            return f"${amount:,.2f}"
        else:
            return f"-${abs(amount):,.2f}"
    except ValueError:
        return amount_str

def calculate_realized_pnl(entry_price, exit_price, position_size, side='buy'):
    """Calculate realized PnL for a closed trade"""
    try:
        entry = float(entry_price) if entry_price and entry_price != 'NULL' else 0
        exit = float(exit_price) if exit_price and exit_price != 'NULL' else 0
        size = float(position_size) if position_size and position_size != 'NULL' else 0
        
        if not entry or not exit or not size:
            return 0.0
        
        if side == 'buy':
            # For buy orders: (exit_price - entry_price) * position_size
            pnl = (exit - entry) * size
        else:
            # For sell orders: (entry_price - exit_price) * position_size
            pnl = (entry - exit) * size
        
        return pnl
    except (ValueError, TypeError):
        return 0.0

def main():
    """Main function"""
    print(f"\n📊 DOCKER PnL ANALYSIS - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
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
    
    # Get all trades
    print(f"\n📊 Fetching trades from database...")
    trades_query = """
    SELECT 
        trade_id,
        pair,
        exchange,
        status,
        entry_price,
        exit_price,
        position_size,
        unrealized_pnl,
        realized_pnl,
        entry_time,
        exit_time,
        created_at,
        updated_at,
        entry_reason,
        exit_reason,
        strategy
    FROM trading.trades 
    ORDER BY created_at DESC
    """
    
    trades = run_docker_query_json(trades_query)
    
    if not trades:
        print("❌ No trades found or error connecting to database")
        return
    
    print(f"📊 Found {len(trades)} trades")
    print()
    
    # Analyze each trade
    closed_trades = []
    open_trades = []
    total_realized_pnl = 0.0
    total_unrealized_pnl = 0.0
    
    for i, trade in enumerate(trades, 1):
        trade_id = trade.get('trade_id', 'N/A')
        pair = trade.get('pair', 'N/A')
        exchange = trade.get('exchange', 'N/A')
        status = trade.get('status', 'N/A')
        entry_price = trade.get('entry_price', 'NULL')
        exit_price = trade.get('exit_price', 'NULL')
        position_size = trade.get('position_size', 'NULL')
        stored_realized_pnl = trade.get('realized_pnl', '0')
        stored_unrealized_pnl = trade.get('unrealized_pnl', '0')
        entry_time = trade.get('entry_time', 'N/A')
        exit_time = trade.get('exit_time', 'N/A')
        exit_reason = trade.get('exit_reason', 'N/A')
        
        print(f"🔍 Trade #{i}: {pair} on {exchange} - {status}")
        print(f"   Entry Price: {format_currency(entry_price)}")
        print(f"   Exit Price: {format_currency(exit_price)}")
        print(f"   Position Size: {position_size}")
        print(f"   Entry Time: {entry_time}")
        print(f"   Exit Time: {exit_time}")
        print(f"   Exit Reason: {exit_reason}")
        print(f"   Stored Realized PnL: {format_currency(stored_realized_pnl)}")
        print(f"   Stored Unrealized PnL: {format_currency(stored_unrealized_pnl)}")
        
        if status == 'CLOSED' and entry_price != 'NULL' and exit_price != 'NULL' and position_size != 'NULL':
            # Calculate expected realized PnL
            calculated_pnl = calculate_realized_pnl(entry_price, exit_price, position_size, 'buy')
            print(f"   Calculated Realized PnL: {format_currency(str(calculated_pnl))}")
            
            # Check if stored value matches calculated
            stored_pnl = float(stored_realized_pnl) if stored_realized_pnl != 'NULL' else 0
            if abs(calculated_pnl - stored_pnl) < 0.01:
                print(f"   ✅ Realized PnL matches calculation")
            else:
                print(f"   ⚠️  DISCREPANCY: Stored={format_currency(stored_realized_pnl)}, Calculated={format_currency(str(calculated_pnl))}")
            
            closed_trades.append(trade)
            total_realized_pnl += stored_pnl
        elif status == 'OPEN':
            open_trades.append(trade)
            unrealized_pnl = float(stored_unrealized_pnl) if stored_unrealized_pnl != 'NULL' else 0
            total_unrealized_pnl += unrealized_pnl
            print(f"   ✅ Open trade")
        else:
            print(f"   ❓ Status: {status}")
        
        print("-" * 80)
    
    # Summary
    print(f"\n📊 SUMMARY:")
    print(f"   Total Trades: {len(trades)}")
    print(f"   Closed Trades: {len(closed_trades)}")
    print(f"   Open Trades: {len(open_trades)}")
    print(f"   Total Realized PnL: {format_currency(str(total_realized_pnl))}")
    print(f"   Total Unrealized PnL: {format_currency(str(total_unrealized_pnl))}")
    print(f"   Combined PnL: {format_currency(str(total_realized_pnl + total_unrealized_pnl))}")
    
    # Show closed trades summary
    if closed_trades:
        print(f"\n📋 CLOSED TRADES SUMMARY:")
        print("-" * 80)
        print(f"{'Pair':<12} {'Entry':<10} {'Exit':<10} {'Size':<8} {'Realized PnL':<12} {'Exit Reason':<30}")
        print("-" * 80)
        
        for trade in closed_trades:
            pair = trade.get('pair', 'N/A')
            entry = format_currency(trade.get('entry_price', 'NULL'))
            exit = format_currency(trade.get('exit_price', 'NULL'))
            size = trade.get('position_size', 'N/A')
            pnl = format_currency(trade.get('realized_pnl', '0'))
            reason = trade.get('exit_reason', 'N/A')
            if reason and len(reason) > 28:
                reason = reason[:28] + "..."
            
            print(f"{pair:<12} {entry:<10} {exit:<10} {size:<8} {pnl:<12} {reason:<30}")
    
    # Show open trades summary
    if open_trades:
        print(f"\n📋 OPEN TRADES SUMMARY:")
        print("-" * 80)
        print(f"{'Pair':<12} {'Entry':<10} {'Size':<8} {'Unrealized PnL':<15}")
        print("-" * 80)
        
        for trade in open_trades:
            pair = trade.get('pair', 'N/A')
            entry = format_currency(trade.get('entry_price', 'NULL'))
            size = trade.get('position_size', 'N/A')
            pnl = format_currency(trade.get('unrealized_pnl', '0'))
            
            print(f"{pair:<12} {entry:<10} {size:<8} {pnl:<15}")
    
    print("\n" + "=" * 100)

if __name__ == "__main__":
    main()
