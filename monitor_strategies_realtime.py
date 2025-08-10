#!/usr/bin/env python3
"""
Real-time monitoring of strategy execution across all exchanges
"""

import asyncio
import httpx
import time
from datetime import datetime
from typing import Dict, Any

# Service URLs
EXCHANGE_SERVICE_URL = "http://localhost:8003"
STRATEGY_SERVICE_URL = "http://localhost:8004"
ORCHESTRATOR_SERVICE_URL = "http://localhost:8005"

class RealTimeStrategyMonitor:
    def __init__(self):
        self.client = None
        self.last_cycle_count = 0
        self.last_trade_count = 0
    
    async def __aenter__(self):
        self.client = httpx.AsyncClient(timeout=10.0)
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.client:
            await self.client.aclose()
    
    async def get_trading_status(self) -> Dict[str, Any]:
        """Get current trading status"""
        try:
            response = await self.client.get(f"{ORCHESTRATOR_SERVICE_URL}/api/v1/trading/status")
            if response.status_code == 200:
                return response.json()
            else:
                return {'is_running': False, 'cycles_completed': 0, 'last_cycle': 'Never'}
        except Exception:
            return {'is_running': False, 'cycles_completed': 0, 'last_cycle': 'Never'}
    
    async def get_strategy_signals(self, exchange: str, pair: str) -> Dict[str, Any]:
        """Get strategy signals for a specific exchange/pair"""
        try:
            response = await self.client.get(f"{STRATEGY_SERVICE_URL}/api/v1/signals/consensus/{exchange}/{pair}")
            if response.status_code == 200:
                return response.json()
            else:
                return {'consensus_signal': 'error', 'agreement_percentage': 0, 'participating_strategies': 0}
        except Exception:
            return {'consensus_signal': 'error', 'agreement_percentage': 0, 'participating_strategies': 0}
    
    async def get_exchange_health(self, exchange: str) -> Dict[str, Any]:
        """Get health status for an exchange"""
        try:
            response = await self.client.get(f"{EXCHANGE_SERVICE_URL}/api/v1/exchanges/{exchange}/health")
            if response.status_code == 200:
                return response.json()
            else:
                return {'status': 'error', 'response_time': 0, 'error_count': 999}
        except Exception:
            return {'status': 'error', 'response_time': 0, 'error_count': 999}
    
    async def monitor_strategies(self, refresh_interval: int = 30):
        """Monitor strategies in real-time"""
        exchanges = ['binance', 'bybit', 'cryptocom']
        test_pairs = {
            'binance': 'BTCUSDC',
            'bybit': 'BTCUSDC',
            'cryptocom': 'BTCUSD'
        }
        
        print("🔍 REAL-TIME STRATEGY MONITORING")
        print("=" * 60)
        print("Press Ctrl+C to stop monitoring")
        print("=" * 60)
        
        cycle = 0
        while True:
            cycle += 1
            current_time = datetime.now().strftime("%H:%M:%S")
            
            print(f"\n🔄 CYCLE {cycle} - {current_time}")
            print("-" * 60)
            
            # Check trading status
            trading_status = await self.get_trading_status()
            is_running = trading_status.get('is_running', False)
            cycles_completed = trading_status.get('cycles_completed', 0)
            last_cycle = trading_status.get('last_cycle', 'Never')
            
            # Check for new cycles
            new_cycles = cycles_completed - self.last_cycle_count
            if new_cycles > 0:
                print(f"🎯 NEW TRADING CYCLES DETECTED: +{new_cycles}")
                self.last_cycle_count = cycles_completed
            
            print(f"📊 Trading Status: {'🟢 RUNNING' if is_running else '🔴 STOPPED'}")
            print(f"   Total Cycles: {cycles_completed}")
            print(f"   Last Cycle: {last_cycle}")
            
            # Check each exchange
            print(f"\n📈 Exchange Status:")
            for exchange in exchanges:
                health = await self.get_exchange_health(exchange)
                status = health.get('status', 'unknown')
                response_time = health.get('response_time', 0)
                error_count = health.get('error_count', 0)
                
                status_icon = "🟢" if status == 'healthy' else "🟡" if status == 'degraded' else "🔴"
                print(f"   {status_icon} {exchange.upper()}: {status} ({response_time:.3f}s, {error_count} errors)")
            
            # Check strategy signals
            print(f"\n🧠 Strategy Signals:")
            for exchange in exchanges:
                pair = test_pairs[exchange]
                signals = await self.get_strategy_signals(exchange, pair)
                
                consensus = signals.get('consensus_signal', 'unknown')
                agreement = signals.get('agreement_percentage', 0)
                participating = signals.get('participating_strategies', 0)
                
                # Signal icons
                signal_icon = "🟢" if consensus == 'buy' else "🔴" if consensus == 'sell' else "🟡"
                print(f"   {signal_icon} {exchange.upper()}: {consensus.upper()} ({agreement}% agreement, {participating} strategies)")
            
            # Check for significant changes
            if new_cycles > 0:
                print(f"\n🎉 ACTIVITY DETECTED!")
                print(f"   New trading cycles: {new_cycles}")
                print(f"   Check logs for detailed strategy execution")
            
            print(f"\n⏰ Next update in {refresh_interval} seconds...")
            print("=" * 60)
            
            await asyncio.sleep(refresh_interval)

async def main():
    """Main function"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Real-time strategy monitoring')
    parser.add_argument('--interval', '-i', type=int, default=30, 
                       help='Refresh interval in seconds (default: 30)')
    args = parser.parse_args()
    
    try:
        async with RealTimeStrategyMonitor() as monitor:
            await monitor.monitor_strategies(args.interval)
    except KeyboardInterrupt:
        print("\n\n🛑 Monitoring stopped by user")
    except Exception as e:
        print(f"\n❌ Error during monitoring: {e}")

if __name__ == "__main__":
    asyncio.run(main()) 