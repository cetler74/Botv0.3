#!/usr/bin/env python3
"""
Profitability Monitor - Real-time Trading Performance Tracking
Monitors key profitability metrics and provides actionable insights
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

class ProfitabilityMonitor:
    def __init__(self):
        self.orchestrator_url = "http://localhost:8005"
        self.database_url = "http://localhost:8002"
        self.exchange_url = "http://localhost:8003"
        self.config_url = "http://localhost:8001"
        
        # Profitability targets
        self.target_win_rate = 0.60  # 60% target win rate
        self.target_profit_factor = 1.5  # 1.5 target profit factor
        self.target_daily_return = 0.02  # 2% target daily return
        self.max_daily_loss = 0.05  # 5% max daily loss
        self.min_trades_per_day = 3  # Minimum trades per day
        
        # Performance tracking
        self.daily_stats = {}
        self.weekly_stats = {}
        self.monthly_stats = {}
        
    async def get_comprehensive_data(self) -> Dict[str, Any]:
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
            
            # Get open trades
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.database_url}/api/v1/trades/open")
                response.raise_for_status()
                open_trades = response.json().get('trades', [])
            
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
            
            return {
                'trading_status': trading_status,
                'all_trades': all_trades,
                'open_trades': open_trades,
                'balances': balances
            }
        except Exception as e:
            logger.error(f"Error getting comprehensive data: {e}")
            return {}
    
    def calculate_profitability_metrics(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Calculate comprehensive profitability metrics"""
        if not data:
            return {}
        
        all_trades = data.get('all_trades', [])
        open_trades = data.get('open_trades', [])
        balances = data.get('balances', {})
        
        # Basic metrics
        total_trades = len(all_trades)
        closed_trades = [t for t in all_trades if t.get('status') == 'CLOSED']
        closed_count = len(closed_trades)
        
        # PnL calculations
        total_pnl = sum(t.get('realized_pnl', 0) for t in closed_trades)
        profitable_trades = [t for t in closed_trades if t.get('realized_pnl', 0) > 0]
        losing_trades = [t for t in closed_trades if t.get('realized_pnl', 0) < 0]
        
        win_rate = len(profitable_trades) / max(closed_count, 1)
        loss_rate = len(losing_trades) / max(closed_count, 1)
        
        # Profit factor calculation
        gross_profit = sum(t.get('realized_pnl', 0) for t in profitable_trades)
        gross_loss = abs(sum(t.get('realized_pnl', 0) for t in losing_trades))
        profit_factor = gross_profit / max(gross_loss, 1)
        
        # Average metrics
        avg_profit = np.mean([t.get('realized_pnl', 0) for t in profitable_trades]) if profitable_trades else 0
        avg_loss = np.mean([t.get('realized_pnl', 0) for t in losing_trades]) if losing_trades else 0
        avg_trade = np.mean([t.get('realized_pnl', 0) for t in closed_trades]) if closed_trades else 0
        
        # Risk metrics
        max_profit = max([t.get('realized_pnl', 0) for t in closed_trades]) if closed_trades else 0
        max_loss = min([t.get('realized_pnl', 0) for t in closed_trades]) if closed_trades else 0
        
        # Drawdown calculation
        cumulative_pnl = np.cumsum([t.get('realized_pnl', 0) for t in closed_trades])
        running_max = np.maximum.accumulate(cumulative_pnl)
        drawdown = cumulative_pnl - running_max
        max_drawdown = abs(np.min(drawdown)) if len(drawdown) > 0 else 0
        
        # Sharpe ratio (simplified)
        pnl_values = [t.get('realized_pnl', 0) for t in closed_trades]
        sharpe_ratio = np.mean(pnl_values) / np.std(pnl_values) if len(pnl_values) > 1 and np.std(pnl_values) > 0 else 0
        
        # Daily metrics
        today = datetime.now().date()
        today_trades = []
        for t in closed_trades:
            if t.get('exit_time'):
                try:
                    exit_time_str = t['exit_time'].replace('Z', '+00:00')
                    exit_time = datetime.fromisoformat(exit_time_str)
                    if exit_time.date() == today:
                        today_trades.append(t)
                except Exception as e:
                    logger.warning(f"Error parsing trade exit time: {e}")
                    continue
        daily_pnl = sum(t.get('realized_pnl', 0) for t in today_trades)
        daily_trades = len(today_trades)
        
        # Balance metrics
        total_balance = sum(b['total'] for b in balances.values())
        used_balance = sum(b['used'] for b in balances.values())
        balance_utilization = used_balance / max(total_balance, 1)
        
        # ROI calculation
        initial_balance = 1000  # Assume $1000 initial balance (can be made configurable)
        total_roi = (total_balance + total_pnl - initial_balance) / initial_balance if initial_balance > 0 else 0
        
        return {
            'total_trades': total_trades,
            'closed_trades': closed_count,
            'open_trades': len(open_trades),
            'win_rate': win_rate,
            'loss_rate': loss_rate,
            'profit_factor': profit_factor,
            'total_pnl': total_pnl,
            'gross_profit': gross_profit,
            'gross_loss': gross_loss,
            'avg_profit': avg_profit,
            'avg_loss': avg_loss,
            'avg_trade': avg_trade,
            'max_profit': max_profit,
            'max_loss': max_loss,
            'max_drawdown': max_drawdown,
            'sharpe_ratio': sharpe_ratio,
            'daily_pnl': daily_pnl,
            'daily_trades': daily_trades,
            'balance_utilization': balance_utilization,
            'total_roi': total_roi,
            'total_balance': total_balance,
            'used_balance': used_balance
        }
    
    def assess_profitability_health(self, metrics: Dict[str, Any]) -> Dict[str, Any]:
        """Assess overall profitability health and generate recommendations"""
        health_score = 0
        alerts = []
        recommendations = []
        
        # Win rate assessment
        if metrics.get('win_rate', 0) < self.target_win_rate:
            health_score += 20
            alerts.append({
                "type": "LOW_WIN_RATE",
                "severity": "HIGH",
                "message": f"Win rate below target: {metrics['win_rate']:.1%} vs {self.target_win_rate:.1%}",
                "action": "REVIEW_ENTRY_CRITERIA"
            })
            recommendations.append("Consider tightening entry criteria or improving exit strategies")
        else:
            health_score += 10
        
        # Profit factor assessment
        if metrics.get('profit_factor', 0) < self.target_profit_factor:
            health_score += 25
            alerts.append({
                "type": "LOW_PROFIT_FACTOR",
                "severity": "HIGH",
                "message": f"Profit factor below target: {metrics['profit_factor']:.2f} vs {self.target_profit_factor}",
                "action": "IMPROVE_RISK_REWARD"
            })
            recommendations.append("Focus on improving risk/reward ratios and reducing losses")
        else:
            health_score += 15
        
        # Daily trading activity
        if metrics.get('daily_trades', 0) < self.min_trades_per_day:
            health_score += 15
            alerts.append({
                "type": "LOW_TRADING_ACTIVITY",
                "severity": "MEDIUM",
                "message": f"Low daily trading activity: {metrics['daily_trades']} trades vs {self.min_trades_per_day}",
                "action": "RELAX_ENTRY_CRITERIA"
            })
            recommendations.append("Consider relaxing entry criteria to increase trading opportunities")
        
        # Balance utilization
        if metrics.get('balance_utilization', 0) < 0.1:  # Less than 10% utilization
            health_score += 10
            alerts.append({
                "type": "LOW_BALANCE_UTILIZATION",
                "severity": "MEDIUM",
                "message": f"Low balance utilization: {metrics['balance_utilization']:.1%}",
                "action": "INCREASE_POSITION_SIZES"
            })
            recommendations.append("Consider increasing position sizes or reducing minimum order sizes")
        
        # Drawdown assessment
        if metrics.get('max_drawdown', 0) > 0.1:  # More than 10% drawdown
            health_score += 20
            alerts.append({
                "type": "HIGH_DRAWDOWN",
                "severity": "HIGH",
                "message": f"High maximum drawdown: {metrics['max_drawdown']:.1%}",
                "action": "REDUCE_POSITION_SIZES"
            })
            recommendations.append("Implement tighter stop losses or reduce position sizes")
        
        # Sharpe ratio assessment
        sharpe_ratio = metrics.get('sharpe_ratio', 0)
        if sharpe_ratio < 1.0:
            health_score += 15
            alerts.append({
                "type": "LOW_SHARPE_RATIO",
                "severity": "MEDIUM",
                "message": f"Low Sharpe ratio: {sharpe_ratio:.2f}",
                "action": "IMPROVE_RISK_ADJUSTED_RETURNS"
            })
            recommendations.append("Focus on improving risk-adjusted returns through better position sizing")
        
        # Determine overall health level
        if health_score >= 60:
            health_level = "POOR"
        elif health_score >= 40:
            health_level = "FAIR"
        elif health_score >= 20:
            health_level = "GOOD"
        else:
            health_level = "EXCELLENT"
        
        return {
            "health_level": health_level,
            "health_score": health_score,
            "alerts": alerts,
            "recommendations": recommendations,
            "metrics": metrics
        }
    
    def print_profitability_report(self, health_assessment: Dict[str, Any]):
        """Print comprehensive profitability report"""
        metrics = health_assessment['metrics']
        alerts = health_assessment['alerts']
        recommendations = health_assessment['recommendations']
        
        print("\n" + "="*80)
        print("PROFITABILITY MONITORING REPORT")
        print("="*80)
        
        print(f"\n📊 PERFORMANCE METRICS:")
        print(f"   Health Level: {health_assessment['health_level']}")
        print(f"   Health Score: {health_assessment['health_score']}/100")
        print(f"   Total Trades: {metrics['total_trades']}")
        print(f"   Closed Trades: {metrics['closed_trades']}")
        print(f"   Open Trades: {metrics['open_trades']}")
        print(f"   Win Rate: {metrics['win_rate']:.1%}")
        print(f"   Loss Rate: {metrics['loss_rate']:.1%}")
        print(f"   Profit Factor: {metrics['profit_factor']:.2f}")
        print(f"   Total PnL: ${metrics['total_pnl']:.2f}")
        print(f"   Gross Profit: ${metrics['gross_profit']:.2f}")
        print(f"   Gross Loss: ${metrics['gross_loss']:.2f}")
        print(f"   Average Profit: ${metrics['avg_profit']:.2f}")
        print(f"   Average Loss: ${metrics['avg_loss']:.2f}")
        print(f"   Average Trade: ${metrics['avg_trade']:.2f}")
        print(f"   Max Profit: ${metrics['max_profit']:.2f}")
        print(f"   Max Loss: ${metrics['max_loss']:.2f}")
        print(f"   Max Drawdown: {metrics['max_drawdown']:.1%}")
        print(f"   Sharpe Ratio: {metrics['sharpe_ratio']:.2f}")
        print(f"   Daily PnL: ${metrics['daily_pnl']:.2f}")
        print(f"   Daily Trades: {metrics['daily_trades']}")
        print(f"   Balance Utilization: {metrics['balance_utilization']:.1%}")
        print(f"   Total ROI: {metrics['total_roi']:.1%}")
        
        if alerts:
            print(f"\n🚨 PROFITABILITY ALERTS:")
            for i, alert in enumerate(alerts, 1):
                severity_icon = "🔴" if alert['severity'] == 'HIGH' else "🟠" if alert['severity'] == 'MEDIUM' else "🟡"
                print(f"   {i}. {severity_icon} {alert['type']}: {alert['message']}")
                print(f"      Action: {alert['action']}")
                print()
        else:
            print(f"\n✅ No profitability alerts - performance is excellent!")
        
        if recommendations:
            print(f"\n💡 RECOMMENDATIONS:")
            for i, rec in enumerate(recommendations, 1):
                print(f"   {i}. {rec}")
            print()
        
        # Performance vs targets
        print(f"\n🎯 PERFORMANCE VS TARGETS:")
        print(f"   Win Rate: {metrics['win_rate']:.1%} vs {self.target_win_rate:.1%} target")
        print(f"   Profit Factor: {metrics['profit_factor']:.2f} vs {self.target_profit_factor} target")
        print(f"   Daily Trades: {metrics['daily_trades']} vs {self.min_trades_per_day} minimum")
        
        print("="*80)
    
    async def monitor_profitability(self, interval_seconds: int = 300):  # 5 minutes
        """Monitor profitability continuously"""
        print("🚀 Starting Profitability Monitor")
        print(f"Monitoring every {interval_seconds} seconds")
        print("Press Ctrl+C to stop")
        
        while True:
            try:
                # Get data
                data = await self.get_comprehensive_data()
                metrics = self.calculate_profitability_metrics(data)
                health_assessment = self.assess_profitability_health(metrics)
                
                # Print report
                self.print_profitability_report(health_assessment)
                
                # Save to file
                with open("profitability_report.json", "w") as f:
                    json.dump(health_assessment, f, indent=2, default=str)
                
                print(f"\n⏰ Next update in {interval_seconds} seconds...")
                await asyncio.sleep(interval_seconds)
                
            except KeyboardInterrupt:
                print("\n⏹️ Stopping profitability monitor...")
                break
            except Exception as e:
                logger.error(f"Error in profitability monitoring: {e}")
                await asyncio.sleep(60)

async def main():
    """Main function"""
    monitor = ProfitabilityMonitor()
    
    print("📈 Profitability Monitor - Real-time Trading Performance Tracking")
    
    # Get initial assessment
    data = await monitor.get_comprehensive_data()
    metrics = monitor.calculate_profitability_metrics(data)
    health_assessment = monitor.assess_profitability_health(metrics)
    
    # Print initial report
    monitor.print_profitability_report(health_assessment)
    
    # Ask if user wants continuous monitoring
    print("\nWould you like to start continuous monitoring? (y/n): ", end="")
    try:
        response = input().lower().strip()
        if response in ['y', 'yes']:
            await monitor.monitor_profitability()
    except KeyboardInterrupt:
        print("\n⏹️ Stopping...")

if __name__ == "__main__":
    asyncio.run(main()) 