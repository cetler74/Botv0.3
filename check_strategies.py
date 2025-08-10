#!/usr/bin/env python3
"""
Check if strategies are running for all exchanges
"""

import asyncio
import httpx
import json
import time
from datetime import datetime
from typing import Dict, List, Any, Optional

# Service URLs
EXCHANGE_SERVICE_URL = "http://localhost:8003"
STRATEGY_SERVICE_URL = "http://localhost:8004"
ORCHESTRATOR_SERVICE_URL = "http://localhost:8005"
DATABASE_SERVICE_URL = "http://localhost:8002"
CONFIG_SERVICE_URL = "http://localhost:8001"

class StrategyMonitor:
    def __init__(self):
        self.client: Optional[httpx.AsyncClient] = None
    
    async def __aenter__(self):
        self.client = httpx.AsyncClient(timeout=30.0)
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.client:
            await self.client.aclose()
    
    async def check_exchange_health(self) -> Dict[str, Any]:
        """Check health of all exchanges"""
        print("=== Exchange Health Check ===")
        exchanges = ['binance', 'bybit', 'cryptocom']
        health_status = {}
        
        for exchange in exchanges:
            try:
                response = await self.client.get(f"{EXCHANGE_SERVICE_URL}/api/v1/exchanges/{exchange}/health")
                if response.status_code == 200:
                    health = response.json()
                    status = health.get('status', 'unknown')
                    response_time = health.get('response_time', 0)
                    error_count = health.get('error_count', 0)
                    
                    print(f"  {exchange.upper()}: {status} (Response: {response_time:.3f}s, Errors: {error_count})")
                    health_status[exchange] = {
                        'status': status,
                        'response_time': response_time,
                        'error_count': error_count
                    }
                else:
                    print(f"  {exchange.upper()}: Failed to get health ({response.status_code})")
                    health_status[exchange] = {'status': 'error', 'response_time': 0, 'error_count': 999}
            except Exception as e:
                print(f"  {exchange.upper()}: Error checking health - {e}")
                health_status[exchange] = {'status': 'error', 'response_time': 0, 'error_count': 999}
        
        return health_status
    
    async def check_strategy_service_status(self) -> bool:
        """Check if strategy service is running and healthy"""
        print("\n=== Strategy Service Status ===")
        try:
            response = await self.client.get(f"{STRATEGY_SERVICE_URL}/health")
            if response.status_code == 200:
                health = response.json()
                print(f"  Status: {health.get('status', 'unknown')}")
                print(f"  Version: {health.get('version', 'unknown')}")
                print(f"  Timestamp: {health.get('timestamp', 'unknown')}")
                return True
            else:
                print(f"  Strategy service unhealthy: {response.status_code}")
                return False
        except Exception as e:
            print(f"  Strategy service error: {e}")
            return False
    
    async def check_orchestrator_status(self) -> bool:
        """Check if orchestrator service is running"""
        print("\n=== Orchestrator Service Status ===")
        try:
            response = await self.client.get(f"{ORCHESTRATOR_SERVICE_URL}/health")
            if response.status_code == 200:
                health = response.json()
                print(f"  Status: {health.get('status', 'unknown')}")
                print(f"  Version: {health.get('version', 'unknown')}")
                return True
            else:
                print(f"  Orchestrator unhealthy: {response.status_code}")
                return False
        except Exception as e:
            print(f"  Orchestrator error: {e}")
            return False
    
    async def check_strategy_signals_for_all_exchanges(self) -> Dict[str, Any]:
        """Check strategy signals for all exchanges"""
        print("\n=== Strategy Signals Check ===")
        exchanges = ['binance', 'bybit', 'cryptocom']
        test_pairs = {
            'binance': 'BTCUSDC',
            'bybit': 'BTCUSDC',
            'cryptocom': 'BTCUSD'
        }
        
        signals_status = {}
        
        for exchange in exchanges:
            pair = test_pairs[exchange]
            print(f"\n  Testing {exchange.upper()} - {pair}:")
            
            try:
                # Check consensus signals
                response = await self.client.get(f"{STRATEGY_SERVICE_URL}/api/v1/signals/consensus/{exchange}/{pair}")
                
                if response.status_code == 200:
                    data = response.json()
                    consensus = data.get('consensus_signal', 'unknown')
                    agreement = data.get('agreement_percentage', 0)
                    strategies = data.get('participating_strategies', 0)
                    
                    print(f"    ✓ Consensus: {consensus}")
                    print(f"    ✓ Agreement: {agreement}%")
                    print(f"    ✓ Participating strategies: {strategies}")
                    
                    # Show individual strategy signals
                    signals = data.get('signals', [])
                    if signals:
                        print(f"    Strategy details:")
                        for signal in signals:
                            strategy_name = signal.get('strategy_name', 'unknown')
                            signal_type = signal.get('signal', 'unknown')
                            confidence = signal.get('confidence', 0)
                            print(f"      - {strategy_name}: {signal_type} (confidence: {confidence})")
                    
                    signals_status[exchange] = {
                        'status': 'working',
                        'consensus': consensus,
                        'agreement': agreement,
                        'participating_strategies': strategies,
                        'signals_count': len(signals)
                    }
                else:
                    print(f"    ✗ Failed: {response.status_code}")
                    if response.status_code == 404:
                        error_detail = response.json().get('detail', 'Unknown error')
                        print(f"    Error: {error_detail}")
                    signals_status[exchange] = {'status': 'failed', 'error': response.status_code}
                    
            except Exception as e:
                print(f"    ✗ Error: {e}")
                signals_status[exchange] = {'status': 'error', 'error': str(e)}
        
        return signals_status
    
    async def check_trading_activity(self) -> Dict[str, Any]:
        """Check if trading activity is happening"""
        print("\n=== Trading Activity Check ===")
        
        try:
            # Check orchestrator trading status
            response = await self.client.get(f"{ORCHESTRATOR_SERVICE_URL}/api/v1/trading/status")
            if response.status_code == 200:
                status = response.json()
                is_running = status.get('is_running', False)
                last_cycle = status.get('last_cycle', 'Never')
                cycles_completed = status.get('cycles_completed', 0)
                
                print(f"  Trading Status: {'Running' if is_running else 'Stopped'}")
                print(f"  Last Cycle: {last_cycle}")
                print(f"  Cycles Completed: {cycles_completed}")
                
                return {
                    'is_running': is_running,
                    'last_cycle': last_cycle,
                    'cycles_completed': cycles_completed
                }
            else:
                print(f"  Failed to get trading status: {response.status_code}")
                return {'is_running': False, 'error': response.status_code}
                
        except Exception as e:
            print(f"  Error checking trading activity: {e}")
            return {'is_running': False, 'error': str(e)}
    
    async def check_active_trades(self) -> Dict[str, Any]:
        """Check for active trades across all exchanges"""
        print("\n=== Active Trades Check ===")
        exchanges = ['binance', 'bybit', 'cryptocom']
        active_trades = {}
        
        for exchange in exchanges:
            try:
                response = await self.client.get(f"{ORCHESTRATOR_SERVICE_URL}/api/v1/trading/trades/open/{exchange}")
                if response.status_code == 200:
                    trades = response.json()
                    trade_count = len(trades)
                    print(f"  {exchange.upper()}: {trade_count} active trades")
                    
                    if trade_count > 0:
                        print(f"    Recent trades:")
                        for trade in trades[:3]:  # Show first 3 trades
                            symbol = trade.get('symbol', 'unknown')
                            side = trade.get('side', 'unknown')
                            amount = trade.get('amount', 0)
                            entry_price = trade.get('entry_price', 0)
                            print(f"      - {symbol} {side} {amount} @ {entry_price}")
                    
                    active_trades[exchange] = trade_count
                else:
                    print(f"  {exchange.upper()}: Failed to get trades ({response.status_code})")
                    active_trades[exchange] = 0
                    
            except Exception as e:
                print(f"  {exchange.upper()}: Error - {e}")
                active_trades[exchange] = 0
        
        return active_trades
    
    async def check_strategy_logs(self) -> None:
        """Check recent strategy service logs"""
        print("\n=== Recent Strategy Activity ===")
        try:
            # Check if there are any recent strategy executions
            response = await self.client.get(f"{STRATEGY_SERVICE_URL}/api/v1/strategies/status")
            if response.status_code == 200:
                status = response.json()
                active_strategies = status.get('active_strategies', [])
                total_strategies = status.get('total_strategies', 0)
                
                print(f"  Active Strategies: {len(active_strategies)}/{total_strategies}")
                if active_strategies:
                    print(f"  Strategy List:")
                    for strategy in active_strategies:
                        print(f"    - {strategy}")
                else:
                    print(f"  No active strategies found")
            else:
                print(f"  Failed to get strategy status: {response.status_code}")
                
        except Exception as e:
            print(f"  Error checking strategy logs: {e}")
    
    async def run_comprehensive_check(self) -> Dict[str, Any]:
        """Run comprehensive strategy check"""
        print("🔍 COMPREHENSIVE STRATEGY MONITORING")
        print("=" * 50)
        
        results = {}
        
        # Check exchange health
        results['exchange_health'] = await self.check_exchange_health()
        
        # Check service status
        results['strategy_service_ok'] = await self.check_strategy_service_status()
        results['orchestrator_ok'] = await self.check_orchestrator_status()
        
        # Check strategy signals
        results['strategy_signals'] = await self.check_strategy_signals_for_all_exchanges()
        
        # Check trading activity
        results['trading_activity'] = await self.check_trading_activity()
        
        # Check active trades
        results['active_trades'] = await self.check_active_trades()
        
        # Check strategy logs
        await self.check_strategy_logs()
        
        # Summary
        print("\n" + "=" * 50)
        print("📊 SUMMARY")
        print("=" * 50)
        
        # Exchange health summary
        healthy_exchanges = sum(1 for h in results['exchange_health'].values() if h.get('status') == 'healthy')
        total_exchanges = len(results['exchange_health'])
        print(f"Exchanges: {healthy_exchanges}/{total_exchanges} healthy")
        
        # Strategy signals summary
        working_strategies = sum(1 for s in results['strategy_signals'].values() if s.get('status') == 'working')
        total_strategies = len(results['strategy_signals'])
        print(f"Strategy Signals: {working_strategies}/{total_strategies} working")
        
        # Trading activity summary
        trading_status = results['trading_activity']
        if trading_status.get('is_running'):
            print(f"Trading: ✅ ACTIVE (Cycles: {trading_status.get('cycles_completed', 0)})")
        else:
            print(f"Trading: ❌ INACTIVE")
        
        # Active trades summary
        total_active_trades = sum(results['active_trades'].values())
        print(f"Active Trades: {total_active_trades} across all exchanges")
        
        return results

async def main():
    """Main function"""
    async with StrategyMonitor() as monitor:
        results = await monitor.run_comprehensive_check()
        
        # Return results for potential further processing
        return results

if __name__ == "__main__":
    asyncio.run(main()) 