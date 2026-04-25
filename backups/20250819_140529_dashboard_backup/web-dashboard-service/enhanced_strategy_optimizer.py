#!/usr/bin/env python3
"""
Enhanced Strategy Optimizer
Analyzes complete trading history and provides comprehensive optimization recommendations
"""

import asyncio
import httpx
import json
import yaml
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta
import statistics

@dataclass
class OptimizationResult:
    """Data structure for optimization results"""
    category: str
    target: str
    current_value: Any
    recommended_value: Any
    config_path: str
    priority: str
    expected_impact: str
    confidence: float
    reasoning: str

class EnhancedStrategyOptimizer:
    def __init__(self):
        # Use localhost for external execution
        self.orchestrator_url = "http://localhost:8005"
        self.database_url = "http://localhost:8002"
        self.config_url = "http://localhost:8001"
        self.exchange_url = "http://localhost:8003"
        
    async def get_complete_trading_data(self) -> Dict[str, Any]:
        """Get complete trading data including all historical trades and current status"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                # Get all trades (no limit to get complete history)
                trades_response = await client.get(f"{self.database_url}/api/v1/trades?limit=10000")
                trades_response.raise_for_status()
                trades_data = trades_response.json()
                
                # Get current trading status
                status_response = await client.get(f"{self.orchestrator_url}/api/v1/trading/status")
                status_response.raise_for_status()
                trading_status = status_response.json()
                
                # Get current config
                config_response = await client.get(f"{self.config_url}/api/v1/config/all")
                config_response.raise_for_status()
                current_config = config_response.json()
                
                # Get balances
                balances = {}
                exchanges = ["binance", "bybit", "cryptocom"]
                for exchange in exchanges:
                    try:
                        balance_response = await client.get(f"{self.exchange_url}/api/v1/account/balance/{exchange}")
                        if balance_response.status_code == 200:
                            balance_data = balance_response.json()
                            balances[exchange] = balance_data
                    except Exception as e:
                        print(f"Warning: Could not get balance for {exchange}: {e}")
                
                return {
                    "trades": trades_data.get('trades', []),
                    "trading_status": trading_status,
                    "current_config": current_config,
                    "balances": balances
                }
                
            except Exception as e:
                print(f"Error getting trading data: {e}")
                return {}
    
    def analyze_complete_performance(self, trades: List[Dict[str, Any]], trading_status: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze complete trading performance with detailed metrics"""
        if not trades:
            return {}
        
        # Basic metrics
        total_trades = len(trades)
        closed_trades = [t for t in trades if t.get('status') == 'CLOSED']
        open_trades = [t for t in trades if t.get('status') == 'OPEN']
        pending_trades = [t for t in trades if t.get('status') == 'PENDING']
        
        cycle_count = trading_status.get('cycle_count', 0)
        trades_per_cycle = total_trades / max(cycle_count, 1)
        
        # PnL analysis
        total_pnl = sum(t.get('realized_pnl', 0) for t in closed_trades)
        profitable_trades = [t for t in closed_trades if t.get('realized_pnl', 0) > 0]
        losing_trades = [t for t in closed_trades if t.get('realized_pnl', 0) < 0]
        
        win_rate = len(profitable_trades) / max(len(closed_trades), 1)
        
        # Strategy analysis
        strategy_stats = {}
        for trade in trades:
            strategy = trade.get('strategy', 'unknown')
            if strategy not in strategy_stats:
                strategy_stats[strategy] = {
                    'total_trades': 0,
                    'closed_trades': 0,
                    'profitable_trades': 0,
                    'total_pnl': 0,
                    'avg_pnl': 0,
                    'max_pnl': 0,
                    'min_pnl': 0,
                    'win_rate': 0,
                    'last_trade_time': None
                }
            
            strategy_stats[strategy]['total_trades'] += 1
            
            if trade.get('status') == 'CLOSED':
                strategy_stats[strategy]['closed_trades'] += 1
                pnl = trade.get('realized_pnl', 0)
                strategy_stats[strategy]['total_pnl'] += pnl
                
                if pnl > 0:
                    strategy_stats[strategy]['profitable_trades'] += 1
                
                # Track trade timing
                exit_time = trade.get('exit_time')
                if exit_time:
                    strategy_stats[strategy]['last_trade_time'] = exit_time
        
        # Calculate strategy metrics
        for strategy in strategy_stats:
            stats = strategy_stats[strategy]
            if stats['closed_trades'] > 0:
                stats['win_rate'] = stats['profitable_trades'] / stats['closed_trades']
                stats['avg_pnl'] = stats['total_pnl'] / stats['closed_trades']
                
                # Get PnL range
                strategy_trades = [t for t in closed_trades if t.get('strategy') == strategy]
                pnl_values = [t.get('realized_pnl', 0) for t in strategy_trades]
                if pnl_values:
                    stats['max_pnl'] = max(pnl_values)
                    stats['min_pnl'] = min(pnl_values)
        
        # Exchange analysis
        exchange_stats = {}
        for trade in trades:
            exchange = trade.get('exchange', 'unknown')
            if exchange not in exchange_stats:
                exchange_stats[exchange] = {
                    'total_trades': 0,
                    'closed_trades': 0,
                    'profitable_trades': 0,
                    'total_pnl': 0,
                    'win_rate': 0,
                    'last_trade_time': None
                }
            
            exchange_stats[exchange]['total_trades'] += 1
            
            if trade.get('status') == 'CLOSED':
                exchange_stats[exchange]['closed_trades'] += 1
                pnl = trade.get('realized_pnl', 0)
                exchange_stats[exchange]['total_pnl'] += pnl
                
                if pnl > 0:
                    exchange_stats[exchange]['profitable_trades'] += 1
                
                exit_time = trade.get('exit_time')
                if exit_time:
                    exchange_stats[exchange]['last_trade_time'] = exit_time
        
        # Calculate exchange metrics
        for exchange in exchange_stats:
            stats = exchange_stats[exchange]
            if stats['closed_trades'] > 0:
                stats['win_rate'] = stats['profitable_trades'] / stats['closed_trades']
        
        return {
            "total_trades": total_trades,
            "closed_trades": len(closed_trades),
            "open_trades": len(open_trades),
            "pending_trades": len(pending_trades),
            "cycle_count": cycle_count,
            "trades_per_cycle": trades_per_cycle,
            "total_pnl": total_pnl,
            "win_rate": win_rate,
            "strategy_stats": strategy_stats,
            "exchange_stats": exchange_stats,
            "performance_issues": self.identify_performance_issues(trades_per_cycle, win_rate, strategy_stats, exchange_stats)
        }
    
    def identify_performance_issues(self, trades_per_cycle: float, win_rate: float, strategy_stats: Dict, exchange_stats: Dict) -> List[str]:
        """Identify specific performance issues"""
        issues = []
        
        # Trading frequency issues
        if trades_per_cycle < 0.1:
            issues.append(f"CRITICAL: Extremely low trading frequency ({trades_per_cycle:.3f} trades/cycle)")
        elif trades_per_cycle < 0.5:
            issues.append(f"HIGH: Low trading frequency ({trades_per_cycle:.3f} trades/cycle)")
        
        # Win rate issues
        if win_rate < 0.4:
            issues.append(f"CRITICAL: Poor win rate ({win_rate:.1%})")
        elif win_rate < 0.6:
            issues.append(f"MEDIUM: Below average win rate ({win_rate:.1%})")
        
        # Strategy-specific issues
        for strategy, stats in strategy_stats.items():
            if stats['total_trades'] > 0 and stats['closed_trades'] == 0:
                issues.append(f"MEDIUM: {strategy} has {stats['total_trades']} trades but none closed")
            elif stats['closed_trades'] > 0 and stats['win_rate'] < 0.4:
                issues.append(f"HIGH: {strategy} poor performance ({stats['win_rate']:.1%} win rate)")
        
        # Exchange-specific issues
        for exchange, stats in exchange_stats.items():
            if stats['total_trades'] > 0 and stats['closed_trades'] == 0:
                issues.append(f"MEDIUM: {exchange} has {stats['total_trades']} trades but none closed")
            elif stats['closed_trades'] > 0 and stats['win_rate'] < 0.4:
                issues.append(f"HIGH: {exchange} poor performance ({stats['win_rate']:.1%} win rate)")
        
        return issues
    
    def generate_comprehensive_recommendations(self, performance_data: Dict[str, Any], current_config: Dict[str, Any]) -> List[OptimizationResult]:
        """Generate comprehensive optimization recommendations"""
        recommendations = []
        
        trades_per_cycle = performance_data.get('trades_per_cycle', 0)
        win_rate = performance_data.get('win_rate', 0)
        strategy_stats = performance_data.get('strategy_stats', {})
        performance_issues = performance_data.get('performance_issues', [])
        
        # Get current config values
        strategies_config = current_config.get('strategies', {})
        trading_config = current_config.get('trading', {})
        
        # 1. CRITICAL: Trading Frequency Optimization
        if trades_per_cycle < 0.1:
            # Extremely low trading frequency - aggressive optimization needed
            current_adx = strategies_config.get('engulfing_multi_tf', {}).get('parameters', {}).get('adx_entry_threshold', 25)
            current_min_confidence = strategies_config.get('engulfing_multi_tf', {}).get('parameters', {}).get('min_confidence', 0.4)
            current_confirmation_timeframes = strategies_config.get('engulfing_multi_tf', {}).get('parameters', {}).get('confirmation_timeframes', 1)
            
            # Aggressive ADX reduction
            if current_adx > 15:
                recommendations.append(OptimizationResult(
                    category="CRITICAL - Trading Frequency",
                    target="ADX Entry Threshold",
                    current_value=current_adx,
                    recommended_value=max(10, current_adx - 8),  # Aggressive reduction
                    config_path="strategies.engulfing_multi_tf.parameters.adx_entry_threshold",
                    priority="critical",
                    expected_impact="Dramatically increase trading frequency by relaxing ADX requirements",
                    confidence=0.9,
                    reasoning=f"Current {trades_per_cycle:.3f} trades/cycle is critically low. Aggressive ADX reduction needed."
                ))
            
            # Reduce confidence requirements
            if current_min_confidence > 0.3:
                recommendations.append(OptimizationResult(
                    category="CRITICAL - Trading Frequency",
                    target="Minimum Confidence",
                    current_value=current_min_confidence,
                    recommended_value=max(0.2, current_min_confidence - 0.2),
                    config_path="strategies.engulfing_multi_tf.parameters.min_confidence",
                    priority="critical",
                    expected_impact="Increase trade opportunities by reducing confidence requirements",
                    confidence=0.85,
                    reasoning="Extremely low trading frequency requires relaxing confidence thresholds"
                ))
            
            # Reduce confirmation timeframes
            if current_confirmation_timeframes > 0:
                recommendations.append(OptimizationResult(
                    category="CRITICAL - Trading Frequency",
                    target="Confirmation Timeframes",
                    current_value=current_confirmation_timeframes,
                    recommended_value=0,
                    config_path="strategies.engulfing_multi_tf.parameters.confirmation_timeframes",
                    priority="critical",
                    expected_impact="Eliminate confirmation delays to increase trade frequency",
                    confidence=0.8,
                    reasoning="Remove confirmation timeframes to speed up trade execution"
                ))
        
        # 2. Strategy-Specific Optimizations
        for strategy, stats in strategy_stats.items():
            if strategy in strategies_config:
                strategy_config = strategies_config[strategy]
                
                # Check if strategy is enabled but not performing
                if strategy_config.get('enabled', True) and stats['total_trades'] > 0:
                    if stats['closed_trades'] == 0:
                        # Strategy has trades but none closed - check parameters
                        if strategy == 'engulfing_multi_tf':
                            current_min_engulfing = strategy_config.get('parameters', {}).get('min_engulfing_size_multiplier', 1.0)
                            if current_min_engulfing > 0.8:
                                recommendations.append(OptimizationResult(
                                    category="Strategy Performance",
                                    target=f"{strategy} - Engulfing Size",
                                    current_value=current_min_engulfing,
                                    recommended_value=max(0.5, current_min_engulfing - 0.3),
                                    config_path=f"strategies.{strategy}.parameters.min_engulfing_size_multiplier",
                                    priority="high",
                                    expected_impact="Reduce engulfing size requirements to allow more trades",
                                    confidence=0.75,
                                    reasoning=f"{strategy} has {stats['total_trades']} trades but none closed - likely too restrictive"
                                ))
                    
                    elif stats['closed_trades'] > 0 and stats['win_rate'] < 0.4:
                        # Poor performing strategy
                        if strategy == 'engulfing_multi_tf':
                            current_adx = strategy_config.get('parameters', {}).get('adx_entry_threshold', 25)
                            if current_adx < 20:
                                recommendations.append(OptimizationResult(
                                    category="Strategy Performance",
                                    target=f"{strategy} - ADX Threshold",
                                    current_value=current_adx,
                                    recommended_value=current_adx + 5,
                                    config_path=f"strategies.{strategy}.parameters.adx_entry_threshold",
                                    priority="high",
                                    expected_impact="Increase ADX threshold to improve trade quality",
                                    confidence=0.7,
                                    reasoning=f"{strategy} has {stats['win_rate']:.1%} win rate - needs stricter entry criteria"
                                ))
        
        # 3. Trading Parameters Optimization
        current_min_order_size = trading_config.get('min_order_size_usd', 15.0)
        current_position_size = trading_config.get('position_size_percentage', 0.12)
        current_min_balance = trading_config.get('min_exchange_balance', 50.0)
        
        # Reduce minimum order size if trading frequency is very low
        if trades_per_cycle < 0.1 and current_min_order_size > 10.0:
            recommendations.append(OptimizationResult(
                category="Trading Parameters",
                target="Minimum Order Size",
                current_value=current_min_order_size,
                recommended_value=max(5.0, current_min_order_size - 5.0),
                config_path="trading.min_order_size_usd",
                priority="high",
                expected_impact="Enable smaller trades to increase trading opportunities",
                confidence=0.8,
                reasoning="Extremely low trading frequency - smaller orders may help"
            ))
        
        # Increase position size if win rate is good
        if win_rate > 0.6 and current_position_size < 0.2:
            recommendations.append(OptimizationResult(
                category="Trading Parameters",
                target="Position Size",
                current_value=current_position_size,
                recommended_value=min(0.25, current_position_size + 0.05),
                config_path="trading.position_size_percentage",
                priority="medium",
                expected_impact="Increase position size to maximize profits from good win rate",
                confidence=0.7,
                reasoning=f"Good win rate ({win_rate:.1%}) - can afford larger positions"
            ))
        
        # 4. Risk Management Optimization
        if win_rate > 0.7:
            # Excellent performance - optimize for growth
            current_max_trades = trading_config.get('max_trades_per_exchange', 2)
            if current_max_trades < 4:
                recommendations.append(OptimizationResult(
                    category="Risk Management",
                    target="Max Trades Per Exchange",
                    current_value=current_max_trades,
                    recommended_value=min(5, current_max_trades + 1),
                    config_path="trading.max_trades_per_exchange",
                    priority="medium",
                    expected_impact="Allow more concurrent trades to capitalize on good performance",
                    confidence=0.6,
                    reasoning=f"Excellent win rate ({win_rate:.1%}) - can handle more trades"
                ))
        
        return recommendations
    
    async def run_enhanced_optimization(self) -> Dict[str, Any]:
        """Run the enhanced optimization analysis"""
        print("🔍 Enhanced Strategy Optimizer - Analyzing Complete Trading History")
        print("=" * 70)
        
        # Get complete data
        data = await self.get_complete_trading_data()
        if not data:
            return {"error": "Failed to get trading data"}
        
        trades = data.get('trades', [])
        trading_status = data.get('trading_status', {})
        current_config = data.get('current_config', {})
        
        print(f"📊 Data Retrieved:")
        print(f"   Total Trades: {len(trades)}")
        print(f"   Cycle Count: {trading_status.get('cycle_count', 0)}")
        print(f"   Trading Status: {trading_status.get('status', 'unknown')}")
        
        # Analyze performance
        performance_data = self.analyze_complete_performance(trades, trading_status)
        
        print(f"\n📈 Performance Analysis:")
        print(f"   Trades per Cycle: {performance_data.get('trades_per_cycle', 0):.3f}")
        print(f"   Win Rate: {performance_data.get('win_rate', 0):.1%}")
        print(f"   Total PnL: ${performance_data.get('total_pnl', 0):.2f}")
        
        # Identify issues
        issues = performance_data.get('performance_issues', [])
        if issues:
            print(f"\n⚠️  Performance Issues Identified:")
            for issue in issues:
                print(f"   • {issue}")
        
        # Generate recommendations
        recommendations = self.generate_comprehensive_recommendations(performance_data, current_config)
        
        print(f"\n🎯 Optimization Recommendations:")
        print(f"   Total Recommendations: {len(recommendations)}")
        
        for i, rec in enumerate(recommendations, 1):
            print(f"\n   {i}. {rec.category}")
            print(f"      Target: {rec.target}")
            print(f"      Current: {rec.current_value} → Recommended: {rec.recommended_value}")
            print(f"      Priority: {rec.priority.upper()}")
            print(f"      Confidence: {rec.confidence:.0%}")
            print(f"      Impact: {rec.expected_impact}")
            print(f"      Reasoning: {rec.reasoning}")
        
        return {
            "performance_data": performance_data,
            "recommendations": [
                {
                    "category": rec.category,
                    "target": rec.target,
                    "current_value": rec.current_value,
                    "recommended_value": rec.recommended_value,
                    "config_path": rec.config_path,
                    "priority": rec.priority,
                    "expected_impact": rec.expected_impact,
                    "confidence": rec.confidence,
                    "reasoning": rec.reasoning
                }
                for rec in recommendations
            ],
            "summary": {
                "total_recommendations": len(recommendations),
                "critical_priority": len([r for r in recommendations if r.priority == "critical"]),
                "high_priority": len([r for r in recommendations if r.priority == "high"]),
                "medium_priority": len([r for r in recommendations if r.priority == "medium"]),
                "performance_issues": len(issues)
            }
        }

async def main():
    """Main function"""
    optimizer = EnhancedStrategyOptimizer()
    result = await optimizer.run_enhanced_optimization()
    
    if "error" in result:
        print(f"❌ Error: {result['error']}")
    else:
        print(f"\n✅ Enhanced Optimization Complete!")
        print(f"   Recommendations: {result['summary']['total_recommendations']}")
        print(f"   Critical: {result['summary']['critical_priority']}")
        print(f"   High: {result['summary']['high_priority']}")
        print(f"   Medium: {result['summary']['medium_priority']}")

if __name__ == "__main__":
    asyncio.run(main()) 