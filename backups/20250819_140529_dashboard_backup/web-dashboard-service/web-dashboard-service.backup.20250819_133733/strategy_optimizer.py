#!/usr/bin/env python3
"""
Comprehensive Strategy Optimizer - Full Set of Optimizations
Applies data-driven parameter optimization based on profitability analysis
"""

import asyncio
import httpx
import json
import time
import yaml
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Any, Optional, Tuple
import pandas as pd
import numpy as np
from dataclasses import dataclass, asdict
import logging
import itertools
import statistics

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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

@dataclass
class StrategyTest:
    """Data structure for strategy testing"""
    test_id: str
    strategy_name: str
    parameters: Dict[str, Any]
    start_time: datetime
    end_time: Optional[datetime] = None
    trades_executed: int = 0
    trades_profitable: int = 0
    total_pnl: float = 0.0
    win_rate: float = 0.0
    avg_profit_per_trade: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    profit_factor: float = 0.0
    balance_utilization: float = 0.0
    trades_per_cycle: float = 0.0
    status: str = "running"

class ComprehensiveStrategyOptimizer:
    def __init__(self):
        # Use Docker service names for internal communication
        self.orchestrator_url = "http://trading-bot-orchestrator:8005"
        self.database_url = "http://trading-bot-database:8002"
        self.config_url = "http://trading-bot-config:8001"
        self.strategy_url = "http://trading-bot-strategy:8004"
        self.exchange_url = "http://trading-bot-exchange:8003"
        self.web_dashboard_url = "http://trading-bot-web:8006"
        self.tests: List[StrategyTest] = []
        self.current_test: Optional[StrategyTest] = None
        self.optimization_results: List[OptimizationResult] = []
        
    async def get_current_performance_data(self) -> Dict[str, Any]:
        """Get comprehensive current performance data"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                print("   Getting trading status...")
                # Get trading status
                response = await client.get(f"{self.orchestrator_url}/api/v1/trading/status")
                response.raise_for_status()
                trading_status = response.json()
                print("   ✅ Trading status retrieved")
                
                print("   Getting trades data...")
                # Get all trades
                response = await client.get(f"{self.database_url}/api/v1/trades?limit=1000")
                response.raise_for_status()
                trades_data = response.json()
                print("   ✅ Trades data retrieved")
                
                print("   Getting config data...")
                # Get current config
                response = await client.get(f"{self.config_url}/api/v1/config/all")
                response.raise_for_status()
                current_config = response.json()
                print("   ✅ Config data retrieved")
                
                print("   Getting balances...")
                # Get balances from individual exchanges
                balances_data = {}
                exchanges = ["binance", "bybit", "cryptocom"]
                
                for exchange in exchanges:
                    try:
                        response = await client.get(f"{self.exchange_url}/api/v1/account/balance/{exchange}")
                        response.raise_for_status()
                        exchange_balance = response.json()
                        balances_data[exchange] = exchange_balance
                    except Exception as e:
                        print(f"   ⚠️  Could not get balance for {exchange}: {e}")
                        balances_data[exchange] = {}
                
                print("   ✅ Balances data retrieved")
                
                # Calculate profitability metrics manually
                profitability_data = self.calculate_profitability_metrics(
                    trades_data.get('trades', []), 
                    trading_status, 
                    balances_data
                )
                print("   ✅ Profitability metrics calculated")
                
                return {
                    "profitability": profitability_data,
                    "trading_status": trading_status,
                    "trades": trades_data.get('trades', []),
                    "current_config": current_config
                }
                
        except Exception as e:
            logger.error(f"Error getting performance data: {e}")
            import traceback
            traceback.print_exc()
            return {}
    
    def calculate_profitability_metrics(self, trades: List[Dict[str, Any]], trading_status: Dict[str, Any], balances: Dict[str, Any]) -> Dict[str, Any]:
        """Calculate profitability metrics from raw data"""
        if not trades:
            return {
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "total_pnl": 0.0,
                "trades_per_cycle": 0.0,
                "max_drawdown": 0.0,
                "sharpe_ratio": 0.0,
                "balance_utilization": 0.0,
                "exchange_stats": {},
                "strategy_stats": {}
            }
        
        # Calculate basic metrics
        total_trades = len(trades)
        
        # Check for missing PnL data
        trades_with_pnl = [t for t in trades if t.get('realized_pnl') is not None]
        trades_without_pnl = total_trades - len(trades_with_pnl)
        
        if trades_without_pnl > 0:
            print(f"   ⚠️  Warning: {trades_without_pnl} trades have missing realized_pnl data")
        
        # Only calculate metrics if we have PnL data
        if trades_with_pnl:
            profitable_trades = len([t for t in trades_with_pnl if t.get('realized_pnl', 0) > 0])
            win_rate = profitable_trades / len(trades_with_pnl) if trades_with_pnl else 0.0
            
            total_pnl = sum(t.get('realized_pnl', 0) for t in trades_with_pnl)
            total_profit = sum(t.get('realized_pnl', 0) for t in trades_with_pnl if t.get('realized_pnl', 0) > 0)
            total_loss = abs(sum(t.get('realized_pnl', 0) for t in trades_with_pnl if t.get('realized_pnl', 0) < 0))
            profit_factor = total_profit / total_loss if total_loss > 0 else 0.0
        else:
            # If no PnL data, don't make optimization recommendations
            win_rate = 0.0
            total_pnl = 0.0
            profit_factor = 0.0
            print("   ❌ No PnL data available - cannot calculate performance metrics")
        
        # Calculate balance utilization
        total_balance = sum(b.get('total_balance_usd', 0) for b in balances.values())
        balance_utilization = (total_pnl / total_balance * 100) if total_balance > 0 else 0.0
        
        # Calculate trades per cycle
        cycle_count = trading_status.get('cycle_count', 1)
        trades_per_cycle = total_trades / cycle_count if cycle_count > 0 else 0.0
        
        # Calculate exchange and strategy stats
        exchange_stats = {}
        strategy_stats = {}
        
        for trade in trades:
            exchange = trade.get('exchange', 'unknown')
            strategy = trade.get('strategy', 'unknown')
            
            if exchange not in exchange_stats:
                exchange_stats[exchange] = {'total_trades': 0, 'profitable_trades': 0, 'total_pnl': 0}
            if strategy not in strategy_stats:
                strategy_stats[strategy] = {'total_trades': 0, 'profitable_trades': 0, 'total_pnl': 0}
            
            exchange_stats[exchange]['total_trades'] += 1
            strategy_stats[strategy]['total_trades'] += 1
            
            if trade.get('realized_pnl', 0) > 0:
                exchange_stats[exchange]['profitable_trades'] += 1
                strategy_stats[strategy]['profitable_trades'] += 1
            
            exchange_stats[exchange]['total_pnl'] += trade.get('realized_pnl', 0)
            strategy_stats[strategy]['total_pnl'] += trade.get('realized_pnl', 0)
        
        # Calculate win rates for exchanges and strategies
        for stats in exchange_stats.values():
            stats['win_rate'] = stats['profitable_trades'] / stats['total_trades'] if stats['total_trades'] > 0 else 0.0
        
        for stats in strategy_stats.values():
            stats['win_rate'] = stats['profitable_trades'] / stats['total_trades'] if stats['total_trades'] > 0 else 0.0
        
        return {
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "total_pnl": total_pnl,
            "trades_per_cycle": trades_per_cycle,
            "max_drawdown": 0.0,  # Simplified for now
            "sharpe_ratio": 0.0,  # Simplified for now
            "balance_utilization": balance_utilization,
            "exchange_stats": exchange_stats,
            "strategy_stats": strategy_stats
        }
    
    async def analyze_performance_gaps(self, performance_data: Dict[str, Any]) -> List[OptimizationResult]:
        """Analyze performance gaps and generate optimization recommendations"""
        results = []
        
        profitability = performance_data.get("profitability", {})
        current_config = performance_data.get("current_config", {})
        
        # Extract current metrics
        win_rate = profitability.get("win_rate", 0)
        profit_factor = profitability.get("profit_factor", 0)
        balance_utilization = profitability.get("balance_utilization", 0)
        trades_per_cycle = profitability.get("trades_per_cycle", 0)
        max_drawdown = profitability.get("max_drawdown", 0)
        sharpe_ratio = profitability.get("sharpe_ratio", 0)
        
        # Get current config values (with defaults)
        trading_config = current_config.get("trading", {})
        strategies_config = current_config.get("strategies", {})
        
        # 1. Trading Frequency Optimizations
        if trades_per_cycle < 0.1:  # Very low trading frequency
            current_adx = strategies_config.get("engulfing_multi_tf", {}).get("parameters", {}).get("adx_entry_threshold", 25)
            if current_adx > 15:
                results.append(OptimizationResult(
                    category="Trading Frequency",
                    target="Entry Criteria",
                    current_value=current_adx,
                    recommended_value=current_adx - 3,  # Reduce by 3
                    config_path="strategies.engulfing_multi_tf.parameters.adx_entry_threshold",
                    priority="high",
                    expected_impact="Increase trading frequency by relaxing entry criteria",
                    confidence=0.8
                ))
        
        # 2. Balance Utilization Optimizations
        if balance_utilization < 0.05:  # Very low balance utilization
            current_min_order = trading_config.get("min_order_size_usd", 15.0)
            if current_min_order > 15.0:
                results.append(OptimizationResult(
                    category="Balance Utilization",
                    target="Trading Parameters",
                    current_value=current_min_order,
                    recommended_value=15.0,
                    config_path="trading.min_order_size_usd",
                    priority="high",
                    expected_impact="Enable smaller trades to increase balance utilization",
                    confidence=0.9
                ))
        
        # 3. Strategy Performance Optimizations
        strategy_stats = profitability.get("strategy_stats", {})
        for strategy_name, stats in strategy_stats.items():
            if strategy_name in strategies_config:
                strategy_win_rate = stats.get("win_rate", 0)
                total_trades = stats.get("total_trades", 0)
                
                # Only make recommendations if we have meaningful data
                if total_trades >= 2 and strategy_win_rate > 0:  # Only if we have PnL data
                    # Disable poor performing strategies
                    if strategy_win_rate < 0.4:
                        current_enabled = strategies_config[strategy_name].get("enabled", True)
                        if current_enabled:
                            results.append(OptimizationResult(
                                category="Strategy Performance",
                                target=f"{strategy_name} Strategy",
                                current_value="enabled",
                                recommended_value="disabled",
                                config_path=f"strategies.{strategy_name}.enabled",
                                priority="high",
                                expected_impact="Stop losing trades from underperforming strategy",
                                confidence=0.8
                            ))
                
                # Optimize high performing strategies
                elif total_trades >= 2 and strategy_win_rate > 0.8:
                    # Check for strategy-specific optimization parameters that actually exist
                    strategy_params = strategies_config[strategy_name].get("parameters", {})
                    
                    # For heikin_ashi strategy, optimize risk_per_trade if it exists
                    if strategy_name == "heikin_ashi" and "risk_per_trade" in strategy_params:
                        current_risk = strategy_params.get("risk_per_trade", 0.01)
                        if current_risk < 0.02:  # Increase risk for high-performing strategy
                            results.append(OptimizationResult(
                                category="Strategy Performance",
                                target=f"{strategy_name} Strategy",
                                current_value=current_risk,
                                recommended_value=min(current_risk + 0.005, 0.02),
                                config_path=f"strategies.{strategy_name}.parameters.risk_per_trade",
                                priority="low",
                                expected_impact="Increase risk allocation to high-performing strategy",
                                confidence=0.7
                            ))
                    
                    # For other strategies, check if they have position_size_multiplier
                    elif "position_size_multiplier" in strategy_params:
                        current_multiplier = strategy_params.get("position_size_multiplier", 1.0)
                        if current_multiplier < 1.5:
                            results.append(OptimizationResult(
                                category="Strategy Performance",
                                target=f"{strategy_name} Strategy",
                                current_value=current_multiplier,
                                recommended_value=min(current_multiplier + 0.2, 1.5),
                                config_path=f"strategies.{strategy_name}.parameters.position_size_multiplier",
                                priority="low",
                                expected_impact="Increase allocation to high-performing strategy",
                                confidence=0.7
                            ))
        
        # 4. Exchange Performance Optimizations
        exchange_stats = profitability.get("exchange_stats", {})
        for exchange_name, stats in exchange_stats.items():
            if stats.get("total_trades", 0) >= 2:
                exchange_win_rate = stats.get("win_rate", 0)
                
                # Optimize high performing exchanges
                if exchange_win_rate > 0.8:
                    current_max_trades = trading_config.get("max_trades_per_exchange", 20)
                    if current_max_trades < 10:
                        results.append(OptimizationResult(
                            category="Exchange Performance",
                            target=f"{exchange_name.upper()} Exchange",
                            current_value=current_max_trades,
                            recommended_value=min(current_max_trades + 2, 10),
                            config_path="trading.max_trades_per_exchange",
                            priority="low",
                            expected_impact="Increase allocation to high-performing exchange",
                            confidence=0.6
                        ))
                
                # Reduce allocation to poor performing exchanges
                elif exchange_win_rate < 0.4:
                    current_min_balance = trading_config.get("min_exchange_balance", 50.0)
                    if current_min_balance > 30.0:
                        results.append(OptimizationResult(
                            category="Exchange Performance",
                            target=f"{exchange_name.upper()} Exchange",
                            current_value=current_min_balance,
                            recommended_value=30.0,
                            config_path="trading.min_exchange_balance",
                            priority="medium",
                            expected_impact="Reduce balance requirements for poor performing exchange",
                            confidence=0.6
                        ))
        
        return results
        
        # 7. Exchange-Specific Optimizations
        exchange_stats = profitability.get("exchange_stats", {})
        for exchange, stats in exchange_stats.items():
            if stats.get("total_trades", 0) >= 2:
                exchange_win_rate = stats.get("win_rate", 0)
                if exchange_win_rate < 0.4:
                    results.append(OptimizationResult(
                        category="Exchange Performance",
                        target=f"{exchange.upper()} Minimum Balance",
                        current_value=current_config.get("trading", {}).get("min_exchange_balance", 50.0),
                        recommended_value=30.0,
                        config_path="trading.min_exchange_balance",
                        priority="medium",
                        expected_impact="Reduce balance requirements for poor performing exchange",
                        confidence=0.6
                    ))
        
        return results
    
    async def apply_optimizations(self, optimizations: List[OptimizationResult], auto_apply: bool = False) -> Dict[str, Any]:
        """Apply optimization recommendations"""
        applied_changes = []
        skipped_changes = []
        
        # Sort by priority (high first)
        priority_order = {"high": 3, "medium": 2, "low": 1}
        sorted_optimizations = sorted(optimizations, key=lambda x: priority_order.get(x.priority, 0), reverse=True)
        
        for opt in sorted_optimizations:
            if auto_apply or opt.priority == "high":
                try:
                    # Apply the change
                    success = await self.apply_single_optimization(opt)
                    if success:
                        applied_changes.append(opt)
                        logger.info(f"✅ Applied: {opt.category} - {opt.target}")
                    else:
                        skipped_changes.append(opt)
                        logger.warning(f"⚠️ Failed to apply: {opt.category} - {opt.target}")
                except Exception as e:
                    logger.error(f"❌ Error applying {opt.category} - {opt.target}: {e}")
                    skipped_changes.append(opt)
            else:
                skipped_changes.append(opt)
                logger.info(f"⏸️ Skipped (not auto-applied): {opt.category} - {opt.target}")
        
        return {
            "applied": applied_changes,
            "skipped": skipped_changes,
            "total_applied": len(applied_changes),
            "total_skipped": len(skipped_changes)
        }
    
    async def apply_single_optimization(self, optimization: OptimizationResult) -> bool:
        """Apply a single optimization to the config"""
        try:
            # Update via config service first
            async with httpx.AsyncClient() as client:
                response = await client.put(f"{self.config_url}/api/v1/config/update", 
                                          params={"path": optimization.config_path, "value": optimization.recommended_value})
                if response.status_code == 200:
                    return True
            
            # Fallback: update config.yaml directly
            return await self.update_config_yaml_direct(optimization.config_path, optimization.recommended_value)
            
        except Exception as e:
            logger.error(f"Error applying optimization: {e}")
            return False
    
    async def update_config_yaml_direct(self, config_path: str, value: Any) -> bool:
        """Update config.yaml directly"""
        try:
            config_file = "config/config.yaml"
            if not os.path.exists(config_file):
                logger.error(f"Config file not found: {config_file}")
                return False
            
            with open(config_file, 'r') as f:
                config = yaml.safe_load(f)
            
            # Navigate to the nested path and update
            path_parts = config_path.split('.')
            current = config
            
            for part in path_parts[:-1]:
                if part not in current:
                    current[part] = {}
                current = current[part]
            
            current[path_parts[-1]] = value
            
            # Save updated config
            with open(config_file, 'w') as f:
                yaml.dump(config, f, default_flow_style=False)
            
            logger.info(f"Updated {config_path} = {value} in config.yaml")
            return True
            
        except Exception as e:
            logger.error(f"Error updating config.yaml: {e}")
            return False
    
    async def create_parameter_combinations(self) -> List[Dict[str, Any]]:
        """Create optimized parameter combinations based on current performance"""
        combinations = []
        
        # Get current performance to determine parameter ranges
        performance_data = await self.get_current_performance_data()
        profitability = performance_data.get("profitability", {})
        
        win_rate = profitability.get("win_rate", 0)
        profit_factor = profitability.get("profit_factor", 0)
        trades_per_cycle = profitability.get("trades_per_cycle", 0)
        
        # Adaptive parameter ranges based on current performance
        if win_rate < 0.5:
            # Low win rate - focus on quality over quantity
            adx_range = [20, 25, 30]
            confidence_range = [0.5, 0.6, 0.7]
            confirmation_range = [1, 2]
        elif win_rate < 0.7:
            # Moderate win rate - balance quality and quantity
            adx_range = [15, 20, 25]
            confidence_range = [0.4, 0.5, 0.6]
            confirmation_range = [0, 1]
        else:
            # High win rate - focus on quantity
            adx_range = [12, 15, 18]
            confidence_range = [0.3, 0.4, 0.5]
            confirmation_range = [0]
        
        if profit_factor < 1.2:
            # Low profit factor - increase position size
            position_range = [0.15, 0.18, 0.20]
        else:
            # Good profit factor - maintain or slightly increase
            position_range = [0.12, 0.15, 0.18]
        
        if trades_per_cycle < 0.1:
            # Low frequency - reduce barriers
            order_size_range = [15, 20, 25]
            cooldown_range = [15, 20, 25]
        else:
            # Good frequency - maintain current settings
            order_size_range = [25, 30, 35]
            cooldown_range = [25, 30, 35]
        
        parameter_ranges = {
            "adx_entry_threshold": adx_range,
            "min_confidence": confidence_range,
            "confirmation_timeframes": confirmation_range,
            "min_engulfing_size_multiplier": [0.3, 0.4, 0.5],
            "cooldown_minutes": cooldown_range,
            "position_size_percentage": position_range,
            "min_order_size_usd": order_size_range,
        }
        
        # Generate combinations
        keys = list(parameter_ranges.keys())
        values = list(parameter_ranges.values())
        
        for combination in itertools.product(*values):
            params = dict(zip(keys, combination))
            combinations.append(params)
        
        # Limit to top combinations for efficiency
        combinations = combinations[:15]
        
        logger.info(f"Generated {len(combinations)} adaptive parameter combinations")
        return combinations
    
    async def run_comprehensive_optimization(self, auto_apply: bool = False, test_duration_minutes: int = 20):
        """Run comprehensive optimization process"""
        print("\n" + "="*80)
        print("🚀 COMPREHENSIVE STRATEGY OPTIMIZER")
        print("="*80)
        
        # Step 1: Analyze current performance
        print("\n📊 STEP 1: Analyzing Current Performance...")
        performance_data = await self.get_current_performance_data()
        
        if not performance_data:
            print("❌ Failed to get performance data")
            return
        
        profitability = performance_data.get("profitability", {})
        print(f"   Current Win Rate: {profitability.get('win_rate', 0):.1%}")
        print(f"   Current Profit Factor: {profitability.get('profit_factor', 0):.2f}")
        print(f"   Current Balance Utilization: {profitability.get('balance_utilization', 0):.1%}")
        print(f"   Current Trades/Cycle: {profitability.get('trades_per_cycle', 0):.3f}")
        
        # Step 2: Generate optimization recommendations
        print("\n🎯 STEP 2: Generating Optimization Recommendations...")
        optimizations = await self.analyze_performance_gaps(performance_data)
        
        if not optimizations:
            print("✅ No optimizations needed - current settings are optimal!")
            return
        
        print(f"   Found {len(optimizations)} optimization opportunities:")
        for i, opt in enumerate(optimizations, 1):
            priority_icon = "🔴" if opt.priority == "high" else "🟡" if opt.priority == "medium" else "🟢"
            print(f"   {i}. {priority_icon} {opt.category} - {opt.target}")
            print(f"      Current: {opt.current_value} → Recommended: {opt.recommended_value}")
            print(f"      Expected Impact: {opt.expected_impact}")
            print()
        
        # Step 3: Apply optimizations
        print("\n⚙️ STEP 3: Applying Optimizations...")
        if auto_apply:
            print("   Auto-applying high priority optimizations...")
            applied_results = await self.apply_optimizations(optimizations, auto_apply=True)
            
            print(f"   ✅ Applied: {applied_results['total_applied']} changes")
            print(f"   ⏸️ Skipped: {applied_results['total_skipped']} changes")
            
            if applied_results['applied']:
                print("\n   Applied Changes:")
                for change in applied_results['applied']:
                    print(f"      • {change.config_path} = {change.recommended_value}")
        else:
            print("   Showing recommendations (use --auto-apply to apply automatically)")
            for opt in optimizations:
                print(f"      {opt.config_path} = {opt.recommended_value}")
        
        # Step 4: Run parameter testing (if requested)
        if test_duration_minutes > 0:
            print(f"\n🧪 STEP 4: Testing Parameter Combinations ({test_duration_minutes} min each)...")
            await self.run_parameter_testing(test_duration_minutes)
        
        # Step 5: Generate final report
        print("\n📋 STEP 5: Generating Final Report...")
        await self.generate_optimization_report(optimizations, performance_data)
        
        print("\n" + "="*80)
        print("✅ COMPREHENSIVE OPTIMIZATION COMPLETE")
        print("="*80)
    
    async def run_parameter_testing(self, test_duration_minutes: int):
        """Run parameter combination testing"""
        combinations = await self.create_parameter_combinations()
        
        print(f"   Testing {len(combinations)} parameter combinations...")
        
        for i, params in enumerate(combinations, 1):
            print(f"\n   🧪 Test {i}/{len(combinations)}: {params}")
            
            # Start test
            test_id = await self.start_strategy_test("engulfing_multi_tf", params)
            
            # Monitor test
            await self.monitor_test_performance(test_id, test_duration_minutes)
            
            # Wait between tests
            if i < len(combinations):
                print("   ⏳ Waiting 3 minutes before next test...")
                await asyncio.sleep(180)
    
    async def generate_optimization_report(self, optimizations: List[OptimizationResult], performance_data: Dict[str, Any]):
        """Generate comprehensive optimization report"""
        report = {
            "timestamp": datetime.now().isoformat(),
            "performance_baseline": performance_data.get("profitability", {}),
            "optimizations": [asdict(opt) for opt in optimizations],
            "test_results": [asdict(test) for test in self.tests if test.status == "completed"],
            "summary": {
                "total_optimizations": len(optimizations),
                "high_priority": len([opt for opt in optimizations if opt.priority == "high"]),
                "medium_priority": len([opt for opt in optimizations if opt.priority == "medium"]),
                "low_priority": len([opt for opt in optimizations if opt.priority == "low"]),
                "expected_improvements": self.calculate_expected_improvements(optimizations)
            }
        }
        
        # Save report
        with open("comprehensive_optimization_report.json", "w") as f:
            json.dump(report, f, indent=2, default=str)
        
        print("   💾 Report saved to 'comprehensive_optimization_report.json'")
        
        # Print summary
        print(f"\n   📈 Expected Improvements:")
        improvements = report["summary"]["expected_improvements"]
        for metric, improvement in improvements.items():
            print(f"      • {metric}: {improvement}")
    
    def calculate_expected_improvements(self, optimizations: List[OptimizationResult]) -> Dict[str, str]:
        """Calculate expected improvements from optimizations"""
        improvements = {
            "Win Rate": "0%",
            "Profit Factor": "0%",
            "Trading Frequency": "0%",
            "Balance Utilization": "0%"
        }
        
        for opt in optimizations:
            if opt.category == "Win Rate":
                improvements["Win Rate"] = "+15-20%"
            elif opt.category == "Profit Factor":
                improvements["Profit Factor"] = "+20-30%"
            elif opt.category == "Trading Frequency":
                improvements["Trading Frequency"] = "+40-60%"
            elif opt.category == "Balance Utilization":
                improvements["Balance Utilization"] = "+50-100%"
        
        return improvements
    
    # Keep existing methods for backward compatibility
    async def start_strategy_test(self, strategy_name: str, parameters: Dict[str, Any]) -> str:
        """Start a new strategy test"""
        test_id = f"test_{strategy_name}_{int(time.time())}"
        
        test = StrategyTest(
            test_id=test_id,
            strategy_name=strategy_name,
            parameters=parameters,
            start_time=datetime.now(timezone.utc)
        )
        
        self.tests.append(test)
        self.current_test = test
        
        # Update strategy parameters
        await self.update_strategy_parameters(strategy_name, parameters)
        
        logger.info(f"Started test {test_id} with parameters: {parameters}")
        return test_id
    
    async def update_strategy_parameters(self, strategy_name: str, parameters: Dict[str, Any]):
        """Update strategy parameters via config service"""
        try:
            for param_name, param_value in parameters.items():
                async with httpx.AsyncClient() as client:
                    response = await client.put(f"{self.config_url}/api/v1/config/strategies/{strategy_name}/parameters/{param_name}", 
                                              json={"value": param_value})
                    if response.status_code != 200:
                        logger.warning(f"Could not update {strategy_name}.{param_name}: {response.status_code}")
            
            logger.info(f"Updated {strategy_name} parameters: {parameters}")
            
        except Exception as e:
            logger.error(f"Error updating strategy parameters: {e}")
            await self.update_config_yaml_direct(f"strategies.{strategy_name}.parameters.{param_name}", param_value)
    
    async def monitor_test_performance(self, test_id: str, duration_minutes: int = 30):
        """Monitor test performance for specified duration"""
        test = next((t for t in self.tests if t.test_id == test_id), None)
        if not test:
            logger.error(f"Test {test_id} not found")
            return
        
        start_time = time.time()
        end_time = start_time + (duration_minutes * 60)
        
        logger.info(f"Monitoring test {test_id} for {duration_minutes} minutes")
        
        while time.time() < end_time and test.status == "running":
            try:
                # Get current trading status and trades
                async with httpx.AsyncClient() as client:
                    response = await client.get(f"{self.orchestrator_url}/api/v1/trading/status")
                    response.raise_for_status()
                    trading_status = response.json()
                    
                    response = await client.get(f"{self.database_url}/api/v1/trades?limit=100")
                    response.raise_for_status()
                    trades = response.json().get('trades', [])
                
                # Filter trades that occurred during this test
                test_trades = []
                for t in trades:
                    if t.get('entry_time'):
                        try:
                            entry_time_str = t['entry_time'].replace('Z', '+00:00')
                            entry_time = datetime.fromisoformat(entry_time_str)
                            
                            if test.start_time.tzinfo is None:
                                test_start_time = test.start_time.replace(tzinfo=timezone.utc)
                            else:
                                test_start_time = test.start_time
                            
                            if entry_time >= test_start_time:
                                test_trades.append(t)
                        except Exception as e:
                            logger.warning(f"Error parsing trade entry time: {e}")
                            continue
                
                # Calculate performance metrics
                await self.calculate_test_metrics(test, test_trades, trading_status)
                
                # Log progress
                logger.info(f"Test {test_id}: {test.trades_executed} trades, PnL: ${test.total_pnl:.2f}, Win Rate: {test.win_rate:.1%}")
                
                await asyncio.sleep(60)  # Check every minute
                
            except Exception as e:
                logger.error(f"Error monitoring test {test_id}: {e}")
                await asyncio.sleep(10)
        
        # Mark test as completed
        test.end_time = datetime.now(timezone.utc)
        test.status = "completed"
        logger.info(f"Test {test_id} completed")
    
    async def calculate_test_metrics(self, test: StrategyTest, trades: List[Dict[str, Any]], trading_status: Dict[str, Any]):
        """Calculate comprehensive performance metrics for a test"""
        if not trades:
            return
        
        # Basic metrics
        test.trades_executed = len(trades)
        profitable_trades = [t for t in trades if t.get('realized_pnl', 0) > 0]
        test.trades_profitable = len(profitable_trades)
        test.total_pnl = sum(t.get('realized_pnl', 0) for t in trades)
        test.win_rate = test.trades_profitable / max(test.trades_executed, 1)
        
        # Advanced metrics
        if test.trades_executed > 0:
            pnl_values = [t.get('realized_pnl', 0) for t in trades]
            test.avg_profit_per_trade = statistics.mean(pnl_values)
            
            # Calculate drawdown
            cumulative_pnl = []
            running_max = 0
            max_drawdown = 0
            
            for pnl in pnl_values:
                if not cumulative_pnl:
                    cumulative_pnl.append(pnl)
                else:
                    cumulative_pnl.append(cumulative_pnl[-1] + pnl)
                
                running_max = max(running_max, cumulative_pnl[-1])
                drawdown = running_max - cumulative_pnl[-1]
                max_drawdown = max(max_drawdown, drawdown)
            
            test.max_drawdown = max_drawdown
            
            # Calculate Sharpe ratio
            if len(pnl_values) > 1 and statistics.stdev(pnl_values) > 0:
                test.sharpe_ratio = statistics.mean(pnl_values) / statistics.stdev(pnl_values)
            
            # Calculate profit factor
            gross_profit = sum(p for p in pnl_values if p > 0)
            gross_loss = abs(sum(p for p in pnl_values if p < 0))
            test.profit_factor = gross_profit / max(gross_loss, 1)
        
        # Calculate balance utilization and trades per cycle
        cycle_count = trading_status.get('cycle_count', 0)
        test.trades_per_cycle = test.trades_executed / max(cycle_count, 1)
        
        # Balance utilization would require balance data, simplified for now
        test.balance_utilization = min(test.trades_executed * 0.1, 1.0)  # Simplified calculation

async def main():
    """Main function"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Comprehensive Strategy Optimizer")
    parser.add_argument("--auto-apply", action="store_true", help="Automatically apply high priority optimizations")
    parser.add_argument("--test-duration", type=int, default=20, help="Duration for each parameter test in minutes")
    parser.add_argument("--skip-testing", action="store_true", help="Skip parameter testing phase")
    
    args = parser.parse_args()
    
    optimizer = ComprehensiveStrategyOptimizer()
    
    print("🎯 Comprehensive Strategy Optimizer - Full Set of Optimizations")
    print("This tool analyzes performance and applies data-driven optimizations")
    
    # Run comprehensive optimization
    test_duration = 0 if args.skip_testing else args.test_duration
    await optimizer.run_comprehensive_optimization(
        auto_apply=args.auto_apply,
        test_duration_minutes=test_duration
    )

if __name__ == "__main__":
    asyncio.run(main()) 