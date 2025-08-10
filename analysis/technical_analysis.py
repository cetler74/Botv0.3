"""
Technical Analysis module for the crypto trading bot.
Provides technical indicators and analysis functionality.
"""
import pandas as pd
import numpy as np
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass
import logging
from datetime import datetime
import talib
from numba import jit
from talib._ta_lib import MA_Type

logger = logging.getLogger(__name__)

@dataclass
class IndicatorResult:
    """Result of a technical indicator calculation."""
    name: str
    value: float
    signal: str  # 'buy', 'sell', 'hold'
    confidence: float
    metadata: Dict[str, Any]

class TechnicalAnalysis:
    """Technical analysis functionality."""
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize technical analysis."""
        self.config = config
        self._setup_logging()

    def _setup_logging(self):
        """Set up logging configuration."""
        # Get log level from config, with proper fallback handling
        log_level = 'INFO'  # Default
        if hasattr(self.config, 'log_level'):
            config_level = getattr(self.config, 'log_level', 'INFO')
            if isinstance(config_level, str):
                log_level = config_level
        elif isinstance(self.config, dict) and 'log_level' in self.config:
            config_level = self.config.get('log_level', 'INFO')
            if isinstance(config_level, str):
                log_level = config_level
        
        # Ensure the log level is valid
        valid_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
        if log_level.upper() not in valid_levels:
            log_level = 'INFO'
            
        logging.basicConfig(
            level=getattr(logging, log_level.upper()),
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )

    def calculate_rsi(self, data: pd.DataFrame, period: int = 14) -> pd.Series:
        """Calculate RSI indicator."""
        delta = data['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi
    
    def calculate_macd(self, data: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> Dict[str, pd.Series]:
        """Calculate MACD indicator."""
        exp1 = data['close'].ewm(span=fast).mean()
        exp2 = data['close'].ewm(span=slow).mean()
        macd = exp1 - exp2
        signal_line = macd.ewm(span=signal).mean()
        histogram = macd - signal_line
        
        return {
            'macd': macd,
            'signal': signal_line,
            'histogram': histogram
        }
    
    def calculate_bollinger_bands(self, data: pd.DataFrame, period: int = 20, std_dev: int = 2) -> Dict[str, pd.Series]:
        """Calculate Bollinger Bands."""
        sma = data['close'].rolling(window=period).mean()
        std = data['close'].rolling(window=period).std()
        upper = sma + (std * std_dev)
        lower = sma - (std * std_dev)
        return {
            'upper': upper,
            'middle': sma,
            'lower': lower
        }
    
    def calculate_moving_averages(self, data: pd.DataFrame, periods: list = [20, 50, 200]) -> Dict[str, pd.Series]:
        """Calculate multiple moving averages."""
        mas = {}
        for period in periods:
            mas[f'ma_{period}'] = data['close'].rolling(window=period).mean()
        return mas

    def calculate_sma(
        self,
        ohlcv: pd.DataFrame,
        period: int = 20,
        price: str = 'close'
    ) -> IndicatorResult:
        """Calculate Simple Moving Average."""
        self._validate_ohlcv(ohlcv)
        values = talib.SMA(np.asarray(ohlcv[price], dtype=np.float64), timeperiod=period)
        return IndicatorResult(
            name='SMA',
            value=float(values[-1]),
            signal='',
            confidence=1.0,
            metadata={'period': period, 'price': price}
        )

    def calculate_ema(
        self,
        ohlcv: pd.DataFrame,
        period: int = 20,
        price: str = 'close'
    ) -> IndicatorResult:
        """Calculate Exponential Moving Average."""
        self._validate_ohlcv(ohlcv)
        values = talib.EMA(np.asarray(ohlcv[price], dtype=np.float64), timeperiod=period)
        return IndicatorResult(
            name='EMA',
            value=float(values[-1]),
            signal='',
            confidence=1.0,
            metadata={'period': period, 'price': price}
        )

    def calculate_atr(
        self,
        ohlcv: pd.DataFrame,
        period: int = 14
    ) -> IndicatorResult:
        """Calculate Average True Range."""
        self._validate_ohlcv(ohlcv)
        high = np.asarray(ohlcv['high'], dtype=np.float64)
        low = np.asarray(ohlcv['low'], dtype=np.float64)
        close = np.asarray(ohlcv['close'], dtype=np.float64)
        values = talib.ATR(high, low, close, timeperiod=period)
        return IndicatorResult(
            name='ATR',
            value=float(values[-1]),
            signal='',
            confidence=1.0,
            metadata={'period': period}
        )

    def calculate_stochastic(
        self,
        ohlcv: pd.DataFrame,
        k_period: int = 14,
        d_period: int = 3,
        slowing: int = 3
    ) -> IndicatorResult:
        """Calculate Stochastic Oscillator."""
        self._validate_ohlcv(ohlcv)
        high = np.asarray(ohlcv['high'], dtype=np.float64)
        low = np.asarray(ohlcv['low'], dtype=np.float64)
        close = np.asarray(ohlcv['close'], dtype=np.float64)
        slowk, slowd = talib.STOCH(
            high,
            low,
            close,
            fastk_period=k_period,
            slowk_period=slowing,
            slowk_matype=MA_Type.SMA,
            slowd_period=d_period,
            slowd_matype=MA_Type.SMA
        )
        return IndicatorResult(
            name='Stochastic',
            value=float(slowk[-1]),
            signal='',
            confidence=1.0,
            metadata={
                'k_period': k_period,
                'd_period': d_period,
                'slowing': slowing
            }
        )

    def calculate_volume_profile(self, data: pd.DataFrame, num_bins: int = 24) -> IndicatorResult:
        """Calculate Volume Profile as a histogram of volume by price bins."""
        if len(data) == 0:
            return IndicatorResult(name='VolumeProfile', value=np.array([]), signal='', confidence=1.0, metadata={'num_bins': num_bins})
        hist, bin_edges = np.histogram(data['close'], bins=num_bins, weights=data['volume'])
        return IndicatorResult(name='VolumeProfile', value=hist, signal='', confidence=1.0, metadata={'num_bins': num_bins, 'bin_edges': bin_edges})

    @staticmethod
    @jit(nopython=True)
    def _calculate_vwma(
        close: np.ndarray,
        volume: np.ndarray,
        period: int
    ) -> np.ndarray:
        """Calculate Volume Weighted Moving Average (optimized with Numba)."""
        close = close.astype(np.float64)
        volume = volume.astype(np.float64)
        vwma = np.zeros_like(close)
        for i in range(period - 1, len(close)):
            vwma[i] = np.sum(close[i-period+1:i+1] * volume[i-period+1:i+1]) / np.sum(volume[i-period+1:i+1])
        return vwma

    def calculate_vwma(
        self,
        ohlcv: pd.DataFrame,
        period: int = 20
    ) -> IndicatorResult:
        """Calculate Volume Weighted Moving Average."""
        self._validate_ohlcv(ohlcv)
        values = self._calculate_vwma(
            ohlcv['close'].values,
            ohlcv['volume'].values,
            period
        )
        return IndicatorResult(
            name='VWMA',
            value=values[-1],
            signal='',
            confidence=1.0,
            metadata={'period': period}
        )

    def detect_patterns(self, data: pd.DataFrame, patterns: list) -> dict:
        """Stub: Return empty arrays for each pattern (implement real logic as needed)."""
        return {pattern: np.array([]) for pattern in patterns}

    def calculate_support_resistance(
        self,
        ohlcv: pd.DataFrame,
        window: int = 20,
        threshold: float = 0.02
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Calculate support and resistance levels."""
        self._validate_ohlcv(ohlcv)
        highs = ohlcv['high'].values
        lows = ohlcv['low'].values
        
        resistance = []
        support = []
        
        for i in range(window, len(ohlcv) - window):
            # Resistance
            if all(highs[i] > highs[i-window:i]) and all(highs[i] > highs[i+1:i+window+1]):
                resistance.append(highs[i])
            
            # Support
            if all(lows[i] < lows[i-window:i]) and all(lows[i] < lows[i+1:i+window+1]):
                support.append(lows[i])
        
        return np.array(support), np.array(resistance)

    def calculate_market_regime(
        self,
        ohlcv: pd.DataFrame,
        window: int = 20,
        threshold: float = 0.02
    ) -> str:
        """Determine market regime (trending, ranging, volatile)."""
        self._validate_ohlcv(ohlcv)
        
        # Calculate ATR for volatility
        atr = self.calculate_atr(ohlcv, period=window).value
        
        # Calculate price range
        price_range = (ohlcv['high'] - ohlcv['low']).values
        
        # Calculate trend using ADX
        adx = talib.ADX(
            ohlcv['high'].values,
            ohlcv['low'].values,
            ohlcv['close'].values,
            timeperiod=window
        )
        
        # Determine regime
        if np.mean(atr[-window:]) > threshold * np.mean(price_range[-window:]):
            return 'volatile'
        elif np.mean(adx[-window:]) > 25:
            return 'trending'
        else:
            return 'ranging'

    def _validate_ohlcv(self, ohlcv: pd.DataFrame) -> None:
        """Validate OHLCV data format and values."""
        required_columns = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
        if not all(col in ohlcv.columns for col in required_columns):
            raise ValueError(f"OHLCV data must contain columns: {required_columns}")
        # Raise error if any close price is negative or zero
        if (ohlcv['close'] <= 0).any():
            raise ValueError("OHLCV data contains non-positive close prices.")

    @staticmethod
    def adx(data: pd.DataFrame, period: int = 14) -> pd.Series:
        """Calculate ADX indicator using TA-Lib."""
        high = np.asarray(data['high'], dtype=np.float64)
        low = np.asarray(data['low'], dtype=np.float64)
        close = np.asarray(data['close'], dtype=np.float64)
        adx_values = talib.ADX(high, low, close, timeperiod=period)
        return pd.Series(adx_values, index=data.index)

    @staticmethod
    def atr(data: pd.DataFrame, period: int = 14) -> pd.Series:
        """Calculate ATR indicator using TA-Lib."""
        high = np.asarray(data['high'], dtype=np.float64)
        low = np.asarray(data['low'], dtype=np.float64)
        close = np.asarray(data['close'], dtype=np.float64)
        atr_values = talib.ATR(high, low, close, timeperiod=period)
        return pd.Series(atr_values, index=data.index) 