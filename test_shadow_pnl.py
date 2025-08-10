#!/usr/bin/env python3
"""
Test Shadow PnL Calculation
===========================

This script tests the Shadow PnL calculation using the backfilled fills data.
"""

import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

def test_shadow_pnl():
    """Test Shadow PnL calculation with fills data"""
    
    conn = psycopg2.connect(
        host=os.getenv('DB_HOST', 'localhost'),
        port=os.getenv('DB_PORT', 5432),
        database=os.getenv('DB_NAME', 'trading_bot'),
        user=os.getenv('DB_USER', 'trading_user'),
        password=os.getenv('DB_PASSWORD', 'trading_password')
    )
    
    cursor = conn.cursor()
    
    print("=== Shadow PnL Calculation Test ===")
    
    # Get fills for Crypto.com
    cursor.execute("""
        SELECT 
            symbol,
            side,
            qty,
            price,
            fee,
            timestamp
        FROM trading.fills 
        WHERE exchange = 'cryptocom'
        ORDER BY timestamp ASC
    """)
    
    fills = cursor.fetchall()
    print(f"Found {len(fills)} fills for Crypto.com")
    
    if fills:
        print("\nSample fills:")
        for fill in fills[:5]:
            symbol, side, qty, price, fee, timestamp = fill
            notional = float(qty) * float(price)
            print(f"  {symbol}: {side} {qty} @ ${price:.4f} = ${notional:.2f} (fee: ${fee:.4f})")
    
    # Calculate realized PnL using FIFO method
    print("\n=== FIFO PnL Calculation ===")
    
    # Get all fills for a specific symbol (e.g., ETHFI/USDC)
    cursor.execute("""
        SELECT 
            side,
            qty,
            price,
            fee,
            timestamp
        FROM trading.fills 
        WHERE symbol = 'ETHFI/USDC'
        ORDER BY timestamp ASC
    """)
    
    ethfi_fills = cursor.fetchall()
    print(f"ETHFI/USDC fills: {len(ethfi_fills)}")
    
    if ethfi_fills:
        # Simple FIFO calculation
        position = 0
        avg_cost = 0
        realized_pnl = 0
        
        for side, qty, price, fee, timestamp in ethfi_fills:
            qty = float(qty)
            price = float(price)
            fee = float(fee)
            
            if side == 'buy':
                if position >= 0:  # Adding to long position
                    total_cost = position * avg_cost + qty * price
                    position += qty
                    avg_cost = total_cost / position if position > 0 else 0
                else:  # Closing short position
                    close_qty = min(abs(position), qty)
                    realized_pnl += (avg_cost - price) * close_qty - fee
                    position += close_qty
                    if position > 0:  # Still have some left to buy
                        avg_cost = price
            else:  # sell
                if position > 0:  # Closing long position
                    close_qty = min(position, qty)
                    realized_pnl += (price - avg_cost) * close_qty - fee
                    position -= close_qty
                else:  # Adding to short position
                    total_cost = abs(position) * avg_cost + qty * price
                    position -= qty
                    avg_cost = total_cost / abs(position) if position < 0 else 0
        
        print(f"Final position: {position:.8f}")
        print(f"Average cost: ${avg_cost:.4f}")
        print(f"Realized PnL: ${realized_pnl:.2f}")
    
    cursor.close()
    conn.close()

if __name__ == "__main__":
    test_shadow_pnl()
