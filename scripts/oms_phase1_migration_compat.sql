-- OMS Phase 1: Event Sourcing Schema Migration (Compatibility Version)
-- Creates foundational tables for event-driven order management system
-- Compatible with existing VARCHAR trade_id format
-- Version: 1.0-compat
-- Date: 2025-01-09

-- Set search path
SET search_path TO trading;

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- First, ensure trade_id has unique constraint (needed for foreign keys)
DO $$ 
BEGIN
    ALTER TABLE trading.trades ADD CONSTRAINT trades_trade_id_unique UNIQUE (trade_id);
EXCEPTION 
    WHEN duplicate_table THEN 
        RAISE NOTICE 'Constraint trades_trade_id_unique already exists, skipping';
    WHEN others THEN
        RAISE NOTICE 'Could not add constraint, continuing: %', SQLERRM;
END $$;

-- =====================================================
-- EVENTS TABLE - Core event sourcing table
-- =====================================================
CREATE TABLE IF NOT EXISTS trading.events (
    event_id UUID PRIMARY KEY DEFAULT public.uuid_generate_v4(),
    aggregate_id VARCHAR(255) NOT NULL,  -- Usually local_order_id or trade_id
    aggregate_type VARCHAR(50) NOT NULL, -- 'order', 'trade', 'balance'
    event_type VARCHAR(100) NOT NULL,    -- 'OrderCreated', 'ExchangeAck', 'OrderUpdate', 'Fill', etc.
    payload JSONB NOT NULL,              -- Event data in JSON format
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    sequence_number BIGSERIAL,           -- For ordering events per aggregate
    event_version INTEGER DEFAULT 1,     -- For event schema evolution
    correlation_id UUID,                 -- For tracing related events
    causation_id UUID                    -- Event that caused this event
);

-- =====================================================
-- ORDER_MAPPINGS TABLE - Local to Exchange ID mapping
-- =====================================================
CREATE TABLE IF NOT EXISTS trading.order_mappings (
    id SERIAL PRIMARY KEY,
    local_order_id UUID NOT NULL,                    -- Our internal order ID
    exchange VARCHAR(50) NOT NULL,                   -- Exchange name
    exchange_order_id VARCHAR(255) NOT NULL,         -- Exchange's order ID
    client_order_id VARCHAR(255) NOT NULL,           -- Our client order ID sent to exchange
    symbol VARCHAR(50) NOT NULL,                     -- Trading pair
    side VARCHAR(10) NOT NULL,                       -- 'buy' or 'sell'
    order_type VARCHAR(20) NOT NULL,                 -- 'market', 'limit', 'stop'
    amount DECIMAL(20,8) NOT NULL,                   -- Order amount
    price DECIMAL(20,8),                             -- Order price (null for market orders)
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',   -- 'PENDING', 'ACKNOWLEDGED', 'FILLED', 'CANCELLED', 'REJECTED'
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    acknowledged_at TIMESTAMP WITH TIME ZONE,        -- When exchange acknowledged the order
    trade_id VARCHAR(255) REFERENCES trading.trades(trade_id) -- Link to trade record (VARCHAR compatibility)
);

-- =====================================================
-- FILLS TABLE - Granular execution tracking
-- =====================================================
CREATE TABLE IF NOT EXISTS trading.fills (
    fill_id UUID PRIMARY KEY DEFAULT public.uuid_generate_v4(),
    local_order_id UUID NOT NULL,                    -- References order_mappings.local_order_id
    exchange_order_id VARCHAR(255) NOT NULL,         -- Exchange order ID
    exchange VARCHAR(50) NOT NULL,                   -- Exchange name
    symbol VARCHAR(50) NOT NULL,                     -- Trading pair
    side VARCHAR(10) NOT NULL,                       -- 'buy' or 'sell'
    qty DECIMAL(20,8) NOT NULL,                      -- Fill quantity
    price DECIMAL(20,8) NOT NULL,                    -- Fill price
    fee DECIMAL(20,8) NOT NULL DEFAULT 0,            -- Fee amount
    fee_asset VARCHAR(10) NOT NULL DEFAULT 'USD',    -- Fee currency
    trade_id VARCHAR(255),                           -- Exchange's trade ID (different from our trade_id)
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL,     -- Fill timestamp from exchange
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    is_maker BOOLEAN DEFAULT false,                  -- Maker vs taker fee calculation
    commission_rate DECIMAL(10,6)                    -- Commission rate applied
);

