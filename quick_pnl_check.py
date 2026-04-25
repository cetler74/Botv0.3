#!/usr/bin/env python3
"""
Quick PnL Check - Simple command-line tool for checking PnL totals
"""

import sys
from datetime import datetime
import psycopg2
import psycopg2.extras
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

def quick_pnl_check():
    """Quick PnL totals check"""
    
    # Simple queries for quick results
    today_query = """
    SELECT 
        COUNT(*) as trades,
        COALESCE(SUM(unrealized_pnl), 0) as unrealized,
        COALESCE(SUM(realized_pnl), 0) as realized,
        COALESCE(SUM(unrealized_pnl + realized_pnl), 0) as total
    FROM trading.trades 
    WHERE DATE(created_at) = CURRENT_DATE
    """
    
    overall_query = """
    SELECT 
        COUNT(*) as trades,
        COALESCE(SUM(unrealized_pnl), 0) as unrealized,
        COALESCE(SUM(realized_pnl), 0) as realized,
        COALESCE(SUM(unrealized_pnl + realized_pnl), 0) as total
    FROM trading.trades
    """
    
    try:
        # Connect to database
        connection = get_db_connection()
        if not connection:
            return
        
        # Get today's data
        today_results = execute_query(connection, today_query)
        today_data = today_results[0] if today_results else {
            'trades': 0,
            'unrealized': 0,
            'realized': 0,
            'total': 0
        }
        
        # Get overall data
        overall_results = execute_query(connection, overall_query)
        overall_data = overall_results[0] if overall_results else {
            'trades': 0,
            'unrealized': 0,
            'realized': 0,
            'total': 0
        }
        
        # Print results
        print(f"\n📊 PnL CHECK - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 60)
        
        print(f"📅 TODAY:")
        print(f"   Trades: {today_data.get('trades', 0)}")
        print(f"   Unrealized: ${today_data.get('unrealized', 0):,.2f}")
        print(f"   Realized: ${today_data.get('realized', 0):,.2f}")
        print(f"   Total: ${today_data.get('total', 0):,.2f}")
        
        print(f"\n📈 OVERALL:")
        print(f"   Trades: {overall_data.get('trades', 0)}")
        print(f"   Unrealized: ${overall_data.get('unrealized', 0):,.2f}")
        print(f"   Realized: ${overall_data.get('realized', 0):,.2f}")
        print(f"   Total: ${overall_data.get('total', 0):,.2f}")
        
        print("=" * 60)
        
    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        if connection:
            connection.close()

if __name__ == "__main__":
    quick_pnl_check()
