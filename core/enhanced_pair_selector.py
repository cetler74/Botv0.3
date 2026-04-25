"""
Enhanced Scalping-Optimized Pair Selector
Integrates all advanced systems for optimal scalping pair selection
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import numpy as np
from dataclasses import dataclass

from .intraday_liquidity_monitor import IntradayLiquidityMonitor, LiquidityMetrics
from .spread_analysis_engine import SpreadAnalysisEngine, SpreadMetrics
from .volatility_filter import VolatilityFilter, VolatilityMetrics
from .performance_metrics_tracker import PerformanceMetricsTracker, PairPerformanceMetrics

logger = logging.getLogger(__name__)

@dataclass
class ScalpingPairScore:
    """Comprehensive scalping suitability score for a trading pair"""
    symbol: str
    exchange: str
    timestamp: datetime
    
    # Individual component scores (0-100)
    liquidity_score: float
    spread_score: float
    volatility_score: float
    performance_score: float
    
    # Weighted overall score
    overall_score: float
    
    # Detailed metrics
    liquidity_metrics: Optional[LiquidityMetrics]
    spread_metrics: Optional[SpreadMetrics]
    volatility_metrics: Optional[VolatilityMetrics]
    performance_metrics: Optional[PairPerformanceMetrics]
    
    # Scalping suitability assessment
    is_suitable_for_scalping: bool
    suitability_reasons: List[str]
    risk_factors: List[str]

class EnhancedPairSelector:
    """Advanced pair selector optimized for scalping strategies"""
    
    def __init__(self, config_manager):
        self.config_manager = config_manager
        
        # Initialize all analysis engines
        self.liquidity_monitor = IntradayLiquidityMonitor(config_manager)
        self.spread_analyzer = SpreadAnalysisEngine(config_manager)
        self.volatility_filter = VolatilityFilter(config_manager)
        self.performance_tracker = PerformanceMetricsTracker(config_manager)
        
        # Configuration will be loaded asynchronously
        self.pair_config = {}
        self.scoring_weights = {}
        self.selection_criteria = {}
        
        # Cache for performance
        self.score_cache: Dict[str, ScalpingPairScore] = {}
        self.cache_ttl = 300  # 5 minutes cache TTL
    
    async def _load_config_from_service(self):
        """Load configuration from config service"""
        try:
            if hasattr(self.config_manager, 'get_pair_selector_config'):
                if asyncio.iscoroutinefunction(self.config_manager.get_pair_selector_config):
                    self.pair_config = await self.config_manager.get_pair_selector_config()
                else:
                    self.pair_config = self.config_manager.get_pair_selector_config()
            else:
                # Fallback to hardcoded config if method doesn't exist
                self.pair_config = {
                    'update_interval_minutes': 15,
                    'scoring_weights': {
                        'liquidity_score': 0.3,
                        'spread_tightness': 0.25,
                        'volatility_suitability': 0.2,
                        'performance_metrics': 0.15,
                        'volume_24h': 0.1
                    },
                    'selection_criteria': {
                        'min_volume_1h': 100000,
                        'min_volume_15m': 25000,
                        'min_volume_5m': 10000,
                        'min_order_book_depth': 50000,
                        'max_spread_percentage': 0.5,
                        'max_spread_1h_avg': 0.3,
                        'min_spread_consistency': 0.7,
                        'min_volatility_15m': 0.5,
                        'max_volatility_15m': 3.0,
                        'optimal_volatility_range': [1.0, 2.5],
                        'min_volatility_consistency': 0.6,
                        'max_slippage_threshold': 0.1,
                        'min_execution_success_rate': 0.95,
                        'max_execution_time': 2.0,
                        'min_win_rate': 0.6,
                        'min_profit_factor': 1.2,
                        'max_drawdown': 0.15
                    }
                }
            
            self.scoring_weights = self.pair_config.get('scoring_weights', {})
            self.selection_criteria = self.pair_config.get('selection_criteria', {})
            logger.info("✅ Enhanced pair selector configuration loaded successfully")
            
        except Exception as e:
            logger.error(f"❌ Failed to load configuration: {e}")
            # Use fallback configuration
            self.pair_config = {
                'update_interval_minutes': 15,
                'scoring_weights': {
                    'liquidity_score': 0.3,
                    'spread_tightness': 0.25,
                    'volatility_suitability': 0.2,
                    'performance_metrics': 0.15,
                    'volume_24h': 0.1
                },
                'selection_criteria': {
                    'min_volume_1h': 100000,
                    'min_volume_15m': 25000,
                    'min_volume_5m': 10000,
                    'min_order_book_depth': 50000,
                    'max_spread_percentage': 0.5,
                    'max_spread_1h_avg': 0.3,
                    'min_spread_consistency': 0.7,
                    'min_volatility_15m': 0.5,
                    'max_volatility_15m': 3.0,
                    'optimal_volatility_range': [1.0, 2.5],
                    'min_volatility_consistency': 0.6,
                    'max_slippage_threshold': 0.1,
                    'min_execution_success_rate': 0.95,
                    'max_execution_time': 2.0,
                    'min_win_rate': 0.6,
                    'min_profit_factor': 1.2,
                    'max_drawdown': 0.15
                }
            }
            self.scoring_weights = self.pair_config.get('scoring_weights', {})
            self.selection_criteria = self.pair_config.get('selection_criteria', {})
        
    async def select_top_scalping_pairs(self, exchange_name: str, base_currency: str, 
                                      num_pairs: int = 15) -> List[Tuple[str, float]]:
        """Select top pairs optimized for scalping strategies"""
        try:
            # Load configuration from service if not already loaded
            if not self.pair_config:
                await self._load_config_from_service()
            logger.info(f"Starting enhanced pair selection for {exchange_name} with {base_currency}")
            
            # Get candidate pairs from exchange
            candidate_pairs = await self._get_candidate_pairs(exchange_name, base_currency)
            logger.info(f"Found {len(candidate_pairs)} candidate pairs for analysis")
            
            # Analyze each pair comprehensively
            pair_scores = []
            for symbol in candidate_pairs:
                try:
                    score = await self._analyze_pair_comprehensive(exchange_name, symbol)
                    if score and score.is_suitable_for_scalping:
                        pair_scores.append((symbol, score.overall_score))
                        logger.debug(f"{symbol}: {score.overall_score:.2f} - {score.suitability_reasons}")
                except Exception as e:
                    logger.warning(f"Error analyzing {symbol}: {e}")
                    continue
            
            # Sort by overall score and return top pairs
            pair_scores.sort(key=lambda x: x[1], reverse=True)
            selected_pairs = pair_scores[:num_pairs]
            
            logger.info(f"Selected {len(selected_pairs)} pairs for {exchange_name}: {[p[0] for p in selected_pairs]}")
            return selected_pairs
            
        except Exception as e:
            logger.error(f"Error in enhanced pair selection for {exchange_name}: {e}")
            return []
    
    async def _get_candidate_pairs(self, exchange_name: str, base_currency: str) -> List[str]:
        """Get initial candidate pairs from exchange"""
        try:
            import ccxt.async_support as ccxt_async
            
            exchange_config = await self.config_manager.get_exchange_config(exchange_name)
            exchange_class = getattr(ccxt_async, exchange_name.lower())
            exchange = exchange_class({
                'apiKey': exchange_config.get('api_key', ''),
                'secret': exchange_config.get('secret', ''),
                'enableRateLimit': True,
                'sandbox': exchange_config.get('sandbox', False)
            })
            
            await exchange.load_markets()
            
            # Filter pairs by basic criteria
            candidate_pairs = []
            for symbol, market in exchange.markets.items():
                if (market.get('active') and 
                    market.get('quote') == base_currency and
                    market.get('type') == 'spot'):
                    candidate_pairs.append(symbol)
            
            await exchange.close()
            
            # Limit to top candidates by volume for efficiency
            # Sort by volume and take top 50 for detailed analysis
            if len(candidate_pairs) > 50:
                # Quick volume-based pre-filtering
                volume_sorted = await self._quick_volume_sort(exchange_name, candidate_pairs)
                candidate_pairs = [pair[0] for pair in volume_sorted[:50]]
            
            return candidate_pairs
            
        except Exception as e:
            logger.error(f"Error getting candidate pairs for {exchange_name}: {e}")
            return []
    
    async def _quick_volume_sort(self, exchange_name: str, pairs: List[str]) -> List[Tuple[str, float]]:
        """Quick volume-based sorting for pre-filtering"""
        try:
            import ccxt.async_support as ccxt_async
            
            exchange_config = await self.config_manager.get_exchange_config(exchange_name)
            exchange_class = getattr(ccxt_async, exchange_name.lower())
            exchange = exchange_class({
                'apiKey': exchange_config.get('api_key', ''),
                'secret': exchange_config.get('secret', ''),
                'enableRateLimit': True,
                'sandbox': exchange_config.get('sandbox', False)
            })
            
            pair_volumes = []
            for symbol in pairs:
                try:
                    ticker = await exchange.fetch_ticker(symbol)
                    volume = ticker.get('quoteVolume', 0) or ticker.get('baseVolume', 0) or 0
                    pair_volumes.append((symbol, float(volume)))
                except:
                    pair_volumes.append((symbol, 0.0))
            
            await exchange.close()
            
            # Sort by volume descending
            pair_volumes.sort(key=lambda x: x[1], reverse=True)
            return pair_volumes
            
        except Exception as e:
            logger.error(f"Error in quick volume sort: {e}")
            return [(pair, 0.0) for pair in pairs]
    
    async def _analyze_pair_comprehensive(self, exchange_name: str, symbol: str) -> Optional[ScalpingPairScore]:
        """Perform comprehensive analysis of a trading pair"""
        try:
            # Check cache first
            cache_key = f"{exchange_name}_{symbol}"
            if cache_key in self.score_cache:
                cached_score = self.score_cache[cache_key]
                if (datetime.utcnow() - cached_score.timestamp).total_seconds() < self.cache_ttl:
                    return cached_score
            
            # Run all analyses in parallel for efficiency
            tasks = [
                self.liquidity_monitor.get_liquidity_metrics(exchange_name, symbol),
                self.spread_analyzer.analyze_spread(exchange_name, symbol),
                self.volatility_filter.analyze_volatility(exchange_name, symbol),
                self.performance_tracker.calculate_performance_metrics(symbol, exchange_name)
            ]
            
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            liquidity_metrics = results[0] if not isinstance(results[0], Exception) else None
            spread_metrics = results[1] if not isinstance(results[1], Exception) else None
            volatility_metrics = results[2] if not isinstance(results[2], Exception) else None
            performance_metrics = results[3] if not isinstance(results[3], Exception) else None
            
            # Calculate individual scores
            liquidity_score = liquidity_metrics.scalping_suitability if liquidity_metrics else 0.0
            spread_score = spread_metrics.scalping_spread_score if spread_metrics else 0.0
            volatility_score = volatility_metrics.scalping_volatility_score if volatility_metrics else 0.0
            performance_score = performance_metrics.scalping_suitability if performance_metrics else 50.0  # Default moderate score
            
            # Calculate weighted overall score
            overall_score = (
                liquidity_score * self.scoring_weights.get('liquidity_score', 0.25) +
                spread_score * self.scoring_weights.get('spread_tightness', 0.25) +
                volatility_score * self.scoring_weights.get('volatility_suitability', 0.20) +
                performance_score * self.scoring_weights.get('performance_metrics', 0.20) +
                50.0 * self.scoring_weights.get('volume_24h', 0.10)  # Default moderate score for volume
            )
            
            # Assess scalping suitability
            suitability_assessment = self._assess_scalping_suitability(
                liquidity_metrics, spread_metrics, volatility_metrics, performance_metrics
            )
            
            score = ScalpingPairScore(
                symbol=symbol,
                exchange=exchange_name,
                timestamp=datetime.utcnow(),
                liquidity_score=liquidity_score,
                spread_score=spread_score,
                volatility_score=volatility_score,
                performance_score=performance_score,
                overall_score=overall_score,
                liquidity_metrics=liquidity_metrics,
                spread_metrics=spread_metrics,
                volatility_metrics=volatility_metrics,
                performance_metrics=performance_metrics,
                is_suitable_for_scalping=suitability_assessment['suitable'],
                suitability_reasons=suitability_assessment['reasons'],
                risk_factors=suitability_assessment['risks']
            )
            
            # Cache the result
            self.score_cache[cache_key] = score
            
            return score
            
        except Exception as e:
            logger.error(f"Error in comprehensive analysis for {symbol}: {e}")
            return None
    
    def _assess_scalping_suitability(self, liquidity_metrics: Optional[LiquidityMetrics],
                                   spread_metrics: Optional[SpreadMetrics],
                                   volatility_metrics: Optional[VolatilityMetrics],
                                   performance_metrics: Optional[PairPerformanceMetrics]) -> Dict:
        """Assess overall scalping suitability based on all metrics"""
        suitable = True
        reasons = []
        risks = []
        
        # Liquidity assessment
        if liquidity_metrics:
            if liquidity_metrics.scalping_suitability >= 70:
                reasons.append("High liquidity suitability")
            elif liquidity_metrics.scalping_suitability >= 50:
                reasons.append("Moderate liquidity suitability")
            else:
                suitable = False
                risks.append("Low liquidity suitability")
            
            if liquidity_metrics.volume_15m < self.selection_criteria.get('min_volume_15m', 25000):
                suitable = False
                risks.append("Insufficient 15-minute volume")
        else:
            risks.append("No liquidity data available")
        
        # Spread assessment
        if spread_metrics:
            if spread_metrics.current_spread_percentage <= self.selection_criteria.get('max_spread_percentage', 0.5):
                reasons.append("Tight spreads suitable for scalping")
            else:
                suitable = False
                risks.append(f"Spread too wide: {spread_metrics.current_spread_percentage:.2f}%")
            
            if spread_metrics.scalping_spread_score >= 70:
                reasons.append("High spread quality score")
            elif spread_metrics.scalping_spread_score < 50:
                suitable = False
                risks.append("Low spread quality score")
        else:
            risks.append("No spread data available")
        
        # Volatility assessment
        if volatility_metrics:
            min_vol = self.selection_criteria.get('min_volatility_15m', 0.2)
            max_vol = self.selection_criteria.get('max_volatility_15m', 5.0)
            
            if min_vol <= volatility_metrics.current_volatility_15m <= max_vol:
                reasons.append("Volatility within optimal range for scalping")
            else:
                suitable = False
                risks.append(f"Volatility outside optimal range: {volatility_metrics.current_volatility_15m:.2f}%")
            
            if volatility_metrics.scalping_volatility_score >= 70:
                reasons.append("High volatility suitability score")
            elif volatility_metrics.scalping_volatility_score < 50:
                suitable = False
                risks.append("Low volatility suitability score")
        else:
            risks.append("No volatility data available")
        
        # Performance assessment
        if performance_metrics:
            min_win_rate = self.selection_criteria.get('min_win_rate', 60)
            min_profit_factor = self.selection_criteria.get('min_profit_factor', 1.2)
            
            if performance_metrics.win_rate >= min_win_rate:
                reasons.append(f"Good historical win rate: {performance_metrics.win_rate:.1f}%")
            else:
                risks.append(f"Low historical win rate: {performance_metrics.win_rate:.1f}%")
            
            if performance_metrics.profit_factor >= min_profit_factor:
                reasons.append(f"Good profit factor: {performance_metrics.profit_factor:.2f}")
            else:
                risks.append(f"Low profit factor: {performance_metrics.profit_factor:.2f}")
            
            if performance_metrics.scalping_suitability >= 70:
                reasons.append("High historical scalping suitability")
            elif performance_metrics.scalping_suitability < 50:
                suitable = False
                risks.append("Low historical scalping suitability")
        else:
            reasons.append("No historical performance data (new pair)")
        
        return {
            'suitable': suitable,
            'reasons': reasons,
            'risks': risks
        }
    
    async def get_pair_analysis_report(self, exchange_name: str, symbol: str) -> Optional[Dict]:
        """Get detailed analysis report for a specific pair"""
        try:
            score = await self._analyze_pair_comprehensive(exchange_name, symbol)
            if not score:
                return None
            
            return {
                'symbol': symbol,
                'exchange': exchange_name,
                'timestamp': score.timestamp.isoformat(),
                'overall_score': score.overall_score,
                'is_suitable_for_scalping': score.is_suitable_for_scalping,
                'suitability_reasons': score.suitability_reasons,
                'risk_factors': score.risk_factors,
                'component_scores': {
                    'liquidity_score': score.liquidity_score,
                    'spread_score': score.spread_score,
                    'volatility_score': score.volatility_score,
                    'performance_score': score.performance_score
                },
                'detailed_metrics': {
                    'liquidity': score.liquidity_metrics.__dict__ if score.liquidity_metrics else None,
                    'spread': score.spread_metrics.__dict__ if score.spread_metrics else None,
                    'volatility': score.volatility_metrics.__dict__ if score.volatility_metrics else None,
                    'performance': score.performance_metrics.__dict__ if score.performance_metrics else None
                }
            }
            
        except Exception as e:
            logger.error(f"Error generating analysis report for {symbol}: {e}")
            return None
    
    async def update_performance_data(self, symbol: str, exchange: str, trade_data: Dict):
        """Update performance data with new trade information"""
        try:
            await self.performance_tracker.record_trade(symbol, exchange, trade_data)
            
            # Invalidate cache for this pair
            cache_key = f"{exchange}_{symbol}"
            if cache_key in self.score_cache:
                del self.score_cache[cache_key]
                
        except Exception as e:
            logger.error(f"Error updating performance data for {symbol}: {e}")
    
    def clear_cache(self):
        """Clear the score cache"""
        self.score_cache.clear()
        logger.info("Pair selector cache cleared")
