-- Delete all trades with status = 'FAILED'

-- First, let's see how many FAILED trades we have
SELECT 'Before deletion - FAILED trades count:' as info, COUNT(*) as count 
FROM trading.trades 
WHERE status = 'FAILED';

-- Show some examples of what will be deleted (first 5)
SELECT 'Sample FAILED trades to be deleted:' as info;
SELECT id, pair, side, quantity, price, created_at, status 
FROM trading.trades 
WHERE status = 'FAILED' 
ORDER BY created_at DESC 
LIMIT 5;

-- Start transaction for safety
BEGIN;

-- Delete all FAILED trades
DELETE FROM trading.trades WHERE status = 'FAILED';

-- Show how many were deleted
SELECT 'Records deleted:' as info, ROW_COUNT() as deleted_count;

-- Verify no FAILED trades remain
SELECT 'After deletion - FAILED trades count:' as info, COUNT(*) as count 
FROM trading.trades 
WHERE status = 'FAILED';

-- Commit the transaction
COMMIT;

SELECT 'Deletion completed successfully!' as status;
