#!/usr/bin/env python3
"""
Cycle Analysis Tool for Trading Bot Optimization
Gathers comprehensive data on each trading cycle to analyze performance and optimize parameters
"""

import asyncio
import httpx
import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
import pandas as pd
import numpy as np
from dataclasses import dataclass, asdict
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

@dataclass
class CycleData:
    """Data structure for cycle analysis"""
    cycle_id: int
    timestamp: datetime
    cycle_type: str  # 'entry', 'exit', 'maintenance'
    duration_seconds: float
    signals_generated: int
    signals_analyzed: int
    trades_attempted: int
    trades_executed: int
    trades_rejected: int
    rejection_reasons: List[str]
    balance_available: Dict[str, float]
    open_trades_count: int
    max_trades_per_exchange: int
    min_balance_threshold: float
    min_order_size_usd: float
    momentum_filter_decisions: Dict[str, int]
    strategy_signals: Dict[str, Dict[str, Any]]
    errors: List[str]
    performance_metrics: Dict[str, float]

class CycleAnalyzer:
    def __init__(self):
        self.orchestrator_url = "http://localhost:8005"
        self.database_url = "http://localhost:8002"
        self.exchange_url = "http://localhost:8003"
        self.strategy_url = "http://localhost:8004"
        self.config_url = "http://localhost:8001"
        self.cycle_data: List[CycleData] = []
        self.last_cycle_count = 0
        
    async def get_trading_status(self) -> Dict[str, Any]:
        """Get current trading status"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.orchestrator_url}/api/v1/trading/status")
                response.raise_for_status()
                return response.json()
        except Exception as e:
            logger.error(f"Error getting trading status: {e}")
            return {}
    
    async def get_open_trades(self) -> List[Dict[str, Any]]:
        """Get all open trades"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.database_url}/api/v1/trades/open")
                response.raise_for_status()
                return response.json().get('trades', [])
        except Exception as e:
            logger.error(f"Error getting open trades: {e}")
            return []
    
    async def get_trade_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get trade history"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.database_url}/api/v1/trades?limit={limit}")
                response.raise_for_status()
                return response.json().get('trades', [])
        except Exception as e:
            logger.error(f"Error getting trade history: {e}")
            return []
    
    async def get_exchange_balances(self) -> Dict[str, Dict[str, float]]:
        """Get balances for all exchanges"""
        balances = {}
        exchanges = ['binance', 'bybit', 'cryptocom']
        
        for exchange in exchanges:
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(f"{self.exchange_url}/api/v1/account/balance/{exchange}")
                    response.raise_for_status()
                    data = response.json()
                    balances[exchange] = {
                        'total': data.get('total', {}).get('USDC', 0) or data.get('total', {}).get('USD', 0),
                        'available': data.get('free', {}).get('USDC', 0) or data.get('free', {}).get('USD', 0),
                        'used': data.get('used', {}).get('USDC', 0) or data.get('used', {}).get('USD', 0)
                    }
            except Exception as e:
                logger.error(f"Error getting balance for {exchange}: {e}")
                balances[exchange] = {'total': 0, 'available': 0, 'used': 0}
        
        return balances
    
    async def get_trading_config(self) -> Dict[str, Any]:
        """Get trading configuration"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.config_url}/api/v1/config/trading")
                response.raise_for_status()
                return response.json()
        except Exception as e:
            logger.error(f"Error getting trading config: {e}")
            return {}
    
    async def get_strategy_signals(self) -> Dict[str, Dict[str, Any]]:
        """Get current strategy signals"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.strategy_url}/api/v1/strategies/signals")
                response.raise_for_status()
                return response.json()
        except Exception as e:
            logger.error(f"Error getting strategy signals: {e}")
            return {}
    
    async def analyze_cycle(self, cycle_id: int) -> CycleData:
        """Analyze a single cycle and gather comprehensive data"""
        start_time = time.time()
        
        # Get current data
        trading_status = await self.get_trading_status()
        open_trades = await self.get_open_trades()
        balances = await self.get_exchange_balances()
        config = await self.get_trading_config()
        strategy_signals = await self.get_strategy_signals()
        
        # Calculate metrics
        duration = time.time() - start_time
        open_trades_count = len(open_trades)
        max_trades_per_exchange = config.get('max_trades_per_exchange', 20)
        min_balance_threshold = config.get('min_exchange_balance', 50.0)
        min_order_size_usd = config.get('min_order_size_usd', 50.0)
        
        # Analyze strategy signals
        signals_generated = 0
        signals_analyzed = 0
        for exchange_signals in strategy_signals.values():
            for pair_signals in exchange_signals.values():
                signals_analyzed += 1
                if pair_signals.get('signal') in ['buy', 'sell']:
                    signals_generated += 1
        
        # Create cycle data
        cycle_data = CycleData(
            cycle_id=cycle_id,
            timestamp=datetime.utcnow(),
            cycle_type='entry',  # We'll track entry cycles
            duration_seconds=duration,
            signals_generated=signals_generated,
            signals_analyzed=signals_analyzed,
            trades_attempted=0,  # Will be updated from logs
            trades_executed=0,   # Will be updated from logs
            trades_rejected=0,   # Will be updated from logs
            rejection_reasons=[],
            balance_available={k: v['available'] for k, v in balances.items()},
            open_trades_count=open_trades_count,
            max_trades_per_exchange=max_trades_per_exchange,
            min_balance_threshold=min_balance_threshold,
            min_order_size_usd=min_order_size_usd,
            momentum_filter_decisions={},
            strategy_signals=strategy_signals,
            errors=[],
            performance_metrics={
                'signal_generation_rate': signals_generated / max(signals_analyzed, 1),
                'balance_utilization': sum(v['used'] for v in balances.values()) / max(sum(v['total'] for v in balances.values()), 1),
                'trade_saturation': open_trades_count / (max_trades_per_exchange * len(balances))
            }
        )
        
        return cycle_data
    
    async def monitor_cycles(self, interval_seconds: int = 60, max_cycles: int = 100):
        """Monitor cycles continuously and gather data"""
        logger.info(f"Starting cycle monitoring (interval: {interval_seconds}s, max cycles: {max_cycles})")
        
        while len(self.cycle_data) < max_cycles:
            try:
                # Get current cycle count
                trading_status = await self.get_trading_status()
                current_cycle_count = trading_status.get('cycle_count', 0)
                
                # Check if we have new cycles
                if current_cycle_count > self.last_cycle_count:
                    new_cycles = current_cycle_count - self.last_cycle_count
                    logger.info(f"Detected {new_cycles} new cycles (total: {current_cycle_count})")
                    
                    # Analyze the latest cycle
                    cycle_data = await self.analyze_cycle(current_cycle_count)
                    self.cycle_data.append(cycle_data)
                    
                    # Log cycle summary
                    self.log_cycle_summary(cycle_data)
                    
                    self.last_cycle_count = current_cycle_count
                
                await asyncio.sleep(interval_seconds)
                
            except Exception as e:
                logger.error(f"Error in cycle monitoring: {e}")
                await asyncio.sleep(10)
    
    def log_cycle_summary(self, cycle_data: CycleData):
        """Log a summary of the cycle data"""
        logger.info(f"=== CYCLE {cycle_data.cycle_id} SUMMARY ===")
        logger.info(f"Signals: {cycle_data.signals_generated}/{cycle_data.signals_analyzed} generated")
        logger.info(f"Open Trades: {cycle_data.open_trades_count}")
        logger.info(f"Balances: {cycle_data.balance_available}")
        logger.info(f"Signal Rate: {cycle_data.performance_metrics['signal_generation_rate']:.2%}")
        logger.info(f"Balance Utilization: {cycle_data.performance_metrics['balance_utilization']:.2%}")
        logger.info(f"Trade Saturation: {cycle_data.performance_metrics['trade_saturation']:.2%}")
        logger.info("=" * 50)
    
    def generate_analysis_report(self) -> Dict[str, Any]:
        """Generate comprehensive analysis report"""
        if not self.cycle_data:
            return {"error": "No cycle data available"}
        
        df = pd.DataFrame([asdict(cycle) for cycle in self.cycle_data])
        
        # Convert timestamp strings to datetime
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        
        # Calculate statistics
        report = {
            "summary": {
                "total_cycles": len(self.cycle_data),
                "monitoring_period": str(df['timestamp'].max() - df['timestamp'].min()),
                "average_cycle_duration": df['duration_seconds'].mean(),
                "total_signals_generated": df['signals_generated'].sum(),
                "total_signals_analyzed": df['signals_analyzed'].sum(),
                "average_signal_rate": df['signals_generated'].sum() / max(df['signals_analyzed'].sum(), 1),
                "average_open_trades": df['open_trades_count'].mean(),
                "max_open_trades": df['open_trades_count'].max(),
                "min_open_trades": df['open_trades_count'].min()
            },
            "balance_analysis": {
                "average_balance_utilization": df['performance_metrics'].apply(lambda x: x['balance_utilization']).mean(),
                "average_trade_saturation": df['performance_metrics'].apply(lambda x: x['trade_saturation']).mean(),
                "balance_trends": {}
            },
            "parameter_analysis": {
                "min_balance_threshold": df['min_balance_threshold'].iloc[-1],
                "min_order_size_usd": df['min_order_size_usd'].iloc[-1],
                "max_trades_per_exchange": df['max_trades_per_exchange'].iloc[-1]
            },
            "recommendations": self.generate_recommendations(df)
        }
        
        # Add balance trends for each exchange
        for exchange in ['binance', 'bybit', 'cryptocom']:
            if exchange in df['balance_available'].iloc[0]:
                balances = df['balance_available'].apply(lambda x: x.get(exchange, 0))
                report["balance_analysis"]["balance_trends"][exchange] = {
                    "average": balances.mean(),
                    "min": balances.min(),
                    "max": balances.max(),
                    "trend": "increasing" if balances.iloc[-1] > balances.iloc[0] else "decreasing"
                }
        
        return report
    
    def generate_recommendations(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        """Generate optimization recommendations based on cycle data"""
        recommendations = []
        
        # Analyze signal generation rate
        avg_signal_rate = df['signals_generated'].sum() / max(df['signals_analyzed'].sum(), 1)
        if avg_signal_rate < 0.1:  # Less than 10% signal generation
            recommendations.append({
                "category": "Signal Generation",
                "issue": "Low signal generation rate",
                "current_value": f"{avg_signal_rate:.2%}",
                "recommendation": "Consider relaxing strategy parameters (ADX threshold, volume requirements, etc.)",
                "priority": "high"
            })
        
        # Analyze trade saturation
        avg_trade_saturation = df['performance_metrics'].apply(lambda x: x['trade_saturation']).mean()
        if avg_trade_saturation < 0.3:  # Less than 30% trade saturation
            recommendations.append({
                "category": "Trade Utilization",
                "issue": "Low trade utilization",
                "current_value": f"{avg_trade_saturation:.2%}",
                "recommendation": "Consider reducing max_trades_per_exchange or increasing position size",
                "priority": "medium"
            })
        
        # Analyze balance utilization
        avg_balance_utilization = df['performance_metrics'].apply(lambda x: x['balance_utilization']).mean()
        if avg_balance_utilization < 0.2:  # Less than 20% balance utilization
            recommendations.append({
                "category": "Balance Utilization",
                "issue": "Low balance utilization",
                "current_value": f"{avg_balance_utilization:.2%}",
                "recommendation": "Consider increasing position_size_percentage or reducing min_order_size_usd",
                "priority": "medium"
            })
        
        # Analyze minimum balance threshold
        min_balance_threshold = df['min_balance_threshold'].iloc[-1]
        if min_balance_threshold > 100:
            recommendations.append({
                "category": "Balance Threshold",
                "issue": "High minimum balance threshold",
                "current_value": f"${min_balance_threshold}",
                "recommendation": "Consider reducing min_exchange_balance to allow more trading opportunities",
                "priority": "medium"
            })
        
        return recommendations
    
    def save_analysis_report(self, filename: str = "cycle_analysis_report.json"):
        """Save analysis report to file"""
        report = self.generate_analysis_report()
        with open(filename, 'w') as f:
            json.dump(report, f, indent=2, default=str)
        logger.info(f"Analysis report saved to {filename}")
    
    def print_analysis_report(self):
        """Print analysis report to console"""
        report = self.generate_analysis_report()
        
        print("\n" + "="*80)
        print("CYCLE ANALYSIS REPORT")
        print("="*80)
        
        # Summary
        print(f"\n📊 SUMMARY:")
        print(f"   Total Cycles: {report['summary']['total_cycles']}")
        print(f"   Monitoring Period: {report['summary']['monitoring_period']}")
        print(f"   Average Cycle Duration: {report['summary']['average_cycle_duration']:.2f}s")
        print(f"   Total Signals Generated: {report['summary']['total_signals_generated']}")
        print(f"   Total Signals Analyzed: {report['summary']['total_signals_analyzed']}")
        print(f"   Average Signal Rate: {report['summary']['average_signal_rate']:.2%}")
        print(f"   Average Open Trades: {report['summary']['average_open_trades']:.1f}")
        
        # Balance Analysis
        print(f"\n💰 BALANCE ANALYSIS:")
        print(f"   Average Balance Utilization: {report['balance_analysis']['average_balance_utilization']:.2%}")
        print(f"   Average Trade Saturation: {report['balance_analysis']['average_trade_saturation']:.2%}")
        
        for exchange, data in report['balance_analysis']['balance_trends'].items():
            print(f"   {exchange.upper()}: ${data['average']:.2f} avg (${data['min']:.2f} - ${data['max']:.2f}) - {data['trend']}")
        
        # Current Parameters
        print(f"\n⚙️  CURRENT PARAMETERS:")
        print(f"   Min Balance Threshold: ${report['parameter_analysis']['min_balance_threshold']}")
        print(f"   Min Order Size USD: ${report['parameter_analysis']['min_order_size_usd']}")
        print(f"   Max Trades Per Exchange: {report['parameter_analysis']['max_trades_per_exchange']}")
        
        # Recommendations
        print(f"\n🎯 OPTIMIZATION RECOMMENDATIONS:")
        for i, rec in enumerate(report['recommendations'], 1):
            priority_icon = "🔴" if rec['priority'] == 'high' else "🟡" if rec['priority'] == 'medium' else "🟢"
            print(f"   {i}. {priority_icon} {rec['category']}: {rec['issue']}")
            print(f"      Current: {rec['current_value']}")
            print(f"      Recommendation: {rec['recommendation']}")
            print()

async def main():
    """Main function to run cycle analysis"""
    analyzer = CycleAnalyzer()
    
    print("🚀 Starting Cycle Analysis Tool")
    print("This tool will monitor trading cycles and provide optimization recommendations")
    print("Press Ctrl+C to stop and generate analysis report")
    
    try:
        # Start monitoring
        await analyzer.monitor_cycles(interval_seconds=60, max_cycles=50)
    except KeyboardInterrupt:
        print("\n⏹️  Stopping cycle monitoring...")
    
    # Generate and display report
    analyzer.print_analysis_report()
    analyzer.save_analysis_report()
    
    print("\n✅ Analysis complete! Check 'cycle_analysis_report.json' for detailed report.")

if __name__ == "__main__":
    asyncio.run(main()) 