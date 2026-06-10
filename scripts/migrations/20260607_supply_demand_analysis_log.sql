CREATE TABLE IF NOT EXISTS trading.strategy_supply_demand_analysis_log (
    id BIGSERIAL PRIMARY KEY,
    log_ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    venue TEXT NOT NULL,
    symbol TEXT NOT NULL,
    asset_class TEXT,
    timeframe_mode TEXT,
    structure_timeframe TEXT,
    entry_timeframe TEXT,
    trend_direction TEXT,
    signal TEXT,
    step1_pass BOOLEAN,
    step2_pass BOOLEAN,
    step3_pass BOOLEAN,
    step1_reason TEXT,
    step2_reason TEXT,
    step3_reason TEXT,
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

CREATE INDEX IF NOT EXISTS idx_sd_audit_log_ts
    ON trading.strategy_supply_demand_analysis_log (log_ts DESC);

CREATE INDEX IF NOT EXISTS idx_sd_audit_venue_symbol
    ON trading.strategy_supply_demand_analysis_log (venue, symbol);

CREATE INDEX IF NOT EXISTS idx_sd_audit_steps
    ON trading.strategy_supply_demand_analysis_log (step1_pass, step2_pass, step3_pass);

CREATE UNIQUE INDEX IF NOT EXISTS uq_sd_audit_venue_symbol_candle_source
    ON trading.strategy_supply_demand_analysis_log (venue, symbol, candle_ts, source)
    WHERE candle_ts IS NOT NULL;
