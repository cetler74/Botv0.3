CREATE TABLE IF NOT EXISTS trading.strategy_dual_sma_analysis_log (
    id BIGSERIAL PRIMARY KEY,
    log_ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    venue TEXT NOT NULL,
    symbol TEXT NOT NULL,
    asset_class TEXT,
    daily_bias TEXT,
    trend_15m TEXT,
    entry_signal_5m TEXT,
    signal TEXT,
    sma20_slope NUMERIC,
    price_vs_sma20_pct NUMERIC,
    price_vs_sma200_pct NUMERIC,
    extension_distance_pct NUMERIC,
    daily_gap_vs_sma200_pct NUMERIC,
    sma200_flat BOOLEAN,
    squeeze_detected BOOLEAN,
    daily_pass BOOLEAN,
    confirm_15m_pass BOOLEAN,
    entry_5m_pass BOOLEAN,
    precision_pass BOOLEAN,
    daily_reason TEXT,
    confirm_15m_reason TEXT,
    entry_5m_reason TEXT,
    precision_reason TEXT,
    entry_reason TEXT,
    reward_risk NUMERIC,
    entry_price NUMERIC,
    stop_hint NUMERIC,
    target_hint NUMERIC,
    record JSONB NOT NULL DEFAULT '{}'::jsonb,
    candle_ts TIMESTAMPTZ,
    source TEXT NOT NULL DEFAULT 'strategy-service',
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_dual_sma_audit_log_ts
    ON trading.strategy_dual_sma_analysis_log (log_ts DESC);

CREATE INDEX IF NOT EXISTS idx_dual_sma_audit_venue_symbol
    ON trading.strategy_dual_sma_analysis_log (venue, symbol);

CREATE INDEX IF NOT EXISTS idx_dual_sma_audit_gates
    ON trading.strategy_dual_sma_analysis_log (daily_pass, confirm_15m_pass, entry_5m_pass);

CREATE UNIQUE INDEX IF NOT EXISTS uq_dual_sma_audit_venue_symbol_candle_source
    ON trading.strategy_dual_sma_analysis_log (venue, symbol, candle_ts, source)
    WHERE candle_ts IS NOT NULL;
