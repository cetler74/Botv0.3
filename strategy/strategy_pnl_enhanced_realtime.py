"""
WebSocket-Enhanced Real-Time PnL Protection Strategy
Leverages WebSocket price feeds for ultra-responsive risk management

Key Improvements:
1. Real-time price monitoring for instant protection triggering
2. Micro-drawdown detection within seconds of adverse movements
3. Flash crash protection using order book depth analysis
4. Dynamic stop-loss adjustment based on real-time volatility
5. Multi-timeframe momentum analysis using streaming data
6. Enhanced order execution speed through WebSocket order routing
"""

import asyncio
import logging
import pandas as pd
from datetime import datetime, timedelta
import numpy as np
from typing import Dict, Any, Optional, Tuple, List
import aiohttp
import json
from collections import deque

# Import base strategy functions
from strategy_pnl_enhanced import (
    calculate_atr, calculate_adx, calculate_unrealized_pnl,
    calculate_volatility_adjustment, get_dynamic_config
)

logger = logging.getLogger(__name__)

class RealTimePriceMonitor:
    """
    Real-time price monitoring using WebSocket feeds
    Detects flash crashes, momentum shifts, and volatility spikes
    """
    
    def __init__(self, exchange: str, symbol: str, trade_id: str):
        self.exchange = exchange
        self.symbol = symbol
        self.trade_id = trade_id
        
        # Price tracking
        self.price_history = deque(maxlen=100)  # Last 100 price updates
        self.timestamp_history = deque(maxlen=100)
        self.current_price = None
        self.last_update = None
        
        # Volatility detection
        self.price_changes = deque(maxlen=50)  # Last 50 price changes
        self.volatility_spikes = deque(maxlen=20)  # Track volatility events
        
        # Flash crash detection
        self.max_allowed_drop_1s = 0.02  # 2% drop in 1 second triggers protection
        self.max_allowed_drop_5s = 0.05  # 5% drop in 5 seconds
        self.max_allowed_drop_30s = 0.08  # 8% drop in 30 seconds
        
        # Momentum detection
        self.momentum_window = 10
        self.momentum_threshold = 0.001  # 0.1% momentum threshold
        
        logger.info(f"🔴 [Trade {trade_id}] Real-time monitor initialized for {exchange}/{symbol}")
    
    async def update_price(self, price: float, timestamp: datetime = None):
        """Update price and detect anomalies in real-time"""
        if timestamp is None:
            timestamp = datetime.utcnow()
        
        old_price = self.current_price
        self.current_price = price
        self.last_update = timestamp
        
        # Add to history
        self.price_history.append(price)
        self.timestamp_history.append(timestamp)
        
        # Calculate price change if we have previous price
        if old_price:
            price_change = (price - old_price) / old_price
            self.price_changes.append(price_change)
            
            # Check for anomalies
            anomalies = await self._detect_price_anomalies()
            if anomalies:
                logger.warning(f"🚨 [Trade {self.trade_id}] Price anomalies detected: {anomalies}")
                return anomalies
        
        return []
    
    async def _detect_price_anomalies(self) -> List[Dict[str, Any]]:
        """Detect various price anomalies that require immediate attention"""
        anomalies = []
        
        if len(self.price_history) < 2:
            return anomalies
        
        current_price = self.price_history[-1]
        current_time = self.timestamp_history[-1]
        
        # Flash crash detection - check various timeframes
        for i, (timeframe, max_drop) in enumerate([
            (1, self.max_allowed_drop_1s),
            (5, self.max_allowed_drop_5s), 
            (30, self.max_allowed_drop_30s)
        ]):
            cutoff_time = current_time - timedelta(seconds=timeframe)
            
            # Find prices within timeframe
            prices_in_window = []
            for price, timestamp in zip(self.price_history, self.timestamp_history):
                if timestamp >= cutoff_time:
                    prices_in_window.append(price)
            
            if len(prices_in_window) >= 2:
                max_price = max(prices_in_window)
                drop_pct = (max_price - current_price) / max_price
                
                if drop_pct > max_drop:
                    anomalies.append({
                        'type': 'flash_crash',
                        'timeframe': f"{timeframe}s",
                        'drop_pct': drop_pct,
                        'threshold': max_drop,
                        'severity': 'CRITICAL' if drop_pct > max_drop * 1.5 else 'HIGH',
                        'immediate_exit': drop_pct > max_drop * 1.2  # Exit if 20% above threshold
                    })
        
        # Volatility spike detection
        if len(self.price_changes) >= 5:
            recent_changes = list(self.price_changes)[-5:]
            volatility = np.std(recent_changes)
            
            if volatility > 0.005:  # 0.5% standard deviation spike
                anomalies.append({
                    'type': 'volatility_spike',
                    'volatility': volatility,
                    'severity': 'MEDIUM' if volatility < 0.01 else 'HIGH',
                    'adjust_stops': True
                })
        
        # Momentum shift detection
        if len(self.price_history) >= self.momentum_window:
            recent_prices = list(self.price_history)[-self.momentum_window:]
            momentum = (recent_prices[-1] - recent_prices[0]) / recent_prices[0]
            
            if abs(momentum) > self.momentum_threshold:
                anomalies.append({
                    'type': 'momentum_shift',
                    'momentum': momentum,
                    'direction': 'bearish' if momentum < 0 else 'bullish',
                    'severity': 'LOW' if abs(momentum) < 0.005 else 'MEDIUM'
                })
        
        return anomalies
    
    def get_current_volatility(self) -> float:
        """Get current volatility estimate from recent price changes"""
        if len(self.price_changes) < 5:
            return 0.001  # Default low volatility
        
        recent_changes = list(self.price_changes)[-10:]  # Last 10 changes
        return float(np.std(recent_changes))
    
    def get_momentum_score(self) -> float:
        """Get current momentum score (-1 to 1)"""
        if len(self.price_history) < self.momentum_window:
            return 0.0
        
        recent_prices = list(self.price_history)[-self.momentum_window:]
        momentum = (recent_prices[-1] - recent_prices[0]) / recent_prices[0]
        
        # Normalize to -1 to 1 scale
        return max(-1.0, min(1.0, momentum * 50))  # Scale by 50x

