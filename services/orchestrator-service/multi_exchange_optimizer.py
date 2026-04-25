"""
Multi-Exchange Optimization Framework

This module provides intelligent multi-exchange optimization for the trading bot's
trailing stop system, including exchange-specific performance tuning, latency
optimization, and dynamic exchange selection based on market conditions.

Key Features:
- Exchange-specific performance profiling and optimization
- Dynamic exchange selection based on latency and reliability
- Intelligent order routing for optimal execution
- Real-time performance monitoring per exchange
- Adaptive retry policies per exchange
- Exchange-specific parameter tuning

Author: Claude Code  
Created: 2025-08-30
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple
from enum import Enum
import json
import httpx
from dataclasses import dataclass, asdict
from collections import defaultdict, deque
import statistics
import time

logger = logging.getLogger(__name__)

class ExchangeStatus(Enum):
    """Exchange operational status"""
    OPTIMAL = "optimal"
    DEGRADED = "degraded" 
    MAINTENANCE = "maintenance"
    UNAVAILABLE = "unavailable"

class OptimizationMetric(Enum):
    """Metrics used for optimization"""
    LATENCY = "latency"
    SUCCESS_RATE = "success_rate"
    FILL_RATE = "fill_rate"
    SLIPPAGE = "slippage"
    FEES = "fees"
    LIQUIDITY = "liquidity"

@dataclass
class ExchangePerformanceMetrics:
    """Performance metrics for an exchange"""
    exchange: str
    avg_latency: float = 0.0
    p95_latency: float = 0.0
    success_rate: float = 100.0
    order_fill_rate: float = 100.0
    avg_slippage: float = 0.0
    uptime: float = 100.0
    error_count: int = 0
    last_error: Optional[datetime] = None
    optimal_order_size: float = 0.0
    recommended_trail_distance: float = 0.0025  # 0.25% default

@dataclass
class ExchangeConfiguration:
    """Exchange-specific configuration"""
    exchange: str
    max_order_size: float
    min_order_size: float
    rate_limit_rpm: int
    supports_modify_order: bool
    trailing_stop_native: bool
    optimal_batch_size: int
    priority_score: float = 1.0

class MultiExchangeOptimizer:
    """
    Multi-exchange optimization system for trailing stops
    
    Provides intelligent exchange selection, performance optimization,
    and exchange-specific parameter tuning for optimal trading execution.
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize multi-exchange optimizer
        
        Args:
            config: Configuration dictionary
        """
        self.config = config
        
        # Exchange configurations
        self.exchange_configs = {
            'binance': ExchangeConfiguration(
                exchange='binance',
                max_order_size=1000000,  # $1M
                min_order_size=10,       # $10
                rate_limit_rpm=1200,     # 20 requests per second
                supports_modify_order=False,
                trailing_stop_native=True,
                optimal_batch_size=50,
                priority_score=1.0
            ),
            'cryptocom': ExchangeConfiguration(
                exchange='cryptocom',
                max_order_size=500000,   # $500K
                min_order_size=5,        # $5
                rate_limit_rpm=600,      # 10 requests per second
                supports_modify_order=False,
                trailing_stop_native=False,
                optimal_batch_size=25,
                priority_score=0.8
            ),
            'bybit': ExchangeConfiguration(
                exchange='bybit',
                max_order_size=2000000,  # $2M
                min_order_size=1,        # $1
                rate_limit_rpm=1800,     # 30 requests per second
                supports_modify_order=True,
                trailing_stop_native=True,
                optimal_batch_size=75,
                priority_score=0.9
            )
        }
        
        # Performance tracking
        self.performance_metrics = {}
        self.latency_history = defaultdict(lambda: deque(maxlen=100))
        self.success_history = defaultdict(lambda: deque(maxlen=100))
        self.error_history = defaultdict(lambda: deque(maxlen=50))
        
        # Optimization parameters
        self.optimization_params = {
            'binance': {
                'trail_distance': 0.0025,    # 0.25%
                'update_threshold': 0.001,    # 0.1% minimum move
                'batch_delay': 0.1,           # 100ms between batch operations
                'retry_delay': 2.0,           # 2s retry delay
                'max_concurrent': 10          # Max concurrent operations
            },
            'cryptocom': {
                'trail_distance': 0.003,      # 0.3% (slightly wider due to lower liquidity)
                'update_threshold': 0.0015,   # 0.15% minimum move
                'batch_delay': 0.2,           # 200ms between batch operations
                'retry_delay': 3.0,           # 3s retry delay
                'max_concurrent': 5           # Max concurrent operations
            },
            'bybit': {
                'trail_distance': 0.002,      # 0.2% (tighter due to native support)
                'update_threshold': 0.0005,   # 0.05% minimum move
                'batch_delay': 0.05,          # 50ms between batch operations
                'retry_delay': 1.0,           # 1s retry delay
                'max_concurrent': 15          # Max concurrent operations
            }
        }
        
        # HTTP clients
        self.http_client = httpx.AsyncClient(timeout=30.0)
        self.http_client_fast = httpx.AsyncClient(timeout=5.0)
        
        # Service URLs
        self.exchange_url = "http://exchange-service:8003"
        self.database_url = "http://database-service:8002"
        
        logger.info("⚡ Multi-Exchange Optimizer initialized")
    
    async def optimize_exchange_parameters(self, exchange: str) -> Dict[str, Any]:
        """
        Optimize parameters for a specific exchange based on performance metrics
        
        Args:
            exchange: Exchange name
            
        Returns:
            Optimized parameters for the exchange
        """
        try:
            logger.info(f"🔧 Optimizing parameters for {exchange}")
            
            # Get current performance metrics
            metrics = await self._collect_exchange_metrics(exchange)
            
            # Get base parameters
            base_params = self.optimization_params.get(exchange, {}).copy()
            optimized_params = base_params.copy()
            
            # Optimize trail distance based on success rate and slippage
            if metrics.success_rate < 95:
                # Widen trail distance if success rate is low
                optimized_params['trail_distance'] = min(
                    base_params['trail_distance'] * 1.2,
                    0.005  # Max 0.5%
                )
                logger.info(f"📈 Widened trail distance for {exchange}: {optimized_params['trail_distance']:.4f}")
            
            elif metrics.success_rate > 98 and metrics.avg_slippage < 0.001:
                # Tighten trail distance if performance is excellent
                optimized_params['trail_distance'] = max(
                    base_params['trail_distance'] * 0.9,
                    0.0015  # Min 0.15%
                )
                logger.info(f"📉 Tightened trail distance for {exchange}: {optimized_params['trail_distance']:.4f}")
            
            # Optimize update threshold based on latency
            if metrics.avg_latency > 500:  # High latency
                # Increase update threshold to reduce frequency
                optimized_params['update_threshold'] = base_params['update_threshold'] * 1.5
                logger.info(f"⏱️ Increased update threshold for {exchange} due to high latency")
            
            elif metrics.avg_latency < 100:  # Low latency
                # Decrease update threshold for more responsive updates
                optimized_params['update_threshold'] = base_params['update_threshold'] * 0.8
                logger.info(f"⚡ Decreased update threshold for {exchange} due to low latency")
            
            # Optimize retry delays based on error patterns
            recent_errors = len([e for e in self.error_history[exchange] 
                               if (datetime.utcnow() - e).total_seconds() < 3600])  # Last hour
            
            if recent_errors > 10:
                # Increase retry delay if many recent errors
                optimized_params['retry_delay'] = min(base_params['retry_delay'] * 2, 10.0)
                logger.info(f"🐌 Increased retry delay for {exchange} due to errors")
            
            # Optimize concurrency based on success rate
            if metrics.success_rate > 99:
                # Increase concurrency if performance is excellent
                optimized_params['max_concurrent'] = min(
                    base_params['max_concurrent'] * 1.2,
                    20
                )
            elif metrics.success_rate < 95:
                # Decrease concurrency if performance is poor
                optimized_params['max_concurrent'] = max(
                    base_params['max_concurrent'] * 0.8,
                    3
                )
            
            # Store optimized parameters
            self.optimization_params[exchange] = optimized_params
            
            logger.info(f"✅ Optimization complete for {exchange}")
            return optimized_params
            
        except Exception as e:
            logger.error(f"❌ Error optimizing {exchange} parameters: {e}")
            return self.optimization_params.get(exchange, {})
    
    async def select_optimal_exchange(self, trade_amount: float, 
                                    symbol: str) -> Tuple[str, float]:
        """
        Select the optimal exchange for a trade based on current conditions
        
        Args:
            trade_amount: Trade amount in USD
            symbol: Trading symbol
            
        Returns:
            Tuple of (exchange_name, confidence_score)
        """
        try:
            logger.debug(f"🎯 Selecting optimal exchange for ${trade_amount:.2f} {symbol}")
            
            exchange_scores = {}
            
            for exchange_name in ['binance', 'cryptocom', 'bybit']:
                try:
                    config = self.exchange_configs[exchange_name]
                    metrics = await self._collect_exchange_metrics(exchange_name)
                    
                    # Calculate composite score
                    score = await self._calculate_exchange_score(
                        exchange_name, config, metrics, trade_amount, symbol
                    )
                    
                    exchange_scores[exchange_name] = score
                    
                except Exception as e:
                    logger.warning(f"Error evaluating {exchange_name}: {e}")
                    exchange_scores[exchange_name] = 0.0
            
            # Select exchange with highest score
            if exchange_scores:
                best_exchange = max(exchange_scores.items(), key=lambda x: x[1])
                
                logger.info(f"📊 Exchange selection: {best_exchange[0]} (score: {best_exchange[1]:.2f})")
                return best_exchange[0], best_exchange[1]
            
            # Fallback to Binance if no scores available
            logger.warning("⚠️ No exchange scores available, defaulting to Binance")
            return 'binance', 0.5
            
        except Exception as e:
            logger.error(f"❌ Error selecting optimal exchange: {e}")
            return 'binance', 0.1  # Low confidence fallback
    
    async def _calculate_exchange_score(self, exchange: str, config: ExchangeConfiguration,
                                       metrics: ExchangePerformanceMetrics, 
                                       trade_amount: float, symbol: str) -> float:
        """Calculate composite score for an exchange"""
        try:
            score = 0.0
            
            # Size compatibility (25% weight)
            if config.min_order_size <= trade_amount <= config.max_order_size:
                score += 25.0
            elif trade_amount > config.max_order_size:
                score += 10.0  # Partial score for oversized orders
            else:
                score += 5.0   # Low score for undersized orders
            
            # Performance metrics (50% weight)
            score += (metrics.success_rate / 100.0) * 20.0  # Success rate (20%)
            score += min((200 - metrics.avg_latency) / 200.0, 1.0) * 15.0  # Latency (15%)
            score += (metrics.uptime / 100.0) * 10.0  # Uptime (10%)
            score += max(0, (100 - metrics.error_count) / 100.0) * 5.0  # Error rate (5%)
            
            # Exchange-specific features (15% weight)
            if config.trailing_stop_native:
                score += 8.0  # Native trailing stop support
            if config.supports_modify_order:
                score += 4.0  # Order modification support
            score += config.priority_score * 3.0  # Base priority
            
            # Recent performance (10% weight)
            recent_success_rate = self._get_recent_success_rate(exchange)
            score += (recent_success_rate / 100.0) * 10.0
            
            return min(score, 100.0)  # Cap at 100
            
        except Exception as e:
            logger.error(f"Error calculating score for {exchange}: {e}")
            return 0.0
    
    async def _collect_exchange_metrics(self, exchange: str) -> ExchangePerformanceMetrics:
        """Collect current performance metrics for an exchange"""
        try:
            # Get metrics from exchange service
            response = await self.http_client_fast.get(
                f"{self.exchange_url}/api/v1/exchange/{exchange}/metrics"
            )
            
            if response.status_code == 200:
                data = response.json()
                
                return ExchangePerformanceMetrics(
                    exchange=exchange,
                    avg_latency=data.get('avg_latency', 100.0),
                    p95_latency=data.get('p95_latency', 200.0),
                    success_rate=data.get('success_rate', 95.0),
                    order_fill_rate=data.get('fill_rate', 98.0),
                    avg_slippage=data.get('avg_slippage', 0.001),
                    uptime=data.get('uptime', 99.0),
                    error_count=data.get('error_count', 0),
                    last_error=datetime.fromisoformat(data['last_error']) if data.get('last_error') else None
                )
            
            # Fallback to cached/default metrics
            return self.performance_metrics.get(exchange, ExchangePerformanceMetrics(exchange=exchange))
            
        except Exception as e:
            logger.warning(f"Error collecting metrics for {exchange}: {e}")
            return ExchangePerformanceMetrics(exchange=exchange)
    
    def _get_recent_success_rate(self, exchange: str) -> float:
        """Get recent success rate for an exchange"""
        try:
            recent_successes = list(self.success_history[exchange])
            if recent_successes:
                return (sum(recent_successes) / len(recent_successes)) * 100
            return 95.0  # Default assumption
            
        except Exception:
            return 95.0
    
    async def optimize_order_routing(self, trades: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        """
        Optimize order routing across exchanges for multiple trades
        
        Args:
            trades: List of trade dictionaries
            
        Returns:
            Dictionary mapping exchange names to trade lists
        """
        try:
            logger.info(f"🚦 Optimizing order routing for {len(trades)} trades")
            
            routing_plan = defaultdict(list)
            
            for trade in trades:
                try:
                    trade_amount = abs(trade.get('quantity', 0) * trade.get('price', 0))
                    symbol = trade.get('symbol', '')
                    
                    # Select optimal exchange
                    optimal_exchange, confidence = await self.select_optimal_exchange(
                        trade_amount, symbol
                    )
                    
                    # Add to routing plan
                    routing_plan[optimal_exchange].append({
                        **trade,
                        'routing_confidence': confidence,
                        'routing_timestamp': datetime.utcnow().isoformat()
                    })
                    
                except Exception as e:
                    logger.warning(f"Error routing trade: {e}")
                    # Default to Binance
                    routing_plan['binance'].append(trade)
            
            logger.info(f"✅ Routing complete: {dict([(k, len(v)) for k, v in routing_plan.items()])}")
            return dict(routing_plan)
            
        except Exception as e:
            logger.error(f"❌ Error in order routing optimization: {e}")
            return {'binance': trades}  # Fallback to single exchange
    
    async def get_optimization_status(self) -> Dict[str, Any]:
        """Get comprehensive optimization status and recommendations"""
        try:
            status = {
                'timestamp': datetime.utcnow().isoformat(),
                'exchanges': {},
                'recommendations': []
            }
            
            for exchange in ['binance', 'cryptocom', 'bybit']:
                try:
                    config = self.exchange_configs[exchange]
                    metrics = await self._collect_exchange_metrics(exchange)
                    params = self.optimization_params.get(exchange, {})
                    
                    exchange_status = {
                        'config': asdict(config),
                        'metrics': asdict(metrics),
                        'optimized_params': params,
                        'status': self._determine_exchange_status(metrics),
                        'recent_error_count': len([e for e in self.error_history[exchange] 
                                                 if (datetime.utcnow() - e).total_seconds() < 3600])
                    }
                    
                    status['exchanges'][exchange] = exchange_status
                    
                    # Generate recommendations
                    recommendations = self._generate_recommendations(exchange, metrics, params)
                    status['recommendations'].extend(recommendations)
                    
                except Exception as e:
                    logger.error(f"Error getting status for {exchange}: {e}")
            
            return status
            
        except Exception as e:
            logger.error(f"Error getting optimization status: {e}")
            return {'error': str(e)}
    
    def _determine_exchange_status(self, metrics: ExchangePerformanceMetrics) -> ExchangeStatus:
        """Determine exchange operational status"""
        if metrics.success_rate >= 98 and metrics.avg_latency < 200 and metrics.uptime >= 99:
            return ExchangeStatus.OPTIMAL
        elif metrics.success_rate >= 90 and metrics.uptime >= 95:
            return ExchangeStatus.DEGRADED
        elif metrics.uptime < 80:
            return ExchangeStatus.MAINTENANCE
        else:
            return ExchangeStatus.UNAVAILABLE
    
    def _generate_recommendations(self, exchange: str, metrics: ExchangePerformanceMetrics, 
                                params: Dict[str, Any]) -> List[str]:
        """Generate optimization recommendations"""
        recommendations = []
        
        try:
            if metrics.success_rate < 95:
                recommendations.append(
                    f"Consider widening trail distance for {exchange} (current: {params.get('trail_distance', 0.0025):.4f})"
                )
            
            if metrics.avg_latency > 500:
                recommendations.append(
                    f"High latency detected on {exchange} ({metrics.avg_latency:.0f}ms) - consider reducing update frequency"
                )
            
            if metrics.error_count > 5:
                recommendations.append(
                    f"Elevated error count on {exchange} - investigate API stability"
                )
            
            if metrics.uptime < 99:
                recommendations.append(
                    f"Low uptime on {exchange} ({metrics.uptime:.1f}%) - consider reducing priority"
                )
                
        except Exception as e:
            logger.error(f"Error generating recommendations: {e}")
        
        return recommendations
    
    async def record_performance_event(self, exchange: str, event_type: str, 
                                     latency: Optional[float] = None,
                                     success: Optional[bool] = None,
                                     error: Optional[str] = None):
        """Record a performance event for optimization"""
        try:
            timestamp = datetime.utcnow()
            
            if latency is not None:
                self.latency_history[exchange].append(latency)
            
            if success is not None:
                self.success_history[exchange].append(1 if success else 0)
            
            if error:
                self.error_history[exchange].append(timestamp)
            
            # Trigger re-optimization if performance degradation detected
            if success is False or (latency and latency > 1000):
                logger.info(f"⚡ Performance degradation detected on {exchange}, triggering optimization")
                asyncio.create_task(self.optimize_exchange_parameters(exchange))
                
        except Exception as e:
            logger.error(f"Error recording performance event: {e}")
    
    async def cleanup(self):
        """Cleanup resources"""
        await self.http_client.aclose()
        await self.http_client_fast.aclose()
        logger.info("⚡ Multi-Exchange Optimizer cleanup completed")

# Factory function
async def create_multi_exchange_optimizer(config: Dict[str, Any]) -> MultiExchangeOptimizer:
    """
    Create and initialize MultiExchangeOptimizer
    
    Args:
        config: Configuration dictionary
        
    Returns:
        Initialized MultiExchangeOptimizer instance
    """
    optimizer = MultiExchangeOptimizer(config)
    logger.info("⚡ Multi-Exchange Optimizer created and ready")
    return optimizer