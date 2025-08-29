from fix_unrealized_pnl_fees import calculate_unrealized_pnl_with_fees

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
import json

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
        
        # Explicit module mapping to match actual filenames
        strategy_module_map = {
            'vwma_hull': 'strategy.vwma_hull_strategy',
            'heikin_ashi': 'strategy.heikin_ashi_strategy',
            'multi_timeframe_confluence': 'strategy.multi_timeframe_confluence_strategy',
            'engulfing_multi_tf': 'strategy.engulfing_multi_tf',
        }
        for strategy_name, strategy_config in strategies_config.items():
            if not strategy_config.get('enabled', False):
                continue
            try:
                module_name = strategy_module_map.get(strategy_name)
                if not module_name:
                    logger.error(f"No module mapping for strategy: {strategy_name}")
                    continue
                class_name = strategy_classes[strategy_name]
                module = importlib.import_module(module_name)
                strategy_class = getattr(module, class_name)
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
            # CRITICAL FIX: Check market regime protection before analysis
            market_regime_ok = await self._check_market_regime_protection(exchange_name, pair)
            if not market_regime_ok:
                return {
                    'pair': pair,
                    'exchange': exchange_name,
                    'timestamp': datetime.utcnow().isoformat(),
                    'strategies': {},
                    'consensus': {'signal': 'hold', 'reason': 'Market regime protection active'}
                }
            
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
                    # Update strategy with market data (use primary timeframe)
                    primary_timeframe = timeframes[0]
                    if primary_timeframe in market_data:
                        await strategy_instance.update(market_data[primary_timeframe])
                    # Generate signal (expecting 4-tuple: signal, confidence, strength, reason)
                    signal_result = await strategy_instance.generate_signal(
                        market_data[primary_timeframe], None, pair, primary_timeframe
                    )
                    if isinstance(signal_result, (tuple, list)) and len(signal_result) >= 4:
                        signal, confidence, strength, reason = signal_result
                    else:
                        signal, confidence, strength = signal_result[:3]
                        reason = ''
                    # Store results
                    analysis_results['strategies'][strategy_name] = {
                        'signal': signal,
                        'confidence': confidence,
                        'strength': strength,
                        'reason': reason,
                        'market_regime': getattr(strategy_instance.state, 'market_regime', 'unknown'),
                        'timestamp': datetime.utcnow().isoformat()
                    }
                    # Update last analysis time
                    strategy_data['last_analysis'] = datetime.utcnow()
                except Exception as e:
                    logger.error(f"Error analyzing {pair} on {exchange_name} with {strategy_name}: {e}")
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
        """Calculate consensus from multiple strategy results, including reasons."""
        try:
            valid_signals = []
            total_confidence = 0
            total_strength = 0
            signal_counts = {'buy': 0, 'sell': 0, 'hold': 0}
            reasons = []
            for strategy_name, result in strategy_results.items():
                if 'error' in result:
                    continue
                signal = result.get('signal', 'hold')
                confidence = result.get('confidence', 0)
                strength = result.get('strength', 0)
                reason = result.get('reason', '')
                if signal in ['buy', 'sell', 'hold']:
                    valid_signals.append({
                        'strategy': strategy_name,
                        'signal': signal,
                        'confidence': confidence,
                        'strength': strength,
                        'reason': reason
                    })
                    signal_counts[signal] += 1
                    total_confidence += confidence
                    total_strength += strength
                    if reason:
                        reasons.append(f"{strategy_name}: {reason}")
            if not valid_signals:
                return {
                    'signal': 'hold',
                    'confidence': 0,
                    'strength': 0,
                    'agreement': 0,
                    'participating_strategies': 0,
                    'reason': ''
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
            consensus_reason = ' | '.join(reasons) if reasons else ''
            return {
                'signal': consensus_signal,
                'confidence': avg_confidence,
                'strength': avg_strength,
                'agreement': agreement,
                'participating_strategies': total_strategies,
                'signal_distribution': signal_counts,
                'reason': consensus_reason
            }
        except Exception as e:
            logger.error(f"Error calculating consensus: {e}")
            return {
                'signal': 'hold',
                'confidence': 0,
                'strength': 0,
                'agreement': 0,
                'participating_strategies': 0,
                'reason': ''
            }

    async def check_entry_signals(self, exchange_name: str, pair: str) -> List[Dict[str, Any]]:
        """Check for entry signals across all strategies, including detailed reason."""
        try:
            # Analyze the pair
            analysis = await self.analyze_pair(exchange_name, pair)
            if not analysis or 'strategies' not in analysis:
                return []
                
            signals = []
            
            # Check individual strategy signals first
            for strategy_name, strategy_result in analysis['strategies'].items():
                if 'error' in strategy_result:
                    continue
                    
                signal = strategy_result.get('signal', 'hold')
                confidence = strategy_result.get('confidence', 0)
                reason = strategy_result.get('reason', '')
                
                # Lower threshold for individual strategies
                min_confidence = 0.5  # Lower threshold for individual strategies
                
                if signal in ['buy', 'sell'] and confidence >= min_confidence:
                    signals.append({
                        'pair': pair,
                        'signal': signal,
                        'confidence': confidence,
                        'strength': strategy_result.get('strength', 0),
                        'strategy': strategy_name,
                        'exchange': exchange_name,
                        'reason': reason,
                        'details': {
                            'strategy_name': strategy_name,
                            'market_regime': strategy_result.get('market_regime', 'unknown')
                        }
                    })
            
            # Also check consensus if available
            if 'consensus' in analysis:
                consensus = analysis['consensus']
                min_confidence = self.config.get('strategy_manager', {}).get('min_confidence', 0.6)
                min_agreement = self.config.get('strategy_manager', {}).get('min_agreement', 50)
                
                if (consensus['signal'] in ['buy', 'sell'] and 
                    consensus['confidence'] >= min_confidence and
                    consensus['agreement'] >= min_agreement):
                    signals.append({
                        'pair': pair,
                        'signal': consensus['signal'],
                        'confidence': consensus['confidence'],
                        'strength': consensus['strength'],
                        'strategy': 'consensus',
                        'exchange': exchange_name,
                        'reason': consensus.get('reason', ''),
                        'details': {
                            'agreement': consensus['agreement'],
                            'participating_strategies': consensus['participating_strategies'],
                            'signal_distribution': consensus['signal_distribution'],
                            'individual_signals': analysis['strategies']
                        }
                    })
            
            return signals
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
                    exit_result = await strategy_instance.should_exit()
                    
                    # Handle both tuple and boolean returns for compatibility
                    if isinstance(exit_result, tuple) and len(exit_result) >= 2:
                        should_exit, reason = exit_result
                    elif isinstance(exit_result, bool):
                        should_exit = exit_result
                        reason = 'strategy_exit' if should_exit else None
                    else:
                        logger.warning(f"Unexpected return type from {strategy_name}.should_exit(): {type(exit_result)}")
                        continue
                        
                    if should_exit:
                        exit_signals.append({
                            'pair': pair,
                            'signal': 'exit',
                            'strategy': strategy_name,
                            'exchange': exchange_name,
                            'reason': reason or 'strategy_exit',
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
            from strategy.strategy_pnl_enhanced import check_profit_protection_enhanced, restore_profit_protection_state
            class ProfitProtectionState:
                def __init__(self):
                    self.profit_protection_active = False
                    self.peak_unrealized_pnl = 0.0
                    self.consecutive_decreases = 0
                    self.last_unrealized_pnl = 0.0
                    self.profit_protection_activated_at = None
                    self.position = 'long'  # Default position side
            
            # CRITICAL FIX: Load existing state from database instead of creating fresh state
            state = await self._load_protection_state(trade_data.get('trade_id'), ProfitProtectionState())
            entry_price = trade_data.get('entry_price')
            position_size = trade_data.get('position_size')
            if entry_price is None or position_size is None:
                logger.error(f"[ProfitProtection] Trade {trade_data.get('trade_id')} missing entry_price or position_size. Skipping.")
                return False, None
            try:
                entry_price = float(entry_price)
                position_size = float(position_size)
            except Exception as e:
                logger.error(f"[ProfitProtection] Trade {trade_data.get('trade_id')} invalid entry_price or position_size: {e}. Skipping.")
                return False, None
            if entry_price <= 0 or position_size <= 0:
                logger.error(f"[ProfitProtection] Trade {trade_data.get('trade_id')} entry_price or position_size <= 0. Skipping.")
                return False, None
            unrealized_pnl = calculate_unrealized_pnl_with_fees(entry_price, current_price, position_size)
            should_exit, reason, risk_data = check_profit_protection_enhanced(
                state=state,
                unrealized_pnl=unrealized_pnl,
                entry_price=entry_price,
                position_size=position_size,
                config=self.config,
                trade_id=trade_data.get('trade_id'),
                current_price=current_price
            )
            if should_exit and self.database_manager:
                await self.database_manager.update_trade(
                    trade_data['trade_id'],
                    {
                        'profit_protection': 'active' if state.profit_protection_active else 'inactive',
                        'unrealized_pnl': unrealized_pnl,
                        'highest_price': max(current_price, float(trade_data.get('highest_price', 0) or 0))
                    }
                )
            # CRITICAL FIX: Save state back to database
            await self._save_protection_state(trade_data.get('trade_id'), state, risk_data)
            return should_exit, reason
        except Exception as e:
            logger.error(f"Error applying profit protection: {e}")
            return False, None

    async def apply_trailing_stop(self, trade_data: Dict[str, Any], current_price: float) -> Tuple[bool, Optional[str]]:
        """Apply trailing stop logic using existing strategy_pnl module"""
        try:
            from strategy.strategy_pnl_enhanced import manage_trailing_stop_enhanced
            class TrailingStopState:
                def __init__(self):
                    self.trailing_stop_active = False
                    self.trailing_stop_level = 0.0
                    self.highest_price_seen = 0.0
                    self.lowest_price_seen = float('inf')
                    self.position = 'long'
            
            # CRITICAL FIX: Load existing trailing stop state from database
            state = await self._load_trailing_stop_state(trade_data.get('trade_id'), TrailingStopState())
            exchange_name = trade_data.get('exchange')
            pair = trade_data.get('pair')
            entry_price = trade_data.get('entry_price')
            position_size = trade_data.get('position_size')
            if not exchange_name or not pair:
                logger.error(f"[TrailingStop] Trade {trade_data.get('trade_id')} missing exchange or pair. Skipping.")
                return False, None
            if entry_price is None or position_size is None:
                logger.error(f"[TrailingStop] Trade {trade_data.get('trade_id')} missing entry_price or position_size. Skipping.")
                return False, None
            try:
                entry_price = float(entry_price)
                position_size = float(position_size)
            except Exception as e:
                logger.error(f"[TrailingStop] Trade {trade_data.get('trade_id')} invalid entry_price or position_size: {e}. Skipping.")
                return False, None
            if entry_price <= 0 or position_size <= 0:
                logger.error(f"[TrailingStop] Trade {trade_data.get('trade_id')} entry_price or position_size <= 0. Skipping.")
                return False, None
            if not self.exchange_manager:
                logger.error(f"[TrailingStop] Exchange manager not initialized. Skipping.")
                return False, None
            market_data = await self.exchange_manager.get_ohlcv(exchange_name, pair, '1h', 50)
            if market_data is None:
                logger.error(f"[TrailingStop] Trade {trade_data.get('trade_id')} missing market data. Skipping.")
                return False, None
            should_exit, reason, risk_data = manage_trailing_stop_enhanced(
                state=state,
                current_price=current_price,
                entry_price=entry_price,
                position_size=position_size,
                config=self.config,
                ohlcv_data=market_data,
                trade_id=trade_data.get('trade_id')
            )
            if should_exit and self.database_manager:
                await self.database_manager.update_trade(
                    trade_data['trade_id'],
                    {
                        'trail_stop': 'active' if state.trailing_stop_active else 'inactive',
                        'trail_stop_trigger': state.trailing_stop_level,
                        'highest_price': state.highest_price_seen
                    }
                )
            # CRITICAL FIX: Save trailing stop state back to database
            await self._save_trailing_stop_state(trade_data.get('trade_id'), state, risk_data)
            return should_exit, reason
        except Exception as e:
            logger.error(f"Error applying trailing stop: {e}")
            return False, None
    
    async def _load_protection_state(self, trade_id: str, default_state):
        """Load profit protection state from database"""
        try:
            if hasattr(self, 'database_manager') and self.database_manager:
                # Load from trailing_stops table if it exists
                query = """
                    SELECT profit_protection_enabled, profit_protection_active, 
                           profit_lock_percentage, highest_price_seen, recovery_data
                    FROM trading.trailing_stops 
                    WHERE trade_id = %s
                """
                result = await self.database_manager.execute_single_query(query, (trade_id,))
                if result:
                    # Parse recovery_data JSON for detailed state
                    recovery_data = result.get('recovery_data', {})
                    if isinstance(recovery_data, str):
                        import json
                        recovery_data = json.loads(recovery_data) if recovery_data else {}
                    
                    default_state.profit_protection_active = result.get('profit_protection_active', False)
                    default_state.peak_unrealized_pnl = float(recovery_data.get('peak_unrealized_pnl', 0.0))
                    default_state.consecutive_decreases = int(recovery_data.get('consecutive_decreases', 0))
                    default_state.last_unrealized_pnl = float(recovery_data.get('last_unrealized_pnl', 0.0))
                    default_state.profit_protection_activated_at = recovery_data.get('profit_protection_activated_at')
                    default_state.position = 'long'  # Assume long positions for now
                    logger.info(f"[StateRestore] Loaded protection state for {trade_id}: active={default_state.profit_protection_active}, peak={default_state.peak_unrealized_pnl}")
                else:
                    logger.debug(f"[StateRestore] No existing protection state found for {trade_id}, using defaults")
        except Exception as e:
            logger.error(f"Error loading protection state for {trade_id}: {e}")
        return default_state
    
    async def _save_protection_state(self, trade_id: str, state, risk_data: dict):
        """Save profit protection state to database"""
        try:
            if hasattr(self, 'database_manager') and self.database_manager:
                # Prepare recovery data with detailed state
                recovery_data = {
                    'peak_unrealized_pnl': state.peak_unrealized_pnl,
                    'consecutive_decreases': state.consecutive_decreases,
                    'last_unrealized_pnl': state.last_unrealized_pnl,
                    'profit_protection_activated_at': state.profit_protection_activated_at.isoformat() if state.profit_protection_activated_at else None
                }
                
                # Update or insert trailing_stops record
                upsert_query = """
                    INSERT INTO trading.trailing_stops 
                    (trade_id, profit_protection_enabled, profit_protection_active, recovery_data, created_at, updated_at)
                    VALUES (%s, true, %s, %s, NOW(), NOW())
                    ON CONFLICT (trade_id) DO UPDATE SET
                        profit_protection_active = EXCLUDED.profit_protection_active,
                        recovery_data = EXCLUDED.recovery_data,
                        updated_at = NOW()
                """
                await self.database_manager.execute_query(
                    upsert_query, 
                    (trade_id, state.profit_protection_active, json.dumps(recovery_data))
                )
                logger.debug(f"[StateSave] Saved protection state for {trade_id}: active={state.profit_protection_active}")
        except Exception as e:
            logger.error(f"Error saving protection state for {trade_id}: {e}")
    
    async def _load_trailing_stop_state(self, trade_id: str, default_state):
        """Load trailing stop state from database"""
        try:
            if hasattr(self, 'database_manager') and self.database_manager:
                # Load from trailing_stops table
                query = """
                    SELECT trailing_enabled, is_active, current_stop_price, 
                           highest_price_seen, lowest_price_seen, recovery_data
                    FROM trading.trailing_stops 
                    WHERE trade_id = %s
                """
                result = await self.database_manager.execute_single_query(query, (trade_id,))
                if result:
                    # Parse recovery_data JSON for detailed state
                    recovery_data = result.get('recovery_data', {})
                    if isinstance(recovery_data, str):
                        import json
                        recovery_data = json.loads(recovery_data) if recovery_data else {}
                    
                    default_state.trailing_stop_active = result.get('is_active', False)
                    default_state.trailing_stop_level = float(result.get('current_stop_price', 0.0))
                    default_state.highest_price_seen = float(result.get('highest_price_seen', 0.0))
                    default_state.lowest_price_seen = float(result.get('lowest_price_seen', float('inf')))
                    default_state.position = 'long'  # Assume long positions
                    logger.info(f"[StateRestore] Loaded trailing state for {trade_id}: active={default_state.trailing_stop_active}, level={default_state.trailing_stop_level}")
                else:
                    logger.debug(f"[StateRestore] No existing trailing stop state found for {trade_id}, using defaults")
        except Exception as e:
            logger.error(f"Error loading trailing stop state for {trade_id}: {e}")
        return default_state
    
    async def _save_trailing_stop_state(self, trade_id: str, state, risk_data: dict):
        """Save trailing stop state to database"""
        try:
            if hasattr(self, 'database_manager') and self.database_manager:
                # Get trade details for insertion
                trade_data = await self.database_manager.get_trade_by_id(trade_id)
                if not trade_data:
                    logger.error(f"Trade {trade_id} not found, cannot save trailing stop state")
                    return
                
                # Prepare recovery data with detailed state 
                recovery_data = {
                    'trailing_stop_active': state.trailing_stop_active,
                    'trailing_stop_level': state.trailing_stop_level,
                    'highest_price_seen': state.highest_price_seen,
                    'lowest_price_seen': state.lowest_price_seen if state.lowest_price_seen != float('inf') else None,
                    'last_update': datetime.utcnow().isoformat()
                }
                
                # Update or insert trailing_stops record with all required fields
                upsert_query = """
                    INSERT INTO trading.trailing_stops 
                    (trade_id, exchange, pair, trailing_enabled, is_active, current_stop_price, 
                     highest_price_seen, lowest_price_seen, entry_price, current_price, 
                     position_side, recovery_data, created_at, updated_at)
                    VALUES (%s, %s, %s, true, %s, %s, %s, %s, %s, %s, 'long', %s, NOW(), NOW())
                    ON CONFLICT (trade_id) DO UPDATE SET
                        is_active = EXCLUDED.is_active,
                        current_stop_price = EXCLUDED.current_stop_price,
                        highest_price_seen = EXCLUDED.highest_price_seen,
                        lowest_price_seen = EXCLUDED.lowest_price_seen,
                        current_price = EXCLUDED.current_price,
                        recovery_data = EXCLUDED.recovery_data,
                        updated_at = NOW()
                """
                
                await self.database_manager.execute_query(
                    upsert_query, 
                    (
                        trade_id, 
                        trade_data.get('exchange'), 
                        trade_data.get('pair'),
                        state.trailing_stop_active,
                        state.trailing_stop_level,
                        state.highest_price_seen,
                        state.lowest_price_seen if state.lowest_price_seen != float('inf') else None,
                        trade_data.get('entry_price'),
                        trade_data.get('current_price'),
                        json.dumps(recovery_data)
                    )
                )
                logger.debug(f"[StateSave] Saved trailing stop state for {trade_id}: active={state.trailing_stop_active}")
        except Exception as e:
            logger.error(f"Error saving trailing stop state for {trade_id}: {e}")
    
    async def _check_market_regime_protection(self, exchange_name: str, pair: str) -> bool:
        """Check if market regime allows new positions (BTC/ETH correlation protection)"""
        try:
            # Skip protection for BTC and ETH themselves
            if pair.upper().startswith(('BTC', 'ETH')):
                return True
            
            # Get BTC data for correlation analysis
            btc_data = await self.exchange_manager.get_ohlcv(exchange_name, 'BTC/USDC', '1h', limit=24)
            if btc_data is None or len(btc_data) < 24:
                logger.warning("[MarketRegime] Cannot get BTC data, allowing trades")
                return True
            
            # Calculate BTC 4-hour price change
            current_btc = btc_data['close'].iloc[-1]
            btc_4h_ago = btc_data['close'].iloc[-4] if len(btc_data) >= 4 else btc_data['close'].iloc[0]
            btc_4h_change = (current_btc - btc_4h_ago) / btc_4h_ago
            
            # Block new positions if BTC down >3% in 4 hours
            if btc_4h_change < -0.03:
                logger.warning(f"[MarketRegime] BTC down {btc_4h_change:.2%} in 4h, blocking new {pair} positions")
                return False
            
            # Additional check: BTC 1-hour momentum
            btc_1h_ago = btc_data['close'].iloc[-2] if len(btc_data) >= 2 else current_btc
            btc_1h_change = (current_btc - btc_1h_ago) / btc_1h_ago
            
            # Block if BTC down >2% in 1 hour (flash crash protection)
            if btc_1h_change < -0.02:
                logger.warning(f"[MarketRegime] BTC down {btc_1h_change:.2%} in 1h, blocking new {pair} positions")
                return False
            
            # Try to get ETH data for additional confirmation
            try:
                eth_data = await self.exchange_manager.get_ohlcv(exchange_name, 'ETH/USDC', '1h', limit=4)
                if eth_data is not None and len(eth_data) >= 4:
                    current_eth = eth_data['close'].iloc[-1]
                    eth_4h_ago = eth_data['close'].iloc[-4]
                    eth_4h_change = (current_eth - eth_4h_ago) / eth_4h_ago
                    
                    # If both BTC and ETH down >2% in 4h, block trades
                    if btc_4h_change < -0.02 and eth_4h_change < -0.02:
                        logger.warning(f"[MarketRegime] Both BTC ({btc_4h_change:.2%}) and ETH ({eth_4h_change:.2%}) down, blocking {pair}")
                        return False
            except Exception as e:
                logger.debug(f"Could not get ETH data for regime check: {e}")
            
            logger.debug(f"[MarketRegime] BTC 4h: {btc_4h_change:.2%}, 1h: {btc_1h_change:.2%} - allowing {pair} trades")
            return True
            
        except Exception as e:
            logger.error(f"Error in market regime protection: {e}")
            return True  # Allow trades if check fails
            
    async def get_strategy_performance(self, strategy_name: str, exchange: str = "unknown", pair: str = "unknown") -> Dict[str, Any]:
        """Get performance metrics for a specific strategy"""
        try:
            if self.database_manager:
                # If exchange and pair are not provided, get the most recent performance for the strategy
                if exchange == "unknown" or pair == "unknown":
                    # Get all performance records for this strategy and return the most recent
                    query = """
                        SELECT * FROM trading.strategy_performance 
                        WHERE strategy_name = %s
                        ORDER BY last_updated DESC
                        LIMIT 1
                    """
                    result = await self.database_manager.execute_single_query(query, (strategy_name,))
                    return result or {}
                else:
                    performance = await self.database_manager.get_strategy_performance(
                        strategy_name, exchange, pair
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

    def get_strategies(self):
        """Return all enabled strategy instances as a list."""
        return [data['instance'] for data in self.strategies.values() if data['enabled']]

    def get_strategy(self, exchange_name=None):
        """Return the strategy instance for the given exchange. Currently always returns 'heikin_ashi'."""
        strat = self.strategies.get('heikin_ashi', {})
        instance = strat.get('instance')
        logger.debug(f"[StrategyManager.get_strategy] Returning: {type(instance)} {instance}")
        return instance 