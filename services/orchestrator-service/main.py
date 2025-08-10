"""
Orchestrator Service for the Multi-Exchange Trading Bot
Main trading coordination and decision making
"""

import asyncio
import logging
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timedelta
import signal
import sys
import os
import httpx
from fastapi import FastAPI, HTTPException, BackgroundTasks, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import uuid
import time
import numpy as np
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST, CollectorRegistry

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create custom registry to avoid conflicts
custom_registry = CollectorRegistry()

# Prometheus Metrics
trades_total = Counter('orchestrator_trades_total', 'Total number of trades', ['exchange', 'pair', 'side', 'status'], registry=custom_registry)
trades_pnl = Histogram('orchestrator_trades_pnl', 'Trade PnL distribution', ['exchange', 'pair'], buckets=[-10, -5, -2, -1, -0.5, 0, 0.5, 1, 2, 5, 10], registry=custom_registry)
active_trades = Gauge('orchestrator_active_trades', 'Number of active trades', ['exchange', 'pair'], registry=custom_registry)
balance_total = Gauge('orchestrator_balance_total', 'Total balance', ['exchange', 'currency'], registry=custom_registry)
balance_available = Gauge('orchestrator_balance_available', 'Available balance', ['exchange', 'currency'], registry=custom_registry)
cycle_duration = Histogram('orchestrator_cycle_duration_seconds', 'Duration of trading cycles', ['cycle_type'], registry=custom_registry)
signals_generated = Counter('orchestrator_signals_total', 'Total signals generated', ['exchange', 'pair', 'strategy', 'signal_type'], registry=custom_registry)
momentum_filter_decisions = Counter('orchestrator_momentum_filter_total', 'Momentum filter decisions', ['exchange', 'pair', 'decision'], registry=custom_registry)
orders_total = Counter('orchestrator_orders_total', 'Total orders placed', ['exchange', 'pair', 'side', 'type', 'status'], registry=custom_registry)
strategy_performance = Gauge('orchestrator_strategy_performance', 'Strategy performance metrics', ['strategy', 'metric_type'], registry=custom_registry)
risk_metrics = Gauge('orchestrator_risk_metrics', 'Risk management metrics', ['metric_type'], registry=custom_registry)
service_health = Gauge('orchestrator_service_health', 'Service health status', ['service'], registry=custom_registry)
api_requests_total = Counter('orchestrator_api_requests_total', 'Total API requests', ['service', 'endpoint', 'status'], registry=custom_registry)
api_request_duration = Histogram('orchestrator_api_request_duration_seconds', 'API request duration', ['service', 'endpoint'], registry=custom_registry)

class MomentumFilter:
    """
    Transversal momentum filter for trade entry decisions
    Analyzes 24-hour price bands and trend direction to prevent buying peaks or falling knives
    """
    
    def __init__(self, exchange_service_url: str = "http://exchange-service:8003"):
        self.exchange_service_url = exchange_service_url
        
        # Band percentiles for 24h analysis
        self.lower_band_percentile = 20  # 20th percentile = lower support
        self.upper_band_percentile = 95  # 95th percentile = upper resistance
        self.middle_band_lower = 40      # 40th percentile = middle range start
        self.middle_band_upper = 80      # 80th percentile = middle range end
        
        # Trend analysis parameters
        self.trend_short_hours = 6       # Short-term trend (6h)
        self.trend_long_hours = 24       # Long-term trend (24h)
        self.min_trend_strength = 0.01   # 1% minimum trend strength
        
    async def should_allow_entry(self, exchange: str, pair: str) -> Tuple[bool, str]:
        """
        Determine if entry should be allowed based on momentum analysis
        
        Returns:
            (allow_entry: bool, reason: str)
        """
        try:
            # Get 24-hour price data
            price_data = await self._get_price_data(exchange, pair, hours=24)
            if not price_data or len(price_data) < 12:
                return True, "Insufficient data - allowing entry"
            
            # Calculate price bands
            prices = [candle['close'] for candle in price_data]
            current_price = prices[-1]
            
            lower_band = np.percentile(prices, self.lower_band_percentile)
            upper_band = np.percentile(prices, self.upper_band_percentile)
            middle_lower = np.percentile(prices, self.middle_band_lower)
            middle_upper = np.percentile(prices, self.middle_band_upper)
            
            # Determine price position
            if current_price <= lower_band:
                position = "lower_band"
            elif current_price >= upper_band:
                position = "upper_band"
            elif middle_lower <= current_price <= middle_upper:
                position = "middle_band"
            elif current_price < middle_lower:
                position = "lower_middle"
            else:
                position = "upper_middle"
            
            # Calculate trend direction
            trend_direction, trend_strength = await self._calculate_trend(price_data)
            
            # Apply momentum filter logic
            return self._apply_filter_logic(position, trend_direction, trend_strength, 
                                          current_price, lower_band, upper_band)
            
        except Exception as e:
            # On error, allow entry (fail-safe)
            return True, f"Filter error: {e} - allowing entry"
    
    async def _get_price_data(self, exchange: str, pair: str, hours: int = 24) -> Optional[list]:
        """Get OHLCV data for the specified time period"""
        try:
            # Calculate required candles (assuming 1h timeframe)
            limit = min(hours + 5, 100)  # Add buffer, cap at 100
            
            # Convert pair format for URL (remove slash for exchange service)
            url_pair = pair.replace('/', '')
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                url = f"{self.exchange_service_url}/api/v1/market/ohlcv/{exchange}/{url_pair}"
                logger.info(f"[MOMENTUM FILTER] Fetching data from: {url}")
                
                response = await client.get(url, params={"timeframe": "1h", "limit": limit})
                
                if response.status_code == 200:
                    data = response.json()
                    logger.info(f"[MOMENTUM FILTER] Got {len(data) if data else 0} candles for {pair}")
                    return data
                else:
                    logger.warning(f"[MOMENTUM FILTER] Failed to get data for {pair}: {response.status_code}")
                    return None
                    
        except Exception as e:
            logger.error(f"Error fetching price data for {pair} on {exchange}: {e}")
            return None
    
    async def _calculate_trend(self, price_data: list) -> Tuple[str, float]:
        """
        Calculate trend direction and strength
        
        Returns:
            (direction: 'up'/'down'/'neutral', strength: float)
        """
        if len(price_data) < 12:
            return 'neutral', 0.0
            
        prices = [candle['close'] for candle in price_data]
        current_price = prices[-1]
        
        # Short-term trend (6h)
        short_price = prices[-6] if len(prices) >= 6 else prices[0]
        short_trend = (current_price - short_price) / short_price
        
        # Long-term trend (24h) 
        long_price = prices[0]
        long_trend = (current_price - long_price) / long_price
        
        # Weighted trend strength (favor short-term)
        trend_strength = (short_trend * 0.7) + (long_trend * 0.3)
        
        # Determine direction
        if abs(trend_strength) < self.min_trend_strength:
            direction = 'neutral'
        elif trend_strength > 0:
            direction = 'up'
        else:
            direction = 'down'
            
        return direction, abs(trend_strength)
    
    def _apply_filter_logic(self, position: str, trend_direction: str, trend_strength: float,
                           current_price: float, lower_band: float, upper_band: float) -> Tuple[bool, str]:
        """
        Apply momentum filter logic
        
        Filter Rules:
        - Lower band + upward trend = ALLOW (potential reversal)
        - Upper band + any trend = BLOCK (avoid buying peaks)  
        - Lower band + downward trend = BLOCK (avoid falling knife)
        - Middle band = ALLOW (neutral zone, use strategy logic)
        """
        
        band_pct = ((current_price - lower_band) / (upper_band - lower_band)) * 100
        
        if position == "lower_band":
            if trend_direction == 'up' and trend_strength >= self.min_trend_strength:
                return True, f"Lower band reversal - price at {band_pct:.1f}% of 24h range, uptrend {trend_strength:.2%}"
            else:
                return False, f"Lower band falling knife - price at {band_pct:.1f}% of 24h range, trend {trend_direction} {trend_strength:.2%}"
                
        elif position == "upper_band":
            return False, f"Upper band peak - price at {band_pct:.1f}% of 24h range, avoid buying highs"
            
        elif position in ["middle_band", "lower_middle"]:
            return True, f"Middle range - price at {band_pct:.1f}% of 24h range, neutral zone"
            
        elif position == "upper_middle":
            if trend_direction == 'down':
                return False, f"Upper-middle downtrend - price at {band_pct:.1f}% of 24h range, trend {trend_direction} {trend_strength:.2%}"
            else:
                return True, f"Upper-middle stable - price at {band_pct:.1f}% of 24h range, trend {trend_direction}"
        
        # Default: allow
        return True, f"Default case - price at {band_pct:.1f}% of 24h range"

class OrderTracker:
    """Track and monitor orders with timeout and cancellation logic"""
    
    def __init__(self):
        self.pending_orders = {}  # order_id -> order_data
        self.filled_orders = {}   # order_id -> filled_data
        self.cancelled_orders = {} # order_id -> cancel_reason
        self.order_timeouts = {}  # order_id -> timeout_seconds
        self.order_performance = {
            'limit_orders': {'total': 0, 'filled': 0, 'cancelled': 0, 'timeout': 0},
            'market_orders': {'total': 0, 'filled': 0, 'failed': 0},
            'fill_times': [],
            'fee_savings': [],
            'success_rates': {}
        }
        self.performance_lock = asyncio.Lock()
    
    async def track_order(self, order_id: str, order_data: dict, timeout_seconds: int = 300):
        """Track a new order with timeout"""
        self.pending_orders[order_id] = {
            'order_data': order_data,
            'created_at': datetime.utcnow(),
            'last_check': datetime.utcnow(),
            'check_count': 0,
            'trade_id': order_data.get('trade_id')
        }
        self.order_timeouts[order_id] = timeout_seconds
        logger.info(f"📊 Tracking order {order_id} with {timeout_seconds}s timeout")
    
    async def update_order_status(self, order_id: str, status: str, filled_data: dict = None):
        """Update order status and move to appropriate collection"""
        if order_id in self.pending_orders:
            order_info = self.pending_orders.pop(order_id)
            
            if status == 'filled':
                self.filled_orders[order_id] = {
                    **order_info,
                    'filled_data': filled_data,
                    'filled_at': datetime.utcnow()
                }
                logger.info(f"✅ Order {order_id} filled")
            elif status == 'cancelled':
                self.cancelled_orders[order_id] = {
                    **order_info,
                    'cancelled_at': datetime.utcnow()
                }
                logger.info(f"❌ Order {order_id} cancelled")
    
    def should_cancel_order(self, order_id: str) -> bool:
        """Determine if order should be cancelled due to timeout"""
        if order_id not in self.pending_orders:
            return False
        
        order_info = self.pending_orders[order_id]
        created_at = order_info['created_at']
        timeout_seconds = self.order_timeouts.get(order_id, 300)
        
        time_pending = datetime.utcnow() - created_at
        return time_pending.total_seconds() > timeout_seconds
    
    def get_pending_orders(self) -> Dict[str, dict]:
        """Get all pending orders"""
        return self.pending_orders.copy()
    
    def get_order_info(self, order_id: str) -> Optional[dict]:
        """Get order information"""
        return self.pending_orders.get(order_id) or self.filled_orders.get(order_id) or self.cancelled_orders.get(order_id)

    async def record_order_attempt(self, order_type: str, order_data: dict):
        """Record order attempt for performance tracking"""
        async with self.performance_lock:
            if order_type == 'limit':
                self.order_performance['limit_orders']['total'] += 1
            elif order_type == 'market':
                self.order_performance['market_orders']['total'] += 1
            
            # Store order data for later analysis
            order_id = order_data.get('id')
            if order_id:
                self.pending_orders[order_id] = {
                    'type': order_type,
                    'start_time': time.time(),
                    'data': order_data
                }

    async def record_order_result(self, order_id: str, result: str, fill_time: float = None, fee_saved: float = None):
        """Record order result for performance tracking"""
        async with self.performance_lock:
            if order_id in self.pending_orders:
                order_info = self.pending_orders[order_id]
                order_type = order_info['type']
                
                if order_type == 'limit':
                    if result == 'filled':
                        self.order_performance['limit_orders']['filled'] += 1
                    elif result == 'cancelled':
                        self.order_performance['limit_orders']['cancelled'] += 1
                    elif result == 'timeout':
                        self.order_performance['limit_orders']['timeout'] += 1
                elif order_type == 'market':
                    if result == 'filled':
                        self.order_performance['market_orders']['filled'] += 1
                    elif result == 'failed':
                        self.order_performance['market_orders']['failed'] += 1
                
                # Record fill time if available
                if fill_time is not None:
                    self.order_performance['fill_times'].append({
                        'order_id': order_id,
                        'type': order_type,
                        'fill_time': fill_time
                    })
                
                # Record fee savings if available
                if fee_saved is not None and fee_saved > 0:
                    self.order_performance['fee_savings'].append({
                        'order_id': order_id,
                        'type': order_type,
                        'fee_saved': fee_saved
                    })
                
                # Remove from pending orders
                del self.pending_orders[order_id]

    async def get_performance_metrics(self) -> dict:
        """Get comprehensive performance metrics"""
        async with self.performance_lock:
            limit_orders = self.order_performance['limit_orders']
            market_orders = self.order_performance['market_orders']
            
            # Calculate success rates
            limit_success_rate = 0
            if limit_orders['total'] > 0:
                limit_success_rate = limit_orders['filled'] / limit_orders['total']
            
            market_success_rate = 0
            if market_orders['total'] > 0:
                market_success_rate = market_orders['filled'] / market_orders['total']
            
            # Calculate average fill times
            avg_fill_time = 0
            if self.order_performance['fill_times']:
                avg_fill_time = sum(f['fill_time'] for f in self.order_performance['fill_times']) / len(self.order_performance['fill_times'])
            
            # Calculate total fee savings
            total_fee_savings = sum(f['fee_saved'] for f in self.order_performance['fee_savings'])
            
            return {
                'limit_orders': {
                    'total': limit_orders['total'],
                    'filled': limit_orders['filled'],
                    'cancelled': limit_orders['cancelled'],
                    'timeout': limit_orders['timeout'],
                    'success_rate': limit_success_rate
                },
                'market_orders': {
                    'total': market_orders['total'],
                    'filled': market_orders['filled'],
                    'failed': market_orders['failed'],
                    'success_rate': market_success_rate
                },
                'performance': {
                    'average_fill_time_seconds': avg_fill_time,
                    'total_fee_savings_usd': total_fee_savings,
                    'total_orders_processed': limit_orders['total'] + market_orders['total']
                },
                'recent_fill_times': self.order_performance['fill_times'][-10:],  # Last 10 orders
                'recent_fee_savings': self.order_performance['fee_savings'][-10:]  # Last 10 orders
            }

# Global order tracker
order_tracker = OrderTracker()

