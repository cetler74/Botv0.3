-- Clear all trades with status = 'FAILED'
-- Updated for current trading.trades schema

-- First, let's see how many FAILED trades we have
SELECT 'Before deletion - FAILED trades count:' as info, COUNT(*) as count 
FROM trading.trades 
WHERE status = 'FAILED';

-- Show some examples of what will be deleted (first 5)
SELECT 'Sample FAILED trades to be deleted:' as info;
SELECT id, trade_id, pair, exchange, entry_price, status, created_at 
FROM trading.trades 
WHERE status = 'FAILED' 
ORDER BY created_at DESC 
LIMIT 5;

-- Start transaction for safety
BEGIN;

-- Delete all FAILED trades
DELETE FROM trading.trades WHERE status = 'FAILED';

-- Show how many were deleted (using a different approach since ROW_COUNT() doesn't exist)
SELECT 'Records deleted - checking count after deletion:' as info;

-- Verify no FAILED trades remain
SELECT 'After deletion - FAILED trades count:' as info, COUNT(*) as count 
FROM trading.trades 
WHERE status = 'FAILED';

-- Commit the transaction
COMMIT;

SELECT 'Deletion completed successfully!' as status;
