"""
Exchange Manager for the Multi-Exchange Trading Bot
Handles all exchange operations for Binance, Crypto.com, and Bybit
"""

import asyncio
import logging
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
import ccxt
import ccxt.async_support as ccxt_async
import pandas as pd
import numpy as np
from decimal import Decimal
import time

logger = logging.getLogger(__name__)


class ExchangeManager:
    """Manages multiple exchange connections and operations"""
    
    def __init__(self, config: Dict[str, Any], database_manager=None):
        self.config = config
        self.database_manager = database_manager
        self.exchanges = {}
        self.rate_limits = {}
        self._initialize_exchanges()
        
    def _initialize_exchanges(self) -> None:
        """Initialize exchange connections"""
        exchange_configs = self.config.get('exchanges', {})
        
        for exchange_name, config in exchange_configs.items():
            try:
                # Map exchange names to CCXT exchange classes
                exchange_class_map = {
                    'binance': ccxt_async.binance,
                    'cryptocom': ccxt_async.cryptocom,
                    'bybit': ccxt_async.bybit
                }
                
                if exchange_name not in exchange_class_map:
                    logger.warning(f"Unsupported exchange: {exchange_name}")
                    continue
                    
                exchange_class = exchange_class_map[exchange_name]
                
                # Initialize exchange with configuration
                api_key = config.get('api_key', '').strip()
                api_secret = config.get('api_secret', '').strip()
                if exchange_name == 'binance':
                    print('[DEBUG] BINANCE apiKey:', repr(api_key))
                    print('[DEBUG] BINANCE secret:', repr(api_secret))
                exchange_config = {
                    'apiKey': api_key,
                    'secret': api_secret,
                    'sandbox': config.get('sandbox', False),
                    'enableRateLimit': True,
                    'rateLimit': 100,  # 100ms between requests
                    'timeout': 30000,  # 30 seconds
                }
                if exchange_name == 'bybit':
                    exchange_config['accountType'] = 'UNIFIED'
                if exchange_name == 'binance':
                    exchange_config['options'] = {'defaultType': 'spot'}
                
                # Create exchange instance
                exchange = exchange_class(exchange_config)
                if exchange_name == 'binance':
                    exchange.verbose = True
                    # Monkey-patch the request method to log the endpoint
                    orig_request = exchange.request
                    async def debug_request(*args, **kwargs):
                        logger.info(f"[DEBUG] CCXT REQUEST: method={args[1] if len(args)>1 else ''} url={args[0] if len(args)>0 else ''}")
                        return await orig_request(*args, **kwargs)
                    exchange.request = debug_request
                    logger.info(f"[DEBUG] Monkey-patch applied for {exchange_name}")
                
                # Store exchange and its configuration
                self.exchanges[exchange_name] = {
                    'instance': exchange,
                    'config': config,
                    'last_request': 0,
                    'request_count': 0
                }
                
                # Initialize rate limiting
                self.rate_limits[exchange_name] = {
                    'last_request': 0,
                    'request_count': 0,
                    'rate_limit_delay': config.get('rate_limit_delay', 0.1)
                }
                
                logger.info(f"Initialized {exchange_name} exchange")
                
            except Exception as e:
                logger.error(f"Failed to initialize {exchange_name} exchange: {e}")
                
    async def _rate_limit(self, exchange_name: str) -> None:
        """Apply rate limiting for exchange requests"""
        if exchange_name in self.rate_limits:
            rate_limit = self.rate_limits[exchange_name]
            current_time = time.time()
            
            # Check if we need to wait
            time_since_last = current_time - rate_limit['last_request']
            if time_since_last < rate_limit['rate_limit_delay']:
                await asyncio.sleep(rate_limit['rate_limit_delay'] - time_since_last)
                
            rate_limit['last_request'] = time.time()
            rate_limit['request_count'] += 1
            
    async def _handle_exchange_error(self, exchange_name: str, error: Exception, operation: str) -> None:
        """Handle exchange errors and create alerts"""
        error_msg = f"Exchange error on {exchange_name} during {operation}: {str(error)}"
        logger.error(error_msg)
        
        # Create alert in database
        if self.database_manager:
            await self.database_manager.create_alert({
                'level': 'ERROR',
                'category': 'EXCHANGE',
                'message': error_msg,
                'exchange': exchange_name,
                'details': {
                    'operation': operation,
                    'error_type': type(error).__name__,
                    'timestamp': datetime.utcnow().isoformat()
                }
            })
            
    # Market Data Operations
    async def get_ticker(self, exchange_name: str, symbol: str) -> Optional[Dict[str, Any]]:
        """Get ticker information for a symbol"""
        try:
            await self._rate_limit(exchange_name)
            exchange = self.exchanges[exchange_name]['instance']
            # Validate symbol
            markets = await exchange.load_markets()
            if symbol not in markets:
                import difflib
                suggestion = difflib.get_close_matches(symbol, markets.keys(), n=1)
                msg = f"Symbol {symbol} not found on {exchange_name}."
                if suggestion:
                    msg += f" Did you mean: {suggestion[0]}?"
                logger.error(msg)
                await self._handle_exchange_error(exchange_name, Exception(msg), f"get_ticker({symbol})")
                return None
            ticker = await exchange.fetch_ticker(symbol)
            # Cache the data
            if self.database_manager:
                await self.database_manager.cache_market_data(
                    exchange_name, symbol, 'ticker', ticker, expires_in_minutes=1
                )
            return ticker
        except Exception as e:
            await self._handle_exchange_error(exchange_name, e, f"get_ticker({symbol})")
            return None
            
    async def get_ohlcv(self, exchange_name: str, symbol: str, timeframe: str = '1h', 
                       limit: int = 100) -> Optional[pd.DataFrame]:
        """Get OHLCV data for a symbol"""
        # Defensive check for symbol validity
        if not isinstance(symbol, str) or '/' not in symbol:
            logger.error(f"Invalid symbol passed to get_ohlcv: {symbol} (exchange: {exchange_name}, timeframe: {timeframe})")
            return None
        logger.info(f"[DEBUG] get_ohlcv called with symbol={symbol}, timeframe={timeframe}, limit={limit}")
        try:
            logger.info(f"Requesting OHLCV for {symbol} on {exchange_name} ({timeframe}, limit={limit})")
            await self._rate_limit(exchange_name)
            exchange = self.exchanges[exchange_name]['instance']
            
            # Check cache first
            if self.database_manager:
                cached_data = await self.database_manager.get_cached_market_data(
                    exchange_name, symbol, f'ohlcv_{timeframe}'
                )
                if cached_data:
                    logger.info(f"Loaded {len(cached_data)} cached candles for {symbol} on {exchange_name} ({timeframe})")
                    return pd.DataFrame(cached_data)
            
            # Fetch from exchange
            ohlcv_data = await exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            
            if not ohlcv_data:
                logger.warning(f"No OHLCV data returned for {symbol} on {exchange_name} ({timeframe})")
                return None
                
            # Convert to DataFrame
            df = pd.DataFrame(ohlcv_data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)
            logger.info(f"Fetched {len(df)} candles for {symbol} on {exchange_name} ({timeframe})")
            
            # Cache the data
            if self.database_manager:
                df_reset = df.reset_index()
                df_reset['timestamp'] = df_reset['timestamp'].dt.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                await self.database_manager.cache_market_data(
                    exchange_name, symbol, f'ohlcv_{timeframe}', 
                    df_reset.to_dict('records'), expires_in_minutes=5
                )
                
            return df
            
        except Exception as e:
            logger.error(f"Exchange error on {exchange_name} during get_ohlcv({symbol}, {timeframe}): {e}")
            await self._handle_exchange_error(exchange_name, e, f"get_ohlcv({symbol}, {timeframe})")
            return None
            
    async def get_order_book(self, exchange_name: str, symbol: str, limit: int = 20) -> Optional[Dict[str, Any]]:
        """Get order book for a symbol"""
        try:
            await self._rate_limit(exchange_name)
            exchange = self.exchanges[exchange_name]['instance']
            
            order_book = await exchange.fetch_order_book(symbol, limit)
            
            # Cache the data
            if self.database_manager:
                await self.database_manager.cache_market_data(
                    exchange_name, symbol, 'orderbook', order_book, expires_in_minutes=1
                )
                
            return order_book
            
        except Exception as e:
            await self._handle_exchange_error(exchange_name, e, f"get_order_book({symbol})")
            return None
            
    # Account Operations
    async def get_balance(self, exchange_name: str) -> Optional[Dict[str, Any]]:
        """Get account balance for an exchange"""
        try:
            await self._rate_limit(exchange_name)
            exchange = self.exchanges[exchange_name]['instance']
            logger.info(f"[DEBUG] About to call fetch_balance for {exchange_name}")
            balance = await exchange.fetch_balance()

            # Return the full free/total/used dicts
            balance_info = {
                'free': balance.get('free', {}),
                'total': balance.get('total', {}),
                'used': balance.get('used', {}),
                'timestamp': datetime.utcnow().isoformat()
            }

            # Optionally, update database with base currency as before
            base_currency = self.exchanges[exchange_name]['config'].get('base_currency', 'USDC')
            if self.database_manager:
                await self.database_manager.update_balance(
                    exchange_name,
                    float(balance.get('total', {}).get(base_currency, 0)),
                    float(balance.get('free', {}).get(base_currency, 0)),
                    0, 0
                )

            return balance_info

        except Exception as e:
            logger.error(f"Exception in get_balance for {exchange_name}: {e}")
            logger.error(f"Exception type: {type(e)}, args: {e.args}")
            await self._handle_exchange_error(exchange_name, e, "get_balance")
            return None
            
    async def get_positions(self, exchange_name: str) -> List[Dict[str, Any]]:
        """Get current positions for an exchange"""
        try:
            await self._rate_limit(exchange_name)
            exchange = self.exchanges[exchange_name]['instance']
            
            # Check if exchange supports futures
            if hasattr(exchange, 'fetch_positions'):
                positions = await exchange.fetch_positions()
                return positions
            else:
                logger.warning(f"{exchange_name} does not support futures positions")
                return []
                
        except Exception as e:
            await self._handle_exchange_error(exchange_name, e, "get_positions")
            return []
            
    # Trading Operations
    async def create_order(self, exchange_name: str, symbol: str, order_type: str, 
                          side: str, amount: float, price: Optional[float] = None,
                          params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """Create an order on the exchange"""
        try:
            await self._rate_limit(exchange_name)
            exchange = self.exchanges[exchange_name]['instance']
            
            # Prepare order parameters
            order_params = params or {}
            
            # Add exchange-specific parameters
            if exchange_name == 'bybit':
                order_params['timeInForce'] = 'GTC'
            elif exchange_name == 'binance':
                order_params['timeInForce'] = 'GTC'
                
            # Create the order
            order = await exchange.create_order(
                symbol=symbol,
                type=order_type,
                side=side,
                amount=amount,
                price=price,
                params=order_params
            )
            
            logger.info(f"Created {side} order on {exchange_name} for {symbol}: {order['id']}")
            return order
            
        except Exception as e:
            await self._handle_exchange_error(exchange_name, e, f"create_order({symbol}, {side})")
            return None
            
    async def cancel_order(self, exchange_name: str, order_id: str, symbol: str) -> bool:
        """Cancel an order on the exchange"""
        try:
            await self._rate_limit(exchange_name)
            exchange = self.exchanges[exchange_name]['instance']
            
            result = await exchange.cancel_order(order_id, symbol)
            logger.info(f"Cancelled order {order_id} on {exchange_name}")
            return True
            
        except Exception as e:
            await self._handle_exchange_error(exchange_name, e, f"cancel_order({order_id})")
            return False
            
    async def get_order(self, exchange_name: str, order_id: str, symbol: str) -> Optional[Dict[str, Any]]:
        """Get order information"""
        try:
            await self._rate_limit(exchange_name)
            exchange = self.exchanges[exchange_name]['instance']
            
            order = await exchange.fetch_order(order_id, symbol)
            return order
            
        except Exception as e:
            await self._handle_exchange_error(exchange_name, e, f"get_order({order_id})")
            return None
            
    async def get_open_orders(self, exchange_name: str, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get open orders for an exchange"""
        try:
            await self._rate_limit(exchange_name)
            exchange = self.exchanges[exchange_name]['instance']
            
            orders = await exchange.fetch_open_orders(symbol)
            return orders
            
        except Exception as e:
            await self._handle_exchange_error(exchange_name, e, "get_open_orders")
            return []
            
    # Market Information
    async def get_markets(self, exchange_name: str) -> Dict[str, Any]:
        """Get available markets for an exchange"""
        try:
            await self._rate_limit(exchange_name)
            exchange = self.exchanges[exchange_name]['instance']
            
            markets = await exchange.load_markets()
            return markets
            
        except Exception as e:
            await self._handle_exchange_error(exchange_name, e, "get_markets")
            return {}
            
    async def get_trading_pairs(self, exchange_name: str, base_currency: str = 'USDC') -> List[str]:
        """Get available trading pairs for an exchange (returns valid symbol keys for fetch_ohlcv)"""
        try:
            markets = await self.get_markets(exchange_name)
            # Only return symbols that are valid keys in the markets dict and match the base_currency
            pairs = [symbol for symbol, market in markets.items()
                     if market.get('type') == 'swap' and
                        market.get('quote') == base_currency and
                        market.get('active') and
                        symbol in markets]
            # If no 'swap' markets, fallback to spot pairs with the correct quote
            if not pairs:
                pairs = [symbol for symbol, market in markets.items()
                         if market.get('quote') == base_currency and
                            market.get('active') and
                            symbol in markets]
            return pairs
        except Exception as e:
            logger.error(f"Failed to get trading pairs for {exchange_name}: {e}")
            return []
            
    async def get_24h_volume(self, exchange_name: str, symbol: str) -> Optional[float]:
        """Get 24-hour volume for a symbol"""
        try:
            ticker = await self.get_ticker(exchange_name, symbol)
            if ticker and 'quoteVolume' in ticker:
                return float(ticker['quoteVolume'])
            return None
            
        except Exception as e:
            logger.error(f"Failed to get 24h volume for {symbol} on {exchange_name}: {e}")
            return None
            
    # Simulation Mode Support
    async def create_simulation_order(self, exchange_name: str, symbol: str, order_type: str,
                                    side: str, amount: float, price: Optional[float] = None) -> Dict[str, Any]:
        """Create a simulated order (for simulation mode)"""
        # Generate a fake order ID
        order_id = f"sim_{exchange_name}_{int(time.time() * 1000)}"
        
        # Use current market price if not provided
        if price is None:
            ticker = await self.get_ticker(exchange_name, symbol)
            price = float(ticker['last']) if ticker else 0
            
        # Create simulated order response
        simulated_order = {
            'id': order_id,
            'symbol': symbol,
            'type': order_type,
            'side': side,
            'amount': amount,
            'price': price,
            'cost': amount * price,
            'status': 'closed',
            'filled': amount,
            'remaining': 0,
            'timestamp': int(time.time() * 1000),
            'datetime': datetime.utcnow().isoformat(),
            'fee': {
                'cost': 0,
                'currency': 'USDC'
            },
            'info': {
                'simulated': True,
                'exchange': exchange_name
            }
        }
        
        logger.info(f"Created simulated {side} order on {exchange_name} for {symbol}: {order_id}")
        return simulated_order
        
    # Utility Methods
    async def is_exchange_healthy(self, exchange_name: str) -> bool:
        """Check if exchange is healthy and responsive"""
        try:
            await self._rate_limit(exchange_name)
            exchange = self.exchanges[exchange_name]['instance']
            
            # Try to fetch server time
            await exchange.fetch_time()
            return True
            
        except Exception as e:
            logger.error(f"Exchange {exchange_name} health check failed: {e}")
            return False
            
    async def get_exchange_info(self, exchange_name: str) -> Dict[str, Any]:
        """Get exchange information and status"""
        try:
            exchange = self.exchanges[exchange_name]['instance']
            
            info = {
                'name': exchange_name,
                'url': exchange.urls.get('www', ''),
                'version': exchange.version,
                'has': exchange.has,
                'timeframes': exchange.timeframes,
                'rateLimit': exchange.rateLimit,
                'sandbox': exchange.sandbox,
                'healthy': await self.is_exchange_healthy(exchange_name)
            }
            
            return info
            
        except Exception as e:
            logger.error(f"Failed to get exchange info for {exchange_name}: {e}")
            return {}
            
    async def close(self):
        """Close all exchange connections"""
        for exchange_name, exchange_data in self.exchanges.items():
            try:
                await exchange_data['instance'].close()
                logger.info(f"Closed connection to {exchange_name}")
            except Exception as e:
                logger.error(f"Error closing {exchange_name} connection: {e}")
                
    # Strategy Integration Methods
    async def get_market_data_for_strategy(self, exchange_name: str, symbol: str, 
                                         timeframes: List[str] = ['1h', '15m', '5m']) -> Dict[str, pd.DataFrame]:
        """Get market data for multiple timeframes (for strategy analysis)"""
        market_data = {}
        
        for timeframe in timeframes:
            try:
                ohlcv = await self.get_ohlcv(exchange_name, symbol, timeframe, limit=100)
                if ohlcv is not None:
                    market_data[timeframe] = ohlcv
            except Exception as e:
                logger.error(f"Failed to get {timeframe} data for {symbol} on {exchange_name}: {e}")
                
        return market_data
        
    async def execute_strategy_signal(self, exchange_name: str, signal: Dict[str, Any], 
                                    simulation_mode: bool = False) -> Optional[Dict[str, Any]]:
        """Execute a trading signal from a strategy"""
        try:
            symbol = signal['symbol']
            side = signal['side']  # 'buy' or 'sell'
            amount = signal.get('amount', 0)
            price = signal.get('price')
            order_type = signal.get('order_type', 'market')
            
            if simulation_mode:
                return await self.create_simulation_order(
                    exchange_name, symbol, order_type, side, amount, price
                )
            else:
                return await self.create_order(
                    exchange_name, symbol, order_type, side, amount, price
                )
                
        except Exception as e:
            logger.error(f"Failed to execute strategy signal on {exchange_name}: {e}")
            return None 