"""
Base strategy module for the crypto trading bot.
Defines the interface for all trading strategies.
"""
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any, Union, Tuple
from dataclasses import dataclass
import logging
from datetime import datetime
import pandas as pd
import numpy as np

# from analysis import TechnicalAnalysis, PatternRecognition, SignalGenerator
from core.database_manager import DatabaseManager
#from exchange import BaseExchange
from strategy.condition_logger import ConditionLogger
from strategy.strategy_pnl import calculate_unrealized_pnl, check_profit_protection, check_profit_protection_enhanced, restore_profit_protection_state

logger = logging.getLogger(__name__)

@dataclass
class StrategyState:
    """Current state of a trading strategy."""
    pair: str
    position: str  # 'long' or 'none' (spot market only)
    entry_price: Optional[float]
    stop_loss: Optional[float]
    take_profit: Optional[float]
    position_size: Optional[float]
    entry_time: Optional[datetime]
    last_signal: Optional[str]
    last_signal_time: Optional[datetime]
    indicators: Dict[str, Any]
    patterns: Dict[str, Any]
    market_regime: str
    performance: Dict[str, float]
    # Enhanced state for optimizer integration
    current_parameters: Dict[str, Any]
    parameter_cache_time: Optional[datetime]
    last_optimization_time: Optional[datetime]
    # Risk management fields
    profit_protection_active: bool = False
    peak_unrealized_pnl: float = 0.0
    consecutive_decreases: int = 0
    last_unrealized_pnl: float = 0.0
    trailing_stop_active: bool = False
    trailing_stop_level: float = 0.0
    highest_price_seen: float = 0.0

