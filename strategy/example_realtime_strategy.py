"""
Example Implementation: Enhanced PnL Strategy with Real-Time WebSocket Protection
Shows how to integrate real-time enhancements with existing strategy framework

This example demonstrates:
1. How to add real-time protection to any existing strategy
2. WebSocket-driven risk management integration
3. Fallback handling when WebSocket data is unavailable
4. Configuration and monitoring setup
"""

import logging
import asyncio
from datetime import datetime
from typing import Dict, Any, Optional, Tuple

# Import existing strategy framework
from base_strategy import BaseStrategy

# Import real-time integration
from realtime_integration import (
    integrate_realtime_protection,
    cleanup_realtime_resources,
    is_realtime_available,
    get_realtime_status,
    merge_realtime_config
)

# Import standard PnL functions for fallback
from strategy_pnl_enhanced import (
    calculate_unrealized_pnl,
    check_profit_protection_enhanced,
    manage_trailing_stop_enhanced
)

logger = logging.getLogger(__name__)

class EnhancedRealtimePnLStrategy(BaseStrategy):
    """
    Example strategy that demonstrates real-time WebSocket-driven enhancements
    to the existing PnL protection system
    """
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize strategy with real-time configuration"""
        
        # Merge real-time defaults with provided config
        enhanced_config = merge_realtime_config(config)
        super().__init__(enhanced_config)
        
        self.strategy_name = "enhanced_realtime_pnl"
        self.version = "1.0.0"
        
        # Real-time status tracking
        self.realtime_enabled = enhanced_config.get('realtime_protection', {}).get('enabled', True)
        self.realtime_status = None
        
        logger.info(f"🚀 {self.strategy_name} v{self.version} initialized (realtime: {self.realtime_enabled})")
        logger.info(f"📊 Real-time available: {is_realtime_available()}")
    
    async def check_exit_conditions(self, trade_data: Dict[str, Any]) -> Tuple[bool, Optional[str], Dict[str, Any]]:
        """
        Enhanced exit condition checking with real-time WebSocket protection
        
        This is the main integration point where real-time enhancements are applied
        """
        
        trade_id = trade_data.get('trade_id')
        exchange = trade_data.get('exchange')
        symbol = trade_data.get('symbol')  # e.g., 'BTC/USDC'
        current_price = float(trade_data.get('current_price', 0))
        entry_price = float(trade_data.get('entry_price', 0))
        position_size = float(trade_data.get('position_size', 0))
        ohlcv_data = trade_data.get('ohlcv_data')
        
        logger.debug(f"[Trade {trade_id}] Checking exit conditions - price=${current_price:.4f}")
        
        try:
            # Use integrated real-time protection
            should_exit, exit_reason, protection_data = await integrate_realtime_protection(
                state=self.state,
                trade_id=trade_id,
                exchange=exchange,
                symbol=symbol,
                current_price=current_price,
                entry_price=entry_price,
                position_size=position_size,
                config=self.config,
                ohlcv_data=ohlcv_data
            )
            
            # Update real-time status for monitoring
            self.realtime_status = get_realtime_status(self.state)
            
            # Log protection details
            protections_checked = protection_data.get('protections_checked', [])
            logger.info(f"[Trade {trade_id}] Protection checks: {', '.join(protections_checked)}")
            
            if should_exit:
                logger.info(f"🛑 [Trade {trade_id}] Exit triggered: {exit_reason}")
                
                # Log real-time data if available
                if 'realtime_protection' in protection_data:
                    rt_data = protection_data['realtime_protection']
                    if rt_data.get('volatility'):
                        logger.info(f"📊 [Trade {trade_id}] Real-time metrics - vol:{rt_data['volatility']:.3f}, momentum:{rt_data.get('momentum', 0):.2f}")
                
                return True, exit_reason, protection_data
            
            # Log monitoring status periodically
            if hasattr(self.state, 'last_status_log'):
                if (datetime.utcnow() - self.state.last_status_log).seconds > 30:  # Every 30 seconds
                    await self._log_monitoring_status(trade_id)
            else:
                self.state.last_status_log = datetime.utcnow()
            
            return False, None, protection_data
            
        except Exception as e:
            logger.error(f"❌ [Trade {trade_id}] Error in exit condition check: {e}")
            
            # Fallback to basic protection on error
            current_pnl_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0
            
            if current_pnl_pct <= -0.05:  # 5% emergency exit
                logger.warning(f"⚠️ [Trade {trade_id}] Emergency fallback exit at {current_pnl_pct:.2%}")
                return True, "emergency_fallback", {'error': str(e)}
            
            return False, None, {'error': str(e)}
    
    async def on_trade_opened(self, trade_data: Dict[str, Any]):
        """
        Called when a new trade is opened
        Initialize real-time monitoring
        """
        
        trade_id = trade_data.get('trade_id')
        exchange = trade_data.get('exchange')
        symbol = trade_data.get('symbol')
        
        logger.info(f"🟢 [Trade {trade_id}] Trade opened - initializing real-time protection")
        
        # Real-time protection will be initialized on first exit condition check
        # This allows lazy initialization to avoid unnecessary overhead
        
        # Log configuration for this trade
        if self.realtime_enabled and is_realtime_available():
            rt_config = self.config.get('realtime_protection', {})
            logger.info(f"📋 [Trade {trade_id}] Real-time config - check_interval:{rt_config.get('check_interval_seconds', 1)}s")
        
        await super().on_trade_opened(trade_data)
    
    async def on_trade_closed(self, trade_data: Dict[str, Any]):
        """
        Called when a trade is closed
        Clean up real-time monitoring resources
        """
        
        trade_id = trade_data.get('trade_id')
        exit_reason = trade_data.get('exit_reason', 'unknown')
        
        logger.info(f"🔴 [Trade {trade_id}] Trade closed - reason: {exit_reason}")
        
        # Clean up real-time resources
        try:
            await cleanup_realtime_resources(self.state)
            logger.info(f"🧹 [Trade {trade_id}] Real-time resources cleaned up")
        except Exception as e:
            logger.error(f"❌ [Trade {trade_id}] Error cleaning up real-time resources: {e}")
        
        await super().on_trade_closed(trade_data)
    
    async def _log_monitoring_status(self, trade_id: str):
        """Log current real-time monitoring status"""
        
        status = get_realtime_status(self.state)
        
        if status['monitor_active']:
            logger.info(f"📊 [Trade {trade_id}] Monitor status - active:{status['monitor_active']}, "
                       f"price:${status.get('current_price', 0):.4f}, "
                       f"vol:{status.get('volatility', 0):.3f}, "
                       f"momentum:{status.get('momentum', 0):.2f}")
        else:
            logger.debug(f"📊 [Trade {trade_id}] Monitor inactive")
        
        self.state.last_status_log = datetime.utcnow()
    
    def get_strategy_info(self) -> Dict[str, Any]:
        """Get strategy information including real-time status"""
        
        base_info = super().get_strategy_info()
        
        # Add real-time specific information
        base_info.update({
            'realtime_enabled': self.realtime_enabled,
            'realtime_available': is_realtime_available(),
            'realtime_status': self.realtime_status,
            'features': [
                'Real-time WebSocket price monitoring',
                'Flash crash detection (1s/5s/30s)',
                'Micro-drawdown protection (0.3%)',
                'Volatility-based trailing stops',
                'Momentum-adjusted risk management',
                'Order book liquidity analysis',
                'Emergency exit mechanisms',
                'Enhanced ATR/ADX analysis'
            ]
        })
        
        return base_info

# Example configuration for the enhanced strategy
EXAMPLE_CONFIG = {
    'strategy': 'enhanced_realtime_pnl',
    'version': '1.0.0',
    
    # Standard trading configuration
    'trading': {
        'profit_protection': {
            'activation_threshold': 0.01,      # 1% profit to activate
            'max_drawdown': 0.30,              # 30% max drawdown from peak
            'consecutive_threshold': 8          # 8 consecutive decreases
        },
        'trailing_stop': {
            'activation_threshold': 0.03,      # 3% profit to activate
            'base_trailing_distance': 0.015    # 1.5% trailing distance
        }
    },
    
    # Real-time enhancements
    'realtime_protection': {
        'enabled': True,
        'check_interval_seconds': 1.0,        # Check every second
        'flash_crash_thresholds': {
            '1s': 0.02,                        # 2% drop in 1 second
            '5s': 0.05,                        # 5% drop in 5 seconds
            '30s': 0.08                        # 8% drop in 30 seconds
        },
        'micro_drawdown_threshold': 0.003,    # 0.3% micro-drawdown
        'volatility_exit_threshold': 0.01,    # 1% volatility exit
        'liquidity_threshold': 0.3            # 30% min liquidity score
    },
    
    'realtime_trailing': {
        'enabled': True,
        'base_distance': 0.015,               # 1.5% base distance
        'volatility_adjustment': True,         # Adjust for volatility
        'momentum_adjustment': True,           # Adjust for momentum
        'min_distance': 0.005,                # 0.5% minimum
        'max_distance': 0.05                  # 5% maximum
    }
}

# Example usage function
async def example_usage():
    """
    Example of how to use the enhanced real-time strategy
    """
    
    print("🚀 Enhanced Real-Time PnL Strategy Example")
    print(f"📊 Real-time capabilities available: {is_realtime_available()}")
    
    # Initialize strategy
    strategy = EnhancedRealtimePnLStrategy(EXAMPLE_CONFIG)
    
    # Example trade data
    trade_data = {
        'trade_id': 'example-123',
        'exchange': 'binance',
        'symbol': 'BTC/USDC',
        'current_price': 65000.0,
        'entry_price': 64500.0,
        'position_size': 0.1,
        'ohlcv_data': None  # Would contain actual OHLCV data
    }
    
    # Simulate trade opening
    await strategy.on_trade_opened(trade_data)
    
    # Simulate exit condition checks
    for i in range(3):
        print(f"\n--- Check {i+1} ---")
        
        # Simulate price movement
        trade_data['current_price'] += 100 * (i - 1)  # Some price movement
        
        should_exit, reason, data = await strategy.check_exit_conditions(trade_data)
        
        print(f"Exit: {should_exit}, Reason: {reason}")
        print(f"Protections checked: {data.get('protections_checked', [])}")
        
        if should_exit:
            break
        
        await asyncio.sleep(1)  # Wait 1 second
    
    # Simulate trade closing
    await strategy.on_trade_closed({**trade_data, 'exit_reason': reason or 'manual'})
    
    print("\n📋 Strategy Info:")
    info = strategy.get_strategy_info()
    for key, value in info.items():
        if key == 'features':
            print(f"  {key}:")
            for feature in value:
                print(f"    - {feature}")
        else:
            print(f"  {key}: {value}")

if __name__ == "__main__":
    # Run example
    asyncio.run(example_usage())