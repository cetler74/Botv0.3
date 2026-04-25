#!/usr/bin/env python3
"""
Analyze Real Trades - Analyze the actual trades from trading.trades table
"""

import psycopg2
import psycopg2.extras
from datetime import datetime
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def get_db_connection():
    """Get database connection"""
    try:
        connection = psycopg2.connect(
            host=os.getenv('DB_HOST', 'localhost'),
            port=os.getenv('DB_PORT', '5432'),
            database=os.getenv('DB_NAME', 'trading_bot_futures'),
            user=os.getenv('DB_USER', 'carloslarramba'),
            password=os.getenv('DB_PASSWORD', 'mypassword')
        )
        return connection
    except Exception as e:
        print(f"❌ Database connection error: {e}")
        return None

def execute_query(connection, query, params=None):
    """Execute SQL query and return results"""
    try:
        with connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)
            results = cursor.fetchall()
            return [dict(row) for row in results]
    except Exception as e:
        print(f"❌ Query execution error: {e}")
        return []

def format_currency(amount):
    """Format currency amount"""
    if amount is None:
        return "NULL"
    elif amount == 0:
        return "$0.00"
    elif amount > 0:
        return f"${amount:,.2f}"
    else:
        return f"-${abs(amount):,.2f}"

def calculate_realized_pnl(entry_price, exit_price, position_size, side='buy'):
    """Calculate realized PnL for a closed trade"""
    if not entry_price or not exit_price or not position_size:
        return 0.0
    
    if side == 'buy':
        # For buy orders: (exit_price - entry_price) * position_size
        pnl = (exit_price - entry_price) * position_size
    else:
        # For sell orders: (entry_price - exit_price) * position_size
        pnl = (entry_price - exit_price) * position_size
    
    return pnl

def main():
    """Main function"""
    print(f"\n📊 ANALYZING REAL TRADES - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 100)
    
    # Connect to database
    connection = get_db_connection()
    if not connection:
        print("❌ Failed to connect to database")
        return
    
    try:
        # Get all trades from the table
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
        
        trades = execute_query(connection, trades_query)
        
        if not trades:
            print("❌ No trades found")
            return
        
        print(f"📊 Found {len(trades)} total trades")
        print()
        
        # Analyze each trade
        closed_trades = []
        open_trades = []
        total_realized_pnl = 0.0
        total_unrealized_pnl = 0.0
        
        for i, trade in enumerate(trades, 1):
            trade_id = trade['trade_id']
            pair = trade['pair']
            exchange = trade['exchange']
            status = trade['status']
            entry_price = trade['entry_price']
            exit_price = trade['exit_price']
            position_size = trade['position_size']
            stored_realized_pnl = trade['realized_pnl']
            stored_unrealized_pnl = trade['unrealized_pnl']
            entry_time = trade['entry_time']
            exit_time = trade['exit_time']
            exit_reason = trade['exit_reason']
            
            print(f"🔍 Trade #{i}: {pair} on {exchange} - {status}")
            print(f"   Entry Price: {format_currency(entry_price)}")
            print(f"   Exit Price: {format_currency(exit_price)}")
            print(f"   Position Size: {position_size}")
            print(f"   Entry Time: {entry_time}")
            print(f"   Exit Time: {exit_time}")
            print(f"   Exit Reason: {exit_reason}")
            print(f"   Stored Realized PnL: {format_currency(stored_realized_pnl)}")
            print(f"   Stored Unrealized PnL: {format_currency(stored_unrealized_pnl)}")
            
            if status == 'CLOSED' and entry_price and exit_price and position_size:
                # Calculate expected realized PnL
                calculated_pnl = calculate_realized_pnl(entry_price, exit_price, position_size, 'buy')
                print(f"   Calculated Realized PnL: {format_currency(calculated_pnl)}")
                
                # Check if stored value matches calculated
                if abs(calculated_pnl - stored_realized_pnl) < 0.01:
                    print(f"   ✅ Realized PnL matches calculation")
                else:
                    print(f"   ⚠️  DISCREPANCY: Stored={format_currency(stored_realized_pnl)}, Calculated={format_currency(calculated_pnl)}")
                
                closed_trades.append(trade)
                total_realized_pnl += float(stored_realized_pnl) if stored_realized_pnl else 0
            elif status == 'OPEN':
                open_trades.append(trade)
                total_unrealized_pnl += float(stored_unrealized_pnl) if stored_unrealized_pnl else 0
                print(f"   ✅ Open trade")
            else:
                print(f"   ❓ Status: {status}")
            
            print("-" * 80)
        
        # Summary
        print(f"\n📊 SUMMARY:")
        print(f"   Total Trades: {len(trades)}")
        print(f"   Closed Trades: {len(closed_trades)}")
        print(f"   Open Trades: {len(open_trades)}")
        print(f"   Total Realized PnL: {format_currency(total_realized_pnl)}")
        print(f"   Total Unrealized PnL: {format_currency(total_unrealized_pnl)}")
        print(f"   Combined PnL: {format_currency(total_realized_pnl + total_unrealized_pnl)}")
        
        # Show closed trades summary
        if closed_trades:
            print(f"\n📋 CLOSED TRADES SUMMARY:")
            print("-" * 80)
            print(f"{'Pair':<12} {'Entry':<10} {'Exit':<10} {'Size':<8} {'Realized PnL':<12} {'Exit Reason':<30}")
            print("-" * 80)
            
            for trade in closed_trades:
                pair = trade['pair']
                entry = format_currency(trade['entry_price'])
                exit = format_currency(trade['exit_price'])
                size = trade['position_size']
                pnl = format_currency(trade['realized_pnl'])
                reason = trade['exit_reason'][:28] + "..." if trade['exit_reason'] and len(trade['exit_reason']) > 28 else trade['exit_reason'] or "N/A"
                
                print(f"{pair:<12} {entry:<10} {exit:<10} {size:<8} {pnl:<12} {reason:<30}")
        
        # Show open trades summary
        if open_trades:
            print(f"\n📋 OPEN TRADES SUMMARY:")
            print("-" * 80)
            print(f"{'Pair':<12} {'Entry':<10} {'Size':<8} {'Unrealized PnL':<15}")
            print("-" * 80)
            
            for trade in open_trades:
                pair = trade['pair']
                entry = format_currency(trade['entry_price'])
                size = trade['position_size']
                pnl = format_currency(trade['unrealized_pnl'])
                
                print(f"{pair:<12} {entry:<10} {size:<8} {pnl:<15}")
        
        print("\n" + "=" * 100)
        
    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        connection.close()

if __name__ == "__main__":
    main()
