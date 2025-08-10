"""
Improved Heikin-Ashi strategy for crypto trading and scalping.
Configurable parameters (set in config.yaml under 'heikin_ashi_strategy', e.g.):

heikin_ashi_strategy:
  min_candles: 50
  adx_period: 7
  rsi_period: 7
  atr_period: 7
  adx_threshold: 20
  rsi_overbought: 70
  rsi_oversold: 30
  min_candle_size: 0.002
  volume_sma_period: 7
"""
import pandas as pd
import numpy as np
import pandas_ta as ta
import logging
from typing import Tuple, Optional
from strategy.base_strategy import BaseStrategy, StrategyState

logger = logging.getLogger(__name__)

class HeikinAshiStrategy(BaseStrategy):
    def __init__(self, config, exchange, database, redis_client=None, exchange_name=None):
        # Load config from the correct section in strategies.heikin_ashi
        if hasattr(config, 'strategies') and hasattr(config.strategies, 'heikin_ashi'):
            strat_cfg = config.strategies.heikin_ashi.parameters if hasattr(config.strategies.heikin_ashi, 'parameters') else {}
        elif isinstance(config, dict) and 'strategies' in config and 'heikin_ashi' in config['strategies']:
            strat_cfg = config['strategies']['heikin_ashi'].get('parameters', {})
        elif isinstance(config, dict) and 'heikin_ashi' in config:
            # Direct key access for strategy service config format
            strat_cfg = config['heikin_ashi'].get('parameters', {})
        else:
            strat_cfg = config.get('heikin_ashi_strategy', {})  # Fallback to old key
            
        # Call parent constructor
        super().__init__(config, exchange, database, redis_client)
        
        self.exchange_name = exchange_name or strat_cfg.get('exchange_name', 'binance')
        self.min_candles = strat_cfg.get('min_candles', 50)
        self.adx_period = strat_cfg.get('adx_period', 7)
        self.rsi_period = strat_cfg.get('rsi_period', 7)
        self.atr_period = strat_cfg.get('atr_period', 7)
        self.adx_threshold = strat_cfg.get('adx_threshold', 15)  # FINAL FIX: Use 15 as fallback instead of 20
        self.rsi_overbought = strat_cfg.get('rsi_overbought', 85)  # FINAL FIX: Use 85 as fallback instead of 70
        self.rsi_oversold = strat_cfg.get('rsi_oversold', 15)  # FINAL FIX: Use 15 as fallback instead of 30
        self.rsi_buy_threshold = strat_cfg.get('rsi_buy_threshold', 50)
        self.rsi_confluence_threshold = strat_cfg.get('rsi_confluence_threshold', 30)
        self.rsi_uptrend_min = strat_cfg.get('rsi_uptrend_min', 40)  # ULTRA FINAL FIX: Use 40 to capture RSI 44.17 signals
        self.rsi_uptrend_max = strat_cfg.get('rsi_uptrend_max', 85)  # FINAL FIX: Use 85 as fallback instead of 70
        
        # Enhanced profitability filters
        self.require_ema_confluence = strat_cfg.get('require_ema_confluence', False)  # FINAL FIX: Use False as fallback instead of True
        self.ema_fast_period = strat_cfg.get('ema_fast_period', 9)
        self.ema_slow_period = strat_cfg.get('ema_slow_period', 21)
        self.min_trend_strength = strat_cfg.get('min_trend_strength', 0.002)
        self.trend_period = strat_cfg.get('trend_period', 14)  # Period for trend strength calculation
        self.volume_spike_multiplier = strat_cfg.get('volume_spike_multiplier', 1.02)  # FINAL FIX: Use 1.02 as fallback instead of 1.5
        self.min_candle_size = strat_cfg.get('min_candle_size', 0.002)
        self.volume_sma_period = strat_cfg.get('volume_sma_period', 7)
        self.volume_threshold_percentage = strat_cfg.get('volume_threshold_percentage', 0.2)  # FINAL FIX: Use 0.2 as fallback instead of 0.5
        self.risk_per_trade = strat_cfg.get('risk_per_trade', 0.01)
        self.max_hold_time_hours = strat_cfg.get('max_hold_time_hours', 48)
        self.primary_timeframe = strat_cfg.get('primary_timeframe', '1h')
        
        # Log final loaded parameters for verification
        logger.info(f"✅ HeikinAshi FINAL params: ADX>{self.adx_threshold}, RSI range ({self.rsi_uptrend_min}-{self.rsi_uptrend_max}), Vol spike {self.volume_spike_multiplier}x, Vol base {self.volume_threshold_percentage}, EMA confluence {self.require_ema_confluence}")

    async def initialize(self, pair: str) -> None:
        """Initialize the strategy for a specific trading pair."""
        self.state.pair = pair
        self.state.indicators = {}
        self.state.patterns = {}
        self.state.market_regime = 'unknown'
        self.state.performance = {
            'total_trades': 0,
            'winning_trades': 0,
            'total_pnl': 0.0,
            'win_rate': 0.0,
            'max_drawdown': 0.0,
            'current_drawdown': 0.0,
            'unrealized_pnl': 0.0
        }
        logger.info(f"Initialized Heikin Ashi strategy for {pair}")

    async def analyze_pair(self, pair, min_candles=None, exchange_name=None, market_data=None):
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"[HeikinAshiStrategy] analyze_pair called for {pair}")
        """
        Analyze a trading pair on configured timeframes and return signals for each.
        """
        # Get timeframes from config, fallback to default
        config_timeframes = self.config.get('heikin_ashi', {}).get('target_timeframes', ['1h', '15m'])
        timeframes = config_timeframes
        results = {}
        for timeframe in timeframes:
            logger.info(f"[HeikinAshiStrategy] Analyzing {pair} on {timeframe}")
            min_candles_tf = min_candles or self.min_candles
            
            # Use provided market_data if available, otherwise try to get from exchange
            if market_data is not None and timeframe in market_data:
                df = market_data[timeframe]
            elif self.exchange is not None:
                ex_name = exchange_name if exchange_name is not None else self.exchange_name
                df = await self.exchange.get_ohlcv(ex_name, pair, timeframe, limit=min_candles_tf)
            else:
                logger.warning(f"[HeikinAshiStrategy] No market data available for {pair} on {timeframe} and no exchange object")
                results[timeframe] = ('hold', 0, {})
                continue
                
            if df is None or len(df) < min_candles_tf:
                logger.warning(f"[HeikinAshiStrategy] Not enough data for {pair} on {timeframe}: df={df}")
                logger.debug(f"[HeikinAshiStrategy] DataFrame for {pair} on {timeframe}: {df.shape if df is not None else 'None'}\n{df.head() if df is not None else ''}")
                results[timeframe] = ('hold', 0, {})
                continue
            df = df.sort_index()
            logger.debug(f"[HeikinAshiStrategy] DataFrame shape for {pair} on {timeframe}: {df.shape}")
            logger.debug(f"[HeikinAshiStrategy] DataFrame columns: {df.columns.tolist()}")
            logger.debug(f"[HeikinAshiStrategy] DataFrame head: {df.head()}")
            
            indicators = {}
            try:
                # Check if we have enough data for indicators
                if len(df) < max(self.adx_period, self.rsi_period, self.atr_period, self.volume_sma_period):
                    logger.warning(f"[HeikinAshiStrategy] Not enough data for indicators: {len(df)} < {max(self.adx_period, self.rsi_period, self.atr_period, self.volume_sma_period)}")
                    results[timeframe] = ('hold', 0, {})
                    continue
                
                adx_result = ta.adx(df['high'], df['low'], df['close'], length=self.adx_period)
                rsi = ta.rsi(df['close'], length=self.rsi_period)
                atr = ta.atr(df['high'], df['low'], df['close'], length=self.atr_period)
                volume_sma = df['volume'].rolling(self.volume_sma_period).mean()
                
                # Enhanced profitability indicators
                ema_fast = ta.ema(df['close'], length=self.ema_fast_period)
                ema_slow = ta.ema(df['close'], length=self.ema_slow_period)
                
                # Calculate trend strength (price change over trend period)
                trend_strength = (df['close'].iloc[-1] - df['close'].iloc[-self.trend_period]) / df['close'].iloc[-self.trend_period]
                
                logger.debug(f"[HeikinAshiStrategy] ADX result: {adx_result}")
                logger.debug(f"[HeikinAshiStrategy] RSI result: {rsi}")
                logger.debug(f"[HeikinAshiStrategy] ATR result: {atr}")
                
                # Extract ADX value - pandas-ta returns a DataFrame with ADX, DMP, DMN columns
                if hasattr(adx_result, 'columns') and 'ADX_' + str(self.adx_period) in adx_result.columns:
                    adx = adx_result['ADX_' + str(self.adx_period)].iloc[-1]
                elif hasattr(adx_result, 'iloc'):
                    # If it's a Series, get the last value
                    adx = adx_result.iloc[-1]
                else:
                    # Fallback
                    adx = adx_result[-1]
                
                indicators = {
                    'adx': adx,
                    'rsi': rsi.iloc[-1] if hasattr(rsi, 'iloc') else rsi[-1],
                    'atr': atr.iloc[-1] if hasattr(atr, 'iloc') else atr[-1],
                    'volume': df['volume'].iloc[-1],
                    'volume_sma': volume_sma.iloc[-1],
                    'ema_fast': ema_fast.iloc[-1] if hasattr(ema_fast, 'iloc') else ema_fast[-1],
                    'ema_slow': ema_slow.iloc[-1] if hasattr(ema_slow, 'iloc') else ema_slow[-1],
                    'trend_strength': trend_strength,
                    'current_price': df['close'].iloc[-1]
                }
            except Exception as e:
                logger.error(f"[HeikinAshiStrategy] Indicator calculation error for {pair} on {timeframe}: {e}")
                logger.error(f"[HeikinAshiStrategy] DataFrame info: {df.info()}")
                results[timeframe] = ('hold', 0, {})
                continue
            logger.info(f"[HeikinAshiStrategy] {pair} {timeframe} indicators: {indicators}")
            for name, value in indicators.items():
                if pd.isna(value):
                    logger.warning(f"[HeikinAshiStrategy] Indicator {name} is NaN for {pair} on {timeframe}, holding.")
                    results[timeframe] = ('hold', 0, indicators)
                    break
            else:
                # Enhanced condition logging for Heikin Ashi strategy
                # ADX check: use configured threshold
                adx_pass = indicators['adx'] > self.adx_threshold
                await self._log_condition(
                    'adx_check', indicators['adx'], f'ADX check for {pair} on {timeframe}', adx_pass, 'indicator', 
                    pair, context={'timeframe': timeframe, 'current_value': f"adx={indicators['adx']:.2f}", 'target_value': f"adx > {self.adx_threshold}"}
                )
                
                # Enhanced RSI buy signal: More selective conditions for profitability
                rsi_confluence_threshold = self.rsi_confluence_threshold
                
                # Only two scenarios: strongly oversold OR strong uptrend momentum
                rsi_oversold_signal = indicators['rsi'] < rsi_confluence_threshold  # True oversold (< 30)
                rsi_uptrend_signal = (indicators['rsi'] >= self.rsi_uptrend_min and 
                                    indicators['rsi'] <= self.rsi_uptrend_max)  # Strong momentum 55-70
                rsi_pass = rsi_oversold_signal or rsi_uptrend_signal
                
                await self._log_condition(
                    'rsi_check', indicators['rsi'], f'Enhanced RSI check for {pair} on {timeframe}', rsi_pass, 'indicator', 
                    pair, context={'timeframe': timeframe, 'current_value': f"rsi={indicators['rsi']:.2f}", 'target_value': f"rsi < {rsi_confluence_threshold} OR ({self.rsi_uptrend_min} <= rsi <= {self.rsi_uptrend_max})"}
                )
                
                atr_pass = indicators['atr'] > self.min_candle_size
                await self._log_condition(
                    'atr_check', indicators['atr'], f'ATR check for {pair} on {timeframe}', atr_pass, 'indicator', 
                    pair, context={'timeframe': timeframe, 'current_value': f"atr={indicators['atr']:.4f}", 'target_value': f"atr > {self.min_candle_size}"}
                )
                
                # Enhanced volume check: require volume spike for strong signals
                volume_spike_threshold = indicators['volume_sma'] * self.volume_spike_multiplier
                volume_threshold = indicators['volume_sma'] * self.volume_threshold_percentage
                vol_pass = indicators['volume'] > max(volume_threshold, volume_spike_threshold)
                await self._log_condition(
                    'volume_check', indicators['volume'], f'Enhanced volume check for {pair} on {timeframe}', vol_pass, 'indicator', 
                    pair, context={'timeframe': timeframe, 'current_value': f"volume={indicators['volume']:.2f}", 'target_value': f"volume > {max(volume_threshold, volume_spike_threshold):.2f} (spike: {volume_spike_threshold:.2f}, base: {volume_threshold:.2f})"}
                )
                
                # EMA confluence check: price must be above both EMAs for uptrend confirmation
                ema_pass = True
                if self.require_ema_confluence:
                    price_above_emas = (indicators['current_price'] > indicators['ema_fast'] and 
                                      indicators['current_price'] > indicators['ema_slow'] and
                                      indicators['ema_fast'] > indicators['ema_slow'])  # EMAs aligned
                    ema_pass = price_above_emas
                    await self._log_condition(
                        'ema_confluence_check', indicators['current_price'], f'EMA confluence check for {pair} on {timeframe}', ema_pass, 'indicator', 
                        pair, context={'timeframe': timeframe, 'current_value': f"price={indicators['current_price']:.4f}, ema_fast={indicators['ema_fast']:.4f}, ema_slow={indicators['ema_slow']:.4f}", 'target_value': f"price > ema_fast > ema_slow"}
                    )
                
                # Trend strength check: require minimum trend strength for entries
                trend_strength_pass = abs(indicators['trend_strength']) >= self.min_trend_strength
                await self._log_condition(
                    'trend_strength_check', indicators['trend_strength'], f'Trend strength check for {pair} on {timeframe}', trend_strength_pass, 'indicator', 
                    pair, context={'timeframe': timeframe, 'current_value': f"trend_strength={indicators['trend_strength']:.4f}", 'target_value': f"abs(trend_strength) >= {self.min_trend_strength}"}
                )
                
                checks = [adx_pass, rsi_pass, atr_pass, vol_pass, ema_pass, trend_strength_pass]
                if all(checks):
                    await self._log_condition(
                        'signal_generation', 'buy', f'Signal generation for {pair} on {timeframe}', True, 'signal', 
                        pair, context={'timeframe': timeframe, 'indicators': indicators, 'current_value': f"all_conditions_met: adx={indicators['adx']:.2f}, rsi={indicators['rsi']:.2f}, atr={indicators['atr']:.4f}, trend_strength={indicators['trend_strength']:.4f}", 'target_value': "all enhanced conditions passed"}
                    )
                    results[timeframe] = ('buy', 1, indicators)
                else:
                    failed_checks = []
                    if not adx_pass: failed_checks.append('adx')
                    if not rsi_pass: failed_checks.append('rsi')
                    if not atr_pass: failed_checks.append('atr')
                    if not vol_pass: failed_checks.append('volume')
                    if not ema_pass: failed_checks.append('ema_confluence')
                    if not trend_strength_pass: failed_checks.append('trend_strength')
                    
                    await self._log_condition(
                        'signal_generation', 'hold', f'Signal generation for {pair} on {timeframe}', False, 'signal', 
                        pair, context={'timeframe': timeframe, 'failed_checks': failed_checks, 'indicators': indicators, 'current_value': f"failed_checks: {', '.join(failed_checks)}", 'target_value': "all conditions must pass"}
                    )
                    results[timeframe] = ('hold', 0, indicators)
        return results

    async def update(self, market_data):
        """No-op update for compatibility with the strategy manager."""
        pass

    async def generate_signal(self, market_data, *args, **kwargs):
        import logging
        logger = logging.getLogger(__name__)
        
        # Use exchange_adapter if provided to override self.exchange
        exchange_adapter = kwargs.get('exchange_adapter')
        if exchange_adapter is not None:
            self.exchange = exchange_adapter
        
        # Extract pair from kwargs first, then args, then try to get from market_data if available
        pair = kwargs.get('pair')
        if pair is None and args:
            pair = args[0]
        if pair is None and hasattr(market_data, 'name'):
            pair = market_data.name
        timeframe = kwargs.get('timeframe', self.primary_timeframe)
        min_candles = kwargs.get('min_candles', self.min_candles)
        logger.info(f"[HeikinAshiStrategy] generate_signal called for pair={pair}, timeframe={timeframe}")
        if pair is None:
            logger.error(f"[HeikinAshiStrategy] generate_signal early return: pair is None. args={args}, kwargs={kwargs}")
            return 'hold', 0.0, 0.0
        if market_data is None or len(market_data) == 0:
            logger.error(f"[HeikinAshiStrategy] generate_signal early return: market_data is None or empty for pair={pair}")
            return 'hold', 0.0, 0.0
        try:
            # Pass market_data to analyze_pair if it's a dict with timeframes
            config_timeframes = self.config.get('heikin_ashi', {}).get('target_timeframes', ['1h', '15m'])
            if isinstance(market_data, dict) and any(tf in market_data for tf in config_timeframes):
                results = await self.analyze_pair(pair, min_candles, market_data=market_data)
            else:
                results = await self.analyze_pair(pair, min_candles)
        except Exception as e:
            logger.error(f"[HeikinAshiStrategy] Exception in analyze_pair: {e}")
            return 'hold', 0.0, 0.0
            
        # Use the primary timeframe result (configurable, defaults to 1h if available, otherwise first available)
        if self.primary_timeframe in results:
            signal, confidence, indicators = results[self.primary_timeframe]
        elif results:
            # Use the first available timeframe
            first_tf = list(results.keys())[0]
            signal, confidence, indicators = results[first_tf]
        else:
            return 'hold', 0.0, 0.0
            
        strength = confidence  # For now, use confidence as strength
        
        # Build detailed reason based on indicators
        reason = []
        if indicators and isinstance(indicators, dict):
            if indicators.get('adx', 0) > self.adx_threshold:
                reason.append('adx_strong')
            if indicators.get('rsi', 0) > self.rsi_overbought:
                reason.append('rsi_bullish')
            if indicators.get('atr', 0) > self.min_candle_size:
                reason.append('atr_ok')
            if indicators.get('volume', 0) > indicators.get('volume_sma', 0):
                reason.append('volume_above_sma')
                
        reason_str = '+'.join(reason) if reason else 'no_signal'
        
        return signal, confidence, strength

    async def calculate_position_size(self, signal_type: str) -> float:
        """Calculate position size based on risk management rules."""
        try:
            # Get account balance
            balance = await self.exchange.get_balance()
            available_balance = balance.get('free', 0.0)
            
            # Calculate risk per trade (configurable percentage of available balance)
            risk_amount = available_balance * self.risk_per_trade
            
            # Get current price
            ticker = await self.exchange.get_ticker(self.state.pair, self.exchange_name)
            if not ticker or 'last' not in ticker:
                logger.warning(f"[HeikinAshiStrategy] Could not fetch ticker for {self.state.pair} on {self.exchange_name}")
                return 0.0
            current_price = ticker['last']
            
            # Calculate position size based on risk
            position_size = risk_amount / current_price
            
            return position_size
            
        except Exception as e:
            logger.error(f"Error calculating position size: {str(e)}")
            return 0.0

    async def should_exit(self) -> Tuple[bool, Optional[str]]:
        """Check if current position should be exited based on stop loss, take profit, or fallback logic."""
        import logging
        logger = logging.getLogger(__name__)
        try:
            # No position, nothing to exit
            if not hasattr(self, 'state') or getattr(self.state, 'position', 'none') == 'none':
                logger.debug("[HeikinAshiStrategy] No open position, should_exit=False")
                return False, None

            # Ensure we have recent OHLCV data
            if not hasattr(self, '_current_ohlcv') or self._current_ohlcv is None:
                logger.warning("[HeikinAshiStrategy] No OHLCV data for exit check.")
                return False, None

            ohlcv = self._current_ohlcv
            current_price = ohlcv['close'].iloc[-1]
            pair = getattr(self.state, 'pair', 'N/A')

            # 1. Stop Loss
            if getattr(self.state, 'stop_loss', None) is not None and current_price <= self.state.stop_loss:
                logger.info(f"[HeikinAshiStrategy] Exiting {pair} due to stop loss: {current_price} <= {self.state.stop_loss}")
                return True, 'stop_loss'

            # 2. Take Profit
            if getattr(self.state, 'take_profit', None) is not None and current_price >= self.state.take_profit:
                logger.info(f"[HeikinAshiStrategy] Exiting {pair} due to take profit: {current_price} >= {self.state.take_profit}")
                return True, 'take_profit'

            # 3. Fallback: time-based exit (configurable max hold time)
            if getattr(self.state, 'entry_time', None):
                from datetime import datetime
                time_in_trade = datetime.utcnow() - self.state.entry_time
                max_hold_seconds = self.max_hold_time_hours * 3600  # Convert hours to seconds
                if time_in_trade.total_seconds() > max_hold_seconds:
                    logger.info(f"[HeikinAshiStrategy] Exiting {pair} due to max hold time exceeded: {time_in_trade} > {self.max_hold_time_hours}h")
                    return True, 'max_hold'

            logger.debug(f"[HeikinAshiStrategy] No exit condition met for {pair}.")
            return False, None
        except Exception as e:
            logger.error(f"Error in should_exit: {e}")
            return False, None 