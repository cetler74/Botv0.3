#!/usr/bin/env python3
"""
Backfill Fills Table from Historical Trades
===========================================

This script populates the fills table from existing trade data to enable
Shadow PnL calculations using FIFO methodology.

The fills table is essential for:
- Shadow PnL calculations (realized/unrealized PnL)
- FIFO (First In, First Out) position tracking
- Granular trade execution analysis
- Performance attribution

Usage:
    python3 backfill_fills_table.py
"""

import asyncio
import psycopg2
import uuid
import os
from datetime import datetime, timezone
from dotenv import load_dotenv
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

class FillsBackfiller:
    def __init__(self):
        self.db_config = {
            'host': os.getenv('DB_HOST', 'localhost'),
            'port': os.getenv('DB_PORT', 5432),
            'database': os.getenv('DB_NAME', 'trading_bot'),
            'user': os.getenv('DB_USER', 'trading_user'),
            'password': os.getenv('DB_PASSWORD', 'trading_password')
        }
    
    def connect_db(self):
        """Connect to the database"""
        try:
            conn = psycopg2.connect(**self.db_config)
            conn.autocommit = True
            return conn
        except Exception as e:
            logger.error(f"Database connection failed: {e}")
            raise
    
    def get_historical_trades(self, conn):
        """Get all historical trades that need fills created"""
        cursor = conn.cursor()
        
        # Get trades that have been executed (not just created)
        query = """
        SELECT 
            trade_id,
            pair,
            strategy,
            side,
            order_type,
            price,
            amount,
            fee,
            timestamp,
            status,
            entry_price,
            exit_price,
            entry_time,
            exit_time,
            realized_pnl
        FROM trading.trades 
        WHERE status IN ('CLOSED', 'OPEN')
        AND amount > 0
        ORDER BY entry_time ASC
        """
        
        cursor.execute(query)
        trades = cursor.fetchall()
        cursor.close()
        
        logger.info(f"Found {len(trades)} historical trades to backfill")
        return trades
    
    def create_fill_from_trade(self, conn, trade_data):
        """Create a fill record from a trade"""
        cursor = conn.cursor()
        
        (
            trade_id, pair, strategy, side, order_type, price, 
            amount, fee, timestamp, status, entry_price, 
            exit_price, entry_time, exit_time, realized_pnl
        ) = trade_data
        
        try:
            # Generate a local order ID for this trade
            local_order_id = str(uuid.uuid4())
            
            # Create fill record
            fill_query = """
            INSERT INTO trading.fills (
                fill_id,
                local_order_id,
                exchange_order_id,
                exchange,
                symbol,
                side,
                qty,
                price,
                fee,
                fee_asset,
                trade_id,
                timestamp,
                created_at,
                is_maker,
                commission_rate
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """
            
            # Determine exchange from pair format or use default
            exchange = 'cryptocom'  # Default, could be enhanced with pair analysis
            
            # Determine fill price
            fill_price = entry_price if entry_price else price
            
            # Determine fill timestamp
            fill_timestamp = entry_time if entry_time else timestamp
            
            # Determine if it's a maker or taker (default to taker for market orders)
            is_maker = order_type.lower() != 'market'
            
            # Estimate commission rate (0.1% is typical for most exchanges)
            commission_rate = 0.001
            
            fill_data = (
                str(uuid.uuid4()),  # fill_id
                local_order_id,     # local_order_id
                f"backfill_{trade_id}",  # exchange_order_id (synthetic)
                exchange,           # exchange
                pair,               # symbol
                side.lower(),       # side
                amount,             # qty
                fill_price,         # price
                fee,                # fee
                'USD',              # fee_asset
                trade_id,           # trade_id (our internal trade_id)
                fill_timestamp,     # timestamp
                datetime.now(timezone.utc),  # created_at
                is_maker,           # is_maker
                commission_rate     # commission_rate
            )
            
            cursor.execute(fill_query, fill_data)
            
            # Also create an order mapping record
            order_mapping_query = """
            INSERT INTO trading.order_mappings (
                local_order_id,
                exchange,
                exchange_order_id,
                client_order_id,
                symbol,
                side,
                order_type,
                amount,
                price,
                status,
                created_at,
                trade_id
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """
            
            order_mapping_data = (
                local_order_id,     # local_order_id
                exchange,           # exchange
                f"backfill_{trade_id}",  # exchange_order_id
                f"backfill_{trade_id}",  # client_order_id
                pair,               # symbol
                side.lower(),       # side
                order_type.lower(), # order_type
                amount,             # amount
                fill_price,         # price
                'FILLED',           # status
                fill_timestamp,     # created_at
                trade_id            # trade_id
            )
            
            cursor.execute(order_mapping_query, order_mapping_data)
            
            cursor.close()
            return True
            
        except Exception as e:
            logger.error(f"Failed to create fill for trade {trade_id}: {e}")
            cursor.close()
            return False
    
    def backfill_fills(self):
        """Main backfill process"""
        logger.info("Starting fills table backfill process...")
        
        conn = self.connect_db()
        
        try:
            # Get historical trades
            trades = self.get_historical_trades(conn)
            
            if not trades:
                logger.info("No historical trades found to backfill")
                return
            
            # Process each trade
            successful_fills = 0
            failed_fills = 0
            
            for trade in trades:
                trade_id = trade[0]
                pair = trade[1]
                
                logger.info(f"Processing trade {trade_id} for {pair}")
                
                if self.create_fill_from_trade(conn, trade):
                    successful_fills += 1
                else:
                    failed_fills += 1
            
            logger.info(f"Backfill completed: {successful_fills} successful, {failed_fills} failed")
            
            # Verify the backfill
            self.verify_backfill(conn)
            
        except Exception as e:
            logger.error(f"Backfill process failed: {e}")
            raise
        finally:
            conn.close()
    
    def verify_backfill(self, conn):
        """Verify the backfill was successful"""
        cursor = conn.cursor()
        
        # Check fills count
        cursor.execute("SELECT COUNT(*) FROM trading.fills")
        fills_count = cursor.fetchone()[0]
        
        # Check order mappings count
        cursor.execute("SELECT COUNT(*) FROM trading.order_mappings")
        order_mappings_count = cursor.fetchone()[0]
        
        # Check for any fills with recent data
        cursor.execute("""
            SELECT COUNT(*) FROM trading.fills 
            WHERE created_at > NOW() - INTERVAL '1 hour'
        """)
        recent_fills = cursor.fetchone()[0]
        
        cursor.close()
        
        logger.info(f"Verification results:")
        logger.info(f"  Total fills: {fills_count}")
        logger.info(f"  Total order mappings: {order_mappings_count}")
        logger.info(f"  Recent fills (last hour): {recent_fills}")
        
        if fills_count > 0:
            logger.info("✅ Backfill verification successful!")
        else:
            logger.warning("⚠️  No fills found - backfill may have failed")

def main():
    """Main entry point"""
    try:
        backfiller = FillsBackfiller()
        backfiller.backfill_fills()
        print("✅ Fills table backfill completed successfully!")
    except Exception as e:
        print(f"❌ Backfill failed: {e}")
        return 1
    
    return 0

if __name__ == "__main__":
    exit(main())
