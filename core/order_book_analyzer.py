"""
Advanced Order Book Analyzer for Scalping Pair Selection
Focuses on order book depth, liquidity, and execution quality
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import numpy as np
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class OrderBookMetrics:
    """Comprehensive order book analysis metrics"""
    symbol: str
    exchange: str
    timestamp: datetime
    
    # Basic order book metrics
    best_bid: float
    best_ask: float
    mid_price: float
    spread: float
    spread_percentage: float
    
    # Order book depth analysis
    depth_levels: int
    total_bid_volume: float
    total_ask_volume: float
    order_book_imbalance: float
    
    # Depth at different price levels
    depth_at_0_1pct: float
    depth_at_0_5pct: float
    depth_at_1pct: float
    depth_at_2pct: float
    
    # Execution quality metrics
    slippage_100_usd: float
    slippage_1000_usd: float
    slippage_5000_usd: float
    market_impact_1000_usd: float
    
    # Order book stability
    order_book_stability: float
    price_level_distribution: float
    volume_concentration: float
    
    # Scalping suitability score
    scalping_suitability_score: float
    is_suitable_for_scalping: bool
    suitability_reasons: List[str]
    risk_factors: List[str]

class OrderBookAnalyzer:
    """Advanced order book analyzer for scalping pair selection"""
    
    def __init__(self, config_manager):
        self.config_manager = config_manager
        self.order_book_cache: Dict[str, OrderBookMetrics] = {}
        self.cache_ttl = 60  # 1 minute cache TTL
        
    async def analyze_order_book(self, exchange_name: str, symbol: str) -> Optional[OrderBookMetrics]:
        """Perform comprehensive order book analysis for a trading pair"""
        try:
            import ccxt.async_support as ccxt_async
            
            # Get exchange configuration
            exchange_config = await self.config_manager.get_exchange_config(exchange_name)
            exchange_class = getattr(ccxt_async, exchange_name.lower())
            exchange = exchange_class({
                'apiKey': exchange_config.get('api_key', ''),
                'secret': exchange_config.get('secret', ''),
                'enableRateLimit': True,
                'sandbox': exchange_config.get('sandbox', False)
            })
            
            # Fetch deep order book (100 levels)
            order_book = await exchange.fetch_order_book(symbol, limit=100)
            await exchange.close()
            
            if not order_book.get('bids') or not order_book.get('asks'):
                logger.warning(f"No order book data available for {symbol}")
                return None
            
            # Calculate comprehensive order book metrics
            metrics = self._calculate_order_book_metrics(symbol, exchange_name, order_book)
            
            # Assess scalping suitability
            self._assess_scalping_suitability(metrics)
            
            # Cache the results
            self.order_book_cache[f"{exchange_name}:{symbol}"] = metrics
            
            return metrics
            
        except Exception as e:
            logger.error(f"Error analyzing order book for {symbol} on {exchange_name}: {e}")
            return None
    
    def _calculate_order_book_metrics(self, symbol: str, exchange: str, order_book: Dict) -> OrderBookMetrics:
        """Calculate comprehensive order book metrics"""
        bids = order_book['bids']
        asks = order_book['asks']
        
        # Basic metrics
        best_bid = bids[0][0] if len(bids) > 0 and len(bids[0]) > 0 else 0
        best_ask = asks[0][0] if len(asks) > 0 and len(asks[0]) > 0 else 0
        mid_price = (best_bid + best_ask) / 2
        spread = best_ask - best_bid
        spread_percentage = (spread / mid_price) * 100
        
        # Order book depth analysis
        depth_levels = len(bids) + len(asks)
        total_bid_volume = sum(bid[1] for bid in bids if len(bid) >= 2)
        total_ask_volume = sum(ask[1] for ask in asks if len(ask) >= 2)
        order_book_imbalance = total_bid_volume / total_ask_volume if total_ask_volume > 0 else 1.0
        
        # Calculate depth at different price levels
        depth_at_0_1pct = self._calculate_depth_at_percentage(bids, asks, mid_price, 0.1)
        depth_at_0_5pct = self._calculate_depth_at_percentage(bids, asks, mid_price, 0.5)
        depth_at_1pct = self._calculate_depth_at_percentage(bids, asks, mid_price, 1.0)
        depth_at_2pct = self._calculate_depth_at_percentage(bids, asks, mid_price, 2.0)
        
        # Calculate execution quality metrics
        slippage_100_usd = self._calculate_slippage(bids, asks, 100, mid_price)
        slippage_1000_usd = self._calculate_slippage(bids, asks, 1000, mid_price)
        slippage_5000_usd = self._calculate_slippage(bids, asks, 5000, mid_price)
        market_impact_1000_usd = self._calculate_market_impact(bids, asks, 1000, mid_price)
        
        # Calculate order book stability metrics
        order_book_stability = self._calculate_order_book_stability(bids, asks)
        price_level_distribution = self._calculate_price_level_distribution(bids, asks, mid_price)
        volume_concentration = self._calculate_volume_concentration(bids, asks)
        
        return OrderBookMetrics(
            symbol=symbol,
            exchange=exchange,
            timestamp=datetime.utcnow(),
            best_bid=best_bid,
            best_ask=best_ask,
            mid_price=mid_price,
            spread=spread,
            spread_percentage=spread_percentage,
            depth_levels=depth_levels,
            total_bid_volume=total_bid_volume,
            total_ask_volume=total_ask_volume,
            order_book_imbalance=order_book_imbalance,
            depth_at_0_1pct=depth_at_0_1pct,
            depth_at_0_5pct=depth_at_0_5pct,
            depth_at_1pct=depth_at_1pct,
            depth_at_2pct=depth_at_2pct,
            slippage_100_usd=slippage_100_usd,
            slippage_1000_usd=slippage_1000_usd,
            slippage_5000_usd=slippage_5000_usd,
            market_impact_1000_usd=market_impact_1000_usd,
            order_book_stability=order_book_stability,
            price_level_distribution=price_level_distribution,
            volume_concentration=volume_concentration,
            scalping_suitability_score=0.0,  # Will be calculated
            is_suitable_for_scalping=False,  # Will be assessed
            suitability_reasons=[],
            risk_factors=[]
        )
    
    def _calculate_depth_at_percentage(self, bids: List, asks: List, mid_price: float, percentage: float) -> float:
        """Calculate order book depth at a specific percentage from mid price"""
        if mid_price <= 0:
            return 0.0
            
        price_threshold = mid_price * (1 + percentage / 100)
        
        # Calculate depth on both sides
        bid_depth = sum(bid[1] for bid in bids if len(bid) >= 2 and bid[0] >= mid_price * (1 - percentage / 100))
        ask_depth = sum(ask[1] for ask in asks if len(ask) >= 2 and ask[0] <= price_threshold)
        
        return bid_depth + ask_depth
    
    def _calculate_slippage(self, bids: List, asks: List, order_size_usd: float, mid_price: float) -> float:
        """Calculate estimated slippage for a given order size"""
        if not bids or not asks:
            return float('inf')
        
        # Calculate slippage for buy order (walking the ask side)
        remaining_size = order_size_usd
        total_cost = 0.0
        
        for ask in asks:
            if remaining_size <= 0:
                break
            if len(ask) >= 2:
                price, volume = ask[0], ask[1]
                volume_usd = volume * price
                size_to_fill = min(remaining_size, volume_usd)
                total_cost += size_to_fill
                remaining_size -= size_to_fill
            else:
                continue
        
        if remaining_size > 0:
            return float('inf')  # Not enough liquidity
        
        # Calculate average execution price
        if order_size_usd > 0:
            avg_execution_price = total_cost / order_size_usd
            slippage = (avg_execution_price - mid_price) / mid_price * 100
            return abs(slippage)  # Return absolute value
        else:
            return 0.0
    
    def _calculate_market_impact(self, bids: List, asks: List, order_size_usd: float, mid_price: float) -> float:
        """Calculate market impact of a large order"""
        # Market impact is the price movement caused by the order
        # This is a simplified calculation based on order book depth
        
        total_liquidity = sum(bid[1] * bid[0] for bid in bids[:5]) + sum(ask[1] * ask[0] for ask in asks[:5])
        
        if total_liquidity == 0:
            return float('inf')
        
        # Market impact increases with order size relative to available liquidity
        impact_ratio = order_size_usd / total_liquidity
        market_impact = impact_ratio * 100  # Convert to percentage
        
        return market_impact
    
    def _calculate_order_book_stability(self, bids: List, asks: List) -> float:
        """Calculate order book stability based on level distribution"""
        if not bids or not asks:
            return 0.0
        
        # Stability is based on how evenly distributed the orders are
        bid_levels = len(bids)
        ask_levels = len(asks)
        
        # More levels = more stable
        stability = min(100, (bid_levels + ask_levels) / 2)
        
        return stability
    
    def _calculate_price_level_distribution(self, bids: List, asks: List, mid_price: float) -> float:
        """Calculate how well distributed the price levels are"""
        if not bids or not asks:
            return 0.0
        
        # Calculate price range coverage
        min_bid = min(bid[0] for bid in bids)
        max_ask = max(ask[0] for ask in asks)
        
        price_range = max_ask - min_bid
        if price_range == 0:
            return 0.0
        
        # Good distribution means orders are spread across the price range
        distribution_score = min(100, (price_range / mid_price) * 1000)
        
        return distribution_score
    
    def _calculate_volume_concentration(self, bids: List, asks: List) -> float:
        """Calculate volume concentration (lower is better for stability)"""
        if not bids or not asks:
            return 100.0
        
        # Calculate what percentage of volume is in the top 5 levels
        total_bid_volume = sum(bid[1] for bid in bids if len(bid) >= 2)
        total_ask_volume = sum(ask[1] for ask in asks if len(ask) >= 2)
        
        top5_bid_volume = sum(bid[1] for bid in bids[:5] if len(bid) >= 2)
        top5_ask_volume = sum(ask[1] for ask in asks[:5] if len(ask) >= 2)
        
        if total_bid_volume == 0 or total_ask_volume == 0:
            return 100.0
        
        bid_concentration = (top5_bid_volume / total_bid_volume) * 100
        ask_concentration = (top5_ask_volume / total_ask_volume) * 100
        
        # Average concentration (lower is better)
        avg_concentration = (bid_concentration + ask_concentration) / 2
        
        return avg_concentration
    
    def _assess_scalping_suitability(self, metrics: OrderBookMetrics):
        """Assess if the order book is suitable for scalping"""
        suitability_reasons = []
        risk_factors = []
        score = 0.0
        
        # Spread analysis (30% weight)
        if metrics.spread_percentage <= 0.1:
            score += 30
            suitability_reasons.append("Excellent spread (<0.1%)")
        elif metrics.spread_percentage <= 0.3:
            score += 25
            suitability_reasons.append("Good spread (<0.3%)")
        elif metrics.spread_percentage <= 0.5:
            score += 20
            suitability_reasons.append("Acceptable spread (<0.5%)")
        else:
            risk_factors.append(f"Wide spread ({metrics.spread_percentage:.2f}%)")
        
        # Order book depth (25% weight)
        if metrics.depth_at_0_1pct >= 50000:
            score += 25
            suitability_reasons.append("Excellent depth at 0.1%")
        elif metrics.depth_at_0_1pct >= 20000:
            score += 20
            suitability_reasons.append("Good depth at 0.1%")
        elif metrics.depth_at_0_1pct >= 10000:
            score += 15
            suitability_reasons.append("Acceptable depth at 0.1%")
        else:
            risk_factors.append(f"Low depth at 0.1% ({metrics.depth_at_0_1pct:.0f})")
        
        # Order book stability (20% weight)
        if metrics.order_book_stability >= 80:
            score += 20
            suitability_reasons.append("High order book stability")
        elif metrics.order_book_stability >= 60:
            score += 15
            suitability_reasons.append("Good order book stability")
        elif metrics.order_book_stability >= 40:
            score += 10
            suitability_reasons.append("Moderate order book stability")
        else:
            risk_factors.append(f"Low order book stability ({metrics.order_book_stability:.0f})")
        
        # Slippage analysis (15% weight)
        if metrics.slippage_1000_usd <= 0.05:
            score += 15
            suitability_reasons.append("Excellent slippage for $1000 orders")
        elif metrics.slippage_1000_usd <= 0.1:
            score += 12
            suitability_reasons.append("Good slippage for $1000 orders")
        elif metrics.slippage_1000_usd <= 0.2:
            score += 8
            suitability_reasons.append("Acceptable slippage for $1000 orders")
        else:
            risk_factors.append(f"High slippage for $1000 orders ({metrics.slippage_1000_usd:.2f}%)")
        
        # Order book imbalance (10% weight)
        if 0.8 <= metrics.order_book_imbalance <= 1.2:
            score += 10
            suitability_reasons.append("Balanced order book")
        elif 0.6 <= metrics.order_book_imbalance <= 1.5:
            score += 7
            suitability_reasons.append("Moderately balanced order book")
        else:
            risk_factors.append(f"Imbalanced order book (ratio: {metrics.order_book_imbalance:.2f})")
        
        # Update metrics
        metrics.scalping_suitability_score = score
        metrics.is_suitable_for_scalping = score >= 70
        metrics.suitability_reasons = suitability_reasons
        metrics.risk_factors = risk_factors
    
    async def get_order_book_quality_ranking(self, exchange_name: str, symbols: List[str]) -> List[Tuple[str, float]]:
        """Rank symbols by order book quality for scalping"""
        rankings = []
        
        for symbol in symbols:
            metrics = await self.analyze_order_book(exchange_name, symbol)
            if metrics and metrics.is_suitable_for_scalping:
                rankings.append((symbol, metrics.scalping_suitability_score))
        
        # Sort by score (highest first)
        rankings.sort(key=lambda x: x[1], reverse=True)
        
        return rankings
