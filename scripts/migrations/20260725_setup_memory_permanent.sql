-- Durable permanent setup-memory outcomes (consecutive same-condition wins/losses).
-- Source of truth for permanent block/promote decisions across restarts.

CREATE TABLE IF NOT EXISTS trading.setup_memory_permanent (
    id BIGSERIAL PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    match_level TEXT NOT NULL,
    outcome TEXT NOT NULL,
    market_type TEXT NOT NULL,
    strategy_key TEXT NOT NULL,
    strategy_version TEXT,
    config_hash TEXT,
    side TEXT NOT NULL,
    coin TEXT NOT NULL,
    regime TEXT,
    why TEXT,
    streak_count INT NOT NULL DEFAULT 0,
    source_mix TEXT NOT NULL DEFAULT 'real',
    evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    released_at TIMESTAMPTZ,
    release_reason TEXT,
    CONSTRAINT setup_memory_permanent_outcome_chk
        CHECK (outcome IN ('block', 'promote')),
    CONSTRAINT setup_memory_permanent_match_level_chk
        CHECK (match_level IN ('exact', 'strategy_side_coin_regime')),
    CONSTRAINT setup_memory_permanent_status_chk
        CHECK (status IN ('active', 'released', 'superseded'))
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_setup_memory_permanent_active
    ON trading.setup_memory_permanent (fingerprint, outcome, match_level)
    WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_setup_memory_permanent_status
    ON trading.setup_memory_permanent (status);

CREATE INDEX IF NOT EXISTS idx_setup_memory_permanent_strategy
    ON trading.setup_memory_permanent (market_type, strategy_key, side, coin);

CREATE INDEX IF NOT EXISTS idx_setup_memory_permanent_updated
    ON trading.setup_memory_permanent (updated_at DESC);
