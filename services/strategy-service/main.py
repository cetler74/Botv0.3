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
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import json

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="Strategy Service",
    description="Strategy analysis and signal generation",
    version="1.0.0"
)

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
config_service_url = os.getenv("CONFIG_SERVICE_URL", "http://config-service:8001")
exchange_service_url = os.getenv("EXCHANGE_SERVICE_URL", "http://exchange-service:8003")
database_service_url = os.getenv("DATABASE_SERVICE_URL", "http://database_service:8002")

class StrategyManager:
    """Manages all trading strategies and coordinates analysis"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self._initialize_strategies()
        
    def _initialize_strategies(self) -> None:
        """Initialize all enabled strategies"""
        strategies_config = self.config.get('strategies', {})
        
        # Strategy class mapping
        strategy_classes = {
            'vwma_hull': 'VWMAHullStrategy',
            'heikin_ashi': 'HeikinAshiStrategy', 
            'multi_timeframe_confluence': 'MultiTimeframeConfluenceStrategy',
            'engulfing_multi_tf': 'EngulfingMultiTimeframeStrategy',
            'strategy_pnl_enhanced': 'StrategyPnLEnhanced'
        }
        
        for strategy_name, strategy_config in strategies_config.items():
            if not strategy_config.get('enabled', False):
                continue
                
            try:
                # Import strategy module
                if strategy_name in ['vwma_hull', 'heikin_ashi', 'multi_timeframe_confluence', 'engulfing_multi_tf']:
                    module_name = f"{strategy_name}_strategy" if strategy_name != 'vwma_hull' else 'vwma_hull_strategy'
                    class_name = strategy_classes[strategy_name]
                    
                    # Import the strategy module
                    module = importlib.import_module(module_name)
                    strategy_class = getattr(module, class_name)
                    
                    # Create strategy instance
                    strategy_instance = strategy_class(
                        config=strategy_config,
                        exchange=None,  # Will be set later
                        database=None   # Will be set later
                    )
                    
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
        """Analyze a trading pair using all enabled strategies"""
        try:
            # Get market data for all timeframes
            market_data = await self._get_market_data_for_strategy(
                exchange_name, pair, timeframes
            )
            
            if not market_data:
                logger.warning(f"No market data available for {pair} on {exchange_name}")
                return {}
                
            analysis_results = {
                'pair': pair,
                'exchange': exchange_name,
                'timestamp': datetime.utcnow().isoformat(),
                'strategies': {},
                'consensus': {}
            }
            
            # Run analysis for each enabled strategy
            for strategy_name, strategy_data in strategies.items():
                if not strategy_data['enabled']:
                    continue
                    
                try:
                    strategy_instance = strategy_data['instance']
                    
                    # Initialize strategy for this pair
                    await strategy_instance.initialize(pair)
                    
                    # Update strategy with market data (use primary timeframe)
                    primary_timeframe = timeframes[0]
                    if primary_timeframe in market_data:
                        await strategy_instance.update(market_data[primary_timeframe])
                    
                    # Generate signal
                    signal, confidence, strength = await strategy_instance.generate_signal(
                        market_data[primary_timeframe], None, pair, primary_timeframe
                    )
                    
                    # Store results
                    analysis_results['strategies'][strategy_name] = {
                        'signal': signal,
                        'confidence': confidence,
                        'strength': strength,
                        'market_regime': getattr(strategy_instance.state, 'market_regime', 'unknown'),
                        'timestamp': datetime.utcnow().isoformat()
                    }
                    
                    # Update last analysis time
                    strategy_data['last_analysis'] = datetime.utcnow()
                    
                except Exception as e:
                    logger.error(f"Error analyzing {pair} with {strategy_name}: {e}")
                    analysis_results['strategies'][strategy_name] = {
                        'error': str(e),
                        'timestamp': datetime.utcnow().isoformat()
                    }
                    
            # Calculate consensus
            analysis_results['consensus'] = self._calculate_consensus(analysis_results['strategies'])
            
            # Cache results
            cache_key = f"{exchange_name}_{pair}_{int(datetime.utcnow().timestamp() / 300)}"  # 5-minute cache
            signal_cache[cache_key] = analysis_results
            
            return analysis_results
            
        except Exception as e:
            logger.error(f"Error analyzing pair {pair} on {exchange_name}: {e}")
            return {}
            
    def _calculate_consensus(self, strategy_results: Dict[str, Any]) -> Dict[str, Any]:
        """Calculate consensus from multiple strategy results"""
        try:
            valid_signals = []
            total_confidence = 0
            total_strength = 0
            signal_counts = {'buy': 0, 'sell': 0, 'hold': 0}
            
            for strategy_name, result in strategy_results.items():
                if 'error' in result:
                    continue
                    
                signal = result.get('signal', 'hold')
                confidence = result.get('confidence', 0)
                strength = result.get('strength', 0)
                
                if signal in ['buy', 'sell', 'hold']:
                    valid_signals.append({
                        'strategy': strategy_name,
                        'signal': signal,
                        'confidence': confidence,
                        'strength': strength
                    })
                    
                    signal_counts[signal] += 1
                    total_confidence += confidence
                    total_strength += strength
                    
            if not valid_signals:
                return {
                    'signal': 'hold',
                    'confidence': 0,
                    'strength': 0,
                    'agreement': 0,
                    'participating_strategies': 0
                }
                
            # Determine consensus signal
            max_count = max(signal_counts.values())
            consensus_signal = 'hold'
            
            if signal_counts['buy'] == max_count and signal_counts['buy'] > 0:
                consensus_signal = 'buy'
            elif signal_counts['sell'] == max_count and signal_counts['sell'] > 0:
                consensus_signal = 'sell'
                
            # Calculate agreement percentage
            total_strategies = len(valid_signals)
            agreement = (max_count / total_strategies) * 100 if total_strategies > 0 else 0
            
            # Calculate average confidence and strength
            avg_confidence = total_confidence / total_strategies if total_strategies > 0 else 0
            avg_strength = total_strength / total_strategies if total_strategies > 0 else 0
            
            return {
                'signal': consensus_signal,
                'confidence': avg_confidence,
                'strength': avg_strength,
                'agreement': agreement,
                'participating_strategies': total_strategies,
                'signal_breakdown': signal_counts
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
                try:
                    async with httpx.AsyncClient() as client:
                        response = await client.get(
                            f"{exchange_service_url}/api/v1/market/ohlcv/{exchange_name}/{symbol}",
                            params={'timeframe': timeframe, 'limit': 100}
                        )
                        response.raise_for_status()
                        
                        ohlcv_data = response.json()
                        
                        # Convert to DataFrame
                        df = pd.DataFrame(ohlcv_data)
                        df['timestamp'] = pd.to_datetime(df['timestamp'])
                        df.set_index('timestamp', inplace=True)
                        
                        market_data[timeframe] = df
                        
                except Exception as e:
                    logger.error(f"Failed to get {timeframe} data for {symbol}: {e}")
                    continue
                    
            return market_data
            
        except Exception as e:
            logger.error(f"Error getting market data: {e}")
            return {}

# Global strategy manager
strategy_manager = None

async def get_config_from_service() -> Dict[str, Any]:
    """Get configuration from config service"""
    try:
        async with httpx.AsyncClient() as client:
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
        # Filter strategies if specified
        if request.strategies:
            # Temporarily disable non-requested strategies
            original_states = {}
            for strategy_name in strategies:
                original_states[strategy_name] = strategies[strategy_name]['enabled']
                strategies[strategy_name]['enabled'] = strategy_name in request.strategies
        
        # Perform analysis
        analysis_results = await strategy_manager.analyze_pair(
            exchange, pair, request.timeframes
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
    
    # Check cache
    cache_key = f"{exchange}_{pair}_{int(datetime.utcnow().timestamp() / 300)}"
    if cache_key in signal_cache:
        return signal_cache[cache_key]
    
    # If not in cache, perform analysis
    analysis_results = await strategy_manager.analyze_pair(exchange, pair)
    
    if not analysis_results:
        raise HTTPException(status_code=404, detail="No signals available")
    
    return analysis_results

@app.get("/api/v1/signals/consensus/{exchange}/{pair}")
async def get_consensus_signals(exchange: str, pair: str):
    """Get consensus signals for a pair"""
    if not strategy_manager:
        raise HTTPException(status_code=503, detail="Strategy manager not initialized")
    
    # Get full analysis
    analysis_results = await strategy_manager.analyze_pair(exchange, pair)
    
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
        async with httpx.AsyncClient() as client:
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
        async with httpx.AsyncClient() as client:
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
    await initialize_strategies()

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    global signal_cache
    signal_cache.clear()
    logger.info("Strategy service shutdown complete")

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8004,
        reload=True,
        log_level="info"
    ) 