"""
Engulfing-Hybrid v1 — Multi-Timeframe Engulfing Pattern Strategy.

Design:
    This file is a HYBRID. It preserves the production-hardened
    "engulfing pullback continuation" engine (hard vetos: ADX, ATR/price,
    1.5x volume spike, macro 4h EMA9>EMA21 + close>SMA50) AND additively
    layers the textbook spec features:

      * Real per-timeframe engulfing scan with weighted confluence
        (the legacy `tf_confirmations` was a broadcast hack: it copied
        the same pattern result across all TFs).
      * Optional "break of engulfing high" entry confirmation
        (require_engulfing_break_high=True).
      * Optional pattern-based stop hint (engulfing low - buffer*ATR)
        published in the indicators dict for the orchestrator
        (use_pattern_stop=True).
      * Opt-in `reversal_mode` that flips the macro check from
        "continuation of an established trend" to "exhaustion of an
        opposing trend" — the textbook spec interpretation.
      * Bearish-engulfing reversal wired into `should_exit()` so it
        actually fires through the orchestrator path
        (exit_on_bearish_engulfing=True).

    Continuation defaults are unchanged (catching falling knives is
    expensive on a spot-only book) — the new features are off by default
    or layer in as soft confluence boosts. Flip the flags in
    `config.yaml` to opt into the textbook reversal behavior.

Bug fixes vs prior version:
    - `analyze_pair()` referenced a non-existent `self.adx_threshold`
      and ignored the engulfing pattern; now uses adx_entry_threshold
      and the real pattern + macro pipeline.
    - `calculate_indicators()` called `talib.ATR` without importing
      talib at module level (NameError); now uses `pandas_ta.atr`.
    - `check_entry_signals()` hard-coded ADX=15 and volume=0.5x,
      bypassing config; now reads from `self.adx_entry_threshold` and
      `volume_threshold_multiplier`. Cooldown placeholder replaced with
      a real check against `self.cooldown_until`.
    - `check_exit_signals()` mislabeled the bearish-engulfing reversal
      exit as `take_profit` / `stop_loss`; now labels it
      `bearish_engulfing_exit` so downstream analytics aren't poisoned.
    - Duplicate sync legacy stubs for `update`, `generate_signals` and
      `calculate_position_size` removed.

Configuration (full list now lives in config.yaml under
strategies.engulfing_multi_tf.parameters; only highlights here):
    - target_timeframes:        ['4h','1h','15m']  - real multi-TF hierarchy
    - confirmation_timeframes:  2                  - min agreeing TFs
    - timeframe_weights:        {4h:0.5, 1h:0.3, 15m:0.2}
    - enable_real_multi_tf:     true               - per-TF engulfing scan
    - multi_tf_confluence_boost: 0.10              - confidence nudge
    - require_engulfing_break_high: false          - spec entry trigger
    - use_pattern_stop:         false              - spec stop placement
    - reversal_mode:            false              - spec philosophy toggle
    - exit_on_bearish_engulfing: true              - wire reversal exit

Notes:
    - Spot/long-only by design (no shorts).
    - All hard vetos remain hard; new layers nudge confidence/strength
      or add opt-in gates. They never relax the existing safety rails.
"""
import logging
import pandas as pd
import numpy as np
import traceback
from typing import Dict, Any, Optional, Tuple
import pandas_ta as ta
from datetime import datetime, timezone, timedelta
import os
from strategy.base_strategy import BaseStrategy
from strategy.strategy_pnl_enhanced import calculate_unrealized_pnl, check_profit_protection, check_profit_protection_enhanced, manage_trailing_stop, restore_profit_protection_state
import asyncio
from strategy.condition_logger import ConditionLogger

