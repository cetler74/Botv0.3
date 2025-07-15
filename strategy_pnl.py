import logging
import pandas as pd
from datetime import datetime
import numpy as np

def calculate_atr(ohlcv_data, period=14):
    """
    Calculate Average True Range for volatility-based adjustments.

    Args:
        ohlcv_data: DataFrame with OHLCV data
        period: ATR calculation period

    Returns:
        ATR value
    """
    if len(ohlcv_data) < period:
        return None

    try:
        high_low = ohlcv_data['high'] - ohlcv_data['low']
        high_close = abs(ohlcv_data['high'] - ohlcv_data['close'].shift(1))
        low_close = abs(ohlcv_data['low'] - ohlcv_data['close'].shift(1))

        # Ensure we have pandas Series and calculate true range properly
        tr_data = pd.DataFrame({
            'hl': high_low,
            'hc': high_close,
            'lc': low_close
        })
        
        true_range = tr_data.max(axis=1)
        atr = true_range.rolling(window=period).mean()

        # Get the last value safely - simplified approach
        if len(atr) == 0:
            return None
            
        # Convert to list and get last value to avoid type issues
        atr_list = atr.tolist()
        if not atr_list:
            return None
            
        last_value = atr_list[-1]
        
        # Check if the value is valid
        if last_value is None:
            return None
            
        # Convert to float safely
        try:
            float_value = float(last_value)
            # Check for NaN using simple comparison
            if float_value != float_value:  # NaN check
                return None
            return float_value
        except (ValueError, TypeError):
            return None
            
    except Exception:
        return None

def calculate_volatility_adjustment(atr_value, current_price):
    """
    Calculate volatility adjustment multiplier based on ATR.
    
    Args:
        atr_value: Average True Range value
        current_price: Current asset price
        
    Returns:
        Volatility adjustment multiplier (0.8 to 1.4)
    """
    if not atr_value or current_price <= 0:
        return 1.0
    
    # Calculate volatility as ATR percentage of current price
    volatility_pct = atr_value / current_price
    
    # Adjust thresholds based on volatility
    if volatility_pct > 0.05:  # High volatility (>5%)
        return 1.4
    elif volatility_pct > 0.03:  # Medium volatility (3-5%)
        return 1.2
    elif volatility_pct < 0.015:  # Low volatility (<1.5%)
        return 0.8
    else:
        return 1.0  # Normal volatility

def _validate_position_for_pnl(position):
    """
    Validate that a position object has all required fields for PnL calculation.
    
    Args:
        position: Position object or state
        
    Returns:
        Tuple of (is_valid, missing_fields)
    """
    # Required fields for PnL calculation (position OR side required)
    required_fields = ['entry_price', 'position_size']
    missing_fields = []
    
    # Check basic required fields
    for field in required_fields:
        if not hasattr(position, field):
            missing_fields.append(field)
        elif getattr(position, field) is None:
            missing_fields.append(field)
        elif field == 'entry_price' and float(getattr(position, field)) == 0.0:
            missing_fields.append('entry_price_zero')
        elif field == 'position_size' and float(getattr(position, field)) == 0.0:
            missing_fields.append('position_size_zero')
    
    # Check position/side field - need at least one
    has_position = hasattr(position, 'position') and getattr(position, 'position') is not None
    has_side = hasattr(position, 'side') and getattr(position, 'side') is not None
    
    if not has_position and not has_side:
        missing_fields.append('position_or_side')
    elif has_position:
        # Validate position field values
        position_value = getattr(position, 'position').lower() if getattr(position, 'position') else ''
        if position_value not in ['long', 'short', 'none', 'buy', 'sell']:
            missing_fields.append('valid_position_value')
        elif position_value == 'none':
            missing_fields.append('position_none')
    elif has_side:
        # Validate side field values  
        side_value = getattr(position, 'side').lower() if getattr(position, 'side') else ''
        if side_value not in ['long', 'short', 'buy', 'sell']:
            missing_fields.append('valid_side_value')
            
    # Return validation result
    is_valid = len(missing_fields) == 0
    return is_valid, missing_fields

