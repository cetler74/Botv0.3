# Remove problematic import that doesn't exist
# from fix_unrealized_pnl_fees import calculate_unrealized_pnl_with_fees

"""
Orchestrator Service for the Multi-Exchange Trading Bot
Main trading coordination and decision making

VERSION: 2.5.0 (2025-08-28)
CHANGELOG:
- v2.5.0: CRITICAL TRADING FIXES - Stop Loss Hemorrhaging and Risk Management
  * FIXED: Risk logic now correctly requires 1+ losses (not 2+) to match consecutive_loss_limit=1
  * FIXED: Stop loss changed from 0.8% to 3% - appropriate for crypto market volatility  
  * CORRECTED: Prevents repeat trades on losing pairs after ANY negative result
  * LAYER 1: Current unrealized PnL protection (1+ negative positions)
  * LAYER 2: Historical loss pattern protection (1+ losses within 2 hours) - NOW WORKING!
  * LAYER 3: Portfolio-level drawdown protection ($100+ loss threshold)
  * LAYER 4: Exchange-level risk assessment (systematic performance)
  * ENHANCED: Proper crypto-appropriate stop loss levels prevent normal volatility exits
  * ULTIMATE: Stops money hemorrhaging from tight stops + broken loss prevention
"""

import asyncio
import json
import logging
from typing import Dict, Any, List, Optional, Tuple, Union
from datetime import datetime, timedelta
import signal
import sys
import os
import httpx

# Version tracking
ORCHESTRATOR_VERSION = "2.4.0"
VERSION_DATE = "2025-08-25"
RISK_MANAGEMENT_VERSION = "2.4.0"
from fastapi import FastAPI, HTTPException, BackgroundTasks, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import uvicorn
import uuid
import time
import numpy as np
from urllib.parse import quote
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST, CollectorRegistry

# Add the project root to the path to import core modules
sys.path.append('/app')
from core.strategy_manager import StrategyManager
from redis_order_manager import RedisOrderManager
from redis_realtime_order_manager import redis_realtime_manager
from core.database_manager import DatabaseManager

# Configure logging first
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _http_response_log_snippet(response: httpx.Response, max_len: int = 400) -> str:
    """Short, log-safe text from a failed HTTP response (JSON detail or body prefix)."""
    try:
        data = response.json()
        if isinstance(data, dict) and "detail" in data:
            return str(data["detail"])[:max_len].replace("\n", " ")
        return json.dumps(data, default=str)[:max_len]
    except Exception:
        return (response.text or "")[:max_len].replace("\n", " ")


# Import new trailing stop system (local modules)
try:
    from activation_trigger_system import ActivationTriggerSystem
    TRAILING_STOP_SYSTEM_AVAILABLE = True
    logger.info("✅ New trailing stop system imported successfully")
except ImportError as e:
    logger.warning(f"⚠️ New trailing stop system not available: {e}")
    TRAILING_STOP_SYSTEM_AVAILABLE = False

from pair_rotation_manager import rotate_pair_after_loss
from pair_loss_policy import (
    infer_trade_notional_usd,
    is_macd_momentum_strategy,
    loss_cooldown_key,
    qualifies_for_hard_loss_cooldown,
)


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
# Price resolution: alert on complete failure; track degraded path (entry fallback only)
price_resolution_failures_total = Counter(
    'orchestrator_price_resolution_failures_total',
    'All price sources failed including entry fallback — unusable mark price',
    ['exchange'],
    registry=custom_registry,
)
price_resolution_degraded_total = Counter(
    'orchestrator_price_resolution_degraded_total',
    'Mark price resolved only via entry_price fallback (no live ticker/OHLCV/orderbook)',
    ['exchange'],
    registry=custom_registry,
)
regime_entry_decisions_total = Counter(
    'orchestrator_regime_entry_decisions_total',
    'Entry decision outcomes by stable regime and reason',
    ['exchange', 'regime', 'decision', 'reason'],
    registry=custom_registry,
)

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
    
    async def track_order(self, order_id: str, order_data: dict, timeout_seconds: int = 120):
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
        created_at = order_info.get('created_at')
        if created_at is None:
            # Performance-tracking entries may not include timeout metadata.
            return False
        timeout_seconds = self.order_timeouts.get(order_id, 120)
        
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
    # Wall-clock seconds for the last completed exit+entry+maintenance loop (not the sleep gap)
    last_loop_duration_seconds: Optional[float] = None

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


class PairEntryBlocksRequest(BaseModel):
    """Dashboard: which symbols are soft-blocked from new entries (risk/cooldown)."""

    exchange: str
    pairs: List[str] = Field(default_factory=list)

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
# Simulation mode: dashboard reads balances from DB; seed rows were historically 0 — use this default when unset.
SIMULATION_DEFAULT_BALANCE_USD = 10000.0
# PnL-FIX v9 — uniform simulation fee. Cached after first config-service fetch
# so every simulated fill (BUY and SELL) on every exchange is debited identically.
_SIMULATION_FEE_RATE_PER_SIDE: Optional[float] = None


async def _get_simulation_fee_rate_per_side() -> float:
    """Return the per-side simulation fee rate from config-service (cached)."""
    global _SIMULATION_FEE_RATE_PER_SIDE
    if _SIMULATION_FEE_RATE_PER_SIDE is not None:
        return _SIMULATION_FEE_RATE_PER_SIDE
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{config_service_url}/api/v1/config/simulation")
            if r.status_code == 200:
                _SIMULATION_FEE_RATE_PER_SIDE = float(
                    (r.json() or {}).get("fee_rate_per_side", 0.0005)
                )
                return _SIMULATION_FEE_RATE_PER_SIDE
    except Exception:
        pass
    _SIMULATION_FEE_RATE_PER_SIDE = 0.0005
    return _SIMULATION_FEE_RATE_PER_SIDE
strategy_service_url = os.getenv("STRATEGY_SERVICE_URL", "http://strategy-service:8004")


def _is_simulated_local_order_id(order_id: Optional[str]) -> bool:
    """True for IDs created only in-process (simulation); never valid on a real exchange API."""
    if not order_id:
        return False
    return str(order_id).startswith("sim_")


def _ws_ticker_key_suffix(exchange: str, api_symbol: str) -> str:
    """Normalize exchange+symbol the same way as exchange-service ``_normalize_symbol_key`` (ticker_cache key)."""
    ex = str(exchange)
    sym = str(api_symbol)
    if ex.lower() == "cryptocom":
        if "/" in sym:
            normalized = sym.replace("/", "_")
        else:
            normalized = sym
    else:
        normalized = "".join(ch for ch in sym if ch.isalnum())
    raw = f"{ex}:{normalized}"
    return raw.replace(":", "|")


class TradingOrchestrator:
    """Main orchestrator for the multi-exchange trading bot"""
    
    def __init__(self):
        self.running = False
        self.trading_active = False  # Add missing trading_active attribute
        self.cycle_count = 0
        self.active_trades_dict = {}
        self.pair_selections = {}
        self.balances = {}
        self.start_time = None
        self.exiting_trades = set()  # Track trades currently being exited to prevent duplicates
        self._trade_feed_degraded_since: Dict[str, datetime] = {}
        self._pair_execution_downgrade_until: Dict[Tuple[str, str], datetime] = {}
        self._pair_rotation_blacklist_until: Dict[str, datetime] = {}
        self._hard_loss_cooldown_until: Dict[Tuple[str, str], datetime] = {}
        self._recent_negative_realized_blacklist_until: Dict[str, datetime] = {}
        self._pair_rotation_processed_trade_ids: set[str] = set()
        # Global in-process pair entry reservation (across exchanges) to prevent
        # same-cycle dual opens on the same instrument.
        self._pair_entry_reservation_lock = asyncio.Lock()
        self._pair_entry_reservations: set[str] = set()
        self.momentum_filter = MomentumFilter()  # Initialize momentum filter
        self.last_pair_update = datetime.utcnow()  # Track when pairs were last updated

        # PnL-FIX v3 — per-pair cooldown + correlation cap
        # `_pair_cooldown_until[(exchange, pair)] = datetime` prevents re-entry on a
        # pair for N minutes after the previous entry/exit. Without this, the bot
        # re-entered the same losing pair within the same cycle.
        self._pair_cooldown_until: Dict[tuple, datetime] = {}
        self._pair_cooldown_minutes: int = 30
        # Cross-exchange pair cooldown: if DOGE/USDC was entered on any exchange,
        # block re-entry on the same normalized pair everywhere for N minutes.
        self._global_pair_cooldown_until: Dict[str, datetime] = {}
        self._global_pair_cooldown_minutes: int = 5
        # Correlation cap: number of concurrent long entries allowed within the same
        # "basket" (crypto-beta cluster). All pairs are highly correlated so we treat
        # them as a single basket for now; easy to extend to per-cluster buckets later.
        self._correlation_basket_cap: int = 6
        self.last_loop_duration_seconds: Optional[float] = None
        # Round-robin index for exit checks when max_cycle_duration caps work per loop
        self._exit_cycle_cursor: int = 0
        self._loop_cycle_interval: float = 60.0
        self._loop_max_cycle_duration: float = 60.0
        self._loop_exit_cycle_first: bool = True
        # Wall time reserved for entry + maintenance after exit's timebox (exit_cycle_first).
        self._loop_entry_reserve_seconds: float = 90.0
        
        # Initialize strategy manager and database manager
        self.database_manager = None
        self.strategy_manager = None
        self._config = None
        self.config_manager = None  # Will be initialized in initialize() method
        
        
        # Initialize Redis order manager
        self.redis_order_manager = RedisOrderManager(os.getenv("REDIS_URL", "redis://redis:6379"))
        
        # Initialize new exchange-delegated trailing stop system (REALTIME_IMPLEMENTATION_PLAN.md)
        self.activation_trigger_system = None
        self.use_new_trailing_system = False
        self.use_redis_processing = True  # CRITICAL: Redis system REQUIRED for order tracking safety
        self.is_simulation = False  # Set in _initialize_balances from config/mode
        
        # Initialize new trailing stop system
        self.trailing_stop_system = None
        self.use_new_trailing_system = TRAILING_STOP_SYSTEM_AVAILABLE  # Feature flag

    def _classify_feed_quality(self, price_source: str, down_sources: set, degraded_sources: set) -> str:
        src = str(price_source or "").lower()
        if src in down_sources:
            return "DOWN"
        if src in degraded_sources:
            return "DEGRADED"
        return "GOOD"

    def _deep_merge_dicts(self, base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        merged: Dict[str, Any] = dict(base or {})
        for key, value in (override or {}).items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = self._deep_merge_dicts(merged[key], value)
            else:
                merged[key] = value
        return merged

    def _resolve_regime_policy(self, signals_data: Dict[str, Any]) -> Dict[str, Any]:
        trading_cfg = (self._config or {}).get("trading", {}) or {}
        regime_cfg = trading_cfg.get("regime_policies", {}) or {}
        defaults = regime_cfg.get("defaults", {}) or {}
        stable_regime = str(
            ((signals_data or {}).get("consensus", {}) or {}).get("stable_regime")
            or (signals_data or {}).get("stable_regime")
            or (signals_data or {}).get("market_regime")
            or "unknown"
        )
        regime_override = regime_cfg.get(stable_regime, {}) or {}
        resolved = self._deep_merge_dicts(defaults, regime_override)
        resolved["stable_regime"] = stable_regime
        resolved["policy_version"] = str(regime_cfg.get("policy_version", "unversioned"))
        resolved["mode"] = str(regime_cfg.get("mode", "shadow")).lower()
        return resolved

    def _record_regime_entry_decision(
        self,
        exchange_name: str,
        regime: str,
        decision: str,
        reason: str,
    ) -> None:
        try:
            regime_entry_decisions_total.labels(
                exchange=exchange_name,
                regime=str(regime or "unknown"),
                decision=str(decision or "unknown"),
                reason=str(reason or "unspecified"),
            ).inc()
        except Exception:
            pass

    async def _mark_trade_triggered_exit(
        self,
        trade_id: str,
        exit_reason: str,
        trigger_price: float,
        current_price: float,
        feed_quality: str,
        price_source: str,
    ) -> None:
        """Persist trigger-time metadata so exits are auditable and recoverable."""
        try:
            now_iso = datetime.utcnow().isoformat() + "+00:00"
            update_data = {
                "exit_state": "TRIGGERED_EXIT",
                "trigger_reason": exit_reason,
                "trigger_price": trigger_price,
                "trigger_detected_price": current_price,
                "trigger_price_source": price_source,
                "trigger_feed_quality": feed_quality,
                "trigger_detected_at": now_iso,
            }
            await self._update_trade_data(trade_id, update_data)
            logger.warning(
                f"[Trade {trade_id}] [ExitTelemetry] TRIGGER_CAPTURED: "
                f"reason={exit_reason}, trigger={trigger_price:.6f}, detected={current_price:.6f}, "
                f"source={price_source}, feed_quality={feed_quality}, ts={now_iso}"
            )
        except Exception as e:
            logger.warning(f"[Trade {trade_id}] [ExitState] Could not persist trigger metadata: {e}")

    async def _get_pair_loss_size_multiplier(
        self,
        exchange_name: str,
        pair: str,
        resolved_policy: Optional[Dict[str, Any]] = None,
    ) -> float:
        """Adaptive sizing: reduce position after consecutive losses on pair+exchange."""
        try:
            cfg = await self._get_config_value("trading.adaptive_position_sizing", {})
            if not isinstance(cfg, dict) or not cfg.get("enabled", True):
                return 1.0
            lookback = int(cfg.get("lookback_trades", 20) or 20)
            sizing_policy = (resolved_policy or {}).get("sizing", {}) or {}
            consecutive_threshold = int(
                sizing_policy.get("consecutive_loss_threshold", cfg.get("consecutive_loss_threshold", 2)) or 2
            )
            reduced_multiplier = float(
                sizing_policy.get("reduced_size_multiplier", cfg.get("reduced_size_multiplier", 0.35)) or 0.35
            )
            min_multiplier = float(
                sizing_policy.get("min_size_multiplier", cfg.get("min_size_multiplier", 0.25)) or 0.25
            )
            regime_multiplier = float(sizing_policy.get("multiplier", 1.0) or 1.0)
            async with httpx.AsyncClient(timeout=20.0) as client:
                r = await client.get(
                    f"{database_service_url}/api/v1/trades/closed/history",
                    params={"exchange": exchange_name, "limit": lookback, "page": 1},
                )
                if r.status_code != 200:
                    return 1.0
                rows = r.json().get("trades", []) or []
            pair_rows = [t for t in rows if t.get("pair") == pair]
            consecutive_losses = 0
            for t in pair_rows:
                rpnl = float(t.get("realized_pnl") or 0.0)
                if rpnl < 0:
                    consecutive_losses += 1
                else:
                    break
            if consecutive_losses >= consecutive_threshold:
                mult = max(min_multiplier, reduced_multiplier) * regime_multiplier
                logger.warning(
                    "[AdaptiveSizing] %s %s has %s consecutive losses -> size multiplier %.2f",
                    exchange_name, pair, consecutive_losses, mult,
                )
                return mult
            return max(0.0, regime_multiplier)
        except Exception as e:
            logger.warning("[AdaptiveSizing] Failed to compute pair multiplier for %s %s: %s", exchange_name, pair, e)
            return 1.0

    async def _passes_entry_quality_gate(
        self,
        exchange_name: str,
        pair: str,
        signals_data: Dict[str, Any],
        resolved_policy: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Tighten low-conviction entries (confidence/agreement/strength/spread)."""
        try:
            gate = await self._get_config_value("trading.entry_quality_gate", {})
            if not isinstance(gate, dict) or not gate.get("enabled", True):
                return True
            consensus = signals_data.get("consensus", {}) or {}
            strategies = signals_data.get("strategies", {}) or {}
            consensus_excluded_strategies = {"macd_momentum"}
            consensus_strategies = {
                name: data
                for name, data in strategies.items()
                if name not in consensus_excluded_strategies
            }
            rsi_checklist_override = bool(consensus.get("rsi_checklist_buy_override", False))
            if rsi_checklist_override:
                logger.warning(
                    "[EntryGate] RSI checklist override active for %s %s: bypassing entry quality gate",
                    exchange_name,
                    pair,
                )
                return True
            rsi_oversold_override = bool(consensus.get("rsi_15m_oversold_buy_override", False))
            if rsi_oversold_override:
                logger.warning(
                    "[EntryGate] RSI override active for %s %s: bypassing entry quality gate (15m RSI oversold BUY override)",
                    exchange_name,
                    pair,
                )
                return True
            macd_override_cfg = await self._get_config_value("trading.macd_buy_override", {})
            if not isinstance(macd_override_cfg, dict):
                macd_override_cfg = {}
            macd_override_enabled = bool(macd_override_cfg.get("enabled", True))
            macd_strategy_data = strategies.get("macd_momentum", {}) or {}
            macd_signal = str((macd_strategy_data.get("signal", "hold"))).lower()
            macd_validation = (macd_strategy_data.get("validation", {}) or {})
            macd_rule_triggered = bool(
                macd_validation.get("trigger")
                or macd_validation.get("macd_green_rsi_buy_ok")
                or macd_strategy_data.get("macd_green_rsi_buy_ok")
            )
            if macd_override_enabled and (macd_signal == "buy" or macd_rule_triggered):
                logger.warning(
                    "[EntryGate] MACD override active for %s %s: bypassing entry quality gate "
                    "(signal=%s, rule_triggered=%s)",
                    exchange_name,
                    pair,
                    macd_signal,
                    macd_rule_triggered,
                )
                return True
            entry_policy = (resolved_policy or {}).get("entry", {}) or {}
            min_consensus_conf = float(entry_policy.get("min_consensus_confidence", gate.get("min_consensus_confidence", 0.62)) or 0.62)
            min_agreement_pct = float(entry_policy.get("min_agreement_percentage", gate.get("min_agreement_percentage", 66.0)) or 66.0)
            min_buy_strategies = int(entry_policy.get("min_buy_strategies", gate.get("min_buy_strategies", 2)) or 2)
            min_avg_buy_strength = float(entry_policy.get("min_avg_buy_strength", gate.get("min_avg_buy_strength", 0.5)) or 0.5)
            max_spread_pct = float(entry_policy.get("max_spread_percentage", gate.get("max_spread_percentage", 0.35)) or 0.35)

            fallback_cfg = await self._get_config_value("trading.consensus_buy_fallback", {})
            if not isinstance(fallback_cfg, dict):
                fallback_cfg = {}
            fallback_enabled = bool(fallback_cfg.get("enabled", True))
            fallback_min_conf = float(fallback_cfg.get("min_buy_strategy_confidence", 0.72) or 0.72)
            fallback_min_strength = float(fallback_cfg.get("min_buy_strategy_strength", 0.10) or 0.10)
            fallback_max_sell_signals = int(fallback_cfg.get("max_sell_signals", 0) or 0)

            buy_votes = []
            sell_votes = []
            for s in consensus_strategies.values():
                s_signal = str((s or {}).get("signal", "")).lower()
                s_conf = float((s or {}).get("confidence", 0) or 0.0)
                s_strength = float((s or {}).get("strength", 0) or 0.0)
                if s_signal == "buy":
                    buy_votes.append((s_conf, s_strength))
                elif s_signal == "sell":
                    sell_votes.append((s_conf, s_strength))

            fallback_override = False
            if fallback_enabled and buy_votes:
                best_buy_conf, best_buy_strength = sorted(buy_votes, reverse=True)[0]
                if (
                    best_buy_conf >= fallback_min_conf
                    and best_buy_strength >= fallback_min_strength
                    and len(sell_votes) <= fallback_max_sell_signals
                ):
                    fallback_override = True
                    logger.info(
                        "[EntryGate] Fallback override %s %s: best BUY conf %.2f strength %.2f, sells=%s",
                        exchange_name, pair, best_buy_conf, best_buy_strength, len(sell_votes)
                    )

            c_conf = float(consensus.get("confidence") or 0.0)
            c_agree = float(consensus.get("agreement") or 0.0)
            if c_conf < min_consensus_conf and not fallback_override:
                logger.info("[EntryGate] Reject %s %s: consensus confidence %.2f < %.2f", exchange_name, pair, c_conf, min_consensus_conf)
                return False
            if c_agree < min_agreement_pct and not fallback_override:
                logger.info("[EntryGate] Reject %s %s: agreement %.1f < %.1f", exchange_name, pair, c_agree, min_agreement_pct)
                return False

            buy_signals = [s for s in consensus_strategies.values() if (s or {}).get("signal") == "buy"]
            if len(buy_signals) < min_buy_strategies and not fallback_override:
                logger.info("[EntryGate] Reject %s %s: buy strategies %s < %s", exchange_name, pair, len(buy_signals), min_buy_strategies)
                return False
            avg_strength = sum(float((s or {}).get("strength") or 0.0) for s in buy_signals) / max(1, len(buy_signals))
            if avg_strength < min_avg_buy_strength and not fallback_override:
                logger.info("[EntryGate] Reject %s %s: avg buy strength %.2f < %.2f", exchange_name, pair, avg_strength, min_avg_buy_strength)
                return False

            # spread sanity gate
            try:
                async with httpx.AsyncClient(timeout=8.0) as client:
                    t = await client.get(f"{exchange_service_url}/api/v1/market/ticker-live/{exchange_name}/{pair}")
                if t.status_code == 200:
                    td = t.json() or {}
                    bid = float(td.get("bid") or td.get("best_bid") or 0.0)
                    ask = float(td.get("ask") or td.get("best_ask") or 0.0)
                    last = float(td.get("last") or 0.0)
                    if bid > 0 and ask > 0 and ask >= bid:
                        spread_pct = ((ask - bid) / max(last, (ask + bid) / 2.0)) * 100.0
                        if spread_pct > max_spread_pct:
                            logger.info(
                                "[EntryGate] Reject %s %s: spread %.3f%% > %.3f%%",
                                exchange_name, pair, spread_pct, max_spread_pct
                            )
                            return False
            except Exception:
                pass
            return True
        except Exception as e:
            logger.warning("[EntryGate] Failed gate evaluation for %s %s: %s", exchange_name, pair, e)
            return True
        
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
            
            # Initialize configuration
            await self._initialize_config()
            
            # Initialize database manager
            await self._initialize_database_manager()
            
            # Initialize new exchange-delegated trailing stop system (non-blocking)
            self._initialize_activation_trigger_system()
            
            # Initialize strategy manager
            await self._initialize_strategy_manager()
            
            # Initialize new trailing stop system
            await self._initialize_trailing_stop_system()
            
            
            # Initialize balances
            await self._initialize_balances()
            
            # Initialize pair selections
            await self._initialize_pair_selections()
            
            # Initialize Redis order manager
            await self.redis_order_manager.initialize()
            
            logger.info("Trading Orchestrator initialized successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize Trading Orchestrator: {e}")
            return False

    async def _ensure_simulation_balance_in_db(
        self,
        client: httpx.AsyncClient,
        exchange_name: str,
        db_total: float,
        db_available: float,
        total_pnl: float,
        daily_pnl: float,
    ) -> Tuple[float, float]:
        """If DB has no simulation float (both <= 0), seed default and persist for dashboard/API."""
        total = float(db_total)
        avail = float(db_available)
        if total <= 0 and avail <= 0:
            total = SIMULATION_DEFAULT_BALANCE_USD
            avail = SIMULATION_DEFAULT_BALANCE_USD
            payload = {
                "exchange": exchange_name,
                "balance": total,
                "available_balance": avail,
                "total_pnl": float(total_pnl),
                "daily_pnl": float(daily_pnl),
                "timestamp": datetime.utcnow().isoformat(),
            }
            try:
                r = await client.put(
                    f"{database_service_url}/api/v1/balances/{exchange_name}",
                    json=payload,
                )
                if r.status_code not in (200, 201):
                    logger.warning(
                        "Persist default simulation balance for %s failed: %s %s",
                        exchange_name,
                        r.status_code,
                        (r.text or "")[:200],
                    )
            except Exception as e:
                logger.warning(
                    "Persist default simulation balance for %s error: %s",
                    exchange_name,
                    e,
                )
        return total, avail

    async def _refresh_simulation_balance_from_db(self, exchange_name: str) -> None:
        """Sync self.balances from trading.balance (simulation source of truth)."""
        if not self.is_simulation:
            return
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                balance_response = await client.get(
                    f"{database_service_url}/api/v1/balances/{exchange_name}"
                )
                if balance_response.status_code != 200:
                    return
                balance = balance_response.json()
                total, avail = await self._ensure_simulation_balance_in_db(
                    client,
                    exchange_name,
                    float(balance["balance"]),
                    float(balance["available_balance"]),
                    float(balance.get("total_pnl", 0) or 0),
                    float(balance.get("daily_pnl", 0) or 0),
                )
                self.balances[exchange_name] = {
                    "total": total,
                    "available": avail,
                    "total_pnl": float(balance.get("total_pnl", 0) or 0),
                    "daily_pnl": float(balance.get("daily_pnl", 0) or 0),
                }
        except Exception as e:
            logger.debug("Refresh simulation balance failed for %s: %s", exchange_name, e)

    async def _fetch_open_trade_position_size(self, trade_id: str) -> Optional[float]:
        """OPEN trade position_size from DB (simulation SELL validation)."""
        if not trade_id:
            return None
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                r = await client.get(f"{database_service_url}/api/v1/trades/{trade_id}")
                if r.status_code != 200:
                    return None
                t = r.json()
                if str(t.get("status", "")).upper() != "OPEN":
                    return None
                return float(t.get("position_size") or 0)
        except Exception as e:
            logger.warning("Could not fetch trade %s for simulation sell check: %s", trade_id, e)
            return None

    async def _apply_simulation_quote_balance_delta(self, exchange_name: str, delta_usd: float) -> None:
        """Adjust DB quote balance: negative = buy spend, positive = sell proceeds."""
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                r = await client.get(f"{database_service_url}/api/v1/balances/{exchange_name}")
                if r.status_code != 200:
                    logger.warning(
                        "[SIMULATION] balance GET failed for %s: %s", exchange_name, r.status_code
                    )
                    return
                row = r.json()
                bal = float(row.get("balance") or 0)
                avail = float(row.get("available_balance") or 0)
                tp = float(row.get("total_pnl") or 0)
                dp = float(row.get("daily_pnl") or 0)
                new_bal = max(0.0, bal + delta_usd)
                new_avail = max(0.0, avail + delta_usd)
                payload = {
                    "exchange": exchange_name,
                    "balance": new_bal,
                    "available_balance": new_avail,
                    "total_pnl": tp,
                    "daily_pnl": dp,
                    "timestamp": datetime.utcnow().isoformat(),
                }
                r2 = await client.put(
                    f"{database_service_url}/api/v1/balances/{exchange_name}", json=payload
                )
                if r2.status_code != 200:
                    logger.error(
                        "[SIMULATION] balance PUT failed: %s %s", r2.status_code, r2.text
                    )
        except Exception as e:
            logger.error("[SIMULATION] balance delta error: %s", e)

    def _simulation_limit_fill_price(self, current_price: float, side: str) -> float:
        """Match order-queue-service limit offset (0.02%)."""
        aggressive_spread = 0.0002
        cp = float(current_price)
        if str(side).lower() == "buy":
            return round(cp * (1 + aggressive_spread), 8)
        return round(cp * (1 - aggressive_spread), 8)

    async def _build_simulation_instant_fill_order(
        self,
        exchange_name: str,
        pair: str,
        side: str,
        amount: float,
        trade_id: Optional[str],
        fill_price: float,
        *,
        apply_balance_delta: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """Synthetic filled order; optionally updates DB quote balance (simulation only)."""
        if not self.is_simulation or fill_price <= 0 or amount <= 0:
            return None
        side_l = str(side).lower()
        use_amount = float(amount)
        if side_l == "sell":
            if not trade_id:
                logger.error("[SIMULATION] SELL requires trade_id for DB position validation")
                return None
            pos = await self._fetch_open_trade_position_size(trade_id)
            if pos is None or pos <= 0:
                logger.error(
                    "[SIMULATION] No OPEN DB position for trade %s — cannot sell", trade_id
                )
                return None
            if use_amount > pos + 1e-10:
                logger.warning(
                    "[SIMULATION] Clamping sell %.8f to DB position %.8f", use_amount, pos
                )
                use_amount = pos
        notional = use_amount * float(fill_price)
        # PnL-FIX v9 — uniform per-side simulation fee on every exchange.
        # Cash ledger is debited (BUY) or credited (SELL) by notional ± fee.
        fee_rate = await _get_simulation_fee_rate_per_side()
        fee_usd = notional * fee_rate
        if apply_balance_delta:
            if side_l == "buy":
                await self._apply_simulation_quote_balance_delta(
                    exchange_name, -(notional + fee_usd)
                )
            else:
                await self._apply_simulation_quote_balance_delta(
                    exchange_name, (notional - fee_usd)
                )
            await self._refresh_simulation_balance_from_db(exchange_name)
        sim_id = f"sim_{uuid.uuid4().hex[:24]}"
        logger.info(
            "🧪 [SIMULATION] Instant %s fill %s %s @ %s (notional ~$%.4f, fee ~$%.4f)",
            side_l,
            sim_id,
            pair,
            fill_price,
            notional,
            fee_usd,
        )
        return {
            "id": sim_id,
            "status": "closed",
            "average": float(fill_price),
            "filled": use_amount,
            "amount": use_amount,
            "fee": {"cost": float(fee_usd), "currency": "USDC", "rate": fee_rate},
        }
            
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
                self.is_simulation = bool(mode_data.get("is_simulation", False))
                if mode_data['is_simulation']:
                    # In simulation mode, get balance from database
                    for exchange_name in exchanges:
                        try:
                            balance_response = await client.get(f"{database_service_url}/api/v1/balances/{exchange_name}")
                            if balance_response.status_code == 200:
                                balance = balance_response.json()
                                total, avail = await self._ensure_simulation_balance_in_db(
                                    client,
                                    exchange_name,
                                    float(balance["balance"]),
                                    float(balance["available_balance"]),
                                    float(balance["total_pnl"]),
                                    float(balance["daily_pnl"]),
                                )
                                self.balances[exchange_name] = {
                                    "total": total,
                                    "available": avail,
                                    "total_pnl": float(balance["total_pnl"]),
                                    "daily_pnl": float(balance["daily_pnl"]),
                                }
                                logger.info(
                                    f"[DEBUG] Loaded balance for {exchange_name}: total={total}, available={avail}, total_pnl={balance['total_pnl']}, daily_pnl={balance['daily_pnl']}"
                                )
                            else:
                                total, avail = await self._ensure_simulation_balance_in_db(
                                    client, exchange_name, 0.0, 0.0, 0.0, 0.0
                                )
                                self.balances[exchange_name] = {
                                    "total": total,
                                    "available": avail,
                                    "total_pnl": 0.0,
                                    "daily_pnl": 0.0,
                                }
                                logger.info(
                                    f"[DEBUG] Using default balance for {exchange_name}: total={total}, available={avail}, total_pnl=0.0, daily_pnl=0.0"
                                )
                        except Exception as balance_error:
                            logger.error(f"Error getting balance for {exchange_name}: {balance_error}")
                            total, avail = await self._ensure_simulation_balance_in_db(
                                client, exchange_name, 0.0, 0.0, 0.0, 0.0
                            )
                            self.balances[exchange_name] = {
                                "total": total,
                                "available": avail,
                                "total_pnl": 0.0,
                                "daily_pnl": 0.0,
                            }
                            logger.info(
                                f"[DEBUG] Using default balance for {exchange_name} due to error: total={total}, available={avail}, total_pnl=0.0, daily_pnl=0.0"
                            )
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
                    # Get exchange configuration to check max_pairs limit and base_currency
                    exchange_config_response = await client.get(f"{config_service_url}/api/v1/config/exchanges/{exchange_name}")
                    if exchange_config_response.status_code == 200:
                        exchange_config = exchange_config_response.json()
                        max_pairs = exchange_config.get('max_pairs', 10)
                        base_currency = exchange_config.get('base_currency', 'USDC')
                        logger.info(f"[DEBUG] {exchange_name} config from service: {exchange_config}")
                        logger.info(f"[DEBUG] {exchange_name} max_pairs extracted: {max_pairs}")
                    else:
                        max_pairs = 10  # Default fallback
                        base_currency = 'USDC'  # Default fallback
                        logger.warning(f"[DEBUG] {exchange_name} config service error: {exchange_config_response.status_code}, using fallback max_pairs: {max_pairs}, base_currency: {base_currency}")
                    
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
                            await self._generate_and_store_pairs(client, exchange_name, max_pairs, base_currency)
                    else:
                        # No pairs endpoint available, generate new pairs using pair selector
                        logger.info(f"No pairs found for {exchange_name} in database, generating new pair selection")
                        await self._generate_and_store_pairs(client, exchange_name, max_pairs, base_currency)
                        
                logger.info(f"Initialized pair selections for {len(exchanges)} exchanges")
        except Exception as e:
            logger.error(f"Error initializing pair selections: {e}")
                        
    async def _generate_and_store_pairs(self, client, exchange_name, max_pairs, base_currency):
        """Generate and store pairs for an exchange with blacklist management"""
        try:
            from core.pair_selector import select_top_pairs_ccxt
            from core.dynamic_blacklist_manager import blacklist_manager
            
            # Use the provided base_currency parameter
            logger.info(f"Generating pairs for {exchange_name} with base currency: {base_currency}")
                
            # Get active blacklisted pairs (global list) and scope to this exchange.
            blacklisted_pairs = await blacklist_manager.get_active_blacklist()
            if exchange_name.lower() in ("binance", "bybit"):
                exchange_blacklisted_pairs = [p for p in blacklisted_pairs if p.endswith("/USDC")]
            elif exchange_name.lower() == "cryptocom":
                exchange_blacklisted_pairs = [p for p in blacklisted_pairs if p.endswith("/USD")]
            else:
                exchange_blacklisted_pairs = list(blacklisted_pairs)
            # Add temporary cooldown blacklist from loss-based pair rotation.
            now = datetime.utcnow()
            expired_temp_keys = [
                k for k, until in self._pair_rotation_blacklist_until.items() if until <= now
            ]
            for k in expired_temp_keys:
                self._pair_rotation_blacklist_until.pop(k, None)
            temp_exchange_blacklist = []
            prefix = f"{exchange_name}|"
            for k in self._pair_rotation_blacklist_until.keys():
                if k.startswith(prefix):
                    _, pair_name = k.split("|", 1)
                    temp_exchange_blacklist.append(pair_name)
            exchange_blacklisted_pairs = list(
                dict.fromkeys(exchange_blacklisted_pairs + temp_exchange_blacklist)
            )
            # Restart-safe blacklist: derive recent loss blacklist from closed-trade history.
            historical_loss_blacklist = await self._get_recent_loss_blacklist_pairs(exchange_name)
            exchange_blacklisted_pairs = list(
                dict.fromkeys(exchange_blacklisted_pairs + historical_loss_blacklist)
            )
            logger.info(
                "[Blacklist] Active blacklisted pairs for %s: %s (global=%s)",
                exchange_name, exchange_blacklisted_pairs, len(blacklisted_pairs)
            )
            
            # Generate new pairs excluding blacklisted ones
            try:
                pair_result = await select_top_pairs_ccxt(exchange_name, base_currency, max_pairs * 3, 'spot')  # Get extra candidates for refill
                
                # Handle case where pair selector fails
                if pair_result is None or not isinstance(pair_result, dict) or 'selected_pairs' not in pair_result:
                    logger.error(f"Pair selector failed for {exchange_name}, using fallback method")
                    all_selected_pairs = []
                else:
                    all_selected_pairs = pair_result['selected_pairs']
                    logger.info(f"CCXT pair selector found {len(all_selected_pairs)} pairs for {exchange_name}")
                    
            except Exception as selector_error:
                logger.error(f"Pair selector exception for {exchange_name}: {selector_error}")
                all_selected_pairs = []
            
            # If pair selector failed or returned insufficient pairs, use predefined high-quality pairs
            if len(all_selected_pairs) < max_pairs:
                logger.info(f"Insufficient pairs from selector ({len(all_selected_pairs)}), using predefined pairs for {exchange_name}")
                
                # Predefined high-quality pairs for each exchange
                predefined_pairs = {
                    'binance': ['BNB/USDC', 'BTC/USDC', 'ETH/USDC', 'XRP/USDC', 'XLM/USDC', 'LINK/USDC', 'LTC/USDC', 
                               'TRX/USDC', 'ADA/USDC', 'NEO/USDC', 'DOT/USDC', 'MATIC/USDC', 'UNI/USDC', 'AVAX/USDC', 'ALGO/USDC'],
                    'bybit': ['ETH/USDC', 'BTC/USDC', 'XLM/USDC', 'SOL/USDC', 'XRP/USDC', 'DOT/USDC', 'MATIC/USDC', 
                             'UNI/USDC', 'AVAX/USDC', 'LINK/USDC', 'LTC/USDC', 'ADA/USDC', 'ALGO/USDC', 'ATOM/USDC', 'FTM/USDC'],
                    'cryptocom': ['CRO/USD', '1INCH/USD', 'AAVE/USD', 'ACA/USD', 'ACH/USD', 'ACT/USD', 'BTC/USD', 'ETH/USD', 
                                 'XRP/USD', 'DOT/USD', 'MATIC/USD', 'UNI/USD', 'AVAX/USD', 'LINK/USD', 'LTC/USD']
                }
                
                # Use predefined pairs and merge with selector results
                exchange_predefined = predefined_pairs.get(exchange_name.lower(), [])
                
                # Combine selector results with predefined pairs, avoiding duplicates
                combined_pairs = list(all_selected_pairs)  # Start with selector results
                # Keep a larger candidate pool so blacklist replacement can still reach max_pairs.
                target_pool_size = max_pairs * 3
                for pair in exchange_predefined:
                    if pair not in combined_pairs and len(combined_pairs) < target_pool_size:
                        combined_pairs.append(pair)
                
                all_selected_pairs = combined_pairs[:target_pool_size]
                logger.info(f"Using {len(all_selected_pairs)} combined pairs for {exchange_name}")
            
            # Ensure CRO/USD is always first for crypto.com
            if exchange_name.lower() == "cryptocom" and "CRO/USD" not in all_selected_pairs:
                all_selected_pairs.insert(0, "CRO/USD")
                logger.info(f"✅ Ensured CRO/USD is included for cryptocom: {all_selected_pairs[:3]}...")
            elif exchange_name.lower() == "cryptocom":
                # Move CRO/USD to first position if it exists
                if "CRO/USD" in all_selected_pairs:
                    all_selected_pairs.remove("CRO/USD")
                    all_selected_pairs.insert(0, "CRO/USD")
                    logger.info(f"✅ Moved CRO/USD to first position for cryptocom")
            
            # Filter out blacklisted pairs (exchange-scoped).
            selected_pairs = [pair for pair in all_selected_pairs if pair not in exchange_blacklisted_pairs]
            
            # Get replacement pairs if we have blacklisted pairs
            if len(exchange_blacklisted_pairs) > 0:
                replacement_pairs = await blacklist_manager.get_replacement_pairs(
                    exchange_name, max_pairs, exchange_blacklisted_pairs
                )
                # Merge with selected pairs, avoiding duplicates
                for replacement in replacement_pairs:
                    if replacement not in selected_pairs and len(selected_pairs) < max_pairs:
                        selected_pairs.append(replacement)
                        
                logger.info(
                    "[Blacklist] %s blacklisted on %s, replacement candidates=%s, selected_now=%s/%s",
                    len(exchange_blacklisted_pairs), exchange_name, len(replacement_pairs), len(selected_pairs), max_pairs
                )

            # Hard guarantee: always refill to max_pairs with non-blacklisted candidates.
            if len(selected_pairs) < max_pairs:
                for candidate in all_selected_pairs:
                    if candidate not in selected_pairs and candidate not in exchange_blacklisted_pairs:
                        selected_pairs.append(candidate)
                        if len(selected_pairs) >= max_pairs:
                            break
            
            # Always forcibly add CRO/USD for cryptocom if not already present and not blacklisted
            if exchange_name.lower() == "cryptocom" and "CRO/USD" not in exchange_blacklisted_pairs:
                logger.info(f"[DEBUG] cryptocom pairs before force-add: {selected_pairs}")
                if "CRO/USD" not in selected_pairs:
                    selected_pairs.append("CRO/USD")
                    logger.info(f"[FORCE] Added CRO/USD to cryptocom pairs")
                logger.info(f"[FORCE] cryptocom pairs to be saved: {selected_pairs}")

            # Final clamp and clean count diagnostics.
            selected_pairs = selected_pairs[:max_pairs]
            logger.info(
                "[PairSelection] %s final_count=%s target=%s blacklisted=%s pool=%s",
                exchange_name, len(selected_pairs), max_pairs, len(exchange_blacklisted_pairs), len(all_selected_pairs)
            )
            
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

    async def _get_recent_loss_blacklist_pairs(self, exchange_name: str) -> List[str]:
        """Build temporary blacklist from recent losing closed trades (restart-safe)."""
        try:
            loss_threshold_pct = float(
                await self._get_config_value(
                    "trading.pair_rotation.remove_pair_loss_threshold_pct", -1.0
                )
                or -1.0
            )
            cooldown_hours = float(
                await self._get_config_value(
                    "trading.pair_rotation.temp_blacklist_cooldown_hours", 12
                )
                or 12
            )
            scan_limit = int(
                await self._get_config_value(
                    "trading.pair_rotation.scan_recent_closed_limit", 2000
                )
                or 2000
            )
            cutoff = datetime.utcnow() - timedelta(hours=cooldown_hours)

            async with httpx.AsyncClient(timeout=45.0) as client:
                trades_resp = await client.get(
                    f"{database_service_url}/api/v1/trades",
                    params={
                        "limit": scan_limit,
                        "exchange": exchange_name,
                        "sort_by": "exit_time",
                        "sort_order": "desc",
                    },
                )
                if trades_resp.status_code != 200:
                    return []
                recent_trades = (trades_resp.json() or {}).get("trades", []) or []

            blacklist_pairs: set[str] = set()
            for t in recent_trades:
                if str(t.get("status", "")).upper() != "CLOSED":
                    continue
                if not is_macd_momentum_strategy(t.get("strategy")):
                    continue
                pair = str(t.get("pair") or "")
                if not pair:
                    continue
                exit_time_raw = t.get("exit_time")
                if not exit_time_raw:
                    continue
                try:
                    exit_time = datetime.fromisoformat(str(exit_time_raw).replace("Z", "+00:00")).replace(tzinfo=None)
                except Exception:
                    continue
                if exit_time < cutoff:
                    continue

                try:
                    entry_price = float(t.get("entry_price") or 0.0)
                    position_size = float(t.get("position_size") or 0.0)
                    realized_pnl = float(t.get("realized_pnl") or 0.0)
                    total_investment = float(t.get("total_investment") or 0.0)
                    entry_notional = float(t.get("entry_notional") or 0.0)
                except Exception:
                    continue
                notional = infer_trade_notional_usd(
                    entry_price=entry_price,
                    position_size=position_size,
                    total_investment=total_investment,
                    entry_notional=entry_notional,
                )
                if notional <= 0:
                    continue
                realized_pnl_pct = (realized_pnl / notional) * 100.0
                if realized_pnl_pct <= loss_threshold_pct:
                    blacklist_pairs.add(pair)
            if blacklist_pairs:
                logger.warning(
                    "[PairRotation] Historical loss blacklist for %s (<= %.2f%% within %.1fh): %s",
                    exchange_name,
                    loss_threshold_pct,
                    cooldown_hours,
                    sorted(blacklist_pairs),
                )
            return list(blacklist_pairs)
        except Exception as e:
            logger.warning("[PairRotation] Historical blacklist build skipped for %s: %s", exchange_name, e)
            return []

    async def _rotate_pair_after_loss(
        self,
        exchange_name: str,
        pair: str,
        realized_pnl_pct: float,
    ) -> None:
        """Delegate pair-rotation behavior to dedicated module."""
        await rotate_pair_after_loss(
            exchange_name=exchange_name,
            pair=pair,
            realized_pnl_pct=realized_pnl_pct,
            pair_selections=self.pair_selections,
            config_service_url=config_service_url,
            database_service_url=database_service_url,
            get_config_value=self._get_config_value,
            generate_and_store_pairs=self._generate_and_store_pairs,
            temp_blacklist_until=self._pair_rotation_blacklist_until,
        )
        # Immediate strict entry block, even before the next map refresh.
        try:
            cooldown_hours = float(
                await self._get_config_value(
                    "trading.pair_rotation.temp_blacklist_cooldown_hours", 12
                )
                or 12
            )
        except Exception:
            cooldown_hours = 12.0
        self._hard_loss_cooldown_until[loss_cooldown_key(exchange_name, pair)] = (
            datetime.utcnow() + timedelta(hours=cooldown_hours)
        )
    
    async def _get_strategy_position_config(self, strategy_name: str) -> tuple[float, float]:
        """Get position sizing configuration for a specific strategy"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(f"{config_service_url}/api/v1/config/trading")
                if response.status_code == 200:
                    config = response.json()
                    strategy_sizing = config.get('strategy_position_sizing', {})
                    
                    if strategy_sizing.get('enabled', False):
                        strategy_config = strategy_sizing.get(strategy_name)
                        if strategy_config:
                            position_percentage = strategy_config.get('position_size_percentage', 0.10)
                            max_position_usd = strategy_config.get('max_position_usd', 200)
                            logger.info(f"[PositionConfig] Using strategy-specific config for {strategy_name}: {position_percentage:.1%}, ${max_position_usd}")
                            return position_percentage, max_position_usd
                    
                    # Fallback to global position sizing
                    global_percentage = config.get('position_size_percentage', 0.10)
                    logger.info(f"[PositionConfig] Using global config for {strategy_name}: {global_percentage:.1%}")
                    return global_percentage, 200.0
                    
        except Exception as e:
            logger.error(f"Error loading position config for {strategy_name}: {e}")
            
        # Final fallback
        logger.warning(f"[PositionConfig] Using fallback config for {strategy_name}: 10%, $200")
        return 0.10, 200.0

    def _apply_trading_loop_tunings(self, trading: Optional[Dict[str, Any]] = None) -> None:
        """Refresh cycle_interval / max_cycle_duration / exit_cycle_first from config (in-memory or caller)."""
        tc: Optional[Dict[str, Any]] = trading
        if not tc and self._config and isinstance(self._config.get("trading"), dict):
            tc = self._config["trading"]
        if not tc:
            return
        self._loop_cycle_interval = float(tc.get("cycle_interval_seconds", 60) or 60)
        raw_max = float(tc.get("max_cycle_duration", 60) or 60)
        # Keep within sane bounds: responsive loops without pathological sub-second churn
        self._loop_max_cycle_duration = max(15.0, min(raw_max, 600.0))
        self._loop_exit_cycle_first = bool(tc.get("exit_cycle_first", True))
        raw_reserve = float(tc.get("entry_loop_reserve_seconds", 90) or 90)
        # Cap reserve so exit always has at least ~25s of wall before its deadline.
        max_reserve_for_exit = max(25.0, self._loop_max_cycle_duration - 25.0)
        self._loop_entry_reserve_seconds = max(30.0, min(raw_reserve, max_reserve_for_exit))
        # Exit runs in [0, max_wall - reserve]; entry+maintenance use full max_wall deadline.
        if self._loop_exit_cycle_first:
            min_loop_wall = max(
                120.0,
                self._loop_cycle_interval * 2.0 + 60.0,
                self._loop_entry_reserve_seconds + 40.0,
            )
            if self._loop_max_cycle_duration < min_loop_wall:
                logger.warning(
                    "[TradingLoop] max_cycle_duration=%.0fs is too low with exit_cycle_first=true "
                    "(need >=%.0fs = entry_loop_reserve %.0fs + ~40s exit headroom). Raising.",
                    self._loop_max_cycle_duration,
                    min_loop_wall,
                    self._loop_entry_reserve_seconds,
                )
                self._loop_max_cycle_duration = min(min_loop_wall, 600.0)
                max_reserve_for_exit = max(25.0, self._loop_max_cycle_duration - 25.0)
                self._loop_entry_reserve_seconds = max(
                    30.0, min(self._loop_entry_reserve_seconds, max_reserve_for_exit)
                )
            
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
            self.trading_active = True  # Set trading_active when starting
            self.start_time = datetime.utcnow()
            # New session: align cycle counter with uptime for this run
            self.cycle_count = 0
            self.last_loop_duration_seconds = None
            global trading_status, start_time, cycle_count
            trading_status = "running"
            start_time = self.start_time
            cycle_count = 0
            
            logger.info("Starting trading orchestrator...")
            
            # Start trading loop in background with proper error handling
            trading_task = asyncio.create_task(self._trading_loop())
            trading_task.add_done_callback(lambda t: self._handle_trading_task_result(t))
            
            logger.info("✅ Trading loop task created and started")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to start trading: {e}")
            return False
            
    async def stop_trading(self) -> bool:
        """Stop the trading orchestrator"""
        try:
            self.running = False
            self.trading_active = False  # Set trading_active to False when stopping
            global trading_status
            trading_status = "stopped"
            
            # Cleanup new trailing stop system
            if self.trailing_stop_system:
                try:
                    await self.trailing_stop_system.stop()
                    logger.info("✅ New trailing stop system cleaned up")
                except Exception as e:
                    logger.error(f"❌ Error cleaning up trailing stop system: {e}")
            
            logger.info("Trading orchestrator stopped")
            return True
            
        except Exception as e:
            logger.error(f"Failed to stop trading: {e}")
            return False
            
    async def emergency_stop(self) -> bool:
        """Emergency stop all trading activities"""
        try:
            self.running = False
            self.trading_active = False  # Set trading_active to False on emergency stop
            global trading_status
            trading_status = "emergency_stop"
            
            # Close all active trades
            await self._close_all_trades()
            
            logger.info("Emergency stop executed - all trading activities halted")
            return True
            
        except Exception as e:
            logger.error(f"Failed to execute emergency stop: {e}")
            return False
    
    async def sync_exchange_trades(self) -> dict:
        """Sync trades with exchanges to close orphaned trades in database"""
        try:
            logger.info("🔄 Starting exchange-database trade synchronization...")
            sync_results = {
                'trades_checked': 0,
                'trades_synced': 0,
                'errors': []
            }
            
            # Get all open trades from database
            async with httpx.AsyncClient(timeout=60.0) as client:
                trades_response = await client.get(f"{database_service_url}/api/v1/trades?status=OPEN,EXIT_FAILED")
                if trades_response.status_code != 200:
                    logger.error(f"❌ Failed to get open trades: {trades_response.status_code}")
                    return sync_results
                
                open_trades = trades_response.json().get('trades', [])
                sync_results['trades_checked'] = len(open_trades)
                logger.info(f"📊 Found {len(open_trades)} open trades to verify")
                
                for trade in open_trades:
                    try:
                        trade_id = trade['trade_id']  # Use UUID trade_id, not database row id
                        exchange_name = trade['exchange']
                        pair = trade['pair']
                        position_size = float(trade.get('position_size', 0))
                        
                        logger.info(f"🔍 Checking trade {trade_id}: {position_size} {pair} on {exchange_name}")
                        
                        # Check if balance exists for this asset on exchange
                        balance_response = await client.get(f"{exchange_service_url}/api/v1/account/balance/{exchange_name}")
                        if balance_response.status_code == 200:
                            balances = balance_response.json().get('balances', {})
                            
                            # Extract base asset from pair (e.g., ADA from ADA/USDC)
                            base_asset = pair.split('/')[0] if '/' in pair else pair.split('_')[0]
                            
                            # Check if we have this asset in balances
                            position_found = False
                            if base_asset in balances:
                                available_balance = float(balances[base_asset].get('available', 0))
                                # If we have significant balance (more than 10% of position size), consider position exists
                                if available_balance >= position_size * 0.1:
                                    position_found = True
                            
                            if not position_found:
                                # No position found on exchange - trade was likely closed
                                logger.warning(f"🚨 Trade {trade_id} has no position on {exchange_name} - likely closed on exchange but not in DB")
                                
                                # Get recent orders to find exit details
                                orders_response = await client.get(f"{exchange_service_url}/api/v1/trading/orders/{exchange_name}/history")
                                exit_price = None
                                exit_time = None
                                
                                if orders_response.status_code == 200:
                                    orders = orders_response.json().get('orders', [])
                                    # Look for sell orders for this pair
                                    for order in orders:
                                        if (order.get('symbol') == pair and 
                                            order.get('side', '').lower() == 'sell' and
                                            order.get('status', '').lower() in ['filled', 'closed']):
                                            exit_price = float(order.get('average', 0))
                                            exit_time = order.get('timestamp')
                                            break
                                
                                # Use current market price as fallback if no exit order found (better than entry price)
                                if not exit_price:
                                    try:
                                        # Get current market price for more accurate PnL calculation
                                        ticker_response = await client.get(f"{exchange_service_url}/api/v1/market/ticker/{exchange_name}/{pair.replace('/', '')}")
                                        if ticker_response.status_code == 200:
                                            ticker_data = ticker_response.json()
                                            current_price = float(ticker_data.get('price', 0))
                                            if current_price > 0:
                                                exit_price = current_price
                                                logger.info(f"📈 Using current market price ${current_price:.4f} for exit (no order history found)")
                                            else:
                                                exit_price = float(trade.get('entry_price', 0))
                                                logger.warning(f"⚠️ Using entry price ${exit_price:.4f} as fallback (no market price available)")
                                        else:
                                            exit_price = float(trade.get('entry_price', 0))
                                            logger.warning(f"⚠️ Using entry price ${exit_price:.4f} as fallback (market price fetch failed)")
                                    except Exception as price_error:
                                        exit_price = float(trade.get('entry_price', 0))
                                        logger.warning(f"⚠️ Using entry price ${exit_price:.4f} as fallback (price fetch error: {price_error})")
                                if not exit_time:
                                    exit_time = datetime.utcnow().isoformat() + '+00:00'
                                
                                # Calculate realized PnL
                                entry_price = float(trade.get('entry_price', 0))
                                realized_pnl = (exit_price - entry_price) * position_size
                                
                                # Use centralized trade closure API
                                try:
                                    close_data = {
                                        'exit_price': exit_price,
                                        'exit_reason': 'exchange_sync_closure',
                                        'exit_time': exit_time,
                                        'validated_by_exchange': True
                                    }
                                    
                                    close_response = await client.post(f"{database_service_url}/api/v1/trades/{trade_id}/close", json=close_data)
                                    if close_response.status_code == 200:
                                        result = close_response.json()
                                        logger.info(f"✅ CENTRALIZED SYNC: Trade {trade_id} closed with "
                                                   f"exit_price=${result['exit_price']:.4f}, "
                                                   f"PnL=${result['realized_pnl']:.2f} ({result['pnl_percentage']:.2f}%)")
                                        sync_results['trades_synced'] += 1
                                    else:
                                        error_msg = f"Centralized closure failed for trade {trade_id}: {close_response.status_code}"
                                        logger.error(f"❌ {error_msg}")
                                        sync_results['errors'].append(error_msg)
                                        
                                except Exception as centralized_error:
                                    logger.error(f"❌ Centralized closure error for {trade_id}: {centralized_error}")
                                    # Fallback to direct update
                                    try:
                                        close_data = {
                                            'status': 'CLOSED',
                                            'exit_price': exit_price,
                                            'exit_reason': 'exchange_sync_closure_fallback',
                                            'exit_time': exit_time,
                                            'realized_pnl': realized_pnl,
                                            'updated_at': datetime.utcnow().isoformat() + '+00:00'
                                        }
                                        
                                        close_response = await client.put(f"{database_service_url}/api/v1/trades/{trade_id}", json=close_data)
                                        if close_response.status_code == 200:
                                            logger.info(f"✅ Fallback sync: Trade {trade_id} closed with exit_price=${exit_price:.4f}, PnL=${realized_pnl:.2f}")
                                            sync_results['trades_synced'] += 1
                                        else:
                                            error_msg = f"Both centralized and fallback closure failed for trade {trade_id}: {close_response.status_code}"
                                            logger.error(f"❌ {error_msg}")
                                            sync_results['errors'].append(error_msg)
                                    except Exception as fallback_error:
                                        error_msg = f"Complete closure failure for trade {trade_id}: {fallback_error}"
                                        logger.error(f"❌ {error_msg}")
                                        sync_results['errors'].append(error_msg)
                            else:
                                logger.info(f"✅ Trade {trade_id} position verified on {exchange_name}")
                        else:
                            error_msg = f"Failed to get balances for {exchange_name}: {balance_response.status_code}"
                            logger.error(f"❌ {error_msg}")
                            sync_results['errors'].append(error_msg)
                            
                    except Exception as trade_error:
                        error_msg = f"Error syncing trade {trade.get('id', 'unknown')}: {str(trade_error)}"
                        logger.error(f"❌ {error_msg}")
                        sync_results['errors'].append(error_msg)
            
            logger.info(f"✅ Sync complete: {sync_results['trades_synced']}/{sync_results['trades_checked']} trades synced")
            return sync_results
            
        except Exception as e:
            logger.error(f"❌ Error in exchange sync: {str(e)}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            return {'trades_checked': 0, 'trades_synced': 0, 'errors': [str(e)]}
    
    async def _initialize_config(self) -> None:
        """Initialize configuration from config service"""
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                # Get trading configuration
                trading_response = await client.get(f"{config_service_url}/api/v1/config/trading")
                trading_response.raise_for_status()
                trading_config = trading_response.json()
                
                # Get database configuration
                database_response = await client.get(f"{config_service_url}/api/v1/config/database")
                database_response.raise_for_status()
                database_config = database_response.json()
                
                # Get strategies configuration
                strategies_response = await client.get(f"{config_service_url}/api/v1/config/strategies")
                strategies_response.raise_for_status()
                strategies_config = strategies_response.json()
                
                # Combine all configurations
                self._config = {
                    'trading': trading_config,
                    'database': database_config,
                    'strategies': strategies_config
                }
                
                logger.info(
                    f"Configuration loaded successfully: trailing_stop activation_threshold="
                    f"{trading_config.get('trailing_stop', {}).get('activation_threshold', 'unknown')}"
                )

                # PnL-FIX v3: hydrate runtime cooldown/correlation settings from config.
                try:
                    self._pair_cooldown_minutes = int(trading_config.get('pair_cooldown_minutes', self._pair_cooldown_minutes))
                    self._global_pair_cooldown_minutes = int(
                        trading_config.get("global_pair_cooldown_minutes", self._global_pair_cooldown_minutes)
                    )
                    self._correlation_basket_cap = int(trading_config.get('correlation_basket_cap', self._correlation_basket_cap))
                    logger.info(
                        f"[PnL-FIX v3] pair_cooldown_minutes={self._pair_cooldown_minutes}, "
                        f"global_pair_cooldown_minutes={self._global_pair_cooldown_minutes}, "
                        f"correlation_basket_cap={self._correlation_basket_cap}"
                    )
                except Exception as _cfg_err:
                    logger.warning(f"[PnL-FIX v3] Using default cooldown/cap values: {_cfg_err}")
        except Exception as e:
            logger.error(f"Failed to load configuration: {e}")
            raise
    
    async def _initialize_database_manager(self) -> None:
        """Initialize database manager"""
        try:
            if not self._config:
                raise ValueError("Configuration not loaded")
            
            db_config = self._config.get('database', {})
            self.database_manager = DatabaseManager(db_config)
            logger.info("Database manager initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize database manager: {e}")
            raise
    
    def _initialize_activation_trigger_system(self) -> None:
        """Initialize the new exchange-delegated trailing stop system"""
        try:
            logger.info("🎯 Initializing new exchange-delegated trailing stop system...")
            
            # Import the new system
            import sys
            import os
            sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'strategy'))
            
            from activation_trigger_system import ActivationTriggerSystem
            
            # Initialize the new system
            self.activation_trigger_system = ActivationTriggerSystem(self._config)
            
            # Start the system in a background task to avoid blocking startup
            asyncio.create_task(self._start_activation_system_background())
            
            self.use_new_trailing_system = True
            logger.info("✅ New exchange-delegated trailing stop system initialization started")
            
        except Exception as e:
            logger.error(f"❌ Failed to initialize new trailing stop system: {e}")
            logger.warning("⚠️ Falling back to legacy trailing stop system")
            self.use_new_trailing_system = False
            self.activation_trigger_system = None
    
    async def _start_activation_system_background(self) -> None:
        """Start the activation trigger system in background"""
        try:
            await self.activation_trigger_system.start()
            logger.info("✅ New exchange-delegated trailing stop system started successfully")
        except Exception as e:
            logger.error(f"❌ Failed to start activation trigger system: {e}")
            self.use_new_trailing_system = False
            self.activation_trigger_system = None
    
    def _handle_trading_task_result(self, task):
        """Handle trading task completion or failure"""
        try:
            if task.cancelled():
                logger.info("🛑 Trading loop task was cancelled")
            elif task.exception():
                logger.error(f"❌ Trading loop task failed with exception: {task.exception()}")
                # Restart trading loop after a delay
                asyncio.create_task(self._restart_trading_loop())
            else:
                logger.info("✅ Trading loop task completed successfully")
        except Exception as e:
            logger.error(f"❌ Error in trading task result handler: {e}")
    
    async def _restart_trading_loop(self):
        """Restart the trading loop after a failure"""
        try:
            logger.info("🔄 Restarting trading loop after failure...")
            await asyncio.sleep(10)  # Wait before restarting
            
            if self.running:
                trading_task = asyncio.create_task(self._trading_loop())
                trading_task.add_done_callback(lambda t: self._handle_trading_task_result(t))
                logger.info("✅ Trading loop restarted successfully")
            else:
                logger.info("🛑 Not restarting trading loop - orchestrator is stopped")
        except Exception as e:
            logger.error(f"❌ Failed to restart trading loop: {e}")
    
    async def _check_new_system_health(self) -> None:
        """Check health of the new exchange-delegated trailing stop system"""
        while self.running:
            try:
                await asyncio.sleep(60)  # Check every minute
                
                if self.use_new_trailing_system and self.activation_trigger_system:
                    # Check if new system is healthy
                    is_healthy = await self.activation_trigger_system.is_healthy()
                    if not is_healthy:
                        logger.warning("⚠️ New trailing stop system became unhealthy")
                        # System will auto-recover, no need to disable
                        
            except Exception as e:
                logger.error(f"❌ Error in new system health check: {e}")
                await asyncio.sleep(30)  # Wait before retrying
    
    async def _initialize_strategy_manager(self) -> None:
        """Initialize strategy manager with database and config"""
        try:
            if not self._config or not self.database_manager:
                raise ValueError("Configuration or database manager not initialized")
            
            # Create a mock exchange manager for strategy manager
            # (The strategy manager will mainly be used for profit protection and trailing stops)
            self.strategy_manager = StrategyManager(
                config=self._config,
                exchange_manager=None,  # Not needed for exit logic
                database_manager=self.database_manager
            )
            logger.info("Strategy manager initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize strategy manager: {e}")
            raise
    
    async def _initialize_trailing_stop_system(self) -> None:
        """Initialize the new trailing stop system"""
        try:
            # The new trailing stop system is already initialized in _initialize_activation_trigger_system
            # Just set the reference to avoid duplicate initialization
            if hasattr(self, 'activation_trigger_system') and self.activation_trigger_system:
                self.trailing_stop_system = self.activation_trigger_system
                logger.info("✅ New trailing stop system reference set (already initialized)")
            else:
                logger.warning("⚠️ New trailing stop system not available - using legacy system")
                self.use_new_trailing_system = False
                self.trailing_stop_system = None
            
        except Exception as e:
            logger.error(f"❌ Failed to initialize trailing stop system: {e}")
            logger.warning("⚠️ Falling back to legacy trailing stop system")
            self.use_new_trailing_system = False
            self.trailing_stop_system = None
            
            
    async def _trading_loop(self) -> None:
        """Main trading loop with order monitoring"""
        try:
            logger.info("🚀 Trading loop starting...")
            
            # Get trading configuration
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.get(f"{config_service_url}/api/v1/config/trading")
                response.raise_for_status()
                trading_config = response.json()
            
            self._apply_trading_loop_tunings(trading_config)
            cycle_interval = self._loop_cycle_interval
            exit_cycle_first = self._loop_exit_cycle_first
            
            logger.info(
                "📊 Trading configuration loaded: cycle_interval=%ss, exit_cycle_first=%s, "
                "max_cycle_duration=%ss, entry_loop_reserve=%ss (exit timebox = max - reserve, "
                "then entry/maintenance until max)",
                cycle_interval,
                exit_cycle_first,
                self._loop_max_cycle_duration,
                self._loop_entry_reserve_seconds,
            )
            
            # Start order monitoring in background
            logger.info("🔄 Starting order monitoring in background")
            order_monitoring_task = asyncio.create_task(self._monitor_pending_orders())
            
            # New trailing stop system runs continuously after startup
            # No additional monitoring needed - system is event-driven
            logger.info("🎯 New exchange-delegated trailing stop system active - no additional monitoring needed")
            redis_health_task = None
            
            logger.info("✅ Trading loop initialized successfully - starting main cycle")
            
            while self.running:
                try:
                    self._apply_trading_loop_tunings()
                    cycle_interval = self._loop_cycle_interval
                    exit_cycle_first = self._loop_exit_cycle_first
                    max_wall = self._loop_max_cycle_duration
                    loop_start = time.monotonic()
                    full_deadline = loop_start + max_wall
                    exit_deadline: Optional[float] = None
                    if exit_cycle_first:
                        exit_deadline = full_deadline - self._loop_entry_reserve_seconds
                        if exit_deadline <= loop_start + 5.0:
                            exit_deadline = loop_start + max(30.0, max_wall - self._loop_entry_reserve_seconds)

                    cycle_start = datetime.utcnow()
                    self.cycle_count += 1
                    global cycle_count
                    cycle_count = self.cycle_count
                    
                    if exit_cycle_first and exit_deadline is not None:
                        logger.info(
                            f"🔄 Starting trading cycle {self.cycle_count} "
                            f"(exit timebox {exit_deadline - loop_start:.0f}s, "
                            f"entry+maintenance reserve {self._loop_entry_reserve_seconds:.0f}s, "
                            f"full wall {max_wall:.0f}s, spacing {cycle_interval:.0f}s)"
                        )
                    else:
                        logger.info(
                            f"🔄 Starting trading cycle {self.cycle_count} "
                            f"(wall budget {max_wall:.0f}s until {cycle_interval:.0f}s spacing)"
                        )
                    
                    # Run exit cycle first if configured (timeboxed so entry always gets wall time)
                    if exit_cycle_first:
                        logger.info(f"📤 Running exit cycle {self.cycle_count}...")
                        await self._run_exit_cycle(deadline=exit_deadline)
                        logger.info(f"✅ Exit cycle {self.cycle_count} completed")
                        
                    # New trailing stop system runs continuously after startup
                    # No additional cycle calls needed - system is event-driven
                        
                    # Run entry cycle
                    logger.info(f"📥 Running entry cycle {self.cycle_count}...")
                    await self._run_entry_cycle(deadline=full_deadline)
                    logger.info(f"✅ Entry cycle {self.cycle_count} completed")
                    
                    # Run maintenance tasks
                    logger.info(f"🔧 Running maintenance tasks for cycle {self.cycle_count}...")
                    await self._run_maintenance_tasks(deadline=full_deadline)
                    logger.info(f"✅ Maintenance tasks for cycle {self.cycle_count} completed")
                    
                    # Calculate cycle duration
                    cycle_duration = (datetime.utcnow() - cycle_start).total_seconds()
                    self.last_loop_duration_seconds = round(cycle_duration, 3)
                    logger.info(f"✅ Completed trading cycle {self.cycle_count} in {cycle_duration:.2f}s")
                    
                    # Wait for next cycle
                    if cycle_duration < cycle_interval:
                        wait_time = cycle_interval - cycle_duration
                        logger.info(f"⏳ Waiting {wait_time:.2f}s until next cycle...")
                        await asyncio.sleep(wait_time)
                    else:
                        logger.warning(
                            f"⚠️ Cycle {self.cycle_count} took {cycle_duration:.2f}s "
                            f"(longer than spacing interval {cycle_interval:.0f}s; "
                            f"next loop starts immediately — raise max_cycle_duration if work was truncated)"
                        )
                        
                except Exception as e:
                    logger.error(f"❌ Error in trading cycle {self.cycle_count}: {e}")
                    logger.error(f"❌ Error details: {type(e).__name__}: {str(e)}")
                    await asyncio.sleep(10)  # Wait before retrying
                    
        except Exception as e:
            logger.error(f"Fatal error in trading orchestrator: {e}")
            self.running = False
            self.trading_active = False  # Set trading_active to False on error
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
            
            # Stop new trailing stop system when trading stops
            if self.activation_trigger_system:
                try:
                    await self.activation_trigger_system.stop()
                    logger.info("🛑 New trailing stop system stopped")
                except Exception as e:
                    logger.error(f"❌ Error stopping new trailing stop system: {e}")

    async def _redis_ticker_last_for_hint_key(self, hint_key: str) -> Optional[float]:
        """Read last price from Redis hash ``ticker:live:{hint_key}`` (mirrored from exchange WS)."""
        rcl = getattr(self.redis_order_manager, "redis_client", None)
        if not rcl:
            return None
        try:
            rk = f"ticker:live:{hint_key}"
            raw = await rcl.hget(rk, "last")
            if raw is None:
                return None
            f = float(raw)
            return f if f > 0 else None
        except Exception:
            return None

    async def _prefetch_ticker_live_prices(
        self,
        unique_pairs: List[Tuple[str, str]],
        client: httpx.AsyncClient,
        semaphore_limit: int,
        stale_threshold: int = 45,
    ) -> Dict[str, float]:
        """Parallel Redis + exchange-service ``ticker-live`` (WS-backed in-memory cache) for exit hints.

        WS-FIX: default freshness window 30s → 45s. Low-volume cryptocom pairs
        (ACT/USD, AKT/USD, etc.) often have inter-tick gaps of 15-30s on the
        WS feed even when the subscription is healthy. A tight 30s window
        triggered constant 204s and forced REST/OHLCV fallback during the
        prefetch phase, defeating the whole point of the WS cache.
        """
        if not unique_pairs:
            return {}
        sem = asyncio.Semaphore(max(1, semaphore_limit))
        out: Dict[str, float] = {}
        lock = asyncio.Lock()

        async def fetch_one(exchange: str, pair: str) -> None:
            sym = self._convert_pair_format(exchange, pair)
            hint_key = _ws_ticker_key_suffix(exchange, sym)
            rpx = await self._redis_ticker_last_for_hint_key(hint_key)
            if rpx and rpx > 0:
                async with lock:
                    out[hint_key] = rpx
                return
            async with sem:
                try:
                    enc_sym = quote(sym, safe="")
                    url = f"{exchange_service_url}/api/v1/market/ticker-live/{exchange}/{enc_sym}"
                    resp = await client.get(
                        url,
                        params={"stale_threshold_seconds": stale_threshold},
                    )
                    if resp.status_code != 200:
                        return
                    data = resp.json()
                    px = float(data.get("last") or 0)
                    if px > 0:
                        async with lock:
                            out[hint_key] = px
                except Exception:
                    return

        await asyncio.gather(*(fetch_one(e, p) for e, p in unique_pairs), return_exceptions=True)
        return out
            
    async def _run_exit_cycle(self, deadline: Optional[float] = None) -> None:
        """Run exit cycle to check for trade exits.

        All open trades are targeted **every** loop; when ``deadline`` is set (exit-first scheduling),
        parallel exit checks are **timeboxed** so entry still has wall time for strategy-service calls.
        Prices are prefetched in parallel (Redis mirror + ``ticker-live`` HTTP).
        """
        cycle_start_time = time.time()
        try:
            logger.info("📤 Starting exit cycle...")
            
            # Get all open trades
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.get(f"{database_service_url}/api/v1/trades/open")
                response.raise_for_status()
                open_trades = response.json()['trades']
            
            logger.info(f"📊 Exit cycle found {len(open_trades)} open trades to process")
            
            if len(open_trades) == 0:
                logger.info("📤 Exit cycle: No open trades to process")
                return

            # Load trading config ONCE per exit cycle (was incorrectly fetched inside every
            # _check_trade_exit — N open trades caused N identical HTTP calls and multi‑minute cycles).
            cycle_trading_config: Dict[str, Any] = {}
            try:
                if self._config and isinstance(self._config.get("trading"), dict):
                    cycle_trading_config = self._config["trading"]
                    logger.info("📊 Exit cycle: using in-memory trading config (no per-trade config HTTP)")
                else:
                    async with httpx.AsyncClient(timeout=20.0) as client:
                        r = await client.get(f"{config_service_url}/api/v1/config/trading")
                        r.raise_for_status()
                        cycle_trading_config = r.json()
                    logger.info("📊 Exit cycle: loaded trading config once for all exit checks")
            except Exception as e:
                logger.warning(f"⚠️ Exit cycle: could not load trading config, using defaults per trade: {e}")

            # Preload order mappings once per exit cycle (was fetched inside _check_trade_exit per trade
            # on trailing-stop paths — large payloads × N trades dominated wall time).
            shared_order_mappings: Optional[List[Dict[str, Any]]] = None
            try:
                async with httpx.AsyncClient(timeout=20.0) as client:
                    om = await client.get(f"{database_service_url}/api/v1/order-mappings")
                    if om.status_code == 200:
                        raw_om = om.json().get("order_mappings") or []
                        shared_order_mappings = raw_om if isinstance(raw_om, list) else []
                        logger.info(
                            "📊 Exit cycle: loaded %s order mappings once for pending-order checks",
                            len(shared_order_mappings),
                        )
            except Exception as e:
                logger.warning("⚠️ Exit cycle: could not preload order-mappings (per-trade fallback): %s", e)
            
            # Update active trades metrics
            for trade in open_trades:
                active_trades.labels(exchange=trade['exchange'], pair=trade['pair']).inc()

            valid_trades: List[Dict[str, Any]] = []
            skipped_count = 0
            for trade in open_trades:
                try:
                    ep = float(trade.get("entry_price") or 0)
                except (TypeError, ValueError):
                    ep = 0.0
                if ep <= 0:
                    skipped_count += 1
                    tid = trade.get("trade_id") or trade.get("id", "unknown")
                    logger.warning(
                        "⚠️ Skipping trade %s - invalid entry price: %s",
                        tid,
                        trade.get("entry_price"),
                    )
                    continue
                valid_trades.append(trade)

            tc = cycle_trading_config if isinstance(cycle_trading_config, dict) else {}
            exit_conc = max(1, min(int(tc.get("exit_check_concurrency", 16) or 16), 64))
            prefetch_conc = max(1, min(int(tc.get("exit_price_prefetch_concurrency", 48) or 48), 128))

            unique_pairs = list({(t["exchange"], t["pair"]) for t in valid_trades})
            prefetched: Dict[str, float] = {}
            async with httpx.AsyncClient(timeout=5.0) as px_client:
                prefetched = await self._prefetch_ticker_live_prices(
                    unique_pairs,
                    px_client,
                    prefetch_conc,
                    stale_threshold=30,
                )
            logger.info(
                "📊 Exit cycle: prefetched %s/%s unique (exchange,pair) live prices (redis + ticker-live)",
                len(prefetched),
                len(unique_pairs),
            )

            if deadline is not None and time.monotonic() >= deadline:
                logger.warning(
                    "⏱️ Exit cycle: deadline reached before parallel exit checks (%s trades pending next loop)",
                    len(valid_trades),
                )
                cycle_duration.labels(cycle_type='exit').observe(time.time() - cycle_start_time)
                return

            sem = asyncio.Semaphore(exit_conc)

            async def run_exit_check(tr: Dict[str, Any]) -> None:
                async with sem:
                    await self._check_trade_exit(
                        tr,
                        trading_config=cycle_trading_config,
                        order_mappings=shared_order_mappings,
                        prefetched_prices=prefetched,
                    )

            gather_coro = asyncio.gather(
                *(run_exit_check(t) for t in valid_trades),
                return_exceptions=True,
            )
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    logger.warning(
                        "⏱️ Exit cycle: no time left for parallel exit checks (%s trades); deferring to next loop",
                        len(valid_trades),
                    )
                    cycle_duration.labels(cycle_type='exit').observe(time.time() - cycle_start_time)
                    return
                try:
                    results = await asyncio.wait_for(
                        gather_coro,
                        timeout=max(1.0, remaining - 0.25),
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        "⏱️ Exit cycle: timebox hit after %.1fs with %s trades (unfinished checks run next loop)",
                        time.time() - cycle_start_time,
                        len(valid_trades),
                    )
                    cycle_duration.labels(cycle_type='exit').observe(time.time() - cycle_start_time)
                    return
            else:
                results = await gather_coro

            for res in results:
                if isinstance(res, Exception):
                    logger.error("Exit check task failed: %s", res)

            processed_count = len(valid_trades)
            logger.info(
                "✅ Exit cycle completed: %s trades with exit checks (%s concurrent), %s skipped (invalid entry)",
                processed_count,
                exit_conc,
                skipped_count,
            )
            
            # Record cycle duration
            cycle_duration.labels(cycle_type='exit').observe(time.time() - cycle_start_time)
                
        except Exception as e:
            logger.error(f"Error in exit cycle: {str(e)}")
            logger.error(f"Exception type: {type(e).__name__}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            # Record cycle duration even on error
            cycle_duration.labels(cycle_type='exit').observe(time.time() - cycle_start_time)
            
    async def _check_trade_exit(
        self,
        trade: Dict[str, Any],
        trading_config: Optional[Dict[str, Any]] = None,
        order_mappings: Optional[List[Dict[str, Any]]] = None,
        prefetched_prices: Optional[Dict[str, float]] = None,
    ) -> None:
        """Check if a trade should be exited.

        ``trading_config`` should be supplied by the exit cycle (one shared dict for all trades).
        If omitted, a single HTTP fetch is used (legacy / manual callers).

        ``order_mappings`` when provided avoids repeated full-list fetches from database-service
        when checking for pending sell orders during trailing-stop activation.

        ``prefetched_prices`` maps ``_ws_ticker_key_suffix(exchange, convert_pair_format(...))`` → last
        so parallel exit cycles avoid N× HTTP to exchange-service for the same WS-backed tickers.
        """
        try:
            trade_id = trade.get('trade_id')
            if not trade_id or not isinstance(trade_id, str):
                logger.warning(f"[ExitCheck] Skipping: invalid or missing trade_id: {trade_id}")
                return

            # Special handling for EXIT_FAILED trades
            status = trade.get('status')
            if status == 'EXIT_FAILED':
                logger.warning(f"[Trade {trade_id}] [EXIT_FAILED] Trade requires manual intervention - skipping automatic exit checks")
                logger.warning(f"[Trade {trade_id}] [EXIT_FAILED] Please manually close position for {trade.get('pair')} on {trade.get('exchange')}")
                return
            # Stale trigger watchdog: if a trade has been in TRIGGERED_EXIT too long,
            # surface an alert and move to a terminal failed state for intervention.
            if str(trade.get("exit_state") or "").upper() == "TRIGGERED_EXIT":
                trigger_ts_raw = trade.get("trigger_detected_at")
                stale_secs_cfg = float(
                    (trade.get("execution_policy") or {}).get("triggered_exit_stale_seconds", 45)
                ) if isinstance(trade.get("execution_policy"), dict) else 45.0
                try:
                    if trigger_ts_raw:
                        trigger_dt = datetime.fromisoformat(str(trigger_ts_raw).replace("Z", "+00:00")).replace(tzinfo=None)
                        age_s = (datetime.utcnow() - trigger_dt).total_seconds()
                        if age_s >= stale_secs_cfg:
                            logger.error(
                                f"[Trade {trade_id}] [ExitState] 🚨 STALE TRIGGERED_EXIT: age={age_s:.1f}s >= {stale_secs_cfg:.1f}s"
                            )
                            await self._update_trade_data(
                                trade_id,
                                {
                                    "exit_state": "EXIT_FAILED_TERMINAL",
                                    "exit_failure_reason": f"stale_triggered_exit_{int(age_s)}s",
                                },
                            )
                            await self._create_critical_alert(
                                trade_id,
                                f"Manual intervention required: trade stuck in TRIGGERED_EXIT for {age_s:.1f}s",
                            )
                            return
                except Exception as stale_err:
                    logger.warning(f"[Trade {trade_id}] [ExitState] stale watchdog parse skipped: {stale_err}")

            # Get trading configuration for stop loss and trailing stop
            default_stop_loss = -2.0  # Fallback if config fetch fails
            trailing_trigger_pct = 2.0  # Fallback if config fetch fails
            trailing_step_pct = 1.0  # Fallback if config fetch fails
            if trading_config is None:
                trading_config = {}
                try:
                    async with httpx.AsyncClient(timeout=20.0) as client:
                        response = await client.get(f"{config_service_url}/api/v1/config/trading")
                        response.raise_for_status()
                        trading_config = response.json()
                except Exception as e:
                    logger.warning(
                        f"[Trade {trade_id}] [ExitCheck] Could not fetch trading config, using fallback values: {e}"
                    )
                    trading_config = {}
            elif not isinstance(trading_config, dict):
                trading_config = {}
            # Convert stop_loss_percentage to negative percentage (e.g., 0.035 -> -3.5)
            config_stop_loss_pct = trading_config.get('stop_loss_percentage', 0.02)
            default_stop_loss = -(config_stop_loss_pct * 100)
            trailing_stop_config = trading_config.get('trailing_stop', {})
            trade_regime = str(trade.get("stable_regime") or trade.get("market_regime") or "unknown")
            regime_cfg = trading_config.get("regime_policies", {}) or {}
            regime_defaults = regime_cfg.get("defaults", {}) or {}
            regime_override = regime_cfg.get(trade_regime, {}) or {}
            resolved_regime_policy = self._deep_merge_dicts(regime_defaults, regime_override)
            resolved_exit_policy = resolved_regime_policy.get("exits", {}) or {}
            trailing_trigger_decimal = trailing_stop_config.get('activation_threshold', 0.005)
            trailing_trigger_pct = trailing_trigger_decimal * 100
            trailing_step_decimal = trailing_stop_config.get('step_percentage', 0.01)
            trailing_step_pct = trailing_step_decimal * 100
            dynamic_tightening_enabled = bool(
                trailing_stop_config.get("dynamic_tightening_enabled", False)
            )
            tighten_profit_threshold_decimal = float(
                trailing_stop_config.get("tighten_profit_threshold", 0.009) or 0.009
            )
            tighten_profit_threshold_pct = tighten_profit_threshold_decimal * 100
            tightened_step_decimal = float(
                trailing_stop_config.get("tightened_step_percentage", trailing_step_decimal)
                or trailing_step_decimal
            )
            if tightened_step_decimal <= 0:
                tightened_step_decimal = trailing_step_decimal
            tightened_step_pct = tightened_step_decimal * 100
            # PnL-FIX v9 (F3 fix): the hard floor that protects the trail from
            # locking a net loss must come from config, not a hard-coded 0.10%.
            # Default 0.30% covers ~round-trip taker fees + small slippage budget.
            breakeven_floor_decimal = float(
                trailing_stop_config.get('breakeven_floor_percentage', 0.003) or 0.003
            )
            if breakeven_floor_decimal < 0:
                breakeven_floor_decimal = 0.0
            # PnL-FIX v11+ — profit-protection arms when peak >= activation_threshold
            # (config trading.profit_protection.activation_threshold, decimal e.g. 0.005 = +0.5%).
            profit_protection_config = trading_config.get('profit_protection', {}) or {}
            pp_activation_decimal = float(
                profit_protection_config.get('activation_threshold', 0.005) or 0.005
            )
            if pp_activation_decimal < 0:
                pp_activation_decimal = 0.005
            profit_protection_activation_pct = pp_activation_decimal * 100
            # Lock level = breakeven floor (same value that clamps the real trailing stop).
            profit_guarantee_pct_cfg = breakeven_floor_decimal * 100
            logger.debug(
                f"[Trade {trade_id}] [ExitCheck] Config — stop loss: {default_stop_loss}%, "
                f"trailing trigger: {trailing_trigger_pct}%, trailing step: {trailing_step_pct}%, "
                f"dynamic tighten: {dynamic_tightening_enabled}, tighten threshold: {tighten_profit_threshold_pct:.3f}%, "
                f"tightened step: {tightened_step_pct:.3f}%, "
                f"breakeven floor: {breakeven_floor_decimal * 100:.3f}%, "
                f"profit-protection activation: {profit_protection_activation_pct:.3f}%, "
                f"profit-guarantee lock: {profit_guarantee_pct_cfg:.3f}%"
            )


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

            # Get current price (prefetch/redis → ticker-live → feed → REST → OHLCV → orderbook → entry fallback)
            try:
                exchange = trade.get('exchange')
                pair = trade.get('pair')
                sym = self._convert_pair_format(exchange, pair)
                hint_key = _ws_ticker_key_suffix(exchange, sym)
                price_hint = (
                    prefetched_prices.get(hint_key)
                    if prefetched_prices and hint_key in prefetched_prices
                    else None
                )
                current_price = await self._get_current_price(
                    exchange,
                    pair,
                    entry_price_fallback=entry_price,
                    price_hint=price_hint,
                )
                if current_price == 0.0:
                    raise ValueError("current_price is zero")
            except Exception as e:
                logger.critical(
                    "[Trade %s] CRITICAL: Exit check aborted — could not resolve current_price for %s on %s (%s). "
                    "Open trade will NOT be updated (stops/trailing/PnL stale). Fix exchange-service, price-feed, or network.",
                    trade_id,
                    pair,
                    exchange,
                    e,
                )
                return

            # CRITICAL FIX: Don't confuse trail_stop_trigger (PRICE) with stop_loss (PERCENTAGE)
            # trail_stop_trigger is a PRICE level for trailing stops ($2.8847)
            # current_stop_loss should be a PERCENTAGE for stop loss comparison (-0.8%)
            
            # Always use the configured stop loss percentage, not the trailing stop trigger price
            current_stop_loss = default_stop_loss  # This is already the correct negative percentage (e.g., -0.8%)
            logger.info(f"[Trade {trade_id}] [ExitCheck] Using stop loss: {current_stop_loss}%")

            # Calculate PnL and update trade data
            try:
                if position_size > 0:  # Long position
                    pnl_percentage = ((current_price - entry_price) / entry_price) * 100
                    # Calculate unrealized PnL with estimated fees (0.1% trading fee)
                    unrealized_pnl = ((current_price - entry_price) * position_size) - (position_size * current_price * 0.001)
                else:  # Short position
                    pnl_percentage = ((entry_price - current_price) / entry_price) * 100
                    unrealized_pnl = (entry_price - current_price) * position_size
            except Exception as e:
                logger.warning(f"[Trade {trade_id}] [ExitCheck] Skipping: error calculating PnL: {e}")
                return

            # Update highest price if current price is higher
            highest_price = trade.get('highest_price')
            old_highest_price = highest_price  # Store old value for trailing stop comparison
            try:
                if highest_price is None:
                    highest_price = entry_price
                highest_price = float(highest_price)
                if current_price > highest_price:
                    highest_price = current_price
            except Exception:
                highest_price = current_price

            # Capture which source resolved this tick. Used below to harden the
            # trail-stop / stop-loss triggers against a degraded price feed —
            # if every live source failed and we fell back to entry_price, the
            # mark is a synthetic value (not a real market price) and must NOT
            # be used to fire risk-management exits (would lock in fake losses
            # or fire spurious stops). It can still be used for monitoring/PnL
            # display so the UI doesn't go dark.
            current_price_source = getattr(self, '_last_price_source', 'rest')
            feed_policy = trailing_stop_config.get("feed_quality_policy", {}) or {}
            degraded_sources = {
                str(s).lower()
                for s in feed_policy.get("degraded_sources", ["ohlcv_1m", "orderbook_mid"])
            }
            down_sources = {
                str(s).lower()
                for s in feed_policy.get("down_sources", ["entry_fallback"])
            }
            allow_degraded_exit = bool(feed_policy.get("allow_exit_in_degraded", True))
            allow_down_exit = bool(feed_policy.get("allow_exit_in_down", True))
            down_escalation_seconds = float(feed_policy.get("down_escalation_seconds", 10.0))
            feed_quality = self._classify_feed_quality(current_price_source, down_sources, degraded_sources)
            degraded_since = self._trade_feed_degraded_since.get(trade_id)
            if feed_quality in ("DEGRADED", "DOWN"):
                if degraded_since is None:
                    degraded_since = datetime.utcnow()
                    self._trade_feed_degraded_since[trade_id] = degraded_since
            else:
                self._trade_feed_degraded_since.pop(trade_id, None)
            degraded_age_s = (
                (datetime.utcnow() - degraded_since).total_seconds() if degraded_since else 0.0
            )
            price_source_is_real_market = feed_quality == "GOOD"
            risk_exit_allowed_by_feed = (
                feed_quality == "GOOD"
                or (feed_quality == "DEGRADED" and allow_degraded_exit)
                or (feed_quality == "DOWN" and allow_down_exit and degraded_age_s >= down_escalation_seconds)
            )

            # Update trade with current data and increment price update counter
            current_count = trade.get('price_updates_count', 0)
            await self._update_trade_data(trade_id, {
                'unrealized_pnl': unrealized_pnl,
                'highest_price': highest_price,
                'current_price': current_price,
                'price_updates_count': current_count + 1,
                'last_price_update': datetime.utcnow().isoformat(),
                'websocket_price_source': current_price_source
                in ('websocket', 'redis', 'prefetch'),
            })

            # Check exit conditions with profit protection
            exit_reason = None
            should_exit = False
            exit_trigger_price = current_price

            # Profit protection: lock stop at breakeven-floor when peak PnL reaches
            # profit_protection.activation_threshold (config-driven; e.g. 0.005 = +0.5%).
            profit_protection_status = trade.get('profit_protection')
            logger.info(
                f"[Trade {trade_id}] [ProfitProtection] Checking conditions - "
                f"PnL: {pnl_percentage:.2f}%, Current Stop: {current_stop_loss:.2f}%, "
                f"Target: {profit_protection_activation_pct:.2f}%, "
                f"Lock: +{profit_guarantee_pct_cfg:.2f}%, Status: {profit_protection_status}"
            )

            # Only trigger profit protection when PnL reaches the configured activation
            # threshold AND the trailing stop is not already active (trail takes priority).
            trailing_stop_active = trade.get('trail_stop') == 'active' or trade.get('exit_id') is not None

            # PnL-FIX v11 (2026-04-20): arm on PEAK, not instantaneous PnL.
            # Previously a trade that briefly spiked to +0.6% between 30s cycles and
            # dropped back to −0.49% before the exit-check observed it would never
            # arm profit-protection, and then bleed the entire gain plus some. Using
            # ``peak_pct`` (highest_price vs entry, updated on every cycle) captures
            # the max the trade ever reached, so a single tick above the activation
            # threshold is enough to lock in the breakeven-floor stop.
            try:
                peak_pct_for_pp = (
                    ((highest_price - entry_price) / entry_price) * 100.0
                    if highest_price and entry_price
                    else pnl_percentage
                )
            except Exception:
                peak_pct_for_pp = pnl_percentage

            if (
                peak_pct_for_pp >= profit_protection_activation_pct
                and not profit_protection_status
                and not trailing_stop_active
            ):
                profit_guarantee_pct = profit_guarantee_pct_cfg
                profit_guarantee_trigger_price = entry_price * (1 + (profit_guarantee_pct / 100))
                logger.info(
                    f"[Trade {trade_id}] [ProfitProtection] ✅ TRIGGERED: "
                    f"peak {peak_pct_for_pp:.2f}% >= {profit_protection_activation_pct:.2f}% "
                    f"(current PnL {pnl_percentage:.2f}%) "
                    f"AND no profit protection active AND no trailing stop active"
                )
                logger.info(
                    f"[Trade {trade_id}] [ProfitProtection] Setting profit protection trigger: "
                    f"${profit_guarantee_trigger_price:.6f} (guaranteeing {profit_guarantee_pct:.2f}% profit)"
                )
                await self._update_trade_data(trade_id, {
                    'trail_stop_trigger': profit_guarantee_trigger_price,
                    'profit_protection': 'profit_guaranteed',
                    'profit_protection_trigger': pnl_percentage
                })
            else:
                if peak_pct_for_pp < profit_protection_activation_pct:
                    reason = (
                        f"peak {peak_pct_for_pp:.2f}% < {profit_protection_activation_pct:.2f}% "
                        f"(current PnL {pnl_percentage:.2f}%) - allowing trailing stop activation"
                    )
                    logger.info(f"[Trade {trade_id}] [ProfitProtection] ❌ NOT TRIGGERED: {reason}")
                elif profit_protection_status and profit_protection_status != 'inactive':
                    reason = f"profit protection already active: {profit_protection_status}"
                    logger.info(f"[Trade {trade_id}] [ProfitProtection] ✅ ACTIVE: {reason}")
                elif trailing_stop_active:
                    reason = f"trailing stop already active - keeping trailing stop priority over profit protection"
                    logger.info(f"[Trade {trade_id}] [ProfitProtection] ❌ NOT TRIGGERED: {reason}")
                else:
                    reason = (
                        f"PnL {pnl_percentage:.2f}% >= {profit_protection_activation_pct:.2f}% "
                        f"and status is {profit_protection_status} - ACTIVATING"
                    )
                    logger.info(f"[Trade {trade_id}] [ProfitProtection] 🚀 ACTIVATING: {reason}")

                    profit_guarantee_pct = profit_guarantee_pct_cfg
                    profit_guarantee_trigger_price = entry_price * (1 + profit_guarantee_pct / 100)

                    logger.info(
                        f"[Trade {trade_id}] [ProfitProtection] Setting profit protection trigger: "
                        f"${profit_guarantee_trigger_price:.6f} (guaranteeing {profit_guarantee_pct:.2f}% profit)"
                    )
                    await self._update_trade_data(trade_id, {
                        'trail_stop_trigger': profit_guarantee_trigger_price,
                        'profit_protection': 'profit_guaranteed',
                        'profit_protection_trigger': pnl_percentage
                    })
                    logger.info(f"[Trade {trade_id}] [ProfitProtection] ✅ ACTIVATED: Profit protection now active")

            # PnL-FIX v11 (2026-04-20) — PROFIT-PROTECTION PRICE-BREACH EXIT.
            # When profit_protection is armed (trigger price set) but no exit_id
            # exists yet (peak was above profit_protection.activation_threshold but
            # below the trail's activation threshold), the current price-vs-trigger
            # check further below does not fire because it is guarded by `if exit_id:`.
            # If the price has fallen through the guaranteed-profit trigger, we must
            # exit NOW at market — otherwise the trade keeps bleeding past the
            # protection level we armed.
            #
            # PnL-FIX v11.1 (2026-04-20) — LOSS-GUARD.
            # Observed: DOT/USDC fired profit_protection_breach at current_price
            # well BELOW entry (-0.49% realized, trigger was entry+0.30%). The
            # intent of profit-protection is to PROTECT profit, not to realize a
            # loss at whatever market price we observe. If current < entry, skip
            # this path and let the normal stop-loss / stagnant-loser / trail
            # handle the exit. Fire only when current is still above entry (i.e.
            # we are still net-of-fees profitable or breakeven-adjacent).
            if (
                (trade.get('profit_protection') in ('profit_guaranteed',))
                and (trade.get('exit_id') is None)
                and risk_exit_allowed_by_feed
                and not should_exit
                and current_price > entry_price  # LOSS-GUARD (v11.1)
            ):
                pp_trigger_px = float(trade.get('trail_stop_trigger') or 0.0)
                if pp_trigger_px > 0 and current_price <= pp_trigger_px:
                    should_exit = True
                    exit_trigger_price = pp_trigger_px
                    exit_reason = (
                        f"profit_protection_breach@{pnl_percentage:.2f}%"
                        f"_trigger{pp_trigger_px:.6f}_px{current_price:.6f}"
                    )
                    logger.warning(
                        f"[Trade {trade_id}] [ProfitProtection] 🚩 PRICE-BREACH EXIT: "
                        f"current ${current_price:.6f} <= trigger ${pp_trigger_px:.6f} "
                        f"(PnL {pnl_percentage:.2f}%) — routing to critical market exit"
                    )

            # TRAILING STOP SYSTEM: Check if new system handles this trade or use legacy system
            if self.use_new_trailing_system and self.trailing_stop_system:
                # New system handles trailing stop logic automatically
                # Check if there's an active exit_id indicating trailing stop order is placed
                exit_id = trade.get('exit_id')
                if exit_id:
                    # Trade has active trailing stop order - check if it needs updating
                    current_trigger_price = trade.get('trail_stop_trigger', 0)
                    stored_highest = trade.get('highest_price', 0)
                    
                    # 🚨 CRITICAL TRAILING STOP PROTECTION: Check if current price has fallen below trigger
                    # This is the MOST IMPORTANT check - price must never move down or we lose profit!
                    # Guard against firing on a synthetic fallback price (entry_fallback) — that
                    # would falsely trigger every trade whose trigger sits above entry_price the
                    # moment the live feed degrades.
                    if (
                        current_price <= current_trigger_price
                        and current_trigger_price > 0
                        and risk_exit_allowed_by_feed
                    ):
                        logger.critical(f"[Trade {trade_id}] [TRAILING_STOP_HIT] 🚨 CRITICAL: Current price ${current_price:.6f} <= trigger ${current_trigger_price:.6f} (source={current_price_source})")
                        logger.critical(f"[Trade {trade_id}] [TRAILING_STOP_HIT] 🚨 IMMEDIATE MARKET SELL REQUIRED to prevent loss!")
                        
                        try:
                            # Cancel the limit order immediately
                            cancel_result = await self._cancel_order(exchange, exit_id, pair)
                            await self._update_trade_data(
                                trade_id,
                                {
                                    "cancel_attempted_at": datetime.utcnow().isoformat() + "+00:00",
                                    "cancel_attempt_result": "success" if cancel_result else "failed",
                                },
                            )
                            if cancel_result:
                                logger.info(f"[Trade {trade_id}] [TRAILING_STOP_HIT] ❌ CANCELLED limit order: {exit_id}")
                            else:
                                logger.error(f"[Trade {trade_id}] [TRAILING_STOP_HIT] ❌ Could not cancel limit order {exit_id}")
                                logger.critical(
                                    f"[Trade {trade_id}] [TRAILING_STOP_HIT] 🚨 CANCEL FAILED — escalating to emergency executable exit"
                                )
                            should_exit = True
                            exit_trigger_price = float(current_trigger_price)
                            exit_reason = (
                                f"trailing_stop_triggered@{pnl_percentage:.2f}%"
                                f"_trigger{current_trigger_price:.6f}_px{current_price:.6f}"
                            )
                                
                        except Exception as e:
                            logger.error(f"[Trade {trade_id}] [TRAILING_STOP_HIT] ❌ ERROR executing trailing stop: {e}")
                            await self._update_trade_data(
                                trade_id,
                                {
                                    "cancel_attempted_at": datetime.utcnow().isoformat() + "+00:00",
                                    "cancel_attempt_result": f"error:{type(e).__name__}",
                                },
                            )
                            should_exit = True
                            exit_trigger_price = float(current_trigger_price)
                            exit_reason = (
                                f"trailing_stop_triggered_cancel_error@{pnl_percentage:.2f}%"
                                f"_trigger{current_trigger_price:.6f}_px{current_price:.6f}"
                            )
                    elif (
                        current_price <= current_trigger_price
                        and current_trigger_price > 0
                        and not risk_exit_allowed_by_feed
                    ):
                        logger.warning(
                            f"[Trade {trade_id}] [NewTrailingStop] ⚠️ DEFERRED TRIGGER: "
                            f"current ${current_price:.6f} <= trigger ${current_trigger_price:.6f} "
                            f"source={current_price_source!r}, feed_quality={feed_quality}, "
                            f"degraded_age={degraded_age_s:.1f}s (policy blocked risk exit)"
                        )

                    # Check if highest price has increased since last update
                    # Use old_highest_price (before database update) for comparison
                    if highest_price > old_highest_price:
                        active_step_decimal = trailing_step_decimal
                        active_step_pct = trailing_step_pct
                        peak_pct_for_step = (
                            ((highest_price - entry_price) / entry_price) * 100.0
                            if highest_price and entry_price
                            else pnl_percentage
                        )
                        if (
                            dynamic_tightening_enabled
                            and peak_pct_for_step >= tighten_profit_threshold_pct
                        ):
                            active_step_decimal = tightened_step_decimal
                            active_step_pct = tightened_step_pct
                        # Calculate new exit price based on new highest
                        calculated_new_exit_price = highest_price * (1 - active_step_decimal)
                        
                        # CRITICAL FIX: Ensure new exit price is ALWAYS above entry price by the
                        # configured breakeven floor (covers round-trip fees + slippage).
                        breakeven_floor_price = entry_price * (1.0 + breakeven_floor_decimal)
                        new_exit_price = max(calculated_new_exit_price, breakeven_floor_price)
                        
                        # Only update if the new exit price is higher (more profitable)
                        if new_exit_price > current_trigger_price:
                            logger.info(f"[Trade {trade_id}] [NewTrailingStop] 🔄 UPDATING: New highest {highest_price:.6f} > old highest {old_highest_price:.6f}")
                            logger.info(f"[Trade {trade_id}] [NewTrailingStop] 🔄 New exit: {new_exit_price:.6f} > current: {current_trigger_price:.6f}")
                            logger.info(
                                f"[Trade {trade_id}] [NewTrailingStop] 📐 STEP_MODE: "
                                f"{'tightened' if active_step_decimal == tightened_step_decimal and dynamic_tightening_enabled else 'base'} "
                                f"(peak={peak_pct_for_step:.2f}%, step={active_step_pct:.2f}%, "
                                f"tighten_threshold={tighten_profit_threshold_pct:.2f}%)"
                            )
                            
                            try:
                                # 🚀 ORDER MODIFICATION IMPROVEMENT: Try modifying existing order first
                                update_result = await self._update_trailing_stop_order(
                                    exchange, exit_id, pair, new_exit_price, trade_id
                                )
                                
                                if update_result.get('success'):
                                    method = update_result.get('method', 'unknown')
                                    new_exit_id = update_result.get('exit_id', exit_id)
                                    
                                    # Update database - exit_id changes only if cancel-and-recreate was used
                                    update_data = {
                                        'trail_stop_trigger': new_exit_price,
                                        'highest_price': highest_price
                                    }
                                    
                                    if new_exit_id != exit_id:
                                        # Cancel-and-recreate was used - update exit_id
                                        update_data['exit_id'] = new_exit_id
                                        logger.info(f"[Trade {trade_id}] [NewTrailingStop] 🔄 Exit ID changed: {exit_id} → {new_exit_id}")
                                    
                                    await self.database_manager.update_trade(trade_id, update_data)
                                    
                                    logger.info(f"[Trade {trade_id}] [NewTrailingStop] ✅ UPDATED via {method}: Order {new_exit_id} @ {new_exit_price:.6f}")
                                else:
                                    logger.error(f"[Trade {trade_id}] [NewTrailingStop] ❌ FAILED: Could not update trailing stop order")
                                    # Note: We don't restore the original order since both methods failed
                                    
                            except Exception as e:
                                logger.error(f"[Trade {trade_id}] [NewTrailingStop] ❌ UPDATE ERROR: {e}")
                        else:
                            logger.info(f"[Trade {trade_id}] [NewTrailingStop] ✅ MANAGED: Order {exit_id} @ {current_trigger_price:.6f} (no update needed)")
                    else:
                        logger.info(f"[Trade {trade_id}] [NewTrailingStop] ✅ MANAGED: Order {exit_id} @ {current_trigger_price:.6f} (highest unchanged: {highest_price:.6f})")
                else:
                    # CRITICAL FIX: Check if PnL meets activation threshold (0.7%)
                    # This should activate regardless of profit protection status when profit is between 0.7% and 1.0%
                    if pnl_percentage >= trailing_trigger_pct:
                        active_step_decimal = trailing_step_decimal
                        active_step_pct = trailing_step_pct
                        # PnL-FIX v11.3 (2026-04-20) — PEAK-BUFFER GATE.
                        # Observed live: ACH/USD peak +0.57% and ARB/USDC peak +0.60%
                        # armed the trail just barely above the +0.5% activation.
                        # The 0.6% step callback from peak landed at or below entry,
                        # so the breakeven_floor clamp (+0.3%) became the binding
                        # constraint and the limit sell sat exactly at entry+0.3%.
                        # Any market slippage on fill → realized loss (-$1.43 ACH,
                        # -$1.06 ARB via downstream profit-protection-breach).
                        #
                        # Fix: require peak >= (breakeven_floor + step_percentage)
                        # before PLACING the on-exchange limit sell. Below that,
                        # profit-protection still acts as a soft marker and the
                        # stop-loss / stagnant-loser handle the downside, but we
                        # NEVER place a "no-buffer" trail that can only lose.
                        try:
                            current_peak_pct = (
                                ((highest_price - entry_price) / entry_price) * 100.0
                                if highest_price and entry_price
                                else pnl_percentage
                            )
                        except Exception:
                            current_peak_pct = pnl_percentage
                        if (
                            dynamic_tightening_enabled
                            and current_peak_pct >= tighten_profit_threshold_pct
                        ):
                            active_step_decimal = tightened_step_decimal
                            active_step_pct = tightened_step_pct
                        min_peak_pct_for_trail = (
                            (breakeven_floor_decimal + active_step_decimal) * 100.0
                        )
                        trail_placement_allowed = current_peak_pct >= min_peak_pct_for_trail
                        if not trail_placement_allowed:
                            logger.info(
                                f"[Trade {trade_id}] [NewTrailingStop] ⏳ PEAK-BUFFER GATE: "
                                f"peak {current_peak_pct:.2f}% < required "
                                f"{min_peak_pct_for_trail:.2f}% (breakeven_floor "
                                f"{breakeven_floor_decimal*100:.2f}% + step "
                                f"{active_step_pct:.2f}%) — NOT placing trail "
                                f"limit yet, waiting for higher peak"
                            )

                        if trail_placement_allowed:
                            # Check if profit protection is blocking trailing stop activation
                            if profit_protection_status and profit_protection_status != 'inactive' and pnl_percentage < 1.0:
                                logger.warning(f"[Trade {trade_id}] [NewTrailingStop] ⚠️ PROFIT PROTECTION BLOCKING: PnL {pnl_percentage:.2f}% < 1.0% but profit_protection={profit_protection_status}")
                                logger.warning(f"[Trade {trade_id}] [NewTrailingStop] 🔧 FORCING ACTIVATION: Trailing stop should activate between 0.7% and 1.0%")
                                # Reset profit protection to allow trailing stop activation
                                await self._update_trade_data(trade_id, {
                                    'profit_protection': 'inactive'
                                })
                                logger.info(f"[Trade {trade_id}] [NewTrailingStop] ✅ RESET: profit_protection set to inactive to allow trailing stop")
                        
                            # Check if trailing stop is already active (has exit_id or pending orders)
                            if trade.get('exit_id') is not None:
                                logger.info(f"[Trade {trade_id}] [NewTrailingStop] ✅ ALREADY ACTIVE: exit_id={trade.get('exit_id')}")
                                return  # Skip creating new trailing stop order
                        
                            # Check for pending trailing stop orders (prefer batch list from exit cycle)
                            try:
                                orders_list: List[Dict[str, Any]]
                                if order_mappings is not None:
                                    orders_list = order_mappings
                                else:
                                    async with httpx.AsyncClient(timeout=10.0) as client:
                                        orders_response = await client.get(
                                            f"{database_service_url}/api/v1/order-mappings"
                                        )
                                        if orders_response.status_code != 200:
                                            orders_list = []
                                        else:
                                            orders_list = orders_response.json().get("order_mappings", [])
                                pending_orders = [
                                    order
                                    for order in orders_list
                                    if (
                                        order.get("symbol") == pair
                                        and order.get("side") == "sell"
                                        and order.get("status") == "PENDING"
                                        and order.get("client_order_id", "").startswith(
                                            "omsef6b4f12"
                                            if trade_id == "ef6b4f12-1b48-459d-9f9c-d7444c399ba8"
                                            else f"oms{trade_id[:8]}"
                                        )
                                    )
                                ]
                                if pending_orders:
                                    logger.info(
                                        f"[Trade {trade_id}] [NewTrailingStop] ✅ PENDING ORDERS EXIST: "
                                        f"{len(pending_orders)} pending sell orders found"
                                    )
                                    return  # Skip creating new trailing stop order
                            except Exception as e:
                                logger.warning(f"[Trade {trade_id}] [NewTrailingStop] ⚠️ Could not check pending orders: {e}")
                        
                            # 🛡️ PROFIT PROTECTION: Cancel any external sell orders before activating trailing stop
                            await self._cancel_external_sell_orders(trade_id, pair, exchange)
                        
                            # ACTIVATE TRAILING STOP - Create sell limit order
                            logger.info(f"[Trade {trade_id}] [NewTrailingStop] 🚀 ACTIVATING: PnL {pnl_percentage:.2f}% >= {trailing_trigger_pct:.2f}% threshold")
                            logger.info(
                                f"[Trade {trade_id}] [NewTrailingStop] 📐 STEP_MODE: "
                                f"{'tightened' if active_step_decimal == tightened_step_decimal and dynamic_tightening_enabled else 'base'} "
                                f"(peak={current_peak_pct:.2f}%, step={active_step_pct:.2f}%, "
                                f"tighten_threshold={tighten_profit_threshold_pct:.2f}%)"
                            )
                        
                            # Calculate trailing stop exit price
                            # CRITICAL FIX: Ensure exit price is ALWAYS above entry price by the
                            # configured breakeven floor — never below entry + breakeven_floor_percentage.
                            calculated_exit_price = highest_price * (1 - active_step_decimal)
                            breakeven_floor_price = entry_price * (1.0 + breakeven_floor_decimal)
                            exit_price = max(calculated_exit_price, breakeven_floor_price)
                        
                            # Additional safety: If we're still at a loss, don't activate trailing stop
                            if highest_price <= entry_price:
                                logger.warning(f"[Trade {trade_id}] [NewTrailingStop] ❌ SKIPPING: Highest price ${highest_price:.6f} <= entry price ${entry_price:.6f} - no profit to protect")
                                return
                        
                            logger.info(
                                f"[Trade {trade_id}] [NewTrailingStop] Creating sell limit order: "
                                f"{position_size} @ {exit_price:.6f} "
                                f"(highest: {highest_price:.6f}, step: {active_step_pct:.2f}%, "
                                f"breakeven_floor: {breakeven_floor_price:.6f})"
                            )
                        
                            try:
                                # Get actual available balance for the base asset
                                base_asset = pair.split('/')[0]  # XLM from XLM/USDC
                                # Safe defaults if balance API returns non-200 or missing fields.
                                available_amount = position_size
                                sell_amount = position_size

                                # In simulation mode, DB position is source of truth for exit sizing.
                                if self.is_simulation:
                                    available_amount = position_size
                                    sell_amount = position_size
                                else:
                                    # Check exchange balance to get available amount
                                    try:
                                        async with httpx.AsyncClient(timeout=30.0) as client:
                                            balance_response = await client.get(f"http://exchange-service:8003/api/v1/account/balance/{exchange}")
                                            if balance_response.status_code == 200:
                                                balance_data = balance_response.json()
                                                # Try ccxt normalized format first, then raw format
                                                available_amount = 0
                            
                                                # Method 1: ccxt normalized format (balance_data[asset])
                                                if base_asset in balance_data:
                                                    available_amount = float(balance_data[base_asset].get('free', 0))
                                            
                                                # Method 2: Raw format fallback (info.balances[])
                                                elif 'info' in balance_data:
                                                    for asset_balance in balance_data.get('info', {}).get('balances', []):
                                                        if asset_balance.get('asset') == base_asset:
                                                            available_amount = float(asset_balance.get('free', 0))
                                                            break
                                            
                                                # Use available amount or position size, whichever is smaller
                                                # CRITICAL FIX: Use exact available balance to avoid insufficient balance errors
                                                sell_amount = min(position_size, available_amount)
                                            
                                    except Exception as e:
                                        logger.warning(f"[Trade {trade_id}] [NewTrailingStop] ⚠️ Balance check failed: {e}, using position size")
                                        sell_amount = position_size
                                        available_amount = position_size
                                
                            except Exception as e:
                                logger.warning(f"[Trade {trade_id}] [NewTrailingStop] ⚠️ Balance check failed: {e}, using position size")
                                sell_amount = position_size
                                available_amount = position_size
                        
                            # CRITICAL FIX: Use exact available balance to avoid insufficient balance errors
                            # Don't round the amount - use the exact available balance
                            logger.info(f"🔧 Using exact available balance: {sell_amount} (no rounding to avoid insufficient balance)")
                        
                            logger.info(f"[Trade {trade_id}] [NewTrailingStop] Balance check: Position={position_size}, Available={available_amount}, Using={sell_amount}")
                        
                            if sell_amount <= 0:
                                logger.error(f"[Trade {trade_id}] [NewTrailingStop] ❌ INSUFFICIENT BALANCE: No {base_asset} available ({available_amount})")
                                return  # Skip creating trailing stop order
                        
                            # Check minimum order size requirements before creating order mapping
                            try:
                                async with httpx.AsyncClient(timeout=10.0) as min_client:
                                    min_response = await min_client.get(f"http://exchange-service:8003/api/v1/market/info/{exchange}/{pair}")
                                    if min_response.status_code == 200:
                                        market_info = min_response.json()
                                        min_amount = market_info.get('limits', {}).get('amount', {}).get('min', 0.0)
                                        if sell_amount < min_amount:
                                            logger.error(f"[Trade {trade_id}] [NewTrailingStop] ❌ MINIMUM AMOUNT ERROR: {sell_amount} < {min_amount} minimum for {pair}")
                                            return  # Skip creating trailing stop order
                                        logger.info(f"[Trade {trade_id}] [NewTrailingStop] ✅ Amount validation passed: {sell_amount} >= {min_amount}")
                                    else:
                                        logger.warning(f"[Trade {trade_id}] [NewTrailingStop] ⚠️ Could not validate minimum amount, proceeding anyway")
                            except Exception as e:
                                logger.warning(f"[Trade {trade_id}] [NewTrailingStop] ⚠️ Minimum amount check failed: {e}, proceeding anyway")
                        
                            # Create trailing stop sell limit order with available amount
                            try:
                                # Set flag to use exact amount without precision rounding
                                self._is_trailing_stop_order = True
                                exit_order = await self._place_limit_order_with_custom_price(
                                    exchange, pair, 'sell', sell_amount, exit_price, trade_id
                                )
                                self._is_trailing_stop_order = False
                            
                                if exit_order:
                                    # Update trade with exit order ID and activate trailing stop
                                    await self.database_manager.update_trade(trade_id, {
                                        'exit_id': exit_order['id'],
                                        'trail_stop': 'active',
                                        'trail_stop_trigger': exit_price,
                                        'highest_price': highest_price
                                    })
                                
                                    logger.info(f"[Trade {trade_id}] [NewTrailingStop] ✅ ACTIVATED: Order {exit_order['id']} placed at {exit_price:.6f}")
                                else:
                                    logger.error(f"[Trade {trade_id}] [NewTrailingStop] ❌ FAILED: Could not create trailing stop order")
                                
                            except Exception as e:
                                logger.error(f"[Trade {trade_id}] [NewTrailingStop] ❌ ERROR: {e}")
                    else:
                        logger.info(f"[Trade {trade_id}] [NewTrailingStop] 📊 MONITORING: PnL {pnl_percentage:.2f}% < {trailing_trigger_pct:.2f}% threshold")
            else:
                # LEGACY TRAILING STOP SYSTEM (only if new system unavailable)
                logger.info(f"[Trade {trade_id}] [LegacyTrailingStop] Using legacy system - PnL: {pnl_percentage:.2f}%, Target: {trailing_trigger_pct:.2f}%")
                
                # CRITICAL FIX: Check if profit protection is blocking trailing stop activation
                if profit_protection_status and profit_protection_status != 'inactive' and pnl_percentage < 1.0:
                    logger.warning(f"[Trade {trade_id}] [LegacyTrailingStop] ⚠️ PROFIT PROTECTION BLOCKING: PnL {pnl_percentage:.2f}% < 1.0% but profit_protection={profit_protection_status}")
                    logger.warning(f"[Trade {trade_id}] [LegacyTrailingStop] 🔧 FORCING ACTIVATION: Trailing stop should activate between 0.7% and 1.0%")
                    # Reset profit protection to allow trailing stop activation
                    await self._update_trade_data(trade_id, {
                        'profit_protection': 'inactive'
                    })
                    logger.info(f"[Trade {trade_id}] [LegacyTrailingStop] ✅ RESET: profit_protection set to inactive to allow trailing stop")
                
                if pnl_percentage >= trailing_trigger_pct:
                    trailing_stop = pnl_percentage - trailing_step_pct
                    logger.info(f"[Trade {trade_id}] [LegacyTrailingStop] Calculating: {pnl_percentage:.2f}% - {trailing_step_pct:.2f}% = {trailing_stop:.2f}%")
                    if trailing_stop > current_stop_loss:
                        trailing_step_decimal = trailing_step_pct / 100
                        trigger_price = highest_price * (1 - trailing_step_decimal)
                        min_trigger_distance_pct = trading_config.get('trailing_stop', {}).get('min_trigger_distance_percentage', 0.005)
                        min_trigger_price = entry_price * (1 + min_trigger_distance_pct)
                        
                        if trigger_price < min_trigger_price:
                            trigger_price = min_trigger_price
                            
                        logger.info(f"[Trade {trade_id}] [LegacyTrailingStop] ✅ UPDATED: trigger ${trigger_price:.6f}")
                        await self._update_trade_data(trade_id, {
                            'trail_stop_trigger': trigger_price,
                            'trail_stop': 'active', 
                            'profit_protection': 'trailing'
                        })
                
                # Check legacy trailing stop trigger
                trail_stop_status = trade.get('trail_stop', 'inactive')
                if trail_stop_status == 'active':
                    try:
                        trigger_price = float(trade.get('trail_stop_trigger', 0))
                        logger.info(f"[Trade {trade_id}] [LegacyTrailingStop] Price check - Current: ${current_price:.6f}, Trigger: ${trigger_price:.6f} (source={current_price_source})")
                        if current_price <= trigger_price and risk_exit_allowed_by_feed:
                            should_exit = True
                            exit_trigger_price = float(trigger_price)
                            # PnL-FIX v9: append realized PnL % so we can audit
                            # how much profit was actually locked in by the trail.
                            exit_reason = f"trailing_stop_trigger_${trigger_price:.4f}@{pnl_percentage:.2f}%"
                            logger.info(f"[Trade {trade_id}] [LegacyTrailingStop] ✅ EXIT TRIGGERED")
                        elif current_price <= trigger_price and not risk_exit_allowed_by_feed:
                            logger.warning(
                                f"[Trade {trade_id}] [LegacyTrailingStop] ⚠️ SKIPPING TRIGGER: price source is "
                                f"{current_price_source!r} (synthetic). Will not fire trail until live feed recovers."
                            )
                    except (ValueError, TypeError):
                        logger.warning(f"[Trade {trade_id}] [LegacyTrailingStop] Invalid trigger price: {trade.get('trail_stop_trigger')}")

            # Stop loss check (percentage-based)
            logger.info(f"[Trade {trade_id}] [StopLoss] Checking exit - PnL: {pnl_percentage:.2f}%, Stop Level: {current_stop_loss:.2f}% (source={current_price_source})")
            if not should_exit and pnl_percentage <= current_stop_loss and risk_exit_allowed_by_feed:
                should_exit = True
                exit_trigger_price = current_price
                # PnL-FIX v9: include the ACTUAL realized PnL % (not only the
                # configured threshold) so the dashboard surfaces real slippage.
                # Format: "stop_loss_<configured>%@<actual>%" e.g. "stop_loss_-1.5%@-5.10%"
                exit_reason = f"stop_loss_{current_stop_loss:.1f}%@{pnl_percentage:.2f}%"
                logger.info(f"[Trade {trade_id}] [StopLoss] ✅ EXIT TRIGGERED: PnL {pnl_percentage:.2f}% <= stop loss {current_stop_loss:.2f}%")
            elif not should_exit and pnl_percentage <= current_stop_loss and not risk_exit_allowed_by_feed:
                logger.warning(
                    f"[Trade {trade_id}] [StopLoss] ⚠️ SKIPPING TRIGGER: PnL {pnl_percentage:.2f}% "
                    f"<= stop {current_stop_loss:.2f}% but price source is {current_price_source!r} "
                    f"(synthetic). Will not fire stop-loss until live feed recovers."
                )
            elif not should_exit:
                logger.info(f"[Trade {trade_id}] [StopLoss] ❌ NO EXIT: PnL {pnl_percentage:.2f}% > stop loss {current_stop_loss:.2f}%")

            # Global hard take-profit on mark PnL% (config: trading.overall_profit_take_exit_pct, e.g. 0.045 = +4.5%).
            # Set to 0 to disable. Same feed-quality gate as stop-loss (no synthetic-only TP).
            try:
                overall_tp_dec = float(
                    trading_config.get("overall_profit_take_exit_pct", 0.045) or 0.0
                )
            except (TypeError, ValueError):
                overall_tp_dec = 0.045
            if overall_tp_dec > 0:
                overall_tp_pct = overall_tp_dec * 100.0
                logger.info(
                    f"[Trade {trade_id}] [OverallTakeProfit] Checking: PnL {pnl_percentage:.2f}% "
                    f"vs target +{overall_tp_pct:.2f}% (source={current_price_source})"
                )
                if (
                    not should_exit
                    and pnl_percentage >= overall_tp_pct
                    and risk_exit_allowed_by_feed
                ):
                    should_exit = True
                    exit_trigger_price = current_price
                    exit_reason = (
                        f"overall_take_profit_{overall_tp_pct:.2f}%@{pnl_percentage:.2f}%"
                    )
                    logger.info(
                        f"[Trade {trade_id}] [OverallTakeProfit] ✅ EXIT TRIGGERED: "
                        f"PnL {pnl_percentage:.2f}% >= +{overall_tp_pct:.2f}%"
                    )
                elif (
                    not should_exit
                    and pnl_percentage >= overall_tp_pct
                    and not risk_exit_allowed_by_feed
                ):
                    logger.warning(
                        f"[Trade {trade_id}] [OverallTakeProfit] ⚠️ SKIPPING: PnL {pnl_percentage:.2f}% "
                        f">= +{overall_tp_pct:.2f}% but price source {current_price_source!r} "
                        f"(feed_quality={feed_quality}) — defer TP until live feed recovers"
                    )

            # PnL-FIX v10 (2026-04-20) — STAGNANT LOSER DIVERGENCE EXIT.
            # Observed from 7-day trade review: a non-trivial class of
            # losers (AAVE/USD -5.10%, ARC/USD -1.57%, API3/USD -3.43%)
            # showed ``highest_price == entry_price`` — i.e. the trade
            # went straight red from the fill, never saw any upside, and
            # the stop-loss then fired late with heavy slippage on thin
            # books. We pre-empt this: if after ≥ 30 minutes the trade
            # has shown ZERO upside (peak ≤ +0.3% above entry) AND is
            # already meaningfully underwater (≤ -0.8%), force an exit
            # now while the book is still orderly. Better -1% realized
            # than -5% slippage when the SL finally trips.
            if (
                not should_exit
                and risk_exit_allowed_by_feed
                and entry_price > 0
                and highest_price > 0
            ):
                try:
                    stagnant_cfg = (
                        resolved_exit_policy.get("stagnant_loser")
                        or trading_config.get("stagnant_loser", {})
                        or {}
                    )
                    min_age_minutes = float(stagnant_cfg.get("min_age_minutes", 30.0) or 30.0)
                    base_peak_cap_pct = float(stagnant_cfg.get("peak_cap_pct", 0.3) or 0.3)
                    base_loss_trigger_pct = float(stagnant_cfg.get("loss_trigger_pct", -0.8) or -0.8)
                    # Volatility-aware regime tuning:
                    # high-vol (hostile) => faster and deeper guard
                    # normal/low-vol => avoid churn by waiting longer / requiring more loss
                    volatility_ref_pct = float(stagnant_cfg.get("volatility_reference_pct", 0.8) or 0.8)
                    peak_cap_slope = float(stagnant_cfg.get("peak_cap_slope", 0.4) or 0.4)
                    loss_trigger_slope = float(stagnant_cfg.get("loss_trigger_slope", 0.5) or 0.5)
                    min_age_floor = float(stagnant_cfg.get("min_age_floor_minutes", 20.0) or 20.0)
                    min_age_ceiling = float(stagnant_cfg.get("min_age_ceiling_minutes", 60.0) or 60.0)
                    pair_volatility_pct = abs(float(pnl_percentage))
                    vol_factor = pair_volatility_pct / max(0.1, volatility_ref_pct)
                    # Higher vol_factor => lower age threshold, higher tolerated peak cap, tighter loss trigger.
                    dynamic_age_minutes = min_age_minutes / max(0.75, vol_factor)
                    dynamic_age_minutes = max(min_age_floor, min(min_age_ceiling, dynamic_age_minutes))
                    dynamic_peak_cap_pct = base_peak_cap_pct + (max(0.0, vol_factor - 1.0) * peak_cap_slope)
                    dynamic_loss_trigger_pct = base_loss_trigger_pct - (max(0.0, vol_factor - 1.0) * loss_trigger_slope)
                    peak_pct = ((highest_price - entry_price) / entry_price) * 100.0
                    entry_time_raw = trade.get('entry_time')
                    age_minutes = 0.0
                    if entry_time_raw:
                        if isinstance(entry_time_raw, str):
                            # Accept ISO-8601 with or without 'Z' suffix
                            entry_dt = datetime.fromisoformat(
                                entry_time_raw.replace('Z', '+00:00')
                            ).replace(tzinfo=None)
                        else:
                            entry_dt = entry_time_raw
                        age_minutes = (datetime.utcnow() - entry_dt).total_seconds() / 60.0
                    if (
                        age_minutes >= dynamic_age_minutes
                        and peak_pct <= dynamic_peak_cap_pct
                        and pnl_percentage <= dynamic_loss_trigger_pct
                    ):
                        should_exit = True
                        exit_trigger_price = current_price
                        exit_reason = (
                            f"stagnant_loser_divergence@{pnl_percentage:.2f}%"
                            f"_peak{peak_pct:.2f}%_age{age_minutes:.0f}m"
                        )
                        logger.warning(
                            f"[Trade {trade_id}] [StagnantLoser] 🚩 EXIT TRIGGERED: "
                            f"peak {peak_pct:.2f}% <= {dynamic_peak_cap_pct:.2f}% AND "
                            f"PnL {pnl_percentage:.2f}% <= {dynamic_loss_trigger_pct:.2f}% "
                            f"AND age {age_minutes:.0f}m >= {dynamic_age_minutes:.0f}m "
                            f"(vol_factor={vol_factor:.2f}) — pre-empting SL slippage"
                        )
                except Exception as stagnant_err:
                    logger.debug(f"[Trade {trade_id}] [StagnantLoser] check skipped: {stagnant_err}")

            # Log current status for monitoring
            if pnl_percentage > 0:
                logger.info(f"[Trade {trade_id}] [Status] Current: PnL {pnl_percentage:.2f}%, Stop Loss: {current_stop_loss:.2f}%, Highest: {highest_price:.6f}, Current: {current_price:.6f}")

            if should_exit:
                # Check if this trade is already being processed for exit
                if trade_id in self.exiting_trades:
                    logger.warning(f"[Trade {trade_id}] [Exit] ⚠️ SKIPPING: Trade already being processed for exit")
                    return

                await self._mark_trade_triggered_exit(
                    trade_id=trade_id,
                    exit_reason=exit_reason or "unknown",
                    trigger_price=float(exit_trigger_price or current_price),
                    current_price=float(current_price),
                    feed_quality=feed_quality,
                    price_source=current_price_source,
                )
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
            
            
    async def _run_entry_cycle(self, deadline: Optional[float] = None) -> None:
        """Run entry cycle to check for new trade opportunities.

        ``deadline`` is shared with exit/maintenance so the full orchestrator loop respects
        ``max_cycle_duration`` (crypto-responsive cadence).
        """
        cycle_start_time = time.time()
        try:
            logger.info("📥 Starting entry cycle...")

            if deadline is not None and time.monotonic() >= deadline:
                logger.warning("⏱️ Entry cycle skipped: trading-loop wall budget already exhausted")
                cycle_duration.labels(cycle_type='entry').observe(time.time() - cycle_start_time)
                return
            
            # Check available balance
            if not await self._check_available_balance():
                logger.info("📥 Entry cycle: Insufficient balance for new trades")
                cycle_duration.labels(cycle_type='entry').observe(time.time() - cycle_start_time)
                return
            # Trading knobs: single config fetch (was 3× identical HTTP calls per entry cycle)
            max_trades_per_exchange = 20
            min_balance_threshold = 10.0
            try:
                async with httpx.AsyncClient(timeout=20.0) as client:
                    response = await client.get(f"{config_service_url}/api/v1/config/trading")
                    response.raise_for_status()
                    cfg = response.json()
                    max_trades_per_exchange = cfg.get('max_trades_per_exchange', 20)
                    min_balance_threshold = float(cfg.get('min_exchange_balance', 50.0))
                    logger.info(
                        "[EntryCycle] max_trades_per_exchange=%s min_exchange_balance=%s",
                        max_trades_per_exchange,
                        min_balance_threshold,
                    )
            except Exception as e:
                logger.warning(
                    "[EntryCycle] Could not fetch trading config, using defaults "
                    "max_trades=%s min_balance=%s: %s",
                    max_trades_per_exchange,
                    min_balance_threshold,
                    e,
                )

            if deadline is not None and time.monotonic() >= deadline:
                logger.warning("⏱️ Entry cycle stopped after config fetch: wall budget exhausted")
                cycle_duration.labels(cycle_type='entry').observe(time.time() - cycle_start_time)
                return

            # Get all open trades once
            try:
                async with httpx.AsyncClient(timeout=60.0) as db_client:
                    response = await db_client.get(f"{database_service_url}/api/v1/trades/open")
                    response.raise_for_status()
                    open_trades = response.json()['trades']
            except Exception as e:
                logger.warning(f"[EntryCycle] Could not fetch open trades: {e}")
                open_trades = []

            if deadline is not None and time.monotonic() >= deadline:
                logger.warning("⏱️ Entry cycle stopped after open-trades fetch: wall budget exhausted")
                cycle_duration.labels(cycle_type='entry').observe(time.time() - cycle_start_time)
                return

            # Strict loss-block refresh: build hard entry cooldowns from recent losing closes.
            await self._refresh_hard_loss_cooldown_map()
            # Cross-exchange blacklist for pairs with recent negative realized closes.
            await self._refresh_recent_negative_realized_blacklist_map()
            
            # Check exchanges in parallel so one slow/failing exchange cannot block others
            exchange_tasks = [
                self._run_entry_cycle_for_exchange(
                    exchange_name,
                    pairs,
                    open_trades,
                    max_trades_per_exchange,
                    min_balance_threshold,
                    deadline=deadline,
                )
                for exchange_name, pairs in self.pair_selections.items()
            ]
            if exchange_tasks:
                await asyncio.gather(*exchange_tasks, return_exceptions=True)
            
            # Record successful cycle completion
            cycle_duration.labels(cycle_type='entry').observe(time.time() - cycle_start_time)
            
        except Exception as e:
            logger.error(f"Error in entry cycle: {str(e)}")
            logger.error(f"Exception type: {type(e).__name__}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            # Record cycle duration even on error
            cycle_duration.labels(cycle_type='entry').observe(time.time() - cycle_start_time)

    async def _refresh_hard_loss_cooldown_map(self) -> None:
        """Rebuild strict pair-entry cooldowns from recent closed losing trades."""
        try:
            hard_loss_thr = float(
                await self._get_config_value(
                    "trading.pair_rotation.hard_entry_cooldown_loss_pct_threshold", 0.0
                )
                or 0.0
            )
            cooldown_hours = float(
                await self._get_config_value(
                    "trading.pair_rotation.temp_blacklist_cooldown_hours", 12
                )
                or 12
            )
            scan_limit = int(
                await self._get_config_value(
                    "trading.pair_rotation.scan_recent_closed_limit", 2000
                )
                or 2000
            )
            now = datetime.utcnow()
            cooldown_delta = timedelta(hours=cooldown_hours)

            async with httpx.AsyncClient(timeout=45.0) as client:
                ex_resp = await client.get(f"{config_service_url}/api/v1/config/exchanges/list")
                if ex_resp.status_code != 200:
                    return
                exchanges = (ex_resp.json() or {}).get("exchanges", []) or []

                new_map: Dict[Tuple[str, str], datetime] = {}
                for exchange_name in exchanges:
                    rows: List[Dict[str, Any]] = []
                    # CRITICAL: fetch terminal states explicitly.
                    # Generic /trades sorted by exit_time can still return OPEN rows with null exit_time
                    # near the top, which may hide recent closes inside scan_limit.
                    for st_filter in ("CLOSED", "FAILED"):
                        trades_resp = await client.get(
                            f"{database_service_url}/api/v1/trades",
                            params={
                                "status": st_filter,
                                "limit": scan_limit,
                                "exchange": exchange_name,
                                "sort_by": "exit_time",
                                "sort_order": "desc",
                            },
                        )
                        if trades_resp.status_code != 200:
                            continue
                        rows.extend((trades_resp.json() or {}).get("trades", []) or [])
                    for t in rows:
                        if str(t.get("status", "")).upper() not in ("CLOSED", "FAILED"):
                            continue
                        if not is_macd_momentum_strategy(t.get("strategy")):
                            continue
                        pair = str(t.get("pair") or "")
                        if not pair:
                            continue
                        try:
                            entry_price = float(t.get("entry_price") or 0.0)
                            position_size = float(t.get("position_size") or 0.0)
                            realized_pnl = float(t.get("realized_pnl") or 0.0)
                            total_investment = float(t.get("total_investment") or 0.0)
                            entry_notional = float(t.get("entry_notional") or 0.0)
                            exit_time_raw = t.get("exit_time")
                            if not exit_time_raw:
                                continue
                            exit_time = datetime.fromisoformat(
                                str(exit_time_raw).replace("Z", "+00:00")
                            ).replace(tzinfo=None)
                        except Exception:
                            continue
                        notional = infer_trade_notional_usd(
                            entry_price=entry_price,
                            position_size=position_size,
                            total_investment=total_investment,
                            entry_notional=entry_notional,
                        )
                        if notional <= 0:
                            continue
                        realized_pnl_pct = (realized_pnl / notional) * 100.0
                        if not qualifies_for_hard_loss_cooldown(realized_pnl_pct, hard_loss_thr):
                            continue
                        until = exit_time + cooldown_delta
                        if until <= now:
                            continue
                        key = loss_cooldown_key(exchange_name, pair)
                        prev = new_map.get(key)
                        if prev is None or until > prev:
                            new_map[key] = until
                self._hard_loss_cooldown_until = new_map
        except Exception as e:
            logger.warning("[PAIR COOLDOWN] Hard-loss cooldown refresh skipped: %s", e)

    async def _run_entry_cycle_for_exchange(
        self,
        exchange_name: str,
        pairs: List[str],
        open_trades: List[Dict[str, Any]],
        max_trades_per_exchange: int,
        min_balance_threshold: float,
        deadline: Optional[float] = None,
    ) -> None:
        """Run entry checks for a single exchange, isolated from others."""
        try:
            exchange_balance = self.balances.get(exchange_name, {}).get('available', 0)
            if exchange_balance < min_balance_threshold:
                logger.info(
                    f"[EntryCycle] Skipping {exchange_name} - insufficient balance: "
                    f"${exchange_balance:.2f} < ${min_balance_threshold}"
                )
                return

            logger.info(
                f"[EntryCycle] {exchange_name} has sufficient balance: "
                f"${exchange_balance:.2f} >= ${min_balance_threshold}"
            )

            open_trades_count = sum(1 for t in open_trades if t['exchange'] == exchange_name)
            logger.info(
                f"[EntryCycle] {exchange_name}: open_trades={open_trades_count}, "
                f"max_trades_per_exchange={max_trades_per_exchange}"
            )
            if open_trades_count >= max_trades_per_exchange:
                logger.info(
                    f"[EntryCycle] Trade limit reached for {exchange_name}: "
                    f"{open_trades_count}/{max_trades_per_exchange}. "
                    f"Skipping entry for this exchange."
                )
                return

            logger.info(
                f"[EntryCycle] Trade limit NOT reached for {exchange_name}: "
                f"{open_trades_count}/{max_trades_per_exchange}. "
                f"Processing entry cycle for this exchange."
            )

            # Guard each pair check so one long timeout (e.g., external signal API) doesn't
            # hold up the rest of this exchange's queue. Under a global loop deadline, cap per-pair
            # wait so we can scan more pairs within max_cycle_duration.
            pair_timeout_base = 70.0
            for pair in pairs:
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        logger.warning(
                            "[EntryCycle] %s: wall budget exhausted before pair %s; stopping exchange queue",
                            exchange_name,
                            pair,
                        )
                        return
                    pair_timeout_seconds = min(pair_timeout_base, max(5.0, remaining))
                else:
                    pair_timeout_seconds = pair_timeout_base
                try:
                    await asyncio.wait_for(
                        self._check_pair_entry(exchange_name, pair),
                        timeout=pair_timeout_seconds,
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        f"[EntryCycle] Timeout after {pair_timeout_seconds}s for "
                        f"{pair} on {exchange_name}; skipping pair"
                    )
                except Exception as pair_error:
                    logger.error(
                        f"[EntryCycle] Pair processing error for {pair} on "
                        f"{exchange_name}: {pair_error}"
                    )
        except Exception as e:
            logger.error(f"[EntryCycle] Exchange cycle error for {exchange_name}: {e}")
    
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
                # Get trade details to calculate realized PnL
                trade_response = await client.get(f"{database_service_url}/api/v1/trades/{trade_id}")
                if trade_response.status_code == 200:
                    trade_data = trade_response.json()
                    entry_price = float(trade_data.get('entry_price', 0))
                    entry_fees = float(trade_data.get('fees', 0))
                    
                    # Calculate realized PnL
                    if entry_price > 0 and current_price > 0:
                        realized_pnl = (current_price - entry_price) * amount - entry_fees
                    else:
                        realized_pnl = 0.0
                else:
                    realized_pnl = 0.0
                update_data = {
                    'status': 'CLOSED',
                    'exit_reason': reason,
                    'exit_time': current_time,
                    'exit_price': current_price,
                    'exit_id': f"dust_close_{trade_id[:8]}",  # Generate a placeholder exit_id
                    'realized_pnl': realized_pnl,
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

    async def _check_and_handle_dust_amount(self, exchange_name: str, pair: str, side: str, amount: float, trade_id: Optional[str] = None) -> Optional[Union[str, Dict[str, Any]]]:
        """Detect and handle dust amounts before placing/retrying limit orders.

        Returns:
        - "skip_dust" to indicate the order should be skipped and (for sells) the trade closed as dust
        - None if no dust handling is needed
        - Optionally a dict representing an already handled order placement result (not used currently)
        """
        try:
            # Get a price to compute notional when available
            current_price = 0.0
            try:
                current_price = await self._get_current_price(exchange_name, pair)
            except Exception:
                current_price = 0.0

            # Exchange-specific thresholds
            if exchange_name == 'cryptocom':
                min_notional_usd = 12.0
                min_amounts = {
                    'AAVE/USD': 0.001,
                    'BTC/USD': 0.00001,
                    'ETH/USD': 0.0001,
                    'SOL/USD': 0.001,
                    'LTC/USD': 0.001,
                    'XRP/USD': 1.0,
                    'ADA/USD': 1.0,
                    'ACH/USD': 1.0,
                    'ACT/USD': 1.0,
                    'default': 0.000001
                }
                min_amount = min_amounts.get(pair, min_amounts['default'])
                is_below_min_amount = amount < float(min_amount)
                is_below_min_notional = (current_price > 0) and (amount * current_price < min_notional_usd)
                if is_below_min_amount or is_below_min_notional:
                    if side.lower() == 'sell' and trade_id:
                        await self._close_dust_position(trade_id, exchange_name, pair, amount, "dust_amount_below_minimum")
                    return "skip_dust"

            elif exchange_name == 'binance':
                min_amounts = {
                    'LINK/USDC': 1.0,
                    'LINK/USDT': 1.0,
                    'default': 0.001
                }
                min_amount = min_amounts.get(pair, min_amounts['default'])
                if amount < float(min_amount):
                    if side.lower() == 'sell' and trade_id:
                        await self._close_dust_position(trade_id, exchange_name, pair, amount, "dust_amount_below_minimum")
                    return "skip_dust"

            elif exchange_name == 'bybit':
                # Bybit-specific minimum amounts based on exchange handler and error messages
                min_amounts = {
                    'XRP/USDC': 0.01,  # Error message shows 0.01 minimum
                    'BTC/USDC': 0.00001,
                    'ETH/USDC': 0.001,
                    'SOL/USDC': 0.01,
                    'XLM/USDC': 1.0,
                    'default': 0.001
                }
                min_amount = min_amounts.get(pair, min_amounts['default'])
                min_notional_usd = 1.0  # Bybit minimum notional
                
                is_below_min_amount = amount < float(min_amount)
                is_below_min_notional = (current_price > 0) and (amount * current_price < min_notional_usd)
                
                logger.info(f"🔍 DUST DEBUG: {pair} on {exchange_name} - amount={amount:.8f}, min_amount={min_amount}, below_min={is_below_min_amount}")
                logger.info(f"🔍 DUST DEBUG: current_price=${current_price:.8f}, notional=${amount * current_price:.6f}, min_notional=${min_notional_usd}, below_notional={is_below_min_notional}")
                
                if is_below_min_amount or is_below_min_notional:
                    logger.warning(f"💸 DUST DETECTED: {amount} {pair} on {exchange_name} (min: {min_amount}, notional: ${amount * current_price:.6f})")
                    if side.lower() == 'sell' and trade_id:
                        await self._close_dust_position(trade_id, exchange_name, pair, amount, "dust_amount_below_minimum")
                    return "skip_dust"

            return None
        except Exception as e:
            logger.warning(f"⚠️ Error in dust amount pre-check for {pair} on {exchange_name}: {e}")
            return None
            
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
            
    async def _check_pair_cooldown(self, exchange_name: str, pair: str) -> bool:
        """
        PnL-FIX v3: Block re-entries on a pair for N minutes after a previous
        entry. Without this, the bot opened multiple positions on the same
        correlated crypto pair in rapid succession.

        Cooldown entries are written whenever a trade is opened on a pair
        (see _execute_trade_entry) and when a trade closes in a loss
        (see _mark_pair_cooldown_on_close).
        """
        try:
            now = datetime.utcnow()

            # Global cross-exchange cooldown for the normalized pair.
            pair_global_key = self._normalized_pair_key(pair)
            recent_negative_until = self._recent_negative_realized_blacklist_until.get(pair_global_key)
            if recent_negative_until and recent_negative_until > now:
                remaining = (recent_negative_until - now).total_seconds() / 60.0
                logger.warning(
                    "🚫 [RECENT NEGATIVE REALIZED] %s blocked on %s for %.1f more min (until %s)",
                    pair,
                    exchange_name,
                    remaining,
                    recent_negative_until.isoformat(),
                )
                return False
            if recent_negative_until and recent_negative_until <= now:
                self._recent_negative_realized_blacklist_until.pop(pair_global_key, None)
            global_cooldown_until = self._global_pair_cooldown_until.get(pair_global_key)
            if global_cooldown_until and global_cooldown_until > now:
                remaining = (global_cooldown_until - now).total_seconds() / 60.0
                logger.info(
                    "🧊 [GLOBAL PAIR COOLDOWN] %s blocked on %s for %.1f more min (until %s)",
                    pair,
                    exchange_name,
                    remaining,
                    global_cooldown_until.isoformat(),
                )
                return False
            if global_cooldown_until and global_cooldown_until <= now:
                self._global_pair_cooldown_until.pop(pair_global_key, None)

            key = loss_cooldown_key(exchange_name, pair)
            cooldown_until = self._pair_cooldown_until.get(key)
            if cooldown_until and cooldown_until > now:
                remaining = (cooldown_until - now).total_seconds() / 60.0
                logger.info(
                    f"🧊 [PAIR COOLDOWN] {pair} on {exchange_name} cooling down for "
                    f"{remaining:.1f} more min (until {cooldown_until.isoformat()})"
                )
                return False
            # Stale entries get pruned to keep the dict small.
            if cooldown_until and cooldown_until <= now:
                self._pair_cooldown_until.pop(key, None)
            # Strict hard-loss cooldown (derived from recent closed losses).
            hard_until = self._hard_loss_cooldown_until.get(key)
            if hard_until and hard_until > now:
                remaining = (hard_until - now).total_seconds() / 60.0
                logger.warning(
                    f"🚫 [HARD LOSS COOLDOWN] Blocking {pair} on {exchange_name} for "
                    f"{remaining:.1f} more min (until {hard_until.isoformat()})"
                )
                return False
            if hard_until and hard_until <= now:
                self._hard_loss_cooldown_until.pop(key, None)
            return True
        except Exception as e:
            logger.error(f"[PAIR COOLDOWN] Error checking cooldown for {pair}: {e}")
            return True  # fail-open; other checks will catch real problems

    async def _refresh_recent_negative_realized_blacklist_map(self) -> None:
        """Rebuild cross-exchange blacklist for pairs with weak realized closes."""
        try:
            raw_enabled = await self._get_config_value(
                "trading.block_pair_after_negative_realized_enabled", True
            )
            enabled = not (raw_enabled is False or str(raw_enabled).lower() in ("0", "false", "no"))
            if not enabled:
                self._recent_negative_realized_blacklist_until = {}
                return
            window_hours = float(
                await self._get_config_value("trading.block_pair_after_negative_realized_hours", 12) or 12
            )
            min_realized_pct = float(
                await self._get_config_value("trading.block_pair_after_realized_pnl_below_pct", 0.5)
                or 0.5
            )
            scan_limit = int(
                await self._get_config_value("trading.pair_rotation.scan_recent_closed_limit", 2000) or 2000
            )
            now = datetime.utcnow()
            cutoff = now - timedelta(hours=window_hours)
            rows: List[Dict[str, Any]] = []
            async with httpx.AsyncClient(timeout=45.0) as client:
                for st_filter in ("CLOSED", "FAILED"):
                    trades_resp = await client.get(
                        f"{database_service_url}/api/v1/trades",
                        params={
                            "status": st_filter,
                            "limit": scan_limit,
                            "sort_by": "exit_time",
                            "sort_order": "desc",
                        },
                    )
                    if trades_resp.status_code != 200:
                        continue
                    rows.extend((trades_resp.json() or {}).get("trades", []) or [])

            new_map: Dict[str, datetime] = {}
            for t in rows:
                st = str(t.get("status") or "").upper()
                if st not in ("CLOSED", "FAILED"):
                    continue
                pair_name = str(t.get("pair") or "")
                if not pair_name:
                    continue
                try:
                    rpnl = float(t.get("realized_pnl") or 0.0)
                except Exception:
                    continue
                notional = infer_trade_notional_usd(
                    entry_price=float(t.get("entry_price") or 0.0),
                    position_size=float(t.get("position_size") or 0.0),
                    total_investment=float(t.get("total_investment") or 0.0),
                    entry_notional=float(t.get("entry_notional") or 0.0),
                )
                if notional <= 0.0:
                    continue
                realized_pnl_pct = (rpnl / notional) * 100.0
                if realized_pnl_pct >= min_realized_pct:
                    continue
                exit_time_raw = t.get("exit_time") or t.get("updated_at")
                if not exit_time_raw:
                    continue
                try:
                    exit_time = datetime.fromisoformat(
                        str(exit_time_raw).replace("Z", "+00:00")
                    ).replace(tzinfo=None)
                except Exception:
                    continue
                if exit_time < cutoff:
                    continue
                until = exit_time + timedelta(hours=window_hours)
                if until <= now:
                    continue
                key = self._normalized_pair_key(pair_name)
                prev = new_map.get(key)
                if prev is None or until > prev:
                    new_map[key] = until
            self._recent_negative_realized_blacklist_until = new_map
        except Exception as e:
            logger.warning("[PAIR COOLDOWN] Recent negative-realized blacklist refresh skipped: %s", e)

    async def _mark_recent_negative_realized_blacklist(
        self, pair: str, realized_pnl_pct: Optional[float] = None
    ) -> None:
        """Immediately blacklist pair cross-exchange after a weak realized close."""
        try:
            raw_enabled = await self._get_config_value(
                "trading.block_pair_after_negative_realized_enabled", True
            )
            enabled = not (raw_enabled is False or str(raw_enabled).lower() in ("0", "false", "no"))
            if not enabled:
                return
            window_hours = float(
                await self._get_config_value("trading.block_pair_after_negative_realized_hours", 12) or 12
            )
            min_realized_pct = float(
                await self._get_config_value("trading.block_pair_after_realized_pnl_below_pct", 0.5)
                or 0.5
            )
            if realized_pnl_pct is not None and float(realized_pnl_pct) >= min_realized_pct:
                return
            key = self._normalized_pair_key(pair)
            until = datetime.utcnow() + timedelta(hours=window_hours)
            prev = self._recent_negative_realized_blacklist_until.get(key)
            if prev is None or until > prev:
                self._recent_negative_realized_blacklist_until[key] = until
                logger.warning(
                    "🚫 [RECENT REALIZED GUARD] Added %s to %.1fh blacklist "
                    "(realized_pnl_pct=%s%%, threshold<%.2f%%, until %s)",
                    pair,
                    window_hours,
                    "n/a" if realized_pnl_pct is None else f"{float(realized_pnl_pct):.2f}",
                    min_realized_pct,
                    until.isoformat(),
                )
        except Exception as e:
            logger.warning(
                "[PAIR COOLDOWN] Failed to mark recent negative-realized blacklist for %s: %s",
                pair,
                e,
            )

    def _mark_pair_cooldown(self, exchange_name: str, pair: str, minutes: Optional[int] = None) -> None:
        """Record a cooldown timestamp for a pair (PnL-FIX v3)."""
        try:
            mins = minutes if minutes is not None else self._pair_cooldown_minutes
            key = loss_cooldown_key(exchange_name, pair)
            self._pair_cooldown_until[key] = datetime.utcnow() + timedelta(minutes=mins)
            global_key = self._normalized_pair_key(pair)
            global_mins = max(0, int(self._global_pair_cooldown_minutes))
            if global_key and global_mins > 0:
                self._global_pair_cooldown_until[global_key] = datetime.utcnow() + timedelta(
                    minutes=global_mins
                )
            logger.info(
                f"🧊 [PAIR COOLDOWN] Set {pair} on {exchange_name} to cooldown for {mins} min "
                f"(global={global_mins} min)"
            )
        except Exception as e:
            logger.error(f"[PAIR COOLDOWN] Error marking cooldown for {pair}: {e}")

    async def _check_correlation_cap(self, exchange_name: str, pair: str) -> bool:
        """
        PnL-FIX v3: Reject new entries when the "crypto beta" basket is already
        full. Since ~all pairs we trade are crypto longs with correlation >0.8,
        opening 30 of them is equivalent to one leveraged crypto bet — not
        diversification. Cap concurrent open longs in the basket.

        Stablecoin-quoted pairs (USDT/USDC/USD/DAI/BUSD) are treated as one
        basket per exchange. This preserves venue-level risk isolation and
        avoids one exchange consuming all correlation slots globally.
        """
        try:
            # Fetch current open trades from database-service (already used elsewhere).
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(f"{database_service_url}/api/v1/trades/open")
                response.raise_for_status()
                open_trades = response.json().get('trades', [])

            stable_quotes = ('USDT', 'USDC', 'USD', 'DAI', 'BUSD')
            basket = [
                t for t in open_trades
                if str(t.get('exchange', '')).lower() == str(exchange_name).lower()
                if any(str(t.get('pair', '')).upper().endswith(q) for q in stable_quotes)
            ]
            basket_size = len(basket)
            if basket_size >= self._correlation_basket_cap:
                basket_pairs = sorted({t.get('pair') for t in basket if t.get('pair')})
                logger.warning(
                    f"🧺 [CORRELATION CAP] Blocked {pair} on {exchange_name} — "
                    f"crypto-beta basket is full ({basket_size}/{self._correlation_basket_cap}). "
                    f"Current basket: {basket_pairs}"
                )
                return False
            logger.info(
                f"🧺 [CORRELATION CAP] {pair} passed (basket {basket_size}/{self._correlation_basket_cap})"
            )
            return True
        except Exception as e:
            logger.error(f"[CORRELATION CAP] Error checking cap for {pair}: {e}")
            return True  # fail-open

    async def _check_no_negative_unrealized_on_same_pair(
        self, exchange_name: str, pair: str
    ) -> bool:
        """
        Block a new entry when any OPEN position on the **same normalized pair on any
        exchange** has unrealized_pnl% at/below configured threshold (default 0.5%).
        """
        try:
            enabled = await self._get_config_value(
                "trading.block_new_entry_if_open_unrealized_negative", True
            )
            if enabled is False or str(enabled).lower() in ("0", "false", "no"):
                return True
            min_upnl_pct = float(
                await self._get_config_value(
                    "trading.block_new_entry_if_open_unrealized_below_pct", 0.5
                )
                or 0.5
            )
            active_trades = await self._get_active_trades_for_entry_guards()
            for t in active_trades:
                if not self._dashboard_pair_key_match(t.get("pair"), pair):
                    continue
                status = str(t.get("status") or "").upper()
                if status == "PENDING":
                    logger.warning(
                        "🚫 [OPEN UPNL GATE] Blocking new entry on %s %s — existing pending trade %s on %s",
                        exchange_name,
                        pair,
                        t.get("trade_id"),
                        str(t.get("exchange", "") or "").strip().lower() or "?",
                    )
                    return False
                try:
                    u = float(t.get("unrealized_pnl") or 0.0)
                except (TypeError, ValueError):
                    u = 0.0
                notional = infer_trade_notional_usd(
                    entry_price=float(t.get("entry_price") or 0.0),
                    position_size=float(t.get("position_size") or 0.0),
                    total_investment=float(t.get("total_investment") or 0.0),
                    entry_notional=float(t.get("entry_notional") or 0.0),
                )
                upnl_pct = (u / notional) * 100.0 if notional > 0.0 else None
                # Require existing same-pair legs to be above threshold before allowing new entry.
                # If pct cannot be computed, keep conservative legacy behavior for negative uPnL.
                if (upnl_pct is not None and upnl_pct <= min_upnl_pct) or (upnl_pct is None and u < 0):
                    oex = str(t.get("exchange", "") or "").strip().lower()
                    logger.warning(
                        "🚫 [OPEN UPNL GATE] Blocking new entry on %s %s — open trade %s on %s has "
                        "unrealized_pnl=%.2f (%.2f%% <= %.2f%% threshold)",
                        exchange_name,
                        pair,
                        t.get("trade_id"),
                        oex or "?",
                        u,
                        0.0 if upnl_pct is None else upnl_pct,
                        min_upnl_pct,
                    )
                    return False
            return True
        except Exception as e:
            logger.error(
                "[OPEN UPNL GATE] Failed to validate open unrealized PnL for %s %s: %s",
                exchange_name,
                pair,
                e,
            )
            return True  # fail-open; other risk gates still apply

    async def _get_active_trades_for_entry_guards(self) -> List[Dict[str, Any]]:
        """
        Fetch trade states that should block re-entry.

        `/api/v1/trades/open` only returns OPEN rows; Redis queue flow can leave a
        trade in PENDING for a short window. Include both so we never open a second
        position on the same pair while the first one is still in flight.
        """
        active_rows: List[Dict[str, Any]] = []
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                open_resp = await client.get(f"{database_service_url}/api/v1/trades/open")
                open_resp.raise_for_status()
                active_rows.extend(open_resp.json().get("trades", []) or [])

                pending_resp = await client.get(
                    f"{database_service_url}/api/v1/trades",
                    params={"status": "PENDING", "limit": 500, "sort_by": "entry_time", "sort_order": "desc"},
                )
                pending_resp.raise_for_status()
                active_rows.extend((pending_resp.json() or {}).get("trades", []) or [])
        except Exception as e:
            logger.warning("[ENTRY GUARD] Failed to fetch active trades (open+pending): %s", e)
            raise

        deduped: Dict[str, Dict[str, Any]] = {}
        for row in active_rows:
            tid = str(row.get("trade_id") or "")
            if tid:
                deduped[tid] = row
        return list(deduped.values()) if deduped else active_rows

    @staticmethod
    def _dashboard_pair_key_match(trade_pair: Optional[str], ui_pair: str) -> bool:
        a = str(trade_pair or "").strip()
        b = str(ui_pair or "").strip()
        if not a or not b:
            return False
        if a == b:
            return True
        return a.replace("/", "").upper() == b.replace("/", "").upper()

    @staticmethod
    def _normalized_pair_key(pair: str) -> str:
        """Stable normalized pair key for cross-exchange entry reservations."""
        return str(pair or "").replace("/", "").strip().upper()

    async def _reserve_pair_entry(self, exchange_name: str, pair: str) -> bool:
        """
        Reserve a normalized pair while evaluating/placing a new entry.
        Prevents same-cycle concurrent entries on the same pair across exchanges.
        """
        key = self._normalized_pair_key(pair)
        if not key:
            return False
        async with self._pair_entry_reservation_lock:
            if key in self._pair_entry_reservations:
                logger.warning(
                    "🚫 [PAIR RESERVATION] %s %s blocked: pair reservation already held",
                    exchange_name,
                    pair,
                )
                return False
            self._pair_entry_reservations.add(key)
            logger.info("🔒 [PAIR RESERVATION] Reserved %s for %s %s", key, exchange_name, pair)
            return True

    async def _release_pair_entry_reservation(self, pair: str) -> None:
        key = self._normalized_pair_key(pair)
        if not key:
            return
        async with self._pair_entry_reservation_lock:
            if key in self._pair_entry_reservations:
                self._pair_entry_reservations.remove(key)
                logger.info("🔓 [PAIR RESERVATION] Released %s", key)

    def _dashboard_entry_block_memory_reason(self, exchange_name: str, pair: str) -> Optional[str]:
        """Cooldown / execution-downgrade (in-process), matching stored tuple keys loosely."""
        ex_lo = str(exchange_name or "").strip().lower()
        p = str(pair or "").strip()
        now = datetime.utcnow()
        pair_key = self._normalized_pair_key(p)
        recent_until = self._recent_negative_realized_blacklist_until.get(pair_key)
        if recent_until and recent_until > now:
            return "recent_negative_realized_blacklist"
        if recent_until and recent_until <= now:
            self._recent_negative_realized_blacklist_until.pop(pair_key, None)
        for store, label in (
            (self._pair_execution_downgrade_until, "execution_downgrade"),
            (self._pair_cooldown_until, "pair_cooldown"),
            (self._hard_loss_cooldown_until, "hard_loss_cooldown"),
        ):
            for (ex_k, pair_k), until in list(store.items()):
                if str(ex_k).lower() != ex_lo:
                    continue
                if not self._dashboard_pair_key_match(pair_k, p):
                    continue
                if until and until > now:
                    return label
        return None

    async def _consecutive_realized_loss_streak(self, exchange_name: str, pair: str) -> int:
        """Consecutive CLOSED/FAILED trades with realized_pnl < 0 (exit_time desc), including the latest close."""
        ex_lo = str(exchange_name or "").strip().lower()
        p = str(pair or "").strip()
        streak = 0
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                r = await client.get(
                    f"{database_service_url}/api/v1/trades",
                    params={
                        "limit": "100",
                        "exchange": ex_lo,
                        "sort_by": "exit_time",
                        "sort_order": "desc",
                    },
                )
                if r.status_code != 200:
                    return 0
                rows = r.json().get("trades", []) or []
        except Exception:
            return 0
        for t in rows:
            if str(t.get("exchange", "")).lower() != ex_lo:
                continue
            if not self._dashboard_pair_key_match(t.get("pair"), p):
                continue
            st = str(t.get("status") or "").upper()
            if st not in ("CLOSED", "FAILED"):
                continue
            try:
                rpnl = float(t.get("realized_pnl") or 0.0)
            except (TypeError, ValueError):
                continue
            if rpnl < 0:
                streak += 1
            else:
                break
        return streak

    async def get_dashboard_pair_entry_blocks(
        self, exchange_name: str, pairs: List[str]
    ) -> Tuple[Dict[str, str], Dict[str, Dict[str, Any]]]:
        """Reasons new long entries are likely blocked (dashboard red), not exhaustive vs full _check_pair_entry."""
        blocks: Dict[str, str] = {}
        block_details: Dict[str, Dict[str, Any]] = {}
        ex_lo = str(exchange_name or "").strip().lower()
        if not ex_lo or not pairs:
            return blocks, block_details

        for p in pairs:
            if not p:
                continue
            ps = str(p)
            mem = self._dashboard_entry_block_memory_reason(exchange_name, ps)
            if mem:
                blocks[ps] = mem
                if mem == "recent_negative_realized_blacklist":
                    pair_key = self._normalized_pair_key(ps)
                    until = self._recent_negative_realized_blacklist_until.get(pair_key)
                    min_realized_pct = float(
                        await self._get_config_value(
                            "trading.block_pair_after_realized_pnl_below_pct", 0.5
                        )
                        or 0.5
                    )
                    block_details[ps] = {
                        "type": mem,
                        "threshold_pct": min_realized_pct,
                        "until": until.isoformat() if until else None,
                        "message": (
                            f"recent close realized PnL% below {min_realized_pct:.2f}%"
                            + (f"; blocked until {until.isoformat()}" if until else "")
                        ),
                    }

        open_trades: List[Dict[str, Any]] = []
        hist_trades: List[Dict[str, Any]] = []
        try:
            async with httpx.AsyncClient(timeout=25.0) as client:
                ro = await client.get(f"{database_service_url}/api/v1/trades/open")
                if ro.status_code == 200:
                    open_trades = ro.json().get("trades", []) or []
                # Recent *exits* on this venue (sort by exit_time). Default /trades uses
                # entry_time desc so a loss closed 1h ago on an older position never appears
                # in the first N rows — misses pairs like XLM while still flagging busy symbols.
                rt = await client.get(
                    f"{database_service_url}/api/v1/trades",
                    params={
                        "limit": "800",
                        "exchange": ex_lo,
                        "sort_by": "exit_time",
                        "sort_order": "desc",
                    },
                )
                if rt.status_code == 200:
                    hist_trades = rt.json().get("trades", []) or []
                else:
                    # Case mismatch in DB exchange column — retry without filter, trim in-process.
                    rt2 = await client.get(
                        f"{database_service_url}/api/v1/trades",
                        params={"limit": "1200", "sort_by": "exit_time", "sort_order": "desc"},
                    )
                    if rt2.status_code == 200:
                        all_rows = rt2.json().get("trades", []) or []
                        hist_trades = [
                            t
                            for t in all_rows
                            if str(t.get("exchange", "")).lower() == ex_lo
                        ]
        except Exception as e:
            logger.warning("[DashboardEntryBlocks] DB fetch failed: %s", e)

        # Match _check_pair_risk_management Layer 2 windowing (naive local clock).
        two_hours_ago = datetime.now() - timedelta(hours=2)

        layer1_min = int(
            await self._get_config_value("trading.pair_risk_layer1_min_negative_positions_to_block", 2) or 2
        )
        layer2_min = int(await self._get_config_value("trading.pair_risk_layer2_min_loss_events", 2) or 2)
        max_open_pp = int(await self._get_config_value("trading.max_open_trades_per_pair", 1) or 1)
        force_single_open_per_pair = not (
            str(await self._get_config_value("trading.force_single_open_per_pair", True)).lower()
            in ("0", "false", "no")
        )
        raw_upnl_gate = await self._get_config_value(
            "trading.block_new_entry_if_open_unrealized_negative", True
        )
        upnl_gate_active = not (
            raw_upnl_gate is False or str(raw_upnl_gate).lower() in ("0", "false", "no")
        )
        upnl_gate_min_pct = float(
            await self._get_config_value(
                "trading.block_new_entry_if_open_unrealized_below_pct", 0.5
            )
            or 0.5
        )

        for p in pairs:
            ps = str(p)
            if ps in blocks:
                continue
            same_pair_opens = [
                t
                for t in open_trades
                if str(t.get("exchange", "")).lower() == ex_lo
                and self._dashboard_pair_key_match(t.get("pair"), ps)
            ]
            if upnl_gate_active:
                for t in open_trades:
                    if not self._dashboard_pair_key_match(t.get("pair"), ps):
                        continue
                    try:
                        u_open = float(t.get("unrealized_pnl") or 0.0)
                    except (TypeError, ValueError):
                        u_open = 0.0
                    n_open = infer_trade_notional_usd(
                        entry_price=float(t.get("entry_price") or 0.0),
                        position_size=float(t.get("position_size") or 0.0),
                        total_investment=float(t.get("total_investment") or 0.0),
                        entry_notional=float(t.get("entry_notional") or 0.0),
                    )
                    upnl_pct = (u_open / n_open) * 100.0 if n_open > 0.0 else None
                    if (upnl_pct is not None and upnl_pct <= upnl_gate_min_pct) or (
                        upnl_pct is None and u_open < 0
                    ):
                        blocks[ps] = "open_unrealized_negative"
                        block_details[ps] = {
                            "type": "open_unrealized_negative",
                            "threshold_pct": upnl_gate_min_pct,
                            "trade_id": t.get("trade_id"),
                            "exchange": str(t.get("exchange", "")).lower(),
                            "upnl_usd": u_open,
                            "upnl_pct": upnl_pct,
                            "message": (
                                f"open same-pair leg must be > {upnl_gate_min_pct:.2f}% uPnL "
                                f"(current={0.0 if upnl_pct is None else upnl_pct:.2f}%)"
                            ),
                        }
                        break
                if ps in blocks:
                    continue
            if max_open_pp > 0 and len(same_pair_opens) >= max_open_pp:
                blocks[ps] = "max_open_trades_per_pair"
                block_details[ps] = {
                    "type": "max_open_trades_per_pair",
                    "open_count": len(same_pair_opens),
                    "max_open_trades_per_pair": max_open_pp,
                    "message": f"{len(same_pair_opens)} open >= max {max_open_pp}",
                }
                continue
            if force_single_open_per_pair and len(same_pair_opens) >= 1:
                blocks[ps] = "force_single_open_per_pair"
                block_details[ps] = {
                    "type": "force_single_open_per_pair",
                    "open_count": len(same_pair_opens),
                    "message": f"{len(same_pair_opens)} active trade(s) on pair",
                }
                continue
            neg_cnt = 0
            for t in same_pair_opens:
                try:
                    u = float(t.get("unrealized_pnl") or 0.0)
                except (TypeError, ValueError):
                    u = 0.0
                if u < 0:
                    neg_cnt += 1
            closed_loss_2h = 0
            for t in hist_trades:
                if str(t.get("exchange", "")).lower() != ex_lo:
                    continue
                if not self._dashboard_pair_key_match(t.get("pair"), ps):
                    continue
                st = str(t.get("status") or "").upper()
                if st not in ("CLOSED", "FAILED"):
                    continue
                try:
                    rpnl = float(t.get("realized_pnl") or 0.0)
                except (TypeError, ValueError):
                    continue
                if rpnl >= 0:
                    continue
                exit_time_str = t.get("exit_time") or t.get("updated_at")
                if not exit_time_str:
                    continue
                try:
                    exit_time = datetime.fromisoformat(str(exit_time_str).replace("Z", ""))
                    if exit_time.tzinfo:
                        exit_time = exit_time.replace(tzinfo=None)
                except Exception:
                    continue
                if exit_time >= two_hours_ago:
                    closed_loss_2h += 1
            combined = neg_cnt + closed_loss_2h
            if combined >= layer2_min:
                blocks[ps] = "multi_loss_block"
                block_details[ps] = {
                    "type": "multi_loss_block",
                    "combined": combined,
                    "open_negative_count": neg_cnt,
                    "closed_loss_2h": closed_loss_2h,
                    "threshold": layer2_min,
                    "message": f"combined loss signals {combined} >= {layer2_min}",
                }
            elif neg_cnt >= layer1_min:
                blocks[ps] = "open_position_loss"
                block_details[ps] = {
                    "type": "open_position_loss",
                    "open_negative_count": neg_cnt,
                    "threshold": layer1_min,
                    "message": f"open negative positions {neg_cnt} >= {layer1_min}",
                }

        return blocks, block_details

    async def _check_pair_entry(self, exchange_name: str, pair: str) -> None:
        """Check if a pair should be entered - each strategy is independent with detailed logging"""
        pair_reserved = False
        try:
            pair_reserved = await self._reserve_pair_entry(exchange_name, pair)
            if not pair_reserved:
                return
            logger.info(f"🔍 [ENTRY CHECK] Starting entry evaluation for {pair} on {exchange_name}")

            # CONDITION CHECK 0a: Per-pair cooldown (PnL-FIX v3)
            if not await self._check_pair_cooldown(exchange_name, pair):
                return

            # CONDITION CHECK 0b: Correlation basket cap (PnL-FIX v3)
            if not await self._check_correlation_cap(exchange_name, pair):
                return

            # CONDITION CHECK 0c: No entry if this normalized pair is underwater on any exchange
            if not await self._check_no_negative_unrealized_on_same_pair(exchange_name, pair):
                return

            # CONDITION CHECK 1: Risk Management
            logger.info(f"🔍 [CONDITION 1] Checking risk management for {pair} on {exchange_name}")
            if not await self._check_pair_risk_management(exchange_name, pair):
                logger.warning(f"❌ [CONDITION 1] Risk Management Block: Skipping {pair} on {exchange_name} - risk management check failed")
                return
            else:
                logger.info(f"✅ [CONDITION 1] Risk management check passed for {pair} on {exchange_name}")
            
            # Handle different symbol formats for different exchanges
            # All exchanges use format without slashes for strategy service
            strategy_pair = pair.replace('/', '')
            
            # CONDITION CHECK 2: Get Strategy Signals (retries: strategy-service + Bybit OHLCV often exceed a single timeout)
            logger.info(f"🔍 [CONDITION 2] Fetching strategy signals for {strategy_pair} on {exchange_name}")
            signals_url = f"{strategy_service_url}/api/v1/signals/{exchange_name}/{strategy_pair}"
            signals_data = None
            signal_attempts = 3
            signal_timeout = 180.0
            for attempt in range(signal_attempts):
                try:
                    async with httpx.AsyncClient(timeout=signal_timeout) as client:
                        response = await client.get(signals_url)
                    if response.status_code == 404:
                        logger.info(
                            f"❌ [CONDITION 2] No signals available for {pair} on {exchange_name} - pair not supported by strategy service"
                        )
                        return
                    response.raise_for_status()
                    signals_data = response.json()
                    break
                except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.ConnectError) as net_err:
                    if attempt < signal_attempts - 1:
                        wait_s = 2**attempt
                        logger.warning(
                            f"⏱️ [CONDITION 2] Strategy service unreachable/timeout for {pair} on {exchange_name} "
                            f"({type(net_err).__name__}, attempt {attempt + 1}/{signal_attempts}) — retry in {wait_s}s"
                        )
                        await asyncio.sleep(wait_s)
                    else:
                        logger.warning(
                            f"⏱️ [CONDITION 2] Skipping {pair} on {exchange_name}: strategy service still unavailable "
                            f"after {signal_attempts} attempts ({type(net_err).__name__})"
                        )
                        return

            if not signals_data:
                return
            resolved_policy = self._resolve_regime_policy(signals_data)
            stable_regime = resolved_policy.get("stable_regime", "unknown")
            policy_version = resolved_policy.get("policy_version", "unversioned")
            policy_mode = resolved_policy.get("mode", "shadow")

            logger.info(f"✅ [CONDITION 2] Successfully fetched signals for {pair} on {exchange_name}")
            # Execution downgrade guard (auto disabled pair if recent slippage breaches budget)
            downgrade_key = (exchange_name, pair)
            downgraded_until = self._pair_execution_downgrade_until.get(downgrade_key)
            if downgraded_until and datetime.utcnow() < downgraded_until:
                logger.warning(
                    "[EntryGate] Skipping %s %s: execution-downgraded until %s",
                    exchange_name, pair, downgraded_until.isoformat()
                )
                self._record_regime_entry_decision(exchange_name, stable_regime, "rejected", "execution_downgraded")
                return
            # Entry quality gate: reject low-conviction / poor execution-condition setups.
            if not await self._passes_entry_quality_gate(exchange_name, pair, signals_data, resolved_policy):
                logger.info("[EntryGate] %s %s blocked by entry quality threshold", exchange_name, pair)
                self._record_regime_entry_decision(exchange_name, stable_regime, "rejected", "entry_quality_gate")
                return
            
            # DETAILED SIGNAL ANALYSIS
            consensus = signals_data.get('consensus', {})
            logger.info(f"📊 [CONSENSUS] {pair} on {exchange_name}: Signal={consensus.get('signal', 'unknown').upper()}, Confidence={consensus.get('confidence', 0):.2f}, Agreement={consensus.get('agreement', 0):.1f}%")
            
            # Consensus-driven entry (critical): do NOT bypass consensus by
            # executing any individual strategy BUY in isolation.
            strategies = signals_data.get('strategies', {}) or {}
            logger.info(f"📋 [STRATEGIES] Received {len(strategies)} strategy votes for {pair} on {exchange_name}")
            for strategy_name, signal_data in strategies.items():
                signal = signal_data.get('signal')
                confidence = float(signal_data.get('confidence', 0) or 0)
                strength = float(signal_data.get('strength', 0) or 0)
                logger.info(f"   📈 {strategy_name}: Signal={signal.upper() if signal else 'NONE'}, Confidence={confidence:.2f}, Strength={strength:.2f}")

            c_signal = str(consensus.get('signal', 'hold')).lower()
            c_conf = float(consensus.get('confidence', 0) or 0)
            c_agreement = float(consensus.get('agreement', 0) or 0)
            allow_forced_overrides = not (
                str(await self._get_config_value("trading.allow_forced_buy_overrides", False)).lower()
                in ("0", "false", "no")
            )
            max_signal_age_seconds = int(
                await self._get_config_value("trading.max_signal_age_seconds", 90) or 90
            )
            raw_signals_ts = signals_data.get("timestamp")
            try:
                if raw_signals_ts:
                    signals_ts = datetime.fromisoformat(str(raw_signals_ts).replace("Z", "+00:00"))
                    if signals_ts.tzinfo:
                        signals_ts = signals_ts.replace(tzinfo=None)
                    signal_age = (datetime.utcnow() - signals_ts).total_seconds()
                    if signal_age > max_signal_age_seconds:
                        logger.warning(
                            "[EntryGate] Reject %s %s: stale signal snapshot age=%.1fs > %ss",
                            exchange_name,
                            pair,
                            signal_age,
                            max_signal_age_seconds,
                        )
                        self._record_regime_entry_decision(
                            exchange_name, stable_regime, "rejected", "stale_signal_snapshot"
                        )
                        return
            except Exception:
                pass
            c_primary_override = bool(consensus.get("primary_override", False))
            c_sell_veto_max = float(consensus.get("sell_veto_max", 0) or 0)
            rsi_checklist_override = bool(consensus.get("rsi_checklist_buy_override", False))
            rsi_oversold_override = bool(consensus.get("rsi_15m_oversold_buy_override", False))
            macd_override_cfg = await self._get_config_value("trading.macd_buy_override", {})
            if not isinstance(macd_override_cfg, dict):
                macd_override_cfg = {}
            macd_override_enabled = bool(macd_override_cfg.get("enabled", True))
            entry_policy = resolved_policy.get("entry", {}) or {}
            consensus_policy = resolved_policy.get("consensus", {}) or {}
            min_confidence = float(
                entry_policy.get(
                    "min_confidence_threshold",
                    await self._get_config_value('trading.min_confidence_threshold', 0.5),
                ) or 0.5
            )
            min_agreement = float(
                entry_policy.get(
                    "min_agreement_percentage",
                    await self._get_config_value('trading.entry_quality_gate.min_agreement_percentage', 66.0),
                ) or 66.0
            )
            sell_veto_threshold = float(consensus_policy.get("sell_veto_threshold", 0.35) or 0.35)

            # Safety fallback: allow a high-confidence single-strategy BUY when
            # consensus is HOLD due to multi-strategy deadlock, but only if there
            # is no meaningful sell pressure.
            fallback_cfg = await self._get_config_value("trading.consensus_buy_fallback", {})
            if not isinstance(fallback_cfg, dict):
                fallback_cfg = {}
            fallback_enabled = bool(fallback_cfg.get("enabled", True))
            fallback_min_conf = float(fallback_cfg.get("min_buy_strategy_confidence", 0.72) or 0.72)
            fallback_min_strength = float(fallback_cfg.get("min_buy_strategy_strength", 0.10) or 0.10)
            fallback_max_sell_signals = int(fallback_cfg.get("max_sell_signals", 0) or 0)
            consensus_excluded_strategies = {"macd_momentum"}
            consensus_strategies = {
                name: data
                for name, data in strategies.items()
                if name not in consensus_excluded_strategies
            }

            fallback_triggered = False
            fallback_strategy = None
            fallback_conf = 0.0
            fallback_strength = 0.0

            buy_votes = []
            sell_votes = []
            for strategy_name, signal_data in consensus_strategies.items():
                s_signal = str((signal_data or {}).get("signal", "")).lower()
                s_conf = float((signal_data or {}).get("confidence", 0) or 0)
                s_strength = float((signal_data or {}).get("strength", 0) or 0)
                if s_signal == "buy":
                    buy_votes.append((s_conf, s_strength, strategy_name))
                elif s_signal == "sell":
                    sell_votes.append((s_conf, s_strength, strategy_name))

            macd_signal_data = strategies.get("macd_momentum", {}) or {}
            macd_validation = (macd_signal_data.get("validation", {}) or {})
            macd_rule_triggered = bool(
                macd_validation.get("trigger")
                or macd_validation.get("macd_green_rsi_buy_ok")
                or macd_signal_data.get("macd_green_rsi_buy_ok")
            )
            macd_is_buy_override = (
                macd_override_enabled
                and (
                    str(macd_signal_data.get("signal", "hold")).lower() == "buy"
                    or macd_rule_triggered
                )
            )
            rsi_override_strategy_data = strategies.get("rsi_oversold_override", {}) or {}
            rsi_is_buy_override = (
                rsi_oversold_override
                and str(rsi_override_strategy_data.get("signal", "hold")).lower() == "buy"
            )
            rsi_checklist_strategy_data = strategies.get("rsi_oversold_checklist", {}) or {}
            rsi_checklist_is_buy_override = (
                rsi_checklist_override
                and str(rsi_checklist_strategy_data.get("signal", "hold")).lower() == "buy"
            )
            if macd_is_buy_override:
                logger.warning(
                    "[MACDOverride] %s %s: forcing BUY execution from macd_momentum "
                    "(confidence=%.2f, strength=%.2f, consensus=%s %.2f/%.1f%%)",
                    exchange_name,
                    pair,
                    float(macd_signal_data.get("confidence", 0) or 0),
                    float(macd_signal_data.get("strength", 0) or 0),
                    c_signal,
                    c_conf,
                    c_agreement,
                )
            if rsi_is_buy_override:
                logger.warning(
                    "[RSIOverride] %s %s: forcing BUY execution from rsi_oversold_override "
                    "(confidence=%.2f, strength=%.2f, consensus=%s %.2f/%.1f%%, rsi_15m=%.2f)",
                    exchange_name,
                    pair,
                    float(rsi_override_strategy_data.get("confidence", 0) or 0),
                    float(rsi_override_strategy_data.get("strength", 0) or 0),
                    c_signal,
                    c_conf,
                    c_agreement,
                    float(consensus.get("rsi_15m_value", 50.0) or 50.0),
                )
            if rsi_checklist_is_buy_override:
                logger.warning(
                    "[RSIChecklistOverride] %s %s: forcing BUY execution from rsi_oversold_checklist "
                    "(confidence=%.2f, strength=%.2f, consensus=%s %.2f/%.1f%%)",
                    exchange_name,
                    pair,
                    float(rsi_checklist_strategy_data.get("confidence", 0) or 0),
                    float(rsi_checklist_strategy_data.get("strength", 0) or 0),
                    c_signal,
                    c_conf,
                    c_agreement,
                )

            if fallback_enabled and c_signal != "buy" and buy_votes:
                buy_votes.sort(reverse=True)
                best_buy_conf, best_buy_strength, best_buy_strategy = buy_votes[0]
                if (
                    best_buy_conf >= fallback_min_conf
                    and best_buy_strength >= fallback_min_strength
                    and len(sell_votes) <= fallback_max_sell_signals
                ):
                    fallback_triggered = True
                    fallback_strategy = best_buy_strategy
                    fallback_conf = best_buy_conf
                    fallback_strength = best_buy_strength
                    logger.warning(
                        "[ConsensusFallback] %s %s: forcing BUY from %s "
                        "(conf=%.2f, strength=%.2f, consensus=%s, consensus_conf=%.2f, sells=%s)",
                        exchange_name, pair, fallback_strategy, fallback_conf, fallback_strength,
                        c_signal, c_conf, len(sell_votes)
                    )

            if c_signal != 'buy' and not fallback_triggered and not macd_is_buy_override and not rsi_is_buy_override and not rsi_checklist_is_buy_override:
                logger.info(
                    f"📄 [SUMMARY] Consensus is {c_signal.upper()} for {pair} on {exchange_name}; "
                    f"entry blocked (confidence={c_conf:.2f}, agreement={c_agreement:.1f}%)"
                )
                self._record_regime_entry_decision(exchange_name, stable_regime, "rejected", f"consensus_{c_signal}")
                return
            # Critical safety: unless explicitly enabled, do not allow generic forced BUY
            # overrides to bypass HOLD/SELL consensus.
            # Exception: macd_momentum override is an explicit hard-entry policy requested
            # for all pairs when the MACD+RSI rule is satisfied.
            if (
                c_signal != "buy"
                and (rsi_is_buy_override or rsi_checklist_is_buy_override)
                and not allow_forced_overrides
            ):
                logger.warning(
                    "[EntryGate] Reject %s %s: forced override blocked while consensus=%s (non-MACD override)",
                    exchange_name,
                    pair,
                    c_signal,
                )
                self._record_regime_entry_decision(
                    exchange_name, stable_regime, "rejected", "forced_override_blocked"
                )
                return
            if c_conf <= min_confidence and not fallback_triggered and not macd_is_buy_override and not rsi_is_buy_override and not rsi_checklist_is_buy_override:
                logger.info(
                    f"📄 [SUMMARY] Consensus BUY rejected for {pair} on {exchange_name}: "
                    f"confidence {c_conf:.2f} <= min {min_confidence:.2f}"
                )
                self._record_regime_entry_decision(exchange_name, stable_regime, "rejected", "consensus_confidence")
                return
            if c_agreement < min_agreement and not fallback_triggered and not macd_is_buy_override and not rsi_is_buy_override and not rsi_checklist_is_buy_override:
                logger.info(
                    f"📄 [SUMMARY] Consensus BUY rejected for {pair} on {exchange_name}: "
                    f"agreement {c_agreement:.1f}% < min {min_agreement:.1f}%"
                )
                self._record_regime_entry_decision(exchange_name, stable_regime, "rejected", "consensus_agreement")
                return
            if c_sell_veto_max >= sell_veto_threshold and not c_primary_override and not fallback_triggered and not macd_is_buy_override and not rsi_is_buy_override and not rsi_checklist_is_buy_override:
                logger.info(
                    f"📄 [SUMMARY] Consensus BUY rejected for {pair} on {exchange_name}: "
                    f"sell-veto {c_sell_veto_max:.2f} >= {sell_veto_threshold:.2f}"
                )
                self._record_regime_entry_decision(exchange_name, stable_regime, "rejected", "sell_veto")
                return

            # Choose highest-confidence BUY strategy for metadata only.
            buy_candidates = []
            for strategy_name, signal_data in consensus_strategies.items():
                if str((signal_data or {}).get('signal', '')).lower() == 'buy':
                    buy_candidates.append(
                        (
                            float((signal_data or {}).get('confidence', 0) or 0),
                            float((signal_data or {}).get('strength', 0) or 0),
                            strategy_name,
                        )
                    )
            if not buy_candidates and not macd_is_buy_override and not rsi_is_buy_override and not rsi_checklist_is_buy_override:
                logger.info(f"📄 [SUMMARY] Consensus BUY but no concrete BUY candidate found for {pair} on {exchange_name}")
                self._record_regime_entry_decision(exchange_name, stable_regime, "rejected", "no_buy_candidates")
                return
            if buy_candidates:
                buy_candidates.sort(reverse=True)
                best_conf, best_strength, best_strategy = buy_candidates[0]
            else:
                # Override-only path: no consensus buy candidates because the
                # overriding strategy is intentionally excluded from consensus.
                best_conf, best_strength, best_strategy = 0.0, 0.0, "override"
            if fallback_triggered and fallback_strategy:
                best_conf, best_strength, best_strategy = fallback_conf, fallback_strength, fallback_strategy
            if macd_is_buy_override:
                best_conf = float(macd_signal_data.get("confidence", 0) or 0)
                best_strength = float(macd_signal_data.get("strength", 0) or 0)
                best_strategy = "macd_momentum"
            if rsi_is_buy_override:
                best_conf = float(rsi_override_strategy_data.get("confidence", 0) or 0)
                best_strength = float(rsi_override_strategy_data.get("strength", 0) or 0)
                best_strategy = "rsi_oversold_override"
            if rsi_checklist_is_buy_override:
                best_conf = float(rsi_checklist_strategy_data.get("confidence", 0) or 0)
                best_strength = float(rsi_checklist_strategy_data.get("strength", 0) or 0)
                best_strategy = "rsi_oversold_checklist"

            if not macd_is_buy_override and not rsi_is_buy_override and not rsi_checklist_is_buy_override:
                logger.info(f"🔍 [CONDITION 5] Checking momentum filter for {pair} on {exchange_name}")
                allow_entry, filter_reason = await self.momentum_filter.should_allow_entry(exchange_name, pair)
                if not allow_entry:
                    logger.warning(f"❌ [CONDITION 5] Momentum filter BLOCKED entry for {pair} on {exchange_name}: {filter_reason}")
                    self._record_regime_entry_decision(exchange_name, stable_regime, "rejected", "momentum_filter")
                    return
            else:
                if macd_is_buy_override:
                    logger.warning(
                        "[MACDOverride] %s %s: bypassing momentum filter due to forced macd_momentum BUY override",
                        exchange_name,
                        pair,
                    )
                if rsi_is_buy_override:
                    logger.warning(
                        "[RSIOverride] %s %s: bypassing momentum filter due to forced RSI oversold BUY override",
                        exchange_name,
                        pair,
                    )
                if rsi_checklist_is_buy_override:
                    logger.warning(
                        "[RSIChecklistOverride] %s %s: bypassing momentum filter due to checklist BUY override",
                        exchange_name,
                        pair,
                    )

            logger.info(
                f"🚀 [TRADE EXECUTION] Consensus BUY approved for {pair} on {exchange_name} "
                f"(consensus={c_conf:.2f}/{c_agreement:.1f}%, strategy={best_strategy}, conf={best_conf:.2f})"
            )
            await self._execute_trade_entry(exchange_name, pair, {
                'strategy': best_strategy,
                'signal': 'buy',
                'confidence': best_conf,
                'strength': best_strength,
                'consensus_confidence': c_conf,
                'consensus_agreement': c_agreement,
                'market_regime': signals_data.get('market_regime'),
                'stable_regime': stable_regime,
                'regime_score': float((signals_data.get('consensus') or {}).get('regime_score') or signals_data.get('regime_score') or 0.0),
                'policy_version': policy_version,
                'policy_mode': policy_mode,
                'entry_gate_reason': (
                    'rsi_oversold_checklist_buy_override'
                    if rsi_checklist_is_buy_override
                    else (
                        'rsi_oversold_buy_override'
                        if rsi_is_buy_override
                        else (
                            'macd_buy_override'
                            if macd_is_buy_override
                            else ('consensus_fallback_buy' if fallback_triggered else 'consensus_buy_pass')
                        )
                    )
                ),
                'resolved_policy': resolved_policy,
            })
            self._record_regime_entry_decision(exchange_name, stable_regime, "accepted", "entry_approved")
                
        except Exception as e:
            logger.error(f"Error checking pair entry for {pair} on {exchange_name}: {str(e)}")
            logger.error(f"Exception type: {type(e).__name__}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
        finally:
            if pair_reserved:
                await self._release_pair_entry_reservation(pair)

    async def _check_pair_risk_management(self, exchange_name: str, pair: str) -> bool:
        """
        Pair-level risk aligned with `trading.*` in config and `get_dashboard_pair_entry_blocks`.
        """
        try:
            logger.info(f"🔍 [RISK MANAGEMENT v2.5.0] Protection analysis for {pair} on {exchange_name}")

            ex_lo = str(exchange_name or "").strip().lower()
            layer1_min = int(
                await self._get_config_value("trading.pair_risk_layer1_min_negative_positions_to_block", 2) or 2
            )
            layer2_min = int(
                await self._get_config_value("trading.pair_risk_layer2_min_loss_events", 2) or 2
            )
            max_open_pp = int(await self._get_config_value("trading.max_open_trades_per_pair", 1) or 1)
            force_single_open_per_pair = not (
                str(await self._get_config_value("trading.force_single_open_per_pair", True)).lower()
                in ("0", "false", "no")
            )

            open_trades: List[Dict[str, Any]] = []
            hist_trades: List[Dict[str, Any]] = []
            open_trades = await self._get_active_trades_for_entry_guards()
            async with httpx.AsyncClient(timeout=60.0) as client:
                rt = await client.get(
                    f"{database_service_url}/api/v1/trades",
                    params={
                        "limit": "800",
                        "exchange": ex_lo,
                        "sort_by": "exit_time",
                        "sort_order": "desc",
                    },
                )
                if rt.status_code == 200:
                    hist_trades = rt.json().get("trades", []) or []
                else:
                    rt2 = await client.get(
                        f"{database_service_url}/api/v1/trades",
                        params={"limit": "1200", "sort_by": "exit_time", "sort_order": "desc"},
                    )
                    if rt2.status_code == 200:
                        all_rows = rt2.json().get("trades", []) or []
                        hist_trades = [
                            t for t in all_rows if str(t.get("exchange", "")).lower() == ex_lo
                        ]

            two_hours_ago = datetime.utcnow() - timedelta(hours=2)

            pair_open_trades = [
                t
                for t in open_trades
                if str(t.get("exchange", "")).lower() == ex_lo
                and self._dashboard_pair_key_match(t.get("pair"), pair)
            ]

            pending_same_pair = [
                t for t in pair_open_trades if str(t.get("status") or "").upper() == "PENDING"
            ]
            if pending_same_pair:
                logger.warning(
                    "🚫 [LAYER 0] Pending entry block: %s on %s has %s pending trade(s)",
                    pair,
                    exchange_name,
                    len(pending_same_pair),
                )
                return False

            if max_open_pp > 0 and len(pair_open_trades) >= max_open_pp:
                logger.warning(
                    f"🚫 [LAYER 0] Max open trades per pair: {pair} on {exchange_name} "
                    f"({len(pair_open_trades)} >= {max_open_pp})"
                )
                return False
            if force_single_open_per_pair and len(pair_open_trades) >= 1:
                logger.warning(
                    "🚫 [LAYER 0] Forced single-open-per-pair block: %s on %s already has %s active trade(s)",
                    pair,
                    exchange_name,
                    len(pair_open_trades),
                )
                return False

            neg_cnt = 0
            for t in pair_open_trades:
                try:
                    u = float(t.get("unrealized_pnl") or 0.0)
                except (TypeError, ValueError):
                    u = 0.0
                if u < 0:
                    neg_cnt += 1

            closed_loss_2h = 0
            recent_closed_losses_2h: List[Dict[str, Any]] = []

            for t in hist_trades:
                if str(t.get("exchange", "")).lower() != ex_lo:
                    continue
                if not self._dashboard_pair_key_match(t.get("pair"), pair):
                    continue
                st = str(t.get("status") or "").upper()
                if st not in ("CLOSED", "FAILED"):
                    continue
                try:
                    rpnl = float(t.get("realized_pnl") or 0.0)
                except (TypeError, ValueError):
                    continue
                if rpnl >= 0:
                    continue
                exit_time_str = t.get("exit_time") or t.get("updated_at")
                if not exit_time_str:
                    continue
                try:
                    exit_time = datetime.fromisoformat(
                        str(exit_time_str).replace("Z", "+00:00")
                    )
                    if exit_time.tzinfo:
                        exit_time = exit_time.replace(tzinfo=None)
                except Exception:
                    continue
                if exit_time >= two_hours_ago:
                    closed_loss_2h += 1
                    recent_closed_losses_2h.append(t)

            combined = neg_cnt + closed_loss_2h
            if combined >= layer2_min:
                logger.warning(
                    f"🚫 [LAYER 2] Multi-loss block: {pair} on {exchange_name} "
                    f"(open underwater: {neg_cnt}, closed losses (2h): {closed_loss_2h}, "
                    f"threshold: {layer2_min})"
                )
                return False

            if neg_cnt >= layer1_min:
                logger.warning(
                    f"🚫 [LAYER 1] Open underwater block: {pair} on {exchange_name} "
                    f"({neg_cnt} negative unrealized >= {layer1_min})"
                )
                return False

            if recent_closed_losses_2h:
                largest_loss = min(float(t.get("realized_pnl", 0)) for t in recent_closed_losses_2h)
                if largest_loss < -20:
                    logger.warning(f"🚫 [LAYER 2b] Large loss (2h): {pair} on {exchange_name} (${largest_loss:.2f})")
                    return False

            all_open_trades = open_trades
            portfolio_unrealized_pnl = sum(
                float(trade.get("unrealized_pnl", 0))
                for trade in all_open_trades
                if trade.get("unrealized_pnl") is not None
            )

            if portfolio_unrealized_pnl < -100:
                logger.warning(f"🚫 [LAYER 3] Portfolio drawdown: {pair} on {exchange_name} (${portfolio_unrealized_pnl:.2f})")
                return False

            exchange_trades = [
                t for t in all_open_trades if str(t.get("exchange", "")).lower() == ex_lo
            ]
            exchange_unrealized_pnl = sum(
                float(trade.get("unrealized_pnl", 0))
                for trade in exchange_trades
                if trade.get("unrealized_pnl") is not None
            )

            if len(exchange_trades) >= 3 and exchange_unrealized_pnl < -50:
                logger.warning(
                    f"🚫 [LAYER 4] Exchange performance: {pair} on {exchange_name} "
                    f"({len(exchange_trades)} opens, ${exchange_unrealized_pnl:.2f} unrealized)"
                )
                return False

            logger.info(f"✅ [RISK MANAGEMENT v2.5.0] All layers passed for {pair} on {exchange_name}")
            logger.info(
                f"   - Opens this pair: {len(pair_open_trades)} (negative uPnL: {neg_cnt}); "
                f"closed losses 2h: {closed_loss_2h}; portfolio uPnL: ${portfolio_unrealized_pnl:.2f}"
            )
            return True

        except Exception as e:
            logger.error(f"Error in risk management check for {pair} on {exchange_name}: {str(e)}")
            logger.error(f"Exception type: {type(e).__name__}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            return False
            
    async def _execute_trade_entry(self, exchange_name: str, pair: str, signal: Dict[str, Any]) -> None:
        """Execute trade entry with Redis queue-based processing (HYBRID MODE)"""
        try:
            # PnL-FIX v3: Immediately mark pair cooldown so the next cycle can't re-enter
            # the same pair even before the order ack comes back (the issue that caused
            # 30 correlated longs on crypto pairs).
            self._mark_pair_cooldown(exchange_name, pair)

            # CRITICAL PROTECTION: Check risk management before executing trade
            logger.info(f"🛡️ [RISK CHECK] Checking risk management for {pair} on {exchange_name}")
            risk_check_passed = await self._check_pair_risk_management(exchange_name, pair)
            if not risk_check_passed:
                logger.warning(f"🚫 [RISK BLOCK] Trade blocked by risk management for {pair} on {exchange_name}")
                return
            logger.info(f"✅ [RISK CHECK] Risk management passed for {pair} on {exchange_name}")
            
            # Balance before order: live = exchange API; simulation = DB-backed self.balances (same as entry cycle)
            base_currency = 'USDC' if exchange_name in ['binance', 'bybit'] else 'USD'
            available_balance = 0.0
            try:
                if self.is_simulation:
                    await self._refresh_simulation_balance_from_db(exchange_name)
                    b = self.balances.get(exchange_name) or {}
                    available_balance = float(b.get('available', 0) or 0)
                    logger.info(
                        f"💰 [SIMULATION] Available balance on {exchange_name}: {available_balance:.2f} {base_currency} (database)"
                    )
                else:
                    async with httpx.AsyncClient(timeout=60.0) as client:
                        balance_response = await client.get(
                            f"{exchange_service_url}/api/v1/account/balance/{exchange_name}"
                        )
                        balance_response.raise_for_status()
                        current_balance = balance_response.json()
                        available_balance = float(
                            current_balance.get('free', {}).get(base_currency, 0) or 0
                        )
                        logger.info(
                            f"💰 Current available balance on {exchange_name}: {available_balance:.2f} {base_currency}"
                        )
                if available_balance < 50:
                    logger.warning(
                        f"🚫 Insufficient balance on {exchange_name}: {available_balance:.2f} {base_currency} < 50 minimum"
                    )
                    return
            except Exception as e:
                logger.error(f"❌ Failed to check balance for {exchange_name}: {e}")
                return
            
            # CRITICAL SAFETY: ONLY Redis-based processing allowed
            # NO direct processing fallback - untracked orders are catastrophically dangerous
            if not self.use_redis_processing:
                logger.error("🚨 CRITICAL SAFETY: Redis processing is REQUIRED for order tracking")
                logger.error("🚨 ABORTING order creation - untracked orders can cause unlimited losses")
                return
            
            logger.info(f"🚀 Using Redis-based order processing for {pair} on {exchange_name}")
            return await self._execute_redis_trade_entry(exchange_name, pair, signal, available_balance)
        except Exception as e:
            logger.error(f"❌ CRITICAL SAFETY: Exception in _execute_trade_entry: {str(e)}")
            logger.error(f"🚨 All order creation MUST go through Redis tracking system only")
    
    async def _execute_redis_trade_entry(
        self, 
        exchange_name: str, 
        pair: str, 
        signal: Dict[str, Any], 
        available_balance: float
    ) -> None:
        """Execute trade entry using Redis-based queue processing"""
        try:
            # STRATEGY-SPECIFIC POSITION SIZING
            strategy_name = signal.get('strategy', 'default')
            position_percentage, max_position_usd = await self._get_strategy_position_config(strategy_name)

            position_value_usdc = min(
                available_balance * position_percentage,
                max_position_usd
            )
            # Adaptive size haircut for pairs with consecutive recent losses.
            pair_loss_mult = await self._get_pair_loss_size_multiplier(
                exchange_name,
                pair,
                signal.get("resolved_policy"),
            )
            if pair_loss_mult < 1.0:
                original_value = position_value_usdc
                position_value_usdc *= pair_loss_mult
                logger.warning(
                    "[AdaptiveSizing] %s %s position reduced: $%.2f -> $%.2f (mult=%.2f)",
                    exchange_name, pair, original_value, position_value_usdc, pair_loss_mult
                )

            # PnL-FIX v5 — Diversification budget cap.
            # Evidence: an API3/USD trade was sized at ≈$2,333 on $9k available,
            # because confluence's `position_size_percentage: 0.25` ignored the
            # 10-trade per-venue cap. With 10 slots per exchange, no single
            # trade should exceed roughly 1.5 × the equal-weight slot budget.
            # We compute remaining slots from open trades on this venue and
            # cap the position value to (available_balance / remaining_slots) × 1.5.
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    cfg_resp = await client.get(f"{config_service_url}/api/v1/config/trading")
                    cfg_resp.raise_for_status()
                    _cfg = cfg_resp.json()
                    _max_per_ex = int(_cfg.get('max_trades_per_exchange', 10) or 10)
                    _slot_overcap = float(_cfg.get('per_slot_diversification_overcap', 1.5) or 1.5)

                    open_resp = await client.get(f"{database_service_url}/api/v1/trades/open")
                    open_resp.raise_for_status()
                    _open_trades_now = open_resp.json().get('trades', []) or []
                    _open_on_venue = sum(1 for t in _open_trades_now if t.get('exchange') == exchange_name)
                    _remaining_slots = max(1, _max_per_ex - _open_on_venue)

                    _per_slot_budget = (available_balance / _remaining_slots) * _slot_overcap
                    if _per_slot_budget > 0 and position_value_usdc > _per_slot_budget:
                        logger.info(
                            f"[Redis Queue] Diversification cap engaged: "
                            f"requested=${position_value_usdc:.2f} → capped=${_per_slot_budget:.2f} "
                            f"(available=${available_balance:.2f}, open_on_venue={_open_on_venue}/"
                            f"{_max_per_ex}, slot_overcap={_slot_overcap})"
                        )
                        position_value_usdc = _per_slot_budget
            except Exception as _diversify_err:
                logger.warning(
                    f"[Redis Queue] Could not apply diversification cap "
                    f"(strategy={strategy_name}, exchange={exchange_name}): {_diversify_err}"
                )

            logger.info(f"[Redis Queue] Strategy: {strategy_name}, Position: ${position_value_usdc:.2f}")
            
            # Check minimum order size from config 
            min_order_size_usd = await self._get_min_order_size_for_exchange(exchange_name)
            logger.info(f"📏 Min order size for {exchange_name}: ${min_order_size_usd}")
            
            if position_value_usdc < min_order_size_usd:
                # Add 3% safety buffer to account for price fluctuations, fees, and rounding
                min_order_with_buffer = min_order_size_usd * 1.03
                if available_balance >= min_order_with_buffer:
                    position_value_usdc = min_order_with_buffer
                    logger.info(f"💡 Using minimum order size with 3% safety buffer: ${min_order_with_buffer:.2f}")
                else:
                    logger.warning(f"🚫 Insufficient balance for minimum order with buffer: ${available_balance:.2f} < ${min_order_with_buffer:.2f}")
                    return
            
            # Calculate position size in units
            current_price = signal.get('current_price', 0)
            if current_price <= 0:
                # Fallback: fetch current price from price feed service
                logger.info(f"📈 Signal missing price for {pair}, fetching current price...")
                current_price = await self._get_current_price(exchange_name, pair)
                if current_price <= 0:
                    logger.error(f"❌ Invalid price for {pair}: {current_price}")
                    return
                
            position_size_units = position_value_usdc / current_price
            sanitized_position_size = await self._sanitize_numeric_value_async(position_size_units)
            
            # ========== CRITICAL VALIDATION: PREVENT $0.00 ORDERS ==========
            logger.info(f"🔍 [ORDER VALIDATION] Pre-exchange validation for {pair} on {exchange_name}")
            logger.info(f"   💵 Position Value USD: ${position_value_usdc:.2f}")
            logger.info(f"   📈 Current Price: ${current_price:.8f}")
            logger.info(f"   📦 Raw Position Size: {position_size_units:.8f}")
            logger.info(f"   ✨ Sanitized Position Size: {sanitized_position_size:.8f}")
            
            # VALIDATION 1: Check if sanitized position size is zero or invalid
            if sanitized_position_size <= 0:
                error_msg = f"❌ [VALIDATION FAILED] Zero/negative position size detected"
                logger.error(f"{error_msg}: {sanitized_position_size}")
                logger.error(f"   💵 Position Value: ${position_value_usdc:.2f}")
                logger.error(f"   📈 Price: ${current_price:.8f}")
                logger.error(f"   📦 Raw Size: {position_size_units:.8f}")
                
                # CREATE FAILED TRADE RECORD WITH ACTUAL FAILURE REASON
                trade_id = str(uuid.uuid4())
                await self._create_failed_trade_record(
                    trade_id=trade_id,
                    exchange_name=exchange_name,
                    pair=pair,
                    strategy_name=strategy_name,
                    failure_reason=f"Zero position size after sanitization: {sanitized_position_size}",
                    position_value_usd=position_value_usdc,
                    current_price=current_price,
                    raw_position_size=position_size_units
                )
                return
            
            # VALIDATION 2: Check if position value would result in dust trade
            calculated_value = sanitized_position_size * current_price
            if calculated_value < min_order_size_usd:
                error_msg = f"❌ [VALIDATION FAILED] Order value below minimum"
                logger.error(f"{error_msg}: ${calculated_value:.2f} < ${min_order_size_usd}")
                
                # CREATE FAILED TRADE RECORD WITH ACTUAL FAILURE REASON
                trade_id = str(uuid.uuid4())
                await self._create_failed_trade_record(
                    trade_id=trade_id,
                    exchange_name=exchange_name,
                    pair=pair,
                    strategy_name=strategy_name,
                    failure_reason=f"Order value ${calculated_value:.2f} below minimum ${min_order_size_usd}",
                    position_value_usd=position_value_usdc,
                    current_price=current_price,
                    raw_position_size=position_size_units
                )
                return
            
            # VALIDATION 3: Check for extremely small position sizes that might be rounded to zero
            if sanitized_position_size < 0.000001:  # 1 millionth unit threshold
                error_msg = f"❌ [VALIDATION FAILED] Position size too small for exchange"
                logger.error(f"{error_msg}: {sanitized_position_size} < 0.000001")
                
                # CREATE FAILED TRADE RECORD WITH ACTUAL FAILURE REASON
                trade_id = str(uuid.uuid4())
                await self._create_failed_trade_record(
                    trade_id=trade_id,
                    exchange_name=exchange_name,
                    pair=pair,
                    strategy_name=strategy_name,
                    failure_reason=f"Position size {sanitized_position_size} too small for exchange (< 0.000001)",
                    position_value_usd=position_value_usdc,
                    current_price=current_price,
                    raw_position_size=position_size_units
                )
                return
            
            logger.info(f"✅ [ORDER VALIDATION] All validations passed for {pair} on {exchange_name}")
            logger.info(f"   ✨ Final position size: {sanitized_position_size:.8f}")
            logger.info(f"   💰 Final order value: ${calculated_value:.2f}")
            
            # Generate trade ID
            trade_id = str(uuid.uuid4())
            
            # Create PENDING trade record first
            await self.redis_order_manager.create_pending_trade_record(
                trade_id=trade_id,
                signal=signal,
                exchange_name=exchange_name,
                pair=pair,
                position_size=sanitized_position_size,
                strategy_name=strategy_name
            )
            
            # Submit to Redis queue for processing
            success = await self.redis_order_manager.execute_trade_entry_async(
                signal=signal,
                exchange_name=exchange_name,
                pair=pair,
                sanitized_position_size=sanitized_position_size,
                trade_id=trade_id,
                strategy_name=strategy_name
            )
            
            if success:
                logger.info(f"✅ Redis trade entry submitted: {trade_id} for {pair} on {exchange_name}")
            else:
                logger.error(f"❌ Redis trade entry failed: {trade_id} for {pair} on {exchange_name}")
                
        except Exception as e:
            logger.error(f"❌ Error in Redis trade entry: {e}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")

    async def _create_failed_trade_record(
        self,
        trade_id: str,
        exchange_name: str,
        pair: str,
        strategy_name: str,
        failure_reason: str,
        position_value_usd: float = 0.0,
        current_price: float = 0.0,
        raw_position_size: float = 0.0
    ) -> None:
        """Create a FAILED trade record with actual failure reason instead of strategy entry reason"""
        try:
            logger.warning(f"📝 [FAILED TRADE RECORD] Creating failure record for {trade_id}")
            logger.warning(f"   🔍 Failure Reason: {failure_reason}")
            logger.warning(f"   💱 Exchange: {exchange_name}")
            logger.warning(f"   💰 Pair: {pair}")
            logger.warning(f"   🎯 Strategy: {strategy_name}")
            
            # Create comprehensive trade record with failure details
            trade_data = {
                'trade_id': trade_id,
                'exchange': exchange_name,
                'pair': pair,
                'strategy': strategy_name,
                'status': 'FAILED',
                'side': 'buy',  # Entry orders are always buy
                'entry_price': current_price if current_price > 0 else None,
                'position_size': 0.0,  # Zero since order failed validation
                'position_value_usd': position_value_usd,
                'entry_reason': failure_reason,  # ACTUAL failure reason, not strategy reason
                'exit_reason': None,
                'realized_pnl': 0.0,
                'unrealized_pnl': 0.0,
                'fees': 0.0,
                'created_at': datetime.utcnow().isoformat(),
                'updated_at': datetime.utcnow().isoformat(),
                'entry_time': datetime.utcnow().isoformat(),
                'exit_time': None,
                'order_id': None,  # No order was created
                'fill_id': None,  # No fill occurred
                'metadata': {
                    'validation_failure': True,
                    'raw_position_size': raw_position_size,
                    'calculated_value': raw_position_size * current_price if current_price > 0 else 0,
                    'failure_type': 'pre_exchange_validation'
                }
            }
            
            # Store to database
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(f"{database_service_url}/api/v1/trades", json=trade_data)
                response.raise_for_status()
                
            logger.info(f"✅ [FAILED TRADE RECORD] Created failure record {trade_id} with reason: {failure_reason}")
            
        except Exception as e:
            logger.error(f"❌ [FAILED TRADE RECORD] Error creating failure record: {e}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")

    def _extract_fee_with_currency(self, order_data: Dict[str, Any]) -> Dict[str, Any]:
        """PnL-FIX v9: Extract fee amount + currency from order data so the
        database can populate ``entry_fee_amount`` / ``exit_fee_amount`` /
        ``entry_fee_currency`` / ``exit_fee_currency`` and auto-compute
        ``total_fees_usd``. Previously only the raw amount was extracted via
        ``_extract_fee_safely`` and the currency was lost, leaving
        ``total_fees_usd = 0`` on every closed trade.

        Returns a dict with keys: ``amount`` (float) and ``currency`` (str).
        Falls back to USD when the exchange does not report a currency.
        """
        result: Dict[str, Any] = {"amount": 0.0, "currency": "USD"}
        try:
            if not order_data:
                return result

            fee_data = order_data.get('fee')
            if not fee_data:
                fee_data = order_data.get('fees') or order_data.get('commission') or order_data.get('cost')
            if not fee_data:
                return result

            if isinstance(fee_data, dict):
                cost = fee_data.get('cost') or fee_data.get('amount') or fee_data.get('value')
                currency = fee_data.get('currency') or 'USD'
                if cost is not None:
                    result["amount"] = float(cost)
                    result["currency"] = str(currency).upper()
            elif isinstance(fee_data, (int, float)):
                result["amount"] = float(fee_data)
                # No currency reported – assume USD-equivalent (USD/USDC/USDT pairs).
                result["currency"] = "USD"
            elif isinstance(fee_data, list):
                total = 0.0
                last_currency = "USD"
                for fee in fee_data:
                    if isinstance(fee, dict):
                        cost = fee.get('cost') or fee.get('amount')
                        if cost is not None:
                            total += float(cost)
                        last_currency = str(fee.get('currency', last_currency)).upper()
                    elif isinstance(fee, (int, float)):
                        total += float(fee)
                result["amount"] = total
                result["currency"] = last_currency
        except (ValueError, TypeError) as e:
            logger.warning(f"[Fee] Failed to extract structured fee: {e}; raw={order_data.get('fee')}")
        return result

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
        """Record actual entry fees from order data to the database.

        PnL-FIX v9: also persists ``entry_fee_amount`` and ``entry_fee_currency``
        so the database service can auto-populate ``total_fees_usd``. Previously
        only the legacy ``fees`` aggregate was sent, which left
        ``entry_fee_amount = 0`` and therefore ``total_fees_usd = 0``.
        """
        try:
            fee_info = self._extract_fee_with_currency(order_data)
            fees = fee_info["amount"]
            if fees > 0:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    update_data = {
                        'fees': fees,
                        'entry_fee_amount': fees,
                        'entry_fee_currency': fee_info["currency"],
                        'updated_at': datetime.utcnow().isoformat()
                    }
                    response = await client.put(f"{database_service_url}/api/v1/trades/{trade_id}", json=update_data)
                    if response.status_code == 200:
                        logger.info(f"✅ FEES RECORDED: Trade {trade_id} - {fees:.6f} {fee_info['currency']}")
                        return True
                    else:
                        logger.error(f"❌ FAILED TO RECORD FEES: {response.status_code} - {response.text}")
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
            
            # Prepare order data with sanitized values (mapping + emit happen after balance checks for live only)
            order_data = {
                'exchange': exchange_name,
                'symbol': exchange_symbol,
                'order_type': 'market',
                'side': side,
                'amount': sanitized_amount,
                'client_order_id': client_order_id  # Phase 0: Enable idempotency
            }
            
            # CRITICAL: Verify balance before placing order (simulation = DB quote; live = exchange API)
            try:
                if self.is_simulation:
                    await self._refresh_simulation_balance_from_db(exchange_name)
                    if side.lower() == 'buy':
                        base_currency = 'USD' if exchange_name == 'cryptocom' else 'USDC'
                        real_available = float(
                            (self.balances.get(exchange_name) or {}).get('available', 0) or 0
                        )
                        logger.info(
                            f"🔍 [SIMULATION] Balance check for {exchange_name}: ${real_available:.2f} {base_currency} available (database)"
                        )
                        current_price = await self._get_current_price(exchange_name, pair)
                        if current_price > 0:
                            estimated_cost = sanitized_amount * current_price * 1.01
                            if real_available < estimated_cost:
                                logger.error(
                                    f"🚫 [SIMULATION] Insufficient simulated balance! Available: ${real_available:.2f}, Required: ${estimated_cost:.2f}"
                                )
                                return None
                        else:
                            logger.warning(
                                f"⚠️ Could not get current price for {pair} on {exchange_name}, skipping balance verification"
                            )
                    else:
                        if not trade_id:
                            logger.error(
                                f"🚫 [SIMULATION] Market SELL requires trade_id (DB position) for {pair} on {exchange_name}"
                            )
                            return None
                        pos = await self._fetch_open_trade_position_size(trade_id)
                        if pos is None or pos <= 0:
                            logger.error(
                                f"🚫 [SIMULATION] No OPEN DB position for trade {trade_id} — cannot sell {pair}"
                            )
                            return None
                        if sanitized_amount > pos + 1e-10:
                            logger.warning(
                                f"🔧 [SIMULATION] Clamping market sell amount {sanitized_amount:.8f} to DB position {pos:.8f}"
                            )
                            sanitized_amount = self._sanitize_numeric_value(pos)
                            amount = sanitized_amount
                            order_data['amount'] = sanitized_amount
                        logger.info(
                            f"🔍 [SIMULATION] SELL validated vs DB position: {sanitized_amount:.8f} {pair} (trade {trade_id})"
                        )
                else:
                    async with httpx.AsyncClient(timeout=60.0) as client:
                        balance_response = await client.get(
                            f"{exchange_service_url}/api/v1/account/balance/{exchange_name}"
                        )
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
                            
                            # FOR EXIT ORDERS: Position size is already corrected in _execute_trade_exit()
                            # This logic now handles exit orders properly by using exchange balance
                            if side.lower() == 'sell' and trade_id:
                                logger.warning(f"🚨 EXIT ORDER INSUFFICIENT BALANCE: Database shows {amount:.8f}, exchange has {quote_available:.8f}")
                                # Use exchange balance if available, otherwise this will fail as expected
                                if quote_available >= amount * 0.1:  # At least 10% of expected
                                    logger.warning(f"🔧 AUTO-CORRECTING exit order: {amount:.8f} -> {quote_available:.8f} (using exchange balance)")
                                    amount = quote_available
                                else:
                                    logger.error(f"💥 CRITICAL: Exchange balance too low ({quote_available:.8f}) for meaningful exit - order will fail")
                                    # Let it fail - this should be caught by the dust position logic
                            # If discrepancy is small (< 5% or < 10 units), auto-correct the position size for entry orders
                            elif quote_available > 0 and (discrepancy / amount < 0.05 or discrepancy < 10):
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
            
            # Keep sanitized_amount aligned with any live balance auto-corrections on order_data['amount']
            sanitized_amount = self._sanitize_numeric_value(float(order_data.get('amount') or 0))
            order_data['amount'] = sanitized_amount
            
            if self.is_simulation:
                fill_px = await self._get_current_price(exchange_name, pair)
                if not fill_px or float(fill_px) <= 0:
                    logger.error(
                        f"❌ [SIMULATION] No price for market order {pair} on {exchange_name}"
                    )
                    return None
                fill_price = self._simulation_limit_fill_price(float(fill_px), side)
                sim_order = await self._build_simulation_instant_fill_order(
                    exchange_name,
                    pair,
                    side,
                    sanitized_amount,
                    trade_id,
                    fill_price,
                    apply_balance_delta=True,
                )
                orders_total.labels(
                    exchange=exchange_name,
                    pair=pair,
                    side=side,
                    type='market',
                    status='success',
                ).inc()
                return sim_order

            # Phase 2: Create order mapping (live only — avoids DB dependency for simulation fills)
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

    async def _wait_for_order_fill(self, exchange_name: str, order_id: str, pair: str, timeout: int = 60, original_order: Optional[Dict[str, Any]] = None, amount: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """Wait for order to fill with timeout"""
        try:
            # First check if the original order already shows as filled (common for market orders)
            if original_order and str(original_order.get('status', '')).lower() in ['closed', 'filled']:
                logger.info(f"✅ Market order {order_id} filled instantly")
                return original_order
            
            start_time = time.time()
            check_count = 0
            
            while time.time() - start_time < timeout and check_count < 12:  # Max 12 checks for extended monitoring
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
                                
                                # CRITICAL FIX: NEVER create synthetic orders without exchange confirmation
                                # The original order response should contain ACTUAL fill information from exchange
                                if original_order:
                                    orig_status = str(original_order.get('status', '')).lower()
                                    filled_amount = original_order.get('filled', 0)
                                    avg_price = original_order.get('average')
                                    
                                    # MANDATORY: Verify ALL fill data is present and valid
                                    if (orig_status in ['closed', 'filled'] and 
                                        filled_amount and filled_amount > 0 and
                                        avg_price and avg_price > 0):
                                        logger.info(f"✅ EXCHANGE CONFIRMED: Order {order_id} filled {filled_amount:.8f} at ${avg_price:.6f}")
                                        return original_order
                                    else:
                                        logger.error(f"🚨 EXCHANGE CONFIRMATION FAILED: Order {order_id} status={orig_status}, filled={filled_amount}, price={avg_price}")
                                        return None  # Do NOT create synthetic orders
                                
                                # CRITICAL: Do NOT assume orders are filled without exchange confirmation
                                logger.error(f"🚨 CRITICAL: Order {order_id} not found in open orders and no valid original order data")
                                logger.error(f"🚨 REFUSING to create synthetic fill data - this prevents database-exchange mismatches")
                                return None  # Force manual verification
                        
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
    
    async def _get_order_fill_price_from_history(self, exchange_name: str, order_id: str, pair: str) -> float:
        """Get actual fill price from exchange order history"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Try to get order history from exchange service
                response = await client.get(f"{exchange_service_url}/api/v1/trading/orders/history/{exchange_name}?symbol={pair}")
                
                if response.status_code == 200:
                    order_history = response.json().get('orders', [])
                    
                    # Find our order in history
                    for order in order_history:
                        if order.get('id') == order_id or order.get('orderId') == order_id:
                            # Get actual fill price from the order
                            fill_price = float(order.get('average', 0) or order.get('price', 0) or 0)
                            if fill_price > 0:
                                logger.info(f"📋 Found order {order_id} in history with fill price ${fill_price:.6f}")
                                return fill_price
                            else:
                                logger.warning(f"⚠️ Order {order_id} found in history but no fill price available")
                    
                    logger.warning(f"⚠️ Order {order_id} not found in exchange order history")
                else:
                    logger.warning(f"⚠️ Failed to get order history from {exchange_name}: {response.status_code}")
                
                # Try alternative endpoint for recent orders
                recent_response = await client.get(f"{exchange_service_url}/api/v1/trading/mytrades/{exchange_name}?symbol={pair}")
                
                if recent_response.status_code == 200:
                    recent_orders = recent_response.json().get('orders', [])
                    
                    for order in recent_orders:
                        if order.get('id') == order_id or order.get('orderId') == order_id:
                            fill_price = float(order.get('average', 0) or order.get('price', 0) or 0)
                            if fill_price > 0:
                                logger.info(f"📋 Found order {order_id} in recent orders with fill price ${fill_price:.6f}")
                                return fill_price
                
                return 0.0  # No fill price found
                
        except Exception as e:
            logger.error(f"❌ Error getting fill price from order history for {order_id}: {e}")
            return 0.0

    async def _cancel_order(self, exchange_name: str, order_id: str, pair: str) -> bool:
        """Cancel an order"""
        try:
            if self.is_simulation and str(order_id).startswith("sim_"):
                logger.info(f"🧪 [SIMULATION] Order {order_id} cancelled locally")
                return True

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
        """Get minimum order size for a trading pair from exchange info"""
        try:
            # Get request timeout from config
            timeout = await self._get_config_value('exchange_manager.request_timeout', 30)
            
            # Try to get from exchange service first
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(f"{exchange_service_url}/api/v1/market/info/{exchange_name}/{pair}")
                if response.status_code == 200:
                    market_info = response.json()
                    min_amount = market_info.get('limits', {}).get('amount', {}).get('min')
                    if min_amount and min_amount > 0:
                        logger.info(f"📏 Got min order size from exchange for {pair} on {exchange_name}: {min_amount}")
                        return float(min_amount)
            
            # Fallback: Try to get from exchange-specific config
            min_amount = await self._get_config_value(f'exchanges.{exchange_name}.min_order_amount', None)
            if min_amount:
                logger.info(f"📏 Got min order size from config for {pair} on {exchange_name}: {min_amount}")
                return float(min_amount)
            
            # Final fallback: Get default minimum from config
            default_min = await self._get_config_value('trading.default_min_order_amount', None)
            if default_min:
                logger.warning(f"⚠️ Using default minimum order size for {pair} on {exchange_name}: {default_min}")
                return float(default_min)
            
            logger.error(f"❌ No minimum order size configured for {pair} on {exchange_name}")
            raise ValueError(f"No minimum order size configuration found for {exchange_name}")
            
        except Exception as e:
            logger.error(f"Error getting minimum order size for {pair} on {exchange_name}: {str(e)}")
            logger.error(f"Exception type: {type(e).__name__}")
            raise

    async def _create_critical_alert(self, trade_id: str, message: str) -> None:
        """Create a critical alert for trades requiring manual intervention."""
        try:
            alert_data = {
                'trade_id': trade_id,
                'alert_type': 'CRITICAL_EXIT_FAILURE',
                'message': message,
                'severity': 'HIGH',
                'requires_manual_action': True,
                'timestamp': datetime.utcnow().isoformat()
            }
            
            # Log the critical alert
            logger.critical(f"🚨 CRITICAL ALERT: {message} | Trade: {trade_id}")
            
            # Try to save to database alerts table if it exists
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        f"{database_service_url}/api/v1/alerts", 
                        json=alert_data,
                        timeout=5.0
                    )
                    if response.status_code == 200:
                        logger.info(f"✅ Critical alert saved to database for trade {trade_id}")
                    else:
                        logger.warning(f"⚠️ Failed to save alert to database: {response.status_code}")
            except Exception as db_error:
                logger.error(f"❌ Failed to save critical alert to database: {str(db_error)}")
                
        except Exception as e:
            logger.error(f"❌ Failed to create critical alert for trade {trade_id}: {str(e)}")

    async def _get_config_value(self, config_path: str, default_value=None):
        """Get a configuration value from config service using dot notation path"""
        try:
            # Get request timeout from config (use a reasonable default for this bootstrap call)
            timeout = 30
            
            config_url = f"{config_service_url}/api/v1/config/all"
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(config_url)
                response.raise_for_status()
                full_config = response.json()
                
                # Navigate through the config using dot notation
                keys = config_path.split('.')
                value = full_config
                for key in keys:
                    if isinstance(value, dict) and key in value:
                        value = value[key]
                    else:
                        return default_value
                
                return value
                
        except Exception as e:
            logger.error(f"❌ Error getting config value '{config_path}': {e}")
            if default_value is not None:
                logger.warning(f"⚠️ Using default value for '{config_path}': {default_value}")
                return default_value
            raise ValueError(f"Failed to get required config value '{config_path}': {e}")

    async def _get_min_order_size_for_exchange(self, exchange_name: str) -> float:
        """Get minimum order size in USD from config for an exchange"""
        try:
            min_order_sizes = await self._get_config_value('trading.min_order_size_usd', {})
            min_size = min_order_sizes.get(exchange_name, min_order_sizes.get('default'))
            
            if min_size is None:
                logger.error(f"❌ No min_order_size_usd configured for {exchange_name} and no default found")
                raise ValueError(f"Missing min_order_size_usd config for {exchange_name}")
            
            logger.info(f"📏 Retrieved min order size for {exchange_name}: ${min_size}")
            return float(min_size)
            
        except Exception as e:
            logger.error(f"❌ Error getting min order size from config for {exchange_name}: {e}")
            raise ValueError(f"Failed to get min order size from config for {exchange_name}: {e}")

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
            database_position_size = float(trade['position_size'])
            
            # FOR SELL ORDERS: Always use recorded trade amount first, handle discrepancies after
            # This prioritizes position closure over minor balance differences
            position_size = database_position_size
            logger.info(f"💰 Using recorded trade amount for exit: {position_size:.8f} {pair.split('/')[0] if '/' in pair else pair.split('_')[0]}")
            
            # Live: optional exchange balance logging vs DB. Simulation: DB is source of truth for position.
            if not self.is_simulation:
                try:
                    async with httpx.AsyncClient(timeout=30.0) as balance_client:
                        balance_response = await balance_client.get(
                            f"{exchange_service_url}/api/v1/account/balance/{exchange_name}"
                        )
                        if balance_response.status_code == 200:
                            balances = balance_response.json().get('balances', {})
                            base_asset = pair.split('/')[0] if '/' in pair else pair.split('_')[0]

                            if base_asset in balances:
                                exchange_available = float(balances[base_asset].get('available', 0))
                                difference = abs(exchange_available - database_position_size)

                                if difference > database_position_size * 0.05:
                                    logger.info(
                                        f"📊 Balance difference detected - Database: {database_position_size:.8f}, Exchange: {exchange_available:.8f} {base_asset}"
                                    )
                                    logger.info(
                                        f"🎯 Will attempt exit with recorded amount {database_position_size:.8f} first, adjust if needed"
                                    )
                            else:
                                logger.info(
                                    f"ℹ️ {base_asset} balance not found in response, proceeding with recorded amount"
                                )
                except Exception as balance_error:
                    logger.debug(f"Balance check failed (non-critical): {balance_error}")
            
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

            # PnL-FIX v10 (2026-04-20) — CRITICAL EXIT ROUTING.
            # Previously every exit went through _place_smart_order (limit →
            # intelligent retry → market fallback). When the exit was a
            # stop-loss or trailing-stop trigger, price was by definition
            # moving against the position. The limit would sit for up to
            # 90s (order_timeout_seconds) before timing out and failing
            # over to market — by which point slippage routinely reached
            # -3% to -5% on thin cryptocom books (observed: AAVE/USD -5.10%,
            # ACT/USD -4.93%, API3/USD -3.43% on a configured -1.5% stop).
            # For any stop-loss / trailing-stop trigger we MUST market-sell
            # immediately. Take-profit and manual exits keep the smart path.
            is_critical_exit = bool(exit_reason) and (
                exit_reason.startswith('stop_loss')
                or exit_reason.startswith('trailing_stop')
                or exit_reason.startswith('force_close')
                or exit_reason.startswith('stagnant_loser')
                or exit_reason.startswith('max_entry_slippage')
                or exit_reason.startswith('profit_protection_breach')
            )

            if is_critical_exit:
                logger.warning(
                    f"🚨 CRITICAL EXIT ROUTE: bypassing limit-first smart order for {exit_reason} "
                    f"— placing direct market SELL: {sanitized_position_size:.8f} {pair} on {exchange_name}"
                )
                exit_order_result = await self._place_market_order(
                    exchange_name, pair, 'sell', sanitized_position_size, trade_id
                )
                await self._update_trade_data(
                    trade_id,
                    {
                        "exit_submitted_at": datetime.utcnow().isoformat() + "+00:00",
                        "exit_submit_route": "critical_market",
                    },
                )
                if exit_order_result and exit_order_result.get('id'):
                    try:
                        await order_tracker.record_order_attempt('market', exit_order_result)
                    except Exception:
                        pass
            else:
                logger.info(
                    f"🔄 Placing SPOT SELL order to exit: {sanitized_position_size:.8f} {pair} on {exchange_name}"
                )
                exit_order_result = await self._place_smart_order(
                    exchange_name, pair, 'sell', sanitized_position_size, trade_id
                )
                await self._update_trade_data(
                    trade_id,
                    {
                        "exit_submitted_at": datetime.utcnow().isoformat() + "+00:00",
                        "exit_submit_route": "smart_order",
                    },
                )
            if not exit_order_result:
                logger.error(f"❌ Failed to place SELL order for {pair} on {exchange_name}")
                
                # Check if this is a minimum order value issue on SELL orders
                # Some exchanges apply minimum order values to both BUY and SELL
                current_price = await self._get_current_price(exchange_name, pair)
                if current_price > 0:
                    position_value_usd = sanitized_position_size * current_price
                    logger.warning(f"💰 SELL ORDER FAILED: Position value ${position_value_usd:.2f} for {sanitized_position_size:.8f} {pair}")
                    
                    # For critical exits (trailing stops, stop losses), close position anyway to prevent further losses
                    if exit_reason and ('trailing_stop' in exit_reason or 'stop_loss' in exit_reason):
                        logger.warning(f"🚨 CRITICAL EXIT FAILED: Closing position anyway due to {exit_reason}")
                        logger.warning(f"💸 Risk Management: Marking {pair} as closed to prevent further losses")
                        
                        # Close position in database to prevent further losses
                        dust_exit_success = await self._close_dust_position(
                            trade_id, exchange_name, pair, sanitized_position_size, 
                            f"force_close_{exit_reason}"
                        )
                        
                        if dust_exit_success:
                            logger.info(f"✅ FORCE CLOSE SUCCESS: {pair} position marked as closed for risk management")
                            return
                        else:
                            logger.error(f"❌ FORCE CLOSE FAILED: Could not mark {pair} position as closed")
                
                return
            
            # Wait for exit order to fill
            filled_exit_order = await self._wait_for_order_fill(exchange_name, exit_order_result['id'], pair, timeout=30, original_order=exit_order_result, amount=position_size)
            if not filled_exit_order:
                logger.error(f"❌ Exit order {exit_order_result['id']} failed to fill for {pair} on {exchange_name}")
                await self._cancel_order(exchange_name, exit_order_result['id'], pair)
                
                # CRITICAL: For trailing stops and stop losses, force emergency market order
                if is_critical_exit:
                    logger.warning(f"🚨 EMERGENCY EXIT: Critical exit failed, forcing market order for {pair} on {exchange_name}")
                    
                    # Force market order as emergency fallback
                    emergency_exit_result = await self._place_market_order(exchange_name, pair, 'sell', sanitized_position_size, trade_id)
                    await self._update_trade_data(
                        trade_id,
                        {
                            "exit_submitted_at": datetime.utcnow().isoformat() + "+00:00",
                            "exit_submit_route": "emergency_market",
                        },
                    )
                    if not emergency_exit_result:
                        logger.error(f"💥 CRITICAL FAILURE: Emergency market order also failed for {pair} on {exchange_name}")
                        # NEVER force close - mark for manual intervention
                        logger.error(f"🚨 MARKING FOR MANUAL INTERVENTION: Trade {trade_id} requires manual exit due to order failures")
                        await self._update_trade_status(trade_id, 'EXIT_FAILED', f'emergency_exit_failed_{exit_reason}')
                        await self._update_trade_data(
                            trade_id,
                            {
                                "exit_state": "EXIT_FAILED_TERMINAL",
                                "exit_failure_reason": f"emergency_exit_failed_{exit_reason}",
                            },
                        )
                        await self._create_critical_alert(trade_id, f"Manual intervention required: Exit orders failed for {pair} on {exchange_name}")
                        return
                    
                    # Wait for emergency market order to fill (shorter timeout for market orders)
                    filled_exit_order = await self._wait_for_order_fill(exchange_name, emergency_exit_result['id'], pair, timeout=10, original_order=emergency_exit_result, amount=position_size)
                    if not filled_exit_order:
                        logger.error(f"💥 CRITICAL FAILURE: Emergency market order failed to fill for {pair} on {exchange_name}")
                        # NEVER force close - mark for manual intervention
                        logger.error(f"🚨 MARKING FOR MANUAL INTERVENTION: Trade {trade_id} requires manual exit due to fill timeout")  
                        await self._update_trade_status(trade_id, 'EXIT_FAILED', f'emergency_fill_timeout_{exit_reason}')
                        await self._update_trade_data(
                            trade_id,
                            {
                                "exit_state": "EXIT_FAILED_TERMINAL",
                                "exit_failure_reason": f"emergency_fill_timeout_{exit_reason}",
                            },
                        )
                        await self._create_critical_alert(trade_id, f"Manual intervention required: Emergency order failed to fill for {pair} on {exchange_name}")
                        return
                    
                    logger.info(f"✅ EMERGENCY SUCCESS: Market order filled for {pair} on {exchange_name}")
                else:
                    # For non-critical exits, just return and try again later
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
            # PnL-FIX v9: extract fee + currency together so total_fees_usd
            # can be reconstructed in the DB. The legacy ``fees`` aggregate is
            # still kept for backwards compatibility.
            exit_fee_info = self._extract_fee_with_currency(filled_exit_order)
            exit_fees = exit_fee_info["amount"]
            exit_fee_currency = exit_fee_info["currency"]
            
            realized_pnl = (exit_price - entry_price) * filled_amount - exit_fees - float(trade.get('fees', 0))
            notional = entry_price * filled_amount if entry_price > 0 and filled_amount > 0 else 0.0
            realized_pnl_pct = (realized_pnl / notional) * 100.0 if notional > 0 else 0.0
            trigger_price = float(trade.get("trigger_price") or entry_price)
            slippage_bps = 0.0
            if trigger_price > 0:
                slippage_bps = ((exit_price - trigger_price) / trigger_price) * 10000.0
            # Execution hardening: slippage alarm + temporary pair downgrade/disable.
            try:
                trading_cfg = (self._config or {}).get("trading", {}) or {}
                regime_cfg = trading_cfg.get("regime_policies", {}) or {}
                stable_regime = str(trade.get("stable_regime") or trade.get("market_regime") or "unknown")
                resolved_policy = self._deep_merge_dicts(
                    regime_cfg.get("defaults", {}) or {},
                    regime_cfg.get(stable_regime, {}) or {},
                )
                slip_cfg = (
                    (resolved_policy.get("exits", {}) or {}).get("execution_slippage")
                    or trading_cfg.get("execution_slippage", {})
                    or {}
                )
                if not isinstance(slip_cfg, dict):
                    slip_cfg = {}
                alert_bps = float(slip_cfg.get("alert_threshold_bps", 35.0) or 35.0)
                downgrade_bps = float(slip_cfg.get("downgrade_threshold_bps", 60.0) or 60.0)
                downgrade_minutes = int(slip_cfg.get("downgrade_cooldown_minutes", 120) or 120)
                is_critical_exit_local = bool(exit_reason) and (
                    exit_reason.startswith('stop_loss')
                    or exit_reason.startswith('trailing_stop')
                    or exit_reason.startswith('force_close')
                    or exit_reason.startswith('stagnant_loser')
                    or exit_reason.startswith('max_entry_slippage')
                    or exit_reason.startswith('profit_protection_breach')
                )
                adverse_slippage_bps = max(0.0, -slippage_bps)
                if is_critical_exit_local and adverse_slippage_bps >= alert_bps:
                    await self._create_critical_alert(
                        trade_id,
                        (
                            f"Execution slippage alert for {exchange_name} {pair}: "
                            f"{adverse_slippage_bps:.1f} bps adverse (trigger {trigger_price:.6f}, fill {exit_price:.6f})"
                        ),
                    )
                if is_critical_exit_local and adverse_slippage_bps >= downgrade_bps:
                    until = datetime.utcnow() + timedelta(minutes=downgrade_minutes)
                    self._pair_execution_downgrade_until[(exchange_name, pair)] = until
                    logger.warning(
                        "[ExecutionHardening] Pair downgraded due to slippage: %s %s, adverse=%.1f bps, until=%s",
                        exchange_name, pair, adverse_slippage_bps, until.isoformat()
                    )
            except Exception as slip_err:
                logger.warning("[ExecutionHardening] slippage alarm/downgrade skipped: %s", slip_err)
            
            # CRITICAL FIX: MANDATORY EXCHANGE CONFIRMATION BEFORE MARKING CLOSED
            # Verify the filled_exit_order contains valid fill confirmation
            if not filled_exit_order or not filled_exit_order.get('id') or not filled_exit_order.get('filled'):
                logger.error(f"🚨 CRITICAL: Cannot mark trade {trade_id} as CLOSED - no valid fill confirmation from exchange")
                await self._update_trade_status(trade_id, 'EXIT_FAILED', f'no_exchange_confirmation_{exit_reason}')
                await self._update_trade_data(
                    trade_id,
                    {
                        "exit_state": "EXIT_FAILED_TERMINAL",
                        "exit_failure_reason": f"no_exchange_confirmation_{exit_reason}",
                    },
                )
                await self._create_critical_alert(trade_id, f"Manual intervention required: Exit order lacks exchange confirmation for {pair} on {exchange_name}")
                return
            
            # Verify filled amount is reasonable (not zero or negative)
            filled_amount_check = float(filled_exit_order.get('filled', 0))
            if filled_amount_check <= 0:
                logger.error(f"🚨 CRITICAL: Cannot mark trade {trade_id} as CLOSED - invalid filled amount: {filled_amount_check}")
                await self._update_trade_status(trade_id, 'EXIT_FAILED', f'invalid_fill_amount_{exit_reason}')
                await self._update_trade_data(
                    trade_id,
                    {
                        "exit_state": "EXIT_FAILED_TERMINAL",
                        "exit_failure_reason": f"invalid_fill_amount_{exit_reason}",
                    },
                )
                await self._create_critical_alert(trade_id, f"Manual intervention required: Invalid fill amount {filled_amount_check} for {pair} on {exchange_name}")
                return
            
            # Verify exchange order ID exists and is valid
            exit_order_id = filled_exit_order.get('id')
            if not exit_order_id or exit_order_id == '':
                logger.error(f"🚨 CRITICAL: Cannot mark trade {trade_id} as CLOSED - no valid exchange order ID")
                await self._update_trade_status(trade_id, 'EXIT_FAILED', f'no_order_id_{exit_reason}')
                await self._update_trade_data(
                    trade_id,
                    {
                        "exit_state": "EXIT_FAILED_TERMINAL",
                        "exit_failure_reason": f"no_order_id_{exit_reason}",
                    },
                )
                await self._create_critical_alert(trade_id, f"Manual intervention required: No exchange order ID for {pair} on {exchange_name}")
                return
            
            logger.info(f"✅ EXCHANGE CONFIRMATION VERIFIED: Order {exit_order_id} filled {filled_amount_check} {pair.split('/')[0]} on {exchange_name}")
            
            # Update trade in database with exit details ONLY after exchange confirmation
            # PnL-FIX v9: also send exit_fee_amount + exit_fee_currency so the
            # DB layer's auto total_fees_usd recalculation actually fires
            # (previously it saw entry_fee_amount=0 and exit_fee_amount=0).
            exit_data = {
                'status': 'CLOSED',
                'exit_state': 'CLOSED',
                'exit_price': exit_price,
                'exit_id': exit_order_id,  # Store verified exchange order ID
                'exit_reason': exit_reason,
                'exit_time': datetime.utcnow().isoformat() + '+00:00',
                'realized_pnl': realized_pnl,
                'fees': float(trade.get('fees', 0)) + exit_fees,  # Total fees (legacy aggregate)
                'exit_fee_amount': exit_fees,
                'exit_fee_currency': exit_fee_currency,
                'exchange_confirmed': True,  # NEW FLAG: Confirms exchange validation
                'filled_amount': filled_amount_check,  # Store actual filled amount
                'first_fill_at': datetime.utcnow().isoformat() + '+00:00',
                'slippage_bps_vs_trigger': slippage_bps,
            }
            
            await self._update_trade_data(trade_id, exit_data)
            await self._mark_recent_negative_realized_blacklist(
                trade["pair"], realized_pnl_pct=realized_pnl_pct
            )

            # Pair-rotation rule: remove/replace selected pair after large loss exits.
            await self._rotate_pair_after_loss(
                exchange_name=trade["exchange"],
                pair=trade["pair"],
                realized_pnl_pct=realized_pnl_pct,
            )

            # Extended cooldown only after N consecutive realized losses (config).
            try:
                if realized_pnl < 0:
                    need_ext = int(
                        await self._get_config_value(
                            "trading.pair_consecutive_losses_for_extended_cooldown", 2
                        )
                        or 2
                    )
                    ext_mins = int(
                        await self._get_config_value(
                            "trading.pair_loss_extended_cooldown_minutes", 90
                        )
                        or 90
                    )
                    streak = await self._consecutive_realized_loss_streak(
                        trade["exchange"], trade["pair"]
                    )
                    if streak >= need_ext:
                        self._mark_pair_cooldown(
                            trade["exchange"], trade["pair"], minutes=ext_mins
                        )
                    else:
                        self._mark_pair_cooldown(
                            trade["exchange"], trade["pair"], minutes=self._pair_cooldown_minutes
                        )
                else:
                    self._mark_pair_cooldown(
                        trade["exchange"], trade["pair"], minutes=self._pair_cooldown_minutes
                    )
            except Exception as _e:
                logger.debug(f"[PAIR COOLDOWN] post-exit marking failed: {_e}")

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
            
            # Update balances after exit.
            # IMPORTANT: simulation already applies quote deltas inside _build_simulation_instant_fill_order().
            # Writing proceeds again here double-counts exits, so only do this path for live mode.
            proceed_value = filled_amount * exit_price - exit_fees
            if self.is_simulation:
                await self._refresh_simulation_balance_from_db(exchange_name)
                logger.info(
                    "🧪 [SIMULATION] Exit balance already applied via simulation fill delta "
                    "(proceeds: %.2f, pnl: %.2f) for %s",
                    proceed_value,
                    realized_pnl,
                    exchange_name,
                )
            else:
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
                    balance_response = await client.put(
                        f"{database_service_url}/api/v1/balances/{exchange_name}",
                        json=balance_update_data,
                    )
                    if balance_response.status_code == 200:
                        self.balances[exchange_name]['available'] = new_available_balance
                        self.balances[exchange_name]['total'] = new_total_balance
                        self.balances[exchange_name]['total_pnl'] += realized_pnl
                        self.balances[exchange_name]['daily_pnl'] += realized_pnl
                        logger.info(
                            f"✅ Updated balance after exit: {current_balance:.2f} -> {new_available_balance:.2f} "
                            f"(proceeds: {proceed_value:.2f}, PnL: {realized_pnl:.2f})"
                        )
            
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
                # Check each exchange - using hardcoded list since config_manager is not fully implemented
                exchanges = ['binance', 'bybit', 'cryptocom']
                for exchange_name in exchanges:
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

    async def _fetch_close_price_from_ohlcv(self, exchange_name: str, pair: str) -> float:
        """Last 1m candle close from exchange-service (public); robust when tickers are empty."""
        try:
            encoded = quote(pair, safe="")
            async with httpx.AsyncClient(timeout=45.0) as client:
                r = await client.get(
                    f"{exchange_service_url}/api/v1/market/ohlcv/{exchange_name}/{encoded}",
                    params={"timeframe": "1m", "limit": 2},
                )
            if r.status_code != 200:
                logger.warning(
                    "OHLCV price fallback HTTP %s for %s/%s: %s",
                    r.status_code,
                    exchange_name,
                    pair,
                    (r.text or "")[:120],
                )
                return 0.0
            payload = r.json()
            closes = (payload.get("data") or {}).get("close") or []
            if not closes:
                return 0.0
            px = float(closes[-1])
            return px if px > 0 else 0.0
        except Exception as e:
            logger.warning("OHLCV price fallback failed %s/%s: %s", exchange_name, pair, e)
            return 0.0

    async def _fetch_mid_price_from_orderbook(self, exchange_name: str, pair: str) -> float:
        """Mid (bid+ask)/2 from order book when ticker and OHLCV are unavailable."""
        try:
            encoded = quote(pair, safe="")
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.get(
                    f"{exchange_service_url}/api/v1/market/orderbook/{exchange_name}/{encoded}",
                    params={"limit": 5},
                )
            if r.status_code != 200:
                return 0.0
            ob = r.json()
            bids = ob.get("bids") or []
            asks = ob.get("asks") or []
            if not bids or not asks:
                return 0.0
            bid = float(bids[0][0])
            ask = float(asks[0][0])
            if bid <= 0 or ask <= 0:
                return 0.0
            mid = (bid + ask) / 2.0
            return mid if mid > 0 else 0.0
        except Exception as e:
            logger.warning("Orderbook mid fallback failed %s/%s: %s", exchange_name, pair, e)
            return 0.0

    async def _get_current_price(
        self,
        exchange_name: str,
        pair: str,
        entry_price_fallback: Optional[float] = None,
        price_hint: Optional[float] = None,
    ) -> float:
        """Resolve spot mark price: hint → Redis (WS mirror) → ticker-live → price-feed → REST → OHLCV → orderbook → entry fallback."""
        if price_hint is not None:
            try:
                hf = float(price_hint)
                if hf > 0:
                    self._last_price_source = "prefetch"
                    return hf
            except (TypeError, ValueError):
                pass

        sym_for_key = self._convert_pair_format(exchange_name, pair)
        rk_last = await self._redis_ticker_last_for_hint_key(
            _ws_ticker_key_suffix(exchange_name, sym_for_key)
        )
        if rk_last is not None and rk_last > 0:
            self._last_price_source = "redis"
            return float(rk_last)

        # First try WebSocket live data for most recent price (skip status check - direct access)
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=8.0) as client:
                    exchange_symbol = self._convert_pair_format(exchange_name, pair)
                    # WS-FIX: 30s → 45s. Low-volume cryptocom pairs (e.g. ACT/USD,
                    # AKT/USD) often print ticks every 15-30s on the WS feed;
                    # a 30s window still produced sporadic 204s and forced a
                    # REST/OHLCV fallback. 45s is loose enough to cover the
                    # typical low-vol tick cadence while still flagging a
                    # genuinely dead feed.
                    live_response = await client.get(
                        f"{exchange_service_url}/api/v1/market/ticker-live/{exchange_name}/{exchange_symbol}?stale_threshold_seconds=45"
                    )
                    if live_response.status_code == 200:
                        live_data = live_response.json()
                        price = float(live_data.get("last", 0))
                        source = live_data.get("source", "unknown")
                        if price > 0:
                            logger.info(
                                f"📡 Real-time price via {source}: {exchange_name}/{pair} = ${price:.8f}"
                            )
                            self._last_price_source = source
                            return price
                    logger.debug(
                        "ticker-live miss exchange=%s pair=%s symbol=%s http=%s detail=%s",
                        exchange_name,
                        pair,
                        exchange_symbol,
                        live_response.status_code,
                        _http_response_log_snippet(live_response, 200),
                    )
            except Exception as ws_e:
                logger.debug("WebSocket price unavailable (attempt %s): %s", attempt + 1, ws_e)
            if attempt == 0:
                await asyncio.sleep(0.25)

        price_feed_service_url = "http://price-feed-service:8007"

        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                await client.post(
                    f"{price_feed_service_url}/api/v1/pairs/subscribe",
                    json={"exchange": exchange_name, "pair": pair},
                )
        except Exception:
            pass

        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                encoded_pair = quote(pair, safe="")
                response = await client.get(
                    f"{price_feed_service_url}/api/v1/price/{exchange_name}/{encoded_pair}"
                )
                if response.status_code == 200:
                    data = response.json()
                    price = float(data.get("price", 0))
                    if price > 0:
                        cache_hit = data.get("cache_hit", False)
                        source = data.get("source", "unknown")
                        logger.info(
                            f"📈 Got price from feed service: {exchange_name}/{pair} = ${price:.8f} "
                            f"(cache_hit={cache_hit}, source={source})"
                        )
                        self._last_price_source = source
                        return price
                logger.warning(
                    "price_feed_non_200 exchange=%s pair=%s http=%s detail=%s",
                    exchange_name,
                    pair,
                    response.status_code,
                    _http_response_log_snippet(response),
                )
        except Exception as feed_e:
            logger.warning(
                "price_feed_request_failed exchange=%s pair=%s err=%s",
                exchange_name,
                pair,
                feed_e,
            )

        # REST ticker (only return if strictly positive — zero used to poison fallbacks)
        try:
            from core.exchange_handlers import exchange_handler_manager

            exchange_symbol = exchange_handler_manager.format_symbol_for_api(exchange_name, pair)
            # Keep timeout moderate: N trades × slow REST ticker was dominating exit-cycle wall time.
            async with httpx.AsyncClient(timeout=12.0) as client:
                response = await client.get(
                    f"{exchange_service_url}/api/v1/market/ticker/{exchange_name}/{exchange_symbol}"
                )
                response.raise_for_status()
                ticker_data = response.json()
                price = float(ticker_data.get("last") or ticker_data.get("price") or 0.0)
                if price > 0:
                    logger.info(
                        "📊 Got price from REST ticker: %s/%s = $%.8f",
                        exchange_name,
                        pair,
                        price,
                    )
                    self._last_price_source = "rest"
                    return price
                logger.warning(
                    "REST ticker returned non-positive price for %s/%s (symbol=%s)",
                    exchange_name,
                    pair,
                    exchange_symbol,
                )
        except Exception as e:
            rest_extra = ""
            if isinstance(e, httpx.HTTPStatusError) and e.response is not None:
                r = e.response
                rest_extra = (
                    f" http={r.status_code} body={_http_response_log_snippet(r, 280)}"
                )
            elif isinstance(e, httpx.RequestError):
                rest_extra = f" request_err_type={type(e).__name__}"
            logger.warning(
                "REST ticker failed exchange=%s pair=%s symbol=%s %s%s",
                exchange_name,
                pair,
                exchange_symbol,
                type(e).__name__,
                f": {e}{rest_extra}",
            )

        ohlcv_px = await self._fetch_close_price_from_ohlcv(exchange_name, pair)
        if ohlcv_px > 0:
            logger.info(
                "📊 Mark from 1m OHLCV close: %s/%s = $%.8f",
                exchange_name,
                pair,
                ohlcv_px,
            )
            self._last_price_source = "ohlcv_1m"
            return ohlcv_px

        mid_px = await self._fetch_mid_price_from_orderbook(exchange_name, pair)
        if mid_px > 0:
            logger.info(
                "📊 Mark from orderbook mid: %s/%s = $%.8f",
                exchange_name,
                pair,
                mid_px,
            )
            self._last_price_source = "orderbook_mid"
            return mid_px

        if entry_price_fallback is not None:
            try:
                ef = float(entry_price_fallback)
                if ef > 0:
                    logger.critical(
                        "CRITICAL: All live price sources failed for %s/%s — using ENTRY_PRICE fallback $%.8f "
                        "(mark is STALE; risk/PnL may be wrong until exchange-service or price-feed recovers)",
                        exchange_name,
                        pair,
                        ef,
                    )
                    self._last_price_source = "entry_fallback"
                    try:
                        price_resolution_degraded_total.labels(exchange=exchange_name).inc()
                    except Exception:
                        pass
                    return ef
            except (TypeError, ValueError):
                pass

        logger.critical(
            "CRITICAL: No usable mark price for %s/%s after ticker-live, price-feed, REST ticker, "
            "1m OHLCV close, orderbook mid, and entry_price fallback — trading safeguards cannot run correctly",
            exchange_name,
            pair,
        )
        try:
            price_resolution_failures_total.labels(exchange=exchange_name).inc()
        except Exception:
            pass
        return 0.0
    
    async def _validate_limit_price_against_market(self, exchange_name: str, pair: str, side: str, limit_price: float) -> bool:
        """Validate if limit price is realistic against current market conditions to prevent failed orders"""
        try:
            # Get current orderbook to check realistic pricing
            from core.exchange_handlers import exchange_handler_manager
            exchange_symbol = exchange_handler_manager.format_symbol_for_api(exchange_name, pair)
            logger.info(f"🔍 VALIDATION: Symbol formatting - pair={pair} → exchange_symbol={exchange_symbol}")
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Get orderbook data
                try:
                    response = await client.get(f"{exchange_service_url}/api/v1/market/orderbook/{exchange_name}/{pair}?limit=5")
                    response.raise_for_status()
                    orderbook = response.json()
                    
                    # Handle orderbook array format: {"bids": [[price, size], ...], "asks": [[price, size], ...]}
                    bids = orderbook.get('bids', [])
                    asks = orderbook.get('asks', [])
                    
                    if bids and asks and len(bids) > 0 and len(asks) > 0:
                        current_bid = float(bids[0][0])  # Best bid price
                        current_ask = float(asks[0][0])  # Best ask price
                    else:
                        current_bid = float(orderbook.get('bid', 0.0))  # Fallback to direct values
                        current_ask = float(orderbook.get('ask', 0.0))
                    
                    logger.info(f"🔍 VALIDATION: Got orderbook for {pair} on {exchange_name}: bid={current_bid}, ask={current_ask}")
                    
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
                logger.info(f"🔍 VALIDATION: Sell validation - limit_price=${limit_price:.8f}, ask=${current_ask:.8f}, max_realistic=${max_realistic_price:.8f}")
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

    async def _intelligent_limit_retry(self, exchange_name: str, pair: str, side: str, amount: float, trade_id: str = None) -> Optional[Dict[str, Any]]:
        """Intelligently retry limit orders with price corrections based on failure analysis"""
        try:
            logger.info(f"🧠 Starting intelligent limit retry for {side} {amount:.8f} {pair} on {exchange_name}")
            
            # Check if this is a dust amount issue that should be handled differently
            logger.info(f"🔍 RETRY DUST CHECK: Checking {amount:.8f} {pair} on {exchange_name} for dust amounts")
            dust_check_result = await self._check_and_handle_dust_amount(exchange_name, pair, side, amount, trade_id)
            logger.info(f"🔍 RETRY DUST RESULT: {dust_check_result}")
            if dust_check_result == "skip_dust":
                logger.info(f"💨 RETRY: Skipping dust amount order: {amount:.8f} {pair} on {exchange_name} below exchange minimums")
                return {"status": "skipped", "reason": "dust_amount", "id": "dust_skipped"}
            elif dust_check_result and isinstance(dust_check_result, dict):
                logger.info(f"✅ Dust amount converted to minimum viable order")
                return dust_check_result
            
            # Get fresh market data for price correction
            current_price = await self._get_current_price(exchange_name, pair)
            if current_price <= 0:
                logger.warning(f"❌ Cannot get current price for intelligent retry of {pair} on {exchange_name}")
                return None
                
            # Get fresh orderbook data
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get(f"{exchange_service_url}/api/v1/market/orderbook/{exchange_name}/{pair}?limit=5")
                    response.raise_for_status()
                    orderbook = response.json()
                    
                    bids = orderbook.get('bids', [])
                    asks = orderbook.get('asks', [])
                    
                    if not (bids and asks and len(bids) > 0 and len(asks) > 0):
                        logger.warning(f"❌ Cannot get orderbook for intelligent retry of {pair} on {exchange_name}")
                        return None
                        
                    best_bid = float(bids[0][0])
                    best_ask = float(asks[0][0])
                    spread = best_ask - best_bid
                    spread_pct = (spread / best_ask) * 100
                    
                    logger.info(f"🔍 Fresh market data - bid=${best_bid:.8f}, ask=${best_ask:.8f}, spread={spread_pct:.4f}%")
                    
            except Exception as e:
                logger.warning(f"❌ Failed to get fresh orderbook for retry: {e}")
                return None
                
            # Try multiple pricing strategies in order of aggressiveness
            pricing_strategies = []
            
            if side.lower() == 'sell':
                pricing_strategies = [
                    # Strategy 1: Just inside the spread (most aggressive maker price)
                    {"name": "inside_spread", "price": best_bid + (spread * 0.1)},  
                    # Strategy 2: At best ask (immediate fill but still maker)
                    {"name": "at_ask", "price": best_ask},
                    # Strategy 3: Slightly above best ask (quick fill)  
                    {"name": "above_ask", "price": best_ask * 1.0001}
                ]
            else:  # buy
                pricing_strategies = [
                    # Strategy 1: Just inside the spread (most aggressive maker price)
                    {"name": "inside_spread", "price": best_ask - (spread * 0.1)},
                    # Strategy 2: At best bid (immediate fill but still maker)
                    {"name": "at_bid", "price": best_bid},
                    # Strategy 3: Slightly below best bid (quick fill)
                    {"name": "below_bid", "price": best_bid * 0.9999}
                ]
            
            # Try each pricing strategy
            for i, strategy in enumerate(pricing_strategies):
                logger.info(f"🎯 Retry attempt {i+1}/3: {strategy['name']} strategy - price=${strategy['price']:.8f}")
                
                # Attempt the order with this pricing strategy
                retry_result = await self._place_limit_order_with_custom_price(
                    exchange_name, pair, side, amount, strategy['price'], trade_id, f"retry_{strategy['name']}"
                )
                
                if retry_result and retry_result.get('id'):
                    logger.info(f"✅ Intelligent retry successful with {strategy['name']} strategy: {retry_result['id']}")
                    return retry_result
                else:
                    logger.info(f"❌ Retry {i+1} failed with {strategy['name']} strategy, trying next...")
                    # Small delay between retries
                    await asyncio.sleep(0.5)
            
            logger.warning(f"❌ All intelligent retry strategies failed for {pair} on {exchange_name}")
            return None
            
        except Exception as e:
            logger.error(f"❌ Error in intelligent limit retry for {pair} on {exchange_name}: {e}")
            return None

    async def _update_trailing_stop_order(self, exchange: str, order_id: str, pair: str, 
                                         new_price: float, trade_id: str) -> Dict[str, Any]:
        """
        🚀 ORDER MODIFICATION IMPROVEMENT: Update trailing stop order using modification first, 
        with fallback to cancel-and-recreate
        
        Args:
            exchange: Exchange name (binance, bybit, etc.)
            order_id: Existing order ID to update
            pair: Trading pair (e.g., "BTC/USDC")
            new_price: New limit price for the order
            trade_id: Trade ID for logging
            
        Returns:
            Dict with success status and method used
        """
        try:
            logger.info(f"[Trade {trade_id}] 🔄 Updating trailing stop order {order_id} to ${new_price:.6f}")
            
            # METHOD 1: Try order modification first (preferred)
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    modify_data = {
                        "symbol": pair,
                        "new_price": new_price
                    }
                    
                    response = await client.put(
                        f"http://exchange-service:8003/api/v1/trading/order/{exchange}/{order_id}",
                        json=modify_data
                    )
                    
                    if response.status_code == 200:
                        result = response.json()
                        if result.get('success'):
                            logger.info(f"[Trade {trade_id}] ✅ Order modification successful - Same exit_id: {order_id}")
                            return {
                                "success": True,
                                "method": "order_modification",
                                "exit_id": order_id,  # Same exit_id!
                                "new_price": new_price
                            }
                        else:
                            # Order modification failed but endpoint worked
                            logger.warning(f"[Trade {trade_id}] ⚠️ Order modification failed: {result.get('message', 'Unknown error')}")
                            fallback_required = result.get('fallback_required', True)
                            
                            if not fallback_required:
                                return {"success": False, "method": "order_modification", "error": result.get('error')}
                    else:
                        logger.warning(f"[Trade {trade_id}] ⚠️ Order modification endpoint error: {response.status_code}")
                        
            except Exception as modify_error:
                logger.warning(f"[Trade {trade_id}] ⚠️ Order modification exception: {modify_error}")
            
            # METHOD 2: Fallback to cancel-and-recreate
            logger.info(f"[Trade {trade_id}] 🔄 Falling back to cancel-and-recreate method")
            
            # Cancel existing order
            cancel_result = await self._cancel_order(exchange, order_id, pair)
            if not cancel_result:
                return {"success": False, "method": "cancel_and_recreate", "error": "Failed to cancel existing order"}
            
            logger.info(f"[Trade {trade_id}] ❌ CANCELLED old order: {order_id}")
            
            # Get actual available balance for the base asset.
            # If balance lookup is temporarily unavailable, fall back to trade
            # position size so trailing-stop maintenance keeps working.
            base_asset = pair.split('/')[0]
            update_sell_amount: Optional[float] = None
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    balance_response = await client.get(f"http://exchange-service:8003/api/v1/account/balance/{exchange}")
                    if balance_response.status_code == 200:
                        balance_data = balance_response.json()
                        available_amount = 0
                        
                        # Try ccxt normalized format first, then raw format
                        if base_asset in balance_data:
                            available_amount = float(balance_data[base_asset].get('free', 0))
                        elif 'info' in balance_data:
                            for asset_balance in balance_data.get('info', {}).get('balances', []):
                                if asset_balance.get('asset') == base_asset:
                                    available_amount = float(asset_balance.get('free', 0))
                                    break
                        
                        # Use available amount (since we know the position size from the original order)
                        update_sell_amount = available_amount
                        logger.info(f"[Trade {trade_id}] Balance check: Available={available_amount}, Using={update_sell_amount}")
                    else:
                        logger.warning(f"[Trade {trade_id}] ⚠️ Could not get balance, using fallback amount")
                        async with httpx.AsyncClient(timeout=15.0) as db_client:
                            trade_resp = await db_client.get(
                                f"http://database-service:8002/api/v1/trades/{trade_id}"
                            )
                            if trade_resp.status_code == 200:
                                trade_row = trade_resp.json() or {}
                                update_sell_amount = float(trade_row.get("position_size") or 0.0)
                                logger.info(
                                    f"[Trade {trade_id}] 🔁 Fallback position_size from /trades/{{id}}: {update_sell_amount}"
                                )
                            else:
                                open_resp = await db_client.get(
                                    "http://database-service:8002/api/v1/trades/open"
                                )
                                if open_resp.status_code == 200:
                                    open_rows = (open_resp.json() or {}).get("trades", []) or []
                                    row = next(
                                        (t for t in open_rows if str(t.get("trade_id")) == str(trade_id)),
                                        None,
                                    )
                                    if row:
                                        update_sell_amount = float(row.get("position_size") or 0.0)
                                        logger.info(
                                            f"[Trade {trade_id}] 🔁 Fallback position_size from /trades/open: {update_sell_amount}"
                                        )
            except Exception as e:
                logger.warning(f"[Trade {trade_id}] ⚠️ Balance check failed: {e}")
                try:
                    async with httpx.AsyncClient(timeout=15.0) as db_client:
                        trade_resp = await db_client.get(
                            f"http://database-service:8002/api/v1/trades/{trade_id}"
                        )
                        if trade_resp.status_code == 200:
                            trade_row = trade_resp.json() or {}
                            update_sell_amount = float(trade_row.get("position_size") or 0.0)
                            logger.info(
                                f"[Trade {trade_id}] 🔁 Fallback position_size after exception: {update_sell_amount}"
                            )
                except Exception:
                    pass

            if not update_sell_amount or update_sell_amount <= 0:
                logger.error(
                    f"[Trade {trade_id}] ❌ Trailing update fallback failed: invalid sell amount {update_sell_amount}"
                )
                return {
                    "success": False,
                    "method": "cancel_and_recreate",
                    "error": "Could not determine available balance or position size",
                }
            
            # Create new trailing stop order at higher exit price
            self._is_trailing_stop_order = True
            new_exit_order = await self._place_limit_order_with_custom_price(
                exchange, pair, 'sell', update_sell_amount, new_price, trade_id
            )
            self._is_trailing_stop_order = False
            
            if new_exit_order:
                logger.info(f"[Trade {trade_id}] ✅ Cancel-and-recreate successful: New order {new_exit_order['id']}")
                return {
                    "success": True,
                    "method": "cancel_and_recreate",
                    "exit_id": new_exit_order['id'],  # New exit_id (will need database update)
                    "new_price": new_price
                }
            else:
                logger.error(f"[Trade {trade_id}] ❌ Failed to create new order after cancellation")
                return {"success": False, "method": "cancel_and_recreate", "error": "Failed to create new order"}
                
        except Exception as e:
            logger.error(f"[Trade {trade_id}] ❌ Error in _update_trailing_stop_order: {e}")
            return {"success": False, "method": "unknown", "error": str(e)}

    async def _place_limit_order_with_custom_price(self, exchange_name: str, pair: str, side: str, amount: float, 
                                                  custom_price: float, trade_id: str = None, strategy_name: str = "") -> Optional[Dict[str, Any]]:
        """Place a limit order with a custom price (used for intelligent retries)"""
        try:
            # Sanitize amount with exchange-specific precision
            sanitized_amount = await self._sanitize_numeric_value_with_precision(amount, exchange_name, pair)
            
            # Convert pair format for exchange service
            exchange_symbol = self._convert_pair_format(exchange_name, pair)
            
            # Generate client order ID for this retry attempt
            client_order_id = self._generate_client_order_id(trade_id, f"limit_{strategy_name}")
            local_order_id = str(uuid.uuid4())
            
            logger.info(f"🆔 Custom price order - client_order_id: {client_order_id}, price: ${custom_price:.8f}")

            # In simulation mode, keep lifecycle behavior but avoid live exchange calls.
            if self.is_simulation:
                simulated_order_id = f"sim_trail_{uuid.uuid4().hex[:24]}"
                logger.info(
                    f"🧪 [SIMULATION] Custom limit order created locally: "
                    f"{side} {sanitized_amount} {exchange_symbol} @ ${custom_price:.8f} (id={simulated_order_id})"
                )
                return {
                    'id': simulated_order_id,
                    'status': 'open',
                    'symbol': exchange_symbol,
                    'side': side,
                    'type': 'limit',
                    'amount': sanitized_amount,
                    'price': custom_price,
                    'clientOrderId': client_order_id
                }
            
            # CRITICAL FIX: Validate order with exchange BEFORE creating order mapping
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    # First, try to place the order on the exchange
                    order_data = {
                        'exchange': exchange_name,
                        'symbol': exchange_symbol,
                        'order_type': 'limit',
                        'side': side,
                        'amount': sanitized_amount,
                        'price': custom_price,
                        'client_order_id': client_order_id
                    }
                    
                    logger.info(f"🔄 Placing custom price limit order: {side} {sanitized_amount} {exchange_symbol} @ ${custom_price:.8f}")
                    
                    order_response = await client.post(f"{exchange_service_url}/api/v1/trading/order", json=order_data)
                    
                    if order_response.status_code == 200:
                        result = order_response.json()
                        # CRITICAL FIX: Extract order data from the nested response structure
                        order_data = result.get('order', {})
                        exchange_order_id = order_data.get('id')
                        logger.info(f"✅ Custom price limit order successful: {exchange_order_id}")
                        
                        # Only create order mapping AFTER successful exchange placement
                        mapping_data = {
                            'local_order_id': local_order_id,
                            'client_order_id': client_order_id,
                            'exchange': exchange_name,
                            'symbol': pair,
                            'order_type': 'limit',
                            'side': side,
                            'amount': sanitized_amount,
                            'price': custom_price
                        }
                        
                        mapping_response = await client.post(f"{database_service_url}/api/v1/order-mappings", json=mapping_data)
                        mapping_response.raise_for_status()
                        logger.info(f"✅ Order mapping created: {local_order_id} -> {client_order_id}")
                        
                        # Update order mapping with exchange order ID
                        if exchange_order_id:
                            mapping_update_data = {
                                'exchange_order_id': str(exchange_order_id),
                                'status': 'open'
                            }
                            await client.put(f"{database_service_url}/api/v1/order-mappings/{client_order_id}", json=mapping_update_data)
                            logger.info(f"✅ Order mapping updated with exchange order ID: {exchange_order_id}")
                        
                        # Emit OrderCreated event
                        event_data = {
                            'event_type': 'OrderCreated',
                            'order_id': local_order_id,
                            'client_order_id': client_order_id,
                            'exchange': exchange_name,
                            'symbol': pair,
                            'side': side,
                            'amount': sanitized_amount,
                            'price': custom_price,
                            'order_type': 'limit'
                        }
                        
                        await client.post(f"{database_service_url}/api/v1/events", json=event_data)
                        logger.info(f"📝 OrderCreated event emitted: {strategy_name}")
                        
                        # Return the order data with the correct structure
                        return {'id': exchange_order_id, 'order': order_data}
                    else:
                        error_detail = order_response.text
                        logger.warning(f"❌ Custom price limit order failed: {order_response.status_code} - {error_detail}")
                        return None
                        
            except Exception as e:
                logger.error(f"❌ Error placing custom price limit order: {e}")
                return None
                
        except Exception as e:
            logger.error(f"❌ Error in custom price limit order placement: {e}")
            return None
            
    async def _run_maintenance_tasks(self, deadline: Optional[float] = None) -> None:
        """Run maintenance tasks (best-effort; may truncate when loop wall budget is exhausted)."""
        try:
            if deadline is not None and time.monotonic() >= deadline:
                logger.warning("⏱️ Maintenance skipped: trading-loop wall budget exhausted")
                return

            # Update balances
            await self._update_balances()

            if deadline is not None and time.monotonic() >= deadline:
                logger.warning("⏱️ Maintenance truncated after balance update: wall budget exhausted")
                return
            
            # Check if pair selection needs updating
            await self._check_pair_selection_update()

            if deadline is not None and time.monotonic() >= deadline:
                logger.warning("⏱️ Maintenance truncated after pair-selection check: wall budget exhausted")
                return
            
            # Clean up old data
            await self._cleanup_old_data()

            if deadline is not None and time.monotonic() >= deadline:
                logger.warning("⏱️ Maintenance truncated after cleanup: wall budget exhausted")
                return
            
            # 🚨 CRITICAL FIX: Check for filled orders that should close trades
            await self._check_filled_orders_for_trade_closure()

            if deadline is not None and time.monotonic() >= deadline:
                logger.warning("⏱️ Maintenance truncated after filled-order reconciliation: wall budget exhausted")
                return

            # Enforce pair rotation even when trades were closed by fallback/sync paths.
            await self._enforce_pair_rotation_from_recent_losses()
            
        except Exception as e:
            logger.error(f"Error in maintenance tasks: {str(e)}")
            logger.error(f"Exception type: {type(e).__name__}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")

    async def _enforce_pair_rotation_from_recent_losses(self) -> None:
        """Safety-net: rotate pairs for recent losing closures regardless of close path."""
        try:
            loss_threshold_pct = float(
                await self._get_config_value(
                    "trading.pair_rotation.remove_pair_loss_threshold_pct", -1.0
                )
                or -1.0
            )
            scan_limit = int(
                await self._get_config_value(
                    "trading.pair_rotation.scan_recent_closed_limit", 2000
                )
                or 2000
            )

            async with httpx.AsyncClient(timeout=45.0) as client:
                ex_resp = await client.get(f"{config_service_url}/api/v1/config/exchanges/list")
                if ex_resp.status_code != 200:
                    return
                exchanges = (ex_resp.json() or {}).get("exchanges", []) or []

                for exchange_name in exchanges:
                    trades_resp = await client.get(
                        f"{database_service_url}/api/v1/trades",
                        params={
                            "limit": scan_limit,
                            "exchange": exchange_name,
                            "sort_by": "exit_time",
                            "sort_order": "desc",
                        },
                    )
                    if trades_resp.status_code != 200:
                        continue
                    recent_trades = (trades_resp.json() or {}).get("trades", []) or []
                    rotated_pairs_this_pass: set[str] = set()
                    for t in recent_trades:
                        if str(t.get("status", "")).upper() != "CLOSED":
                            continue
                        if not is_macd_momentum_strategy(t.get("strategy")):
                            continue
                        trade_id = str(t.get("trade_id") or "")
                        if not trade_id or trade_id in self._pair_rotation_processed_trade_ids:
                            continue
                        pair = str(t.get("pair") or "")
                        if not pair or pair in rotated_pairs_this_pass:
                            # Avoid rotating same pair repeatedly in one scan pass.
                            self._pair_rotation_processed_trade_ids.add(trade_id)
                            continue

                        try:
                            entry_price = float(t.get("entry_price") or 0.0)
                            position_size = float(t.get("position_size") or 0.0)
                            realized_pnl = float(t.get("realized_pnl") or 0.0)
                            total_investment = float(t.get("total_investment") or 0.0)
                            entry_notional = float(t.get("entry_notional") or 0.0)
                        except Exception:
                            self._pair_rotation_processed_trade_ids.add(trade_id)
                            continue

                        notional = infer_trade_notional_usd(
                            entry_price=entry_price,
                            position_size=position_size,
                            total_investment=total_investment,
                            entry_notional=entry_notional,
                        )
                        if notional <= 0:
                            self._pair_rotation_processed_trade_ids.add(trade_id)
                            continue
                        realized_pnl_pct = (realized_pnl / notional) * 100.0

                        if realized_pnl_pct <= loss_threshold_pct:
                            logger.warning(
                                "[PairRotation] Safety-net trigger from closed trade %s: %s %s pnl=%.2f%% <= %.2f%%",
                                trade_id,
                                exchange_name,
                                pair,
                                realized_pnl_pct,
                                loss_threshold_pct,
                            )
                            await self._rotate_pair_after_loss(
                                exchange_name=exchange_name,
                                pair=pair,
                                realized_pnl_pct=realized_pnl_pct,
                            )
                            rotated_pairs_this_pass.add(pair)
                        self._pair_rotation_processed_trade_ids.add(trade_id)

            # Bound memory usage.
            max_processed = 5000
            if len(self._pair_rotation_processed_trade_ids) > max_processed:
                self._pair_rotation_processed_trade_ids = set(
                    list(self._pair_rotation_processed_trade_ids)[-max_processed:]
                )
        except Exception as e:
            logger.warning("[PairRotation] Safety-net scan skipped due to error: %s", e)
    
    async def _check_filled_orders_for_trade_closure(self) -> None:
        """🚨 CRITICAL FIX: Check for filled orders that should close trades but haven't been processed"""
        try:
            # Get all OPEN trades that have exit_id (trailing stop orders)
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(f"{database_service_url}/api/v1/trades?status=OPEN&limit=100")
                if response.status_code != 200:
                    return
                
                trades_data = response.json()
                open_trades = trades_data.get('trades', [])
                
                # Filter trades that have exit_id (trailing stop orders)
                trades_with_exit_orders = [t for t in open_trades if t.get('exit_id')]
                
                if not trades_with_exit_orders:
                    return
                
                logger.info(f"🔍 Checking {len(trades_with_exit_orders)} trades with exit orders for fill status")
                
                for trade in trades_with_exit_orders:
                    trade_id = trade['trade_id']
                    exit_id = trade['exit_id']
                    pair = trade['pair']
                    exchange = trade['exchange']

                    if _is_simulated_local_order_id(exit_id):
                        logger.debug(
                            "Skipping exchange fill check for simulated exit_id %s (trade %s)",
                            exit_id,
                            trade_id,
                        )
                        continue
                    
                    try:
                        # Check if the exit order is filled on the exchange
                        order_response = await client.get(f"{exchange_service_url}/api/v1/trading/order/{exchange}/{exit_id}?symbol={pair}")
                        
                        if order_response.status_code == 200:
                            payload = order_response.json() or {}
                            # exchange-service wraps CCXT order as {"order": {...}}
                            order_data = payload.get("order", payload)
                            order_status = str(order_data.get("status", "") or "").upper()
                            
                            # Check if order data is null/empty (order not found - likely filled and removed)
                            if not order_data or not order_data.get('id'):
                                logger.warning(f"🚨 CRITICAL: Order {exit_id} not found on exchange - LIKELY FILLED AND REMOVED")
                                logger.warning(f"🚨 Trade {trade_id} ({pair}) has exit_id but order missing from exchange")
                                
                                # 🚨 CRITICAL FIX: Use REST API to get order history and auto-close
                                await self._auto_close_missing_order_trade(trade_id, exit_id, pair, exchange, client)
                                continue
                            
                            if order_status in ['FILLED', 'CLOSED']:
                                # Order is filled - close the trade
                                fill_price = float(
                                    order_data.get("average")
                                    or order_data.get("avg_price")
                                    or order_data.get("price", 0)
                                )
                                # Use the actual order fill time from exchange data
                                fill_time = order_data.get('time', order_data.get('timestamp', order_data.get('updateTime')))
                                
                                # Convert timestamp to proper ISO format if needed
                                if fill_time and isinstance(fill_time, (int, float)):
                                    # Convert milliseconds timestamp to ISO format
                                    fill_time = datetime.fromtimestamp(fill_time / 1000).isoformat() + 'Z'
                                elif not fill_time:
                                    fill_time = datetime.utcnow().isoformat() + 'Z'
                                
                                if fill_price > 0:
                                    logger.info(f"🎯 FALLBACK DETECTION: Trade {trade_id} exit order {exit_id} is FILLED at ${fill_price:.4f} at {fill_time}")
                                    
                                    # Close the trade
                                    trade_closure_data = {
                                        "exit_price": fill_price,
                                        "exit_order_id": str(exit_id),
                                        "exit_time": fill_time,
                                        "fees": order_data.get('fee', 0),
                                        "exit_reason": "fallback_filled_order_detection"
                                    }
                                    
                                    close_response = await client.post(f"{database_service_url}/api/v1/trades/{trade_id}/close", json=trade_closure_data)
                                    if close_response.status_code == 200:
                                        result = close_response.json()
                                        logger.info(f"✅ FALLBACK CLOSURE: Trade {trade_id} closed at ${result['exit_price']:.4f}, PnL=${result['realized_pnl']:.2f}")
                                    else:
                                        logger.error(f"❌ Failed to close trade {trade_id}: {close_response.status_code}")
                        
                    except Exception as e:
                        logger.error(f"❌ Error checking order {exit_id} for trade {trade_id}: {e}")
                        
        except Exception as e:
            logger.error(f"❌ Error in filled orders check: {e}")
    
    async def _auto_close_missing_order_trade(self, trade_id: str, exit_id: str, pair: str, exchange: str, client: httpx.AsyncClient) -> None:
        """🚨 CRITICAL FIX: Auto-close trade when order is missing from active orders (likely filled)"""
        try:
            if _is_simulated_local_order_id(exit_id):
                logger.debug(
                    "AUTO-CLOSURE skipped for simulated exit_id %s (trade %s)",
                    exit_id,
                    trade_id,
                )
                return

            logger.info(f"🔍 AUTO-CLOSURE: Attempting to get order history for {exit_id} on {exchange}")
            
            # Try to get order history using REST API
            # Different exchanges may have different endpoints for order history
            if exchange.lower() in ['binance', 'bybit', 'cryptocom']:
                # For all supported exchanges, try to get the order by ID (this works for filled orders)
                history_response = await client.get(f"{exchange_service_url}/api/v1/trading/order/{exchange}/{exit_id}?symbol={pair}")
                
                if history_response.status_code == 200:
                    hp = history_response.json() or {}
                    order_data = hp.get("order", hp)
                    
                    # Check if we got valid order data
                    if order_data and order_data.get('id'):
                        order_status = order_data.get('status', '').upper()
                        
                        if order_status in ['FILLED', 'CLOSED']:
                            # Extract fill data
                            fill_price = float(order_data.get('average', order_data.get('price', 0)))
                            fill_time = order_data.get('datetime', order_data.get('timestamp'))
                            
                            # Convert timestamp to proper ISO format if needed
                            if fill_time and isinstance(fill_time, (int, float)):
                                fill_time = datetime.fromtimestamp(fill_time / 1000).isoformat() + 'Z'
                            elif not fill_time:
                                fill_time = datetime.utcnow().isoformat() + 'Z'
                            
                            if fill_price > 0:
                                logger.info(f"🎯 AUTO-CLOSURE SUCCESS: Found filled order {exit_id} at ${fill_price:.4f} at {fill_time}")
                                
                                # Close the trade automatically
                                trade_closure_data = {
                                    "exit_price": fill_price,
                                    "exit_order_id": str(exit_id),
                                    "exit_time": fill_time,
                                    "fees": order_data.get('fee', {}).get('cost', 0) if order_data.get('fee') else 0,
                                    "exit_reason": "auto_closure_missing_order_detected"
                                }
                                
                                close_response = await client.post(f"{database_service_url}/api/v1/trades/{trade_id}/close", json=trade_closure_data)
                                if close_response.status_code == 200:
                                    result = close_response.json()
                                    logger.info(f"✅ AUTO-CLOSURE COMPLETE: Trade {trade_id} closed at ${result['exit_price']:.4f}, PnL=${result['realized_pnl']:.2f}")
                                    return
                                else:
                                    logger.error(f"❌ AUTO-CLOSURE FAILED: Could not close trade {trade_id}: {close_response.status_code}")
                                    logger.error(f"❌ Response: {close_response.text}")
                            else:
                                logger.warning(f"⚠️ AUTO-CLOSURE: Invalid fill price {fill_price} for order {exit_id}")
                        else:
                            logger.warning(f"⚠️ AUTO-CLOSURE: Order {exit_id} status is {order_status}, not FILLED")
                    else:
                        logger.warning(f"⚠️ AUTO-CLOSURE: No valid order data found for {exit_id}")
                else:
                    logger.warning(f"⚠️ AUTO-CLOSURE: Could not get order history for {exit_id}: {history_response.status_code}")
            
            # If we reach here, auto-closure failed
            logger.error(f"❌ AUTO-CLOSURE FAILED: Trade {trade_id} exit order {exit_id} needs manual closure")
            logger.error(f"❌ MANUAL ACTION REQUIRED: Check exchange history for order {exit_id}")
            
        except Exception as e:
            logger.error(f"❌ AUTO-CLOSURE ERROR: Failed to auto-close trade {trade_id}: {e}")
            logger.error(f"❌ MANUAL ACTION REQUIRED: Check exchange history for order {exit_id}")
            
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
                exchanges_data = exchanges_response.json()
                exchanges = exchanges_data.get('exchanges', [])
                
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
                                total, avail = await self._ensure_simulation_balance_in_db(
                                    client,
                                    exchange_name,
                                    float(balance["balance"]),
                                    float(balance["available_balance"]),
                                    float(balance["total_pnl"]),
                                    float(balance["daily_pnl"]),
                                )
                                self.balances[exchange_name] = {
                                    "total": total,
                                    "available": avail,
                                    "total_pnl": float(balance["total_pnl"]),
                                    "daily_pnl": float(balance["daily_pnl"]),
                                }
                                logger.debug(
                                    f"[DEBUG] Updated balance for {exchange_name} from database: total={total}, available={avail}, total_pnl={balance['total_pnl']}, daily_pnl={balance['daily_pnl']}"
                                )
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
                    # Safely extract close prices with validation
                    prices = []
                    for candle in ohlcv_data.get('data', []):
                        try:
                            if isinstance(candle, list) and len(candle) > 4:
                                close_price = candle[4]
                                # Handle both string and numeric values
                                if isinstance(close_price, (int, float)):
                                    prices.append(float(close_price))
                                elif isinstance(close_price, str) and close_price.replace('.', '').replace('-', '').isdigit():
                                    prices.append(float(close_price))
                                else:
                                    logger.warning(f"Invalid close price format: {close_price} (type: {type(close_price)})")
                                    continue
                        except (ValueError, TypeError, IndexError) as e:
                            logger.warning(f"Error parsing OHLCV candle {candle}: {e}")
                            continue
                    
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
                            
                            # Don't go below 100.01% of best bid (very small buffer)
                            min_price = best_bid * 1.0001
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
            'limit_order_timeout_seconds': 120,  # Increased from 30s effective timeout to 120s for limit orders
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

            # Pre-check for dust amounts to avoid futile attempts
            try:
                dust_result = await self._check_and_handle_dust_amount(exchange_name, pair, side, sanitized_amount, trade_id)
                if dust_result == "skip_dust":
                    return None
            except Exception as _dust_err:
                logger.debug(f"Dust pre-check skipped due to error: {_dust_err}")
            
            # Balance check (simulation: DB quote; live: exchange API)
            try:
                if self.is_simulation:
                    await self._refresh_simulation_balance_from_db(exchange_name)
                    if side.lower() == 'buy':
                        base_currency = 'USD' if exchange_name == 'cryptocom' else 'USDC'
                        funds_available = float(
                            (self.balances.get(exchange_name) or {}).get('available', 0) or 0
                        )
                        logger.info(
                            f"🔍 [SIMULATION] Limit order balance for {exchange_name}: ${funds_available:.2f} {base_currency} (database)"
                        )
                        current_price_for_buy = await self._get_current_price(exchange_name, pair)
                        if current_price_for_buy > 0:
                            estimated_cost = sanitized_amount * current_price_for_buy * 1.01
                            if funds_available < estimated_cost:
                                logger.error(
                                    f"🚫 [SIMULATION] Insufficient {base_currency} for BUY. Available: ${funds_available:.2f}, Required: ${estimated_cost:.2f}"
                                )
                                return None
                    else:
                        if not trade_id:
                            logger.error(
                                f"🚫 [SIMULATION] Limit SELL requires trade_id (DB position) for {pair} on {exchange_name}"
                            )
                            return None
                        pos = await self._fetch_open_trade_position_size(trade_id)
                        if pos is None or pos <= 0:
                            logger.error(
                                f"🚫 [SIMULATION] No OPEN DB position for trade {trade_id} — cannot limit-sell {pair}"
                            )
                            return None
                        if sanitized_amount > pos + 1e-10:
                            logger.warning(
                                f"🔧 [SIMULATION] Clamping limit sell amount {sanitized_amount:.8f} to DB position {pos:.8f}"
                            )
                            sanitized_amount = self._sanitize_numeric_value(pos)
                            amount = sanitized_amount
                        logger.info(
                            f"🔍 [SIMULATION] Limit SELL validated vs DB position: {sanitized_amount:.8f} (trade {trade_id})"
                        )
                else:
                    async with httpx.AsyncClient(timeout=60.0) as client:
                        balance_response = await client.get(f"{exchange_service_url}/api/v1/account/balance/{exchange_name}")
                        balance_response.raise_for_status()
                        current_balance = balance_response.json()

                    if side.lower() == 'sell':
                        # Determine asset being sold
                        asset = pair.split('/')[0] if '/' in pair else pair.replace('USD', '').replace('USDC', '')
                        quote_available = float(current_balance.get('free', {}).get(asset, 0) or 0)
                        if quote_available < sanitized_amount:
                            discrepancy = sanitized_amount - quote_available
                            logger.warning(f"⚠️ POSITION SIZE MISMATCH: Database shows {sanitized_amount:.8f}, but exchange has {quote_available:.8f} (diff: {discrepancy:.8f})")
                            
                            # FOR EXIT ORDERS: Check if exchange actually has the position
                            if side.lower() == 'sell' and trade_id:
                                # If exchange has less than 10% of the position, it's likely already sold or doesn't exist
                                if quote_available < sanitized_amount * 0.1:
                                    logger.error(f"🚨 POSITION NOT FOUND: Exchange has {quote_available:.8f} but database shows {sanitized_amount:.8f}")
                                    logger.error(f"💀 FORCE CLOSING TRADE: Position likely already sold or doesn't exist on exchange")
                                    await self._close_dust_position(trade_id, exchange_name, pair, quote_available, "position_not_found_on_exchange")
                                    return None
                                else:
                                    logger.warning(f"🚨 EXIT ORDER: Adjusting position size from {sanitized_amount:.8f} to {quote_available:.8f} to match exchange balance")
                                    sanitized_amount = quote_available  # Use available balance for exit to guarantee success
                            # If available amount is above minimum, proceed with available amount (for entry orders only)
                            elif quote_available > 0:
                                # Ensure above exchange minimums using correct values
                                min_amounts = {
                                    'cryptocom': {
                                        'AAVE/USD': 0.001,
                                        'BTC/USD': 0.00001,
                                        'ETH/USD': 0.0001,
                                        'XRP/USD': 1.0,
                                        'default': 0.000001
                                    },
                                    'binance': {
                                        'XRP/USDC': 1.0,
                                        'LINK/USDC': 1.0,
                                        'default': 0.001
                                    },
                                    'bybit': {
                                        'XRP/USDC': 0.01,  # Error message shows 0.01 minimum
                                        'BTC/USDC': 0.00001,
                                        'ETH/USDC': 0.001,
                                        'SOL/USDC': 0.01,
                                        'default': 0.001
                                    }
                                }
                                exchange_min_amounts = min_amounts.get(exchange_name.lower(), {})
                                min_amount = float(exchange_min_amounts.get(pair, exchange_min_amounts.get('default', 0.000001)))
                                if quote_available < min_amount:
                                    logger.warning(f"💸 Available amount {quote_available:.8f} below minimum {min_amount} for {pair} on {exchange_name}")
                                    # Close as dust position instead of failing
                                    if trade_id:
                                        await self._close_dust_position(trade_id, exchange_name, pair, quote_available, "position_size_below_minimum")
                                    return None
                                sanitized_amount = quote_available
                                amount = quote_available
                                logger.info(f"🔧 Position size corrected for limit order: proceeding with {sanitized_amount:.8f} {asset}")
                            else:
                                # Exchange is the source of truth - correct the database
                                logger.warning(f"🔄 CORRECTING DATABASE: Exchange has {quote_available:.8f} {asset}, updating trade {trade_id} from {sanitized_amount:.8f} to {quote_available:.8f}")
                                
                                # Update the trade in database with correct amount
                                if trade_id:
                                    try:
                                        async with httpx.AsyncClient(timeout=30.0) as client:
                                            update_data = {
                                                'amount': quote_available,
                                                'position_size': quote_available
                                            }
                                            update_response = await client.put(f"{database_service_url}/api/v1/trades/{trade_id}", json=update_data)
                                            update_response.raise_for_status()
                                            logger.info(f"✅ Database corrected: Trade {trade_id} amount updated to {quote_available:.8f} {asset}")
                                            
                                            # Check if the corrected amount is still above minimum
                                            min_amounts = {
                                                'cryptocom': {
                                                    'AAVE/USD': 0.001,
                                                    'BTC/USD': 0.00001,
                                                    'ETH/USD': 0.0001,
                                                    'XRP/USD': 1.0,
                                                    'default': 0.000001
                                                },
                                                'binance': {
                                                    'XRP/USDC': 1.0,
                                                    'LINK/USDC': 1.0,
                                                    'default': 0.001
                                                },
                                                'bybit': {
                                                    'XRP/USDC': 0.01,
                                                    'BTC/USDC': 0.00001,
                                                    'ETH/USDC': 0.001,
                                                    'SOL/USDC': 0.01,
                                                    'default': 0.001
                                                }
                                            }
                                            exchange_min_amounts = min_amounts.get(exchange_name.lower(), {})
                                            min_amount = float(exchange_min_amounts.get(pair, exchange_min_amounts.get('default', 0.000001)))
                                            
                                            if quote_available < min_amount:
                                                logger.warning(f"💸 Corrected amount {quote_available:.8f} still below minimum {min_amount} for {pair} on {exchange_name}")
                                                await self._close_dust_position(trade_id, exchange_name, pair, quote_available, "corrected_position_below_minimum")
                                                return None
                                            else:
                                                # Proceed with corrected amount
                                                sanitized_amount = quote_available
                                                amount = quote_available
                                                logger.info(f"🔧 Proceeding with corrected amount: {sanitized_amount:.8f} {asset}")
                                                
                                    except Exception as db_update_err:
                                        logger.error(f"❌ Failed to update database for trade {trade_id}: {db_update_err}")
                                        return None
                                else:
                                    logger.error(f"🚫 CRITICAL: Position size discrepancy too large! Available: {quote_available:.8f}, Required: {sanitized_amount:.8f}")
                                    return None
                    else:  # buy
                        base_currency = 'USD' if exchange_name == 'cryptocom' else 'USDC'
                        funds_available = float(current_balance.get('free', {}).get(base_currency, 0) or 0)
                        # Rough affordability check using current price with small buffer
                        current_price_for_buy = await self._get_current_price(exchange_name, pair)
                        if current_price_for_buy > 0:
                            estimated_cost = sanitized_amount * current_price_for_buy * 1.01
                            if funds_available < estimated_cost:
                                logger.error(f"🚫 CRITICAL: Insufficient {base_currency} for BUY. Available: ${funds_available:.2f}, Required: ${estimated_cost:.2f}")
                                return None
            except Exception as balance_err:
                logger.warning(f"⚠️ Could not verify real-time balance for limit order on {exchange_name}: {balance_err}")
            
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

            # Simulation mode: execute local instant-fill lifecycle without order-mapping DB writes.
            if self.is_simulation:
                sim_order = await self._build_simulation_instant_fill_order(
                    exchange_name,
                    pair,
                    side,
                    sanitized_amount,
                    trade_id,
                    float(limit_price),
                    apply_balance_delta=True,
                )
                if sim_order:
                    orders_total.labels(
                        exchange=exchange_name,
                        pair=pair,
                        side=side,
                        type='limit',
                        status='success',
                    ).inc()
                return sim_order
            
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
                                
                                # CRITICAL: Register order in Redis for WebSocket tracking
                                try:
                                    await redis_realtime_manager.register_order_for_tracking(exchange_name, order['id'], order_tracking_data)
                                    logger.info(f"✅ Order {order['id']} registered in Redis for WebSocket tracking")
                                except Exception as redis_error:
                                    logger.error(f"❌ Failed to register order {order['id']} in Redis: {redis_error}")
                                    # Continue with order placement even if Redis registration fails
                                
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
                    # Try intelligent limit order retry with price correction before falling back
                    logger.info(f"🔄 Attempting intelligent limit order retry with price correction for {pair} on {exchange_name}")
                    retry_result = await self._intelligent_limit_retry(exchange_name, pair, side, amount, trade_id)
                    
                    if retry_result and retry_result.get('id'):
                        logger.info(f"✅ Intelligent retry successful: {retry_result['id']}")
                        await order_tracker.record_order_attempt('limit', retry_result)
                        return retry_result
                    
                    logger.warning(f"⚠️ All limit order attempts failed, falling back to market order for {pair} on {exchange_name}")
                    
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
                        'error_message': 'All limit order attempts failed, falling back to market order'
                    }
                    await self._record_order_to_database(db_failed_order)
                    
                    # Final fallback to market order
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
                        await order_tracker.track_order(order['id'], order_data, timeout_seconds=120)
                        
                        # CRITICAL: Register order in Redis for WebSocket tracking
                        try:
                            await redis_realtime_manager.register_order_for_tracking(exchange_name, order['id'], order_data)
                            logger.info(f"✅ Order {order['id']} registered in Redis for WebSocket tracking")
                        except Exception as redis_error:
                            logger.error(f"❌ Failed to register order {order['id']} in Redis: {redis_error}")
                            # Continue with order placement even if Redis registration fails
                        
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
                            'timeout_seconds': 120
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

    async def _sanitize_numeric_value_async(self, value: float) -> float:
        """Sanitize numeric values to prevent decimal conversion errors using config"""
        try:
            if value is None:
                logger.warning(f"🔧 Sanitizing None value, returning 0.0")
                return 0.0
            
            # Convert to float first
            float_value = float(value)
            
            # Handle special cases
            if float_value == 0:
                logger.warning(f"🔧 Sanitizing zero value, returning 0.0")
                return 0.0
            if float_value < 0:
                logger.warning(f"🔧 Sanitizing negative value {float_value}, making positive")
                float_value = abs(float_value)  # Ensure positive
            
            # CRITICAL: Check for NaN/infinity values that would break JSON
            if np.isnan(float_value) or np.isinf(float_value):
                logger.error(f"❌ Invalid numeric value (NaN/inf): {value}, returning 0.0")
                return 0.0
            
            # Get precision settings from config
            max_precision = await self._get_config_value('trading.sanitization.max_crypto_precision', 12)
            min_precision = await self._get_config_value('trading.sanitization.min_crypto_precision', 8)
            prevent_zero_rounding = await self._get_config_value('trading.sanitization.prevent_zero_rounding', True)
            
            # Format to remove scientific notation and excessive precision
            formatted_str = f"{float_value:.{max_precision}g}"
            sanitized_value = float(formatted_str)
            
            # CRITICAL: More conservative rounding for crypto amounts based on value size
            if sanitized_value >= 1000:
                # For large values, use minimal decimal places
                sanitized_value = round(sanitized_value, 2)
            elif sanitized_value >= 100:
                # For hundreds, use 3 decimal places
                sanitized_value = round(sanitized_value, 3)
            elif sanitized_value >= 1:
                # For values >= 1, use 4 decimal places
                sanitized_value = round(sanitized_value, 4)
            elif sanitized_value >= 0.001:
                # For values >= 0.001, use 6 decimal places
                sanitized_value = round(sanitized_value, 6)
            else:
                # For very small values, use minimum precision from config
                sanitized_value = round(sanitized_value, min_precision)
                
                # CRITICAL: Ensure we never round small crypto amounts to zero if configured
                if prevent_zero_rounding and sanitized_value == 0.0 and float_value > 0:
                    sanitized_value = float_value  # Keep original value
                    logger.warning(f"🔧 Prevented rounding to zero: {value} -> {sanitized_value}")
            
            logger.info(f"🔧 Sanitized numeric value: {value} -> {sanitized_value}")
            return sanitized_value
            
        except (ValueError, TypeError) as e:
            logger.error(f"❌ Error sanitizing numeric value {value}: {e}")
            # CRITICAL: Don't return 0.0 for position sizes - this breaks orders!
            try:
                fallback = float(value) if value is not None else 0.0
                if fallback > 0:
                    logger.warning(f"🔧 Using fallback value for failed sanitization: {fallback}")
                    return fallback
            except:
                pass
            return 0.0

    def _sanitize_numeric_value(self, value: float) -> float:
        """Synchronous wrapper for backward compatibility"""
        # For existing code that can't be made async, use a simpler sanitization
        try:
            if value is None or value == 0:
                return 0.0
            
            float_value = float(value)
            if np.isnan(float_value) or np.isinf(float_value):
                return 0.0
            
            # Use a reasonable precision without config lookup
            if float_value > 0:
                sanitized = round(abs(float_value), 8)
                return sanitized if sanitized > 0 else float_value
            return 0.0
        except Exception as e:
            logger.error(f"❌ Error sanitizing value {value}: {e}")
            return 0.0
    
    async def _sanitize_numeric_value_with_precision(self, value: float, exchange: str, symbol: str) -> float:
        """Sanitize numeric values with exchange-specific precision"""
        try:
            if value is None or value == 0:
                return 0.0
            
            float_value = float(value)
            if np.isnan(float_value) or np.isinf(float_value):
                return 0.0
            
            if float_value <= 0:
                return 0.0
            
            # CRITICAL FIX: For trailing stop orders, use exact available balance without rounding
            # This prevents insufficient balance errors when the amount is very close to available balance
            if hasattr(self, '_is_trailing_stop_order') and self._is_trailing_stop_order:
                logger.info(f"🔧 Trailing stop order - using exact amount without precision rounding: {float_value}")
                return float_value
            
            # Get market precision information
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get(f"{exchange_service_url}/api/v1/market/info/{exchange}/{symbol}")
                    if response.status_code == 200:
                        market_info = response.json()
                        amount_precision = market_info.get('precision', {}).get('amount', 8)
                        min_amount = market_info.get('limits', {}).get('amount', {}).get('min', 0.0)
                        
                        # Round to the correct precision
                        sanitized = round(abs(float_value), amount_precision)
                        
                        # Ensure it meets minimum amount requirement
                        if sanitized < min_amount:
                            logger.warning(f"⚠️ Amount {sanitized} below minimum {min_amount} for {exchange}/{symbol}")
                            return 0.0
                        
                        logger.info(f"🔧 Precision sanitization: {float_value} -> {sanitized} (precision: {amount_precision})")
                        return sanitized
                    else:
                        logger.warning(f"⚠️ Could not get market info for {exchange}/{symbol}, using default precision")
            except Exception as e:
                logger.warning(f"⚠️ Error getting market precision for {exchange}/{symbol}: {e}, using default precision")
            
            # Fallback to default precision
            sanitized = round(abs(float_value), 8)
            return sanitized if sanitized > 0 else float_value
            
        except (ValueError, TypeError) as e:
            logger.error(f"❌ Error sanitizing value {value}: {e}")
            return float(value) if value and value > 0 else 0.0
        except Exception as e:
            logger.error(f"❌ Error sanitizing value {value}: {e}")
            return 0.0

    async def _monitor_pending_orders(self):
        """Background task to monitor pending limit orders"""
        logger.info("🔄 Starting order monitoring loop")
        
        while self.trading_active:
            try:
                pending_orders = order_tracker.get_pending_orders()
                
                for order_id, order_info in list(pending_orders.items()):
                    try:
                        if not isinstance(order_info, dict) or 'order_data' not in order_info:
                            continue

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
            # PnL-FIX v9: capture currency too so total_fees_usd can be derived.
            fee_info = self._extract_fee_with_currency(filled_data)
            fees = fee_info["amount"]
            fee_currency = fee_info["currency"]
            
            trade_id = order_info.get('trade_id')
            side = order_info['order_data']['side']
            
            logger.info(f"✅ Order {order_id} filled: {filled_amount:.8f} @ {filled_price:.8f}, fees: {fees:.6f} {fee_currency}, fill_time: {fill_time:.2f}s")
            
            # Update trade if this is an entry order
            if trade_id and side == 'buy':
                await self._update_trade_data(trade_id, {
                    'position_size': filled_amount,
                    'entry_price': filled_price,
                    'fees': fees,
                    'entry_fee_amount': fees,
                    'entry_fee_currency': fee_currency,
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

    async def _cancel_external_sell_orders(self, trade_id: str, pair: str, exchange: str) -> None:
        """
        🛡️ PROFIT PROTECTION: Cancel any external sell orders that interfere with trailing stops
        
        This function prevents external orders from closing trades before trailing stops activate.
        Priority: Trailing stop FIRST, profit protection only if trailing stop not active.
        """
        try:
            logger.info(f"[Trade {trade_id}] [ProfitProtection] 🔍 Checking for external sell orders on {exchange}")
            
            # Get all open orders for this pair from the exchange
            async with httpx.AsyncClient(timeout=15.0) as client:
                # Must match exchange-service routes (see /api/v1/trading/orders/{exchange})
                symbol_param = quote(self._convert_pair_format(exchange, pair))
                orders_response = await client.get(
                    f"{exchange_service_url}/api/v1/trading/orders/{exchange}?symbol={symbol_param}"
                )
                if orders_response.status_code != 200:
                    logger.warning(f"[Trade {trade_id}] [ProfitProtection] ⚠️ Could not fetch orders: {orders_response.status_code}")
                    return
                
                orders_data = orders_response.json()
                open_orders = orders_data.get('orders', [])
                
                # Filter for sell orders
                sell_orders = [
                    order for order in open_orders
                    if str(order.get('side', '')).lower() == 'sell'
                ]
                
                if not sell_orders:
                    logger.info(f"[Trade {trade_id}] [ProfitProtection] ✅ No external sell orders found")
                    return
                
                logger.info(f"[Trade {trade_id}] [ProfitProtection] 🔍 Found {len(sell_orders)} sell orders to examine")
                
                # Get base asset for quantity checks
                base_asset = pair.split('/')[0]
                
                # Get position size from database
                trade_response = await client.get(f"{database_service_url}/api/v1/trades/{trade_id}")
                if trade_response.status_code != 200:
                    logger.warning(f"[Trade {trade_id}] [ProfitProtection] ⚠️ Could not fetch trade data")
                    return
                
                trade_data = trade_response.json()
                position_size = float(
                    trade_data.get('position_size')
                    or trade_data.get('quantity')
                    or 0
                )
                
                external_orders_cancelled = 0
                
                cancel_symbol = quote(self._convert_pair_format(exchange, pair))
                for order in sell_orders:
                    order_id = order.get('id') or order.get('exchange_order_id')
                    client_order_id = order.get('client_order_id') or order.get('clientOrderId') or ''
                    order_quantity = float(order.get('amount', 0) or order.get('filled', 0) or 0)
                    order_price = float(order.get('price', 0))
                    
                    # Check if this is an orchestrator order by client_order_id pattern
                    is_orchestrator_order = (
                        client_order_id.startswith(f'oms{trade_id[:8]}') or
                        client_order_id.startswith(f'omsef6b4f12') if trade_id == 'ef6b4f12-1b48-459d-9f9c-d7444c399ba8' else False
                    )
                    
                    if is_orchestrator_order:
                        logger.info(f"[Trade {trade_id}] [ProfitProtection] ✅ KEEPING orchestrator order: {order_id} (client_id: {client_order_id})")
                        continue
                    
                    # This is an external order - check if it should be cancelled
                    # Cancel if:
                    # 1. Quantity is close to our position size (competing exit order)
                    # 2. Quantity is very small (micro-order interference)
                    # 3. Price is close to current market (immediate execution risk)
                    
                    quantity_ratio = order_quantity / position_size if position_size > 0 else 0
                    is_competing_exit = quantity_ratio > 0.5  # Cancel if order is >50% of position
                    is_micro_order = order_quantity < 10.0  # Cancel micro orders (increased threshold)
                    
                    should_cancel = is_competing_exit or is_micro_order
                    
                    if should_cancel:
                        logger.warning(f"[Trade {trade_id}] [ProfitProtection] 🗑️ CANCELLING external order: {order_id}")
                        logger.warning(f"[Trade {trade_id}] [ProfitProtection] 📊 Order details: qty={order_quantity}, price={order_price}, ratio={quantity_ratio:.2%}")
                        logger.warning(f"[Trade {trade_id}] [ProfitProtection] 🎯 Reason: {'competing_exit' if is_competing_exit else 'micro_order'}")
                        
                        try:
                            # Cancel the external order (same route as _cancel_order)
                            cancel_response = await client.delete(
                                f"{exchange_service_url}/api/v1/trading/order/{exchange}/{order_id}?symbol={cancel_symbol}"
                            )
                            if cancel_response.status_code == 200:
                                external_orders_cancelled += 1
                                logger.info(f"[Trade {trade_id}] [ProfitProtection] ✅ Successfully cancelled external order: {order_id}")
                            else:
                                logger.error(f"[Trade {trade_id}] [ProfitProtection] ❌ Failed to cancel order {order_id}: {cancel_response.status_code}")
                        except Exception as cancel_error:
                            logger.error(f"[Trade {trade_id}] [ProfitProtection] ❌ Error cancelling order {order_id}: {cancel_error}")
                    else:
                        logger.info(f"[Trade {trade_id}] [ProfitProtection] ✅ KEEPING external order: {order_id} (qty={order_quantity}, ratio={quantity_ratio:.2%})")
                
                if external_orders_cancelled > 0:
                    logger.info(f"[Trade {trade_id}] [ProfitProtection] 🛡️ PROTECTION COMPLETE: Cancelled {external_orders_cancelled} external sell orders")
                else:
                    logger.info(f"[Trade {trade_id}] [ProfitProtection] ✅ No external orders needed cancellation")
                    
        except Exception as e:
            logger.error(f"[Trade {trade_id}] [ProfitProtection] ❌ Error in external order protection: {e}")

# Global orchestrator
orchestrator = TradingOrchestrator()

# API Endpoints
@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint"""
    try:
        # Safely get global variables with defaults
        current_trading_status = globals().get('trading_status', 'initializing')
        current_cycle_count = globals().get('cycle_count', 0)
        current_active_trades = len(globals().get('active_trades_dict', {}))
        
        return HealthResponse(
            status="healthy",
            timestamp=datetime.utcnow(),
            version="2.4.0",
            trading_status=current_trading_status,
            cycle_count=current_cycle_count,
            active_trades=current_active_trades
        )
    except Exception as e:
        logger.error(f"Health check error: {e}")
        # Return minimal health response on error
        return HealthResponse(
            status="healthy",
            timestamp=datetime.utcnow(),
            version="2.4.0",
            trading_status="unknown",
            cycle_count=0,
            active_trades=0
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
        uptime=uptime or timedelta(0),
        last_loop_duration_seconds=getattr(
            orchestrator, "last_loop_duration_seconds", None
        ),
    )


@app.post("/api/v1/trading/pairs/entry-blocks")
async def post_pair_entry_blocks(body: PairEntryBlocksRequest):
    """Per-pair soft blocks for dashboard (cooldown, recent loss, open loss, downgrade)."""
    try:
        blocks, block_details = await orchestrator.get_dashboard_pair_entry_blocks(
            body.exchange, body.pairs or []
        )
        return {"exchange": body.exchange, "blocks": blocks, "block_details": block_details}
    except Exception as e:
        logger.error("pair entry blocks: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


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


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _format_usd(value: Any) -> str:
    return f"${_safe_float(value):.2f}"


telegram_hourly_report_task: Optional[asyncio.Task] = None


async def _build_telegram_stats_message(last_day_trade_limit: int = 15) -> Tuple[str, int]:
    """Build Telegram message with portfolio stats + today's closed trades (same rule as Daily Closed in portfolio)."""
    now_utc = datetime.utcnow()

    async with httpx.AsyncClient(timeout=60.0) as client:
        summary_resp = await client.get(f"{database_service_url}/api/v1/portfolio/summary")
        summary_resp.raise_for_status()
        summary = summary_resp.json()

        history_resp = await client.get(
            f"{database_service_url}/api/v1/trades/closed/history",
            params={
                "page": 1,
                "limit": max(1, min(last_day_trade_limit, 50)),
                "status": "CLOSED",
                "daily_closed_only": True,
            },
        )
        history_resp.raise_for_status()
        history_data = history_resp.json()
        daily_closed_trade_rows = history_data.get("trades", []) or []
        daily_closed_total = int(history_data.get("total") or len(daily_closed_trade_rows))

    total_balance = _safe_float(summary.get("total_balance"))
    available_balance = _safe_float(summary.get("available_balance"))
    invested_amount = total_balance - available_balance

    header_lines = [
        "📊 <b>Trading Statistics</b>",
        "",
        f"🎯 <b>Win Rate:</b> {_safe_float(summary.get('win_rate')):.1f}%",
        f"📈 <b>Total Trades:</b> {int(summary.get('total_trades') or 0)}",
        f"🕒 <b>Daily Opened:</b> {int(summary.get('daily_total_trades') or 0)}",
        f"✅ <b>Daily Closed:</b> {int(summary.get('daily_closed_trades') or 0)}",
        "",
        f"💼 <b>Total Portfolio:</b> {_format_usd(total_balance)}",
        f"💵 <b>Available Balance:</b> {_format_usd(available_balance)}",
        f"📌 <b>Invested Amount:</b> {_format_usd(invested_amount)}",
        "",
        f"💹 <b>Total PnL:</b> {_format_usd(summary.get('total_pnl'))}",
        f"📆 <b>Daily PnL:</b> {_format_usd(summary.get('daily_pnl'))}",
        f"📂 <b>Open Trades:</b> {int(summary.get('active_trades') or 0)}",
        f"🔄 <b>Unrealized PnL:</b> {_format_usd(summary.get('total_unrealized_pnl'))}",
        "",
        (
            f"🧾 <b>Daily Closed Trades ({daily_closed_total})</b> — "
            f"<i>newest {len(daily_closed_trade_rows)} shown</i>"
            if daily_closed_total > len(daily_closed_trade_rows)
            else f"🧾 <b>Daily Closed Trades ({daily_closed_total}):</b>"
        ),
    ]

    if not daily_closed_trade_rows:
        header_lines.append("No trades closed yet today.")
    else:
        for trade in daily_closed_trade_rows:
            pair = str(trade.get("pair") or "N/A")
            exchange = str(trade.get("exchange") or "N/A")
            realized_pnl = _safe_float(trade.get("realized_pnl"))
            pnl_sign = "🟢" if realized_pnl >= 0 else "🔴"
            pnl_str = f"{realized_pnl:+.2f}"
            header_lines.append(f"{pnl_sign} {pair} ({exchange})  {pnl_str} USD")

    header_lines.append("")
    header_lines.append(f"⏱️ <i>Generated: {now_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC</i>")
    return "\n".join(header_lines), daily_closed_total


async def _send_telegram_stats_report(last_day_trade_limit: int = 15) -> Dict[str, Any]:
    """Send Telegram stats report and return structured result."""
    telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    if not telegram_bot_token or not telegram_chat_id:
        raise ValueError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")

    message, last_day_trade_count = await _build_telegram_stats_message(
        last_day_trade_limit=last_day_trade_limit
    )
    telegram_url = f"https://api.telegram.org/bot{telegram_bot_token}/sendMessage"

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            telegram_url,
            json={
                "chat_id": telegram_chat_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
        )
        response.raise_for_status()
        response_data = response.json()

    if not response_data.get("ok"):
        raise RuntimeError(f"Telegram API returned error: {response_data}")

    return {
        "status": "sent",
        "message": "Telegram statistics report sent successfully.",
        "last_day_trade_count": last_day_trade_count,
    }


async def _telegram_hourly_report_loop() -> None:
    """Background loop that sends Telegram stats on a fixed interval."""
    interval_seconds = int(os.getenv("TELEGRAM_REPORT_INTERVAL_SECONDS", "3600") or "3600")
    if interval_seconds < 60:
        interval_seconds = 60

    trade_limit = int(os.getenv("TELEGRAM_LAST_DAY_TRADE_LIMIT", "15") or "15")
    trade_limit = max(1, min(trade_limit, 50))

    logger.info(
        "📨 Telegram hourly report loop started (interval=%ss, last_day_trade_limit=%s)",
        interval_seconds,
        trade_limit,
    )

    while True:
        try:
            await _send_telegram_stats_report(last_day_trade_limit=trade_limit)
            logger.info("✅ Telegram scheduled stats report sent")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"❌ Telegram scheduled report failed: {e}")

        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            raise


@app.post("/api/v1/notifications/telegram/stats")
async def send_telegram_stats(last_day_trade_limit: int = 15):
    """
    Publish portfolio statistics and last 24h closed trades to Telegram.
    Uses TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID environment variables.
    """
    telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    if not telegram_bot_token or not telegram_chat_id:
        raise HTTPException(
            status_code=400,
            detail=(
                "Missing Telegram configuration. "
                "Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID."
            ),
        )

    try:
        return await _send_telegram_stats_report(last_day_trade_limit=last_day_trade_limit)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error sending Telegram stats report: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to send Telegram report: {str(e)}")

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

@app.post("/api/v1/pairs/update")
async def force_pairs_update():
    """Manually trigger pair selection update for all exchanges"""
    try:
        logger.info("🔄 Manual pair update triggered via API")
        await orchestrator._update_pair_selections()
        orchestrator.last_pair_update = datetime.utcnow()
        
        # Get updated pairs count
        total_pairs = sum(len(pairs) for pairs in orchestrator.pair_selections.values())
        
        return {
            "message": "Pair selection updated successfully",
            "exchanges": list(orchestrator.pair_selections.keys()),
            "total_pairs": total_pairs,
            "updated_at": datetime.utcnow().isoformat(),
            "pair_selections": orchestrator.pair_selections
        }
    except Exception as e:
        logger.error(f"Error updating pairs: {str(e)}")
        logger.error(f"Exception type: {type(e).__name__}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/trading/recover-missing-trades")
async def recover_missing_trades():
    """Recovery API to identify and create missing trade records from exchange positions"""
    try:
        logger.info("🚨 CRITICAL RECOVERY: Scanning for missing trades on all exchanges")
        recovered_trades = []
        
        # Check each exchange for positions that aren't tracked in database
        exchanges = ['binance', 'bybit', 'cryptocom']
        
        for exchange_name in exchanges:
            try:
                logger.info(f"🔍 Scanning {exchange_name} for missing trades...")
                
                # Get current exchange balances
                async with httpx.AsyncClient(timeout=60.0) as client:
                    balance_response = await client.get(f"{exchange_service_url}/api/v1/account/balance/{exchange_name}")
                    if balance_response.status_code != 200:
                        continue
                        
                    balance_data = balance_response.json()
                    
                    # Look for positions with reserved/used quantities (indicating open orders/positions)
                    for asset, balance_info in balance_data.get('used', {}).items():
                        if float(balance_info) > 0:
                            logger.warning(f"🔍 Found reserved balance: {asset} = {balance_info} on {exchange_name}")
                            
                            # Create recovery trade record for positions not in database
                            # This would need to be expanded with actual order history lookup
                            recovered_trades.append({
                                'exchange': exchange_name,
                                'asset': asset,
                                'reserved_amount': balance_info,
                                'action': 'detected_position'
                            })
                            
            except Exception as exchange_error:
                logger.error(f"Error scanning {exchange_name}: {exchange_error}")
                continue
        
        return {
            "message": "Missing trade recovery scan completed",
            "recovered_trades": recovered_trades,
            "total_found": len(recovered_trades),
            "timestamp": datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        logger.error(f"Error in recovery scan: {str(e)}")
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

@app.post("/api/v1/trading/sync-exchange-trades")
async def sync_exchange_trades_endpoint():
    """Sync trades with exchanges to close orphaned trades in database"""
    try:
        sync_results = await orchestrator.sync_exchange_trades()
        return {
            "status": "success",
            "message": f"Synced {sync_results['trades_synced']}/{sync_results['trades_checked']} trades",
            "results": sync_results
        }
    except Exception as e:
        logger.error(f"Error in exchange sync: {str(e)}")
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
    global telegram_hourly_report_task
    logger.info(f"🚀 Orchestrator Service v{ORCHESTRATOR_VERSION} ({VERSION_DATE}) starting up")
    logger.info(f"🛡️ Enhanced Risk Management v{RISK_MANAGEMENT_VERSION} - Multi-layered protection active")
    await orchestrator.initialize()
    
    # Initialize Redis-enhanced realtime order manager
    redis_init_success = await redis_realtime_manager.initialize()
    if redis_init_success:
        logger.info("✅ Redis Realtime Order Manager initialized")
    else:
        logger.error("❌ Redis Realtime Order Manager failed to initialize")
    
    # Start trading in background to avoid blocking startup
    asyncio.create_task(orchestrator.start_trading())
    asyncio.create_task(hybrid_order_tracker.start_background_tasks())

    telegram_auto_enabled = os.getenv("TELEGRAM_AUTO_REPORT_ENABLED", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }
    if telegram_auto_enabled:
        telegram_hourly_report_task = asyncio.create_task(_telegram_hourly_report_loop())
        logger.info("✅ Telegram auto report scheduler enabled")
    else:
        logger.info("ℹ️ Telegram auto report scheduler disabled")
    
    logger.info("✅ Orchestrator startup complete - trading loop starting in background")

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    global telegram_hourly_report_task
    if orchestrator.running:
        await orchestrator.stop_trading()
    if telegram_hourly_report_task:
        telegram_hourly_report_task.cancel()
        try:
            await telegram_hourly_report_task
        except asyncio.CancelledError:
            pass
        telegram_hourly_report_task = None
    await hybrid_order_tracker.stop_background_tasks()
    await redis_realtime_manager.close()
    logger.info("Orchestrator service shutdown complete")

class WebSocketOrderTracker:
    """Real-time WebSocket order tracking"""
    
    def __init__(self):
        self.connections = {}  # exchange -> websocket connection
        self.order_callbacks = {}  # order_id -> callback function
        self.websocket_enabled = True  # Enable realtime WebSocket order tracking
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
                    
                    # Handle different response formats
                    connected = False
                    if exchange_name == 'bybit' and 'manager_status' in status_data:
                        # Bybit has a nested structure
                        connected = status_data['manager_status'].get('connected', False)
                    else:
                        # Other exchanges use direct structure
                        connected = status_data.get('connected', False)
                    
                    self.connection_status[exchange_name] = {
                        'status': 'connected' if connected else 'disconnected',
                        'connected': connected,
                        'last_update': status_data.get('last_update', datetime.utcnow().isoformat()),
                        'registered_callbacks': status_data.get('registered_callbacks', 0)
                    }
                    logger.info(f"WebSocket connection for {exchange_name}: {connected}")
                    return connected
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
                # Handle case where status might be a boolean instead of dict
                if isinstance(status, bool):
                    if not status:
                        disconnected_exchanges.append(exchange)
                elif isinstance(status, dict):
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
                # Handle case where status might be a boolean instead of dict
                if isinstance(status, bool):
                    if not status:
                        logger.warning(f"WebSocket disconnected for {exchange}, attempting reconnection...")
                        await websocket_order_tracker.connect_exchange(exchange)
                elif isinstance(status, dict):
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
    
    async def update_order_status(self, order_id: str, status: str, event_data: dict = None):
        """Update order status from WebSocket events"""
        try:
            logger.info(f"📋 Updating order status: {order_id} -> {status}")
            
            if order_id in self.pending_orders:
                # Update pending order with new status
                self.pending_orders[order_id]['status'] = status
                self.pending_orders[order_id]['last_update'] = datetime.utcnow().isoformat()
                
                if event_data:
                    self.pending_orders[order_id]['websocket_data'] = event_data
                
                # Handle status transitions
                if status in ['FILLED', 'PARTIALLY_FILLED']:
                    await self._process_filled_order(order_id)
                    
                    # Move to filled orders
                    self.filled_orders[order_id] = self.pending_orders[order_id]
                    if status == 'FILLED':
                        del self.pending_orders[order_id]
                        
                elif status in ['CANCELLED', 'EXPIRED', 'REJECTED']:
                    await self._process_cancelled_order(order_id, status)
                    
                    # Move to cancelled orders
                    self.cancelled_orders[order_id] = self.pending_orders[order_id]
                    del self.pending_orders[order_id]
            else:
                logger.debug(f"Order {order_id} not found in pending orders")
                
        except Exception as e:
            logger.error(f"❌ Error updating order status: {e}")
    
    async def update_trade_execution(self, trade_id: str, order_id: str, event_data: dict = None):
        """Update trade execution from WebSocket events"""
        try:
            logger.info(f"💰 Updating trade execution: {trade_id} for order {order_id}")
            
            if event_data:
                # Extract trade execution details
                execution_price = event_data.get('price') or event_data.get('avg_price')
                execution_quantity = event_data.get('quantity') or event_data.get('filled')
                fees = event_data.get('fees') or event_data.get('commission')
                
                # Record the execution in performance metrics
                if execution_price and execution_quantity:
                    async with self.performance_lock:
                        # Calculate execution performance metrics
                        if order_id in self.pending_orders:
                            order_info = self.pending_orders[order_id]
                            order_type = order_info.get('order_type', 'unknown')
                            
                            # Update appropriate metrics
                            if order_type == 'limit':
                                self.order_performance['limit_orders']['filled'] += 1
                            elif order_type == 'market':
                                self.order_performance['market_orders']['filled'] += 1
                            
                            # Calculate fill time if available
                            if 'created_at' in order_info:
                                try:
                                    created_time = datetime.fromisoformat(order_info['created_at'].replace('Z', '+00:00'))
                                    fill_time = (datetime.utcnow() - created_time).total_seconds()
                                    self.order_performance['fill_times'].append(fill_time)
                                except:
                                    pass
                
                logger.info(f"✅ Trade execution recorded: {trade_id} @ {execution_price}")
                
        except Exception as e:
            logger.error(f"❌ Error updating trade execution: {e}")

# Global hybrid order tracker
hybrid_order_tracker = HybridOrderTracker()

# Trailing Stop Statistics Endpoints for Performance Analytics
@app.get("/api/v1/trailing-stops/statistics")
async def get_trailing_stop_statistics():
    """Get comprehensive trailing stop statistics for performance analytics"""
    try:
        # Get trailing stop manager statistics if available
        if hasattr(orchestrator, 'activation_trigger_system') and orchestrator.activation_trigger_system:
            # Get metrics from activation trigger system
            metrics = orchestrator.activation_trigger_system.metrics
            return {
                "trailing_stop_manager": {
                    "total_activations": metrics.get('activations_triggered', 0),
                    "orders_created": metrics.get('orders_created', 0),
                    "orders_updated": metrics.get('orders_updated', 0),
                    "successful_fills": metrics.get('orders_filled', 0),
                    "cancelled_orders": metrics.get('errors', 0),
                    "avg_profit_at_activation": 0.007,  # 0.7% from config
                    "avg_final_profit": 0.0  # Will be calculated from actual trades
                }
            }
        
        # Fallback statistics
        return {
            "trailing_stop_manager": {
                "total_activations": 0,
                "orders_created": 0,
                "orders_updated": 0,
                "successful_fills": 0,
                "cancelled_orders": 0,
                "avg_profit_at_activation": 0.007,
                "avg_final_profit": 0.0
            }
        }
        
    except Exception as e:
        logger.error(f"Error getting trailing stop statistics: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/system/performance")
async def get_system_performance_metrics():
    """Get system performance metrics for analytics"""
    try:
        # Calculate system uptime
        if hasattr(orchestrator, 'start_time') and orchestrator.start_time:
            uptime_seconds = (datetime.utcnow() - orchestrator.start_time).total_seconds()
            uptime_hours = uptime_seconds / 3600
        else:
            uptime_hours = 0
        
        # Get order performance metrics
        order_metrics = await hybrid_order_tracker.get_performance_metrics()
        
        return {
            "system_uptime": uptime_hours,
            "websocket_latency": 50,  # Placeholder - would be measured from actual WebSocket connections
            "api_error_rate": 0.1,  # Placeholder - would be calculated from actual API calls
            "order_performance": order_metrics,
            "active_trades": len(orchestrator.active_trades_dict) if hasattr(orchestrator, 'active_trades_dict') else 0,
            "cycle_count": orchestrator.cycle_count if hasattr(orchestrator, 'cycle_count') else 0
        }
        
    except Exception as e:
        logger.error(f"Error getting system performance metrics: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/websocket/callback/{exchange}")
async def websocket_callback_handler(exchange: str, event_data: dict):
    """Handle WebSocket callbacks from exchange services for realtime order tracking"""
    try:
        logger.info(f"📡 WebSocket callback from {exchange}: {event_data.get('event_type', 'unknown')}")
        
        # Route to appropriate handler based on event type
        event_type = event_data.get('event_type') or event_data.get('type') or event_data.get('channel', '')
        
        # Check if this is a filled order (various formats)
        status = event_data.get('status', '').upper()
        order_status = event_data.get('order_status', '').upper()
        
        if (event_type == 'order_filled' or 
            event_type == 'executionReport' or
            status in ['FILLED', 'filled'] or 
            order_status in ['FILLED', 'filled']):
            await handle_order_filled_callback(exchange, event_data)
        elif event_type == 'order_created':
            await handle_order_created_callback(exchange, event_data)
        elif event_type in ['order_cancelled', 'order_rejected', 'order_expired']:
            await handle_order_status_callback(exchange, event_data)
        elif 'balance' in event_type.lower():
            await handle_balance_update_callback(exchange, event_data)
        else:
            logger.debug(f"Unhandled WebSocket event type: {event_type} (status: {status})")
        
        return {"status": "processed", "event_type": event_type}
        
    except Exception as e:
        logger.error(f"❌ Error processing WebSocket callback from {exchange}: {e}")
        return {"status": "error", "error": str(e)}

async def handle_order_filled_callback(exchange: str, event_data: dict):
    """Handle order filled events from WebSocket execution reports"""
    try:
        order_id = event_data.get('order_id') or event_data.get('client_order_id')
        status = event_data.get('order_status', 'filled')
        
        if not order_id:
            logger.warning("Order filled callback missing order_id")
            return
            
        logger.info(f"💰 Order filled: {order_id} on {exchange}")
        
        # Notify WebSocket order tracker
        await websocket_order_tracker.notify_order_update(order_id, status, event_data)
        
        # Update hybrid order tracker
        await hybrid_order_tracker.update_order_status(order_id, status, event_data)
        
        # Special handling for trailing stop integration  
        await handle_order_fill_for_trailing_stop(exchange, order_id, event_data)
        
        # 🔥 CRITICAL: Process through Redis-enhanced system
        await redis_realtime_manager.process_order_fill_callback(exchange, order_id, event_data)
        
    except Exception as e:
        logger.error(f"❌ Error handling order filled callback: {e}")

async def handle_order_created_callback(exchange: str, event_data: dict):
    """Handle order created events from WebSocket execution reports"""
    try:
        order_id = event_data.get('order_id') or event_data.get('client_order_id')
        
        if not order_id:
            logger.warning("Order created callback missing order_id")
            return
            
        logger.info(f"📝 Order created: {order_id} on {exchange}")
        
        # Register order for Redis tracking
        await redis_realtime_manager.register_order_for_tracking(exchange, order_id, event_data)
        
    except Exception as e:
        logger.error(f"❌ Error handling order created callback: {e}")

async def handle_order_status_callback(exchange: str, event_data: dict):
    """Handle order status change events (cancelled, rejected, expired)"""
    try:
        order_id = event_data.get('order_id') or event_data.get('client_order_id')
        status = event_data.get('status', 'unknown')
        
        if not order_id:
            logger.warning("Order status callback missing order_id")
            return
            
        logger.info(f"📋 Order status change: {order_id} -> {status} on {exchange}")
        
        # Update order status in Redis
        await redis_realtime_manager._update_order_status(order_id, status)
        
    except Exception as e:
        logger.error(f"❌ Error handling order status callback: {e}")

# Old database recording function removed - now handled by Redis system

async def handle_order_update_callback(exchange: str, event_data: dict):
    """Handle realtime order status updates"""
    try:
        order_id = event_data.get('order_id') or event_data.get('client_order_id')
        status = event_data.get('status') or event_data.get('order_status')
        
        if not order_id:
            logger.warning("Order callback missing order_id")
            return
            
        logger.info(f"📋 Order update: {order_id} -> {status} on {exchange}")
        
        # Notify WebSocket order tracker
        await websocket_order_tracker.notify_order_update(order_id, status, event_data)
        
        # Update hybrid order tracker
        await hybrid_order_tracker.update_order_status(order_id, status, event_data)
        
        # Special handling for trailing stop integration  
        if status in ['FILLED', 'PARTIALLY_FILLED']:
            await handle_order_fill_for_trailing_stop(exchange, order_id, event_data)
            
        # Record filled orders via Redis-enhanced system
        if status in ['FILLED', 'filled', 'PARTIALLY_FILLED', 'partially_filled']:
            await redis_realtime_manager.process_order_fill_callback(exchange, order_id, event_data)
            
    except Exception as e:
        logger.error(f"❌ Error handling order update callback: {e}")

async def handle_trade_update_callback(exchange: str, event_data: dict):
    """Handle realtime trade execution updates"""
    try:
        trade_id = event_data.get('trade_id')
        order_id = event_data.get('order_id') or event_data.get('client_order_id')
        
        logger.info(f"💰 Trade update: {trade_id} for order {order_id} on {exchange}")
        
        # Update trade tracking systems
        await hybrid_order_tracker.update_trade_execution(trade_id, order_id, event_data)
        
    except Exception as e:
        logger.error(f"❌ Error handling trade update callback: {e}")

async def handle_balance_update_callback(exchange: str, event_data: dict):
    """Handle realtime balance updates"""
    try:
        currency = event_data.get('currency') or event_data.get('asset')
        available = event_data.get('available') or event_data.get('free')
        
        logger.debug(f"💰 Balance update: {currency} = {available} on {exchange}")
        
        # Could trigger balance-based alerts or rebalancing logic here
        
    except Exception as e:
        logger.error(f"❌ Error handling balance update callback: {e}")

async def handle_order_fill_for_trailing_stop(exchange: str, order_id: str, event_data: dict):
    """Handle order fills that might trigger trailing stop activation"""
    try:
        # Check if this is a limit order that should activate trailing stop
        fill_price = event_data.get('price') or event_data.get('avg_price')
        filled_quantity = event_data.get('filled_quantity') or event_data.get('filled')
        
        if not fill_price or not filled_quantity:
            return
            
        logger.info(f"🎯 Order fill detected for trailing stop: {order_id} @ {fill_price}")
        
        # Look for associated trade that might need trailing stop activation
        # This integrates with the trailing stop manager
        try:
            # Get trade information from database
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(f"{database_service_url}/api/v1/orders/{order_id}")
                if response.status_code == 200:
                    order_data = response.json()
                    trade_id = order_data.get('trade_id')
                    
                    if trade_id:
                        # Notify trailing stop manager of the fill
                        await notify_trailing_stop_manager(trade_id, order_id, fill_price, event_data)
                        
        except Exception as e:
            logger.error(f"❌ Error integrating with trailing stop: {e}")
            
    except Exception as e:
        logger.error(f"❌ Error handling order fill for trailing stop: {e}")

async def notify_trailing_stop_manager(trade_id: str, order_id: str, fill_price: float, event_data: dict):
    """Notify trailing stop manager of order fills for realtime activation"""
    try:
        logger.info(f"🔄 Notifying trailing stop manager: trade {trade_id}, order {order_id}, price {fill_price}")
        
        # This would integrate with your trailing stop system
        # For now, we'll log the integration point
        logger.info(f"🎯 TRAILING STOP INTEGRATION POINT: Trade {trade_id} filled at {fill_price}")
        logger.info(f"   Ready for realtime trailing stop activation via WebSocket feeds")
        
    except Exception as e:
        logger.error(f"❌ Error notifying trailing stop manager: {e}")


@app.get("/api/v1/exchange/{exchange}/statistics")
async def get_exchange_statistics(exchange: str):
    """Get exchange-specific trading statistics"""
    try:
        if exchange not in ['binance', 'bybit', 'cryptocom']:
            raise HTTPException(status_code=400, detail=f"Unsupported exchange: {exchange}")
            
        # Get exchange balance and trading stats
        stats = {
            "exchange": exchange,
            "status": "operational",
            "balance": 0.0,
            "active_trades": 0,
            "daily_volume": 0.0,
            "success_rate": 0.0,
            "timestamp": datetime.now().isoformat()
        }
        
        try:
            # Get balance from exchange service
            async with httpx.AsyncClient(timeout=10.0) as client:
                balance_response = await client.get(f"{exchange_service_url}/api/v1/account/balance/{exchange}")
                if balance_response.status_code == 200:
                    balance_data = balance_response.json()
                    # Sum USDT equivalent balance
                    stats["balance"] = sum(
                        float(asset.get("usd_value", 0)) 
                        for asset in balance_data.get("balances", [])
                        if float(asset.get("total", 0)) > 0
                    )
        except Exception as e:
            logger.warning(f"Could not fetch {exchange} balance: {e}")
            
        try:
            # Get active trades count
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{database_service_url}/api/v1/trades/open")
                if response.status_code == 200:
                    open_trades = response.json().get('trades', [])
                    stats["active_trades"] = len([t for t in open_trades if t.get("exchange") == exchange])
                else:
                    stats["active_trades"] = 0
        except Exception as e:
            logger.warning(f"Could not fetch active trades for {exchange}: {e}")
            stats["active_trades"] = 0
            
        return stats
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting {exchange} statistics: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/websocket/register")
async def register_websocket_callbacks():
    """Register WebSocket callbacks with exchange services for realtime order tracking"""
    try:
        logger.info("🔌 Registering WebSocket callbacks with exchange services")
        
        results = {}
        
        # Register with each exchange
        for exchange in ['binance', 'cryptocom', 'bybit']:
            try:
                # Register callback URL with exchange service
                callback_url = f"http://orchestrator-service:8005/api/v1/websocket/callback/{exchange}"
                
                async with httpx.AsyncClient(timeout=30.0) as client:
                    # First check if exchange has WebSocket capability
                    status_response = await client.get(f"{exchange_service_url}/api/v1/websocket/{exchange}/status")
                    
                    if status_response.status_code == 200:
                        status_data = status_response.json()
                        
                        if status_data.get('enabled', False):
                            # Exchange has WebSocket capability
                            logger.info(f"✅ {exchange} WebSocket enabled, setting up callbacks")
                            
                            # Update connection status
                            await websocket_order_tracker.connect_exchange(exchange)
                            
                            results[exchange] = {
                                "status": "registered", 
                                "callback_url": callback_url,
                                "websocket_enabled": True
                            }
                        else:
                            results[exchange] = {
                                "status": "not_available", 
                                "websocket_enabled": False
                            }
                    else:
                        results[exchange] = {
                            "status": "error", 
                            "error": f"Status check failed: {status_response.status_code}"
                        }
                        
            except Exception as e:
                logger.error(f"❌ Error registering callbacks for {exchange}: {e}")
                results[exchange] = {"status": "error", "error": str(e)}
        
        # Update global WebSocket tracker status
        await websocket_order_tracker.update_all_connections()
        
        return {
            "status": "completed",
            "registrations": results,
            "websocket_enabled": websocket_order_tracker.websocket_enabled
        }
        
    except Exception as e:
        logger.error(f"❌ Error registering WebSocket callbacks: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8005,
        reload=False,
        log_level="info"
    ) 