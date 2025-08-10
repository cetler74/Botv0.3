"""
Pattern Recognition module for the crypto trading bot.
Provides candlestick pattern and chart pattern recognition.
"""
import pandas as pd
import numpy as np
from typing import Dict, Any, List
from dataclasses import dataclass

@dataclass
class PatternResult:
    """Result of a pattern recognition analysis."""
    name: str
    confidence: float
    signal: str  # 'buy', 'sell', 'hold'
    location: int  # Index in the data where pattern was found
    metadata: Dict[str, Any]

class PatternRecognition:
    """Pattern recognition functionality."""
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize pattern recognition."""
        self.config = config
    
    def detect_doji(self, data: pd.DataFrame) -> List[PatternResult]:
        """Detect Doji candlestick patterns."""
        results = []
        
        for i in range(len(data)):
            open_price = data.iloc[i]['open']
            close_price = data.iloc[i]['close']
            high_price = data.iloc[i]['high']
            low_price = data.iloc[i]['low']
            
            # Calculate body and shadow sizes
            body_size = abs(close_price - open_price)
            total_range = high_price - low_price
            
            # Doji: very small body relative to total range
            if total_range > 0 and body_size / total_range < 0.1:
                confidence = 1.0 - (body_size / total_range) * 10
                
                results.append(PatternResult(
                    name="Doji",
                    confidence=confidence,
                    signal="hold",  # Doji indicates indecision
                    location=i,
                    metadata={
                        'body_ratio': body_size / total_range if total_range > 0 else 0,
                        'total_range': total_range
                    }
                ))
        
        return results
    
    def detect_hammer(self, data: pd.DataFrame) -> List[PatternResult]:
        """Detect Hammer candlestick patterns."""
        results = []
        
        for i in range(len(data)):
            open_price = data.iloc[i]['open']
            close_price = data.iloc[i]['close']
            high_price = data.iloc[i]['high']
            low_price = data.iloc[i]['low']
            
            # Calculate components
            body_size = abs(close_price - open_price)
            lower_shadow = min(open_price, close_price) - low_price
            upper_shadow = high_price - max(open_price, close_price)
            total_range = high_price - low_price
            
            # Hammer: small body, long lower shadow, short upper shadow
            if (total_range > 0 and 
                lower_shadow > 2 * body_size and 
                upper_shadow < body_size and
                body_size / total_range < 0.3):
                
                confidence = min(1.0, lower_shadow / (2 * body_size))
                
                results.append(PatternResult(
                    name="Hammer",
                    confidence=confidence,
                    signal="buy",  # Hammer is typically bullish
                    location=i,
                    metadata={
                        'lower_shadow_ratio': lower_shadow / total_range if total_range > 0 else 0,
                        'body_ratio': body_size / total_range if total_range > 0 else 0
                    }
                ))
        
        return results
    
    def detect_engulfing(self, data: pd.DataFrame) -> List[PatternResult]:
        """Detect Engulfing candlestick patterns."""
        results = []
        
        for i in range(1, len(data)):
            # Current and previous candles
            prev_open = data.iloc[i-1]['open']
            prev_close = data.iloc[i-1]['close']
            curr_open = data.iloc[i]['open']
            curr_close = data.iloc[i]['close']
            
            prev_body = abs(prev_close - prev_open)
            curr_body = abs(curr_close - curr_open)
            
            # Bullish engulfing
            if (prev_close < prev_open and  # Previous candle is bearish
                curr_close > curr_open and  # Current candle is bullish
                curr_open < prev_close and  # Current opens below previous close
                curr_close > prev_open and  # Current closes above previous open
                curr_body > prev_body):     # Current body is larger
                
                confidence = min(1.0, curr_body / prev_body - 1.0)
                
                results.append(PatternResult(
                    name="Bullish Engulfing",
                    confidence=confidence,
                    signal="buy",
                    location=i,
                    metadata={
                        'size_ratio': curr_body / prev_body if prev_body > 0 else 0,
                        'engulfing_strength': (curr_close - prev_open) / prev_body if prev_body > 0 else 0
                    }
                ))
            
            # Bearish engulfing
            elif (prev_close > prev_open and  # Previous candle is bullish
                  curr_close < curr_open and  # Current candle is bearish
                  curr_open > prev_close and  # Current opens above previous close
                  curr_close < prev_open and  # Current closes below previous open
                  curr_body > prev_body):     # Current body is larger
                
                confidence = min(1.0, curr_body / prev_body - 1.0)
                
                results.append(PatternResult(
                    name="Bearish Engulfing",
                    confidence=confidence,
                    signal="sell",
                    location=i,
                    metadata={
                        'size_ratio': curr_body / prev_body if prev_body > 0 else 0,
                        'engulfing_strength': (prev_open - curr_close) / prev_body if prev_body > 0 else 0
                    }
                ))
        
        return results
    
    def detect_shooting_star(self, data: pd.DataFrame) -> List[PatternResult]:
        """Detect Shooting Star candlestick patterns."""
        results = []
        
        for i in range(len(data)):
            open_price = data.iloc[i]['open']
            close_price = data.iloc[i]['close']
            high_price = data.iloc[i]['high']
            low_price = data.iloc[i]['low']
            
            # Calculate components
            body_size = abs(close_price - open_price)
            upper_shadow = high_price - max(open_price, close_price)
            lower_shadow = min(open_price, close_price) - low_price
            total_range = high_price - low_price
            
            # Shooting star: small body, long upper shadow, short lower shadow
            if (total_range > 0 and 
                upper_shadow > 2 * body_size and 
                lower_shadow < body_size and
                body_size / total_range < 0.3):
                
                confidence = min(1.0, upper_shadow / (2 * body_size))
                
                results.append(PatternResult(
                    name="Shooting Star",
                    confidence=confidence,
                    signal="sell",  # Shooting star is typically bearish
                    location=i,
                    metadata={
                        'upper_shadow_ratio': upper_shadow / total_range if total_range > 0 else 0,
                        'body_ratio': body_size / total_range if total_range > 0 else 0
                    }
                ))
        
        return results
    
    def detect_support_resistance(self, data: pd.DataFrame, window: int = 20) -> Dict[str, List[float]]:
        """Detect support and resistance levels."""
        highs = data['high'].values
        lows = data['low'].values
        
        support_levels = []
        resistance_levels = []
        
        # Find local minima (support) and maxima (resistance)
        for i in range(window, len(data) - window):
            # Check for local minimum (support)
            if all(lows[i] <= lows[i-j] for j in range(1, window+1)) and \
               all(lows[i] <= lows[i+j] for j in range(1, window+1)):
                support_levels.append(lows[i])
            
            # Check for local maximum (resistance)
            if all(highs[i] >= highs[i-j] for j in range(1, window+1)) and \
               all(highs[i] >= highs[i+j] for j in range(1, window+1)):
                resistance_levels.append(highs[i])
        
        return {
            'support': support_levels,
            'resistance': resistance_levels
        }
    
    def detect_trend_lines(self, data: pd.DataFrame) -> Dict[str, Any]:
        """Detect trend lines in price data."""
        closes = data['close'].values
        
        # Simple trend line detection using linear regression
        x = np.arange(len(closes))
        
        # Overall trend
        slope, intercept = np.polyfit(x, closes, 1)
        
        # Trend strength (R-squared)
        y_pred = slope * x + intercept
        ss_res = np.sum((closes - y_pred) ** 2)
        ss_tot = np.sum((closes - np.mean(closes)) ** 2)
        r_squared = 1 - (ss_res / ss_tot) if ss_tot != 0 else 0
        
        trend_direction = "bullish" if slope > 0 else "bearish" if slope < 0 else "sideways"
        
        return {
            'slope': slope,
            'intercept': intercept,
            'r_squared': r_squared,
            'trend_direction': trend_direction,
            'trend_strength': abs(slope) * r_squared
        }
    
    def detect_triangles(self, data: pd.DataFrame, window: int = 20) -> List[PatternResult]:
        """Detect triangle patterns (ascending, descending, symmetrical)."""
        results = []
        
        if len(data) < window * 2:
            return results
        
        highs = data['high'].values
        lows = data['low'].values
        
        # Look for triangle patterns in sliding windows
        for i in range(window, len(data) - window):
            window_highs = highs[i-window:i+window]
            window_lows = lows[i-window:i+window]
            
            # Find trend lines for highs and lows
            x = np.arange(len(window_highs))
            
            try:
                high_slope, _ = np.polyfit(x, window_highs, 1)
                low_slope, _ = np.polyfit(x, window_lows, 1)
                
                # Ascending triangle: flat resistance, rising support
                if abs(high_slope) < 0.001 and low_slope > 0.001:
                    results.append(PatternResult(
                        name="Ascending Triangle",
                        confidence=0.7,
                        signal="buy",
                        location=i,
                        metadata={'high_slope': high_slope, 'low_slope': low_slope}
                    ))
                
                # Descending triangle: falling resistance, flat support
                elif high_slope < -0.001 and abs(low_slope) < 0.001:
                    results.append(PatternResult(
                        name="Descending Triangle",
                        confidence=0.7,
                        signal="sell",
                        location=i,
                        metadata={'high_slope': high_slope, 'low_slope': low_slope}
                    ))
                
                # Symmetrical triangle: converging lines
                elif high_slope < -0.001 and low_slope > 0.001:
                    results.append(PatternResult(
                        name="Symmetrical Triangle",
                        confidence=0.6,
                        signal="hold",
                        location=i,
                        metadata={'high_slope': high_slope, 'low_slope': low_slope}
                    ))
                    
            except np.linalg.LinAlgError:
                continue
        
        return results
    
    def detect_head_and_shoulders(self, data: pd.DataFrame, window: int = 10) -> List[PatternResult]:
        """Detect Head and Shoulders patterns."""
        results = []
        
        if len(data) < window * 6:  # Need enough data for pattern
            return results
        
        highs = data['high'].values
        
        # Find local maxima
        peaks = []
        for i in range(window, len(highs) - window):
            if all(highs[i] >= highs[i-j] for j in range(1, window+1)) and \
               all(highs[i] >= highs[i+j] for j in range(1, window+1)):
                peaks.append((i, highs[i]))
        
        # Look for head and shoulders pattern (3 peaks with middle one highest)
        for i in range(1, len(peaks) - 1):
            left_shoulder = peaks[i-1]
            head = peaks[i]
            right_shoulder = peaks[i+1]
            
            # Check if middle peak is highest and shoulders are roughly equal
            if (head[1] > left_shoulder[1] and head[1] > right_shoulder[1] and
                abs(left_shoulder[1] - right_shoulder[1]) / head[1] < 0.05):
                
                confidence = 0.8 * (1 - abs(left_shoulder[1] - right_shoulder[1]) / head[1])
                
                results.append(PatternResult(
                    name="Head and Shoulders",
                    confidence=confidence,
                    signal="sell",
                    location=head[0],
                    metadata={
                        'left_shoulder': left_shoulder,
                        'head': head,
                        'right_shoulder': right_shoulder
                    }
                ))
        
        return results
    
    def analyze_all_patterns(self, data: pd.DataFrame) -> Dict[str, List[PatternResult]]:
        """Analyze all available patterns."""
        return {
            'doji': self.detect_doji(data),
            'hammer': self.detect_hammer(data),
            'engulfing': self.detect_engulfing(data),
            'shooting_star': self.detect_shooting_star(data),
            'triangles': self.detect_triangles(data),
            'head_and_shoulders': self.detect_head_and_shoulders(data)
        } 