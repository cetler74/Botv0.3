"""
Analysis package for the crypto trading bot.
Provides market regime detection and technical analysis functionality.
"""

from .market_regime import MarketRegimeDetector, MarketRegime, RegimeSignal
from .technical_analysis import TechnicalAnalysis, IndicatorResult
from .pattern_recognition import PatternRecognition, PatternResult
from .signal_generator import SignalGenerator, Signal

__all__ = [
    'MarketRegimeDetector',
    'MarketRegime', 
    'RegimeSignal',
    'TechnicalAnalysis',
    'IndicatorResult',
    'PatternRecognition',
    'PatternResult',
    'SignalGenerator',
    'Signal'
] 