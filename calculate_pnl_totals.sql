-- Calculate PnL Totals for Trading Trades
-- This script calculates the sum of unrealized_pnl and realized_pnl for today and overall totals

SET search_path TO trading;

-- Today's PnL Totals (trades created today)
SELECT 
    'TODAY' as period,
    COUNT(*) as total_trades,
    COALESCE(SUM(unrealized_pnl), 0) as total_unrealized_pnl,
    COALESCE(SUM(realized_pnl), 0) as total_realized_pnl,
    COALESCE(SUM(unrealized_pnl + realized_pnl), 0) as total_combined_pnl
FROM trading.trades 
WHERE DATE(created_at) = CURRENT_DATE;

-- Overall PnL Totals (all trades)
SELECT 
    'OVERALL' as period,
    COUNT(*) as total_trades,
    COALESCE(SUM(unrealized_pnl), 0) as total_unrealized_pnl,
    COALESCE(SUM(realized_pnl), 0) as total_realized_pnl,
    COALESCE(SUM(unrealized_pnl + realized_pnl), 0) as total_combined_pnl
FROM trading.trades;

-- Detailed breakdown by status for today
SELECT 
    'TODAY_BY_STATUS' as period,
    status,
    COUNT(*) as trade_count,
    COALESCE(SUM(unrealized_pnl), 0) as total_unrealized_pnl,
    COALESCE(SUM(realized_pnl), 0) as total_realized_pnl,
    COALESCE(SUM(unrealized_pnl + realized_pnl), 0) as total_combined_pnl
FROM trading.trades 
WHERE DATE(created_at) = CURRENT_DATE
GROUP BY status
ORDER BY status;

-- Detailed breakdown by status for overall
SELECT 
    'OVERALL_BY_STATUS' as period,
    status,
    COUNT(*) as trade_count,
    COALESCE(SUM(unrealized_pnl), 0) as total_unrealized_pnl,
    COALESCE(SUM(realized_pnl), 0) as total_realized_pnl,
    COALESCE(SUM(unrealized_pnl + realized_pnl), 0) as total_combined_pnl
FROM trading.trades 
GROUP BY status
ORDER BY status;

-- Summary view combining all results
WITH today_totals AS (
    SELECT 
        COUNT(*) as trade_count,
        COALESCE(SUM(unrealized_pnl), 0) as unrealized_pnl,
        COALESCE(SUM(realized_pnl), 0) as realized_pnl,
        COALESCE(SUM(unrealized_pnl + realized_pnl), 0) as combined_pnl
    FROM trading.trades 
    WHERE DATE(created_at) = CURRENT_DATE
),
overall_totals AS (
    SELECT 
        COUNT(*) as trade_count,
        COALESCE(SUM(unrealized_pnl), 0) as unrealized_pnl,
        COALESCE(SUM(realized_pnl), 0) as realized_pnl,
        COALESCE(SUM(unrealized_pnl + realized_pnl), 0) as combined_pnl
    FROM trading.trades
)
SELECT 
    'SUMMARY' as report_type,
    'Today' as period,
    t.trade_count,
    t.unrealized_pnl,
    t.realized_pnl,
    t.combined_pnl
FROM today_totals t
UNION ALL
SELECT 
    'SUMMARY' as report_type,
    'Overall' as period,
    o.trade_count,
    o.unrealized_pnl,
    o.realized_pnl,
    o.combined_pnl
FROM overall_totals o
ORDER BY period;
