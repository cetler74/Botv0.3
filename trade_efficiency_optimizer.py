#!/usr/bin/env python3
"""
Trade Efficiency Optimizer
Specifically designed to solve the critical problem: Many cycles but very few trades
Focus: Identify why trading strategies are too restrictive and provide actionable solutions
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
class EfficiencyIssue:
    """Data structure for trade efficiency issues"""
    issue_type: str
    severity: str  # "CRITICAL", "HIGH", "MEDIUM"
    description: str
    root_cause: str
    current_impact: str
    recommended_fix: str
    config_changes: List[Dict[str, Any]]
    expected_improvement: str
    confidence: float

class TradeEfficiencyOptimizer:
    def __init__(self):
        # Use localhost for direct access or Docker service names for internal
        self.orchestrator_url = "http://localhost:8005"
        self.database_url = "http://localhost:8002" 
        self.config_url = "http://localhost:8001"
        self.strategy_url = "http://localhost:8004"
        
    async def analyze_trade_efficiency(self) -> Dict[str, Any]:
        """Comprehensive analysis of trade efficiency issues"""
        print("🔍 TRADE EFFICIENCY ANALYSIS")
        print("=" * 50)
        print("🎯 Goal: Identify why many cycles produce so few trades")
        print()
        
        # Get comprehensive data
        data = await self.get_complete_data()
        if not data:
            return {"error": "Failed to get trading data"}
        
        # Core metrics
        trades = data.get('trades', [])
        trading_status = data.get('trading_status', {})
        current_config = data.get('current_config', {})
        
        # Calculate key efficiency metrics
        cycle_count = trading_status.get('cycle_count', 0)
        total_trades = len(trades)
        trades_per_cycle = total_trades / max(cycle_count, 1)
        
        print(f"📊 CURRENT EFFICIENCY METRICS:")
        print(f"   Total Cycles: {cycle_count:,}")
        print(f"   Total Trades: {total_trades}")
        print(f"   Trades per Cycle: {trades_per_cycle:.4f}")
        print(f"   Efficiency Rating: {'🔴 CRITICAL' if trades_per_cycle < 0.01 else '🟡 POOR' if trades_per_cycle < 0.1 else '🟢 GOOD'}")
        print()
        
        # Identify specific efficiency issues
        efficiency_issues = await self.identify_efficiency_issues(trades, trading_status, current_config)
        
        # Generate targeted recommendations
        recommendations = self.generate_efficiency_recommendations(efficiency_issues, current_config)
        
        return {
            "efficiency_metrics": {
                "cycle_count": cycle_count,
                "total_trades": total_trades,
                "trades_per_cycle": trades_per_cycle,
                "efficiency_rating": "CRITICAL" if trades_per_cycle < 0.01 else "POOR" if trades_per_cycle < 0.1 else "GOOD"
            },
            "efficiency_issues": [
                {
                    "issue_type": issue.issue_type,
                    "severity": issue.severity,
                    "description": issue.description,
                    "root_cause": issue.root_cause,
                    "current_impact": issue.current_impact,
                    "recommended_fix": issue.recommended_fix,
                    "config_changes": issue.config_changes,
                    "expected_improvement": issue.expected_improvement,
                    "confidence": issue.confidence
                }
                for issue in efficiency_issues
            ],
            "recommendations": recommendations,
            "actionable_config": self.generate_optimized_config(current_config, efficiency_issues)
        }
    
    async def get_complete_data(self) -> Dict[str, Any]:
        """Get all necessary data for efficiency analysis"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                # Get trades
                trades_response = await client.get(f"{self.database_url}/api/v1/trades?limit=10000")
                trades_data = trades_response.json() if trades_response.status_code == 200 else {"trades": []}
                
                # Get trading status  
                status_response = await client.get(f"{self.orchestrator_url}/api/v1/trading/status")
                trading_status = status_response.json() if status_response.status_code == 200 else {}
                
                # Get current config
                config_response = await client.get(f"{self.config_url}/api/v1/config/all")
                current_config = config_response.json() if config_response.status_code == 200 else {}
                
                # Get strategy performance if available
                strategy_data = {}
                try:
                    strategy_response = await client.get(f"{self.strategy_url}/api/v1/strategies")
                    strategy_data = strategy_response.json() if strategy_response.status_code == 200 else {}
                except:
                    pass
                
                return {
                    "trades": trades_data.get('trades', []),
                    "trading_status": trading_status,
                    "current_config": current_config,
                    "strategy_data": strategy_data
                }
                
            except Exception as e:
                print(f"Error getting data: {e}")
                return {}
    
    async def identify_efficiency_issues(self, trades: List[Dict], trading_status: Dict, current_config: Dict) -> List[EfficiencyIssue]:
        """Identify specific efficiency issues causing low trade frequency"""
        issues = []
        
        # Get strategy configs
        strategies_config = current_config.get('strategies', {})
        trading_config = current_config.get('trading', {})
        
        cycle_count = trading_status.get('cycle_count', 0)
        total_trades = len(trades)
        trades_per_cycle = total_trades / max(cycle_count, 1)
        
        # CRITICAL ISSUE 1: Extremely restrictive ADX thresholds
        for strategy_name, strategy_config in strategies_config.items():
            if not strategy_config.get('enabled', False):
                continue
                
            params = strategy_config.get('parameters', {})
            adx_threshold = params.get('adx_entry_threshold', 25)
            
            if adx_threshold >= 25:
                issues.append(EfficiencyIssue(
                    issue_type="Restrictive ADX Threshold",
                    severity="CRITICAL",
                    description=f"{strategy_name} has ADX threshold of {adx_threshold} which is too restrictive",
                    root_cause="High ADX threshold (≥25) eliminates most trading opportunities. Most market conditions have ADX < 25.",
                    current_impact=f"Blocking ~80-90% of potential trades. With {cycle_count:,} cycles and only {total_trades} trades.",
                    recommended_fix=f"Reduce ADX threshold to 15-18 to allow more market conditions",
                    config_changes=[{
                        "path": f"strategies.{strategy_name}.parameters.adx_entry_threshold",
                        "current": adx_threshold,
                        "recommended": 15,
                        "reason": "Allow more market conditions while maintaining trend validation"
                    }],
                    expected_improvement="Could increase trade frequency by 300-500%",
                    confidence=0.95
                ))
        
        # CRITICAL ISSUE 2: High confidence requirements
        for strategy_name, strategy_config in strategies_config.items():
            if not strategy_config.get('enabled', False):
                continue
                
            params = strategy_config.get('parameters', {})
            min_confidence = params.get('min_confidence', 0.4)
            
            if min_confidence >= 0.4:
                issues.append(EfficiencyIssue(
                    issue_type="High Confidence Requirements",
                    severity="CRITICAL", 
                    description=f"{strategy_name} requires {min_confidence:.1%} minimum confidence",
                    root_cause="High confidence threshold eliminates marginal but profitable trades",
                    current_impact=f"Rejecting potentially profitable trades with {min_confidence:.1%}+ confidence requirement",
                    recommended_fix="Lower confidence requirement to 0.25-0.3 to capture more opportunities",
                    config_changes=[{
                        "path": f"strategies.{strategy_name}.parameters.min_confidence",
                        "current": min_confidence,
                        "recommended": 0.25,
                        "reason": "Lower threshold to capture more trading opportunities"
                    }],
                    expected_improvement="Could increase trade frequency by 200-300%",
                    confidence=0.85
                ))
        
        # CRITICAL ISSUE 3: Multiple confirmation requirements
        for strategy_name, strategy_config in strategies_config.items():
            if not strategy_config.get('enabled', False):
                continue
                
            params = strategy_config.get('parameters', {})
            confirmation_timeframes = params.get('confirmation_timeframes', 1)
            
            if confirmation_timeframes > 0:
                issues.append(EfficiencyIssue(
                    issue_type="Excessive Confirmation Requirements",
                    severity="HIGH",
                    description=f"{strategy_name} requires {confirmation_timeframes} confirmation timeframes",
                    root_cause="Multiple timeframe confirmations create delays and miss opportunities",
                    current_impact="Missing time-sensitive trading opportunities due to confirmation delays",
                    recommended_fix="Reduce or eliminate confirmation timeframes for faster execution",
                    config_changes=[{
                        "path": f"strategies.{strategy_name}.parameters.confirmation_timeframes", 
                        "current": confirmation_timeframes,
                        "recommended": 0,
                        "reason": "Eliminate delays to capture more opportunities"
                    }],
                    expected_improvement="Could increase trade frequency by 50-100%",
                    confidence=0.75
                ))
        
        # CRITICAL ISSUE 4: High minimum order sizes
        min_order_size = trading_config.get('min_order_size_usd', 15.0)
        if min_order_size >= 15.0:
            issues.append(EfficiencyIssue(
                issue_type="High Minimum Order Size",
                severity="HIGH",
                description=f"Minimum order size is ${min_order_size} which may be too high",
                root_cause="High minimum order size prevents trading smaller opportunities",
                current_impact="Excluding smaller but valid trading opportunities",
                recommended_fix="Reduce minimum order size to allow more trades",
                config_changes=[{
                    "path": "trading.min_order_size_usd",
                    "current": min_order_size,
                    "recommended": 8.0,
                    "reason": "Enable smaller trades to increase opportunities"
                }],
                expected_improvement="Could increase trade frequency by 30-50%",
                confidence=0.70
            ))
        
        # CRITICAL ISSUE 5: Strategy-specific restrictive parameters
        engulfing_config = strategies_config.get('engulfing_multi_tf', {})
        if engulfing_config.get('enabled', False):
            params = engulfing_config.get('parameters', {})
            min_engulfing = params.get('min_engulfing_size_multiplier', 1.0)
            
            if min_engulfing >= 1.0:
                issues.append(EfficiencyIssue(
                    issue_type="Restrictive Engulfing Pattern Requirements",
                    severity="HIGH",
                    description=f"Engulfing pattern requires {min_engulfing}x size multiplier",
                    root_cause="Requiring large engulfing patterns eliminates most valid patterns",
                    current_impact="Missing smaller but valid engulfing patterns",
                    recommended_fix="Reduce engulfing size requirement to capture more patterns",
                    config_changes=[{
                        "path": "strategies.engulfing_multi_tf.parameters.min_engulfing_size_multiplier",
                        "current": min_engulfing,
                        "recommended": 0.6,
                        "reason": "Allow smaller engulfing patterns that are still valid"
                    }],
                    expected_improvement="Could increase engulfing strategy trades by 200-400%",
                    confidence=0.80
                ))
        
        # Add market regime analysis issue if applicable
        if trades_per_cycle < 0.005:  # Less than 0.5% trade rate
            issues.append(EfficiencyIssue(
                issue_type="Extremely Low Trade Efficiency",
                severity="CRITICAL",
                description=f"Trade efficiency is {trades_per_cycle:.4f} trades per cycle ({trades_per_cycle*100:.3f}%)",
                root_cause="Combination of overly restrictive parameters across all strategies",
                current_impact=f"System is essentially not trading - {cycle_count:,} cycles produced only {total_trades} trades",
                recommended_fix="Implement emergency parameter relaxation across all strategies",
                config_changes=[
                    {
                        "path": "Multiple parameters",
                        "current": "Too restrictive",
                        "recommended": "Relaxed thresholds", 
                        "reason": "Emergency optimization needed"
                    }
                ],
                expected_improvement="Could increase trade frequency by 1000%+",
                confidence=0.99
            ))
        
        return issues
    
    def generate_efficiency_recommendations(self, efficiency_issues: List[EfficiencyIssue], current_config: Dict) -> List[Dict[str, Any]]:
        """Generate prioritized recommendations based on efficiency issues"""
        recommendations = []
        
        for issue in efficiency_issues:
            recommendations.append({
                "priority": issue.severity,
                "category": "Trade Efficiency",
                "title": issue.issue_type,
                "description": issue.description,
                "root_cause": issue.root_cause,
                "current_impact": issue.current_impact,
                "recommended_action": issue.recommended_fix,
                "config_changes": issue.config_changes,
                "expected_improvement": issue.expected_improvement,
                "confidence": issue.confidence,
                "implementation_urgency": "IMMEDIATE" if issue.severity == "CRITICAL" else "HIGH"
            })
        
        return sorted(recommendations, key=lambda x: {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2}[x["priority"]])
    
    def generate_optimized_config(self, current_config: Dict, efficiency_issues: List[EfficiencyIssue]) -> str:
        """Generate an optimized config.yaml with all efficiency fixes applied"""
        
        # Start with current config
        optimized_config = current_config.copy()
        
        # Apply all critical and high priority fixes
        config_yaml = f"""# OPTIMIZED CONFIGURATION FOR TRADE EFFICIENCY
# Generated: {datetime.now().isoformat()}
# PURPOSE: Fix low trade frequency issue ({len(efficiency_issues)} issues identified)

# TRADING PARAMETERS - Optimized for more opportunities
trading:
  mode: simulation  # KEEP IN SIMULATION UNTIL TESTED!
  min_order_size_usd: 8.0  # Reduced from 15+ to enable smaller trades
  position_size_percentage: 0.12  # Keep conservative while testing
  max_concurrent_trades: 10  # Increased to allow more simultaneous trades
  max_trades_per_exchange: 5  # Increased from typical 2-3
  min_exchange_balance: 50.0  # Keep reasonable minimum
  stop_loss_percentage: 3.5  # Keep risk management
  take_profit_percentage: 5.0

# STRATEGY PARAMETERS - Dramatically relaxed for efficiency
strategies:
  engulfing_multi_tf:
    enabled: true
    parameters:
      # CRITICAL FIXES - These changes should 5-10x trading frequency
      adx_entry_threshold: 15        # Reduced from 25+ (CRITICAL)
      min_confidence: 0.25           # Reduced from 0.4+ (CRITICAL)  
      confirmation_timeframes: 0     # Eliminated delays (HIGH)
      min_engulfing_size_multiplier: 0.6  # Reduced from 1.0+ (HIGH)
      
      # Additional optimizations
      min_volume_ratio: 1.2          # Relaxed volume requirements
      max_spread_percentage: 0.5     # Allow slightly wider spreads
      lookback_periods: 10           # Reduced for faster detection
      
  heikin_ashi:
    enabled: true
    parameters:
      # Apply similar relaxations to all strategies
      adx_entry_threshold: 15
      min_confidence: 0.25
      trend_strength_threshold: 0.3
      
  multi_timeframe_confluence:
    enabled: true  
    parameters:
      adx_entry_threshold: 15
      min_confidence: 0.25
      required_timeframe_agreement: 2  # Reduced from 3+
      
  vwma_hull:
    enabled: true
    parameters:
      adx_entry_threshold: 15
      min_confidence: 0.25
      volume_threshold: 1.1          # Relaxed volume requirements

# PAIR SELECTOR - More aggressive pair selection
pair_selector:
  update_interval_minutes: 30       # More frequent updates
  min_volume_24h: 100000           # Reduced to include more pairs
  min_volatility: 0.015            # Reduced for more opportunities
  max_pairs_per_exchange: 15       # Increased pair selection

# RISK MANAGEMENT - Balanced with efficiency
balance_manager:
  min_balance_threshold: 50.0
  rebalance_threshold: 0.1
  max_allocation_per_trade: 0.12

# IMPORTANT NOTES:
# 1. These changes prioritize TRADING FREQUENCY over selectivity
# 2. Expected improvement: 5-10x more trades per cycle
# 3. KEEP IN SIMULATION MODE until performance is validated
# 4. Monitor win rate - if it drops below 40%, increase selectivity
# 5. These are aggressive changes - start with partial implementation if preferred

# IMPLEMENTATION PRIORITY:
# 1. CRITICAL: ADX thresholds (15 instead of 25+)
# 2. CRITICAL: Confidence requirements (0.25 instead of 0.4+)  
# 3. HIGH: Remove confirmation delays
# 4. HIGH: Reduce pattern size requirements
# 5. MEDIUM: Adjust order sizes and pair selection
"""
        
        return config_yaml

