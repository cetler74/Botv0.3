"""
Trading Orchestrator for the Multi-Exchange Trading Bot
Main orchestrator that coordinates all components and manages trading cycles
"""

import asyncio
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
import signal
import sys
import os

# Add the project root to the path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config_manager import ConfigManager
from core.database_manager import DatabaseManager
from core.exchange_manager import ExchangeManager
from core.strategy_manager import StrategyManager
from core.pair_selector import PairSelector, BinancePairSelector, BybitPairSelector

logger = logging.getLogger(__name__)

# Ensure logs directory exists
os.makedirs('logs', exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/trading_bot.log'),
        logging.StreamHandler()
    ]
)

logging.info("TEST LOG: Logging is working and configured!")


class TradingOrchestrator:
    """Main orchestrator for the multi-exchange trading bot"""
    
    def __init__(self, config_path: str = "config/config.yaml"):
        self.config_path = config_path
        self.config_manager = None
        self.database_manager = None
        self.exchange_manager = None
        self.strategy_manager = None
        self.running = False
        self.cycle_count = 0
        
        # Trading state
        self.active_trades = {}
        self.pair_selections = {}
        self.balances = {}
        
        # Setup signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        logger.info(f"Received signal {signum}, shutting down gracefully...")
        self.running = False
        
    async def initialize(self) -> bool:
        """Initialize all components"""
        try:
            logger.info("Initializing Trading Orchestrator...")
            
            # Initialize configuration manager
            self.config_manager = ConfigManager(self.config_path)
            logger.info("Configuration manager initialized")
            
            # Initialize database manager
            db_config = self.config_manager.get_database_config()
            self.database_manager = DatabaseManager(db_config)
            logger.info("Database manager initialized")
            
            # Set database manager in config manager for alerts
            self.config_manager.set_database_manager(self.database_manager)
            
            # Initialize exchange manager
            self.exchange_manager = ExchangeManager(
                self.config_manager.config_data, 
                self.database_manager
            )
            logger.info("Exchange manager initialized")
            
            # Initialize strategy manager
            self.strategy_manager = StrategyManager(
                self.config_manager.config_data,
                self.exchange_manager,
                self.database_manager
            )
            logger.info("Strategy manager initialized")
            
            # Initialize balances
            await self._initialize_balances()
            
            # Initialize pair selections
            await self._initialize_pair_selections()
            
            logger.info("Trading Orchestrator initialized successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize Trading Orchestrator: {e}")
            return False
            
    async def _initialize_balances(self) -> None:
        """Initialize balance tracking for all exchanges"""
        try:
            exchanges = self.config_manager.get_all_exchanges()
            
            for exchange_name in exchanges:
                if self.config_manager.is_simulation_mode():
                    # In simulation mode, get balance from database
                    balance = await self.database_manager.get_balance(exchange_name)
                    if balance:
                        self.balances[exchange_name] = {
                            'total': float(balance['balance']),
                            'available': float(balance['available_balance']),
                            'total_pnl': float(balance['total_pnl']),
                            'daily_pnl': float(balance['daily_pnl'])
                        }
                    else:
                        # Initialize with default values
                        self.balances[exchange_name] = {
                            'total': 10000.0,  # Default simulation balance
                            'available': 10000.0,
                            'total_pnl': 0.0,
                            'daily_pnl': 0.0
                        }
                else:
                    # In live mode, get balance from exchange
                    balance = await self.exchange_manager.get_balance(exchange_name)
                    if balance:
                        self.balances[exchange_name] = {
                            'total': balance['total'],
                            'available': balance['free'],
                            'total_pnl': 0.0,  # Will be calculated from trades
                            'daily_pnl': 0.0   # Will be calculated from trades
                        }
                        
            logger.info(f"Initialized balances for {len(exchanges)} exchanges")
            
        except Exception as e:
            logger.error(f"Error initializing balances: {e}")
            
    async def _initialize_pair_selections(self) -> None:
        """Dynamically select and persist pairs for all exchanges"""
        try:
            exchanges = self.config_manager.get_all_exchanges()
            for exchange_name in exchanges:
                # Get config for this exchange
                exchange_config = self.config_manager.get_exchange_config(exchange_name)
                base_pair = exchange_config.get("base_currency", "USDC")
                num_pairs = exchange_config.get("max_pairs", 15)

                # Use the correct PairSelector for this exchange
                if exchange_name.lower() == "cryptocom":
                    selector = PairSelector(base_pair=base_pair, num_pairs=num_pairs)
                elif exchange_name.lower() == "binance":
                    selector = BinancePairSelector(base_pair=base_pair, num_pairs=num_pairs)
                elif exchange_name.lower() == "bybit":
                    selector = BybitPairSelector(base_pair=base_pair, num_pairs=num_pairs)
                else:
                    logger.warning(f"No pair selector implemented for {exchange_name}, skipping.")
                    continue

                result = await selector.select_top_pairs()

                # Persist to database
                await self.database_manager.save_pairs(
                    exchange=exchange_name,
                    pairs=result["selected_pairs"]
                )

                # Update in-memory
                self.pair_selections[exchange_name] = result["selected_pairs"]

                # Log timestamp/result
                logger.info(f"Selected pairs for {exchange_name}: {result['selected_pairs']} at {result['timestamp']}")

            logger.info(f"Pair selection complete for {len(exchanges)} exchanges")

        except Exception as e:
            logger.error(f"Error in dynamic pair selection: {e}")
            
    async def run(self) -> None:
        """Main trading loop"""
        try:
            if not await self.initialize():
                logger.error("Failed to initialize, exiting...")
                return
                
            self.running = True
            logger.info("Starting trading orchestrator...")
            
            # Get trading configuration
            trading_config = self.config_manager.get_trading_config()
            cycle_interval = trading_config.get('cycle_interval_seconds', 60)
            exit_cycle_first = trading_config.get('exit_cycle_first', True)
            
            while self.running:
                try:
                    cycle_start = datetime.utcnow()
                    self.cycle_count += 1
                    
                    logger.info(f"Starting trading cycle {self.cycle_count}")
                    
                    # Run exit cycle first if configured
                    if exit_cycle_first:
                        await self._run_exit_cycle()
                        
                    # Run entry cycle
                    await self._run_entry_cycle()
                    
                    # Run maintenance tasks
                    await self._run_maintenance_tasks()
                    
                    # Calculate cycle duration
                    cycle_duration = (datetime.utcnow() - cycle_start).total_seconds()
                    logger.info(f"Completed trading cycle {self.cycle_count} in {cycle_duration:.2f}s")
                    
                    # Wait for next cycle
                    if cycle_duration < cycle_interval:
                        await asyncio.sleep(cycle_interval - cycle_duration)
                        
                except Exception as e:
                    logger.error(f"Error in trading cycle {self.cycle_count}: {e}")
                    await asyncio.sleep(10)  # Wait before retrying
                    
        except Exception as e:
            logger.error(f"Fatal error in trading orchestrator: {e}")
        finally:
            await self.shutdown()
            
    async def _run_exit_cycle(self) -> None:
        """Run exit cycle to check for exit signals"""
        try:
            logger.info("Running exit cycle...")
            
            # Get all open trades
            open_trades = await self.database_manager.get_open_trades()
            
            if not open_trades:
                logger.info("No open trades to check")
                return
                
            logger.info(f"Checking {len(open_trades)} open trades for exit signals")
            
            for trade in open_trades:
                try:
                    await self._check_trade_exit(trade)
                except Exception as e:
                    logger.error(f"Error checking exit for trade {trade.get('trade_id')}: {e}")
                    
        except Exception as e:
            logger.error(f"Error in exit cycle: {e}")
            
    async def _check_trade_exit(self, trade: Dict[str, Any]) -> None:
        """Check if a specific trade should be exited"""
        try:
            trade_id = trade['trade_id']
            exchange_name = trade['exchange']
            pair = trade['pair']
            
            # Get current price
            ticker = await self.exchange_manager.get_ticker(exchange_name, pair)
            if not ticker:
                logger.warning(f"Could not get ticker for {pair} on {exchange_name}")
                return
                
            current_price = float(ticker['last'])
            
            # Check profit protection
            should_exit, reason = await self.strategy_manager.apply_profit_protection(
                trade, current_price
            )
            
            if should_exit:
                await self._execute_trade_exit(trade, current_price, reason)
                return
                
            # Check trailing stop
            should_exit, reason = await self.strategy_manager.apply_trailing_stop(
                trade, current_price
            )
            
            if should_exit:
                await self._execute_trade_exit(trade, current_price, reason)
                return
                
            # Check strategy exit signals
            exit_signals = await self.strategy_manager.check_exit_signals(
                exchange_name, pair, trade
            )
            
            if exit_signals:
                await self._execute_trade_exit(trade, current_price, "strategy_exit")
                return
                
            # Update unrealized PnL
            entry_price = float(trade.get('entry_price', 0))
            position_size = float(trade.get('position_size', 0))
            
            if entry_price > 0 and position_size > 0:
                unrealized_pnl = (current_price - entry_price) * position_size
                await self.database_manager.update_trade(trade_id, {
                    'unrealized_pnl': unrealized_pnl,
                    'highest_price': max(current_price, float(trade.get('highest_price', 0)))
                })
                
        except Exception as e:
            logger.error(f"Error checking trade exit: {e}")
            
    async def _execute_trade_exit(self, trade: Dict[str, Any], exit_price: float, reason: str) -> None:
        """Execute trade exit"""
        try:
            trade_id = trade['trade_id']
            exchange_name = trade['exchange']
            pair = trade['pair']
            
            logger.info(f"Executing exit for trade {trade_id}: {reason}")
            
            # Calculate realized PnL
            entry_price = float(trade.get('entry_price', 0))
            position_size = float(trade.get('position_size', 0))
            realized_pnl = (exit_price - entry_price) * position_size
            
            # Execute exit order
            if self.config_manager.is_simulation_mode():
                # Create simulated exit order
                exit_order = await self.exchange_manager.create_simulation_order(
                    exchange_name, pair, 'market', 'sell', position_size, exit_price
                )
            else:
                # Create real exit order
                exit_order = await self.exchange_manager.create_order(
                    exchange_name, pair, 'market', 'sell', position_size, exit_price
                )
                
            if exit_order:
                # Update trade record
                await self.database_manager.update_trade(trade_id, {
                    'exit_price': exit_price,
                    'exit_id': exit_order['id'],
                    'exit_time': datetime.utcnow(),
                    'realized_pnl': realized_pnl,
                    'status': 'CLOSED',
                    'exit_reason': reason
                })
                
                # Update balance
                await self._update_balance_after_trade(exchange_name, realized_pnl)
                
                # Update strategy performance
                await self.strategy_manager.update_strategy_performance(
                    trade.get('strategy', 'unknown'),
                    exchange_name,
                    pair,
                    {'pnl': realized_pnl}
                )
                
                logger.info(f"Successfully exited trade {trade_id} with PnL: {realized_pnl:.2f}")
                
        except Exception as e:
            logger.error(f"Error executing trade exit: {e}")
            
    async def _run_entry_cycle(self) -> None:
        """Run entry cycle to check for entry signals"""
        try:
            logger.info("Running entry cycle...")
            
            # Check available balance
            if not await self._check_available_balance():
                logger.info("Insufficient balance for new trades")
                return
                
            # Check for entry signals on all pairs
            for exchange_name, pairs in self.pair_selections.items():
                if not pairs:
                    continue
                    
                # Check max trades per exchange
                open_trades = await self.database_manager.get_open_trades(exchange_name)
                max_trades = self.config_manager.get_trading_config().get('max_trades_per_exchange', 3)
                
                if len(open_trades) >= max_trades:
                    logger.info(f"Maximum trades reached for {exchange_name}")
                    continue
                    
                # Check each pair for entry signals
                for pair in pairs:
                    try:
                        await self._check_pair_entry(exchange_name, pair)
                    except Exception as e:
                        logger.error(f"Error checking entry for {pair} on {exchange_name}: {e}")
                        
        except Exception as e:
            logger.error(f"Error in entry cycle: {e}")
            
    async def _check_available_balance(self) -> bool:
        """Check if there's sufficient balance for new trades"""
        try:
            total_available = 0
            min_balance = self.config_manager.get_balance_manager_config().get('min_balance_threshold', 10)
            
            for exchange_name, balance in self.balances.items():
                total_available += balance['available']
                
            return total_available >= min_balance
            
        except Exception as e:
            logger.error(f"Error checking available balance: {e}")
            return False
            
    async def _check_pair_entry(self, exchange_name: str, pair: str) -> None:
        """Check for entry signals on a specific pair"""
        try:
            # Check entry signals
            entry_signals = await self.strategy_manager.check_entry_signals(exchange_name, pair)
            
            if not entry_signals:
                return
                
            # Get the best signal
            best_signal = max(entry_signals, key=lambda x: x.get('confidence', 0))
            
            # Check if signal meets minimum criteria
            min_confidence = self.config_manager.get_trading_config().get('min_confidence', 0.6)
            
            if best_signal.get('confidence', 0) < min_confidence:
                return
                
            # Execute entry
            await self._execute_trade_entry(exchange_name, pair, best_signal)
            
        except Exception as e:
            logger.error(f"Error checking pair entry: {e}")
            
    async def _execute_trade_entry(self, exchange_name: str, pair: str, signal: Dict[str, Any]) -> None:
        """Execute trade entry"""
        try:
            # Calculate position size
            balance = self.balances[exchange_name]
            position_size_pct = self.config_manager.get_trading_config().get('position_size_percentage', 0.1)
            position_size = balance['available'] * position_size_pct
            
            # Get current price
            ticker = await self.exchange_manager.get_ticker(exchange_name, pair)
            if not ticker:
                return
                
            current_price = float(ticker['last'])
            
            # Calculate actual position size in units
            position_units = position_size / current_price
            
            # Execute entry order
            if self.config_manager.is_simulation_mode():
                # Create simulated entry order
                entry_order = await self.exchange_manager.create_simulation_order(
                    exchange_name, pair, 'market', 'buy', position_units, current_price
                )
            else:
                # Create real entry order
                entry_order = await self.exchange_manager.create_order(
                    exchange_name, pair, 'market', 'buy', position_units, current_price
                )
                
            if entry_order:
                # Create trade record
                trade_data = {
                    'pair': pair,
                    'exchange': exchange_name,
                    'entry_price': current_price,
                    'entry_id': entry_order['id'],
                    'entry_time': datetime.utcnow(),
                    'position_size': position_units,
                    'strategy': signal.get('strategy', 'consensus'),
                    'entry_reason': f"Signal: {signal.get('signal')}, Confidence: {signal.get('confidence'):.2f}"
                }
                
                trade_id = await self.database_manager.create_trade(trade_data)
                
                if trade_id:
                    # Update balance
                    self.balances[exchange_name]['available'] -= position_size
                    
                    logger.info(f"Successfully entered trade {trade_id} for {pair} on {exchange_name}")
                    
        except Exception as e:
            logger.error(f"Error executing trade entry: {e}")
            
    async def _update_balance_after_trade(self, exchange_name: str, pnl: float) -> None:
        """Update balance after trade completion"""
        try:
            if exchange_name in self.balances:
                self.balances[exchange_name]['total_pnl'] += pnl
                self.balances[exchange_name]['daily_pnl'] += pnl
                
                # Update database
                balance = self.balances[exchange_name]
                await self.database_manager.update_balance(
                    exchange_name,
                    balance['total'],
                    balance['available'],
                    balance['total_pnl'],
                    balance['daily_pnl']
                )
                
        except Exception as e:
            logger.error(f"Error updating balance: {e}")
            
    async def _run_maintenance_tasks(self) -> None:
        """Run maintenance tasks"""
        try:
            # Clean up expired cache
            await self.strategy_manager.cleanup_cache()
            await self.database_manager.cleanup_expired_cache()
            
            # Update balances from exchanges (in live mode)
            if not self.config_manager.is_simulation_mode():
                for exchange_name in self.config_manager.get_all_exchanges():
                    try:
                        balance = await self.exchange_manager.get_balance(exchange_name)
                        if balance:
                            self.balances[exchange_name] = {
                                'total': balance['total'],
                                'available': balance['free'],
                                'total_pnl': self.balances[exchange_name]['total_pnl'],
                                'daily_pnl': self.balances[exchange_name]['daily_pnl']
                            }
                    except Exception as e:
                        logger.error(f"Error updating balance for {exchange_name}: {e}")
                        
        except Exception as e:
            logger.error(f"Error in maintenance tasks: {e}")
            
    async def shutdown(self) -> None:
        """Shutdown the orchestrator gracefully"""
        try:
            logger.info("Shutting down Trading Orchestrator...")
            
            # Close all components
            if self.exchange_manager:
                await self.exchange_manager.close()
                
            if self.database_manager:
                await self.database_manager.close()
                
            logger.info("Trading Orchestrator shutdown complete")
            
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")


async def main():
    """Main entry point"""
    try:
        # Setup logging
        # The logging configuration is now handled at the top of the file
        # logging.basicConfig(
        #     level=logging.INFO,
        #     format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        #     handlers=[
        #         logging.FileHandler('logs/trading_bot.log'),
        #         logging.StreamHandler()
        #     ]
        # )
        
        # Create and run orchestrator
        orchestrator = TradingOrchestrator()
        await orchestrator.run()
        
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, shutting down...")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main()) 