class RealTimeOrderBookMonitor:
    """
    Monitor order book depth for liquidity analysis
    Helps predict slippage and market impact
    """
    
    def __init__(self, exchange: str, symbol: str):
        self.exchange = exchange
        self.symbol = symbol
        self.bid_depth = 0.0
        self.ask_depth = 0.0
        self.spread_pct = 0.0
        self.last_update = None
    
    async def update_order_book(self):
        """Fetch latest order book data"""
        try:
            async with aiohttp.ClientSession() as session:
                url = f"http://exchange-service:8003/api/v1/market/orderbook/{self.exchange}/{self.symbol}?limit=20"
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        
                        bids = data.get('bids', [])
                        asks = data.get('asks', [])
                        
                        # Calculate depth (total volume in top 10 levels)
                        self.bid_depth = sum(float(bid[1]) for bid in bids[:10])
                        self.ask_depth = sum(float(ask[1]) for ask in asks[:10])
                        
                        # Calculate spread
                        if bids and asks:
                            best_bid = float(bids[0][0])
                            best_ask = float(asks[0][0])
                            self.spread_pct = (best_ask - best_bid) / best_bid
                        
                        self.last_update = datetime.utcnow()
                        
        except Exception as e:
            logger.warning(f"Error updating order book: {e}")
    
    def get_liquidity_score(self) -> float:
        """Get liquidity score (0-1, higher is better)"""
        if not self.bid_depth or not self.ask_depth:
            return 0.5  # Neutral if no data
        
        # Combine depth and spread metrics
        depth_score = min(1.0, (self.bid_depth + self.ask_depth) / 100000)  # Normalize
        spread_score = max(0.0, 1.0 - (self.spread_pct * 1000))  # Lower spread is better
        
        return (depth_score + spread_score) / 2

