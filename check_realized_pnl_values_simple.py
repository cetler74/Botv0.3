#!/usr/bin/env python3
"""
Check realized_pnl values in the database using psycopg2
"""

import psycopg2
import os
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def check_closed_trades():
    """Check all closed trades and their realized_pnl values"""
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
        
        # Check closed trades
        query = """
        SELECT 
            id, status, realized_pnl, entry_price, exit_price, position_size,
            pair, exchange
        FROM trading.trades 
        WHERE status = 'CLOSED'
        ORDER BY id DESC
        """
        
        cursor.execute(query)
        trades = cursor.fetchall()
        
        logger.info(f"📊 Found {len(trades)} closed trades")
        
        total_realized_pnl = 0
        zero_pnl_count = 0
        
        for trade in trades:
            trade_id, status, realized_pnl, entry_price, exit_price, position_size, pair, exchange = trade
            realized_pnl = float(realized_pnl) if realized_pnl is not None else 0
            total_realized_pnl += realized_pnl
            
            if realized_pnl == 0:
                zero_pnl_count += 1
                logger.info(f"Trade {trade_id} ({pair}): realized_pnl = {realized_pnl}")
                
                # Calculate what it should be
                entry_price = float(entry_price)
                exit_price = float(exit_price)
                position_size = float(position_size)
                calculated_pnl = (exit_price - entry_price) * position_size
                
                logger.info(f"  Entry: ${entry_price:.6f}, Exit: ${exit_price:.6f}, Size: {position_size:.3f}")
                logger.info(f"  Calculated PnL: ${calculated_pnl:.2f}")
                
        logger.info(f"💰 Total realized PnL: ${total_realized_pnl:.2f}")
        logger.info(f"🔢 Trades with zero PnL: {zero_pnl_count}")
        
        # Check portfolio summary query
        summary_query = """
        SELECT 
            COALESCE(SUM(realized_pnl), 0) as total_realized_pnl,
            COALESCE(SUM(CASE 
                WHEN exit_time >= CURRENT_DATE 
                THEN realized_pnl 
                ELSE 0 
            END), 0) as daily_realized_pnl
        FROM trading.trades 
        WHERE status = 'CLOSED' AND realized_pnl IS NOT NULL
        """
        
        cursor.execute(summary_query)
        result = cursor.fetchone()
        
        logger.info("📊 Portfolio Summary Query Results:")
        logger.info(f"   Total Realized PnL: ${result[0]:.2f}")
        logger.info(f"   Daily Realized PnL: ${result[1]:.2f}")
        
        cursor.close()
        conn.close()
        
    except Exception as e:
        logger.error(f"❌ Error: {e}")

if __name__ == "__main__":
    check_closed_trades()
