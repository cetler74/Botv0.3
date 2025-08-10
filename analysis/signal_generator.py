"""
Signal Generator module for the crypto trading bot.
Combines technical indicators and patterns to generate trading signals.
"""
import pandas as pd
import numpy as np
from typing import Dict, Any, List, Tuple
from dataclasses import dataclass
from datetime import datetime

@dataclass
class Signal:
    """Trading signal with metadata."""
    action: str  # 'buy', 'sell', 'hold'
    confidence: float
    strength: float
    price: float
    timestamp: datetime
    indicators: Dict[str, Any]
    patterns: List[str]
    reasoning: List[str]

class SignalGenerator:
    """Generate trading signals from technical analysis."""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        
        # Signal thresholds
        self.min_confidence = getattr(config, 'min_signal_confidence', 0.6)
        self.min_strength = getattr(config, 'min_signal_strength', 0.5)
        
        # Indicator weights
        self.indicator_weights = {
            'trend': 0.3,
            'momentum': 0.25,
            'volume': 0.2,
            'patterns': 0.15,
            'volatility': 0.1
        }
    
    def generate_signal(
        self,
        data: pd.DataFrame,
        indicators: Dict[str, Any],
        patterns: List[Any]
    ) -> Signal:
        """Generate a comprehensive trading signal."""
        try:
            current_price = data['close'].iloc[-1]
            timestamp = datetime.utcnow()
            
            # Analyze different components
            trend_signal = self._analyze_trend(data, indicators)
            momentum_signal = self._analyze_momentum(indicators)
            volume_signal = self._analyze_volume(data)
            pattern_signal = self._analyze_patterns(patterns)
            volatility_signal = self._analyze_volatility(data, indicators)
            
            # Combine all signals
            combined_action, combined_confidence, combined_strength = self._combine_signals([
                trend_signal,
                momentum_signal,
                volume_signal,
                pattern_signal,
                volatility_signal
            ])
            
            # Build reasoning
            reasoning = self._build_reasoning(
                trend_signal, momentum_signal, volume_signal, 
                pattern_signal, volatility_signal
            )
            
            # Extract pattern names
            pattern_names = [p.name for p in patterns if hasattr(p, 'name')]
            
            return Signal(
                action=combined_action,
                confidence=combined_confidence,
                strength=combined_strength,
                price=current_price,
                timestamp=timestamp,
                indicators=indicators,
                patterns=pattern_names,
                reasoning=reasoning
            )
            
        except Exception as e:
            # Return hold signal on error
            return self._create_hold_signal(data, f"Error generating signal: {str(e)}")
    
    def _analyze_trend(self, data: pd.DataFrame, indicators: Dict[str, Any]) -> Tuple[str, float, float]:
        """Analyze trend indicators."""
        try:
            signals = []
            
            # Moving average analysis
            if 'sma_20' in indicators and 'sma_50' in indicators:
                sma_20 = indicators['sma_20']
                sma_50 = indicators['sma_50']
                current_price = data['close'].iloc[-1]
                
                if current_price > sma_20 > sma_50:
                    signals.append(('buy', 0.8, 0.7))
                elif current_price < sma_20 < sma_50:
                    signals.append(('sell', 0.8, 0.7))
                else:
                    signals.append(('hold', 0.5, 0.3))
            
            # EMA analysis
            if 'ema_12' in indicators and 'ema_26' in indicators:
                ema_12 = indicators['ema_12']
                ema_26 = indicators['ema_26']
                
                if ema_12 > ema_26:
                    signals.append(('buy', 0.7, 0.6))
                else:
                    signals.append(('sell', 0.7, 0.6))
            
            # ADX trend strength
            if 'adx' in indicators:
                adx = indicators['adx']
                plus_di = indicators.get('plus_di', 0)
                minus_di = indicators.get('minus_di', 0)
                
                if adx > 25:  # Strong trend
                    if plus_di > minus_di:
                        signals.append(('buy', 0.9, 0.8))
                    else:
                        signals.append(('sell', 0.9, 0.8))
                else:
                    signals.append(('hold', 0.6, 0.4))
            
            return self._aggregate_signals(signals)
            
        except Exception as e:
            return 'hold', 0.0, 0.0
    
    def _analyze_momentum(self, indicators: Dict[str, Any]) -> Tuple[str, float, float]:
        """Analyze momentum indicators."""
        try:
            signals = []
            
            # RSI analysis
            if 'rsi' in indicators:
                rsi = indicators['rsi']
                
                if rsi < 30:
                    signals.append(('buy', 0.8, 0.7))  # Oversold
                elif rsi > 70:
                    signals.append(('sell', 0.8, 0.7))  # Overbought
                elif 40 <= rsi <= 60:
                    signals.append(('hold', 0.6, 0.4))  # Neutral
                elif rsi > 50:
                    signals.append(('buy', 0.6, 0.5))  # Bullish momentum
                else:
                    signals.append(('sell', 0.6, 0.5))  # Bearish momentum
            
            # MACD analysis
            if 'macd' in indicators and 'macd_signal' in indicators:
                macd = indicators['macd']
                macd_signal = indicators['macd_signal']
                macd_hist = indicators.get('macd_hist', 0)
                
                if macd > macd_signal and macd_hist > 0:
                    signals.append(('buy', 0.8, 0.7))
                elif macd < macd_signal and macd_hist < 0:
                    signals.append(('sell', 0.8, 0.7))
                else:
                    signals.append(('hold', 0.5, 0.3))
            
            # Stochastic analysis (if available)
            if 'stoch_k' in indicators and 'stoch_d' in indicators:
                stoch_k = indicators['stoch_k']
                stoch_d = indicators['stoch_d']
                
                if stoch_k < 20 and stoch_d < 20:
                    signals.append(('buy', 0.7, 0.6))  # Oversold
                elif stoch_k > 80 and stoch_d > 80:
                    signals.append(('sell', 0.7, 0.6))  # Overbought
                elif stoch_k > stoch_d:
                    signals.append(('buy', 0.6, 0.5))
                else:
                    signals.append(('sell', 0.6, 0.5))
            
            return self._aggregate_signals(signals)
            
        except Exception as e:
            return 'hold', 0.0, 0.0
    
    def _analyze_volume(self, data: pd.DataFrame) -> Tuple[str, float, float]:
        """Analyze volume indicators."""
        try:
            if 'volume' not in data.columns or len(data) < 20:
                return 'hold', 0.0, 0.0
            
            signals = []
            current_volume = data['volume'].iloc[-1]
            avg_volume = data['volume'].tail(20).mean()
            
            # Volume confirmation
            volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1.0
            
            # Price and volume relationship
            price_change = (data['close'].iloc[-1] - data['close'].iloc[-2]) / data['close'].iloc[-2]
            
            if volume_ratio > 1.5:  # High volume
                if price_change > 0.01:  # Price up with high volume
                    signals.append(('buy', 0.8, 0.7))
                elif price_change < -0.01:  # Price down with high volume
                    signals.append(('sell', 0.8, 0.7))
                else:
                    signals.append(('hold', 0.6, 0.4))
            elif volume_ratio < 0.5:  # Low volume
                signals.append(('hold', 0.4, 0.2))  # Low confidence on low volume
            else:
                # Normal volume - neutral signal
                if price_change > 0:
                    signals.append(('buy', 0.5, 0.4))
                elif price_change < 0:
                    signals.append(('sell', 0.5, 0.4))
                else:
                    signals.append(('hold', 0.5, 0.3))
            
            return self._aggregate_signals(signals)
            
        except Exception as e:
            return 'hold', 0.0, 0.0
    
    def _analyze_patterns(self, patterns: List[Any]) -> Tuple[str, float, float]:
        """Analyze chart patterns."""
        try:
            if not patterns:
                return 'hold', 0.0, 0.0
            
            signals = []
            
            for pattern in patterns:
                if hasattr(pattern, 'signal') and hasattr(pattern, 'confidence'):
                    # Weight pattern signals by their confidence
                    strength = pattern.confidence * 0.8  # Patterns are generally less reliable
                    signals.append((pattern.signal, pattern.confidence, strength))
            
            return self._aggregate_signals(signals)
            
        except Exception as e:
            return 'hold', 0.0, 0.0
    
    def _analyze_volatility(self, data: pd.DataFrame, indicators: Dict[str, Any]) -> Tuple[str, float, float]:
        """Analyze volatility indicators."""
        try:
            signals = []
            
            # Bollinger Bands analysis
            if all(key in indicators for key in ['bb_upper', 'bb_lower', 'bb_middle']):
                current_price = data['close'].iloc[-1]
                bb_upper = indicators['bb_upper']
                bb_lower = indicators['bb_lower']
                bb_middle = indicators['bb_middle']
                
                bb_position = (current_price - bb_lower) / (bb_upper - bb_lower) if bb_upper != bb_lower else 0.5
                
                if bb_position < 0.1:
                    signals.append(('buy', 0.7, 0.6))  # Near lower band
                elif bb_position > 0.9:
                    signals.append(('sell', 0.7, 0.6))  # Near upper band
                elif 0.4 <= bb_position <= 0.6:
                    signals.append(('hold', 0.5, 0.3))  # Near middle
                else:
                    # Trending within bands
                    if bb_position > 0.5:
                        signals.append(('buy', 0.6, 0.4))
                    else:
                        signals.append(('sell', 0.6, 0.4))
            
            # ATR-based volatility
            if 'atr' in indicators:
                atr = indicators['atr']
                current_price = data['close'].iloc[-1]
                atr_ratio = atr / current_price if current_price > 0 else 0
                
                # High volatility reduces signal confidence
                volatility_factor = max(0.3, 1.0 - (atr_ratio * 20))
                
                # Apply volatility adjustment to all signals
                adjusted_signals = []
                for action, conf, strength in signals:
                    adjusted_signals.append((action, conf * volatility_factor, strength * volatility_factor))
                signals = adjusted_signals
            
            return self._aggregate_signals(signals) if signals else ('hold', 0.0, 0.0)
            
        except Exception as e:
            return 'hold', 0.0, 0.0
    
    def _aggregate_signals(self, signals: List[Tuple[str, float, float]]) -> Tuple[str, float, float]:
        """Aggregate multiple signals into one."""
        if not signals:
            return 'hold', 0.0, 0.0
        
        # Count votes and calculate weighted averages
        buy_votes = [s for s in signals if s[0] == 'buy']
        sell_votes = [s for s in signals if s[0] == 'sell']
        hold_votes = [s for s in signals if s[0] == 'hold']
        
        # Calculate weighted scores
        buy_score = sum(conf * strength for _, conf, strength in buy_votes)
        sell_score = sum(conf * strength for _, conf, strength in sell_votes)
        hold_score = sum(conf * strength for _, conf, strength in hold_votes)
        
        total_score = buy_score + sell_score + hold_score
        
        if total_score == 0:
            return 'hold', 0.0, 0.0
        
        # Determine action based on highest score
        if buy_score > sell_score and buy_score > hold_score:
            action = 'buy'
            confidence = buy_score / total_score
            strength = sum(strength for _, _, strength in buy_votes) / len(buy_votes)
        elif sell_score > buy_score and sell_score > hold_score:
            action = 'sell'
            confidence = sell_score / total_score
            strength = sum(strength for _, _, strength in sell_votes) / len(sell_votes)
        else:
            action = 'hold'
            confidence = hold_score / total_score if hold_votes else 0.5
            strength = sum(strength for _, _, strength in hold_votes) / len(hold_votes) if hold_votes else 0.3
        
        return action, confidence, strength
    
    def _combine_signals(self, component_signals: List[Tuple[str, float, float]]) -> Tuple[str, float, float]:
        """Combine signals from different analysis components."""
        if not component_signals:
            return 'hold', 0.0, 0.0
        
        # Apply weights to each component
        weights = [
            self.indicator_weights['trend'],
            self.indicator_weights['momentum'],
            self.indicator_weights['volume'],
            self.indicator_weights['patterns'],
            self.indicator_weights['volatility']
        ]
        
        weighted_signals = []
        for i, (action, conf, strength) in enumerate(component_signals):
            weight = weights[i] if i < len(weights) else 0.1
            weighted_signals.append((action, conf * weight, strength * weight))
        
        return self._aggregate_signals(weighted_signals)
    
    def _build_reasoning(
        self,
        trend_signal: Tuple[str, float, float],
        momentum_signal: Tuple[str, float, float],
        volume_signal: Tuple[str, float, float],
        pattern_signal: Tuple[str, float, float],
        volatility_signal: Tuple[str, float, float]
    ) -> List[str]:
        """Build reasoning for the signal."""
        reasoning = []
        
        # Trend reasoning
        if trend_signal[1] > 0.6:
            reasoning.append(f"Trend analysis: {trend_signal[0]} (confidence: {trend_signal[1]:.2f})")
        
        # Momentum reasoning
        if momentum_signal[1] > 0.6:
            reasoning.append(f"Momentum analysis: {momentum_signal[0]} (confidence: {momentum_signal[1]:.2f})")
        
        # Volume reasoning
        if volume_signal[1] > 0.6:
            reasoning.append(f"Volume analysis: {volume_signal[0]} (confidence: {volume_signal[1]:.2f})")
        
        # Pattern reasoning
        if pattern_signal[1] > 0.6:
            reasoning.append(f"Pattern analysis: {pattern_signal[0]} (confidence: {pattern_signal[1]:.2f})")
        
        # Volatility reasoning
        if volatility_signal[1] > 0.6:
            reasoning.append(f"Volatility analysis: {volatility_signal[0]} (confidence: {volatility_signal[1]:.2f})")
        
        if not reasoning:
            reasoning.append("No strong signals detected - holding position")
        
        return reasoning
    
    def _create_hold_signal(self, data: pd.DataFrame, reason: str) -> Signal:
        """Create a hold signal with given reason."""
        current_price = data['close'].iloc[-1] if len(data) > 0 else 0.0
        
        return Signal(
            action='hold',
            confidence=0.5,
            strength=0.3,
            price=current_price,
            timestamp=datetime.utcnow(),
            indicators={},
            patterns=[],
            reasoning=[reason]
        )
    
    def validate_signal(self, signal: Signal) -> bool:
        """Validate if signal meets minimum requirements."""
        return (
            signal.confidence >= self.min_confidence and
            signal.strength >= self.min_strength and
            signal.action in ['buy', 'sell', 'hold']
        )
    
    def adjust_signal_for_market_conditions(
        self,
        signal: Signal,
        market_regime: str,
        volatility_level: str
    ) -> Signal:
        """Adjust signal based on market conditions."""
        try:
            adjusted_signal = signal
            
            # Market regime adjustments
            if market_regime == 'bear' and signal.action == 'buy':
                # Reduce buy signal confidence in bear market
                adjusted_signal.confidence *= 0.7
                adjusted_signal.reasoning.append("Reduced confidence due to bear market")
            elif market_regime == 'bull' and signal.action == 'sell':
                # Reduce sell signal confidence in bull market
                adjusted_signal.confidence *= 0.7
                adjusted_signal.reasoning.append("Reduced confidence due to bull market")
            
            # Volatility adjustments
            if volatility_level == 'high':
                # Reduce all signal confidence in high volatility
                adjusted_signal.confidence *= 0.8
                adjusted_signal.reasoning.append("Reduced confidence due to high volatility")
            elif volatility_level == 'low':
                # Increase signal confidence in low volatility
                adjusted_signal.confidence = min(1.0, adjusted_signal.confidence * 1.1)
                adjusted_signal.reasoning.append("Increased confidence due to low volatility")
            
            return adjusted_signal
            
        except Exception as e:
            return signal  # Return original signal on error 