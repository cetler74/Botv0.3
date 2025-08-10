"""
Market Regime Detection System for Crypto Trading Bot.
Identifies bull, bear, and sideways market conditions using multiple indicators.
"""
import logging
import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
import talib

logger = logging.getLogger(__name__)

class MarketRegime(Enum):
    """Market regime enumeration."""
    BULL = "bull"
    BEAR = "bear"
    SIDEWAYS = "sideways"
    TRANSITION = "transition"
    UNKNOWN = "unknown"

@dataclass
class RegimeSignal:
    """Market regime signal with confidence."""
    regime: MarketRegime
    confidence: float
    strength: float
    duration: int  # Number of periods in this regime
    indicators: Dict[str, float]
    timestamp: datetime

@dataclass
class RegimeMetrics:
    """Comprehensive regime analysis metrics."""
    current_regime: MarketRegime
    regime_confidence: float
    regime_strength: float
    regime_duration: int
    trend_direction: float  # -1 to 1
    volatility_regime: str  # low, medium, high
    momentum_score: float
    support_resistance_levels: Dict[str, float]
    regime_change_probability: float

class MarketRegimeDetector:
    """Advanced market regime detection system."""
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize the regime detector."""
        self.config = config
        
        # Regime detection parameters
        self.lookback_period = getattr(config, 'regime_lookback_period', 50)
        self.trend_period = getattr(config, 'regime_trend_period', 20)
        self.momentum_period = getattr(config, 'regime_momentum_period', 14)
        self.volatility_period = getattr(config, 'regime_volatility_period', 20)
        
        # Thresholds
        self.bull_threshold = getattr(config, 'bull_threshold', 0.6)
        self.bear_threshold = getattr(config, 'bear_threshold', -0.6)
        self.sideways_threshold = getattr(config, 'sideways_threshold', 0.3)
        self.transition_threshold = getattr(config, 'transition_threshold', 0.8)
        
        # Volatility thresholds
        self.low_volatility_threshold = getattr(config, 'low_volatility_threshold', 0.02)
        self.high_volatility_threshold = getattr(config, 'high_volatility_threshold', 0.05)
        
        # Internal state
        self.regime_history = []
        self.indicator_cache = {}
        self.last_update = None
        
    async def detect_regime(
        self,
        market_data: Dict[str, pd.DataFrame],
        timeframe: str = '1h'
    ) -> Dict[str, RegimeSignal]:
        """
        Detect market regime for multiple trading pairs.
        
        Args:
            market_data: Dictionary of pair -> OHLCV DataFrame
            timeframe: Timeframe for analysis
            
        Returns:
            Dictionary of pair -> RegimeSignal
        """
        regime_signals = {}
        
        try:
            for pair, ohlcv in market_data.items():
                if not isinstance(ohlcv, pd.DataFrame) or len(ohlcv) < self.lookback_period:
                    logger.warning(f"Insufficient data for regime detection: {pair}")
                    continue
                
                # Calculate regime for this pair
                regime_signal = await self._analyze_single_pair_regime(pair, ohlcv)
                regime_signals[pair] = regime_signal
                
            # Update regime history
            self._update_regime_history(regime_signals)
            
            return regime_signals
            
        except Exception as e:
            logger.error(f"Error detecting market regime: {str(e)}")
            return {}
    
    async def _analyze_single_pair_regime(
        self,
        pair: str,
        ohlcv: pd.DataFrame
    ) -> RegimeSignal:
        """Analyze market regime for a single trading pair."""
        try:
            # Calculate all indicators
            indicators = await self._calculate_regime_indicators(ohlcv)
            
            # Calculate regime scores
            trend_score = self._calculate_trend_score(indicators)
            momentum_score = self._calculate_momentum_score(indicators)
            volatility_score = self._calculate_volatility_score(indicators)
            volume_score = self._calculate_volume_score(indicators)
            
            # Combine scores
            combined_score = self._combine_regime_scores(
                trend_score, momentum_score, volatility_score, volume_score
            )
            
            # Determine regime
            regime = self._determine_regime(combined_score)
            
            # Calculate confidence
            confidence = self._calculate_regime_confidence(
                combined_score, indicators
            )
            
            # Calculate strength
            strength = abs(combined_score)
            
            # Calculate duration (placeholder)
            duration = self._calculate_regime_duration(pair, regime)
            
            return RegimeSignal(
                regime=regime,
                confidence=confidence,
                strength=strength,
                duration=duration,
                indicators=indicators,
                timestamp=datetime.utcnow()
            )
            
        except Exception as e:
            logger.error(f"Error analyzing regime for {pair}: {str(e)}")
            return RegimeSignal(
                MarketRegime.UNKNOWN, 0.0, 0.0, 0, {}, datetime.utcnow()
            )
    
    async def _calculate_regime_indicators(self, ohlcv: pd.DataFrame) -> Dict[str, float]:
        """Calculate all indicators needed for regime detection."""
        try:
            indicators = {}
            
            # Price-based indicators
            close = ohlcv['close'].values
            high = ohlcv['high'].values
            low = ohlcv['low'].values
            volume = ohlcv['volume'].values if 'volume' in ohlcv.columns else np.ones(len(close))
            
            # Moving averages
            indicators['sma_20'] = talib.SMA(close, timeperiod=20)[-1]
            indicators['sma_50'] = talib.SMA(close, timeperiod=50)[-1]
            indicators['ema_12'] = talib.EMA(close, timeperiod=12)[-1]
            indicators['ema_26'] = talib.EMA(close, timeperiod=26)[-1]
            
            # Trend indicators
            indicators['adx'] = talib.ADX(high, low, close, timeperiod=14)[-1]
            indicators['plus_di'] = talib.PLUS_DI(high, low, close, timeperiod=14)[-1]
            indicators['minus_di'] = talib.MINUS_DI(high, low, close, timeperiod=14)[-1]
            
            # Momentum indicators
            indicators['rsi'] = talib.RSI(close, timeperiod=14)[-1]
            indicators['macd'], indicators['macd_signal'], indicators['macd_hist'] = talib.MACD(close)
            indicators['macd'] = indicators['macd'][-1] if indicators['macd'] is not None else 0
            indicators['macd_signal'] = indicators['macd_signal'][-1] if indicators['macd_signal'] is not None else 0
            indicators['macd_hist'] = indicators['macd_hist'][-1] if indicators['macd_hist'] is not None else 0
            
            # Volatility indicators
            indicators['atr'] = talib.ATR(high, low, close, timeperiod=14)[-1]
            indicators['bb_upper'], indicators['bb_middle'], indicators['bb_lower'] = talib.BBANDS(close)
            indicators['bb_upper'] = indicators['bb_upper'][-1]
            indicators['bb_middle'] = indicators['bb_middle'][-1]
            indicators['bb_lower'] = indicators['bb_lower'][-1]
            
            # Volume indicators
            indicators['obv'] = talib.OBV(close, volume)[-1]
            indicators['ad'] = talib.AD(high, low, close, volume)[-1]
            
            # Price position
            indicators['current_price'] = close[-1]
            indicators['price_vs_sma20'] = (close[-1] - indicators['sma_20']) / indicators['sma_20']
            indicators['price_vs_sma50'] = (close[-1] - indicators['sma_50']) / indicators['sma_50']
            
            # Bollinger Band position
            bb_width = indicators['bb_upper'] - indicators['bb_lower']
            indicators['bb_position'] = (close[-1] - indicators['bb_lower']) / bb_width if bb_width > 0 else 0.5
            
            # Volatility metrics
            returns = pd.Series(close).pct_change().dropna()
            indicators['volatility'] = returns.tail(self.volatility_period).std()
            
            return indicators
            
        except Exception as e:
            logger.error(f"Error calculating regime indicators: {str(e)}")
            return {}
    
    def _calculate_trend_score(self, indicators: Dict[str, float]) -> float:
        """Calculate trend score from indicators."""
        try:
            score = 0.0
            weight_sum = 0.0
            
            # Moving average relationships
            if indicators.get('sma_20') and indicators.get('sma_50'):
                ma_score = 1.0 if indicators['sma_20'] > indicators['sma_50'] else -1.0
                score += ma_score * 0.3
                weight_sum += 0.3
            
            # Price vs moving averages
            if indicators.get('price_vs_sma20') is not None:
                price_ma_score = np.tanh(indicators['price_vs_sma20'] * 10)  # Scale and bound
                score += price_ma_score * 0.25
                weight_sum += 0.25
            
            # ADX and directional indicators
            if indicators.get('adx') and indicators.get('plus_di') and indicators.get('minus_di'):
                if indicators['adx'] > 25:  # Strong trend
                    if indicators['plus_di'] > indicators['minus_di']:
                        adx_score = 1.0
                    else:
                        adx_score = -1.0
                    score += adx_score * 0.25
                    weight_sum += 0.25
            
            # MACD
            if indicators.get('macd') is not None and indicators.get('macd_signal') is not None:
                macd_score = 1.0 if indicators['macd'] > indicators['macd_signal'] else -1.0
                score += macd_score * 0.2
                weight_sum += 0.2
            
            return score / weight_sum if weight_sum > 0 else 0.0
            
        except Exception as e:
            logger.error(f"Error calculating trend score: {str(e)}")
            return 0.0
    
    def _calculate_momentum_score(self, indicators: Dict[str, float]) -> float:
        """Calculate momentum score from indicators."""
        try:
            score = 0.0
            weight_sum = 0.0
            
            # RSI
            if indicators.get('rsi') is not None:
                rsi = indicators['rsi']
                if rsi > 70:
                    rsi_score = 1.0  # Overbought but bullish momentum
                elif rsi < 30:
                    rsi_score = -1.0  # Oversold but bearish momentum
                else:
                    rsi_score = (rsi - 50) / 20  # Normalized around 50
                score += rsi_score * 0.4
                weight_sum += 0.4
            
            # MACD histogram
            if indicators.get('macd_hist') is not None:
                macd_hist_score = np.tanh(indicators['macd_hist'] * 100)  # Scale and bound
                score += macd_hist_score * 0.3
                weight_sum += 0.3
            
            # Price momentum (simple)
            if indicators.get('price_vs_sma20') is not None:
                momentum_score = np.tanh(indicators['price_vs_sma20'] * 5)
                score += momentum_score * 0.3
                weight_sum += 0.3
            
            return score / weight_sum if weight_sum > 0 else 0.0
            
        except Exception as e:
            logger.error(f"Error calculating momentum score: {str(e)}")
            return 0.0
    
    def _calculate_volatility_score(self, indicators: Dict[str, float]) -> float:
        """Calculate volatility score (affects regime confidence)."""
        try:
            volatility = indicators.get('volatility', 0.02)
            
            if volatility < self.low_volatility_threshold:
                return 1.0  # Low volatility = high confidence
            elif volatility > self.high_volatility_threshold:
                return -1.0  # High volatility = low confidence
            else:
                # Linear interpolation between thresholds
                mid_point = (self.low_volatility_threshold + self.high_volatility_threshold) / 2
                if volatility < mid_point:
                    return 1.0 - 2 * (volatility - self.low_volatility_threshold) / (mid_point - self.low_volatility_threshold)
                else:
                    return -2 * (volatility - mid_point) / (self.high_volatility_threshold - mid_point)
            
        except Exception as e:
            logger.error(f"Error calculating volatility score: {str(e)}")
            return 0.0
    
    def _calculate_volume_score(self, indicators: Dict[str, float]) -> float:
        """Calculate volume score from indicators."""
        try:
            score = 0.0
            weight_sum = 0.0
            
            # OBV trend (placeholder - would need historical OBV)
            if indicators.get('obv') is not None:
                # This is simplified - in practice, you'd compare current OBV to historical
                obv_score = 0.0  # Placeholder
                score += obv_score * 0.5
                weight_sum += 0.5
            
            # A/D Line (placeholder)
            if indicators.get('ad') is not None:
                ad_score = 0.0  # Placeholder
                score += ad_score * 0.5
                weight_sum += 0.5
            
            return score / weight_sum if weight_sum > 0 else 0.0
            
        except Exception as e:
            logger.error(f"Error calculating volume score: {str(e)}")
            return 0.0
    
    def _combine_regime_scores(
        self,
        trend_score: float,
        momentum_score: float,
        volatility_score: float,
        volume_score: float
    ) -> float:
        """Combine individual scores into overall regime score."""
        try:
            # Weighted combination
            weights = {
                'trend': 0.4,
                'momentum': 0.3,
                'volatility': 0.2,
                'volume': 0.1
            }
            
            combined = (
                trend_score * weights['trend'] +
                momentum_score * weights['momentum'] +
                volatility_score * weights['volatility'] +
                volume_score * weights['volume']
            )
            
            return combined
            
        except Exception as e:
            logger.error(f"Error combining regime scores: {str(e)}")
            return 0.0
    
    def _determine_regime(self, combined_score: float) -> MarketRegime:
        """Determine market regime from combined score."""
        try:
            if combined_score > self.bull_threshold:
                return MarketRegime.BULL
            elif combined_score < self.bear_threshold:
                return MarketRegime.BEAR
            elif abs(combined_score) < self.sideways_threshold:
                return MarketRegime.SIDEWAYS
            else:
                return MarketRegime.TRANSITION
            
        except Exception as e:
            logger.error(f"Error determining regime: {str(e)}")
            return MarketRegime.UNKNOWN
    
    def _calculate_regime_confidence(
        self,
        combined_score: float,
        indicators: Dict[str, float]
    ) -> float:
        """Calculate confidence in regime determination."""
        try:
            # Base confidence on score magnitude
            base_confidence = min(1.0, abs(combined_score))
            
            # Adjust for volatility
            volatility = indicators.get('volatility', 0.02)
            volatility_adjustment = 1.0 - min(1.0, volatility / 0.1)  # Reduce confidence with high volatility
            
            # Adjust for ADX (trend strength)
            adx = indicators.get('adx', 0)
            adx_adjustment = min(1.0, adx / 50) if adx else 0.5  # Higher ADX = higher confidence
            
            # Combined confidence
            confidence = base_confidence * 0.5 + volatility_adjustment * 0.3 + adx_adjustment * 0.2
            
            return max(0.0, min(1.0, confidence))
            
        except Exception as e:
            logger.error(f"Error calculating regime confidence: {str(e)}")
            return 0.0
    
    def _calculate_regime_duration(self, pair: str, current_regime: MarketRegime) -> int:
        """Calculate how long the current regime has been in effect."""
        try:
            # This is a placeholder - in practice, you'd track regime history
            return 1  # Default to 1 period
            
        except Exception as e:
            logger.error(f"Error calculating regime duration: {str(e)}")
            return 0
    
    def _update_regime_history(self, regime_signals: Dict[str, RegimeSignal]) -> None:
        """Update internal regime history."""
        try:
            timestamp = datetime.utcnow()
            self.regime_history.append({
                'timestamp': timestamp,
                'regimes': regime_signals
            })
            
            # Keep only recent history (last 100 updates)
            if len(self.regime_history) > 100:
                self.regime_history = self.regime_history[-100:]
                
            self.last_update = timestamp
            
        except Exception as e:
            logger.error(f"Error updating regime history: {str(e)}")
    
    def get_regime_summary(self) -> Dict[str, Any]:
        """Get summary of current market regimes."""
        try:
            if not self.regime_history:
                return {}
            
            latest_regimes = self.regime_history[-1]['regimes']
            
            # Count regimes
            regime_counts = {}
            total_confidence = 0.0
            
            for pair, signal in latest_regimes.items():
                regime = signal.regime.value
                regime_counts[regime] = regime_counts.get(regime, 0) + 1
                total_confidence += signal.confidence
            
            avg_confidence = total_confidence / len(latest_regimes) if latest_regimes else 0.0
            
            # Determine overall market regime
            if regime_counts:
                dominant_regime = max(regime_counts, key=regime_counts.get)
            else:
                dominant_regime = MarketRegime.UNKNOWN.value
            
            return {
                'dominant_regime': dominant_regime,
                'regime_distribution': regime_counts,
                'average_confidence': avg_confidence,
                'total_pairs': len(latest_regimes),
                'last_update': self.last_update.isoformat() if self.last_update else None
            }
            
        except Exception as e:
            logger.error(f"Error getting regime summary: {str(e)}")
            return {}
    
    def should_adjust_strategy_for_regime(
        self,
        pair: str,
        strategy_name: str
    ) -> Tuple[bool, Dict[str, Any]]:
        """Determine if strategy should be adjusted for current regime."""
        try:
            if not self.regime_history:
                return False, {}
            
            latest_regimes = self.regime_history[-1]['regimes']
            if pair not in latest_regimes:
                return False, {}
            
            regime_signal = latest_regimes[pair]
            regime = regime_signal.regime
            confidence = regime_signal.confidence
            
            # Only suggest adjustments for high-confidence regime detection
            if confidence < 0.7:
                return False, {}
            
            adjustments = {}
            
            # Regime-specific adjustments
            if regime == MarketRegime.BULL:
                adjustments = {
                    'position_size_multiplier': 1.2,  # Increase position size
                    'stop_loss_multiplier': 0.8,      # Tighter stops
                    'take_profit_multiplier': 1.5,    # Larger targets
                    'entry_threshold_adjustment': -0.1 # Easier entry
                }
            elif regime == MarketRegime.BEAR:
                adjustments = {
                    'position_size_multiplier': 0.6,  # Reduce position size
                    'stop_loss_multiplier': 1.2,      # Wider stops
                    'take_profit_multiplier': 0.8,    # Smaller targets
                    'entry_threshold_adjustment': 0.1 # Harder entry
                }
            elif regime == MarketRegime.SIDEWAYS:
                adjustments = {
                    'position_size_multiplier': 0.8,  # Slightly reduce size
                    'stop_loss_multiplier': 1.0,      # Normal stops
                    'take_profit_multiplier': 0.9,    # Slightly smaller targets
                    'entry_threshold_adjustment': 0.05 # Slightly harder entry
                }
            
            return len(adjustments) > 0, adjustments
            
        except Exception as e:
            logger.error(f"Error checking strategy adjustment: {str(e)}")
            return False, {} 