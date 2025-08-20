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

        tr_data = pd.DataFrame({
            'hl': high_low,
            'hc': high_close,
            'lc': low_close
        })
        
        true_range = tr_data.max(axis=1)
        atr = true_range.rolling(window=period).mean()
        
        atr_list = atr.tolist()
        if not atr_list:
            return None
            
        last_value = atr_list[-1]
        if last_value is None:
            return None
            
        float_value = float(last_value)
        if float_value != float_value:  # NaN check
            return None
        return float_value
            
    except Exception:
        return None

def calculate_adx(ohlcv_data, period=14):
    """
    Calculate ADX for market regime detection (trending vs. sideways).

    Args:
        ohlcv_data: DataFrame with OHLCV data
        period: ADX calculation period

    Returns:
        ADX value
    """
    if len(ohlcv_data) < period:
        return None

    try:
        high = ohlcv_data['high']
        low = ohlcv_data['low']
        close = ohlcv_data['close']
        
        plus_dm = high.diff()
        minus_dm = -low.diff()
        
        plus_dm[plus_dm < 0] = 0
        minus_dm[minus_dm < 0] = 0
        
        tr = pd.DataFrame({
            'hl': high - low,
            'hc': abs(high - close.shift(1)),
            'lc': abs(low - close.shift(1))
        }).max(axis=1)
        
        atr = tr.rolling(window=period).mean()
        
        plus_di = 100 * (plus_dm.rolling(window=period).mean() / atr)
        minus_di = 100 * (minus_dm.rolling(window=period).mean() / atr)
        
        dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di)
        adx = dx.rolling(window=period).mean()
        
        adx_list = adx.tolist()
        if not adx_list:
            return None
            
        last_value = adx_list[-1]
        if last_value is None:
            return None
            
        float_value = float(last_value)
        if float_value != float_value:  # NaN check
            return None
        return float_value
        
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
    
    volatility_pct = atr_value / current_price
    
    if volatility_pct > 0.05:  # High volatility (>5%)
        return 1.4
    elif volatility_pct > 0.03:  # Medium volatility (3-5%)
        return 1.2
    elif volatility_pct < 0.015:  # Low volatility (<1.5%)
        return 0.8
    else:
        return 1.0

def get_dynamic_config(atr_value, current_price, base_config):
    """
    Adjust thresholds dynamically based on ATR volatility.

    Args:
        atr_value: Average True Range value
        current_price: Current asset price
        base_config: Base configuration object or dict

    Returns:
        Adjusted configuration
    """
    config = base_config.copy() if isinstance(base_config, dict) else base_config.__dict__.copy()
    volatility_pct = atr_value / current_price if atr_value and current_price > 0 else 0.03
    
    # Dynamic thresholds based on volatility
    if volatility_pct > 0.05:  # High volatility
        thresholds = {
            'profit_protection_threshold': 0.015,  # 1.5%
            'trailing_stop_activation': 0.04,      # 4%
            'max_drawdown': 0.35                   # 35%
        }
    elif volatility_pct > 0.03:  # Medium volatility
        thresholds = {
            'profit_protection_threshold': 0.01,   # 1%
            'trailing_stop_activation': 0.03,      # 3%
            'max_drawdown': 0.30                   # 30%
        }
    else:  # Low volatility
        thresholds = {
            'profit_protection_threshold': 0.008,  # 0.8%
            'trailing_stop_activation': 0.025,     # 2.5%
            'max_drawdown': 0.25                   # 25%
        }
    
    for key, value in thresholds.items():
        if isinstance(config, dict):
            config[key] = value
        else:
            setattr(config, key, value)
    
    return config

def _validate_position_for_pnl(position):
    """
    Validate position object for PnL calculation.
    
    Args:
        position: Position object or state
        
    Returns:
        Tuple of (is_valid, missing_fields)
    """
    required_fields = ['entry_price', 'position_size']
    missing_fields = []
    
    for field in required_fields:
        if not hasattr(position, field) or getattr(position, field) is None:
            missing_fields.append(field)
        elif field == 'entry_price' and float(getattr(position, field)) == 0.0:
            missing_fields.append('entry_price_zero')
        elif field == 'position_size' and float(getattr(position, field)) == 0.0:
            missing_fields.append('position_size_zero')
    
    has_position = hasattr(position, 'position') and getattr(position, 'position') is not None
    has_side = hasattr(position, 'side') and getattr(position, 'side') is not None
    
    if not has_position and not has_side:
        missing_fields.append('position_or_side')
    elif has_position:
        position_value = getattr(position, 'position').lower() if getattr(position, 'position') else ''
        if position_value not in ['long', 'short', 'none', 'buy', 'sell']:
            missing_fields.append('valid_position_value')
        elif position_value == 'none':
            missing_fields.append('position_none')
    elif has_side:
        side_value = getattr(position, 'side').lower() if getattr(position, 'side') else ''
        if side_value not in ['long', 'short', 'buy', 'sell']:
            missing_fields.append('valid_side_value')
            
    return len(missing_fields) == 0, missing_fields

