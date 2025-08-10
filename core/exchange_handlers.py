#!/usr/bin/env python3
"""
Exchange Handlers
================

This module provides exchange-specific handlers for managing different symbol formats,
minimum order sizes, and other exchange-specific requirements.
"""

from typing import Dict, Optional, Tuple
import logging

logger = logging.getLogger(__name__)

class ExchangeHandler:
    """Base class for exchange-specific handlers"""
    
    def __init__(self, exchange_name: str):
        self.exchange_name = exchange_name
    
    def format_symbol_for_api(self, symbol: str) -> str:
        """Convert symbol to exchange-specific format for API calls"""
        raise NotImplementedError
    
    def format_symbol_for_display(self, symbol: str) -> str:
        """Convert symbol to display format"""
        raise NotImplementedError
    
    def get_minimum_order_size(self, symbol: str) -> float:
        """Get minimum order size for a symbol"""
        raise NotImplementedError
    
    def get_minimum_notional(self, symbol: str) -> float:
        """Get minimum notional value for a symbol"""
        raise NotImplementedError
    
    def validate_order(self, symbol: str, side: str, amount: float, price: Optional[float] = None) -> Tuple[bool, str]:
        """Validate order parameters"""
        raise NotImplementedError
    
    def get_order_params(self, order_type: str, side: str) -> Dict[str, any]:
        """Get exchange-specific order parameters"""
        return {}

class CryptoComHandler(ExchangeHandler):
    """Handler for Crypto.com exchange"""
    
    def __init__(self):
        super().__init__("cryptocom")
        
        # Crypto.com symbol format mappings
        self.symbol_formats = {
            # Format: display_symbol -> api_symbol
            "BTC/USD": "BTC/USD:USD",
            "ETH/USD": "ETH/USD:USD",
            "CRO/USD": "CRO/USD:USD",
            "1INCH/USD": "1INCH/USD:USD",
            "A2Z/USD": "A2Z/USD:USD",
            "AAVE/USD": "AAVE/USD:USD",
            "ACA/USD": "ACA/USD:USD",
            "ACH/USD": "ACH/USD:USD",
            "ACT/USD": "ACT/USD:USD",
        }
        
        # Minimum order sizes (in base currency)
        self.min_order_sizes = {
            "BTC/USD": 0.00001,
            "ETH/USD": 0.0001,
            "CRO/USD": 1.0,
            "1INCH/USD": 1.0,
            "A2Z/USD": 1.0,
            "AAVE/USD": 0.01,
            "ACA/USD": 1.0,
            "ACH/USD": 1.0,
            "ACT/USD": 1.0,
            "default": 0.000001
        }
        
        # Minimum notional values (in USD)
        self.min_notional = 12.0
    
    def format_symbol_for_api(self, symbol: str) -> str:
        """Convert symbol to Crypto.com API format"""
        # Crypto.com expects symbols with slashes and :USD suffix for USD pairs
        if symbol in self.symbol_formats:
            return self.symbol_formats[symbol]
        
        # For unknown symbols, add :USD suffix if it's a USD pair
        if symbol.endswith("/USD"):
            return f"{symbol}:USD"
        
        return symbol
    
    def format_symbol_for_display(self, symbol: str) -> str:
        """Convert symbol to display format"""
        # Remove :USD suffix for display
        if symbol.endswith(":USD"):
            return symbol[:-4]
        return symbol
    
    def get_minimum_order_size(self, symbol: str) -> float:
        """Get minimum order size for a symbol"""
        return self.min_order_sizes.get(symbol, self.min_order_sizes["default"])
    
    def get_minimum_notional(self, symbol: str) -> float:
        """Get minimum notional value"""
        return self.min_notional
    
    def validate_order(self, symbol: str, side: str, amount: float, price: Optional[float] = None) -> Tuple[bool, str]:
        """Validate order parameters"""
        min_size = self.get_minimum_order_size(symbol)
        
        if amount < min_size:
            return False, f"Order amount {amount} below minimum {min_size} for {symbol}"
        
        if price:
            notional = amount * price
            if notional < self.min_notional:
                return False, f"Order notional ${notional:.2f} below minimum ${self.min_notional} for {symbol}"
        
        return True, "Order validation passed"

