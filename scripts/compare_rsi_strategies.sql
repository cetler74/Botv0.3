-- Head-to-head performance report for RSI strategies.
-- Usage:
--   docker exec trading-bot-postgres psql -U carloslarramba -d trading_bot_futures -f /path/to/this.sql

\echo '=== Overall: rsi_oversold_override vs rsi_oversold_checklist ==='
WITH t AS (
    SELECT
        strategy,
        realized_pnl::float8 AS rpnl,
        entry_time,
        exit_time,
        pair,
        exchange,
        COALESCE((regexp_match(entry_reason, 'stable_regime=([^,\]]+)'))[1], 'unknown') AS stable_regime
    FROM trading.trades
    WHERE status = 'CLOSED'
      AND strategy IN ('rsi_oversold_override', 'rsi_oversold_checklist')
      AND realized_pnl IS NOT NULL
)
SELECT
    strategy,
    COUNT(*) AS trades,
    ROUND(100.0 * AVG((rpnl > 0)::int), 2) AS win_rate_pct,
    ROUND(AVG(rpnl)::numeric, 4) AS avg_pnl,
    ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY rpnl)::numeric, 4) AS median_pnl,
    ROUND(SUM(rpnl)::numeric, 4) AS total_pnl,
    ROUND(MIN(rpnl)::numeric, 4) AS worst_trade,
    ROUND(MAX(rpnl)::numeric, 4) AS best_trade
FROM t
GROUP BY strategy
ORDER BY strategy;

\echo ''
\echo '=== By stable_regime (min 3 trades per bucket) ==='
WITH t AS (
    SELECT
        strategy,
        realized_pnl::float8 AS rpnl,
        COALESCE((regexp_match(entry_reason, 'stable_regime=([^,\]]+)'))[1], 'unknown') AS stable_regime
    FROM trading.trades
    WHERE status = 'CLOSED'
      AND strategy IN ('rsi_oversold_override', 'rsi_oversold_checklist')
      AND realized_pnl IS NOT NULL
)
SELECT
    strategy,
    stable_regime,
    COUNT(*) AS trades,
    ROUND(100.0 * AVG((rpnl > 0)::int), 2) AS win_rate_pct,
    ROUND(AVG(rpnl)::numeric, 4) AS avg_pnl,
    ROUND(SUM(rpnl)::numeric, 4) AS total_pnl
FROM t
GROUP BY strategy, stable_regime
HAVING COUNT(*) >= 3
ORDER BY strategy, trades DESC;