def calculate_unrealized_pnl(position, current_price, trade_id=None):
    """
    Calculate unrealized PnL for a position.
    For spot market trading, we only handle long positions.
    
    Args:
        position: Position object with entry_price, position_size, position/side fields
        current_price: Current price of the asset
        trade_id: Optional trade ID for logging
        
    Returns:
        Unrealized PnL or 0 if calculation isn't possible
    """
    logger = logging.getLogger(__name__)
    
    # CRITICAL: Add debug logging to track function entry
    if trade_id:
        logger.info(f"[Trade {trade_id}] [PnL] Starting PnL calculation - current_price={current_price}")
    
    # Validate position object
    is_valid, missing_fields = _validate_position_for_pnl(position)
    if not is_valid:
        if trade_id:
            logger.warning(f"[Trade {trade_id}] [PnL] Cannot calculate unrealized PnL: missing fields {missing_fields}")
        return 0.0
        
    # Ensure values are numbers
    try:
        entry_price = float(position.entry_price)
        position_size = float(position.position_size)
        current_price = float(current_price)
        
        if trade_id:
            logger.info(f"[Trade {trade_id}] [PnL] Validated values - entry_price={entry_price}, position_size={position_size}, current_price={current_price}")
            
    except (TypeError, ValueError) as e:
        if trade_id:
            logger.warning(f"[Trade {trade_id}] [PnL] Cannot calculate unrealized PnL: {e}")
        return 0.0
        
    # Get position type (handle both 'position' and 'side' fields)
    position_type = getattr(position, 'position', None)
    if position_type is None:
        position_type = getattr(position, 'side', None)
    
    # Normalize position type to handle case-insensitive comparison
    if position_type:
        position_type = position_type.lower()
    
    if trade_id:
        logger.info(f"[Trade {trade_id}] [PnL] Position type: {position_type}")
    
    # For spot market, we only handle long/buy positions (map both formats)
    valid_long_positions = ['long', 'buy']
    if position_type not in valid_long_positions:
        if trade_id:
            logger.warning(f"[Trade {trade_id}] [PnL] Invalid position type for spot market: {position_type}")
        return 0.0
        
    # Calculate unrealized PnL for long position
    # For SPOT trading, PnL = (current_price - entry_price) * position_size
    unrealized_pnl = (current_price - entry_price) * position_size
    
    # Calculate PnL percentage for logging
    pnl_percentage = (current_price - entry_price) / entry_price * 100 if entry_price != 0 else 0
        
    # Log the calculation with position type and percentage
    if trade_id:
        logger.info(f"[Trade {trade_id}] [PnL] Calculated unrealized PnL: {unrealized_pnl:.5f} ({pnl_percentage:.2f}%) (current={current_price:.5f}, entry={entry_price:.5f}, size={position_size})")
        
    return unrealized_pnl

