#!/usr/bin/env python3
"""
Balance Discrepancy Investigation Script
Deep dive into the locked balance mismatch issue
"""

import asyncio
import asyncpg
import logging
from datetime import datetime, timedelta
import json

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

async def investigate_balance_discrepancy():
    """Investigate the locked balance discrepancy"""
    
    logger.info("🔍 Starting Balance Discrepancy Investigation")
    
    # Connect to database
    try:
        conn = await asyncpg.connect(
            host='localhost',
            port=5432,
            database='trading_bot_futures',
            user='carloslarramba',
            password='mypassword'
        )
        logger.info("✅ Connected to database")
    except Exception as e:
        logger.error(f"❌ Failed to connect to database: {e}")
        return
    
    try:
        # 1. Check current balance status
        logger.info("\n1. CURRENT BALANCE STATUS")
        logger.info("=" * 50)
        
        balance_query = """
        SELECT 
            exchange,
            balance,
            available_balance,
            (balance - available_balance) as locked_balance,
            total_pnl,
            daily_pnl,
            timestamp
        FROM trading.balance 
        ORDER BY exchange;
        """
        
        balance_results = await conn.fetch(balance_query)
        for row in balance_results:
            logger.info(f"Exchange: {row['exchange']}")
            logger.info(f"  Total Balance: ${row['balance']}")
            logger.info(f"  Available Balance: ${row['available_balance']}")
            logger.info(f"  Locked Balance: ${row['locked_balance']}")
            logger.info(f"  Total PnL: ${row['total_pnl']}")
            logger.info(f"  Daily PnL: ${row['daily_pnl']}")
            logger.info(f"  Last Update: {row['timestamp']}")
            logger.info("-" * 30)
        
        # 2. Check for open trades
        logger.info("\n2. OPEN TRADES ANALYSIS")
        logger.info("=" * 50)
        
        open_trades_query = """
        SELECT 
            trade_id,
            exchange,
            pair,
            entry_price,
            position_size,
            (entry_price * ABS(position_size)) as position_value_usdc,
            entry_time,
            strategy,
            status
        FROM trading.trades 
        WHERE status = 'OPEN'
        ORDER BY exchange, entry_time DESC;
        """
        
        open_trades = await conn.fetch(open_trades_query)
        if open_trades:
            logger.info(f"Found {len(open_trades)} open trades:")
            for trade in open_trades:
                logger.info(f"  Trade {trade['trade_id'][:8]}...")
                logger.info(f"    Exchange: {trade['exchange']}")
                logger.info(f"    Pair: {trade['pair']}")
                logger.info(f"    Position Value: ${trade['position_value_usdc']:.2f}")
                logger.info(f"    Entry Time: {trade['entry_time']}")
                logger.info(f"    Status: {trade['status']}")
                logger.info("-" * 25)
        else:
            logger.info("✅ No open trades found")
        
        # 3. Calculate expected locked balance by exchange
        logger.info("\n3. EXPECTED LOCKED BALANCE BY EXCHANGE")
        logger.info("=" * 50)
        
        expected_locked_query = """
        SELECT 
            exchange,
            COUNT(*) as open_trade_count,
            SUM(entry_price * ABS(position_size)) as expected_locked_balance
        FROM trading.trades 
        WHERE status = 'OPEN'
        GROUP BY exchange
        ORDER BY exchange;
        """
        
        expected_locked = await conn.fetch(expected_locked_query)
        expected_by_exchange = {}
        
        if expected_locked:
            for row in expected_locked:
                expected_by_exchange[row['exchange']] = {
                    'count': row['open_trade_count'],
                    'amount': float(row['expected_locked_balance'])
                }
                logger.info(f"Exchange: {row['exchange']}")
                logger.info(f"  Open Trades: {row['open_trade_count']}")
                logger.info(f"  Expected Locked: ${row['expected_locked_balance']:.2f}")
        else:
            logger.info("✅ No open trades found - expected locked balance should be $0.00 for all exchanges")
        
        # 4. Compare actual vs expected
        logger.info("\n4. ACTUAL VS EXPECTED COMPARISON")
        logger.info("=" * 50)
        
        for balance_row in balance_results:
            exchange = balance_row['exchange']
            actual_locked = float(balance_row['locked_balance'])
            expected_info = expected_by_exchange.get(exchange, {'count': 0, 'amount': 0.0})
            expected_locked_amount = expected_info['amount']
            discrepancy = actual_locked - expected_locked_amount
            
            logger.info(f"Exchange: {exchange}")
            logger.info(f"  Actual Locked: ${actual_locked:.2f}")
            logger.info(f"  Expected Locked: ${expected_locked_amount:.2f}")
            logger.info(f"  Discrepancy: ${discrepancy:.2f}")
            
            if abs(discrepancy) > 1.0:
                logger.warning(f"  ⚠️  SIGNIFICANT DISCREPANCY: ${discrepancy:.2f}")
                
                # Look for recent closed trades that might explain the discrepancy
                recent_closed_query = """
                SELECT 
                    trade_id,
                    exit_time,
                    realized_pnl,
                    entry_price * ABS(position_size) as position_value
                FROM trading.trades 
                WHERE exchange = $1 
                  AND status = 'CLOSED' 
                  AND exit_time >= NOW() - INTERVAL '24 hours'
                ORDER BY exit_time DESC
                LIMIT 10;
                """
                
                recent_closed = await conn.fetch(recent_closed_query, exchange)
                if recent_closed:
                    logger.info(f"  Recent closed trades for {exchange}:")
                    for trade in recent_closed:
                        logger.info(f"    Trade {trade['trade_id'][:8]}... closed at {trade['exit_time']}")
                        logger.info(f"      Position Value: ${trade['position_value']:.2f}")
                        logger.info(f"      Realized PnL: ${trade['realized_pnl']:.2f}")
            else:
                logger.info(f"  ✅ Balance is consistent")
            
            logger.info("-" * 30)
        
        # 5. Check for orphaned balance records
        logger.info("\n5. ORPHANED BALANCE ANALYSIS")
        logger.info("=" * 50)
        
        # Check if there are balance updates that don't correspond to recent trades
        balance_history_query = """
        SELECT 
            exchange,
            balance,
            available_balance,
            (balance - available_balance) as locked_balance,
            timestamp,
            LAG(balance - available_balance) OVER (PARTITION BY exchange ORDER BY timestamp) as prev_locked
        FROM trading.balance 
        WHERE timestamp >= NOW() - INTERVAL '24 hours'
        ORDER BY exchange, timestamp DESC;
        """
        
        balance_history = await conn.fetch(balance_history_query)
        if balance_history:
            logger.info("Recent balance changes:")
            for row in balance_history:
                prev_locked = row['prev_locked'] or 0
                locked_change = float(row['locked_balance']) - prev_locked
                
                if abs(locked_change) > 1.0:
                    logger.info(f"  {row['exchange']} at {row['timestamp']}")
                    logger.info(f"    Locked balance changed by: ${locked_change:.2f}")
                    logger.info(f"    Current locked: ${row['locked_balance']:.2f}")
        
        # 6. Recommendations
        logger.info("\n6. RECOMMENDATIONS")
        logger.info("=" * 50)
        
        total_discrepancy = sum(
            abs(float(row['locked_balance']) - expected_by_exchange.get(row['exchange'], {'amount': 0.0})['amount']) 
            for row in balance_results
        )
        
        if total_discrepancy > 1.0:
            logger.warning("🚨 BALANCE DISCREPANCIES DETECTED")
            logger.info("Recommended actions:")
            logger.info("1. Check if trades were closed but balance updates failed")
            logger.info("2. Look for network failures during balance update operations")
            logger.info("3. Check database logs for constraint violations or deadlocks")
            logger.info("4. Verify that all closed trades have corresponding balance updates")
            logger.info("5. Consider implementing balance reconciliation process")
            logger.info("6. Add transaction logging for balance updates")
            
            # Suggest a balance fix if no open trades
            if not open_trades:
                logger.info("\n🔧 POTENTIAL FIX:")
                logger.info("Since there are no open trades, locked balances should be $0.00")
                logger.info("You could run an update to fix this:")
                logger.info("UPDATE trading.balance SET available_balance = balance WHERE exchange IN ('binance', 'bybit', 'cryptocom');")
        else:
            logger.info("✅ Balances appear to be consistent")
    
    except Exception as e:
        logger.error(f"❌ Error during investigation: {e}")
    
    finally:
        await conn.close()
        logger.info("🔍 Investigation completed")

if __name__ == "__main__":
    asyncio.run(investigate_balance_discrepancy())