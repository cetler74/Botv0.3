"""
Hybrid Heikin-Ashi multi-timeframe trend-confluence strategy for crypto trading.

Design (HA-Hybrid v1):
  • Core engine = battle-tested multi-timeframe (4h/1h/15m) confluence with
    hard-veto filters (ADX, ATR, volume, positive trend slope, EMA confluence,
    15m-above-EMA9 micro-trend) and ATR-based dynamic SL/TP. All entry/exit
    SL/TP arithmetic stays on the RAW OHLC because Heikin-Ashi prices are
    synthetic averages and cannot be used as real stop levels.

  • Heikin-Ashi LAYER (NEW) = computed from raw OHLC and used purely as a
    SOFT CONFIRMATION that boosts signal_strength when the textbook HA
    bullish-continuation pattern is present (consecutive green HA candles
    with no lower wick). HA color flips on the execution timeframe also
    contribute a SOFT EXIT signal (gated by RSI / momentum so we don't
    bail on every minor pullback).

  • MACD CONFIRMATION (NEW) = standard 12/26/9 MACD on raw close acts as
    an additional soft-confirmation tick (MACD line above signal AND
    rising histogram). Bearish MACD cross combined with HA color flip
    triggers an early exit.

Heikin-Ashi formulas (canonical):
    HA_Close = (Open + High + Low + Close) / 4
    HA_Open  = (Prev HA_Open + Prev HA_Close) / 2
    HA_High  = max(High, HA_Open, HA_Close)
    HA_Low   = min(Low,  HA_Open, HA_Close)

Configurable parameters live under `strategies.heikin_ashi.parameters` in
config/config.yaml. New HA / MACD knobs (all optional, sane defaults):

  enable_ha_confirmation:        true
  ha_min_green_streak:           2          # 2-3 consecutive green HA candles
  ha_no_lower_wick_tolerance:    0.0001     # |HA_Open - HA_Low| / HA_Open <= tol
  ha_confirmation_boost:         0.10       # additive bump to signal_strength
  ha_exit_on_color_flip:         true
  ha_exit_rsi_floor:             55         # only flip-exit if RSI >= floor
  enable_macd_confirmation:      true
  macd_fast:                     12
  macd_slow:                     26
  macd_signal:                   9
  macd_confirmation_boost:       0.05
"""
import pandas as pd
import numpy as np
import pandas_ta as ta
import logging
from typing import Tuple, Optional, Dict, Any
from strategy.base_strategy import BaseStrategy, StrategyState

logger = logging.getLogger(__name__)

