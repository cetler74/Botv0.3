"""
Real-Time Integration Module for Enhanced PnL Strategy
Seamlessly integrates WebSocket-driven enhancements with existing strategy framework

Usage:
    from strategy.realtime_integration import integrate_realtime_protection
    
    # In your strategy's check_exit_conditions method:
    should_exit, reason = await integrate_realtime_protection(
        state=self.state,
        trade_id=trade_id,
        exchange=exchange,
        symbol=symbol,
        current_price=current_price,
        entry_price=entry_price,
        position_size=position_size,
        config=self.config
    )
"""

import asyncio
import logging
from typing import Dict, Any, Optional, Tuple
from datetime import datetime

try:
    from strategy_pnl_enhanced_realtime import (
        enhanced_profit_protection_realtime,
        enhanced_trailing_stop_realtime,
        RealTimeProtectionManager
    )
    REALTIME_AVAILABLE = True
except ImportError:
    REALTIME_AVAILABLE = False
    logging.warning("Real-time enhancements not available - falling back to standard protection")

# Import standard protection functions as fallbacks
from strategy_pnl_enhanced import (
    check_profit_protection_enhanced,
    manage_trailing_stop_enhanced
)

logger = logging.getLogger(__name__)

async def integrate_realtime_protection(
    state, trade_id: str, exchange: str, symbol: str,
    current_price: float, entry_price: float, position_size: float,
    config: Dict[str, Any], ohlcv_data=None
) -> Tuple[bool, Optional[str], Dict[str, Any]]:
    """
    Integrated real-time protection that combines WebSocket enhancements with standard protection
    
    Priority Order:
    1. Real-time emergency exits (flash crashes, micro-drawdowns)
    2. Real-time trailing stops (momentum-adjusted)  
    3. Standard enhanced protection (ATR/ADX based)
    4. Fallback protection (basic thresholds)
    
    Args:
        state: Strategy state object
        trade_id: Trade identifier
        exchange: Exchange name (e.g., 'binance')
        symbol: Trading symbol (e.g., 'BTC/USDC')
        current_price: Current market price
        entry_price: Position entry price
        position_size: Position size
        config: Configuration dictionary
        ohlcv_data: OHLCV data for standard protection
    
    Returns:
        Tuple of (should_exit, exit_reason, protection_data)
    """
    
    protection_data = {
        'timestamp': datetime.utcnow().isoformat(),
        'realtime_enabled': REALTIME_AVAILABLE,
        'protections_checked': []
    }
    
    logger.debug(f"[Trade {trade_id}] Starting integrated protection check")
    
    # Phase 1: Real-time emergency protection (if available)
    if REALTIME_AVAILABLE:
        try:
            should_exit, reason, rt_data = await enhanced_profit_protection_realtime(
                state=state,
                trade_id=trade_id,
                exchange=exchange,
                symbol=symbol,
                entry_price=entry_price,
                position_size=position_size,
                config=config
            )
            
            protection_data['realtime_protection'] = rt_data
            protection_data['protections_checked'].append('realtime_emergency')
            
            if should_exit:
                logger.info(f"🚨 [Trade {trade_id}] Real-time emergency exit: {reason}")
                return True, f"realtime_{reason}", protection_data
                
        except Exception as e:
            logger.error(f"❌ [Trade {trade_id}] Real-time protection error: {e}")
            # Continue with standard protection if real-time fails
    
    # Phase 2: Real-time trailing stops (if available and no emergency)
    if REALTIME_AVAILABLE:
        try:
            should_exit, reason, rt_trailing_data = enhanced_trailing_stop_realtime(
                state=state,
                current_price=current_price,
                entry_price=entry_price,
                position_size=position_size,
                config=config,
                trade_id=trade_id,
                exchange=exchange,
                symbol=symbol
            )
            
            protection_data['realtime_trailing'] = rt_trailing_data
            protection_data['protections_checked'].append('realtime_trailing')
            
            if should_exit:
                logger.info(f"🎯 [Trade {trade_id}] Real-time trailing stop: {reason}")
                return True, f"realtime_{reason}", protection_data
                
        except Exception as e:
            logger.error(f"❌ [Trade {trade_id}] Real-time trailing stop error: {e}")
    
    # Phase 3: Standard enhanced protection (ATR/ADX based)
    try:
        # Calculate current unrealized PnL
        from strategy_pnl_enhanced import calculate_unrealized_pnl
        
        # Create a position object for PnL calculation
        class PositionProxy:
            def __init__(self, entry_price, position_size, position_type='long'):
                self.entry_price = entry_price
                self.position_size = position_size
                self.position = position_type
                self.entry_fee_amount = 0.0  # Will be filled if available
                self.exit_fee_amount = 0.0
        
        position_proxy = PositionProxy(entry_price, position_size)
        unrealized_pnl = calculate_unrealized_pnl(
            position=position_proxy,
            current_price=current_price,
            trade_id=trade_id
        )
        
        # Enhanced profit protection check
        should_exit, reason, enhanced_data = check_profit_protection_enhanced(
            state=state,
            unrealized_pnl=unrealized_pnl,
            entry_price=entry_price,
            position_size=position_size,
            config=config,
            ohlcv_data=ohlcv_data,
            trade_id=trade_id,
            current_price=current_price,
            pair=symbol
        )
        
        protection_data['enhanced_protection'] = enhanced_data
        protection_data['protections_checked'].append('enhanced_profit_protection')
        
        if should_exit:
            logger.info(f"🛡️ [Trade {trade_id}] Enhanced protection exit: {reason}")
            return True, f"enhanced_{reason}", protection_data
        
        # Enhanced trailing stop check
        should_exit, reason, trailing_data = manage_trailing_stop_enhanced(
            state=state,
            current_price=current_price,
            entry_price=entry_price,
            position_size=position_size,
            config=config,
            ohlcv_data=ohlcv_data,
            trade_id=trade_id,
            pair=symbol
        )
        
        protection_data['enhanced_trailing'] = trailing_data
        protection_data['protections_checked'].append('enhanced_trailing_stop')
        
        if should_exit:
            logger.info(f"🎯 [Trade {trade_id}] Enhanced trailing stop: {reason}")
            return True, f"enhanced_{reason}", protection_data
            
    except Exception as e:
        logger.error(f"❌ [Trade {trade_id}] Enhanced protection error: {e}")
        
        # Phase 4: Fallback basic protection
        try:
            current_pnl_pct = (current_price - entry_price) / entry_price
            
            # Basic emergency exits
            if current_pnl_pct <= -0.05:  # 5% loss emergency exit
                logger.warning(f"⚠️ [Trade {trade_id}] Basic emergency exit at {current_pnl_pct:.2%}")
                protection_data['protections_checked'].append('basic_emergency')
                return True, "basic_emergency_exit", protection_data
            
            protection_data['protections_checked'].append('basic_fallback')
            
        except Exception as fallback_error:
            logger.error(f"❌ [Trade {trade_id}] Even fallback protection failed: {fallback_error}")
    
    # No exit conditions met
    logger.debug(f"[Trade {trade_id}] All protection checks passed - continuing trade")
    return False, None, protection_data

