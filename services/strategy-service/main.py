"""
Strategy Service for the Multi-Exchange Trading Bot
Strategy analysis and signal generation
"""

import asyncio
import logging
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import importlib
import sys
import os
import httpx
from fastapi import FastAPI, HTTPException, BackgroundTasks, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import json
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST, CollectorRegistry

# Import market regime detector
sys.path.append('/app')
from strategy.market_regime_detector import MarketRegimeDetector, MarketRegime

# Custom JSON encoder for numpy types
class NumpyJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        elif isinstance(obj, np.floating):
            return float(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        elif isinstance(obj, np.bool_):
            return bool(obj)
        elif isinstance(obj, pd.Series):
            return obj.tolist()
        elif isinstance(obj, pd.DataFrame):
            return obj.to_dict('records')
        elif isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create custom registry to avoid conflicts
strategy_registry = CollectorRegistry()

# Prometheus Metrics
strategy_analyses = Counter('strategy_analyses_total', 'Total strategy analyses performed', ['strategy', 'exchange', 'pair', 'result'], registry=strategy_registry)
analysis_duration = Histogram('strategy_analysis_duration_seconds', 'Strategy analysis duration', ['strategy'], registry=strategy_registry)
signals_generated = Counter('strategy_signals_total', 'Total signals generated', ['strategy', 'signal_type'], registry=strategy_registry)
active_strategies = Gauge('strategy_active_strategies', 'Number of active strategies', registry=strategy_registry)

# Initialize FastAPI app
app = FastAPI(
    title="Strategy Service",
    description="Strategy analysis and signal generation",
    version="1.0.0"
)

# Configure custom JSON encoder
app.json_encoder = NumpyJSONEncoder

# Override FastAPI's jsonable_encoder to handle numpy types
from fastapi.encoders import jsonable_encoder as original_jsonable_encoder

def custom_jsonable_encoder(obj, **kwargs):
    """Custom JSON encoder that handles numpy types"""
    if isinstance(obj, dict):
        return {k: custom_jsonable_encoder(v, **kwargs) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [custom_jsonable_encoder(item, **kwargs) for item in obj]
    elif isinstance(obj, (np.integer, np.floating, np.bool_)):
        if isinstance(obj, np.bool_):
            return bool(obj)
        elif isinstance(obj, np.integer):
            return int(obj)
        elif isinstance(obj, np.floating):
            return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, pd.Series):
        return obj.tolist()
    elif isinstance(obj, pd.DataFrame):
        return obj.to_dict('records')
    elif hasattr(obj, '__dict__'):
        return custom_jsonable_encoder(obj.__dict__, **kwargs)
    else:
        return original_jsonable_encoder(obj, **kwargs)

# Monkey patch FastAPI's jsonable_encoder
import fastapi.encoders
fastapi.encoders.jsonable_encoder = custom_jsonable_encoder

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Data Models
class StrategySignal(BaseModel):
    strategy_name: str
    pair: str
    exchange: str
    signal: str  # 'buy', 'sell', 'hold'
    confidence: float
    strength: float
    timestamp: datetime
    market_regime: str = "unknown"

class StrategyConsensus(BaseModel):
    pair: str
    exchange: str
    consensus_signal: str
    agreement_percentage: float
    participating_strategies: int
    signals: List[StrategySignal]
    timestamp: datetime

class StrategyPerformance(BaseModel):
    strategy_name: str
    total_trades: int
    winning_trades: int
    win_rate: float
    total_pnl: float
    sharpe_ratio: float
    max_drawdown: float
    period: str

class AnalysisRequest(BaseModel):
    exchange: str
    pair: str
    timeframes: List[str] = ['1h', '15m', '5m']
    strategies: Optional[List[str]] = None

class HealthResponse(BaseModel):
    status: str
    timestamp: datetime
    version: str
    strategies_loaded: int
    total_strategies: int

# Global variables
strategies = {}
strategy_instances = {}
signal_cache = {}
strategy_manager = None
config_service_url = os.getenv("CONFIG_SERVICE_URL", "http://config-service:8001")
exchange_service_url = os.getenv("EXCHANGE_SERVICE_URL", "http://exchange-service:8003")
database_service_url = os.getenv("DATABASE_SERVICE_URL", "http://database_service:8002")

class ExchangeAdapter:
    """Adapter to provide exchange functionality to strategies via HTTP calls"""
    
    def __init__(self, exchange_service_url: str, exchange_name: str = None):
        self.exchange_service_url = exchange_service_url
        self.exchange_name = exchange_name
    
    async def get_ohlcv(self, exchange_name: str, symbol: str, timeframe: str, limit: int = 100):
        """Get OHLCV data from exchange service"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Convert symbol format if needed (remove slash for URL)
                url_symbol = symbol.replace('/', '')
                response = await client.get(
                    f"{self.exchange_service_url}/api/v1/market/ohlcv/{exchange_name}/{url_symbol}",
                    params={'timeframe': timeframe, 'limit': limit}
                )
                response.raise_for_status()
                
                # Convert response to DataFrame
                data = response.json()
                if data:
                    df = pd.DataFrame(data)
                    df['timestamp'] = pd.to_datetime(df['timestamp'])
                    return df
                return None
                
        except Exception as e:
            logger.error(f"Error fetching OHLCV for {symbol} on {exchange_name}: {e}")
            return None
    
    async def get_ticker(self, symbol: str, exchange_name: str = None):
        """Get ticker data from exchange service - compatible with base strategy interface"""
        try:
            # Use provided exchange_name or fall back to adapter's exchange_name
            if exchange_name is None:
                exchange_name = self.exchange_name or 'binance'  # Fallback
            
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Convert symbol format if needed (remove slash for URL)
                url_symbol = symbol.replace('/', '')
                response = await client.get(
                    f"{self.exchange_service_url}/api/v1/market/ticker/{exchange_name}/{url_symbol}"
                )
                response.raise_for_status()
                return response.json()
                
        except Exception as e:
            logger.error(f"Error fetching ticker for {symbol} on {exchange_name}: {e}")
            return None

# Global exchange adapter
exchange_adapter = ExchangeAdapter(exchange_service_url)

# Global market regime detector
market_regime_detector = MarketRegimeDetector()

class StrategyManager:
    """Manages all trading strategies and coordinates analysis"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self._initialize_strategies()
        
    def _initialize_strategies(self) -> None:
        """Initialize all enabled strategies"""
        strategies_config = self.config.get('strategies', {})
        
        # Strategy class mapping with actual module names
        strategy_mapping = {
            'vwma_hull': {
                'module': 'vwma_hull_strategy',
                'class': 'VWMAHullStrategy'
            },
            'heikin_ashi': {
                'module': 'heikin_ashi_strategy', 
                'class': 'HeikinAshiStrategy'
            },
            'multi_timeframe_confluence': {
                'module': 'multi_timeframe_confluence_strategy',
                'class': 'MultiTimeframeConfluenceStrategy'
            },
            'engulfing_multi_tf': {
                'module': 'engulfing_multi_tf',
                'class': 'EngulfingMultiTimeframeStrategy'
            }
            # Note: strategy_pnl_enhanced contains utility functions, not a strategy class
        }
        
        for strategy_name, strategy_config in strategies_config.items():
            if not strategy_config.get('enabled', False):
                continue
                
            try:
                # Import strategy module
                if strategy_name in strategy_mapping:
                    mapping = strategy_mapping[strategy_name]
                    module_name = mapping['module']
                    class_name = mapping['class']
                    
                    # Import the strategy module with correct path
                    module = importlib.import_module(f"strategy.{module_name}")
                    strategy_class = getattr(module, class_name)
                    
                    # Create strategy instance with a generic exchange adapter (will be replaced per analysis)
                    strategy_instance = strategy_class(
                        config=strategy_config,
                        exchange=None,  # Will be set per analysis with exchange-specific adapter
                        database=None,   # Can be None for now
                        redis_client=None  # Can be None for now
                    )
                    
                    # Enable detailed logging mode for condition logger even without Redis
                    if hasattr(strategy_instance, '_condition_logger'):
                        strategy_instance._condition_logger.detailed_mode = True
                        logger.info(f"Enabled detailed condition logging for {strategy_name}")
                    
                    # Set exchange_name attribute for proper logging
                    strategy_instance.exchange_name = None  # Will be set per analysis
                    
                    strategies[strategy_name] = {
                        'instance': strategy_instance,
                        'config': strategy_config,
                        'enabled': True,
                        'last_analysis': None
                    }
                    
                    logger.info(f"Initialized strategy: {strategy_name}")
                    
            except Exception as e:
                logger.error(f"Failed to initialize strategy {strategy_name}: {e}")
                continue
                
    async def analyze_pair(self, exchange_name: str, pair: str, 
                          timeframes: List[str] = ['1h', '15m', '5m']) -> Dict[str, Any]:
        """Analyze a trading pair using market regime-based strategy selection"""
        try:
            # Get market data for all timeframes
            market_data = await self._get_market_data_for_strategy(
                exchange_name, pair, timeframes
            )
            
            if not market_data:
                logger.warning(f"No market data available for {pair} on {exchange_name}")
                return {}
            
            # STEP 1: Detect Market Regime using primary timeframe
            primary_timeframe = timeframes[0]
            primary_ohlcv = market_data.get(primary_timeframe)
            
            if primary_ohlcv is None or len(primary_ohlcv) < 50:
                logger.warning(f"Insufficient data for regime detection: {pair} on {exchange_name}")
                # Fallback to all strategies if we can't detect regime
                market_regime = MarketRegime.LOW_VOLATILITY
                applicable_strategies = list(strategies.keys())
                regime_analysis = {"reason": "insufficient_data"}
            else:
                # Detect market regime
                market_regime, regime_analysis = market_regime_detector.detect_regime(primary_ohlcv, pair)
                applicable_strategies = market_regime_detector.get_applicable_strategies(market_regime)
                
                # Log regime detection
                logger.info(f"📊 [MARKET REGIME] {pair} on {exchange_name}: {market_regime.value}")
                logger.info(f"🎯 [STRATEGY SELECTION] Applicable strategies: {applicable_strategies}")
            
            analysis_results = {
                'pair': pair,
                'exchange': exchange_name,
                'timestamp': datetime.utcnow().isoformat(),
                'market_regime': market_regime.value,
                'regime_analysis': regime_analysis,
                'applicable_strategies': applicable_strategies,
                'strategies': {},
                'consensus': {}
            }
            
            # STEP 2: Run analysis only for applicable strategies
            strategies_analyzed = 0
            for strategy_name in applicable_strategies:
                if strategy_name not in strategies:
                    logger.warning(f"Strategy {strategy_name} not available, skipping")
                    continue
                    
                strategy_data = strategies[strategy_name]
                if not strategy_data['enabled']:
                    logger.info(f"Strategy {strategy_name} disabled, skipping")
                    continue
                    
                try:
                    logger.info(f"🔍 [STRATEGY ANALYSIS] {strategy_name} analyzing {pair} on {exchange_name} (regime: {market_regime.value})")
                    strategy_instance = strategy_data['instance']
                    
                    # Create exchange-specific adapter for this analysis
                    exchange_specific_adapter = ExchangeAdapter(exchange_service_url, exchange_name)
                    
                    # Set exchange and exchange name for proper logging and functionality
                    strategy_instance.exchange = exchange_specific_adapter
                    strategy_instance.exchange_name = exchange_name
                    
                    # Initialize strategy for this pair with detailed logging
                    logger.info(f"🔧 [STRATEGY INIT] Initializing {strategy_name} for {pair}")
                    await strategy_instance.initialize(pair)
                    
                    # Update strategy with market data (use primary timeframe)
                    if primary_timeframe in market_data:
                        logger.info(f"📊 [STRATEGY DATA] Updating {strategy_name} with {primary_timeframe} data ({len(market_data[primary_timeframe])} candles)")
                        await strategy_instance.update(market_data[primary_timeframe])
                    
                    # CRITICAL FIX: Set market regime in strategy state
                    if hasattr(strategy_instance, 'state') and strategy_instance.state:
                        strategy_instance.state.market_regime = market_regime.value
                    elif hasattr(strategy_instance, 'state'):
                        # Initialize state with market regime if state exists but is empty
                        from types import SimpleNamespace
                        strategy_instance.state = SimpleNamespace()
                        strategy_instance.state.market_regime = market_regime.value
                    
                    # Generate signal - pass full market_data AND the exchange adapter for strategies that need multiple timeframes
                    logger.info(f"🎯 [SIGNAL GENERATION] {strategy_name} generating signal for {pair} on {exchange_name}")
                    signal, confidence, strength = await strategy_instance.generate_signal(
                        market_data, pair=pair, timeframe=primary_timeframe, exchange_adapter=exchange_specific_adapter
                    )
                    
                    # Store results with detailed logging
                    # Clean strategy state to ensure JSON serialization
                    clean_state = self._clean_strategy_state_for_serialization(strategy_instance)
                    
                    analysis_results['strategies'][strategy_name] = {
                        'signal': signal,
                        'confidence': confidence,
                        'strength': strength,
                        'market_regime': clean_state['market_regime'],
                        'timestamp': datetime.utcnow().isoformat(),
                        'selected_for_regime': market_regime.value,
                        'state': clean_state
                    }
                    
                    # Log the final result
                    signal_emoji = "🟢" if signal == "buy" else "🔴" if signal == "sell" else "⚪"
                    logger.info(f"📈 [STRATEGY RESULT] {strategy_name}: {signal_emoji} {signal.upper()} | "
                              f"Confidence: {confidence:.2f} | Strength: {strength:.2f} | "
                              f"Regime: {getattr(strategy_instance.state, 'market_regime', 'unknown')}")
                    
                    # Update last analysis time
                    strategy_data['last_analysis'] = datetime.utcnow()
                    strategies_analyzed += 1
                    
                except Exception as e:
                    logger.error(f"Error analyzing {pair} with {strategy_name}: {e}")
                    analysis_results['strategies'][strategy_name] = {
                        'error': str(e),
                        'timestamp': datetime.utcnow().isoformat(),
                        'selected_for_regime': market_regime.value
                    }
                    
            # Calculate consensus with detailed logging
            logger.info(f"🤝 [CONSENSUS] Calculating consensus from {strategies_analyzed} regime-selected strategies "
                       f"(regime: {market_regime.value})")
            analysis_results['consensus'] = self._calculate_consensus(analysis_results['strategies'])
            
            consensus = analysis_results['consensus']
            consensus_emoji = "🟢" if consensus['signal'] == "buy" else "🔴" if consensus['signal'] == "sell" else "⚪"
            logger.info(f"🎯 [CONSENSUS RESULT] {pair} on {exchange_name}: {consensus_emoji} {consensus['signal'].upper()} | "
                       f"Confidence: {consensus['confidence']:.2f} | Agreement: {consensus['agreement']:.2f} | "
                       f"Participating: {consensus['participating_strategies']} | Regime: {market_regime.value}")
            
            # Cache results
            cache_key = f"{exchange_name}_{pair}_{int(datetime.utcnow().timestamp() / 300)}"  # 5-minute cache
            signal_cache[cache_key] = analysis_results
            
            return analysis_results
            
        except Exception as e:
            logger.error(f"Error analyzing pair {pair} on {exchange_name}: {e}")
            return {}
            
    def _calculate_consensus(self, strategy_results: Dict[str, Any]) -> Dict[str, Any]:
        """Calculate regime-aware consensus with strategy weighting"""
        try:
            valid_signals = []
            weighted_confidence = 0
            weighted_strength = 0
            weighted_signal_counts = {'buy': 0, 'sell': 0, 'hold': 0}
            signal_counts = {'buy': 0, 'sell': 0, 'hold': 0}  # Fix: Initialize signal_counts
            total_weight = 0
            
            # Define strategy weights based on regime appropriateness
            # First strategy in regime mapping gets highest weight
            regime_weights = {
                'primary': 1.0,    # First strategy for this regime
                'secondary': 0.7,  # Supporting strategies
                'fallback': 0.5    # Available but not optimal
            }
            
            for strategy_name, result in strategy_results.items():
                if 'error' in result:
                    continue
                    
                signal = result.get('signal', 'hold')
                confidence = result.get('confidence', 0)
                strength = result.get('strength', 0)
                selected_for_regime = result.get('selected_for_regime', 'unknown')
                
                if signal in ['buy', 'sell', 'hold']:
                    # Determine strategy weight based on regime mapping position
                    # This maintains regime-appropriate strategy prioritization
                    strategy_weight = regime_weights.get('secondary', 0.7)  # Default weight
                    
                    # Primary strategies get higher weight (position 0 in regime mapping)
                    if strategy_name == 'heikin_ashi' and selected_for_regime in ['trending_up', 'trending_down', 'high_volatility']:
                        strategy_weight = regime_weights['primary']
                    elif strategy_name == 'vwma_hull' and selected_for_regime in ['breakout']:
                        strategy_weight = regime_weights['primary'] 
                    elif strategy_name == 'multi_timeframe_confluence' and selected_for_regime in ['sideways', 'low_volatility']:
                        strategy_weight = regime_weights['primary']
                    elif strategy_name == 'engulfing_multi_tf' and selected_for_regime in ['reversal_zone']:
                        strategy_weight = regime_weights['primary']
                    
                    valid_signals.append({
                        'strategy': strategy_name,
                        'signal': signal,
                        'confidence': confidence,
                        'strength': strength,
                        'weight': strategy_weight,
                        'regime': selected_for_regime
                    })
                    
                    # Apply weighted counting for regime-appropriate consensus
                    weighted_signal_counts[signal] += strategy_weight
                    weighted_confidence += confidence * strategy_weight
                    weighted_strength += strength * strategy_weight
                    total_weight += strategy_weight
                    
                    # Keep unweighted counts for compatibility
                    signal_counts[signal] += 1
                    
            if not valid_signals:
                return {
                    'signal': 'hold',
                    'confidence': 0,
                    'strength': 0,
                    'agreement': 0,
                    'participating_strategies': 0
                }
                
            # Determine consensus signal using WEIGHTED votes (regime-aware)
            max_weighted_count = max(weighted_signal_counts.values())
            consensus_signal = 'hold'
            
            if weighted_signal_counts['buy'] == max_weighted_count and weighted_signal_counts['buy'] > 0:
                consensus_signal = 'buy'
            elif weighted_signal_counts['sell'] == max_weighted_count and weighted_signal_counts['sell'] > 0:
                consensus_signal = 'sell'
                
            # Calculate agreement percentage (weighted)
            total_strategies = len(valid_signals)
            agreement = (max_weighted_count / total_weight) * 100 if total_weight > 0 else 0
            
            # Calculate weighted average confidence and strength (regime-aware)
            avg_confidence = weighted_confidence / total_weight if total_weight > 0 else 0
            avg_strength = weighted_strength / total_weight if total_weight > 0 else 0
            
            return {
                'signal': consensus_signal,
                'confidence': avg_confidence,
                'strength': avg_strength,
                'agreement': agreement,
                'participating_strategies': total_strategies,
                'signal_breakdown': signal_counts,
                'weighted_breakdown': weighted_signal_counts  # Show regime weighting
            }
            
        except Exception as e:
            logger.error(f"Error calculating consensus: {e}")
            return {
                'signal': 'hold',
                'confidence': 0,
                'strength': 0,
                'agreement': 0,
                'participating_strategies': 0
            }
            
    async def _get_market_data_for_strategy(self, exchange_name: str, symbol: str, 
                                           timeframes: List[str] = ['1h', '15m', '5m']) -> Dict[str, pd.DataFrame]:
        """Get market data for strategy analysis"""
        try:
            market_data = {}
            
            for timeframe in timeframes:
                # Retry logic for handling temporary timeout errors
                max_retries = 3
                for retry in range(max_retries):
                    try:
                        async with httpx.AsyncClient(timeout=30.0) as client:
                            response = await client.get(
                                f"{exchange_service_url}/api/v1/market/ohlcv/{exchange_name}/{symbol}",
                                params={'timeframe': timeframe, 'limit': 100}
                            )
                            
                            # If successful, break out of retry loop
                            break
                    except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.TimeoutException) as timeout_error:
                        if retry < max_retries - 1:
                            wait_time = 2 ** retry  # Exponential backoff: 1s, 2s, 4s
                            logger.warning(f"Timeout getting {timeframe} data for {symbol} on {exchange_name}, retry {retry + 1}/{max_retries} in {wait_time}s")
                            await asyncio.sleep(wait_time)
                            continue
                        else:
                            logger.error(f"Failed to get {timeframe} data for {symbol} on {exchange_name} after {max_retries} retries: {type(timeout_error).__name__}")
                            break
                    except Exception as e:
                        logger.error(f"Failed to get {timeframe} data for {symbol} on {exchange_name}: {type(e).__name__}: {str(e)}")
                        break
                
                try:
                    if response.status_code == 200:
                        ohlcv_data = response.json()
                        
                        # Handle different response formats
                        if 'data' in ohlcv_data:
                            data = ohlcv_data['data']
                        else:
                            data = ohlcv_data
                        
                        # Convert to DataFrame
                        df = pd.DataFrame(data)
                        
                        # Validate data structure
                        if df.empty:
                            logger.warning(f"Empty OHLCV data for {symbol} on {exchange_name} {timeframe}")
                            continue
                        
                        # Ensure we have the required columns
                        if len(df.columns) >= 5:
                            # Handle different column count scenarios
                            if len(df.columns) == 5:
                                df.columns = ['timestamp', 'open', 'high', 'low', 'close']
                                df['volume'] = 0  # Add volume column with default value
                            elif len(df.columns) >= 6:
                                df.columns = ['timestamp', 'open', 'high', 'low', 'close', 'volume'] + [f'extra_{i}' for i in range(len(df.columns) - 6)]
                            
                            # Handle timestamp conversion - check if it's already a datetime string
                            try:
                                if isinstance(df['timestamp'].iloc[0], str):
                                    df['timestamp'] = pd.to_datetime(df['timestamp'])
                                else:
                                    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                                df.set_index('timestamp', inplace=True)
                                
                                # Ensure numeric columns are properly typed
                                for col in ['open', 'high', 'low', 'close', 'volume']:
                                    df[col] = pd.to_numeric(df[col], errors='coerce')
                                
                                market_data[timeframe] = df
                                logger.info(f"Got {len(df)} {timeframe} candles for {symbol} on {exchange_name}")
                                
                            except Exception as timestamp_error:
                                logger.error(f"Timestamp processing error for {symbol} on {exchange_name} {timeframe}: {timestamp_error}")
                                continue
                                
                        else:
                            logger.warning(f"Invalid OHLCV data format for {symbol} on {exchange_name} {timeframe}: expected >=5 columns, got {len(df.columns)}")
                    else:
                        logger.warning(f"Failed to get {timeframe} data for {symbol} on {exchange_name}: {response.status_code}")
                        
                except Exception as e:
                    logger.error(f"Failed to get {timeframe} data for {symbol} on {exchange_name}: {type(e).__name__}: {str(e)}")
                    continue
                    
            return market_data
            
        except Exception as e:
            logger.error(f"Error getting market data for {symbol} on {exchange_name}: {e}")
            return {}

    def _clean_strategy_state_for_serialization(self, strategy_instance) -> Dict[str, Any]:
        """Clean strategy state to ensure JSON serialization compatibility"""
        try:
            # Get the strategy state
            state = strategy_instance.state
            
            # Create a clean copy of indicators, converting numpy/pandas objects
            clean_indicators = {}
            if hasattr(state, 'indicators') and state.indicators:
                for key, value in state.indicators.items():
                    if isinstance(value, (pd.Series, pd.DataFrame)):
                        # Convert to list of values, handling NaN
                        clean_indicators[key] = value.tolist() if not value.empty else []
                    elif isinstance(value, np.ndarray):
                        clean_indicators[key] = value.tolist()
                    elif isinstance(value, np.bool_):
                        clean_indicators[key] = bool(value)
                    elif isinstance(value, (np.integer, np.floating)):
                        clean_indicators[key] = float(value) if isinstance(value, np.floating) else int(value)
                    else:
                        clean_indicators[key] = value
            
            # Create a clean copy of patterns
            clean_patterns = {}
            if hasattr(state, 'patterns') and state.patterns:
                for key, value in state.patterns.items():
                    if isinstance(value, dict):
                        clean_patterns[key] = {}
                        for k, v in value.items():
                            if isinstance(v, np.bool_):
                                clean_patterns[key][k] = bool(v)
                            elif isinstance(v, (np.integer, np.floating)):
                                clean_patterns[key][k] = float(v) if isinstance(v, np.floating) else int(v)
                            elif isinstance(v, datetime):
                                clean_patterns[key][k] = v.isoformat()
                            else:
                                clean_patterns[key][k] = v
                    else:
                        clean_patterns[key] = value
            
            # Create a clean copy of performance
            clean_performance = {}
            if hasattr(state, 'performance') and state.performance:
                for key, value in state.performance.items():
                    if isinstance(value, np.bool_):
                        clean_performance[key] = bool(value)
                    elif isinstance(value, (np.integer, np.floating)):
                        clean_performance[key] = float(value) if isinstance(value, np.floating) else int(value)
                    else:
                        clean_performance[key] = value
            
            return {
                'indicators': clean_indicators,
                'patterns': clean_patterns,
                'performance': clean_performance,
                'market_regime': getattr(state, 'market_regime', 'unknown')
            }
            
        except Exception as e:
            logger.error(f"Error cleaning strategy state: {e}")
            return {
                'indicators': {},
                'patterns': {},
                'performance': {},
                'market_regime': 'unknown'
            }