class HeikinAshiStrategy(BaseStrategy):
    def __init__(self, config, exchange, database, redis_client=None, exchange_name=None):
        # Load config from the correct section in strategies.heikin_ashi
        if hasattr(config, 'strategies') and hasattr(config.strategies, 'heikin_ashi'):
            strat_cfg = config.strategies.heikin_ashi.parameters if hasattr(config.strategies.heikin_ashi, 'parameters') else {}
        elif isinstance(config, dict) and 'strategies' in config and 'heikin_ashi' in config['strategies']:
            strat_cfg = config['strategies']['heikin_ashi'].get('parameters', {})
        elif isinstance(config, dict) and 'heikin_ashi' in config:
            # Direct key access for strategy service config format
            strat_cfg = config['heikin_ashi'].get('parameters', {})
        elif isinstance(config, dict) and 'parameters' in config:
            # Direct parameters config from strategy service
            strat_cfg = config['parameters']
        else:
            strat_cfg = config.get('heikin_ashi_strategy', {})  # Fallback to old key
            
        # Call parent constructor
        super().__init__(config, exchange, database, redis_client)
        
        self.exchange_name = exchange_name or strat_cfg.get('exchange_name', 'binance')
        self.min_candles = strat_cfg.get('min_candles', 50)
        self.adx_period = strat_cfg.get('adx_period', 7)
        self.rsi_period = strat_cfg.get('rsi_period', 7)
        self.atr_period = strat_cfg.get('atr_period', 7)
        # Optimized ADX threshold for better win rate
        self.adx_threshold = strat_cfg.get('adx_threshold', 27)  # CRYPTO OPTIMIZED: 25-30 range for crypto trend strength
        
        # Updated RSI ranges for crypto market volatility
        self.rsi_overbought = strat_cfg.get('rsi_overbought', 75)  # CRYPTO OPTIMIZED: Standard crypto overbought
        self.rsi_oversold = strat_cfg.get('rsi_oversold', 25)  # CRYPTO OPTIMIZED: Standard crypto oversold level
        self.rsi_buy_threshold = strat_cfg.get('rsi_buy_threshold', 40)
        self.rsi_confluence_threshold = strat_cfg.get('rsi_confluence_threshold', 45)  # OPTIMIZED: Lower threshold
        self.rsi_uptrend_min = strat_cfg.get('rsi_uptrend_min', 40)  # CRYPTO OPTIMIZED: Uptrend momentum threshold
        self.rsi_uptrend_max = strat_cfg.get('rsi_uptrend_max', 70)  # CRYPTO OPTIMIZED: Uptrend bias ceiling
        
        # Enhanced profitability filters
        self.require_ema_confluence = strat_cfg.get('require_ema_confluence', False)  # FINAL FIX: Use False as fallback instead of True
        self.ema_fast_period = strat_cfg.get('ema_fast_period', 9)
        self.ema_slow_period = strat_cfg.get('ema_slow_period', 21)
        # PnL-FIX v12: Stricter macro gate — previously `should_buy = macro_buy OR signal_buy`
        # allowed pure 1h impulse entries while 4h was still HOLD (chop / counter-trend longs).
        self.require_macro_buy_vote = strat_cfg.get('require_macro_buy_vote', False)
        # 4h close must be above 4h EMA(slow) when True (structural uptrend alignment).
        self.require_macro_price_above_slow_ema = strat_cfg.get(
            'require_macro_price_above_slow_ema', False
        )
        # PnL-FIX v8 (F4): Instant-loss filter on the execution timeframe.
        # Live data showed ARC/USD, API3/USD, AAVE/USD heikin_ashi entries
        # all hit SL with peak == entry (i.e. price went straight down from
        # the first tick). A simple `15m close > EMA9` gate vetos entries
        # whose immediate micro-trend is already bearish, since heikin_ashi's
        # 1h/4h trend bias can lag intraday reversals.
        self.require_execution_above_ema_fast = strat_cfg.get(
            'require_execution_above_ema_fast', True
        )
        self.min_trend_strength = strat_cfg.get('min_trend_strength', 0.002)
        self.trend_period = strat_cfg.get('trend_period', 14)  # Period for trend strength calculation
        # Enhanced volume requirements for better signal quality
        self.volume_spike_multiplier = strat_cfg.get('volume_spike_multiplier', 1.5)  # OPTIMIZED: Lower requirement
        self.min_candle_size = strat_cfg.get('min_candle_size', 0.002)
        self.volume_sma_period = strat_cfg.get('volume_sma_period', 7)
        self.volume_threshold_percentage = strat_cfg.get('volume_threshold_percentage', 0.35)  # CRYPTO OPTIMIZED: Enhanced volume filtering (0.3-0.4 range)
        
        # ATR-based dynamic stop-loss parameters
        self.atr_stop_multiplier = strat_cfg.get('atr_stop_multiplier', 2.25)  # CRYPTO OPTIMIZED: 2.0-2.5x ATR for stops
        self.atr_take_profit_multiplier = strat_cfg.get('atr_take_profit_multiplier', 3.0)  # Risk-reward ratio optimization
        
        # PHASE 2: Multi-Timeframe Hierarchy Parameters
        self.macro_timeframe = strat_cfg.get('macro_timeframe', '4h')  # Primary trend analysis
        self.signal_timeframe = strat_cfg.get('signal_timeframe', '1h')  # Signal confirmation 
        self.execution_timeframe = strat_cfg.get('execution_timeframe', '15m')  # Entry/exit timing
        self.timeframe_hierarchy = [self.macro_timeframe, self.signal_timeframe, self.execution_timeframe]
        
        # Confluence scoring system
        self.min_confluence_score = strat_cfg.get('min_confluence_score', 1.5)  # OPTIMIZED: Lower requirement for easier qualification
        self.macro_weight = strat_cfg.get('macro_weight', 2.5)  # OPTIMIZED: Lower weight
        self.signal_weight = strat_cfg.get('signal_weight', 2.0)  # Medium weight for signal
        self.execution_weight = strat_cfg.get('execution_weight', 1.5)  # OPTIMIZED: Higher weight

        # PnL-FIX v6: Per-timeframe signal-strength thresholds.
        # Were hardcoded at 0.70 / 0.80 / 0.85 — now config-driven so the
        # bot can be tuned without a code change. Defaults preserve the
        # previous behaviour for backwards compatibility.
        self.tf_strength_threshold_macro = strat_cfg.get('tf_strength_threshold_macro', 0.70)
        self.tf_strength_threshold_signal = strat_cfg.get('tf_strength_threshold_signal', 0.80)
        self.tf_strength_threshold_execution = strat_cfg.get('tf_strength_threshold_execution', 0.85)
        
        # PHASE 3: Advanced Technical Features
        self.enable_rsi_divergence = strat_cfg.get('enable_rsi_divergence', True)
        self.divergence_lookback = strat_cfg.get('divergence_lookback', 20)  # Periods to look back for divergence
        self.enable_support_resistance = strat_cfg.get('enable_support_resistance', True)
        self.sr_period = strat_cfg.get('sr_period', 50)  # Period for S/R calculation
        self.breakout_confirmation_volume = strat_cfg.get('breakout_confirmation_volume', 1.5)  # Volume multiplier for breakouts
        
        # Enhanced target ratios for advanced features
        self.phase2_target_rr = strat_cfg.get('phase2_target_rr', 2.0)  # 1:2.0 risk/reward for Phase 2
        self.phase3_target_rr = strat_cfg.get('phase3_target_rr', 2.5)  # 1:2.5 risk/reward for Phase 3
        self.risk_per_trade = strat_cfg.get('risk_per_trade', 0.01)
        self.max_hold_time_hours = strat_cfg.get('max_hold_time_hours', 48)
        self.primary_timeframe = strat_cfg.get('primary_timeframe', '1h')

        # HA-Hybrid v1 — true Heikin-Ashi confirmation layer (soft).
        # HA candles are computed from raw OHLC and used ONLY to nudge
        # signal_strength up/down. SL/TP still use raw OHLC + ATR because
        # HA prices are synthetic averages, not tradeable levels.
        self.enable_ha_confirmation = strat_cfg.get('enable_ha_confirmation', True)
        self.ha_min_green_streak = int(strat_cfg.get('ha_min_green_streak', 2))
        self.ha_no_lower_wick_tolerance = float(strat_cfg.get('ha_no_lower_wick_tolerance', 0.0001))
        self.ha_confirmation_boost = float(strat_cfg.get('ha_confirmation_boost', 0.10))
        self.ha_exit_on_color_flip = strat_cfg.get('ha_exit_on_color_flip', True)
        self.ha_exit_rsi_floor = float(strat_cfg.get('ha_exit_rsi_floor', 55))

        # HA-Hybrid v1 — MACD confirmation (12/26/9 standard) on raw close.
        # Used as an additional soft-confirmation tick for entries and
        # combined with HA color flip for an early bearish-cross exit.
        self.enable_macd_confirmation = strat_cfg.get('enable_macd_confirmation', True)
        self.macd_fast = int(strat_cfg.get('macd_fast', 12))
        self.macd_slow = int(strat_cfg.get('macd_slow', 26))
        self.macd_signal = int(strat_cfg.get('macd_signal', 9))
        self.macd_confirmation_boost = float(strat_cfg.get('macd_confirmation_boost', 0.05))

        # Log final loaded parameters for verification
        logger.info(f"✅ HeikinAshi ADVANCED v2.0 params:")
        logger.info(f"   Core: ADX>{self.adx_threshold}, RSI ({self.rsi_oversold}-{self.rsi_overbought}, uptrend {self.rsi_uptrend_min}-{self.rsi_uptrend_max})")
        logger.info(f"   Volume: spike {self.volume_spike_multiplier}x, base {self.volume_threshold_percentage}")
        logger.info(f"   ATR: stop {self.atr_stop_multiplier}x, target {self.atr_take_profit_multiplier}x")
        logger.info(f"   Multi-TF: {'/'.join(self.timeframe_hierarchy)}, confluence min {self.min_confluence_score}/3")
        logger.info(
            f"   TF strength thresholds: macro={self.tf_strength_threshold_macro:.2f}, "
            f"signal={self.tf_strength_threshold_signal:.2f}, "
            f"execution={self.tf_strength_threshold_execution:.2f}"
        )
        logger.info(f"   Advanced: RSI divergence {self.enable_rsi_divergence}, S/R {self.enable_support_resistance}")
        logger.info(f"   Targets: Phase2 1:{self.phase2_target_rr}, Phase3 1:{self.phase3_target_rr}")
        logger.info(
            f"   HA layer: enabled={self.enable_ha_confirmation}, "
            f"min_streak={self.ha_min_green_streak}, boost=+{self.ha_confirmation_boost:.2f}, "
            f"exit_on_flip={self.ha_exit_on_color_flip} (rsi_floor={self.ha_exit_rsi_floor:.0f})"
        )
        logger.info(
            f"   MACD layer: enabled={self.enable_macd_confirmation}, "
            f"params={self.macd_fast}/{self.macd_slow}/{self.macd_signal}, "
            f"boost=+{self.macd_confirmation_boost:.2f}"
        )

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
        logger.info(f"Initialized Heikin Ashi strategy for {pair}")

    async def analyze_pair(self, pair, min_candles=None, exchange_name=None, market_data=None):
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"[HeikinAshiStrategy] analyze_pair called for {pair}")
        """
        Analyze a trading pair using multi-timeframe hierarchy and advanced technical features.
        """
        # Use multi-timeframe hierarchy for analysis
        timeframes = self.timeframe_hierarchy
        results = {}
        timeframe_signals = {}
        
        # Phase 2: Multi-timeframe analysis with hierarchy
        for i, timeframe in enumerate(timeframes):
            logger.info(f"[HeikinAshiStrategy] Analyzing {pair} on {timeframe} (hierarchy level {i+1})")
            min_candles_tf = max(min_candles or self.min_candles, 100)  # Ensure enough data for advanced features
            
            # Use provided market_data if available, otherwise try to get from exchange
            if market_data is not None and timeframe in market_data:
                df = market_data[timeframe]
            elif self.exchange is not None:
                ex_name = exchange_name if exchange_name is not None else self.exchange_name
                df = await self.exchange.get_ohlcv(ex_name, pair, timeframe, limit=min_candles_tf)
            else:
                logger.warning(f"[HeikinAshiStrategy] No market data available for {pair} on {timeframe} and no exchange object")
                results[timeframe] = ('hold', 0, {})
                continue
                
            if df is None or len(df) < min_candles_tf:
                logger.warning(f"[HeikinAshiStrategy] Not enough data for {pair} on {timeframe}: df={df}")
                logger.debug(f"[HeikinAshiStrategy] DataFrame for {pair} on {timeframe}: {df.shape if df is not None else 'None'}\n{df.head() if df is not None else ''}")
                results[timeframe] = ('hold', 0, {})
                continue
            df = df.sort_index()
            logger.debug(f"[HeikinAshiStrategy] DataFrame shape for {pair} on {timeframe}: {df.shape}")
            logger.debug(f"[HeikinAshiStrategy] DataFrame columns: {df.columns.tolist()}")
            logger.debug(f"[HeikinAshiStrategy] DataFrame head: {df.head()}")
            
            # Data quality validation - skip pairs with insufficient price movement
            price_range = (df['high'].max() - df['low'].min()) / df['close'].mean() if df['close'].mean() > 0 else 0
            avg_volume = df['volume'].mean()
            if price_range < 0.001 or avg_volume < 1.0:  # Less than 0.1% price range or minimal volume
                logger.warning(f"[HeikinAshiStrategy] {pair} on {timeframe}: Insufficient price movement ({price_range:.6f}) or volume ({avg_volume:.1f}), holding.")
                timeframe_signals[timeframe] = ('hold', 0, {})
                continue
            
            indicators = {}
            try:
                # Check if we have enough data for advanced indicators
                min_required = max(self.adx_period, self.rsi_period, self.atr_period, self.volume_sma_period, self.sr_period, self.divergence_lookback + 10)
                if len(df) < min_required:
                    logger.warning(f"[HeikinAshiStrategy] Not enough data for advanced indicators: {len(df)} < {min_required}")
                    timeframe_signals[timeframe] = ('hold', 0, {})
                    continue
                
                adx_result = ta.adx(df['high'], df['low'], df['close'], length=self.adx_period)
                rsi = ta.rsi(df['close'], length=self.rsi_period)
                atr = ta.atr(df['high'], df['low'], df['close'], length=self.atr_period)
                volume_sma = df['volume'].rolling(self.volume_sma_period).mean()
                
                # Enhanced profitability indicators
                ema_fast = ta.ema(df['close'], length=self.ema_fast_period)
                ema_slow = ta.ema(df['close'], length=self.ema_slow_period)
                
                # Calculate trend strength (price change over trend period)
                trend_strength = (df['close'].iloc[-1] - df['close'].iloc[-self.trend_period]) / df['close'].iloc[-self.trend_period]
                
                logger.debug(f"[HeikinAshiStrategy] ADX result: {adx_result}")
                logger.debug(f"[HeikinAshiStrategy] RSI result: {rsi}")
                logger.debug(f"[HeikinAshiStrategy] ATR result: {atr}")
                
                # Extract ADX value - pandas-ta returns a DataFrame with ADX, DMP, DMN columns
                if hasattr(adx_result, 'columns') and 'ADX_' + str(self.adx_period) in adx_result.columns:
                    adx = adx_result['ADX_' + str(self.adx_period)].iloc[-1]
                elif hasattr(adx_result, 'iloc'):
                    # If it's a Series, get the last value
                    adx = adx_result.iloc[-1]
                else:
                    # Fallback
                    adx = adx_result[-1]
                
                # Base indicators
                rsi_value = rsi.iloc[-1] if hasattr(rsi, 'iloc') else rsi[-1]
                atr_value = atr.iloc[-1] if hasattr(atr, 'iloc') else atr[-1]
                current_price = df['close'].iloc[-1]
                
                indicators = {
                    'adx': adx,
                    'rsi': rsi_value,
                    'atr': atr_value,
                    'volume': df['volume'].iloc[-1],
                    'volume_sma': volume_sma.iloc[-1],
                    'ema_fast': ema_fast.iloc[-1] if hasattr(ema_fast, 'iloc') else ema_fast[-1],
                    'ema_slow': ema_slow.iloc[-1] if hasattr(ema_slow, 'iloc') else ema_slow[-1],
                    'trend_strength': trend_strength,
                    'current_price': current_price,
                    'timeframe': timeframe
                }
                
                # PHASE 3: Advanced Technical Features
                if self.enable_rsi_divergence:
                    divergences = await self.detect_rsi_divergence(df, rsi)
                    indicators['divergences'] = divergences
                    indicators['has_bullish_divergence'] = divergences['bullish_divergence'] or divergences['hidden_bullish']
                    indicators['has_bearish_divergence'] = divergences['bearish_divergence'] or divergences['hidden_bearish']
                else:
                    indicators['has_bullish_divergence'] = False
                    indicators['has_bearish_divergence'] = False
                
                if self.enable_support_resistance:
                    sr_levels = await self.calculate_support_resistance(df)
                    indicators['support_resistance'] = sr_levels
                    indicators['near_support'] = abs(current_price - sr_levels['support']) / current_price < 0.01  # Within 1%
                    indicators['near_resistance'] = abs(current_price - sr_levels['resistance']) / current_price < 0.01
                    indicators['above_pivot'] = current_price > sr_levels['pivot']
                else:
                    indicators['near_support'] = False
                    indicators['near_resistance'] = False
                    indicators['above_pivot'] = True

                # HA-Hybrid v1: Heikin-Ashi confirmation layer (soft).
                # Computed from raw OHLC; never used to drive SL/TP because HA
                # prices are synthetic averages.
                if self.enable_ha_confirmation:
                    ha_df = self.compute_heikin_ashi(df)
                    ha_pattern = self.detect_ha_pattern(ha_df)
                    indicators['ha_pattern'] = ha_pattern
                    indicators['ha_bullish_continuation'] = ha_pattern['bullish_continuation']
                    indicators['ha_bearish_reversal'] = ha_pattern['bearish_reversal']
                else:
                    indicators['ha_pattern'] = None
                    indicators['ha_bullish_continuation'] = False
                    indicators['ha_bearish_reversal'] = False

                # HA-Hybrid v1: MACD confirmation layer (soft).
                if self.enable_macd_confirmation:
                    macd_state = self.compute_macd_state(df)
                    indicators['macd_state'] = macd_state
                    indicators['macd_bullish'] = macd_state['bullish']
                    indicators['macd_bearish'] = macd_state['bearish']
                else:
                    indicators['macd_state'] = None
                    indicators['macd_bullish'] = False
                    indicators['macd_bearish'] = False
            except Exception as e:
                logger.error(f"[HeikinAshiStrategy] Indicator calculation error for {pair} on {timeframe}: {e}")
                logger.error(f"[HeikinAshiStrategy] DataFrame info: {df.info()}")
                timeframe_signals[timeframe] = ('hold', 0, {})
                continue
            logger.info(f"[HeikinAshiStrategy] {pair} {timeframe} indicators calculated")
            
            # Check for NaN values in core indicators
            core_indicators = ['adx', 'rsi', 'atr', 'volume', 'volume_sma', 'current_price']
            for name in core_indicators:
                if name in indicators and pd.isna(indicators[name]):
                    logger.warning(f"[HeikinAshiStrategy] Core indicator {name} is NaN for {pair} on {timeframe}, holding.")
                    timeframe_signals[timeframe] = ('hold', 0, indicators)
                    break
            else:
                # PHASE 2: Enhanced Multi-Timeframe Analysis
                # Enhanced condition logging for Heikin Ashi strategy
                # ADX check: use configured threshold
                adx_pass = indicators['adx'] > self.adx_threshold
                await self._log_condition(
                    'adx_check', indicators['adx'], f'ADX check for {pair} on {timeframe}', adx_pass, 'indicator', 
                    pair, context={'timeframe': timeframe, 'current_value': f"adx={indicators['adx']:.2f}", 'target_value': f"adx > {self.adx_threshold}"}
                )
                
                # Enhanced RSI buy signal: More selective conditions for profitability
                rsi_confluence_threshold = self.rsi_confluence_threshold
                
                # Crypto-optimized RSI conditions: oversold bounces OR strong uptrend momentum
                rsi_oversold_signal = indicators['rsi'] < self.rsi_oversold  # Crypto oversold (< 25)
                rsi_uptrend_signal = (indicators['rsi'] >= self.rsi_uptrend_min and 
                                    indicators['rsi'] <= self.rsi_uptrend_max)  # Uptrend bias 40-70
                rsi_pass = rsi_oversold_signal or rsi_uptrend_signal
                
                await self._log_condition(
                    'rsi_check', indicators['rsi'], f'Crypto-optimized RSI check for {pair} on {timeframe}', rsi_pass, 'indicator', 
                    pair, context={'timeframe': timeframe, 'current_value': f"rsi={indicators['rsi']:.2f}", 'target_value': f"rsi < {self.rsi_oversold} OR ({self.rsi_uptrend_min} <= rsi <= {self.rsi_uptrend_max})"}
                )
                
                atr_pass = indicators['atr'] > self.min_candle_size
                await self._log_condition(
                    'atr_check', indicators['atr'], f'ATR check for {pair} on {timeframe}', atr_pass, 'indicator', 
                    pair, context={'timeframe': timeframe, 'current_value': f"atr={indicators['atr']:.4f}", 'target_value': f"atr > {self.min_candle_size}"}
                )
                
                # Crypto-optimized volume requirements: stronger volume confirmation
                volume_spike_threshold = indicators['volume_sma'] * self.volume_spike_multiplier
                volume_base_threshold = indicators['volume_sma'] * self.volume_threshold_percentage
                # Require both base volume AND spike for high-quality signals
                vol_pass = (indicators['volume'] > volume_base_threshold and 
                           indicators['volume'] > volume_spike_threshold)
                await self._log_condition(
                    'volume_check', indicators['volume'], f'Crypto-optimized volume check for {pair} on {timeframe}', vol_pass, 'indicator', 
                    pair, context={'timeframe': timeframe, 'current_value': f"volume={indicators['volume']:.2f}", 'target_value': f"volume > {volume_base_threshold:.2f} AND volume > {volume_spike_threshold:.2f}"}
                )
                
                # EMA confluence check: price must be above both EMAs for uptrend confirmation
                ema_pass = True
                if self.require_ema_confluence:
                    price_above_emas = (indicators['current_price'] > indicators['ema_fast'] and 
                                      indicators['current_price'] > indicators['ema_slow'] and
                                      indicators['ema_fast'] > indicators['ema_slow'])  # EMAs aligned
                    ema_pass = price_above_emas
                    await self._log_condition(
                        'ema_confluence_check', indicators['current_price'], f'EMA confluence check for {pair} on {timeframe}', ema_pass, 'indicator', 
                        pair, context={'timeframe': timeframe, 'current_value': f"price={indicators['current_price']:.4f}, ema_fast={indicators['ema_fast']:.4f}, ema_slow={indicators['ema_slow']:.4f}", 'target_value': f"price > ema_fast > ema_slow"}
                    )
                
                # CRITICAL FIX (entry-correctness): For long-only strategy we must require a
                # *positive* trend strength. The previous abs() check let strong DOWNTRENDS pass
                # and produce BUY signals (root cause of large unrealized losses).
                trend_strength_value = indicators['trend_strength']
                trend_strength_pass = trend_strength_value >= self.min_trend_strength
                indicators['trend_direction_bullish'] = trend_strength_value > 0
                await self._log_condition(
                    'trend_strength_check', trend_strength_value,
                    f'Trend strength check for {pair} on {timeframe}', trend_strength_pass, 'indicator',
                    pair, context={
                        'timeframe': timeframe,
                        'current_value': f"trend_strength={trend_strength_value:.4f}",
                        'target_value': f"trend_strength >= {self.min_trend_strength} (positive only for long entries)"
                    }
                )
                
                # PHASE 3: Advanced signal enhancement with divergences and S/R
                divergence_boost = False
                sr_confirmation = True
                
                if self.enable_rsi_divergence:
                    # Bullish divergence provides signal boost
                    divergence_boost = indicators.get('has_bullish_divergence', False)
                    # Bearish divergence filters out signals
                    if indicators.get('has_bearish_divergence', False):
                        await self._log_condition(
                            'divergence_filter', 'bearish_divergence', f'RSI bearish divergence filter for {pair} on {timeframe}', False, 'signal',
                            pair, context={'timeframe': timeframe, 'current_value': 'bearish divergence detected', 'target_value': 'no bearish divergence'}
                        )
                        timeframe_signals[timeframe] = ('hold', 0, indicators)
                        continue
                
                if self.enable_support_resistance:
                    # Confirm signal is not near strong resistance
                    if indicators.get('near_resistance', False) and not indicators.get('above_pivot', True):
                        sr_confirmation = False
                        await self._log_condition(
                            'sr_filter', 'near_resistance', f'S/R resistance filter for {pair} on {timeframe}', False, 'signal',
                            pair, context={'timeframe': timeframe, 'current_value': 'near resistance level', 'target_value': 'away from resistance'}
                        )
                
                # PnL-FIX v8 (F4): Execution-timeframe instant-loss filter.
                # If the EXECUTION timeframe (15m by default) is below its
                # EMA9, the most recent micro-trend has already turned. Block
                # the signal here — even if 1h/4h still look bullish.
                exec_micro_trend_pass = True
                if (
                    self.require_execution_above_ema_fast
                    and timeframe == self.execution_timeframe
                ):
                    ema_fast_val = indicators.get('ema_fast')
                    cur_price = indicators.get('current_price')
                    if ema_fast_val is not None and cur_price is not None:
                        exec_micro_trend_pass = cur_price > ema_fast_val
                        await self._log_condition(
                            'exec_micro_trend_check', cur_price,
                            f'15m micro-trend filter for {pair} on {timeframe}',
                            exec_micro_trend_pass, 'indicator',
                            pair, context={
                                'timeframe': timeframe,
                                'current_value': f"close={cur_price:.6f}, ema_fast={ema_fast_val:.6f}",
                                'target_value': "close > ema_fast (15m micro-trend bullish)",
                            }
                        )

                # CRITICAL FIX (entry-correctness): Trend direction, ADX, ATR (volatility) and
                # Volume are HARD VETOS. Previously a 5/7 majority let signals fire even when
                # ATR + Volume both failed — producing entries in dead, illiquid markets.
                hard_vetos_pass = (
                    adx_pass and atr_pass and vol_pass and trend_strength_pass
                    and ema_pass and exec_micro_trend_pass
                )

                # Soft-quality checks still contribute to the strength score.
                base_checks = [
                    adx_pass, rsi_pass, atr_pass, vol_pass,
                    ema_pass, trend_strength_pass, sr_confirmation,
                ]
                signal_strength = sum(base_checks) / len(base_checks)
                if divergence_boost:
                    signal_strength = min(1.0, signal_strength * 1.2)  # 20% boost for divergence

                # HA-Hybrid v1: textbook HA bullish continuation pattern adds
                # an additive boost, while a bearish HA reversal applies a
                # symmetric penalty. These are SOFT signals — they only nudge
                # signal_strength, they never veto.
                if self.enable_ha_confirmation and isinstance(indicators.get('ha_pattern'), dict):
                    if indicators['ha_bullish_continuation']:
                        signal_strength = min(1.0, signal_strength + self.ha_confirmation_boost)
                        await self._log_condition(
                            'ha_confirmation', 'bullish_continuation',
                            f'HA bullish continuation for {pair} on {timeframe}', True, 'indicator',
                            pair, context={
                                'timeframe': timeframe,
                                'green_streak': indicators['ha_pattern']['green_streak'],
                                'last_no_lower_wick': indicators['ha_pattern']['last_no_lower_wick'],
                                'current_value': f"+{self.ha_confirmation_boost:.2f} boost applied",
                                'target_value': f"green_streak >= {self.ha_min_green_streak} AND no lower wicks",
                            }
                        )
                    elif indicators['ha_bearish_reversal']:
                        signal_strength = max(0.0, signal_strength - self.ha_confirmation_boost)
                        await self._log_condition(
                            'ha_confirmation', 'bearish_reversal',
                            f'HA bearish reversal for {pair} on {timeframe}', False, 'indicator',
                            pair, context={
                                'timeframe': timeframe,
                                'last_color': indicators['ha_pattern']['last_color'],
                                'current_value': f"-{self.ha_confirmation_boost:.2f} penalty applied",
                                'target_value': "no HA color flip from green to red/doji",
                            }
                        )

                # HA-Hybrid v1: MACD confirmation. Bullish posture (macd>signal
                # AND rising histogram) adds boost; bearish posture penalises.
                if self.enable_macd_confirmation and isinstance(indicators.get('macd_state'), dict):
                    if indicators['macd_bullish']:
                        signal_strength = min(1.0, signal_strength + self.macd_confirmation_boost)
                        await self._log_condition(
                            'macd_confirmation', 'bullish',
                            f'MACD bullish for {pair} on {timeframe}', True, 'indicator',
                            pair, context={
                                'timeframe': timeframe,
                                'macd': f"{indicators['macd_state']['macd']:.6f}",
                                'signal': f"{indicators['macd_state']['signal']:.6f}",
                                'hist': f"{indicators['macd_state']['hist']:.6f}",
                                'current_value': f"+{self.macd_confirmation_boost:.2f} boost applied",
                                'target_value': "macd > signal AND histogram rising",
                            }
                        )
                    elif indicators['macd_bearish']:
                        signal_strength = max(0.0, signal_strength - self.macd_confirmation_boost)
                        await self._log_condition(
                            'macd_confirmation', 'bearish',
                            f'MACD bearish for {pair} on {timeframe}', False, 'indicator',
                            pair, context={
                                'timeframe': timeframe,
                                'macd': f"{indicators['macd_state']['macd']:.6f}",
                                'signal': f"{indicators['macd_state']['signal']:.6f}",
                                'current_value': f"-{self.macd_confirmation_boost:.2f} penalty applied",
                                'target_value': "macd > signal AND histogram rising",
                            }
                        )

                # Force HOLD if any hard veto failed, regardless of signal_strength.
                if not hard_vetos_pass:
                    signal_strength = 0.0
                
                # Determine timeframe-specific signal based on hierarchy role.
                # PnL-FIX v6: thresholds are now configurable (see __init__).
                if timeframe == self.macro_timeframe:
                    tf_signal_threshold = self.tf_strength_threshold_macro
                elif timeframe == self.signal_timeframe:
                    tf_signal_threshold = self.tf_strength_threshold_signal
                else:
                    tf_signal_threshold = self.tf_strength_threshold_execution
                
                if signal_strength >= tf_signal_threshold:
                    # Determine target risk-reward ratio based on features enabled
                    if self.enable_rsi_divergence and self.enable_support_resistance:
                        target_rr = self.phase3_target_rr  # Phase 3: 1:2.5
                        atr_multiplier = self.phase3_target_rr
                    else:
                        target_rr = self.phase2_target_rr  # Phase 2: 1:2.0
                        atr_multiplier = self.phase2_target_rr
                    
                    await self._log_condition(
                        'signal_generation', 'buy', f'Advanced signal for {pair} on {timeframe}', True, 'signal', 
                        pair, context={
                            'timeframe': timeframe, 
                            'signal_strength': f'{signal_strength:.2f}',
                            'threshold': f'{tf_signal_threshold:.2f}',
                            'divergence_boost': divergence_boost,
                            'sr_confirmation': sr_confirmation,
                            'target_rr': f'1:{target_rr}',
                            'current_value': f"strength={signal_strength:.2f}, adx={indicators['adx']:.2f}, rsi={indicators['rsi']:.2f}", 
                            'target_value': f"advanced multi-timeframe conditions passed (threshold={tf_signal_threshold:.2f})"
                        }
                    )
                    
                    # Enhanced ATR-based calculations with phase-specific targets
                    current_price = indicators['current_price']
                    atr_value = indicators['atr']
                    stop_loss = current_price - (atr_value * self.atr_stop_multiplier)
                    take_profit = current_price + (atr_value * self.atr_stop_multiplier * atr_multiplier)
                    
                    indicators['suggested_stop_loss'] = stop_loss
                    indicators['suggested_take_profit'] = take_profit
                    indicators['risk_reward_ratio'] = atr_multiplier
                    indicators['signal_strength'] = signal_strength
                    indicators['divergence_boost'] = divergence_boost
                    
                    timeframe_signals[timeframe] = ('buy', signal_strength, indicators)
                else:
                    failed_checks = []
                    if not adx_pass: failed_checks.append('adx')
                    if not rsi_pass: failed_checks.append('rsi')
                    if not atr_pass: failed_checks.append('atr')
                    if not vol_pass: failed_checks.append('volume')
                    if not ema_pass: failed_checks.append('ema_confluence')
                    if not trend_strength_pass: failed_checks.append('trend_strength')
                    if not sr_confirmation: failed_checks.append('support_resistance')
                    
                    await self._log_condition(
                        'signal_generation', 'hold', f'Advanced signal for {pair} on {timeframe}', False, 'signal', 
                        pair, context={
                            'timeframe': timeframe, 
                            'failed_checks': failed_checks, 
                            'signal_strength': f'{signal_strength:.2f}',
                            'threshold': f'{tf_signal_threshold:.2f}',
                            'current_value': f"strength={signal_strength:.2f}, failed: {', '.join(failed_checks)}", 
                            'target_value': f"advanced conditions threshold {tf_signal_threshold:.2f}"
                        }
                    )
                    timeframe_signals[timeframe] = ('hold', signal_strength, indicators)
        
        # PHASE 2: Multi-timeframe confluence analysis
        confluence_score, signal_breakdown = await self.calculate_confluence_score(timeframe_signals)

        logger.info(f"[HeikinAshiStrategy] Multi-timeframe confluence for {pair}: score={confluence_score:.2f}, min_required={self.min_confluence_score}")

        # PnL-FIX v5 — Weighted confluence final signal.
        # Previously: final = execution_timeframe (15m) signal verbatim.
        # Problem: 4h + 1h could vote BUY @ strength=1.00 yet final was HOLD
        # because 15m noise failed a single filter (e.g. atr or volume).
        #
        # New logic:
        #   1) Confluence score must clear `min_confluence_score` (gate).
        #   2) BUY is allowed when EITHER
        #         (a) the macro (4h) timeframe voted BUY (trend confirmation), OR
        #         (b) the signal (1h) timeframe voted BUY AND macro did not
        #             explicitly vote HOLD with strong evidence.
        #      In either case, 15m only acts as an entry-timing tie-breaker:
        #      if 15m is BUY we use it for SL/TP precision; if 15m is HOLD
        #      we synthesise entry-timing details from the strongest BUY TF.
        #   3) Final confidence = the weighted confluence score itself,
        #      so a 4h+1h agreement is rewarded even if 15m is undecided.
        if confluence_score >= self.min_confluence_score:
            macro_sig = timeframe_signals.get(self.macro_timeframe,    ('hold', 0, {}))
            signal_sig = timeframe_signals.get(self.signal_timeframe,  ('hold', 0, {}))
            exec_sig  = timeframe_signals.get(self.execution_timeframe, ('hold', 0, {}))

            macro_buy  = macro_sig[0]  == 'buy'
            signal_buy = signal_sig[0] == 'buy'
            exec_buy   = exec_sig[0]   == 'buy'

            should_buy = macro_buy or signal_buy

            # PnL-FIX v12 — require 4h to actually vote BUY (not 1h-only confluence).
            if should_buy and self.require_macro_buy_vote:
                if not macro_buy:
                    should_buy = False
                    logger.info(
                        f"[HeikinAshiStrategy] HOLD {pair} — require_macro_buy_vote: "
                        f"macro={macro_sig[0]} (need 4h BUY), signal={signal_sig[0]}, exec={exec_sig[0]}"
                    )

            # PnL-FIX v12 — 4h structural filter: spot longs only when price sits above macro EMA slow.
            if should_buy and self.require_macro_price_above_slow_ema:
                macro_pack = macro_sig[2] if len(macro_sig) > 2 and isinstance(macro_sig[2], dict) else {}
                cp_m = macro_pack.get('current_price')
                es_m = macro_pack.get('ema_slow')
                try:
                    if cp_m is None or es_m is None or float(cp_m) <= float(es_m):
                        should_buy = False
                        logger.info(
                            f"[HeikinAshiStrategy] HOLD {pair} — macro not above EMA_slow "
                            f"(close={cp_m}, ema_slow={es_m})"
                        )
                except (TypeError, ValueError):
                    should_buy = False
                    logger.info(
                        f"[HeikinAshiStrategy] HOLD {pair} — macro EMA_slow filter skipped (bad values)"
                    )

            if should_buy:
                # Pick indicator pack from the most reliable BUY-voting TF
                # so SL/TP/ATR levels reflect a real bullish setup.
                if exec_buy and isinstance(exec_sig[2], dict):
                    chosen_tf, chosen_pack = self.execution_timeframe, exec_sig[2]
                elif signal_buy and isinstance(signal_sig[2], dict):
                    chosen_tf, chosen_pack = self.signal_timeframe, signal_sig[2]
                elif macro_buy and isinstance(macro_sig[2], dict):
                    chosen_tf, chosen_pack = self.macro_timeframe, macro_sig[2]
                else:
                    chosen_tf, chosen_pack = self.execution_timeframe, {}

                final_signal = 'buy'
                final_confidence = float(confluence_score)
                final_indicators = dict(chosen_pack) if isinstance(chosen_pack, dict) else {}
                final_indicators.update({
                    'confluence_score': confluence_score,
                    'signal_breakdown': signal_breakdown,
                    'final_decision_tf': chosen_tf,
                    'macro_vote':  macro_sig[0],
                    'signal_vote': signal_sig[0],
                    'exec_vote':   exec_sig[0],
                    'timeframe_signals': {
                        tf: (sig, conf) for tf, (sig, conf, _) in timeframe_signals.items()
                    },
                })
                results['final'] = (final_signal, final_confidence, final_indicators)
                logger.info(
                    f"[HeikinAshiStrategy] Final confluent signal for {pair}: BUY "
                    f"(confidence={final_confidence:.2f}, confluence={confluence_score:.2f}, "
                    f"macro={macro_sig[0]}, signal={signal_sig[0]}, exec={exec_sig[0]}, "
                    f"chosen_tf={chosen_tf})"
                )
            else:
                # Confluence ≥ threshold but neither macro nor signal voted BUY.
                # Stay flat — a 15m-only spike isn't enough.
                results['final'] = (
                    'hold',
                    float(confluence_score),
                    {
                        'confluence_score': confluence_score,
                        'signal_breakdown': signal_breakdown,
                        'reason': 'no_macro_or_signal_buy',
                    },
                )
                logger.info(
                    f"[HeikinAshiStrategy] HOLD for {pair} — confluence "
                    f"{confluence_score:.2f} ≥ {self.min_confluence_score} but "
                    f"macro+signal not bullish (macro={macro_sig[0]}, signal={signal_sig[0]})"
                )
        else:
            results['final'] = (
                'hold',
                float(confluence_score),
                {
                    'confluence_score': confluence_score,
                    'signal_breakdown': signal_breakdown,
                    'reason': 'insufficient_confluence',
                },
            )
            logger.info(
                f"[HeikinAshiStrategy] Insufficient confluence for {pair}: "
                f"{confluence_score:.2f} < {self.min_confluence_score}"
            )
        
        # Also return individual timeframe results for debugging
        for tf, (sig, conf, ind) in timeframe_signals.items():
            results[tf] = (sig, conf, ind)
        
        return results

    async def update(self, market_data):
        """No-op update for compatibility with the strategy manager."""
        pass

    async def generate_signal(self, market_data, *args, **kwargs):
        import logging
        logger = logging.getLogger(__name__)

        # OPTION-A: apply regime-driven parameter overrides (if any) before
        # any downstream logic reads self.<attr>. Safe no-op when the
        # config has no regime_overrides block for this strategy.
        try:
            self._apply_regime_overrides(getattr(self.state, 'market_regime', None))
        except Exception as _ra_err:
            logger.debug(f"[HeikinAshiStrategy] regime-override apply failed: {_ra_err}")

        # Use exchange_adapter if provided to override self.exchange
        exchange_adapter = kwargs.get('exchange_adapter')
        if exchange_adapter is not None:
            self.exchange = exchange_adapter
        
        # Extract pair from kwargs first, then args, then try to get from market_data if available
        pair = kwargs.get('pair')
        if pair is None and args:
            pair = args[0]
        if pair is None and hasattr(market_data, 'name'):
            pair = market_data.name
        timeframe = kwargs.get('timeframe', self.primary_timeframe)
        min_candles = kwargs.get('min_candles', self.min_candles)
        logger.info(f"[HeikinAshiStrategy] generate_signal called for pair={pair}, timeframe={timeframe}")
        if pair is None:
            logger.error(f"[HeikinAshiStrategy] generate_signal early return: pair is None. args={args}, kwargs={kwargs}")
            return 'hold', 0.0, 0.0
        if market_data is None or len(market_data) == 0:
            logger.error(f"[HeikinAshiStrategy] generate_signal early return: market_data is None or empty for pair={pair}")
            return 'hold', 0.0, 0.0
        try:
            # Pass market_data to analyze_pair if it's a dict with timeframes
            config_timeframes = self.config.get('heikin_ashi', {}).get('target_timeframes', ['1h', '15m'])
            if isinstance(market_data, dict) and any(tf in market_data for tf in config_timeframes):
                results = await self.analyze_pair(pair, min_candles, market_data=market_data)
            else:
                results = await self.analyze_pair(pair, min_candles)
        except Exception as e:
            logger.error(f"[HeikinAshiStrategy] Exception in analyze_pair: {e}")
            return 'hold', 0.0, 0.0
            
        # Use the multi-timeframe confluent result if available
        if 'final' in results:
            signal, confidence, indicators = results['final']
        elif self.execution_timeframe in results:
            # Fallback to execution timeframe if no confluence result
            signal, confidence, indicators = results[self.execution_timeframe]
        elif results:
            # Use the first available timeframe as last resort
            first_tf = list(results.keys())[0]
            signal, confidence, indicators = results[first_tf]
        else:
            return 'hold', 0.0, 0.0
            
        strength = confidence  # For now, use confidence as strength
        
        # Build detailed reason with concrete indicator values so post-mortem analysis
        # can identify what triggered each entry (previously every trade stored the
        # placeholder "Queue-based heikin_ashi strategy signal").
        reason_parts = []
        if indicators and isinstance(indicators, dict):
            if indicators.get('confluence_score', 0) >= self.min_confluence_score:
                reason_parts.append(f'confluence={indicators["confluence_score"]:.2f}')
            if indicators.get('divergence_boost', False):
                reason_parts.append('rsi_div')
            if indicators.get('near_support', False):
                reason_parts.append('near_support')
            if 'adx' in indicators:
                reason_parts.append(f'adx={indicators["adx"]:.1f}')
            if 'rsi' in indicators:
                reason_parts.append(f'rsi={indicators["rsi"]:.1f}')
            if 'atr' in indicators:
                reason_parts.append(f'atr={indicators["atr"]:.4f}')
            if 'trend_strength' in indicators:
                reason_parts.append(f'trend={indicators["trend_strength"]:+.3f}')
            if 'signal_strength' in indicators:
                reason_parts.append(f'strength={indicators["signal_strength"]:.2f}')
            if indicators.get('ha_bullish_continuation'):
                ha_p = indicators.get('ha_pattern') or {}
                reason_parts.append(f'ha_green_streak={ha_p.get("green_streak", 0)}')
            if indicators.get('macd_bullish'):
                reason_parts.append('macd_bull')

        reason_str = 'heikin_ashi:' + '|'.join(reason_parts) if reason_parts else 'heikin_ashi:insufficient_confluence'
        # Stash the reason on the indicators dict so the orchestrator can persist it.
        if isinstance(indicators, dict):
            indicators['entry_reason'] = reason_str

        return signal, confidence, strength

    async def calculate_position_size(self, signal_type: str, current_price: float = None, current_atr: float = None, signal_strength: float = 1.0) -> float:
        """Calculate position size based on advanced ATR risk management and signal strength."""
        import logging
        logger = logging.getLogger(__name__)
        try:
            # Get account balance
            balance = await self.exchange.get_balance()
            available_balance = balance.get('free', 0.0)
            
            # Get current price if not provided
            if current_price is None:
                ticker = await self.exchange.get_ticker(self.state.pair, self.exchange_name)
                if not ticker or 'last' not in ticker:
                    logger.warning(f"[HeikinAshiStrategy] Could not fetch ticker for {self.state.pair} on {self.exchange_name}")
                    return 0.0
                current_price = ticker['last']
            
            # Base risk per trade (can be adjusted based on signal strength)
            base_risk = self.risk_per_trade
            
            # Adjust risk based on signal strength and advanced features
            if self.enable_rsi_divergence and self.enable_support_resistance:
                # Phase 3: More confident with advanced features
                risk_adjustment = min(1.5, 1.0 + (signal_strength - 0.5))  # Up to 50% increase
            else:
                # Phase 2: Moderate adjustment
                risk_adjustment = min(1.3, 1.0 + (signal_strength - 0.7))  # Up to 30% increase
            
            adjusted_risk = base_risk * risk_adjustment
            
            # Calculate ATR-based position size if ATR is available
            if current_atr is not None:
                # Calculate stop loss distance
                stop_distance = current_atr * self.atr_stop_multiplier
                
                # Calculate risk amount
                risk_amount = available_balance * adjusted_risk
                
                # Position size = Risk Amount / Stop Distance
                position_size = risk_amount / stop_distance
                
                logger.info(f"[HeikinAshiStrategy] Advanced ATR position sizing: signal_strength={signal_strength:.2f}, risk_adj={risk_adjustment:.2f}, risk=${risk_amount:.2f}, stop_distance={stop_distance:.6f}, size={position_size:.6f}")
            else:
                # Fallback to simple percentage-based sizing
                risk_amount = available_balance * adjusted_risk
                position_size = risk_amount / current_price
                
                logger.info(f"[HeikinAshiStrategy] Simple position sizing: signal_strength={signal_strength:.2f}, risk=${risk_amount:.2f}, price=${current_price:.6f}, size={position_size:.6f}")
            
            return position_size
            
        except Exception as e:
            logger.error(f"Error calculating position size: {str(e)}")
            return 0.0

    @staticmethod
    def compute_heikin_ashi(df: pd.DataFrame) -> pd.DataFrame:
        """
        Compute canonical Heikin-Ashi candles from a raw OHLC dataframe.

        HA_Close = (Open + High + Low + Close) / 4
        HA_Open  = (Prev HA_Open + Prev HA_Close) / 2  (seed = (open[0] + close[0]) / 2)
        HA_High  = max(High, HA_Open, HA_Close)
        HA_Low   = min(Low,  HA_Open, HA_Close)

        Returns a new DataFrame with columns: open, high, low, close, volume
        (the volume column is copied verbatim from the source). The original
        `df` is NOT mutated. Returns an empty frame if input is empty.
        """
        if df is None or len(df) == 0:
            return pd.DataFrame(columns=['open', 'high', 'low', 'close', 'volume'])

        o = df['open'].to_numpy(dtype=float)
        h = df['high'].to_numpy(dtype=float)
        l = df['low'].to_numpy(dtype=float)
        c = df['close'].to_numpy(dtype=float)
        n = len(df)

        ha_close = (o + h + l + c) / 4.0
        ha_open = np.empty(n, dtype=float)
        ha_open[0] = (o[0] + c[0]) / 2.0
        for i in range(1, n):
            ha_open[i] = (ha_open[i - 1] + ha_close[i - 1]) / 2.0

        ha_high = np.maximum.reduce([h, ha_open, ha_close])
        ha_low = np.minimum.reduce([l, ha_open, ha_close])

        ha = pd.DataFrame(
            {
                'open': ha_open,
                'high': ha_high,
                'low': ha_low,
                'close': ha_close,
                'volume': df['volume'].to_numpy() if 'volume' in df.columns else np.zeros(n),
            },
            index=df.index,
        )
        return ha

    def detect_ha_pattern(self, ha_df: pd.DataFrame) -> Dict[str, Any]:
        """
        Detect textbook Heikin-Ashi continuation / reversal patterns on the
        most recent candles.

        Returns a dict with:
          green_streak       : int  — consecutive green HA candles ending at last bar
          red_streak         : int  — consecutive red HA candles ending at last bar
          last_color         : 'green' | 'red' | 'doji'
          last_no_lower_wick : bool — last candle has effectively no lower wick
          last_no_upper_wick : bool — last candle has effectively no upper wick
          color_flipped      : bool — color of last candle differs from prior
          bullish_continuation : bool — green_streak >= ha_min_green_streak AND
                                        the LAST `ha_min_green_streak` candles
                                        all show no lower wick
          bearish_reversal   : bool — color flipped to red OR doji after a green run
        """
        empty = {
            'green_streak': 0, 'red_streak': 0, 'last_color': 'doji',
            'last_no_lower_wick': False, 'last_no_upper_wick': False,
            'color_flipped': False, 'bullish_continuation': False,
            'bearish_reversal': False,
        }
        if ha_df is None or len(ha_df) < max(3, self.ha_min_green_streak + 1):
            return empty

        ha_open = ha_df['open']
        ha_close = ha_df['close']
        ha_high = ha_df['high']
        ha_low = ha_df['low']

        def _color(i: int) -> str:
            body = ha_close.iloc[i] - ha_open.iloc[i]
            rng = max(ha_high.iloc[i] - ha_low.iloc[i], 1e-12)
            if abs(body) / rng < 0.1:
                return 'doji'
            return 'green' if body > 0 else 'red'

        green_streak = 0
        for i in range(len(ha_df) - 1, -1, -1):
            if _color(i) == 'green':
                green_streak += 1
            else:
                break

        red_streak = 0
        for i in range(len(ha_df) - 1, -1, -1):
            if _color(i) == 'red':
                red_streak += 1
            else:
                break

        last_color = _color(len(ha_df) - 1)
        prev_color = _color(len(ha_df) - 2) if len(ha_df) >= 2 else last_color
        color_flipped = last_color != prev_color and 'doji' not in (last_color, prev_color)

        tol = self.ha_no_lower_wick_tolerance
        last_o = float(ha_open.iloc[-1])
        last_l = float(ha_low.iloc[-1])
        last_h = float(ha_high.iloc[-1])
        last_c = float(ha_close.iloc[-1])
        ref = max(abs(last_o), 1e-12)
        last_no_lower_wick = (last_o - last_l) / ref <= tol
        last_no_upper_wick = (last_h - last_c) / ref <= tol

        bullish_continuation = False
        if green_streak >= self.ha_min_green_streak:
            no_wick_ok = True
            for j in range(self.ha_min_green_streak):
                idx = len(ha_df) - 1 - j
                ref_j = max(abs(ha_open.iloc[idx]), 1e-12)
                if (ha_open.iloc[idx] - ha_low.iloc[idx]) / ref_j > tol:
                    no_wick_ok = False
                    break
            bullish_continuation = no_wick_ok

        bearish_reversal = (
            (last_color in ('red', 'doji')) and prev_color == 'green'
        )

        return {
            'green_streak': green_streak,
            'red_streak': red_streak,
            'last_color': last_color,
            'last_no_lower_wick': last_no_lower_wick,
            'last_no_upper_wick': last_no_upper_wick,
            'color_flipped': color_flipped,
            'bullish_continuation': bullish_continuation,
            'bearish_reversal': bearish_reversal,
        }

    def compute_macd_state(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        Compute MACD on raw close and return a small state dict.

        Returns:
          macd       : float — last MACD line value
          signal     : float — last MACD signal line value
          hist       : float — last MACD histogram value
          hist_prev  : float — previous MACD histogram value
          bullish    : bool  — macd > signal AND hist rising (hist > hist_prev)
          bearish    : bool  — macd < signal AND hist falling (hist < hist_prev)
          bullish_cross : bool — macd crossed up through signal on last bar
          bearish_cross : bool — macd crossed down through signal on last bar
        """
        empty = {
            'macd': float('nan'), 'signal': float('nan'), 'hist': float('nan'),
            'hist_prev': float('nan'), 'bullish': False, 'bearish': False,
            'bullish_cross': False, 'bearish_cross': False,
        }
        if df is None or len(df) < max(self.macd_slow + self.macd_signal + 2, 35):
            return empty
        try:
            macd_df = ta.macd(df['close'], fast=self.macd_fast,
                              slow=self.macd_slow, signal=self.macd_signal)
            if macd_df is None or macd_df.empty:
                return empty
            macd_col = f'MACD_{self.macd_fast}_{self.macd_slow}_{self.macd_signal}'
            sig_col = f'MACDs_{self.macd_fast}_{self.macd_slow}_{self.macd_signal}'
            hist_col = f'MACDh_{self.macd_fast}_{self.macd_slow}_{self.macd_signal}'
            macd_line = macd_df[macd_col]
            sig_line = macd_df[sig_col]
            hist_line = macd_df[hist_col]
            macd_now = float(macd_line.iloc[-1])
            macd_prev = float(macd_line.iloc[-2])
            sig_now = float(sig_line.iloc[-1])
            sig_prev = float(sig_line.iloc[-2])
            hist_now = float(hist_line.iloc[-1])
            hist_prev = float(hist_line.iloc[-2])
            bullish = macd_now > sig_now and hist_now > hist_prev
            bearish = macd_now < sig_now and hist_now < hist_prev
            bullish_cross = (macd_prev <= sig_prev) and (macd_now > sig_now)
            bearish_cross = (macd_prev >= sig_prev) and (macd_now < sig_now)
            return {
                'macd': macd_now, 'signal': sig_now, 'hist': hist_now,
                'hist_prev': hist_prev, 'bullish': bullish, 'bearish': bearish,
                'bullish_cross': bullish_cross, 'bearish_cross': bearish_cross,
            }
        except Exception as e:
            logger.debug(f"[HeikinAshiStrategy] MACD computation failed: {e}")
            return empty

    async def detect_rsi_divergence(self, df: pd.DataFrame, rsi_series: pd.Series) -> Dict[str, bool]:
        """
        Detect RSI divergences for enhanced signal quality
        Returns: {'bullish_divergence', 'bearish_divergence', 'hidden_bullish', 'hidden_bearish'}
        """
        try:
            if len(df) < self.divergence_lookback + 10:
                return {'bullish_divergence': False, 'bearish_divergence': False, 'hidden_bullish': False, 'hidden_bearish': False}
            
            # Get recent data for analysis
            recent_prices = df['close'].tail(self.divergence_lookback)
            recent_rsi = rsi_series.tail(self.divergence_lookback)
            
            # Find peaks and troughs in price and RSI
            price_peaks = []
            price_troughs = []
            rsi_peaks = []
            rsi_troughs = []
            
            for i in range(2, len(recent_prices) - 2):
                # Price peaks and troughs
                if (recent_prices.iloc[i] > recent_prices.iloc[i-1] and 
                    recent_prices.iloc[i] > recent_prices.iloc[i+1] and
                    recent_prices.iloc[i] > recent_prices.iloc[i-2] and 
                    recent_prices.iloc[i] > recent_prices.iloc[i+2]):
                    price_peaks.append((i, recent_prices.iloc[i]))
                    
                if (recent_prices.iloc[i] < recent_prices.iloc[i-1] and 
                    recent_prices.iloc[i] < recent_prices.iloc[i+1] and
                    recent_prices.iloc[i] < recent_prices.iloc[i-2] and 
                    recent_prices.iloc[i] < recent_prices.iloc[i+2]):
                    price_troughs.append((i, recent_prices.iloc[i]))
                
                # RSI peaks and troughs
                if (recent_rsi.iloc[i] > recent_rsi.iloc[i-1] and 
                    recent_rsi.iloc[i] > recent_rsi.iloc[i+1] and
                    recent_rsi.iloc[i] > recent_rsi.iloc[i-2] and 
                    recent_rsi.iloc[i] > recent_rsi.iloc[i+2]):
                    rsi_peaks.append((i, recent_rsi.iloc[i]))
                    
                if (recent_rsi.iloc[i] < recent_rsi.iloc[i-1] and 
                    recent_rsi.iloc[i] < recent_rsi.iloc[i+1] and
                    recent_rsi.iloc[i] < recent_rsi.iloc[i-2] and 
                    recent_rsi.iloc[i] < recent_rsi.iloc[i+2]):
                    rsi_troughs.append((i, recent_rsi.iloc[i]))
            
            divergences = {
                'bullish_divergence': False,
                'bearish_divergence': False, 
                'hidden_bullish': False,
                'hidden_bearish': False
            }
            
            # Classic Bullish Divergence: Lower price lows with higher RSI lows
            if len(price_troughs) >= 2 and len(rsi_troughs) >= 2:
                last_price_trough = price_troughs[-1][1]
                prev_price_trough = price_troughs[-2][1]
                last_rsi_trough = rsi_troughs[-1][1]
                prev_rsi_trough = rsi_troughs[-2][1]
                
                if last_price_trough < prev_price_trough and last_rsi_trough > prev_rsi_trough:
                    divergences['bullish_divergence'] = True
            
            # Classic Bearish Divergence: Higher price highs with lower RSI highs
            if len(price_peaks) >= 2 and len(rsi_peaks) >= 2:
                last_price_peak = price_peaks[-1][1]
                prev_price_peak = price_peaks[-2][1]
                last_rsi_peak = rsi_peaks[-1][1]
                prev_rsi_peak = rsi_peaks[-2][1]
                
                if last_price_peak > prev_price_peak and last_rsi_peak < prev_rsi_peak:
                    divergences['bearish_divergence'] = True
            
            # Hidden Bullish Divergence: Higher price lows with lower RSI lows (trend continuation)
            if len(price_troughs) >= 2 and len(rsi_troughs) >= 2:
                last_price_trough = price_troughs[-1][1]
                prev_price_trough = price_troughs[-2][1]
                last_rsi_trough = rsi_troughs[-1][1]
                prev_rsi_trough = rsi_troughs[-2][1]
                
                if last_price_trough > prev_price_trough and last_rsi_trough < prev_rsi_trough:
                    divergences['hidden_bullish'] = True
            
            # Hidden Bearish Divergence: Lower price highs with higher RSI highs (trend continuation)
            if len(price_peaks) >= 2 and len(rsi_peaks) >= 2:
                last_price_peak = price_peaks[-1][1]
                prev_price_peak = price_peaks[-2][1]
                last_rsi_peak = rsi_peaks[-1][1]
                prev_rsi_peak = rsi_peaks[-2][1]
                
                if last_price_peak < prev_price_peak and last_rsi_peak > prev_rsi_peak:
                    divergences['hidden_bearish'] = True
            
            return divergences
            
        except Exception as e:
            logger.error(f"Error detecting RSI divergence: {e}")
            return {'bullish_divergence': False, 'bearish_divergence': False, 'hidden_bullish': False, 'hidden_bearish': False}

    async def calculate_support_resistance(self, df: pd.DataFrame) -> Dict[str, float]:
        """
        Calculate dynamic support and resistance levels using moving averages and pivot points
        """
        try:
            if len(df) < self.sr_period:
                return {'support': 0.0, 'resistance': 0.0, 'pivot': 0.0}
            
            # Calculate pivot point levels
            high = df['high'].tail(self.sr_period)
            low = df['low'].tail(self.sr_period) 
            close = df['close'].tail(self.sr_period)
            
            # Classic pivot point calculation
            pivot = (high.max() + low.min() + close.iloc[-1]) / 3
            
            # Dynamic support/resistance using moving averages and recent highs/lows
            sma_20 = close.rolling(20).mean().iloc[-1]
            sma_50 = close.rolling(50).mean().iloc[-1] if len(close) >= 50 else close.mean()
            
            # Recent swing highs and lows
            recent_high = high.tail(10).max()
            recent_low = low.tail(10).min()
            
            # Determine support and resistance levels
            current_price = close.iloc[-1]
            
            if current_price > sma_20:
                # Price above MA, use MA as support
                support = max(sma_20, recent_low)
                resistance = min(recent_high, pivot * 1.02)  # Slight buffer above pivot
            else:
                # Price below MA, use MA as resistance
                support = min(recent_low, pivot * 0.98)  # Slight buffer below pivot
                resistance = min(sma_20, recent_high)
            
            return {
                'support': support,
                'resistance': resistance,
                'pivot': pivot,
                'sma_20': sma_20,
                'sma_50': sma_50
            }
            
        except Exception as e:
            logger.error(f"Error calculating support/resistance: {e}")
            return {'support': 0.0, 'resistance': 0.0, 'pivot': 0.0}

    async def calculate_confluence_score(self, timeframe_signals: Dict) -> Tuple[float, Dict]:
        """
        Calculate confluence score based on multi-timeframe alignment
        Returns weighted score and detailed breakdown
        """
        try:
            weights = {
                self.macro_timeframe: self.macro_weight,
                self.signal_timeframe: self.signal_weight, 
                self.execution_timeframe: self.execution_weight
            }
            
            total_weight = 0.0
            weighted_score = 0.0
            signal_breakdown = {}
            
            for timeframe, (signal, confidence, indicators) in timeframe_signals.items():
                if timeframe in weights:
                    weight = weights[timeframe]
                    signal_score = confidence if signal == 'buy' else 0.0
                    
                    weighted_score += signal_score * weight
                    total_weight += weight
                    
                    signal_breakdown[timeframe] = {
                        'signal': signal,
                        'confidence': confidence,
                        'weight': weight,
                        'contribution': signal_score * weight
                    }
            
            final_score = weighted_score / total_weight if total_weight > 0 else 0.0
            
            return final_score, signal_breakdown
            
        except Exception as e:
            logger.error(f"Error calculating confluence score: {e}")
            return 0.0, {}

    async def calculate_atr_levels(self, current_price: float, atr_value: float) -> Tuple[float, float]:
        """Calculate ATR-based stop loss and take profit levels."""
        stop_loss = current_price - (atr_value * self.atr_stop_multiplier)
        take_profit = current_price + (atr_value * self.atr_take_profit_multiplier)
        return stop_loss, take_profit
    
    async def update_dynamic_stops(self, current_price: float, current_atr: float) -> None:
        """Update stop loss and take profit based on current ATR values and advanced features."""
        import logging
        logger = logging.getLogger(__name__)
        
        try:
            if not hasattr(self.state, 'entry_price') or self.state.entry_price is None:
                return
            
            # Determine target multiplier based on enabled features
            if self.enable_rsi_divergence and self.enable_support_resistance:
                target_multiplier = self.phase3_target_rr  # Phase 3: 1:2.5
            else:
                target_multiplier = self.phase2_target_rr  # Phase 2: 1:2.0
            
            # Calculate new ATR-based levels with phase-appropriate targets
            new_stop_loss = current_price - (current_atr * self.atr_stop_multiplier)
            new_take_profit = current_price + (current_atr * self.atr_stop_multiplier * target_multiplier)
            
            # For long positions, only move stop loss up (trailing stop)
            if self.state.position == 'long':
                # Update stop loss only if it's higher than current (trailing up)
                if not hasattr(self.state, 'stop_loss') or self.state.stop_loss is None:
                    self.state.stop_loss = new_stop_loss
                    logger.info(f"[HeikinAshiStrategy] Initial ATR stop loss set: {new_stop_loss:.6f} (Phase {'3' if self.enable_rsi_divergence and self.enable_support_resistance else '2'})")
                elif new_stop_loss > self.state.stop_loss:
                    self.state.stop_loss = new_stop_loss
                    logger.info(f"[HeikinAshiStrategy] ATR trailing stop updated: {new_stop_loss:.6f}")
                
                # Set take profit if not already set (using enhanced target)
                if not hasattr(self.state, 'take_profit') or self.state.take_profit is None:
                    self.state.take_profit = new_take_profit
                    logger.info(f"[HeikinAshiStrategy] Enhanced ATR take profit set: {new_take_profit:.6f} (1:{target_multiplier} R:R)")
                    
        except Exception as e:
            logger.error(f"Error updating dynamic stops: {e}")
    
    async def should_exit(self) -> Tuple[bool, Optional[str]]:
        """Check if current position should be exited based on ATR-based stop loss, take profit, or fallback logic."""
        import logging
        logger = logging.getLogger(__name__)
        try:
            # No position, nothing to exit
            if not hasattr(self, 'state') or getattr(self.state, 'position', 'none') == 'none':
                logger.debug("[HeikinAshiStrategy] No open position, should_exit=False")
                return False, None

            # Ensure we have recent OHLCV data
            if not hasattr(self, '_current_ohlcv') or self._current_ohlcv is None:
                logger.warning("[HeikinAshiStrategy] No OHLCV data for exit check.")
                return False, None

            ohlcv = self._current_ohlcv
            current_price = ohlcv['close'].iloc[-1]
            pair = getattr(self.state, 'pair', 'N/A')
            
            # Update dynamic stops based on current ATR
            try:
                current_atr = ta.atr(ohlcv['high'], ohlcv['low'], ohlcv['close'], length=self.atr_period).iloc[-1]
                await self.update_dynamic_stops(current_price, current_atr)
            except Exception as e:
                logger.warning(f"Could not update dynamic stops: {e}")

            # 1. ATR-based Stop Loss (dynamic)
            if getattr(self.state, 'stop_loss', None) is not None and current_price <= self.state.stop_loss:
                logger.info(f"[HeikinAshiStrategy] Exiting {pair} due to ATR stop loss: {current_price:.6f} <= {self.state.stop_loss:.6f}")
                return True, 'atr_stop_loss'

            # 2. ATR-based Take Profit
            if getattr(self.state, 'take_profit', None) is not None and current_price >= self.state.take_profit:
                logger.info(f"[HeikinAshiStrategy] Exiting {pair} due to ATR take profit: {current_price:.6f} >= {self.state.take_profit:.6f}")
                return True, 'atr_take_profit'

            # 3. Advanced exit conditions with Phase 3 features
            try:
                current_rsi = ta.rsi(ohlcv['close'], length=self.rsi_period).iloc[-1]

                # CRITICAL FIX (exit-correctness): Macro-regime flip exit.
                # When fast EMA crosses below slow EMA *and* trend strength turns
                # negative, exit immediately instead of waiting for the 3% hard stop
                # to fire. Previously trades bled for 10–15 hours before stopping out.
                try:
                    ema_fast_series = ta.ema(ohlcv['close'], length=self.ema_fast_period)
                    ema_slow_series = ta.ema(ohlcv['close'], length=self.ema_slow_period)
                    if ema_fast_series is not None and ema_slow_series is not None:
                        ema_fast_now = float(ema_fast_series.iloc[-1])
                        ema_slow_now = float(ema_slow_series.iloc[-1])
                        # Recent trend slope over the configured trend_period
                        if len(ohlcv) > self.trend_period:
                            trend_now = (
                                ohlcv['close'].iloc[-1]
                                - ohlcv['close'].iloc[-self.trend_period]
                            ) / max(ohlcv['close'].iloc[-self.trend_period], 1e-12)
                        else:
                            trend_now = 0.0
                        regime_bearish = (
                            ema_fast_now < ema_slow_now
                            and trend_now < -self.min_trend_strength
                        )
                        if regime_bearish:
                            logger.info(
                                "[HeikinAshiStrategy] Exiting %s due to regime flip: "
                                "ema_fast=%.6f < ema_slow=%.6f AND trend=%+.4f",
                                pair, ema_fast_now, ema_slow_now, trend_now,
                            )
                            return True, 'regime_flip_exit'
                except Exception as _e:
                    logger.debug(f"[HeikinAshiStrategy] regime-flip exit check failed for {pair}: {_e}")

                # RSI overbought exit
                if current_rsi > self.rsi_overbought:
                    logger.info(f"[HeikinAshiStrategy] Exiting {pair} due to RSI overbought: {current_rsi:.2f} > {self.rsi_overbought}")
                    return True, 'rsi_overbought_exit'

                # HA-Hybrid v1: HA color-flip + MACD bearish-cross combo exit.
                # Either signal alone is too noisy (every pullback flips HA;
                # MACD whipsaws in chop). Requiring BOTH on the execution
                # timeframe gives a cleaner early-exit than waiting for the
                # ATR stop or the existing regime-flip check. We also gate
                # the HA-only exit by an RSI floor so we don't bail when
                # the asset is still in a strong uptrend (RSI < ha_exit_rsi_floor).
                if self.ha_exit_on_color_flip:
                    try:
                        ha_df = self.compute_heikin_ashi(ohlcv)
                        ha_pattern = self.detect_ha_pattern(ha_df)
                        macd_state = self.compute_macd_state(ohlcv) if self.enable_macd_confirmation else None

                        ha_flipped_bearish = ha_pattern.get('bearish_reversal', False)
                        macd_bearish_cross = bool(macd_state and macd_state.get('bearish_cross'))

                        if ha_flipped_bearish and macd_bearish_cross:
                            logger.info(
                                "[HeikinAshiStrategy] Exiting %s on HA bearish flip + MACD bearish cross "
                                "(last_color=%s, prev_streak_green=%d, macd=%.6f, signal=%.6f)",
                                pair, ha_pattern.get('last_color'), ha_pattern.get('green_streak', 0),
                                macd_state.get('macd', float('nan')) if macd_state else float('nan'),
                                macd_state.get('signal', float('nan')) if macd_state else float('nan'),
                            )
                            return True, 'ha_macd_bearish_exit'

                        if ha_flipped_bearish and current_rsi >= self.ha_exit_rsi_floor:
                            logger.info(
                                "[HeikinAshiStrategy] Exiting %s on HA color flip (rsi=%.2f >= floor=%.0f, "
                                "last_color=%s)",
                                pair, current_rsi, self.ha_exit_rsi_floor, ha_pattern.get('last_color'),
                            )
                            return True, 'ha_color_flip_exit'
                    except Exception as _ha_exit_err:
                        logger.debug(
                            f"[HeikinAshiStrategy] HA/MACD exit check failed for {pair}: {_ha_exit_err}"
                        )

                # Phase 3: Bearish divergence exit signal
                if self.enable_rsi_divergence:
                    divergences = await self.detect_rsi_divergence(ohlcv, ta.rsi(ohlcv['close'], length=self.rsi_period))
                    if divergences.get('bearish_divergence', False) or divergences.get('hidden_bearish', False):
                        logger.info(f"[HeikinAshiStrategy] Exiting {pair} due to bearish RSI divergence")
                        return True, 'bearish_divergence_exit'
                
                # Phase 3: Resistance level exit
                if self.enable_support_resistance:
                    sr_levels = await self.calculate_support_resistance(ohlcv)
                    if current_price >= sr_levels['resistance'] * 0.99:  # Within 1% of resistance
                        logger.info(f"[HeikinAshiStrategy] Exiting {pair} near resistance: ${current_price:.6f} >= ${sr_levels['resistance']:.6f}")
                        return True, 'resistance_exit'
                        
            except Exception as e:
                logger.warning(f"Could not check advanced exit conditions: {e}")

            # 4. Fallback: time-based exit (configurable max hold time)
            if getattr(self.state, 'entry_time', None):
                from datetime import datetime
                time_in_trade = datetime.utcnow() - self.state.entry_time
                max_hold_seconds = self.max_hold_time_hours * 3600  # Convert hours to seconds
                if time_in_trade.total_seconds() > max_hold_seconds:
                    logger.info(f"[HeikinAshiStrategy] Exiting {pair} due to max hold time exceeded: {time_in_trade} > {self.max_hold_time_hours}h")
                    return True, 'max_hold_time'

            logger.debug(f"[HeikinAshiStrategy] No exit condition met for {pair}.")
            return False, None
        except Exception as e:
            logger.error(f"Error in should_exit: {e}")
            return False, None 