def check_profit_protection_enhanced(state, unrealized_pnl, entry_price, position_size, 
                                   config, ohlcv_data=None, trade_id=None, logger=None, 
                                   current_price=None):
    """
    Enhanced profit protection with crypto-optimized parameters and volatility adjustments.
    
    Args:
        state: Strategy state with profit protection tracking
        unrealized_pnl: Current unrealized PnL
        entry_price: Entry price of the position
        position_size: Size of the position
        config: Config object with trading parameters
        ohlcv_data: OHLCV data for volatility calculations (optional)
        trade_id: Optional trade ID for logging
        logger: Optional logger instance
        current_price: Current price (for logging)
        
    Returns:
        Tuple of (should_exit, reason, risk_data)
    """
    if not logger:
        import logging
        logger = logging.getLogger(__name__)
    
    # CRITICAL: Add debug logging to track function entry
    if trade_id:
        logger.info(f"[Trade {trade_id}] [ProfitProtection] Starting profit protection check - unrealized_pnl={unrealized_pnl}, entry_price={entry_price}, position_size={position_size}")
    
    # Defensive checks for None values
    if entry_price is None or position_size is None or unrealized_pnl is None:
        logger.warning(f"[Trade {trade_id}] [ProfitProtection] Skipping check: entry_price={entry_price}, position_size={position_size}, unrealized_pnl={unrealized_pnl}")
        return False, None, {'profit_protection_active': False}
    
    # Defensive: ensure current_price is not None if used
    if current_price is None:
        current_price = entry_price
    
    # Get crypto-optimized configuration settings from config.yaml structure
    try:
        # Initialize defaults
        threshold = 0.01  # 1.0% (changed from 0.5%)
        max_drawdown = 0.30  # 30%
        consecutive_threshold = 8  # 8 decreases
        significant_decrease_multiplier = 0.002  # 0.2%
        
        # Pattern 1: config.trading (for yaml-based config)
        if hasattr(config, 'trading') and config.trading:
            trading_config = config.trading
            threshold = float(getattr(trading_config, 'profit_protection_threshold', threshold))
            max_drawdown = float(getattr(trading_config, 'profit_protection_max_drawdown', max_drawdown))
            consecutive_threshold = int(getattr(trading_config, 'profit_protection_consecutive_threshold', consecutive_threshold))
            significant_decrease_multiplier = float(getattr(trading_config, 'significant_decrease_multiplier', significant_decrease_multiplier))
        
        # Pattern 2: Direct dictionary access (for dict-based config)
        elif hasattr(config, '__getitem__'):
            trading_config = config.get('trading', {})
            threshold = float(trading_config.get('profit_protection_threshold', threshold))
            max_drawdown = float(trading_config.get('profit_protection_max_drawdown', max_drawdown))
            consecutive_threshold = int(trading_config.get('profit_protection_consecutive_threshold', consecutive_threshold))
            significant_decrease_multiplier = float(trading_config.get('significant_decrease_multiplier', significant_decrease_multiplier))
        
        # Pattern 3: Direct config attributes (fallback)
        else:
            threshold = float(getattr(config, 'profit_protection_threshold', threshold))
            max_drawdown = float(getattr(config, 'profit_protection_max_drawdown', max_drawdown))
            consecutive_threshold = int(getattr(config, 'profit_protection_consecutive_threshold', consecutive_threshold))
            significant_decrease_multiplier = float(getattr(config, 'significant_decrease_multiplier', significant_decrease_multiplier))
        
        if trade_id:
            logger.info(f"[Trade {trade_id}] [ProfitProtection] Config loaded - threshold={threshold:.3f}, max_drawdown={max_drawdown:.3f}, consecutive_threshold={consecutive_threshold}, significant_decrease_multiplier={significant_decrease_multiplier}")
            
    except Exception as e:
        logger.warning(f"[Trade {trade_id}] [ProfitProtection] Error getting settings: {e}. Using crypto-optimized defaults.")
        threshold = 0.01  # 1.0% (changed from 0.5%)
        max_drawdown = 0.30  # 30%
        consecutive_threshold = 8
        significant_decrease_multiplier = 0.002
    
    # Calculate volatility adjustment if OHLCV data available
    volatility_adjustment = 1.0
    atr_value = None
    if ohlcv_data is not None and len(ohlcv_data) >= 14:
        try:
            atr_value = calculate_atr(ohlcv_data)
            if atr_value and current_price > 0:
                volatility_adjustment = calculate_volatility_adjustment(atr_value, current_price)
                logger.info(f"[Trade {trade_id}] [ProfitProtection] Volatility adjustment: {volatility_adjustment:.2f} (ATR: {atr_value:.5f})")
        except Exception as e:
            logger.warning(f"[Trade {trade_id}] [ProfitProtection] Error calculating volatility adjustment: {e}")

    # Apply volatility adjustments to thresholds
    adjusted_max_drawdown = max_drawdown * volatility_adjustment
    adjusted_consecutive_threshold = int(consecutive_threshold * volatility_adjustment)

    # Calculate PnL percentage
    pnl_percentage = (current_price - entry_price) / entry_price if entry_price != 0 else 0
    
    if trade_id:
        logger.info(f"[Trade {trade_id}] [ProfitProtection] PnL percentage: {pnl_percentage:.2%}, volatility_adjustment: {volatility_adjustment:.2f}")
    
    # Initialize state attributes if they don't exist
    try:
        if not hasattr(state, 'profit_protection_active'):
            state.profit_protection_active = False
        if not hasattr(state, 'peak_unrealized_pnl'):
            state.peak_unrealized_pnl = 0.0
        if not hasattr(state, 'consecutive_decreases'):
            state.consecutive_decreases = 0
        if not hasattr(state, 'last_unrealized_pnl'):
            state.last_unrealized_pnl = unrealized_pnl
        if not hasattr(state, 'profit_protection_activated_at'):
            state.profit_protection_activated_at = None
        if not hasattr(state, 'volatility_adjusted_settings'):
            state.volatility_adjusted_settings = {}
        
        if trade_id:
            logger.info(f"[Trade {trade_id}] [ProfitProtection] State initialized successfully")
    except Exception as e:
        logger.error(f"[Trade {trade_id}] [ProfitProtection] Error initializing state: {e}")
        return False, None, {'profit_protection_active': False}

    # Store current volatility adjustments for monitoring
    try:
        state.volatility_adjusted_settings = {
            'max_drawdown': adjusted_max_drawdown,
            'consecutive_threshold': adjusted_consecutive_threshold,
            'volatility_adjustment': volatility_adjustment,
            'atr_value': atr_value
        }
    except Exception as e:
        logger.error(f"[Trade {trade_id}] [ProfitProtection] Error storing volatility settings: {e}")
        
    # Track maximum PnL (only when it increases)
    try:
        # Initialize peak with entry point (0 PnL) if this is the first calculation
        if not hasattr(state, 'peak_unrealized_pnl') or state.peak_unrealized_pnl == 0.0:
            # For the first calculation, set peak to 0 (entry point)
            if not hasattr(state, 'peak_initialized'):
                state.peak_unrealized_pnl = 0.0
                state.peak_initialized = True
                logger.info(f"[Trade {trade_id}] [ProfitProtection] Initialized peak PnL to entry point: 0.0")
        
        # Update peak only when PnL increases (becomes more positive)
        if unrealized_pnl > state.peak_unrealized_pnl:
            old_peak = state.peak_unrealized_pnl
            state.peak_unrealized_pnl = unrealized_pnl
            logger.info(f"[Trade {trade_id}] [ProfitProtection] New peak unrealized PnL: {old_peak:.5f} -> {state.peak_unrealized_pnl:.5f}")
    except Exception as e:
        logger.error(f"[Trade {trade_id}] [ProfitProtection] Error tracking peak PnL: {e}")
        
    # Calculate current drawdown from peak
    try:
        # Drawdown calculation: how much we've lost from our peak (or entry point)
        if state.peak_unrealized_pnl >= 0:
            # If we're at or below entry point (peak = 0), drawdown is the absolute loss
            if state.peak_unrealized_pnl == 0.0:
                current_drawdown = abs(unrealized_pnl) / abs(entry_price * position_size) if entry_price * position_size > 0 else 0
            else:
                # If we had profits, drawdown is the percentage loss from peak
                current_drawdown = (state.peak_unrealized_pnl - unrealized_pnl) / abs(state.peak_unrealized_pnl)
            # Ensure drawdown is non-negative (can't have negative drawdown)
            current_drawdown = max(0, current_drawdown)
        else:
            # If peak is negative, we're in a losing position from entry
            current_drawdown = 0
    except Exception as e:
        logger.error(f"[Trade {trade_id}] [ProfitProtection] Error calculating drawdown: {e}")
        current_drawdown = 0
        
    # Enhanced consecutive decreases logic with volatility-based threshold
    try:
        pnl_change = unrealized_pnl - state.last_unrealized_pnl
        significant_decrease_threshold = abs(entry_price * position_size * significant_decrease_multiplier)

        # Adjust threshold based on volatility (use ATR-based threshold if available)
        if atr_value:
            atr_threshold = atr_value * position_size
            significant_decrease_threshold = max(significant_decrease_threshold, atr_threshold * 0.5)
        
        if pnl_change < -significant_decrease_threshold:  # Significant decrease
            state.consecutive_decreases += 1
            logger.info(f"[Trade {trade_id}] [ProfitProtection] PnL decreased significantly: {pnl_change:.5f}, consecutive_decreases now: {state.consecutive_decreases}")
        elif pnl_change > significant_decrease_threshold:  # Significant increase
            # Reset consecutive decreases on significant increase
            if state.consecutive_decreases > 0:
                logger.info(f"[Trade {trade_id}] [ProfitProtection] PnL increased significantly: {pnl_change:.5f}, resetting consecutive_decreases from {state.consecutive_decreases} to 0")
            state.consecutive_decreases = 0
        # If change is insignificant (between thresholds), don't modify consecutive_decreases
        
        # Always update last_unrealized_pnl for next comparison
        state.last_unrealized_pnl = unrealized_pnl
    except Exception as e:
        logger.error(f"[Trade {trade_id}] [ProfitProtection] Error processing consecutive decreases: {e}")
    
    # Check if profit protection should be activated
    try:
        if not state.profit_protection_active and pnl_percentage >= threshold:
            state.profit_protection_active = True
            state.profit_protection_activated_at = datetime.utcnow()
            logger.info(f"[Trade {trade_id}] [ProfitProtection] Profit protection activated at {pnl_percentage:.2f}%")
    except Exception as e:
        logger.error(f"[Trade {trade_id}] [ProfitProtection] Error checking profit protection activation: {e}")
    
    # Log current state for debugging
    try:
        if trade_id:
            logger.info(f"[Trade {trade_id}] [ProfitProtection] State - active={state.profit_protection_active}, peak_pnl={state.peak_unrealized_pnl:.5f}, consecutive_decreases={state.consecutive_decreases}, drawdown={current_drawdown:.2%}")
    except Exception as e:
        logger.error(f"[Trade {trade_id}] [ProfitProtection] Error logging state: {e}")
    
    # Check exit conditions with volatility-adjusted thresholds
    try:
        if state.profit_protection_active:
            # Check drawdown exit
            if current_drawdown >= adjusted_max_drawdown:
                logger.info(f"[Trade {trade_id}] [ProfitProtection] Exit triggered: drawdown {current_drawdown:.2%} >= {adjusted_max_drawdown:.2%} (volatility adj: {volatility_adjustment:.2f})")
                return True, "volatility_adjusted_drawdown", {
                    'profit_protection_active': True,
                    'current_drawdown': current_drawdown,
                    'max_drawdown': adjusted_max_drawdown,
                    'pnl_percentage': pnl_percentage,
                    'volatility_adjustment': volatility_adjustment,
                    'atr_value': atr_value
                }

            # Check consecutive decreases exit
            if state.consecutive_decreases >= adjusted_consecutive_threshold:
                logger.info(f"[Trade {trade_id}] [ProfitProtection] Exit triggered: {state.consecutive_decreases} consecutive decreases >= {adjusted_consecutive_threshold} (volatility adj: {volatility_adjustment:.2f})")
                return True, "volatility_adjusted_consecutive_decreases", {
                    'profit_protection_active': True,
                    'consecutive_decreases': state.consecutive_decreases,
                    'threshold': adjusted_consecutive_threshold,
                    'pnl_percentage': pnl_percentage,
                    'volatility_adjustment': volatility_adjustment
                }
    except Exception as e:
        logger.error(f"[Trade {trade_id}] [ProfitProtection] Error checking exit conditions: {e}")
    
    return False, None, {
        'profit_protection_active': state.profit_protection_active,
        'current_drawdown': current_drawdown,
        'consecutive_decreases': state.consecutive_decreases,
        'pnl_percentage': pnl_percentage,
        'volatility_adjustment': volatility_adjustment,
        'adjusted_max_drawdown': adjusted_max_drawdown,
        'adjusted_consecutive_threshold': adjusted_consecutive_threshold,
        'atr_value': atr_value
    }

