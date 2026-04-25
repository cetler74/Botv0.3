#!/usr/bin/env python3
"""
Investigate Trade Closing - Understand how trades should be closed and what exit prices should be
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

def main():
    """Main function"""
    print(f"\n🔍 INVESTIGATING TRADE CLOSING - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 100)
    
    # Connect to database
    connection = get_db_connection()
    if not connection:
        print("❌ Failed to connect to database")
        return
    
    try:
        # Check all tables in the trading schema
        print("📋 DATABASE SCHEMA ANALYSIS:")
        tables_query = """
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'trading' 
        ORDER BY table_name
        """
        
        tables = execute_query(connection, tables_query)
        print("   Tables in trading schema:")
        for table in tables:
            print(f"   - {table['table_name']}")
        
        # Check if there are any order-related tables
        print(f"\n🔍 CHECKING FOR ORDER DATA:")
        
        # Look for any tables that might contain order information
        order_tables_query = """
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'trading' 
        AND table_name LIKE '%order%'
        """
        
        order_tables = execute_query(connection, order_tables_query)
        if order_tables:
            print("   Order-related tables found:")
            for table in order_tables:
                print(f"   - {table['table_name']}")
                
                # Get schema for each order table
                schema_query = f"""
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns 
                WHERE table_schema = 'trading' 
                AND table_name = '{table['table_name']}'
                ORDER BY ordinal_position
                """
                
                schema = execute_query(connection, schema_query)
                print(f"     Columns:")
                for col in schema:
                    print(f"       {col['column_name']}: {col['data_type']} (nullable: {col['is_nullable']})")
        else:
            print("   No order-related tables found")
        
        # Check for any fill-related tables
        print(f"\n🔍 CHECKING FOR FILL DATA:")
        fill_tables_query = """
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'trading' 
        AND (table_name LIKE '%fill%' OR table_name LIKE '%execution%')
        """
        
        fill_tables = execute_query(connection, fill_tables_query)
        if fill_tables:
            print("   Fill-related tables found:")
            for table in fill_tables:
                print(f"   - {table['table_name']}")
        else:
            print("   No fill-related tables found")
        
        # Check the closed trade more carefully
        print(f"\n🔍 DETAILED ANALYSIS OF CLOSED TRADE:")
        closed_trade_query = """
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
            strategy,
            entry_id,
            exit_id
        FROM trading.trades 
        WHERE status = 'CLOSED'
        ORDER BY created_at DESC
        """
        
        closed_trades = execute_query(connection, closed_trade_query)
        for trade in closed_trades:
            print(f"   Trade ID: {trade['trade_id']}")
            print(f"   Pair: {trade['pair']}")
            print(f"   Exchange: {trade['exchange']}")
            print(f"   Entry Price: {format_currency(trade['entry_price'])}")
            print(f"   Exit Price: {format_currency(trade['exit_price'])}")
            print(f"   Position Size: {trade['position_size']}")
            print(f"   Entry Time: {trade['entry_time']}")
            print(f"   Exit Time: {trade['exit_time']}")
            print(f"   Entry Reason: {trade['entry_reason']}")
            print(f"   Exit Reason: {trade['exit_reason']}")
            print(f"   Entry ID: {trade['entry_id']}")
            print(f"   Exit ID: {trade['exit_id']}")
            print(f"   Created: {trade['created_at']}")
            print(f"   Updated: {trade['updated_at']}")
            print(f"   Strategy: {trade['strategy']}")
            print("-" * 80)
        
        # Check if there are any other statuses
        print(f"\n🔍 CHECKING ALL TRADE STATUSES:")
        status_query = """
        SELECT status, COUNT(*) as count
        FROM trading.trades 
        GROUP BY status
        ORDER BY status
        """
        
        statuses = execute_query(connection, status_query)
        for status in statuses:
            print(f"   {status['status']}: {status['count']} trades")
        
        # Check for any recent activity or logs
        print(f"\n🔍 CHECKING FOR RECENT ACTIVITY:")
        recent_query = """
        SELECT 
            trade_id,
            pair,
            status,
            created_at,
            updated_at
        FROM trading.trades 
        ORDER BY updated_at DESC
        LIMIT 10
        """
        
        recent_trades = execute_query(connection, recent_query)
        print("   Recent trade updates:")
        for trade in recent_trades:
            print(f"   - {trade['pair']} ({trade['status']}) - Updated: {trade['updated_at']}")
        
        print("\n" + "=" * 100)
        
    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        connection.close()

if __name__ == "__main__":
    main()
