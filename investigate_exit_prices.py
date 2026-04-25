#!/usr/bin/env python3
"""
Investigate Exit Prices - Check trades that should have exit prices but don't
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
    print(f"\n🔍 INVESTIGATING EXIT PRICES - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 100)
    
    # Connect to database
    connection = get_db_connection()
    if not connection:
        print("❌ Failed to connect to database")
        return
    
    try:
        # Check all trades for exit price issues
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
        closed_without_exit_price = []
        closed_with_zero_exit_price = []
        open_trades = []
        
        for i, trade in enumerate(trades, 1):
            trade_id = trade['trade_id']
            pair = trade['pair']
            exchange = trade['exchange']
            status = trade['status']
            entry_price = trade['entry_price']
            exit_price = trade['exit_price']
            exit_time = trade['exit_time']
            exit_reason = trade['exit_reason']
            
            print(f"🔍 Trade #{i}: {pair} on {exchange} - {status}")
            print(f"   Entry Price: {format_currency(entry_price)}")
            print(f"   Exit Price: {format_currency(exit_price)}")
            print(f"   Exit Time: {exit_time}")
            print(f"   Exit Reason: {exit_reason}")
            
            if status == 'CLOSED':
                if exit_price is None:
                    print(f"   ⚠️  CLOSED TRADE WITH NULL EXIT PRICE!")
                    closed_without_exit_price.append(trade)
                elif exit_price == 0:
                    print(f"   ⚠️  CLOSED TRADE WITH ZERO EXIT PRICE!")
                    closed_with_zero_exit_price.append(trade)
                else:
                    print(f"   ✅ Properly closed trade")
            elif status == 'OPEN':
                print(f"   ✅ Open trade (no exit price expected)")
                open_trades.append(trade)
            else:
                print(f"   ❓ Unknown status: {status}")
            
            print("-" * 80)
        
        # Summary
        print(f"\n📊 ANALYSIS SUMMARY:")
        print(f"   Total Trades: {len(trades)}")
        print(f"   Open Trades: {len(open_trades)}")
        print(f"   Closed Trades: {len(closed_without_exit_price) + len(closed_with_zero_exit_price)}")
        print(f"   Closed with NULL exit price: {len(closed_without_exit_price)}")
        print(f"   Closed with ZERO exit price: {len(closed_with_zero_exit_price)}")
        
        # Show problematic trades
        if closed_without_exit_price:
            print(f"\n⚠️  TRADES CLOSED WITHOUT EXIT PRICE:")
            for trade in closed_without_exit_price:
                print(f"   - {trade['pair']} on {trade['exchange']} (ID: {trade['trade_id'][:8]}...)")
        
        if closed_with_zero_exit_price:
            print(f"\n⚠️  TRADES CLOSED WITH ZERO EXIT PRICE:")
            for trade in closed_with_zero_exit_price:
                print(f"   - {trade['pair']} on {trade['exchange']} (ID: {trade['trade_id'][:8]}...)")
                print(f"     Entry: {format_currency(trade['entry_price'])} | Exit: {format_currency(trade['exit_price'])}")
                print(f"     Exit Time: {trade['exit_time']} | Exit Reason: {trade['exit_reason']}")
        
        # Check for orders that might have exit information
        print(f"\n🔍 CHECKING ORDERS TABLE FOR EXIT INFORMATION:")
        orders_query = """
        SELECT 
            o.order_id,
            o.trade_id,
            o.symbol,
            o.side,
            o.order_type,
            o.amount,
            o.price,
            o.filled_amount,
            o.filled_price,
            o.status,
            o.exchange_order_id,
            t.status as trade_status,
            t.exit_price as trade_exit_price
        FROM trading.orders o
        LEFT JOIN trading.trades t ON o.trade_id = t.trade_id
        ORDER BY o.created_at DESC
        LIMIT 20
        """
        
        orders = execute_query(connection, orders_query)
        if orders:
            print(f"   Found {len(orders)} orders")
            for order in orders[:5]:  # Show first 5
                print(f"   Order: {order['symbol']} {order['side']} - Status: {order['status']}")
                print(f"     Filled: {order['filled_amount']} @ {format_currency(order['filled_price'])}")
                print(f"     Trade Status: {order['trade_status']} | Exit Price: {format_currency(order['trade_exit_price'])}")
        else:
            print(f"   No orders found")
        
        print("\n" + "=" * 100)
        
    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        connection.close()

if __name__ == "__main__":
    main()
