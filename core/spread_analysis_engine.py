"""
Advanced Spread Analysis Engine for Scalping Pair Selection
Provides real-time spread analysis with sub-0.5% threshold monitoring
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
class SpreadMetrics:
    """Comprehensive spread analysis metrics"""
    symbol: str
    exchange: str
    timestamp: datetime
    
    # Current spread metrics
    current_spread: float
    current_spread_percentage: float
    mid_price: float
    
    # Historical spread analysis
    avg_spread_1h: float
    avg_spread_15m: float
    avg_spread_5m: float
    spread_volatility: float
    
    # Order book analysis
    order_book_imbalance: float  # bid/ask volume ratio
    order_book_depth_ratio: float  # depth at 0.1% vs 1%
    order_book_stability: float  # how stable the order book is
    
    # Spread quality indicators
    spread_consistency: float  # how consistent spreads are
    spread_tightness: float  # how tight spreads are relative to price
    spread_stability: float  # how stable spreads are over time
    
    # Scalping suitability
    scalping_spread_score: float  # 0-100 score for scalping suitability

class SpreadAnalysisEngine:
    """Real-time spread analysis for optimal scalping pair selection"""
    
    def __init__(self, config_manager):
        self.config_manager = config_manager
        self.spread_history: Dict[str, deque] = {}  # Store historical spread data
        self.max_history_size = 1000  # Keep last 1000 data points per pair
        
    async def analyze_spread(self, exchange_name: str, symbol: str) -> Optional[SpreadMetrics]:
        """Perform comprehensive spread analysis for a trading pair"""
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
            
            # Fetch current order book with deep levels
            order_book = await exchange.fetch_order_book(symbol, limit=100)
            
            # Calculate current spread metrics
            current_metrics = self._calculate_current_spread_metrics(order_book)
            
            # Update historical data
            self._update_spread_history(symbol, current_metrics['current_spread_percentage'])
            
            # Calculate historical spread analysis
            historical_metrics = self._calculate_historical_spread_metrics(symbol)
            
            # Calculate order book analysis
            order_book_metrics = self._calculate_order_book_metrics(order_book)
            
            # Calculate spread quality indicators
            quality_metrics = self._calculate_spread_quality_metrics(symbol, current_metrics, historical_metrics)
            
            # Calculate scalping suitability score
            scalping_score = self._calculate_scalping_spread_score(
                current_metrics, historical_metrics, order_book_metrics, quality_metrics
            )
            
            metrics = SpreadMetrics(
                symbol=symbol,
                exchange=exchange_name,
                timestamp=datetime.utcnow(),
                **current_metrics,
                **historical_metrics,
                **order_book_metrics,
                **quality_metrics,
                scalping_spread_score=scalping_score
            )
            
            await exchange.close()
            return metrics
            
        except Exception as e:
            logger.error(f"Error analyzing spread for {symbol}: {e}")
            return None
    
    def _calculate_current_spread_metrics(self, order_book: Dict) -> Dict:
        """Calculate current spread metrics from order book"""
        if not order_book.get('bids') or not order_book.get('asks'):
            return {
                'current_spread': float('inf'),
                'current_spread_percentage': float('inf'),
                'mid_price': 0.0
            }
        
        best_bid = order_book['bids'][0][0]
        best_ask = order_book['asks'][0][0]
        
        spread = best_ask - best_bid
        mid_price = (best_bid + best_ask) / 2
        spread_percentage = (spread / mid_price) * 100
        
        return {
            'current_spread': spread,
            'current_spread_percentage': spread_percentage,
            'mid_price': mid_price
        }
    
    def _update_spread_history(self, symbol: str, spread_percentage: float):
        """Update historical spread data"""
        if symbol not in self.spread_history:
            self.spread_history[symbol] = deque(maxlen=self.max_history_size)
        
        self.spread_history[symbol].append({
            'timestamp': datetime.utcnow(),
            'spread_percentage': spread_percentage
        })
    
    def _calculate_historical_spread_metrics(self, symbol: str) -> Dict:
        """Calculate historical spread analysis"""
        if symbol not in self.spread_history or len(self.spread_history[symbol]) < 2:
            return {
                'avg_spread_1h': 0.0,
                'avg_spread_15m': 0.0,
                'avg_spread_5m': 0.0,
                'spread_volatility': 0.0
            }
        
        history = self.spread_history[symbol]
        now = datetime.utcnow()
        
        # Calculate averages for different time periods
        spreads_1h = [h['spread_percentage'] for h in history 
                     if (now - h['timestamp']).total_seconds() <= 3600]
        spreads_15m = [h['spread_percentage'] for h in history 
                      if (now - h['timestamp']).total_seconds() <= 900]
        spreads_5m = [h['spread_percentage'] for h in history 
                     if (now - h['timestamp']).total_seconds() <= 300]
        
        avg_spread_1h = np.mean(spreads_1h) if spreads_1h else 0.0
        avg_spread_15m = np.mean(spreads_15m) if spreads_15m else 0.0
        avg_spread_5m = np.mean(spreads_5m) if spreads_5m else 0.0
        
        # Calculate spread volatility (standard deviation)
        spread_volatility = np.std(spreads_15m) if len(spreads_15m) > 1 else 0.0
        
        return {
            'avg_spread_1h': avg_spread_1h,
            'avg_spread_15m': avg_spread_15m,
            'avg_spread_5m': avg_spread_5m,
            'spread_volatility': spread_volatility
        }
    
    def _calculate_order_book_metrics(self, order_book: Dict) -> Dict:
        """Calculate order book depth and imbalance metrics"""
        if not order_book.get('bids') or not order_book.get('asks'):
            return {
                'order_book_imbalance': 0.0,
                'order_book_depth_ratio': 0.0,
                'order_book_stability': 0.0
            }
        
        bids = order_book['bids']
        asks = order_book['asks']
        
        # Calculate order book imbalance (bid/ask volume ratio)
        total_bid_volume = sum(bid[1] for bid in bids[:10])  # Top 10 levels
        total_ask_volume = sum(ask[1] for ask in asks[:10])
        
        if total_ask_volume > 0:
            order_book_imbalance = total_bid_volume / total_ask_volume
        else:
            order_book_imbalance = 1.0
        
        # Calculate depth ratio (depth at 0.1% vs 1% from mid price)
        best_bid = bids[0][0]
        best_ask = asks[0][0]
        mid_price = (best_bid + best_ask) / 2
        
        # Calculate depth at 0.1% and 1% levels
        depth_01_pct = 0.0
        depth_1_pct = 0.0
        
        for bid in bids:
            if bid[0] >= mid_price * 0.999:  # Within 0.1%
                depth_01_pct += bid[1]
            if bid[0] >= mid_price * 0.99:   # Within 1%
                depth_1_pct += bid[1]
        
        for ask in asks:
            if ask[0] <= mid_price * 1.001:  # Within 0.1%
                depth_01_pct += ask[1]
            if ask[0] <= mid_price * 1.01:   # Within 1%
                depth_1_pct += ask[1]
        
        order_book_depth_ratio = depth_01_pct / depth_1_pct if depth_1_pct > 0 else 0.0
        
        # Calculate order book stability (how evenly distributed the orders are)
        bid_levels = len(bids)
        ask_levels = len(asks)
        order_book_stability = min(100, (bid_levels + ask_levels) / 2)  # Normalize to 0-100
        
        return {
            'order_book_imbalance': order_book_imbalance,
            'order_book_depth_ratio': order_book_depth_ratio,
            'order_book_stability': order_book_stability
        }
    
    def _calculate_spread_quality_metrics(self, symbol: str, current_metrics: Dict, 
                                        historical_metrics: Dict) -> Dict:
        """Calculate spread quality indicators"""
        current_spread = current_metrics['current_spread_percentage']
        avg_spread_15m = historical_metrics['avg_spread_15m']
        spread_volatility = historical_metrics['spread_volatility']
        
        # Spread consistency (how close current spread is to average)
        if avg_spread_15m > 0:
            consistency_ratio = min(current_spread, avg_spread_15m) / max(current_spread, avg_spread_15m)
            spread_consistency = consistency_ratio * 100
        else:
            spread_consistency = 0.0
        
        # Spread tightness (inverse of spread percentage, normalized)
        if current_spread > 0:
            spread_tightness = max(0, 100 - (current_spread * 100))  # Lower spread = higher score
        else:
            spread_tightness = 0.0
        
        # Spread stability (inverse of volatility, normalized)
        if spread_volatility > 0:
            spread_stability = max(0, 100 - (spread_volatility * 1000))  # Lower volatility = higher score
        else:
            spread_stability = 100.0
        
        return {
            'spread_consistency': spread_consistency,
            'spread_tightness': spread_tightness,
            'spread_stability': spread_stability
        }
    
    def _calculate_scalping_spread_score(self, current_metrics: Dict, historical_metrics: Dict,
                                       order_book_metrics: Dict, quality_metrics: Dict) -> float:
        """Calculate overall scalping suitability score based on spread analysis"""
        score = 0.0
        
        # Current spread tightness (40% weight)
        current_spread = current_metrics['current_spread_percentage']
        if current_spread < 0.1:
            spread_score = 100
        elif current_spread < 0.2:
            spread_score = 90
        elif current_spread < 0.5:
            spread_score = 70
        elif current_spread < 1.0:
            spread_score = 40
        else:
            spread_score = 0
        
        score += spread_score * 0.4
        
        # Historical spread consistency (25% weight)
        consistency_score = quality_metrics['spread_consistency']
        score += consistency_score * 0.25
        
        # Order book stability (20% weight)
        stability_score = order_book_metrics['order_book_stability']
        score += stability_score * 0.2
        
        # Spread stability over time (15% weight)
        stability_over_time = quality_metrics['spread_stability']
        score += stability_over_time * 0.15
        
        return min(100, max(0, score))
    
    async def get_spread_ranked_pairs(self, exchange_name: str, base_currency: str, 
                                    num_pairs: int = 15, max_spread_threshold: float = 0.5) -> List[Tuple[str, float]]:
        """Get pairs ranked by spread analysis, filtered by maximum spread threshold"""
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
            
            # Analyze spreads for all candidate pairs
            pair_scores = []
            for symbol in candidate_pairs[:50]:  # Limit for efficiency
                spread_metrics = await self.analyze_spread(exchange_name, symbol)
                if (spread_metrics and 
                    spread_metrics.current_spread_percentage <= max_spread_threshold and
                    spread_metrics.scalping_spread_score > 60):  # Only consider good spreads
                    pair_scores.append((symbol, spread_metrics.scalping_spread_score))
            
            # Sort by spread score and return top pairs
            pair_scores.sort(key=lambda x: x[1], reverse=True)
            return pair_scores[:num_pairs]
            
        except Exception as e:
            logger.error(f"Error getting spread-ranked pairs for {exchange_name}: {e}")
            return []
    
    def is_spread_suitable_for_scalping(self, spread_metrics: SpreadMetrics) -> bool:
        """Check if a pair's spread is suitable for scalping"""
        return (spread_metrics.current_spread_percentage <= 0.5 and
                spread_metrics.scalping_spread_score >= 70 and
                spread_metrics.spread_stability >= 60)
