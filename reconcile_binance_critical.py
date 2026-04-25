#!/usr/bin/env python3
"""
CRITICAL: Reconcile Binance Exchange Balances vs Database Positions
Fixes database-exchange mismatches by identifying orphaned positions
"""

import asyncio
import httpx
import json
from datetime import datetime

async def get_binance_balances():
    """Get current Binance balances"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get("http://localhost:8003/api/v1/account/balance/binance")
            if response.status_code == 200:
                return response.json().get('data', [])
            else:
                print(f"❌ Failed to get Binance balances: {response.status_code}")
                return []
    except Exception as e:
        print(f"❌ Error getting Binance balances: {e}")
        return []

async def get_database_positions():
    """Get current database positions for Binance"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get("http://localhost:8002/api/v1/trades?exchange=binance&status=OPEN")
            if response.status_code == 200:
                return response.json().get('trades', [])
            else:
                print(f"❌ Failed to get database positions: {response.status_code}")
                return []
    except Exception as e:
        print(f"❌ Error getting database positions: {e}")
        return []

async def main():
    print("🚨 CRITICAL RECONCILIATION: Binance Exchange vs Database")
    print("=" * 60)
    
    # Get data from both sources
    binance_balances = await get_binance_balances()
    database_positions = await get_database_positions()
    
    # Filter significant balances (> $1 value)
    significant_balances = []
    for balance in binance_balances:
        asset = balance.get('asset')
        total = float(balance.get('free', 0)) + float(balance.get('locked', 0))
        if total > 0.001 and asset not in ['USDC', 'USD']:  # Exclude base currencies
            significant_balances.append({
                'asset': asset,
                'total': total,
                'free': float(balance.get('free', 0)),
                'locked': float(balance.get('locked', 0))
            })
    
    # Create database position map
    db_positions = {}
    for trade in database_positions:
        pair = trade.get('pair', '')
        if '/' in pair:
            asset = pair.split('/')[0]
            position_size = float(trade.get('position_size', 0))
            if asset in db_positions:
                db_positions[asset] += position_size
            else:
                db_positions[asset] = position_size
    
    print("\n📊 BALANCE RECONCILIATION REPORT")
    print("-" * 60)
    
    mismatches = []
    for balance in significant_balances:
        asset = balance['asset']
        exchange_total = balance['total']
        database_total = db_positions.get(asset, 0)
        
        difference = exchange_total - database_total
        
        status = "✅ MATCH" if abs(difference) < 0.001 else "❌ MISMATCH"
        
        print(f"{status} {asset}:")
        print(f"  Exchange: {exchange_total:.8f}")
        print(f"  Database: {database_total:.8f}")
        print(f"  Difference: {difference:.8f}")
        print()
        
        if abs(difference) > 0.001:
            mismatches.append({
                'asset': asset,
                'exchange_balance': exchange_total,
                'database_balance': database_total,
                'difference': difference,
                'pair': f"{asset}/USDC"
            })
    
    if mismatches:
        print("🚨 CRITICAL MISMATCHES FOUND:")
        print("-" * 40)
        for mismatch in mismatches:
            print(f"• {mismatch['asset']}: {mismatch['difference']:.8f} orphaned on exchange")
            print(f"  Pair: {mismatch['pair']}")
            print(f"  Action needed: Create database trade for orphaned position")
            print()
        
        # Save mismatches to file for manual review
        with open('binance_reconciliation_report.json', 'w') as f:
            json.dump({
                'timestamp': datetime.now().isoformat(),
                'exchange': 'binance',
                'mismatches': mismatches,
                'total_mismatches': len(mismatches)
            }, f, indent=2)
        
        print(f"📄 Report saved to: binance_reconciliation_report.json")
    else:
        print("✅ All balances reconciled successfully!")

if __name__ == "__main__":
    asyncio.run(main())