def calculate_unrealized_pnl(position, current_price, trade_id=None, fee_rate=0.001):
    """
    Calculate unrealized PnL with transaction fee adjustment.

    Args:
        position: Position object with entry_price, position_size, position/side
        current_price: Current price of the asset
        trade_id: Optional trade ID for logging
        fee_rate: Transaction fee rate (default 0.1%)

    Returns:
        Unrealized PnL or 0 if calculation isn't possible
    """
    logger = logging.getLogger(__name__)
    
    if trade_id:
        logger.info(f"[Trade {trade_id}] [PnL] Starting PnL calculation - current_price={current_price}, fee_rate={fee_rate}")
    
    is_valid, missing_fields = _validate_position_for_pnl(position)
    if not is_valid:
        if trade_id:
            logger.warning(f"[Trade {trade_id}] [PnL] Cannot calculate unrealized PnL: missing fields {missing_fields}")
        return 0.0
        
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
        
    position_type = getattr(position, 'position', getattr(position, 'side', None))
    if position_type:
        position_type = position_type.lower()
    
    if trade_id:
        logger.info(f"[Trade {trade_id}] [PnL] Position type: {position_type}")
    
    valid_positions = ['long', 'buy', 'short', 'sell']
    if position_type not in valid_positions:
        if trade_id:
            logger.warning(f"[Trade {trade_id}] [PnL] Invalid position type: {position_type}")
        return 0.0
        
    # Calculate gross PnL
    if position_type in ['long', 'buy']:
        gross_pnl = (current_price - entry_price) * position_size
    else:  # short, sell
        gross_pnl = (entry_price - current_price) * position_size
    
    # Apply transaction fees (entry and potential exit)
    entry_fee = entry_price * position_size * fee_rate
    exit_fee = current_price * position_size * fee_rate
    unrealized_pnl = gross_pnl - entry_fee - exit_fee
    
    pnl_percentage = (current_price - entry_price) / entry_price * 100 if entry_price != 0 and position_type in ['long', 'buy'] else (entry_price - current_price) / entry_price * 100
    
    if trade_id:
        logger.info(f"[Trade {trade_id}] [PnL] Calculated unrealized PnL: {unrealized_pnl:.5f} ({pnl_percentage:.2f}%) (gross={gross_pnl:.5f}, entry_fee={entry_fee:.5f}, exit_fee={exit_fee:.5f})")
        
    return unrealized_pnl

