#!/usr/bin/env python3
"""
Trading control and strategy monitoring tool
"""

import asyncio
import httpx
import argparse
from datetime import datetime

# Service URLs
ORCHESTRATOR_SERVICE_URL = "http://localhost:8005"
STRATEGY_SERVICE_URL = "http://localhost:8004"
EXCHANGE_SERVICE_URL = "http://localhost:8003"

class TradingControl:
    def __init__(self):
        self.client = None
    
    async def __aenter__(self):
        self.client = httpx.AsyncClient(timeout=10.0)
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.client:
            await self.client.aclose()
    
    async def start_trading(self):
        """Start trading"""
        try:
            response = await self.client.post(f"{ORCHESTRATOR_SERVICE_URL}/api/v1/trading/start")
            if response.status_code == 200:
                print("✅ Trading started successfully")
                return True
            else:
                print(f"❌ Failed to start trading: {response.status_code}")
                return False
        except Exception as e:
            print(f"❌ Error starting trading: {e}")
            return False
    
    async def stop_trading(self):
        """Stop trading"""
        try:
            response = await self.client.post(f"{ORCHESTRATOR_SERVICE_URL}/api/v1/trading/stop")
            if response.status_code == 200:
                print("✅ Trading stopped successfully")
                return True
            else:
                print(f"❌ Failed to stop trading: {response.status_code}")
                return False
        except Exception as e:
            print(f"❌ Error stopping trading: {e}")
            return False
    
    async def get_status(self):
        """Get trading status"""
        try:
            response = await self.client.get(f"{ORCHESTRATOR_SERVICE_URL}/api/v1/trading/status")
            if response.status_code == 200:
                status = response.json()
                is_running = status.get('is_running', False)
                cycles_completed = status.get('cycles_completed', 0)
                last_cycle = status.get('last_cycle', 'Never')
                
                print("📊 Trading Status:")
                print(f"   Status: {'🟢 RUNNING' if is_running else '🔴 STOPPED'}")
                print(f"   Cycles Completed: {cycles_completed}")
                print(f"   Last Cycle: {last_cycle}")
                return status
            else:
                print(f"❌ Failed to get status: {response.status_code}")
                return None
        except Exception as e:
            print(f"❌ Error getting status: {e}")
            return None
    
    async def check_strategies(self):
        """Check strategy execution for all exchanges"""
        exchanges = ['binance', 'bybit', 'cryptocom']
        test_pairs = {
            'binance': 'BTCUSDC',
            'bybit': 'BTCUSDC',
            'cryptocom': 'BTCUSD'
        }
        
        print("🧠 Strategy Execution Status:")
        print("-" * 50)
        
        for exchange in exchanges:
            pair = test_pairs[exchange]
            try:
                response = await self.client.get(f"{STRATEGY_SERVICE_URL}/api/v1/signals/consensus/{exchange}/{pair}")
                if response.status_code == 200:
                    data = response.json()
                    consensus = data.get('consensus_signal', 'unknown')
                    agreement = data.get('agreement_percentage', 0)
                    participating = data.get('participating_strategies', 0)
                    
                    signal_icon = "🟢" if consensus == 'buy' else "🔴" if consensus == 'sell' else "🟡"
                    print(f"   {signal_icon} {exchange.upper()}: {consensus.upper()} ({agreement}% agreement, {participating} strategies)")
                else:
                    print(f"   ❌ {exchange.upper()}: Failed to get signals ({response.status_code})")
            except Exception as e:
                print(f"   ❌ {exchange.upper()}: Error - {e}")
    
    async def check_exchanges(self):
        """Check exchange health"""
        exchanges = ['binance', 'bybit', 'cryptocom']
        
        print("📈 Exchange Health:")
        print("-" * 50)
        
        for exchange in exchanges:
            try:
                response = await self.client.get(f"{EXCHANGE_SERVICE_URL}/api/v1/exchanges/{exchange}/health")
                if response.status_code == 200:
                    health = response.json()
                    status = health.get('status', 'unknown')
                    response_time = health.get('response_time', 0)
                    error_count = health.get('error_count', 0)
                    
                    status_icon = "🟢" if status == 'healthy' else "🟡" if status == 'degraded' else "🔴"
                    print(f"   {status_icon} {exchange.upper()}: {status} ({response_time:.3f}s, {error_count} errors)")
                else:
                    print(f"   ❌ {exchange.upper()}: Failed to get health ({response.status_code})")
            except Exception as e:
                print(f"   ❌ {exchange.upper()}: Error - {e}")
    
    async def show_logs(self, service: str, lines: int = 20):
        """Show recent logs for a service"""
        import subprocess
        
        service_map = {
            'orchestrator': 'trading-bot-orchestrator',
            'strategy': 'trading-bot-strategy',
            'exchange': 'trading-bot-exchange',
            'database': 'trading-bot-database',
            'config': 'trading-bot-config'
        }
        
        container_name = service_map.get(service.lower())
        if not container_name:
            print(f"❌ Unknown service: {service}")
            print(f"Available services: {', '.join(service_map.keys())}")
            return
        
        try:
            result = subprocess.run(
                ['docker', 'logs', container_name, '--tail', str(lines)],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                print(f"📋 Recent logs for {service}:")
                print("-" * 50)
                print(result.stdout)
            else:
                print(f"❌ Failed to get logs for {service}: {result.stderr}")
        except Exception as e:
            print(f"❌ Error getting logs: {e}")

async def main():
    """Main function"""
    parser = argparse.ArgumentParser(description='Trading control and monitoring')
    parser.add_argument('command', choices=['start', 'stop', 'status', 'strategies', 'exchanges', 'logs'],
                       help='Command to execute')
    parser.add_argument('--service', '-s', default='orchestrator',
                       help='Service for logs command (default: orchestrator)')
    parser.add_argument('--lines', '-n', type=int, default=20,
                       help='Number of log lines to show (default: 20)')
    
    args = parser.parse_args()
    
    async with TradingControl() as control:
        if args.command == 'start':
            await control.start_trading()
        elif args.command == 'stop':
            await control.stop_trading()
        elif args.command == 'status':
            await control.get_status()
        elif args.command == 'strategies':
            await control.check_strategies()
        elif args.command == 'exchanges':
            await control.check_exchanges()
        elif args.command == 'logs':
            await control.show_logs(args.service, args.lines)

if __name__ == "__main__":
    asyncio.run(main()) 