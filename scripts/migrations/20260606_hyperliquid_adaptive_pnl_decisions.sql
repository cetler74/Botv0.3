-- Durable audit log for Hyperliquid adaptive paper-perp PnL controls.
-- This is the source of truth for webpage reporting and future tuning.

CREATE TABLE IF NOT EXISTS trading.hyperliquid_adaptive_pnl_decisions (
    id BIGSERIAL PRIMARY KEY,
    decision_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    decision_type TEXT,
    action TEXT,
    target_type TEXT,
    target TEXT,
    side TEXT,
    situation TEXT,
    config_path TEXT,
    old_value JSONB,
    new_value JSONB,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    intended_effect TEXT,
    current_outcome JSONB NOT NULL DEFAULT '{}'::jsonb,
    live_since TIMESTAMPTZ NOT NULL,
    first_seen_at TIMESTAMPTZ,
    last_seen_at TIMESTAMPTZ,
    released_at TIMESTAMPTZ,
    release_reason TEXT,
    apply_status TEXT NOT NULL DEFAULT 'pending_reload',
    applied_at TIMESTAMPTZ,
    applied_to_cycle BIGINT,
    reload_required BOOLEAN NOT NULL DEFAULT TRUE,
    reload_verified_at TIMESTAMPTZ,
    source TEXT NOT NULL DEFAULT 'orchestrator',
    record JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (decision_key, live_since)
);

ALTER TABLE trading.hyperliquid_adaptive_pnl_decisions
    ADD COLUMN IF NOT EXISTS decision_type TEXT,
    ADD COLUMN IF NOT EXISTS apply_status TEXT NOT NULL DEFAULT 'pending_reload',
    ADD COLUMN IF NOT EXISTS applied_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS applied_to_cycle BIGINT,
    ADD COLUMN IF NOT EXISTS reload_required BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS reload_verified_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_hl_adaptive_pnl_status
    ON trading.hyperliquid_adaptive_pnl_decisions (status);
CREATE INDEX IF NOT EXISTS idx_hl_adaptive_pnl_decision_key
    ON trading.hyperliquid_adaptive_pnl_decisions (decision_key);
CREATE INDEX IF NOT EXISTS idx_hl_adaptive_pnl_last_seen
    ON trading.hyperliquid_adaptive_pnl_decisions (last_seen_at DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_hl_adaptive_pnl_target
    ON trading.hyperliquid_adaptive_pnl_decisions (target_type, target, side);
