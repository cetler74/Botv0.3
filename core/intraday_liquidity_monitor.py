"""
Intraday Liquidity Monitor for Scalping-Optimized Pair Selection
Provides real-time liquidity analysis for minute-level trading decisions
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import ccxt.async_support as ccxt_async
import numpy as np
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class LiquidityMetrics:
    """Real-time liquidity metrics for a trading pair"""
    symbol: str
    exchange: str
    timestamp: datetime
    
    # Volume metrics
    volume_1h: float
    volume_15m: float
    volume_5m: float
    volume_1m: float
    
    # Order book metrics
    bid_ask_spread: float
    spread_percentage: float
    order_book_depth: Dict[str, float]  # depth at different levels
    
    # Volatility metrics
    volatility_1h: float
    volatility_15m: float
    volatility_5m: float
    
    # Execution metrics
    estimated_slippage: float
    market_impact: float
    
    # Quality score (0-100)
    liquidity_score: float
    scalping_suitability: float

class IntradayLiquidityMonitor:
    """Real-time liquidity monitoring for scalping pair selection"""
    
    def __init__(self, config_manager):
        self.config_manager = config_manager
        self.metrics_cache: Dict[str, LiquidityMetrics] = {}
        self.update_interval = 60  # Update every minute
        self.history_buffer = {}  # Store historical data for trend analysis
        
    async def get_liquidity_metrics(self, exchange_name: str, symbol: str) -> Optional[LiquidityMetrics]:
        """Get real-time liquidity metrics for a specific pair"""
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
            
            # Fetch multiple timeframes of OHLCV data
            timeframes = ['1m', '5m', '15m', '1h']
            ohlcv_data = {}
            
            for tf in timeframes:
                try:
                    ohlcv = await exchange.fetch_ohlcv(symbol, tf, limit=100)
                    ohlcv_data[tf] = ohlcv
                except Exception as e:
                    logger.warning(f"Failed to fetch {tf} data for {symbol}: {e}")
                    ohlcv_data[tf] = []
            
            # Get order book
            try:
                order_book = await exchange.fetch_order_book(symbol, limit=20)
            except Exception as e:
                logger.warning(f"Failed to fetch order book for {symbol}: {e}")
                order_book = {'bids': [], 'asks': []}
            
            # Calculate volume metrics
            volume_metrics = self._calculate_volume_metrics(ohlcv_data)
            
            # Calculate spread metrics
            spread_metrics = self._calculate_spread_metrics(order_book)
            
            # Calculate volatility metrics
            volatility_metrics = self._calculate_volatility_metrics(ohlcv_data)
            
            # Calculate execution metrics
            execution_metrics = self._calculate_execution_metrics(order_book, volume_metrics)
            
            # Calculate quality scores
            liquidity_score = self._calculate_liquidity_score(volume_metrics, spread_metrics)
            scalping_suitability = self._calculate_scalping_suitability(
                volume_metrics, spread_metrics, volatility_metrics, execution_metrics
            )
            
            metrics = LiquidityMetrics(
                symbol=symbol,
                exchange=exchange_name,
                timestamp=datetime.utcnow(),
                **volume_metrics,
                **spread_metrics,
                **volatility_metrics,
                **execution_metrics,
                liquidity_score=liquidity_score,
                scalping_suitability=scalping_suitability
            )
            
            await exchange.close()
            return metrics
            
        except Exception as e:
            logger.error(f"Error calculating liquidity metrics for {symbol}: {e}")
            return None
    
    def _calculate_volume_metrics(self, ohlcv_data: Dict) -> Dict:
        """Calculate volume metrics across different timeframes"""
        metrics = {}
        
        for tf, data in ohlcv_data.items():
            if not data:
                metrics[f'volume_{tf}'] = 0.0
                continue
                
            # Use the most recent volume
            recent_volume = data[-1][5] if len(data) > 0 else 0.0
            metrics[f'volume_{tf}'] = float(recent_volume)
        
        return metrics
    
    def _calculate_spread_metrics(self, order_book: Dict) -> Dict:
        """Calculate bid-ask spread metrics"""
        if not order_book.get('bids') or not order_book.get('asks'):
            return {
                'bid_ask_spread': float('inf'),
                'spread_percentage': float('inf'),
                'order_book_depth': {}
            }
        
        best_bid = order_book['bids'][0][0]
        best_ask = order_book['asks'][0][0]
        
        spread = best_ask - best_bid
        spread_percentage = (spread / best_bid) * 100
        
        # Calculate order book depth at different levels
        depth_levels = [0.1, 0.5, 1.0, 2.0]  # percentage from mid price
        mid_price = (best_bid + best_ask) / 2
        order_book_depth = {}
        
        for level in depth_levels:
            price_threshold = mid_price * (1 + level / 100)
            depth = sum(ask[1] for ask in order_book['asks'] if ask[0] <= price_threshold)
            order_book_depth[f'depth_{level}pct'] = depth
        
        return {
            'bid_ask_spread': spread,
            'spread_percentage': spread_percentage,
            'order_book_depth': order_book_depth
        }
    
    def _calculate_volatility_metrics(self, ohlcv_data: Dict) -> Dict:
        """Calculate volatility metrics across timeframes"""
        metrics = {}
        
        for tf, data in ohlcv_data.items():
            if len(data) < 10:  # Need sufficient data
                metrics[f'volatility_{tf}'] = 0.0
                continue
            
            # Calculate ATR-based volatility
            highs = [candle[2] for candle in data[-20:]]  # Last 20 candles
            lows = [candle[3] for candle in data[-20:]]
            closes = [candle[4] for candle in data[-20:]]
            
            # True Range calculation
            true_ranges = []
            for i in range(1, len(data)):
                tr1 = highs[i] - lows[i]
                tr2 = abs(highs[i] - closes[i-1])
                tr3 = abs(lows[i] - closes[i-1])
                true_ranges.append(max(tr1, tr2, tr3))
            
            # Average True Range
            atr = np.mean(true_ranges) if true_ranges else 0
            current_price = closes[-1] if closes else 1
            
            # Volatility as percentage of price
            volatility = (atr / current_price) * 100 if current_price > 0 else 0
            metrics[f'volatility_{tf}'] = volatility
        
        return metrics
    
    def _calculate_execution_metrics(self, order_book: Dict, volume_metrics: Dict) -> Dict:
        """Calculate execution quality metrics"""
        if not order_book.get('bids') or not order_book.get('asks'):
            return {
                'estimated_slippage': float('inf'),
                'market_impact': float('inf')
            }
        
        # Estimate slippage for a typical scalping order size
        typical_order_size = 1000  # USD equivalent
        best_bid = order_book['bids'][0][0]
        best_ask = order_book['asks'][0][0]
        mid_price = (best_bid + best_ask) / 2
        
        # Calculate slippage by walking the order book
        total_bid_volume = 0
        total_ask_volume = 0
        weighted_bid_price = 0
        weighted_ask_price = 0
        
        for bid in order_book['bids']:
            if total_bid_volume >= typical_order_size:
                break
            volume_to_add = min(bid[1], typical_order_size - total_bid_volume)
            weighted_bid_price += bid[0] * volume_to_add
            total_bid_volume += volume_to_add
        
        for ask in order_book['asks']:
            if total_ask_volume >= typical_order_size:
                break
            volume_to_add = min(ask[1], typical_order_size - total_ask_volume)
            weighted_ask_price += ask[0] * volume_to_add
            total_ask_volume += volume_to_add
        
        # Calculate average execution prices
        avg_bid_price = weighted_bid_price / total_bid_volume if total_bid_volume > 0 else best_bid
        avg_ask_price = weighted_ask_price / total_ask_volume if total_ask_volume > 0 else best_ask
        
        # Slippage calculation
        bid_slippage = abs(avg_bid_price - best_bid) / best_bid * 100
        ask_slippage = abs(avg_ask_price - best_ask) / best_ask * 100
        estimated_slippage = (bid_slippage + ask_slippage) / 2
        
        # Market impact (how much the order would move the price)
        market_impact = abs(avg_ask_price - avg_bid_price) / mid_price * 100
        
        return {
            'estimated_slippage': estimated_slippage,
            'market_impact': market_impact
        }
    
    def _calculate_liquidity_score(self, volume_metrics: Dict, spread_metrics: Dict) -> float:
        """Calculate overall liquidity score (0-100)"""
        score = 0
        
        # Volume component (40% weight)
        volume_1h = volume_metrics.get('volume_1h', 0)
        volume_15m = volume_metrics.get('volume_15m', 0)
        
        # Normalize volume scores (higher is better)
        volume_score = min(100, (volume_1h / 1000000) * 20 + (volume_15m / 100000) * 20)
        score += volume_score * 0.4
        
        # Spread component (30% weight) - lower is better
        spread_pct = spread_metrics.get('spread_percentage', float('inf'))
        if spread_pct < 0.1:
            spread_score = 100
        elif spread_pct < 0.5:
            spread_score = 80
        elif spread_pct < 1.0:
            spread_score = 60
        elif spread_pct < 2.0:
            spread_score = 40
        else:
            spread_score = 0
        
        score += spread_score * 0.3
        
        # Order book depth component (30% weight)
        depth_1pct = spread_metrics.get('order_book_depth', {}).get('depth_1pct', 0)
        depth_score = min(100, (depth_1pct / 10000) * 100)  # Normalize to 10k units
        score += depth_score * 0.3
        
        return min(100, max(0, score))
    
    def _calculate_scalping_suitability(self, volume_metrics: Dict, spread_metrics: Dict, 
                                      volatility_metrics: Dict, execution_metrics: Dict) -> float:
        """Calculate scalping suitability score (0-100)"""
        score = 0
        
        # Liquidity score (40% weight)
        liquidity_score = self._calculate_liquidity_score(volume_metrics, spread_metrics)
        score += liquidity_score * 0.4
        
        # Volatility component (30% weight) - moderate volatility is best for scalping
        volatility_15m = volatility_metrics.get('volatility_15m', 0)
        if 0.5 <= volatility_15m <= 3.0:  # Sweet spot for scalping
            volatility_score = 100
        elif 0.2 <= volatility_15m <= 5.0:  # Acceptable range
            volatility_score = 70
        elif volatility_15m < 0.2:  # Too low volatility
            volatility_score = 30
        else:  # Too high volatility
            volatility_score = 20
        
        score += volatility_score * 0.3
        
        # Execution quality (30% weight)
        slippage = execution_metrics.get('estimated_slippage', float('inf'))
        if slippage < 0.1:
            execution_score = 100
        elif slippage < 0.3:
            execution_score = 80
        elif slippage < 0.5:
            execution_score = 60
        else:
            execution_score = 20
        
        score += execution_score * 0.3
        
        return min(100, max(0, score))
    
    async def get_top_scalping_pairs(self, exchange_name: str, base_currency: str, 
                                   num_pairs: int = 15) -> List[Tuple[str, float]]:
        """Get top pairs ranked by scalping suitability"""
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
            
            # Get liquidity metrics for all candidate pairs
            pair_scores = []
            for symbol in candidate_pairs[:50]:  # Limit to top 50 by volume for efficiency
                metrics = await self.get_liquidity_metrics(exchange_name, symbol)
                if metrics and metrics.scalping_suitability > 50:  # Only consider suitable pairs
                    pair_scores.append((symbol, metrics.scalping_suitability))
            
            # Sort by scalping suitability and return top pairs
            pair_scores.sort(key=lambda x: x[1], reverse=True)
            return pair_scores[:num_pairs]
            
        except Exception as e:
            logger.error(f"Error getting top scalping pairs for {exchange_name}: {e}")
            return []
