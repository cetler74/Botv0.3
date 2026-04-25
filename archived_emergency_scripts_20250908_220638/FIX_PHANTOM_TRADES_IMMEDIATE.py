#!/usr/bin/env python3
"""
Fix Phantom Trades Immediately
This script fixes the immediate phantom trades by updating order mappings and closing trades
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
    logger.info("🚀 Starting immediate phantom trade fix...")
    
    # List of phantom trades to fix
    phantom_trades = [
        {
            'order_id': '2340822881',
            'description': 'XRP/USDC - Order filled but trade still OPEN'
        },
        {
            'order_id': '323536925', 
            'description': 'BCH/USDC - Order filled but mapping still PENDING'
        }
    ]
    
    fixed_count = 0
    
    for phantom in phantom_trades:
        order_id = phantom['order_id']
        description = phantom['description']
        
        logger.info(f"🔍 Processing {description} (Order: {order_id})")
        
        # Get Redis data
        redis_data = get_redis_data(order_id)
        if not redis_data:
            logger.error(f"❌ No Redis data found for order {order_id}")
            continue
        
        # Extract data
        trade_id = redis_data.get('trade_id')
        filled_price = float(redis_data.get('filled_price', 0))
        filled_amount = float(redis_data.get('filled_amount', 0))
        fees = float(redis_data.get('fees', 0))
        client_order_id = redis_data.get('client_order_id')
        
        if not trade_id:
            logger.error(f"❌ No trade_id in Redis data for order {order_id}")
            continue
        
        logger.info(f"📊 Order {order_id}: filled_price={filled_price}, filled_amount={filled_amount}, fees={fees}")
        
        # Check current trade status
        trade_result = run_sql_command(f"SELECT trade_id, pair, entry_price, position_size, status, exit_price FROM trading.trades WHERE trade_id = '{trade_id}';")
        
        if trade_result.returncode != 0:
            logger.error(f"❌ Failed to get trade data: {trade_result.stderr}")
            continue
        
        # Parse trade data
        lines = trade_result.stdout.strip().split('\n')
        trade_data = None
        for line in lines:
            if '|' in line and 'trade_id' not in line and '---' not in line and line.strip():
                parts = [part.strip() for part in line.split('|')]
                if len(parts) >= 6:
                    trade_data = {
                        'trade_id': parts[0],
                        'pair': parts[1],
                        'entry_price': float(parts[2]) if parts[2] else 0,
                        'position_size': float(parts[3]) if parts[3] else 0,
                        'status': parts[4],
                        'exit_price': float(parts[5]) if parts[5] else 0
                    }
                    break
        
        if not trade_data:
            logger.error(f"❌ Could not parse trade data for {trade_id}")
            continue
        
        logger.info(f"📊 Trade {trade_id}: status={trade_data['status']}, entry_price={trade_data['entry_price']}, exit_price={trade_data['exit_price']}")
        
        # Fix 1: Update order mapping to FILLED
        logger.info(f"🔧 Updating order mapping for {client_order_id}")
        update_mapping_sql = f"""
            UPDATE trading.order_mappings 
            SET status = 'FILLED',
                filled_price = {filled_price},
                filled_amount = {filled_amount},
                fees = {fees},
                filled_at = NOW(),
                updated_at = NOW()
            WHERE client_order_id = '{client_order_id}';
        """
        
        mapping_result = run_sql_command(update_mapping_sql)
        if mapping_result.returncode == 0:
            logger.info(f"✅ Updated order mapping for {client_order_id}")
        else:
            logger.error(f"❌ Failed to update order mapping: {mapping_result.stderr}")
        
        # Fix 2: Close trade if it's still OPEN
        if trade_data['status'] == 'OPEN':
            logger.info(f"🔧 Closing trade {trade_id}")
            
            # Calculate realized PnL
            entry_price = trade_data['entry_price']
            position_size = trade_data['position_size']
            realized_pnl = (filled_price - entry_price) * position_size - fees
            
            close_trade_sql = f"""
                UPDATE trading.trades 
                SET status = 'CLOSED',
                    exit_price = {filled_price},
                    exit_time = NOW(),
                    exit_id = '{order_id}',
                    realized_pnl = {realized_pnl},
                    exit_reason = 'phantom_trade_fix',
                    updated_at = NOW()
                WHERE trade_id = '{trade_id}';
            """
            
            close_result = run_sql_command(close_trade_sql)
            if close_result.returncode == 0:
                logger.info(f"✅ Closed trade {trade_id} - PnL: ${realized_pnl:.4f}")
            else:
                logger.error(f"❌ Failed to close trade: {close_result.stderr}")
        
        # Fix 3: Update exit_price if trade is already CLOSED but has wrong exit_price
        elif trade_data['status'] == 'CLOSED' and abs(trade_data['exit_price'] - filled_price) > 0.000001:
            logger.info(f"🔧 Updating exit price for trade {trade_id}")
            
            # Calculate realized PnL
            entry_price = trade_data['entry_price']
            position_size = trade_data['position_size']
            realized_pnl = (filled_price - entry_price) * position_size - fees
            
            update_exit_sql = f"""
                UPDATE trading.trades 
                SET exit_price = {filled_price},
                    realized_pnl = {realized_pnl},
                    updated_at = NOW()
                WHERE trade_id = '{trade_id}';
            """
            
            exit_result = run_sql_command(update_exit_sql)
            if exit_result.returncode == 0:
                logger.info(f"✅ Updated exit price for trade {trade_id} - PnL: ${realized_pnl:.4f}")
            else:
                logger.error(f"❌ Failed to update exit price: {exit_result.stderr}")
        
        fixed_count += 1
        logger.info(f"✅ Completed fix for {description}")
    
    logger.info(f"\n📊 IMMEDIATE PHANTOM TRADE FIX SUMMARY:")
    logger.info(f"✅ Trades Fixed: {fixed_count}")
    logger.info(f"🎉 Immediate phantom trade fix completed!")

if __name__ == "__main__":
    main()