-- =====================================================
-- ASSET_POSITIONS TABLE - Real-time asset tracking
-- =====================================================
CREATE TABLE IF NOT EXISTS trading.asset_positions (
    id SERIAL PRIMARY KEY,
    exchange VARCHAR(50) NOT NULL,
    asset VARCHAR(20) NOT NULL,                      -- 'BTC', 'ETH', 'USDT', etc.
    free_balance DECIMAL(20,8) NOT NULL DEFAULT 0,   -- Available balance
    used_balance DECIMAL(20,8) NOT NULL DEFAULT 0,   -- Balance in open orders
    total_balance DECIMAL(20,8) NOT NULL DEFAULT 0,  -- Total balance
    last_updated TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- =====================================================
-- Create missing functions if needed
-- =====================================================
CREATE OR REPLACE FUNCTION trading.update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- =====================================================
-- INDEXES for Performance
-- =====================================================

-- Events table indexes
CREATE INDEX IF NOT EXISTS idx_events_aggregate_id ON trading.events(aggregate_id);
CREATE INDEX IF NOT EXISTS idx_events_aggregate_type ON trading.events(aggregate_type);
CREATE INDEX IF NOT EXISTS idx_events_event_type ON trading.events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_created_at ON trading.events(created_at);
CREATE INDEX IF NOT EXISTS idx_events_sequence_number ON trading.events(sequence_number);
CREATE INDEX IF NOT EXISTS idx_events_correlation_id ON trading.events(correlation_id);

-- Composite index for aggregate event ordering
CREATE INDEX IF NOT EXISTS idx_events_aggregate_sequence ON trading.events(aggregate_id, sequence_number);

-- Order mappings indexes
CREATE INDEX IF NOT EXISTS idx_order_mappings_local_order_id ON trading.order_mappings(local_order_id);
CREATE INDEX IF NOT EXISTS idx_order_mappings_exchange_order_id ON trading.order_mappings(exchange_order_id);
CREATE INDEX IF NOT EXISTS idx_order_mappings_client_order_id ON trading.order_mappings(client_order_id);
CREATE INDEX IF NOT EXISTS idx_order_mappings_exchange ON trading.order_mappings(exchange);
CREATE INDEX IF NOT EXISTS idx_order_mappings_status ON trading.order_mappings(status);
CREATE INDEX IF NOT EXISTS idx_order_mappings_trade_id ON trading.order_mappings(trade_id);
CREATE INDEX IF NOT EXISTS idx_order_mappings_symbol ON trading.order_mappings(symbol);

-- Fills table indexes
CREATE INDEX IF NOT EXISTS idx_fills_local_order_id ON trading.fills(local_order_id);
CREATE INDEX IF NOT EXISTS idx_fills_exchange_order_id ON trading.fills(exchange_order_id);
CREATE INDEX IF NOT EXISTS idx_fills_exchange ON trading.fills(exchange);
CREATE INDEX IF NOT EXISTS idx_fills_symbol ON trading.fills(symbol);
CREATE INDEX IF NOT EXISTS idx_fills_timestamp ON trading.fills(timestamp);
CREATE INDEX IF NOT EXISTS idx_fills_trade_id ON trading.fills(trade_id);

-- Composite index for fill queries
CREATE INDEX IF NOT EXISTS idx_fills_exchange_symbol ON trading.fills(exchange, symbol);

-- Asset positions indexes
CREATE INDEX IF NOT EXISTS idx_asset_positions_exchange ON trading.asset_positions(exchange);
CREATE INDEX IF NOT EXISTS idx_asset_positions_asset ON trading.asset_positions(asset);
CREATE INDEX IF NOT EXISTS idx_asset_positions_last_updated ON trading.asset_positions(last_updated);

-- =====================================================
-- UNIQUE CONSTRAINTS
-- =====================================================

-- Ensure unique exchange order mappings
CREATE UNIQUE INDEX IF NOT EXISTS idx_order_mappings_exchange_order_unique 
    ON trading.order_mappings(exchange, exchange_order_id);

-- Ensure unique client order IDs (critical for idempotency)
CREATE UNIQUE INDEX IF NOT EXISTS idx_order_mappings_client_order_unique 
    ON trading.order_mappings(client_order_id);

-- Ensure unique asset positions per exchange
CREATE UNIQUE INDEX IF NOT EXISTS idx_asset_positions_exchange_asset_unique 
    ON trading.asset_positions(exchange, asset);

-- Prevent duplicate fills (same exchange trade_id should only appear once)
CREATE UNIQUE INDEX IF NOT EXISTS idx_fills_exchange_trade_unique 
    ON trading.fills(exchange, trade_id) 
    WHERE trade_id IS NOT NULL;

-- =====================================================
-- TRIGGERS for automatic timestamp updates
-- =====================================================

-- Order mappings trigger
DROP TRIGGER IF EXISTS update_order_mappings_updated_at ON trading.order_mappings;
CREATE TRIGGER update_order_mappings_updated_at 
    BEFORE UPDATE ON trading.order_mappings
    FOR EACH ROW EXECUTE FUNCTION trading.update_updated_at_column();

-- Asset positions trigger
DROP TRIGGER IF EXISTS update_asset_positions_updated_at ON trading.asset_positions;
CREATE TRIGGER update_asset_positions_updated_at 
    BEFORE UPDATE ON trading.asset_positions
    FOR EACH ROW EXECUTE FUNCTION trading.update_updated_at_column();

-- =====================================================
-- VIEWS for common queries
-- =====================================================

-- Latest events per aggregate view
DROP VIEW IF EXISTS trading.latest_events_per_aggregate CASCADE;
CREATE VIEW trading.latest_events_per_aggregate AS
SELECT DISTINCT ON (aggregate_id, aggregate_type) 
    event_id,
    aggregate_id,
    aggregate_type,
    event_type,
    payload,
    created_at,
    sequence_number
FROM trading.events
ORDER BY aggregate_id, aggregate_type, sequence_number DESC;

-- Order status summary view
DROP VIEW IF EXISTS trading.order_status_summary CASCADE;
CREATE VIEW trading.order_status_summary AS
SELECT 
    om.local_order_id,
    om.exchange,
    om.symbol,
    om.side,
    om.order_type,
    om.amount,
    om.price,
    om.status,
    om.created_at,
    om.acknowledged_at,
    COALESCE(f.total_filled, 0) as filled_amount,
    COALESCE(f.avg_price, 0) as avg_fill_price,
    COALESCE(f.total_fees, 0) as total_fees,
    f.fill_count
FROM trading.order_mappings om
LEFT JOIN (
    SELECT 
        local_order_id,
        SUM(qty) as total_filled,
        AVG(price) as avg_price,
        SUM(fee) as total_fees,
        COUNT(*) as fill_count
    FROM trading.fills
    GROUP BY local_order_id
) f ON om.local_order_id = f.local_order_id;

-- =====================================================
-- INITIAL DATA and VALIDATION
-- =====================================================

-- Validate migration success
DO $$ 
BEGIN 
    -- Check if all tables exist
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'trading' AND table_name = 'events') THEN
        RAISE EXCEPTION 'Migration failed: events table not created';
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'trading' AND table_name = 'order_mappings') THEN
        RAISE EXCEPTION 'Migration failed: order_mappings table not created';
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'trading' AND table_name = 'fills') THEN
        RAISE EXCEPTION 'Migration failed: fills table not created';
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'trading' AND table_name = 'asset_positions') THEN
        RAISE EXCEPTION 'Migration failed: asset_positions table not created';
    END IF;
    
    RAISE NOTICE 'OMS Phase 1 migration completed successfully!';
    RAISE NOTICE 'Created tables: events, order_mappings, fills, asset_positions';
    RAISE NOTICE 'Created indexes and constraints for performance and data integrity';
    RAISE NOTICE 'Created views: latest_events_per_aggregate, order_status_summary';
END $$;

-- Create config_audit table if it doesn't exist
CREATE TABLE IF NOT EXISTS trading.config_audit (
    id SERIAL PRIMARY KEY,
    component VARCHAR(50) NOT NULL,
    config_key VARCHAR(100) NOT NULL,
    old_value TEXT,
    new_value TEXT NOT NULL,
    changed_by VARCHAR(50) NOT NULL,
    changed_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Add migration record
INSERT INTO trading.config_audit (component, config_key, new_value, changed_by)
VALUES ('oms', 'phase1_migration_compat', 'completed', 'system')
ON CONFLICT DO NOTHING;