class BinanceHandler(ExchangeHandler):
    """Handler for Binance exchange"""
    
    def __init__(self):
        super().__init__("binance")
        
        # Binance symbol format mappings
        self.symbol_formats = {
            "BTC/USDC": "BTCUSDC",
            "ETH/USDC": "ETHUSDC",
            "BNB/USDC": "BNBUSDC",
            "XRP/USDC": "XRPUSDC",
            "XLM/USDC": "XLMUSDC",
            "LINK/USDC": "LINKUSDC",
            "LTC/USDC": "LTCUSDC",
            "TRX/USDC": "TRXUSDC",
            "ADA/USDC": "ADAUSDC",
            "NEO/USDC": "NEOUSDC",
        }
        
        # Minimum order sizes (in base currency)
        self.min_order_sizes = {
            "BTC/USDC": 0.00001,
            "ETH/USDC": 0.0001,
            "BNB/USDC": 0.01,
            "XRP/USDC": 1.0,
            "XLM/USDC": 1.0,
            "LINK/USDC": 1.0,
            "LTC/USDC": 0.01,
            "TRX/USDC": 1.0,
            "ADA/USDC": 1.0,
            "NEO/USDC": 0.01,
            "default": 0.000001
        }
        
        # Minimum notional values (in USDC)
        self.min_notional = 5.0
    
    def format_symbol_for_api(self, symbol: str) -> str:
        """Convert symbol to Binance API format"""
        # Binance expects symbols without slashes
        if symbol in self.symbol_formats:
            return self.symbol_formats[symbol]
        
        # For unknown symbols, remove slashes
        return symbol.replace("/", "")
    
    def format_symbol_for_display(self, symbol: str) -> str:
        """Convert symbol to display format"""
        # Add slashes for display if not present
        if "/" not in symbol:
            # Try to add slash before USDC
            if symbol.endswith("USDC"):
                base = symbol[:-4]
                return f"{base}/USDC"
            elif symbol.endswith("USD"):
                base = symbol[:-3]
                return f"{base}/USD"
        
        return symbol
    
    def get_minimum_order_size(self, symbol: str) -> float:
        """Get minimum order size for a symbol"""
        return self.min_order_sizes.get(symbol, self.min_order_sizes["default"])
    
    def get_minimum_notional(self, symbol: str) -> float:
        """Get minimum notional value"""
        return self.min_notional
    
    def validate_order(self, symbol: str, side: str, amount: float, price: Optional[float] = None) -> Tuple[bool, str]:
        """Validate order parameters"""
        min_size = self.get_minimum_order_size(symbol)
        
        if amount < min_size:
            return False, f"Order amount {amount} below minimum {min_size} for {symbol}"
        
        if price:
            notional = amount * price
            if notional < self.min_notional:
                return False, f"Order notional ${notional:.2f} below minimum ${self.min_notional} for {symbol}"
        
        return True, "Order validation passed"