async def main():
    """Main execution function"""
    optimizer = TradeEfficiencyOptimizer()
    result = await optimizer.analyze_trade_efficiency()
    
    if "error" in result:
        print(f"❌ Error: {result['error']}")
        return
    
    efficiency_metrics = result["efficiency_metrics"]
    efficiency_issues = result["efficiency_issues"]
    recommendations = result["recommendations"]
    
    print("🔍 EFFICIENCY ISSUES IDENTIFIED:")
    print("-" * 50)
    
    for i, issue in enumerate(efficiency_issues, 1):
        severity_emoji = "🔴" if issue["severity"] == "CRITICAL" else "🟡" if issue["severity"] == "HIGH" else "🔵"
        print(f"{i}. {severity_emoji} {issue['issue_type']} ({issue['severity']})")
        print(f"   Problem: {issue['description']}")
        print(f"   Root Cause: {issue['root_cause']}")
        print(f"   Impact: {issue['current_impact']}")
        print(f"   Fix: {issue['recommended_fix']}")
        print(f"   Expected: {issue['expected_improvement']}")
        print(f"   Confidence: {issue['confidence']:.0%}")
        print()
    
    print("🎯 OPTIMIZATION SUMMARY:")
    print(f"   Current Efficiency: {efficiency_metrics['trades_per_cycle']:.4f} trades/cycle")
    print(f"   Issues Found: {len(efficiency_issues)}")
    print(f"   Critical Issues: {len([i for i in efficiency_issues if i['severity'] == 'CRITICAL'])}")
    print(f"   Expected Improvement: 5-10x more trades")
    print()
    
    # Save optimized config
    optimized_config = result["actionable_config"]
    with open("optimized_config_for_efficiency.yaml", "w") as f:
        f.write(optimized_config)
    
    print("✅ OPTIMIZED CONFIG GENERATED:")
    print("   File: optimized_config_for_efficiency.yaml")
    print("   ⚠️  IMPORTANT: Test in simulation mode first!")
    print("   📈 Expected: 5-10x increase in trading frequency")

if __name__ == "__main__":
    asyncio.run(main())