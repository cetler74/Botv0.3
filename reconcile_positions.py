#!/usr/bin/env python3
"""
Position Reconciliation Script
Syncs database positions with actual exchange balances
"""

import asyncio
import httpx
import json
from typing import Dict, List, Any
from decimal import Decimal, ROUND_DOWN

# Service URLs
DATABASE_URL = "http://localhost:8002"
EXCHANGE_URL = "http://localhost:8003"

async def get_exchange_balance(exchange: str, asset: str) -> float:
    """Get actual balance from exchange"""
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{EXCHANGE_URL}/api/v1/account/balance/{exchange}")
        response.raise_for_status()
        balance_data = response.json()
        return balance_data.get(asset, {}).get('free', 0.0)

async def get_open_trades(exchange: str, pair: str) -> List[Dict]:
    """Get all open trades for a specific pair/exchange"""
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{DATABASE_URL}/api/v1/trades/open")
        response.raise_for_status()
        trades_data = response.json()
        
        return [
            trade for trade in trades_data.get('trades', [])
            if trade.get('exchange') == exchange 
            and trade.get('pair') == pair
            and trade.get('status') == 'OPEN'
            and trade.get('position_size') is not None
        ]

async def update_trade_position(trade_id: str, new_position_size: float) -> bool:
    """Update trade position size in database"""
    async with httpx.AsyncClient() as client:
        update_data = {'position_size': new_position_size}
        response = await client.put(f"{DATABASE_URL}/api/v1/trades/{trade_id}", json=update_data)
        return response.status_code == 200

async def close_trade(trade_id: str, reason: str) -> bool:
    """Close a trade that can't be fulfilled"""
    async with httpx.AsyncClient() as client:
        update_data = {
            'status': 'CLOSED',
            'exit_reason': f'Position reconciliation: {reason}',
            'realized_pnl': 0.0
        }
        response = await client.put(f"{DATABASE_URL}/api/v1/trades/{trade_id}", json=update_data)
        return response.status_code == 200

async def reconcile_pair(exchange: str, pair: str):
    """Reconcile positions for a specific pair/exchange"""
    print(f"\n🔍 Reconciling {pair} on {exchange}")
    
    # Get asset name (e.g., ACH from ACH/USD)
    asset = pair.split('/')[0]
    
    # Get actual exchange balance
    actual_balance = await get_exchange_balance(exchange, asset)
    print(f"📊 Actual {asset} balance on {exchange}: {actual_balance:.8f}")
    
    # Get all open trades
    trades = await get_open_trades(exchange, pair)
    if not trades:
        print(f"✅ No open trades found for {pair} on {exchange}")
        return
    
    # Calculate total position size in database
    total_db_position = sum(trade.get('position_size', 0) for trade in trades)
    print(f"📈 Total database position: {total_db_position:.8f}")
    print(f"🎯 Discrepancy: {total_db_position - actual_balance:.8f} ({((total_db_position - actual_balance) / total_db_position * 100):.1f}% over-allocated)")
    
    if total_db_position <= actual_balance * 1.01:  # Within 1% tolerance
        print(f"✅ Positions are already synchronized (within 1% tolerance)")
        return
    
    if actual_balance < 0.01:  # Essentially zero balance
        print(f"❌ No {asset} balance available - closing all trades")
        for trade in trades:
            success = await close_trade(trade['trade_id'], f"No {asset} balance available")
            status = "✅" if success else "❌"
            print(f"  {status} Closed trade {trade['trade_id']}")
        return
    
    # Proportional redistribution
    print(f"🔄 Redistributing {actual_balance:.8f} {asset} across {len(trades)} trades")
    
    updates = []
    total_allocated = 0.0
    
    # Sort trades by entry time (oldest first) to be fair
    trades.sort(key=lambda x: x.get('entry_time', ''))
    
    for i, trade in enumerate(trades):
        if i == len(trades) - 1:  # Last trade gets remainder
            new_position = actual_balance - total_allocated
        else:
            # Proportional allocation
            proportion = trade.get('position_size', 0) / total_db_position
            new_position = float(Decimal(str(actual_balance * proportion)).quantize(Decimal('0.00000001'), rounding=ROUND_DOWN))
        
        if new_position < 0.000001:  # Too small, close the trade
            success = await close_trade(trade['trade_id'], f"Position too small after reconciliation")
            status = "❌ CLOSED" if success else "❌ FAILED TO CLOSE"
            print(f"  {status} {trade['trade_id']}: {trade.get('position_size', 0):.8f} -> 0 (too small)")
        else:
            updates.append((trade['trade_id'], new_position, trade.get('position_size', 0)))
            total_allocated += new_position
    
    # Apply updates
    for trade_id, new_position, old_position in updates:
        success = await update_trade_position(trade_id, new_position)
        status = "✅" if success else "❌"
        print(f"  {status} {trade_id}: {old_position:.8f} -> {new_position:.8f}")
    
    print(f"🎯 Total allocated: {total_allocated:.8f} / {actual_balance:.8f} ({(total_allocated/actual_balance*100):.1f}%)")

async def main():
    """Main reconciliation function"""
    print("🚀 Starting Position Reconciliation")
    print("=" * 50)
    
    # List of pairs to reconcile (add more as needed)
    reconciliation_targets = [
        ("cryptocom", "ACH/USD"),
        ("cryptocom", "CRO/USD"),
        ("cryptocom", "AAVE/USD"),
        # Add other problematic pairs here
    ]
    
    for exchange, pair in reconciliation_targets:
        try:
            await reconcile_pair(exchange, pair)
        except Exception as e:
            print(f"❌ Error reconciling {pair} on {exchange}: {e}")
    
    print("\n" + "=" * 50)
    print("✅ Position reconciliation completed")

if __name__ == "__main__":
    asyncio.run(main())