def check_profit_protection(state, unrealized_pnl, entry_price, position_size, config, trade_id=None, logger=None, current_price=None):
    """
    Backward compatibility wrapper for check_profit_protection_enhanced.
    Calls the enhanced version without OHLCV data.
    """
    return check_profit_protection_enhanced(
        state=state,
        unrealized_pnl=unrealized_pnl,
        entry_price=entry_price,
        position_size=position_size,
        config=config,
        ohlcv_data=None,
        trade_id=trade_id,
        logger=logger,
        current_price=current_price
    )

def manage_trailing_stop_enhanced(state, current_price, entry_price, position_size, 
                                config, ohlcv_data, trade_id, logger=None):
    """
    Enhanced trailing stop with crypto-optimized parameters and ATR-based distances.
    
    Args:
        state: Position state object
        current_price: Current market price
        entry_price: Position entry price
        position_size: Size of the position
        config: Configuration object
        ohlcv_data: OHLCV data for ATR calculation
        trade_id: Trade identifier (required)
        logger: Optional logger instance
    """
    logger = logger or logging.getLogger(__name__)
    if trade_id is None:
        logger.error("Trade ID is required for trailing stop management")
        return False, None, {}
    
    # CRITICAL: Add debug logging to track function entry
    logger.info(f"[Trade {trade_id}] [TrailingStop] Starting trailing stop check - current_price={current_price}, entry_price={entry_price}")
    
    # Get crypto-optimized trailing stop configuration from config.yaml structure
    try:
        # Initialize defaults
        activation_threshold_pct = 0.03  # 3%
        base_trailing_distance_pct = 0.15  # 15%
        
        # Pattern 1: config.trading (for yaml-based config)
        if hasattr(config, 'trading') and config.trading:
            trading_config = config.trading
            activation_threshold_pct = float(getattr(trading_config, 'trailing_stop_activation', activation_threshold_pct))
            base_trailing_distance_pct = float(getattr(trading_config, 'trailing_stop_distance', base_trailing_distance_pct))
        
        # Pattern 2: Direct dictionary access (for dict-based config)
        elif hasattr(config, '__getitem__'):
            trading_config = config.get('trading', {})
            activation_threshold_pct = float(trading_config.get('trailing_stop_activation', activation_threshold_pct))
            base_trailing_distance_pct = float(trading_config.get('trailing_stop_distance', base_trailing_distance_pct))
        
        # Pattern 3: Direct config attributes (fallback)
        else:
            activation_threshold_pct = float(getattr(config, 'trailing_stop_activation', activation_threshold_pct))
            base_trailing_distance_pct = float(getattr(config, 'trailing_stop_distance', base_trailing_distance_pct))
        
        logger.info(f"[Trade {trade_id}] [TrailingStop] Config loaded - activation_threshold={activation_threshold_pct:.3f}, base_distance={base_trailing_distance_pct:.3f}")
        
    except Exception as e:
        logger.warning(f"[Trade {trade_id}] [TrailingStop] Error getting settings: {e}. Using crypto-optimized defaults.")
        activation_threshold_pct = 0.03  # 3%
        base_trailing_distance_pct = 0.15  # 15%

    # Calculate ATR-based trailing distance if possible
    trailing_distance_pct = base_trailing_distance_pct
    atr_value = None

    if ohlcv_data is not None and len(ohlcv_data) >= 14:
        try:
            atr_value = calculate_atr(ohlcv_data)
            if atr_value and current_price > 0:
                # Use ATR as percentage of current price for trailing distance
                atr_pct = atr_value / current_price
                # Minimum trailing distance should be 2x ATR, maximum 3x base distance
                atr_based_distance = max(atr_pct * 2, base_trailing_distance_pct * 0.5)
                atr_based_distance = min(atr_based_distance, base_trailing_distance_pct * 3)
                trailing_distance_pct = atr_based_distance

                logger.info(f"[Trade {trade_id}] [TrailingStop] ATR-adjusted trailing distance: {trailing_distance_pct:.3f} (ATR: {atr_value:.5f})")
        except Exception as e:
            logger.warning(f"[Trade {trade_id}] [TrailingStop] Error calculating ATR adjustment: {e}")
    
    # Initialize tracking attributes if they don't exist
    if not hasattr(state, 'trailing_stop_active'):
        state.trailing_stop_active = False
        state.trailing_stop_level = 0.0
        state.highest_price_seen = 0.0
    
    # For logging - get position side
    side = state.position
    
    # Normalize position side to handle case-insensitive comparison
    if side:
        side = side.lower()
    
    logger.info(f"[Trade {trade_id}] [TrailingStop] Position side: {side}, trailing_stop_active: {state.trailing_stop_active}")
    
    # Only relevant for long/buy positions currently (can be extended for shorts)
    valid_long_positions = ['long', 'buy']
    if side not in valid_long_positions:
        logger.info(f"[Trade {trade_id}] [TrailingStop] Skipping - not a long position")
        risk_data = {
            'trailing_stop_active': state.trailing_stop_active,
            'trailing_stop_level': state.trailing_stop_level,
            'highest_price_seen': state.highest_price_seen,
            'trailing_stop_distance_pct': trailing_distance_pct,
            'activation_threshold_pct': activation_threshold_pct
        }
        return False, None, risk_data
    
    # Calculate current profit percentage
    profit_pct = (current_price - entry_price) / entry_price if entry_price else 0.0
    
    logger.info(f"[Trade {trade_id}] [TrailingStop] Current profit: {profit_pct:.2%}, activation threshold: {activation_threshold_pct:.2%}")
    
    # Check if we should activate trailing stop (if we hit activation threshold)
    if not state.trailing_stop_active and profit_pct >= activation_threshold_pct:
        state.trailing_stop_active = True
        state.highest_price_seen = current_price
        # Initial stop level is current_price minus the trailing distance
        state.trailing_stop_level = current_price * (1 - trailing_distance_pct)
        logger.info(f"[Trade {trade_id}] [TrailingStop] Activated with ATR-enhanced distance: profit={profit_pct:.2%}, "
                  f"distance={trailing_distance_pct:.3f}, stop_level={state.trailing_stop_level:.2f}")
    
    # If trailing stop is active, update stop level if needed
    if state.trailing_stop_active:
        # If price moved higher, adjust trailing stop upward
        if current_price > state.highest_price_seen:
            # Record new highest price
            old_highest = state.highest_price_seen
            state.highest_price_seen = current_price
            # Calculate new stop level
            new_stop_level = current_price * (1 - trailing_distance_pct)
            # Only move stop up, never down
            if new_stop_level > state.trailing_stop_level:
                old_level = state.trailing_stop_level
                state.trailing_stop_level = new_stop_level
                logger.info(f"[Trade {trade_id}] [TrailingStop] Raised: {old_level:.2f} -> {new_stop_level:.2f}, "
                          f"highest: {old_highest:.2f} -> {current_price:.2f}")
        
        # Check if price hit the trailing stop
        if current_price <= state.trailing_stop_level:
            locked_profit_pct = (state.trailing_stop_level - entry_price) / entry_price
            logger.info(f"[Trade {trade_id}] [TrailingStop] Triggered exit: current_price={current_price:.2f} "
                      f"<= stop_level={state.trailing_stop_level:.2f}, locked_profit={locked_profit_pct:.2%}")
            
            # Reset for next trade
            state.trailing_stop_active = False
            state.trailing_stop_level = 0.0
            state.highest_price_seen = 0.0
            
            # Prepare risk data for database update
            risk_data = {
                'trailing_stop_active': state.trailing_stop_active,
                'trailing_stop_level': state.trailing_stop_level,
                'highest_price_seen': state.highest_price_seen,
                'trailing_stop_distance_pct': trailing_distance_pct,
                'activation_threshold_pct': activation_threshold_pct,
                'locked_profit_pct': locked_profit_pct
            }
            
            return True, "atr_enhanced_trailing_stop", risk_data
        
        # Log current status for monitoring
        logger.info(f"[Trade {trade_id}] [TrailingStop] Status: current_price={current_price:.2f}, "
                   f"highest_seen={state.highest_price_seen:.2f}, stop_level={state.trailing_stop_level:.2f}, "
                   f"profit_pct={profit_pct:.2%}")
    
    # Prepare risk data for database update (even if not exiting)
    risk_data = {
        'trailing_stop_active': state.trailing_stop_active,
        'trailing_stop_level': state.trailing_stop_level,
        'highest_price_seen': state.highest_price_seen,
        'trailing_stop_distance_pct': trailing_distance_pct,
        'activation_threshold_pct': activation_threshold_pct,
        'current_profit_pct': profit_pct,
        'atr_value': atr_value
    }
    
    return False, None, risk_data

