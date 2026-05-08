"""
Market Regime Detector for Strategy Selection
Analyzes market conditions to determine which trading strategies are most appropriate

VERSION: 2.0.0 (2025-08-24)
CHANGELOG:
- v2.0.0: CRITICAL CRYPTO MARKET OPTIMIZATION UPDATE
  * FIXED: Division by zero error in Bollinger Band position calculation
  * IMPROVED: Updated all volatility thresholds for 2025 cryptocurrency markets
  * ENHANCED: Recalibrated ADX thresholds (20/35 instead of 25/40) for crypto trends
  * OPTIMIZED: RSI extreme levels (25/75 instead of 30/70) for crypto momentum
  * REFINED: Volume analysis periods (10 vs 5) for crypto intraday volatility
  * TUNED: Price range analysis (15 vs 20 periods) for faster regime detection
  * RESOLVED: Contradictory regime classifications causing strategy selection errors
"""

import pandas as pd
import numpy as np
import pandas_ta as ta
import logging
from typing import Dict, List, Tuple, Optional
from enum import Enum

logger = logging.getLogger(__name__)

# Version tracking
MARKET_REGIME_DETECTOR_VERSION = "2.0.0"
VERSION_DATE = "2025-08-24"

class MarketRegime(Enum):
    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down" 
    SIDEWAYS = "sideways"
    REVERSAL_ZONE = "reversal_zone"
    BREAKOUT = "breakout"
    HIGH_VOLATILITY = "high_volatility"
    LOW_VOLATILITY = "low_volatility"