# Global strategy manager
strategy_manager = None

async def get_config_from_service() -> Dict[str, Any]:
    """Get configuration from config service"""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{config_service_url}/api/v1/config/strategies")
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.error(f"Failed to get config from service: {e}")
        # Fallback to default strategies
        return {
            'vwma_hull': {
                'enabled': True,
                'parameters': {}
            },
            'heikin_ashi': {
                'enabled': True,
                'parameters': {}
            }
        }

async def initialize_strategies():
    """Initialize strategy manager"""
    global strategy_manager
    try:
        config = await get_config_from_service()
        strategy_manager = StrategyManager({'strategies': config})
        logger.info("Strategy service initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize strategy service: {e}")
        raise

# API Endpoints
@app.get("/metrics")
async def get_metrics():
    """Prometheus metrics endpoint"""
    return Response(generate_latest(strategy_registry), media_type=CONTENT_TYPE_LATEST)

@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint"""
    enabled_count = sum(1 for s in strategies.values() if s['enabled'])
    return HealthResponse(
        status="healthy" if enabled_count > 0 else "degraded",
        timestamp=datetime.utcnow(),
        version="1.0.0",
        strategies_loaded=enabled_count,
        total_strategies=len(strategies)
    )

@app.get("/ready")
async def readiness_check():
    """Readiness check endpoint"""
    if not strategy_manager:
        raise HTTPException(status_code=503, detail="Strategy manager not initialized")
    return {"status": "ready"}

@app.get("/live")
async def liveness_check():
    """Liveness check endpoint"""
    return {"status": "alive"}

# Strategy Management Endpoints
@app.get("/api/v1/strategies")
async def get_strategies():
    """Get all strategies"""
    strategy_list = []
    for name, data in strategies.items():
        strategy_list.append({
            'name': name,
            'enabled': data['enabled'],
            'last_analysis': data['last_analysis'],
            'config': data['config']
        })
    
    return {
        "strategies": strategy_list,
        "total": len(strategy_list),
        "enabled": sum(1 for s in strategy_list if s['enabled'])
    }

@app.get("/api/v1/strategies/{strategy_name}")
async def get_strategy(strategy_name: str):
    """Get specific strategy details"""
    if strategy_name not in strategies:
        raise HTTPException(status_code=404, detail=f"Strategy {strategy_name} not found")
    
    strategy_data = strategies[strategy_name]
    return {
        'name': strategy_name,
        'enabled': strategy_data['enabled'],
        'last_analysis': strategy_data['last_analysis'],
        'config': strategy_data['config']
    }

@app.post("/api/v1/strategies/{strategy_name}/enable")
async def enable_strategy(strategy_name: str):
    """Enable a strategy"""
    if strategy_name not in strategies:
        raise HTTPException(status_code=404, detail=f"Strategy {strategy_name} not found")
    
    strategies[strategy_name]['enabled'] = True
    logger.info(f"Enabled strategy: {strategy_name}")
    return {"message": f"Strategy {strategy_name} enabled"}

@app.post("/api/v1/strategies/{strategy_name}/disable")
async def disable_strategy(strategy_name: str):
    """Disable a strategy"""
    if strategy_name not in strategies:
        raise HTTPException(status_code=404, detail=f"Strategy {strategy_name} not found")
    
    strategies[strategy_name]['enabled'] = False
    logger.info(f"Disabled strategy: {strategy_name}")
    return {"message": f"Strategy {strategy_name} disabled"}

# Signal Generation Endpoints
@app.post("/api/v1/analysis/{exchange}/{pair}")
async def analyze_pair(exchange: str, pair: str, request: AnalysisRequest):
    """Analyze a trading pair"""
    if not strategy_manager:
        raise HTTPException(status_code=503, detail="Strategy manager not initialized")
    
    try:
        # Convert pair format: Add slash if missing (ACXUSD -> ACX/USD)
        formatted_pair = pair
        if '/' not in pair:
            # Handle common patterns like ACXUSD -> ACX/USD, BTCUSD -> BTC/USD
            if len(pair) >= 6 and pair.endswith('USD'):
                base = pair[:-3]
                formatted_pair = f"{base}/USD"
            elif len(pair) >= 7 and pair.endswith('USDC'):
                base = pair[:-4] 
                formatted_pair = f"{base}/USDC"
            else:
                # For other formats, return 404
                raise HTTPException(status_code=404, detail=f"Unsupported pair format: {pair}")
        
        # Filter strategies if specified
        if request.strategies:
            # Temporarily disable non-requested strategies
            original_states = {}
            for strategy_name in strategies:
                original_states[strategy_name] = strategies[strategy_name]['enabled']
                strategies[strategy_name]['enabled'] = strategy_name in request.strategies
        
        # Perform analysis with formatted pair
        analysis_results = await strategy_manager.analyze_pair(
            exchange, formatted_pair, request.timeframes
        )
        
        # Restore original strategy states
        if request.strategies:
            for strategy_name, original_state in original_states.items():
                strategies[strategy_name]['enabled'] = original_state
        
        if not analysis_results:
            raise HTTPException(status_code=404, detail="Analysis failed - no results")
        
        return analysis_results
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error analyzing {pair} on {exchange}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/signals/{exchange}/{pair}")
async def get_signals(exchange: str, pair: str):
    """Get cached signals for a pair"""
    if not strategy_manager:
        raise HTTPException(status_code=503, detail="Strategy manager not initialized")
    
    # Convert pair format: Add slash if missing (ACXUSD -> ACX/USD)
    formatted_pair = pair
    if '/' not in pair:
        # Handle common patterns like ACXUSD -> ACX/USD, BTCUSD -> BTC/USD
        if len(pair) >= 6 and pair.endswith('USD'):
            base = pair[:-3]
            formatted_pair = f"{base}/USD"
        elif len(pair) >= 7 and pair.endswith('USDC'):
            base = pair[:-4] 
            formatted_pair = f"{base}/USDC"
        else:
            # For other formats, return 404
            raise HTTPException(status_code=404, detail=f"Unsupported pair format: {pair}")
    
    # Check cache with formatted pair
    cache_key = f"{exchange}_{formatted_pair}_{int(datetime.utcnow().timestamp() / 300)}"
    if cache_key in signal_cache:
        return signal_cache[cache_key]
    
    # If not in cache, perform analysis with formatted pair
    analysis_results = await strategy_manager.analyze_pair(exchange, formatted_pair)
    
    if not analysis_results:
        raise HTTPException(status_code=404, detail="No signals available")
    
    return analysis_results

@app.get("/api/v1/signals/consensus/{exchange}/{pair}")
async def get_consensus_signals(exchange: str, pair: str):
    """Get consensus signals for a pair"""
    if not strategy_manager:
        raise HTTPException(status_code=503, detail="Strategy manager not initialized")
    
    # Convert pair format: Add slash if missing (ACXUSD -> ACX/USD)
    formatted_pair = pair
    if '/' not in pair:
        # Handle common patterns like ACXUSD -> ACX/USD, BTCUSD -> BTC/USD
        if len(pair) >= 6 and pair.endswith('USD'):
            base = pair[:-3]
            formatted_pair = f"{base}/USD"
        elif len(pair) >= 7 and pair.endswith('USDC'):
            base = pair[:-4] 
            formatted_pair = f"{base}/USDC"
        else:
            # For other formats, return 404
            raise HTTPException(status_code=404, detail=f"Unsupported pair format: {pair}")
    
    # Get full analysis with formatted pair
    analysis_results = await strategy_manager.analyze_pair(exchange, formatted_pair)
    
    if not analysis_results or 'consensus' not in analysis_results:
        raise HTTPException(status_code=404, detail="No consensus available")
    
    # Convert to StrategyConsensus model
    consensus_data = analysis_results['consensus']
    signals = []
    
    for strategy_name, strategy_result in analysis_results['strategies'].items():
        if 'error' not in strategy_result:
            signals.append(StrategySignal(
                strategy_name=strategy_name,
                pair=pair,
                exchange=exchange,
                signal=strategy_result['signal'],
                confidence=strategy_result['confidence'],
                strength=strategy_result['strength'],
                timestamp=datetime.fromisoformat(strategy_result['timestamp']),
                market_regime=strategy_result.get('market_regime', 'unknown')
            ))
    
    consensus = StrategyConsensus(
        pair=pair,
        exchange=exchange,
        consensus_signal=consensus_data['signal'],
        agreement_percentage=consensus_data['agreement'],
        participating_strategies=consensus_data['participating_strategies'],
        signals=signals,
        timestamp=datetime.utcnow()
    )
    
    return consensus

# Strategy Performance Endpoints
@app.get("/api/v1/performance/{strategy_name}")
async def get_strategy_performance(strategy_name: str, period: str = "30d"):
    """Get performance metrics for a strategy"""
    if strategy_name not in strategies:
        raise HTTPException(status_code=404, detail=f"Strategy {strategy_name} not found")
    
    try:
        # Get performance data from database
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{database_service_url}/api/v1/performance/strategy/{strategy_name}",
                params={'period': period}
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                # Return mock data if not available
                return StrategyPerformance(
                    strategy_name=strategy_name,
                    total_trades=0,
                    winning_trades=0,
                    win_rate=0.0,
                    total_pnl=0.0,
                    sharpe_ratio=0.0,
                    max_drawdown=0.0,
                    period=period
                )
                
    except Exception as e:
        logger.error(f"Error getting performance for {strategy_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/performance/{strategy_name}/update")
async def update_strategy_performance(strategy_name: str, performance_data: Dict[str, Any]):
    """Update performance metrics for a strategy"""
    if strategy_name not in strategies:
        raise HTTPException(status_code=404, detail=f"Strategy {strategy_name} not found")
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{database_service_url}/api/v1/performance/strategy/{strategy_name}/update",
                json=performance_data
            )
            response.raise_for_status()
            
        return {"message": f"Performance updated for {strategy_name}"}
        
    except Exception as e:
        logger.error(f"Error updating performance for {strategy_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/performance/compare")
async def compare_strategies(period: str = "30d"):
    """Compare performance across all strategies"""
    try:
        comparison = {}
        
        for strategy_name in strategies:
            try:
                performance = await get_strategy_performance(strategy_name, period)
                comparison[strategy_name] = performance
            except Exception as e:
                logger.error(f"Error getting performance for {strategy_name}: {e}")
                comparison[strategy_name] = {"error": str(e)}
        
        return {
            "period": period,
            "comparison": comparison,
            "timestamp": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Error comparing strategies: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Strategy Configuration Endpoints
@app.get("/api/v1/strategies/{strategy_name}/config")
async def get_strategy_config(strategy_name: str):
    """Get configuration for a strategy"""
    if strategy_name not in strategies:
        raise HTTPException(status_code=404, detail=f"Strategy {strategy_name} not found")
    
    return strategies[strategy_name]['config']

@app.put("/api/v1/strategies/{strategy_name}/config")
async def update_strategy_config(strategy_name: str, config: Dict[str, Any]):
    """Update configuration for a strategy"""
    if strategy_name not in strategies:
        raise HTTPException(status_code=404, detail=f"Strategy {strategy_name} not found")
    
    try:
        # Update local config
        strategies[strategy_name]['config'].update(config)
        
        # Update strategy instance if possible
        strategy_instance = strategies[strategy_name]['instance']
        if hasattr(strategy_instance, 'update_config'):
            await strategy_instance.update_config(config)
        
        logger.info(f"Updated config for strategy: {strategy_name}")
        return {"message": f"Configuration updated for {strategy_name}"}
        
    except Exception as e:
        logger.error(f"Error updating config for {strategy_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/strategies/{strategy_name}/backtest")
async def backtest_strategy(strategy_name: str, backtest_params: Dict[str, Any]):
    """Run backtest for a strategy"""
    if strategy_name not in strategies:
        raise HTTPException(status_code=404, detail=f"Strategy {strategy_name} not found")
    
    try:
        # This would implement backtesting logic
        # For now, return a mock result
        backtest_result = {
            "strategy": strategy_name,
            "period": backtest_params.get("period", "30d"),
            "total_trades": 100,
            "winning_trades": 65,
            "win_rate": 0.65,
            "total_pnl": 1250.0,
            "sharpe_ratio": 1.2,
            "max_drawdown": -150.0,
            "backtest_date": datetime.utcnow().isoformat()
        }
        
        return backtest_result
        
    except Exception as e:
        logger.error(f"Error backtesting {strategy_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Cache Management
@app.delete("/api/v1/cache/clear")
async def clear_cache():
    """Clear signal cache"""
    global signal_cache
    cache_size = len(signal_cache)
    signal_cache.clear()
    logger.info(f"Cleared signal cache ({cache_size} entries)")
    return {"message": f"Cache cleared ({cache_size} entries)"}

@app.get("/api/v1/cache/stats")
async def get_cache_stats():
    """Get cache statistics"""
    return {
        "cache_size": len(signal_cache),
        "cache_keys": list(signal_cache.keys()),
        "timestamp": datetime.utcnow().isoformat()
    }

# Startup and shutdown events
@app.on_event("startup")
async def startup_event():
    """Initialize strategies on startup"""
    logger.info("Starting Strategy Service...")
    await initialize_strategies()
    logger.info("Strategy Service started successfully")

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    logger.info("Shutting down Strategy Service...")
    # Clear any cached data
    signal_cache.clear()
    strategies.clear()
    strategy_instances.clear()
    logger.info("Strategy Service shutdown complete")

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8004,
        reload=True,
        log_level="info"
    ) 