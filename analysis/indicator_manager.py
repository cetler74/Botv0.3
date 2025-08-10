import pandas as pd
import numpy as np
import talib
import logging
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timedelta
import asyncio
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

class IndicatorType(Enum):
    RSI = "rsi"
    EMA = "ema"
    SMA = "sma"
    ATR = "atr"
    BOLLINGER_BANDS = "bollinger_bands"
    MACD = "macd"
    STOCHASTIC = "stochastic"
    VWAP = "vwap"
    VOLUME_RATIO = "volume_ratio"
    ADX = "adx"

@dataclass
class IndicatorRequest:
    """Request for indicator calculation with parameters."""
    indicator_type: IndicatorType
    pair: str
    timeframe: str
    parameters: Dict[str, Any]
    required_periods: int = 0

@dataclass
class IndicatorResult:
    """Result of indicator calculation."""
    indicator_type: IndicatorType
    pair: str
    timeframe: str
    values: pd.Series
    parameters: Dict[str, Any]
    calculated_at: datetime
    cache_key: str

class IndicatorManager:
    """
    Centralized indicator manager that caches and reuses technical indicators.
    
    Benefits:
    - Eliminates redundant calculations across strategies
    - Improves performance by caching results
    - Ensures consistency across all strategies
    - Reduces memory usage through smart caching
    """
    
    def __init__(self, cache_ttl_seconds: int = 60):
        self.cache: Dict[str, IndicatorResult] = {}
        self.cache_ttl = timedelta(seconds=cache_ttl_seconds)
        self.calculation_stats = {
            'cache_hits': 0,
            'cache_misses': 0,
            'total_calculations': 0
        }
        self.logger = logging.getLogger(__name__)
        
    def _generate_cache_key(self, request: IndicatorRequest) -> str:
        """Generate unique cache key for indicator request."""
        param_str = "_".join([f"{k}_{v}" for k, v in sorted(request.parameters.items())])
        return f"{request.indicator_type.value}_{request.pair}_{request.timeframe}_{param_str}"
    
    def _is_cache_valid(self, result: IndicatorResult) -> bool:
        """Check if cached result is still valid."""
        return datetime.utcnow() - result.calculated_at < self.cache_ttl
    
    async def get_indicator(
        self, 
        indicator_type: IndicatorType,
        pair: str,
        timeframe: str,
        data: pd.DataFrame,
        parameters: Dict[str, Any]
    ) -> pd.Series:
        """
        Get indicator value, using cache if available and valid.
        
        Args:
            indicator_type: Type of indicator to calculate
            pair: Trading pair
            timeframe: Timeframe for the data
            data: OHLCV DataFrame
            parameters: Indicator-specific parameters
            
        Returns:
            pandas Series with indicator values
        """
        request = IndicatorRequest(
            indicator_type=indicator_type,
            pair=pair,
            timeframe=timeframe,
            parameters=parameters
        )
        
        cache_key = self._generate_cache_key(request)
        
        # Check cache first
        if cache_key in self.cache:
            cached_result = self.cache[cache_key]
            if self._is_cache_valid(cached_result):
                self.calculation_stats['cache_hits'] += 1
                self.logger.debug(f"Cache hit for {cache_key}")
                return cached_result.values
            else:
                # Remove expired cache entry
                del self.cache[cache_key]
        
        # Calculate indicator
        self.calculation_stats['cache_misses'] += 1
        self.calculation_stats['total_calculations'] += 1
        
        self.logger.debug(f"Calculating {indicator_type.value} for {pair}/{timeframe}")
        
        try:
            values = await self._calculate_indicator(indicator_type, data, parameters)
            
            # Cache the result
            result = IndicatorResult(
                indicator_type=indicator_type,
                pair=pair,
                timeframe=timeframe,
                values=values,
                parameters=parameters,
                calculated_at=datetime.utcnow(),
                cache_key=cache_key
            )
            
            self.cache[cache_key] = result
            return values
            
        except Exception as e:
            self.logger.error(f"Error calculating {indicator_type.value} for {pair}/{timeframe}: {str(e)}")
            raise
    
    async def _calculate_indicator(
        self, 
        indicator_type: IndicatorType, 
        data: pd.DataFrame, 
        parameters: Dict[str, Any]
    ) -> pd.Series:
        """Calculate specific indicator based on type."""
        
        if indicator_type == IndicatorType.RSI:
            return self._calculate_rsi(data, parameters.get('period', 14))
        elif indicator_type == IndicatorType.EMA:
            return self._calculate_ema(data, parameters.get('period', 20))
        elif indicator_type == IndicatorType.SMA:
            return self._calculate_sma(data, parameters.get('period', 20))
        elif indicator_type == IndicatorType.ATR:
            return self._calculate_atr(data, parameters.get('period', 14))
        elif indicator_type == IndicatorType.BOLLINGER_BANDS:
            return self._calculate_bollinger_bands(data, parameters)
        elif indicator_type == IndicatorType.MACD:
            return self._calculate_macd(data, parameters)
        elif indicator_type == IndicatorType.STOCHASTIC:
            return self._calculate_stochastic(data, parameters)
        elif indicator_type == IndicatorType.VWAP:
            return self._calculate_vwap(data, parameters.get('period', 20))
        elif indicator_type == IndicatorType.VOLUME_RATIO:
            return self._calculate_volume_ratio(data, parameters.get('period', 20))
        elif indicator_type == IndicatorType.ADX:
            return self._calculate_adx(data, parameters.get('period', 14))
        else:
            raise ValueError(f"Unsupported indicator type: {indicator_type}")
    
    def _calculate_rsi(self, data: pd.DataFrame, period: int = 14) -> pd.Series:
        """Calculate RSI indicator."""
        try:
            close = data['close'].astype(float)
            delta = close.diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
            rs = gain / loss
            rsi = 100 - (100 / (1 + rs))
            return pd.Series(rsi, index=data.index)
        except Exception as e:
            self.logger.error(f"Error calculating RSI: {str(e)}")
            return pd.Series([50] * len(data), index=data.index)
    
    def _calculate_ema(self, data: pd.DataFrame, period: int = 20) -> pd.Series:
        """Calculate Exponential Moving Average."""
        try:
            ema = data['close'].ewm(span=period).mean()
            return pd.Series(ema, index=data.index)
        except Exception as e:
            self.logger.error(f"Error calculating EMA: {str(e)}")
            return pd.Series([data['close'].mean()] * len(data), index=data.index)
    
    def _calculate_sma(self, data: pd.DataFrame, period: int = 20) -> pd.Series:
        """Calculate Simple Moving Average."""
        try:
            sma = data['close'].rolling(window=period).mean()
            return pd.Series(sma, index=data.index)
        except Exception as e:
            self.logger.error(f"Error calculating SMA: {str(e)}")
            return pd.Series([data['close'].mean()] * len(data), index=data.index)
    
    def _calculate_atr(self, data: pd.DataFrame, period: int = 14) -> pd.Series:
        """Calculate Average True Range."""
        try:
            high = data['high'].values.astype(float)
            low = data['low'].values.astype(float)
            close = data['close'].values.astype(float)
            atr_values = talib.ATR(high, low, close, timeperiod=period)
            return pd.Series(atr_values, index=data.index)
        except Exception as e:
            self.logger.error(f"Error calculating ATR: {str(e)}")
            return pd.Series([0.0] * len(data), index=data.index)
    
    def _calculate_bollinger_bands(self, data: pd.DataFrame, parameters: Dict[str, Any]) -> pd.Series:
        """Calculate Bollinger Bands position."""
        try:
            period = parameters.get('period', 20)
            std_dev = parameters.get('std_dev', 2)
            close = data['close'].values.astype(float)
            
            bb_upper, bb_middle, bb_lower = talib.BBANDS(
                close, timeperiod=period, nbdevup=std_dev, 
                nbdevdn=std_dev, matype=0
            )
            
            # Return position within bands (0-1)
            bb_position = (close - bb_lower) / (bb_upper - bb_lower)
            return pd.Series(bb_position, index=data.index)
        except Exception as e:
            self.logger.error(f"Error calculating Bollinger Bands: {str(e)}")
            return pd.Series([0.5] * len(data), index=data.index)
    
    def _calculate_macd(self, data: pd.DataFrame, parameters: Dict[str, Any]) -> pd.Series:
        """Calculate MACD line."""
        try:
            fast = parameters.get('fast', 12)
            slow = parameters.get('slow', 26)
            signal = parameters.get('signal', 9)
            
            close = data['close'].values.astype(float)
            macd, signal_line, histogram = talib.MACD(
                close, fastperiod=fast, slowperiod=slow, signalperiod=signal
            )
            
            return pd.Series(macd, index=data.index)
        except Exception as e:
            self.logger.error(f"Error calculating MACD: {str(e)}")
            return pd.Series([0.0] * len(data), index=data.index)
    
    def _calculate_stochastic(self, data: pd.DataFrame, parameters: Dict[str, Any]) -> pd.Series:
        """Calculate Stochastic Oscillator %K."""
        try:
            k_period = parameters.get('k_period', 14)
            d_period = parameters.get('d_period', 3)
            slowing = parameters.get('slowing', 3)
            
            high = data['high'].values.astype(float)
            low = data['low'].values.astype(float)
            close = data['close'].values.astype(float)
            
            slowk, slowd = talib.STOCH(
                high, low, close, fastk_period=k_period,
                slowk_period=slowing, slowk_matype=0,
                slowd_period=d_period, slowd_matype=0
            )
            
            return pd.Series(slowk, index=data.index)
        except Exception as e:
            self.logger.error(f"Error calculating Stochastic: {str(e)}")
            return pd.Series([50.0] * len(data), index=data.index)
    
    def _calculate_vwap(self, data: pd.DataFrame, period: int = 20) -> pd.Series:
        """Calculate Volume Weighted Average Price."""
        try:
            typical_price = (data['high'] + data['low'] + data['close']) / 3
            vwap = (typical_price * data['volume']).rolling(window=period).sum() / \
                   data['volume'].rolling(window=period).sum()
            return vwap
        except Exception as e:
            self.logger.error(f"Error calculating VWAP: {str(e)}")
            return pd.Series([data['close'].mean()] * len(data), index=data.index)
    
    def _calculate_volume_ratio(self, data: pd.DataFrame, period: int = 20) -> pd.Series:
        """Calculate volume ratio to average."""
        try:
            volume_avg = data['volume'].rolling(window=period).mean()
            return data['volume'] / volume_avg
        except Exception as e:
            self.logger.error(f"Error calculating volume ratio: {str(e)}")
            return pd.Series([1.0] * len(data), index=data.index)
    
    def _calculate_adx(self, data: pd.DataFrame, period: int = 14) -> pd.Series:
        """Calculate Average Directional Index."""
        try:
            high = data['high'].values.astype(float)
            low = data['low'].values.astype(float)
            close = data['close'].values.astype(float)
            return pd.Series(talib.ADX(high, low, close, timeperiod=period), index=data.index)
        except Exception as e:
            self.logger.error(f"Error calculating ADX: {str(e)}")
            return pd.Series([25.0] * len(data), index=data.index)
    
    def get_cached_indicators(self, pair: str, timeframe: str) -> Dict[str, pd.Series]:
        """Get all cached indicators for a specific pair/timeframe."""
        cached = {}
        for cache_key, result in self.cache.items():
            if result.pair == pair and result.timeframe == timeframe and self._is_cache_valid(result):
                cached[result.indicator_type.value] = result.values
        return cached
    
    def clear_cache(self, pair: Optional[str] = None, timeframe: Optional[str] = None):
        """Clear cache entries, optionally filtered by pair/timeframe."""
        if pair is None and timeframe is None:
            self.cache.clear()
            self.logger.info("Cleared all indicator cache")
        else:
            keys_to_remove = []
            for cache_key, result in self.cache.items():
                if (pair is None or result.pair == pair) and \
                   (timeframe is None or result.timeframe == timeframe):
                    keys_to_remove.append(cache_key)
            
            for key in keys_to_remove:
                del self.cache[key]
            
            self.logger.info(f"Cleared {len(keys_to_remove)} cache entries for {pair}/{timeframe}")
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache performance statistics."""
        hit_rate = (self.calculation_stats['cache_hits'] / 
                   max(1, self.calculation_stats['cache_hits'] + self.calculation_stats['cache_misses'])) * 100
        
        return {
            'cache_hits': self.calculation_stats['cache_hits'],
            'cache_misses': self.calculation_stats['cache_misses'],
            'total_calculations': self.calculation_stats['total_calculations'],
            'hit_rate_percent': hit_rate,
            'cache_size': len(self.cache),
            'cache_ttl_seconds': self.cache_ttl.total_seconds()
        }
    
    async def precalculate_common_indicators(
        self, 
        market_data: Dict[str, Dict[str, pd.DataFrame]]
    ) -> Dict[str, Dict[str, Dict[str, pd.Series]]]:
        """
        Precalculate common indicators for all pairs and timeframes.
        
        Args:
            market_data: Market data structure {pair: {timeframe: DataFrame}}
            
        Returns:
            Precalculated indicators {pair: {timeframe: {indicator: Series}}}
        """
        precalculated = {}
        
        # Common indicator configurations used by multiple strategies
        common_indicators = [
            (IndicatorType.RSI, {'period': 14}),
            (IndicatorType.EMA, {'period': 20}),
            (IndicatorType.EMA, {'period': 50}),
            (IndicatorType.ATR, {'period': 14}),
            (IndicatorType.BOLLINGER_BANDS, {'period': 20, 'std_dev': 2}),
            (IndicatorType.VOLUME_RATIO, {'period': 20}),
            (IndicatorType.ADX, {'period': 14}),
        ]
        
        for pair, timeframes in market_data.items():
            precalculated[pair] = {}
            
            for timeframe, data in timeframes.items():
                precalculated[pair][timeframe] = {}
                
                for indicator_type, parameters in common_indicators:
                    try:
                        values = await self.get_indicator(
                            indicator_type, pair, timeframe, data, parameters
                        )
                        precalculated[pair][timeframe][indicator_type.value] = values
                    except Exception as e:
                        self.logger.error(f"Error precalculating {indicator_type.value} for {pair}/{timeframe}: {str(e)}")
                        continue
        
        return precalculated 