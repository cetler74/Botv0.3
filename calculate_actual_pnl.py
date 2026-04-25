"""
SPOT Trading PnL Calculator - Long positions only
"""
from strategy.strategy_pnl_enhanced import calculate_unrealized_pnl_with_fees, calculate_realized_pnl_with_fees

#!/usr/bin/env python3
"""
Calculate Actual PnL - Calculate real PnL values for trades using current market prices
"""

import psycopg2
import psycopg2.extras
from datetime import datetime
import os
from dotenv import load_dotenv
import httpx
import asyncio

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

async def get_current_price(exchange, symbol):
    """Get current market price from exchange"""
    try:
        # Convert symbol format for API
        if exchange == 'binance':
            api_symbol = symbol.replace('/', '')
        elif exchange == 'cryptocom':
            api_symbol = symbol.replace('/', '').replace('USDC', 'USD')
        else:
            api_symbol = symbol.replace('/', '')
        
        # Use a public API for current prices
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Try Binance public API first
            response = await client.get(f"https://api.binance.com/api/v3/ticker/price?symbol={api_symbol}")
            if response.status_code == 200:
                data = response.json()
                return float(data['price'])
            
            # Fallback to CoinGecko API
            base_currency = symbol.split('/')[0].lower()
            response = await client.get(f"https://api.coingecko.com/api/v3/simple/price?ids={base_currency}&vs_currencies=usd")
            if response.status_code == 200:
                data = response.json()
                if base_currency in data:
                    return float(data[base_currency]['usd'])
        
        return None
    except Exception as e:
        print(f"❌ Error getting price for {symbol}: {e}")
        return None

def calculate_pnl(entry_price, current_price, position_size):
    """Calculate PnL for a trade"""
    if not entry_price or not current_price or not position_size:
        return 0.0
    
    if side == 'buy':
        # For buy orders: (current_price - entry_price) * position_size
        pnl = calculate_unrealized_pnl_with_fees(entry_price, current_price, position_size)
    else:
        # For sell orders: (entry_price - current_price) * position_size
        pnl = (entry_price - current_price) * position_size
    
    return pnl

async def update_trade_pnl(connection, trade_id, current_price, pnl):
    """Update trade with calculated PnL"""
    try:
        update_query = """
        UPDATE trading.trades 
        SET unrealized_pnl = %s, updated_at = %s
        WHERE trade_id = %s
        """
        params = (pnl, datetime.utcnow(), trade_id)
        
        with connection.cursor() as cursor:
            cursor.execute(update_query, params)
            connection.commit()
        
        return True
    except Exception as e:
        print(f"❌ Error updating PnL for trade {trade_id}: {e}")
        return False

async def main():
    """Main function"""
    print(f"\n💰 CALCULATING ACTUAL PnL - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 100)
    
    # Connect to database
    connection = get_db_connection()
    if not connection:
        print("❌ Failed to connect to database")
        return
    
    try:
        # Get all open trades
        trades_query = """
        SELECT 
            trade_id,
            pair,
            exchange,
            status,
            entry_price,
            position_size,
            unrealized_pnl,
            strategy
        FROM trading.trades 
        WHERE status = 'OPEN'
        ORDER BY created_at DESC
        """
        
        trades = execute_query(connection, trades_query)
        
        if not trades:
            print("❌ No open trades found")
            return
        
        print(f"📊 Found {len(trades)} open trades to calculate PnL for")
        print()
        
        total_pnl = 0.0
        updated_count = 0
        
        for i, trade in enumerate(trades, 1):
            trade_id = trade['trade_id']
            pair = trade['pair']
            exchange = trade['exchange']
            entry_price = float(trade['entry_price']) if trade['entry_price'] else 0
            position_size = float(trade['position_size']) if trade['position_size'] else 0
            
            print(f"🔍 Processing Trade #{i}: {pair} on {exchange}")
            print(f"   Entry Price: {format_currency(entry_price)}")
            print(f"   Position Size: {position_size}")
            
            # Get current price
            current_price = await get_current_price(exchange, pair)
            if current_price:
                print(f"   Current Price: {format_currency(current_price)}")
                
                # Calculate PnL (assuming all trades are buy orders for now)
                pnl = calculate_pnl(entry_price, current_price, position_size, 'buy')
                print(f"   Calculated PnL: {format_currency(pnl)}")
                
                # Update database
                success = await update_trade_pnl(connection, trade_id, current_price, pnl)
                if success:
                    print(f"   ✅ Updated database")
                    updated_count += 1
                    total_pnl += pnl
                else:
                    print(f"   ❌ Failed to update database")
            else:
                print(f"   ❌ Could not get current price")
            
            print("-" * 80)
        
        # Show summary
        print(f"\n📊 SUMMARY:")
        print(f"   Trades Processed: {len(trades)}")
        print(f"   Trades Updated: {updated_count}")
        print(f"   Total Unrealized PnL: {format_currency(total_pnl)}")
        
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
        
        print("\n" + "=" * 100)
        
    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        connection.close()

if __name__ == "__main__":
    asyncio.run(main())
