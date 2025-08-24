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
            trend_analysis = self._analyze_trend(indicators, analysis)
            
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
            
            self.logger.info(f"[MARKET REGIME] {pair}: {regime.value} | "
                           f"ADX: {indicators['adx']:.1f} | "
                           f"RSI: {indicators['rsi']:.1f} | "
                           f"Vol Ratio: {indicators['volume_ratio']:.2f}")
            
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
        
        # Bollinger Bands for volatility and squeeze
        bb = ta.bbands(ohlcv['close'], length=20, std=2)
        bb_upper = bb['BBU_20_2.0'].iloc[-1]
        bb_lower = bb['BBL_20_2.0'].iloc[-1]
        bb_middle = bb['BBM_20_2.0'].iloc[-1]
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
        
        return indicators
    
    def _analyze_trend(self, indicators: Dict, analysis: Dict) -> Dict:
        """Analyze trending conditions"""
        conditions = {}
        
        # Strong trend conditions
        strong_trend = indicators['adx'] > self.adx_strong_trend
        trend_direction = indicators['ema_trend']
        ema_separation = abs(indicators['ema_fast'] - indicators['ema_slow']) / indicators['ema_slow']
        
        conditions['strong_adx'] = strong_trend
        conditions['trend_direction'] = trend_direction
        conditions['ema_separation'] = ema_separation > 0.02  # 2% separation
        
        # Calculate trend scores
        if strong_trend and trend_direction == "up" and conditions['ema_separation']:
            analysis['regime_scores']['trending_up'] = 0.8 + (indicators['adx'] - 40) * 0.005
        elif strong_trend and trend_direction == "down" and conditions['ema_separation']:
            analysis['regime_scores']['trending_down'] = 0.8 + (indicators['adx'] - 40) * 0.005
        
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
        
        # Calculate breakout score
        breakout_score = 0
        if volume_spike:
            breakout_score += 0.4
        if bb_breakout and not bb_squeeze:  # Breaking out after squeeze
            breakout_score += 0.4
        if volatility_expansion:
            breakout_score += 0.2
        
        if breakout_score > 0.4:
            analysis['regime_scores']['breakout'] = breakout_score
        
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
        
        # Calculate volatility scores
        if high_atr and wide_bb:
            analysis['regime_scores']['high_volatility'] = 0.7
        elif low_atr and narrow_bb:
            analysis['regime_scores']['low_volatility'] = 0.6
        
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

    def get_applicable_strategies(self, regime: MarketRegime) -> List[str]:
        """
        Get list of strategies most suitable for the detected market regime
        BALANCED APPROACH: Prioritize regime-appropriate strategies while ensuring 
        trending pairs aren't limited by misclassification (addresses CRO/USD issue)
        """
        
        # REGIME-OPTIMIZED STRATEGY MAPPING with expanded coverage
        strategy_mapping = {
            MarketRegime.TRENDING_UP: [
                "heikin_ashi",                 # PRIMARY: Excellent in uptrends
                "vwma_hull",                   # PRIMARY: Volume-weighted trend following
                "multi_timeframe_confluence"   # SECONDARY: Trend confirmation
            ],
            MarketRegime.TRENDING_DOWN: [
                "heikin_ashi",                 # PRIMARY: Excellent in downtrends  
                "vwma_hull",                   # PRIMARY: Volume-weighted trend following
                "multi_timeframe_confluence"   # SECONDARY: Trend confirmation
            ],
            MarketRegime.SIDEWAYS: [
                "multi_timeframe_confluence",  # PRIMARY: Designed for ranging markets
                "heikin_ashi",                 # SECONDARY: Can catch trend breaks
                "vwma_hull"                    # SECONDARY: Volume analysis in ranges
            ],
            MarketRegime.REVERSAL_ZONE: [
                "engulfing_multi_tf",          # PRIMARY: Reversal pattern specialist
                "multi_timeframe_confluence",  # SECONDARY: Confluence confirmation
                "heikin_ashi"                  # SECONDARY: Trend change detection
            ],
            MarketRegime.BREAKOUT: [
                "vwma_hull",                   # PRIMARY: Volume spike detection
                "heikin_ashi",                 # SECONDARY: Momentum capture
                "multi_timeframe_confluence"   # SECONDARY: Breakout confirmation
            ],
            MarketRegime.HIGH_VOLATILITY: [
                "heikin_ashi",                 # PRIMARY: Noise reduction in volatility
                "vwma_hull",                   # SECONDARY: Volume-based filtering
                "multi_timeframe_confluence"   # SECONDARY: Stability check
            ],
            MarketRegime.LOW_VOLATILITY: [
                "multi_timeframe_confluence",  # PRIMARY: Conservative for low vol
                "heikin_ashi",                 # SECONDARY: Trend emergence detection
                "vwma_hull"                    # SECONDARY: Volume pattern analysis
            ]
        }
        
        return strategy_mapping.get(regime, [
            "multi_timeframe_confluence",     # Safe fallback
            "heikin_ashi",                    # Broad applicability  
            "vwma_hull"                       # Volume-based analysis
        ])