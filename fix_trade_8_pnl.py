#!/usr/bin/env python3
"""
Fix realized_pnl for trade 8 specifically
"""

import psycopg2
import os
import logging
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def fix_trade_8_pnl():
    """Fix realized_pnl for trade 8"""
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
        
        # Get trade 8 details
        query = """
        SELECT 
            id, entry_price, exit_price, position_size, realized_pnl, pair
        FROM trading.trades 
        WHERE id = 8
        """
        
        cursor.execute(query)
        trade = cursor.fetchone()
        
        if not trade:
            logger.error("❌ Trade 8 not found")
            return
        
        trade_id, entry_price, exit_price, position_size, realized_pnl, pair = trade
        
        logger.info(f"📊 Trade 8 ({pair}):")
        logger.info(f"   Entry Price: ${entry_price:.6f}")
        logger.info(f"   Exit Price: ${exit_price:.6f}")
        logger.info(f"   Position Size: {position_size:.3f}")
        logger.info(f"   Current Realized PnL: ${realized_pnl:.2f}")
        
        # Calculate correct PnL
        entry_price = float(entry_price)
        exit_price = float(exit_price)
        position_size = float(position_size)
        calculated_pnl = (exit_price - entry_price) * position_size
        
        logger.info(f"   Calculated PnL: ${calculated_pnl:.2f}")
        
        if abs(calculated_pnl - realized_pnl) > 0.01:  # If difference is more than 1 cent
            # Update the trade
            update_query = """
            UPDATE trading.trades 
            SET realized_pnl = %s, updated_at = %s
            WHERE id = 8
            """
            
            cursor.execute(update_query, (calculated_pnl, datetime.utcnow()))
            conn.commit()
            
            logger.info(f"✅ Updated trade 8 realized_pnl from ${realized_pnl:.2f} to ${calculated_pnl:.2f}")
        else:
            logger.info("✅ Trade 8 PnL is already correct")
        
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
    fix_trade_8_pnl()
