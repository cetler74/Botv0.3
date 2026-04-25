#!/usr/bin/env python3
"""
Check Database Connection - Verify which database we're connected to
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

def main():
    """Main function"""
    print(f"\n🔍 CHECKING DATABASE CONNECTION - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 100)
    
    # Show connection details
    print("📋 CONNECTION DETAILS:")
    print(f"   Host: {os.getenv('DB_HOST', 'localhost')}")
    print(f"   Port: {os.getenv('DB_PORT', '5432')}")
    print(f"   Database: {os.getenv('DB_NAME', 'trading_bot_futures')}")
    print(f"   User: {os.getenv('DB_USER', 'carloslarramba')}")
    print(f"   Password: {'*' * len(os.getenv('DB_PASSWORD', 'mypassword'))}")
    
    # Connect to database
    connection = get_db_connection()
    if not connection:
        print("❌ Failed to connect to database")
        return
    
    try:
        # Check current database
        db_info_query = "SELECT current_database(), current_user, version()"
        db_info = execute_query(connection, db_info_query)
        if db_info:
            info = db_info[0]
            print(f"\n📊 DATABASE INFO:")
            print(f"   Current Database: {info['current_database']}")
            print(f"   Current User: {info['current_user']}")
            print(f"   Version: {info['version'][:50]}...")
        
        # Check all databases
        print(f"\n📋 ALL DATABASES:")
        databases_query = "SELECT datname FROM pg_database WHERE datistemplate = false ORDER BY datname"
        databases = execute_query(connection, databases_query)
        for db in databases:
            print(f"   - {db['datname']}")
        
        # Check all schemas in current database
        print(f"\n📋 SCHEMAS IN CURRENT DATABASE:")
        schemas_query = "SELECT schema_name FROM information_schema.schemata ORDER BY schema_name"
        schemas = execute_query(connection, schemas_query)
        for schema in schemas:
            print(f"   - {schema['schema_name']}")
        
        # Check tables in trading schema
        print(f"\n📋 TABLES IN TRADING SCHEMA:")
        tables_query = "SELECT table_name FROM information_schema.tables WHERE table_schema = 'trading' ORDER BY table_name"
        tables = execute_query(connection, tables_query)
        for table in tables:
            print(f"   - {table['table_name']}")
        
        # Check trades table count
        print(f"\n📊 TRADES TABLE INFO:")
        count_query = "SELECT COUNT(*) as total_trades FROM trading.trades"
        count_result = execute_query(connection, count_query)
        if count_result:
            print(f"   Total trades: {count_result[0]['total_trades']}")
        
        # Show sample of trades
        print(f"\n📋 SAMPLE TRADES (first 3):")
        sample_query = """
        SELECT trade_id, pair, exchange, status, entry_price, exit_price, created_at
        FROM trading.trades 
        ORDER BY created_at DESC 
        LIMIT 3
        """
        sample_trades = execute_query(connection, sample_query)
        for trade in sample_trades:
            print(f"   - {trade['pair']} on {trade['exchange']} ({trade['status']}) - Entry: {trade['entry_price']}, Exit: {trade['exit_price']}")
        
        print("\n" + "=" * 100)
        
    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        connection.close()

if __name__ == "__main__":
    main()