class BybitHandler(ExchangeHandler):
    """Handler for Bybit exchange"""
    
    def __init__(self):
        super().__init__("bybit")
        
        # Bybit symbol format mappings
        self.symbol_formats = {
            "ETH/USDC": "ETHUSDC",
            "BTC/USDC": "BTCUSDC",
            "XLM/USDC": "XLMUSDC",
            "SOL/USDC": "SOLUSDC",
            "XRP/USDC": "XRPUSDC",
        }
        
        # Minimum order sizes (in base currency)
        self.min_order_sizes = {
            "ETH/USDC": 0.001,
            "BTC/USDC": 0.00001,
            "XLM/USDC": 1.0,
            "SOL/USDC": 0.01,
            "XRP/USDC": 1.0,
            "default": 0.000001
        }
        
        # Minimum notional values (in USDC)
        self.min_notional = 1.0
        
        # Bybit-specific order parameters
        self.order_params = {
            "market_buy": {
                "createMarketBuyOrderRequiresPrice": False,
                "timeInForce": "GTC"
            },
            "market_sell": {
                "timeInForce": "GTC"
            },
            "limit": {
                "timeInForce": "GTC"
            }
        }
    
    def format_symbol_for_api(self, symbol: str) -> str:
        """Convert symbol to Bybit API format"""
        # Bybit expects symbols without slashes
        if symbol in self.symbol_formats:
            return self.symbol_formats[symbol]
        
        # For unknown symbols, remove slashes
        return symbol.replace("/", "")
    
    def format_symbol_for_display(self, symbol: str) -> str:
        """Convert symbol to display format"""
        # Add slashes for display if not present
        if "/" not in symbol:
            # Try to add slash before USDC
            if symbol.endswith("USDC"):
                base = symbol[:-4]
                return f"{base}/USDC"
            elif symbol.endswith("USD"):
                base = symbol[:-3]
                return f"{base}/USD"
        
        return symbol
    
    def get_minimum_order_size(self, symbol: str) -> float:
        """Get minimum order size for a symbol"""
        return self.min_order_sizes.get(symbol, self.min_order_sizes["default"])
    
    def get_minimum_notional(self, symbol: str) -> float:
        """Get minimum notional value"""
        return self.min_notional
    
    def validate_order(self, symbol: str, side: str, amount: float, price: Optional[float] = None) -> Tuple[bool, str]:
        """Validate order parameters"""
        min_size = self.get_minimum_order_size(symbol)
        
        if amount < min_size:
            return False, f"Order amount {amount} below minimum {min_size} for {symbol}"
        
        if price:
            notional = amount * price
            if notional < self.min_notional:
                return False, f"Order notional ${notional:.2f} below minimum ${self.min_notional} for {symbol}"
        
        return True, "Order validation passed"
    
    def get_order_params(self, order_type: str, side: str) -> Dict[str, any]:
        """Get Bybit-specific order parameters"""
        if order_type == 'market':
            if side.lower() == 'buy':
                return self.order_params.get('market_buy', {})
            else:
                return self.order_params.get('market_sell', {})
        elif order_type == 'limit':
            return self.order_params.get('limit', {})
        
        return {}

class ExchangeHandlerManager:
    """Manager for exchange handlers"""
    
    def __init__(self):
        self.handlers: Dict[str, ExchangeHandler] = {
            "cryptocom": CryptoComHandler(),
            "binance": BinanceHandler(),
            "bybit": BybitHandler(),
        }
    
    def get_handler(self, exchange_name: str) -> ExchangeHandler:
        """Get handler for an exchange"""
        handler = self.handlers.get(exchange_name.lower())
        if not handler:
            raise ValueError(f"No handler found for exchange: {exchange_name}")
        return handler
    
    def format_symbol_for_api(self, exchange_name: str, symbol: str) -> str:
        """Format symbol for API calls"""
        handler = self.get_handler(exchange_name)
        return handler.format_symbol_for_api(symbol)
    
    def format_symbol_for_display(self, exchange_name: str, symbol: str) -> str:
        """Format symbol for display"""
        handler = self.get_handler(exchange_name)
        return handler.format_symbol_for_display(symbol)
    
    def get_minimum_order_size(self, exchange_name: str, symbol: str) -> float:
        """Get minimum order size"""
        handler = self.get_handler(exchange_name)
        return handler.get_minimum_order_size(symbol)
    
    def get_minimum_notional(self, exchange_name: str, symbol: str) -> float:
        """Get minimum notional value"""
        handler = self.get_handler(exchange_name)
        return handler.get_minimum_notional(symbol)
    
    def validate_order(self, exchange_name: str, symbol: str, side: str, amount: float, price: Optional[float] = None) -> Tuple[bool, str]:
        """Validate order parameters"""
        handler = self.get_handler(exchange_name)
        return handler.validate_order(symbol, side, amount, price)
    
    def get_order_params(self, exchange_name: str, order_type: str, side: str) -> Dict[str, any]:
        """Get exchange-specific order parameters"""
        handler = self.get_handler(exchange_name)
        return handler.get_order_params(order_type, side)

# Global instance
exchange_handler_manager = ExchangeHandlerManager()