def manage_trailing_stop(state, current_price, entry_price, position_size, config, trade_id, logger=None):
    """
    Backward compatibility wrapper for manage_trailing_stop_enhanced.
    Calls the enhanced version without OHLCV data.
    """
    return manage_trailing_stop_enhanced(
        state=state,
        current_price=current_price,
        entry_price=entry_price,
        position_size=position_size,
        config=config,
        ohlcv_data=None,
        trade_id=trade_id,
        logger=logger
    )

def restore_profit_protection_state(state, risk_data_from_db, logger=None):
    """
    Restore profit protection state from database risk data.
    
    Args:
        state: Strategy state object to update
        risk_data_from_db: Risk data dictionary from database
        logger: Optional logger instance
    """
    if not logger:
        import logging
        logger = logging.getLogger(__name__)
    
    if not risk_data_from_db:
        logger.debug("No risk data from database to restore")
        return
    
    try:
        # Restore profit protection state
        if 'profit_protection_active' in risk_data_from_db:
            state.profit_protection_active = bool(risk_data_from_db['profit_protection_active'])
        
        if 'peak_unrealized_pnl' in risk_data_from_db:
            state.peak_unrealized_pnl = float(risk_data_from_db['peak_unrealized_pnl'])
        
        if 'consecutive_decreases' in risk_data_from_db:
            state.consecutive_decreases = int(risk_data_from_db['consecutive_decreases'])
        
        if 'last_unrealized_pnl' in risk_data_from_db:
            state.last_unrealized_pnl = float(risk_data_from_db['last_unrealized_pnl'])
        
        if 'profit_protection_activated_at' in risk_data_from_db:
            state.profit_protection_activated_at = risk_data_from_db['profit_protection_activated_at']
        
        # Restore trailing stop state
        if 'trailing_stop_active' in risk_data_from_db:
            state.trailing_stop_active = bool(risk_data_from_db['trailing_stop_active'])
        
        if 'trailing_stop_level' in risk_data_from_db:
            state.trailing_stop_level = float(risk_data_from_db['trailing_stop_level'])
        
        if 'highest_price_seen' in risk_data_from_db:
            state.highest_price_seen = float(risk_data_from_db['highest_price_seen'])
        
        logger.debug(f"Restored profit protection state: active={getattr(state, 'profit_protection_active', False)}, "
                    f"peak_pnl={getattr(state, 'peak_unrealized_pnl', 0)}, "
                    f"consecutive_decreases={getattr(state, 'consecutive_decreases', 0)}")
        
    except Exception as e:
        logger.error(f"Error restoring profit protection state: {e}")

def check_profit_floor(current_pnl_pct, trade_config, has_profit_protection_active=False):
    """
    Check if trade violates minimum profit floor or maximum loss limits.
    
    Args:
        current_pnl_pct: Current PnL as percentage (e.g., 0.02 for 2%)
        trade_config: Trading configuration dictionary
        has_profit_protection_active: Whether profit protection is currently active
        
    Returns:
        Tuple of (should_exit, exit_reason)
    """
    min_profit_floor = trade_config.get('minimum_profit_floor', 0.01)  # 1% default
    max_acceptable_loss = trade_config.get('maximum_acceptable_loss', 0.03)  # 3% default
    emergency_threshold = trade_config.get('emergency_close_threshold', 0.04)  # 4% default
    
    # Emergency closure for major losses
    if current_pnl_pct <= -emergency_threshold:
        return True, "emergency_loss_limit"
    
    # For profit protection active trades, enforce profit floor
    if has_profit_protection_active:
        if current_pnl_pct < min_profit_floor:
            return True, "profit_floor_violation"
    
    # For non-protected trades, enforce maximum loss
    elif current_pnl_pct <= -max_acceptable_loss:
        return True, "maximum_loss_exceeded"
    
    return False, "" 