class BaseStrategy(ABC):
    """Base class for all trading strategies."""

    def __init__(
        self,
        config: Dict[str, Any],
        exchange: Any,
        database: DatabaseManager,
        redis_client=None
    ):
        """Initialize the strategy."""
        super().__init__()
        self.config = config
        self.exchange = exchange
        self.database = database
        self.redis_client = redis_client
        
        # Initialize components with safe config access
        if isinstance(config, dict):
            optimizer_config = config.get('optimizer', {})
            analyzer_config = config.get('analyzer', {})
            feedback_config = config.get('feedback', {})
        else:
            optimizer_config = getattr(config, 'optimizer', {})
            analyzer_config = getattr(config, 'analyzer', {})
            feedback_config = getattr(config, 'feedback', {})
            
        # Remove or comment out all references to TechnicalAnalysis
        # from analysis import TechnicalAnalysis, PatternRecognition, SignalGenerator
        # self.technical_analysis = TechnicalAnalysis(config)
        # self.pattern_recognition = PatternRecognition(config)
        # self.signal_generator = SignalGenerator(config)
        
        # Ensure trade tracking properties exist
        self.trade_id = None
        
        # Initialize state with all required fields for PnL calculations
        self.state = StrategyState(
            pair='',
            position='none',  # Use 'none' as default, not None. Only 'long' or 'none' allowed for spot market.
            entry_price=0.0,  # Use 0.0 instead of None
            stop_loss=None,
            take_profit=None,
            position_size=0.0,  # Use 0.0 instead of None
            entry_time=None,
            last_signal=None,
            last_signal_time=None,
            indicators={},
            patterns={},
            market_regime='unknown',
            performance={},
            current_parameters={},
            parameter_cache_time=None,
            last_optimization_time=None,
            profit_protection_active=False,
            peak_unrealized_pnl=0.0,
            consecutive_decreases=0,
            last_unrealized_pnl=0.0,
            trailing_stop_active=False,
            trailing_stop_level=0.0,
            highest_price_seen=0.0
        )
        
        # Initialize additional state attributes needed for risk management
        self.state.profit_protection_active = False
        self.state.peak_unrealized_pnl = 0.0
        self.state.consecutive_decreases = 0
        self.state.last_unrealized_pnl = 0.0
        self.state.trailing_stop_active = False
        self.state.trailing_stop_level = 0.0
        self.state.highest_price_seen = 0.0
        
        self._condition_logger = ConditionLogger(
            strategy_name=self.__class__.__name__,
            redis_client=redis_client
        )
        
        # Initialize strategy optimizer if Redis is available
        self._strategy_optimizer = None
        self._strategy_analyzer = None
        self._feedback_manager = None
        
        if redis_client:
            try:
                from strategy.strategy_optimizer import StrategyOptimizer
                from strategy.strategy_analyzer import StrategyAnalyzer
                from strategy.strategy_feedback_manager import StrategyFeedbackManager
                
                self._strategy_optimizer = StrategyOptimizer(
                    redis_client=redis_client,
                    database_manager=database,
                    config=optimizer_config
                )
                
                self._strategy_analyzer = StrategyAnalyzer(
                    redis_client=redis_client,
                    database_manager=database,
                    config=analyzer_config
                )
                
                self._feedback_manager = StrategyFeedbackManager(
                    redis_client=redis_client,
                    optimizer=self._strategy_optimizer,
                    analyzer=self._strategy_analyzer,
                    config=feedback_config
                )
                
                logger.info(f"Initialized {self.__class__.__name__} with optimizer, analyzer, and feedback manager")
            except Exception as e:
                logger.warning(f"Failed to initialize optimization components: {str(e)}")

    @abstractmethod
    async def initialize(self, pair: str) -> None:
        """Initialize the strategy for a specific trading pair."""
        pass

    @abstractmethod
    async def update(self, ohlcv: pd.DataFrame) -> None:
        """Update strategy state with new market data."""
        pass

    @abstractmethod
    async def generate_signal(self, ohlcv: pd.DataFrame, indicators_cache: Optional[dict] = None, pair: Optional[str] = None, timeframe: Optional[str] = None) -> Tuple[str, float, float]:
        """Generate trading signal based on current state, using indicator cache if provided."""
        raise NotImplementedError("generate_signal must be implemented by subclasses")

    @abstractmethod
    async def calculate_position_size(self, signal_type: str) -> float:
        """Calculate position size based on risk management rules."""
        pass

    @abstractmethod
    async def should_exit(self) -> bool:
        """Check if current position should be closed."""
        pass

    async def execute_trade(self, signal_type: str) -> bool:
        """Execute a trade based on the signal."""
        try:
            # For SPOT trading, we only allow 'buy' signals
            if signal_type != 'buy':
                logger.warning(f"Invalid signal type for SPOT trading: {signal_type}. Only 'buy' signals are allowed.")
                return False
            
            # Calculate position size
            position_size = await self.calculate_position_size(signal_type)
            
            # Get current price
            ticker = await self.exchange.get_ticker(self.state.pair)
            current_price = ticker['last']
            
            # Calculate stop loss and take profit
            stop_loss, take_profit = self._calculate_levels(
                signal_type,
                current_price
            )
            
            # Execute order
            order = await self.exchange.create_order(
                self.state.pair,
                'market',
                'buy',
                position_size
            )
            filled_amount = order.get('filled', position_size)
            
            # Update state
            self.state.position = 'long'  # Always 'long' for SPOT trading
            self.state.entry_price = current_price
            self.state.stop_loss = stop_loss
            self.state.take_profit = take_profit
            self.state.position_size = filled_amount
            self.state.entry_time = datetime.utcnow()
            logger.info(f"Trade executed: requested={position_size}, filled={filled_amount}")
            
            # Store trade in database
            await self.database.store_trade({
                'pair': self.state.pair,
                'type': 'buy',  # Always 'buy' for SPOT trading
                'price': current_price,
                'size': filled_amount,
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'timestamp': datetime.utcnow()
            })
            
            logger.info(f"Executed buy order for {self.state.pair}")
            return True
            
        except Exception as e:
            logger.error(f"Error executing trade: {str(e)}")
            return False

    async def close_position(self) -> bool:
        """Close current position."""
        try:
            if self.state.position == 'none':
                return True
            
            # Get current price
            ticker = await self.exchange.get_ticker(self.state.pair)
            current_price = ticker['last']
            
            # Execute closing order
            if self.state.position_size is None:
                logger.error(f"Cannot close position: position_size is None for {self.state.pair}")
                return False
            close_amount = float(self.state.position_size)
            if self.state.position == 'long':
                order = await self.exchange.create_order(
                    self.state.pair,
                    'market',
                    'sell',
                    close_amount
                )
            else:  # short
                order = await self.exchange.create_order(
                    self.state.pair,
                    'market',
                    'buy',
                    close_amount
                )
            close_filled = order.get('filled', self.state.position_size)
            logger.info(f"Close executed: requested={self.state.position_size}, filled={close_filled}")
            
            # Calculate profit/loss
            pnl = (current_price - self.state.entry_price) * close_filled
            if self.state.position == 'short':
                pnl = -pnl
            
            # Log trade outcome to strategy optimizer
            trade_data = {
                'pair': self.state.pair,
                'entry_price': self.state.entry_price,
                'exit_price': current_price,
                'entry_time': self.state.entry_time.isoformat() if self.state.entry_time else None,
                'exit_time': datetime.utcnow().isoformat(),
                'position_type': self.state.position,
                'amount': close_filled,
                'pnl': pnl,
                'stop_loss': self.state.stop_loss,
                'take_profit': self.state.take_profit
            }
            is_profitable = pnl > 0
            await self.log_trade_outcome(trade_data, is_profitable)
            
            # Update performance
            self.state.performance['total_trades'] = self.state.performance.get('total_trades', 0) + 1
            if is_profitable:
                self.state.performance['winning_trades'] = self.state.performance.get('winning_trades', 0) + 1
            self.state.performance['total_pnl'] = self.state.performance.get('total_pnl', 0) + pnl
            self.state.performance['win_rate'] = (
                self.state.performance.get('winning_trades', 0) / 
                self.state.performance['total_trades']
            )
            
            # Reset state
            self.state.position = 'none'
            self.state.entry_price = None
            self.state.stop_loss = None
            self.state.take_profit = None
            self.state.position_size = None
            self.state.entry_time = None
            
            logger.info(f"Closed position for {self.state.pair} with PnL: {pnl}")
            return True
            
        except Exception as e:
            logger.error(f"Error closing position: {str(e)}")
            return False

    def _calculate_levels(
        self,
        signal_type: str,
        current_price: float
    ) -> Tuple[Optional[float], Optional[float]]:
        """Calculate stop loss and take profit levels."""
        try:
            # Get ATR for volatility-based levels
            atr = self.state.indicators.get('ATR', None)
            if atr is None:
                return None, None
            
            atr_value = atr.values[-1]
            
            if signal_type == 'buy':
                stop_loss = current_price - (2 * atr_value)
                take_profit = current_price + (3 * atr_value)
            else:  # sell
                stop_loss = current_price + (2 * atr_value)
                take_profit = current_price - (3 * atr_value)
            
            return stop_loss, take_profit
            
        except Exception as e:
            logger.error(f"Error calculating levels: {str(e)}")
            return None, None

    async def update_performance(self) -> None:
        """Update strategy performance metrics."""
        try:
            # Get current price
            ticker = await self.exchange.get_ticker(self.state.pair)
            current_price = ticker['last']
            
            if self.state.position != 'none':
                # Calculate unrealized PnL
                pnl = (current_price - self.state.entry_price) * self.state.position_size
                if self.state.position == 'short':
                    pnl = -pnl
                
                # Update performance
                self.state.performance['unrealized_pnl'] = pnl
                self.state.performance['current_drawdown'] = min(
                    self.state.performance.get('current_drawdown', 0),
                    pnl
                )
            
            # Store performance in database if method is available
            if hasattr(self.database, 'update_strategy_performance'):
                try:
                    strategy_name = self.__class__.__name__.lower().replace('strategy', '')
                    exchange_name = getattr(self, 'exchange_name', 'unknown')
                    await self.database.update_strategy_performance(
                        strategy_name,
                        exchange_name,
                        self.state.pair,
                        self.state.performance
                    )
                except Exception as e:
                    logger.warning(f"Could not update strategy performance in database: {e}")
            
        except Exception as e:
            logger.error(f"Error updating performance: {str(e)}")

    async def _log_condition(self, name, value, description, result, condition_type=None, pair=None, reason=None, market_regime=None, volatility=None, context=None):
        desc = description or name
        # Pass available context/market_regime/volatility
        current_market_regime = getattr(self.state, 'market_regime', market_regime) if hasattr(self, 'state') else market_regime
        current_volatility = getattr(self.state, 'volatility', volatility) if hasattr(self, 'state') else volatility
        await self._condition_logger.log_condition(name, value, desc, result, condition_type, market_regime=current_market_regime, volatility=current_volatility, context=context)

    async def check_entry_signals(self, market_data, predictions, indicators_cache=None):
        """
        Check for entry signals for all pairs in market_data, passing ohlcv and indicator cache to generate_signal.
        Args:
            market_data: Dict of pair -> DataFrame or dict of timeframes
            predictions: Any ML predictions (optional)
            indicators_cache: Centralized indicator cache (should be passed from orchestrator/manager)
        Returns:
            List of entry signal dicts
        Note:
            All subclasses should expect to receive ohlcv, indicators_cache, pair, and timeframe in generate_signal.
        """
        entry_signals = []
        for pair, tf_data in market_data.items():
            try:
                # Handle nested timeframe data structure
                ohlcv = None
                timeframe = None
                if isinstance(tf_data, dict):
                    if len(tf_data) == 0:
                        logger.warning(f"[{self.__class__.__name__}] No timeframes available for {pair}")
                        continue
                    sorted_tfs = sorted(tf_data.keys(), key=lambda x: int(''.join(filter(str.isdigit, x))) if any(c.isdigit() for c in x) else 0, reverse=True)
                    timeframe = sorted_tfs[0]
                    ohlcv = tf_data[timeframe]
                    logger.debug(f"[{self.__class__.__name__}] Using timeframe {timeframe} for {pair}")
                else:
                    ohlcv = tf_data
                if not isinstance(ohlcv, pd.DataFrame):
                    logger.warning(f"[{self.__class__.__name__}] Entry signal check: ohlcv for {pair} is not a DataFrame, type: {type(ohlcv)}")
                    continue
                required_cols = ['open', 'high', 'low', 'close', 'volume']
                missing_cols = [col for col in required_cols if col not in ohlcv.columns]
                if missing_cols:
                    logger.warning(f"[{self.__class__.__name__}] Entry signal check: ohlcv for {pair} is missing columns: {missing_cols}")
                    continue
                if len(ohlcv) < 10:
                    logger.warning(f"[{self.__class__.__name__}] Insufficient data for {pair}: {len(ohlcv)} rows")
                    continue
                await self.initialize(pair)
                await self.update(ohlcv)
                signal_result = await self.generate_signal(ohlcv, indicators_cache, pair, timeframe)
                if signal_result is None:
                    logger.warning(f"[{self.__class__.__name__}] generate_signal returned None for {pair}")
                    continue
                if not isinstance(signal_result, (tuple, list)) or len(signal_result) < 3:
                    logger.warning(f"[{self.__class__.__name__}] generate_signal returned invalid format for {pair}: {signal_result}")
                    continue
                signal, confidence, *_rest = signal_result
                reason = _rest[1] if len(_rest) > 1 else (_rest[0] if len(_rest) > 0 else '')
                if signal in ('buy', 'sell'):
                    entry_signals.append({
                        'pair': pair,
                        'signal': signal,
                        'confidence': confidence,
                        'strategy': self.__class__.__name__.lower().replace('strategy', ''),
                        'reason': reason
                    })
                    logger.info(f"[{self.__class__.__name__}] Generated {signal} signal for {pair} with confidence {confidence:.2f} reason: {reason}")
                else:
                    logger.debug(f"[{self.__class__.__name__}] No entry signal for {pair}: {signal}")
            except Exception as e:
                logger.error(f"[{self.__class__.__name__}] Error checking entry signals for {pair}: {str(e)}", exc_info=True)
                continue
        return entry_signals

    async def check_exit_signals(self, market_data, predictions):
        """Check for exit signals for all pairs in market_data."""
        exit_signals = []
        for pair, ohlcv in market_data.items():
            try:
                # Defensive check - ensure we have a proper DataFrame with required columns
                if not isinstance(ohlcv, pd.DataFrame):
                    logger.warning(f"[{self.__class__.__name__}] Exit signal check: ohlcv for {pair} is not a DataFrame")
                    continue
                    
                required_cols = ['open', 'high', 'low', 'close', 'volume']
                missing_cols = [col for col in required_cols if col not in ohlcv.columns]
                if missing_cols:
                    logger.warning(f"[{self.__class__.__name__}] Exit signal check: ohlcv for {pair} is missing columns: {missing_cols}")
                    continue
                
                # Now proceed with normal exit check
                await self.initialize(pair)
                await self.update(ohlcv)
                should_exit = await self.should_exit()
                if should_exit:
                    exit_signals.append({
                        'pair': pair,
                        'signal': 'exit',
                        'strategy': self.__class__.__name__.lower().replace('strategy', '')
                    })
            except Exception as e:
                logger.error(f"[{self.__class__.__name__}] Error checking exit signals for {pair}: {str(e)}", exc_info=True)
                continue
        return exit_signals

    async def check_exit(self, market_data=None, predictions=None, trade_id=None):
        """
        Base implementation of the check_exit method called by the orchestrator.
        Checks if the current position should be exited.
        
        Args:
            market_data: Optional OHLCV data to use for analysis
            predictions: Optional ML predictions to consider
            trade_id: Optional trade ID for logging purposes
        
        Returns:
            True if exit is required, otherwise False
        """
        try:
            # If market_data is provided, update the strategy state
            if market_data is not None and isinstance(market_data, pd.DataFrame) and not market_data.empty:
                await self.update(market_data)
            
            # Set trade_id if provided
            if hasattr(self, 'trade_id') and trade_id is not None:
                self.trade_id = trade_id
                
            # Call the should_exit method which should be implemented by subclasses
            return await self.should_exit()
        except Exception as e:
            logger.error(f"Error in check_exit: {e}")
            return False

    async def fallback_should_exit(self, ohlcv=None) -> bool:
        """Fallback exit logic: stop loss, take profit, and time-based exit. Can be called by all strategies."""
        import logging
        from datetime import datetime
        logger = logging.getLogger(__name__)
        try:
            if not hasattr(self, 'state') or self.state.position == 'none':
                logger.debug("[BaseStrategy] No open position, fallback_should_exit=False")
                return False
            # Use provided ohlcv or self._current_ohlcv
            if ohlcv is None and hasattr(self, '_current_ohlcv'):
                ohlcv = self._current_ohlcv
            if ohlcv is None:
                logger.warning("[BaseStrategy] No OHLCV data for fallback exit check.")
                return False
            current_price = ohlcv['close'].iloc[-1]
            pair = getattr(self.state, 'pair', 'N/A')
            # Stop Loss
            if self.state.stop_loss is not None and current_price <= self.state.stop_loss:
                logger.info(f"[BaseStrategy] Exiting {pair} due to stop loss: {current_price} <= {self.state.stop_loss}")
                return True
            # Take Profit
            if self.state.take_profit is not None and current_price >= self.state.take_profit:
                logger.info(f"[BaseStrategy] Exiting {pair} due to take profit: {current_price} >= {self.state.take_profit}")
                return True
            # Time-based exit (48h max hold)
            if self.state.entry_time:
                time_in_trade = datetime.utcnow() - self.state.entry_time
                if time_in_trade.total_seconds() > 172800:
                    logger.info(f"[BaseStrategy] Exiting {pair} due to max hold time exceeded: {time_in_trade}")
                    return True
            logger.debug(f"[BaseStrategy] No fallback exit condition met for {pair}.")
            return False
        except Exception as e:
            logger.error(f"Error in fallback_should_exit: {e}")
            return False

    async def get_optimized_parameters(self, market_regime: Optional[str] = None, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Get optimized parameters for this strategy and pair with caching.
        
        Args:
            market_regime: Current market regime (bull, bear, sideways, high_volatility, low_volatility)
            force_refresh: Force refresh of cached parameters
            
        Returns:
            Dictionary of optimized parameters
        """
        if not self._strategy_optimizer or not self.state.pair:
            return {}
            
        try:
            # Check cache validity (5 minutes)
            now = datetime.utcnow()
            cache_valid = (
                not force_refresh and 
                self.state.parameter_cache_time and 
                (now - self.state.parameter_cache_time).total_seconds() < 300 and
                self.state.current_parameters
            )
            
            if cache_valid:
                return self.state.current_parameters
            
            strategy_name = self.__class__.__name__.lower().replace('strategy', '')
            parameters = await self._strategy_optimizer.get_optimized_parameters(
                strategy_name=strategy_name,
                pair=self.state.pair,
                market_regime=market_regime or self.state.market_regime
            )
            
            # Update cache
            self.state.current_parameters = parameters
            self.state.parameter_cache_time = now
            
            return parameters
            
        except Exception as e:
            logger.error(f"Error getting optimized parameters: {str(e)}")
            return {}

    async def apply_optimized_parameters(self, parameters: Dict[str, Any]) -> bool:
        """
        Apply optimized parameters to strategy configuration.
        
        Args:
            parameters: Dictionary of parameters to apply
            
        Returns:
            True if parameters were applied successfully
        """
        if not parameters:
            return False
            
        try:
            # Log parameter changes
            if self._feedback_manager:
                old_params = getattr(self, '_last_applied_parameters', {})
                if old_params != parameters:
                    await self._feedback_manager.log_parameter_change(
                        strategy_name=self.__class__.__name__.lower().replace('strategy', ''),
                        pair=self.state.pair,
                        old_parameters=old_params,
                        new_parameters=parameters,
                        market_regime=self.state.market_regime,
                        confidence_score=0.8,  # Default confidence
                        expected_improvement=0.05  # Default expected improvement
                    )
                    self._last_applied_parameters = parameters.copy()
            
            # Apply parameters to strategy-specific attributes
            # This method should be overridden by specific strategies
            return await self._apply_strategy_parameters(parameters)
            
        except Exception as e:
            logger.error(f"Error applying optimized parameters: {str(e)}")
            return False

    async def _apply_strategy_parameters(self, parameters: Dict[str, Any]) -> bool:
        """
        Apply strategy-specific parameters. Override in subclasses.
        
        Args:
            parameters: Dictionary of parameters to apply
            
        Returns:
            True if parameters were applied successfully
        """
        # Default implementation - override in subclasses
        return True

    async def log_trade_outcome(self, trade_data: Dict[str, Any], is_profitable: bool) -> None:
        """
        Enhanced trade outcome logging with feedback integration.
        
        Args:
            trade_data: Trade data including entry/exit prices, PnL, etc.
            is_profitable: Whether the trade was profitable
        """
        if not self.state.pair:
            return
            
        try:
            strategy_name = self.__class__.__name__.lower().replace('strategy', '')
            
            # Log to strategy analyzer
            if self._strategy_analyzer:
                await self._strategy_analyzer.track_trade_outcome(
                    strategy_name=strategy_name,
                    pair=self.state.pair,
                    trade_data=trade_data,
                    is_profitable=is_profitable
                )
            
            # Log to strategy optimizer
            if self._strategy_optimizer:
                await self._strategy_optimizer.log_trade_result(
                    strategy_name=strategy_name,
                    pair=self.state.pair,
                    trade_data=trade_data,
                    is_profitable=is_profitable
                )
            
            # Log to feedback manager
            if self._feedback_manager:
                await self._feedback_manager.track_trade_outcome(
                    strategy_name=strategy_name,
                    pair=self.state.pair,
                    trade_data=trade_data,
                    is_profitable=is_profitable
                )
                
        except Exception as e:
            logger.error(f"Error logging trade outcome: {str(e)}")

    async def log_condition_outcome(self, condition_name: str, condition_value: Any, 
                                  condition_result: bool, metadata: Optional[Dict[str, Any]] = None) -> None:
        """
        Log individual condition outcomes for analysis.
        
        Args:
            condition_name: Name of the condition (e.g., 'rsi_oversold', 'volume_spike')
            condition_value: The actual value of the condition
            condition_result: Whether the condition was met (True/False)
            metadata: Additional metadata about the condition
        """
        try:
            if not self.state.pair:
                return
                
            # Enhanced condition logging with metadata
            condition_data = {
                'strategy': self.__class__.__name__.lower().replace('strategy', ''),
                'pair': self.state.pair,
                'condition_name': condition_name,
                'condition_value': condition_value,
                'condition_result': condition_result,
                'market_regime': self.state.market_regime,
                'timestamp': datetime.utcnow().isoformat(),
                'metadata': metadata or {}
            }
            
            # Log to condition logger
            await self._log_condition(
                name=condition_name,
                value=condition_value,
                description=f"{condition_name} for {self.state.pair}",
                result=condition_result,
                condition_type=metadata.get('type') if metadata else None,
                pair=self.state.pair,
                market_regime=self.state.market_regime,
                volatility=self.state.indicators.get('ATR', None)
            )
            
            # Log to strategy analyzer for condition tracking
            if self._strategy_analyzer:
                await self._strategy_analyzer.log_condition_outcome(
                    strategy_name=condition_data['strategy'],
                    pair=self.state.pair,
                    condition_data=condition_data
                )
                
        except Exception as e:
            logger.error(f"Error logging condition outcome: {str(e)}")

    async def trigger_parameter_optimization(self, market_regime: Optional[str] = None, 
                                           force: bool = False) -> bool:
        """
        Enhanced parameter optimization trigger with feedback integration.
        
        Args:
            market_regime: Current market regime
            force: Force optimization even if recently optimized
            
        Returns:
            True if optimization was triggered successfully
        """
        if not self._strategy_optimizer or not self.state.pair:
            return False
            
        try:
            # Check if optimization is needed
            now = datetime.utcnow()
            if not force and self.state.last_optimization_time:
                time_since_last = (now - self.state.last_optimization_time).total_seconds()
                if time_since_last < 3600:  # 1 hour cooldown
                    return False
            
            strategy_name = self.__class__.__name__.lower().replace('strategy', '')
            
            # Trigger optimization via feedback manager if available
            if self._feedback_manager:
                success = await self._feedback_manager.trigger_manual_reoptimization(
                    strategy_name=strategy_name,
                    pair=self.state.pair,
                    market_regime=market_regime or self.state.market_regime
                )
            else:
                # Fallback to direct optimizer call
                success = await self._strategy_optimizer.trigger_optimization(
                    strategy_name=strategy_name,
                    pair=self.state.pair,
                    market_regime=market_regime or self.state.market_regime
                )
            
            if success:
                self.state.last_optimization_time = now
                # Force parameter refresh on next call
                self.state.parameter_cache_time = None
                
            return success
            
        except Exception as e:
            logger.error(f"Error triggering optimization: {str(e)}")
            return False

    async def check_performance_degradation(self) -> bool:
        """
        Check if strategy performance has degraded and needs attention.
        
        Returns:
            True if performance degradation detected
        """
        if not self._feedback_manager or not self.state.pair:
            return False
            
        try:
            strategy_name = self.__class__.__name__.lower().replace('strategy', '')
            return await self._feedback_manager.check_performance_degradation(
                strategy_name=strategy_name,
                pair=self.state.pair
            )
        except Exception as e:
            logger.error(f"Error checking performance degradation: {str(e)}")
            return False 