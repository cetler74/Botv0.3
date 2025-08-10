#!/usr/bin/env python3
"""
Quick Cycle Analysis - Immediate Analysis of Current Trading State
Provides instant recommendations based on current data without waiting for new cycles
"""

import asyncio
import httpx
import json
from datetime import datetime
from typing import Dict, List, Any
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class QuickCycleAnalyzer:
    def __init__(self):
        self.orchestrator_url = "http://localhost:8005"
        self.database_url = "http://localhost:8002"
        self.exchange_url = "http://localhost:8003"
        self.strategy_url = "http://localhost:8004"
        self.config_url = "http://localhost:8001"
    
    async def get_current_state(self) -> Dict[str, Any]:
        """Get current trading state"""
        try:
            # Get trading status
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.orchestrator_url}/api/v1/trading/status")
                response.raise_for_status()
                trading_status = response.json()
            
            # Get open trades
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.database_url}/api/v1/trades/open")
                response.raise_for_status()
                open_trades = response.json().get('trades', [])
            
            # Get trade history
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.database_url}/api/v1/trades?limit=50")
                response.raise_for_status()
                trade_history = response.json().get('trades', [])
            
            # Get balances
            balances = {}
            for exchange in ['binance', 'bybit', 'cryptocom']:
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
            
            # Get trading config
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.config_url}/api/v1/config/trading")
                response.raise_for_status()
                config = response.json()
            
            return {
                'trading_status': trading_status,
                'open_trades': open_trades,
                'trade_history': trade_history,
                'balances': balances,
                'config': config
            }
        except Exception as e:
            logger.error(f"Error getting current state: {e}")
            return {}
    
    def analyze_performance(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze current performance and generate recommendations"""
        if not data:
            return {"error": "No data available"}
        
        trading_status = data.get('trading_status', {})
        open_trades = data.get('open_trades', [])
        trade_history = data.get('trade_history', [])
        balances = data.get('balances', {})
        config = data.get('config', {})
        
        # Calculate metrics
        cycle_count = trading_status.get('cycle_count', 0)
        total_trades = len(trade_history)
        open_trades_count = len(open_trades)
        closed_trades = [t for t in trade_history if t.get('status') == 'CLOSED']
        closed_trades_count = len(closed_trades)
        
        # Calculate PnL
        total_pnl = sum(t.get('realized_pnl', 0) for t in closed_trades)
        profitable_trades = [t for t in closed_trades if t.get('realized_pnl', 0) > 0]
        win_rate = len(profitable_trades) / max(closed_trades_count, 1)
        
        # Calculate balance utilization
        total_balance = sum(b['total'] for b in balances.values())
        used_balance = sum(b['used'] for b in balances.values())
        balance_utilization = used_balance / max(total_balance, 1)
        
        # Get current parameters
        max_trades_per_exchange = config.get('max_trades_per_exchange', 20)
        min_exchange_balance = config.get('min_exchange_balance', 50.0)
        min_order_size_usd = config.get('min_order_size_usd', 50.0)
        position_size_percentage = config.get('position_size_percentage', 0.08)
        
        # Calculate trade efficiency
        trades_per_cycle = total_trades / max(cycle_count, 1)
        trade_saturation = open_trades_count / (max_trades_per_exchange * len(balances))
        
        # Generate recommendations
        recommendations = []
        
        # Low trade generation
        if trades_per_cycle < 0.1:  # Less than 1 trade per 10 cycles
            recommendations.append({
                "category": "Trade Generation",
                "issue": "Very low trade generation rate",
                "current_value": f"{trades_per_cycle:.3f} trades/cycle",
                "recommendation": "Consider relaxing strategy parameters (ADX threshold, volume requirements, RSI thresholds)",
                "priority": "high"
            })
        
        # Low balance utilization
        if balance_utilization < 0.2:  # Less than 20% balance utilization
            recommendations.append({
                "category": "Balance Utilization",
                "issue": "Low balance utilization",
                "current_value": f"{balance_utilization:.1%}",
                "recommendation": "Consider increasing position_size_percentage or reducing min_order_size_usd",
                "priority": "medium"
            })
        
        # High minimum balance threshold
        if min_exchange_balance > 100:
            recommendations.append({
                "category": "Balance Threshold",
                "issue": "High minimum balance threshold",
                "current_value": f"${min_exchange_balance}",
                "recommendation": "Consider reducing min_exchange_balance to allow more trading opportunities",
                "priority": "medium"
            })
        
        # Low trade saturation
        if trade_saturation < 0.3:
            recommendations.append({
                "category": "Trade Saturation",
                "issue": "Low trade saturation",
                "current_value": f"{trade_saturation:.1%}",
                "recommendation": "Consider reducing max_trades_per_exchange or increasing position size",
                "priority": "medium"
            })
        
        # High minimum order size
        if min_order_size_usd > 50:
            recommendations.append({
                "category": "Order Size",
                "issue": "High minimum order size",
                "current_value": f"${min_order_size_usd}",
                "recommendation": "Consider reducing min_order_size_usd to allow smaller trades",
                "priority": "medium"
            })
        
        return {
            "summary": {
                "cycle_count": cycle_count,
                "total_trades": total_trades,
                "open_trades": open_trades_count,
                "closed_trades": closed_trades_count,
                "trades_per_cycle": trades_per_cycle,
                "total_pnl": total_pnl,
                "win_rate": win_rate,
                "balance_utilization": balance_utilization,
                "trade_saturation": trade_saturation
            },
            "current_parameters": {
                "max_trades_per_exchange": max_trades_per_exchange,
                "min_exchange_balance": min_exchange_balance,
                "min_order_size_usd": min_order_size_usd,
                "position_size_percentage": position_size_percentage
            },
            "balances": balances,
            "recommendations": recommendations
        }
    
    def print_analysis(self, analysis: Dict[str, Any]):
        """Print analysis results"""
        if "error" in analysis:
            print(f"❌ Error: {analysis['error']}")
            return
        
        summary = analysis['summary']
        params = analysis['current_parameters']
        balances = analysis['balances']
        recommendations = analysis['recommendations']
        
        print("\n" + "="*80)
        print("QUICK CYCLE ANALYSIS REPORT")
        print("="*80)
        
        print(f"\n📊 PERFORMANCE SUMMARY:")
        print(f"   Cycle Count: {summary['cycle_count']}")
        print(f"   Total Trades: {summary['total_trades']}")
        print(f"   Open Trades: {summary['open_trades']}")
        print(f"   Closed Trades: {summary['closed_trades']}")
        print(f"   Trades per Cycle: {summary['trades_per_cycle']:.3f}")
        print(f"   Total PnL: ${summary['total_pnl']:.2f}")
        print(f"   Win Rate: {summary['win_rate']:.1%}")
        print(f"   Balance Utilization: {summary['balance_utilization']:.1%}")
        print(f"   Trade Saturation: {summary['trade_saturation']:.1%}")
        
        print(f"\n💰 BALANCES:")
        for exchange, balance in balances.items():
            print(f"   {exchange.upper()}: ${balance['total']:.2f} total, ${balance['available']:.2f} available, ${balance['used']:.2f} used")
        
        print(f"\n⚙️  CURRENT PARAMETERS:")
        print(f"   Max Trades Per Exchange: {params['max_trades_per_exchange']}")
        print(f"   Min Exchange Balance: ${params['min_exchange_balance']}")
        print(f"   Min Order Size USD: ${params['min_order_size_usd']}")
        print(f"   Position Size Percentage: {params['position_size_percentage']:.1%}")
        
        print(f"\n🎯 RECOMMENDATIONS:")
        if not recommendations:
            print("   ✅ No immediate issues detected. Current parameters appear optimal.")
        else:
            for i, rec in enumerate(recommendations, 1):
                priority_icon = "🔴" if rec['priority'] == 'high' else "🟡" if rec['priority'] == 'medium' else "🟢"
                print(f"   {i}. {priority_icon} {rec['category']}: {rec['issue']}")
                print(f"      Current: {rec['current_value']}")
                print(f"      Recommendation: {rec['recommendation']}")
                print()
        
        print("="*80)

async def main():
    """Main function"""
    analyzer = QuickCycleAnalyzer()
    
    print("🔍 Quick Cycle Analysis - Analyzing current trading state...")
    
    # Get current state
    data = await analyzer.get_current_state()
    
    # Analyze performance
    analysis = analyzer.analyze_performance(data)
    
    # Print results
    analyzer.print_analysis(analysis)
    
    # Save to file
    with open("quick_analysis_report.json", "w") as f:
        json.dump(analysis, f, indent=2, default=str)
    print("\n💾 Analysis saved to 'quick_analysis_report.json'")

if __name__ == "__main__":
    asyncio.run(main()) 