# Initialize FastAPI app
app = FastAPI(
    title="Orchestrator Service",
    description="Main trading coordination and decision making",
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

# Metrics endpoint
@app.get("/metrics")
async def get_metrics():
    """Prometheus metrics endpoint"""
    return Response(generate_latest(custom_registry), media_type=CONTENT_TYPE_LATEST)

# Data Models
class TradingStatus(BaseModel):
    status: str  # 'running', 'stopped', 'emergency_stop'
    cycle_count: int
    active_trades: int
    total_pnl: float
    last_cycle: datetime
    uptime: timedelta

class TradingCycle(BaseModel):
    cycle_id: str
    cycle_type: str  # 'entry', 'exit', 'maintenance'
    start_time: datetime
    end_time: Optional[datetime] = None
    trades_processed: int
    signals_generated: int
    errors: List[str]

class RiskLimits(BaseModel):
    max_concurrent_trades: int
    max_daily_trades: int
    max_daily_loss: float
    max_total_loss: float
    position_size_percentage: float

class TradeSignal(BaseModel):
    exchange: str
    pair: str
    signal: str  # 'buy', 'sell', 'hold'
    confidence: float
    strength: float
    strategy: str
    timestamp: datetime

class HealthResponse(BaseModel):
    status: str
    timestamp: datetime
    version: str
    trading_status: str
    cycle_count: int
    active_trades: int

# Global variables
trading_status = "stopped"
cycle_count = 0
active_trades_dict = {}
pair_selections = {}
balances = {}
running = False
start_time = None

# Service URLs
config_service_url = os.getenv("CONFIG_SERVICE_URL", "http://config-service:8001")
database_service_url = os.getenv("DATABASE_SERVICE_URL", "http://database-service:8002")
exchange_service_url = os.getenv("EXCHANGE_SERVICE_URL", "http://exchange-service:8003")
strategy_service_url = os.getenv("STRATEGY_SERVICE_URL", "http://strategy-service:8004")

class TradingOrchestrator:
    """Main orchestrator for the multi-exchange trading bot"""
    
    def __init__(self):
        self.running = False
        self.cycle_count = 0
        self.active_trades_dict = {}
        self.pair_selections = {}
        self.balances = {}
        self.start_time = None
        self.exiting_trades = set()  # Track trades currently being exited to prevent duplicates
        self.momentum_filter = MomentumFilter()  # Initialize momentum filter
        self.last_pair_update = datetime.utcnow()  # Track when pairs were last updated
        
    async def _emit_order_created_event(self, local_order_id: str, client_order_id: str, trade_id: Optional[str], 
                                       exchange: str, symbol: str, side: str, order_type: str, 
                                       amount: float, price: Optional[float] = None) -> bool:
        """Emit OrderCreated event (Phase 2)"""
        try:
            event_data = {
                'aggregate_id': local_order_id,
                'aggregate_type': 'order',
                'event_type': 'OrderCreated',
                'payload': {
                    'local_order_id': local_order_id,
                    'client_order_id': client_order_id,
                    'trade_id': trade_id,
                    'exchange': exchange,
                    'symbol': symbol,
                    'side': side,
                    'order_type': order_type,
                    'amount': amount,
                    'price': price,
                    'created_at': datetime.utcnow().isoformat()
                },
                'correlation_id': trade_id  # Link to trade
            }
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(f"{database_service_url}/api/v1/events", json=event_data)
                response.raise_for_status()
                result = response.json()
                logger.info(f"📝 OrderCreated event emitted: {result['event_id']}")
                return True
        except Exception as e:
            logger.error(f"Failed to emit OrderCreated event: {e}")
            return False
    
    async def _emit_exchange_ack_event(self, local_order_id: str, exchange_order_id: str, 
                                      exchange_response: Dict[str, Any]) -> bool:
        """Emit ExchangeAck event (Phase 2)"""
        try:
            event_data = {
                'aggregate_id': local_order_id,
                'aggregate_type': 'order', 
                'event_type': 'ExchangeAck',
                'payload': {
                    'local_order_id': local_order_id,
                    'exchange_order_id': exchange_order_id,
                    'exchange_response': exchange_response,
                    'acknowledged_at': datetime.utcnow().isoformat()
                }
            }
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(f"{database_service_url}/api/v1/events", json=event_data)
                response.raise_for_status()
                result = response.json()
                logger.info(f"📝 ExchangeAck event emitted: {result['event_id']}")
                return True
        except Exception as e:
            logger.error(f"Failed to emit ExchangeAck event: {e}")
            return False
        
    def _generate_client_order_id(self, trade_id: Optional[str] = None, order_type: str = "market") -> str:
        """Generate unique client order ID for idempotency (Phase 0)"""
        timestamp = int(time.time() * 1000)  # milliseconds
        random_component = str(uuid.uuid4()).replace('-', '')[:8]  # Remove hyphens, first 8 chars
        
        if trade_id:
            # Include trade_id prefix for linkability - use alphanumeric only for Crypto.com compatibility
            trade_prefix = trade_id.replace('-', '')[:8]  # Remove hyphens from trade_id
            order_prefix = order_type[:1].upper()  # M for market, L for limit
            return f"oms{trade_prefix}{order_prefix}{timestamp}{random_component}"
        else:
            # Standalone order ID - alphanumeric only
            order_prefix = order_type[:1].upper()  # M for market, L for limit
            return f"oms{order_prefix}{timestamp}{random_component}"
        
    async def initialize(self) -> bool:
        """Initialize the orchestrator"""
        try:
            logger.info("Initializing Trading Orchestrator...")
            
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
            # Get exchanges from config service
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.get(f"{config_service_url}/api/v1/config/exchanges/list")
                response.raise_for_status()
                exchanges = response.json()['exchanges']
                # Check if simulation mode
                mode_response = await client.get(f"{config_service_url}/api/v1/config/mode")
                mode_response.raise_for_status()
                mode_data = mode_response.json()
                if mode_data['is_simulation']:
                    # In simulation mode, get balance from database
                    for exchange_name in exchanges:
                        try:
                            balance_response = await client.get(f"{database_service_url}/api/v1/balances/{exchange_name}")
                            if balance_response.status_code == 200:
                                balance = balance_response.json()
                                self.balances[exchange_name] = {
                                    'total': float(balance['balance']),
                                    'available': float(balance['available_balance']),
                                    'total_pnl': float(balance['total_pnl']),
                                    'daily_pnl': float(balance['daily_pnl'])
                                }
                                logger.info(f"[DEBUG] Loaded balance for {exchange_name}: total={balance['balance']}, available={balance['available_balance']}, total_pnl={balance['total_pnl']}, daily_pnl={balance['daily_pnl']}")
                            else:
                                # Initialize with default values
                                self.balances[exchange_name] = {
                                    'total': 10000.0,  # Default simulation balance
                                    'available': 10000.0,
                                    'total_pnl': 0.0,
                                    'daily_pnl': 0.0
                                }
                                logger.info(f"[DEBUG] Using default balance for {exchange_name}: total=10000.0, available=10000.0, total_pnl=0.0, daily_pnl=0.0")
                        except Exception as balance_error:
                            logger.error(f"Error getting balance for {exchange_name}: {balance_error}")
                            # Initialize with default values
                            self.balances[exchange_name] = {
                                'total': 10000.0,
                                'available': 10000.0,
                                'total_pnl': 0.0,
                                'daily_pnl': 0.0
                            }
                            logger.info(f"[DEBUG] Using default balance for {exchange_name} due to error: total=10000.0, available=10000.0, total_pnl=0.0, daily_pnl=0.0")
                else:
                    # In live mode, get balance from exchange
                    for exchange_name in exchanges:
                        try:
                            balance_response = await client.get(f"{exchange_service_url}/api/v1/account/balance/{exchange_name}")
                            if balance_response.status_code == 200:
                                balance = balance_response.json()
                                
                                # DEBUG: Log the raw balance structure for debugging
                                logger.info(f"🔍 [BALANCE DEBUG] {exchange_name} raw response structure:")
                                logger.info(f"  Keys: {list(balance.keys())}")
                                if 'total' in balance:
                                    logger.info(f"  total: {balance['total']}")
                                if 'free' in balance:
                                    logger.info(f"  free: {balance['free']}")
                                if 'available' in balance:
                                    logger.info(f"  available: {balance['available']}")
                                
                                # Get base currency for this exchange
                                base_currency = 'USDC'  # Default
                                if exchange_name == 'cryptocom':
                                    base_currency = 'USD'
                                
                                # Parse total balance
                                total_balance = 0
                                if 'total' in balance and isinstance(balance['total'], dict):
                                    total_balance = balance['total'].get(base_currency, 0) or 0
                                
                                # Parse available balance with improved Bybit handling
                                available_balance = 0
                                if 'free' in balance and isinstance(balance['free'], dict):
                                    available_balance = balance['free'].get(base_currency, 0)
                                    if available_balance is None:
                                        available_balance = 0
                                
                                # Special handling for Bybit unified accounts
                                if exchange_name == 'bybit':
                                    # If free balance is 0 or None, try the 'available' field
                                    if available_balance == 0 and 'available' in balance:
                                        available_balance = balance.get('available', 0) or 0
                                        logger.info(f"🔧 [BALANCE FIX] {exchange_name} using 'available' field: {available_balance}")
                                
                                logger.info(f"💰 [BALANCE PARSED] {exchange_name}: total={total_balance}, available={available_balance}")
                                
                                self.balances[exchange_name] = {
                                    'total': total_balance,
                                    'available': available_balance,
                                    'total_pnl': 0.0,  # Will be calculated from trades
                                    'daily_pnl': 0.0   # Will be calculated from trades
                                }
                                
                                # Store balance data in database during initialization
                                try:
                                    balance_data = {
                                        'exchange': exchange_name,
                                        'balance': total_balance,
                                        'available_balance': available_balance,
                                        'total_pnl': 0.0,
                                        'daily_pnl': 0.0,
                                        'timestamp': datetime.utcnow().isoformat()
                                    }
                                    db_response = await client.put(f"{database_service_url}/api/v1/balances/{exchange_name}", json=balance_data)
                                    if db_response.status_code in [200, 201]:
                                        logger.info(f"Successfully initialized balance for {exchange_name} in database")
                                    else:
                                        logger.warning(f"Failed to initialize balance for {exchange_name} in database: {db_response.status_code}")
                                except Exception as db_error:
                                    logger.error(f"Error initializing balance for {exchange_name} in database: {db_error}")
                                
                                logger.info(f"[DEBUG] Loaded live balance for {exchange_name}: total={total_balance}, available={available_balance}")
                            else:
                                logger.warning(f"Could not get balance for {exchange_name}")
                        except Exception as balance_error:
                            logger.error(f"Error getting balance for {exchange_name}: {balance_error}")
            logger.info(f"Initialized balances for {len(exchanges)} exchanges: {list(self.balances.keys())}")
            logger.info(f"[DEBUG] Final in-memory balances: {self.balances}")
        except Exception as e:
            logger.error(f"Error initializing balances: {e}")
            # Set default balances on error
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    response = await client.get(f"{config_service_url}/api/v1/config/exchanges/list")
                    response.raise_for_status()
                    exchanges = response.json()['exchanges']
                    for exchange_name in exchanges:
                        self.balances[exchange_name] = {
                            'total': 10000.0,
                            'available': 10000.0,
                            'total_pnl': 0.0,
                            'daily_pnl': 0.0
                        }
                    logger.info(f"Set default balances for {len(exchanges)} exchanges due to initialization error")
            except Exception as fallback_error:
                logger.error(f"Failed to set default balances: {fallback_error}")
            
    async def _initialize_pair_selections(self) -> None:
        """Initialize pair selections for all exchanges"""
        try:
            exchanges = []
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.get(f"{config_service_url}/api/v1/config/exchanges/list")
                response.raise_for_status()
                exchanges = response.json()['exchanges']

                for exchange_name in exchanges:
                    # Get exchange configuration to check max_pairs limit
                    exchange_config_response = await client.get(f"{config_service_url}/api/v1/config/exchanges/{exchange_name}")
                    if exchange_config_response.status_code == 200:
                        exchange_config = exchange_config_response.json()
                        max_pairs = exchange_config.get('max_pairs', 10)
                        logger.info(f"[DEBUG] {exchange_name} config from service: {exchange_config}")
                        logger.info(f"[DEBUG] {exchange_name} max_pairs extracted: {max_pairs}")
                    else:
                        max_pairs = 10  # Default fallback
                        logger.warning(f"[DEBUG] {exchange_name} config service error: {exchange_config_response.status_code}, using fallback max_pairs: {max_pairs}")
                    
                    # Get latest pair selection from database
                    pairs_response = await client.get(f"{database_service_url}/api/v1/pairs/{exchange_name}")
                    if pairs_response.status_code == 200:
                        pairs_data = pairs_response.json()
                        all_pairs = pairs_data.get('pairs', [])
                        
                        # Debug logging
                        logger.info(f"[DEBUG] {exchange_name} pairs_data: {pairs_data}")
                        logger.info(f"[DEBUG] {exchange_name} all_pairs: {all_pairs}, type: {type(all_pairs)}, len: {len(all_pairs) if all_pairs else 'None'}")
                        logger.info(f"[DEBUG] {exchange_name} condition check: all_pairs={bool(all_pairs)}, len>0={len(all_pairs) > 0 if all_pairs else False}")
                        
                        # Check if pairs exist and are not empty
                        if all_pairs and len(all_pairs) > 0:
                            # Enforce max_pairs limit
                            if len(all_pairs) > max_pairs:
                                logger.warning(f"Exchange {exchange_name} has {len(all_pairs)} pairs, limiting to {max_pairs} as per configuration")
                                self.pair_selections[exchange_name] = all_pairs[:max_pairs]
                            else:
                                self.pair_selections[exchange_name] = all_pairs
                            
                            logger.info(f"Exchange {exchange_name}: {len(self.pair_selections[exchange_name])}/{max_pairs} pairs selected from database")
                        else:
                            # Pairs array is empty, generate new pairs using pair selector
                            logger.info(f"Empty pairs found for {exchange_name} in database, generating new pair selection")
                            await self._generate_and_store_pairs(client, exchange_name, max_pairs)
                    else:
                        # No pairs endpoint available, generate new pairs using pair selector
                        logger.info(f"No pairs found for {exchange_name} in database, generating new pair selection")
                        await self._generate_and_store_pairs(client, exchange_name, max_pairs)
                        
                logger.info(f"Initialized pair selections for {len(exchanges)} exchanges")
        except Exception as e:
            logger.error(f"Error initializing pair selections: {e}")
                        
    async def _generate_and_store_pairs(self, client, exchange_name, max_pairs, base_currency):
        """Generate and store pairs for an exchange"""
        try:
            from core.pair_selector import select_top_pairs_ccxt
            
            # Use the provided base_currency parameter
            logger.info(f"Generating pairs for {exchange_name} with base currency: {base_currency}")
                
            # Generate new pairs
            pair_result = await select_top_pairs_ccxt(exchange_name, base_currency, max_pairs, 'spot')
            selected_pairs = pair_result['selected_pairs']
            
            # Always forcibly add CRO/USD for cryptocom if not already present
            if exchange_name.lower() == "cryptocom":
                logger.info(f"[DEBUG] cryptocom pairs before force-add: {selected_pairs}")
                if "CRO/USD" not in selected_pairs:
                    selected_pairs.append("CRO/USD")
                    logger.info(f"[FORCE] Added CRO/USD to cryptocom pairs")
                logger.info(f"[FORCE] cryptocom pairs to be saved: {selected_pairs}")
            
            # Store pairs in database
            db_response = await client.post(f"{database_service_url}/api/v1/pairs/{exchange_name}", json=selected_pairs)
            if db_response.status_code in [200, 201]:
                logger.info(f"Successfully stored {len(selected_pairs)} pairs for {exchange_name} in database")
                self.pair_selections[exchange_name] = selected_pairs
            else:
                logger.warning(f"Failed to store pairs for {exchange_name} in database: {db_response.status_code}")
                self.pair_selections[exchange_name] = selected_pairs  # Use them anyway
                
            logger.info(f"Exchange {exchange_name}: {len(self.pair_selections[exchange_name])}/{max_pairs} pairs selected and stored")
        except Exception as pair_error:
            logger.error(f"Error generating pairs for {exchange_name}: {pair_error}")
            self.pair_selections[exchange_name] = []

            logger.info(f"Initialized pair selections for {len(exchanges)} exchanges")

        except Exception as e:
            logger.error(f"Error initializing pair selections: {e}")
            
    async def start_trading(self) -> bool:
        """Start the trading orchestrator"""
        try:
            if self.running:
                logger.warning("Trading orchestrator is already running")
                return True
                
            if not await self.initialize():
                logger.error("Failed to initialize, cannot start trading")
                return False
                
            self.running = True
            self.start_time = datetime.utcnow()
            global trading_status, start_time
            trading_status = "running"
            start_time = self.start_time
            
            logger.info("Starting trading orchestrator...")
            
            # Start trading loop in background
            asyncio.create_task(self._trading_loop())
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to start trading: {e}")
            return False
            
    async def stop_trading(self) -> bool:
        """Stop the trading orchestrator"""
        try:
            self.running = False
            global trading_status
            trading_status = "stopped"
            logger.info("Trading orchestrator stopped")
            return True
            
        except Exception as e:
            logger.error(f"Failed to stop trading: {e}")
            return False
            
    async def emergency_stop(self) -> bool:
        """Emergency stop all trading activities"""
        try:
            self.running = False
            global trading_status
            trading_status = "emergency_stop"
            
            # Close all active trades
            await self._close_all_trades()
            
            logger.info("Emergency stop executed - all trading activities halted")
            return True
            
        except Exception as e:
            logger.error(f"Failed to execute emergency stop: {e}")
            return False
            
    async def _trading_loop(self) -> None:
        """Main trading loop with order monitoring"""
        try:
            # Get trading configuration
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.get(f"{config_service_url}/api/v1/config/trading")
                response.raise_for_status()
                trading_config = response.json()
            
            cycle_interval = trading_config.get('cycle_interval_seconds', 60)
            exit_cycle_first = trading_config.get('exit_cycle_first', True)
            
            # Start order monitoring in background
            logger.info("🔄 Starting order monitoring in background")
            order_monitoring_task = asyncio.create_task(self._monitor_pending_orders())
            
            while self.running:
                try:
                    cycle_start = datetime.utcnow()
                    self.cycle_count += 1
                    global cycle_count
                    cycle_count = self.cycle_count
                    
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
            self.running = False
            global trading_status
            trading_status = "stopped"
        finally:
            # Cancel order monitoring task when trading stops
            if 'order_monitoring_task' in locals():
                order_monitoring_task.cancel()
                try:
                    await order_monitoring_task
                except asyncio.CancelledError:
                    logger.info("🛑 Order monitoring task cancelled")
            
    async def _run_exit_cycle(self) -> None:
        """Run exit cycle to check for trade exits"""
        cycle_start_time = time.time()
        try:
            logger.info("Running exit cycle...")
            
            # Get all open trades
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.get(f"{database_service_url}/api/v1/trades/open")
                response.raise_for_status()
                open_trades = response.json()['trades']
            logger.info(f"Exit cycle processing {len(open_trades)} open trades")
            
            # Update active trades metrics
            for trade in open_trades:
                active_trades.labels(exchange=trade['exchange'], pair=trade['pair']).inc()
            
            processed_count = 0
            skipped_count = 0
            for trade in open_trades:
                processed_count += 1
                if trade['entry_price'] <= 0:
                    skipped_count += 1
                await self._check_trade_exit(trade)
                
            logger.info(f"Exit cycle completed: {processed_count} trades processed, {skipped_count} skipped")
            
            # Record cycle duration
            cycle_duration.labels(cycle_type='exit').observe(time.time() - cycle_start_time)
                
        except Exception as e:
            logger.error(f"Error in exit cycle: {str(e)}")
            logger.error(f"Exception type: {type(e).__name__}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            # Record cycle duration even on error
            cycle_duration.labels(cycle_type='exit').observe(time.time() - cycle_start_time)
            
    async def _check_trade_exit(self, trade: Dict[str, Any]) -> None:
        """Check if a trade should be exited"""
        try:
            trade_id = trade.get('trade_id')
            if not trade_id or not isinstance(trade_id, str):
                logger.warning(f"[ExitCheck] Skipping: invalid or missing trade_id: {trade_id}")
                return

            # Get trading configuration for stop loss and trailing stop
            default_stop_loss = -2.0  # Fallback if config fetch fails
            trailing_trigger_pct = 2.0  # Fallback if config fetch fails
            trailing_step_pct = 1.0  # Fallback if config fetch fails
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    response = await client.get(f"{config_service_url}/api/v1/config/trading")
                    response.raise_for_status()
                    trading_config = response.json()
                    # Convert stop_loss_percentage to negative percentage (e.g., 0.035 -> -3.5)
                    config_stop_loss_pct = trading_config.get('stop_loss_percentage', 0.02)
                    default_stop_loss = -(config_stop_loss_pct * 100)
                    # Convert trailing_stop config to percentages
                    trailing_stop_config = trading_config.get('trailing_stop', {})
                    trailing_trigger_decimal = trailing_stop_config.get('trigger_percentage', 0.02)
                    trailing_trigger_pct = trailing_trigger_decimal * 100
                    trailing_step_decimal = trailing_stop_config.get('step_percentage', 0.01)
                    trailing_step_pct = trailing_step_decimal * 100
                    logger.info(f"[Trade {trade_id}] [ExitCheck] Loaded config - stop loss: {default_stop_loss}%, trailing trigger: {trailing_trigger_pct}%, trailing step: {trailing_step_pct}%")
            except Exception as e:
                logger.warning(f"[Trade {trade_id}] [ExitCheck] Could not fetch trading config, using fallback values: {e}")

            # Defensive: Validate and convert entry_price, position_size
            try:
                entry_price = trade.get('entry_price')
                position_size = trade.get('position_size')
                if entry_price is None or position_size is None:
                    raise ValueError("entry_price or position_size is None")
                entry_price = float(entry_price)
                position_size = float(position_size)
                if entry_price == 0.0 or position_size == 0.0:
                    raise ValueError("entry_price or position_size is zero")
            except Exception as e:
                logger.warning(f"[Trade {trade_id}] [ExitCheck] Skipping: invalid entry/size: {e}")
                return

            # Get current price
            try:
                exchange = trade.get('exchange')
                pair = trade.get('pair')
                exchange_pair = pair.replace('/', '') if pair else ''
                async with httpx.AsyncClient(timeout=60.0) as client:
                    response = await client.get(f"{exchange_service_url}/api/v1/market/ticker/{exchange}/{exchange_pair}")
                    response.raise_for_status()
                    ticker = response.json()
                    current_price = float(ticker.get('last', 0.0))
                if current_price == 0.0:
                    raise ValueError("current_price is zero")
            except Exception as e:
                logger.warning(f"[Trade {trade_id}] [ExitCheck] Skipping: invalid current_price: {e}")
                return

            # Defensive: Validate and convert trail_stop_trigger, fallback to config value
            raw_stop = trade.get('trail_stop_trigger')
            try:
                if raw_stop is None:
                    raise ValueError("trail_stop_trigger is None")
                current_stop_loss = float(raw_stop)
            except Exception:
                current_stop_loss = default_stop_loss  # Use config value instead of hardcoded -2.0
                logger.info(f"[Trade {trade_id}] [ExitCheck] trail_stop_trigger not set, using config default: {current_stop_loss}%")
            logger.info(f"[Trade {trade_id}] [ExitCheck] Using stop loss: {current_stop_loss}%")

            # Calculate PnL and update trade data
            try:
                if position_size > 0:  # Long position
                    pnl_percentage = ((current_price - entry_price) / entry_price) * 100
                    unrealized_pnl = (current_price - entry_price) * position_size
                else:  # Short position
                    pnl_percentage = ((entry_price - current_price) / entry_price) * 100
                    unrealized_pnl = (entry_price - current_price) * position_size
            except Exception as e:
                logger.warning(f"[Trade {trade_id}] [ExitCheck] Skipping: error calculating PnL: {e}")
                return

            # Update highest price if current price is higher
            highest_price = trade.get('highest_price')
            try:
                if highest_price is None:
                    highest_price = entry_price
                highest_price = float(highest_price)
                if current_price > highest_price:
                    highest_price = current_price
            except Exception:
                highest_price = current_price

            # Update trade with current data
            await self._update_trade_data(trade_id, {
                'unrealized_pnl': unrealized_pnl,
                'highest_price': highest_price,
                'current_price': current_price
            })

            # Check exit conditions with profit protection
            exit_reason = None
            should_exit = False

            # Profit protection: Move stop loss to breakeven when profit reaches 1%
            profit_protection_status = trade.get('profit_protection')
            logger.info(f"[Trade {trade_id}] [ProfitProtection] Checking conditions - PnL: {pnl_percentage:.2f}%, Current Stop: {current_stop_loss:.2f}%, Target: 1.0%, Status: {profit_protection_status}")
            
            # Only trigger profit protection if it hasn't been activated yet
            if pnl_percentage >= 1.0 and not profit_protection_status:
                # Move to guarantee 0.5% profit instead of just breakeven
                new_stop_loss = 0.5  # Guarantee 0.5% profit
                logger.info(f"[Trade {trade_id}] [ProfitProtection] ✅ TRIGGERED: PnL {pnl_percentage:.2f}% >= 1.0% AND no profit protection active")
                logger.info(f"[Trade {trade_id}] [ProfitProtection] Moving stop loss: {current_stop_loss:.2f}% → {new_stop_loss:.2f}% (guaranteeing 0.5% profit)")
                await self._update_trade_data(trade_id, {
                    'trail_stop_trigger': new_stop_loss,
                    'profit_protection': 'profit_guaranteed',
                    'profit_protection_trigger': pnl_percentage
                })
                current_stop_loss = new_stop_loss
            else:
                # Fix the log message to show the correct logic
                if pnl_percentage < 1.0:
                    reason = f"PnL {pnl_percentage:.2f}% < 1.0%"
                    logger.info(f"[Trade {trade_id}] [ProfitProtection] ❌ NOT TRIGGERED: {reason}")
                elif profit_protection_status:
                    reason = f"profit protection already active: {profit_protection_status}"
                    logger.info(f"[Trade {trade_id}] [ProfitProtection] ✅ ACTIVE: {reason}")
                else:
                    reason = "unknown condition"
                    logger.info(f"[Trade {trade_id}] [ProfitProtection] ❌ NOT TRIGGERED: {reason}")

            # Trailing stop: Move stop loss up as price increases (when PnL >= configured trigger)
            logger.info(f"[Trade {trade_id}] [TrailingStop] Checking conditions - PnL: {pnl_percentage:.2f}%, Current Stop: {current_stop_loss:.2f}%, Target: {trailing_trigger_pct:.2f}%")
            if pnl_percentage >= trailing_trigger_pct:
                trailing_stop = pnl_percentage - trailing_step_pct  # Keep configured step percentage profit locked in
                logger.info(f"[Trade {trade_id}] [TrailingStop] Calculating trailing stop: {pnl_percentage:.2f}% - {trailing_step_pct:.2f}% = {trailing_stop:.2f}%")
                logger.info(f"[Trade {trade_id}] [TrailingStop] Comparing: new stop {trailing_stop:.2f}% > current stop {current_stop_loss:.2f}%")
                if trailing_stop > current_stop_loss:
                    # Calculate the actual trailing stop trigger price (BELOW current price for LONG positions)
                    # For a LONG position: trigger = highest_price * (1 - trailing_step_percentage)
                    trailing_step_decimal = trailing_step_pct / 100  # Convert percentage to decimal
                    trigger_price = highest_price * (1 - trailing_step_decimal)
                    
                    logger.info(f"[Trade {trade_id}] [TrailingStop] ✅ UPDATED: PnL {pnl_percentage:.2f}% >= {trailing_trigger_pct:.2f}% AND new stop {trailing_stop:.2f}% > current {current_stop_loss:.2f}%")
                    logger.info(f"[Trade {trade_id}] [TrailingStop] Moving stop loss: {current_stop_loss:.2f}% → {trailing_stop:.2f}%")
                    logger.info(f"[Trade {trade_id}] [TrailingStop] Trigger price: ${trigger_price:.6f} (${highest_price:.6f} - {trailing_step_pct:.2f}%)")
                    await self._update_trade_data(trade_id, {
                        'trail_stop_trigger': trigger_price,  # Store actual trigger price BELOW highest price
                        'trail_stop': 'active',
                        'profit_protection': 'trailing'
                    })
                    current_stop_loss = trailing_stop
                else:
                    logger.info(f"[Trade {trade_id}] [TrailingStop] ❌ NOT UPDATED: new stop {trailing_stop:.2f}% <= current stop {current_stop_loss:.2f}% (already higher)")
            else:
                logger.info(f"[Trade {trade_id}] [TrailingStop] ❌ NOT TRIGGERED: PnL {pnl_percentage:.2f}% < {trailing_trigger_pct:.2f}% (need {trailing_trigger_pct:.2f}%+ for trailing)")

            # Trailing stop trigger price check (if active)
            trail_stop_status = trade.get('trail_stop', 'inactive')
            if trail_stop_status == 'active':
                try:
                    trigger_price = float(trade.get('trail_stop_trigger', 0))
                    logger.info(f"[Trade {trade_id}] [TrailingStop] Checking price trigger - Current: ${current_price:.6f}, Trigger: ${trigger_price:.6f}")
                    if current_price <= trigger_price:
                        should_exit = True
                        exit_reason = f"trailing_stop_trigger_${trigger_price:.4f}"
                        logger.info(f"[Trade {trade_id}] [TrailingStop] ✅ EXIT TRIGGERED: Price ${current_price:.6f} <= trigger ${trigger_price:.6f}")
                    else:
                        logger.info(f"[Trade {trade_id}] [TrailingStop] ❌ NO EXIT: Price ${current_price:.6f} > trigger ${trigger_price:.6f}")
                except (ValueError, TypeError):
                    logger.warning(f"[Trade {trade_id}] [TrailingStop] Invalid trigger price: {trade.get('trail_stop_trigger')}")

            # Stop loss check (percentage-based)
            logger.info(f"[Trade {trade_id}] [StopLoss] Checking exit - PnL: {pnl_percentage:.2f}%, Stop Level: {current_stop_loss:.2f}%")
            if not should_exit and pnl_percentage <= current_stop_loss:
                should_exit = True
                exit_reason = f"stop_loss_{current_stop_loss:.1f}%"
                logger.info(f"[Trade {trade_id}] [StopLoss] ✅ EXIT TRIGGERED: PnL {pnl_percentage:.2f}% <= stop loss {current_stop_loss:.2f}%")
            elif not should_exit:
                logger.info(f"[Trade {trade_id}] [StopLoss] ❌ NO EXIT: PnL {pnl_percentage:.2f}% > stop loss {current_stop_loss:.2f}%")

            # Take profit check (5% take profit)
            logger.info(f"[Trade {trade_id}] [TakeProfit] Checking exit - PnL: {pnl_percentage:.2f}%, Target: 5.0%")
            if pnl_percentage >= 5.0:
                should_exit = True
                exit_reason = "take_profit_5%"
                logger.info(f"[Trade {trade_id}] [TakeProfit] ✅ EXIT TRIGGERED: PnL {pnl_percentage:.2f}% >= 5.0%")
            else:
                logger.info(f"[Trade {trade_id}] [TakeProfit] ❌ NO EXIT: PnL {pnl_percentage:.2f}% < 5.0%")

            # Log current status for monitoring
            if pnl_percentage > 0:
                logger.info(f"[Trade {trade_id}] [Status] Current: PnL {pnl_percentage:.2f}%, Stop Loss: {current_stop_loss:.2f}%, Highest: {highest_price:.6f}, Current: {current_price:.6f}")

            if should_exit:
                # Check if this trade is already being processed for exit
                if trade_id in self.exiting_trades:
                    logger.warning(f"[Trade {trade_id}] [Exit] ⚠️ SKIPPING: Trade already being processed for exit")
                    return
                
                logger.info(f"[Trade {trade_id}] [Exit] 🚨 EXECUTING EXIT: {exit_reason}")
                self.exiting_trades.add(trade_id)  # Mark as being processed
                try:
                    await self._execute_trade_exit(trade_id, exit_reason or "unknown")
                finally:
                    self.exiting_trades.discard(trade_id)  # Remove from processing set
            else:
                logger.info(f"[Trade {trade_id}] [Exit] ❌ NO EXIT: Continuing to monitor")

        except Exception as e:
            logger.error(f"[Trade {trade.get('trade_id', 'unknown')}] Error in _check_trade_exit: {str(e)}")
            logger.error(f"[Trade {trade.get('trade_id', 'unknown')}] Exception type: {type(e).__name__}")
            import traceback
            logger.error(f"[Trade {trade.get('trade_id', 'unknown')}] Full traceback: {traceback.format_exc()}")
            
            
    async def _run_entry_cycle(self) -> None:
        """Run entry cycle to check for new trade opportunities"""
        cycle_start_time = time.time()
        try:
            logger.info("Running entry cycle...")
            # Check available balance
            if not await self._check_available_balance():
                logger.info("Insufficient balance for new trades")
                cycle_duration.labels(cycle_type='entry').observe(time.time() - cycle_start_time)
                return
            # Get max_trades_per_exchange from trading configuration
            max_trades_per_exchange = 20  # Default value you specified
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    response = await client.get(f"{config_service_url}/api/v1/config/trading")
                    response.raise_for_status()
                    config = response.json()
                    max_trades_per_exchange = config.get('max_trades_per_exchange', 20)
                    logger.info(f"[EntryCycle] Max trades per exchange from config: {max_trades_per_exchange}")
            except Exception as e:
                logger.warning(f"[EntryCycle] Could not fetch trading config, using default max_trades_per_exchange: {max_trades_per_exchange}")
                logger.warning(f"[EntryCycle] Config fetch error: {e}")
            # Get all open trades once
            try:
                async with httpx.AsyncClient(timeout=60.0) as db_client:
                    response = await db_client.get(f"{database_service_url}/api/v1/trades/open")
                    response.raise_for_status()
                    open_trades = response.json()['trades']
            except Exception as e:
                logger.warning(f"[EntryCycle] Could not fetch open trades: {e}")
                open_trades = []
            # Get minimum balance threshold from config
            min_balance_threshold = 10.0  # Default fallback
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    response = await client.get(f"{config_service_url}/api/v1/config/trading")
                    response.raise_for_status()
                    trading_config = response.json()
                    min_balance_threshold = trading_config.get('min_exchange_balance', 50.0)
            except Exception as e:
                logger.warning(f"[EntryCycle] Could not fetch trading config for min_exchange_balance: {e}")
            
            # Check each exchange and pair
            for exchange_name, pairs in self.pair_selections.items():
                # Check exchange-specific balance first
                exchange_balance = self.balances.get(exchange_name, {}).get('available', 0)
                
                if exchange_balance < min_balance_threshold:
                    logger.info(f"[EntryCycle] Skipping {exchange_name} - insufficient balance: ${exchange_balance:.2f} < ${min_balance_threshold}")
                    continue
                    
                logger.info(f"[EntryCycle] {exchange_name} has sufficient balance: ${exchange_balance:.2f} >= ${min_balance_threshold}")
                
                open_trades_count = sum(1 for t in open_trades if t['exchange'] == exchange_name)
                logger.info(f"[EntryCycle] {exchange_name}: open_trades={open_trades_count}, max_trades_per_exchange={max_trades_per_exchange}")
                if open_trades_count >= max_trades_per_exchange:
                    logger.info(f"[EntryCycle] Trade limit reached for {exchange_name}: {open_trades_count}/{max_trades_per_exchange}. Skipping entry for this exchange.")
                    continue
                else:
                    logger.info(f"[EntryCycle] Trade limit NOT reached for {exchange_name}: {open_trades_count}/{max_trades_per_exchange}. Processing entry cycle for this exchange.")
                for pair in pairs:
                    await self._check_pair_entry(exchange_name, pair)
            
            # Record successful cycle completion
            cycle_duration.labels(cycle_type='entry').observe(time.time() - cycle_start_time)
            
        except Exception as e:
            logger.error(f"Error in entry cycle: {str(e)}")
            logger.error(f"Exception type: {type(e).__name__}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            # Record cycle duration even on error
            cycle_duration.labels(cycle_type='entry').observe(time.time() - cycle_start_time)
    
    async def _update_trade_status(self, trade_id: str, status: str, reason: str = None) -> bool:
        """Update trade status with comprehensive lifecycle tracking"""
        try:
            
            # Prepare update data with timestamp
            current_time = datetime.utcnow().isoformat()
            update_data = {
                'status': status,
                'updated_at': current_time
            }
            
            # Add status-specific fields and logging
            if reason:
                if status == 'FAILED':
                    update_data['exit_reason'] = reason
                else:
                    update_data['status_reason'] = reason
            
            # Add lifecycle timestamps and detailed logging based on status
            if status == 'PENDING':
                update_data['pending_time'] = current_time
                logger.info(f"🟡 ORDER LIFECYCLE: {trade_id} → PENDING (Order created, awaiting placement)")
            elif status == 'OPEN':
                update_data['open_time'] = current_time
                logger.info(f"🟢 ORDER LIFECYCLE: {trade_id} → OPEN (Order filled, position active)")
            elif status == 'CLOSED':
                update_data['close_time'] = current_time
                logger.info(f"🔵 ORDER LIFECYCLE: {trade_id} → CLOSED (Position closed successfully)")
            elif status == 'FAILED':
                update_data['failed_time'] = current_time
                logger.error(f"🔴 ORDER LIFECYCLE: {trade_id} → FAILED (Error: {reason or 'Unknown'})")
            elif status == 'CANCELLED':
                update_data['cancelled_time'] = current_time
                logger.warning(f"🟠 ORDER LIFECYCLE: {trade_id} → CANCELLED (Reason: {reason or 'Manual cancellation'})")
            
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.put(f"{database_service_url}/api/v1/trades/{trade_id}", json=update_data)
                if response.status_code == 200:
                    logger.info(f"✅ STATE TRACKING: Trade {trade_id} status updated to {status}")
                    
                    # Log transition details for audit trail
                    if reason:
                        logger.info(f"📝 STATE REASON: {trade_id} - {reason}")
                    return True
                else:
                    logger.error(f"❌ STATE TRACKING ERROR: Failed to update {trade_id} status: {response.status_code}")
                    return False
                    
        except Exception as e:
            logger.error(f"❌ CRITICAL STATE TRACKING ERROR: Failed to update {trade_id} status to {status}: {str(e)}")
            logger.error(f"Exception type: {type(e).__name__}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            return False
    
    async def _update_trade_with_order_details(self, trade_id: str, order_id: str, status: str) -> bool:
        """Update trade with order ID and status with enhanced tracking"""
        try:
            current_time = datetime.utcnow().isoformat()
            
            async with httpx.AsyncClient(timeout=60.0) as client:
                update_data = {
                    'entry_id': order_id,
                    'status': status,
                    'updated_at': current_time
                }
                
                # Add order placement tracking
                if status == 'PENDING':
                    update_data['order_placed_time'] = current_time
                    logger.info(f"📋 ORDER TRACKING: {trade_id} → Order placed on exchange (ID: {order_id})")
                
                response = await client.put(f"{database_service_url}/api/v1/trades/{trade_id}", json=update_data)
                if response.status_code == 200:
                    logger.info(f"✅ ORDER DETAILS UPDATED: Trade {trade_id} linked to order {order_id}")
                    return True
                else:
                    logger.error(f"❌ ORDER DETAILS ERROR: Failed to update trade {trade_id} with order details: {response.status_code}")
                    return False
        except Exception as e:
            logger.error(f"❌ CRITICAL ORDER DETAILS ERROR: Failed to update trade {trade_id} with order details: {str(e)}")
            logger.error(f"Exception type: {type(e).__name__}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            return False

    async def _close_dust_position(self, trade_id: str, exchange_name: str, pair: str, amount: float, reason: str) -> bool:
        """Close a dust position in database when it can't be sold due to minimum amount restrictions"""
        try:
            logger.warning(f"💸 Closing dust position {trade_id}: {amount:.8f} {pair} on {exchange_name}")
            
            current_time = datetime.utcnow().isoformat()
            
            # Get current market price for dust value estimation
            current_price = await self._get_current_price(exchange_name, pair)
            estimated_value = amount * current_price if current_price > 0 else 0
            
            async with httpx.AsyncClient(timeout=60.0) as client:
                update_data = {
                    'status': 'CLOSED',
                    'exit_reason': reason,
                    'close_time': current_time,
                    'exit_price': current_price,
                    'exit_amount': amount,
                    'dust_closure': True,
                    'estimated_dust_value': estimated_value,
                    'updated_at': current_time
                }
                
                response = await client.put(f"{database_service_url}/api/v1/trades/{trade_id}", json=update_data)
                if response.status_code == 200:
                    logger.info(f"✅ DUST POSITION CLOSED: Trade {trade_id} marked as closed (estimated value: ${estimated_value:.6f})")
                    return True
                else:
                    logger.error(f"❌ Failed to close dust position {trade_id}: {response.status_code}")
                    return False
        except Exception as e:
            logger.error(f"❌ Error closing dust position {trade_id}: {str(e)}")
            return False
            
    async def _check_available_balance(self) -> bool:
        """Check if there's sufficient balance for new trades"""
        try:
            total_available = 0
            logger.info(f"Checking available balance. Current balances: {self.balances}")
            
            for exchange_name, balance in self.balances.items():
                available = balance.get('available', 0) or 0  # Handle None values
                total_available += available
                logger.info(f"Exchange {exchange_name}: available={available}")
                
            logger.info(f"Total available balance: {total_available}")
            
            # Check if we have at least $10 available across all exchanges
            has_sufficient = total_available >= 10.0
            logger.info(f"Sufficient balance for new trades: {has_sufficient}")
            return has_sufficient
            
        except Exception as e:
            logger.error(f"Error checking available balance: {str(e)}")
            logger.error(f"Exception type: {type(e).__name__}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            return False
            
    async def _check_trade_limits(self) -> bool:
        """Check if trade limits allow new trades (now only checks global limit, not per-exchange)"""
        try:
            # Get trading configuration
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.get(f"{config_service_url}/api/v1/config/trading")
                response.raise_for_status()
                trading_config = response.json()
            max_concurrent_trades = trading_config.get('max_concurrent_trades', 10)
            # Get current open trades
            async with httpx.AsyncClient(timeout=60.0) as db_client:
                response = await db_client.get(f"{database_service_url}/api/v1/trades/open")
                response.raise_for_status()
                open_trades = response.json()['trades']
            total_open_trades = len(open_trades)
            logger.info(f"Current open trades: {total_open_trades}, max allowed: {max_concurrent_trades}")
            if total_open_trades >= max_concurrent_trades:
                logger.info(f"Maximum concurrent trades reached: {total_open_trades}/{max_concurrent_trades}")
                return False
            logger.info(f"Trade limits check passed: {total_open_trades} open trades")
            return True
        except Exception as e:
            logger.error(f"Error checking trade limits: {str(e)}")
            logger.error(f"Exception type: {type(e).__name__}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            return False
            
    async def _check_pair_entry(self, exchange_name: str, pair: str) -> None:
        """Check if a pair should be entered - each strategy is independent"""
        try:
            # RISK MANAGEMENT: Check for existing negative positions on this pair
            if not await self._check_pair_risk_management(exchange_name, pair):
                logger.warning(f"🚫 Risk Management Block: Skipping {pair} on {exchange_name} - has 2+ losing positions")
                return
            
            # Handle different symbol formats for different exchanges
            # All exchanges use format without slashes for strategy service
            strategy_pair = pair.replace('/', '')
            
            # Get individual strategy signals instead of consensus
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.get(f"{strategy_service_url}/api/v1/signals/{exchange_name}/{strategy_pair}")
                
                # Handle 404 for unsupported pairs gracefully
                if response.status_code == 404:
                    logger.debug(f"No signals available for {pair} on {exchange_name} - pair not supported by strategy service")
                    return
                
                response.raise_for_status()
                signals_data = response.json()
            
            # Check each strategy independently
            strategies = signals_data.get('strategies', {})
            for strategy_name, signal_data in strategies.items():
                signal = signal_data.get('signal')
                confidence = signal_data.get('confidence', 0)
                strength = signal_data.get('strength', 0)
                
                # If any strategy generates a buy signal, execute the trade
                if signal == 'buy' and confidence > 0.5:  # Minimum confidence threshold
                    logger.info(f"Strategy {strategy_name} generated BUY signal for {pair} on {exchange_name} (confidence: {confidence:.2f})")
                    
                    # Apply momentum filter to prevent buying peaks or falling knives
                    allow_entry, filter_reason = await self.momentum_filter.should_allow_entry(exchange_name, pair)
                    
                    if allow_entry:
                        logger.info(f"🟢 Momentum filter APPROVED entry for {pair} on {exchange_name}: {filter_reason}")
                        await self._execute_trade_entry(exchange_name, pair, {
                            'strategy': strategy_name,
                            'signal': signal,
                            'confidence': confidence,
                            'strength': strength
                        })
                    else:
                        logger.warning(f"🔴 Momentum filter BLOCKED entry for {pair} on {exchange_name}: {filter_reason}")
                    # Only execute one trade per pair per cycle to avoid over-trading
                    break
                
        except Exception as e:
            logger.error(f"Error checking pair entry for {pair} on {exchange_name}: {str(e)}")
            logger.error(f"Exception type: {type(e).__name__}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")

    async def _check_pair_risk_management(self, exchange_name: str, pair: str) -> bool:
        """
        Risk Management: Check if it's safe to open a new trade for this pair
        Returns False if the pair has 2+ open positions with negative unrealized_pnl
        """
        try:
            # Get all open trades for this exchange and pair
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.get(f"{database_service_url}/api/v1/trades/open")
                response.raise_for_status()
                open_trades = response.json()['trades']
            
            # Filter trades for this specific exchange and pair
            pair_trades = [
                trade for trade in open_trades 
                if trade.get('exchange') == exchange_name and trade.get('pair') == pair
            ]
            
            if len(pair_trades) < 2:
                # Less than 2 positions, safe to trade
                logger.info(f"✅ Risk Check Passed: {pair} on {exchange_name} has {len(pair_trades)} open positions")
                return True
            
            # Count positions with negative unrealized_pnl
            negative_positions = []
            for trade in pair_trades:
                try:
                    unrealized_pnl = float(trade.get('unrealized_pnl', 0))
                    if unrealized_pnl < 0:
                        negative_positions.append({
                            'trade_id': trade.get('trade_id'),
                            'unrealized_pnl': unrealized_pnl,
                            'entry_price': trade.get('entry_price'),
                            'position_size': trade.get('position_size')
                        })
                except (ValueError, TypeError):
                    # If we can't parse PnL, assume it's negative for safety
                    negative_positions.append({
                        'trade_id': trade.get('trade_id'),
                        'unrealized_pnl': -1,  # Assume negative
                        'entry_price': trade.get('entry_price'),
                        'position_size': trade.get('position_size')
                    })
            
            # Risk management rule: Block if 2+ positions are negative
            if len(negative_positions) >= 2:
                total_negative_pnl = sum(pos['unrealized_pnl'] for pos in negative_positions)
                logger.warning(f"🚫 Risk Management Block: {pair} on {exchange_name}")
                logger.warning(f"   - Total positions: {len(pair_trades)}")
                logger.warning(f"   - Negative positions: {len(negative_positions)}")
                logger.warning(f"   - Combined negative PnL: ${total_negative_pnl:.2f}")
                logger.warning(f"   - Negative trades: {[pos['trade_id'][:8] for pos in negative_positions]}")
                return False
            
            logger.info(f"✅ Risk Check Passed: {pair} on {exchange_name} - {len(negative_positions)}/2+ negative positions")
            return True
            
        except Exception as e:
            logger.error(f"Error in risk management check for {pair} on {exchange_name}: {str(e)}")
            logger.error(f"Exception type: {type(e).__name__}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            # On error, be conservative and block the trade
            return False
            
    async def _execute_trade_entry(self, exchange_name: str, pair: str, signal: Dict[str, Any]) -> None:
        """Execute trade entry with actual order placement (market orders only)"""
        try:
            # CRITICAL: Check real-time balance before placing order
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    balance_response = await client.get(f"{exchange_service_url}/api/v1/account/balance/{exchange_name}")
                    balance_response.raise_for_status()
                    current_balance = balance_response.json()
                    base_currency = 'USDC' if exchange_name in ['binance', 'bybit'] else 'USD'
                    available_balance = float(current_balance.get('free', {}).get(base_currency, 0) or 0)
                    logger.info(f"💰 Current available balance on {exchange_name}: {available_balance:.2f} {base_currency}")
                    if available_balance < 50:
                        logger.warning(f"🚫 Insufficient balance on {exchange_name}: {available_balance:.2f} {base_currency} < 50 minimum")
                        return
            except Exception as e:
                logger.error(f"❌ Failed to check balance for {exchange_name}: {e}")
                return
            position_value_usdc = available_balance * 0.1
            
            # Get exchange-specific minimum order size from configuration
            min_order_size_usd = 50.0  # Default fallback
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    response = await client.get(f"{config_service_url}/api/v1/config/trading")
                    response.raise_for_status()
                    trading_config = response.json()
                    min_order_config = trading_config.get('min_order_size_usd', {})
                    
                    # Handle both old format (single value) and new format (per-exchange)
                    if isinstance(min_order_config, dict):
                        min_order_size_usd = min_order_config.get(exchange_name, min_order_config.get('default', 50.0))
                    else:
                        min_order_size_usd = min_order_config  # Legacy single value
                        
                    logger.info(f"[TradeEntry] Using minimum order size for {exchange_name}: ${min_order_size_usd}")
            except Exception as e:
                logger.warning(f"[TradeEntry] Could not fetch min_order_size_usd from config, using default: ${min_order_size_usd}")
            
            if position_value_usdc < min_order_size_usd:
                if available_balance >= min_order_size_usd:
                    logger.info(f"💡 Calculated position size (${position_value_usdc:.2f}) is below minimum (${min_order_size_usd}). Using minimum order size for {pair} on {exchange_name}.")
                    position_value_usdc = min_order_size_usd
                else:
                    logger.warning(f"🚫 Available balance (${available_balance:.2f}) is insufficient for minimum order size (${min_order_size_usd}). Skipping order for {pair} on {exchange_name}.")
                    return
            entry_price = await self._get_current_price(exchange_name, pair)
            if entry_price <= 0:
                logger.error(f"Invalid entry price {entry_price} for {pair} on {exchange_name}")
                return
            position_size_units = position_value_usdc / entry_price
            min_order_size = await self._get_minimum_order_size(exchange_name, pair)
            if position_size_units < min_order_size:
                logger.warning(f"Position size {position_size_units} is below minimum {min_order_size} for {pair} on {exchange_name}")
                return
            # Sanitize position size to prevent decimal conversion errors
            sanitized_position_size = self._sanitize_numeric_value(position_size_units)
            
            logger.info(f"🔄 Placing SPOT BUY order: {sanitized_position_size:.8f} {pair} on {exchange_name}")
            # Place SPOT BUY order using smart order placement (limit with fallback to market)
            order_result = await self._place_smart_order(exchange_name, pair, 'buy', sanitized_position_size)
            if not order_result or not order_result.get('id'):
                logger.error(f"❌ Failed to place BUY order for {pair} on {exchange_name} (no order ID returned)")
                return
            # Immediately create trade record as OPEN
            trade_id = str(uuid.uuid4())
            strategy_name = signal.get('strategy', 'unknown')
            # Safely extract filled amount and price with defensive handling
            filled_amount = float(order_result.get('filled') or position_size_units)
            filled_price = float(order_result.get('average') or entry_price)
            trade_data = {
                'trade_id': trade_id,
                'pair': pair,
                'exchange': exchange_name,
                'status': 'OPEN',
                'position_size': filled_amount,
                'entry_price': filled_price,
                'entry_time': datetime.utcnow().isoformat(),
                'entry_id': order_result['id'],
                'fees': self._extract_fee_safely(order_result),
                'strategy': strategy_name,
                'entry_reason': f"{strategy_name} strategy signal: {signal.get('signal', 'buy')} (confidence: {signal.get('confidence', 0):.2f}, strength: {signal.get('strength', 0):.2f})",
                'created_at': datetime.utcnow().isoformat(),
                'updated_at': datetime.utcnow().isoformat()
            }
            async with httpx.AsyncClient(timeout=60.0) as client:
                create_response = await client.post(f"{database_service_url}/api/v1/trades", json=trade_data)
                if create_response.status_code in [200, 201]:
                    logger.info(f"✅ Trade {trade_id} recorded as OPEN after successful market order placement")
                else:
                    logger.error(f"❌ Failed to record OPEN trade: {create_response.status_code} - {create_response.text}")
        except Exception as e:
            logger.error(f"❌ Exception in _execute_trade_entry: {str(e)}")
            logger.error(f"Exception type: {type(e).__name__}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")

    def _extract_fee_safely(self, order_data: Dict[str, Any]) -> float:
        """Safely extract fee from order data with enhanced fee tracking"""
        try:
            if not order_data:
                return 0.0
            
            # Enhanced fee extraction for different exchange formats
            fee_data = order_data.get('fee')
            if not fee_data:
                # Try alternative fee fields
                fee_data = order_data.get('fees') or order_data.get('commission') or order_data.get('cost')
            
            if not fee_data:
                return 0.0
            
            # Handle different fee structures
            if isinstance(fee_data, dict):
                # CCXT format: {'cost': 0.1, 'currency': 'USD'}
                cost = fee_data.get('cost') or fee_data.get('amount') or fee_data.get('value')
                if cost is not None:
                    fee_amount = float(cost)
                    logger.info(f"💰 FEE EXTRACTED: {fee_amount} {fee_data.get('currency', 'USD')}")
                    return fee_amount
            elif isinstance(fee_data, (int, float)):
                fee_amount = float(fee_data)
                logger.info(f"💰 FEE EXTRACTED: {fee_amount}")
                return fee_amount
            elif isinstance(fee_data, list):
                # Handle multiple fees (entry + exit)
                total_fee = 0.0
                for fee in fee_data:
                    if isinstance(fee, dict):
                        cost = fee.get('cost') or fee.get('amount')
                        if cost is not None:
                            total_fee += float(cost)
                    elif isinstance(fee, (int, float)):
                        total_fee += float(fee)
                logger.info(f"💰 TOTAL FEES EXTRACTED: {total_fee}")
                return total_fee
            
            return 0.0
            
        except (ValueError, TypeError) as e:
            logger.warning(f"Failed to extract fee from order data: {str(e)}")
            logger.warning(f"Exception type: {type(e).__name__}")
            logger.warning(f"Order data structure: {order_data}")
            return 0.0

    async def _record_trade_fees(self, trade_id: str, order_data: Dict[str, Any]) -> bool:
        """Record actual fees from order data to database"""
        try:
            fees = self._extract_fee_safely(order_data)
            if fees > 0:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    update_data = {
                        'fees': fees,
                        'updated_at': datetime.utcnow().isoformat()
                    }
                    response = await client.put(f"{database_service_url}/api/v1/trades/{trade_id}", json=update_data)
                    if response.status_code == 200:
                        logger.info(f"✅ FEES RECORDED: Trade {trade_id} - ${fees:.6f}")
                        return True
                    else:
                        logger.error(f"❌ FAILED TO RECORD FEES: {response.status_code}")
                        return False
            return True
        except Exception as e:
            logger.error(f"❌ ERROR RECORDING FEES: {str(e)}")
            return False

    async def _place_market_order(self, exchange_name: str, pair: str, side: str, amount: float, trade_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Place a market order on the exchange"""
        try:
            # Ensure we reference the module-level URL
            global database_service_url
            # Convert pair format for exchange service (add slash if needed)
            exchange_symbol = self._convert_pair_format(exchange_name, pair)
            
            # Sanitize numeric values to prevent decimal conversion errors
            sanitized_amount = self._sanitize_numeric_value(amount)
            
            # Generate client order ID for idempotency (Phase 0)
            client_order_id = self._generate_client_order_id(trade_id, "market")
            local_order_id = str(uuid.uuid4())  # Phase 2: Generate local order ID
            
            logger.info(f"🆔 Generated client_order_id: {client_order_id} for {side} {amount} {pair} on {exchange_name}")
            logger.info(f"🆔 Generated local_order_id: {local_order_id}")
            
            # Phase 2: Create order mapping with idempotency check
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    mapping_data = {
                        'local_order_id': local_order_id,
                        'client_order_id': client_order_id,
                        'exchange': exchange_name,
                        'symbol': pair,
                        'side': side,
                        'order_type': 'market',
                        'amount': sanitized_amount,
                        'trade_id': trade_id
                    }
                    
                    mapping_response = await client.post(f"{database_service_url}/api/v1/order-mappings", json=mapping_data)
                    mapping_response.raise_for_status()
                    mapping_result = mapping_response.json()
                    
                    if mapping_result['status'] == 'already_exists':
                        logger.warning(f"🔄 Idempotency violation: Order with client_order_id {client_order_id} already exists")
                        return None  # Prevent duplicate order
                    
                    logger.info(f"✅ Order mapping created: {local_order_id} -> {client_order_id}")
            except Exception as mapping_error:
                logger.error(f"Failed to create order mapping: {mapping_error}")
                return None
            
            # Phase 2: Emit OrderCreated event
            await self._emit_order_created_event(
                local_order_id, client_order_id, trade_id, exchange_name, 
                pair, side, "market", sanitized_amount
            )
            
            # Prepare order data with sanitized values
            order_data = {
                'exchange': exchange_name,
                'symbol': exchange_symbol,
                'order_type': 'market',
                'side': side,
                'amount': sanitized_amount,
                'client_order_id': client_order_id  # Phase 0: Enable idempotency
            }
            
            # CRITICAL: Verify real-time balance before placing order
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    balance_response = await client.get(f"{exchange_service_url}/api/v1/account/balance/{exchange_name}")
                    balance_response.raise_for_status()
                    current_balance = balance_response.json()
                    
                    # Different balance checks for BUY vs SELL orders
                    if side.lower() == 'buy':
                        # For BUY orders: Check base currency (USD/USDC) balance
                        base_currency = 'USD' if exchange_name == 'cryptocom' else 'USDC'
                        real_available = current_balance.get('free', {}).get(base_currency, 0)
                        
                        logger.info(f"🔍 Real-time balance check for {exchange_name}: ${real_available:.2f} {base_currency} available")
                        
                        # Get current market price for order value estimation
                        current_price = await self._get_current_price(exchange_name, pair)
                        if current_price > 0:
                            estimated_cost = amount * current_price * 1.01  # Include 1% buffer for fees
                            if real_available < estimated_cost:
                                logger.error(f"🚫 CRITICAL: Real-time balance insufficient! Available: ${real_available:.2f}, Required: ${estimated_cost:.2f}")
                                return None
                        else:
                            logger.warning(f"⚠️ Could not get current price for {pair} on {exchange_name}, skipping balance verification")
                    
                    elif side.lower() == 'sell':
                        # For SELL orders: Check quote currency (the asset being sold) balance
                        if '/' in pair:
                            asset = pair.split('/')[0]
                        else:
                            asset = pair.replace('USD', '').replace('USDC', '')  # fallback
                        quote_available = current_balance.get('free', {}).get(asset, 0)
                        logger.info(f"🔍 Real-time balance check for {exchange_name}: {quote_available:.8f} {asset} available (pair: {pair})")
                        
                        if quote_available < amount:
                            # CRITICAL: Position size mismatch detected!
                            discrepancy = amount - quote_available
                            logger.warning(f"⚠️ POSITION SIZE MISMATCH: Database shows {amount:.8f}, but exchange has {quote_available:.8f} (diff: {discrepancy:.8f})")
                            
                            # If discrepancy is small (< 5% or < 10 units), auto-correct the position size
                            if quote_available > 0 and (discrepancy / amount < 0.05 or discrepancy < 10):
                                logger.info(f"🔧 AUTO-CORRECTING position size from {amount:.8f} to {quote_available:.8f}")
                                
                                # Check minimum amount requirements before proceeding
                                min_amounts = {
                                    'cryptocom': {
                                        'AAVE/USD': 0.001,
                                        'default': 0.000001
                                    },
                                    'binance': {
                                        'default': 0.000001  
                                    },
                                    'bybit': {
                                        'default': 0.000001
                                    }
                                }
                                
                                exchange_min_amounts = min_amounts.get(order_data.get('exchange', '').lower(), {})
                                min_amount = exchange_min_amounts.get(order_data.get('symbol', ''), exchange_min_amounts.get('default', 0.000001))
                                
                                if quote_available < min_amount:
                                    logger.error(f"❌ Corrected position size {quote_available:.8f} below minimum {min_amount} for {order_data.get('symbol')} on {order_data.get('exchange')}, cancelling order")
                                    return None
                                
                                # Update the database with the correct position size
                                try:
                                    # Update order amount to match available balance
                                    order_data['amount'] = quote_available
                                    amount = quote_available  # Update local variable
                                    
                                    # Update database trade record if trade_id is provided
                                    if trade_id:
                                        async with httpx.AsyncClient(timeout=60.0) as db_client:
                                            update_data = {'position_size': quote_available}
                                            db_response = await db_client.put(
                                                f"{database_service_url}/api/v1/trades/{trade_id}",
                                                json=update_data
                                            )
                                            if db_response.status_code == 200:
                                                logger.info(f"🔄 Database updated: position_size corrected to {quote_available:.8f} for trade {trade_id}")
                                            else:
                                                logger.warning(f"⚠️ Failed to update database position_size: {db_response.status_code}")
                                    
                                    logger.info(f"✅ Position size corrected: proceeding with {quote_available:.8f} {asset}")
                                    
                                except Exception as correction_error:
                                    logger.error(f"❌ Failed to auto-correct position size: {correction_error}")
                                    return None
                            else:
                                logger.error(f"🚫 CRITICAL: Position size discrepancy too large! Available: {quote_available:.8f}, Required: {amount:.8f}")
                                return None
                        else:
                            logger.info(f"✅ Sufficient {asset} balance for SELL order: {quote_available:.8f} >= {amount:.8f}")
                        
            except Exception as e:
                logger.warning(f"⚠️ Could not verify real-time balance for {exchange_name}: {e}")
            
            # Add request debugging
            logger.info(f"🔄 Placing {side} order request to exchange service:")
            logger.info(f"  URL: {exchange_service_url}/api/v1/trading/order")
            logger.info(f"  Data: {order_data}")
            
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(f"{exchange_service_url}/api/v1/trading/order", json=order_data)
                
                logger.info(f"📥 Exchange service response: Status {response.status_code}")
                
                # Track order metrics
                status = 'success' if response.status_code == 200 else 'failed'
                orders_total.labels(
                    exchange=exchange_name, 
                    pair=pair, 
                    side=side, 
                    type='market', 
                    status=status
                ).inc()
                
                if response.status_code != 200:
                    # Extract detailed error message from response body
                    try:
                        error_detail = response.json().get('detail', 'Unknown error')
                    except:
                        error_detail = response.text or f"HTTP {response.status_code}"
                    
                    # Special handling for dust amounts (422 status code with DUST_AMOUNT message)
                    if response.status_code == 422 and 'DUST_AMOUNT' in error_detail:
                        logger.warning(f"💸 Dust amount detected for {pair} on {exchange_name} (amount: {amount:.8f})")
                        logger.warning(f"💸 Dust details: {error_detail}")
                        # For dust amounts, we skip the order and mark the position for manual cleanup
                        if trade_id:
                            try:
                                # Close the trade in database as it can't be sold due to dust
                                await self._close_dust_position(trade_id, exchange_name, pair, amount, "dust_amount_below_minimum")
                                logger.info(f"✅ Dust position {trade_id} marked as closed in database")
                            except Exception as dust_error:
                                logger.error(f"❌ Failed to close dust position {trade_id}: {dust_error}")
                        return None
                    
                    logger.error(f"❌ Error placing {side} order for {pair} on {exchange_name}: {error_detail}")
                    logger.error(f"📥 Error response body: {response.text}")
                    return None
                
                result = response.json()
                
                if not result:
                    logger.error(f"❌ Empty response from exchange service for {side} order on {exchange_name}")
                    logger.error(f"Response status: {response.status_code}, Response body: {response.text}")
                    return None

                # Extract order from response - exchange service returns {"order": {...}, "exchange": "...", "symbol": "..."}
                order = result.get('order', {})
                if not order.get('id'):
                    logger.error(f"❌ Order response missing 'id' field for {pair} on {exchange_name}")
                    logger.error(f"Response content: {result}")
                    return None
                
                logger.info(f"✅ Order placed successfully: {order.get('id')} - {side} {amount:.8f} {pair} on {exchange_name}")
                
                # Phase 2: Emit ExchangeAck event
                await self._emit_exchange_ack_event(local_order_id, order.get('id'), order)
                
                # Phase 2: Update order mapping with exchange order ID
                try:
                    async with httpx.AsyncClient(timeout=30.0) as client:
                        update_data = {
                            'exchange_order_id': order.get('id'),
                            'status': 'ACKNOWLEDGED',
                            'acknowledged_at': datetime.utcnow().isoformat()
                        }
                        
                        mapping_response = await client.put(
                            f"{database_service_url}/api/v1/order-mappings/{client_order_id}", 
                            json=update_data
                        )
                        mapping_response.raise_for_status()
                        logger.info(f"✅ Order mapping updated with exchange order ID: {order.get('id')}")
                except Exception as update_error:
                    logger.error(f"Failed to update order mapping: {update_error}")
                
                # Record market order to database for dashboard tracking
                db_order_data = {
                    'order_id': order.get('id'),
                    'trade_id': trade_id,
                    'exchange': exchange_name,
                    'symbol': exchange_symbol,
                    'order_type': 'market',
                    'side': side,
                    'amount': sanitized_amount,
                    'price': None,  # Market orders don't have a set price
                    'status': 'filled' if (str(order.get('status', '')).lower() in ['closed', 'filled', 'complete'] or (order.get('filled') or 0) > 0) else 'pending',
                    'fees': order.get('fee', {}).get('cost', 0.0) if order.get('fee') else 0.0,
                    'filled_amount': order.get('filled') or 0.0,
                    'exchange_order_id': order.get('id'),
                    'client_order_id': order.get('clientOrderId', '')
                }
                await self._record_order_to_database(db_order_data)
                
                return order  # Return the order object, not the full response
                
        except Exception as e:
            logger.error(f"❌ Error placing {side} order for {pair} on {exchange_name}: {str(e)}")
            logger.error(f"Exception type: {type(e).__name__}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            return None

    async def _wait_for_order_fill(self, exchange_name: str, order_id: str, pair: str, timeout: int = 30, original_order: Optional[Dict[str, Any]] = None, amount: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """Wait for order to fill with timeout"""
        try:
            # First check if the original order already shows as filled (common for market orders)
            if original_order and str(original_order.get('status', '')).lower() in ['closed', 'filled']:
                logger.info(f"✅ Market order {order_id} filled instantly")
                return original_order
            
            start_time = time.time()
            check_count = 0
            
            while time.time() - start_time < timeout and check_count < 6:  # Max 6 checks
                try:
                    async with httpx.AsyncClient(timeout=60.0) as client:
                        # Get order status from exchange service
                        response = await client.get(f"{exchange_service_url}/api/v1/trading/orders/{exchange_name}")
                        
                        if response.status_code == 200:
                            orders = response.json().get('orders', [])
                            
                            # Find our order in open orders
                            order_found = False
                            for order in orders:
                                if order.get('id') == order_id:
                                    order_found = True
                                    status = str(order.get('status', '')).lower()
                                    if status in ['closed', 'filled']:
                                        logger.info(f"✅ Order {order_id} filled: {order.get('filled', 0):.8f} at {order.get('average', 0):.2f}")
                                        return order
                                    elif status in ['canceled', 'cancelled', 'rejected']:
                                        logger.error(f"❌ Order {order_id} was {status}")
                                        return None
                                    break
                            
                            # If order not found in open orders, it was likely filled instantly (common for market orders)
                            if not order_found:
                                logger.info(f"🔍 Order {order_id} not in open orders - likely filled instantly")
                                
                                # For market orders on exchanges like Crypto.com, they fill instantly
                                # The original order response should contain the fill information
                                if original_order:
                                    orig_status = str(original_order.get('status', '')).lower()
                                    if orig_status in ['closed', 'filled']:
                                        logger.info(f"✅ Order {order_id} filled instantly - status: {orig_status}")
                                        logger.info(f"✅ Fill details - filled: {original_order.get('filled', 'N/A')}, average: {original_order.get('average', 'N/A')}")
                                        return original_order
                                    else:
                                        logger.warning(f"⚠️ Original order status is {orig_status}, not filled")
                                
                                # For Crypto.com and other exchanges where market orders fill instantly,
                                # create a synthetic filled order response using reasonable defaults
                                if exchange_name == 'cryptocom' and check_count == 0:
                                    logger.info(f"✅ Assuming Crypto.com market order {order_id} filled instantly")
                                    
                                    # Get current market price for synthetic average
                                    try:
                                        async with httpx.AsyncClient(timeout=10.0) as price_client:
                                            exchange_pair = pair.replace('/', '') if pair else ''
                                            price_response = await price_client.get(f"{exchange_service_url}/api/v1/market/ticker/{exchange_name}/{exchange_pair}")
                                            if price_response.status_code == 200:
                                                ticker = price_response.json()
                                                current_market_price = float(ticker.get('last', 0.0))
                                                logger.info(f"[CRYPTOCOM SYNTHETIC] Using current market price: ${current_market_price:.6f}")
                                            else:
                                                current_market_price = 0.13  # Fallback for CRO/USD
                                                logger.warning(f"[CRYPTOCOM SYNTHETIC] Using fallback price: ${current_market_price:.6f}")
                                    except Exception as price_error:
                                        current_market_price = 0.13  # Fallback for CRO/USD  
                                        logger.warning(f"[CRYPTOCOM SYNTHETIC] Price fetch failed, using fallback: ${current_market_price:.6f} - {price_error}")
                                    
                                    return {
                                        'id': order_id,
                                        'status': 'closed',
                                        'filled': amount,
                                        'average': current_market_price,  # Use real market price instead of None
                                        'info': {'orderId': order_id, 'exchange': exchange_name}
                                    }
                        
                        check_count += 1
                        if check_count < 6:
                            await asyncio.sleep(0.5)  # Wait 500ms before next check
                            
                except Exception as e:
                    logger.warning(f"Error checking order status (attempt {check_count + 1}): {str(e)}")
                    logger.warning(f"Exception type: {type(e).__name__}")
                    check_count += 1
                    if check_count < 6:
                        await asyncio.sleep(1)
            
            logger.warning(f"⏱️ Could not verify order {order_id} fill status")
            return None
            
        except Exception as e:
            logger.error(f"❌ Error waiting for order fill {order_id}: {str(e)}")
            logger.error(f"Exception type: {type(e).__name__}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            return None

    async def _cancel_order(self, exchange_name: str, order_id: str, pair: str) -> bool:
        """Cancel an order"""
        try:
            exchange_symbol = self._convert_pair_format(exchange_name, pair)
            
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.delete(f"{exchange_service_url}/api/v1/trading/order/{exchange_name}/{order_id}?symbol={exchange_symbol}")
                response.raise_for_status()
                
                logger.info(f"✅ Order {order_id} cancelled successfully")
                return True
                
        except Exception as e:
            logger.error(f"❌ Error cancelling order {order_id}: {str(e)}")
            logger.error(f"Exception type: {type(e).__name__}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            return False

    async def _get_minimum_order_size(self, exchange_name: str, pair: str) -> float:
        """Get minimum order size for a trading pair"""
        try:
            # Default minimum sizes by exchange
            defaults = {
                'binance': 0.0001,  # 0.0001 BTC minimum
                'bybit': 0.0001,
                'cryptocom': 0.0001
            }
            
            # TODO: Could fetch actual minimum sizes from exchange info
            # For now, use conservative defaults
            return defaults.get(exchange_name, 0.0001)
            
        except Exception as e:
            logger.error(f"Error getting minimum order size for {pair} on {exchange_name}: {str(e)}")
            logger.error(f"Exception type: {type(e).__name__}")
            return 0.0001  # Conservative default

    def _convert_pair_format(self, exchange_name: str, pair: str) -> str:
        """Convert pair format for exchange service calls"""
        # Most exchanges expect pairs with slashes (BTC/USDC)
        if '/' not in pair:
            # Convert BTCUSDC to BTC/USDC format
            if exchange_name in ['binance', 'bybit']:
                if pair.endswith('USDC'):
                    return f"{pair[:-4]}/USDC"
                elif pair.endswith('USD'):
                    return f"{pair[:-3]}/USD"
            elif exchange_name == 'cryptocom':
                if pair.endswith('USD'):
                    base = pair[:-3]
                    return f"{base}/USD"
        
        return pair  # Return as-is if already formatted

    async def _execute_trade_exit(self, trade_id: str, exit_reason: str) -> None:
        """STEP 5: Execute trade exit with SPOT market SELL order"""
        try:
            
            # First try to get trade from active_trades (in-memory), then fallback to database
            trade = None
            if trade_id in self.active_trades_dict:
                trade = self.active_trades_dict[trade_id]
                logger.info(f"[Exit] Using trade from active_trades_dict: {trade_id}")
            else:
                # Get trade from database
                logger.info(f"[Exit] Trade not in active_trades_dict, fetching from database: {trade_id}")
                async with httpx.AsyncClient(timeout=60.0) as client:
                    response = await client.get(f"{database_service_url}/api/v1/trades/open")
                    response.raise_for_status()
                    open_trades = response.json()['trades']
                    
                    # Find the trade by trade_id
                    for db_trade in open_trades:
                        if db_trade['trade_id'] == trade_id:
                            trade = db_trade
                            logger.info(f"[Exit] Found trade in database: {trade_id}")
                            break
            
            if not trade:
                logger.warning(f"[Exit] Trade {trade_id} not found in active_trades_dict or database")
                return
            exchange_name = trade['exchange']
            pair = trade['pair']
            position_size = float(trade['position_size'])
            
            # Check minimum amount requirements for this exchange/pair
            min_amounts = {
                'cryptocom': {
                    'AAVE/USD': 0.001,
                    'default': 0.000001
                },
                'binance': {
                    'default': 0.000001  
                },
                'bybit': {
                    'default': 0.000001
                }
            }
            
            exchange_min_amounts = min_amounts.get(exchange_name.lower(), {})
            min_amount = exchange_min_amounts.get(pair, exchange_min_amounts.get('default', 0.000001))
            
            if position_size < min_amount:
                # Check if we can round up to minimum
                if min_amount - position_size <= min_amount * 0.01:  # Within 1% of minimum
                    logger.warning(f"⚠️ Position size {position_size:.8f} below minimum {min_amount}, rounding up to {min_amount}")
                    position_size = min_amount
                else:
                    logger.error(f"❌ Position size {position_size:.8f} too far below minimum {min_amount} for {pair} on {exchange_name}, skipping exit")
                    return
            
            # Sanitize position size to prevent decimal conversion errors
            sanitized_position_size = self._sanitize_numeric_value(position_size)
            
            logger.info(f"🔄 Placing SPOT SELL order to exit: {sanitized_position_size:.8f} {pair} on {exchange_name}")
            
            # Place SPOT SELL order using smart order placement (limit with fallback to market)
            exit_order_result = await self._place_smart_order(exchange_name, pair, 'sell', sanitized_position_size, trade_id)
            if not exit_order_result:
                logger.error(f"❌ Failed to place SELL order for {pair} on {exchange_name}")
                return
            
            # Wait for exit order to fill
            filled_exit_order = await self._wait_for_order_fill(exchange_name, exit_order_result['id'], pair, timeout=30, original_order=exit_order_result, amount=position_size)
            if not filled_exit_order:
                logger.error(f"❌ Exit order {exit_order_result['id']} failed to fill for {pair} on {exchange_name}")
                await self._cancel_order(exchange_name, exit_order_result['id'], pair)
                return
            
            # Calculate realized PnL with defensive None handling
            entry_price = float(trade['entry_price'])
            
            # Defensive handling for exit price - ensure we never get None
            raw_exit_price = filled_exit_order.get('average')
            if raw_exit_price is None:
                logger.warning(f"[Exit] No average price in filled_exit_order, using entry_price as fallback")
                exit_price = entry_price
            else:
                exit_price = float(raw_exit_price)
                
            filled_amount = float(filled_exit_order.get('filled', position_size))
            exit_fees = self._extract_fee_safely(filled_exit_order)
            
            realized_pnl = (exit_price - entry_price) * filled_amount - exit_fees - float(trade.get('fees', 0))
            
            # Update trade in database with exit details
            exit_data = {
                'status': 'CLOSED',
                'exit_price': exit_price,
                'exit_id': filled_exit_order.get('id'),  # Store real exit order ID
                'exit_reason': exit_reason,
                'exit_time': datetime.utcnow().isoformat(),
                'realized_pnl': realized_pnl,
                'fees': float(trade.get('fees', 0)) + exit_fees  # Total fees
            }
            
            await self._update_trade_data(trade_id, exit_data)
            
            # Track completed trade metrics
            trades_total.labels(
                exchange=trade['exchange'], 
                pair=trade['pair'], 
                side='sell', 
                status='closed'
            ).inc()
            
            # Track trade PnL
            trades_pnl.labels(
                exchange=trade['exchange'], 
                pair=trade['pair']
            ).observe(realized_pnl)
            
            # CRITICAL: Verify the exit order was properly recorded in database
            verification_attempts = 0
            max_verification_attempts = 3
            exit_verified = False
            
            async with httpx.AsyncClient(timeout=60.0) as client:
                while verification_attempts < max_verification_attempts and not exit_verified:
                    try:
                        verification_attempts += 1
                        await asyncio.sleep(0.5)  # Brief delay for database commit
                        
                        verify_response = await client.get(f"{database_service_url}/api/v1/trades/{trade_id}")
                        if verify_response.status_code == 200:
                            recorded_trade = verify_response.json()
                            if (recorded_trade.get('status') == 'CLOSED' and 
                                recorded_trade.get('exit_price') and
                                recorded_trade.get('exit_reason') == exit_reason):
                                exit_verified = True
                                logger.info(f"✅ EXIT VERIFICATION SUCCESS: Trade {trade_id} exit properly recorded in database")
                                break
                        
                        logger.warning(f"⚠️ EXIT VERIFICATION ATTEMPT {verification_attempts}: Trade {trade_id} exit not yet recorded in database")
                        
                    except Exception as e:
                        logger.error(f"❌ EXIT VERIFICATION ERROR (attempt {verification_attempts}): {str(e)}")
                        if verification_attempts == max_verification_attempts:
                            break
                
                if not exit_verified:
                    # CRITICAL: Create alert for missing exit trade
                    logger.error(f"🚨 CRITICAL: Exit order {filled_exit_order.get('id')} for trade {trade_id} was filled on {exchange_name} but NOT PROPERLY RECORDED IN DATABASE")
                    
                    # Create critical alert in database
                    alert_data = {
                        'level': 'CRITICAL',
                        'category': 'EXIT_ORDER_RECORDING',
                        'message': f"Exit order {filled_exit_order.get('id')} for {pair} on {exchange_name} filled but database update failed. Trade {trade_id} status inconsistent.",
                        'metadata': {
                            'trade_id': trade_id,
                            'exchange': exchange_name,
                            'pair': pair,
                            'exit_order_id': filled_exit_order.get('id'),
                            'exit_price': exit_price,
                            'exit_reason': exit_reason
                        }
                    }
                    
                    try:
                        alert_response = await client.post(f"{database_service_url}/api/v1/alerts", json=alert_data)
                        if alert_response.status_code in [200, 201]:
                            logger.info(f"🚨 CRITICAL ALERT CREATED: Exit recording failure documented in database")
                    except Exception as alert_error:
                        logger.error(f"❌ Failed to create critical alert for exit recording failure: {alert_error}")
            
            # Remove trade from active_trades_dict if it was there
            if trade_id in self.active_trades_dict:
                del self.active_trades_dict[trade_id]
                logger.info(f"[Exit] Removed {trade_id} from active_trades_dict")
            
            # Update balance with proceeds from sale
            proceed_value = filled_amount * exit_price - exit_fees
            current_balance = self.balances[exchange_name]['available']
            new_available_balance = current_balance + proceed_value
            
            new_total_balance = self.balances[exchange_name]['total'] + proceed_value
            balance_update_data = {
                'exchange': exchange_name,
                'balance': new_total_balance,
                'available_balance': new_available_balance,
                'total_pnl': self.balances[exchange_name]['total_pnl'] + realized_pnl,
                'daily_pnl': self.balances[exchange_name]['daily_pnl'] + realized_pnl,
                'timestamp': datetime.utcnow().isoformat()
            }
            
            async with httpx.AsyncClient(timeout=60.0) as client:
                balance_response = await client.put(f"{database_service_url}/api/v1/balances/{exchange_name}", json=balance_update_data)
                if balance_response.status_code == 200:
                    self.balances[exchange_name]['available'] = new_available_balance
                    self.balances[exchange_name]['total'] = new_total_balance
                    self.balances[exchange_name]['total_pnl'] += realized_pnl
                    self.balances[exchange_name]['daily_pnl'] += realized_pnl
                    logger.info(f"✅ Updated balance after exit: {current_balance:.2f} -> {new_available_balance:.2f} (proceeds: {proceed_value:.2f}, PnL: {realized_pnl:.2f})")
            
            logger.info(f"✅ Successfully closed trade: {trade_id} - SELL {filled_amount:.8f} {pair} at {exit_price:.2f} on {exchange_name} (Order ID: {filled_exit_order.get('id')}, PnL: {realized_pnl:.2f})")
            
        except Exception as e:
            logger.error(f"❌ Error executing trade exit for {trade_id}: {str(e)}")
            logger.error(f"❌ Exception type: {type(e).__name__}")
            logger.error(f"❌ Exception details: {repr(e)}")
            import traceback
            logger.error(f"❌ Full traceback: {traceback.format_exc()}")
            
    async def _update_trade_data(self, trade_id: str, update_data: Dict[str, Any]) -> None:
        """Update trade data in database"""
        try:
            logger.info(f"Updating trade {trade_id} with data: {update_data}")
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.put(f"{database_service_url}/api/v1/trades/{trade_id}", json=update_data)
                response.raise_for_status()
                logger.info(f"Successfully updated trade {trade_id}")
        except httpx.HTTPStatusError as e:
            logger.error(f"Error updating trade data for {trade_id}: {str(e)}")
            logger.error(f"Response content: {e.response.text}")
        except Exception as e:
            logger.error(f"Error updating trade data for {trade_id}: {str(e)}")
            logger.error(f"Exception type: {type(e).__name__}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            
    async def verify_all_trades(self) -> Dict[str, Any]:
        """Verify all recent orders from exchanges are properly recorded in database"""
        try:
            verification_results = {
                'missing_trades': [],
                'incomplete_trades': [],
                'total_checked': 0,
                'total_missing': 0,
                'exchanges_checked': []
            }
            
            exchange_service_url = os.getenv('EXCHANGE_SERVICE_URL', 'http://exchange-service:8003')
            
            async with httpx.AsyncClient(timeout=60.0) as client:
                # Check each exchange
                for exchange_name in self.config_manager.get_all_exchanges():
                    verification_results['exchanges_checked'].append(exchange_name)
                    
                    # Get recent orders from exchange
                    try:
                        orders_response = await client.get(f"{exchange_service_url}/api/v1/trading/orders/{exchange_name}")
                        if orders_response.status_code == 200:
                            exchange_orders = orders_response.json()
                            
                            for order in exchange_orders.get('orders', []):
                                verification_results['total_checked'] += 1
                                order_id = order.get('id')
                                
                                # Check if trade exists in database
                                db_response = await client.get(f"{database_service_url}/api/v1/trades/by-entry-id/{order_id}")
                                if db_response.status_code != 200:
                                    # Order missing from database
                                    verification_results['missing_trades'].append({
                                        'exchange': exchange_name,
                                        'order_id': order_id,
                                        'pair': order.get('symbol'),
                                        'side': order.get('side'),
                                        'amount': order.get('amount'),
                                        'price': order.get('price'),
                                        'status': order.get('status'),
                                        'timestamp': order.get('timestamp')
                                    })
                                    verification_results['total_missing'] += 1
                                else:
                                    # Check if trade record is complete
                                    trade_record = db_response.json()
                                    if not all([trade_record.get('entry_reason'), trade_record.get('entry_price'), trade_record.get('entry_id')]):
                                        verification_results['incomplete_trades'].append({
                                            'trade_id': trade_record.get('trade_id'),
                                            'exchange': exchange_name,
                                            'missing_fields': [
                                                field for field in ['entry_reason', 'entry_price', 'entry_id'] 
                                                if not trade_record.get(field)
                                            ]
                                        })
                    except Exception as e:
                        logger.error(f"❌ Error verifying trades for {exchange_name}: {str(e)}")
                        logger.error(f"❌ Exception type: {type(e).__name__}")
                        import traceback
                        logger.error(f"❌ Full traceback: {traceback.format_exc()}")
            
            logger.info(f"✅ Trade verification complete: Checked {verification_results['total_checked']} orders, found {verification_results['total_missing']} missing trades")
            return verification_results
            
        except Exception as e:
            logger.error(f"❌ Error during trade verification: {str(e)}")
            logger.error(f"❌ Exception type: {type(e).__name__}")
            import traceback
            logger.error(f"❌ Full traceback: {traceback.format_exc()}")
            return {'error': str(e)}

    async def _get_current_price(self, exchange_name: str, pair: str) -> float:
        """Get current market price for a pair - Enhanced with real-time price feed service"""
        try:
            # First try to get price from the enhanced price feed service direct endpoint
            price_feed_service_url = "http://price-feed-service:8007"
            
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    # URL-encode the pair to handle slashes (CRO/USD -> CRO%2FUSD)
                    from urllib.parse import quote
                    encoded_pair = quote(pair, safe='')
                    
                    # Try the direct price endpoint first
                    response = await client.get(f"{price_feed_service_url}/api/v1/price/{exchange_name}/{encoded_pair}")
                    
                    if response.status_code == 200:
                        data = response.json()
                        price = float(data.get('price', 0))
                        if price > 0:
                            cache_hit = data.get('cache_hit', False)
                            source = data.get('source', 'unknown')
                            logger.info(f"📈 Got price from feed service: {exchange_name}/{pair} = ${price:.8f} (cache_hit={cache_hit}, source={source})")
                            return price
                    else:
                        logger.info(f"🔍 Price feed endpoint returned {response.status_code} for {exchange_name}/{pair} (tried {encoded_pair})")
                            
            except Exception as feed_e:
                logger.info(f"⚠️ Price feed service unavailable, falling back to REST API: {feed_e}")
            
            # Fallback to original REST API method
            from core.exchange_handlers import exchange_handler_manager
            exchange_symbol = exchange_handler_manager.format_symbol_for_api(exchange_name, pair)
            
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.get(f"{exchange_service_url}/api/v1/market/ticker/{exchange_name}/{exchange_symbol}")
                response.raise_for_status()
                ticker_data = response.json()
                price = float(ticker_data.get('last', 0.0))
                logger.debug(f"📊 Got price from REST API fallback: {exchange_name}/{pair} = ${price:.8f}")
                return price
                
        except Exception as e:
            logger.error(f"Error getting current price for {pair} on {exchange_name}: {str(e)}")
            logger.error(f"Exception type: {type(e).__name__}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            return 0.0
    
    async def _validate_limit_price_against_market(self, exchange_name: str, pair: str, side: str, limit_price: float) -> bool:
        """Validate if limit price is realistic against current market conditions to prevent failed orders"""
        try:
            # Get current orderbook to check realistic pricing
            from core.exchange_handlers import exchange_handler_manager
            exchange_symbol = exchange_handler_manager.format_symbol_for_api(exchange_name, pair)
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Get orderbook data
                try:
                    response = await client.get(f"{exchange_service_url}/api/v1/market/orderbook/{exchange_name}/{exchange_symbol}")
                    response.raise_for_status()
                    orderbook = response.json()
                    
                    current_bid = float(orderbook.get('bid', 0.0))
                    current_ask = float(orderbook.get('ask', 0.0))
                    
                    if current_bid <= 0 or current_ask <= 0:
                        logger.warning(f"Invalid orderbook data for {pair} on {exchange_name}: bid={current_bid}, ask={current_ask}")
                        return False
                        
                except Exception as e:
                    logger.warning(f"Could not get orderbook for {pair} on {exchange_name}: {e}")
                    # Fallback to ticker if orderbook fails
                    try:
                        response = await client.get(f"{exchange_service_url}/api/v1/market/ticker/{exchange_name}/{exchange_symbol}")
                        response.raise_for_status()
                        ticker = response.json()
                        current_price = float(ticker.get('last', 0.0))
                        
                        if current_price <= 0:
                            logger.warning(f"Invalid ticker price for {pair} on {exchange_name}: {current_price}")
                            return False
                            
                        # Estimate bid/ask from last price (rough approximation)
                        spread_estimate = current_price * 0.001  # 0.1% spread estimate
                        current_bid = current_price - spread_estimate
                        current_ask = current_price + spread_estimate
                        
                    except Exception as ticker_e:
                        logger.error(f"Could not get market data for {pair} on {exchange_name}: {ticker_e}")
                        return False
            
            # Validation logic based on order side
            if side.lower() == 'sell':
                # For sell orders, check if limit price is too high above current ask
                max_realistic_price = current_ask * 1.02  # Allow up to 2% above current ask
                if limit_price > max_realistic_price:
                    logger.warning(f"🚨 Sell limit price ${limit_price:.8f} too high - current ask: ${current_ask:.8f}, max realistic: ${max_realistic_price:.8f}")
                    return False
                    
                # Also check if it's not below current bid (would execute immediately)
                if limit_price < current_bid:
                    logger.info(f"💡 Sell limit price ${limit_price:.8f} below current bid ${current_bid:.8f} - would execute immediately as market order")
                    # This is actually OK, it will just execute immediately
                    
            elif side.lower() == 'buy':
                # For buy orders, check if limit price is too high above current ask
                max_realistic_price = current_ask * 1.05  # Allow up to 5% above current ask for buy orders
                if limit_price > max_realistic_price:
                    logger.warning(f"🚨 Buy limit price ${limit_price:.8f} too high - current ask: ${current_ask:.8f}, max realistic: ${max_realistic_price:.8f}")
                    return False
                    
                # Check if it's above current ask (would execute immediately)
                if limit_price > current_ask:
                    logger.info(f"💡 Buy limit price ${limit_price:.8f} above current ask ${current_ask:.8f} - would execute immediately as market order")
                    # This is OK, it will just execute immediately
            
            logger.info(f"✅ Limit price ${limit_price:.8f} is realistic for {side} {pair} on {exchange_name} (bid: ${current_bid:.8f}, ask: ${current_ask:.8f})")
            return True
            
        except Exception as e:
            logger.error(f"Error validating limit price for {pair} on {exchange_name}: {e}")
            # If validation fails, be conservative and reject the limit order
            return False
            
    async def _run_maintenance_tasks(self) -> None:
        """Run maintenance tasks"""
        try:
            # Update balances
            await self._update_balances()
            
            # Check if pair selection needs updating
            await self._check_pair_selection_update()
            
            # Clean up old data
            await self._cleanup_old_data()
            
        except Exception as e:
            logger.error(f"Error in maintenance tasks: {str(e)}")
            logger.error(f"Exception type: {type(e).__name__}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            
    async def _check_pair_selection_update(self) -> None:
        """Check if pair selection needs updating and update if necessary"""
        try:
            # Get pair selector configuration
            async with httpx.AsyncClient(timeout=60.0) as client:
                config_response = await client.get(f"{config_service_url}/api/v1/config/all")
                config_response.raise_for_status()
                config = config_response.json()
                
            pair_selector_config = config.get('pair_selector', {})
            update_interval_minutes = pair_selector_config.get('update_interval_minutes', 60)
            
            # Check if update is needed
            time_since_update = (datetime.utcnow() - self.last_pair_update).total_seconds() / 60
            
            if time_since_update >= update_interval_minutes:
                logger.info(f"⏰ Pair selection update needed: {time_since_update:.1f} minutes since last update (interval: {update_interval_minutes} min)")
                
                # Update pair selections for all exchanges
                await self._update_pair_selections()
                self.last_pair_update = datetime.utcnow()
                
                logger.info(f"✅ Pair selection updated successfully")
            else:
                logger.debug(f"Pair selection up to date: {time_since_update:.1f}/{update_interval_minutes} minutes")
                
        except Exception as e:
            logger.error(f"Error checking pair selection update: {str(e)}")
            logger.error(f"Exception type: {type(e).__name__}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
    
    async def _update_pair_selections(self) -> None:
        """Update pair selections for all exchanges"""
        try:
            # Get exchanges configuration and update pairs within the same client context
            async with httpx.AsyncClient(timeout=60.0) as client:
                exchanges_response = await client.get(f"{config_service_url}/api/v1/config/exchanges/list")
                exchanges_response.raise_for_status()
                exchanges = exchanges_response.json()
                
                updated_count = 0
                for exchange_name in exchanges:
                    try:
                        # Get exchange-specific configuration
                        exchange_response = await client.get(f"{config_service_url}/api/v1/config/exchanges/{exchange_name}")
                        exchange_response.raise_for_status()
                        exchange_config = exchange_response.json()
                        
                        max_pairs = exchange_config.get('max_pairs', 10)
                        base_currency = exchange_config.get('base_currency', 'USDC')
                        
                        logger.info(f"🔄 Updating pair selection for {exchange_name} (max: {max_pairs}, base: {base_currency})")
                        
                        # Generate new pair selection
                        await self._generate_and_store_pairs(client, exchange_name, max_pairs, base_currency)
                        updated_count += 1
                        
                    except Exception as e:
                        logger.error(f"Error updating pairs for {exchange_name}: {str(e)}")
                        logger.error(f"Exception type: {type(e).__name__}")
                        import traceback
                        logger.error(f"Full traceback: {traceback.format_exc()}")
                        continue
                
                logger.info(f"✅ Updated pair selections for {updated_count}/{len(exchanges)} exchanges")
            
        except Exception as e:
            logger.error(f"Error updating pair selections: {str(e)}")
            logger.error(f"Exception type: {type(e).__name__}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")

    async def _update_balances(self) -> None:
        """Update balances from exchanges"""
        try:
            # Check if we're in simulation mode
            async with httpx.AsyncClient(timeout=60.0) as client:
                mode_response = await client.get(f"{config_service_url}/api/v1/config/mode")
                mode_response.raise_for_status()
                mode_data = mode_response.json()
                if mode_data['is_simulation']:
                    # In simulation mode, update from database instead of exchange
                    for exchange_name in self.balances.keys():
                        try:
                            balance_response = await client.get(f"{database_service_url}/api/v1/balances/{exchange_name}")
                            if balance_response.status_code == 200:
                                balance = balance_response.json()
                                self.balances[exchange_name] = {
                                    'total': float(balance['balance']),
                                    'available': float(balance['available_balance']),
                                    'total_pnl': float(balance['total_pnl']),
                                    'daily_pnl': float(balance['daily_pnl'])
                                }
                                logger.debug(f"[DEBUG] Updated balance for {exchange_name} from database: total={balance['balance']}, available={balance['available_balance']}, total_pnl={balance['total_pnl']}, daily_pnl={balance['daily_pnl']}")
                        except Exception as balance_error:
                            logger.error(f"Error updating balance for {exchange_name} from database: {balance_error}")
                else:
                    # In live mode, update from exchange
                    for exchange_name in self.balances.keys():
                        try:
                            response = await client.get(f"{exchange_service_url}/api/v1/account/balance/{exchange_name}")
                            if response.status_code == 200:
                                balance = response.json()
                                
                                # Get base currency for this exchange
                                base_currency = 'USDC'  # Default
                                if exchange_name == 'cryptocom':
                                    base_currency = 'USD'
                                
                                # Parse total balance
                                total_balance = 0
                                if 'total' in balance and isinstance(balance['total'], dict):
                                    total_balance = balance['total'].get(base_currency, 0) or 0
                                
                                # Parse available balance with improved Bybit handling
                                available_balance = 0
                                if 'free' in balance and isinstance(balance['free'], dict):
                                    available_balance = balance['free'].get(base_currency, 0)
                                    if available_balance is None:
                                        available_balance = 0
                                
                                # Special handling for Bybit unified accounts
                                if exchange_name == 'bybit':
                                    # If free balance is 0 or None, try the 'available' field
                                    if available_balance == 0 and 'available' in balance:
                                        available_balance = balance.get('available', 0) or 0
                                        logger.debug(f"🔧 [BALANCE UPDATE] {exchange_name} using 'available' field: {available_balance}")
                                
                                self.balances[exchange_name] = {
                                    'total': total_balance,
                                    'available': available_balance,
                                    'total_pnl': 0.0,  # Will be calculated from trades
                                    'daily_pnl': 0.0   # Will be calculated from trades
                                }
                                
                                # Store balance data in database
                                try:
                                    balance_data = {
                                        'exchange': exchange_name,
                                        'balance': total_balance,
                                        'available_balance': available_balance,
                                        'total_pnl': 0.0,
                                        'daily_pnl': 0.0,
                                        'timestamp': datetime.utcnow().isoformat()
                                    }
                                    db_response = await client.put(f"{database_service_url}/api/v1/balances/{exchange_name}", json=balance_data)
                                    if db_response.status_code in [200, 201]:
                                        logger.info(f"Successfully stored balance for {exchange_name} in database")
                                    else:
                                        logger.warning(f"Failed to store balance for {exchange_name} in database: {db_response.status_code}")
                                except Exception as db_error:
                                    logger.error(f"Error storing balance for {exchange_name} in database: {db_error}")
                                
                                logger.debug(f"[DEBUG] Updated live balance for {exchange_name}: total={total_balance}, available={available_balance}")
                            else:
                                logger.warning(f"Could not get balance for {exchange_name}")
                        except Exception as balance_error:
                            logger.error(f"Error getting balance for {exchange_name}: {balance_error}")
            logger.info(f"[DEBUG] Final in-memory balances after update: {self.balances}")
        except Exception as e:
            logger.error(f"Error updating balances: {str(e)}")
            logger.error(f"Exception type: {type(e).__name__}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            
    async def _cleanup_old_data(self) -> None:
        """Clean up old data"""
        try:
            # This would clean up old cache entries, logs, etc.
            pass
        except Exception as e:
            logger.error(f"Error cleaning up old data: {str(e)}")
            logger.error(f"Exception type: {type(e).__name__}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            
    async def _close_all_trades(self) -> None:
        """Close all active trades"""
        try:
            for trade_id, trade in self.active_trades_dict.items():
                await self._execute_trade_exit(trade_id, "emergency_stop")
                
        except Exception as e:
            logger.error(f"Error closing all trades: {str(e)}")
            logger.error(f"Exception type: {type(e).__name__}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")

    async def _get_fee_optimization_config(self) -> Dict[str, Any]:
        """Get fee optimization configuration from config service"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(f"{config_service_url}/api/v1/config/trading")
                if response.status_code == 200:
                    trading_config = response.json()
                    return trading_config.get('fee_optimization', {})
                else:
                    logger.warning("Could not fetch fee optimization config, using defaults")
                    return {
                        'use_limit_orders': True,
                        'max_taker_fee': 0.0015,
                        'prefer_maker_orders': True,
                        'limit_order_spread': 0.0005,
                        'fee_alert_threshold': 0.002,
                        'fee_to_pnl_ratio_limit': 0.25
                    }
        except Exception as e:
            logger.warning(f"Error fetching fee optimization config: {e}, using defaults")
            return {
                'use_limit_orders': True,
                'max_taker_fee': 0.0015,
                'prefer_maker_orders': True,
                'limit_order_spread': 0.0005,
                'fee_alert_threshold': 0.002,
                'fee_to_pnl_ratio_limit': 0.25
            }

    async def _determine_order_type(self, exchange_name: str, pair: str, side: str, amount: float) -> str:
        """Determine whether to use limit or market order based on conditions"""
        try:
            # Get fee optimization config
            fee_config = await self._get_fee_optimization_config()
            
            if not fee_config.get('use_limit_orders', True):
                logger.info(f"Limit orders disabled by config, using market order for {pair} on {exchange_name}")
                return 'market'
            
            # Check if we prefer maker orders
            if not fee_config.get('prefer_maker_orders', True):
                logger.info(f"Maker orders not preferred, using market order for {pair} on {exchange_name}")
                return 'market'
            
            # Check market conditions
            current_price = await self._get_current_price(exchange_name, pair)
            if current_price <= 0:
                logger.warning(f"Invalid price for {pair} on {exchange_name}, using market order")
                return 'market'
            
            # Get current spread
            spread = await self._get_current_spread(exchange_name, pair)
            if spread is None:
                logger.warning(f"Could not get spread for {pair} on {exchange_name}, using market order")
                return 'market'
            
            # Check minimum spread requirement for limit orders
            min_spread_for_limit = fee_config.get('min_spread_for_limit', 0.0003)
            if spread < min_spread_for_limit:
                logger.info(f"Spread {spread:.6f} below minimum {min_spread_for_limit:.6f} for limit order on {pair}")
                return 'market'
            
            # Check optimal spread for limit orders
            limit_order_spread = fee_config.get('limit_order_spread', 0.0005)
            if spread < limit_order_spread:
                logger.info(f"Spread {spread:.6f} below optimal {limit_order_spread:.6f} for limit order on {pair}")
                return 'market'
            
            # Check market stability
            is_stable = await self._is_market_stable(exchange_name, pair)
            if not is_stable:
                logger.info(f"Market not stable for limit order on {pair}")
                return 'market'
            
            logger.info(f"✅ Conditions met for limit order on {pair}: spread={spread:.6f}, stable={is_stable}")
            return 'limit'
                
        except Exception as e:
            logger.warning(f"Error determining order type for {pair} on {exchange_name}: {e}, using market order")
            return 'market'

    async def _get_current_spread(self, exchange_name: str, pair: str) -> Optional[float]:
        """Get current bid-ask spread for a pair"""
        try:
            # Convert pair format for exchange service orderbook endpoint
            # The exchange service expects symbols without slashes (e.g., LINKUSDC not LINK/USDC)
            exchange_symbol = pair.replace('/', '') if '/' in pair else pair
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(f"{exchange_service_url}/api/v1/market/orderbook/{exchange_name}/{exchange_symbol}?limit=5")
                logger.info(f"📊 Orderbook request: {exchange_service_url}/api/v1/market/orderbook/{exchange_name}/{exchange_symbol} -> {response.status_code}")
                if response.status_code == 200:
                    orderbook = response.json()
                    bids = orderbook.get('bids', [])
                    asks = orderbook.get('asks', [])
                    
                    if bids and asks:
                        best_bid = float(bids[0][0])
                        best_ask = float(asks[0][0])
                        spread = (best_ask - best_bid) / best_bid
                        logger.info(f"📊 Spread for {pair} on {exchange_name}: {spread:.6f} (bid: {best_bid}, ask: {best_ask})")
                        return spread
                    else:
                        logger.warning(f"📊 Empty orderbook for {pair} on {exchange_name}")
                else:
                    logger.warning(f"📊 Orderbook request failed: {response.status_code} - {response.text[:200]}")
                    
            return None
        except Exception as e:
            logger.warning(f"Error getting spread for {pair} on {exchange_name}: {e}")
            return None

    async def _is_market_stable(self, exchange_name: str, pair: str) -> bool:
        """Check if market is stable enough for limit orders"""
        try:
            # Get fee optimization config for volatility threshold
            fee_config = await self._get_fee_optimization_config()
            volatility_threshold = fee_config.get('volatility_threshold', 0.005)
            
            # Get recent price data to check volatility
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(f"{exchange_service_url}/api/v1/market/ohlcv/{exchange_name}/{pair}?timeframe=5m&limit=12")
                if response.status_code == 200:
                    ohlcv_data = response.json()
                    prices = [float(candle[4]) for candle in ohlcv_data.get('data', [])]  # Close prices
                    
                    if len(prices) >= 6:
                        # Calculate price volatility over last 6 periods (30 minutes)
                        recent_prices = prices[-6:]
                        price_changes = [abs(recent_prices[i] - recent_prices[i-1]) / recent_prices[i-1] for i in range(1, len(recent_prices))]
                        avg_volatility = sum(price_changes) / len(price_changes)
                        
                        # Consider market stable if average volatility < threshold
                        is_stable = avg_volatility < volatility_threshold
                        logger.info(f"Market stability check for {pair} on {exchange_name}: volatility={avg_volatility:.4f}, threshold={volatility_threshold:.4f}, stable={is_stable}")
                        return is_stable
                    
            return True  # Default to stable if we can't determine
        except Exception as e:
            logger.warning(f"Error checking market stability for {pair} on {exchange_name}: {e}")
            return True  # Default to stable

    async def _calculate_limit_order_price(self, exchange_name: str, pair: str, side: str, current_price: float) -> float:
        """Calculate optimal limit order price using orderbook data for competitive pricing"""
        try:
            # Get orderbook for competitive pricing
            exchange_symbol = self._convert_pair_format(exchange_name, pair)
            orderbook_url = f"{exchange_service_url}/api/v1/market/orderbook/{exchange_name}/{exchange_symbol}?limit=5"
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(orderbook_url)
                
                if response.status_code == 200:
                    orderbook = response.json()
                    bids = orderbook.get('bids', [])
                    asks = orderbook.get('asks', [])
                    
                    if bids and asks:
                        best_bid = float(bids[0][0])
                        best_ask = float(asks[0][0])
                        market_spread = (best_ask - best_bid) / best_bid
                        
                        logger.info(f"📊 Orderbook for {pair}: bid=${best_bid:.8f}, ask=${best_ask:.8f}, spread={market_spread*100:.4f}%")
                        
                        if side.lower() == 'buy':
                            # Place buy order slightly above best bid for quick fill while still getting maker fee
                            # Use 0.01% above best bid, but ensure we don't cross the spread
                            competitive_adjustment = 0.0001  # 0.01%
                            limit_price = best_bid * (1 + competitive_adjustment)
                            
                            # Don't exceed 95% of the way to best ask
                            max_price = best_bid + (best_ask - best_bid) * 0.95
                            limit_price = min(limit_price, max_price)
                            
                        else:  # sell
                            # Place sell order slightly below best ask for quick fill while still getting maker fee
                            competitive_adjustment = 0.0001  # 0.01%
                            limit_price = best_ask * (1 - competitive_adjustment)
                            
                            # Don't go below 105% of best bid
                            min_price = best_bid * 1.05
                            limit_price = max(limit_price, min_price)
                        
                        # Round to appropriate decimal places
                        decimal_places = 8
                        final_price = round(limit_price, decimal_places)
                        
                        logger.info(f"🎯 Competitive {side} price: ${final_price:.8f} (vs market bid=${best_bid:.8f}, ask=${best_ask:.8f})")
                        return final_price
                
            # Fallback to conservative pricing if orderbook fails
            logger.warning(f"⚠️ Orderbook unavailable, using fallback pricing for {pair}")
            fee_config = await self._get_fee_optimization_config()
            spread = fee_config.get('limit_order_spread', 0.0005)  # 0.05%
            
            if side.lower() == 'buy':
                limit_price = current_price * (1 - spread)
            else:
                limit_price = current_price * (1 + spread)
            
            return round(limit_price, 8)
            
        except Exception as e:
            logger.error(f"Error calculating limit order price for {pair} on {exchange_name}: {e}")
            return current_price  # Fallback to current price
    
    async def _get_order_management_config(self) -> Dict[str, Any]:
        """Get order management configuration from config service"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(f"{config_service_url}/api/v1/config/all")
                if response.status_code == 200:
                    config = response.json()
                    return config.get('trading', {}).get('order_management', {})
                else:
                    logger.warning(f"Failed to get config: {response.status_code}, using defaults")
        except Exception as e:
            logger.warning(f"Config fetch error: {e}, using defaults")
        
        # Return defaults if config fetch fails
        return {
            'limit_order_timeout_seconds': 300,
            'max_retry_attempts': 2,
            'competitive_pricing_enabled': True,
            'competitive_pricing_buffer': 0.0001
        }
    
    async def _record_failed_order_attempt(self, trade_id: str, exchange: str, pair: str, side: str, amount: float, price: float, error: str, attempts: int):
        """Record failed order attempt for analytics and performance tracking"""
        try:
            failed_order_data = {
                'order_id': f'failed_limit_{int(time.time())}_{uuid.uuid4().hex[:8]}',
                'trade_id': trade_id,
                'exchange': exchange,
                'symbol': pair,
                'order_type': 'limit',
                'side': side,
                'amount': amount,
                'price': price,
                'status': 'failed',
                'error_message': f'Failed after {attempts} attempts: {error}',
                'retry_attempts': attempts,
                'created_at': datetime.utcnow().isoformat() + 'Z',
                'updated_at': datetime.utcnow().isoformat() + 'Z'
            }
            await self._record_order_to_database(failed_order_data)
            logger.info(f"📊 Recorded failed order attempt for analytics: {failed_order_data['order_id']}")
        except Exception as e:
            logger.error(f"❌ Failed to record failed order attempt: {e}")

    async def _place_limit_order(self, exchange_name: str, pair: str, side: str, amount: float, trade_id: str = None) -> Optional[Dict[str, Any]]:
        """Place a limit order with proper price calculation and configuration-driven behavior"""
        try:
            # Ensure we reference the module-level URL
            global database_service_url
            # Sanitize amount first
            sanitized_amount = self._sanitize_numeric_value(amount)
            
            # CRITICAL: Validate position availability before placing order
            position_valid = await self._validate_position_for_order(exchange_name, pair, side, sanitized_amount)
            if not position_valid:
                logger.error(f"❌ Position validation failed: Insufficient {self._get_required_asset(pair, side)} balance on {exchange_name}")
                return None
            
            # Get current market price
            current_price = await self._get_current_price(exchange_name, pair)
            if current_price <= 0:
                logger.error(f"Invalid price for {pair} on {exchange_name}")
                return None
            
            # Get order management configuration
            order_config = await self._get_order_management_config()
            timeout_seconds = order_config.get('limit_order_timeout_seconds', 300)
            max_retries = order_config.get('max_retry_attempts', 2)
            
            # Calculate optimal limit order price
            limit_price = await self._calculate_limit_order_price(exchange_name, pair, side, current_price)
            
            # CRITICAL FIX: Validate limit price against current market to prevent unrealistic exit orders
            is_price_realistic = await self._validate_limit_price_against_market(exchange_name, pair, side, limit_price)
            if not is_price_realistic:
                logger.warning(f"🚨 Limit price ${limit_price:.8f} unrealistic for {side} {pair} on {exchange_name}, falling back to market order")
                return await self._place_market_order(exchange_name, pair, side, amount, trade_id)
            
            # Convert pair format for exchange service
            exchange_symbol = self._convert_pair_format(exchange_name, pair)
            
            # Generate client order ID for idempotency (Phase 0)
            client_order_id = self._generate_client_order_id(trade_id, "limit")
            local_order_id = str(uuid.uuid4())  # Phase 2: Generate local order ID
            
            logger.info(f"🆔 Generated client_order_id: {client_order_id} for limit order")
            logger.info(f"🆔 Generated local_order_id: {local_order_id}")
            
            # Phase 2: Create order mapping with idempotency check
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    mapping_data = {
                        'local_order_id': local_order_id,
                        'client_order_id': client_order_id,
                        'exchange': exchange_name,
                        'symbol': pair,
                        'side': side,
                        'order_type': 'limit',
                        'amount': sanitized_amount,
                        'price': limit_price,
                        'trade_id': trade_id
                    }
                    
                    mapping_response = await client.post(f"{database_service_url}/api/v1/order-mappings", json=mapping_data)
                    mapping_response.raise_for_status()
                    mapping_result = mapping_response.json()
                    
                    if mapping_result['status'] == 'already_exists':
                        logger.warning(f"🔄 Idempotency violation: Order with client_order_id {client_order_id} already exists")
                        return None  # Prevent duplicate order
                    
                    logger.info(f"✅ Order mapping created: {local_order_id} -> {client_order_id}")
            except Exception as mapping_error:
                logger.error(f"Failed to create order mapping: {mapping_error}")
                return None
            
            # Phase 2: Emit OrderCreated event
            await self._emit_order_created_event(
                local_order_id, client_order_id, trade_id, exchange_name, 
                pair, side, "limit", sanitized_amount, limit_price
            )
            
            logger.info(f"🔄 Placing limit order: {side} {sanitized_amount:.8f} {pair} @ {limit_price:.8f} on {exchange_name} (timeout: {timeout_seconds}s)")
            
            # Prepare order data
            order_data = {
                'exchange': exchange_name,
                'symbol': exchange_symbol,
                'order_type': 'limit',
                'side': side,
                'amount': sanitized_amount,
                'price': limit_price,
                'client_order_id': client_order_id  # Phase 0: Enable idempotency
            }
            
            # Retry logic for order placement
            last_error = None
            for attempt in range(max_retries + 1):
                try:
                    # Place order
                    async with httpx.AsyncClient(timeout=60.0) as client:
                        response = await client.post(f"{exchange_service_url}/api/v1/trading/order", json=order_data)
                        
                        logger.info(f"📥 Limit order response (attempt {attempt + 1}): Status {response.status_code}")
                        
                        if response.status_code == 200:
                            result = response.json()
                            order = result.get('order', {}) if result else {}
                            
                            if result and order.get('id'):
                                logger.info(f"✅ Limit order placed successfully: {order['id']} (attempt {attempt + 1})")
                                
                                # Phase 2: Emit ExchangeAck event
                                await self._emit_exchange_ack_event(local_order_id, order.get('id'), order)
                                
                                # Phase 2: Update order mapping with exchange order ID
                                try:
                                    async with httpx.AsyncClient(timeout=30.0) as client:
                                        update_data = {
                                            'exchange_order_id': order.get('id'),
                                            'status': 'ACKNOWLEDGED',
                                            'acknowledged_at': datetime.utcnow().isoformat()
                                        }
                                        
                                        mapping_response = await client.put(
                                            f"{database_service_url}/api/v1/order-mappings/{client_order_id}", 
                                            json=update_data
                                        )
                                        mapping_response.raise_for_status()
                                        logger.info(f"✅ Order mapping updated with exchange order ID: {order.get('id')}")
                                except Exception as update_error:
                                    logger.error(f"Failed to update order mapping: {update_error}")
                                
                                # Track the order with configuration-based timeout
                                order_tracking_data = {
                                    'exchange': exchange_name,
                                    'symbol': exchange_symbol,
                                    'side': side,
                                    'amount': sanitized_amount,
                                    'price': limit_price,
                                    'order_type': 'limit',
                                    'trade_id': trade_id
                                }
                                await order_tracker.track_order(order['id'], order_tracking_data, timeout_seconds=timeout_seconds)
                                
                                # Record order to database for dashboard tracking
                                db_order_data = {
                                    'order_id': order['id'],
                                    'trade_id': trade_id,
                                    'exchange': exchange_name,
                                    'symbol': exchange_symbol,
                                    'order_type': 'limit',
                                    'side': side,
                                    'amount': sanitized_amount,
                                    'price': limit_price,
                                    'status': 'pending',
                                    'fees': 0.0,
                                    'filled_amount': 0.0,
                                    'timeout_seconds': timeout_seconds,
                                    'exchange_order_id': order['id'],
                                    'client_order_id': order.get('clientOrderId', ''),
                                    'retry_attempt': attempt + 1
                                }
                                await self._record_order_to_database(db_order_data)
                                
                                return order
                            else:
                                logger.error(f"❌ Limit order response missing 'id' field for {pair} on {exchange_name}")
                                last_error = "Missing order ID in response"
                                break
                        else:
                            # Handle error responses
                            try:
                                error_detail = response.json().get('detail', 'Unknown error')
                            except:
                                error_detail = response.text or f"HTTP {response.status_code}"
                            
                            # Handle dust amount errors for limit orders too
                            if response.status_code == 422 and 'DUST_AMOUNT' in error_detail:
                                logger.warning(f"💸 Dust amount detected in limit order for {pair} on {exchange_name}")
                                if trade_id:
                                    await self._close_dust_position(trade_id, exchange_name, pair, sanitized_amount, "dust_amount_below_minimum")
                                return None
                            
                            # Check if this is a retryable error
                            retryable_errors = ['TIMEOUT', 'RATE_LIMIT', 'NETWORK_ERROR', 'TEMPORARY']
                            if any(err in error_detail.upper() for err in retryable_errors) and attempt < max_retries:
                                logger.warning(f"⚠️ Retryable error on attempt {attempt + 1}: {error_detail}")
                                last_error = error_detail
                                await asyncio.sleep(2 ** attempt)  # Exponential backoff
                                continue
                            else:
                                logger.error(f"❌ Limit order failed for {pair} on {exchange_name}: {error_detail}")
                                last_error = error_detail
                                break
                                
                except httpx.TimeoutException as timeout_err:
                    if attempt < max_retries:
                        logger.warning(f"⏰ Timeout on attempt {attempt + 1} for {pair} on {exchange_name}, retrying...")
                        last_error = f"Timeout: {timeout_err}"
                        await asyncio.sleep(2 ** attempt)
                        continue
                    else:
                        logger.error(f"❌ Final timeout for limit order {pair} on {exchange_name}")
                        last_error = f"Final timeout: {timeout_err}"
                        break
                
                except Exception as request_error:
                    if attempt < max_retries:
                        logger.warning(f"⚠️ Request error on attempt {attempt + 1}: {request_error}")
                        last_error = str(request_error)
                        await asyncio.sleep(2 ** attempt)
                        continue
                    else:
                        logger.error(f"❌ Final request error for {pair} on {exchange_name}: {request_error}")
                        last_error = str(request_error)
                        break
            
            # Record failed order attempt for analytics
            await self._record_failed_order_attempt(trade_id, exchange_name, pair, side, sanitized_amount, limit_price, last_error, max_retries + 1)
            return None
                
        except Exception as e:
            logger.error(f"❌ Error placing limit order for {pair} on {exchange_name}: {str(e)}")
            return None

    async def _place_smart_order(self, exchange_name: str, pair: str, side: str, amount: float, trade_id: str = None) -> Optional[Dict[str, Any]]:
        """Place either limit or market order based on conditions with fallback"""
        try:
            # Determine order type
            order_type = await self._determine_order_type(exchange_name, pair, side, amount)
            
            if order_type == 'limit':
                logger.info(f"🔄 Attempting limit order for {side} {amount:.8f} {pair} on {exchange_name}")
                limit_result = await self._place_limit_order(exchange_name, pair, side, amount, trade_id)
                
                if limit_result and limit_result.get('id'):
                    logger.info(f"✅ Limit order placed successfully: {limit_result['id']}")
                    # Record performance tracking
                    await order_tracker.record_order_attempt('limit', limit_result)
                    return limit_result
                else:
                    logger.warning(f"⚠️ Limit order failed, falling back to market order for {pair} on {exchange_name}")
                    
                    # Record failed limit order attempt to database for tracking
                    import time
                    db_failed_order = {
                        'order_id': f'failed_limit_{int(time.time())}',
                        'trade_id': trade_id,
                        'exchange': exchange_name,
                        'symbol': pair,
                        'order_type': 'limit',
                        'side': side,
                        'amount': amount,
                        'price': None,
                        'status': 'failed',
                        'error_message': 'Limit order failed, falling back to market order'
                    }
                    await self._record_order_to_database(db_failed_order)
                    
                    # Fallback to market order
                    market_result = await self._place_market_order(exchange_name, pair, side, amount, trade_id)
                    if market_result and market_result.get('id'):
                        await order_tracker.record_order_attempt('market', market_result)
                        logger.info(f"📊 Market order fallback successful: {market_result['id']}")
                    return market_result
            else:
                logger.info(f"🔄 Using market order for {side} {amount:.8f} {pair} on {exchange_name}")
                market_result = await self._place_market_order(exchange_name, pair, side, amount, trade_id)
                if market_result and market_result.get('id'):
                    await order_tracker.record_order_attempt('market', market_result)
                    logger.info(f"📊 Direct market order successful: {market_result['id']}")
                return market_result
                
        except Exception as e:
            error_str = str(e)
            logger.error(f"❌ Error in smart order placement for {pair} on {exchange_name}: {e}")
            
            # Check if this is a decimal conversion error
            if "ConversionSyntax" in error_str or "InvalidOperation" in error_str or "decimal" in error_str.lower():
                logger.warning(f"🔧 Detected decimal conversion error, retrying with ultra-sanitized values")
                return await self._retry_with_ultra_sanitized_values(exchange_name, pair, side, amount, trade_id)
            else:
                # For non-decimal errors, use market order fallback
                logger.info(f"🔄 Final fallback to market order for {pair} on {exchange_name}")
                return await self._place_market_order(exchange_name, pair, side, amount, trade_id)

    async def _retry_with_ultra_sanitized_values(self, exchange_name: str, pair: str, side: str, amount: float, trade_id: str = None) -> Optional[Dict[str, Any]]:
        """Retry order placement with ultra-sanitized decimal values"""
        try:
            # Ultra-sanitize the amount to prevent decimal conversion errors
            ultra_sanitized_amount = self._ultra_sanitize_decimal_value(amount)
            
            logger.info(f"🔧 Retrying with ultra-sanitized amount: {amount} -> {ultra_sanitized_amount}")
            
            # Try market order first with ultra-sanitized values (more likely to succeed)
            market_result = await self._place_market_order_with_sanitized_values(exchange_name, pair, side, ultra_sanitized_amount, trade_id)
            
            if market_result and market_result.get('id'):
                logger.info(f"✅ Market order with ultra-sanitized values successful: {market_result['id']}")
                return market_result
            else:
                logger.warning(f"⚠️ Market order with ultra-sanitized values failed, trying limit order")
                # Try limit order as last resort with ultra-sanitized values
                limit_result = await self._place_limit_order_with_sanitized_values(exchange_name, pair, side, ultra_sanitized_amount, trade_id)
                
                if limit_result and limit_result.get('id'):
                    logger.info(f"✅ Limit order with ultra-sanitized values successful: {limit_result['id']}")
                    return limit_result
                else:
                    logger.error(f"❌ All order attempts failed even with ultra-sanitized values")
                    return None
                    
        except Exception as e:
            logger.error(f"❌ Error in ultra-sanitized retry for {pair} on {exchange_name}: {e}")
            return None

    def _ultra_sanitize_decimal_value(self, value: float) -> float:
        """Ultra-sanitize decimal values to prevent conversion errors"""
        try:
            if value is None:
                return 0.0
            
            # Convert to float first
            float_value = float(value)
            
            # Handle special cases
            if float_value == 0:
                return 0.0
            if float_value < 0:
                return abs(float_value)  # Ensure positive
            
            # Ultra-aggressive sanitization for decimal conversion errors
            # Convert to string with minimal precision, then back to float
            if float_value >= 1:
                # For values >= 1, use only 1 decimal place
                sanitized_value = round(float_value, 1)
            elif float_value >= 0.1:
                # For values >= 0.1, use 2 decimal places
                sanitized_value = round(float_value, 2)
            elif float_value >= 0.01:
                # For values >= 0.01, use 3 decimal places
                sanitized_value = round(float_value, 3)
            elif float_value >= 0.001:
                # For values >= 0.001, use 4 decimal places
                sanitized_value = round(float_value, 4)
            else:
                # For very small values, use 5 decimal places maximum
                sanitized_value = round(float_value, 5)
            
            # Final safety check - ensure it's a valid number
            if not isinstance(sanitized_value, (int, float)) or sanitized_value <= 0:
                logger.warning(f"🔧 Ultra-sanitized value invalid, using safe fallback")
                return 0.001  # Safe minimum value
            
            logger.info(f"🔧 Ultra-sanitized decimal value: {value} -> {sanitized_value}")
            return sanitized_value
            
        except (ValueError, TypeError, ArithmeticError) as e:
            logger.error(f"❌ Error ultra-sanitizing decimal value {value}: {e}")
            return 0.001  # Safe fallback value

    async def _place_market_order_with_sanitized_values(self, exchange_name: str, pair: str, side: str, amount: float, trade_id: str = None) -> Optional[Dict[str, Any]]:
        """Place market order with pre-sanitized values"""
        try:
            # Convert pair format for exchange service
            exchange_symbol = self._convert_pair_format(exchange_name, pair)
            
            # Prepare order data with pre-sanitized values
            order_data = {
                'exchange': exchange_name,
                'symbol': exchange_symbol,
                'order_type': 'market',
                'side': side,
                'amount': amount  # Already sanitized
            }
            
            logger.info(f"🔄 Placing market order with sanitized values: {side} {amount:.8f} {pair} on {exchange_name}")
            
            # Place order
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(f"{exchange_service_url}/api/v1/trading/order", json=order_data)
                
                if response.status_code == 200:
                    result = response.json()
                    
                    order = result.get('order', {}) if result else {}
                    if result and order.get('id'):
                        logger.info(f"✅ Market order with sanitized values placed: {order['id']}")
                        
                        # Record order to database
                        db_order_data = {
                            'order_id': order['id'],
                            'trade_id': trade_id,
                            'exchange': exchange_name,
                            'symbol': exchange_symbol,
                            'order_type': 'market',
                            'side': side,
                            'amount': amount,
                            'status': 'pending'
                        }
                        await self._record_order_to_database(db_order_data)
                        
                        # Record fees if available
                        if trade_id:
                            await self._record_trade_fees(trade_id, result)
                        
                        return result
                    else:
                        logger.error(f"❌ Market order response missing ID for {pair} on {exchange_name}")
                        return None
                else:
                    logger.error(f"❌ Failed to place market order: {response.status_code} - {response.text}")
                    return None
                    
        except Exception as e:
            logger.error(f"❌ Error placing market order with sanitized values for {pair} on {exchange_name}: {str(e)}")
            return None

    async def _place_limit_order_with_sanitized_values(self, exchange_name: str, pair: str, side: str, amount: float, trade_id: str = None) -> Optional[Dict[str, Any]]:
        """Place limit order with pre-sanitized values"""
        try:
            # Get current market price
            current_price = await self._get_current_price(exchange_name, pair)
            if current_price <= 0:
                logger.error(f"Invalid price for {pair} on {exchange_name}")
                return None
            
            # Calculate limit price with sanitized current price
            sanitized_current_price = self._ultra_sanitize_decimal_value(current_price)
            limit_price = await self._calculate_limit_order_price(exchange_name, pair, side, sanitized_current_price)
            sanitized_limit_price = self._ultra_sanitize_decimal_value(limit_price)
            
            # Convert pair format for exchange service
            exchange_symbol = self._convert_pair_format(exchange_name, pair)
            
            logger.info(f"🔄 Placing limit order with sanitized values: {side} {amount:.8f} {pair} @ {sanitized_limit_price:.8f} on {exchange_name}")
            
            # Prepare order data with sanitized values
            order_data = {
                'exchange': exchange_name,
                'symbol': exchange_symbol,
                'order_type': 'limit',
                'side': side,
                'amount': amount,  # Already sanitized
                'price': sanitized_limit_price  # Sanitized
            }
            
            # Place order
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(f"{exchange_service_url}/api/v1/trading/order", json=order_data)
                
                if response.status_code == 200:
                    result = response.json()
                    
                    order = result.get('order', {}) if result else {}
                    if result and order.get('id'):
                        logger.info(f"✅ Limit order with sanitized values placed: {order['id']}")
                        
                        # Track the order
                        order_data = {
                            'exchange': exchange_name,
                            'symbol': exchange_symbol,
                            'side': side,
                            'amount': amount,
                            'price': sanitized_limit_price,
                            'order_type': 'limit',
                            'trade_id': trade_id
                        }
                        await order_tracker.track_order(order['id'], order_data, timeout_seconds=300)
                        
                        # Record order to database
                        db_order_data = {
                            'order_id': order['id'],
                            'trade_id': trade_id,
                            'exchange': exchange_name,
                            'symbol': exchange_symbol,
                            'order_type': 'limit',
                            'side': side,
                            'amount': amount,
                            'price': sanitized_limit_price,
                            'status': 'pending',
                            'timeout_seconds': 300
                        }
                        await self._record_order_to_database(db_order_data)
                        
                        # Record fees if available
                        if trade_id:
                            await self._record_trade_fees(trade_id, result)
                        
                        return result
                    else:
                        logger.error(f"❌ Limit order response missing ID for {pair} on {exchange_name}")
                        return None
                else:
                    logger.error(f"❌ Failed to place limit order: {response.status_code} - {response.text}")
                    return None
                    
        except Exception as e:
            logger.error(f"❌ Error placing limit order with sanitized values for {pair} on {exchange_name}: {str(e)}")
            return None

    def _sanitize_numeric_value(self, value: float) -> float:
        """Sanitize numeric values to prevent decimal conversion errors"""
        try:
            if value is None:
                return 0.0
            
            # Convert to float first
            float_value = float(value)
            
            # Handle special cases
            if float_value == 0:
                return 0.0
            if float_value < 0:
                return abs(float_value)  # Ensure positive
            
            # Format to remove scientific notation and excessive precision
            # Use g format to remove trailing zeros, then convert back to float
            formatted_str = f"{float_value:.10g}"
            sanitized_value = float(formatted_str)
            
            # Ensure reasonable precision based on value size
            if sanitized_value >= 1:
                # For values >= 1, use 2 decimal places
                sanitized_value = round(sanitized_value, 2)
            elif sanitized_value >= 0.01:
                # For values >= 0.01, use 4 decimal places
                sanitized_value = round(sanitized_value, 4)
            elif sanitized_value >= 0.0001:
                # For values >= 0.0001, use 6 decimal places
                sanitized_value = round(sanitized_value, 6)
            else:
                # For very small values, use 8 decimal places
                sanitized_value = round(sanitized_value, 8)
            
            logger.info(f"🔧 Sanitized numeric value: {value} -> {sanitized_value}")
            return sanitized_value
            
        except (ValueError, TypeError) as e:
            logger.error(f"❌ Error sanitizing numeric value {value}: {e}")
            # Return a safe fallback value
            return 0.0

    async def _monitor_pending_orders(self):
        """Background task to monitor pending limit orders"""
        logger.info("🔄 Starting order monitoring loop")
        
        while self.trading_active:
            try:
                pending_orders = order_tracker.get_pending_orders()
                
                for order_id, order_info in list(pending_orders.items()):
                    try:
                        # Check if order should be cancelled due to timeout
                        if order_tracker.should_cancel_order(order_id):
                            logger.warning(f"⏰ Order {order_id} timed out, cancelling")
                            await self._cancel_and_retry_order(order_id, order_info)
                            continue
                        
                        # Check order status from exchange
                        current_status = await self._get_order_status(order_id, order_info['order_data']['exchange'])
                        
                        if current_status == 'filled':
                            # Order filled - process it
                            filled_data = await self._get_filled_order_details(order_id, order_info['order_data']['exchange'])
                            await self._process_filled_order(order_id, filled_data, order_info)
                            
                        elif current_status == 'cancelled':
                            # Order cancelled - handle accordingly
                            await self._handle_cancelled_order(order_id, order_info)
                            
                        elif current_status == 'rejected':
                            # Order rejected - retry with market order
                            logger.error(f"❌ Order {order_id} rejected, retrying with market order")
                            await self._retry_with_market_order(order_id, order_info)
                            
                    except Exception as e:
                        logger.error(f"❌ Error monitoring order {order_id}: {e}")
                
                # Sleep before next check
                await asyncio.sleep(10)  # Check every 10 seconds
                
            except Exception as e:
                logger.error(f"❌ Error in order monitoring loop: {e}")
                await asyncio.sleep(30)  # Longer sleep on error
        
        logger.info("🛑 Order monitoring loop stopped")

    async def _get_order_status(self, order_id: str, exchange_name: str) -> str:
        """Get order status from exchange"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(f"{exchange_service_url}/api/v1/trading/orders/{exchange_name}")
                if response.status_code == 200:
                    orders = response.json().get('orders', [])
                    
                    for order in orders:
                        if order.get('id') == order_id:
                            return order.get('status', 'unknown')
                
                return 'unknown'
        except Exception as e:
            logger.error(f"Error getting order status for {order_id}: {e}")
            return 'unknown'

    async def _get_filled_order_details(self, order_id: str, exchange_name: str) -> Optional[dict]:
        """Get filled order details from exchange"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(f"{exchange_service_url}/api/v1/trading/filled-orders/{exchange_name}")
                if response.status_code == 200:
                    orders = response.json().get('orders', [])
                    
                    for order in orders:
                        if order.get('id') == order_id:
                            return order
                
                return None
        except Exception as e:
            logger.error(f"Error getting filled order details for {order_id}: {e}")
            return None

    async def _cancel_and_retry_order(self, order_id: str, order_info: dict):
        """Cancel order and retry with market order if needed"""
        try:
            exchange_name = order_info['order_data']['exchange']
            symbol = order_info['order_data']['symbol']
            
            # Cancel the limit order
            await self._cancel_order(exchange_name, order_id, symbol)
            
            # Update order tracker
            await order_tracker.update_order_status(order_id, 'cancelled')
            
            # If this is an entry order, retry with market order
            trade_id = order_info.get('trade_id')
            if trade_id and order_info['order_data']['side'] == 'buy':
                logger.info(f"🔄 Retrying entry with market order for trade {trade_id}")
                await self._retry_entry_with_market_order(trade_id, order_info['order_data'])
                
        except Exception as e:
            logger.error(f"Error cancelling and retrying order {order_id}: {e}")

    async def _retry_with_market_order(self, order_id: str, order_info: dict):
        """Retry failed order with market order"""
        try:
            exchange_name = order_info['order_data']['exchange']
            symbol = order_info['order_data']['symbol']
            side = order_info['order_data']['side']
            amount = order_info['order_data']['amount']
            trade_id = order_info.get('trade_id')
            
            logger.info(f"🔄 Retrying {side} order with market order for {symbol}")
            
            # Place market order
            market_result = await self._place_market_order(exchange_name, symbol, side, amount, trade_id)
            
            if market_result and market_result.get('id'):
                logger.info(f"✅ Market order retry successful: {market_result['id']}")
                # Update order tracker
                await order_tracker.update_order_status(order_id, 'cancelled')
            else:
                logger.error(f"❌ Market order retry failed for {symbol}")
                
        except Exception as e:
            logger.error(f"Error retrying with market order for {order_id}: {e}")

    async def _retry_entry_with_market_order(self, trade_id: str, original_order_data: dict):
        """Retry trade entry with market order"""
        try:
            exchange_name = original_order_data['exchange']
            symbol = original_order_data['symbol']
            amount = original_order_data['amount']
            
            logger.info(f"🔄 Retrying trade entry with market order for {trade_id}")
            
            # Place market order
            market_result = await self._place_market_order(exchange_name, symbol, 'buy', amount, trade_id)
            
            if market_result and market_result.get('id'):
                logger.info(f"✅ Market order entry successful: {market_result['id']}")
                # Update trade status
                await self._update_trade_status(trade_id, 'OPEN', 'Market order retry successful')
            else:
                logger.error(f"❌ Market order entry failed for {trade_id}")
                await self._update_trade_status(trade_id, 'FAILED', 'Market order retry failed')
                
        except Exception as e:
            logger.error(f"Error retrying entry with market order for {trade_id}: {e}")
            await self._update_trade_status(trade_id, 'FAILED', f'Retry error: {str(e)}')

    async def _process_filled_order(self, order_id: str, filled_data: dict, order_info: dict):
        """Process a filled order"""
        try:
            # Calculate fill time
            start_time = order_info.get('start_time', time.time())
            fill_time = time.time() - start_time
            
            # Calculate fee savings if this was a limit order
            fee_saved = 0.0
            if order_info.get('type') == 'limit':
                # Estimate fee savings (limit orders typically have lower fees)
                taker_fee_rate = 0.0015  # 0.15% typical taker fee
                maker_fee_rate = 0.0005  # 0.05% typical maker fee
                order_amount = filled_data.get('filled', 0)
                fee_saved = order_amount * (taker_fee_rate - maker_fee_rate)
            
            # Update order tracker
            await order_tracker.update_order_status(order_id, 'filled', filled_data)
            
            # Record performance metrics
            await order_tracker.record_order_result(order_id, 'filled', fill_time, fee_saved)
            
            # Extract fill details
            filled_amount = float(filled_data.get('filled', 0))
            filled_price = float(filled_data.get('average', 0))
            fees = self._extract_fee_safely(filled_data)
            
            trade_id = order_info.get('trade_id')
            side = order_info['order_data']['side']
            
            logger.info(f"✅ Order {order_id} filled: {filled_amount:.8f} @ {filled_price:.8f}, fees: ${fees:.6f}, fill_time: {fill_time:.2f}s")
            
            # Update trade if this is an entry order
            if trade_id and side == 'buy':
                await self._update_trade_data(trade_id, {
                    'position_size': filled_amount,
                    'entry_price': filled_price,
                    'fees': fees,
                    'status': 'OPEN'
                })
                
        except Exception as e:
            logger.error(f"Error processing filled order {order_id}: {e}")

    async def _handle_cancelled_order(self, order_id: str, order_info: dict):
        """Handle a cancelled order"""
        try:
            # Update order tracker
            await order_tracker.update_order_status(order_id, 'cancelled')
            
            # Record performance metrics
            await order_tracker.record_order_result(order_id, 'cancelled')
            
            trade_id = order_info.get('trade_id')
            if trade_id:
                await self._update_trade_status(trade_id, 'FAILED', 'Order cancelled')
                
            logger.info(f"❌ Order {order_id} cancelled")
            
        except Exception as e:
            logger.error(f"Error handling cancelled order {order_id}: {e}")

    async def _record_order_to_database(self, order_data: Dict[str, Any]) -> bool:
        """Record order to database for tracking"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(f"{database_service_url}/api/v1/orders", json=order_data)
                if response.status_code == 200:
                    logger.info(f"✅ Order recorded to database: {order_data.get('order_id')}")
                    return True
                else:
                    logger.error(f"❌ Failed to record order to database: {response.status_code}")
                    return False
        except Exception as e:
            logger.error(f"❌ Error recording order to database: {str(e)}")
            return False

    async def _update_order_status(self, order_id: str, status: str, **kwargs):
        """Update order status in database"""
        try:
            update_data = {
                'status': status,
                'updated_at': datetime.utcnow().isoformat(),
                **kwargs
            }
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.put(f"{database_service_url}/api/v1/orders/{order_id}", json=update_data)
                if response.status_code == 200:
                    logger.info(f"✅ Order {order_id} status updated to {status}")
                    return True
                else:
                    logger.error(f"❌ Failed to update order status: {response.status_code}")
                    return False
        except Exception as e:
            logger.error(f"❌ Error updating order status: {str(e)}")
            return False

    async def _get_order_from_database(self, order_id: str) -> Optional[Dict[str, Any]]:
        """Get order details from database"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(f"{database_service_url}/api/v1/orders/{order_id}")
                if response.status_code == 200:
                    return response.json()
                else:
                    logger.warning(f"Order {order_id} not found in database")
                    return None
        except Exception as e:
            logger.error(f"❌ Error getting order from database: {str(e)}")
            return None
    
    async def _validate_position_for_order(self, exchange_name: str, pair: str, side: str, amount: float) -> bool:
        """Validate if we have sufficient balance for the order using position sync service"""
        try:
            # Get the required asset for this order
            required_asset = self._get_required_asset(pair, side)
            
            # Validate order with position sync service
            position_sync_url = os.getenv("POSITION_SYNC_SERVICE_URL", "http://position-sync-service:8009")
            
            order_data = {
                'exchange': exchange_name,
                'symbol': pair,
                'side': side,
                'amount': amount
            }
            
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(f"{position_sync_url}/validate-order", json=order_data)
                
                if response.status_code == 200:
                    validation_result = response.json()
                    is_valid = validation_result.get('valid', False)
                    available = validation_result.get('available_amount', 0)
                    required = validation_result.get('required_amount', amount)
                    
                    if is_valid:
                        logger.info(f"✅ Position validation passed: {required} {required_asset} <= {available} available on {exchange_name}")
                    else:
                        logger.warning(f"⚠️  Position validation failed: {required} {required_asset} > {available} available on {exchange_name}")
                    
                    return is_valid
                else:
                    logger.error(f"❌ Position validation service error: {response.status_code}")
                    # Fall back to allowing the order if service is unavailable
                    return True
                    
        except Exception as e:
            logger.error(f"❌ Error validating position: {e}")
            # Fall back to allowing the order if validation fails
            return True
    
    def _get_required_asset(self, pair: str, side: str) -> str:
        """Get the asset required for an order based on pair and side"""
        if '/' in pair:
            base_asset, quote_asset = pair.split('/')
        else:
            # Fallback parsing for pairs without slash
            if pair.endswith('USD'):
                base_asset = pair[:-3]
                quote_asset = 'USD'
            elif pair.endswith('USDC'):
                base_asset = pair[:-4]
                quote_asset = 'USDC'
            else:
                base_asset = pair
                quote_asset = 'USD'
        
        if side.lower() == 'buy':
            # For buy orders, we need quote currency (usually USD)
            return quote_asset
        else:
            # For sell orders, we need base currency (the asset being sold)
            return base_asset

# Global orchestrator
orchestrator = TradingOrchestrator()

# API Endpoints
@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint"""
    return HealthResponse(
        status="healthy",
        timestamp=datetime.utcnow(),
        version="1.0.0",
        trading_status=trading_status,
        cycle_count=cycle_count,
        active_trades=len(active_trades_dict)
    )

@app.get("/ready")
async def readiness_check():
    """Readiness check endpoint"""
    return {"status": "ready"}

@app.get("/live")
async def liveness_check():
    """Liveness check endpoint"""
    return {"status": "alive"}

# Trading Control Endpoints
@app.post("/api/v1/trading/start")
async def start_trading():
    """Start trading"""
    try:
        success = await orchestrator.start_trading()
        if success:
            return {"message": "Trading started successfully"}
        else:
            raise HTTPException(status_code=500, detail="Failed to start trading")
    except Exception as e:
        logger.error(f"Error starting trading: {str(e)}")
        logger.error(f"Exception type: {type(e).__name__}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/trading/stop")
async def stop_trading():
    """Stop trading"""
    try:
        success = await orchestrator.stop_trading()
        if success:
            return {"message": "Trading stopped successfully"}
        else:
            raise HTTPException(status_code=500, detail="Failed to stop trading")
    except Exception as e:
        logger.error(f"Error stopping trading: {str(e)}")
        logger.error(f"Exception type: {type(e).__name__}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/trading/emergency-stop")
async def emergency_stop():
    """Emergency stop all trading"""
    try:
        success = await orchestrator.emergency_stop()
        if success:
            return {"message": "Emergency stop executed successfully"}
        else:
            raise HTTPException(status_code=500, detail="Failed to execute emergency stop")
    except Exception as e:
        logger.error(f"Error executing emergency stop: {str(e)}")
        logger.error(f"Exception type: {type(e).__name__}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/trading/status")
async def get_trading_status():
    """Get trading status"""
    uptime = None
    if orchestrator.start_time:
        uptime = datetime.utcnow() - orchestrator.start_time
        
    return TradingStatus(
        status="running" if orchestrator.running else "stopped",
        cycle_count=orchestrator.cycle_count,
        active_trades=len(orchestrator.active_trades_dict),
        total_pnl=0.0,  # Would calculate from trades
        last_cycle=datetime.utcnow(),
        uptime=uptime or timedelta(0)
    )

# Trading Operations Endpoints
@app.post("/api/v1/trading/cycle/entry")
async def run_entry_cycle():
    """Manually run entry cycle"""
    try:
        await orchestrator._run_entry_cycle()
        return {"message": "Entry cycle completed"}
    except Exception as e:
        logger.error(f"Error running entry cycle: {str(e)}")
        logger.error(f"Exception type: {type(e).__name__}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/trading/cycle/exit")
async def run_exit_cycle():
    """Manually run exit cycle"""
    try:
        await orchestrator._run_exit_cycle()
        return {"message": "Exit cycle completed"}
    except Exception as e:
        logger.error(f"Error running exit cycle: {str(e)}")
        logger.error(f"Exception type: {type(e).__name__}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/trading/active-trades")
async def get_active_trades():
    """Get active trades"""
    return {
        "active_trades": list(active_trades_dict.values()),
        "count": len(active_trades_dict)
    }

@app.get("/api/v1/trading/cycle-stats")
async def get_cycle_stats():
    """Get trading cycle statistics"""
    return {
        "cycle_count": cycle_count,
        "trading_status": trading_status,
        "start_time": start_time.isoformat() if start_time else None,
        "uptime": str(datetime.utcnow() - start_time) if start_time else "0:00:00"
    }

# Risk Management Endpoints
@app.get("/api/v1/risk/limits")
async def get_risk_limits():
    """Get current risk limits"""
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(f"{config_service_url}/api/v1/config/trading")
            response.raise_for_status()
            trading_config = response.json()
        
        return RiskLimits(
            max_concurrent_trades=trading_config.get('max_concurrent_trades', 5),
            max_daily_trades=trading_config.get('max_daily_trades', 20),
            max_daily_loss=trading_config.get('max_daily_loss', 100.0),
            max_total_loss=trading_config.get('max_total_loss', 500.0),
            position_size_percentage=trading_config.get('position_size_percentage', 0.1)
        )
    except Exception as e:
        logger.error(f"Error getting risk limits: {str(e)}")
        logger.error(f"Exception type: {type(e).__name__}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/v1/risk/limits")
async def update_risk_limits(limits: RiskLimits):
    """Update risk limits"""
    try:
        # Update configuration
        async with httpx.AsyncClient(timeout=60.0) as client:
            updates = [
                {"path": "trading.max_concurrent_trades", "value": limits.max_concurrent_trades},
                {"path": "trading.max_daily_trades", "value": limits.max_daily_trades},
                {"path": "trading.max_daily_loss", "value": limits.max_daily_loss},
                {"path": "trading.max_total_loss", "value": limits.max_total_loss},
                {"path": "trading.position_size_percentage", "value": limits.position_size_percentage}
            ]
            
            for update in updates:
                response = await client.put(f"{config_service_url}/api/v1/config/update", json=update)
                response.raise_for_status()
        
        return {"message": "Risk limits updated successfully"}
    except Exception as e:
        logger.error(f"Error updating risk limits: {str(e)}")
        logger.error(f"Exception type: {type(e).__name__}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/risk/exposure")
async def get_risk_exposure():
    """Get current risk exposure"""
    try:
        total_exposure = 0
        for trade in active_trades_dict.values():
            total_exposure += abs(trade['position_size'])
        
        return {
            "total_exposure": total_exposure,
            "active_trades": len(active_trades_dict),
            "total_balance": sum((balance['total'] or 0) for balance in orchestrator.balances.values()),
            "available_balance": sum((balance['available'] or 0) for balance in orchestrator.balances.values()),
            "exposure_percentage": (total_exposure / sum((balance['total'] or 0) for balance in orchestrator.balances.values())) * 100 if sum((balance['total'] or 0) for balance in orchestrator.balances.values()) > 0 else 0
        }
    except Exception as e:
        logger.error(f"Error getting risk exposure: {str(e)}")
        logger.error(f"Exception type: {type(e).__name__}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/risk/check")
async def check_risk():
    """Perform risk check"""
    try:
        # Get current exposure
        exposure = await get_risk_exposure()
        
        # Get risk limits
        limits = await get_risk_limits()
        
        # Check risk violations
        violations = []
        
        if exposure['active_trades'] > limits.max_concurrent_trades:
            violations.append(f"Too many active trades: {exposure['active_trades']} > {limits.max_concurrent_trades}")
        
        if exposure['exposure_percentage'] > (limits.position_size_percentage * 100):
            violations.append(f"Exposure too high: {exposure['exposure_percentage']:.2f}%")
        
        return {
            "risk_check_passed": len(violations) == 0,
            "violations": violations,
            "exposure": exposure,
            "limits": limits
        }
    except Exception as e:
        logger.error(f"Error performing risk check: {str(e)}")
        logger.error(f"Exception type: {type(e).__name__}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))

# Pair Selection Endpoints
@app.get("/api/v1/pairs/selected")
async def get_selected_pairs():
    """Get selected pairs for all exchanges"""
    return {
        "pair_selections": orchestrator.pair_selections,
        "total_exchanges": len(orchestrator.pair_selections)
    }

@app.post("/api/v1/pairs/select")
async def select_pairs(exchange: str, pairs: List[str]):
    """Select pairs for an exchange"""
    try:
        orchestrator.pair_selections[exchange] = pairs
        
        # Save to database
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(f"{database_service_url}/api/v1/pairs/{exchange}", json={"pairs": pairs})
            response.raise_for_status()
        
        return {"message": f"Selected {len(pairs)} pairs for {exchange}"}
    except Exception as e:
        logger.error(f"Error selecting pairs for {exchange}: {str(e)}")
        logger.error(f"Exception type: {type(e).__name__}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/trading/verify-trades")
async def verify_all_trades_endpoint():
    """Verify all recent trades are properly recorded in database"""
    try:
        # Use the comprehensive verification function
        verification_results = await orchestrator.verify_all_trades()
        return verification_results
    except Exception as e:
        logger.error(f"Error in trade verification: {str(e)}")
        logger.error(f"Exception type: {type(e).__name__}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/pairs/candidates")
async def get_pair_candidates(exchange: str):
    """Get candidate pairs for an exchange"""
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(f"{exchange_service_url}/api/v1/market/pairs/{exchange}")
            response.raise_for_status()
            pairs_data = response.json()
        
        return {
            "exchange": exchange,
            "candidates": pairs_data['pairs'],
            "total": pairs_data['total']
        }
    except Exception as e:
        logger.error(f"Error getting pair candidates for {exchange}: {str(e)}")
        logger.error(f"Exception type: {type(e).__name__}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/orders/performance")
async def get_order_performance():
    """Get order performance metrics"""
    try:
        metrics = await hybrid_order_tracker.get_performance_metrics()
        return metrics
    except Exception as e:
        logger.error(f"Error getting order performance metrics: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/orders/websocket/status")
async def get_websocket_status():
    """Get WebSocket connection status for order tracking"""
    try:
        # Update connection status from exchange service
        await websocket_order_tracker.update_all_connections()
        return websocket_order_tracker.get_connection_status()
    except Exception as e:
        logger.error(f"Error getting WebSocket status: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.websocket("/ws/orders")
async def websocket_orders(websocket: WebSocket):
    """WebSocket endpoint for real-time order updates"""
    await websocket.accept()
    try:
        while True:
            # Send periodic updates about order status
            data = {
                "type": "order_update",
                "timestamp": datetime.utcnow().isoformat(),
                "performance_metrics": await hybrid_order_tracker.get_performance_metrics(),
                "websocket_status": websocket_order_tracker.get_connection_status()
            }
            await websocket.send_text(json.dumps(data))
            await asyncio.sleep(5)  # Update every 5 seconds
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        try:
            await websocket.close()
        except:
            pass

# Startup and shutdown events
@app.on_event("startup")
async def startup_event():
    """Initialize orchestrator and start trading loop on startup"""
    await orchestrator.initialize()
    await orchestrator.start_trading()
    await hybrid_order_tracker.start_background_tasks()

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    if orchestrator.running:
        await orchestrator.stop_trading()
    await hybrid_order_tracker.stop_background_tasks()
    logger.info("Orchestrator service shutdown complete")

class WebSocketOrderTracker:
    """Real-time WebSocket order tracking"""
    
    def __init__(self):
        self.connections = {}  # exchange -> websocket connection
        self.order_callbacks = {}  # order_id -> callback function
        self.websocket_enabled = False
        self.connection_status = {
            'binance': {'status': 'not_implemented', 'connected': False},
            'cryptocom': {'status': 'not_implemented', 'connected': False},
            'bybit': {'status': 'not_implemented', 'connected': False}
        }
    
    async def connect_exchange(self, exchange_name: str):
        """Establish WebSocket connection to exchange"""
        try:
            # Get WebSocket status from exchange service
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(f"{exchange_service_url}/api/v1/websocket/{exchange_name}/status")
                if response.status_code == 200:
                    status_data = response.json()
                    self.connection_status[exchange_name] = {
                        'status': 'connected' if status_data.get('connected') else 'disconnected',
                        'connected': status_data.get('connected', False),
                        'last_update': status_data.get('last_update', datetime.utcnow().isoformat()),
                        'registered_callbacks': status_data.get('registered_callbacks', 0)
                    }
                    logger.info(f"WebSocket connection for {exchange_name}: {status_data.get('connected', False)}")
                    return status_data.get('connected', False)
                else:
                    self.connection_status[exchange_name] = {
                        'status': 'error',
                        'connected': False,
                        'error': f"HTTP {response.status_code}",
                        'last_update': datetime.utcnow().isoformat()
                    }
                    return False
        except Exception as e:
            logger.error(f"Error connecting to {exchange_name} WebSocket: {e}")
            self.connection_status[exchange_name] = {
                'status': 'error',
                'connected': False,
                'error': str(e),
                'last_update': datetime.utcnow().isoformat()
            }
            return False
    
    async def update_all_connections(self):
        """Update connection status for all exchanges"""
        for exchange_name in ['binance', 'cryptocom', 'bybit']:
            await self.connect_exchange(exchange_name)
    
    async def watch_orders(self, exchange_name: str):
        """Watch orders in real-time via WebSocket"""
        try:
            # This will be implemented when exchange-specific WebSocket handlers are added
            logger.info(f"WebSocket order watching for {exchange_name} not yet implemented")
            return False
        except Exception as e:
            logger.error(f"Error watching orders for {exchange_name}: {e}")
            return False
    
    def register_order_callback(self, order_id: str, callback):
        """Register a callback for order status updates"""
        self.order_callbacks[order_id] = callback
        
        # Also register with exchange service if we know the exchange
        # This will be enhanced when we track exchange per order
        for exchange_name in ['binance', 'cryptocom', 'bybit']:
            try:
                asyncio.create_task(self._register_with_exchange(exchange_name, order_id, callback))
            except Exception as e:
                logger.warning(f"Could not register callback with {exchange_name}: {e}")
    
    def unregister_order_callback(self, order_id: str):
        """Unregister a callback for order status updates"""
        if order_id in self.order_callbacks:
            del self.order_callbacks[order_id]
    
    async def _register_with_exchange(self, exchange_name: str, order_id: str, callback):
        """Register callback with exchange service"""
        try:
            # This would be implemented when we have a proper callback registration endpoint
            # For now, we'll just log the registration attempt
            logger.info(f"Would register callback for {order_id} with {exchange_name}")
        except Exception as e:
            logger.error(f"Error registering callback with {exchange_name}: {e}")
    
    async def notify_order_update(self, order_id: str, status: str, data: dict = None):
        """Notify registered callbacks of order status updates"""
        if order_id in self.order_callbacks:
            try:
                await self.order_callbacks[order_id](status, data)
            except Exception as e:
                logger.error(f"Error in order callback for {order_id}: {e}")
    
    def get_connection_status(self) -> dict:
        """Get current WebSocket connection status"""
        return {
            'websocket_enabled': self.websocket_enabled,
            'connections': self.connection_status,
            'last_update': datetime.utcnow().isoformat()
        }

# Global WebSocket order tracker
websocket_order_tracker = WebSocketOrderTracker()

class HybridOrderTracker:
    """Hybrid order tracking system combining WebSocket and polling"""
    
    def __init__(self):
        self.pending_orders = {}  # order_id -> order_info
        self.filled_orders = {}   # order_id -> filled_data
        self.cancelled_orders = {} # order_id -> cancel_reason
        self.order_timeouts = {}  # order_id -> timeout_seconds
        self.order_performance = {
            'limit_orders': {'total': 0, 'filled': 0, 'cancelled': 0, 'timeout': 0},
            'market_orders': {'total': 0, 'filled': 0, 'failed': 0},
            'fill_times': [],
            'fee_savings': [],
            'success_rates': {}
        }
        self.performance_lock = asyncio.Lock()
        
        # Hybrid tracking
        self.websocket_enabled = True
        self.polling_enabled = True
        self.polling_interval = 30  # seconds
        self.websocket_timeout = 300  # seconds
        self.fallback_threshold = 60  # seconds without WebSocket update
        
        # Performance alerts
        self.performance_alerts = []
        self.alert_thresholds = {
            'low_success_rate': 0.7,  # 70%
            'high_fill_time': 120,    # 2 minutes
            'high_fee_ratio': 0.3,    # 30%
            'websocket_failure': 300  # 5 minutes
        }
        
        # Background tasks
        self.polling_task = None
        self.alert_monitor_task = None
        self.websocket_monitor_task = None
    
    async def start_background_tasks(self):
        """Start background monitoring tasks"""
        if self.polling_task is None:
            self.polling_task = asyncio.create_task(self._polling_monitor())
        if self.alert_monitor_task is None:
            self.alert_monitor_task = asyncio.create_task(self._alert_monitor())
        if self.websocket_monitor_task is None:
            self.websocket_monitor_task = asyncio.create_task(self._websocket_monitor())
        
        # Add sample data for demonstration - DISABLED FOR PRODUCTION
        # await self._add_sample_data()
    
    async def _add_sample_data(self):
        """Add sample data for demonstration purposes"""
        # Sample limit orders - more failures to trigger alerts
        await self.record_order_attempt('limit', {
            'id': 'sample_limit_1',
            'exchange': 'binance',
            'symbol': 'BTC/USDT',
            'side': 'buy',
            'amount': 0.001,
            'price': 45000
        })
        await self.record_order_result('sample_limit_1', 'filled', 45.2, 0.5)
        
        await self.record_order_attempt('limit', {
            'id': 'sample_limit_2',
            'exchange': 'cryptocom',
            'symbol': 'ETH/USDC',
            'side': 'sell',
            'amount': 0.01,
            'price': 2800
        })
        await self.record_order_result('sample_limit_2', 'timeout')
        
        await self.record_order_attempt('limit', {
            'id': 'sample_limit_3',
            'exchange': 'bybit',
            'symbol': 'SOL/USDT',
            'side': 'buy',
            'amount': 0.1,
            'price': 150
        })
        await self.record_order_result('sample_limit_3', 'cancelled')
        
        await self.record_order_attempt('limit', {
            'id': 'sample_limit_4',
            'exchange': 'binance',
            'symbol': 'XRP/USDT',
            'side': 'sell',
            'amount': 100,
            'price': 0.5
        })
        await self.record_order_result('sample_limit_4', 'timeout')
        
        # Sample market orders - more failures to trigger alerts
        await self.record_order_attempt('market', {
            'id': 'sample_market_1',
            'exchange': 'bybit',
            'symbol': 'CRO/USDT',
            'side': 'buy',
            'amount': 100
        })
        await self.record_order_result('sample_market_1', 'filled', 2.1, -0.3)
        
        await self.record_order_attempt('market', {
            'id': 'sample_market_2',
            'exchange': 'binance',
            'symbol': 'ADA/USDT',
            'side': 'sell',
            'amount': 500
        })
        await self.record_order_result('sample_market_2', 'failed')
        
        await self.record_order_attempt('market', {
            'id': 'sample_market_3',
            'exchange': 'cryptocom',
            'symbol': 'AAVE/USD',
            'side': 'buy',
            'amount': 2
        })
        await self.record_order_result('sample_market_3', 'failed')
        
        await self.record_order_attempt('market', {
            'id': 'sample_market_4',
            'exchange': 'bybit',
            'symbol': 'DOT/USDT',
            'side': 'sell',
            'amount': 50
        })
        await self.record_order_result('sample_market_4', 'failed')
        
        logger.info("Added sample data for demonstration")
    
    async def stop_background_tasks(self):
        """Stop background monitoring tasks"""
        if self.polling_task:
            self.polling_task.cancel()
            self.polling_task = None
        if self.alert_monitor_task:
            self.alert_monitor_task.cancel()
            self.alert_monitor_task = None
        if self.websocket_monitor_task:
            self.websocket_monitor_task.cancel()
            self.websocket_monitor_task = None
    
    async def _polling_monitor(self):
        """Background polling for order status updates"""
        while True:
            try:
                await asyncio.sleep(self.polling_interval)
                await self._check_pending_orders()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in polling monitor: {e}")
                await asyncio.sleep(10)
    
    async def _alert_monitor(self):
        """Background monitoring for performance alerts"""
        while True:
            try:
                await asyncio.sleep(60)  # Check every minute
                await self._generate_performance_alerts()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in alert monitor: {e}")
                await asyncio.sleep(10)
    
    async def _websocket_monitor(self):
        """Background monitoring for WebSocket health"""
        while True:
            try:
                await asyncio.sleep(30)  # Check every 30 seconds
                await self._check_websocket_health()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in WebSocket monitor: {e}")
                await asyncio.sleep(10)
    
    async def _check_pending_orders(self):
        """Check status of pending orders via polling"""
        current_time = time.time()
        orders_to_check = []
        
        for order_id, order_info in self.pending_orders.items():
            if current_time - order_info.get('created_at', 0) > self.fallback_threshold:
                orders_to_check.append(order_id)
        
        for order_id in orders_to_check:
            try:
                # Get order status from exchange service
                exchange = self.pending_orders[order_id].get('exchange', 'binance')
                symbol = self.pending_orders[order_id].get('symbol', '')
                
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.get(f"{exchange_service_url}/api/v1/trading/orders/{exchange}?symbol={symbol}")
                    if response.status_code == 200:
                        orders = response.json()
                        for order in orders:
                            if order.get('id') == order_id:
                                status = order.get('status', 'pending')
                                if status in ['filled', 'closed']:
                                    await self._process_filled_order(order_id, order)
                                elif status in ['canceled', 'cancelled']:
                                    await self._process_cancelled_order(order_id, 'polling_detected')
                                break
            except Exception as e:
                logger.error(f"Error checking order {order_id}: {e}")
    
    async def _generate_performance_alerts(self):
        """Generate performance alerts based on current metrics"""
        async with self.performance_lock:
            alerts = []
            
            # Check success rates
            limit_success_rate = 0
            if self.order_performance['limit_orders']['total'] > 0:
                limit_success_rate = self.order_performance['limit_orders']['filled'] / self.order_performance['limit_orders']['total']
            
            market_success_rate = 0
            if self.order_performance['market_orders']['total'] > 0:
                market_success_rate = self.order_performance['market_orders']['filled'] / self.order_performance['market_orders']['total']
            
            if limit_success_rate < self.alert_thresholds['low_success_rate']:
                alerts.append({
                    'type': 'warning',
                    'title': 'Low Limit Order Success Rate',
                    'message': f'Limit order success rate is {limit_success_rate:.1%}, below threshold of {self.alert_thresholds["low_success_rate"]:.1%}',
                    'timestamp': datetime.utcnow().isoformat()
                })
            
            if market_success_rate < self.alert_thresholds['low_success_rate']:
                alerts.append({
                    'type': 'warning',
                    'title': 'Low Market Order Success Rate',
                    'message': f'Market order success rate is {market_success_rate:.1%}, below threshold of {self.alert_thresholds["low_success_rate"]:.1%}',
                    'timestamp': datetime.utcnow().isoformat()
                })
            
            # Check fill times
            if self.order_performance['fill_times']:
                avg_fill_time = sum(self.order_performance['fill_times']) / len(self.order_performance['fill_times'])
                if avg_fill_time > self.alert_thresholds['high_fill_time']:
                    alerts.append({
                        'type': 'warning',
                        'title': 'High Average Fill Time',
                        'message': f'Average fill time is {avg_fill_time:.1f} seconds, above threshold of {self.alert_thresholds["high_fill_time"]} seconds',
                        'timestamp': datetime.utcnow().isoformat()
                    })
            
            # Check fee ratios
            if self.order_performance['fee_savings']:
                total_fee_savings = sum(self.order_performance['fee_savings'])
                if total_fee_savings < 0:  # Negative means high fees
                    fee_ratio = abs(total_fee_savings) / max(1, len(self.order_performance['fee_savings']))
                    if fee_ratio > self.alert_thresholds['high_fee_ratio']:
                        alerts.append({
                            'type': 'error',
                            'title': 'High Fee Ratio',
                            'message': f'Average fee ratio is {fee_ratio:.1%}, above threshold of {self.alert_thresholds["high_fee_ratio"]:.1%}',
                            'timestamp': datetime.utcnow().isoformat()
                        })
            
            # Check WebSocket health
            websocket_status = websocket_order_tracker.get_connection_status()
            disconnected_exchanges = []
            for exchange, status in websocket_status['connections'].items():
                if not status.get('connected', False):
                    disconnected_exchanges.append(exchange)
            
            if disconnected_exchanges:
                alerts.append({
                    'type': 'error',
                    'title': 'WebSocket Disconnections',
                    'message': f'WebSocket connections lost for: {", ".join(disconnected_exchanges)}',
                    'timestamp': datetime.utcnow().isoformat()
                })
            
            # Update alerts
            self.performance_alerts = alerts[-10:]  # Keep last 10 alerts
    
    async def _check_websocket_health(self):
        """Check WebSocket connection health"""
        try:
            # Update connection status
            await websocket_order_tracker.update_all_connections()
            
            # Check for reconnection needs
            websocket_status = websocket_order_tracker.get_connection_status()
            for exchange, status in websocket_status['connections'].items():
                if not status.get('connected', False):
                    logger.warning(f"WebSocket disconnected for {exchange}, attempting reconnection...")
                    await websocket_order_tracker.connect_exchange(exchange)
        except Exception as e:
            logger.error(f"Error checking WebSocket health: {e}")
    
    async def record_order_attempt(self, order_type: str, order_data: dict):
        """Record initial attempt for limit or market orders"""
        async with self.performance_lock:
            order_id = order_data.get('id', str(uuid.uuid4()))
            order_data['created_at'] = time.time()
            order_data['order_type'] = order_type
            
            if order_type == 'limit':
                self.order_performance['limit_orders']['total'] += 1
                self.pending_orders[order_id] = order_data
            else:  # market
                self.order_performance['market_orders']['total'] += 1
                self.pending_orders[order_id] = order_data
            
            logger.info(f"Recorded {order_type} order attempt: {order_id}")
    
    async def record_order_result(self, order_id: str, result: str, fill_time: float = None, fee_saved: float = None):
        """Record the outcome of an order"""
        async with self.performance_lock:
            if order_id in self.pending_orders:
                order_info = self.pending_orders[order_id]
                order_type = order_info.get('order_type', 'unknown')
                
                if result == 'filled':
                    if order_type == 'limit':
                        self.order_performance['limit_orders']['filled'] += 1
                    else:
                        self.order_performance['market_orders']['filled'] += 1
                    
                    if fill_time:
                        self.order_performance['fill_times'].append(fill_time)
                    if fee_saved:
                        self.order_performance['fee_savings'].append(fee_saved)
                    
                    self.filled_orders[order_id] = order_info
                    del self.pending_orders[order_id]
                    
                elif result == 'cancelled':
                    if order_type == 'limit':
                        self.order_performance['limit_orders']['cancelled'] += 1
                    self.cancelled_orders[order_id] = order_info
                    del self.pending_orders[order_id]
                    
                elif result == 'timeout':
                    if order_type == 'limit':
                        self.order_performance['limit_orders']['timeout'] += 1
                    self.order_timeouts[order_id] = order_info
                    del self.pending_orders[order_id]
                    
                elif result == 'failed':
                    self.order_performance['market_orders']['failed'] += 1
                    del self.pending_orders[order_id]
                
                logger.info(f"Recorded order result: {order_id} -> {result}")
    
    async def get_performance_metrics(self) -> dict:
        """Get comprehensive performance metrics"""
        async with self.performance_lock:
            # Calculate success rates
            limit_success_rate = 0
            if self.order_performance['limit_orders']['total'] > 0:
                limit_success_rate = self.order_performance['limit_orders']['filled'] / self.order_performance['limit_orders']['total']
            
            market_success_rate = 0
            if self.order_performance['market_orders']['total'] > 0:
                market_success_rate = self.order_performance['market_orders']['filled'] / self.order_performance['market_orders']['total']
            
            # Calculate averages
            avg_fill_time = 0
            if self.order_performance['fill_times']:
                avg_fill_time = sum(self.order_performance['fill_times']) / len(self.order_performance['fill_times'])
            
            total_fee_savings = sum(self.order_performance['fee_savings'])
            
            return {
                'order_counts': {
                    'limit_orders': self.order_performance['limit_orders'],
                    'market_orders': self.order_performance['market_orders']
                },
                'success_rates': {
                    'limit_orders': limit_success_rate,
                    'market_orders': market_success_rate,
                    'overall': (self.order_performance['limit_orders']['filled'] + self.order_performance['market_orders']['filled']) / 
                              max(1, self.order_performance['limit_orders']['total'] + self.order_performance['market_orders']['total'])
                },
                'performance_metrics': {
                    'avg_fill_time': avg_fill_time,
                    'total_fee_savings': total_fee_savings,
                    'total_orders': self.order_performance['limit_orders']['total'] + self.order_performance['market_orders']['total']
                },
                'pending_orders': len(self.pending_orders),
                'alerts': self.performance_alerts,
                'last_update': datetime.utcnow().isoformat()
            }
    
    async def _process_filled_order(self, order_id: str, order_data: dict):
        """Process a filled order"""
        fill_time = time.time() - self.pending_orders[order_id].get('created_at', time.time())
        fee_saved = 0  # Calculate based on order type and fees
        
        await self.record_order_result(order_id, 'filled', fill_time, fee_saved)
    
    async def _process_cancelled_order(self, order_id: str, reason: str):
        """Process a cancelled order"""
        await self.record_order_result(order_id, 'cancelled')

# Global hybrid order tracker
hybrid_order_tracker = HybridOrderTracker()

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8005,
        reload=True,
        log_level="info"
    ) 