def check_profit_protection_enhanced(state, unrealized_pnl, entry_price, position_size, 
                                   config, ohlcv_data=None, trade_id=None, logger=None, 
                                   current_price=None, pair=None):
    """
    Enhanced profit protection with crypto-optimized parameters, volatility adjustments, and market regime detection.

    Args:
        state: Strategy state with profit protection tracking
        unrealized_pnl: Current unrealized PnL
        entry_price: Entry price of the position
        position_size: Size of the position
        config: Config object with trading parameters
        ohlcv_data: OHLCV data for volatility and regime calculations
        trade_id: Optional trade ID for logging
        logger: Optional logger instance
        current_price: Current price (for logging)
        pair: Trading pair (e.g., 'BTC/USDC')

    Returns:
        Tuple of (should_exit, reason, risk_data)
    """
    logger = logger or logging.getLogger(__name__)
    
    if trade_id:
        logger.info(f"[Trade {trade_id}] [ProfitProtection] Starting check - unrealized_pnl={unrealized_pnl}, entry_price={entry_price}, pair={pair}")
    
    if entry_price is None or position_size is None or unrealized_pnl is None:
        logger.warning(f"[Trade {trade_id}] [ProfitProtection] Skipping check: entry_price={entry_price}, position_size={position_size}, unrealized_pnl={unrealized_pnl}")
        return False, None, {'profit_protection_active': False}
    
    if current_price is None:
        current_price = entry_price
    
    # Get dynamic config based on volatility
    adjusted_config = config
    atr_value = None
    if ohlcv_data is not None and len(ohlcv_data) >= 14:
        atr_value = calculate_atr(ohlcv_data)
        if atr_value and current_price > 0:
            adjusted_config = get_dynamic_config(atr_value, current_price, config)
    
    # Get configuration settings from config file (no hardcoded defaults)
    try:
        # FIX: Use type check for dict vs object for trading_config
        if isinstance(adjusted_config, dict):
            trading_config = adjusted_config.get('trading', adjusted_config)
        else:
            trading_config = getattr(adjusted_config, 'trading', adjusted_config)
        
        if isinstance(trading_config, dict):
            profit_protection_config = trading_config.get('profit_protection', {})
            threshold = float(profit_protection_config.get('activation_threshold', 0.01))
            max_drawdown = float(profit_protection_config.get('max_drawdown', 0.30))
            consecutive_threshold = int(profit_protection_config.get('consecutive_threshold', 8))
            significant_decrease_multiplier = float(profit_protection_config.get('significant_decrease_multiplier', 0.002))
        else:
            profit_protection_config = getattr(trading_config, 'profit_protection', None)
            if profit_protection_config and hasattr(profit_protection_config, '__dict__'):
                threshold = float(getattr(profit_protection_config, 'activation_threshold', 0.01))
                max_drawdown = float(getattr(profit_protection_config, 'max_drawdown', 0.30))
                consecutive_threshold = int(getattr(profit_protection_config, 'consecutive_threshold', 8))
                significant_decrease_multiplier = float(getattr(profit_protection_config, 'significant_decrease_multiplier', 0.002))
            else:
                # Fallback if structure is different
                threshold = 0.01
                max_drawdown = 0.30
                consecutive_threshold = 8
                significant_decrease_multiplier = 0.002
        
        logger.info(f"[Trade {trade_id}] [ProfitProtection] Config loaded - threshold={threshold:.3f}, max_drawdown={max_drawdown:.3f}")
            
    except Exception as e:
        logger.warning(f"[Trade {trade_id}] [ProfitProtection] Error getting settings: {e}. Using defaults.")
    
    # Calculate volatility and regime adjustments
    volatility_adjustment = 1.0
    regime_adjustment = 1.0
    adx_value = None
    
    if ohlcv_data is not None and len(ohlcv_data) >= 14:
        try:
            if atr_value and current_price > 0:
                volatility_adjustment = calculate_volatility_adjustment(atr_value, current_price)
                logger.info(f"[Trade {trade_id}] [ProfitProtection] Volatility adjustment: {volatility_adjustment:.2f} (ATR: {atr_value:.5f})")
                
            adx_value = calculate_adx(ohlcv_data)
            if adx_value:
                regime_adjustment = 1.2 if adx_value > 30 else 0.8  # Looser in trending, tighter in sideways
                logger.info(f"[Trade {trade_id}] [ProfitProtection] Regime adjustment: {regime_adjustment:.2f} (ADX: {adx_value:.2f})")
        except Exception as e:
            logger.warning(f"[Trade {trade_id}] [ProfitProtection] Error calculating adjustments: {e}")
    
    adjusted_max_drawdown = max_drawdown * volatility_adjustment * regime_adjustment
    adjusted_consecutive_threshold = int(consecutive_threshold * volatility_adjustment * regime_adjustment)
    
    # Calculate PnL percentage
    position_type = getattr(state, 'position', getattr(state, 'side', None))
    position_type = position_type.lower() if position_type else None
    if position_type in ['long', 'buy']:
        pnl_percentage = (current_price - entry_price) / entry_price if entry_price != 0 else 0
    else:  # short, sell
        pnl_percentage = (entry_price - current_price) / entry_price if entry_price != 0 else 0
    
    logger.info(f"[Trade {trade_id}] [ProfitProtection] PnL percentage: {pnl_percentage:.2%}, position_type: {position_type}")
    
    # Initialize state attributes
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
        
        logger.info(f"[Trade {trade_id}] [ProfitProtection] State initialized")
    except Exception as e:
        logger.error(f"[Trade {trade_id}] [ProfitProtection] Error initializing state: {e}")
        return False, None, {'profit_protection_active': False}
    
    state.volatility_adjusted_settings = {
        'max_drawdown': adjusted_max_drawdown,
        'consecutive_threshold': adjusted_consecutive_threshold,
        'volatility_adjustment': volatility_adjustment,
        'regime_adjustment': regime_adjustment,
        'atr_value': atr_value,
        'adx_value': adx_value
    }
    
    # Track peak PnL
    try:
        if not hasattr(state, 'peak_initialized'):
            state.peak_unrealized_pnl = 0.0
            state.peak_initialized = True
            logger.info(f"[Trade {trade_id}] [ProfitProtection] Initialized peak PnL to 0.0")
        
        if unrealized_pnl > state.peak_unrealized_pnl:
            old_peak = state.peak_unrealized_pnl
            state.peak_unrealized_pnl = unrealized_pnl
            logger.info(f"[Trade {trade_id}] [ProfitProtection] New peak PnL: {old_peak:.5f} -> {unrealized_pnl:.5f}")
    except Exception as e:
        logger.error(f"[Trade {trade_id}] [ProfitProtection] Error tracking peak PnL: {e}")
    
    # Calculate drawdown
    try:
        if state.peak_unrealized_pnl >= 0:
            if state.peak_unrealized_pnl == 0.0:
                current_drawdown = abs(unrealized_pnl) / abs(entry_price * position_size) if entry_price * position_size > 0 else 0
            else:
                current_drawdown = (state.peak_unrealized_pnl - unrealized_pnl) / abs(state.peak_unrealized_pnl)
            current_drawdown = max(0, current_drawdown)
        else:
            current_drawdown = 0
    except Exception as e:
        logger.error(f"[Trade {trade_id}] [ProfitProtection] Error calculating drawdown: {e}")
        current_drawdown = 0
    
    # Consecutive decreases logic
    try:
        pnl_change = unrealized_pnl - state.last_unrealized_pnl
        significant_decrease_threshold = abs(entry_price * position_size * significant_decrease_multiplier)
        if atr_value:
            atr_threshold = atr_value * position_size
            significant_decrease_threshold = max(significant_decrease_threshold, atr_threshold * 0.5)
        
        if pnl_change < -significant_decrease_threshold:
            state.consecutive_decreases += 1
            logger.info(f"[Trade {trade_id}] [ProfitProtection] Significant decrease: {pnl_change:.5f}, consecutive: {state.consecutive_decreases}")
        elif pnl_change > significant_decrease_threshold:
            logger.info(f"[Trade {trade_id}] [ProfitProtection] Significant increase: {pnl_change:.5f}, resetting consecutive")
            state.consecutive_decreases = 0
        
        state.last_unrealized_pnl = unrealized_pnl
    except Exception as e:
        logger.error(f"[Trade {trade_id}] [ProfitProtection] Error processing consecutive decreases: {e}")
    
    # Activate profit protection
    try:
        if not state.profit_protection_active and abs(pnl_percentage) >= threshold:
            state.profit_protection_active = True
            state.profit_protection_activated_at = datetime.utcnow()
            logger.info(f"[Trade {trade_id}] [ProfitProtection] Activated at {pnl_percentage:.2f}%")
    except Exception as e:
        logger.error(f"[Trade {trade_id}] [ProfitProtection] Error activating profit protection: {e}")
    
    # Log state
    logger.info(f"[Trade {trade_id}] [ProfitProtection] State - active={state.profit_protection_active}, peak_pnl={state.peak_unrealized_pnl:.5f}, drawdown={current_drawdown:.2%}")
    
    # Check exit conditions
    try:
        if state.profit_protection_active:
            if current_drawdown >= adjusted_max_drawdown:
                logger.info(f"[Trade {trade_id}] [ProfitProtection] Exit: drawdown {current_drawdown:.2%} >= {adjusted_max_drawdown:.2%}")
                return True, "volatility_adjusted_drawdown", {
                    'profit_protection_active': True,
                    'current_drawdown': current_drawdown,
                    'max_drawdown': adjusted_max_drawdown,
                    'pnl_percentage': pnl_percentage,
                    'volatility_adjustment': volatility_adjustment,
                    'regime_adjustment': regime_adjustment
                }
            
            if state.consecutive_decreases >= adjusted_consecutive_threshold:
                logger.info(f"[Trade {trade_id}] [ProfitProtection] Exit: {state.consecutive_decreases} decreases >= {adjusted_consecutive_threshold}")
                return True, "volatility_adjusted_consecutive_decreases", {
                    'profit_protection_active': True,
                    'consecutive_decreases': state.consecutive_decreases,
                    'threshold': adjusted_consecutive_threshold,
                    'pnl_percentage': pnl_percentage,
                    'volatility_adjustment': volatility_adjustment,
                    'regime_adjustment': regime_adjustment
                }
    except Exception as e:
        logger.error(f"[Trade {trade_id}] [ProfitProtection] Error checking exits: {e}")
    
    return False, None, {
        'profit_protection_active': state.profit_protection_active,
        'current_drawdown': current_drawdown,
        'consecutive_decreases': state.consecutive_decreases,
        'pnl_percentage': pnl_percentage,
        'volatility_adjustment': volatility_adjustment,
        'adjusted_max_drawdown': adjusted_max_drawdown,
        'adjusted_consecutive_threshold': adjusted_consecutive_threshold,
        'adx_value': adx_value
    }

