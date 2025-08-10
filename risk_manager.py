#!/usr/bin/env python3
"""
Risk Manager - Comprehensive Risk Management System
Implements risk controls to ensure profitability and protect capital
"""

import asyncio
import httpx
import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
import logging
import numpy as np

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class RiskManager:
    def __init__(self):
        self.orchestrator_url = "http://localhost:8005"
        self.database_url = "http://localhost:8002"
        self.exchange_url = "http://localhost:8003"
        self.config_url = "http://localhost:8001"
        
        # Risk parameters
        self.max_daily_loss = 0.05  # 5% max daily loss
        self.max_total_loss = 0.15  # 15% max total loss
        self.max_drawdown = 0.10    # 10% max drawdown
        self.max_correlation = 0.7  # Maximum correlation between positions
        self.min_risk_reward = 1.5  # Minimum risk/reward ratio
        self.max_position_size = 0.02  # 2% max position size
        self.max_sector_exposure = 0.3  # 30% max exposure to any sector
        
        # Performance tracking
        self.daily_pnl = 0.0
        self.total_pnl = 0.0
        self.max_equity = 0.0
        self.current_drawdown = 0.0
        self.daily_trades = 0
        self.consecutive_losses = 0
        
    async def get_current_state(self) -> Dict[str, Any]:
        """Get current trading state for risk assessment"""
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
            
            # Get recent trades for PnL calculation
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.database_url}/api/v1/trades?limit=100")
                response.raise_for_status()
                recent_trades = response.json().get('trades', [])
            
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
                'open_trades': open_trades,
                'recent_trades': recent_trades,
                'balances': balances
            }
        except Exception as e:
            logger.error(f"Error getting current state: {e}")
            return {}
    
    def calculate_risk_metrics(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Calculate comprehensive risk metrics"""
        if not data:
            return {}
        
        open_trades = data.get('open_trades', [])
        recent_trades = data.get('recent_trades', [])
        balances = data.get('balances', {})
        
        # Calculate PnL metrics
        total_balance = sum(b['total'] for b in balances.values())
        used_balance = sum(b['used'] for b in balances.values())
        
        # Calculate daily PnL
        today = datetime.now().date()
        today_trades = []
        for t in recent_trades:
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
        
        # Calculate total PnL
        closed_trades = [t for t in recent_trades if t.get('status') == 'CLOSED']
        total_pnl = sum(t.get('realized_pnl', 0) for t in closed_trades)
        
        # Calculate drawdown
        if total_balance > 0:
            current_equity = total_balance + total_pnl
            if current_equity > self.max_equity:
                self.max_equity = current_equity
            self.current_drawdown = (self.max_equity - current_equity) / self.max_equity if self.max_equity > 0 else 0
        
        # Calculate position concentration
        position_sizes = [t.get('position_size', 0) for t in open_trades]
        max_position_size = max(position_sizes) if position_sizes else 0
        total_position_size = sum(position_sizes)
        
        # Calculate sector exposure (by exchange)
        exchange_exposure = {}
        for trade in open_trades:
            exchange = trade.get('exchange', 'unknown')
            position_value = trade.get('position_size', 0) * trade.get('entry_price', 0)
            exchange_exposure[exchange] = exchange_exposure.get(exchange, 0) + position_value
        
        max_sector_exposure = max(exchange_exposure.values()) / total_balance if total_balance > 0 else 0
        
        # Calculate correlation risk
        correlation_risk = self.calculate_correlation_risk(open_trades)
        
        # Calculate consecutive losses
        recent_closed_trades = [t for t in recent_trades if t.get('status') == 'CLOSED'][-10:]  # Last 10 trades
        consecutive_losses = 0
        for trade in reversed(recent_closed_trades):
            if trade.get('realized_pnl', 0) < 0:
                consecutive_losses += 1
            else:
                break
        
        return {
            'daily_pnl': daily_pnl,
            'total_pnl': total_pnl,
            'daily_loss_pct': daily_pnl / total_balance if total_balance > 0 else 0,
            'total_loss_pct': total_pnl / total_balance if total_balance > 0 else 0,
            'current_drawdown': self.current_drawdown,
            'max_position_size_pct': max_position_size / total_balance if total_balance > 0 else 0,
            'total_position_size_pct': total_position_size / total_balance if total_balance > 0 else 0,
            'max_sector_exposure': max_sector_exposure,
            'correlation_risk': correlation_risk,
            'consecutive_losses': consecutive_losses,
            'open_trades_count': len(open_trades),
            'balance_utilization': used_balance / total_balance if total_balance > 0 else 0
        }
    
    def calculate_correlation_risk(self, trades: List[Dict[str, Any]]) -> float:
        """Calculate correlation risk between open positions"""
        if len(trades) < 2:
            return 0.0
        
        # Extract price movements (simplified correlation calculation)
        prices = []
        for trade in trades:
            entry_price = trade.get('entry_price', 0)
            current_price = trade.get('current_price', entry_price)
            if entry_price > 0:
                price_change = (current_price - entry_price) / entry_price
                prices.append(price_change)
        
        if len(prices) < 2:
            return 0.0
        
        # Calculate correlation coefficient
        try:
            prices_array = np.array(prices)
            if len(prices_array) >= 2:
                correlation_matrix = np.corrcoef(prices_array)
                if correlation_matrix.size > 1:
                    correlation = correlation_matrix[0, 1]
                    return abs(correlation) if not np.isnan(correlation) else 0.0
        except Exception as e:
            logger.warning(f"Error calculating correlation: {e}")
        
        return 0.0
    
    def assess_risk_level(self, metrics: Dict[str, Any]) -> Dict[str, Any]:
        """Assess overall risk level and generate alerts"""
        risk_level = "LOW"
        risk_score = 0
        alerts = []
        
        # Daily loss check
        if metrics.get('daily_loss_pct', 0) < -self.max_daily_loss:
            risk_level = "HIGH"
            risk_score += 30
            alerts.append({
                "type": "DAILY_LOSS_LIMIT",
                "severity": "HIGH",
                "message": f"Daily loss limit exceeded: {metrics['daily_loss_pct']:.2%}",
                "action": "STOP_TRADING"
            })
        
        # Total loss check
        if metrics.get('total_loss_pct', 0) < -self.max_total_loss:
            risk_level = "CRITICAL"
            risk_score += 50
            alerts.append({
                "type": "TOTAL_LOSS_LIMIT",
                "severity": "CRITICAL",
                "message": f"Total loss limit exceeded: {metrics['total_loss_pct']:.2%}",
                "action": "EMERGENCY_STOP"
            })
        
        # Drawdown check
        if metrics.get('current_drawdown', 0) > self.max_drawdown:
            risk_level = "HIGH"
            risk_score += 25
            alerts.append({
                "type": "DRAWDOWN_LIMIT",
                "severity": "HIGH",
                "message": f"Maximum drawdown exceeded: {metrics['current_drawdown']:.2%}",
                "action": "REDUCE_POSITIONS"
            })
        
        # Position size check
        if metrics.get('max_position_size_pct', 0) > self.max_position_size:
            risk_level = "MEDIUM"
            risk_score += 15
            alerts.append({
                "type": "POSITION_SIZE_LIMIT",
                "severity": "MEDIUM",
                "message": f"Position size too large: {metrics['max_position_size_pct']:.2%}",
                "action": "REDUCE_POSITION_SIZE"
            })
        
        # Sector exposure check
        if metrics.get('max_sector_exposure', 0) > self.max_sector_exposure:
            risk_level = "MEDIUM"
            risk_score += 15
            alerts.append({
                "type": "SECTOR_EXPOSURE_LIMIT",
                "severity": "MEDIUM",
                "message": f"Sector exposure too high: {metrics['max_sector_exposure']:.2%}",
                "action": "DIVERSIFY_POSITIONS"
            })
        
        # Correlation risk check
        if metrics.get('correlation_risk', 0) > self.max_correlation:
            risk_level = "MEDIUM"
            risk_score += 10
            alerts.append({
                "type": "CORRELATION_RISK",
                "severity": "MEDIUM",
                "message": f"High correlation between positions: {metrics['correlation_risk']:.2f}",
                "action": "DIVERSIFY_POSITIONS"
            })
        
        # Consecutive losses check
        if metrics.get('consecutive_losses', 0) >= 3:
            risk_level = "MEDIUM"
            risk_score += 20
            alerts.append({
                "type": "CONSECUTIVE_LOSSES",
                "severity": "MEDIUM",
                "message": f"Consecutive losses: {metrics['consecutive_losses']}",
                "action": "PAUSE_TRADING"
            })
        
        return {
            "risk_level": risk_level,
            "risk_score": risk_score,
            "alerts": alerts,
            "metrics": metrics
        }
    
    async def should_allow_trade(self, trade_params: Dict[str, Any]) -> Dict[str, Any]:
        """Determine if a trade should be allowed based on risk assessment"""
        # Get current state
        current_state = await self.get_current_state()
        metrics = self.calculate_risk_metrics(current_state)
        risk_assessment = self.assess_risk_level(metrics)
        
        # Check if any critical alerts exist
        critical_alerts = [a for a in risk_assessment['alerts'] if a['severity'] in ['HIGH', 'CRITICAL']]
        
        if critical_alerts:
            return {
                "allowed": False,
                "reason": f"Risk level too high: {critical_alerts[0]['message']}",
                "risk_level": risk_assessment['risk_level'],
                "alerts": critical_alerts
            }
        
        # Check position size limits
        proposed_position_size = trade_params.get('position_size', 0)
        total_balance = sum(b['total'] for b in current_state.get('balances', {}).values())
        
        if total_balance > 0:
            position_size_pct = proposed_position_size / total_balance
            if position_size_pct > self.max_position_size:
                return {
                    "allowed": False,
                    "reason": f"Position size too large: {position_size_pct:.2%}",
                    "risk_level": "MEDIUM",
                    "max_allowed": self.max_position_size
                }
        
        # Check risk/reward ratio
        entry_price = trade_params.get('entry_price', 0)
        stop_loss = trade_params.get('stop_loss', 0)
        take_profit = trade_params.get('take_profit', 0)
        
        if entry_price > 0 and stop_loss > 0 and take_profit > 0:
            risk = abs(entry_price - stop_loss)
            reward = abs(take_profit - entry_price)
            risk_reward_ratio = reward / risk if risk > 0 else 0
            
            if risk_reward_ratio < self.min_risk_reward:
                return {
                    "allowed": False,
                    "reason": f"Risk/reward ratio too low: {risk_reward_ratio:.2f}",
                    "risk_level": "MEDIUM",
                    "min_required": self.min_risk_reward
                }
        
        return {
            "allowed": True,
            "reason": "Trade meets risk criteria",
            "risk_level": risk_assessment['risk_level'],
            "risk_score": risk_assessment['risk_score']
        }
    
    async def get_optimal_position_size(self, available_balance: float, risk_per_trade: float = 0.02) -> float:
        """Calculate optimal position size based on risk management rules"""
        # Kelly Criterion for position sizing
        win_rate = 0.6  # Assume 60% win rate (can be calculated from historical data)
        avg_win = 0.02  # Assume 2% average win
        avg_loss = 0.01  # Assume 1% average loss
        
        kelly_fraction = (win_rate * avg_win - (1 - win_rate) * avg_loss) / avg_win
        kelly_fraction = max(0, min(kelly_fraction, 0.25))  # Cap at 25%
        
        # Apply risk management constraints
        max_position_size = available_balance * kelly_fraction
        risk_based_size = available_balance * risk_per_trade
        
        # Use the smaller of the two
        optimal_size = min(max_position_size, risk_based_size)
        
        # Ensure minimum and maximum constraints
        min_size = 25.0  # Minimum $25 trade
        max_size = available_balance * self.max_position_size
        
        return max(min_size, min(optimal_size, max_size))
    
    def print_risk_report(self, risk_assessment: Dict[str, Any]):
        """Print comprehensive risk report"""
        metrics = risk_assessment['metrics']
        alerts = risk_assessment['alerts']
        
        print("\n" + "="*80)
        print("RISK MANAGEMENT REPORT")
        print("="*80)
        
        print(f"\n📊 RISK METRICS:")
        print(f"   Risk Level: {risk_assessment['risk_level']}")
        print(f"   Risk Score: {risk_assessment['risk_score']}")
        print(f"   Daily PnL: ${metrics['daily_pnl']:.2f}")
        print(f"   Total PnL: ${metrics['total_pnl']:.2f}")
        print(f"   Daily Loss %: {metrics['daily_loss_pct']:.2%}")
        print(f"   Total Loss %: {metrics['total_loss_pct']:.2%}")
        print(f"   Current Drawdown: {metrics['current_drawdown']:.2%}")
        print(f"   Max Position Size: {metrics['max_position_size_pct']:.2%}")
        print(f"   Total Position Size: {metrics['total_position_size_pct']:.2%}")
        print(f"   Sector Exposure: {metrics['max_sector_exposure']:.2%}")
        print(f"   Correlation Risk: {metrics['correlation_risk']:.2f}")
        print(f"   Consecutive Losses: {metrics['consecutive_losses']}")
        print(f"   Open Trades: {metrics['open_trades_count']}")
        print(f"   Balance Utilization: {metrics['balance_utilization']:.2%}")
        
        if alerts:
            print(f"\n🚨 RISK ALERTS:")
            for i, alert in enumerate(alerts, 1):
                severity_icon = "🔴" if alert['severity'] == 'CRITICAL' else "🟠" if alert['severity'] == 'HIGH' else "🟡"
                print(f"   {i}. {severity_icon} {alert['type']}: {alert['message']}")
                print(f"      Action: {alert['action']}")
                print()
        else:
            print(f"\n✅ No risk alerts - trading conditions are safe")
        
        print("="*80)

async def main():
    """Main function to demonstrate risk management"""
    risk_manager = RiskManager()
    
    print("🛡️ Risk Manager - Comprehensive Risk Management System")
    
    # Get current state and assess risk
    current_state = await risk_manager.get_current_state()
    metrics = risk_manager.calculate_risk_metrics(current_state)
    risk_assessment = risk_manager.assess_risk_level(metrics)
    
    # Print risk report
    risk_manager.print_risk_report(risk_assessment)
    
    # Test trade approval
    test_trade = {
        "position_size": 100.0,
        "entry_price": 50000.0,
        "stop_loss": 49500.0,
        "take_profit": 51000.0
    }
    
    trade_decision = await risk_manager.should_allow_trade(test_trade)
    print(f"\n🧪 Test Trade Decision:")
    print(f"   Allowed: {trade_decision['allowed']}")
    print(f"   Reason: {trade_decision['reason']}")
    print(f"   Risk Level: {trade_decision.get('risk_level', 'N/A')}")
    
    # Calculate optimal position size
    optimal_size = await risk_manager.get_optimal_position_size(1000.0)
    print(f"\n📏 Optimal Position Size for $1000 balance: ${optimal_size:.2f}")

if __name__ == "__main__":
    asyncio.run(main()) 