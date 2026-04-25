"""
Activation Trigger System Integration

This module provides the complete activation trigger system that integrates all
components of the exchange-delegated trailing stop system. It coordinates between
the TrailingStopManager, OrderLifecycleManager, and WebSocketPriceFeed to provide
precise 0.7% profit detection and seamless order management.

This is the final MVP component that ties everything together.

Key Features:
- Precise 0.7% profit activation detection
- Automatic price subscription management
- Complete integration with existing trading system
- Real-time dashboard price updates
- Comprehensive error handling and monitoring
- Production-ready deployment interface

Author: Claude Code
Created: 2025-08-30
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Callable
import json
from dataclasses import dataclass

from order_lifecycle_manager import OrderLifecycleManager, create_trailing_stop_integration
from websocket_price_feed import WebSocketPriceFeed, PriceUpdate
from trailing_stop_manager import TrailingStopManager, TrailingStopData, TrailingStopState

logger = logging.getLogger(__name__)

@dataclass
class ActivationEvent:
    """Activation event data"""
    trade_id: str
    exchange: str
    symbol: str
    entry_price: float
    current_price: float
    profit_pct: float
    activation_time: datetime
    trail_price: float

class ActivationTriggerSystem:
    """
    Complete activation trigger system for exchange-delegated trailing stops
    
    This is the main integration component that coordinates all parts of the 
    trailing stop system to provide precise 0.7% profit detection and 
    seamless order management.
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the activation trigger system
        
        Args:
            config: Configuration dictionary from config.yaml
        """
        self.config = config
        
        # Initialize integrated order lifecycle manager
        self.lifecycle_manager = create_trailing_stop_integration(config)
        self.order_manager = self.lifecycle_manager  # Alias for test compatibility
        
        # Get component references for direct access
        self.trailing_manager = self.lifecycle_manager.get_trailing_manager()
        self.price_feed = self.lifecycle_manager.price_feed
        
        # Activation tracking
        self.activation_threshold = config.get('trading', {}).get('trailing_stop', {}).get('activation_threshold', 0.007)
        self.activated_trades: Dict[str, ActivationEvent] = {}
        self.activation_callbacks: List[Callable[[ActivationEvent], None]] = []
        
        # Dashboard integration
        self.dashboard_callbacks: List[Callable[[PriceUpdate], None]] = []
        
        # System status
        self.system_active = False
        self.initialization_complete = False
        
        # Performance metrics
        self.metrics = {
            'trades_monitored': 0,
            'activations_triggered': 0,
            'orders_created': 0,
            'orders_filled': 0,
            'price_updates_processed': 0,
            'errors': 0,
            'system_uptime_start': None
        }
        
        logger.info(f"🎯 ActivationTriggerSystem initialized - threshold: {self.activation_threshold:.3f} ({self.activation_threshold*100:.1f}%)")
    
    async def start(self):
        """Start the complete activation trigger system"""
        if self.system_active:
            logger.warning("ActivationTriggerSystem already active")
            return
            
        logger.info("🚀 Starting Complete Activation Trigger System")
        
        try:
            # Start the integrated order lifecycle manager
            await self.lifecycle_manager.start()
            
            # Set up dashboard price feed integration
            self.price_feed.add_dashboard_callback(self._handle_dashboard_price_update)
            
            # Mark system as active
            self.system_active = True
            self.initialization_complete = True
            self.metrics['system_uptime_start'] = datetime.utcnow()
            
            logger.info("✅ ActivationTriggerSystem fully operational")
            logger.info(f"📊 Monitoring for {self.activation_threshold*100:.1f}% profit activation threshold")
            
            # Start monitoring for new trades to activate
            await self._start_activation_monitoring()
            
        except Exception as e:
            logger.error(f"❌ Failed to start ActivationTriggerSystem: {e}")
            self.system_active = False
            self.initialization_complete = False
            raise
    
    async def stop(self):
        """Stop the activation trigger system"""
        logger.info("🛑 Stopping ActivationTriggerSystem")
        
        self.system_active = False
        
        # Stop the order lifecycle manager
        await self.lifecycle_manager.stop()
        
        # Clear activation tracking
        self.activated_trades.clear()
        
        logger.info("✅ ActivationTriggerSystem stopped")
    
    async def _start_activation_monitoring(self):
        """Start monitoring loop for trade activations"""
        logger.info("🔄 Starting activation monitoring loop")
        
        while self.system_active:
            try:
                # Get all OPEN trades that might need activation
                await self._check_for_new_activations()
                
                # Monitor existing activated trades
                await self._monitor_activated_trades()
                
                # Update metrics
                self.metrics['trades_monitored'] = len(self.activated_trades)
                
                # Wait before next check (frequent checks for precise activation)
                await asyncio.sleep(2.0)  # Check every 2 seconds for responsive activation
                
            except Exception as e:
                logger.error(f"❌ Error in activation monitoring loop: {e}")
                self.metrics['errors'] += 1
                await asyncio.sleep(5.0)  # Longer wait on error
    
    async def _check_for_new_activations(self):
        """Check for trades that need trailing stop activation"""
        try:
            # Get OPEN trades without exit_id (not yet activated)
            open_trades = await self.lifecycle_manager.get_open_trades_without_exit()
            
            for trade in open_trades:
                if trade.id in self.activated_trades:
                    continue  # Already activated
                
                # Get current price
                current_price = await self.price_feed.get_current_price(trade.exchange, trade.symbol)
                
                if not current_price:
                    continue  # Skip if no price available
                
                # Calculate profit percentage
                profit_pct = (current_price - trade.entry_price) / trade.entry_price
                
                # Check for activation (precise 0.7% threshold)
                if profit_pct >= self.activation_threshold:
                    await self._trigger_activation(trade, current_price, profit_pct)
                    
        except Exception as e:
            logger.error(f"❌ Error checking for new activations: {e}")
            self.metrics['errors'] += 1
    
    async def _trigger_activation(self, trade, current_price: float, profit_pct: float):
        """Trigger trailing stop activation for a trade"""
        try:
            # Calculate trail price (0.25% distance)
            trail_distance = 0.0025  # Fixed 0.25% distance
            trail_price = current_price * (1 - trail_distance)
            
            logger.info(f"🟢 ACTIVATION TRIGGERED: Trade {trade.id}")
            logger.info(f"📊 {trade.exchange} {trade.symbol}: ${trade.entry_price:.4f} → ${current_price:.4f} (+{profit_pct:.2%})")
            logger.info(f"🎯 Trail price: ${trail_price:.4f} (0.25% distance)")
            
            # Create activation event
            activation_event = ActivationEvent(
                trade_id=trade.id,
                exchange=trade.exchange,
                symbol=trade.symbol,
                entry_price=trade.entry_price,
                current_price=current_price,
                profit_pct=profit_pct,
                activation_time=datetime.utcnow(),
                trail_price=trail_price
            )
            
            # Subscribe to real-time price monitoring for this trade
            await self.lifecycle_manager.subscribe_trade_price_monitoring(
                trade.id, trade.exchange, trade.symbol
            )
            
            # Let the trailing manager handle the actual order creation
            # (This will happen automatically in its monitoring cycle)
            
            # Track activation
            self.activated_trades[trade.id] = activation_event
            
            # Notify activation callbacks
            await self._notify_activation_callbacks(activation_event)
            
            # Update metrics
            self.metrics['activations_triggered'] += 1
            
            logger.info(f"✅ Trailing stop activation complete for trade {trade.id}")
            
        except Exception as e:
            logger.error(f"❌ Error triggering activation for trade {trade.id}: {e}")
            self.metrics['errors'] += 1
    
    async def _monitor_activated_trades(self):
        """Monitor activated trades for completion"""
        completed_trades = []
        
        for trade_id, activation_event in self.activated_trades.items():
            try:
                # Check if trade has been completed
                stop_data = self.lifecycle_manager.get_stop_data(trade_id)
                
                if stop_data and stop_data.state == TrailingStopState.FILLED:
                    logger.info(f"🎯 Trade {trade_id} completed via trailing stop")
                    
                    # Unsubscribe from price monitoring
                    await self.lifecycle_manager.unsubscribe_trade_price_monitoring(trade_id)
                    
                    # Mark for cleanup
                    completed_trades.append(trade_id)
                    
                    # Update metrics
                    self.metrics['orders_filled'] += 1
                    
                elif stop_data and stop_data.exit_id and activation_event.trade_id not in [t.trade_id for t in [activation_event]]:
                    # Order has been created
                    if trade_id not in [t for t in completed_trades]:
                        self.metrics['orders_created'] += 1
                        logger.debug(f"📋 Order created for trade {trade_id}: {stop_data.exit_id}")
                
            except Exception as e:
                logger.error(f"❌ Error monitoring activated trade {trade_id}: {e}")
                self.metrics['errors'] += 1
        
        # Clean up completed trades
        for trade_id in completed_trades:
            del self.activated_trades[trade_id]
    
    async def _notify_activation_callbacks(self, activation_event: ActivationEvent):
        """Notify registered activation callbacks"""
        for callback in self.activation_callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(activation_event)
                else:
                    callback(activation_event)
            except Exception as e:
                logger.error(f"❌ Error in activation callback: {e}")
    
    async def _handle_dashboard_price_update(self, price_update: PriceUpdate):
        """Handle price updates for dashboard integration"""
        try:
            # Forward to dashboard callbacks
            for callback in self.dashboard_callbacks:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(price_update)
                    else:
                        callback(price_update)
                except Exception as e:
                    logger.error(f"❌ Error in dashboard price callback: {e}")
            
            # Update metrics
            self.metrics['price_updates_processed'] += 1
            
        except Exception as e:
            logger.error(f"❌ Error handling dashboard price update: {e}")
    
    # Public Interface Methods
    
    def add_activation_callback(self, callback: Callable[[ActivationEvent], None]):
        """Add callback for activation events"""
        self.activation_callbacks.append(callback)
        logger.info(f"➕ Added activation callback (total: {len(self.activation_callbacks)})")
    
    def add_dashboard_callback(self, callback: Callable[[PriceUpdate], None]):
        """Add callback for dashboard price updates"""
        self.dashboard_callbacks.append(callback)
        logger.info(f"📊 Added dashboard callback (total: {len(self.dashboard_callbacks)})")
    
    def get_system_status(self) -> Dict[str, Any]:
        """Get comprehensive system status"""
        uptime = None
        if self.metrics['system_uptime_start']:
            uptime = (datetime.utcnow() - self.metrics['system_uptime_start']).total_seconds()
        
        return {
            'system_active': self.system_active,
            'initialization_complete': self.initialization_complete,
            'activation_threshold': self.activation_threshold,
            'activation_threshold_pct': f"{self.activation_threshold*100:.1f}%",
            'activated_trades_count': len(self.activated_trades),
            'uptime_seconds': uptime,
            'metrics': self.metrics.copy(),
            'lifecycle_manager_stats': self.lifecycle_manager.get_statistics(),
            'price_feed_status': self.price_feed.get_status(),
            'trailing_manager_stats': self.trailing_manager.get_statistics(),
            'activated_trades': {
                trade_id: {
                    'exchange': event.exchange,
                    'symbol': event.symbol,
                    'entry_price': event.entry_price,
                    'activation_price': event.current_price,
                    'profit_pct': event.profit_pct,
                    'trail_price': event.trail_price,
                    'activation_time': event.activation_time.isoformat()
                }
                for trade_id, event in self.activated_trades.items()
            }
        }
    
    def get_activated_trades(self) -> Dict[str, ActivationEvent]:
        """Get currently activated trades"""
        return self.activated_trades.copy()
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get system metrics"""
        return self.metrics.copy()

    async def is_healthy(self) -> bool:
        """Basic health check used by orchestrator supervision loop."""
        try:
            if not self.system_active or not self.initialization_complete:
                return False
            pf_status = self.price_feed.get_status() if self.price_feed else {}
            trailing_stats = self.trailing_manager.get_statistics() if self.trailing_manager else {}
            if trailing_stats.get("errors", 0) > 100:
                return False
            if pf_status and not pf_status.get("connected", True):
                # still healthy if poll-based manager is running
                return bool(self.trailing_manager)
            return True
        except Exception:
            return False
    
    async def force_check_activations(self):
        """Force immediate check for new activations (for testing/debugging)"""
        logger.info("🔄 Forcing activation check")
        await self._check_for_new_activations()
    
    async def get_trade_status(self, trade_id: str) -> Optional[Dict[str, Any]]:
        """Get detailed status for a specific trade"""
        if trade_id in self.activated_trades:
            activation_event = self.activated_trades[trade_id]
            stop_data = self.lifecycle_manager.get_stop_data(trade_id)
            
            return {
                'trade_id': trade_id,
                'activated': True,
                'activation_event': {
                    'exchange': activation_event.exchange,
                    'symbol': activation_event.symbol,
                    'entry_price': activation_event.entry_price,
                    'activation_price': activation_event.current_price,
                    'profit_pct': activation_event.profit_pct,
                    'trail_price': activation_event.trail_price,
                    'activation_time': activation_event.activation_time.isoformat()
                },
                'stop_data': {
                    'state': stop_data.state.value if stop_data else 'unknown',
                    'exit_id': stop_data.exit_id if stop_data else None,
                    'current_trail_price': stop_data.trail_price if stop_data else None,
                    'highest_price': stop_data.highest_price if stop_data else None,
                    'update_count': stop_data.update_count if stop_data else 0
                } if stop_data else None
            }
        
        return None
    
    def __repr__(self):
        """String representation"""
        status = "active" if self.system_active else "inactive"
        return f"ActivationTriggerSystem(status={status}, activated_trades={len(self.activated_trades)}, threshold={self.activation_threshold:.3f})"


