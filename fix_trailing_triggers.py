#!/usr/bin/env python3
"""
Fix existing trades with incorrect trailing stop triggers
Replace percentage values with actual price levels
"""

import asyncio
import asyncpg
import os
from decimal import Decimal

async def fix_trailing_triggers():
    """Fix all trades with incorrect trailing stop triggers"""
    
    # Database connection
    db_config = {
        'host': os.getenv('DB_HOST', 'localhost'),
        'port': int(os.getenv('DB_PORT', 5432)),
        'user': os.getenv('DB_USER', 'trading_bot'),
        'password': os.getenv('DB_PASSWORD', 'trading_password'),
        'database': os.getenv('DB_NAME', 'trading_bot')
    }
    
    try:
        conn = await asyncpg.connect(**db_config)
        
        # Get all active trades with trailing stops
        query = """
        SELECT trade_id, pair, exchange, entry_price, highest_price, 
               trail_stop_trigger, trail_stop, position_size
        FROM trading.trades 
        WHERE status = 'OPEN' 
        AND trail_stop = 'active'
        AND trail_stop_trigger IS NOT NULL
        ORDER BY trade_id
        """
        
        trades = await conn.fetch(query)
        print(f"Found {len(trades)} active trailing stop trades to fix")
        
        fixes_applied = 0
        
        for trade in trades:
            trade_id = trade['trade_id']
            highest_price = float(trade['highest_price'] or trade['entry_price'])
            current_trigger = trade['trail_stop_trigger']
            
            # Check if trigger looks like a percentage (< 1.0 or way above highest_price)
            if current_trigger and (current_trigger < 1.0 or current_trigger > highest_price * 1.5):
                # This looks like a percentage - fix it
                trailing_step_pct = 1.0  # Default 1% step
                trailing_step_decimal = trailing_step_pct / 100
                correct_trigger_price = highest_price * (1 - trailing_step_decimal)
                
                print(f"Trade {trade_id}: {trade['pair']} on {trade['exchange']}")
                print(f"  Highest: ${highest_price:.6f}")
                print(f"  Old trigger: {current_trigger} (incorrect)")
                print(f"  New trigger: ${correct_trigger_price:.6f} (correct)")
                
                # Update the trade
                update_query = """
                UPDATE trading.trades 
                SET trail_stop_trigger = $1
                WHERE trade_id = $2
                """
                
                await conn.execute(update_query, correct_trigger_price, trade_id)
                fixes_applied += 1
                print(f"  ✅ FIXED")
            else:
                print(f"Trade {trade_id}: Trigger ${current_trigger:.6f} looks correct (below highest ${highest_price:.6f})")
        
        print(f"\n✅ Applied {fixes_applied} trailing stop trigger fixes")
        
    except Exception as e:
        print(f"Error fixing trailing triggers: {e}")
    finally:
        if 'conn' in locals():
            await conn.close()

if __name__ == "__main__":
    asyncio.run(fix_trailing_triggers())