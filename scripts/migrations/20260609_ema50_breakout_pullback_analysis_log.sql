CREATE TABLE IF NOT EXISTS trading.strategy_ema50_breakout_pullback_analysis_log (
    id BIGSERIAL PRIMARY KEY,
    log_ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    venue TEXT NOT NULL,
    symbol TEXT NOT NULL,
    asset_class TEXT,
    timeframe TEXT NOT NULL DEFAULT '4h',
    setup_state TEXT,
    direction TEXT,
    ema50_side TEXT,
    signal TEXT,
    breakout_pass BOOLEAN,
    pullback_pass BOOLEAN,
    trigger_pass BOOLEAN,
    breakout_reason TEXT,
    pullback_reason TEXT,
    trigger_reason TEXT,
    swing_level NUMERIC,
    ema50_value NUMERIC,
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

CREATE INDEX IF NOT EXISTS idx_ema50_bp_audit_log_ts
    ON trading.strategy_ema50_breakout_pullback_analysis_log (log_ts DESC);

CREATE INDEX IF NOT EXISTS idx_ema50_bp_audit_venue_symbol
    ON trading.strategy_ema50_breakout_pullback_analysis_log (venue, symbol);

CREATE INDEX IF NOT EXISTS idx_ema50_bp_audit_gates
    ON trading.strategy_ema50_breakout_pullback_analysis_log (breakout_pass, pullback_pass, trigger_pass);

CREATE UNIQUE INDEX IF NOT EXISTS uq_ema50_bp_audit_venue_symbol_candle_source
    ON trading.strategy_ema50_breakout_pullback_analysis_log (venue, symbol, candle_ts, source)
    WHERE candle_ts IS NOT NULL;
