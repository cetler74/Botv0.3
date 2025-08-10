#!/usr/bin/env python3
"""
Real-time Trading Bot Monitor
Shows comprehensive view of trading progress including balances, pairs, strategies, and trading activity
"""

import asyncio
import httpx
import json
from datetime import datetime
import time
import os
import sys

# Service URLs
CONFIG_SERVICE_URL = "http://localhost:8001"
DATABASE_SERVICE_URL = "http://localhost:8002"
EXCHANGE_SERVICE_URL = "http://localhost:8003"
STRATEGY_SERVICE_URL = "http://localhost:8004"
ORCHESTRATOR_SERVICE_URL = "http://localhost:8005"

class TradingMonitor:
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=10.0)
        
    async def get_balances(self):
        """Get current balances for all exchanges"""
        try:
            response = await self.client.get(f"{DATABASE_SERVICE_URL}/api/v1/balances")
            if response.status_code == 200:
                data = response.json()
                return data.get('balances', [])
            return []
        except Exception as e:
            return f"Error: {e}"
    
    async def get_pairs(self):
        """Get trading pairs for all exchanges"""
        pairs = {}
        exchanges = ['binance', 'bybit', 'cryptocom']
        
        for exchange in exchanges:
            try:
                response = await self.client.get(f"{DATABASE_SERVICE_URL}/api/v1/pairs/{exchange}")
                if response.status_code == 200:
                    data = response.json()
                    pairs[exchange] = data.get('pairs', [])
                else:
                    pairs[exchange] = []
            except Exception as e:
                pairs[exchange] = f"Error: {e}"
        
        return pairs
    
    async def get_trading_status(self):
        """Get current trading status"""
        try:
            response = await self.client.get(f"{ORCHESTRATOR_SERVICE_URL}/api/v1/trading/status")
            if response.status_code == 200:
                return response.json()
            return None
        except Exception as e:
            return f"Error: {e}"
    
    async def get_active_trades(self):
        """Get active trades"""
        try:
            response = await self.client.get(f"{DATABASE_SERVICE_URL}/api/v1/trades/open")
            if response.status_code == 200:
                return response.json()
            return []
        except Exception as e:
            return f"Error: {e}"
    
    async def get_strategy_signals(self, exchange, pair):
        """Get strategy signals for a specific pair"""
        try:
            # Convert pair format
            strategy_pair = pair.replace('/', '')
            response = await self.client.get(f"{STRATEGY_SERVICE_URL}/api/v1/signals/consensus/{exchange}/{strategy_pair}")
            if response.status_code == 200:
                return response.json()
            return None
        except Exception as e:
            return f"Error: {e}"
    
    async def get_exchange_health(self):
        """Get exchange health status"""
        health = {}
        exchanges = ['binance', 'bybit', 'cryptocom']
        
        for exchange in exchanges:
            try:
                response = await self.client.get(f"{EXCHANGE_SERVICE_URL}/api/v1/exchanges/{exchange}/health")
                if response.status_code == 200:
                    health[exchange] = response.json()
                else:
                    health[exchange] = {"status": "unknown"}
            except Exception as e:
                health[exchange] = {"status": f"Error: {e}"}
        
        return health
    
    def print_header(self):
        """Print monitoring header"""
        print("=" * 80)
        print(f"🤖 TRADING BOT MONITOR - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 80)
    
    def print_balances(self, balances):
        """Print balance information"""
        print("\n💰 BALANCES:")
        print("-" * 40)
        
        if isinstance(balances, list) and balances:
            total_balance = 0
            for balance in balances:
                exchange = balance.get('exchange', 'unknown')
                total = balance.get('balance', 0)
                available = balance.get('available_balance', 0)
                total_balance += available
                print(f"  {exchange.upper():12} | Total: ${total:>10,.2f} | Available: ${available:>10,.2f}")
            print(f"  {'TOTAL':12} | Available: ${total_balance:>10,.2f}")
        else:
            print("  No balance data available")
    
    def print_pairs(self, pairs):
        """Print trading pairs"""
        print("\n📊 TRADING PAIRS:")
        print("-" * 40)
        
        for exchange, pair_list in pairs.items():
            if isinstance(pair_list, list):
                print(f"  {exchange.upper():12} | {len(pair_list)} pairs: {', '.join(pair_list[:5])}")
                if len(pair_list) > 5:
                    print(f"  {'':12} | ... and {len(pair_list) - 5} more")
            else:
                print(f"  {exchange.upper():12} | {pair_list}")
    
    def print_trading_status(self, status):
        """Print trading status"""
        print("\n🔄 TRADING STATUS:")
        print("-" * 40)
        
        if status and isinstance(status, dict):
            print(f"  Status:        {status.get('status', 'unknown').upper()}")
            print(f"  Cycle Count:   {status.get('cycle_count', 0)}")
            print(f"  Active Trades: {status.get('active_trades', 0)}")
            print(f"  Total PnL:     ${status.get('total_pnl', 0):>10,.2f}")
            if status.get('last_cycle'):
                print(f"  Last Cycle:    {status.get('last_cycle', 'unknown')}")
        else:
            print("  Trading status unavailable")
    
    def print_active_trades(self, trades):
        """Print active trades"""
        print("\n📈 ACTIVE TRADES:")
        print("-" * 40)
        
        if isinstance(trades, list) and trades:
            for trade in trades:
                trade_id = trade.get('trade_id', 'unknown')[:8]
                pair = trade.get('pair', 'unknown')
                exchange = trade.get('exchange', 'unknown')
                entry_price = trade.get('entry_price', 0)
                position_size = trade.get('position_size', 0)
                entry_time = trade.get('entry_time', 'unknown')
                
                print(f"  {trade_id} | {pair:>10} | {exchange:>10} | ${entry_price:>10,.2f} | ${position_size:>10,.2f}")
        else:
            print("  No active trades")
    
    def print_exchange_health(self, health):
        """Print exchange health"""
        print("\n🏥 EXCHANGE HEALTH:")
        print("-" * 40)
        
        for exchange, status in health.items():
            if isinstance(status, dict):
                health_status = status.get('status', 'unknown')
                response_time = status.get('response_time', 0)
                print(f"  {exchange.upper():12} | {health_status:>10} | {response_time:>8.2f}s")
            else:
                print(f"  {exchange.upper():12} | {status}")
    
    async def print_strategy_signals(self, pairs):
        """Print strategy signals for sample pairs"""
        print("\n🎯 STRATEGY SIGNALS (Sample):")
        print("-" * 40)
        
        # Check signals for first pair of each exchange
        for exchange, pair_list in pairs.items():
            if isinstance(pair_list, list) and pair_list:
                sample_pair = pair_list[0]
                signal = await self.get_strategy_signals(exchange, sample_pair)
                
                if signal and isinstance(signal, dict):
                    consensus = signal.get('consensus_signal', 'unknown')
                    agreement = signal.get('agreement_percentage', 0)
                    participating = signal.get('participating_strategies', 0)
                    print(f"  {exchange.upper():12} | {sample_pair:>10} | {consensus:>6} | {agreement:>6.1f}% | {participating} strategies")
                else:
                    print(f"  {exchange.upper():12} | {sample_pair:>10} | {signal}")
    
    async def monitor_once(self):
        """Run one monitoring cycle"""
        self.print_header()
        
        # Gather all data
        balances = await self.get_balances()
        pairs = await self.get_pairs()
        trading_status = await self.get_trading_status()
        active_trades = await self.get_active_trades()
        exchange_health = await self.get_exchange_health()
        
        # Print all sections
        self.print_balances(balances)
        self.print_pairs(pairs)
        self.print_trading_status(trading_status)
        self.print_active_trades(active_trades)
        self.print_exchange_health(exchange_health)
        await self.print_strategy_signals(pairs)
        
        print("\n" + "=" * 80)
    
    async def monitor_continuous(self, interval=30):
        """Monitor continuously with specified interval"""
        print(f"Starting continuous monitoring (refresh every {interval} seconds)")
        print("Press Ctrl+C to stop")
        
        try:
            while True:
                await self.monitor_once()
                await asyncio.sleep(interval)
        except KeyboardInterrupt:
            print("\nMonitoring stopped by user")
        finally:
            await self.client.aclose()

async def main():
    monitor = TradingMonitor()
    
    if len(sys.argv) > 1 and sys.argv[1] == "--continuous":
        interval = int(sys.argv[2]) if len(sys.argv) > 2 else 30
        await monitor.monitor_continuous(interval)
    else:
        await monitor.monitor_once()
        await monitor.client.aclose()

if __name__ == "__main__":
    asyncio.run(main()) 