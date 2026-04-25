#!/usr/bin/env python3
"""
Reopen Invalid Closed Trade - Reopen trades that were closed without proper exit information
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

def reopen_trade(connection, trade_id):
    """Reopen a trade that was incorrectly closed"""
    try:
        update_query = """
        UPDATE trading.trades 
        SET 
            status = 'OPEN',
            exit_price = NULL,
            exit_time = NULL,
            exit_reason = NULL,
            exit_id = NULL,
            realized_pnl = 0,
            updated_at = %s
        WHERE trade_id = %s
        """
        params = (datetime.utcnow(), trade_id)
        
        with connection.cursor() as cursor:
            cursor.execute(update_query, params)
            connection.commit()
        
        return True
    except Exception as e:
        print(f"❌ Error reopening trade {trade_id}: {e}")
        return False

def main():
    """Main function"""
    print(f"\n🔄 REOPENING INVALID CLOSED TRADE - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 100)
    
    # Connect to database
    connection = get_db_connection()
    if not connection:
        print("❌ Failed to connect to database")
        return
    
    try:
        # Find trades that are closed but missing proper exit information
        invalid_closed_trades_query = """
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
            exit_reason,
            exit_id,
            created_at,
            updated_at,
            strategy
        FROM trading.trades 
        WHERE status = 'CLOSED' 
        AND (exit_time IS NULL OR exit_reason IS NULL OR exit_id IS NULL)
        ORDER BY created_at DESC
        """
        
        invalid_trades = execute_query(connection, invalid_closed_trades_query)
        
        if not invalid_trades:
            print("✅ No invalid closed trades found")
            return
        
        print(f"⚠️  Found {len(invalid_trades)} invalid closed trades")
        print()
        
        for i, trade in enumerate(invalid_trades, 1):
            trade_id = trade['trade_id']
            pair = trade['pair']
            exchange = trade['exchange']
            entry_price = trade['entry_price']
            exit_price = trade['exit_price']
            position_size = trade['position_size']
            exit_time = trade['exit_time']
            exit_reason = trade['exit_reason']
            exit_id = trade['exit_id']
            
            print(f"🔍 Invalid Closed Trade #{i}: {pair} on {exchange}")
            print(f"   Trade ID: {trade_id}")
            print(f"   Entry Price: {format_currency(entry_price)}")
            print(f"   Exit Price: {format_currency(exit_price)}")
            print(f"   Position Size: {position_size}")
            print(f"   Exit Time: {exit_time}")
            print(f"   Exit Reason: {exit_reason}")
            print(f"   Exit ID: {exit_id}")
            print(f"   Created: {trade['created_at']}")
            print(f"   Updated: {trade['updated_at']}")
            
            # Check what's missing
            missing_info = []
            if exit_time is None:
                missing_info.append("exit_time")
            if exit_reason is None:
                missing_info.append("exit_reason")
            if exit_id is None:
                missing_info.append("exit_id")
            
            print(f"   ❌ Missing: {', '.join(missing_info)}")
            
            # Reopen the trade
            print(f"   🔄 Reopening trade...")
            success = reopen_trade(connection, trade_id)
            if success:
                print(f"   ✅ Trade reopened successfully")
            else:
                print(f"   ❌ Failed to reopen trade")
            
            print("-" * 80)
        
        # Show updated totals
        print(f"\n📈 UPDATED TOTALS:")
        totals_query = """
        SELECT 
            COUNT(*) as total_trades,
            COALESCE(SUM(unrealized_pnl), 0) as total_unrealized_pnl,
            COALESCE(SUM(realized_pnl), 0) as total_realized_pnl,
            COALESCE(SUM(unrealized_pnl + realized_pnl), 0) as total_combined_pnl
        FROM trading.trades
        """
        
        totals = execute_query(connection, totals_query)
        if totals:
            total_data = totals[0]
            print(f"   Total Trades: {total_data['total_trades']}")
            print(f"   Unrealized PnL: {format_currency(total_data['total_unrealized_pnl'])}")
            print(f"   Realized PnL: {format_currency(total_data['total_realized_pnl'])}")
            print(f"   Combined PnL: {format_currency(total_data['total_combined_pnl'])}")
        
        # Show status breakdown
        print(f"\n📊 STATUS BREAKDOWN:")
        status_query = """
        SELECT status, COUNT(*) as count
        FROM trading.trades 
        GROUP BY status
        ORDER BY status
        """
        
        statuses = execute_query(connection, status_query)
        for status in statuses:
            print(f"   {status['status']}: {status['count']} trades")
        
        print("\n" + "=" * 100)
        
    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        connection.close()

if __name__ == "__main__":
    main()
