"""
Fibonacci Strategy for crypto trading based on Fibonacci retracement levels.
This strategy detects uptrend patterns and uses Fibonacci 0.5 retracement for entries,
with three consecutive bearish candles with lower lows for exits.

Configurable parameters (set in config.yaml under 'strategies.fibonacci'):

fibonacci:
  enabled: true
  parameters:
    min_candles: 25
    swing_lookback: 20
    fib_tolerance: 0.005
    trend_detection_period: 10
    trend_slope_threshold: 0.001
    exit_candles_required: 3
    atr_period: 14
    atr_multiplier_sl: 1.5
    atr_multiplier_tp: 2.0
    max_hold_time_hours: 24
    risk_per_trade: 0.01
    min_volume_threshold: 1.2
    volume_lookback: 10
  target_timeframes:
    - 1h
    - 15m
"""
import pandas as pd
import numpy as np
import pandas_ta as ta
import logging
from typing import Tuple, Optional, Dict, Any
from datetime import datetime, timedelta
from strategy.base_strategy import BaseStrategy, StrategyState

logger = logging.getLogger(__name__)

class FibonacciStrategy(BaseStrategy):
    def __init__(self, config, exchange, database, redis_client=None, exchange_name=None):
        # Load config from the correct section in strategies.fibonacci
        if hasattr(config, 'strategies') and hasattr(config.strategies, 'fibonacci'):
            strat_cfg = config.strategies.fibonacci.parameters if hasattr(config.strategies.fibonacci, 'parameters') else {}
        elif isinstance(config, dict) and 'strategies' in config and 'fibonacci' in config['strategies']:
            strat_cfg = config['strategies']['fibonacci'].get('parameters', {})
        elif isinstance(config, dict) and 'fibonacci' in config:
            # Direct key access for strategy service config format
            strat_cfg = config['fibonacci'].get('parameters', {})
        else:
            strat_cfg = config.get('fibonacci_strategy', {})  # Fallback to old key
            
        # Call parent constructor
        super().__init__(config, exchange, database, redis_client)
        
        self.exchange_name = exchange_name or strat_cfg.get('exchange_name', 'binance')
        self.min_candles = strat_cfg.get('min_candles', 25)
        self.swing_lookback = strat_cfg.get('swing_lookback', 20)
        self.fib_tolerance = strat_cfg.get('fib_tolerance', 0.005)  # 0.5% tolerance for Fibonacci levels
        self.trend_detection_period = strat_cfg.get('trend_detection_period', 10)
        self.trend_slope_threshold = strat_cfg.get('trend_slope_threshold', 0.001)
        self.exit_candles_required = strat_cfg.get('exit_candles_required', 3)
        self.atr_period = strat_cfg.get('atr_period', 14)
        self.atr_multiplier_sl = strat_cfg.get('atr_multiplier_sl', 1.5)
        self.atr_multiplier_tp = strat_cfg.get('atr_multiplier_tp', 2.0)
        self.max_hold_time_hours = strat_cfg.get('max_hold_time_hours', 24)
        self.risk_per_trade = strat_cfg.get('risk_per_trade', 0.01)
        self.min_volume_threshold = strat_cfg.get('min_volume_threshold', 1.2)
        self.volume_lookback = strat_cfg.get('volume_lookback', 10)
        
        # Fibonacci-specific parameters
        self.fib_levels = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]
        self.primary_fib_level = 0.5  # Focus on 0.5 retracement for entries
        
        logger.info(f"✅ Fibonacci Strategy initialized: Swing lookback={self.swing_lookback}, "
                   f"Fib tolerance={self.fib_tolerance}, Exit candles={self.exit_candles_required}")

    async def initialize(self, pair: str) -> None:
        """Initialize the strategy for a specific trading pair."""
        self.state.pair = pair
        self.state.indicators = {}
        self.state.patterns = {}
        self.state.market_regime = 'unknown'
        self.state.performance = {
            'total_trades': 0,
            'winning_trades': 0,
            'total_pnl': 0.0,
            'win_rate': 0.0,
            'max_drawdown': 0.0,
            'current_drawdown': 0.0,
            'unrealized_pnl': 0.0
        }
        logger.info(f"Initialized Fibonacci strategy for {pair}")

    async def update(self, ohlcv: pd.DataFrame) -> None:
        """Update strategy with new market data."""
        if len(ohlcv) < self.min_candles:
            return
            
        # Store the latest data
        self.state.indicators['ohlcv'] = ohlcv
        
        # Calculate Fibonacci levels
        fib_levels = self._calculate_fibonacci_levels(ohlcv)
        self.state.indicators['fibonacci_levels'] = fib_levels
        
        # Calculate ATR for stop loss and take profit
        atr = self._calculate_atr(ohlcv)
        self.state.indicators['atr'] = atr
        
        # Update market regime
        self.state.market_regime = self._detect_market_regime(ohlcv)

    async def generate_signal(self, ohlcv: pd.DataFrame, indicators_cache: Optional[dict] = None, 
                            pair: Optional[str] = None, timeframe: Optional[str] = None) -> Tuple[str, float, float]:
        """
        Generate trading signal based on Fibonacci retracement strategy.
        
        Returns:
            Tuple of (signal_type, confidence, entry_price)
        """
        if len(ohlcv) < self.min_candles:
            return 'none', 0.0, 0.0
            
        # Use cached indicators if available
        if indicators_cache and 'fibonacci_levels' in indicators_cache:
            fib_levels = indicators_cache['fibonacci_levels']
        else:
            fib_levels = self._calculate_fibonacci_levels(ohlcv)
            
        if indicators_cache and 'atr' in indicators_cache:
            atr = indicators_cache['atr']
        else:
            atr = self._calculate_atr(ohlcv)
        
        current_price = ohlcv['close'].iloc[-1]
        
        # Check if we're in an uptrend
        uptrend = self._detect_uptrend(ohlcv)
        if not uptrend:
            return 'none', 0.0, 0.0
            
        # Check if price is near the 0.5 Fibonacci level
        fib_50 = fib_levels.get('50', 0)
        if fib_50 == 0:
            return 'none', 0.0, 0.0
            
        # Calculate distance to 0.5 Fibonacci level
        distance_to_fib50 = abs(current_price - fib_50) / fib_50
        
        if distance_to_fib50 <= self.fib_tolerance:
            # Check for higher high confirmation
            higher_high = self._detect_higher_high(ohlcv)
            
            # Check volume confirmation
            volume_confirmed = self._check_volume_confirmation(ohlcv)
            
            if higher_high and volume_confirmed:
                confidence = self._calculate_entry_confidence(ohlcv, fib_levels, atr)
                
                # Log the entry condition
                await self._log_condition(
                    name="fibonacci_entry",
                    value=f"Price: {current_price:.6f}, Fib50: {fib_50:.6f}, Distance: {distance_to_fib50:.4f}",
                    description="Fibonacci 0.5 retracement entry signal",
                    result=True,
                    condition_type="entry",
                    pair=pair or self.state.pair,
                    reason=f"Price near 0.5 Fibonacci level with higher high and volume confirmation"
                )
                
                return 'buy', confidence, current_price
        
        return 'none', 0.0, 0.0

    async def should_exit(self) -> bool:
        """
        Check if current position should be closed based on Fibonacci exit conditions.
        """
        if self.state.position != 'long':
            return False
            
        if not self.state.indicators.get('ohlcv') is not None:
            return False
            
        ohlcv = self.state.indicators['ohlcv']
        
        # Check for three consecutive bearish candles with lower lows
        if self._three_bearish_lower_lows(ohlcv):
            await self._log_condition(
                name="fibonacci_exit",
                value="Three consecutive bearish candles with lower lows",
                description="Fibonacci exit signal triggered",
                result=True,
                condition_type="exit",
                pair=self.state.pair,
                reason="Three consecutive bearish candles with lower lows detected"
            )
            return True
            
        # Check for time-based exit
        if self.state.entry_time:
            time_in_trade = datetime.utcnow() - self.state.entry_time
            if time_in_trade.total_seconds() > self.max_hold_time_hours * 3600:
                await self._log_condition(
                    name="fibonacci_time_exit",
                    value=f"Time in trade: {time_in_trade.total_seconds() / 3600:.2f} hours",
                    description="Time-based exit triggered",
                    result=True,
                    condition_type="exit",
                    pair=self.state.pair,
                    reason=f"Maximum hold time of {self.max_hold_time_hours} hours exceeded"
                )
                return True
                
        return False

    async def calculate_position_size(self, signal_type: str) -> float:
        """Calculate position size based on risk management rules."""
        if signal_type != 'buy':
            return 0.0
            
        # Get account balance
        try:
            balance = await self.exchange.get_balance()
            if not balance:
                logger.warning("Could not get balance for position sizing")
                return 0.0
                
            # Use base currency balance (e.g., USDT)
            base_currency = self.state.pair.split('/')[1] if '/' in self.state.pair else 'USDT'
            available_balance = balance.get(base_currency, {}).get('free', 0.0)
            
            if available_balance <= 0:
                logger.warning(f"No available balance in {base_currency}")
                return 0.0
                
            # Calculate position size based on risk percentage
            position_size = available_balance * self.risk_per_trade
            
            # Get minimum order size from config
            min_order_size = self.config.get('trading', {}).get('min_order_size_usd', {}).get(self.exchange_name, 15.0)
            
            if position_size < min_order_size:
                logger.warning(f"Calculated position size {position_size} is below minimum {min_order_size}")
                return 0.0
                
            return position_size
            
        except Exception as e:
            logger.error(f"Error calculating position size: {str(e)}")
            return 0.0

    def _calculate_fibonacci_levels(self, ohlcv: pd.DataFrame) -> Dict[str, float]:
        """Calculate Fibonacci retracement levels based on swing high and low."""
        if len(ohlcv) < self.swing_lookback:
            return {}
            
        # Find swing high and low in the lookback period
        recent_data = ohlcv.tail(self.swing_lookback)
        swing_high = recent_data['high'].max()
        swing_low = recent_data['low'].min()
        
        # Calculate range
        price_range = swing_high - swing_low
        
        if price_range == 0:
            return {}
            
        # Calculate Fibonacci levels
        levels = {}
        for level in self.fib_levels:
            if level == 0:
                levels['0'] = swing_high
            elif level == 1.0:
                levels['100'] = swing_low
            else:
                # For retracement levels, we go from high to low
                fib_price = swing_high - (price_range * level)
                levels[str(int(level * 100))] = fib_price
                
        return levels

    def _detect_uptrend(self, ohlcv: pd.DataFrame) -> bool:
        """Detect uptrend using the pattern: earlier candle makes LL, later candle makes HH."""
        if len(ohlcv) < self.trend_detection_period:
            return False
            
        # Split the period into two halves
        half_period = self.trend_detection_period // 2
        first_half = ohlcv.iloc[-self.trend_detection_period:-half_period]
        second_half = ohlcv.iloc[-half_period:]
        
        # Find lowest low in first half and highest high in second half
        first_half_low = first_half['low'].min()
        second_half_high = second_half['high'].max()
        
        # Check if we have a higher high after a lower low
        if second_half_high > first_half_low:
            # Calculate slope of recent prices
            recent_closes = ohlcv['close'].tail(5).values
            if len(recent_closes) >= 2:
                slope = np.polyfit(range(len(recent_closes)), recent_closes, 1)[0]
                return slope > self.trend_slope_threshold
                
        return False

    def _detect_higher_high(self, ohlcv: pd.DataFrame) -> bool:
        """Detect if the latest candle made a higher high."""
        if len(ohlcv) < 5:
            return False
            
        recent_highs = ohlcv['high'].tail(5).values
        return recent_highs[-1] > recent_highs[:-1].max()

    def _three_bearish_lower_lows(self, ohlcv: pd.DataFrame) -> bool:
        """Detect three consecutive bearish candles with lower lows."""
        if len(ohlcv) < self.exit_candles_required:
            return False
            
        recent_candles = ohlcv.tail(self.exit_candles_required)
        
        # Check if all candles are bearish (close < open)
        all_bearish = (recent_candles['close'] < recent_candles['open']).all()
        
        # Check if lows are decreasing
        lows_decreasing = recent_candles['low'].diff().dropna() < 0
        all_lower_lows = lows_decreasing.all()
        
        return all_bearish and all_lower_lows

    def _check_volume_confirmation(self, ohlcv: pd.DataFrame) -> bool:
        """Check if volume confirms the signal."""
        if len(ohlcv) < self.volume_lookback:
            return False
            
        recent_volume = ohlcv['volume'].tail(self.volume_lookback)
        avg_volume = recent_volume.mean()
        current_volume = recent_volume.iloc[-1]
        
        return current_volume > (avg_volume * self.min_volume_threshold)

    def _calculate_atr(self, ohlcv: pd.DataFrame) -> float:
        """Calculate Average True Range."""
        if len(ohlcv) < self.atr_period:
            return 0.0
            
        try:
            atr = ta.atr(ohlcv['high'], ohlcv['low'], ohlcv['close'], length=self.atr_period)
            return atr.iloc[-1] if not pd.isna(atr.iloc[-1]) else 0.0
        except Exception as e:
            logger.error(f"Error calculating ATR: {str(e)}")
            return 0.0

    def _detect_market_regime(self, ohlcv: pd.DataFrame) -> str:
        """Detect current market regime."""
        if len(ohlcv) < 20:
            return 'unknown'
            
        # Calculate volatility
        returns = ohlcv['close'].pct_change().dropna()
        volatility = returns.std()
        
        # Calculate trend strength
        trend_strength = self._detect_uptrend(ohlcv)
        
        if volatility > 0.03:  # High volatility
            return 'volatile'
        elif trend_strength:
            return 'trending'
        else:
            return 'sideways'

    def _calculate_entry_confidence(self, ohlcv: pd.DataFrame, fib_levels: Dict[str, float], atr: float) -> float:
        """Calculate confidence level for the entry signal."""
        confidence = 0.5  # Base confidence
        
        # Distance to Fibonacci level (closer = higher confidence)
        current_price = ohlcv['close'].iloc[-1]
        fib_50 = fib_levels.get('50', 0)
        if fib_50 > 0:
            distance = abs(current_price - fib_50) / fib_50
            if distance <= self.fib_tolerance * 0.5:
                confidence += 0.2
            elif distance <= self.fib_tolerance:
                confidence += 0.1
                
        # Volume confirmation
        if self._check_volume_confirmation(ohlcv):
            confidence += 0.15
            
        # Trend strength
        if self._detect_uptrend(ohlcv):
            confidence += 0.15
            
        # ATR-based volatility (moderate volatility is good)
        if atr > 0:
            price = ohlcv['close'].iloc[-1]
            atr_pct = atr / price
            if 0.005 <= atr_pct <= 0.02:  # Good volatility range
                confidence += 0.1
                
        return min(confidence, 1.0)  # Cap at 1.0

    async def analyze_pair(self, pair, min_candles=None, exchange_name=None, market_data=None):
        """
        Analyze a trading pair on configured timeframes and return signals for each.
        """
        # Get timeframes from config, fallback to default
        config_timeframes = self.config.get('fibonacci', {}).get('target_timeframes', ['1h', '15m'])
        timeframes = config_timeframes
        results = {}
        
        for timeframe in timeframes:
            logger.info(f"[FibonacciStrategy] Analyzing {pair} on {timeframe}")
            
            try:
                # Get market data for this timeframe
                if market_data and timeframe in market_data:
                    ohlcv = market_data[timeframe]
                else:
                    ohlcv = await self.exchange.get_ohlcv(pair, timeframe, limit=100)
                
                if ohlcv is None or len(ohlcv) < self.min_candles:
                    logger.warning(f"Insufficient data for {pair} on {timeframe}")
                    results[timeframe] = {'signal': 'none', 'confidence': 0.0, 'reason': 'insufficient_data'}
                    continue
                
                # Generate signal
                signal, confidence, entry_price = await self.generate_signal(ohlcv, pair=pair, timeframe=timeframe)
                
                # Calculate Fibonacci levels for context
                fib_levels = self._calculate_fibonacci_levels(ohlcv)
                current_price = ohlcv['close'].iloc[-1] if len(ohlcv) > 0 else 0
                
                results[timeframe] = {
                    'signal': signal,
                    'confidence': confidence,
                    'entry_price': entry_price,
                    'current_price': current_price,
                    'fibonacci_levels': fib_levels,
                    'reason': self._get_signal_reason(signal, ohlcv, fib_levels)
                }
                
            except Exception as e:
                logger.error(f"Error analyzing {pair} on {timeframe}: {str(e)}")
                results[timeframe] = {'signal': 'none', 'confidence': 0.0, 'reason': f'error: {str(e)}'}
                
        return results

    def _get_signal_reason(self, signal: str, ohlcv: pd.DataFrame, fib_levels: Dict[str, float]) -> str:
        """Get detailed reason for the signal."""
        if signal == 'none':
            if len(ohlcv) < self.min_candles:
                return "insufficient_data"
            elif not self._detect_uptrend(ohlcv):
                return "no_uptrend"
            else:
                current_price = ohlcv['close'].iloc[-1]
                fib_50 = fib_levels.get('50', 0)
                if fib_50 > 0:
                    distance = abs(current_price - fib_50) / fib_50
                    if distance > self.fib_tolerance:
                        return f"price_not_near_fib50_distance_{distance:.4f}"
                    elif not self._detect_higher_high(ohlcv):
                        return "no_higher_high"
                    elif not self._check_volume_confirmation(ohlcv):
                        return "no_volume_confirmation"
                return "no_fibonacci_setup"
        elif signal == 'buy':
            return "fibonacci_50_retracement_with_confirmation"
        else:
            return "unknown_signal"