def manage_trailing_stop_enhanced(state, current_price, entry_price, position_size, 
                                config, ohlcv_data, trade_id, logger=None, pair=None):
    """
    Enhanced trailing stop with crypto-optimized parameters, ATR adjustments, and short position support.

    Args:
        state: Position state object
        current_price: Current market price
        entry_price: Position entry price
        position_size: Size of the position
        config: Configuration object
        ohlcv_data: OHLCV data for ATR calculation
        trade_id: Trade identifier
        logger: Optional logger instance
        pair: Trading pair (e.g., 'BTC/USDC')

    Returns:
        Tuple of (should_exit, reason, risk_data)
    """
    logger = logger or logging.getLogger(__name__)
    if trade_id is None:
        logger.error("Trade ID required for trailing stop")
        return False, None, {}
    
    logger.info(f"[Trade {trade_id}] [TrailingStop] Starting check - current_price={current_price}, pair={pair}")
    
    adjusted_config = config
    atr_value = None
    if ohlcv_data is not None and len(ohlcv_data) >= 14:
        atr_value = calculate_atr(ohlcv_data)
        if atr_value and current_price > 0:
            adjusted_config = get_dynamic_config(atr_value, current_price, config)
    
    try:
        # Get configuration settings from config file (no hardcoded defaults)
        # FIX: Use type check for dict vs object for trading_config
        if isinstance(adjusted_config, dict):
            trading_config = adjusted_config.get('trading', adjusted_config)
        else:
            trading_config = getattr(adjusted_config, 'trading', adjusted_config)
        
        if isinstance(trading_config, dict):
            trailing_stop_config = trading_config.get('trailing_stop', {})
            activation_threshold_pct = float(trailing_stop_config.get('activation_threshold', 0.03))
            base_trailing_distance_pct = float(trailing_stop_config.get('base_trailing_distance', 0.15))
        else:
            trailing_stop_config = getattr(trading_config, 'trailing_stop', None)
            if trailing_stop_config and hasattr(trailing_stop_config, '__dict__'):
                activation_threshold_pct = float(getattr(trailing_stop_config, 'activation_threshold', 0.03))
                base_trailing_distance_pct = float(getattr(trailing_stop_config, 'base_trailing_distance', 0.15))
            else:
                # Fallback if structure is different
                activation_threshold_pct = 0.03
                base_trailing_distance_pct = 0.15
        
        logger.info(f"[Trade {trade_id}] [TrailingStop] Config loaded - activation={activation_threshold_pct:.3f}")
        
    except Exception as e:
        logger.warning(f"[Trade {trade_id}] [TrailingStop] Error getting settings: {e}. Using defaults.")
    
    trailing_distance_pct = base_trailing_distance_pct
    regime_adjustment = 1.0
    adx_value = None
    
    if ohlcv_data is not None and len(ohlcv_data) >= 14:
        try:
            if atr_value and current_price > 0:
                atr_pct = atr_value / current_price
                atr_based_distance = max(atr_pct * 2, base_trailing_distance_pct * 0.5)
                trailing_distance_pct = min(atr_based_distance, base_trailing_distance_pct * 3)
                logger.info(f"[Trade {trade_id}] [TrailingStop] ATR-adjusted distance: {trailing_distance_pct:.3f}")
                
            adx_value = calculate_adx(ohlcv_data)
            if adx_value:
                regime_adjustment = 1.2 if adx_value > 30 else 0.8
                logger.info(f"[Trade {trade_id}] [TrailingStop] Regime adjustment: {regime_adjustment:.2f}")
                
        except Exception as e:
            logger.warning(f"[Trade {trade_id}] [TrailingStop] Error calculating adjustments: {e}")
    
    trailing_distance_pct *= regime_adjustment
    
    if not hasattr(state, 'trailing_stop_active'):
        state.trailing_stop_active = False
        state.trailing_stop_level = 0.0
        state.highest_price_seen = 0.0
        state.lowest_price_seen = float('inf')
    
    side = getattr(state, 'position', getattr(state, 'side', None))
    side = side.lower() if side else None
    
    logger.info(f"[Trade {trade_id}] [TrailingStop] Position side: {side}")
    
    valid_positions = ['long', 'buy', 'short', 'sell']
    if side not in valid_positions:
        logger.info(f"[Trade {trade_id}] [TrailingStop] Skipping - invalid position")
        return False, None, {
            'trailing_stop_active': state.trailing_stop_active,
            'trailing_stop_level': state.trailing_stop_level
        }
    
    is_long = side in ['long', 'buy']
    profit_pct = (current_price - entry_price) / entry_price if is_long else (entry_price - current_price) / entry_price
    
    logger.info(f"[Trade {trade_id}] [TrailingStop] Profit: {profit_pct:.2%}")
    
    if not state.trailing_stop_active and abs(profit_pct) >= activation_threshold_pct:
        state.trailing_stop_active = True
        if is_long:
            state.highest_price_seen = current_price
            state.trailing_stop_level = current_price * (1 - trailing_distance_pct)
        else:
            state.lowest_price_seen = current_price
            state.trailing_stop_level = current_price * (1 + trailing_distance_pct)
        logger.info(f"[Trade {trade_id}] [TrailingStop] Activated: stop_level={state.trailing_stop_level:.2f}")
    
    if state.trailing_stop_active:
        if is_long:
            if current_price > state.highest_price_seen:
                state.highest_price_seen = current_price
                new_stop_level = current_price * (1 - trailing_distance_pct)
                if new_stop_level > state.trailing_stop_level:
                    logger.info(f"[Trade {trade_id}] [TrailingStop] Raised stop: {state.trailing_stop_level:.2f} -> {new_stop_level:.2f}")
                    state.trailing_stop_level = new_stop_level
            
            if current_price <= state.trailing_stop_level:
                locked_profit_pct = (state.trailing_stop_level - entry_price) / entry_price
                logger.info(f"[Trade {trade_id}] [TrailingStop] Exit: current_price={current_price:.2f} <= stop_level={state.trailing_stop_level:.2f}")
                state.trailing_stop_active = False
                state.trailing_stop_level = 0.0
                state.highest_price_seen = 0.0
                return True, "atr_enhanced_trailing_stop", {
                    'trailing_stop_active': False,
                    'locked_profit_pct': locked_profit_pct,
                    'trailing_stop_distance_pct': trailing_distance_pct
                }
        else:
            if current_price < state.lowest_price_seen:
                state.lowest_price_seen = current_price
                new_stop_level = current_price * (1 + trailing_distance_pct)
                if new_stop_level < state.trailing_stop_level:
                    logger.info(f"[Trade {trade_id}] [TrailingStop] Lowered stop: {state.trailing_stop_level:.2f} -> {new_stop_level:.2f}")
                    state.trailing_stop_level = new_stop_level
            
            if current_price >= state.trailing_stop_level:
                locked_profit_pct = (entry_price - state.trailing_stop_level) / entry_price
                logger.info(f"[Trade {trade_id}] [TrailingStop] Exit: current_price={current_price:.2f} >= stop_level={state.trailing_stop_level:.2f}")
                state.trailing_stop_active = False
                state.trailing_stop_level = 0.0
                state.lowest_price_seen = float('inf')
                return True, "atr_enhanced_trailing_stop", {
                    'trailing_stop_active': False,
                    'locked_profit_pct': locked_profit_pct,
                    'trailing_stop_distance_pct': trailing_distance_pct
                }
    
    return False, None, {
        'trailing_stop_active': state.trailing_stop_active,
        'trailing_stop_level': state.trailing_stop_level,
        'highest_price_seen': state.highest_price_seen,
        'lowest_price_seen': state.lowest_price_seen,
        'trailing_stop_distance_pct': trailing_distance_pct,
        'current_profit_pct': profit_pct
    }

