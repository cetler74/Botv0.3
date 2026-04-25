"""
Advanced Volatility Filter for Scalping-Optimized Pair Selection
Provides real-time volatility analysis with moderate volatility requirements for scalping
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import ccxt.async_support as ccxt_async
import numpy as np
from dataclasses import dataclass
from collections import deque

logger = logging.getLogger(__name__)

@dataclass
class VolatilityMetrics:
    """Comprehensive volatility analysis metrics"""
    symbol: str
    exchange: str
    timestamp: datetime
    
    # Current volatility metrics
    current_volatility_1m: float
    current_volatility_5m: float
    current_volatility_15m: float
    current_volatility_1h: float
    
    # ATR-based volatility
    atr_14: float
    atr_21: float
    atr_percentage: float
    
    # Volatility trends
    volatility_trend_1h: float  # 1-hour trend
    volatility_trend_4h: float  # 4-hour trend
    volatility_momentum: float  # Recent momentum
    
    # Volatility quality indicators
    volatility_consistency: float  # How consistent volatility is
    volatility_predictability: float  # How predictable volatility patterns are
    volatility_stability: float  # How stable volatility is
    
    # Scalping suitability
    scalping_volatility_score: float  # 0-100 score for scalping suitability

class VolatilityFilter:
    """Real-time volatility analysis for optimal scalping pair selection"""
    
    def __init__(self, config_manager):
        self.config_manager = config_manager
        self.volatility_history: Dict[str, deque] = {}  # Store historical volatility data
        self.max_history_size = 2000  # Keep more data for volatility analysis
        
        # Scalping volatility thresholds
        self.min_volatility = 0.2  # Minimum volatility for scalping opportunities
        self.max_volatility = 5.0  # Maximum volatility for risk management
        self.optimal_volatility_range = (0.5, 3.0)  # Sweet spot for scalping
        
    async def analyze_volatility(self, exchange_name: str, symbol: str) -> Optional[VolatilityMetrics]:
        """Perform comprehensive volatility analysis for a trading pair"""
        try:
            # Get exchange instance
            exchange_config = await self.config_manager.get_exchange_config(exchange_name)
            exchange_class = getattr(ccxt_async, exchange_name.lower())
            exchange = exchange_class({
                'apiKey': exchange_config.get('api_key', ''),
                'secret': exchange_config.get('secret', ''),
                'enableRateLimit': True,
                'sandbox': exchange_config.get('sandbox', False)
            })
            
            # Fetch OHLCV data for multiple timeframes
            timeframes = ['1m', '5m', '15m', '1h']
            ohlcv_data = {}
            
            for tf in timeframes:
                try:
                    ohlcv = await exchange.fetch_ohlcv(symbol, tf, limit=100)
                    ohlcv_data[tf] = ohlcv
                except Exception as e:
                    logger.warning(f"Failed to fetch {tf} data for {symbol}: {e}")
                    ohlcv_data[tf] = []
            
            # Calculate current volatility metrics
            current_metrics = self._calculate_current_volatility_metrics(ohlcv_data)
            
            # Calculate ATR-based volatility
            atr_metrics = self._calculate_atr_volatility(ohlcv_data)
            
            # Update historical data
            self._update_volatility_history(symbol, current_metrics['current_volatility_15m'])
            
            # Calculate volatility trends
            trend_metrics = self._calculate_volatility_trends(symbol, ohlcv_data)
            
            # Calculate volatility quality indicators
            quality_metrics = self._calculate_volatility_quality_metrics(symbol, current_metrics, trend_metrics)
            
            # Calculate scalping suitability score
            scalping_score = self._calculate_scalping_volatility_score(
                current_metrics, atr_metrics, trend_metrics, quality_metrics
            )
            
            metrics = VolatilityMetrics(
                symbol=symbol,
                exchange=exchange_name,
                timestamp=datetime.utcnow(),
                **current_metrics,
                **atr_metrics,
                **trend_metrics,
                **quality_metrics,
                scalping_volatility_score=scalping_score
            )
            
            await exchange.close()
            return metrics
            
        except Exception as e:
            logger.error(f"Error analyzing volatility for {symbol}: {e}")
            return None
    
    def _calculate_current_volatility_metrics(self, ohlcv_data: Dict) -> Dict:
        """Calculate current volatility across different timeframes"""
        metrics = {}
        
        for tf, data in ohlcv_data.items():
            if len(data) < 20:  # Need sufficient data
                metrics[f'current_volatility_{tf}'] = 0.0
                continue
            
            # Calculate price changes
            closes = [candle[4] for candle in data[-20:]]  # Last 20 candles
            
            # Calculate returns
            returns = []
            for i in range(1, len(closes)):
                if closes[i-1] > 0:
                    ret = (closes[i] - closes[i-1]) / closes[i-1]
                    returns.append(ret)
            
            if returns:
                # Calculate volatility as standard deviation of returns
                volatility = np.std(returns) * 100  # Convert to percentage
                metrics[f'current_volatility_{tf}'] = volatility
            else:
                metrics[f'current_volatility_{tf}'] = 0.0
        
        return metrics
    
    def _calculate_atr_volatility(self, ohlcv_data: Dict) -> Dict:
        """Calculate Average True Range (ATR) based volatility"""
        # Use 1h data for ATR calculation
        data = ohlcv_data.get('1h', [])
        if len(data) < 25:  # Need sufficient data for ATR
            return {
                'atr_14': 0.0,
                'atr_21': 0.0,
                'atr_percentage': 0.0
            }
        
        # Calculate True Range for each period
        true_ranges = []
        for i in range(1, len(data)):
            high = data[i][2]
            low = data[i][3]
            prev_close = data[i-1][4]
            
            tr1 = high - low
            tr2 = abs(high - prev_close)
            tr3 = abs(low - prev_close)
            
            true_ranges.append(max(tr1, tr2, tr3))
        
        # Calculate ATR for different periods
        atr_14 = np.mean(true_ranges[-14:]) if len(true_ranges) >= 14 else 0.0
        atr_21 = np.mean(true_ranges[-21:]) if len(true_ranges) >= 21 else 0.0
        
        # Calculate ATR as percentage of current price
        current_price = data[-1][4]
        atr_percentage = (atr_14 / current_price) * 100 if current_price > 0 else 0.0
        
        return {
            'atr_14': atr_14,
            'atr_21': atr_21,
            'atr_percentage': atr_percentage
        }
    
    def _update_volatility_history(self, symbol: str, volatility_15m: float):
        """Update historical volatility data"""
        if symbol not in self.volatility_history:
            self.volatility_history[symbol] = deque(maxlen=self.max_history_size)
        
        self.volatility_history[symbol].append({
            'timestamp': datetime.utcnow(),
            'volatility_15m': volatility_15m
        })
    
    def _calculate_volatility_trends(self, symbol: str, ohlcv_data: Dict) -> Dict:
        """Calculate volatility trends and momentum"""
        # Calculate 1-hour trend
        data_1h = ohlcv_data.get('1h', [])
        if len(data_1h) >= 24:  # 24 hours of data
            recent_vol = self._calculate_volatility_for_period(data_1h[-12:])  # Last 12 hours
            older_vol = self._calculate_volatility_for_period(data_1h[-24:-12])  # Previous 12 hours
            
            if older_vol > 0:
                volatility_trend_1h = ((recent_vol - older_vol) / older_vol) * 100
            else:
                volatility_trend_1h = 0.0
        else:
            volatility_trend_1h = 0.0
        
        # Calculate 4-hour trend
        if len(data_1h) >= 48:  # 48 hours of data
            recent_vol = self._calculate_volatility_for_period(data_1h[-24:])  # Last 24 hours
            older_vol = self._calculate_volatility_for_period(data_1h[-48:-24])  # Previous 24 hours
            
            if older_vol > 0:
                volatility_trend_4h = ((recent_vol - older_vol) / older_vol) * 100
            else:
                volatility_trend_4h = 0.0
        else:
            volatility_trend_4h = 0.0
        
        # Calculate volatility momentum (recent acceleration)
        if symbol in self.volatility_history and len(self.volatility_history[symbol]) >= 10:
            recent_volatilities = [h['volatility_15m'] for h in list(self.volatility_history[symbol])[-10:]]
            if len(recent_volatilities) >= 5:
                recent_avg = np.mean(recent_volatilities[-5:])
                older_avg = np.mean(recent_volatilities[-10:-5])
                
                if older_avg > 0:
                    volatility_momentum = ((recent_avg - older_avg) / older_avg) * 100
                else:
                    volatility_momentum = 0.0
            else:
                volatility_momentum = 0.0
        else:
            volatility_momentum = 0.0
        
        return {
            'volatility_trend_1h': volatility_trend_1h,
            'volatility_trend_4h': volatility_trend_4h,
            'volatility_momentum': volatility_momentum
        }
    
    def _calculate_volatility_for_period(self, data: List) -> float:
        """Calculate volatility for a specific period of OHLCV data"""
        if len(data) < 2:
            return 0.0
        
        closes = [candle[4] for candle in data]
        returns = []
        
        for i in range(1, len(closes)):
            if closes[i-1] > 0:
                ret = (closes[i] - closes[i-1]) / closes[i-1]
                returns.append(ret)
        
        if returns:
            return np.std(returns) * 100  # Convert to percentage
        return 0.0
    
    def _calculate_volatility_quality_metrics(self, symbol: str, current_metrics: Dict, 
                                            trend_metrics: Dict) -> Dict:
        """Calculate volatility quality indicators"""
        current_vol_15m = current_metrics['current_volatility_15m']
        
        # Volatility consistency (how consistent volatility is over time)
        if symbol in self.volatility_history and len(self.volatility_history[symbol]) >= 20:
            recent_volatilities = [h['volatility_15m'] for h in list(self.volatility_history[symbol])[-20:]]
            volatility_std = np.std(recent_volatilities)
            volatility_mean = np.mean(recent_volatilities)
            
            if volatility_mean > 0:
                volatility_consistency = max(0, 100 - (volatility_std / volatility_mean) * 100)
            else:
                volatility_consistency = 0.0
        else:
            volatility_consistency = 50.0  # Default moderate score
        
        # Volatility predictability (how predictable volatility patterns are)
        # This is based on trend stability and momentum consistency
        trend_stability = 100 - abs(trend_metrics['volatility_trend_1h'])
        momentum_consistency = 100 - abs(trend_metrics['volatility_momentum'])
        volatility_predictability = (trend_stability + momentum_consistency) / 2
        
        # Volatility stability (how stable volatility is within acceptable range)
        if self.optimal_volatility_range[0] <= current_vol_15m <= self.optimal_volatility_range[1]:
            volatility_stability = 100
        elif self.min_volatility <= current_vol_15m <= self.max_volatility:
            # Linear interpolation within acceptable range
            if current_vol_15m < self.optimal_volatility_range[0]:
                ratio = (current_vol_15m - self.min_volatility) / (self.optimal_volatility_range[0] - self.min_volatility)
            else:
                ratio = (self.max_volatility - current_vol_15m) / (self.max_volatility - self.optimal_volatility_range[1])
            volatility_stability = ratio * 100
        else:
            volatility_stability = 0.0
        
        return {
            'volatility_consistency': volatility_consistency,
            'volatility_predictability': volatility_predictability,
            'volatility_stability': volatility_stability
        }
    
    def _calculate_scalping_volatility_score(self, current_metrics: Dict, atr_metrics: Dict,
                                           trend_metrics: Dict, quality_metrics: Dict) -> float:
        """Calculate overall scalping suitability score based on volatility analysis"""
        score = 0.0
        
        # Current volatility suitability (40% weight)
        current_vol = current_metrics['current_volatility_15m']
        if self.optimal_volatility_range[0] <= current_vol <= self.optimal_volatility_range[1]:
            volatility_score = 100
        elif self.min_volatility <= current_vol <= self.max_volatility:
            # Linear scoring within acceptable range
            if current_vol < self.optimal_volatility_range[0]:
                ratio = (current_vol - self.min_volatility) / (self.optimal_volatility_range[0] - self.min_volatility)
            else:
                ratio = (self.max_volatility - current_vol) / (self.max_volatility - self.optimal_volatility_range[1])
            volatility_score = 50 + (ratio * 50)
        else:
            volatility_score = 0
        
        score += volatility_score * 0.4
        
        # ATR-based volatility (20% weight)
        atr_percentage = atr_metrics['atr_percentage']
        if 0.5 <= atr_percentage <= 3.0:
            atr_score = 100
        elif 0.2 <= atr_percentage <= 5.0:
            atr_score = 70
        else:
            atr_score = 30
        
        score += atr_score * 0.2
        
        # Volatility stability (20% weight)
        stability_score = quality_metrics['volatility_stability']
        score += stability_score * 0.2
        
        # Volatility predictability (20% weight)
        predictability_score = quality_metrics['volatility_predictability']
        score += predictability_score * 0.2
        
        return min(100, max(0, score))
    
    async def get_volatility_ranked_pairs(self, exchange_name: str, base_currency: str, 
                                        num_pairs: int = 15) -> List[Tuple[str, float]]:
        """Get pairs ranked by volatility analysis for scalping suitability"""
        try:
            # Get all available pairs for the exchange
            exchange_config = await self.config_manager.get_exchange_config(exchange_name)
            exchange_class = getattr(ccxt_async, exchange_name.lower())
            exchange = exchange_class({
                'apiKey': exchange_config.get('api_key', ''),
                'secret': exchange_config.get('secret', ''),
                'enableRateLimit': True,
                'sandbox': exchange_config.get('sandbox', False)
            })
            
            await exchange.load_markets()
            
            # Filter pairs by base currency and active status
            candidate_pairs = []
            for symbol, market in exchange.markets.items():
                if (market.get('active') and 
                    market.get('quote') == base_currency and
                    market.get('type') == 'spot'):
                    candidate_pairs.append(symbol)
            
            await exchange.close()
            
            # Analyze volatility for all candidate pairs
            pair_scores = []
            for symbol in candidate_pairs[:50]:  # Limit for efficiency
                volatility_metrics = await self.analyze_volatility(exchange_name, symbol)
                if (volatility_metrics and 
                    volatility_metrics.scalping_volatility_score > 60):  # Only consider suitable pairs
                    pair_scores.append((symbol, volatility_metrics.scalping_volatility_score))
            
            # Sort by volatility score and return top pairs
            pair_scores.sort(key=lambda x: x[1], reverse=True)
            return pair_scores[:num_pairs]
            
        except Exception as e:
            logger.error(f"Error getting volatility-ranked pairs for {exchange_name}: {e}")
            return []
    
    def is_volatility_suitable_for_scalping(self, volatility_metrics: VolatilityMetrics) -> bool:
        """Check if a pair's volatility is suitable for scalping"""
        return (self.min_volatility <= volatility_metrics.current_volatility_15m <= self.max_volatility and
                volatility_metrics.scalping_volatility_score >= 70 and
                volatility_metrics.volatility_stability >= 60)
