"""
Exchange-Delegated Trailing Stop Manager

This module implements the core trailing stop management system that leverages
exchange-native limit orders for precise execution while maintaining intelligent
order management and real-time price monitoring.

Key Features:
- 0.7% activation threshold (matches config.yaml)
- 0.25% trailing distance (improved from 0.35%)
- Exchange-delegated execution via limit orders
- Real-time WebSocket price monitoring
- Complete order lifecycle management
- Database synchronization and tracking

Author: Claude Code
Created: 2025-08-30
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass
from enum import Enum
import json

logger = logging.getLogger(__name__)

class TrailingStopState(Enum):
    """States for trailing stop management"""
    INACTIVE = "inactive"           # No trailing stop activated
    ACTIVE = "active"              # Trailing stop activated, order placed
    UPDATING = "updating"          # Order being updated to new price
    FILLED = "filled"              # Order has been filled
    CANCELLED = "cancelled"        # Order was cancelled
    ERROR = "error"               # Error state requiring attention

@dataclass
class TrailingStopData:
    """Data structure for tracking trailing stop information"""
    trade_id: str
    exchange: str
    symbol: str
    entry_price: float
    position_size: float
    activation_threshold: float = 0.007  # 0.7%
    trail_distance: float = 0.0025       # 0.25%
    current_price: float = 0.0
    highest_price: float = 0.0
    trail_price: float = 0.0
    exit_id: Optional[str] = None        # Exchange order ID
    state: TrailingStopState = TrailingStopState.INACTIVE
    activated_at: Optional[datetime] = None
    last_updated: Optional[datetime] = None
    update_count: int = 0

class TrailingStopManager:
    """
    Core trailing stop management system using exchange-delegated execution
    
    This manager handles the complete lifecycle of trailing stops:
    1. Monitor OPEN trades for 0.7% profit activation
    2. Create limit sell orders at 0.25% trail distance
    3. Update orders when price moves favorably
    4. Track order status and synchronize with database
    """
    
    def __init__(self, config: Dict[str, Any], database_service=None, exchange_service=None, price_feed=None):
        """
        Initialize the trailing stop manager
        
        Args:
            config: Configuration dictionary
            database_service: Database service for trade data
            exchange_service: Exchange service for order management
            price_feed: Optional WebSocketPriceFeed for event-driven ratchet
        """
        self.config = config
        self.database_service = database_service
        self.exchange_service = exchange_service
        # PnL-FIX v8 (Q3): event-driven price feed (WebSocketPriceFeed).
        # When provided + event_driven_enabled=true, the manager subscribes
        # to add_price_callback so the trail ratchets on EVERY price tick
        # instead of only on the polling cadence (default 2s).
        self.price_feed = price_feed
        
        # Load configuration
        self.activation_threshold = config.get('trading', {}).get('trailing_stop', {}).get('activation_threshold', 0.007)
        self.trail_distance = config.get('trading', {}).get('trailing_stop', {}).get('step_percentage', 0.003)  # Use config value: 0.3% distance
        # PnL-FIX v8 (Q1): default lowered 5 → 2 to halve the worst-case poll gap.
        self.check_interval = config.get('trading', {}).get('trailing_stop', {}).get('check_interval_seconds', 2)
        # PnL-FIX v8 (Q2): default lowered 0.001 → 0.0003 so micro-pumps ratchet.
        self.update_threshold = config.get('trading', {}).get('trailing_stop', {}).get('update_threshold', 0.0003)
        # PnL-FIX v8 (F3): hard breakeven floor — the trail price is never
        # allowed below entry * (1 + breakeven_floor_percentage). This
        # guarantees that every armed trail exits with at least this much
        # locked profit (covers round-trip fees). Default 0.30%.
        self.breakeven_floor_percentage = config.get('trading', {}).get('trailing_stop', {}).get(
            'breakeven_floor_percentage', 0.003
        )
        # PnL-FIX v8 (Q3): toggle to enable/disable the event-driven path.
        self.event_driven_enabled = bool(
            config.get('trading', {}).get('trailing_stop', {}).get('event_driven_enabled', True)
        )
        feed_policy = config.get('trading', {}).get('trailing_stop', {}).get('feed_quality_policy', {}) or {}
        self._degraded_sources = {
            str(s).lower() for s in feed_policy.get('degraded_sources', ['ohlcv_1m', 'orderbook_mid'])
        }
        self._down_sources = {
            str(s).lower() for s in feed_policy.get('down_sources', ['entry_fallback'])
        }
        self._allow_exit_in_degraded = bool(feed_policy.get('allow_exit_in_degraded', True))
        self._allow_exit_in_down = bool(feed_policy.get('allow_exit_in_down', True))
        
        # Active trailing stops tracking
        self.active_stops: Dict[str, TrailingStopData] = {}
        # PnL-FIX v8 (Q3): per-trade lock so concurrent ticks for the same
        # trade serialize through _update_trailing_order safely.
        self._tick_locks: Dict[str, asyncio.Lock] = {}
        # Reverse index: (exchange, symbol) -> set(trade_id) for O(1) tick routing.
        self._symbol_index: Dict[Tuple[str, str], set] = {}
        self.monitoring_active = False
        
        # Statistics
        self.stats = {
            'stops_activated': 0,
            'orders_created': 0,
            'orders_updated': 0,
            'orders_updated_via_tick': 0,   # PnL-FIX v8 (Q3): event-driven updates
            'orders_filled': 0,
            'errors': 0,
            'activation_skipped_degraded_feed': 0,
            'updates_skipped_degraded_feed': 0,
        }
        self._last_price_source: str = "unknown"
        self._last_feed_quality: str = "UNKNOWN"
        
        logger.info(
            "🎯 TrailingStopManager initialized - activation:%.4f, trail:%.4f, "
            "check_interval:%ss, update_threshold:%.4f, breakeven_floor:%.4f, "
            "event_driven:%s",
            self.activation_threshold, self.trail_distance, self.check_interval,
            self.update_threshold, self.breakeven_floor_percentage,
            self.event_driven_enabled,
        )
    
    async def start_monitoring(self):
        """Start the main monitoring loop"""
        if self.monitoring_active:
            logger.warning("TrailingStopManager monitoring already active")
            return
            
        self.monitoring_active = True
        logger.info("🚀 Starting trailing stop monitoring")

        # PnL-FIX v8 (Q3): subscribe to the WS price feed so the trail
        # ratchets on every tick instead of only on the polling cadence.
        if self.event_driven_enabled and self.price_feed is not None:
            try:
                self.price_feed.add_price_callback(self._handle_price_tick)
                logger.info(
                    "🔔 [event-driven] TrailingStopManager subscribed to WS price feed — "
                    "trail will ratchet on every tick (poll interval %ss kept as safety net)",
                    self.check_interval,
                )
            except Exception as e:
                logger.error(
                    "⚠️ Could not subscribe to WS price feed (falling back to poll-only): %s",
                    e,
                )
        else:
            logger.info(
                "ℹ️ Event-driven trail disabled (price_feed=%s, enabled=%s) — using polling only",
                self.price_feed is not None, self.event_driven_enabled,
            )
        
        try:
            while self.monitoring_active:
                await self._monitoring_cycle()
                await asyncio.sleep(self.check_interval)
                
        except Exception as e:
            logger.error(f"❌ Critical error in trailing stop monitoring: {e}")
            self.monitoring_active = False
            raise
    
    async def stop_monitoring(self):
        """Stop the monitoring loop"""
        logger.info("🛑 Stopping trailing stop monitoring")
        self.monitoring_active = False
        
        # Clean up any pending operations
        for trade_id, stop_data in self.active_stops.items():
            if stop_data.state == TrailingStopState.UPDATING:
                logger.warning(f"⚠️ Trade {trade_id} left in updating state during shutdown")
    
    async def _monitoring_cycle(self):
        """Execute one complete monitoring cycle"""
        try:
            # Phase 1: Check for new OPEN trades to activate
            await self._check_activation_candidates()
            
            # Phase 2: Monitor active trailing stops for price updates
            await self._monitor_active_stops()
            
            # Phase 3: Check order status and handle fills
            await self._check_order_status()
            
            # Phase 4: Cleanup expired or invalid stops
            await self._cleanup_stops()
            
        except Exception as e:
            logger.error(f"❌ Error in monitoring cycle: {e}")
            self.stats['errors'] += 1
    
    async def _check_activation_candidates(self):
        """Check OPEN trades for trailing stop activation"""
        if not self.database_service:
            return
            
        try:
            # Get all OPEN trades without exit_id (no active trailing stop)
            open_trades = await self.database_service.get_open_trades_without_exit()
            
            for trade in open_trades:
                # Skip if already being managed
                if trade.id in self.active_stops:
                    continue
                
                # Get current price for this trade
                current_price = await self._get_current_price(trade.exchange, trade.symbol)
                if not current_price:
                    continue
                feed_quality = self._last_feed_quality
                if feed_quality == "DEGRADED" and not self._allow_exit_in_degraded:
                    self.stats['activation_skipped_degraded_feed'] += 1
                    logger.warning(
                        f"[Trade {trade.id}] ⚠️ Activation deferred: feed_quality={feed_quality}, "
                        f"source={self._last_price_source}"
                    )
                    continue
                if feed_quality == "DOWN" and not self._allow_exit_in_down:
                    self.stats['activation_skipped_degraded_feed'] += 1
                    logger.warning(
                        f"[Trade {trade.id}] ⚠️ Activation deferred: feed_quality={feed_quality}, "
                        f"source={self._last_price_source}"
                    )
                    continue
                
                # Calculate profit percentage
                profit_pct = (current_price - trade.entry_price) / trade.entry_price
                
                logger.debug(f"[Trade {trade.id}] Price: ${current_price:.4f}, Profit: {profit_pct:.3%}")
                
                # CRITICAL FIX: Check for activation (0.7% threshold) regardless of profit protection status
                if profit_pct >= self.activation_threshold:
                    # PnL-FIX v11.3 — peak-buffer gate (mirror main.py NewTrailingStop):
                    # do not arm exchange trail until peak >= breakeven_floor + step_percentage.
                    min_peak_pct_for_trail = (
                        (self.breakeven_floor_percentage + self.trail_distance) * 100.0
                    )
                    hp = getattr(trade, "highest_price", None)
                    try:
                        hp_f = float(hp) if hp is not None else None
                    except (TypeError, ValueError):
                        hp_f = None
                    if hp_f is None or hp_f <= 0:
                        hp_f = max(float(current_price), float(trade.entry_price))
                    try:
                        current_peak_pct = (
                            (hp_f - float(trade.entry_price)) / float(trade.entry_price)
                        ) * 100.0
                    except Exception:
                        current_peak_pct = profit_pct * 100.0
                    if current_peak_pct < min_peak_pct_for_trail:
                        logger.info(
                            f"[Trade {trade.id}] ⏳ PEAK-BUFFER GATE: peak {current_peak_pct:.2f}% < "
                            f"required {min_peak_pct_for_trail:.2f}% "
                            f"(breakeven_floor {self.breakeven_floor_percentage*100:.2f}% + "
                            f"step {self.trail_distance*100:.2f}%) — deferring trail activation"
                        )
                        continue

                    # Check if profit protection is blocking activation
                    profit_protection_status = getattr(trade, 'profit_protection', None)
                    if profit_protection_status and profit_protection_status != 'inactive' and profit_pct < 1.0:
                        logger.warning(f"[Trade {trade.id}] ⚠️ PROFIT PROTECTION BLOCKING: PnL {profit_pct:.2%} < 1.0% but profit_protection={profit_protection_status}")
                        logger.warning(f"[Trade {trade.id}] 🔧 FORCING ACTIVATION: Trailing stop should activate between 0.7% and 1.0%")
                        # Reset profit protection to allow trailing stop activation
                        if self.database_service:
                            await self.database_service.update_trade(trade.id, {
                                'profit_protection': 'inactive'
                            })
                        logger.info(f"[Trade {trade.id}] ✅ RESET: profit_protection set to inactive to allow trailing stop")
                    
                    await self._activate_trailing_stop(trade, current_price)
                    
                    # CRITICAL: Once trailing stop is active, prevent profit protection from overriding it
                    logger.info(f"[Trade {trade.id}] 🛡️ TRAILING STOP ACTIVE: Preventing profit protection from overriding trailing stop")
                    
        except Exception as e:
            logger.error(f"❌ Error checking activation candidates: {e}")
            self.stats['errors'] += 1
    
    async def _activate_trailing_stop(self, trade, current_price: float):
        """Activate trailing stop for a trade"""
        try:
            # Calculate trail price (callback below current price).
            # PnL-FIX v8 (F3): the safety floor was hardcoded to entry*1.001
            # (+0.10%) which was BELOW round-trip taker fees (~0.30%) — meaning
            # a "successful" trail-stop exit could still be net-negative. The
            # floor is now configurable (default +0.30%) so every armed trail
            # exits at break-even net of fees or better.
            calculated_trail_price = current_price * (1 - self.trail_distance)
            breakeven_floor = trade.entry_price * (1 + self.breakeven_floor_percentage)
            trail_price = max(calculated_trail_price, breakeven_floor)
            
            # Additional safety: If we're still at a loss, don't activate trailing stop
            if current_price <= trade.entry_price:
                logger.warning(f"❌ SKIPPING trailing stop activation: Current price ${current_price:.4f} <= entry price ${trade.entry_price:.4f} - no profit to protect")
                return
            
            logger.info(f"🟢 Activating trailing stop for trade {trade.id}")
            logger.info(f"📊 Entry: ${trade.entry_price:.4f}, Current: ${current_price:.4f}, Trail: ${trail_price:.4f}")
            
            # Create trailing stop data
            stop_data = TrailingStopData(
                trade_id=trade.id,
                exchange=trade.exchange,
                symbol=trade.symbol,
                entry_price=trade.entry_price,
                position_size=trade.position_size,
                current_price=current_price,
                highest_price=current_price,
                trail_price=trail_price,
                activated_at=datetime.utcnow(),
                last_updated=datetime.utcnow(),
                state=TrailingStopState.ACTIVE
            )
            
            # Create limit sell order on exchange
            order_result = await self._create_limit_sell_order(stop_data)
            
            if order_result and order_result.get('success'):
                # Store exit_id and update database
                stop_data.exit_id = order_result['order_id']
                await self.database_service.update_trade_exit_id(trade.id, order_result['order_id'])
                
                # Add to active stops
                self.active_stops[trade.id] = stop_data
                # PnL-FIX v8 (Q3): index by (exchange, symbol) for O(1) tick routing.
                key = (trade.exchange, trade.symbol)
                self._symbol_index.setdefault(key, set()).add(trade.id)
                self._tick_locks.setdefault(trade.id, asyncio.Lock())
                
                # Update statistics
                self.stats['stops_activated'] += 1
                self.stats['orders_created'] += 1
                
                logger.info(f"✅ Trailing stop activated for trade {trade.id} - Order: {order_result['order_id']}")
                
            else:
                logger.error(f"❌ Failed to create trailing stop order for trade {trade.id}")
                self.stats['errors'] += 1
                
        except Exception as e:
            logger.error(f"❌ Error activating trailing stop for trade {trade.id}: {e}")
            self.stats['errors'] += 1
    
    async def _monitor_active_stops(self):
        """Monitor active trailing stops for price updates"""
        for trade_id, stop_data in list(self.active_stops.items()):
            try:
                if stop_data.state not in [TrailingStopState.ACTIVE, TrailingStopState.UPDATING]:
                    continue
                
                # Get current price
                current_price = await self._get_current_price(stop_data.exchange, stop_data.symbol)
                if not current_price:
                    continue
                feed_quality = self._last_feed_quality
                if feed_quality in ("DEGRADED", "DOWN"):
                    self.stats['updates_skipped_degraded_feed'] += 1
                    logger.debug(
                        "[Trade %s] Skipping trail ratchet due to feed_quality=%s source=%s",
                        trade_id, feed_quality, self._last_price_source
                    )
                    stop_data.current_price = current_price
                    stop_data.last_updated = datetime.utcnow()
                    continue
                
                stop_data.current_price = current_price
                
                # Check if we have a new high
                if current_price > stop_data.highest_price:
                    # Update highest price
                    stop_data.highest_price = current_price
                    
                    # PnL-FIX v8 (F3): use the configurable breakeven floor
                    # (default +0.30%) instead of hardcoded +0.10% so the
                    # trail can never settle below fee-covered break-even.
                    calculated_new_trail_price = current_price * (1 - self.trail_distance)
                    breakeven_floor = stop_data.entry_price * (1 + self.breakeven_floor_percentage)
                    new_trail_price = max(calculated_new_trail_price, breakeven_floor)
                    
                    # Check if improvement is significant enough to update
                    price_improvement = (new_trail_price - stop_data.trail_price) / stop_data.trail_price
                    
                    if price_improvement >= self.update_threshold:
                        logger.info(f"📈 [Trade {trade_id}] New high: ${current_price:.4f}, updating trail to ${new_trail_price:.4f}")
                        await self._update_trailing_order(stop_data, new_trail_price)
                    else:
                        logger.debug(f"[Trade {trade_id}] Price improvement {price_improvement:.3%} below threshold")
                
                stop_data.last_updated = datetime.utcnow()
                
            except Exception as e:
                logger.error(f"❌ Error monitoring active stop for trade {trade_id}: {e}")
                self.stats['errors'] += 1
    
    async def _update_trailing_order(self, stop_data: TrailingStopData, new_trail_price: float):
        """Update existing trailing stop order with new price"""
        try:
            if stop_data.state != TrailingStopState.ACTIVE:
                logger.warning(f"⚠️ Cannot update order for trade {stop_data.trade_id} - state: {stop_data.state}")
                return
            
            stop_data.state = TrailingStopState.UPDATING
            
            # CRITICAL: Check if existing order is already filled before attempting update
            order_status = await self._get_order_status(stop_data)
            
            if order_status and order_status.get('state') == 'FILLED':
                logger.info(f"🎯 [Trade {stop_data.trade_id}] Order already FILLED during update attempt - handling fill")
                await self._handle_order_fill(stop_data, order_status)
                return
            elif order_status and order_status.get('state') in ['CANCELLED', 'UNKNOWN']:
                logger.warning(f"⚠️ [Trade {stop_data.trade_id}] Order in unexpected state: {order_status.get('state')} - resetting to error")
                stop_data.state = TrailingStopState.ERROR
                return
            
            # Safe to proceed with update - order is still OPEN
            cancel_result = await self._cancel_order(stop_data)
            
            if cancel_result and cancel_result.get('success'):
                # Update trail price
                stop_data.trail_price = new_trail_price
                
                # Create new order
                order_result = await self._create_limit_sell_order(stop_data)
                
                if order_result and order_result.get('success'):
                    # Update exit_id
                    old_exit_id = stop_data.exit_id
                    stop_data.exit_id = order_result['order_id']
                    stop_data.update_count += 1
                    stop_data.state = TrailingStopState.ACTIVE
                    
                    # Update database
                    await self.database_service.update_trade_exit_id(stop_data.trade_id, order_result['order_id'])
                    
                    self.stats['orders_updated'] += 1
                    
                    logger.info(f"✅ [Trade {stop_data.trade_id}] Order updated: {old_exit_id} → {order_result['order_id']}")
                    
                else:
                    logger.error(f"❌ [Trade {stop_data.trade_id}] Failed to create new order after cancellation")
                    stop_data.state = TrailingStopState.ERROR
                    self.stats['errors'] += 1
            else:
                logger.error(f"❌ [Trade {stop_data.trade_id}] Failed to cancel existing order")
                stop_data.state = TrailingStopState.ERROR
                self.stats['errors'] += 1
                
        except Exception as e:
            logger.error(f"❌ Error updating trailing order for trade {stop_data.trade_id}: {e}")
            stop_data.state = TrailingStopState.ERROR
            self.stats['errors'] += 1
    
    async def _check_order_status(self):
        """Check status of active orders for fills"""
        for trade_id, stop_data in list(self.active_stops.items()):
            try:
                if not stop_data.exit_id or stop_data.state not in [TrailingStopState.ACTIVE, TrailingStopState.UPDATING]:
                    continue
                
                # Check order status on exchange
                order_status = await self._get_order_status(stop_data)
                
                if order_status and order_status.get('state') == 'FILLED':
                    await self._handle_order_fill(stop_data, order_status)
                    
            except Exception as e:
                logger.error(f"❌ Error checking order status for trade {trade_id}: {e}")
                self.stats['errors'] += 1
    
    async def _handle_order_fill(self, stop_data: TrailingStopData, order_status: Dict[str, Any]):
        """Handle a filled trailing stop order"""
        try:
            fill_price = float(order_status.get('fill_price', stop_data.trail_price))
            fill_time = order_status.get('fill_time', datetime.utcnow())
            
            # Calculate realized PnL
            realized_pnl = (fill_price - stop_data.entry_price) * stop_data.position_size
            
            logger.info(f"🎯 [Trade {stop_data.trade_id}] Trailing stop FILLED at ${fill_price:.4f}")
            logger.info(f"💰 Realized PnL: ${realized_pnl:.2f}")
            
            # Update trade in database
            await self.database_service.close_trade(
                trade_id=stop_data.trade_id,
                exit_price=fill_price,
                exit_time=fill_time,
                realized_pnl=realized_pnl,
                exit_reason='trailing_stop_filled'
            )
            
            # Update stop data state
            stop_data.state = TrailingStopState.FILLED
            
            # Update statistics
            self.stats['orders_filled'] += 1
            
            logger.info(f"✅ [Trade {stop_data.trade_id}] Trade closed successfully via trailing stop")
            
            # Remove from active stops (will be cleaned up in next cycle)
            
        except Exception as e:
            logger.error(f"❌ Error handling order fill for trade {stop_data.trade_id}: {e}")
            self.stats['errors'] += 1
    
    async def _cleanup_stops(self):
        """Clean up completed or expired trailing stops"""
        to_remove = []
        
        for trade_id, stop_data in self.active_stops.items():
            # Remove filled orders
            if stop_data.state == TrailingStopState.FILLED:
                to_remove.append(trade_id)
                continue
            
            # Remove old error states (after 1 hour)
            if (stop_data.state == TrailingStopState.ERROR and 
                stop_data.last_updated and 
                (datetime.utcnow() - stop_data.last_updated) > timedelta(hours=1)):
                to_remove.append(trade_id)
                logger.warning(f"🧹 Cleaning up error state for trade {trade_id}")
                continue
        
        # Remove marked stops
        for trade_id in to_remove:
            self._drop_trade(trade_id)
            logger.debug(f"🧹 Cleaned up trailing stop for trade {trade_id}")

    def _drop_trade(self, trade_id: str) -> None:
        """PnL-FIX v8 (Q3): single source of truth for removing a trade from
        active_stops + symbol index + per-trade lock map. Keeps the WS tick
        router from sending updates to a fully-closed trade."""
        stop_data = self.active_stops.pop(trade_id, None)
        if stop_data is not None:
            key = (stop_data.exchange, stop_data.symbol)
            ids = self._symbol_index.get(key)
            if ids:
                ids.discard(trade_id)
                if not ids:
                    self._symbol_index.pop(key, None)
        self._tick_locks.pop(trade_id, None)

    async def _handle_price_tick(self, price_update) -> None:
        """PnL-FIX v8 (Q3): event-driven trail ratchet.

        Called by WebSocketPriceFeed on every tick. Locates any active
        trailing stops for (exchange, symbol), updates highest_price, and
        re-issues the limit order if the new trail beats the old one by at
        least update_threshold. Per-trade asyncio.Lock prevents racing ticks.
        """
        try:
            exchange = getattr(price_update, 'exchange', None)
            symbol = getattr(price_update, 'symbol', None)
            price = getattr(price_update, 'price', None)
            if not exchange or not symbol or not price or price <= 0:
                return

            trade_ids = self._symbol_index.get((exchange, symbol))
            if not trade_ids:
                return  # No active stops on this symbol — fast path

            # Snapshot the set so we can mutate during await calls.
            for trade_id in list(trade_ids):
                stop_data = self.active_stops.get(trade_id)
                if not stop_data:
                    continue
                if stop_data.state not in (TrailingStopState.ACTIVE, TrailingStopState.UPDATING):
                    continue

                # Cheap pre-check before grabbing the lock: only proceed if
                # the tick is a new high.
                if price <= stop_data.highest_price:
                    stop_data.current_price = price
                    continue

                lock = self._tick_locks.setdefault(trade_id, asyncio.Lock())
                async with lock:
                    # Re-check under lock — another tick may have moved us.
                    stop_data = self.active_stops.get(trade_id)
                    if not stop_data or stop_data.state != TrailingStopState.ACTIVE:
                        continue
                    if price <= stop_data.highest_price:
                        continue

                    stop_data.highest_price = price
                    stop_data.current_price = price

                    calculated_new_trail_price = price * (1 - self.trail_distance)
                    breakeven_floor = stop_data.entry_price * (1 + self.breakeven_floor_percentage)
                    new_trail_price = max(calculated_new_trail_price, breakeven_floor)

                    if stop_data.trail_price <= 0:
                        improvement = 1.0  # First-ever update
                    else:
                        improvement = (new_trail_price - stop_data.trail_price) / stop_data.trail_price

                    if improvement >= self.update_threshold:
                        logger.info(
                            "📈 [tick] [Trade %s] %s %s new high $%.6f → trail $%.6f (improvement %.4f%%)",
                            trade_id, exchange, symbol, price, new_trail_price, improvement * 100,
                        )
                        await self._update_trailing_order(stop_data, new_trail_price)
                        self.stats['orders_updated_via_tick'] += 1
                    else:
                        logger.debug(
                            "[tick] [Trade %s] new high $%.6f but improvement %.4f%% < threshold %.4f%%",
                            trade_id, price, improvement * 100, self.update_threshold * 100,
                        )

                    stop_data.last_updated = datetime.utcnow()
        except Exception as e:
            logger.error(f"❌ Error in event-driven tick handler: {e}")
            self.stats['errors'] += 1

    async def _get_current_price(self, exchange: str, symbol: str) -> Optional[float]:
        """Get current price for a symbol from WebSocket or REST API"""
        if not self.exchange_service:
            return None
            
        try:
            # Use WebSocket price feed (real-time) - this is the same feed needed for dashboard
            price_data = await self.exchange_service.get_ticker_live(exchange, symbol)
            
            if price_data and not price_data.get('stale', False):
                self._last_price_source = str(price_data.get('source', 'ws_live')).lower()
                self._last_feed_quality = "GOOD"
                return float(price_data.get('last', 0))
            
            # Fallback to REST API if WebSocket stale
            ticker = await self.exchange_service.get_ticker(exchange, symbol)
            if ticker:
                self._last_price_source = str(ticker.get('source', 'rest')).lower()
                src = self._last_price_source
                if src in self._down_sources:
                    self._last_feed_quality = "DOWN"
                elif src in self._degraded_sources:
                    self._last_feed_quality = "DEGRADED"
                else:
                    self._last_feed_quality = "GOOD"
                return float(ticker.get('last', 0))
                
        except Exception as e:
            logger.error(f"❌ Error getting current price for {exchange} {symbol}: {e}")
            
        return None
    
    async def _create_limit_sell_order(self, stop_data: TrailingStopData) -> Optional[Dict[str, Any]]:
        """Create limit sell order on exchange"""
        if not self.exchange_service:
            return {'success': False, 'error': 'No exchange service available'}
            
        try:
            # CRITICAL FIX: Get actual available balance instead of using theoretical position size
            base_asset = stop_data.symbol.split('/')[0]
            available_amount = stop_data.position_size  # Default to position size
            
            try:
                # Get actual balance from exchange
                balance_result = await self.exchange_service.get_balance(stop_data.exchange)
                if balance_result and 'success' in balance_result and balance_result['success']:
                    balance_data = balance_result.get('data', {})
                    
                    # Try ccxt normalized format first, then raw format
                    if base_asset in balance_data:
                        available_amount = float(balance_data[base_asset].get('free', 0))
                    elif 'info' in balance_data:
                        for asset_balance in balance_data.get('info', {}).get('balances', []):
                            if asset_balance.get('asset') == base_asset:
                                available_amount = float(asset_balance.get('free', 0))
                                break
                    
                    # Use available amount or position size, whichever is smaller
                    sell_quantity = min(stop_data.position_size, available_amount)
                    logger.info(f"[Trade {stop_data.trade_id}] [TrailingStopManager] Balance check: Position={stop_data.position_size}, Available={available_amount}, Using={sell_quantity}")
                else:
                    logger.warning(f"[Trade {stop_data.trade_id}] [TrailingStopManager] ⚠️ Could not fetch balance, using position size")
                    sell_quantity = stop_data.position_size
            except Exception as e:
                logger.warning(f"[Trade {stop_data.trade_id}] [TrailingStopManager] ⚠️ Balance check failed: {e}, using position size")
                sell_quantity = stop_data.position_size
            
            if sell_quantity <= 0:
                logger.error(f"[Trade {stop_data.trade_id}] [TrailingStopManager] ❌ INSUFFICIENT BALANCE: No {base_asset} available")
                return {'success': False, 'error': f'Insufficient {base_asset} balance'}
            
            result = await self.exchange_service.create_limit_sell_order(
                exchange=stop_data.exchange,
                symbol=stop_data.symbol,
                quantity=sell_quantity,
                price=stop_data.trail_price
            )
            
            return result
            
        except Exception as e:
            logger.error(f"❌ Error creating limit sell order: {e}")
            return {'success': False, 'error': str(e)}
    
    async def _cancel_order(self, stop_data: TrailingStopData) -> Optional[Dict[str, Any]]:
        """Cancel existing order on exchange"""
        if not self.exchange_service or not stop_data.exit_id:
            return {'success': False, 'error': 'No exchange service or exit_id'}
            
        try:
            result = await self.exchange_service.cancel_order(
                exchange=stop_data.exchange,
                order_id=stop_data.exit_id,
                symbol=stop_data.symbol,  # required by some exchanges (e.g. Crypto.com)
            )
            
            return result
            
        except Exception as e:
            logger.error(f"❌ Error cancelling order {stop_data.exit_id}: {e}")
            return {'success': False, 'error': str(e)}
    
    async def _get_order_status(self, stop_data: TrailingStopData) -> Optional[Dict[str, Any]]:
        """Get order status from exchange"""
        if not self.exchange_service or not stop_data.exit_id:
            return None
            
        try:
            status = await self.exchange_service.get_order_status(
                exchange=stop_data.exchange,
                order_id=stop_data.exit_id,
                symbol=stop_data.symbol,  # required by some exchanges
            )
            
            return status
            
        except Exception as e:
            logger.error(f"❌ Error getting order status for {stop_data.exit_id}: {e}")
            return None
    
    # Public interface methods
    
    def get_active_stops_count(self) -> int:
        """Get number of active trailing stops"""
        return len([s for s in self.active_stops.values() if s.state == TrailingStopState.ACTIVE])
    
    def get_stop_data(self, trade_id: str) -> Optional[TrailingStopData]:
        """Get trailing stop data for a specific trade"""
        return self.active_stops.get(trade_id)
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get trailing stop manager statistics"""
        return {
            **self.stats,
            'active_stops': len(self.active_stops),
            'last_price_source': self._last_price_source,
            'last_feed_quality': self._last_feed_quality,
            'states': {state.value: len([s for s in self.active_stops.values() if s.state == state]) 
                      for state in TrailingStopState}
        }
    
    async def force_cleanup_trade(self, trade_id: str) -> bool:
        """Force cleanup of a specific trade (for emergency use)"""
        if trade_id in self.active_stops:
            stop_data = self.active_stops[trade_id]
            
            # Try to cancel order if active
            if stop_data.exit_id and stop_data.state == TrailingStopState.ACTIVE:
                await self._cancel_order(stop_data)
            
            # Remove from active stops (also clears symbol index + lock)
            self._drop_trade(trade_id)
            logger.warning(f"⚠️ Force cleanup completed for trade {trade_id}")
            return True
            
        return False
    
    def __repr__(self):
        """String representation"""
        return f"TrailingStopManager(active_stops={len(self.active_stops)}, threshold={self.activation_threshold:.3f})"