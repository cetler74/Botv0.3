#!/usr/bin/env python3
"""
Fix exit_time values for closed trades using psycopg2
"""

import psycopg2
import os
import logging
from datetime import datetime, timezone

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def fix_exit_time():
    """Fix exit_time values for all closed trades"""
    try:
        # Connect to database
        conn = psycopg2.connect(
            host="postgres",
            port="5432",
            database="trading_bot_futures",
            user="carloslarramba",
            password="mypassword"
        )
        
        cursor = conn.cursor()
        
        # Get all closed trades with exit_price but null exit_time
        query = """
        SELECT 
            id, trade_id, pair, exchange, exit_price, exit_time, status
        FROM trading.trades 
        WHERE status = 'CLOSED' 
        AND exit_price IS NOT NULL
        AND exit_time IS NULL
        ORDER BY id
        """
        
        cursor.execute(query)
        trades = cursor.fetchall()
        
        logger.info(f"📊 Found {len(trades)} closed trades with null exit_time")
        
        if not trades:
            logger.info("✅ No trades need exit_time fixing")
            return
        
        # Set exit_time to today's date for all these trades
        today = datetime.now(timezone.utc)
        updated_count = 0
        
        for trade in trades:
            trade_id, trade_uuid, pair, exchange, exit_price, exit_time, status = trade
            
            # Update the trade with today's exit_time
            update_query = """
            UPDATE trading.trades 
            SET exit_time = %s, updated_at = %s
            WHERE id = %s
            """
            
            cursor.execute(update_query, (today, datetime.utcnow(), trade_id))
            
            logger.info(f"✅ Updated trade {trade_id} ({pair}): "
                       f"Exit Price: ${exit_price:.6f}, Exit Time: {today.isoformat()}")
            
            updated_count += 1
        
        # Commit the changes
        conn.commit()
        
        logger.info(f"🎉 Fixed exit_time for {updated_count} trades")
        
        # Verify the fix
        verify_query = """
        SELECT 
            COUNT(*) as total_closed_trades,
            COALESCE(SUM(realized_pnl), 0) as total_realized_pnl,
            COALESCE(SUM(CASE 
                WHEN exit_time >= CURRENT_DATE 
                THEN realized_pnl 
                ELSE 0 
            END), 0) as daily_realized_pnl
        FROM trading.trades 
        WHERE status = 'CLOSED' AND realized_pnl IS NOT NULL
        """
        
        cursor.execute(verify_query)
        result = cursor.fetchone()
        
        logger.info("📊 Verification Results:")
        logger.info(f"   Total Closed Trades: {result[0]}")
        logger.info(f"   Total Realized PnL: ${result[1]:.2f}")
        logger.info(f"   Daily Realized PnL: ${result[2]:.2f}")
        
        cursor.close()
        conn.close()
        
    except Exception as e:
        logger.error(f"❌ Error: {e}")
        if 'conn' in locals():
            conn.rollback()
            conn.close()

if __name__ == "__main__":
    fix_exit_time()
