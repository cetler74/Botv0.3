#!/usr/bin/env python3
"""
Fix ACX position size directly via database connection
"""

import asyncio
import asyncpg
import os
import httpx

async def fix_acx_position_direct():
    """Fix ACX/USD position size directly in database"""
    
    # Database connection config
    db_config = {
        'host': os.getenv('DB_HOST', 'localhost'),
        'port': int(os.getenv('DB_PORT', 5432)),
        'user': os.getenv('DB_USER', 'carloslarramba'),
        'password': os.getenv('DB_PASSWORD', 'mypassword'),
        'database': os.getenv('DB_NAME', 'trading_bot_futures')
    }
    
    try:
        # First get the actual ACX balance from exchange
        async with httpx.AsyncClient() as client:
            balance_response = await client.get("http://localhost:8003/api/v1/account/balance/cryptocom")
            if balance_response.status_code != 200:
                print(f"❌ Failed to get balance: {balance_response.status_code}")
                return
                
            balance_data = balance_response.json()
            actual_acx_balance = balance_data.get('ACX', {}).get('free', 0)
            print(f"💰 Actual ACX balance on exchange: {actual_acx_balance}")
        
        # Connect to database
        conn = await asyncpg.connect(**db_config)
        
        # Get current trade details - first check all ACX trades (check both possible table names)
        try:
            check_query = """
            SELECT trade_id, pair, position_size, entry_price, exchange, status
            FROM trading.trades 
            WHERE pair = 'ACX/USD' AND exchange = 'cryptocom'
            """
            all_trades = await conn.fetch(check_query)
        except Exception as e:
            print(f"Trying without schema prefix: {e}")
            check_query = """
            SELECT trade_id, pair, position_size, entry_price, exchange, status
            FROM trades 
            WHERE pair = 'ACX/USD' AND exchange = 'cryptocom'
            """
            all_trades = await conn.fetch(check_query)
        
        print(f"🔍 Found {len(all_trades)} ACX/USD trades on cryptocom:")
        for t in all_trades:
            print(f"   Trade {t['trade_id']}: status={t['status']}, size={t['position_size']}")
        
        # Use the same table reference as successful query above
        table_ref = "trades" if "trades" in check_query else "trading.trades"
        trade_query = f"""
        SELECT trade_id, pair, position_size, entry_price, exchange, status
        FROM {table_ref}
        WHERE pair = 'ACX/USD' AND exchange = 'cryptocom' AND status = 'OPEN'
        """
        
        trade = await conn.fetchrow(trade_query)
        
        if not trade:
            print("❌ No open ACX/USD trade found")
            return
            
        print(f"📋 Found trade: {trade['trade_id']}")
        print(f"   Current position size: {trade['position_size']}")
        print(f"   Entry price: ${trade['entry_price']}")
        print(f"   Exchange: {trade['exchange']}")
        
        # Calculate discrepancy
        discrepancy = float(trade['position_size']) - actual_acx_balance
        print(f"📊 Discrepancy: {discrepancy:.6f} ACX")
        
        if abs(discrepancy) > 0.01:  # Only update if significant difference
            print(f"\n🔧 Updating position size from {trade['position_size']} to {actual_acx_balance}")
            
            # Update the position size directly
            update_query = f"""
            UPDATE {table_ref}
            SET position_size = $1, updated_at = CURRENT_TIMESTAMP
            WHERE trade_id = $2
            """
            
            await conn.execute(update_query, actual_acx_balance, trade['trade_id'])
            
            print(f"✅ SUCCESS: Position size updated to {actual_acx_balance} ACX")
            
            # Calculate cost impact
            original_cost = float(trade['entry_price']) * float(trade['position_size'])
            adjusted_cost = float(trade['entry_price']) * actual_acx_balance
            cost_difference = original_cost - adjusted_cost
            
            print(f"📈 Original investment: ${original_cost:.6f}")
            print(f"📈 Adjusted investment: ${adjusted_cost:.6f}")
            print(f"💸 Cost difference (fees/partial fill): ${cost_difference:.6f}")
        else:
            print("✅ Position size is already accurate")
            
        await conn.close()
        
        print(f"\n🎯 The sell order should now work with {actual_acx_balance} ACX")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        if 'conn' in locals():
            await conn.close()

if __name__ == "__main__":
    asyncio.run(fix_acx_position_direct())