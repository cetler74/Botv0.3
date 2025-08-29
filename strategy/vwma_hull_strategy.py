from fix_unrealized_pnl_fees import calculate_unrealized_pnl_with_fees

"""
VWMA Hull strategy implementation for the crypto trading bot.
Uses Volume Weighted Moving Average and Hull Moving Average for trend following.
"""
from typing import Dict, List, Optional, Any, Union, Tuple
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import logging
logger = logging.getLogger(__name__)

from strategy.base_strategy import BaseStrategy, StrategyState
from strategy.strategy_pnl_enhanced import calculate_unrealized_pnl, check_profit_protection, check_profit_protection_enhanced, manage_trailing_stop, restore_profit_protection_state
from strategy.condition_logger import ConditionLogger

class VWMAHullStrategy(BaseStrategy):
    """VWMA Hull strategy implementation."""
    STRATEGY_NAME = "VWMA Hull"

    def __init__(
        self,
        config: Dict[str, Any],
        exchange: Any,
        database: Any,
        redis_client=None,
        exchange_name=None
    ):
        """Initialize the VWMA Hull strategy. Uses ConditionLogger for validation and condition checks."""
        super().__init__(config, exchange, database, redis_client)
        self.exchange = exchange
        self.exchange_name = exchange_name or 'binance'  # Default to binance if not provided
        # Initialize logger first
        self.logger = logging.getLogger(__name__)
        # Don't override the condition logger from base class - it's already initialized with proper strategy name
        
        # Safely access config parameters with defaults and debug logging
        parameters = config.get('parameters', {})
        self.vwma_period = parameters.get('vwma_period', 20)
        self.hull_period = parameters.get('hull_period', 10)
        self.volume_threshold = parameters.get('volume_threshold', 1.1)  # Legacy parameter for backward compatibility
        self.trend_threshold = parameters.get('trend_threshold', 0.02)
        
        # New volume validation parameters
        self.volume_method = parameters.get('volume_method', 'percentile')  # percentile, median, adaptive, legacy
        self.volume_percentile = parameters.get('volume_percentile', 0.2)  # 20th percentile threshold
        self.min_absolute_volume = parameters.get('min_absolute_volume', 1000)  # Minimum absolute volume
        self.volume_window = parameters.get('volume_window', 50)  # Window for percentile calculation
        self.volatility_adjustment = parameters.get('volatility_adjustment', True)  # Enable adaptive thresholds
        
        # Scalping parameters
        self.atr_period = parameters.get('atr_period', 7)
        self.min_price_move = parameters.get('min_price_move', 0.001)  # 0.1% minimum price change
        self.min_volatility_threshold = parameters.get('min_volatility_threshold', 0.002)  # ATR > 0.2% of price
        self.scalping_enabled = parameters.get('scalping_enabled', True)
        self.crossover_confidence_boost = parameters.get('crossover_confidence_boost', 0.8)
        self.trend_confidence_boost = parameters.get('trend_confidence_boost', 0.7)
        self.strength_multiplier = parameters.get('strength_multiplier', 10)
        
        # Debug log the loaded parameters
        self.logger.info(f"VWMA Hull Strategy initialized with volume_method: {self.volume_method}, scalping_enabled: {self.scalping_enabled}")
        self.logger.debug(f"All parameters: vwma_period={self.vwma_period}, hull_period={self.hull_period}, "
                         f"volume_method={self.volume_method}, volume_percentile={self.volume_percentile}, "
                         f"min_absolute_volume={self.min_absolute_volume}, trend_threshold={self.trend_threshold}, "
                         f"atr_period={self.atr_period}, min_price_move={self.min_price_move}, "
                         f"min_volatility_threshold={self.min_volatility_threshold}")
        self.trade_id = None
        self._current_ohlcv = None  # For test compatibility
        self._regime_analytics = None # Removed as per edit hint
        
    def _validate_ohlcv_data(self, ohlcv: pd.DataFrame, required_cols: set) -> bool:
        """Validate OHLCV data quality and completeness."""
        try:
            # Check if it's a DataFrame
            if not isinstance(ohlcv, pd.DataFrame):
                self.logger.error(f"OHLCV data is not a DataFrame: {type(ohlcv)}")
                return False
                
            # Check if empty
            if ohlcv.empty:
                self.logger.error("OHLCV data is empty")
                return False
                
            # Check required columns
            if not required_cols.issubset(ohlcv.columns):
                missing = required_cols - set(ohlcv.columns)
                self.logger.error(f"OHLCV missing required columns: {missing}")
                return False
                
            # Check for sufficient data points
            if len(ohlcv) < max(self.vwma_period, self.hull_period):
                self.logger.error(f"Insufficient data points: {len(ohlcv)} < {max(self.vwma_period, self.hull_period)}")
                return False
                
            # Check for NaN values in critical columns
            for col in ['close', 'volume']:
                has_nan = bool(ohlcv[col].isna().any())
                if has_nan:
                    self.logger.error(f"NaN values found in {col} column")
                    return False
                    
            # Check for zero or negative prices
            if (ohlcv['close'] <= 0).any():
                self.logger.error("Zero or negative prices found in close column")
                return False
                
            # Check for negative volume
            if (ohlcv['volume'] < 0).any():
                self.logger.error("Negative volume found")
                return False
                
            return True
            
        except Exception as e:
            self.logger.error(f"Error validating OHLCV data: {e}")
            return False

    def _validate_volume_conditions(self, ohlcv: pd.DataFrame) -> Tuple[bool, str]:
        """Comprehensive volume validation with multiple checks."""
        try:
            current_volume = float(ohlcv['volume'].iloc[-1])
            
            # Check 1: Minimum absolute volume
            if current_volume < self.min_absolute_volume:
                return False, f"Volume too low (absolute): {current_volume} < {self.min_absolute_volume}"
            
            # Method selection based on configuration
            if self.volume_method == 'legacy':
                # Original method for backward compatibility
                if len(ohlcv) < 20:
                    return False, "Insufficient data for legacy volume calculation"
                try:
                    volume_series = ohlcv['volume']
                    if isinstance(volume_series, np.ndarray):
                        volume_series = pd.Series(volume_series)
                    elif not isinstance(volume_series, pd.Series):
                        volume_series = pd.Series(volume_series)
                    rolling_mean = volume_series.rolling(window=20).mean()
                    # Ensure rolling_mean is a pandas Series
                    if isinstance(rolling_mean, np.ndarray):
                        rolling_mean = pd.Series(rolling_mean)
                    avg_volume = float(rolling_mean.iloc[-1])
                    if current_volume < self.volume_threshold * avg_volume:
                        return False, f"Volume below legacy threshold: {current_volume} < {self.volume_threshold * avg_volume:.1f}"
                    return True, "Legacy volume validation passed"
                except Exception as e:
                    return False, f"Legacy volume calculation error: {e}"
            
            elif self.volume_method == 'percentile':
                # Percentile-based filter (primary recommendation)
                window_size = min(self.volume_window, len(ohlcv))
                if window_size < 10:
                    return False, f"Insufficient data for percentile calculation: {window_size} < 10"
                
                try:
                    volume_series = ohlcv['volume']
                    if isinstance(volume_series, np.ndarray):
                        volume_series = pd.Series(volume_series)
                    elif not isinstance(volume_series, pd.Series):
                        volume_series = pd.Series(volume_series)
                    rolling_quantile = volume_series.rolling(window=window_size).quantile(self.volume_percentile)
                    volume_percentile = float(rolling_quantile.iloc[-1])
                    if current_volume < volume_percentile:
                        return False, f"Volume below {self.volume_percentile*100:.0f}th percentile: {current_volume} < {volume_percentile:.1f}"
                    return True, f"Percentile volume validation passed: {current_volume} >= {volume_percentile:.1f}"
                except Exception as e:
                    return False, f"Percentile calculation error: {e}"
            
            elif self.volume_method == 'median':
                # Median-based filtering
                if len(ohlcv) < 20:
                    return False, "Insufficient data for median volume calculation"
                try:
                    volume_series = ohlcv['volume']
                    if isinstance(volume_series, np.ndarray):
                        volume_series = pd.Series(volume_series)
                    elif not isinstance(volume_series, pd.Series):
                        volume_series = pd.Series(volume_series)
                    rolling_median = volume_series.rolling(window=20).median()
                    robust_avg = float(rolling_median.iloc[-1])
                    if current_volume < self.volume_threshold * robust_avg:
                        return False, f"Volume below median threshold: {current_volume} < {self.volume_threshold * robust_avg:.1f}"
                    return True, "Median volume validation passed"
                except Exception as e:
                    return False, f"Median calculation error: {e}"
            
            elif self.volume_method == 'adaptive':
                # Adaptive threshold based on volatility
                if len(ohlcv) < 20:
                    return False, "Insufficient data for adaptive volume calculation"
                
                try:
                    volume_series = ohlcv['volume']
                    if isinstance(volume_series, np.ndarray):
                        volume_series = pd.Series(volume_series)
                    elif not isinstance(volume_series, pd.Series):
                        volume_series = pd.Series(volume_series)
                    rolling_median = volume_series.rolling(window=20).median()
                    robust_avg = float(rolling_median.iloc[-1])
                    
                    if self.volatility_adjustment:
                        rolling_std = volume_series.rolling(window=20).std()
                        volume_std = float(rolling_std.iloc[-1])
                        volume_cv = volume_std / robust_avg if robust_avg > 0 else 0
                        
                        # Adaptive threshold based on coefficient of variation
                        if volume_cv > 2.0:
                            threshold_multiplier = 0.7  # More lenient for volatile periods
                        elif volume_cv > 1.0:
                            threshold_multiplier = 0.9
                        else:
                            threshold_multiplier = self.volume_threshold
                            
                        adaptive_threshold = threshold_multiplier * robust_avg
                        if current_volume < adaptive_threshold:
                            return False, f"Volume below adaptive threshold: {current_volume} < {adaptive_threshold:.1f} (CV: {volume_cv:.2f})"
                        return True, f"Adaptive volume validation passed: {current_volume} >= {adaptive_threshold:.1f} (CV: {volume_cv:.2f})"
                    else:
                        # Simple median-based without volatility adjustment
                        if current_volume < self.volume_threshold * robust_avg:
                            return False, f"Volume below adaptive threshold: {current_volume} < {self.volume_threshold * robust_avg:.1f}"
                        return True, "Adaptive volume validation passed"
                except Exception as e:
                    return False, f"Adaptive calculation error: {e}"
            
            else:
                self.logger.warning(f"Unknown volume method: {self.volume_method}. Using percentile method.")
                # Fallback to percentile method
                window_size = min(self.volume_window, len(ohlcv))
                if window_size < 10:
                    return False, f"Insufficient data for fallback percentile calculation: {window_size} < 10"
                
                try:
                    volume_series = pd.Series(ohlcv['volume'])
                    rolling_quantile = volume_series.rolling(window=window_size).quantile(0.2)
                    volume_percentile = float(rolling_quantile.iloc[-1])
                    if current_volume < volume_percentile:
                        return False, f"Volume below 20th percentile (fallback): {current_volume} < {volume_percentile:.1f}"
                    return True, f"Fallback percentile volume validation passed: {current_volume} >= {volume_percentile:.1f}"
                except Exception as e:
                    return False, f"Fallback percentile error: {e}"
                
        except Exception as e:
            self.logger.error(f"Volume validation error: {e}")
            return False, f"Volume validation error: {e}"

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
        
        # Initialize condition logger with proper strategy name
        if not hasattr(self, 'condition_logger'):
            self.condition_logger = ConditionLogger(strategy_name=self.STRATEGY_NAME, logger_instance=self.logger)
        
        self.logger.info(f"Initialized VWMA Hull strategy for {pair}")

    def _calculate_vwma(self, ohlcv: pd.DataFrame, indicators_cache: Optional[dict] = None, pair: Optional[str] = None, timeframe: Optional[str] = None) -> pd.Series:
        """Calculate VWMA, using cache if provided."""
        cache_key = None
        if indicators_cache is not None and pair and timeframe:
            cache_key = f"VWMA_{pair}_{timeframe}_{self.vwma_period}"
            if cache_key in indicators_cache:
                self.logger.debug(f"[CACHE HIT] VWMA for {pair} {timeframe} (period={self.vwma_period})")
                return indicators_cache[cache_key]
        # Ensure ohlcv is a DataFrame and columns are Series
        close = ohlcv['close']
        if isinstance(close, np.ndarray):
            close = pd.Series(close)
        elif isinstance(close, pd.DataFrame):
            close = close['close'] if 'close' in close else pd.Series(close.iloc[:, 0])
        elif not isinstance(close, pd.Series):
            close = pd.Series(close)
        volume = ohlcv['volume']
        if isinstance(volume, np.ndarray):
            volume = pd.Series(volume)
        elif isinstance(volume, pd.DataFrame):
            volume = volume['volume'] if 'volume' in volume else pd.Series(volume.iloc[:, 0])
        elif not isinstance(volume, pd.Series):
            volume = pd.Series(volume)
        vwma = (close * volume).rolling(self.vwma_period).sum() / volume.rolling(self.vwma_period).sum()
        if indicators_cache is not None and cache_key:
            indicators_cache[cache_key] = vwma
            self.logger.debug(f"[CACHE STORE] VWMA for {pair} {timeframe} (period={self.vwma_period})")
        return vwma

    def _calculate_hull_ma(self, data: Union[pd.Series, pd.DataFrame, np.ndarray], indicators_cache: Optional[dict] = None, pair: Optional[str] = None, timeframe: Optional[str] = None) -> pd.Series:
        """Calculate Hull MA, using cache if provided."""
        cache_key = None
        if indicators_cache is not None and pair and timeframe:
            cache_key = f"HULL_{pair}_{timeframe}_{self.hull_period}"
            if cache_key in indicators_cache:
                self.logger.debug(f"[CACHE HIT] Hull MA for {pair} {timeframe} (period={self.hull_period})")
                return indicators_cache[cache_key]
        # Ensure data is a Series
        if isinstance(data, np.ndarray):
            data = pd.Series(data)
        elif isinstance(data, pd.DataFrame):
            data = data['close'] if 'close' in data else pd.Series(data.iloc[:, 0])
        elif not isinstance(data, pd.Series):
            data = pd.Series(data)
        
        # Ensure data is a pandas Series before using rolling
        if not isinstance(data, pd.Series):
            data = pd.Series(data)
            
        # Validate sufficient data for Hull MA calculation
        required_periods = max(self.hull_period, int(np.sqrt(self.hull_period)))
        if len(data) < required_periods:
            self.logger.warning(f"❌ [HULL MA] Insufficient data for Hull MA: {len(data)} < {required_periods} required")
            # Return NaN series with same index as input
            return pd.Series([np.nan] * len(data), index=data.index if hasattr(data, 'index') else None)
        
        half_length = int(self.hull_period / 2)
        sqrt_length = int(np.sqrt(self.hull_period))
        
        # Calculate WMAs with validation
        wma_half = data.rolling(half_length, min_periods=half_length).mean()
        wma_full = data.rolling(self.hull_period, min_periods=self.hull_period).mean()
        
        # Ensure rolling results are pandas Series
        if isinstance(wma_half, np.ndarray):
            wma_half = pd.Series(wma_half, index=data.index if hasattr(data, 'index') else None)
        if isinstance(wma_full, np.ndarray):
            wma_full = pd.Series(wma_full, index=data.index if hasattr(data, 'index') else None)
        
        # Calculate Hull MA
        hull_raw = 2 * wma_half - wma_full
        hull = hull_raw.rolling(sqrt_length, min_periods=sqrt_length).mean()
        
        # Ensure final result is pandas Series
        if isinstance(hull, np.ndarray):
            hull = pd.Series(hull, index=data.index if hasattr(data, 'index') else None)
        
        # Log calculation status
        valid_values = hull.dropna()
        self.logger.debug(f"✅ [HULL MA] Calculated Hull MA: {len(valid_values)} valid values out of {len(hull)} (period={self.hull_period})")
        
        if len(valid_values) == 0:
            self.logger.warning(f"⚠️ [HULL MA] All Hull MA values are NaN (period={self.hull_period}, data_len={len(data)})")
            
        if indicators_cache is not None and cache_key:
            indicators_cache[cache_key] = hull
            self.logger.debug(f"[CACHE STORE] Hull MA for {pair} {timeframe} (period={self.hull_period})")
        return hull

    def _calculate_atr(self, ohlcv: pd.DataFrame, indicators_cache: Optional[dict] = None, pair: Optional[str] = None, timeframe: Optional[str] = None) -> pd.Series:
        """Calculate Average True Range (ATR) for volatility measurement."""
        cache_key = None
        if indicators_cache is not None and pair and timeframe:
            cache_key = f"ATR_{pair}_{timeframe}_{self.atr_period}"
            if cache_key in indicators_cache:
                self.logger.debug(f"[CACHE HIT] ATR for {pair} {timeframe} (period={self.atr_period})")
                return indicators_cache[cache_key]
        
        try:
            import pandas_ta as ta
            # Calculate ATR using pandas-ta
            atr = ta.atr(ohlcv['high'], ohlcv['low'], ohlcv['close'], length=self.atr_period)
            
            # Convert to pandas Series if needed
            if isinstance(atr, np.ndarray):
                atr = pd.Series(atr)
            
            if indicators_cache is not None and cache_key:
                indicators_cache[cache_key] = atr
                self.logger.debug(f"[CACHE STORE] ATR for {pair} {timeframe} (period={self.atr_period})")
            
            return atr
        except Exception as e:
            self.logger.error(f"Error calculating ATR: {e}")
            return pd.Series([np.nan] * len(ohlcv))

    def _calculate_volatility(self, ohlcv: pd.DataFrame, indicators_cache: Optional[dict] = None, pair: Optional[str] = None, timeframe: Optional[str] = None) -> float:
        """Calculate current volatility as ATR percentage of price."""
        try:
            atr = self._calculate_atr(ohlcv, indicators_cache, pair, timeframe)
            if atr is None or len(atr) == 0:
                return 0.0
            
            current_atr = atr.iloc[-1]
            current_price = ohlcv['close'].iloc[-1]
            
            if pd.isna(current_atr) or pd.isna(current_price) or current_price == 0:
                return 0.0
            
            volatility = current_atr / current_price
            return float(volatility)
        except Exception as e:
            self.logger.error(f"Error calculating volatility: {e}")
            return 0.0

    def _detect_trend(self, vwma: pd.Series, hull: pd.Series) -> str:
        """Detect trend using VWMA and Hull MA."""
        # Calculate trend strength
        trend_strength = abs(vwma.iloc[-1] - hull.iloc[-1]) / hull.iloc[-1]
        
        # Use a much lower threshold for trend detection
        trend_threshold = self.trend_threshold * 0.1  # 10x more sensitive
        
        # Determine trend
        if vwma.iloc[-1] > hull.iloc[-1] and trend_strength > trend_threshold:
            return 'uptrend'
        elif vwma.iloc[-1] < hull.iloc[-1] and trend_strength > trend_threshold:
            return 'downtrend'
        else:
            return 'sideways'

    def _detect_crossover(self, vwma: pd.Series, hull: pd.Series) -> Optional[str]:
        """Detect VWMA and Hull MA crossover - TRUE crossover events only."""
        if len(vwma) < 2 or len(hull) < 2:
            return None
        
        # Get current and previous values
        prev_vwma = vwma.iloc[-2]
        curr_vwma = vwma.iloc[-1]
        prev_hull = hull.iloc[-2]
        curr_hull = hull.iloc[-1]
        
        # Check for bullish crossover (VWMA was below Hull, now above)
        if prev_vwma <= prev_hull and curr_vwma > curr_hull:
            return 'bullish'
        
        # Check for bearish crossover (VWMA was above Hull, now below)
        elif prev_vwma >= prev_hull and curr_vwma < curr_hull:
            return 'bearish'
        
        return None

    async def update(self, ohlcv: pd.DataFrame) -> None:
        """Update strategy with new OHLCV data."""
        try:
            # Required columns for OHLCV data (timestamp is not included in DataFrame)
            required_cols = {'open', 'high', 'low', 'close', 'volume'}
            
            # Validate OHLCV data structure and content
            if not self._validate_ohlcv_data(ohlcv, required_cols):
                # ENHANCED: Instead of aborting, try to work with available data if possible
                data_length = len(ohlcv) if ohlcv is not None else 0
                min_required = max(self.vwma_period, self.hull_period)
                
                if data_length < min_required:
                    # Try with reduced periods if data is limited
                    if data_length >= 5:  # Minimum viable data points
                        self.logger.warning(f"[VWMAHullStrategy] Limited data for {self.state.pair}: {data_length} points. Using reduced periods.")
                        
                        # Temporarily reduce periods for this update
                        original_vwma = self.vwma_period
                        original_hull = self.hull_period
                        
                        self.vwma_period = min(self.vwma_period, data_length - 1)
                        self.hull_period = min(self.hull_period, data_length - 1)
                        
                        # Ensure periods are at least 2
                        self.vwma_period = max(2, self.vwma_period)
                        self.hull_period = max(2, self.hull_period)
                        
                        self.logger.info(f"[VWMAHullStrategy] Adjusted periods: VWMA {original_vwma}->{self.vwma_period}, Hull {original_hull}->{self.hull_period}")
                        
                        # Continue with reduced periods...
                    else:
                        self.logger.error(f"[VWMAHullStrategy] OHLCV data validation failed for {self.state.pair}. Aborting update.")
                        return
                else:
                    self.logger.error(f"[VWMAHullStrategy] OHLCV data validation failed for {self.state.pair}. Aborting update.")
                    return

            self._current_ohlcv = ohlcv.copy() if isinstance(ohlcv, pd.DataFrame) else None
            missing_cols = required_cols - set(ohlcv.columns)
            if missing_cols:
                for col in missing_cols:
                    ohlcv[col] = np.nan
                self.logger.warning(f"[VWMAHullStrategy] OHLCV missing columns {missing_cols} for pair {self.state.pair}. Filled with NaN.")
            if not required_cols.issubset(ohlcv.columns):
                self.logger.error(f"[VWMAHullStrategy] OHLCV data still missing required columns for pair {self.state.pair}. Skipping update. Columns: {ohlcv.columns}")
                return
            try:
                for col in ['open', 'high', 'low', 'close', 'volume']:
                    if col in ohlcv.columns:
                        ohlcv[col] = ohlcv[col].astype(float)
                vwma = self._calculate_vwma(ohlcv)
                hull = self._calculate_hull_ma(vwma)
                # Store only the latest values to avoid serialization issues
                self.state.indicators['vwma'] = float(vwma.iloc[-1]) if not vwma.empty and not pd.isna(vwma.iloc[-1]) else 0.0
                self.state.indicators['hull'] = float(hull.iloc[-1]) if not hull.empty and not pd.isna(hull.iloc[-1]) else 0.0
                # Store additional metadata for analysis
                self.state.indicators['vwma_series_length'] = len(vwma)
                self.state.indicators['hull_series_length'] = len(hull)
                trend = self._detect_trend(vwma, hull)
                crossover = self._detect_crossover(vwma, hull)
                self.state.market_regime = trend
                if crossover:
                    self.state.patterns['crossover'] = {
                        'type': crossover,
                        'confidence': 0.8,
                        'timestamp': datetime.utcnow()
                    }
                # Enhanced market regime logging with detailed values
                if len(vwma) > 0 and len(hull) > 0:
                    current_vwma = vwma.iloc[-1]
                    current_hull = hull.iloc[-1]
                    trend_strength = abs(current_vwma - current_hull) / current_hull if current_hull > 0 else 0
                    await self._log_condition(
                        'market_regime', self.state.market_regime, f'market_regime for {self.state.pair}', True, 'trend', 
                        self.state.pair, 
                        context={'reason': 'Updated market regime after trend detection', 'trend_strength': trend_strength},
                        current_value=f"trend={trend}, vwma={current_vwma:.4f}, hull={current_hull:.4f}, strength={trend_strength:.4f}",
                        target_value=f"trend detection threshold={self.trend_threshold}"
                    )
                if 'crossover' in self.state.patterns:
                    await self.log_condition_outcome(
                        'crossover', self.state.patterns['crossover']['type'], True,
                        {'confidence': self.state.patterns['crossover']['confidence']}
                    )
                await self.update_performance()
            except Exception as e:
                self.logger.error(f"Error updating strategy state: {str(e)}")
        except Exception as e:
            self.logger.error(f"Error updating strategy state: {str(e)}")

    async def update_performance(self) -> None:
        """Update strategy performance metrics."""
        try:
            # Check if exchange is available
            if not self.exchange:
                self.logger.debug("[VWMAHullStrategy] Exchange not available for performance update")
                return
                
            # Get current price
            ticker = await self.exchange.get_ticker(self.state.pair, self.exchange_name)
            if not ticker or 'last' not in ticker:
                self.logger.warning(f"[VWMAHullStrategy] Could not fetch ticker for {self.state.pair} on {self.exchange_name}")
                return
            current_price = ticker['last']
            if self.state.position != 'none':
                # Calculate unrealized PnL
                self.logger.info(f"[DEBUG] PnL calc: trade_id={self.trade_id}, entry_price={self.state.entry_price}, current_price={current_price}, position_size={self.state.position_size}, position={self.state.position}")
                if self.state.position == 'long':
                    unrealized_pnl = calculate_unrealized_pnl_with_fees(self.state.entry_price, current_price, self.state.position_size)
                else:
                    unrealized_pnl = (self.state.entry_price - current_price) * self.state.position_size
                # Update performance
                self.state.performance['unrealized_pnl'] = unrealized_pnl
                await self.log_condition_outcome(
                    'unrealized_pnl', unrealized_pnl, True,
                    {'reason': 'Updated unrealized PnL'}
                )
        except Exception as e:
            self.logger.error(f"Error updating performance: {e}")

    async def apply_optimized_parameters(self, market_regime: Optional[str] = None, force: bool = False) -> bool:
        try:
            optimized_params = await self.get_optimized_parameters(market_regime)
            if optimized_params:
                old_params = {
                    'vwma_period': self.vwma_period,
                    'hull_period': self.hull_period,
                    'volume_threshold': self.volume_threshold,
                    'trend_threshold': self.trend_threshold
                }
                self.vwma_period = optimized_params.get('vwma_period', self.vwma_period)
                self.hull_period = optimized_params.get('hull_period', self.hull_period)
                self.volume_threshold = optimized_params.get('volume_threshold', self.volume_threshold)
                self.trend_threshold = optimized_params.get('trend_threshold', self.trend_threshold)
                if 'parameters' in self.config:
                    self.config['parameters']['vwma_period'] = self.vwma_period
                    self.config['parameters']['hull_period'] = self.hull_period
                    self.config['parameters']['volume_threshold'] = self.volume_threshold
                    self.config['parameters']['trend_threshold'] = self.trend_threshold
                self.logger.info(f"Applied optimized parameters for {self.state.pair}: {optimized_params}")
                
                # Log parameter change if feedback manager is available
                if hasattr(self, '_feedback_manager') and self._feedback_manager:
                    try:
                        await self._feedback_manager.log_parameter_change(
                            strategy_name=self.STRATEGY_NAME.lower().replace(' ', '_'),
                            pair=self.state.pair,
                            old_parameters=old_params,
                            new_parameters=optimized_params,
                            market_regime=market_regime,
                            confidence_score=0.8,
                            expected_improvement=0.05
                        )
                    except Exception as e:
                        self.logger.warning(f"Could not log parameter change to feedback manager: {e}")
                await self.log_condition_outcome(
                    'optimized_parameters_applied', str(optimized_params), True,
                    {'market_regime': market_regime}
                )
                return True
            return False
        except Exception as e:
            self.logger.error(f"Error applying optimized parameters: {str(e)}")
            return False

    def _apply_strategy_parameters(self, params: Dict[str, Any]) -> None:
        # Validate and apply parameters
        self.vwma_period = int(params.get('vwma_period', self.vwma_period))
        self.hull_period = int(params.get('hull_period', self.hull_period))
        self.volume_threshold = float(params.get('volume_threshold', self.volume_threshold))
        self.trend_threshold = float(params.get('trend_threshold', self.trend_threshold))

    async def generate_signal(self, market_data, indicators_cache: Optional[dict] = None, pair: Optional[str] = None, timeframe: Optional[str] = None, exchange_adapter=None) -> Tuple[str, float, float]:
        """Generate trading signal based on VWMA and Hull MA analysis with scalping conditions."""
        try:
            # Use exchange_adapter if provided to override self.exchange
            if exchange_adapter is not None:
                self.exchange = exchange_adapter
            
            # Handle both DataFrame and dict market_data formats
            if isinstance(market_data, dict):
                # If market_data is a dict with timeframes, use the primary timeframe
                if timeframe and timeframe in market_data:
                    ohlcv = market_data[timeframe]
                elif '1h' in market_data:
                    ohlcv = market_data['1h']
                else:
                    # Use the first available timeframe
                    first_tf = list(market_data.keys())[0]
                    ohlcv = market_data[first_tf]
            else:
                # If market_data is already a DataFrame
                ohlcv = market_data
            
            # Validate input data
            min_period = max(self.vwma_period, self.hull_period)
            if ohlcv is None or ohlcv.empty or len(ohlcv) < min_period:
                return 'hold', 0.0, 0.0

            # Calculate indicators
            vwma = self._calculate_vwma(ohlcv, indicators_cache, pair, timeframe)
            hull = self._calculate_hull_ma(vwma, indicators_cache, pair, timeframe)
            atr = self._calculate_atr(ohlcv, indicators_cache, pair, timeframe)
            
            if vwma is None or hull is None or atr is None or len(vwma) < 2 or len(hull) < 2:
                return 'hold', 0.0, 0.0

            current_vwma, prev_vwma = vwma.iloc[-1], vwma.iloc[-2]
            current_hull, prev_hull = hull.iloc[-1], hull.iloc[-2]
            current_price, prev_price = ohlcv['close'].iloc[-1], ohlcv['close'].iloc[-2]
            atr_value = atr.iloc[-1]

            if pd.isna(current_vwma) or pd.isna(current_hull) or pd.isna(prev_vwma) or pd.isna(prev_hull):
                return 'hold', 0.0, 0.0

            # Volume validation (simplified for speed)
            volume_valid, volume_message = self._validate_volume_conditions(ohlcv)
            if not volume_valid:
                return 'hold', 0.0, 0.0

            # Trend and crossover
            trend = self._detect_trend(vwma, hull)
            crossover = self._detect_crossover(vwma, hull)
            trend_strength = abs(current_vwma - current_hull) / current_hull if current_hull > 0 else 0
            price_change = abs(current_price - prev_price) / prev_price if prev_price > 0 else 0

            # Scalping conditions
            signal, confidence, strength, reason = 'hold', 0.0, 0.0, []
            min_price_move = self.min_price_move  # 0.1% price change
            min_volatility = atr_value / current_price > self.min_volatility_threshold  # ATR > 0.2% of price

            if crossover == 'bullish' and min_volatility and price_change > min_price_move:
                signal = 'buy'
                confidence = 0.8 + min(trend_strength * 10, 0.2)
                strength = min(trend_strength * 10, 1.0)
                reason.append('bullish_crossover')
            elif crossover == 'bearish' and min_volatility and price_change > min_price_move:
                signal = 'sell'
                confidence = 0.8 + min(trend_strength * 10, 0.2)
                strength = min(trend_strength * 10, 1.0)
                reason.append('bearish_crossover')
            elif current_vwma > current_hull and trend_strength > self.trend_threshold and min_volatility:
                signal = 'buy'
                confidence = 0.7 + min(trend_strength * 8, 0.2)
                strength = min(trend_strength * 8, 1.0)
                reason.append('vwma_above_hull')
            elif current_vwma < current_hull and trend_strength > self.trend_threshold and min_volatility:
                signal = 'sell'
                confidence = 0.7 + min(trend_strength * 8, 0.2)
                strength = min(trend_strength * 8, 1.0)
                reason.append('vwma_below_hull')

            reason_str = '+'.join(reason) if reason else 'no_signal'
            self.logger.info(f"[VWMAHullStrategy] Generated {signal.upper()} signal for {pair or self.state.pair}: "
                             f"trend={trend}, crossover={crossover}, strength={strength:.3f}, confidence={confidence:.2f}, reason={reason_str}")
            # Enhanced condition logging with detailed values
            await self._log_condition(
                'signal_generation', signal, f'signal_generation for {pair or self.state.pair}', True, 'signal', 
                pair or self.state.pair, 
                context={'trend': trend, 'crossover': crossover, 'trend_strength': trend_strength, 'confidence': confidence, 'strength': strength, 'volume_valid': volume_valid, 'reason': reason_str},
                current_value=f"trend={trend}, crossover={crossover}, strength={strength:.3f}, confidence={confidence:.2f}",
                target_value="signal generation conditions met"
            )
            return signal, confidence, strength
        except Exception as e:
            self.logger.error(f"[VWMAHullStrategy] Error generating signal: {str(e)}")
            return 'hold', 0.0, 0.0

    async def calculate_position_size(self, signal_type: str) -> float:
        """Calculate position size based on risk management rules."""
        try:
            # Check if exchange is available
            if not self.exchange:
                self.logger.debug("[VWMAHullStrategy] Exchange not available for position size calculation")
                return 0.0
                
            # Get account balance
            balance = await self.exchange.get_balance()
            available_balance = balance.get('free', 0.0)
            
            # Calculate risk per trade (1% of available balance)
            risk_amount = available_balance * 0.01
            
            # Get current price and ATR
            ticker = await self.exchange.get_ticker(self.state.pair, self.exchange_name)
            if not ticker or 'last' not in ticker:
                self.logger.warning(f"[VWMAHullStrategy] Could not fetch ticker for {self.state.pair} on {self.exchange_name}")
                return 0.0
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
        """Check if current position should be exited based on strategy and fallback conditions."""
        try:
            # No position, nothing to exit
            if not hasattr(self, 'state') or self.state.position == 'none':
                self.logger.debug("[VWMAHullStrategy] No open position, should_exit=False")
                return False, None

            # Ensure we have recent OHLCV data
            if not hasattr(self, '_current_ohlcv') or self._current_ohlcv is None:
                self.logger.warning("[VWMAHullStrategy] No OHLCV data for exit check.")
                return False, None

            ohlcv = self._current_ohlcv
            current_price = ohlcv['close'].iloc[-1]
            pair = getattr(self.state, 'pair', 'N/A')

            # 1. Stop Loss
            if self.state.stop_loss is not None and current_price <= self.state.stop_loss:
                self.logger.info(f"[VWMAHullStrategy] Exiting {pair} due to stop loss: {current_price} <= {self.state.stop_loss}")
                await self._log_condition('exit_signal', 'stop_loss', 'Stop loss triggered', True, 'exit', pair, reason='stop_loss', context={'current_price': current_price, 'stop_loss': self.state.stop_loss})
                return True, 'stop_loss'

            # 2. Take Profit
            if self.state.take_profit is not None and current_price >= self.state.take_profit:
                self.logger.info(f"[VWMAHullStrategy] Exiting {pair} due to take profit: {current_price} >= {self.state.take_profit}")
                await self._log_condition('exit_signal', 'take_profit', 'Take profit triggered', True, 'exit', pair, reason='take_profit', context={'current_price': current_price, 'take_profit': self.state.take_profit})
                return True, 'take_profit'

            # 3. VWMA/Hull crossover (technical exit)
            vwma = self.state.indicators.get('VWMA')
            hull = self.state.indicators.get('Hull')
            if vwma is not None and hull is not None and len(vwma) > 1 and len(hull) > 1:
                prev_vwma, prev_hull = vwma[-2], hull[-2]
                curr_vwma, curr_hull = vwma[-1], hull[-1]
                # Exit if VWMA crosses below Hull (trend reversal)
                if prev_vwma > prev_hull and curr_vwma < curr_hull:
                    self.logger.info(f"[VWMAHullStrategy] Exiting {pair} due to VWMA/Hull crossover: {prev_vwma:.2f}->{curr_vwma:.2f} crossed {prev_hull:.2f}->{curr_hull:.2f}")
                    await self._log_condition('exit_signal', 'crossover', 'VWMA/Hull crossover exit', True, 'exit', pair, reason='crossover', context={'prev_vwma': prev_vwma, 'prev_hull': prev_hull, 'curr_vwma': curr_vwma, 'curr_hull': curr_hull})
                    return True, 'crossover'

            # 4. Fallback: time-based exit (e.g., 48h max hold)
            if self.state.entry_time:
                time_in_trade = datetime.utcnow() - self.state.entry_time
                if time_in_trade.total_seconds() > 172800:  # 48 hours
                    self.logger.info(f"[VWMAHullStrategy] Exiting {pair} due to max hold time exceeded: {time_in_trade}")
                    await self._log_condition('exit_signal', 'max_hold', 'Max hold time exceeded', True, 'exit', pair, reason='max_hold', context={'time_in_trade': str(time_in_trade)})
                    return True, 'max_hold'

            self.logger.debug(f"[VWMAHullStrategy] No exit condition met for {pair}.")
            return False, None
        except Exception as e:
            self.logger.error(f"Error in should_exit: {e}")
            return False, None

    async def _log_condition(self, name, value, description, result, condition_type, pair, reason=None, market_regime=None, volatility=None, context=None, target_value=None, current_value=None):
        desc = description or name
        # Set the pair on the logger before logging
        if self.condition_logger:
            self.condition_logger.pair = pair
        # Pass available context/market_regime/volatility
        current_market_regime = getattr(self.state, 'market_regime', market_regime) if hasattr(self, 'state') else market_regime
        current_volatility = getattr(self.state, 'volatility', volatility) if hasattr(self, 'state') else volatility
        
        # Get exchange name
        exchange_name = getattr(self, 'exchange_name', None)
        
        await self.condition_logger.log_condition(
            name, value, desc, result, condition_type, 
            market_regime=current_market_regime, 
            volatility=current_volatility, 
            context=context,
            exchange=exchange_name,
            target_value=target_value,
            current_value=current_value
        )

    def _log_detailed_analysis(self, ohlcv, vwma, hull, trend, trend_strength, trend_duration, crossover, crossover_confidence, prev_vwma, prev_hull, current_vwma, current_hull, entry_conditions, all_met, signal, signal_confidence, signal_strength, volume, avg_volume, data_points, sufficient_data):
        import datetime
        GREEN = '\033[92m'
        RED = '\033[91m'
        END = '\033[0m'
        check = f"{GREEN}✓{END}"
        cross = f"{RED}✗{END}"
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        pair = getattr(self.state, 'pair', 'N/A')
        lines = []
        lines.append(f"[{now}] [INFO] [{pair}] VWMA-Hull Strategy Update")
        lines.append("-----------------------------------------------")
        # INDICATOR CALCULATION
        lines.append("INDICATOR CALCULATION:")
        data_valid_status = 'TRUE' if ohlcv is not None and not ohlcv.empty and len(ohlcv) >= 3 else 'FALSE'
        lines.append(f"OHLCV Data Valid: {data_valid_status} {check if data_valid_status == 'TRUE' else cross}")
        vwma_valid = 'TRUE' if vwma is not None and len(vwma) > 0 else 'FALSE'
        lines.append(f"VWMA({self.vwma_period}) calculation successful: {vwma_valid} {check if vwma_valid == 'TRUE' else cross}")
        hull_valid = 'TRUE' if hull is not None and len(hull) > 0 else 'FALSE'
        lines.append(f"Hull({self.hull_period}) calculation successful: {hull_valid} {check if hull_valid == 'TRUE' else cross}")
        if not pd.isna(current_vwma):
            lines.append(f"Current VWMA: {current_vwma:.2f}")
        else:
            lines.append(f"Current VWMA: N/A")
        if not pd.isna(current_hull):
            lines.append(f"Current Hull: {current_hull:.2f}")
        else:
            lines.append(f"Current Hull: N/A")
        lines.append(f"Data Points Used: {data_points} (sufficient: {'TRUE' if sufficient_data else 'FALSE'}) {check if sufficient_data else cross}")
        lines.append("")
        # TREND ANALYSIS
        lines.append("TREND ANALYSIS:")
        lines.append(f"Detected Trend: {trend}")
        lines.append(f"Trend Strength: {trend_strength:.3f} (Threshold: {self.trend_threshold}) {check if trend_strength > self.trend_threshold else cross}")
        lines.append(f"Trend Duration: {trend_duration} candles")
        lines.append("")
        # PATTERN DETECTION - Enhanced crossover analysis
        lines.append("PATTERN DETECTION:")
        crossover_detected = crossover is not None and crossover != 'N/A'
        lines.append(f"Crossover Detected: {crossover if crossover else 'None'} {check if crossover_detected else cross}")
        if not pd.isna(prev_vwma) and not pd.isna(prev_hull):
            lines.append(f"Previous: VWMA({prev_vwma:.2f}) {'<' if prev_vwma < prev_hull else '>' if prev_vwma > prev_hull else '='} Hull({prev_hull:.2f})")
        else:
            lines.append(f"Previous: VWMA(N/A) vs Hull(N/A)")
        if not pd.isna(current_vwma) and not pd.isna(current_hull):
            lines.append(f"Current: VWMA({current_vwma:.2f}) {'>' if current_vwma > current_hull else '<' if current_vwma < current_hull else '='} Hull({current_hull:.2f})")
        else:
            lines.append(f"Current: VWMA(N/A) vs Hull(N/A)")
        lines.append(f"Crossover Confidence: {crossover_confidence:.1f}")
        lines.append("")
        # CONDITION EVALUATION - Enhanced with proper pass/fail indicators
        lines.append("CONDITION EVALUATION:")
        for cond, (curr, target, result) in entry_conditions.items():
            lines.append(f"Entry:{cond} = {curr} (Target: {target}) {check if result else cross}")
        lines.append(f"All Entry Conditions Met: {'TRUE' if all_met else 'FALSE'} {check if all_met else cross}")
        lines.append("")
        # VOLUME ANALYSIS
        lines.append("VOLUME ANALYSIS:")
        if not pd.isna(volume) and not pd.isna(avg_volume) and volume > 0 and avg_volume > 0:
            vol_status = "ABOVE" if volume > avg_volume else "BELOW"
            vol_change = ((volume - avg_volume) / avg_volume * 100) if avg_volume > 0 else 0
            lines.append(f"Current Volume: {volume:.0f}")
            lines.append(f"Avg Volume: {avg_volume:.0f}")
            lines.append(f"Volume vs Average: {vol_status} ({vol_change:+.1f}%) {check if volume > avg_volume * 0.8 else cross}")  # More lenient for scalping
        elif not pd.isna(volume) and volume > 0:
            lines.append(f"Current Volume: {volume:.0f} (avg not available) {check}")  # Still valid for scalping
        else:
            lines.append(f"Volume data: Insufficient {cross}")
        lines.append("")
        # SIGNAL GENERATION
        lines.append("SIGNAL GENERATION:")
        lines.append(f"Generated Signal: {signal.upper()}")
        lines.append(f"Signal Confidence: {signal_confidence:.1f}")
        lines.append(f"Signal Strength: {signal_strength:.3f}")
        lines.append("===============================================")
        for line in lines:
            self.logger.info(line)

    async def analyze_pair(self, pair, min_candles=None, exchange_name=None):
        timeframes = ['1h', '15m']
        results = {}
        for timeframe in timeframes:
            logger.info(f"[VWMAHullStrategy] Analyzing {pair} on {timeframe}")
            df = await self.exchange.get_ohlcv(exchange_name, pair, timeframe, limit=min_candles or 50)
            if df is None or len(df) < (min_candles or 50):
                logger.warning(f"Not enough data for {pair} on {timeframe}")
                results[timeframe] = ('hold', 0, {})
                continue
            # Example indicator calculation and checks
            import pandas_ta as ta
            indicators = {}
            try:
                adx = ta.adx(df['high'], df['low'], df['close'], length=14)
                rsi = ta.rsi(df['close'], length=14)
                indicators = {
                    'adx': adx[-1],
                    'rsi': rsi[-1],
                }
            except Exception as e:
                logger.error(f"[VWMAHullStrategy] Indicator calculation error for {pair} on {timeframe}: {e}")
                results[timeframe] = ('hold', 0, indicators)
                continue
            logger.info(f"[VWMAHullStrategy] {pair} {timeframe} indicators: {indicators}")
            for name, value in indicators.items():
                if pd.isna(value):
                    logger.warning(f"[VWMAHullStrategy] Indicator {name} is NaN for {pair} on {timeframe}, holding.")
                    results[timeframe] = ('hold', 0, indicators)
                    break
            else:
                adx_pass = indicators['adx'] > self.adx_threshold
                logger.info(f"[VWMAHullStrategy] ADX check: current={indicators['adx']}, target>{self.adx_threshold}, result={'PASS' if adx_pass else 'FAIL'}")
                rsi_pass = indicators['rsi'] > 50
                logger.info(f"[VWMAHullStrategy] RSI check: current={indicators['rsi']}, target>50, result={'PASS' if rsi_pass else 'FAIL'}")
                checks = [adx_pass, rsi_pass]
                if all(checks):
                    logger.info(f"[VWMAHullStrategy] Signal for {pair} on {timeframe}: BUY (all conditions met)")
                    results[timeframe] = ('buy', 1, indicators)
                else:
                    logger.info(f"[VWMAHullStrategy] Signal for {pair} on {timeframe}: HOLD (not all conditions met)")
                    results[timeframe] = ('hold', 0, indicators)
        return results
        
    async def check_exit(self, market_data=None, predictions=None, trade_id=None):
        """
        Fully functional exit logic for VWMAHullStrategy.
        Checks if the current position should be exited based on profit protection, trailing stop, stop loss, or take profit.
        
        Args:
            market_data: Optional OHLCV data to use for analysis
            predictions: Optional ML predictions to consider
            trade_id: Optional trade ID for logging purposes
        
        Returns:
            True if exit is required, otherwise False
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
            
            # Return result
            if should_exit:
                return True
            return False
        except Exception as e:
            self.logger.error(f"Error in check_exit: {e}")
            return False

    async def log_trade_outcome(self, trade_result: Dict[str, Any], is_profitable: bool) -> None:
        """Log trade outcome with proper parameter handling."""
        try:
            # Extract trade result data
            trade_id = trade_result.get('trade_id')
            pnl = trade_result.get('pnl', 0.0)
            exit_price = trade_result.get('exit_price', 0.0)
            exit_time = trade_result.get('exit_time')
            exit_reason = trade_result.get('exit_reason', 'unknown')
            
            # Log the trade outcome
            await super().log_trade_outcome({
                'trade_id': trade_id,
                'pnl': pnl,
                'exit_price': exit_price,
                'exit_time': exit_time,
                'exit_reason': exit_reason
            }, is_profitable)
            
        except Exception as e:
            self.logger.error(f"Error logging trade outcome: {str(e)}")

    async def maybe_adapt_parameters_for_regime(self):
        """
        Check regime analytics and trigger parameter re-optimization if win rate drops below threshold.
        """
        try:
            strategy_name = self.STRATEGY_NAME.lower().replace(' ', '_')
            regime = getattr(self.state, 'market_regime', 'unknown')
            # Removed as per edit hint
            # stats = await self._regime_analytics.get_stats(strategy_name, self.state.pair, regime)
            # win_rate = stats.get('win_rate', 1.0)
            # trade_count = stats.get('trade_count', 0)
            # if trade_count >= 10 and win_rate < 0.4:
            #     await self.apply_optimized_parameters(regime, force=True)
            #     self.logger.info(f"[ADAPT] {strategy_name} re-optimized for regime {regime} due to low win rate ({win_rate})")
            pass # Placeholder for future regime adaptation logic
        except Exception as e:
            self.logger.warning(f"Regime adaptation check failed: {e}")

    # Removing duplicate check_entry_signals and check_exit_signals methods
    # These are now implemented in BaseStrategy with defensive checks 