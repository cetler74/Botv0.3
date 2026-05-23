-- Post-trade TA audit: regime, strategy, PnL, entry/exit reasons (90d window).
SELECT
    entry_time::date AS entry_day,
    strategy,
    substring(entry_reason FROM 'stable_regime=([^,\]]+)') AS stable_regime,
    status,
    ROUND(realized_pnl::numeric, 4) AS pnl,
    LEFT(entry_reason, 200) AS entry_reason,
    LEFT(exit_reason, 120) AS exit_reason
FROM trading.trades
WHERE entry_time >= NOW() - INTERVAL '90 days'
ORDER BY entry_time DESC;
