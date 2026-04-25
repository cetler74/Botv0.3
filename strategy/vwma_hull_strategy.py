# Remove problematic import that doesn't exist
# from fix_unrealized_pnl_fees import calculate_unrealized_pnl_with_fees

"""
VWMA Hull strategy implementation for the crypto trading bot.

Design (VWMA-Hull-Hybrid v1):
  • Core engine = battle-tested VWMA-vs-Hull(VWMA) cross with hard vetos
    (volume validation, ADX>=22, 4h macro EMA9>EMA21). All production-hardened
    paths (regime overrides, volume methods, SL/TP, profit protection) preserved.

  • Canonical VWMAHull line (NEW) = single indicator built from Hull's formula
    applied to volume-weighted prices:
        vwma_half = VWMA(close, volume, n/2)
        vwma_full = VWMA(close, volume, n)
        raw       = 2 * vwma_half - vwma_full
        vwma_hull = WMA(raw, int(sqrt(n)))    # one line
    Used as a SOFT CONFIRMATION boost for entries when `close > vwma_hull`,
    matching the spec's primary entry rule. Never replaces the existing
    crossover engine — it nudges signal_strength up.

  • Hull MA bug fix = `_calculate_hull_ma` now uses real linearly-WEIGHTED
    moving averages (WMAs), not SMAs. The previous SMA implementation
    destroyed the low-lag advantage that HMA promises.

  • Soft confirmations (NEW):
      - close > vwma_hull line       (+vwma_hull_boost)
      - HMA slope up (last vs prev)  (+hma_slope_boost)
      - RSI(14) > rsi_long_floor     (+rsi_boost)
    Each is configurable; bearish counterparts apply symmetric penalties.

  • Bug fixes (NEW):
      - should_exit() crossover-back exit: the dead-code `'VWMA'/'Hull'`
        keys (indicators stored as lowercase scalars) are replaced with
        a live recomputation from `_current_ohlcv`.
      - calculate_position_size() ATR access: now computed live from
        `_current_ohlcv` (was reading the never-set `'ATR'` key and
        always returning 0).
      - analyze_pair(): replaced AttributeError on `self.adx_threshold`
        and the broken `adx[-1]` indexing with the same canonical
        VWMAHull + ADX + RSI logic used in generate_signal.
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

        # PnL-FIX v3 hard veto used by generate_signal (was hard-coded to 22).
        self.adx_threshold = float(parameters.get('adx_threshold', 22.0))
        self.adx_period = int(parameters.get('adx_period', 14))

        # VWMA-Hull-Hybrid v1: canonical single-line VWMAHull + soft confirmations.
        # All boosts are additive nudges to confidence/strength — never vetos.
        self.enable_canonical_vwma_hull = parameters.get('enable_canonical_vwma_hull', True)
        self.vwma_hull_boost = float(parameters.get('vwma_hull_boost', 0.10))      # close > VWMAHull
        self.hma_slope_boost = float(parameters.get('hma_slope_boost', 0.05))      # HMA slope up
        self.rsi_long_floor = float(parameters.get('rsi_long_floor', 50.0))         # spec: RSI(14)>50 for longs
        self.rsi_short_ceiling = float(parameters.get('rsi_short_ceiling', 50.0))   # symmetric for shorts
        self.rsi_period_long = int(parameters.get('rsi_period_long', 14))           # spec defaults to 14
        self.rsi_boost = float(parameters.get('rsi_boost', 0.05))
        
        # Debug log the loaded parameters
        self.logger.info(f"VWMA Hull Strategy initialized with volume_method: {self.volume_method}, scalping_enabled: {self.scalping_enabled}")
        self.logger.info(
            f"VWMA-Hull-Hybrid v1: canonical_vwma_hull={self.enable_canonical_vwma_hull}, "
            f"boosts (vwma_hull=+{self.vwma_hull_boost:.2f}, hma_slope=+{self.hma_slope_boost:.2f}, "
            f"rsi=+{self.rsi_boost:.2f}), rsi_long_floor={self.rsi_long_floor:.0f}, "
            f"adx_threshold={self.adx_threshold:.1f}"
        )
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
                
            # Data quality validation - check for sufficient price movement
            price_range = (ohlcv['high'].max() - ohlcv['low'].min()) / ohlcv['close'].mean() if ohlcv['close'].mean() > 0 else 0
            avg_volume = ohlcv['volume'].mean()
            if price_range < 0.001 or avg_volume < 1.0:  # Less than 0.1% price range or minimal volume
                self.logger.warning(f"[VWMAHullStrategy] Insufficient price movement ({price_range:.6f}) or volume ({avg_volume:.1f}) for {self.state.pair}")
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

    @staticmethod
    def _wma(series: pd.Series, length: int) -> pd.Series:
        """
        Linearly-weighted moving average (WMA).

        WMA gives the most recent value the highest weight (proportional to
        position). This is the *correct* MA for Hull's formula. Previously
        this strategy used .rolling().mean() (SMA), destroying HMA's low-lag
        property entirely.
        """
        if series is None or len(series) == 0 or length <= 0:
            return pd.Series([], dtype=float)
        if not isinstance(series, pd.Series):
            series = pd.Series(series)
        weights = np.arange(1, length + 1, dtype=float)
        wsum = weights.sum()

        def _w(window):
            return float(np.dot(window, weights) / wsum)

        return series.rolling(window=length, min_periods=length).apply(_w, raw=True)

    def _calculate_hull_ma(self, data: Union[pd.Series, pd.DataFrame, np.ndarray], indicators_cache: Optional[dict] = None, pair: Optional[str] = None, timeframe: Optional[str] = None) -> pd.Series:
        """
        Canonical Hull Moving Average (HMA).

        HMA = WMA( 2 * WMA(price, n/2) - WMA(price, n), int(sqrt(n)) )

        VWMA-Hull-Hybrid v1 BUG FIX: previously used SMA (`.rolling().mean()`)
        for all three legs which gave the indicator the same lag as a regular
        SMA — defeating the entire reason HMA exists. Now uses real WMAs.
        """
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

        # Validate sufficient data for Hull MA calculation
        required_periods = self.hull_period + max(int(np.sqrt(self.hull_period)), 1)
        if len(data) < required_periods:
            self.logger.warning(f"[HULL MA] Insufficient data for Hull MA: {len(data)} < {required_periods} required")
            return pd.Series([np.nan] * len(data), index=data.index if hasattr(data, 'index') else None)

        half_length = max(int(self.hull_period / 2), 1)
        sqrt_length = max(int(np.sqrt(self.hull_period)), 1)

        wma_half = self._wma(data, half_length)
        wma_full = self._wma(data, self.hull_period)
        hull_raw = 2 * wma_half - wma_full
        hull = self._wma(hull_raw, sqrt_length)

        valid_values = hull.dropna()
        self.logger.debug(
            f"[HULL MA] WMA-based Hull MA computed: {len(valid_values)} valid out of {len(hull)} "
            f"(period={self.hull_period}, half={half_length}, sqrt={sqrt_length})"
        )
        if len(valid_values) == 0:
            self.logger.warning(f"[HULL MA] All Hull MA values are NaN (period={self.hull_period}, data_len={len(data)})")

        if indicators_cache is not None and cache_key:
            indicators_cache[cache_key] = hull
            self.logger.debug(f"[CACHE STORE] Hull MA for {pair} {timeframe} (period={self.hull_period})")
        return hull

    def _calculate_vwma_hull(self, ohlcv: pd.DataFrame, indicators_cache: Optional[dict] = None, pair: Optional[str] = None, timeframe: Optional[str] = None) -> pd.Series:
        """
        Canonical single-line VWMAHull indicator.

        Substitutes volume-weighted prices into Hull's formula:
            vwma_half = VWMA(close, volume, n/2)
            vwma_full = VWMA(close, volume, n)
            raw       = 2 * vwma_half - vwma_full
            vwma_hull = WMA(raw, int(sqrt(n)))

        The result is ONE line that the spec compares against `close`:
        long when `close > vwma_hull`, short when `close < vwma_hull`.
        """
        cache_key = None
        if indicators_cache is not None and pair and timeframe:
            cache_key = f"VWMAHULL_{pair}_{timeframe}_{self.vwma_period}"
            if cache_key in indicators_cache:
                self.logger.debug(f"[CACHE HIT] VWMAHull for {pair} {timeframe} (period={self.vwma_period})")
                return indicators_cache[cache_key]

        if ohlcv is None or len(ohlcv) == 0:
            return pd.Series([], dtype=float)
        n = max(int(self.vwma_period), 2)
        half_n = max(int(n / 2), 1)
        sqrt_n = max(int(np.sqrt(n)), 1)
        if len(ohlcv) < n + sqrt_n:
            return pd.Series([np.nan] * len(ohlcv), index=ohlcv.index)

        close = ohlcv['close'].astype(float)
        volume = ohlcv['volume'].astype(float)
        vol_close = close * volume

        vwma_half = vol_close.rolling(half_n).sum() / volume.rolling(half_n).sum()
        vwma_full = vol_close.rolling(n).sum() / volume.rolling(n).sum()
        raw = 2 * vwma_half - vwma_full
        vwma_hull = self._wma(raw, sqrt_n)

        if indicators_cache is not None and cache_key:
            indicators_cache[cache_key] = vwma_hull
            self.logger.debug(f"[CACHE STORE] VWMAHull for {pair} {timeframe} (period={self.vwma_period})")
        return vwma_hull

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
                    # Calculate unrealized PnL with estimated fees (0.1% trading fee)
                    unrealized_pnl = ((current_price - self.state.entry_price) * self.state.position_size) - (self.state.position_size * current_price * 0.001)
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
        """Generate trading signal based on VWMA and Hull MA analysis with scalping conditions.

        PnL-FIX v3:
          - Added ADX >= 22 as a HARD VETO (was not checked at all in prior version).
          - Added 4h macro-trend alignment check (reject longs when 4h EMA9<EMA21).
          - Keeps existing volume/volatility checks (already hard vetos).
        """
        try:
            import pandas_ta as ta
            # Use exchange_adapter if provided to override self.exchange
            if exchange_adapter is not None:
                self.exchange = exchange_adapter

            # OPTION-A: apply regime-driven parameter overrides before any
            # downstream condition reads self.<attr>. Safe no-op if no
            # regime_overrides block is configured for this strategy.
            try:
                self._apply_regime_overrides(getattr(self.state, 'market_regime', None))
            except Exception as _ra_err:
                self.logger.debug(f"[VWMAHullStrategy] regime-override apply failed: {_ra_err}")

            # Handle both DataFrame and dict market_data formats
            if isinstance(market_data, dict):
                tf_data = market_data
                if timeframe and timeframe in market_data:
                    ohlcv = market_data[timeframe]
                elif '1h' in market_data:
                    ohlcv = market_data['1h']
                else:
                    first_tf = list(market_data.keys())[0]
                    ohlcv = market_data[first_tf]
            else:
                tf_data = {}
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

            # HARD VETO #1: Volume
            volume_valid, volume_message = self._validate_volume_conditions(ohlcv)
            if not volume_valid:
                return 'hold', 0.0, 0.0

            # HARD VETO #2: ADX >= adx_threshold (filter chop). PnL-FIX v3 default 22.
            try:
                adx_df = ta.adx(
                    high=ohlcv['high'], low=ohlcv['low'], close=ohlcv['close'], length=self.adx_period
                )
                if adx_df is not None and not adx_df.empty:
                    # pandas_ta returns ADX_14, DMP_14, DMN_14
                    adx_col = next((c for c in adx_df.columns if c.startswith('ADX')), None)
                    adx_now = float(adx_df[adx_col].iloc[-1]) if adx_col and not pd.isna(adx_df[adx_col].iloc[-1]) else 0.0
                else:
                    adx_now = 0.0
            except Exception as e:
                self.logger.debug(f"[VWMAHullStrategy] ADX calc failed for {pair}: {e}")
                adx_now = 0.0
            if adx_now < self.adx_threshold:
                self.logger.info(
                    f"[VWMAHullStrategy] {pair} HOLD — ADX veto: {adx_now:.1f} < {self.adx_threshold:.1f} (chop)"
                )
                return 'hold', 0.0, 0.0

            # HARD VETO #3: 4h macro trend must be bullish for long entries.
            # PnL-FIX v4: Previously when 4h data was missing (strategy-service
            # used to fetch only ['1h', '15m', '5m']) this veto was silently
            # skipped, defeating the PnL-FIX v3 safeguard. Now the upstream
            # default includes 4h; if it's still absent we treat it as a
            # veto — refusing the entry is safer than trading blind.
            macro_bullish: Optional[bool] = None
            if isinstance(tf_data, dict) and '4h' in tf_data:
                m = tf_data['4h']
                if m is not None and not m.empty and len(m) >= 30:
                    try:
                        ef = ta.ema(m['close'], length=9)
                        es = ta.ema(m['close'], length=21)
                        if ef is not None and es is not None and not pd.isna(ef.iloc[-1]) and not pd.isna(es.iloc[-1]):
                            macro_bullish = bool(ef.iloc[-1] > es.iloc[-1])
                    except Exception:
                        macro_bullish = None

            require_mtf = bool(self.config.get('require_multi_timeframe_confirmation', True))
            if macro_bullish is False:
                self.logger.info(
                    f"[VWMAHullStrategy] {pair} HOLD — macro veto: 4h EMA9 < EMA21"
                )
                return 'hold', 0.0, 0.0
            if macro_bullish is None and require_mtf:
                self.logger.info(
                    f"[VWMAHullStrategy] {pair} HOLD — macro data unavailable (4h) "
                    f"and require_multi_timeframe_confirmation=true"
                )
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

            # VWMA-Hull-Hybrid v1: SOFT CONFIRMATIONS.
            # Each layer either nudges the existing signal up (if it agrees)
            # or applies a symmetric penalty (if it disagrees). They never
            # promote 'hold' to 'buy' on their own — they refine confidence
            # of an already-fired signal.
            try:
                # 1) Canonical single-line VWMAHull (close vs vwma_hull line).
                vwma_hull_line = None
                vwma_hull_now = float('nan')
                close_above_vwma_hull = False
                if self.enable_canonical_vwma_hull:
                    vwma_hull_line = self._calculate_vwma_hull(
                        ohlcv, indicators_cache, pair, timeframe
                    )
                    if vwma_hull_line is not None and len(vwma_hull_line) > 0:
                        vwma_hull_now = float(vwma_hull_line.iloc[-1])
                        if not pd.isna(vwma_hull_now):
                            close_above_vwma_hull = current_price > vwma_hull_now
                            if signal == 'buy' and close_above_vwma_hull:
                                confidence = min(1.0, confidence + self.vwma_hull_boost)
                                strength = min(1.0, strength + self.vwma_hull_boost)
                                reason.append('close>vwma_hull')
                            elif signal == 'buy' and not close_above_vwma_hull:
                                confidence = max(0.0, confidence - self.vwma_hull_boost)
                                strength = max(0.0, strength - self.vwma_hull_boost)
                                reason.append('close<vwma_hull')
                            elif signal == 'sell' and not close_above_vwma_hull:
                                confidence = min(1.0, confidence + self.vwma_hull_boost)
                                strength = min(1.0, strength + self.vwma_hull_boost)
                                reason.append('close<vwma_hull')

                # 2) HMA slope confirmation (slope = sign of last - prev).
                hma_slope_up = current_hull > prev_hull
                if signal == 'buy' and hma_slope_up:
                    confidence = min(1.0, confidence + self.hma_slope_boost)
                    strength = min(1.0, strength + self.hma_slope_boost)
                    reason.append('hma_slope_up')
                elif signal == 'buy' and not hma_slope_up:
                    confidence = max(0.0, confidence - self.hma_slope_boost)
                    strength = max(0.0, strength - self.hma_slope_boost)
                    reason.append('hma_slope_down')
                elif signal == 'sell' and not hma_slope_up:
                    confidence = min(1.0, confidence + self.hma_slope_boost)
                    strength = min(1.0, strength + self.hma_slope_boost)
                    reason.append('hma_slope_down')

                # 3) RSI(14) > rsi_long_floor (default 50) for longs (spec).
                rsi_now = float('nan')
                try:
                    rsi_series = ta.rsi(ohlcv['close'], length=self.rsi_period_long)
                    if rsi_series is not None and len(rsi_series) > 0 and not pd.isna(rsi_series.iloc[-1]):
                        rsi_now = float(rsi_series.iloc[-1])
                        if signal == 'buy' and rsi_now > self.rsi_long_floor:
                            confidence = min(1.0, confidence + self.rsi_boost)
                            strength = min(1.0, strength + self.rsi_boost)
                            reason.append(f'rsi>{self.rsi_long_floor:.0f}')
                        elif signal == 'buy' and rsi_now <= self.rsi_long_floor:
                            confidence = max(0.0, confidence - self.rsi_boost)
                            strength = max(0.0, strength - self.rsi_boost)
                            reason.append(f'rsi<={self.rsi_long_floor:.0f}')
                        elif signal == 'sell' and rsi_now < self.rsi_short_ceiling:
                            confidence = min(1.0, confidence + self.rsi_boost)
                            strength = min(1.0, strength + self.rsi_boost)
                            reason.append(f'rsi<{self.rsi_short_ceiling:.0f}')
                except Exception as _rsi_err:
                    self.logger.debug(f"[VWMAHullStrategy] RSI soft-confirm failed for {pair}: {_rsi_err}")
            except Exception as _soft_err:
                self.logger.debug(f"[VWMAHullStrategy] Soft-confirmation layer failed for {pair}: {_soft_err}")

            reason_str = '+'.join(reason) + f'|adx={adx_now:.1f}' if reason else 'no_signal'
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
        """
        Calculate position size based on ATR-anchored risk management.

        VWMA-Hull-Hybrid v1 BUG FIX: previously read `self.state.indicators.get('ATR')`
        which was never populated AND treated as a Series with `.values[-1]`. Both
        bugs combined to silently return 0 for every call → no orders ever sized
        through this method. We now compute ATR live from `_current_ohlcv`
        (already maintained by `update()`), with a fallback to a fixed ~1% risk
        sizing if ATR is unavailable so we never silently emit zero-size orders.
        """
        try:
            if not self.exchange:
                self.logger.debug("[VWMAHullStrategy] Exchange not available for position size calculation")
                return 0.0

            balance = await self.exchange.get_balance()
            available_balance = balance.get('free', 0.0)

            # 1% of available balance is the risk budget per trade.
            risk_amount = available_balance * 0.01

            ticker = await self.exchange.get_ticker(self.state.pair, self.exchange_name)
            if not ticker or 'last' not in ticker:
                self.logger.warning(
                    f"[VWMAHullStrategy] Could not fetch ticker for {self.state.pair} on {self.exchange_name}"
                )
                return 0.0
            current_price = float(ticker['last'])
            if current_price <= 0:
                return 0.0

            # Live ATR from the most recent OHLCV snapshot maintained by update().
            atr_value: Optional[float] = None
            ohlcv = getattr(self, '_current_ohlcv', None)
            if ohlcv is not None and isinstance(ohlcv, pd.DataFrame) and len(ohlcv) >= self.atr_period + 1:
                try:
                    atr_series = self._calculate_atr(ohlcv)
                    if atr_series is not None and len(atr_series) > 0 and not pd.isna(atr_series.iloc[-1]):
                        atr_value = float(atr_series.iloc[-1])
                except Exception as _atr_err:
                    self.logger.debug(f"[VWMAHullStrategy] Live ATR calc failed: {_atr_err}")

            if atr_value and atr_value > 0:
                # Position size = risk / (stop_distance), stop_distance ≈ 2 * ATR.
                position_size = risk_amount / (2.0 * atr_value)
            else:
                # Fallback: assume 1% stop distance — never emit a zero-size order
                # silently. This mirrors the orchestrator's default trading.stop_loss_percentage.
                self.logger.warning(
                    f"[VWMAHullStrategy] ATR unavailable for {self.state.pair}; "
                    "falling back to fixed-percentage sizing (1% stop)."
                )
                position_size = risk_amount / (current_price * 0.01)

            return float(position_size)

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

            # 3. VWMA/Hull crossover (technical exit) — VWMA-Hull-Hybrid v1 BUG FIX.
            # Previously this branch was dead code: it read 'VWMA'/'Hull' (uppercase)
            # from self.state.indicators which only stores 'vwma'/'hull' (lowercase)
            # AS SCALARS (`float(...)`). Both the key mismatch AND the scalar-vs-series
            # mismatch meant the spec's "crossover-back" exit NEVER fired. We now
            # recompute both lines fresh from `_current_ohlcv` (cheap on the
            # execution timeframe) and additionally check `close < vwma_hull` if
            # the canonical indicator is enabled.
            try:
                if len(ohlcv) >= max(self.vwma_period, self.hull_period) + 2:
                    vwma_series = self._calculate_vwma(ohlcv)
                    hull_series = self._calculate_hull_ma(vwma_series)
                    if (
                        vwma_series is not None and hull_series is not None
                        and len(vwma_series) >= 2 and len(hull_series) >= 2
                        and not pd.isna(vwma_series.iloc[-1]) and not pd.isna(hull_series.iloc[-1])
                        and not pd.isna(vwma_series.iloc[-2]) and not pd.isna(hull_series.iloc[-2])
                    ):
                        prev_vwma = float(vwma_series.iloc[-2])
                        prev_hull = float(hull_series.iloc[-2])
                        curr_vwma = float(vwma_series.iloc[-1])
                        curr_hull = float(hull_series.iloc[-1])
                        if prev_vwma > prev_hull and curr_vwma < curr_hull:
                            self.logger.info(
                                f"[VWMAHullStrategy] Exiting {pair} due to VWMA/Hull crossover: "
                                f"{prev_vwma:.6f}->{curr_vwma:.6f} crossed {prev_hull:.6f}->{curr_hull:.6f}"
                            )
                            await self._log_condition(
                                'exit_signal', 'crossover', 'VWMA/Hull crossover exit', True, 'exit', pair,
                                reason='crossover',
                                context={'prev_vwma': prev_vwma, 'prev_hull': prev_hull,
                                         'curr_vwma': curr_vwma, 'curr_hull': curr_hull},
                            )
                            return True, 'crossover'

                    # 3b. Canonical VWMAHull line break — close drops below the
                    # single VWMAHull line (spec's primary technical exit).
                    if self.enable_canonical_vwma_hull:
                        vh = self._calculate_vwma_hull(ohlcv)
                        if vh is not None and len(vh) >= 2 and not pd.isna(vh.iloc[-1]) and not pd.isna(vh.iloc[-2]):
                            prev_close = float(ohlcv['close'].iloc[-2])
                            curr_close = float(current_price)
                            prev_vh = float(vh.iloc[-2])
                            curr_vh = float(vh.iloc[-1])
                            if prev_close >= prev_vh and curr_close < curr_vh:
                                self.logger.info(
                                    f"[VWMAHullStrategy] Exiting {pair} on close<VWMAHull: "
                                    f"close {prev_close:.6f}->{curr_close:.6f} crossed VH {prev_vh:.6f}->{curr_vh:.6f}"
                                )
                                await self._log_condition(
                                    'exit_signal', 'vwma_hull_break', 'Close dropped below VWMAHull',
                                    True, 'exit', pair, reason='vwma_hull_break',
                                    context={'prev_close': prev_close, 'curr_close': curr_close,
                                             'prev_vwma_hull': prev_vh, 'curr_vwma_hull': curr_vh},
                                )
                                return True, 'vwma_hull_break'
            except Exception as _x_err:
                self.logger.debug(f"[VWMAHullStrategy] Crossover-back exit check failed for {pair}: {_x_err}")

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
        """
        Per-timeframe quick analysis used by the orchestrator's bulk scan.

        VWMA-Hull-Hybrid v1 BUG FIX: this method previously crashed on
        `self.adx_threshold` (only added in __init__ as part of this fix)
        and used `adx[-1]` against a pandas_ta DataFrame (TypeError). It
        now mirrors the canonical signal pipeline used by generate_signal:
        ADX gate, VWMA/Hull cross check, canonical VWMAHull confirmation,
        and an RSI(14)>floor check for longs.
        """
        timeframes = ['1h', '15m']
        results: Dict[str, Tuple[str, float, Dict[str, Any]]] = {}
        ex_name = exchange_name or self.exchange_name
        limit = min_candles or 50
        for timeframe in timeframes:
            logger.info(f"[VWMAHullStrategy] Analyzing {pair} on {timeframe}")
            try:
                df = await self.exchange.get_ohlcv(ex_name, pair, timeframe, limit=limit)
            except Exception as e:
                logger.error(f"[VWMAHullStrategy] OHLCV fetch failed for {pair} on {timeframe}: {e}")
                results[timeframe] = ('hold', 0.0, {})
                continue

            if df is None or len(df) < limit:
                logger.warning(f"[VWMAHullStrategy] Not enough data for {pair} on {timeframe}")
                results[timeframe] = ('hold', 0.0, {})
                continue

            try:
                import pandas_ta as ta
                indicators: Dict[str, Any] = {}

                # ADX (gate).
                adx_df = ta.adx(df['high'], df['low'], df['close'], length=self.adx_period)
                adx_now = float('nan')
                if adx_df is not None and not adx_df.empty:
                    adx_col = next((c for c in adx_df.columns if c.startswith('ADX')), None)
                    if adx_col and not pd.isna(adx_df[adx_col].iloc[-1]):
                        adx_now = float(adx_df[adx_col].iloc[-1])
                indicators['adx'] = adx_now

                # RSI (long-floor confirmation).
                rsi_series = ta.rsi(df['close'], length=self.rsi_period_long)
                rsi_now = float(rsi_series.iloc[-1]) if rsi_series is not None and len(rsi_series) > 0 and not pd.isna(rsi_series.iloc[-1]) else float('nan')
                indicators['rsi'] = rsi_now

                # VWMA / Hull(VWMA) for crossover.
                vwma_series = self._calculate_vwma(df)
                hull_series = self._calculate_hull_ma(vwma_series)
                indicators['vwma'] = float(vwma_series.iloc[-1]) if not pd.isna(vwma_series.iloc[-1]) else float('nan')
                indicators['hull'] = float(hull_series.iloc[-1]) if not pd.isna(hull_series.iloc[-1]) else float('nan')

                # Canonical single-line VWMAHull (close vs vwma_hull).
                close_above_vh: Optional[bool] = None
                if self.enable_canonical_vwma_hull:
                    vh = self._calculate_vwma_hull(df)
                    if vh is not None and len(vh) > 0 and not pd.isna(vh.iloc[-1]):
                        indicators['vwma_hull'] = float(vh.iloc[-1])
                        close_above_vh = float(df['close'].iloc[-1]) > float(vh.iloc[-1])

                # NaN guard.
                if any(pd.isna(v) for v in (adx_now, rsi_now, indicators['vwma'], indicators['hull'])):
                    logger.warning(f"[VWMAHullStrategy] NaN core indicator for {pair} on {timeframe}, holding.")
                    results[timeframe] = ('hold', 0.0, indicators)
                    continue
            except Exception as e:
                logger.error(f"[VWMAHullStrategy] Indicator calculation error for {pair} on {timeframe}: {e}")
                results[timeframe] = ('hold', 0.0, {})
                continue

            adx_pass = adx_now > self.adx_threshold
            rsi_pass = rsi_now > self.rsi_long_floor
            vwma_above_hull = indicators['vwma'] > indicators['hull']
            checks = [adx_pass, rsi_pass, vwma_above_hull]
            if close_above_vh is not None:
                checks.append(close_above_vh)

            passed = sum(1 for c in checks if c)
            confidence = passed / max(len(checks), 1)

            if all(checks):
                logger.info(
                    f"[VWMAHullStrategy] {pair} {timeframe}: BUY (adx={adx_now:.1f}, rsi={rsi_now:.1f}, "
                    f"vwma>hull={vwma_above_hull}, close>vh={close_above_vh})"
                )
                results[timeframe] = ('buy', confidence, indicators)
            else:
                logger.info(
                    f"[VWMAHullStrategy] {pair} {timeframe}: HOLD ({passed}/{len(checks)} checks passed; "
                    f"adx={adx_now:.1f}, rsi={rsi_now:.1f}, vwma>hull={vwma_above_hull}, close>vh={close_above_vh})"
                )
                results[timeframe] = ('hold', confidence, indicators)
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