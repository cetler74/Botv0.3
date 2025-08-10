#!/usr/bin/env python3

import asyncio
import httpx

async def main():
    async with httpx.AsyncClient() as client:
        # Get trade performance
        response = await client.get('http://localhost:8002/api/v1/trades/performance')
        if response.status_code == 200:
            data = response.json()
            print('TRADE PERFORMANCE BY EXCHANGE:')
            print('-' * 60)
            for exchange, stats in data.get('by_exchange', {}).items():
                total_trades = stats.get('total_trades', 0)
                win_rate = stats.get('win_rate', 0) * 100
                total_pnl = stats.get('total_pnl', 0)
                success_rate = stats.get('success_rate', 0) * 100
                print(f'{exchange.upper():12} | Trades: {total_trades:3} | Win Rate: {win_rate:5.1f}% | Success Rate: {success_rate:5.1f}% | PnL: ${total_pnl:8.2f}')
        
        # Get recent trades
        print('\n\nRECENT TRADES:')
        print('-' * 80)
        response = await client.get('http://localhost:8002/api/v1/trades/recent?limit=20')
        if response.status_code == 200:
            trades = response.json().get('trades', [])
            for trade in trades:
                trade_id = trade.get('trade_id', '')[:8]
                exchange = trade.get('exchange', '')
                pair = trade.get('pair', '')
                status = trade.get('status', '')
                pnl = trade.get('pnl', 0)
                entry_time = trade.get('entry_time', '')
                print(f'{trade_id} | {exchange:>10} | {pair:>12} | {status:>10} | ${pnl:>8.2f} | {entry_time}')

asyncio.run(main())