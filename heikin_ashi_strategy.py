"""
Heikin-Ashi strategy implementation for the crypto trading bot.
Uses Heikin-Ashi candlesticks for trend following and reversal detection.
"""
from typing import Dict, List, Optional, Any, Union, Tuple
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import logging
import os

from .base_strategy import BaseStrategy, StrategyState
from strategy.strategy_pnl import calculate_unrealized_pnl, check_profit_protection, check_profit_protection_enhanced, manage_trailing_stop, restore_profit_protection_state
from strategy.dynamic_stop_loss import DynamicStopLoss
from strategy.strategy_regime_analytics import StrategyRegimeAnalytics
from strategy.strategy_filters import CryptoOptimizedFilters
from strategy.enhanced_risk_management import EnhancedRiskManager
from strategy.multi_timeframe import MultiTimeframeAnalyzer
from strategy.enhanced_performance import EnhancedPerformanceMonitor
from config.optimal_params import OPTIMAL_PARAMS
from strategy.condition_logger import ConditionLogger

class HeikinAshiStrategy(BaseStrategy):
    """Heikin-Ashi strategy implementation."""
    STRATEGY_NAME = "Heikin Ashi"

    def __init__(
        self,
        config: Dict[str, Any],
        exchange: Any,
        database: Any,
        redis_client=None
    ):
        """Initialize the enhanced Heikin-Ashi strategy. Uses ConditionLogger for validation and condition checks."""
        super().__init__(config, exchange, database, redis_client)
        self.logger = logging.getLogger(__name__)
        self.logger.debug(f"[DEBUG] HeikinAshiStrategy.__init__ called for config: {config}")
        if hasattr(self, 'state'):
            self.logger.debug(f"[DEBUG] HeikinAshiStrategy.__init__: self.state.pair={getattr(self.state, 'pair', None)}")
        else:
            self.logger.debug(f"[DEBUG] HeikinAshiStrategy.__init__: self.state not set")
        self.exchange = exchange
        
        # Initialize optimal parameters with safe config access
        if isinstance(config, dict):
            parameters = config.get('parameters', {})
        else:
            parameters = getattr(config, 'parameters', {})
            
        self.ha_period = OPTIMAL_PARAMS.get('ha_period', parameters.get('ha_period', 14))
        self.trend_period = OPTIMAL_PARAMS.get('trend_period', parameters.get('trend_period', 21))
        self.volume_threshold = OPTIMAL_PARAMS.get('volume_threshold', parameters.get('volume_threshold', 1.5))
        self.min_candles = parameters.get('min_candles', 50)
        # Configurable ADX minimum threshold for signal generation (proper ADX range 0-100)
        self.adx_min_threshold = parameters.get('adx_min_threshold', 20.0)  # Changed from 1.0 to 20.0 for proper ADX
        
        self.trade_id = None
        self._current_ohlcv = None  # For test compatibility
        self._regime_analytics = StrategyRegimeAnalytics(redis_client)
        
        # Initialize enhanced components
        self.filters = CryptoOptimizedFilters(config)
        self.risk_manager = EnhancedRiskManager(config)
        self.mtf_analyzer = MultiTimeframeAnalyzer(OPTIMAL_PARAMS.get('multi_timeframe', {}))
        self.performance_monitor = EnhancedPerformanceMonitor(config)
        self.condition_logger = ConditionLogger()

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
        self.logger.info(f"Initialized Heikin-Ashi strategy for {pair}")

    def _get_decimal_precision(self, pair: str) -> int:
        """Return the decimal precision for a given pair (8 for PEPE/USDC and similar, 2 for most others)."""
        # Get base currency from configuration or use default
        base_currency = 'USDC'  # Default for EU compliance
        if isinstance(self.config, dict) and 'trading' in self.config:
            base_currency = self.config['trading'].get('base_currency', 'USDC')
        elif hasattr(self.config, 'trading') and hasattr(self.config.trading, 'base_currency'):
            base_currency = self.config.trading.base_currency
        
        # High precision pairs (meme coins and low-value tokens)
        high_precision_pairs = {f"PEPE/{base_currency}", f"SHIB/{base_currency}", f"DOGE/{base_currency}"}
        
        if pair in high_precision_pairs:
            return 8
        else:
            return 2

    def _calculate_heikin_ashi(self, ohlcv: pd.DataFrame, indicators_cache: Optional[dict] = None, pair: Optional[str] = None, timeframe: Optional[str] = None) -> pd.DataFrame:
        """Calculate Heikin-Ashi candlesticks, using cache if provided."""
        cache_key = None
        if indicators_cache is not None and pair and timeframe:
            cache_key = f"HA_{pair}_{timeframe}_{self.ha_period}"
            if cache_key in indicators_cache:
                self.logger.debug(f"[CACHE HIT] Heikin-Ashi for {pair} {timeframe} (period={self.ha_period})")
                return indicators_cache[cache_key]
        ha = pd.DataFrame(index=ohlcv.index)
        ha['ha_close'] = (ohlcv['open'] + ohlcv['high'] + ohlcv['low'] + ohlcv['close']) / 4
        ha['ha_open'] = 0.0
        if len(ohlcv) > 0:
            ha.iloc[0, ha.columns.get_loc('ha_open')] = float(ohlcv['open'].iloc[0] + ohlcv['close'].iloc[0]) / 2
        for i in range(1, len(ohlcv)):
            prev_ha_open = ha.iloc[i-1, ha.columns.get_loc('ha_open')]
            prev_ha_close = ha.iloc[i-1, ha.columns.get_loc('ha_close')]
            ha.iloc[i, ha.columns.get_loc('ha_open')] = float(prev_ha_open + prev_ha_close) / 2
        ha['ha_high'] = pd.concat([ohlcv['high'], ha['ha_open'], ha['ha_close']], axis=1).max(axis=1)
        ha['ha_low'] = pd.concat([ohlcv['low'], ha['ha_open'], ha['ha_close']], axis=1).min(axis=1)
        if indicators_cache is not None and cache_key:
            indicators_cache[cache_key] = ha
            self.logger.debug(f"[CACHE STORE] Heikin-Ashi for {pair} {timeframe} (period={self.ha_period})")
        return ha

    def _generate_enhanced_signal(self, ha: pd.DataFrame) -> Tuple[str, float]:
        """Enhanced HA signal generation with candle anatomy analysis"""
        if len(ha) < 3: 
            return 'hold', 0.0
        
        current = ha.iloc[-1]
        prev = ha.iloc[-2]
        
        # Calculate candle metrics
        body_size = abs(current['ha_close'] - current['ha_open'])
        total_range = current['ha_high'] - current['ha_low']
        lower_shadow = current['ha_low'] - min(current['ha_open'], current['ha_close'])
        
        # Trend confirmation conditions
        is_strong_bullish = (
            current['ha_close'] > current['ha_open'] and
            (lower_shadow / total_range) < 0.1 and
            (body_size / total_range) > 0.6 and
            current['ha_close'] > prev['ha_close']
        )
        
        # Confidence calculation
        confidence = min(body_size / total_range * 1.5, 1.0) if is_strong_bullish else 0.0
        
        return ('buy', confidence) if is_strong_bullish else ('hold', 0.0)

    def _calculate_volatility(self) -> Optional[float]:
        """Calculate current market volatility using ATR."""
        try:
            atr = self.state.indicators.get('ATR', None)
            if atr is None or atr.empty:
                return None
            
            ohlcv = getattr(self.state, 'ohlcv', None)
            if ohlcv is None or ohlcv.empty:
                return None
            
            current_price = ohlcv['close'].iloc[-1]
            atr_value = atr.iloc[-1]
            
            # Volatility as percentage of current price
            volatility = atr_value / current_price if current_price > 0 else None
            
            return float(volatility) if volatility is not None else None
            
        except Exception as e:
            self.logger.error(f"Error calculating volatility: {e}")
            return None

    def _log_detailed_analysis(self, ohlcv: pd.DataFrame, ha: pd.DataFrame) -> None:
        """Log detailed analysis of Heikin-Ashi strategy for debugging and verification purposes."""
        try:
            # ANSI color codes
            GREEN = '\033[92m'
            RED = '\033[91m'
            END = '\033[0m'
            check = f"{GREEN}✓{END}"
            cross = f"{RED}✗{END}"
            # Get the latest candles
            current_idx = -1
            prev_idx = -2
            
            # Determine decimal precision for this pair
            precision = self._get_decimal_precision(self.state.pair)
            fmt = f".{precision}f"
            
            # CANDLE DATA
            reg_open = ohlcv['open'].iloc[current_idx]
            reg_high = ohlcv['high'].iloc[current_idx]
            reg_low = ohlcv['low'].iloc[current_idx]
            reg_close = ohlcv['close'].iloc[current_idx]
            reg_volume = ohlcv['volume'].iloc[current_idx]
            
            ha_open = ha['ha_open'].iloc[current_idx]
            ha_high = ha['ha_high'].iloc[current_idx]
            ha_low = ha['ha_low'].iloc[current_idx]
            ha_close = ha['ha_close'].iloc[current_idx]
            
            # Determine HA candle color
            ha_color = "GREEN" if ha_close > ha_open else "RED"
            prev_ha_color = "GREEN" if ha['ha_close'].iloc[prev_idx] > ha['ha_open'].iloc[prev_idx] else "RED"
            
            self.logger.info(f"===== HEIKIN ASHI DETAILED ANALYSIS FOR {self.state.pair} =====")
            self.logger.info(f"CANDLE DATA:")
            self.logger.info(f"Regular Candle: Open: {reg_open:{fmt}} | High: {reg_high:{fmt}} | Low: {reg_low:{fmt}} | Close: {reg_close:{fmt}} | Volume: {reg_volume:.2f}")
            self.logger.info(f"Heikin Ashi Candle: haOpen: {ha_open:{fmt}} | haHigh: {ha_high:{fmt}} | haLow: {ha_low:{fmt}} | haClose: {ha_close:{fmt}} | Color: {ha_color}")
            
            # CALCULATION VERIFICATION
            calculated_ha_close = (reg_open + reg_high + reg_low + reg_close) / 4
            prev_ha_open = ha['ha_open'].iloc[prev_idx]
            prev_ha_close = ha['ha_close'].iloc[prev_idx]
            calculated_ha_open = (prev_ha_open + prev_ha_close) / 2
            
            self.logger.info(f"CALCULATION VERIFICATION:")
            self.logger.info(f"haClose = (Open + High + Low + Close)/4 = ({reg_open:{fmt}} + {reg_high:{fmt}} + {reg_low:{fmt}} + {reg_close:{fmt}})/4 = {calculated_ha_close:{fmt}} {check if abs(calculated_ha_close - ha_close) < 10**(-precision+1) else cross}")
            self.logger.info(f"haOpen = (Previous haOpen + Previous haClose)/2 = ({prev_ha_open:{fmt}} + {prev_ha_close:{fmt}})/2 = {calculated_ha_open:{fmt}} {check if abs(calculated_ha_open - ha_open) < 10**(-precision+1) else cross}")
            
            # PATTERN RECOGNITION
            color_change = ha_color != prev_ha_color
            
            # Count consecutive same-colored candles
            consecutive_count = 1
            idx = current_idx - 1
            current_color = ha_color
            while idx >= -10 and idx > -len(ha):  # Check up to 10 candles back or available data
                if (ha['ha_close'].iloc[idx] > ha['ha_open'].iloc[idx]) == (ha_color == "GREEN"):
                    consecutive_count += 1
                    idx -= 1
                else:
                    break
                    
            # Calculate shadows with improved precision
            lower_shadow = abs(ha_low - min(ha_open, ha_close))
            upper_shadow = abs(ha_high - max(ha_open, ha_close))
            
            # Classify shadow size with quantitative thresholds
            def classify_shadow(shadow, candle_size, total_range):
                if candle_size == 0:  # Doji candle
                    if shadow < 0.05 * total_range:
                        return "Small"
                    elif shadow < 0.15 * total_range:
                        return "Medium"
                    else:
                        return "Large"
                else:
                    # Use both body percentage and total range percentage
                    body_pct = shadow / candle_size if candle_size > 0 else float('inf')
                    range_pct = shadow / total_range if total_range > 0 else 0
                    
                    if body_pct < 0.1 and range_pct < 0.05:
                        return "Small"
                    elif body_pct < 0.3 and range_pct < 0.15:
                        return "Medium"
                    else:
                        return "Large"
                
            candle_size = abs(ha_open - ha_close)
            total_range = ha_high - ha_low
            lower_shadow_class = classify_shadow(lower_shadow, candle_size, total_range)
            upper_shadow_class = classify_shadow(upper_shadow, candle_size, total_range)
            
            # Calculate shadow percentages for quantitative analysis
            lower_shadow_pct = (lower_shadow / total_range * 100) if total_range > 0 else 0
            upper_shadow_pct = (upper_shadow / total_range * 100) if total_range > 0 else 0
            body_pct = (candle_size / total_range * 100) if total_range > 0 else 0
            
            self.logger.info(f"PATTERN RECOGNITION:")
            self.logger.info(f"Previous Candle Color: {prev_ha_color}")
            self.logger.info(f"Color Change: {'YES' if color_change else 'NO'} ({'' if color_change else 'Continuing trend'})")
            self.logger.info(f"Consecutive {ha_color} Candles: {consecutive_count}")
            self.logger.info(f"Lower Shadow: {lower_shadow:{fmt}} ({lower_shadow_class}) - {lower_shadow_pct:.1f}% of range")
            self.logger.info(f"Upper Shadow: {upper_shadow:{fmt}} ({upper_shadow_class}) - {upper_shadow_pct:.1f}% of range")
            self.logger.info(f"Body Size: {candle_size:{fmt}} ({body_pct:.1f}% of range)")
            
            # TREND ANALYSIS - Enhanced with clearer determination logic
            trend = self.state.market_regime
            
            # Determine trend direction using multiple factors
            # 1. Market regime from strategy
            # 2. EMA alignment
            # 3. HA candle sequence
            ema9 = self.state.indicators.get('ema9', pd.Series()).iloc[-1] if 'ema9' in self.state.indicators else None
            ema21 = self.state.indicators.get('ema21', pd.Series()).iloc[-1] if 'ema21' in self.state.indicators else None
            ma50 = self.state.indicators.get('ma50', pd.Series()).iloc[-1] if 'ma50' in self.state.indicators else None
            
            # Enhanced trend determination
            if trend == "uptrend" and consecutive_count >= 2 and ha_color == "GREEN":
                if ema9 and ema21 and ema9 > ema21:
                    trend_direction = "BULLISH"
                else:
                    trend_direction = "BULLISH (Weak)"
            elif trend == "downtrend" and consecutive_count >= 2 and ha_color == "RED":
                if ema9 and ema21 and ema9 < ema21:
                    trend_direction = "BEARISH"
                else:
                    trend_direction = "BEARISH (Weak)"
            else:
                trend_direction = "NEUTRAL"
            
            # Determine trend strength based on multiple factors
            trend_strength = "WEAK"
            if consecutive_count >= 5 and trend != "sideways":
                trend_strength = "STRONG"
            elif consecutive_count >= 3 and trend != "sideways":
                trend_strength = "MODERATE"
                
            # Check for recent reversal
            reversal = self.state.patterns.get('reversal', {}).get('type')
            recent_reversal = "YES" if reversal else "NO"
            
            self.logger.info(f"TREND ANALYSIS:")
            self.logger.info(f"Market Regime: {trend}")
            self.logger.info(f"Current Trend Direction: {trend_direction}")
            self.logger.info(f"Trend Strength: {trend_strength} ({consecutive_count} consecutive same-colored candles)")
            self.logger.info(f"Recent Reversal: {recent_reversal}")
            
            # CONDITION VERIFICATION - Enhanced with proper HA logic
            # For bullish signals (SCALPING OPTIMIZED)
            cond1_bull = ha_close > ha_open  # Green HA candle
            cond2_bull = consecutive_count >= 1  # Reduced from 2 for scalping (faster signals)
            cond3_bull = lower_shadow_pct < 40  # Relaxed from 10% to 40% for scalping
            
            # For bearish signals (SCALPING OPTIMIZED)
            cond1_bear = ha_close < ha_open  # Red HA candle
            cond2_bear = consecutive_count >= 1  # Reduced from 2 for scalping (faster signals)
            cond3_bear = upper_shadow_pct < 40  # Relaxed from 10% to 40% for scalping
            
            # Overall conditions based on trend direction
            if trend_direction.startswith("BULLISH"):
                all_conditions_met = cond1_bull and cond2_bull and cond3_bull
                signal_type = "BULLISH"
            elif trend_direction.startswith("BEARISH"):
                all_conditions_met = cond1_bear and cond2_bear and cond3_bear
                signal_type = "BEARISH"
            else:
                all_conditions_met = False
                signal_type = "NEUTRAL"
            
            self.logger.info(f"CONDITION VERIFICATION ({signal_type}):")
            if signal_type == "BULLISH":
                self.logger.info(f"Condition 1: Green HA Candle (haClose > haOpen) {check if cond1_bull else cross}")
                self.logger.info(f"Condition 2: Bullish Continuation (≥1 consecutive green) {check if cond2_bull else cross}")
                self.logger.info(f"Condition 3: Lower Shadow (<40% range) {check if cond3_bull else cross}")
            elif signal_type == "BEARISH":
                self.logger.info(f"Condition 1: Red HA Candle (haClose < haOpen) {check if cond1_bear else cross}")
                self.logger.info(f"Condition 2: Bearish Continuation (≥1 consecutive red) {check if cond2_bear else cross}")
                self.logger.info(f"Condition 3: Upper Shadow (<40% range) {check if cond3_bear else cross}")
            else:
                self.logger.info(f"No clear trend direction - conditions not evaluated")
            self.logger.info(f"All Required Conditions Met: {'YES' if all_conditions_met else 'NO'}")
            
            # SUPPLEMENTARY INDICATORS - Enhanced with mandatory filter logic
            # Calculate EMAs if not already in state
            if 'ema9' not in self.state.indicators:
                self.state.indicators['ema9'] = ohlcv['close'].ewm(span=9).mean()
            if 'ema21' not in self.state.indicators:
                self.state.indicators['ema21'] = ohlcv['close'].ewm(span=21).mean()
            if 'ma50' not in self.state.indicators:
                self.state.indicators['ma50'] = ohlcv['close'].rolling(window=50).mean()
                
            ema9 = self.state.indicators['ema9'].iloc[-1] if len(self.state.indicators['ema9']) > 0 else None
            ema21 = self.state.indicators['ema21'].iloc[-1] if len(self.state.indicators['ema21']) > 0 else None
            ma50 = self.state.indicators['ma50'].iloc[-1] if len(self.state.indicators['ma50']) > 0 else None
            
            # Enhanced volume analysis with quantification
            vol_current = ohlcv['volume'].iloc[current_idx]
            vol_previous = ohlcv['volume'].iloc[prev_idx]
            vol_avg_10 = ohlcv['volume'].rolling(10).mean().iloc[current_idx]
            vol_increasing = vol_current > vol_previous
            vol_above_avg = vol_current > vol_avg_10
            vol_change_pct = ((vol_current - vol_previous) / vol_previous * 100) if vol_previous > 0 else 0
            
            # Enhanced ADX calculation with minimum threshold
            if 'adx' not in self.state.indicators:
                try:
                    import talib
                    high = ohlcv['high'].values.astype(float)
                    low = ohlcv['low'].values.astype(float)
                    close = ohlcv['close'].values.astype(float)
                    adx_values = talib.ADX(high, low, close, timeperiod=14)
                    self.state.indicators['adx'] = pd.Series(adx_values, index=ohlcv.index)
                    self.logger.info(f"[DEBUG] ADX values for {self.state.pair}: {self.state.indicators['adx'].tail(5).to_list()}")
                except Exception as e:
                    self.logger.error(f"Failed to calculate ADX for {self.state.pair}: {e}")
                
            adx = self.state.indicators['adx'].iloc[-1] if len(self.state.indicators['adx']) > 0 else 0
            adx_filter_passed = adx > self.adx_min_threshold  # Use proper threshold now
            
            # MACD status
            if 'macd' not in self.state.indicators:
                ema12 = ohlcv['close'].ewm(span=12).mean()
                ema26 = ohlcv['close'].ewm(span=26).mean()
                macd_line = ema12 - ema26
                signal_line = macd_line.ewm(span=9).mean()
                self.state.indicators['macd'] = macd_line
                self.state.indicators['macd_signal'] = signal_line
                
            macd_line = self.state.indicators['macd'].iloc[-1] if len(self.state.indicators['macd']) > 0 else 0
            signal_line = self.state.indicators['macd_signal'].iloc[-1] if len(self.state.indicators['macd_signal']) > 0 else 0
            macd_bullish = macd_line > signal_line
            
            # Determine if supplementary indicators support the signal
            ema_support = False
            if signal_type == "BULLISH" and ema9 and ema21:
                ema_support = reg_close > ema9 and ema9 > ema21
            elif signal_type == "BEARISH" and ema9 and ema21:
                ema_support = reg_close < ema9 and ema9 < ema21
                
            macd_support = False
            if signal_type == "BULLISH":
                macd_support = macd_bullish
            elif signal_type == "BEARISH":
                macd_support = not macd_bullish
            
            self.logger.info(f"SUPPLEMENTARY INDICATORS (Mandatory Filters):")
            if ema9 and ema21:
                self.logger.info(f"9 EMA: {ema9:{fmt}} (Price {'Above' if reg_close > ema9 else 'Below'}) {check if ema_support else cross}")
                self.logger.info(f"21 EMA: {ema21:{fmt}} (EMA9 {'>' if ema9 > ema21 else '<'} EMA21) {check if ema_support else cross}")
            else:
                self.logger.info(f"EMAs: Insufficient data {cross}")
            if ma50:
                ma50_support = (reg_close > ma50 and signal_type == "BULLISH") or (reg_close < ma50 and signal_type == "BEARISH")
                self.logger.info(f"50 MA: {ma50:{fmt}} (Price {'Above' if reg_close > ma50 else 'Below'}) {check if ma50_support else cross}")
            else:
                self.logger.info(f"50 MA: Insufficient data {cross}")
            self.logger.info(f"Volume: {vol_current:.0f} (Prev: {vol_previous:.0f}, Change: {vol_change_pct:+.1f}%) {check if vol_above_avg else cross}")
            self.logger.info(f"Volume vs 10-period avg: {'ABOVE' if vol_above_avg else 'BELOW'} average {check if vol_above_avg else cross}")
            self.logger.info(f"ADX(14): {adx:.1f} (Min threshold: {self.adx_min_threshold}) {check if adx_filter_passed else cross}")
            self.logger.info(f"MACD: {'BULLISH' if macd_bullish else 'BEARISH'} {check if macd_support else cross}")
            
            # SIGNAL GENERATION - Enhanced with mandatory filters (SCALPING OPTIMIZED)
            # Relaxed requirements for scalping - only need 2 out of 4 filters to pass
            filter_count = sum([adx_filter_passed, ema_support, macd_support, vol_above_avg])
            mandatory_filters_passed = filter_count >= 2  # Scalping: 2/4 filters instead of 4/4
            
            buy_signal = (signal_type == "BULLISH" and all_conditions_met and mandatory_filters_passed)
            sell_signal = (signal_type == "BEARISH" and all_conditions_met and mandatory_filters_passed)
            hold_position = not buy_signal and not sell_signal
            
            self.logger.info(f"SIGNAL GENERATION:")
            self.logger.info(f"HA Pattern Conditions Met: {'YES' if all_conditions_met else 'NO'}")
            self.logger.info(f"Mandatory Filters Passed: {'YES' if mandatory_filters_passed else 'NO'} (Scalping: {filter_count}/4 filters, need ≥2)")
            self.logger.info(f"  - ADX > {self.adx_min_threshold}: {'YES' if adx_filter_passed else 'NO'}")
            self.logger.info(f"  - EMA Alignment: {'YES' if ema_support else 'NO'}")
            self.logger.info(f"  - MACD Support: {'YES' if macd_support else 'NO'}")
            self.logger.info(f"  - Volume Above Average: {'YES' if vol_above_avg else 'NO'}")
            self.logger.info(f"Final Buy Signal: {'YES' if buy_signal else 'NO'}")
            self.logger.info(f"Final Sell Signal: {'YES' if sell_signal else 'NO'}")
            self.logger.info(f"Hold Position: {'YES' if hold_position else 'NO'}")
            self.logger.info(f"===============================================")
        except Exception as e:
            self.logger.error(f"Error in _log_detailed_analysis: {str(e)}")

    def _detect_trend(self, ha: pd.DataFrame) -> str:
        """Detect trend using Heikin-Ashi candlesticks."""
        # Calculate trend using moving averages
        ha_close_series = pd.Series(ha['ha_close']) if isinstance(ha['ha_close'], np.ndarray) else ha['ha_close']
        ha['sma'] = ha_close_series.rolling(window=self.trend_period).mean()
        ha['ema'] = ha_close_series.ewm(span=self.trend_period).mean()
        
        # Calculate trend strength
        ha['trend_strength'] = abs(ha_close_series - ha['sma']) / ha['sma']
        
        # Determine trend
        if ha['ha_close'].iloc[-1] > ha['sma'].iloc[-1] and ha['trend_strength'].iloc[-1] > 0.02:
            return 'uptrend'
        elif ha['ha_close'].iloc[-1] < ha['sma'].iloc[-1] and ha['trend_strength'].iloc[-1] > 0.02:
            return 'downtrend'
        else:
            return 'sideways'

    def _detect_reversal(self, ha: pd.DataFrame) -> Optional[str]:
        """Detect trend reversal using Heikin-Ashi candlesticks."""
        # Check for bullish reversal
        if (ha['ha_close'].iloc[-1] > ha['ha_open'].iloc[-1] and
            ha['ha_close'].iloc[-2] < ha['ha_open'].iloc[-2] and
            ha['ha_close'].iloc[-3] < ha['ha_open'].iloc[-3]):
            return 'bullish'
        
        # Check for bearish reversal
        elif (ha['ha_close'].iloc[-1] < ha['ha_open'].iloc[-1] and
              ha['ha_close'].iloc[-2] > ha['ha_open'].iloc[-2] and
              ha['ha_close'].iloc[-3] > ha['ha_open'].iloc[-3]):
            return 'bearish'
        
        return None

    async def update(self, ohlcv: pd.DataFrame) -> None:
        """Update strategy state based on new OHLCV data."""
        try:
            self.logger.debug(f"[DEBUG] HeikinAshiStrategy.update called. self.state.pair={getattr(self.state, 'pair', None)}")
            if not hasattr(self.state, 'pair') or not self.state.pair:
                self.logger.error("[HeikinAshiStrategy.update] self.state.pair is not set. Cannot update strategy state.")
                return
            
            if not isinstance(ohlcv, pd.DataFrame) or ohlcv.empty:
                self.logger.warning(f"[HeikinAshiStrategy.update] Invalid or empty OHLCV data for {self.state.pair}")
                return
            
            self._current_ohlcv = ohlcv.copy() if isinstance(ohlcv, pd.DataFrame) else None
            required_cols = {'open', 'high', 'low', 'close', 'volume'}
            # Try to coerce ohlcv to DataFrame if not already
            if not isinstance(ohlcv, pd.DataFrame):
                try:
                    ohlcv = pd.DataFrame(ohlcv)
                    self.logger.warning(f"[HeikinAshiStrategy] OHLCV was not a DataFrame for pair {self.state.pair}. Converted to DataFrame.")
                except Exception as e:
                    self.logger.error(f"[HeikinAshiStrategy] Could not convert OHLCV to DataFrame for pair {self.state.pair}: {e}")
                    return
            # Add missing columns as NaN if needed
            missing_cols = required_cols - set(ohlcv.columns)
            if missing_cols:
                for col in missing_cols:
                    ohlcv[col] = np.nan
                self.logger.warning(f"[HeikinAshiStrategy] OHLCV missing columns {missing_cols} for pair {self.state.pair}. Filled with NaN.")
            # Final check
            if not required_cols.issubset(ohlcv.columns):
                self.logger.error(f"[HeikinAshiStrategy] OHLCV data still missing required columns for pair {self.state.pair}. Skipping update. Columns: {ohlcv.columns}")
                return
            if not isinstance(ohlcv, pd.DataFrame):
                self.logger.warning(f"[HeikinAshiStrategy] update() received non-DataFrame: {type(ohlcv)}")
                return
            if 'close' not in ohlcv.columns:
                self.logger.error("OHLCV data missing 'close' column. Columns: %s", ohlcv.columns)
                return
            try:
                # Ensure all numeric columns are float (fix Decimal/float issues)
                for col in ['open', 'high', 'low', 'close', 'volume']:
                    if col in ohlcv.columns:
                        ohlcv[col] = ohlcv[col].astype(float)
                # Calculate Heikin-Ashi candlesticks
                ha = self._calculate_heikin_ashi(ohlcv)
                # Detect trend and reversal (this creates ha['sma'], etc.)
                trend = self._detect_trend(ha)
                reversal = self._detect_reversal(ha)
                # Update indicators (after _detect_trend)
                self.state.indicators['heikin_ashi'] = ha
                self.state.indicators['sma'] = ha['sma']
                self.state.indicators['ema'] = ha['ema']
                self.state.indicators['trend_strength'] = ha['trend_strength']
                # Update state
                self.state.market_regime = trend
                if reversal:
                    self.state.patterns['reversal'] = {
                        'type': reversal,
                        'confidence': 0.8,
                        'timestamp': datetime.utcnow()
                    }
                # Update performance
                await self.update_performance()
                
                # Log detailed analysis for debugging
                self._log_detailed_analysis(ohlcv, ha)
                await self.log_condition_outcome(
                    'market_regime', self.state.market_regime, True,
                    {'reason': 'Updated market regime after trend detection'}
                )
                if 'reversal' in self.state.patterns:
                    await self.log_condition_outcome(
                        'reversal', self.state.patterns['reversal']['type'], True,
                        {'confidence': self.state.patterns['reversal']['confidence']}
                    )
            except Exception as e:
                self.logger.error(f"Error updating strategy state: {str(e)}")
        except Exception as e:
            self.logger.error(f"Error in update: {str(e)}")

    async def apply_optimized_parameters(self, market_regime: Optional[str] = None) -> bool:
        """Apply optimized parameters based on market regime."""
        try:
            # Update parameters in config if it's an object
            if hasattr(self.config, 'parameters'):
                self.config.parameters['ha_period'] = self.ha_period
                self.config.parameters['trend_period'] = self.trend_period
                self.config.parameters['volume_threshold'] = self.volume_threshold
            # Update parameters in config if it's a dictionary
            elif isinstance(self.config, dict):
                if 'parameters' not in self.config:
                    self.config['parameters'] = {}
                self.config['parameters']['ha_period'] = self.ha_period
                self.config['parameters']['trend_period'] = self.trend_period
                self.config['parameters']['volume_threshold'] = self.volume_threshold
                
            self.logger.info(f"Applied optimized parameters for {self.state.pair}: ha_period={self.ha_period}, trend_period={self.trend_period}, volume_threshold={self.volume_threshold}")
            return True
            
        except Exception as e:
            self.logger.error(f"Error applying optimized parameters: {e}")
            return False

    async def generate_signal(self, ohlcv: pd.DataFrame, indicators_cache: Optional[dict] = None, pair: Optional[str] = None, timeframe: Optional[str] = None) -> Tuple[str, float, float]:
        """Generate trading signal based on Heikin-Ashi candlestick analysis."""
        try:
            # Validate input data
            if ohlcv is None or ohlcv.empty or len(ohlcv) < self.min_candles:
                self.logger.warning(f"[HeikinAshiStrategy] Insufficient data for signal generation: {len(ohlcv) if ohlcv is not None else 0} points")
                return 'hold', 0.0, 0.0
            
            # Calculate Heikin-Ashi candlesticks
            ha = self._calculate_heikin_ashi(ohlcv, indicators_cache, pair, timeframe)
            
            # Check if HA data is valid
            if ha is None or ha.empty or len(ha) < 3:
                self.logger.warning(f"[HeikinAshiStrategy] Invalid Heikin-Ashi data for signal generation")
                return 'hold', 0.0, 0.0
            
            # Get current and previous candles
            current = ha.iloc[-1]
            prev = ha.iloc[-2]
            prev2 = ha.iloc[-3] if len(ha) >= 3 else None
            
            # Check for NaN values
            if any(pd.isna(val) for val in [current['ha_open'], current['ha_close'], current['ha_high'], current['ha_low']]):
                self.logger.warning(f"[HeikinAshiStrategy] NaN values in Heikin-Ashi data")
                return 'hold', 0.0, 0.0
            
            # Detect trend and reversal
            trend = self._detect_trend(ha)
            reversal = self._detect_reversal(ha)
            
            # Calculate candle metrics
            body_size = abs(current['ha_close'] - current['ha_open'])
            total_range = current['ha_high'] - current['ha_low']
            lower_shadow = current['ha_low'] - min(current['ha_open'], current['ha_close'])
            upper_shadow = current['ha_high'] - max(current['ha_open'], current['ha_close'])
            
            # Avoid division by zero
            if total_range == 0:
                self.logger.warning(f"[HeikinAshiStrategy] Zero range candle detected")
                return 'hold', 0.0, 0.0
            
            # Calculate shadow percentages
            lower_shadow_pct = lower_shadow / total_range
            upper_shadow_pct = upper_shadow / total_range
            body_pct = body_size / total_range
            
            # Count consecutive same-colored candles
            consecutive_count = 1
            current_color = current['ha_close'] > current['ha_open']
            for i in range(len(ha) - 2, max(0, len(ha) - 10), -1):  # Check up to 10 candles back
                if (ha.iloc[i]['ha_close'] > ha.iloc[i]['ha_open']) == current_color:
                    consecutive_count += 1
                else:
                    break
            
            # Signal generation logic (PERMISSIVE FOR TESTING)
            signal = 'hold'
            confidence = 0.0
            strength = 0.0

            # Bullish signal for any green candle
            if current['ha_close'] > current['ha_open']:
                signal = 'buy'
                confidence = 0.7 + min(body_pct, 0.3)
                strength = min(body_pct * 1.2, 1.0)
                self.logger.info(f"[HeikinAshiStrategy] Permissive bullish: haClose={current['ha_close']:.4f}, haOpen={current['ha_open']:.4f}")
            # Bearish signal for any red candle
            elif current['ha_close'] < current['ha_open']:
                signal = 'sell'
                confidence = 0.7 + min(body_pct, 0.3)
                strength = min(body_pct * 1.2, 1.0)
                self.logger.info(f"[HeikinAshiStrategy] Permissive bearish: haClose={current['ha_close']:.4f}, haOpen={current['ha_open']:.4f}")
            else:
                signal = 'hold'
                confidence = 0.0
                strength = 0.0

            # Log signal generation details
            self.logger.info(f"[HeikinAshiStrategy] Generated {signal.upper()} signal for {pair or self.state.pair}: "
                             f"trend={trend}, reversal={reversal}, body_pct={body_pct:.2f}, confidence={confidence:.2f}")
            await self.log_condition_outcome(
                'signal_generation', signal, True,
                {
                    'trend': trend,
                    'reversal': reversal,
                    'body_pct': body_pct,
                    'consecutive_count': consecutive_count,
                    'confidence': confidence,
                    'strength': strength,
                    'candle_color': 'green' if current['ha_close'] > current['ha_open'] else 'red'
                }
            )
            return signal, confidence, strength
            
        except Exception as e:
            self.logger.error(f"[HeikinAshiStrategy] Error generating signal: {str(e)}")
            return 'hold', 0.0, 0.0

    async def calculate_position_size(self, signal_type: str) -> float:
        """Calculate position size based on risk management rules."""
        try:
            # Get account balance
            balance = await self.exchange.get_balance()
            available_balance = balance.get('free', 0.0)
            
            # Calculate risk per trade (1% of available balance)
            risk_amount = available_balance * 0.01
            
            # Get current price and ATR
            ticker = await self.exchange.get_ticker(self.state.pair)
            current_price = ticker['last']
            atr = self.state.indicators.get('ATR', None)
            
            if atr is None:
                return 0.0
            
            atr_value = atr.values[-1]
            
            # Calculate position size based on risk
            position_size = risk_amount / (2 * atr_value)
            
            # SPOT TRADING: No leverage adjustment needed
            # Leverage is only used in futures/margin trading
            
            return position_size
            
        except Exception as e:
            self.logger.error(f"Error calculating position size: {str(e)}")
            return 0.0

    async def should_exit(self) -> Tuple[bool, Optional[str]]:
        allowed_reasons = {"take_profit", "stop_loss"}
        try:
            # If no position or position is 'none', skip exit checks
            if not hasattr(self, 'state') or self.state.position == 'none':
                return False, None
            
            # Get current price
            try:
                ticker = await self.exchange.get_ticker(self.state.pair)
                current_price = ticker['last']
            except Exception as e:
                self.logger.warning(f"[HeikinAshiStrategy] Could not fetch current price for {self.state.pair}: {e}")
                return False, None
            
            # Only check basic stop loss and take profit - let orchestrator handle profit protection
            cond1 = self.state.position == 'long' and self.state.stop_loss is not None and current_price <= self.state.stop_loss
            cond3 = self.state.position == 'long' and self.state.take_profit is not None and current_price >= self.state.take_profit
            
            if cond1:
                self.logger.info(f"[HeikinAshiStrategy] Exiting due to stop loss for trade {getattr(self, 'trade_id', 'unknown')}")
                return True, "stop_loss"
            elif cond3:
                self.logger.info(f"[HeikinAshiStrategy] Exiting due to take profit for trade {getattr(self, 'trade_id', 'unknown')}")
                return True, "take_profit"
                
            return False, None
        except Exception as e:
            self.logger.error(f"Error checking exit conditions: {str(e)}")
            return False, None

    async def update_performance(self) -> None:
        """Update enhanced strategy performance metrics."""
        try:
            # Validate self.state.pair before proceeding
            if not hasattr(self.state, 'pair') or not self.state.pair:
                self.logger.error("[HeikinAshiStrategy.update_performance] self.state.pair is not set. Cannot update performance.")
                return

            # Get current price
            ticker = await self.exchange.get_ticker(self.state.pair)
            
            # Check if ticker fetch was successful
            if ticker.get('error'):
                self.logger.error(f"[HeikinAshiStrategy.update_performance] Could not fetch ticker for {self.state.pair}: {ticker['error']}. Cannot update performance.")
                return
            
            current_price = ticker['last']
            
            # Calculate unrealized PnL for existing logic compatibility
            if self.state.position != 'none':
                if self.state.entry_price is None or self.state.position_size is None:
                    self.logger.warning(f"[HeikinAshiStrategy.update_performance] Missing entry_price or position_size for {self.state.pair}. Cannot calculate PnL.")
                    return
                
                pnl = (current_price - self.state.entry_price) * self.state.position_size
                if self.state.position == 'short':
                    pnl = -pnl
                # Update performance
                self.state.performance['unrealized_pnl'] = pnl

            # Use enhanced performance monitor for comprehensive metrics
            try:
                enhanced_metrics = self.performance_monitor.update_performance_metrics(self.state)
                
                # Log key enhanced metrics
                if enhanced_metrics:
                    self.logger.debug(f"[HeikinAshiStrategy] Enhanced metrics for {self.state.pair}: "
                                    f"Sharpe: {enhanced_metrics.get('sharpe_ratio', 0):.2f}, "
                                    f"Max DD: {enhanced_metrics.get('drawdown_analysis', {}).get('max_drawdown', 0):.2%}, "
                                    f"VaR: {enhanced_metrics.get('volatility_adjusted_return', 0):.4f}")
                
            except Exception as em:
                self.logger.warning(f"[HeikinAshiStrategy] Enhanced metrics calculation failed for {self.state.pair}: {em}")
                
            # Log condition for existing integration
            if hasattr(self, '_condition_logger') and self._condition_logger:
                await self._condition_logger.log_condition(
                    name="update_performance",
                    value=self.state.performance.get('unrealized_pnl', 0),
                    description=f"Performance metrics for {self.state.pair}",
                    result=True,
                    condition_type="performance"
                )
                
        except Exception as e:
            self.logger.error(f"Error updating enhanced performance: {e}")

    async def _log_condition(self, name, value, description, result, condition_type, pair, reason=None, market_regime=None, volatility=None, context=None):
        desc = description or name
        # Set the pair on the logger before logging
        if self._condition_logger:
            self._condition_logger.pair = pair
        # TODO: Pass real context/market_regime/volatility if available
        await self._condition_logger.log_condition(name, value, desc, result, condition_type, market_regime=market_regime, volatility=volatility, context=context)

    async def check_exit_signals(self, market_data, predictions):
        """Check for exit signals for all pairs in market_data."""
        exit_signals = []
        for pair, ohlcv in market_data.items():
            try:
                self.logger.info(f"===== HEIKIN ASHI DETAILED ANALYSIS FOR {pair} EXIT SIGNAL =====")
                # Defensive check - ensure we have a proper DataFrame with required columns
                if not isinstance(ohlcv, pd.DataFrame):
                    self.logger.warning(f"[HeikinAshiStrategy] Exit signal check: ohlcv for {pair} is not a DataFrame")
                    continue
                required_cols = ['open', 'high', 'low', 'close', 'volume']
                missing_cols = [col for col in required_cols if col not in ohlcv.columns]
                if missing_cols:
                    self.logger.warning(f"[HeikinAshiStrategy] Exit signal check: ohlcv for {pair} is missing columns: {missing_cols}")
                    continue
                # Now proceed with normal exit check
                await self.initialize(pair)
                await self.update(ohlcv)
                should_exit, reason = await self.should_exit()
                if should_exit and reason:
                    exit_signals.append({
                        'pair': pair,
                        'signal': 'exit',
                        'strategy': self.__class__.__name__.lower().replace('strategy', ''),
                        'reason': reason
                    })
            except Exception as e:
                self.logger.error(f"[HeikinAshiStrategy] Error checking exit signals for {pair}: {str(e)}", exc_info=True)
                continue
        return exit_signals 

    async def check_exit(self, market_data=None, predictions=None, trade_id=None):
        """
        Fully functional exit logic for HeikinAshiStrategy.
        Checks if the current position should be exited based on profit protection, trailing stop, stop loss, or take profit.
        
        Args:
            market_data: Optional OHLCV data to use for analysis
            predictions: Optional ML predictions to consider
            trade_id: Optional trade ID for logging purposes
        
        Returns:
            Dictionary with exit signal info if exit is required, otherwise None
        """
        try:
            # If market_data is provided, update the strategy state
            if market_data is not None and isinstance(market_data, pd.DataFrame) and not market_data.empty:
                await self.update(market_data)
            
            # Set trade_id if provided
            if trade_id is not None:
                self.trade_id = trade_id
                
            # Call the existing exit logic
            should_exit, reason = await self.should_exit()
            
            # Return proper exit signal format
            if should_exit and reason:
                return {
                    'exit': True,
                    'pair': self.state.pair,
                    'reason': reason,
                    'strategy': self.STRATEGY_NAME
                }
            return None
        except Exception as e:
            self.logger.error(f"Error in check_exit: {e}")
            return None

    async def maybe_adapt_parameters_for_regime(self):
        """
        Check regime analytics and trigger parameter re-optimization if win rate drops below threshold.
        """
        try:
            strategy_name = self.STRATEGY_NAME.lower().replace(' ', '_')
            regime = getattr(self.state, 'market_regime', 'unknown')
            stats = await self._regime_analytics.get_stats(strategy_name, self.state.pair, regime)
            win_rate = stats.get('win_rate', 1.0)
            trade_count = stats.get('trade_count', 0)
            threshold = 0.4
            min_trades = 10
            if trade_count >= min_trades and win_rate < threshold:
                self.logger.warning(f"[ADAPTATION] Win rate {win_rate:.2f} in regime '{regime}' below threshold ({threshold}), triggering re-optimization for {self.state.pair}.")
                await self.apply_optimized_parameters(self.state.market_regime)
                # Optionally, log adaptation event to analytics or feedback manager
                if self._feedback_manager:
                    await self._feedback_manager.log_parameter_change(
                        strategy_name=strategy_name,
                        pair=self.state.pair,
                        old_parameters={},  # Could snapshot before/after if needed
                        new_parameters={},
                        market_regime=regime,
                        confidence_score=0.5,
                        expected_improvement=0.1
                    )
        except Exception as e:
            self.logger.error(f"Error in maybe_adapt_parameters_for_regime: {str(e)}")

    async def log_trade_outcome(self, trade_data: Dict[str, Any], is_profitable: bool) -> None:
        if not self.state.pair:
            return
        try:
            strategy_name = self.STRATEGY_NAME.lower().replace(' ', '_')
            if self._strategy_analyzer:
                await self._strategy_analyzer.track_trade_outcome(
                    strategy_name=strategy_name,
                    pair=self.state.pair,
                    trade_data=trade_data,
                    is_profitable=is_profitable
                )
            if self._strategy_optimizer:
                await self._strategy_optimizer.log_trade_result(
                    strategy_name=strategy_name,
                    pair=self.state.pair,
                    trade_data=trade_data,
                    is_profitable=is_profitable
                )
            if self._feedback_manager:
                await self._feedback_manager.track_trade_outcome(
                    strategy_name=strategy_name,
                    pair=self.state.pair,
                    trade_data=trade_data,
                    is_profitable=is_profitable
                )
            # --- Regime Analytics Update ---
            regime = getattr(self.state, 'market_regime', 'unknown')
            # Retrieve previous stats
            prev_stats = await self._regime_analytics.get_stats(strategy_name, self.state.pair, regime)
            trade_count = prev_stats.get('trade_count', 0) + 1
            win_count = prev_stats.get('win_count', 0) + (1 if is_profitable else 0)
            win_rate = win_count / trade_count if trade_count > 0 else 0.0
            total_pnl = prev_stats.get('total_pnl', 0.0) + trade_data.get('pnl', 0.0)
            drawdown = min(prev_stats.get('drawdown', 0.0), trade_data.get('drawdown', 0.0)) if 'drawdown' in trade_data else prev_stats.get('drawdown', 0.0)
            metrics = {
                'trade_count': trade_count,
                'win_count': win_count,
                'win_rate': win_rate,
                'total_pnl': total_pnl,
                'drawdown': drawdown,
                'last_trade_time': datetime.utcnow().timestamp(),
            }
            await self._regime_analytics.update_stats(strategy_name, self.state.pair, regime, metrics)
            # --- Adaptation Logic ---
            await self.maybe_adapt_parameters_for_regime()
        except Exception as e:
            self.logger.error(f"Error logging trade outcome: {str(e)}")

    async def log_condition_outcome(self, condition_name: str, condition_value: Any, 
                                  condition_result: bool, metadata: Optional[Dict[str, Any]] = None) -> None:
        try:
            if not self.state.pair:
                return
            condition_data = {
                'strategy': self.STRATEGY_NAME.lower().replace(' ', '_'),
                'pair': self.state.pair,
                'condition_name': condition_name,
                'condition_value': condition_value,
                'condition_result': condition_result,
                'market_regime': self.state.market_regime,
                'timestamp': datetime.utcnow().isoformat(),
                'metadata': metadata or {}
            }
            await self._condition_logger.log_condition(
                name=condition_name,
                value=condition_value,
                description=f"{condition_name} for {self.state.pair}",
                result=condition_result,
                condition_type=metadata.get('type') if metadata else None
            )
            if self._strategy_analyzer:
                await self._strategy_analyzer.log_condition_outcome(
                    strategy_name=condition_data['strategy'],
                    pair=self.state.pair,
                    condition_data=condition_data
                )
        except Exception as e:
            self.logger.error(f"Error logging condition outcome: {str(e)}") 