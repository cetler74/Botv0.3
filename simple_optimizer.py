#!/usr/bin/env python3
"""
Simple Strategy Optimizer - Analyze Existing Data and Provide Recommendations
Provides optimization recommendations based on current performance data
"""

import asyncio
import httpx
import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
import pandas as pd
import numpy as np
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class SimpleOptimizer:
    def __init__(self):
        self.orchestrator_url = "http://localhost:8005"
        self.database_url = "http://localhost:8002"
        self.exchange_url = "http://localhost:8003"
        self.config_url = "http://localhost:8001"
        
    async def get_trading_data(self) -> Dict[str, Any]:
        """Get comprehensive trading data"""
        try:
            # Get trading status
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.orchestrator_url}/api/v1/trading/status")
                response.raise_for_status()
                trading_status = response.json()
            
            # Get all trades
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.database_url}/api/v1/trades?limit=1000")
                response.raise_for_status()
                all_trades = response.json().get('trades', [])
            
            # Get strategy signals
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.config_url}/api/v1/config/strategies")
                response.raise_for_status()
                strategies = response.json()
            
            # Get current config
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.config_url}/api/v1/config/trading")
                response.raise_for_status()
                trading_config = response.json()
            
            return {
                'trading_status': trading_status,
                'all_trades': all_trades,
                'strategies': strategies,
                'trading_config': trading_config
            }
        except Exception as e:
            logger.error(f"Error getting trading data: {e}")
            return {}
    
    def analyze_performance_patterns(self, trades: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Analyze trading patterns to identify optimization opportunities"""
        if not trades:
            return {}
        
        # Convert to DataFrame for analysis
        df = pd.DataFrame(trades)
        
        # Basic statistics
        total_trades = len(trades)
        closed_trades = [t for t in trades if t.get('status') == 'CLOSED']
        open_trades = [t for t in trades if t.get('status') == 'OPEN']
        
        if not closed_trades:
            return {"error": "No closed trades to analyze"}
        
        # Analyze by exchange
        exchange_stats = {}
        for trade in closed_trades:
            exchange = trade.get('exchange', 'unknown')
            if exchange not in exchange_stats:
                exchange_stats[exchange] = {'trades': [], 'pnl': 0, 'wins': 0, 'losses': 0}
            
            exchange_stats[exchange]['trades'].append(trade)
            pnl = trade.get('realized_pnl', 0)
            exchange_stats[exchange]['pnl'] += pnl
            if pnl > 0:
                exchange_stats[exchange]['wins'] += 1
            else:
                exchange_stats[exchange]['losses'] += 1
        
        # Calculate win rates by exchange
        for exchange in exchange_stats:
            total = exchange_stats[exchange]['wins'] + exchange_stats[exchange]['losses']
            exchange_stats[exchange]['win_rate'] = exchange_stats[exchange]['wins'] / max(total, 1)
            exchange_stats[exchange]['total_trades'] = total
        
        # Analyze by pair
        pair_stats = {}
        for trade in closed_trades:
            pair = trade.get('pair', 'unknown')
            if pair not in pair_stats:
                pair_stats[pair] = {'trades': [], 'pnl': 0, 'wins': 0, 'losses': 0}
            
            pair_stats[pair]['trades'].append(trade)
            pnl = trade.get('realized_pnl', 0)
            pair_stats[pair]['pnl'] += pnl
            if pnl > 0:
                pair_stats[pair]['wins'] += 1
            else:
                pair_stats[pair]['losses'] += 1
        
        # Calculate win rates by pair
        for pair in pair_stats:
            total = pair_stats[pair]['wins'] + pair_stats[pair]['losses']
            pair_stats[pair]['win_rate'] = pair_stats[pair]['wins'] / max(total, 1)
            pair_stats[pair]['total_trades'] = total
        
        # Analyze by strategy
        strategy_stats = {}
        for trade in closed_trades:
            strategy = trade.get('strategy', 'unknown')
            if strategy not in strategy_stats:
                strategy_stats[strategy] = {'trades': [], 'pnl': 0, 'wins': 0, 'losses': 0}
            
            strategy_stats[strategy]['trades'].append(trade)
            pnl = trade.get('realized_pnl', 0)
            strategy_stats[strategy]['pnl'] += pnl
            if pnl > 0:
                strategy_stats[strategy]['wins'] += 1
            else:
                strategy_stats[strategy]['losses'] += 1
        
        # Calculate win rates by strategy
        for strategy in strategy_stats:
            total = strategy_stats[strategy]['wins'] + strategy_stats[strategy]['losses']
            strategy_stats[strategy]['win_rate'] = strategy_stats[strategy]['wins'] / max(total, 1)
            strategy_stats[strategy]['total_trades'] = total
        
        return {
            'total_trades': total_trades,
            'closed_trades': len(closed_trades),
            'open_trades': len(open_trades),
            'exchange_stats': exchange_stats,
            'pair_stats': pair_stats,
            'strategy_stats': strategy_stats
        }
    
    def generate_optimization_recommendations(self, analysis: Dict[str, Any], strategies: Dict[str, Any], trading_config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Generate specific optimization recommendations"""
        recommendations = []
        
        # Analyze current performance
        exchange_stats = analysis.get('exchange_stats', {})
        pair_stats = analysis.get('pair_stats', {})
        strategy_stats = analysis.get('strategy_stats', {})
        
        # 1. Exchange-specific recommendations
        for exchange, stats in exchange_stats.items():
            win_rate = stats.get('win_rate', 0)
            total_trades = stats.get('total_trades', 0)
            
            if total_trades >= 2:  # Only recommend if we have enough data
                if win_rate < 0.5:
                    recommendations.append({
                        "category": "Exchange Performance",
                        "target": exchange.upper(),
                        "issue": f"Low win rate on {exchange}: {win_rate:.1%}",
                        "recommendation": f"Consider reducing position sizes on {exchange} or reviewing strategy parameters",
                        "priority": "high" if win_rate < 0.3 else "medium"
                    })
                elif win_rate > 0.8:
                    recommendations.append({
                        "category": "Exchange Performance",
                        "target": exchange.upper(),
                        "issue": f"Excellent win rate on {exchange}: {win_rate:.1%}",
                        "recommendation": f"Consider increasing position sizes on {exchange} to maximize profits",
                        "priority": "medium"
                    })
        
        # 2. Pair-specific recommendations
        for pair, stats in pair_stats.items():
            win_rate = stats.get('win_rate', 0)
            total_trades = stats.get('total_trades', 0)
            
            if total_trades >= 2:
                if win_rate < 0.4:
                    recommendations.append({
                        "category": "Pair Performance",
                        "target": pair,
                        "issue": f"Poor performance on {pair}: {win_rate:.1%} win rate",
                        "recommendation": f"Consider removing {pair} from trading pairs or adjusting strategy parameters",
                        "priority": "high"
                    })
                elif win_rate > 0.7:
                    recommendations.append({
                        "category": "Pair Performance",
                        "target": pair,
                        "issue": f"Strong performance on {pair}: {win_rate:.1%} win rate",
                        "recommendation": f"Consider increasing position sizes for {pair} trades",
                        "priority": "medium"
                    })
        
        # 3. Strategy-specific recommendations
        for strategy, stats in strategy_stats.items():
            win_rate = stats.get('win_rate', 0)
            total_trades = stats.get('total_trades', 0)
            
            if total_trades >= 2:
                if win_rate < 0.5:
                    recommendations.append({
                        "category": "Strategy Performance",
                        "target": strategy,
                        "issue": f"Poor performance for {strategy}: {win_rate:.1%} win rate",
                        "recommendation": f"Review and adjust {strategy} parameters or consider disabling it",
                        "priority": "high"
                    })
        
        # 4. Trading frequency recommendations
        total_trades = analysis.get('total_trades', 0)
        cycle_count = trading_config.get('cycle_count', 0) if trading_config else 0
        
        if cycle_count > 0:
            trades_per_cycle = total_trades / cycle_count
            if trades_per_cycle < 0.1:
                recommendations.append({
                    "category": "Trading Frequency",
                    "target": "Overall System",
                    "issue": f"Very low trading frequency: {trades_per_cycle:.3f} trades/cycle",
                    "recommendation": "Relax entry criteria: reduce ADX threshold, lower confidence requirements, reduce confirmation timeframes",
                    "priority": "high"
                })
        
        # 5. Position sizing recommendations
        min_order_size = trading_config.get('min_order_size_usd', 100)
        position_size_pct = trading_config.get('position_size_percentage', 0.08)
        
        if min_order_size > 50:
            recommendations.append({
                "category": "Position Sizing",
                "target": "Order Size",
                "issue": f"High minimum order size: ${min_order_size}",
                "recommendation": f"Reduce min_order_size_usd to $25-50 to allow more trading opportunities",
                "priority": "medium"
            })
        
        if position_size_pct < 0.1:
            recommendations.append({
                "category": "Position Sizing",
                "target": "Position Size",
                "issue": f"Low position size percentage: {position_size_pct:.1%}",
                "recommendation": f"Increase position_size_percentage to 0.12-0.15 for better capital utilization",
                "priority": "medium"
            })
        
        return recommendations
    
    def print_optimization_report(self, analysis: Dict[str, Any], recommendations: List[Dict[str, Any]], strategies: Dict[str, Any], trading_config: Dict[str, Any]):
        """Print comprehensive optimization report"""
        print("\n" + "="*80)
        print("SIMPLE STRATEGY OPTIMIZATION REPORT")
        print("="*80)
        
        # Performance Summary
        print(f"\n📊 PERFORMANCE SUMMARY:")
        print(f"   Total Trades: {analysis.get('total_trades', 0)}")
        print(f"   Closed Trades: {analysis.get('closed_trades', 0)}")
        print(f"   Open Trades: {analysis.get('open_trades', 0)}")
        
        # Exchange Performance
        print(f"\n🏢 EXCHANGE PERFORMANCE:")
        for exchange, stats in analysis.get('exchange_stats', {}).items():
            win_rate = stats.get('win_rate', 0)
            total_trades = stats.get('total_trades', 0)
            pnl = stats.get('pnl', 0)
            print(f"   {exchange.upper()}: {win_rate:.1%} win rate, {total_trades} trades, ${pnl:.2f} PnL")
        
        # Pair Performance
        print(f"\n📈 PAIR PERFORMANCE:")
        for pair, stats in analysis.get('pair_stats', {}).items():
            win_rate = stats.get('win_rate', 0)
            total_trades = stats.get('total_trades', 0)
            pnl = stats.get('pnl', 0)
            print(f"   {pair}: {win_rate:.1%} win rate, {total_trades} trades, ${pnl:.2f} PnL")
        
        # Strategy Performance
        print(f"\n🧠 STRATEGY PERFORMANCE:")
        for strategy, stats in analysis.get('strategy_stats', {}).items():
            win_rate = stats.get('win_rate', 0)
            total_trades = stats.get('total_trades', 0)
            pnl = stats.get('pnl', 0)
            print(f"   {strategy}: {win_rate:.1%} win rate, {total_trades} trades, ${pnl:.2f} PnL")
        
        # Current Configuration
        print(f"\n⚙️  CURRENT CONFIGURATION:")
        print(f"   Min Order Size: ${trading_config.get('min_order_size_usd', 'N/A')}")
        print(f"   Position Size %: {trading_config.get('position_size_percentage', 'N/A')}")
        print(f"   Max Trades Per Exchange: {trading_config.get('max_trades_per_exchange', 'N/A')}")
        print(f"   Min Exchange Balance: ${trading_config.get('min_exchange_balance', 'N/A')}")
        
        # Recommendations
        print(f"\n🎯 OPTIMIZATION RECOMMENDATIONS:")
        if not recommendations:
            print("   ✅ No optimization recommendations - current settings appear optimal!")
        else:
            for i, rec in enumerate(recommendations, 1):
                priority_icon = "🔴" if rec['priority'] == 'high' else "🟡" if rec['priority'] == 'medium' else "🟢"
                print(f"   {i}. {priority_icon} {rec['category']} - {rec['target']}")
                print(f"      Issue: {rec['issue']}")
                print(f"      Recommendation: {rec['recommendation']}")
                print()
        
        print("="*80)
    
    async def run_optimization_analysis(self):
        """Run complete optimization analysis"""
        print("🔍 Simple Strategy Optimizer - Analyzing Current Performance")
        
        # Get data
        data = await self.get_trading_data()
        if not data:
            print("❌ Error: Could not retrieve trading data")
            return
        
        # Analyze performance
        analysis = self.analyze_performance_patterns(data.get('all_trades', []))
        if "error" in analysis:
            print(f"❌ Error: {analysis['error']}")
            return
        
        # Generate recommendations
        recommendations = self.generate_optimization_recommendations(
            analysis, 
            data.get('strategies', {}), 
            data.get('trading_config', {})
        )
        
        # Print report
        self.print_optimization_report(
            analysis, 
            recommendations, 
            data.get('strategies', {}), 
            data.get('trading_config', {})
        )
        
        # Save report
        report = {
            "analysis": analysis,
            "recommendations": recommendations,
            "timestamp": datetime.now().isoformat()
        }
        
        with open("simple_optimization_report.json", "w") as f:
            json.dump(report, f, indent=2, default=str)
        
        print("\n💾 Report saved to 'simple_optimization_report.json'")

async def main():
    """Main function"""
    optimizer = SimpleOptimizer()
    await optimizer.run_optimization_analysis()

if __name__ == "__main__":
    asyncio.run(main()) 