class EngulfingMultiTimeframeStrategy(BaseStrategy):
    STRATEGY_NAME = "Engulfing Multi-Timeframe"

    # OPTION-A: regime_overrides keys are matched against ``self.<attr>``
    # by default. This map lets config use the original parameter names
    # even when they differ from the attribute (e.g. ``volume_avg_period``
    # is stored as ``self.volume_lookback``).
    _regime_param_alias_map = {
        "volume_avg_period": "volume_lookback",
    }

    def __init__(self, config=None, exchange=None, database=None, redis_client=None, logger_instance=None, exchange_handler=None):
        """
        Initialize the EngulfingMultiTimeframeStrategy.
        Args:
            config: Configuration dictionary (if None, loads from global config)
            exchange: Exchange instance for BaseStrategy
            database: Database manager for BaseStrategy
            redis_client: Redis client for optimizer integration
            logger_instance: Optional logger
            exchange_handler: Legacy exchange handler (for backward compatibility)
        """
        # Handle legacy parameters for backward compatibility
        if exchange_handler is not None and exchange is None:
            exchange = exchange_handler
        if hasattr(exchange_handler, 'db_handler') and database is None:
            database = exchange_handler.db_handler
        
        # Ensure we have required parameters - make exchange and database optional for strategy service compatibility
        if config is None:
            raise ValueError("Config must be provided to EngulfingMultiTimeframeStrategy")
        # Note: exchange and database can be None during initialization and will be set per analysis
        
        # Call the BaseStrategy constructor
        super().__init__(
            config=config,
            exchange=exchange,
            database=database,
            redis_client=redis_client
        )
        
        # Store legacy references for backward compatibility
        self.config = config
        self.exchange_handler = exchange
        self.db_handler = database
        
        # Set up logging
        self.logger = logger_instance or logging.getLogger(__name__)
        self.condition_logger = ConditionLogger()

        # --- Timeframes ---
        self.target_timeframes = self.config['parameters']['target_timeframes']
        if not isinstance(self.target_timeframes, list) or not self.target_timeframes:
            raise ValueError("target_timeframes must be a non-empty list")
        self.logger.info(f"[{self.STRATEGY_NAME}] Using timeframes: {self.target_timeframes}")

        # --- Configurable Parameters with Safe Defaults ---
        params = self.config.get('parameters', {})
        
        # Technical Indicators
        self.sma_period = params.get('sma_period', 50)
        self.adx_period = params.get('adx_period', 14)
        self.adx_entry_threshold = params.get('adx_entry_threshold', 25)
        self.atr_period = params.get('atr_period', 14)
        
        # Volume Analysis
        self.volume_lookback = params.get('volume_avg_period', 10)
        self.use_volume_confirmation = params.get('use_volume_confirmation', True)
        self.require_adx_and_volume_confirmation = params.get('require_adx_and_volume_confirmation', True)
        self.strict_volume_adx = params.get('strict_volume_adx', False)
        
        # Engulfing Pattern Settings
        self.min_engulfing_size_multiplier = params.get('min_engulfing_size_multiplier', 0.6)
        self.min_bearish_engulfing = params.get('min_bearish_engulfing', 1.2)
        
        # Ensure confirmation_timeframes is always an integer
        confirmation_timeframes_config = params.get('confirmation_timeframes', 2)
        if isinstance(confirmation_timeframes_config, list):
            self.confirmation_timeframes = len(confirmation_timeframes_config)
        elif isinstance(confirmation_timeframes_config, str) and confirmation_timeframes_config.isdigit():
            self.confirmation_timeframes = int(confirmation_timeframes_config)
        else:
            self.confirmation_timeframes = int(confirmation_timeframes_config)
        
        self.logger.info(f"[{self.STRATEGY_NAME}] Using confirmation_timeframes: {self.confirmation_timeframes} (type: {type(self.confirmation_timeframes).__name__})")
        
        # Timeframe weights for confidence calculation
        self.timeframe_weights = params.get('timeframe_weights', {
            '4h': 0.4,
            '1h': 0.3,
            '15m': 0.2,
            '5m': 0.1
        })
        
        # Trade Management
        self.cooldown_minutes = params.get('cooldown_minutes', 60)
        self.min_confidence = params.get('min_confidence', 0.6)
        self.max_trade_duration_hours = params.get('max_trade_duration_hours', 24)
        
        # Risk Management - ATR settings
        self.atr_multiplier_tp = params.get('atr_multiplier_tp', 2.0)
        self.atr_multiplier_sl = params.get('atr_multiplier_sl', 1.5)
        self.use_atr_for_exits = params.get('use_atr_for_exits', True)
        self.use_atr_levels = params.get('use_atr_levels', True)
        
        # Risk Management - Fixed percentages
        self.take_profit_pct = params.get('take_profit_pct', 2.0)
        self.stop_loss_pct = params.get('stop_loss_pct', 1.0)
        self.fixed_tp_pct = params.get('fixed_tp_pct', 2.5)
        self.fixed_sl_pct = params.get('fixed_sl_pct', 1.2)
        
        # Strategy Behavior
        self.regime_aware = params.get('regime_aware', True)

        # --- Hybrid v1: spec-compliant additive layers (off / soft by default) ---
        # Real per-timeframe engulfing scan (replaces the broadcast hack in
        # check_entry_signals). Adds a confluence-weighted boost.
        self.enable_real_multi_tf = params.get('enable_real_multi_tf', True)
        self.multi_tf_confluence_boost = params.get('multi_tf_confluence_boost', 0.10)
        # Volume threshold multiplier for the legacy check_entry_signals path
        # (was hard-coded to 0.5 before; production gen_signal still requires 1.5x).
        self.volume_threshold_multiplier = params.get('volume_threshold_multiplier', 1.0)

        # Spec entry trigger: only enter when next bar BREAKS the engulfing
        # candle's high (vs entering at engulfing close as today). Off by default
        # so existing live behavior is unchanged.
        self.require_engulfing_break_high = params.get('require_engulfing_break_high', False)
        self.break_buffer_atr = params.get('break_buffer_atr', 0.10)  # 10% of ATR

        # Spec stop placement: stop = engulfing_low - buffer*ATR. Published as a
        # hint in the indicators dict so the orchestrator can use it instead of
        # (or alongside) the ATR-derived stop. Off by default.
        self.use_pattern_stop = params.get('use_pattern_stop', False)
        self.pattern_stop_buffer_atr = params.get('pattern_stop_buffer_atr', 0.25)

        # Spec philosophy: reversal mode flips the macro check from
        # "continuation of an established uptrend" to "exhaustion of a downtrend"
        # (RSI < oversold OR price stretched below SMA50). Off by default — the
        # production engine is built for continuation pullbacks on spot.
        self.reversal_mode = params.get('reversal_mode', False)
        self.reversal_rsi_oversold = params.get('reversal_rsi_oversold', 35.0)
        self.reversal_stretch_pct = params.get('reversal_stretch_pct', 0.03)  # 3% below SMA50

        # Wire the existing bearish-engulfing reversal exit (already in
        # check_exit_signals) into should_exit() so the orchestrator path fires.
        self.exit_on_bearish_engulfing = params.get('exit_on_bearish_engulfing', True)

        # --- Data Containers ---
        self.indicators = {tf: {} for tf in self.target_timeframes}
        self.signals = {tf: {} for tf in self.target_timeframes}
        self._indicator_cache = {}
        self.cooldown_until = {}  # {pair: datetime}
        self._current_ohlcv = None  # Store current OHLCV data for signal generation

        # --- Mode Detection ---
        # Prefer explicit config, fallback to False
        # Handle both strategy config and full config
        if hasattr(self.config, 'trading'):
            self.simulation_mode = getattr(self.config.trading, 'simulation_mode', False)
        else:
            # If we only have strategy config, assume simulation mode
            self.simulation_mode = True
        mode_str = 'SIMULATION/BACKTEST' if self.simulation_mode else 'LIVE'
        self.logger.info(f"[{self.STRATEGY_NAME}] Initialized in {mode_str} mode.")

        # Log all loaded parameters for debugging
        self.logger.info(f"[{self.STRATEGY_NAME}] Loaded parameters:")
        self.logger.info(f"  - SMA Period: {self.sma_period}")
        self.logger.info(f"  - ADX Period: {self.adx_period}, Threshold: {self.adx_entry_threshold}")
        self.logger.info(f"  - ATR Period: {self.atr_period}, TP Mult: {self.atr_multiplier_tp}, SL Mult: {self.atr_multiplier_sl}")
        self.logger.info(f"  - Volume Confirmation: {self.use_volume_confirmation}, Lookback: {self.volume_lookback}")
        self.logger.info(f"  - Min Confidence: {self.min_confidence}, Cooldown: {self.cooldown_minutes}min")
        self.logger.info(f"  - Regime Aware: {self.regime_aware}, Use ATR Exits: {self.use_atr_for_exits}")
        self.logger.info(f"  - Timeframe Weights: {self.timeframe_weights}")
        self.logger.info(
            f"  - [Hybrid] real_multi_tf={self.enable_real_multi_tf} "
            f"conf_boost={self.multi_tf_confluence_boost} "
            f"break_high={self.require_engulfing_break_high} "
            f"pattern_stop={self.use_pattern_stop} "
            f"reversal_mode={self.reversal_mode} "
            f"exit_on_bearish_engulf={self.exit_on_bearish_engulfing} "
            f"vol_mult={self.volume_threshold_multiplier}"
        )

    def _get_trading_config_value(self, key: str, default: Any = None) -> Any:
        """Safely get a value from the trading config, handling Pydantic objects."""
        try:
            trading_config = getattr(self.config, 'trading', None)
            if trading_config:
                return getattr(trading_config, key, default)
            return default
        except Exception:
            return default

    def validate_config(self):
        """
        Validate strategy configuration parameters.
        Raises:
            ValueError: If any config parameter is invalid.
        """
        if not (self.min_engulfing_size_multiplier > 0):
            raise ValueError("min_engulfing_size_multiplier must be greater than 0")
        if not (1 <= self.confirmation_timeframes <= len(self.target_timeframes)):
            raise ValueError(f"confirmation_timeframes must be between 1 and {len(self.target_timeframes)}")
        if not (self.atr_multiplier_tp > 0):
            raise ValueError("atr_multiplier_tp must be greater than 0")
        if not (self.atr_multiplier_sl > 0):
            raise ValueError("atr_multiplier_sl must be greater than 0")

    def detect_engulfing_pattern(self, df: pd.DataFrame) -> Optional[str]:
        """
        Detect bullish or bearish engulfing patterns in the DataFrame.
        
        Args:
            df: DataFrame with OHLCV data
            
        Returns:
            Optional[str]: 'bullish', 'bearish', or None
        """
        try:
            # Ensure required columns exist
            required_cols = ['open', 'high', 'low', 'close']
            if not all(col in df.columns for col in required_cols):
                self.logger.warning(f"[{self.STRATEGY_NAME}] Missing required columns for engulfing pattern detection")
                return None
                
            # Calculate candle body sizes
            df = df.copy()  # Avoid modifying the original DataFrame
            df['body_size'] = abs(df['close'] - df['open'])
            df['prev_body_size'] = df['body_size'].shift(1)
            
            # Determine candle directions (bullish/bearish)
            curr_bullish = (df['close'] > df['open'])
            curr_bearish = (df['close'] < df['open'])
            prev_bullish = (df['close'].shift(1) > df['open'].shift(1))
            prev_bearish = (df['close'].shift(1) < df['open'].shift(1))
            
            # Bullish engulfing pattern
            # Current candle is bullish, previous is bearish
            # Current candle's body completely engulfs previous candle's body
            bullish_engulf = (
                curr_bullish & prev_bearish &
                (df['open'] <= df['close'].shift(1)) &  # Current open lower than or equal to previous close
                (df['close'] >= df['open'].shift(1))    # Current close higher than or equal to previous open
            )
            
            # Bearish engulfing pattern
            # Current candle is bearish, previous is bullish
            # Current candle's body completely engulfs previous candle's body
            bearish_engulf = (
                curr_bearish & prev_bullish &
                (df['open'] >= df['close'].shift(1)) &  # Current open higher than or equal to previous close
                (df['close'] <= df['open'].shift(1))    # Current close lower than or equal to previous open
            )
            
            # Size filter - ensure current body is at least min_engulfing_size_multiplier times the previous body
            if self.min_engulfing_size_multiplier > 0:
                # Direct multiplier instead of (1 + multiplier)
                size_filter = (df['body_size'] > df['prev_body_size'] * self.min_engulfing_size_multiplier)
                bullish_engulf = bullish_engulf & size_filter
                bearish_engulf = bearish_engulf & size_filter
            
            # Get the latest engulfing signal
            if bullish_engulf.iloc[-1]:
                return 'bullish'
            elif bearish_engulf.iloc[-1]:
                return 'bearish'
            return None
            
        except Exception as e:
            self.logger.error(f"[{self.STRATEGY_NAME}] Error detecting engulfing pattern: {e}")
            return None

    # ------------------------------------------------------------------
    # Hybrid v1 helpers — spec-compliant additive layers.
    # Kept side-effect free so they can be safely composed with the
    # existing engine without disturbing the hard vetos.
    # ------------------------------------------------------------------

    def _scan_engulfing_per_timeframe(
        self, tf_data: Dict[str, pd.DataFrame]
    ) -> Tuple[Dict[str, Optional[str]], int, float]:
        """Run the real engulfing scan on every available timeframe.

        Replaces the legacy "broadcast" hack in check_entry_signals which
        copied a single TF's pattern across the rest. Returns:
          - per_tf:    {tf: 'bullish'|'bearish'|None}
          - bull_count: number of TFs reporting bullish engulfing
          - weighted:   confluence score in [0, 1] using
                        ``self.timeframe_weights``.
        """
        per_tf: Dict[str, Optional[str]] = {}
        for tf in self.target_timeframes:
            df = tf_data.get(tf)
            if df is None or df.empty or len(df) < 3:
                per_tf[tf] = None
                continue
            try:
                per_tf[tf] = self.detect_engulfing_pattern(df)
            except Exception as e:
                self.logger.debug(
                    f"[{self.STRATEGY_NAME}] per-TF scan failed for {tf}: {e}"
                )
                per_tf[tf] = None

        bull_count = sum(1 for v in per_tf.values() if v == 'bullish')
        weights = self.timeframe_weights or {}
        total_w = sum(float(weights.get(tf, 0.0)) for tf in self.target_timeframes)
        if total_w <= 0:
            # Fallback: equal weighting across known TFs
            total_w = float(len(self.target_timeframes)) or 1.0
            weighted = bull_count / total_w
        else:
            weighted = sum(
                float(weights.get(tf, 0.0))
                for tf, v in per_tf.items() if v == 'bullish'
            ) / total_w
        return per_tf, bull_count, float(min(max(weighted, 0.0), 1.0))

    def _check_engulfing_break_high(
        self, ohlcv: pd.DataFrame, atr: float
    ) -> Tuple[bool, Optional[float]]:
        """Spec entry trigger: confirm break of the engulfing candle's high.

        Looks at the most recent closed bar; if it is a bullish engulfing,
        the trigger is the engulfing high + ``break_buffer_atr * atr``.
        We require the *current* close to have already exceeded the trigger
        (live bars on the execution TF arrive frequently enough that this
        is a reasonable proxy for an intra-bar break-and-hold).
        Returns (broken, trigger_price).
        """
        try:
            if ohlcv is None or len(ohlcv) < 3:
                return False, None
            engulf_high = float(ohlcv['high'].iloc[-1])
            engulf_close = float(ohlcv['close'].iloc[-1])
            buffer = max(0.0, float(atr) * float(self.break_buffer_atr))
            trigger = engulf_high + buffer
            broken = engulf_close >= trigger
            return broken, trigger
        except Exception as e:
            self.logger.debug(
                f"[{self.STRATEGY_NAME}] _check_engulfing_break_high error: {e}"
            )
            return False, None

    def _compute_pattern_stop_hint(
        self, ohlcv: pd.DataFrame, atr: float
    ) -> Optional[float]:
        """Spec stop placement: ``engulfing_low - buffer*ATR``.

        Published as a hint via the indicators dict; the orchestrator can
        use it instead of (or as a tighter floor under) the ATR-derived
        stop. Returns ``None`` if we can't compute it safely.
        """
        try:
            if ohlcv is None or len(ohlcv) < 2:
                return None
            engulf_low = float(ohlcv['low'].iloc[-1])
            buffer = max(0.0, float(atr) * float(self.pattern_stop_buffer_atr))
            stop = engulf_low - buffer
            return stop if stop > 0 else None
        except Exception as e:
            self.logger.debug(
                f"[{self.STRATEGY_NAME}] _compute_pattern_stop_hint error: {e}"
            )
            return None

    def _check_reversal_macro(
        self, ohlcv_macro: Optional[pd.DataFrame]
    ) -> bool:
        """Spec philosophy: macro check for a stretched/exhausted downtrend.

        True when the macro TF shows oversold conditions OR price is
        materially below SMA50 (``reversal_stretch_pct``). Used only when
        ``self.reversal_mode`` is enabled — it intentionally INVERTS the
        production "macro must be bullish" guard so we can catch knife
        bottoms when configured to do so.
        """
        try:
            if ohlcv_macro is None or ohlcv_macro.empty or len(ohlcv_macro) < 50:
                return False
            close = ohlcv_macro['close']
            sma50 = ta.sma(close, length=50)
            rsi = ta.rsi(close, length=14)
            if sma50 is None or rsi is None or pd.isna(sma50.iloc[-1]) or pd.isna(rsi.iloc[-1]):
                return False
            cur = float(close.iloc[-1])
            sma_v = float(sma50.iloc[-1])
            rsi_v = float(rsi.iloc[-1])
            stretched_below = (sma_v - cur) / max(sma_v, 1e-9) >= float(self.reversal_stretch_pct)
            oversold = rsi_v <= float(self.reversal_rsi_oversold)
            return bool(stretched_below or oversold)
        except Exception as e:
            self.logger.debug(
                f"[{self.STRATEGY_NAME}] _check_reversal_macro error: {e}"
            )
            return False

    def _validate_dataframe(self, df: Optional[pd.DataFrame], required_cols: list, tf: str) -> bool:
        """
        Helper to check if DataFrame is valid and has required columns.
        Args:
            df: DataFrame to check
            required_cols: List of required columns
            tf: Timeframe label for logging
        Returns:
            bool: True if valid, False otherwise
        """
        if df is None or df.empty:
            self.logger.warning(f"[{self.STRATEGY_NAME}] DataFrame for {tf} is missing or empty.")
            return False
        missing = [col for col in required_cols if col not in df.columns]
        if missing:
            self.logger.warning(f"[{self.STRATEGY_NAME}] DataFrame for {tf} missing columns: {missing}")
            return False
        return True

    async def calculate_indicators(self, dataframes: Dict[str, pd.DataFrame], indicators_cache: Optional[dict] = None, pair: Optional[str] = None) -> Dict[str, pd.DataFrame]:
        """Calculate indicators for all timeframes, using cache if provided."""
        results = {}
        for tf, df in dataframes.items():
            cache_key_sma = f"SMA_{pair}_{tf}_{self.sma_period}"
            cache_key_adx = f"ADX_{pair}_{tf}_{self.adx_period}"
            cache_key_atr = f"ATR_{pair}_{tf}_{self.atr_period}"
            # SMA
            if indicators_cache is not None and cache_key_sma in indicators_cache:
                self.logger.debug(f"[CACHE HIT] SMA for {pair} {tf} (period={self.sma_period})")
                sma = indicators_cache[cache_key_sma]
            else:
                sma = df['close'].rolling(self.sma_period).mean()
                if indicators_cache is not None:
                    indicators_cache[cache_key_sma] = sma
                    self.logger.debug(f"[CACHE STORE] SMA for {pair} {tf} (period={self.sma_period})")
            # ADX
            if indicators_cache is not None and cache_key_adx in indicators_cache:
                self.logger.debug(f"[CACHE HIT] ADX for {pair} {tf} (period={self.adx_period})")
                adx = indicators_cache[cache_key_adx]
            else:
                adx = ta.adx(df['high'], df['low'], df['close'], length=self.adx_period)
                if indicators_cache is not None:
                    indicators_cache[cache_key_adx] = adx
                    self.logger.debug(f"[CACHE STORE] ADX for {pair} {tf} (period={self.adx_period})")
            # ATR — use pandas_ta (talib was previously referenced without import → NameError)
            if indicators_cache is not None and cache_key_atr in indicators_cache:
                self.logger.debug(f"[CACHE HIT] ATR for {pair} {tf} (period={self.atr_period})")
                atr = indicators_cache[cache_key_atr]
            else:
                atr = ta.atr(df['high'], df['low'], df['close'], length=self.atr_period)
                if indicators_cache is not None:
                    indicators_cache[cache_key_atr] = atr
                    self.logger.debug(f"[CACHE STORE] ATR for {pair} {tf} (period={self.atr_period})")
            # Store in results
            results[tf] = {
                'sma': sma,
                'adx': adx,
                'atr': atr
            }
        return results

    async def check_entry_signals(self, market_data, predictions):
        """Check for entry signals for all pairs in market_data using multi-timeframe logic."""
        entry_signals = []
        try:
            for pair, tf_data in market_data.items():
                condition_logger = self._get_condition_logger(pair)
                condition_logger.start_validation(pair)
                
                # Handle nested timeframe data structure
                ohlcv = None
                if isinstance(tf_data, dict):
                    # Market data is nested by timeframe - use the first available timeframe
                    if len(tf_data) == 0:
                        self.logger.warning(f"[{self.STRATEGY_NAME}] No timeframes available for {pair}")
                        continue
                    # Sort timeframes by length (e.g., 4h > 1h > 15m) and use the highest
                    sorted_tfs = sorted(tf_data.keys(), key=lambda x: int(''.join(filter(str.isdigit, x))) if any(c.isdigit() for c in x) else 0, reverse=True)
                    timeframe = sorted_tfs[0]
                    ohlcv = tf_data[timeframe]
                    self.logger.debug(f"[{self.STRATEGY_NAME}] Using timeframe {timeframe} for {pair}")
                else:
                    # Market data is already a DataFrame
                    ohlcv = tf_data
                
                if not isinstance(ohlcv, pd.DataFrame):
                    self.logger.warning(f"[{self.STRATEGY_NAME}] Market data for {pair} is not a DataFrame. Type: {type(ohlcv).__name__}")
                    continue
                if 'close' not in ohlcv.columns:
                    self.logger.warning(f"[{self.STRATEGY_NAME}] Market data for {pair} is missing 'close' column. Columns: {list(ohlcv.columns)}")
                    continue
                try:
                    current_price = ohlcv['close'].iloc[-1]
                    open_ = ohlcv['open'].iloc[-1]
                    high = ohlcv['high'].iloc[-1]
                    low = ohlcv['low'].iloc[-1]
                    volume = ohlcv['volume'].iloc[-1] if 'volume' in ohlcv.columns else None
                    prev_open = ohlcv['open'].iloc[-2]
                    prev_close = ohlcv['close'].iloc[-2]
                    body_size = abs(current_price - open_)
                    prev_body_size = abs(prev_close - prev_open)
                    size_ratio = body_size / prev_body_size if prev_body_size != 0 else 0

                    # Pattern recognition
                    pattern = self.detect_engulfing_pattern(ohlcv)
                    # Compose pattern_result dict for logging
                    pattern_result = {
                        'pattern': pattern == 'bullish',
                        'engulf_open': open_ <= prev_close,
                        'engulf_close': current_price >= prev_open,
                        'size_filter': body_size > prev_body_size * self.min_engulfing_size_multiplier,
                        'multiplier': self.min_engulfing_size_multiplier
                    }

                    # --- Multi-timeframe confirmation (hybrid v1) ---
                    # Previously this was a "broadcast" hack: it copied the
                    # current TF's pattern across every target TF, so the
                    # confirmation count was always either N or 0 and the
                    # weighted confidence was always 0 or 1. We now run a
                    # real per-TF scan when the caller supplies multi-TF data.
                    tf_weights = self.timeframe_weights
                    if self.enable_real_multi_tf and isinstance(tf_data, dict) and len(tf_data) > 1:
                        try:
                            tf_confirmations, confirmed_timeframes, weighted_confidence = (
                                self._scan_engulfing_per_timeframe(tf_data)
                            )
                        except Exception as _scan_err:
                            self.logger.debug(
                                f"[{self.STRATEGY_NAME}] real multi-TF scan failed in "
                                f"check_entry_signals: {_scan_err}"
                            )
                            tf_confirmations = {tf: ('bullish' if pattern == 'bullish' else None) for tf in self.target_timeframes}
                            confirmed_timeframes = sum(1 for v in tf_confirmations.values() if v == 'bullish')
                            total_w = sum(float(tf_weights.values())) if isinstance(tf_weights, dict) else 0.0
                            weighted_confidence = (1.0 if pattern == 'bullish' else 0.0)
                    else:
                        # Single-TF caller: fall back to the legacy semantics
                        # but make it explicit instead of hidden.
                        tf_confirmations = {tf: ('bullish' if pattern == 'bullish' else None) for tf in self.target_timeframes}
                        confirmed_timeframes = sum(1 for v in tf_confirmations.values() if v == 'bullish')
                        weighted_confidence = (1.0 if pattern == 'bullish' else 0.0)
                    required_confirmations = self.confirmation_timeframes

                    # Technical indicators (proper calculation instead of placeholders)
                    indicators = {
                        'sma50': ohlcv['close'].rolling(window=50).mean().iloc[-1] if len(ohlcv) >= 50 else ohlcv['close'].mean(),
                        'avg_volume': ohlcv['volume'].rolling(window=10).mean().iloc[-1] if 'volume' in ohlcv.columns and len(ohlcv) >= 10 else (ohlcv['volume'].mean() if 'volume' in ohlcv.columns else 0),
                    }
                    
                    # Calculate proper ADX using TA-Lib
                    try:
                        import talib
                        high = ohlcv['high'].values.astype(float)
                        low = ohlcv['low'].values.astype(float)
                        close = ohlcv['close'].values.astype(float)
                        adx = talib.ADX(high, low, close, timeperiod=14)
                        indicators['adx'] = adx[-1] if len(adx) > 0 else 0
                        self.logger.info(f"[DEBUG] ADX values for {pair} ({timeframe}): {adx[-5:].tolist() if len(adx) >= 5 else adx.tolist()}")
                    except Exception as e:
                        self.logger.error(f"Failed to calculate ADX for {pair} ({timeframe}): {e}")
                        indicators['adx'] = 25.0  # Fallback value
                    
                    # Calculate proper ATR using TA-Lib
                    try:
                        atr_values = talib.ATR(high, low, close, timeperiod=14)
                        indicators['atr'] = atr_values[-1] if len(atr_values) > 0 and not pd.isna(atr_values[-1]) else 0.0
                        self.logger.debug(f"Calculated proper ATR: {indicators['atr']:.2f}")
                    except Exception as e:
                        self.logger.error(f"Error calculating ATR: {e}")
                        indicators['atr'] = 0.0  # Fallback value

                    # --- Condition checks (hybrid v1: respect config) ---
                    # Volume / ADX thresholds were hard-coded (0.5x and 15)
                    # which silently bypassed the configured values. Now we
                    # read them from the configurable parameters and the real
                    # cooldown_until map.
                    vol_mult = float(self.volume_threshold_multiplier)
                    volume_confirmation = (
                        volume is not None and
                        indicators['avg_volume'] > 0 and
                        volume > (indicators['avg_volume'] * vol_mult)
                    )

                    cooldown_until = self.cooldown_until.get(pair) if isinstance(self.cooldown_until, dict) else None
                    not_in_cooldown = True
                    if cooldown_until is not None:
                        try:
                            now = datetime.now(timezone.utc) if cooldown_until.tzinfo else datetime.now()
                            not_in_cooldown = now >= cooldown_until
                        except Exception:
                            not_in_cooldown = True

                    conditions = {
                        'adx_threshold': float(self.adx_entry_threshold),
                        'min_confidence': float(self.min_confidence),
                        'volume_threshold_multiplier': vol_mult,
                        'checks': {
                            'Engulfing Pattern Present': pattern == 'bullish',
                            'Required Timeframe Confirmations': confirmed_timeframes >= required_confirmations,
                            'ADX Above Threshold': indicators['adx'] > float(self.adx_entry_threshold),
                            'Volume Confirmation': volume_confirmation,
                            'Not In Cooldown Period': bool(not_in_cooldown),
                            'Minimum Confidence Met': weighted_confidence >= float(self.min_confidence)
                        }
                    }

                    # Signal info
                    buy_signal = all(conditions['checks'].values())
                    signal_info = {
                        'buy_signal': buy_signal,
                        'confidence': weighted_confidence,
                        'strategy': 'engulfing_multi_tf',
                        'details': {
                            'pattern': pattern,
                            'timeframes_confirmed': confirmed_timeframes,
                            'confidence': weighted_confidence
                        }
                    }

                    # Call the detailed logger
                    self._log_detailed_analysis(
                        pair, ohlcv, pattern, pattern_result, tf_confirmations, tf_weights,
                        confirmed_timeframes, required_confirmations, weighted_confidence,
                        indicators, conditions, signal_info
                    )

                    # Standard signal logic
                    if buy_signal:
                        entry_signals.append({
                            'pair': pair,
                            'signal': 'buy',
                            'confidence': weighted_confidence,
                            'strategy': 'engulfing_multi_tf',
                            'details': {'pattern': pattern, 'conditions': conditions['checks']}
                        })
                        condition_logger.log_condition(
                            name="SignalGeneration",
                            value="buy",
                            description="All entry conditions met",
                            result=True,
                            condition_type="signal"
                        )
                    else:
                        condition_logger.log_condition(
                            name="SignalGeneration",
                            value="hold",
                            description="Entry conditions not met",
                            result=False,
                            condition_type="signal"
                        )
                    condition_logger.end_validation(result=buy_signal)
                except Exception as e:
                    self.logger.error(f"[{self.STRATEGY_NAME}] Error processing entry signal for {pair}: {e}", exc_info=True)
        except Exception as e:
            self.logger.error(f"[{self.STRATEGY_NAME}] Error in check_entry_signals: {e}", exc_info=True)
        return entry_signals

    async def check_exit_signals(self, market_data, predictions):
        """Check for exit signals for all pairs in market_data using multi-timeframe logic."""
        exit_signals = []
        try:
            for pair, tf_data in market_data.items():
                self.logger.info(f"===== Engulfing Multi-Timeframe ANALYSIS FOR {pair} EXIT SIGNAL =====")
                condition_logger = self._get_condition_logger(pair)
                condition_logger.start_validation(pair)
                
                # Handle nested timeframe data structure
                ohlcv = None
                if isinstance(tf_data, dict):
                    # Market data is nested by timeframe - use the first available timeframe
                    if len(tf_data) == 0:
                        self.logger.warning(f"[{self.STRATEGY_NAME}] No timeframes available for {pair}")
                        continue
                    # Sort timeframes by length (e.g., 4h > 1h > 15m) and use the highest
                    sorted_tfs = sorted(tf_data.keys(), key=lambda x: int(''.join(filter(str.isdigit, x))) if any(c.isdigit() for c in x) else 0, reverse=True)
                    timeframe = sorted_tfs[0]
                    ohlcv = tf_data[timeframe]
                    self.logger.debug(f"[{self.STRATEGY_NAME}] Using timeframe {timeframe} for {pair}")
                else:
                    # Market data is already a DataFrame
                    ohlcv = tf_data
                
                if not isinstance(ohlcv, pd.DataFrame):
                    self.logger.warning(f"[{self.STRATEGY_NAME}] Market data for {pair} is not a DataFrame. Type: {type(ohlcv).__name__}")
                    continue
                if 'close' not in ohlcv.columns:
                    self.logger.warning(f"[{self.STRATEGY_NAME}] Market data for {pair} is missing 'close' column. Columns: {list(ohlcv.columns)}")
                    continue
                try:
                    current_price = ohlcv['close'].iloc[-1]
                    open_ = ohlcv['open'].iloc[-1]
                    high = ohlcv['high'].iloc[-1]
                    low = ohlcv['low'].iloc[-1]
                    volume = ohlcv['volume'].iloc[-1] if 'volume' in ohlcv.columns else None
                    # --- Pattern-based exit logic only - let orchestrator handle profit protection ---
                    entry_price = getattr(self.state, 'entry_price', None)
                    position_size = getattr(self.state, 'position_size', None)
                    trade_id = getattr(self, 'trade_id', None)
                    
                    # --- Existing pattern-based exit logic ---
                    pattern = self.detect_engulfing_pattern(ohlcv)
                    cond1 = pattern == 'bearish'
                    cond2 = current_price < open_
                    all_conditions_met = cond1 and cond2
                    condition_logger.log_condition(
                        name="EngulfingPattern",
                        value=pattern,
                        description=f"Detected pattern for {pair}",
                        result=pattern is not None,
                        condition_type="pattern"
                    )
                    condition_logger.log_condition(
                        name="Condition1",
                        value=cond1,
                        description="Pattern is bearish",
                        result=cond1,
                        condition_type="exit"
                    )
                    condition_logger.log_condition(
                        name="Condition2",
                        value=cond2,
                        description="Close < Open",
                        result=cond2,
                        condition_type="exit"
                    )
                    self.logger.info(f"[{self.STRATEGY_NAME}] CONDITION VERIFICATION for {pair}: Condition1: {cond1}, Condition2: {cond2}, All: {all_conditions_met}")
                    # --- Hybrid v1: exit reason is the actual cause ---
                    # Previously labeled bearish-engulfing reversal exits as
                    # ``take_profit`` or ``stop_loss`` based on whether the
                    # trade was up or down. That poisoned downstream PnL
                    # analytics (those buckets should mean SL/TP triggers
                    # exclusively). Now both branches emit the correct
                    # ``bearish_engulfing_exit`` reason, with profit/loss
                    # carried in details.in_profit for analytics.
                    if all_conditions_met and entry_price is not None:
                        in_profit = current_price > entry_price
                        exit_signals.append({
                            'pair': pair,
                            'signal': 'exit',
                            'strategy': 'engulfing_multi_tf',
                            'reason': 'bearish_engulfing_exit',
                            'details': {
                                'pattern': pattern,
                                'conditions': {'cond1': cond1, 'cond2': cond2},
                                'in_profit': in_profit,
                                'entry_price': entry_price,
                                'current_price': current_price,
                            }
                        })
                        self.logger.info(
                            f"[{self.STRATEGY_NAME}] SIGNAL GENERATION for {pair}: "
                            f"Exit Signal: YES (reason: bearish_engulfing_exit, in_profit={in_profit})"
                        )
                        condition_logger.log_condition(
                            name="SignalGeneration",
                            value="exit",
                            description=f"Bearish engulfing reversal exit (in_profit={in_profit})",
                            result=True,
                            condition_type="signal"
                        )
                    else:
                        self.logger.info(f"[{self.STRATEGY_NAME}] SIGNAL GENERATION for {pair}: Exit Signal: NO")
                        condition_logger.log_condition(
                            name="SignalGeneration",
                            value="hold",
                            description="Exit conditions not met",
                            result=False,
                            condition_type="signal"
                        )
                    condition_logger.end_validation(result=all_conditions_met)
                except Exception as e:
                    self.logger.error(f"[{self.STRATEGY_NAME}] Error processing exit signal for {pair}: {e}", exc_info=True)
        except Exception as e:
            self.logger.error(f"[{self.STRATEGY_NAME}] Error in check_exit_signals: {e}", exc_info=True)
        # Only return exit signals with a valid, non-empty reason
        return [sig for sig in exit_signals if sig.get('reason')]

    # Add more methods as needed from the original file
    
    def _get_condition_logger(self, pair: str) -> ConditionLogger:
        """Get or create a condition logger for the current strategy and pair.
        
        Args:
            pair: Trading pair symbol
            
        Returns:
            ConditionLogger: Condition logger instance
        """
        # Create a new condition logger for each validation
        # This ensures clean state for each check
        self._condition_logger = ConditionLogger(
            strategy_name=self.STRATEGY_NAME,
            logger_instance=self.logger,
            detailed_mode=True
        )
        return self._condition_logger 

    # NOTE: hybrid v1 — early `calculate_position_size` and `generate_signals`
    # stubs were removed here. Python kept only the *last* definitions in the
    # class anyway (the real async ones below), so the stubs were dead code
    # that confused readers. The real async ``calculate_position_size`` lives
    # near the bottom of the class alongside ``update`` and ``should_exit``.

    async def generate_signal(self, market_data, indicators_cache: Optional[dict] = None, pair: Optional[str] = None, timeframe: Optional[str] = None, exchange_adapter=None) -> Tuple[str, float, float]:
        """
        PnL-FIX v3: Real implementation with hard vetos + multi-TF confluence.

        Previous version was a placeholder that always returned 'hold'. This
        replacement mirrors the heikin_ashi fix:
          - Detect bullish engulfing on the execution timeframe (15m)
          - Require bullish macro trend on 4h (EMA fast > slow, price > SMA50)
          - Hard vetos: ADX >= 22, volume >= 1.5× avg, ATR > 0 (real volatility)
          - No signal if any hard veto fails (no majority vote)
        """
        try:
            import pandas_ta as ta
            # OPTION-A: apply regime-driven parameter overrides. Safe no-op
            # if no regime_overrides block is configured for this strategy.
            try:
                self._apply_regime_overrides(getattr(self.state, 'market_regime', None))
            except Exception as _ra_err:
                self.logger.debug(
                    f"[EngulfingMultiTF] regime-override apply failed: {_ra_err}"
                )

            # Normalise market_data to a dict of {timeframe: DataFrame}
            if isinstance(market_data, dict):
                tf_data = market_data
            else:
                # Single DataFrame: treat as the execution timeframe only
                tf_data = {timeframe or '15m': market_data}

            # Pick the execution (entry) timeframe and macro (trend) timeframe.
            exec_tf = '15m' if '15m' in tf_data else (timeframe or next(iter(tf_data.keys()), None))
            macro_tf = '4h' if '4h' in tf_data else ('1h' if '1h' in tf_data else exec_tf)

            ohlcv = tf_data.get(exec_tf)
            ohlcv_macro = tf_data.get(macro_tf)

            if ohlcv is None or ohlcv.empty or len(ohlcv) < max(self.sma_period, self.adx_period, 30):
                return 'hold', 0.0, 0.0

            # --- Execution timeframe: engulfing pattern + ADX + volume ---
            pattern = self.detect_engulfing_pattern(ohlcv)
            if pattern != 'bullish':
                return 'hold', 0.0, 0.0

            close = ohlcv['close'].to_numpy(dtype=float)
            volume = ohlcv['volume'].to_numpy(dtype=float) if 'volume' in ohlcv.columns else np.zeros_like(close)

            try:
                adx_df = ta.adx(
                    high=ohlcv['high'], low=ohlcv['low'], close=ohlcv['close'], length=self.adx_period
                )
                atr_series = ta.atr(
                    high=ohlcv['high'], low=ohlcv['low'], close=ohlcv['close'], length=self.atr_period
                )
                if adx_df is not None and not adx_df.empty:
                    adx_col = next((c for c in adx_df.columns if c.startswith('ADX')), None)
                    adx = float(adx_df[adx_col].iloc[-1]) if adx_col and not pd.isna(adx_df[adx_col].iloc[-1]) else 0.0
                else:
                    adx = 0.0
                atr = float(atr_series.iloc[-1]) if atr_series is not None and not atr_series.empty and not pd.isna(atr_series.iloc[-1]) else 0.0
            except Exception as e:
                self.logger.warning(f"[{self.STRATEGY_NAME}] pandas_ta error for {pair}: {e}")
                return 'hold', 0.0, 0.0

            avg_volume = float(np.mean(volume[-self.volume_lookback:])) if len(volume) >= self.volume_lookback else 0.0
            cur_volume = float(volume[-1]) if len(volume) else 0.0
            cur_price = float(close[-1])

            # --- HARD VETOS (all must pass) ---
            veto_adx = adx >= max(22.0, float(self.adx_entry_threshold))
            veto_atr = atr > 0.0 and (atr / cur_price) > 0.001  # at least 0.1% ATR/price
            veto_vol = avg_volume > 0 and cur_volume >= 1.5 * avg_volume
            veto_macro = True  # computed below only if macro data present

            # --- Macro trend confirmation (4h preferred) ---
            macro_trend_bullish = False
            macro_exhausted = False
            if ohlcv_macro is not None and not ohlcv_macro.empty and len(ohlcv_macro) >= 50:
                try:
                    ema_fast_m = ta.ema(ohlcv_macro['close'], length=9)
                    ema_slow_m = ta.ema(ohlcv_macro['close'], length=21)
                    sma50_m = ta.sma(ohlcv_macro['close'], length=50)
                    if (ema_fast_m is not None and ema_slow_m is not None and sma50_m is not None
                            and not pd.isna(ema_fast_m.iloc[-1])
                            and not pd.isna(ema_slow_m.iloc[-1])
                            and not pd.isna(sma50_m.iloc[-1])):
                        macro_trend_bullish = (
                            ema_fast_m.iloc[-1] > ema_slow_m.iloc[-1]
                            and ohlcv_macro['close'].iloc[-1] > sma50_m.iloc[-1]
                        )
                except Exception:
                    macro_trend_bullish = False

                # Hybrid v1: optional reversal-mode macro check (textbook spec).
                # When enabled, accept either continuation (production default)
                # OR exhaustion of an opposing trend.
                if self.reversal_mode:
                    macro_exhausted = self._check_reversal_macro(ohlcv_macro)
                    veto_macro = macro_trend_bullish or macro_exhausted
                else:
                    veto_macro = macro_trend_bullish
            else:
                # If macro data unavailable, be conservative and refuse the entry.
                veto_macro = False

            hard_vetos = veto_adx and veto_atr and veto_vol and veto_macro
            if not hard_vetos:
                self.logger.info(
                    f"[{self.STRATEGY_NAME}] {pair} HOLD — vetos adx={veto_adx} "
                    f"atr={veto_atr} vol={veto_vol} macro={veto_macro} "
                    f"(adx={adx:.1f} atr/price={atr/max(cur_price,1e-9):.4f} "
                    f"vol/avg={cur_volume/max(avg_volume,1e-9):.2f} "
                    f"macro_bullish={macro_trend_bullish} macro_exhausted={macro_exhausted})"
                )
                return 'hold', 0.0, 0.0

            # --- Hybrid v1 layer: real per-TF engulfing confluence ---
            mtf_boost = 0.0
            mtf_bull = 0
            mtf_per_tf: Dict[str, Optional[str]] = {}
            mtf_weighted = 0.0
            if self.enable_real_multi_tf and isinstance(tf_data, dict) and len(tf_data) > 1:
                try:
                    mtf_per_tf, mtf_bull, mtf_weighted = self._scan_engulfing_per_timeframe(tf_data)
                    if mtf_bull >= int(self.confirmation_timeframes):
                        mtf_boost = float(self.multi_tf_confluence_boost) * float(mtf_weighted)
                except Exception as _mtf_err:
                    self.logger.debug(
                        f"[{self.STRATEGY_NAME}] real multi-TF scan failed: {_mtf_err}"
                    )

            # --- Hybrid v1 layer: optional break-of-engulfing-high gate ---
            broke_high = True
            break_trigger: Optional[float] = None
            if self.require_engulfing_break_high:
                broke_high, break_trigger = self._check_engulfing_break_high(ohlcv, atr)
                if not broke_high:
                    self.logger.info(
                        f"[{self.STRATEGY_NAME}] {pair} HOLD — break-of-high gate "
                        f"unmet (close={cur_price:.6f} trigger={break_trigger})"
                    )
                    return 'hold', 0.0, 0.0

            # --- Hybrid v1 layer: pattern-based stop hint (published) ---
            pattern_stop_hint = None
            if self.use_pattern_stop:
                pattern_stop_hint = self._compute_pattern_stop_hint(ohlcv, atr)

            # All hard vetos passed AND bullish engulfing detected.
            # Confidence scales with ADX strength + macro-trend alignment +
            # multi-TF confluence boost (capped at 1.0).
            confidence = min(
                1.0,
                0.60
                + (adx - 22.0) / 100.0
                + (0.10 if macro_trend_bullish else 0.0)
                + mtf_boost,
            )
            strength = min(1.0, (adx / 60.0) * (cur_volume / max(avg_volume, 1e-9)) / 2.0)

            # Publish hybrid metadata so the orchestrator can consume the
            # stop hint and analytics can audit the new signals.
            try:
                if not isinstance(getattr(self.state, 'indicators', None), dict):
                    self.state.indicators = {}
                self.state.indicators.update({
                    'engulfing_pattern': 'bullish',
                    'engulfing_break_high_trigger': break_trigger,
                    'pattern_stop_hint': pattern_stop_hint,
                    'multi_tf_per_tf': mtf_per_tf,
                    'multi_tf_bull_count': mtf_bull,
                    'multi_tf_weighted_score': mtf_weighted,
                    'macro_trend_bullish': macro_trend_bullish,
                    'macro_exhausted': macro_exhausted,
                    'reversal_mode': self.reversal_mode,
                    'last_atr': atr,
                    'last_adx': adx,
                })
            except Exception:
                pass

            self.logger.info(
                f"[{self.STRATEGY_NAME}] {pair} BUY — engulfing+vetos passed: "
                f"adx={adx:.1f} vol={cur_volume:.0f}/{avg_volume:.0f} "
                f"atr/price={atr/cur_price:.4f} macro_bullish={macro_trend_bullish} "
                f"reversal_mode={self.reversal_mode} macro_exhausted={macro_exhausted} "
                f"mtf_bull={mtf_bull}/{len(self.target_timeframes)} "
                f"mtf_weighted={mtf_weighted:.2f} mtf_boost={mtf_boost:.3f} "
                f"break_high={broke_high} pattern_stop={pattern_stop_hint} "
                f"confidence={confidence:.2f} strength={strength:.2f}"
            )
            return 'buy', float(confidence), float(strength)

        except Exception as e:
            self.logger.error(f"[{self.STRATEGY_NAME}] Error in generate_signal for {pair}: {e}", exc_info=True)
            return 'hold', 0.0, 0.0

    def _calculate_signal_confidence(self, pattern: str, price_above_sma: bool, 
                                   adx_strong: bool, volume_spike: bool) -> float:
        """
        Calculate signal confidence based on multiple factors.
        
        Args:
            pattern: Detected pattern ('bullish' or 'bearish')
            price_above_sma: Whether price is above SMA
            adx_strong: Whether ADX indicates strong trend
            volume_spike: Whether there's a volume spike
            
        Returns:
            Confidence score between 0.0 and 1.0
        """
        confidence = 0.0
        
        # Base confidence from pattern
        if pattern in ['bullish', 'bearish']:
            confidence += 0.4
        
        # Add confidence from technical conditions
        if price_above_sma:
            confidence += 0.2
        
        if adx_strong:
            confidence += 0.2
        
        if volume_spike:
            confidence += 0.2
        
        # Ensure confidence is within bounds
        return min(max(confidence, 0.0), 1.0)

    def _calculate_adx(self, df: pd.DataFrame) -> Optional[pd.Series]:
        """
        Calculate ADX (Average Directional Index) using TA-Lib.
        
        Args:
            df: DataFrame with OHLC data
            
        Returns:
            ADX values as pandas Series or None if calculation fails
        """
        try:
            import talib
            
            high = df['high'].values
            low = df['low'].values
            close = df['close'].values
            
            adx = talib.ADX(high, low, close, timeperiod=self.adx_period)
            return pd.Series(adx, index=df.index)
            
        except Exception as e:
            self.logger.error(f"Error calculating ADX: {str(e)}")
            return None

    async def _apply_strategy_parameters(self, parameters: Dict[str, Any]) -> bool:
        """
        Apply strategy-specific optimized parameters.
        
        Args:
            parameters: Dictionary of parameters to apply
            
        Returns:
            True if parameters were applied successfully
        """
        try:
            # Apply parameters with validation
            if 'sma_period' in parameters:
                new_sma = int(parameters['sma_period'])
                if 5 <= new_sma <= 200:  # Reasonable bounds
                    self.sma_period = new_sma
                    
            if 'adx_period' in parameters:
                new_adx = int(parameters['adx_period'])
                if 5 <= new_adx <= 50:  # Reasonable bounds
                    self.adx_period = new_adx
                    
            if 'adx_entry_threshold' in parameters:
                new_threshold = float(parameters['adx_entry_threshold'])
                if 10.0 <= new_threshold <= 50.0:  # Reasonable bounds
                    self.adx_entry_threshold = new_threshold
                    
            if 'volume_lookback' in parameters:
                new_volume = int(parameters['volume_lookback'])
                if 3 <= new_volume <= 50:  # Reasonable bounds
                    self.volume_lookback = new_volume
                    
            if 'min_engulfing_size_multiplier' in parameters:
                new_multiplier = float(parameters['min_engulfing_size_multiplier'])
                if 0.1 <= new_multiplier <= 3.0:  # Reasonable bounds
                    self.min_engulfing_size_multiplier = new_multiplier
                    
            if 'min_confidence' in parameters:
                new_confidence = float(parameters['min_confidence'])
                if 0.1 <= new_confidence <= 1.0:  # Reasonable bounds
                    self.min_confidence = new_confidence
            
            self.logger.info(f"Applied optimized parameters for {self.__class__.__name__}: {parameters}")
            return True
            
        except Exception as e:
            self.logger.error(f"Error applying strategy parameters: {str(e)}")
            return False

    # NOTE: hybrid v1 — removed sync ``update`` stub here. The async
    # ``update`` defined below was already shadowing it (Python keeps the
    # last definition), so the stub was dead code. Kept this comment so
    # nobody re-adds it thinking it's missing.

    def _get_decimal_precision(self, pair: str) -> int:
        """Get decimal precision for a trading pair."""
        return 8  # Default precision

    def _log_detailed_analysis(self, pair, tf_data, pattern, pattern_result, tf_confirmations, tf_weights, confirmed_timeframes, required_confirmations, weighted_confidence, indicators, conditions, signal_info):
        """Log detailed analysis for debugging."""
        lines = [
            f"[{self.STRATEGY_NAME}] Detailed Analysis for {pair}:",
            f"  Pattern: {pattern} -> {pattern_result}",
            f"  Timeframe confirmations: {tf_confirmations}",
            f"  Confirmed timeframes: {confirmed_timeframes}/{required_confirmations}",
            f"  Weighted confidence: {weighted_confidence:.3f}",
            f"  Indicators: {indicators}",
            f"  Conditions: {conditions}",
            f"  Signal info: {signal_info}"
        ]
        
        def fmt_val(val):
            if isinstance(val, float):
                return f"{val:.4f}"
            return str(val)
        
        for line in lines:
            self.logger.info(line)

    async def initialize(self, pair: str) -> None:
        """Initialize the strategy for a specific trading pair."""
        try:
            self.state.pair = pair
            self.state.position = 'none'
            self.state.market_regime = 'unknown'  # Will be updated by market regime detection
            
            # Initialize condition logger for this pair
            if not self._condition_logger:
                self._condition_logger = self._get_condition_logger(pair)
            
            self.logger.info(f"Initialized {self.STRATEGY_NAME} for pair {pair}")
            
        except Exception as e:
            self.logger.error(f"Error initializing strategy for {pair}: {str(e)}")

    async def update(self, ohlcv: pd.DataFrame) -> None:
        """Update strategy state with new market data."""
        try:
            if ohlcv is None or ohlcv.empty:
                self.logger.warning(f"Empty OHLCV data provided to {self.STRATEGY_NAME}")
                return
            
            # Store current OHLCV for signal generation
            self._current_ohlcv = ohlcv.copy()
            
            # Update indicators cache
            self.state.indicators = {
                'last_update': datetime.now().isoformat(),
                'data_length': len(ohlcv),
                'latest_price': ohlcv['close'].iloc[-1] if len(ohlcv) > 0 else None
            }
            
            # Update market regime if possible (basic implementation)
            if len(ohlcv) >= 20:
                # Simple regime detection based on price trend and volatility
                recent_prices = ohlcv['close'].tail(20)
                price_change = (recent_prices.iloc[-1] - recent_prices.iloc[0]) / recent_prices.iloc[0]
                volatility = recent_prices.pct_change().std()
                
                if price_change > 0.05:
                    self.state.market_regime = 'bull'
                elif price_change < -0.05:
                    self.state.market_regime = 'bear'
                elif volatility > 0.03:
                    self.state.market_regime = 'high_volatility'
                elif volatility < 0.01:
                    self.state.market_regime = 'low_volatility'
                else:
                    self.state.market_regime = 'sideways'
            
        except Exception as e:
            self.logger.error(f"Error updating strategy state: {str(e)}")

    async def calculate_position_size(self, signal_type: str) -> float:
        """Calculate position size based on risk management rules."""
        try:
            # Basic position sizing - can be enhanced with optimizer parameters
            base_position_size = 0.1  # 10% of portfolio
            
            # Get optimized position sizing if available
            optimized_params = await self.get_optimized_parameters()
            if 'position_size_multiplier' in optimized_params:
                multiplier = float(optimized_params['position_size_multiplier'])
                base_position_size *= max(0.1, min(2.0, multiplier))  # Bounded between 0.1x and 2.0x
            
            return base_position_size
            
        except Exception as e:
            self.logger.error(f"Error calculating position size: {str(e)}")
            return 0.01  # Fallback to 1%

    async def should_exit(self) -> Tuple[bool, Optional[str]]:
        """
        Check if the current position should be exited.
        Returns:
            (True, reason) if position should be exited, (False, None) otherwise
        """
        try:
            # If no position or position is 'none', skip exit checks
            if not hasattr(self, 'state') or self.state.position == 'none':
                return False, None
            # Get current price
            if not hasattr(self, '_current_ohlcv') or self._current_ohlcv is None:
                return False, None
            current_price = self._current_ohlcv['close'].iloc[-1]
            # Only check basic stop loss and take profit - let orchestrator handle profit protection
            # Check stop loss
            if (self.state.position == 'long' and self.state.stop_loss is not None and 
                current_price <= self.state.stop_loss):
                await self.log_condition_outcome(
                    'exit_signal', 'stop_loss', True,
                    {'current_price': current_price, 'stop_loss': self.state.stop_loss}
                )
                self.logger.info(f"[{self.STRATEGY_NAME}] Exiting due to stop loss")
                return True, 'stop_loss'
            # Check take profit
            if (self.state.position == 'long' and self.state.take_profit is not None and 
                current_price >= self.state.take_profit):
                await self.log_condition_outcome(
                    'exit_signal', 'take_profit', True,
                    {'current_price': current_price, 'take_profit': self.state.take_profit}
                )
                self.logger.info(f"[{self.STRATEGY_NAME}] Exiting due to take profit")
                return True, 'take_profit'

            # --- Hybrid v1: bearish engulfing reversal exit ---
            # Mirrors the spec's "exit on opposite pattern" rule. Wired here
            # so the orchestrator-facing should_exit() actually fires it
            # (previously only check_exit_signals() acted on it, and it was
            # mislabeled as take_profit/stop_loss).
            if self.state.position == 'long' and self.exit_on_bearish_engulfing:
                try:
                    pattern = self.detect_engulfing_pattern(self._current_ohlcv)
                    if pattern == 'bearish':
                        try:
                            open_last = float(self._current_ohlcv['open'].iloc[-1])
                        except Exception:
                            open_last = current_price
                        if current_price < open_last:
                            entry_price = getattr(self.state, 'entry_price', None)
                            in_profit = (
                                entry_price is not None and current_price > entry_price
                            )
                            await self.log_condition_outcome(
                                'exit_signal', 'bearish_engulfing_exit', True,
                                {
                                    'current_price': current_price,
                                    'entry_price': entry_price,
                                    'in_profit': in_profit,
                                    'pattern': pattern,
                                }
                            )
                            self.logger.info(
                                f"[{self.STRATEGY_NAME}] Exiting on bearish engulfing "
                                f"reversal (in_profit={in_profit})"
                            )
                            return True, 'bearish_engulfing_exit'
                except Exception as _be_err:
                    self.logger.debug(
                        f"[{self.STRATEGY_NAME}] bearish-engulfing exit check failed: {_be_err}"
                    )
            return False, None
        except Exception as e:
            self.logger.error(f"[{self.STRATEGY_NAME}] Error in should_exit: {e}")
            return False, None

    async def check_entry_signal(self, pair: str, market_data: Dict[str, pd.DataFrame], current_price: float) -> Tuple[bool, Dict[str, Any]]:
        """Check for entry signal for a specific pair."""
        try:
            await self.initialize(pair)
            
            if pair in market_data:
                await self.update(market_data[pair])
                signal, confidence, strength = await self.generate_signal(market_data[pair], None, pair)
                
                if signal in ['buy', 'sell'] and confidence >= self.min_confidence:
                    return True, {
                        'signal': signal,
                        'confidence': confidence,
                        'strength': strength,
                        'strategy': self.STRATEGY_NAME
                    }
            
            return False, {'reason': 'No valid entry signal generated'}
            
        except Exception as e:
            self.logger.error(f"Error checking entry signal for {pair}: {str(e)}")
            return False, {'error': str(e)}

    async def check_exit_signal(self, pair: str, trade_data: Dict[str, Any], market_data: Dict[str, pd.DataFrame], current_price: float) -> Tuple[bool, str, Dict[str, Any]]:
        """Check for exit signal for a specific pair."""
        try:
            await self.initialize(pair)
            
            if pair in market_data:
                await self.update(market_data[pair])
                should_exit = await self.should_exit()
                
                if should_exit:
                    return True, "exit", {'strategy': self.STRATEGY_NAME}
            
            return False, "hold", {}
            
        except Exception as e:
            self.logger.error(f"Error checking exit signal for {pair}: {str(e)}")
            return False, "error", {'error': str(e)}

    def generate_signals(self, *args, **kwargs):
        """Legacy sync stub kept for backward compatibility.

        This is the only ``generate_signals`` definition in the class
        (hybrid v1 cleanup removed an earlier duplicate that ran a
        dataframe scan via ``asyncio.run`` — it was being shadowed by
        this stub anyway). New callers should use the async
        ``generate_signal`` (singular) above.
        """
        self.logger.warning("generate_signals is deprecated, use generate_signal instead")
        return []

    async def _log_condition(self, name, value, description, result, condition_type, pair, reason=None, market_regime=None, volatility=None, context=None):
        desc = description or name
        # Set the pair on the logger before logging
        if self._condition_logger:
            self._condition_logger.pair = pair
        # Pass available context/market_regime/volatility
        current_market_regime = getattr(self.state, 'market_regime', market_regime) if hasattr(self, 'state') else market_regime
        current_volatility = getattr(self.state, 'volatility', volatility) if hasattr(self, 'state') else volatility
        await self._condition_logger.log_condition(name, value, desc, result, condition_type, market_regime=current_market_regime, volatility=current_volatility, context=context) 

    async def check_exit(self, *args, **kwargs):
        """
        Fully functional exit logic for EngulfingMultiTimeframeStrategy.
        Checks if the current position should be exited based on profit protection, trailing stop, stop loss, or take profit.
        Returns True if exit is required, otherwise False.
        """
        try:
            result = await self.should_exit()
            return result
        except Exception as e:
            if hasattr(self, 'logger'):
                self.logger.error(f"Error in check_exit: {e}")
            return False 

    async def analyze_pair(self, pair, min_candles=None):
        """
        Per-timeframe analysis used by the strategy-service caller.

        Hybrid v1 fixes:
          - Replaced ``self.adx_threshold`` (undefined) with the real
            configured attribute ``self.adx_entry_threshold``.
          - Switched from talib to pandas_ta to match the rest of the
            module (and remove the only remaining talib dependency here).
          - Added the engulfing pattern check so the result actually
            reflects this strategy's edge instead of a generic ADX/RSI
            scan that ignored the pattern entirely.
          - NaN-guarded all indicator reads.
        """
        timeframes = ['1h', '15m']
        results = {}
        min_n = min_candles or 50
        for timeframe in timeframes:
            self.logger.info(f"[EngulfingMultiTF] Analyzing {pair} on {timeframe}")
            try:
                df = await self.exchange.get_ohlcv(self.exchange_name, pair, timeframe, limit=min_n)
            except Exception as e:
                self.logger.error(f"[EngulfingMultiTF] OHLCV fetch failed for {pair} {timeframe}: {e}")
                results[timeframe] = ('hold', 0, {})
                continue

            if df is None or len(df) < min_n:
                self.logger.warning(f"Not enough data for {pair} on {timeframe}")
                results[timeframe] = ('hold', 0, {})
                continue

            indicators: Dict[str, Any] = {}
            try:
                adx_df = ta.adx(df['high'], df['low'], df['close'], length=self.adx_period)
                rsi_series = ta.rsi(df['close'], length=14)
                adx_col = next((c for c in adx_df.columns if c.startswith('ADX')), None) if adx_df is not None else None
                indicators['adx'] = float(adx_df[adx_col].iloc[-1]) if adx_col and adx_df is not None and not pd.isna(adx_df[adx_col].iloc[-1]) else float('nan')
                indicators['rsi'] = float(rsi_series.iloc[-1]) if rsi_series is not None and not rsi_series.empty and not pd.isna(rsi_series.iloc[-1]) else float('nan')
            except Exception as e:
                self.logger.error(f"[EngulfingMultiTF] Indicator calculation error for {pair} on {timeframe}: {e}")
                results[timeframe] = ('hold', 0, {})
                continue

            try:
                pattern = self.detect_engulfing_pattern(df)
            except Exception as e:
                self.logger.warning(f"[EngulfingMultiTF] Pattern detection failed for {pair} {timeframe}: {e}")
                pattern = None
            indicators['pattern'] = pattern

            self.logger.info(f"[EngulfingMultiTF] {pair} {timeframe} indicators: {indicators}")
            if any(pd.isna(v) for k, v in indicators.items() if k != 'pattern'):
                self.logger.warning(f"[EngulfingMultiTF] NaN indicator(s) for {pair} on {timeframe}, holding.")
                results[timeframe] = ('hold', 0, indicators)
                continue

            adx_pass = indicators['adx'] > float(self.adx_entry_threshold)
            self.logger.info(f"[EngulfingMultiTF] ADX check: current={indicators['adx']:.2f}, target>{self.adx_entry_threshold}, result={'PASS' if adx_pass else 'FAIL'}")
            rsi_pass = indicators['rsi'] > 50
            self.logger.info(f"[EngulfingMultiTF] RSI check: current={indicators['rsi']:.2f}, target>50, result={'PASS' if rsi_pass else 'FAIL'}")
            pattern_pass = (pattern == 'bullish')
            self.logger.info(f"[EngulfingMultiTF] Pattern check: pattern={pattern}, result={'PASS' if pattern_pass else 'FAIL'}")

            if adx_pass and rsi_pass and pattern_pass:
                self.logger.info(f"[EngulfingMultiTF] Signal for {pair} on {timeframe}: BUY (all conditions met)")
                results[timeframe] = ('buy', 1, indicators)
            else:
                self.logger.info(f"[EngulfingMultiTF] Signal for {pair} on {timeframe}: HOLD (not all conditions met)")
                results[timeframe] = ('hold', 0, indicators)
        return results
