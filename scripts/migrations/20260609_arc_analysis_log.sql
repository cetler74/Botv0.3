CREATE TABLE IF NOT EXISTS trading.strategy_arc_analysis_log (
    id BIGSERIAL PRIMARY KEY,
    log_ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    venue TEXT NOT NULL,
    symbol TEXT NOT NULL,
    asset_class TEXT,
    box_high NUMERIC,
    box_low NUMERIC,
    swing_high NUMERIC,
    swing_low NUMERIC,
    range_size NUMERIC,
    range_pct_move NUMERIC,
    range_geometry_tag TEXT,
    zone TEXT,
    zone_level TEXT,
    area_pass BOOLEAN,
    range_pass BOOLEAN,
    candle_pass BOOLEAN,
    area_reason TEXT,
    range_reason TEXT,
    candle_reason TEXT,
    signal TEXT,
    setup_state TEXT,
    entry_price NUMERIC,
    stop_hint NUMERIC,
    target_hint NUMERIC,
    target_50 NUMERIC,
    target_100 NUMERIC,
    reward_risk NUMERIC,
    invalidation_reason TEXT,
    entry_reason TEXT,
    record JSONB NOT NULL DEFAULT '{}'::jsonb,
    candle_ts TIMESTAMPTZ,
    source TEXT NOT NULL DEFAULT 'strategy-service',
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_arc_audit_log_ts
    ON trading.strategy_arc_analysis_log (log_ts DESC);

CREATE INDEX IF NOT EXISTS idx_arc_audit_venue_symbol
    ON trading.strategy_arc_analysis_log (venue, symbol);

CREATE INDEX IF NOT EXISTS idx_arc_audit_gates
    ON trading.strategy_arc_analysis_log (area_pass, range_pass, candle_pass);

CREATE UNIQUE INDEX IF NOT EXISTS uq_arc_audit_venue_symbol_candle_source
    ON trading.strategy_arc_analysis_log (venue, symbol, candle_ts, source)
    WHERE candle_ts IS NOT NULL;
