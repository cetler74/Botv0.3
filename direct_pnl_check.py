#!/usr/bin/env python3
"""
Direct PnL Check - Direct database connection for PnL totals
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
    if amount == 0:
        return "$0.00"
    elif amount > 0:
        return f"${amount:,.2f}"
    else:
        return f"-${abs(amount):,.2f}"

def main():
    """Main function"""
    print(f"\n📊 PnL TOTALS CHECK - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)
    
    # Connect to database
    connection = get_db_connection()
    if not connection:
        print("❌ Failed to connect to database")
        return
    
    try:
        # Today's PnL totals
        today_query = """
        SELECT 
            COUNT(*) as total_trades,
            COALESCE(SUM(unrealized_pnl), 0) as total_unrealized_pnl,
            COALESCE(SUM(realized_pnl), 0) as total_realized_pnl,
            COALESCE(SUM(unrealized_pnl + realized_pnl), 0) as total_combined_pnl
        FROM trading.trades 
        WHERE DATE(created_at) = CURRENT_DATE
        """
        
        today_results = execute_query(connection, today_query)
        today_data = today_results[0] if today_results else {
            'total_trades': 0,
            'total_unrealized_pnl': 0,
            'total_realized_pnl': 0,
            'total_combined_pnl': 0
        }
        
        # Overall PnL totals
        overall_query = """
        SELECT 
            COUNT(*) as total_trades,
            COALESCE(SUM(unrealized_pnl), 0) as total_unrealized_pnl,
            COALESCE(SUM(realized_pnl), 0) as total_realized_pnl,
            COALESCE(SUM(unrealized_pnl + realized_pnl), 0) as total_combined_pnl
        FROM trading.trades
        """
        
        overall_results = execute_query(connection, overall_query)
        overall_data = overall_results[0] if overall_results else {
            'total_trades': 0,
            'total_unrealized_pnl': 0,
            'total_realized_pnl': 0,
            'total_combined_pnl': 0
        }
        
        # Status breakdown for today
        today_status_query = """
        SELECT 
            status,
            COUNT(*) as trade_count,
            COALESCE(SUM(unrealized_pnl), 0) as total_unrealized_pnl,
            COALESCE(SUM(realized_pnl), 0) as total_realized_pnl,
            COALESCE(SUM(unrealized_pnl + realized_pnl), 0) as total_combined_pnl
        FROM trading.trades 
        WHERE DATE(created_at) = CURRENT_DATE
        GROUP BY status
        ORDER BY status
        """
        
        today_status = execute_query(connection, today_status_query)
        
        # Status breakdown for overall
        overall_status_query = """
        SELECT 
            status,
            COUNT(*) as trade_count,
            COALESCE(SUM(unrealized_pnl), 0) as total_unrealized_pnl,
            COALESCE(SUM(realized_pnl), 0) as total_realized_pnl,
            COALESCE(SUM(unrealized_pnl + realized_pnl), 0) as total_combined_pnl
        FROM trading.trades 
        GROUP BY status
        ORDER BY status
        """
        
        overall_status = execute_query(connection, overall_status_query)
        
        # Print results
        print("\n📅 TODAY'S TOTALS:")
        print(f"   Total Trades: {today_data['total_trades']}")
        print(f"   Unrealized PnL: {format_currency(today_data['total_unrealized_pnl'])}")
        print(f"   Realized PnL: {format_currency(today_data['total_realized_pnl'])}")
        print(f"   Combined PnL: {format_currency(today_data['total_combined_pnl'])}")
        
        print("\n📈 OVERALL TOTALS:")
        print(f"   Total Trades: {overall_data['total_trades']}")
        print(f"   Unrealized PnL: {format_currency(overall_data['total_unrealized_pnl'])}")
        print(f"   Realized PnL: {format_currency(overall_data['total_realized_pnl'])}")
        print(f"   Combined PnL: {format_currency(overall_data['total_combined_pnl'])}")
        
        # Performance indicators
        if overall_data['total_trades'] > 0:
            avg_trade_pnl = overall_data['total_combined_pnl'] / overall_data['total_trades']
            print(f"\n📊 PERFORMANCE INDICATORS:")
            print(f"   Average PnL per Trade: {format_currency(avg_trade_pnl)}")
            
            if overall_data['total_combined_pnl'] != 0:
                realized_ratio = (overall_data['total_realized_pnl'] / overall_data['total_combined_pnl']) * 100
                print(f"   Realized PnL Ratio: {realized_ratio:.1f}%")
        
        # Today's status breakdown
        if today_status:
            print(f"\n📋 TODAY'S BREAKDOWN BY STATUS:")
            print("-" * 80)
            print(f"{'Status':<15} {'Trades':<8} {'Unrealized':<12} {'Realized':<12} {'Combined':<12}")
            print("-" * 80)
            
            for row in today_status:
                status = row.get('status', 'UNKNOWN')
                trades = row.get('trade_count', 0)
                unrealized = format_currency(row.get('total_unrealized_pnl', 0))
                realized = format_currency(row.get('total_realized_pnl', 0))
                combined = format_currency(row.get('total_combined_pnl', 0))
                
                print(f"{status:<15} {trades:<8} {unrealized:<12} {realized:<12} {combined:<12}")
        
        # Overall status breakdown
        if overall_status:
            print(f"\n📋 OVERALL BREAKDOWN BY STATUS:")
            print("-" * 80)
            print(f"{'Status':<15} {'Trades':<8} {'Unrealized':<12} {'Realized':<12} {'Combined':<12}")
            print("-" * 80)
            
            for row in overall_status:
                status = row.get('status', 'UNKNOWN')
                trades = row.get('trade_count', 0)
                unrealized = format_currency(row.get('total_unrealized_pnl', 0))
                realized = format_currency(row.get('total_realized_pnl', 0))
                combined = format_currency(row.get('total_combined_pnl', 0))
                
                print(f"{status:<15} {trades:<8} {unrealized:<12} {realized:<12} {combined:<12}")
        
        print("\n" + "=" * 80)
        
    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        connection.close()

if __name__ == "__main__":
    main()