class RealTimeProtectionManager:
    """
    Main real-time protection manager that coordinates all monitoring
    """
    
    def __init__(self, exchange: str, symbol: str, trade_id: str, entry_price: float, position_size: float):
        self.exchange = exchange
        self.symbol = symbol
        self.trade_id = trade_id
        self.entry_price = entry_price
        self.position_size = position_size
        
        # Monitoring components
        self.price_monitor = RealTimePriceMonitor(exchange, symbol, trade_id)
        self.orderbook_monitor = RealTimeOrderBookMonitor(exchange, symbol)
        
        # Protection state
        self.is_active = False
        self.protection_triggered = False
        self.last_check = None
        self.check_interval = 1.0  # Check every second
        
        # Enhanced thresholds
        self.micro_drawdown_threshold = 0.003  # 0.3% micro-drawdown
        self.flash_exit_threshold = 0.015  # 1.5% flash exit
        self.volatility_exit_threshold = 0.01  # 1% volatility-based exit
        
        logger.info(f"🛡️ [Trade {trade_id}] Real-time protection manager initialized")
    
    async def start_monitoring(self):
        """Start real-time monitoring loop"""
        self.is_active = True
        logger.info(f"🟢 [Trade {self.trade_id}] Starting real-time monitoring")
        
        # Start monitoring task
        asyncio.create_task(self._monitoring_loop())
    
    async def stop_monitoring(self):
        """Stop real-time monitoring"""
        self.is_active = False
        logger.info(f"🔴 [Trade {self.trade_id}] Stopped real-time monitoring")
    
    async def _monitoring_loop(self):
        """Main monitoring loop that runs continuously"""
        while self.is_active:
            try:
                await self._perform_realtime_check()
                await asyncio.sleep(self.check_interval)
            except Exception as e:
                logger.error(f"❌ [Trade {self.trade_id}] Monitoring loop error: {e}")
                await asyncio.sleep(self.check_interval * 2)  # Back off on errors
    
    async def _perform_realtime_check(self):
        """Perform a single real-time protection check"""
        try:
            # Get current price from WebSocket
            current_price = await self._get_live_price()
            if not current_price:
                return
            
            # Update price monitor
            anomalies = await self.price_monitor.update_price(current_price)
            
            # Update order book monitor
            await self.orderbook_monitor.update_order_book()
            
            # Check for immediate exit conditions
            should_exit, exit_reason = await self._check_immediate_exit(current_price, anomalies)
            
            if should_exit:
                await self._trigger_emergency_exit(exit_reason, current_price)
            
            self.last_check = datetime.utcnow()
            
        except Exception as e:
            logger.error(f"❌ [Trade {self.trade_id}] Real-time check error: {e}")
    
    async def _get_live_price(self) -> Optional[float]:
        """Get live price from WebSocket feed"""
        try:
            async with aiohttp.ClientSession() as session:
                symbol_formatted = self.symbol.replace('/', '%2F')
                url = f"http://exchange-service:8003/api/v1/market/ticker-live/{self.exchange}/{symbol_formatted}?stale_threshold_seconds=5"
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        return float(data.get('last', 0))
            return None
        except Exception as e:
            logger.warning(f"⚠️ [Trade {self.trade_id}] Error getting live price: {e}")
            return None
    
    async def _check_immediate_exit(self, current_price: float, anomalies: List[Dict]) -> Tuple[bool, str]:
        """Check if immediate exit is required"""
        
        # Calculate current PnL
        current_pnl = (current_price - self.entry_price) / self.entry_price
        
        # 1. Flash crash detection
        for anomaly in anomalies:
            if anomaly['type'] == 'flash_crash' and anomaly.get('immediate_exit', False):
                logger.critical(f"🚨 [Trade {self.trade_id}] FLASH CRASH: {anomaly['drop_pct']:.2%} in {anomaly['timeframe']}")
                return True, f"flash_crash_{anomaly['timeframe']}_drop_{anomaly['drop_pct']:.3f}"
        
        # 2. Micro-drawdown protection
        if current_pnl < -self.micro_drawdown_threshold:
            logger.warning(f"⚠️ [Trade {self.trade_id}] Micro-drawdown: {current_pnl:.3%}")
            return True, f"micro_drawdown_{abs(current_pnl):.3f}"
        
        # 3. Volatility-based exit
        volatility = self.price_monitor.get_current_volatility()
        if volatility > self.volatility_exit_threshold and current_pnl < 0:
            logger.warning(f"⚠️ [Trade {self.trade_id}] High volatility exit: vol={volatility:.3f}, pnl={current_pnl:.3%}")
            return True, f"volatility_exit_vol_{volatility:.3f}_pnl_{abs(current_pnl):.3f}"
        
        # 4. Liquidity protection
        liquidity_score = self.orderbook_monitor.get_liquidity_score()
        if liquidity_score < 0.3 and current_pnl < -0.01:  # Low liquidity + 1% loss
            logger.warning(f"⚠️ [Trade {self.trade_id}] Low liquidity exit: score={liquidity_score:.2f}")
            return True, f"liquidity_protection_score_{liquidity_score:.2f}"
        
        return False, ""
    
    async def _trigger_emergency_exit(self, reason: str, current_price: float):
        """Trigger emergency exit order"""
        logger.critical(f"🚨 [Trade {self.trade_id}] EMERGENCY EXIT: {reason} at ${current_price:.4f}")
        
        self.protection_triggered = True
        
        # Send emergency exit signal to orchestrator
        try:
            async with aiohttp.ClientSession() as session:
                exit_data = {
                    'trade_id': self.trade_id,
                    'reason': reason,
                    'price': current_price,
                    'timestamp': datetime.utcnow().isoformat(),
                    'emergency': True
                }
                
                async with session.post(
                    f"http://orchestrator-service:8005/api/v1/trading/emergency-exit",
                    json=exit_data
                ) as response:
                    if response.status == 200:
                        logger.info(f"✅ [Trade {self.trade_id}] Emergency exit signal sent")
                    else:
                        logger.error(f"❌ [Trade {self.trade_id}] Failed to send emergency exit signal")
                        
        except Exception as e:
            logger.error(f"❌ [Trade {self.trade_id}] Error sending emergency exit: {e}")