# Factory Functions and Integration Helpers

async def create_activation_trigger_system(config: Dict[str, Any]) -> ActivationTriggerSystem:
    """
    Create and initialize the complete activation trigger system
    
    Args:
        config: Configuration dictionary from config.yaml
        
    Returns:
        Initialized ActivationTriggerSystem
    """
    
    system = ActivationTriggerSystem(config)
    logger.info("🏗️ ActivationTriggerSystem created and ready for startup")
    return system


def create_trailing_stop_system(config: Dict[str, Any]) -> ActivationTriggerSystem:
    """
    Main factory function to create the complete trailing stop system
    
    This is the primary entry point for integrating exchange-delegated
    trailing stops into the existing trading bot.
    
    Args:
        config: Configuration dictionary from config.yaml
        
    Returns:
        ActivationTriggerSystem ready for integration
        
    Usage:
        # In orchestrator or main trading service
        from strategy.activation_trigger_system import create_trailing_stop_system
        
        trailing_system = create_trailing_stop_system(config)
        await trailing_system.start()
        
        # Add callbacks for integration
        trailing_system.add_dashboard_callback(dashboard_price_handler)
        trailing_system.add_activation_callback(activation_event_handler)
    """
    
    return ActivationTriggerSystem(config)


# Production Integration Class

