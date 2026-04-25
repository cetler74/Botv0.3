#!/usr/bin/env python3
"""
Investigate Trades - Check actual trade data and PnL calculations
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

def execute_query(connection, query):
    """Execute SQL query and return results"""
    try:
        with connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
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
    print(f"\n🔍 TRADE INVESTIGATION - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 100)
    
    # Connect to database
    connection = get_db_connection()
    if not connection:
        print("❌ Failed to connect to database")
        return
    
    try:
        # Get all trades with detailed information
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
            strategy
        FROM trading.trades 
        ORDER BY created_at DESC
        """
        
        trades = execute_query(connection, trades_query)
        
        if not trades:
            print("❌ No trades found in database")
            return
        
        print(f"📊 Found {len(trades)} trades in database")
        print()
        
        # Display each trade
        for i, trade in enumerate(trades, 1):
            print(f"🔍 TRADE #{i}:")
            print(f"   Trade ID: {trade.get('trade_id', 'N/A')}")
            print(f"   Pair: {trade.get('pair', 'N/A')}")
            print(f"   Exchange: {trade.get('exchange', 'N/A')}")
            print(f"   Status: {trade.get('status', 'N/A')}")
            print(f"   Entry Price: {format_currency(trade.get('entry_price'))}")
            print(f"   Exit Price: {format_currency(trade.get('exit_price'))}")
            print(f"   Position Size: {trade.get('position_size', 'N/A')}")
            print(f"   Unrealized PnL: {format_currency(trade.get('unrealized_pnl'))}")
            print(f"   Realized PnL: {format_currency(trade.get('realized_pnl'))}")
            print(f"   Entry Time: {trade.get('entry_time', 'N/A')}")
            print(f"   Exit Time: {trade.get('exit_time', 'N/A')}")
            print(f"   Created: {trade.get('created_at', 'N/A')}")
            print(f"   Updated: {trade.get('updated_at', 'N/A')}")
            print(f"   Strategy: {trade.get('strategy', 'N/A')}")
            print(f"   Entry Reason: {trade.get('entry_reason', 'N/A')}")
            print("-" * 80)
        
        # Check for NULL values
        print("\n🔍 NULL VALUE ANALYSIS:")
        null_analysis_query = """
        SELECT 
            COUNT(*) as total_trades,
            COUNT(CASE WHEN unrealized_pnl IS NULL THEN 1 END) as null_unrealized,
            COUNT(CASE WHEN realized_pnl IS NULL THEN 1 END) as null_realized,
            COUNT(CASE WHEN entry_price IS NULL THEN 1 END) as null_entry_price,
            COUNT(CASE WHEN position_size IS NULL THEN 1 END) as null_position_size
        FROM trading.trades
        """
        
        null_analysis = execute_query(connection, null_analysis_query)
        if null_analysis:
            analysis = null_analysis[0]
            print(f"   Total Trades: {analysis['total_trades']}")
            print(f"   NULL Unrealized PnL: {analysis['null_unrealized']}")
            print(f"   NULL Realized PnL: {analysis['null_realized']}")
            print(f"   NULL Entry Price: {analysis['null_entry_price']}")
            print(f"   NULL Position Size: {analysis['null_position_size']}")
        
        # Check for zero values
        print("\n🔍 ZERO VALUE ANALYSIS:")
        zero_analysis_query = """
        SELECT 
            COUNT(*) as total_trades,
            COUNT(CASE WHEN unrealized_pnl = 0 THEN 1 END) as zero_unrealized,
            COUNT(CASE WHEN realized_pnl = 0 THEN 1 END) as zero_realized,
            COUNT(CASE WHEN entry_price = 0 THEN 1 END) as zero_entry_price,
            COUNT(CASE WHEN position_size = 0 THEN 1 END) as zero_position_size
        FROM trading.trades
        """
        
        zero_analysis = execute_query(connection, zero_analysis_query)
        if zero_analysis:
            analysis = zero_analysis[0]
            print(f"   Total Trades: {analysis['total_trades']}")
            print(f"   Zero Unrealized PnL: {analysis['zero_unrealized']}")
            print(f"   Zero Realized PnL: {analysis['zero_realized']}")
            print(f"   Zero Entry Price: {analysis['zero_entry_price']}")
            print(f"   Zero Position Size: {analysis['zero_position_size']}")
        
        # Check table schema
        print("\n🔍 TABLE SCHEMA:")
        schema_query = """
        SELECT 
            column_name,
            data_type,
            is_nullable,
            column_default
        FROM information_schema.columns 
        WHERE table_schema = 'trading' 
        AND table_name = 'trades'
        ORDER BY ordinal_position
        """
        
        schema = execute_query(connection, schema_query)
        for column in schema:
            print(f"   {column['column_name']}: {column['data_type']} (nullable: {column['is_nullable']})")
        
        print("\n" + "=" * 100)
        
    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        connection.close()

if __name__ == "__main__":
    main()
