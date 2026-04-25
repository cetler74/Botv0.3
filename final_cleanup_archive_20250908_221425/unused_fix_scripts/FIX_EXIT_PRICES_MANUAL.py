#!/usr/bin/env python3
"""
Manual Exit Price Fix
This script manually fixes exit prices for all closed trades using Redis data
"""

import subprocess
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def run_sql_command(sql):
    """Run SQL command in database"""
    result = subprocess.run([
        'docker', 'exec', '-i', 'trading-bot-postgres', 
        'psql', '-U', 'carloslarramba', '-d', 'trading_bot_futures', '-c',
        sql
    ], capture_output=True, text=True)
    return result

def get_redis_data(order_id):
    """Get Redis data for an order"""
    result = subprocess.run([
        'docker', 'exec', '-i', 'trading-bot-redis', 
        'redis-cli', 'HGETALL', f'orders:{order_id}'
    ], capture_output=True, text=True)
    
    if result.returncode != 0:
        return None
    
    # Parse Redis output
    lines = result.stdout.strip().split('\n')
    data = {}
    for i in range(0, len(lines), 2):
        if i + 1 < len(lines):
            key = lines[i].strip()
            value = lines[i + 1].strip()
            data[key] = value
    
    return data

def main():
    """Main function"""
    logger.info("🚀 Starting manual exit price fix...")
    
    # Get all closed trades
    result = run_sql_command("SELECT trade_id, pair, entry_price, exit_price, exit_id, position_size, realized_pnl FROM trading.trades WHERE status = 'CLOSED' ORDER BY exit_time DESC;")
    
    if result.returncode != 0:
        logger.error(f"❌ Failed to get trades: {result.stderr}")
        return
    
    # Parse trades
    lines = result.stdout.strip().split('\n')
    trades = []
    
    for line in lines:
        if '|' in line and 'trade_id' not in line and '---' not in line and line.strip():
            parts = [part.strip() for part in line.split('|')]
            if len(parts) >= 7:
                trades.append({
                    'trade_id': parts[0],
                    'pair': parts[1],
                    'entry_price': float(parts[2]) if parts[2] else 0,
                    'exit_price': float(parts[3]) if parts[3] else 0,
                    'exit_id': parts[4],
                    'position_size': float(parts[5]) if parts[5] else 0,
                    'realized_pnl': float(parts[6]) if parts[6] else 0
                })
    
    logger.info(f"📊 Found {len(trades)} closed trades")
    
    fixed_count = 0
    skipped_count = 0
    
    for trade in trades:
        trade_id = trade['trade_id']
        exit_id = trade['exit_id']
        current_exit_price = trade['exit_price']
        entry_price = trade['entry_price']
        position_size = trade['position_size']
        
        if not exit_id or not current_exit_price:
            logger.debug(f"⚠️ Skipping trade {trade_id} - missing exit_id or exit_price")
            skipped_count += 1
            continue
        
        # Get Redis data
        redis_data = get_redis_data(exit_id)
        if not redis_data:
            logger.debug(f"⚠️ Skipping trade {trade_id} - no Redis data for {exit_id}")
            skipped_count += 1
            continue
        
        order_price = float(redis_data.get('price', 0))
        filled_price = float(redis_data.get('filled_price', 0))
        fees = float(redis_data.get('fees', 0))
        
        if not order_price or not filled_price:
            logger.debug(f"⚠️ Skipping trade {trade_id} - missing price data")
            skipped_count += 1
            continue
        
        # Check if we need to fix
        if abs(current_exit_price - filled_price) < 0.000001:
            logger.debug(f"✅ Trade {trade_id} already correct")
            skipped_count += 1
            continue
        
        # Calculate new PnL using order price (not filled price)
        new_realized_pnl = (order_price - entry_price) * position_size - fees
        
        # Update trade
        update_sql = f"UPDATE trading.trades SET exit_price = {order_price}, realized_pnl = {new_realized_pnl}, updated_at = NOW() WHERE trade_id = '{trade_id}';"
        
        update_result = run_sql_command(update_sql)
        if update_result.returncode == 0:
            logger.info(f"✅ Fixed trade {trade_id}: {current_exit_price} → {order_price}, PnL: {trade['realized_pnl']:.4f} → {new_realized_pnl:.4f}")
            fixed_count += 1
        else:
            logger.error(f"❌ Failed to update trade {trade_id}: {update_result.stderr}")
    
    logger.info(f"\n📊 SUMMARY:")
    logger.info(f"✅ Trades Fixed: {fixed_count}")
    logger.info(f"⏭️ Trades Skipped: {skipped_count}")
    logger.info(f"🎉 Manual exit price fix completed!")

if __name__ == "__main__":
    main()