class TrailingStopSystemIntegration:
    """
    Production-ready integration wrapper for the trailing stop system
    
    This class provides a clean interface for integrating the trailing stop
    system with the existing trading bot architecture.
    """
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize with configuration"""
        self.config = config
        self.system: Optional[ActivationTriggerSystem] = None
        self.integration_active = False
    
    async def initialize(self):
        """Initialize the trailing stop system"""
        if self.system:
            logger.warning("TrailingStopSystemIntegration already initialized")
            return
        
        logger.info("🎯 Initializing trailing stop system integration")
        
        self.system = create_trailing_stop_system(self.config)
        await self.system.start()
        
        self.integration_active = True
        logger.info("✅ Trailing stop system integration ready")
    
    async def shutdown(self):
        """Shutdown the trailing stop system"""
        if self.system:
            await self.system.stop()
            self.system = None
        
        self.integration_active = False
        logger.info("✅ Trailing stop system integration shutdown complete")
    
    def add_dashboard_integration(self, callback):
        """Add dashboard price update callback"""
        if self.system:
            self.system.add_dashboard_callback(callback)
    
    def add_activation_monitoring(self, callback):
        """Add activation event monitoring callback"""
        if self.system:
            self.system.add_activation_callback(callback)
    
    def get_status(self) -> Dict[str, Any]:
        """Get integration status"""
        if self.system:
            return self.system.get_system_status()
        return {'integration_active': False}
    
    @property
    def is_active(self) -> bool:
        """Check if integration is active"""
        return self.integration_active and self.system and self.system.system_active


# Export main classes and functions
__all__ = [
    'ActivationTriggerSystem',
    'ActivationEvent', 
    'create_activation_trigger_system',
    'create_trailing_stop_system',
    'TrailingStopSystemIntegration'
]