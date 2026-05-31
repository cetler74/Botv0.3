-- Live Hyperliquid perp ledger (separate from trading.perp_paper_trades).
CREATE TABLE IF NOT EXISTS trading.perp_live_trades (
    trade_id UUID PRIMARY KEY,
    venue TEXT NOT NULL DEFAULT 'hyperliquid',
    coin TEXT NOT NULL,
    pair TEXT,
    source_exchange TEXT,
    source_pair TEXT,
    source_strategy TEXT NOT NULL,
    source_signal TEXT NOT NULL,
    position_side TEXT NOT NULL CHECK (position_side IN ('long', 'short')),
    leverage DOUBLE PRECISION NOT NULL DEFAULT 2.0,
    margin_used DOUBLE PRECISION NOT NULL DEFAULT 0,
    notional_size DOUBLE PRECISION NOT NULL DEFAULT 0,
    position_size DOUBLE PRECISION NOT NULL,
    entry_price DOUBLE PRECISION NOT NULL DEFAULT 0,
    current_price DOUBLE PRECISION,
    exit_price DOUBLE PRECISION,
    status TEXT NOT NULL DEFAULT 'OPEN',
    entry_time TIMESTAMPTZ NOT NULL,
    exit_time TIMESTAMPTZ,
    unrealized_pnl DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    realized_pnl DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    fees DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    funding DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    strength DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    consensus_confidence DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    consensus_agreement DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    mode TEXT NOT NULL DEFAULT 'live',
    exit_reason TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_perp_live_trades_status
    ON trading.perp_live_trades (status);
CREATE INDEX IF NOT EXISTS idx_perp_live_trades_coin_status
    ON trading.perp_live_trades (coin, status);
CREATE INDEX IF NOT EXISTS idx_perp_live_trades_source_strategy
    ON trading.perp_live_trades (source_strategy);
