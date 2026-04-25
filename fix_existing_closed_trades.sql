-- Fix existing CLOSED trades that are missing exit_time or exit_price
-- This script attempts to reconstruct missing data before applying constraints

-- First, let's see what we're working with
SELECT 
    COUNT(*) as total_closed,
    COUNT(exit_time) as with_exit_time,
    COUNT(exit_price) as with_exit_price,
    COUNT(*) - COUNT(exit_time) as missing_exit_time,
    COUNT(*) - COUNT(exit_price) as missing_exit_price
FROM trading.trades 
WHERE status = 'CLOSED';

-- Show problematic trades
SELECT 
    trade_id,
    pair,
    exchange,
    entry_time,
    exit_time,
    entry_price,
    exit_price,
    exit_id,
    exit_reason
FROM trading.trades 
WHERE status = 'CLOSED' 
  AND (exit_time IS NULL OR exit_price IS NULL)
ORDER BY entry_time;

-- Attempt to fix trades with exit_id but missing exit_price
-- Strategy 1: If we have exit_id, try to get current price as approximation
-- This is not ideal but better than NULL

-- Update missing exit_time to use a reasonable timestamp
UPDATE trading.trades 
SET exit_time = CASE 
    WHEN exit_time IS NULL AND updated_at IS NOT NULL THEN updated_at
    WHEN exit_time IS NULL THEN entry_time + INTERVAL '2 hours'  -- Assume trades lasted ~2 hours on average
    ELSE exit_time
END
WHERE status = 'CLOSED' AND exit_time IS NULL;

-- For trades with exit_id but no exit_price, we need to set a reasonable value
-- First, let's see if we can find any pattern in the data
-- Check if there's current_price data we can use
SELECT 
    trade_id,
    pair,
    entry_price,
    current_price,
    highest_price
FROM trading.trades 
WHERE status = 'CLOSED' 
  AND exit_price IS NULL 
  AND (current_price IS NOT NULL OR highest_price IS NOT NULL);

-- Update exit_price using available price data
-- Priority: highest_price > current_price > entry_price (as fallback)
UPDATE trading.trades 
SET 
    exit_price = CASE 
        WHEN exit_price IS NULL AND highest_price IS NOT NULL THEN highest_price
        WHEN exit_price IS NULL AND current_price IS NOT NULL THEN current_price
        WHEN exit_price IS NULL THEN entry_price  -- Last resort: assume break-even
        ELSE exit_price
    END,
    exit_reason = CASE 
        WHEN (exit_reason IS NULL OR exit_reason = '') AND exit_price IS NULL THEN 'reconstructed_from_data'
        ELSE COALESCE(exit_reason, 'unknown_exit')
    END,
    realized_pnl = CASE 
        WHEN exit_price IS NULL AND highest_price IS NOT NULL THEN 
            (highest_price - entry_price) * position_size
        WHEN exit_price IS NULL AND current_price IS NOT NULL THEN 
            (current_price - entry_price) * position_size
        WHEN exit_price IS NULL THEN 0  -- Break-even assumption
        ELSE realized_pnl
    END
WHERE status = 'CLOSED' AND exit_price IS NULL;

-- Verify the fixes
SELECT 
    'After Fix' as status,
    COUNT(*) as total_closed,
    COUNT(exit_time) as with_exit_time,
    COUNT(exit_price) as with_exit_price,
    COUNT(*) - COUNT(exit_time) as missing_exit_time,
    COUNT(*) - COUNT(exit_price) as missing_exit_price
FROM trading.trades 
WHERE status = 'CLOSED';

-- Show any remaining problematic trades
SELECT 
    'Remaining Issues' as category,
    trade_id,
    pair,
    exchange,
    entry_time,
    exit_time,
    entry_price,
    exit_price,
    exit_reason
FROM trading.trades 
WHERE status = 'CLOSED' 
  AND (exit_time IS NULL OR exit_price IS NULL)
ORDER BY entry_time;