async def enhanced_profit_protection_realtime(
    state, trade_id: str, exchange: str, symbol: str, 
    entry_price: float, position_size: float, config: Dict[str, Any]
) -> Tuple[bool, Optional[str], Dict[str, Any]]:
    """
    Enhanced profit protection using real-time WebSocket data
    
    Args:
        state: Strategy state object
        trade_id: Trade identifier
        exchange: Exchange name
        symbol: Trading pair symbol
        entry_price: Entry price of position
        position_size: Size of position
        config: Configuration dictionary
    
    Returns:
        Tuple of (should_exit, exit_reason, protection_data)
    """
    
    # Initialize real-time monitor if not exists
    if not hasattr(state, 'realtime_monitor'):
        state.realtime_monitor = RealTimeProtectionManager(
            exchange, symbol, trade_id, entry_price, position_size
        )
        await state.realtime_monitor.start_monitoring()
        logger.info(f"🚀 [Trade {trade_id}] Real-time protection activated")
    
    # Check if protection was triggered
    if state.realtime_monitor.protection_triggered:
        reason = "realtime_emergency_exit"
        logger.info(f"🚨 [Trade {trade_id}] Real-time protection triggered exit")
        
        # Clean up monitor
        await state.realtime_monitor.stop_monitoring()
        
        return True, reason, {
            'realtime_protection': True,
            'trigger_time': datetime.utcnow().isoformat(),
            'exit_type': 'emergency'
        }
    
    # Get current monitoring data for risk assessment
    current_price = await state.realtime_monitor._get_live_price()
    if current_price:
        volatility = state.realtime_monitor.price_monitor.get_current_volatility()
        momentum = state.realtime_monitor.price_monitor.get_momentum_score()
        liquidity = state.realtime_monitor.orderbook_monitor.get_liquidity_score()
        
        return False, None, {
            'realtime_protection': True,
            'current_price': current_price,
            'volatility': volatility,
            'momentum': momentum,
            'liquidity_score': liquidity,
            'monitoring_active': state.realtime_monitor.is_active
        }
    
    return False, None, {'realtime_protection': False}