class MarketRegimeDetector:
    """
    Detects market regime based on multiple technical indicators
    to enable intelligent strategy selection
    """
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
        
        # Log version information on initialization
        self.logger.info(f"🔄 Market Regime Detector v{MARKET_REGIME_DETECTOR_VERSION} ({VERSION_DATE}) - Crypto-Optimized")
        
        # Thresholds for regime detection - UPDATED FOR 2025 CRYPTO MARKETS
        self.adx_trend_threshold = 20      # ADX > 20 indicates trending (crypto-optimized)
        self.adx_strong_trend = 35         # ADX > 35 indicates strong trend (crypto-optimized)
        self.adx_sideways_threshold = 15   # ADX < 15 indicates sideways/ranging
        self.rsi_overbought = 75           # RSI > 75 overbought (crypto sustains momentum longer)
        self.rsi_oversold = 25             # RSI < 25 oversold (crypto sustains momentum longer)
        self.bb_squeeze_threshold = 0.012  # 1.2% Bollinger Band squeeze (crypto-optimized)
        self.volume_spike_multiplier = 2.5 # Volume spike for crypto's explosive moves
        self.price_range_sideways = 0.03   # 3% price range for sideways market (tighter for crypto)
        # Require trend persistence over recent candles so one-bar flips don't
        # immediately classify as trending_up/down.
        self.trend_lookback_bars = 4
        self.trend_min_aligned_bars = 3
        self.require_latest_trend_alignment = True
        self.require_consecutive_trend_bars = False

    def configure(self, cfg: Dict[str, any]) -> None:
        """Allow strategy-service to tune detector behavior from config."""
        if not isinstance(cfg, dict):
            return
        self.trend_lookback_bars = max(3, min(6, int(cfg.get("trend_lookback_bars", self.trend_lookback_bars) or self.trend_lookback_bars)))
        self.trend_min_aligned_bars = max(2, min(self.trend_lookback_bars, int(cfg.get("trend_min_aligned_bars", self.trend_min_aligned_bars) or self.trend_min_aligned_bars)))
        self.require_latest_trend_alignment = bool(cfg.get("require_latest_trend_alignment", self.require_latest_trend_alignment))
        self.require_consecutive_trend_bars = bool(cfg.get("require_consecutive_trend_bars", self.require_consecutive_trend_bars))

    @staticmethod
    def _tail_true_count(mask: pd.Series, bars: int) -> int:
        if mask is None or getattr(mask, "empty", True):
            return 0
        tail = mask.fillna(False).astype(bool).tail(max(1, bars)).tolist()
        return sum(1 for v in tail if v)

    @staticmethod
    def _tail_true_streak(mask: pd.Series, bars: int) -> int:
        if mask is None or getattr(mask, "empty", True):
            return 0
        streak = 0
        for v in reversed(mask.fillna(False).astype(bool).tail(max(1, bars)).tolist()):
            if v:
                streak += 1
            else:
                break
        return streak
        
    def detect_regime(self, ohlcv: pd.DataFrame, pair: str = "Unknown") -> Tuple[MarketRegime, Dict[str, any]]:
        """
        Detect current market regime based on technical analysis
        
        Args:
            ohlcv: OHLCV DataFrame with at least 50 periods
            pair: Trading pair name for logging
            
        Returns:
            Tuple of (MarketRegime, analysis_details)
        """
        try:
            if len(ohlcv) < 50:
                self.logger.warning(f"Insufficient data for regime detection: {len(ohlcv)} candles")
                return MarketRegime.LOW_VOLATILITY, {"reason": "insufficient_data"}
            
            # Calculate technical indicators
            indicators = self._calculate_indicators(ohlcv)
            
            # Analyze each regime condition
            analysis = {
                "pair": pair,
                "indicators": indicators,
                "regime_scores": {},
                "conditions": {}
            }
            
            # 1. Trend Analysis (ADX + EMAs)
            trend_analysis = self._analyze_trend(indicators, ohlcv, analysis)
            
            # 2. Reversal Zone Analysis (RSI + Price levels)
            reversal_analysis = self._analyze_reversal_conditions(indicators, ohlcv, analysis)
            
            # 3. Sideways Market Analysis (Price range + ADX)
            sideways_analysis = self._analyze_sideways_market(indicators, ohlcv, analysis)
            
            # 4. Breakout Analysis (Volume + Bollinger Bands)
            breakout_analysis = self._analyze_breakout_conditions(indicators, ohlcv, analysis)
            
            # 5. Volatility Analysis (ATR + Bollinger Band width)
            volatility_analysis = self._analyze_volatility(indicators, analysis)
            
            # Determine primary regime
            regime = self._determine_primary_regime(analysis)
            regime_rankings = self.get_regime_rankings(analysis)
            if regime_rankings:
                analysis["regime_rankings"] = regime_rankings
                analysis["regime_score"] = float(regime_rankings[0]["score"])
                if len(regime_rankings) > 1:
                    analysis["runner_up_regime"] = regime_rankings[1]["regime"]
                    analysis["runner_up_score"] = float(regime_rankings[1]["score"])
                else:
                    analysis["runner_up_regime"] = None
                    analysis["runner_up_score"] = 0.0
            else:
                analysis["regime_rankings"] = []
                analysis["regime_score"] = 0.0
                analysis["runner_up_regime"] = None
                analysis["runner_up_score"] = 0.0
            
            self.logger.info(f"[MARKET REGIME] {pair}: {regime.value} | "
                           f"ADX: {indicators['adx']:.1f} | "
                           f"RSI: {indicators['rsi']:.1f} | "
                           f"Vol Ratio: {indicators['volume_ratio']:.2f}")
            
            # Store analysis for strategy filtering
            self._last_analysis = analysis
            
            return regime, analysis
            
        except Exception as e:
            self.logger.error(f"Error detecting market regime for {pair}: {e}")
            return MarketRegime.LOW_VOLATILITY, {"error": str(e)}
    
    def _calculate_indicators(self, ohlcv: pd.DataFrame) -> Dict[str, float]:
        """Calculate all required technical indicators"""
        indicators = {}
        
        # Trend indicators
        adx_result = ta.adx(ohlcv['high'], ohlcv['low'], ohlcv['close'], length=14)
        indicators['adx'] = adx_result['ADX_14'].iloc[-1] if adx_result is not None else 0
        
        # EMAs for trend direction
        ema_fast = ta.ema(ohlcv['close'], length=9).iloc[-1]
        ema_slow = ta.ema(ohlcv['close'], length=21).iloc[-1]
        indicators['ema_fast'] = ema_fast
        indicators['ema_slow'] = ema_slow
        indicators['ema_trend'] = "up" if ema_fast > ema_slow else "down"
        
        # RSI for overbought/oversold
        indicators['rsi'] = ta.rsi(ohlcv['close'], length=14).iloc[-1]
        
        # Bollinger Bands for volatility and squeeze (column names vary by pandas-ta version)
        bb = ta.bbands(ohlcv["close"], length=20, std=2)

        def _bb_last(bands_df, prefix: str) -> float:
            if bands_df is None or bands_df.empty:
                return float("nan")
            for col in bands_df.columns:
                if str(col).upper().startswith(prefix.upper()):
                    v = bands_df[col].iloc[-1]
                    return float(v) if pd.notna(v) else float("nan")
            return float("nan")

        bb_upper = _bb_last(bb, "BBU")
        bb_lower = _bb_last(bb, "BBL")
        bb_middle = _bb_last(bb, "BBM")
        if not np.isfinite(bb_middle) or bb_middle == 0:
            bb_middle = float(ohlcv["close"].iloc[-1])
        if not np.isfinite(bb_upper):
            bb_upper = bb_middle
        if not np.isfinite(bb_lower):
            bb_lower = bb_middle
        indicators['bb_width'] = (bb_upper - bb_lower) / bb_middle
        
        # CRITICAL FIX: Prevent division by zero when Bollinger Bands collapse
        bb_range = bb_upper - bb_lower
        if bb_range > 0 and not np.isnan(bb_range):
            indicators['bb_position'] = (ohlcv['close'].iloc[-1] - bb_lower) / bb_range
        else:
            indicators['bb_position'] = 0.5  # Neutral position when bands collapse
        
        # ATR for volatility
        indicators['atr'] = ta.atr(ohlcv['high'], ohlcv['low'], ohlcv['close'], length=14).iloc[-1]
        indicators['atr_pct'] = indicators['atr'] / ohlcv['close'].iloc[-1]
        
        # Volume analysis - IMPROVED FOR CRYPTO INTRADAY VOLATILITY
        current_volume = ohlcv['volume'].tail(10).mean()  # Extended from 5 to 10 periods
        avg_volume = ohlcv['volume'].tail(50).mean()
        indicators['volume_ratio'] = current_volume / avg_volume if avg_volume > 0 else 1.0
        
        # Price range analysis - OPTIMIZED FOR CRYPTO REGIME CHANGES
        recent_high = ohlcv['high'].tail(15).max()  # Reduced from 20 to 15 for faster regime detection
        recent_low = ohlcv['low'].tail(15).min()
        current_price = ohlcv['close'].iloc[-1]
        indicators['price_range_pct'] = (recent_high - recent_low) / current_price
        
        # Calculate trend strength for strategy filtering
        if indicators['adx'] > 0:
            # Trend strength based on ADX and EMA separation
            ema_separation = abs(indicators['ema_fast'] - indicators['ema_slow']) / indicators['ema_slow']
            indicators['trend_strength'] = (indicators['adx'] / 100.0) * ema_separation
        else:
            indicators['trend_strength'] = 0.0
        
        return indicators
    
    def _analyze_trend(self, indicators: Dict, ohlcv: pd.DataFrame, analysis: Dict) -> Dict:
        """Analyze trending conditions.

        PnL-FIX v4: Previous code required strong_adx (>35) AND ema_separation>2%
        together. On 15m crypto charts the 9/21 EMAs often run <1% apart even
        when ADX is 80+, so many obviously-trending pairs never scored and fell
        through to ``sideways`` or ``low_volatility``. We now:

          * Accept a moderate-trend threshold (ADX > 20) with only 0.3% EMA
            separation → partial score (0.6+) so more pairs reach the trending
            regimes that actually favour our trend strategies.
          * Keep the original strong-trend bonus on top.
        """
        conditions = {}

        adx = indicators['adx']
        strong_trend = adx > self.adx_strong_trend          # >35
        moderate_trend = adx > self.adx_trend_threshold     # >20
        trend_direction = indicators['ema_trend']
        ema_separation = abs(indicators['ema_fast'] - indicators['ema_slow']) / indicators['ema_slow']
        adx_series = ta.adx(ohlcv['high'], ohlcv['low'], ohlcv['close'], length=14)
        adx_line = adx_series['ADX_14'] if adx_series is not None and 'ADX_14' in adx_series else pd.Series(dtype=float)
        ema_fast_series = ta.ema(ohlcv['close'], length=9)
        ema_slow_series = ta.ema(ohlcv['close'], length=21)
        up_align_mask = (ema_fast_series > ema_slow_series) & (adx_line > self.adx_trend_threshold)
        down_align_mask = (ema_fast_series < ema_slow_series) & (adx_line > self.adx_trend_threshold)
        lookback = max(1, min(self.trend_lookback_bars, len(ohlcv)))
        up_aligned_bars = self._tail_true_count(up_align_mask, lookback)
        down_aligned_bars = self._tail_true_count(down_align_mask, lookback)
        up_streak = self._tail_true_streak(up_align_mask, lookback)
        down_streak = self._tail_true_streak(down_align_mask, lookback)
        latest_up_aligned = bool(up_align_mask.fillna(False).iloc[-1]) if len(up_align_mask) else False
        latest_down_aligned = bool(down_align_mask.fillna(False).iloc[-1]) if len(down_align_mask) else False

        conditions['strong_adx'] = strong_trend
        conditions['moderate_adx'] = moderate_trend
        conditions['trend_direction'] = trend_direction
        conditions['ema_separation_wide'] = ema_separation > 0.02    # 2%+
        conditions['ema_separation_mild'] = ema_separation > 0.003   # 0.3%+
        conditions['ema_separation_value'] = ema_separation
        conditions['trend_lookback_bars'] = lookback
        conditions['trend_min_aligned_bars'] = self.trend_min_aligned_bars
        conditions['up_aligned_bars'] = up_aligned_bars
        conditions['down_aligned_bars'] = down_aligned_bars
        conditions['up_alignment_streak'] = up_streak
        conditions['down_alignment_streak'] = down_streak
        conditions['latest_up_aligned'] = latest_up_aligned
        conditions['latest_down_aligned'] = latest_down_aligned

        def _persistence_ok(direction: str) -> bool:
            aligned = up_aligned_bars if direction == "up" else down_aligned_bars
            latest = latest_up_aligned if direction == "up" else latest_down_aligned
            streak = up_streak if direction == "up" else down_streak
            count_ok = aligned >= self.trend_min_aligned_bars
            latest_ok = (not self.require_latest_trend_alignment) or latest
            streak_ok = (not self.require_consecutive_trend_bars) or (streak >= self.trend_min_aligned_bars)
            return count_ok and latest_ok and streak_ok

        def _score_trending(regime_key: str):
            if strong_trend and conditions['ema_separation_wide']:
                # Strong trend + wide EMA fan → very high confidence
                analysis['regime_scores'][regime_key] = min(
                    0.95, 0.80 + (adx - 35) * 0.005
                )
            elif strong_trend and conditions['ema_separation_mild']:
                # Strong ADX but tight EMAs (common on 15m crypto)
                analysis['regime_scores'][regime_key] = min(
                    0.90, 0.70 + (adx - 35) * 0.005
                )
            elif moderate_trend and conditions['ema_separation_mild']:
                # Moderate trend — enough to prefer trend strategies
                analysis['regime_scores'][regime_key] = 0.55 + (adx - 20) * 0.005

        if trend_direction == "up" and _persistence_ok("up"):
            _score_trending('trending_up')
        elif trend_direction == "down" and _persistence_ok("down"):
            _score_trending('trending_down')

        analysis['conditions']['trend'] = conditions
        return conditions
    
    def _analyze_reversal_conditions(self, indicators: Dict, ohlcv: pd.DataFrame, analysis: Dict) -> Dict:
        """Analyze reversal zone conditions"""
        conditions = {}
        
        # RSI extreme conditions
        rsi_oversold = indicators['rsi'] < self.rsi_oversold
        rsi_overbought = indicators['rsi'] > self.rsi_overbought
        
        # Price at support/resistance (Bollinger Bands)
        at_bb_lower = indicators['bb_position'] < 0.1  # Near lower band
        at_bb_upper = indicators['bb_position'] > 0.9  # Near upper band
        
        # Trend exhaustion (high ADX but RSI extreme)
        trend_exhaustion = indicators['adx'] > 30 and (rsi_oversold or rsi_overbought)
        
        conditions['rsi_oversold'] = rsi_oversold
        conditions['rsi_overbought'] = rsi_overbought
        conditions['at_support'] = at_bb_lower
        conditions['at_resistance'] = at_bb_upper
        conditions['trend_exhaustion'] = trend_exhaustion
        
        # Calculate reversal score
        reversal_score = 0
        if trend_exhaustion:
            reversal_score += 0.4
        if (rsi_oversold and at_bb_lower) or (rsi_overbought and at_bb_upper):
            reversal_score += 0.5
        
        if reversal_score > 0.3:
            analysis['regime_scores']['reversal_zone'] = reversal_score
        
        analysis['conditions']['reversal'] = conditions
        return conditions
    
    def _analyze_sideways_market(self, indicators: Dict, ohlcv: pd.DataFrame, analysis: Dict) -> Dict:
        """Analyze sideways/ranging market conditions"""
        conditions = {}
        
        # Low ADX indicates lack of trend
        low_adx = indicators['adx'] < self.adx_trend_threshold
        
        # Narrow price range
        narrow_range = indicators['price_range_pct'] < self.price_range_sideways
        
        # Price in middle of Bollinger Bands
        in_bb_middle = 0.3 < indicators['bb_position'] < 0.7
        
        # Low volatility - CRYPTO-OPTIMIZED THRESHOLD
        low_volatility = indicators['atr_pct'] < 0.015  # Tightened from 2% to 1.5%
        
        conditions['low_adx'] = low_adx
        conditions['narrow_range'] = narrow_range
        conditions['in_bb_middle'] = in_bb_middle
        conditions['low_volatility'] = low_volatility
        
        # Calculate sideways score
        sideways_score = 0
        if low_adx:
            sideways_score += 0.4
        if narrow_range:
            sideways_score += 0.3
        if in_bb_middle:
            sideways_score += 0.2
        if low_volatility:
            sideways_score += 0.1
        
        if sideways_score > 0.5:
            analysis['regime_scores']['sideways'] = sideways_score
        
        analysis['conditions']['sideways'] = conditions
        return conditions
    
    def _analyze_breakout_conditions(self, indicators: Dict, ohlcv: pd.DataFrame, analysis: Dict) -> Dict:
        """Analyze breakout conditions"""
        conditions = {}
        
        # Volume spike
        volume_spike = indicators['volume_ratio'] > self.volume_spike_multiplier
        
        # Bollinger Band squeeze (low volatility before breakout)
        bb_squeeze = indicators['bb_width'] < self.bb_squeeze_threshold
        
        # Price breaking out of Bollinger Bands
        bb_breakout = indicators['bb_position'] < 0.05 or indicators['bb_position'] > 0.95
        
        # Recent volatility expansion - CRYPTO-OPTIMIZED
        volatility_expansion = indicators['atr_pct'] > 0.025  # Reduced from 3% to 2.5%
        
        conditions['volume_spike'] = volume_spike
        conditions['bb_squeeze'] = bb_squeeze
        conditions['bb_breakout'] = bb_breakout
        conditions['volatility_expansion'] = volatility_expansion
        
        # Calculate breakout score.
        # PnL-FIX v4: Lowered trigger threshold from 0.4 → 0.35 AND let a
        # standalone volume spike qualify. The previous AND-gates meant even
        # pairs with volume_ratio 3-4× (obvious breakouts) never scored high
        # enough to leave ``low_volatility``.
        breakout_score = 0
        if volume_spike:
            breakout_score += 0.45        # was 0.4 — single spike now clears threshold
        if bb_breakout and not bb_squeeze:
            breakout_score += 0.35
        if volatility_expansion:
            breakout_score += 0.25

        if breakout_score >= 0.35:
            analysis['regime_scores']['breakout'] = min(0.95, breakout_score)

        analysis['conditions']['breakout'] = conditions
        return conditions
    
    def _analyze_volatility(self, indicators: Dict, analysis: Dict) -> Dict:
        """Analyze volatility conditions"""
        conditions = {}
        
        # UPDATED 2025 CRYPTO VOLATILITY THRESHOLDS
        # High volatility conditions
        high_atr = indicators['atr_pct'] > 0.03   # Reduced from 4% to 3% for crypto
        wide_bb = indicators['bb_width'] > 0.04   # Reduced from 5% to 4%
        
        # Low volatility conditions
        low_atr = indicators['atr_pct'] < 0.008   # Reduced to 0.8% for crypto precision
        narrow_bb = indicators['bb_width'] < 0.012  # 1.2% for crypto squeeze detection
        
        conditions['high_atr'] = high_atr
        conditions['wide_bb'] = wide_bb
        conditions['low_atr'] = low_atr
        conditions['narrow_bb'] = narrow_bb
        
        # Calculate volatility scores.
        # PnL-FIX v4: Relaxed from strict AND → either condition (with higher
        # score when both agree). Pairs with ATR/price >3% should be classified
        # as high-volatility even if Bollinger width happens to be inside the
        # 4% bucket; same in reverse for low volatility.
        if high_atr and wide_bb:
            analysis['regime_scores']['high_volatility'] = 0.75
        elif high_atr or wide_bb:
            analysis['regime_scores']['high_volatility'] = 0.55
        elif low_atr and narrow_bb:
            analysis['regime_scores']['low_volatility'] = 0.60
        elif low_atr or narrow_bb:
            analysis['regime_scores']['low_volatility'] = 0.40

        analysis['conditions']['volatility'] = conditions
        return conditions
    
    def _determine_primary_regime(self, analysis: Dict) -> MarketRegime:
        """Determine the primary market regime based on all analysis"""
        scores = analysis['regime_scores']
        
        if not scores:
            return MarketRegime.LOW_VOLATILITY
        
        # Find highest scoring regime
        primary_regime = max(scores.items(), key=lambda x: x[1])
        regime_name, score = primary_regime
        
        # Convert string to enum
        regime_map = {
            'trending_up': MarketRegime.TRENDING_UP,
            'trending_down': MarketRegime.TRENDING_DOWN,
            'sideways': MarketRegime.SIDEWAYS,
            'reversal_zone': MarketRegime.REVERSAL_ZONE,
            'breakout': MarketRegime.BREAKOUT,
            'high_volatility': MarketRegime.HIGH_VOLATILITY,
            'low_volatility': MarketRegime.LOW_VOLATILITY
        }
        
        return regime_map.get(regime_name, MarketRegime.LOW_VOLATILITY)

    def get_regime_rankings(self, analysis: Dict) -> List[Dict[str, float]]:
        """Return regime rankings sorted by confidence score."""
        scores = analysis.get("regime_scores", {}) or {}
        if not isinstance(scores, dict) or not scores:
            return [{"regime": MarketRegime.LOW_VOLATILITY.value, "score": 0.0}]
        ranked = sorted(scores.items(), key=lambda item: float(item[1]), reverse=True)
        return [{"regime": str(name), "score": float(score)} for name, score in ranked]

    def get_applicable_strategies(self, regime: MarketRegime, pair: str = None, exchange: str = None) -> List[str]:
        """
        Get list of strategies most suitable for the detected market regime
        BALANCED APPROACH: Prioritize regime-appropriate strategies while ensuring 
        trending pairs aren't limited by misclassification (addresses CRO/USD issue)
        Only returns strategies that are actually enabled and loaded
        """
        
        # ENABLED STRATEGIES - Updated to include optimized heikin_ashi
        # NOTE: heikin_ashi has been RE-ENABLED with optimized parameters for 60%+ win rate
        # PnL-FIX v4: Expand engulfing_multi_tf eligibility beyond REVERSAL_ZONE.
        # The strategy's v3 code requires a bullish 4h macro trend as a hard veto
        # (EMA9>EMA21 AND close>SMA50), so it self-filters out of bearish macros
        # even if we list it here. That means adding it to more regimes is SAFE:
        # the internal macro check prevents low-quality entries, while enlarging
        # the regime coverage gives the strategy realistic trade frequency (it
        # previously saw only ~1 pair per cycle → 0 trades in practice).
        strategy_mapping = {
            MarketRegime.TRENDING_UP: [
                "heikin_ashi",                 # PRIMARY: trend continuation
                "macd_momentum",               # SECONDARY: momentum continuation
                "vwma_hull",                   # SECONDARY: volume-weighted trend
                "engulfing_multi_tf",          # TERTIARY: PnL-FIX v4 — pullback-reversal
                                               # entries inside an uptrend
                "multi_timeframe_confluence"
            ],
            MarketRegime.TRENDING_DOWN: [
                "heikin_ashi",                 # PRIMARY: trend continuation
                "macd_momentum",               # SECONDARY: bearish momentum veto
                "vwma_hull",                   # SECONDARY: volume-weighted trend
                "multi_timeframe_confluence"   # TERTIARY: trend confirmation
                # NOTE: engulfing_multi_tf NOT listed — its 4h macro-bullish hard
                # veto would reject every signal here anyway.
            ],
            MarketRegime.SIDEWAYS: [
                "multi_timeframe_confluence",  # PRIMARY: ranging-market specialist
                "heikin_ashi",                 # SECONDARY
                "vwma_hull"                    # TERTIARY: volume in ranges
            ],
            MarketRegime.REVERSAL_ZONE: [
                "engulfing_multi_tf",          # PRIMARY: reversal-pattern specialist
                "heikin_ashi",                 # SECONDARY
                "multi_timeframe_confluence"   # TERTIARY
            ],
            MarketRegime.BREAKOUT: [
                "vwma_hull",                   # PRIMARY: volume-spike detection
                "macd_momentum",               # SECONDARY: impulse follow-through
                "engulfing_multi_tf",          # SECONDARY: PnL-FIX v4 — bullish
                                               # engulfings often initiate breakouts
                "heikin_ashi",                 # TERTIARY: momentum capture
                "multi_timeframe_confluence"
            ],
            MarketRegime.HIGH_VOLATILITY: [
                "heikin_ashi",                 # PRIMARY: noise reduction
                "macd_momentum",               # SECONDARY: fast momentum tracking
                "engulfing_multi_tf",          # SECONDARY: PnL-FIX v4 — post-flush
                                               # reversal setups (still gated by the
                                               # strategy's own macro + ADX vetos)
                "vwma_hull",                   # TERTIARY: volume-based filtering
                "multi_timeframe_confluence"
            ],
            MarketRegime.LOW_VOLATILITY: [
                "multi_timeframe_confluence",  # PRIMARY: conservative for low vol
                "heikin_ashi",                 # SECONDARY
                "vwma_hull"                    # TERTIARY: volume analysis
            ]
        }
        
        strategies = strategy_mapping.get(regime, [
            "heikin_ashi",                    # Re-enabled as primary fallback
            "macd_momentum",                  # Momentum fallback
            "multi_timeframe_confluence",     # Safe fallback
            "vwma_hull"                       # Volume-based analysis
        ])
        
        # STRATEGY VALIDATION: All strategies in mapping are currently enabled
        # heikin_ashi has been RE-ENABLED with optimized parameters targeting 60%+ win rate
        
        # PAIR-SPECIFIC OPTIMIZATIONS for USD pairs
        if pair and "/USD" in pair:
            logger.info(f"🎯 Applying USD pair strategy optimizations for {pair}")
            
            # For CRO/USD specifically, prioritize strategies that work better with USD pairs
            if pair == "CRO/USD":
                # Reorder to prioritize vwma_hull for CRO/USD - it handles lower volatility better
                if "vwma_hull" in strategies and "multi_timeframe_confluence" in strategies:
                    # Put vwma_hull first for CRO/USD
                    strategies_reordered = ["vwma_hull"]
                    strategies_reordered.extend([s for s in strategies if s != "vwma_hull"])
                    strategies = strategies_reordered
                    logger.info(f"🔄 Prioritized VWMA Hull for CRO/USD (better for USD pair characteristics)")
                    
                # Also include multi_timeframe_confluence with relaxed parameters
                if "multi_timeframe_confluence" not in strategies:
                    strategies.append("multi_timeframe_confluence")
                    logger.info(f"➕ Added Multi-Timeframe Confluence for CRO/USD with relaxed parameters")

        # Hard rule: macd_momentum must be evaluated in every regime.
        if "macd_momentum" not in strategies:
            strategies.append("macd_momentum")
        
        return strategies