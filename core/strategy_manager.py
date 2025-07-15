"""
Strategy Manager for the Multi-Exchange Trading Bot
Coordinates all strategy analysis and signal generation using existing strategy files
"""

import asyncio
import logging
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
import pandas as pd
import importlib
import sys
import os

logger = logging.getLogger(__name__)


class StrategyManager:
    """Manages all trading strategies and coordinates analysis"""
    
    def __init__(self, config: Dict[str, Any], exchange_manager=None, database_manager=None):
        self.config = config
        self.exchange_manager = exchange_manager
        self.database_manager = database_manager
        self.strategies = {}
        self.strategy_instances = {}
        self.signal_cache = {}
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
                        exchange=self.exchange_manager,
                        database=self.database_manager
                    )
                    
                    self.strategies[strategy_name] = {
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
            market_data = await self.exchange_manager.get_market_data_for_strategy(
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
            for strategy_name, strategy_data in self.strategies.items():
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
            self.signal_cache[cache_key] = analysis_results
            
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
                'signal_distribution': signal_counts
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
            
    async def check_entry_signals(self, exchange_name: str, pair: str) -> List[Dict[str, Any]]:
        """Check for entry signals across all strategies"""
        try:
            # Analyze the pair
            analysis = await self.analyze_pair(exchange_name, pair)
            
            if not analysis or 'consensus' not in analysis:
                return []
                
            consensus = analysis['consensus']
            
            # Check if consensus meets entry criteria
            min_confidence = self.config.get('strategy_manager', {}).get('min_confidence', 0.6)
            min_agreement = self.config.get('strategy_manager', {}).get('min_agreement', 50)
            
            if (consensus['signal'] in ['buy', 'sell'] and 
                consensus['confidence'] >= min_confidence and
                consensus['agreement'] >= min_agreement):
                
                return [{
                    'pair': pair,
                    'signal': consensus['signal'],
                    'confidence': consensus['confidence'],
                    'strength': consensus['strength'],
                    'strategy': 'consensus',
                    'exchange': exchange_name,
                    'details': {
                        'agreement': consensus['agreement'],
                        'participating_strategies': consensus['participating_strategies'],
                        'signal_distribution': consensus['signal_distribution'],
                        'individual_signals': analysis['strategies']
                    }
                }]
                
            return []
            
        except Exception as e:
            logger.error(f"Error checking entry signals for {pair} on {exchange_name}: {e}")
            return []
            
    async def check_exit_signals(self, exchange_name: str, pair: str, trade_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Check for exit signals across all strategies"""
        try:
            exit_signals = []
            
            # Check each strategy for exit signals
            for strategy_name, strategy_data in self.strategies.items():
                if not strategy_data['enabled']:
                    continue
                    
                try:
                    strategy_instance = strategy_data['instance']
                    
                    # Initialize strategy for this pair
                    await strategy_instance.initialize(pair)
                    
                    # Get market data for exit analysis
                    market_data = await self.exchange_manager.get_market_data_for_strategy(
                        exchange_name, pair, ['1h', '15m']
                    )
                    
                    if not market_data:
                        continue
                        
                    # Update strategy with market data
                    primary_timeframe = '1h'
                    if primary_timeframe in market_data:
                        await strategy_instance.update(market_data[primary_timeframe])
                    
                    # Check for exit signals
                    should_exit = await strategy_instance.should_exit()
                    
                    if should_exit:
                        exit_signals.append({
                            'pair': pair,
                            'signal': 'exit',
                            'strategy': strategy_name,
                            'exchange': exchange_name,
                            'reason': 'strategy_exit',
                            'details': {
                                'strategy_name': strategy_name,
                                'market_regime': getattr(strategy_instance.state, 'market_regime', 'unknown')
                            }
                        })
                        
                except Exception as e:
                    logger.error(f"Error checking exit signals for {strategy_name} on {pair}: {e}")
                    continue
                    
            return exit_signals
            
        except Exception as e:
            logger.error(f"Error checking exit signals for {pair} on {exchange_name}: {e}")
            return []
            
    async def apply_profit_protection(self, trade_data: Dict[str, Any], current_price: float) -> Tuple[bool, Optional[str]]:
        """Apply profit protection logic using existing strategy_pnl module"""
        try:
            # Import profit protection functions
            from strategy_pnl import check_profit_protection_enhanced
            
            # Create a state object for profit protection
            class ProfitProtectionState:
                def __init__(self):
                    self.profit_protection_active = False
                    self.peak_unrealized_pnl = 0.0
                    self.consecutive_decreases = 0
                    self.last_unrealized_pnl = 0.0
                    self.profit_protection_activated_at = None
                    
            state = ProfitProtectionState()
            
            # Calculate unrealized PnL
            entry_price = float(trade_data.get('entry_price', 0))
            position_size = float(trade_data.get('position_size', 0))
            
            if entry_price <= 0 or position_size <= 0:
                return False, None
                
            unrealized_pnl = (current_price - entry_price) * position_size
            
            # Check profit protection
            should_exit, reason, risk_data = check_profit_protection_enhanced(
                state=state,
                unrealized_pnl=unrealized_pnl,
                entry_price=entry_price,
                position_size=position_size,
                config=self.config,
                trade_id=trade_data.get('trade_id'),
                current_price=current_price
            )
            
            if should_exit:
                # Update trade data with risk management info
                await self.database_manager.update_trade(
                    trade_data['trade_id'],
                    {
                        'profit_protection': 'active' if state.profit_protection_active else 'inactive',
                        'unrealized_pnl': unrealized_pnl,
                        'highest_price': max(current_price, float(trade_data.get('highest_price', 0)))
                    }
                )
                
            return should_exit, reason
            
        except Exception as e:
            logger.error(f"Error applying profit protection: {e}")
            return False, None
            
    async def apply_trailing_stop(self, trade_data: Dict[str, Any], current_price: float) -> Tuple[bool, Optional[str]]:
        """Apply trailing stop logic using existing strategy_pnl module"""
        try:
            # Import trailing stop functions
            from strategy_pnl import manage_trailing_stop_enhanced
            
            # Create a state object for trailing stop
            class TrailingStopState:
                def __init__(self):
                    self.trailing_stop_active = False
                    self.trailing_stop_level = 0.0
                    self.highest_price_seen = 0.0
                    self.position = 'long'  # Assuming long positions for now
                    
            state = TrailingStopState()
            
            # Get market data for ATR calculation
            exchange_name = trade_data.get('exchange')
            pair = trade_data.get('pair')
            
            if not exchange_name or not pair:
                return False, None
                
            market_data = await self.exchange_manager.get_ohlcv(exchange_name, pair, '1h', 50)
            
            if market_data is None:
                return False, None
                
            # Apply trailing stop
            should_exit, reason, risk_data = manage_trailing_stop_enhanced(
                state=state,
                current_price=current_price,
                entry_price=float(trade_data.get('entry_price', 0)),
                position_size=float(trade_data.get('position_size', 0)),
                config=self.config,
                ohlcv_data=market_data,
                trade_id=trade_data.get('trade_id')
            )
            
            if should_exit:
                # Update trade data with trailing stop info
                await self.database_manager.update_trade(
                    trade_data['trade_id'],
                    {
                        'trail_stop': 'active' if state.trailing_stop_active else 'inactive',
                        'trail_stop_trigger': state.trailing_stop_level,
                        'highest_price': state.highest_price_seen
                    }
                )
                
            return should_exit, reason
            
        except Exception as e:
            logger.error(f"Error applying trailing stop: {e}")
            return False, None
            
    async def get_strategy_performance(self, strategy_name: str, exchange_name: str, pair: str) -> Dict[str, Any]:
        """Get performance metrics for a specific strategy"""
        try:
            if self.database_manager:
                performance = await self.database_manager.get_strategy_performance(
                    strategy_name, exchange_name, pair
                )
                return performance or {}
            return {}
            
        except Exception as e:
            logger.error(f"Error getting strategy performance: {e}")
            return {}
            
    async def update_strategy_performance(self, strategy_name: str, exchange_name: str, pair: str, 
                                        trade_result: Dict[str, Any]) -> bool:
        """Update strategy performance metrics"""
        try:
            if self.database_manager:
                # Calculate performance metrics
                performance_data = {
                    'total_trades': 1,
                    'winning_trades': 1 if trade_result.get('pnl', 0) > 0 else 0,
                    'losing_trades': 1 if trade_result.get('pnl', 0) <= 0 else 0,
                    'total_pnl': trade_result.get('pnl', 0),
                    'win_rate': 100 if trade_result.get('pnl', 0) > 0 else 0,
                    'avg_win': trade_result.get('pnl', 0) if trade_result.get('pnl', 0) > 0 else 0,
                    'avg_loss': trade_result.get('pnl', 0) if trade_result.get('pnl', 0) <= 0 else 0,
                    'max_drawdown': 0,  # Would need to calculate from trade history
                    'sharpe_ratio': 0   # Would need to calculate from trade history
                }
                
                return await self.database_manager.update_strategy_performance(
                    strategy_name, exchange_name, pair, performance_data
                )
            return False
            
        except Exception as e:
            logger.error(f"Error updating strategy performance: {e}")
            return False
            
    async def get_enabled_strategies(self) -> List[str]:
        """Get list of enabled strategies"""
        return [name for name, data in self.strategies.items() if data['enabled']]
        
    async def get_strategy_status(self) -> Dict[str, Any]:
        """Get status of all strategies"""
        status = {}
        
        for strategy_name, strategy_data in self.strategies.items():
            status[strategy_name] = {
                'enabled': strategy_data['enabled'],
                'last_analysis': strategy_data['last_analysis'].isoformat() if strategy_data['last_analysis'] else None,
                'config': strategy_data['config']
            }
            
        return status
        
    async def cleanup_cache(self) -> None:
        """Clean up old cache entries"""
        try:
            current_time = datetime.utcnow()
            expired_keys = []
            
            for cache_key, cache_data in self.signal_cache.items():
                cache_time = datetime.fromisoformat(cache_data.get('timestamp', '1970-01-01T00:00:00'))
                if (current_time - cache_time).total_seconds() > 300:  # 5 minutes
                    expired_keys.append(cache_key)
                    
            for key in expired_keys:
                del self.signal_cache[key]
                
            logger.info(f"Cleaned up {len(expired_keys)} expired cache entries")
            
        except Exception as e:
            logger.error(f"Error cleaning up cache: {e}") 