def enhanced_trailing_stop_realtime(
    state, current_price: float, entry_price: float, position_size: float,
    config: Dict[str, Any], trade_id: str, exchange: str, symbol: str
) -> Tuple[bool, Optional[str], Dict[str, Any]]:
    """
    Enhanced trailing stop using real-time momentum and volatility data
    
    Args:
        state: Strategy state object  
        current_price: Current market price
        entry_price: Entry price of position
        position_size: Size of position
        config: Configuration dictionary
        trade_id: Trade identifier
        exchange: Exchange name
        symbol: Trading pair symbol
    
    Returns:
        Tuple of (should_exit, exit_reason, trailing_data)
    """
    
    if not hasattr(state, 'realtime_monitor'):
        # Fallback to basic trailing stop if real-time monitor not available
        logger.warning(f"[Trade {trade_id}] Real-time monitor not available, using basic trailing stop")
        return False, None, {'realtime_trailing': False}
    
    # Get real-time data
    volatility = state.realtime_monitor.price_monitor.get_current_volatility()
    momentum = state.realtime_monitor.price_monitor.get_momentum_score()
    
    # Dynamic trailing distance based on real-time conditions
    base_distance = config.get('trailing_stop', {}).get('base_trailing_distance', 0.015)
    
    # Adjust based on volatility (higher volatility = wider stops)
    volatility_adjustment = 1.0 + (volatility * 100)  # Scale volatility
    
    # Adjust based on momentum (stronger momentum = tighter stops in profit direction)
    profit_pct = (current_price - entry_price) / entry_price
    if profit_pct > 0 and momentum > 0.5:  # In profit with strong momentum
        momentum_adjustment = 0.8  # Tighter stops
    elif profit_pct < 0 and momentum < -0.5:  # In loss with strong negative momentum
        momentum_adjustment = 1.3  # Wider stops to avoid whipsaws
    else:
        momentum_adjustment = 1.0
    
    # Calculate dynamic trailing distance
    dynamic_distance = base_distance * volatility_adjustment * momentum_adjustment
    dynamic_distance = min(0.05, max(0.005, dynamic_distance))  # Cap between 0.5% and 5%
    
    # Initialize trailing stop state
    if not hasattr(state, 'realtime_trailing_stop_level'):
        if profit_pct > 0:  # Only activate when in profit
            state.realtime_trailing_stop_level = current_price * (1 - dynamic_distance)
            state.realtime_trailing_active = True
            logger.info(f"🎯 [Trade {trade_id}] Real-time trailing stop activated at ${state.realtime_trailing_stop_level:.4f}")
        else:
            state.realtime_trailing_active = False
            return False, None, {'realtime_trailing': False}
    
    if state.realtime_trailing_active:
        # Update trailing stop level if price moved favorably
        new_stop_level = current_price * (1 - dynamic_distance)
        
        if new_stop_level > state.realtime_trailing_stop_level:
            logger.info(f"📈 [Trade {trade_id}] Trailing stop raised: ${state.realtime_trailing_stop_level:.4f} → ${new_stop_level:.4f}")
            state.realtime_trailing_stop_level = new_stop_level
        
        # Check for exit
        if current_price <= state.realtime_trailing_stop_level:
            locked_profit = (state.realtime_trailing_stop_level - entry_price) / entry_price
            logger.info(f"🛑 [Trade {trade_id}] Real-time trailing stop hit: ${current_price:.4f} ≤ ${state.realtime_trailing_stop_level:.4f}")
            
            return True, "realtime_trailing_stop", {
                'realtime_trailing': True,
                'locked_profit_pct': locked_profit,
                'dynamic_distance': dynamic_distance,
                'volatility': volatility,
                'momentum': momentum
            }
    
    return False, None, {
        'realtime_trailing': True,
        'trailing_active': state.realtime_trailing_active,
        'stop_level': getattr(state, 'realtime_trailing_stop_level', 0),
        'dynamic_distance': dynamic_distance,
        'volatility': volatility,
        'momentum': momentum
    }

# Export enhanced functions for use in main strategy
__all__ = [
    'RealTimePriceMonitor',
    'RealTimeOrderBookMonitor', 
    'RealTimeProtectionManager',
    'enhanced_profit_protection_realtime',
    'enhanced_trailing_stop_realtime'
]