def check_profit_floor(current_pnl_pct, trade_config, has_profit_protection_active=False, trade_id=None):
    """
    Check profit/loss floor limits with enhanced logging.

    Args:
        current_pnl_pct: Current PnL percentage
        trade_config: Trading configuration dictionary
        has_profit_protection_active: Whether profit protection is active
        trade_id: Optional trade ID for logging

    Returns:
        Tuple of (should_exit, exit_reason)
    """
    logger = logging.getLogger(__name__)
    
    min_profit_floor = trade_config.get('minimum_profit_floor', 0.01)
    max_acceptable_loss = trade_config.get('maximum_acceptable_loss', 0.03)
    emergency_threshold = trade_config.get('emergency_close_threshold', 0.04)
    
    if trade_id:
        logger.info(f"[Trade {trade_id}] [ProfitFloor] Checking: pnl_pct={current_pnl_pct:.2%}, profit_protection={has_profit_protection_active}")
    
    if current_pnl_pct <= -emergency_threshold:
        logger.info(f"[Trade {trade_id}] [ProfitFloor] Exit: pnl_pct={current_pnl_pct:.2%} <= emergency_threshold={-emergency_threshold:.2%}")
        return True, "emergency_loss_limit"
    
    if has_profit_protection_active and current_pnl_pct < min_profit_floor:
        logger.info(f"[Trade {trade_id}] [ProfitFloor] Exit: pnl_pct={current_pnl_pct:.2%} < min_profit_floor={min_profit_floor:.2%}")
        return True, "profit_floor_violation"
    
    if not has_profit_protection_active and current_pnl_pct <= -max_acceptable_loss:
        logger.info(f"[Trade {trade_id}] [ProfitFloor] Exit: pnl_pct={current_pnl_pct:.2%} <= max_acceptable_loss={-max_acceptable_loss:.2%}")
        return True, "maximum_loss_exceeded"
    
    logger.debug(f"[Trade {trade_id}] [ProfitFloor] No exit: pnl_pct={current_pnl_pct:.2%}")
    return False, ""

