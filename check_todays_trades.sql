-- Get count per status for today's records in trading.trades table

-- Show current date first
SELECT CURRENT_DATE as today_date;

-- Get count per status for today's trades
SELECT 
    status, 
    COUNT(*) as count 
FROM trading.trades 
WHERE DATE(created_at) = CURRENT_DATE 
GROUP BY status 
ORDER BY status;

-- Also show total count for today
SELECT 
    'TOTAL' as status,
    COUNT(*) as count 
FROM trading.trades 
WHERE DATE(created_at) = CURRENT_DATE;

-- Show recent trades for context (last 10)
SELECT 
    id,
    pair,
    status,
    side,
    quantity,
    price,
    created_at
FROM trading.trades 
WHERE DATE(created_at) = CURRENT_DATE
ORDER BY created_at DESC 
LIMIT 10;
