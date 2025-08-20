"""
Trading Orchestrator for the Multi-Exchange Trading Bot
Main orchestrator that coordinates all components and manages trading cycles
"""

import asyncio
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta, timezone
import signal
import sys
import os
import uuid
import httpx
from core.config_manager import ConfigManager
from core.database_manager import DatabaseManager
from core.exchange_manager import ExchangeManager
from core.strategy_manager import StrategyManager
from core.pair_selector import PairSelector, BinancePairSelector, BybitPairSelector
from core.balance_manager import BalanceManager

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
        self.config_manager: Optional[ConfigManager] = None
        self.database_manager: Optional[DatabaseManager] = None
        self.exchange_manager = None
        self.strategy_manager = None
        self.balance_manager = None
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
            
            # Log DB connection info
            self.database_manager.log_connection_info()
            
            # Force DB connection context log
            self.database_manager._get_connection()
            
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
            
            # Initialize balance manager
            self.balance_manager = BalanceManager(
                self.database_manager,
                self.config_manager
            )
            await self.balance_manager.initialize_balances()
            logger.info("Balance manager initialized")
            
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
        assert self.config_manager is not None, "config_manager must be initialized"
        assert self.database_manager is not None, "database_manager must be initialized"
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
        assert self.config_manager is not None, "config_manager must be initialized"
        assert self.database_manager is not None, "database_manager must be initialized"
        try:
            logger.info("[DEBUG] _initialize_pair_selections called!")
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

                # Always forcibly add only CRO/USD before saving to DB for cryptocom
                if exchange_name.lower() == "cryptocom":
                    logger.info(f"[DEBUG] cryptocom pairs before force-add: {result['selected_pairs']}")
                    if "CRO/USD" not in result["selected_pairs"]:
                        result["selected_pairs"].append("CRO/USD")
                    logger.info(f"[FORCE] cryptocom pairs to be saved: {result['selected_pairs']}")

                # Persist to database
                logger.info(f"[DEBUG] Saving pairs for {exchange_name}: {result['selected_pairs']}")
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
                    cycle_start = datetime.now(timezone.utc)
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
                    cycle_duration = (datetime.now(timezone.utc) - cycle_start).total_seconds()
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
            for exchange_name in self.pair_selections.keys():
                # Get open trades
                open_trades = []
                if self.database_manager is not None and hasattr(self.database_manager, 'get_open_trades'):
                    open_trades = await self.database_manager.get_open_trades(exchange_name)
                for trade in open_trades:
                    # Check trade exit conditions (profit protection, trailing stop, strategy signals)
                    await self._check_trade_exit(trade)
        except Exception as e:
            logger.error(f"Error in exit cycle: {e}")

    async def _check_trade_exit(self, trade: Dict[str, Any]) -> None:
        """Check if a specific trade should be exited"""
        try:
            trade_id = trade.get('trade_id')
            exchange_name = trade.get('exchange')
            pair = trade.get('pair')
            
            # Defensive checks for required fields
            if not exchange_name or not pair:
                logger.error(f"Trade {trade_id} missing exchange or pair. Skipping.")
                return
                
            # Validate pair is not empty
            if not pair.strip():
                logger.error(f"Trade {trade_id} has empty pair. Skipping.")
                return
                
            ticker = await self.exchange_manager.get_ticker(exchange_name, pair)
            if not ticker:
                logger.warning(f"Could not get ticker for {pair} on {exchange_name}")
                return
            last_price = ticker.get('last')
            if last_price is None:
                logger.error(f"Trade {trade_id} ticker missing 'last' price. Skipping.")
                return
            try:
                current_price = float(last_price)
            except Exception as e:
                logger.error(f"Trade {trade_id} invalid last price '{last_price}': {e}. Skipping.")
                return
            entry_price = trade.get('entry_price')
            position_size = trade.get('position_size')
            if entry_price is None or position_size is None:
                logger.error(f"Trade {trade_id} missing entry_price or position_size. Skipping.")
                return
            try:
                entry_price = float(entry_price)
                position_size = float(position_size)
            except Exception as e:
                logger.error(f"Trade {trade_id} invalid entry_price or position_size: {e}. Skipping.")
                return
            # Check profit protection
            logger.info(f"[RiskMgmt] Checking profit protection for trade {trade_id}: entry={entry_price}, current={current_price}, pnl_pct={((current_price-entry_price)/entry_price)*100:.2f}%")
            should_exit, reason = await self.strategy_manager.apply_profit_protection(trade, current_price)
            if should_exit:
                logger.info(f"[RiskMgmt] Profit protection triggered for trade {trade_id} (reason: {reason})")
                await self._execute_trade_exit(trade, current_price, reason)
                return
            # Check trailing stop
            logger.info(f"[RiskMgmt] Checking trailing stop for trade {trade_id}: entry={entry_price}, current={current_price}, pnl_pct={((current_price-entry_price)/entry_price)*100:.2f}%")
            should_exit, reason = await self.strategy_manager.apply_trailing_stop(trade, current_price)
            if should_exit:
                logger.info(f"[RiskMgmt] Trailing stop triggered for trade {trade_id} (reason: {reason})")
                await self._execute_trade_exit(trade, current_price, reason)
                return
            
            # CRITICAL FIX: Add enhanced exit logic
            enhanced_exit_needed = await self._check_enhanced_exit_conditions(trade, current_price)
            if enhanced_exit_needed:
                exit_reason, exit_type = enhanced_exit_needed
                logger.info(f"[RiskMgmt] Enhanced exit triggered for trade {trade_id}: {exit_type} - {exit_reason}")
                await self._execute_trade_exit(trade, current_price, f"{exit_type}_{exit_reason}")
                return
            # Check strategy exit signals
            exit_signals = await self.strategy_manager.check_exit_signals(exchange_name, pair, trade)
            if exit_signals:
                # Use the first exit signal's reason if available
                reason = exit_signals[0].get('reason', 'strategy_exit')
                await self._execute_trade_exit(trade, current_price, reason)
                return
            # Update unrealized PnL
            if entry_price > 0 and position_size > 0:
                unrealized_pnl = (current_price - entry_price) * position_size
                await self.database_manager.update_trade(trade_id, {
                    'unrealized_pnl': unrealized_pnl,
                    'highest_price': max(current_price, float(trade.get('highest_price', 0) or 0))
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

            # Check available balance for the asset to be sold
            asset = pair.split('/')[0] if '/' in pair else pair
            available = self.balances.get(exchange_name, {}).get('available', {}).get(asset, 0)
            logger.info(f"[ExitBalanceCheck] {exchange_name} {asset}: available={available}, required={position_size}")
            if available < position_size:
                logger.error(f"[ExitBalanceCheck] Insufficient {asset} balance for exit: available={available}, required={position_size}")
                return

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
                    'exit_time': datetime.now(timezone.utc),
                    'realized_pnl': realized_pnl,
                    'status': 'CLOSED',
                    'exit_reason': reason
                })

                # Release balance and add PnL
                position_size_value = position_size * exit_price  # Convert position units to value
                await self.balance_manager.release_balance(exchange_name, position_size_value, realized_pnl)

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
                logger.info(f"[EntryCycle] Checking exchange: {exchange_name} with pairs: {pairs}")
                if not pairs:
                    logger.warning(f"[EntryCycle] No pairs selected for {exchange_name}, skipping.")
                    continue
                # Get valid symbols from exchange markets
                markets = await self.exchange_manager.get_markets(exchange_name)
                valid_symbols = set(markets.keys())
                logger.debug(f"[EntryCycle] Valid symbols for {exchange_name}: {valid_symbols}")
                # Check max trades per exchange
                open_trades = []
                if self.database_manager is not None and hasattr(self.database_manager, 'get_open_trades'):
                    open_trades = await self.database_manager.get_open_trades(exchange_name)
                max_trades = 3
                if self.config_manager is not None and hasattr(self.config_manager, 'get_trading_config'):
                    max_trades = self.config_manager.get_trading_config().get('max_trades_per_exchange', 3)
                logger.info(f"[EntryCycle] {exchange_name}: open_trades={len(open_trades)}, max_trades={max_trades}")
                if len(open_trades) >= max_trades:
                    logger.info(f"[EntryCycle] Trade limit reached for {exchange_name}: {len(open_trades)}/{max_trades}. Skipping entry for this exchange.")
                    continue
                else:
                    logger.info(f"[EntryCycle] Trade limit NOT reached for {exchange_name}: {len(open_trades)}/{max_trades}. Processing entry cycle for this exchange.")
                
                # CRITICAL FIX: Check daily loss limit before processing new trades
                daily_loss_pct = await self._check_daily_loss_limit(exchange_name)
                max_daily_loss = self.config_manager.get_trading_config().get('max_daily_loss_per_exchange', 0.05)
                if daily_loss_pct >= max_daily_loss:
                    logger.warning(f"[EntryCycle] Daily loss limit reached for {exchange_name}: {daily_loss_pct:.2%} >= {max_daily_loss:.2%}. Skipping new trades.")
                    continue
                
                for pair in pairs:
                    # Validate pair before making API calls
                    if not pair or not isinstance(pair, str) or pair.strip() == "":
                        logger.warning(f"[EntryCycle] Skipping invalid or empty pair: {pair} for {exchange_name}")
                        continue
                    if pair not in valid_symbols:
                        logger.warning(f"[EntryCycle] Skipping pair not in valid symbols: {pair} for {exchange_name}")
                        continue
                    logger.info(f"[Orchestrator] Analyzing pair: {pair} on {exchange_name}")
                    # Use strategy manager to check entry signals (this includes detailed reasons)
                    entry_signals = await self.strategy_manager.check_entry_signals(exchange_name, pair)
                    logger.debug(f"[EntryCycle] Entry signals for {pair} on {exchange_name}: {entry_signals}")
                    if entry_signals:
                        # Get the best signal
                        best_signal = max(entry_signals, key=lambda x: x.get('confidence', 0))
                        min_confidence = self.config_manager.get_trading_config().get('min_confidence', 0.6)
                        logger.info(f"[EntryCycle] Best signal for {pair} on {exchange_name}: {best_signal}, min_confidence={min_confidence}")
                        if best_signal.get('confidence', 0) >= min_confidence:
                            logger.info(f"[Orchestrator] Auto-trading on signal: {pair} {best_signal}")
                            await self._execute_trade_entry(exchange_name, pair, best_signal)
                        else:
                            logger.info(f"[Orchestrator] Signal confidence {best_signal.get('confidence', 0)} below threshold {min_confidence} for {pair}")
                    else:
                        logger.info(f"[EntryCycle] No entry signals for {pair} on {exchange_name}")
                        
        except Exception as e:
            logger.error(f"Error in entry cycle: {e}")
    
    async def _check_daily_loss_limit(self, exchange_name: str) -> float:
        """Check daily loss percentage for an exchange"""
        try:
            if not self.database_manager:
                return 0.0
            
            from datetime import datetime, timedelta
            today = datetime.utcnow().date()
            
            # Get today's trades for this exchange
            today_trades = await self.database_manager.get_trades_by_date_range(
                exchange_name, today, today
            )
            
            if not today_trades:
                return 0.0
            
            # Calculate total realized PnL for today
            total_realized_pnl = sum(
                float(trade.get('realized_pnl', 0) or 0) 
                for trade in today_trades 
                if trade.get('status') == 'CLOSED'
            )
            
            # Get exchange balance to calculate percentage
            balances = await self.exchange_manager.get_balance(exchange_name)
            if not balances or 'total' not in balances:
                return 0.0
            
            total_balance = float(balances['total'])
            if total_balance <= 0:
                return 0.0
            
            # Calculate daily loss percentage
            daily_loss_pct = abs(total_realized_pnl) / total_balance if total_realized_pnl < 0 else 0.0
            
            logger.debug(f"[DailyLoss] {exchange_name}: realized_pnl=${total_realized_pnl:.2f}, "
                        f"balance=${total_balance:.2f}, loss_pct={daily_loss_pct:.2%}")
            
            return daily_loss_pct
            
        except Exception as e:
            logger.error(f"Error checking daily loss limit for {exchange_name}: {e}")
            return 0.0
    
    async def _check_enhanced_exit_conditions(self, trade: Dict, current_price: float) -> Optional[Tuple[str, str]]:
        """Check enhanced exit conditions: momentum-based, correlation-based, and time-based exits"""
        try:
            exchange_name = trade.get('exchange')
            pair = trade.get('pair')
            entry_time = trade.get('entry_time')
            trade_id = trade.get('trade_id')
            
            # Get current market data for the pair
            market_data = await self.exchange_manager.get_ohlcv(exchange_name, pair, '1h', limit=50)
            if market_data is None or len(market_data) < 14:
                return None
            
            # 1. MOMENTUM-BASED EXIT: RSI deterioration for long positions
            try:
                import pandas_ta as ta
                rsi = ta.rsi(market_data['close'], length=14)
                if rsi is not None and not rsi.empty:
                    current_rsi = rsi.iloc[-1]
                    if not pd.isna(current_rsi) and current_rsi < 40:
                        return ("momentum_deterioration", f"RSI dropped to {current_rsi:.1f}")
            except Exception as e:
                logger.debug(f"Error calculating RSI for momentum exit: {e}")
            
            # 2. CORRELATION-BASED EXIT: Check BTC crash for altcoin positions
            if not pair.upper().startswith(('BTC', 'ETH')):
                try:
                    btc_data = await self.exchange_manager.get_ohlcv(exchange_name, 'BTC/USDC', '1h', limit=6)
                    if btc_data is not None and len(btc_data) >= 6:
                        btc_current = btc_data['close'].iloc[-1]
                        btc_6h_ago = btc_data['close'].iloc[-6]
                        btc_6h_change = (btc_current - btc_6h_ago) / btc_6h_ago
                        
                        # Exit altcoin longs if BTC drops >5% in 6 hours
                        if btc_6h_change < -0.05:
                            return ("btc_correlation", f"BTC down {btc_6h_change:.2%} in 6h")
                except Exception as e:
                    logger.debug(f"Error checking BTC correlation: {e}")
            
            # 3. TIME-BASED EXIT: Exit trades >24h old in downtrends
            if entry_time:
                try:
                    from datetime import datetime, timedelta
                    if isinstance(entry_time, str):
                        entry_dt = datetime.fromisoformat(entry_time.replace('Z', '+00:00'))
                    else:
                        entry_dt = entry_time
                    
                    trade_age = datetime.utcnow().replace(tzinfo=entry_dt.tzinfo) - entry_dt
                    
                    if trade_age > timedelta(hours=24):
                        # Check if market is in downtrend
                        if len(market_data) >= 21:
                            ema_9 = ta.ema(market_data['close'], length=9)
                            ema_21 = ta.ema(market_data['close'], length=21)
                            if ema_9 is not None and ema_21 is not None:
                                current_ema_9 = ema_9.iloc[-1]
                                current_ema_21 = ema_21.iloc[-1]
                                if not pd.isna(current_ema_9) and not pd.isna(current_ema_21):
                                    if current_ema_9 < current_ema_21:
                                        return ("time_limit_downtrend", f"Trade age {trade_age.total_seconds()/3600:.1f}h in downtrend")
                except Exception as e:
                    logger.debug(f"Error checking time-based exit: {e}")
            
            # 4. VOLUME-BASED EXIT: Exit on high volume selling
            try:
                if len(market_data) >= 20:
                    current_volume = market_data['volume'].iloc[-1]
                    avg_volume = market_data['volume'].tail(20).mean()
                    volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1
                    
                    # Check if high volume is accompanied by price drop
                    price_change = (market_data['close'].iloc[-1] - market_data['close'].iloc[-2]) / market_data['close'].iloc[-2]
                    
                    if volume_ratio > 3.0 and price_change < -0.02:  # 3x volume with >2% drop
                        return ("volume_selling", f"High volume ({volume_ratio:.1f}x) with {price_change:.2%} drop")
            except Exception as e:
                logger.debug(f"Error checking volume-based exit: {e}")
            
            return None
            
        except Exception as e:
            logger.error(f"Error in enhanced exit conditions check: {e}")
            return None
            
    async def _check_available_balance(self) -> bool:
        """Check if there's sufficient balance for new trades by checking the trading.balance table"""
        assert self.config_manager is not None, "config_manager must be initialized"
        assert self.database_manager is not None, "database_manager must be initialized"
        try:
            min_balance = self.config_manager.get_balance_manager_config().get('min_balance_threshold', 10)
            
            # Get all balances from the database
            balances = await self.database_manager.get_all_balances()
            
            if not balances:
                logger.warning("No balances found in database")
                return False
                
            # Check each exchange has sufficient available balance
            for balance in balances:
                exchange = balance['exchange']
                available_balance = float(balance['available_balance'])
                
                logger.info(f"[BalanceCheck] {exchange}: available_balance={available_balance}, min_threshold={min_balance}")
                
                if available_balance < min_balance:
                    logger.warning(f"[BalanceCheck] Insufficient balance on {exchange}: {available_balance} < {min_balance}")
                    return False
                    
            logger.info(f"[BalanceCheck] All exchanges have sufficient balance")
            return True
            
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
            # Calculate position size (10% of available balance)
            position_value = 1000.0  # Fixed position size for now
            
            # Check if we have sufficient available balance
            if not await self.balance_manager.check_available_balance(exchange_name, position_value):
                logger.error(f"Insufficient balance for trade entry on {exchange_name}")
                return
                
            # Reserve balance for this trade
            if not await self.balance_manager.reserve_balance(exchange_name, position_value):
                logger.error(f"Failed to reserve balance for trade on {exchange_name}")
                return
                
            # Get current price
            ticker = await self.exchange_manager.get_ticker(exchange_name, pair)
            if not ticker:
                logger.error(f"Could not get ticker for {pair} on {exchange_name}")
                return
                
            current_price = float(ticker['last'])
            
            # Calculate actual position size in units
            position_units = position_value / current_price
            
            logger.info(f"[TradeEntry] {exchange_name} {pair}: position_value={position_value}, position_units={position_units}, current_price={current_price}")
            
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
                # Get trading config for profit protection and trailing stop settings
                trading_config = self.config_manager.get_trading_config()
                profit_protection_trigger = trading_config.get('profit_protection_trigger', 0.02)  # 2% default
                trail_stop_trigger = trading_config.get('trail_stop_trigger', 0.01)  # 1% default
                
                # Create trade record with all available fields
                entry_reason = signal.get('reason') or f"Signal: {signal.get('signal')}, Confidence: {signal.get('confidence'):.2f}"
                trade_data = {
                    'pair': pair,
                    'exchange': exchange_name,
                    'entry_price': current_price,
                    'exit_price': None,  # Will be set on exit
                    'status': 'OPEN',
                    'entry_id': entry_order['id'],
                    'exit_id': None,  # Will be set on exit
                    'entry_time': datetime.now(timezone.utc),
                    'exit_time': None,  # Will be set on exit
                    'unrealized_pnl': 0.0,
                    'realized_pnl': 0.0,
                    'highest_price': current_price,  # Initialize with entry price
                    'profit_protection': 'inactive',
                    'profit_protection_trigger': profit_protection_trigger,
                    'trail_stop': 'inactive',
                    'trail_stop_trigger': trail_stop_trigger,
                    'position_size': position_units,
                    'fees': 0.0,  # Will be calculated from order if available
                    'strategy': signal.get('strategy', 'consensus'),
                    'entry_reason': entry_reason,
                    'exit_reason': None  # Will be set on exit
                }
                
                logger.info(f"Attempting to create trade with data: {trade_data}")
                trade_id = await self.database_manager.create_trade(trade_data)
                logger.info(f"create_trade returned trade_id: {trade_id}")
                if trade_id:
                    # Do NOT update balance in database here; balance_manager already did it
                    logger.info(f"Successfully entered trade {trade_id} for {pair} on {exchange_name}.")
                else:
                    logger.error(f"Trade creation failed for data: {trade_data}")
                    
        except Exception as e:
            logger.error(f"Error executing trade entry: {e}")
            

            
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
    # Ensure logs directory exists
    os.makedirs("logs", exist_ok=True)
    # Set up file handler
    file_handler = logging.FileHandler("logs/orchestrator.log")
    formatter = logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s')
    file_handler.setFormatter(formatter)
    logging.getLogger().addHandler(file_handler)
    logging.getLogger().setLevel(logging.INFO)
    # TEST LOG
    logging.getLogger().info("TEST LOG: Logging is working and configured!")
    # Initialize orchestrator
    asyncio.run(main()) 