def restore_profit_protection_state(state, risk_data_from_db, logger=None):
    """
    Restore profit protection state from database.

    Args:
        state: Strategy state object
        risk_data_from_db: Risk data dictionary
        logger: Optional logger instance
    """
    logger = logger or logging.getLogger(__name__)
    
    if not risk_data_from_db:
        logger.debug("No risk data to restore")
        return
    
    try:
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
        if 'trailing_stop_active' in risk_data_from_db:
            state.trailing_stop_active = bool(risk_data_from_db['trailing_stop_active'])
        if 'trailing_stop_level' in risk_data_from_db:
            state.trailing_stop_level = float(risk_data_from_db['trailing_stop_level'])
        if 'highest_price_seen' in risk_data_from_db:
            state.highest_price_seen = float(risk_data_from_db['highest_price_seen'])
        if 'lowest_price_seen' in risk_data_from_db:
            state.lowest_price_seen = float(risk_data_from_db.get('lowest_price_seen', float('inf')))
        
        logger.debug(f"Restored state: active={getattr(state, 'profit_protection_active', False)}")
        
    except Exception as e:
        logger.error(f"Error restoring state: {e}")

def check_profit_protection(state, unrealized_pnl, entry_price, position_size, config, trade_id=None, logger=None, current_price=None):
    """
    Backward compatibility wrapper for check_profit_protection_enhanced.
    Calls the enhanced version without OHLCV data or pair.
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
        current_price=current_price,
        pair=None
    )

def manage_trailing_stop(state, current_price, entry_price, position_size, config, trade_id, logger=None):
    """
    Backward compatibility wrapper for manage_trailing_stop_enhanced.
    Calls the enhanced version without OHLCV data or pair.
    """
    return manage_trailing_stop_enhanced(
        state=state,
        current_price=current_price,
        entry_price=entry_price,
        position_size=position_size,
        config=config,
        ohlcv_data=None,
        trade_id=trade_id,
        logger=logger,
        pair=None
    )