async def cleanup_realtime_resources(state):
    """
    Clean up real-time monitoring resources when trade is closed
    
    Args:
        state: Strategy state object
    """
    
    if hasattr(state, 'realtime_monitor') and state.realtime_monitor:
        try:
            await state.realtime_monitor.stop_monitoring()
            logger.info("🧹 Real-time monitoring resources cleaned up")
        except Exception as e:
            logger.error(f"❌ Error cleaning up real-time resources: {e}")

def is_realtime_available() -> bool:
    """Check if real-time enhancements are available"""
    return REALTIME_AVAILABLE

def get_realtime_status(state) -> Dict[str, Any]:
    """
    Get current real-time monitoring status
    
    Args:
        state: Strategy state object
    
    Returns:
        Dictionary with monitoring status information
    """
    
    status = {
        'realtime_available': REALTIME_AVAILABLE,
        'monitor_active': False,
        'last_check': None,
        'protection_triggered': False
    }
    
    if hasattr(state, 'realtime_monitor') and state.realtime_monitor:
        status.update({
            'monitor_active': state.realtime_monitor.is_active,
            'last_check': state.realtime_monitor.last_check,
            'protection_triggered': state.realtime_monitor.protection_triggered
        })
        
        if hasattr(state.realtime_monitor, 'price_monitor'):
            status['current_price'] = state.realtime_monitor.price_monitor.current_price
            status['volatility'] = state.realtime_monitor.price_monitor.get_current_volatility()
            status['momentum'] = state.realtime_monitor.price_monitor.get_momentum_score()
    
    return status

# Configuration helpers
def get_realtime_config_template() -> Dict[str, Any]:
    """
    Get template configuration for real-time protection
    
    Returns:
        Dictionary with recommended real-time configuration
    """
    
    return {
        'realtime_protection': {
            'enabled': True,
            'check_interval_seconds': 1.0,
            'flash_crash_thresholds': {
                '1s': 0.02,   # 2% drop in 1 second
                '5s': 0.05,   # 5% drop in 5 seconds  
                '30s': 0.08   # 8% drop in 30 seconds
            },
            'micro_drawdown_threshold': 0.003,  # 0.3%
            'volatility_exit_threshold': 0.01,   # 1%
            'liquidity_threshold': 0.3           # 30% liquidity score minimum
        },
        'realtime_trailing': {
            'enabled': True,
            'base_distance': 0.015,              # 1.5% base trailing distance
            'volatility_adjustment': True,        # Adjust based on real-time volatility
            'momentum_adjustment': True,          # Adjust based on momentum
            'min_distance': 0.005,               # 0.5% minimum distance
            'max_distance': 0.05                 # 5% maximum distance
        }
    }

def merge_realtime_config(base_config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merge real-time configuration with base configuration
    
    Args:
        base_config: Base strategy configuration
    
    Returns:
        Merged configuration with real-time defaults
    """
    
    merged = base_config.copy()
    template = get_realtime_config_template()
    
    # Merge real-time sections if they don't exist
    if 'realtime_protection' not in merged:
        merged['realtime_protection'] = template['realtime_protection']
    
    if 'realtime_trailing' not in merged:
        merged['realtime_trailing'] = template['realtime_trailing']
    
    return merged

# Export main integration functions
__all__ = [
    'integrate_realtime_protection',
    'cleanup_realtime_resources',
    'is_realtime_available',
    'get_realtime_status',
    'get_realtime_config_template',
    'merge_realtime_config'
]