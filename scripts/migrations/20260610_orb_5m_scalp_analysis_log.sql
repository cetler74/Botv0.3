CREATE TABLE IF NOT EXISTS trading.strategy_orb_5m_scalp_analysis_log (
    id BIGSERIAL PRIMARY KEY,
    log_ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    venue TEXT NOT NULL,
    symbol TEXT NOT NULL,
    asset_class TEXT,
    timeframe TEXT NOT NULL DEFAULT '5m',
    setup_state TEXT,
    direction TEXT,
    signal TEXT,
    or_high NUMERIC,
    or_low NUMERIC,
    or_mid NUMERIC,
    breakout_valid BOOLEAN,
    retest_valid BOOLEAN,
    breakout_reason TEXT,
    retest_reason TEXT,
    rejection_reason TEXT,
    entry_price NUMERIC,
    stop_hint NUMERIC,
    target_hint NUMERIC,
    reward_risk NUMERIC,
    entry_reason TEXT,
    invalidation_reason TEXT,
    record JSONB NOT NULL DEFAULT '{}'::jsonb,
    candle_ts TIMESTAMPTZ,
    source TEXT NOT NULL DEFAULT 'strategy-service',
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_orb_5m_scalp_audit_log_ts
    ON trading.strategy_orb_5m_scalp_analysis_log (log_ts DESC);

CREATE INDEX IF NOT EXISTS idx_orb_5m_scalp_audit_venue_symbol
    ON trading.strategy_orb_5m_scalp_analysis_log (venue, symbol);

CREATE INDEX IF NOT EXISTS idx_orb_5m_scalp_audit_gates
    ON trading.strategy_orb_5m_scalp_analysis_log (breakout_valid, retest_valid);

CREATE UNIQUE INDEX IF NOT EXISTS uq_orb_5m_scalp_audit_venue_symbol_candle_source
    ON trading.strategy_orb_5m_scalp_analysis_log (venue, symbol, candle_ts, source)
    WHERE candle_ts IS NOT NULL;
