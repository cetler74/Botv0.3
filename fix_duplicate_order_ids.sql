-- Fix Duplicate Order ID Issue
-- Reset exit_id to NULL for trades where entry_id = exit_id (impossible condition)

-- First, let's see the affected trades
SELECT 'BEFORE FIX - Trades with duplicate order IDs:' as description;
SELECT trade_id, pair, entry_id, exit_id, entry_price, exit_price, realized_pnl 
FROM trading.trades 
WHERE entry_id IS NOT NULL 
  AND exit_id IS NOT NULL 
  AND entry_id = exit_id
ORDER BY pair, entry_price;

-- Update to fix the issue
UPDATE trading.trades 
SET exit_id = NULL,
    updated_at = CURRENT_TIMESTAMP
WHERE entry_id IS NOT NULL 
  AND exit_id IS NOT NULL 
  AND entry_id = exit_id;

-- Verify the fix
SELECT 'AFTER FIX - Remaining duplicate order IDs (should be 0):' as description;
SELECT COUNT(*) as duplicate_count
FROM trading.trades 
WHERE entry_id IS NOT NULL 
  AND exit_id IS NOT NULL 
  AND entry_id = exit_id;

-- Show trades that now need exit_id to be properly populated
SELECT 'TRADES NOW MISSING EXIT_ID:' as description;
SELECT trade_id, pair, entry_id, exit_id, status, exit_reason
FROM trading.trades 
WHERE status = 'CLOSED' 
  AND entry_id IS NOT NULL 
  AND exit_id IS NULL
ORDER BY pair;
