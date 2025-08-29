-- Bybit Enhanced Order Tracking Database Migration - Version 2.6.0
-- This script adds Bybit-specific tables and enhancements for comprehensive order tracking

-- Migration: Add Bybit-specific order tracking tables
-- Date: 2025-08-28
-- Description: Enhanced database schema for Bybit WebSocket integration

-- =============================================================================
-- 1. CREATE BYBIT ORDER EXECUTIONS TABLE
-- =============================================================================

CREATE TABLE IF NOT EXISTS bybit_order_executions (
    id SERIAL PRIMARY KEY,
    execution_id VARCHAR(100) UNIQUE NOT NULL,
    order_id VARCHAR(100) NOT NULL,
    order_link_id VARCHAR(100),
    symbol VARCHAR(20) NOT NULL,
    side VARCHAR(10) NOT NULL,
    price DECIMAL(20, 8) NOT NULL,
    qty DECIMAL(20, 8) NOT NULL,
    exec_fee DECIMAL(20, 8) NOT NULL DEFAULT 0,
    exec_time TIMESTAMP WITH TIME ZONE NOT NULL,
    fee_rate DECIMAL(20, 8),
    exec_type VARCHAR(20),
    is_maker BOOLEAN DEFAULT FALSE,
    fee_currency VARCHAR(10) DEFAULT 'USDT',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_bybit_executions_order_id ON bybit_order_executions(order_id);
CREATE INDEX IF NOT EXISTS idx_bybit_executions_symbol ON bybit_order_executions(symbol);
CREATE INDEX IF NOT EXISTS idx_bybit_executions_exec_time ON bybit_order_executions(exec_time);
CREATE INDEX IF NOT EXISTS idx_bybit_executions_execution_id ON bybit_order_executions(execution_id);

-- =============================================================================
-- 2. CREATE BYBIT ORDER HISTORY TABLE
-- =============================================================================

CREATE TABLE IF NOT EXISTS bybit_order_history (
    id SERIAL PRIMARY KEY,
    order_id VARCHAR(100) NOT NULL,
    order_link_id VARCHAR(100),
    symbol VARCHAR(20) NOT NULL,
    side VARCHAR(10) NOT NULL,
    order_type VARCHAR(20) NOT NULL,
    price DECIMAL(20, 8),
    qty DECIMAL(20, 8) NOT NULL,
    cum_exec_qty DECIMAL(20, 8) NOT NULL DEFAULT 0,
    cum_exec_fee DECIMAL(20, 8) NOT NULL DEFAULT 0,
    avg_price DECIMAL(20, 8),
    order_status VARCHAR(20) NOT NULL,
    last_exec_price DECIMAL(20, 8),
    last_exec_qty DECIMAL(20, 8),
    exec_time TIMESTAMP WITH TIME ZONE,
    created_time TIMESTAMP WITH TIME ZONE,
    updated_time TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_bybit_order_history_order_id ON bybit_order_history(order_id);
CREATE INDEX IF NOT EXISTS idx_bybit_order_history_symbol ON bybit_order_history(symbol);
CREATE INDEX IF NOT EXISTS idx_bybit_order_history_status ON bybit_order_history(order_status);
CREATE INDEX IF NOT EXISTS idx_bybit_order_history_updated_time ON bybit_order_history(updated_time);

-- =============================================================================
-- 3. CREATE BYBIT POSITION HISTORY TABLE
-- =============================================================================

CREATE TABLE IF NOT EXISTS bybit_position_history (
    id SERIAL PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL,
    side VARCHAR(10) NOT NULL,
    size DECIMAL(20, 8) NOT NULL,
    avg_price DECIMAL(20, 8) NOT NULL,
    unrealized_pnl DECIMAL(20, 8) NOT NULL DEFAULT 0,
    mark_price DECIMAL(20, 8) NOT NULL,
    position_value DECIMAL(20, 8) NOT NULL,
    leverage DECIMAL(10, 2) DEFAULT 1,
    margin_type VARCHAR(10) DEFAULT 'REGULAR_MARGIN',
    position_idx INTEGER DEFAULT 0,
    position_status VARCHAR(20) DEFAULT 'Normal',
    auto_add_margin BOOLEAN DEFAULT FALSE,
    risk_id INTEGER DEFAULT 0,
    free_qty DECIMAL(20, 8) DEFAULT 0,
    tp_sl_mode VARCHAR(10) DEFAULT 'Full',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_bybit_position_history_symbol ON bybit_position_history(symbol);
CREATE INDEX IF NOT EXISTS idx_bybit_position_history_side ON bybit_position_history(side);
CREATE INDEX IF NOT EXISTS idx_bybit_position_history_updated_at ON bybit_position_history(updated_at);

-- =============================================================================
-- 4. CREATE BYBIT WALLET BALANCE HISTORY TABLE
-- =============================================================================

CREATE TABLE IF NOT EXISTS bybit_wallet_balance_history (
    id SERIAL PRIMARY KEY,
    currency VARCHAR(10) NOT NULL,
    wallet_balance DECIMAL(20, 8) NOT NULL,
    available_balance DECIMAL(20, 8) NOT NULL,
    used_margin DECIMAL(20, 8) NOT NULL DEFAULT 0,
    order_margin DECIMAL(20, 8) NOT NULL DEFAULT 0,
    position_margin DECIMAL(20, 8) NOT NULL DEFAULT 0,
    account_type VARCHAR(20) DEFAULT 'UNIFIED',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_bybit_wallet_balance_currency ON bybit_wallet_balance_history(currency);
CREATE INDEX IF NOT EXISTS idx_bybit_wallet_balance_created_at ON bybit_wallet_balance_history(created_at);

-- =============================================================================
-- 5. ENHANCE EXISTING TRADES TABLE FOR BYBIT
-- =============================================================================

-- Add Bybit-specific columns to trades table
ALTER TABLE trades ADD COLUMN IF NOT EXISTS bybit_order_id VARCHAR(100);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS bybit_order_link_id VARCHAR(100);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS bybit_execution_id VARCHAR(100);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS bybit_fee_currency VARCHAR(10) DEFAULT 'USDT';
ALTER TABLE trades ADD COLUMN IF NOT EXISTS bybit_is_maker BOOLEAN DEFAULT FALSE;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS bybit_exec_type VARCHAR(20);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS bybit_fee_rate DECIMAL(20, 8);

-- Indexes for new columns
CREATE INDEX IF NOT EXISTS idx_trades_bybit_order_id ON trades(bybit_order_id);
CREATE INDEX IF NOT EXISTS idx_trades_bybit_execution_id ON trades(bybit_execution_id);

-- =============================================================================
-- 6. CREATE BYBIT WEBSOCKET EVENT LOG TABLE
-- =============================================================================

CREATE TABLE IF NOT EXISTS bybit_websocket_events (
    id SERIAL PRIMARY KEY,
    event_type VARCHAR(50) NOT NULL,
    topic VARCHAR(50),
    event_data JSONB NOT NULL,
    processed BOOLEAN DEFAULT FALSE,
    processing_error TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    processed_at TIMESTAMP WITH TIME ZONE
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_bybit_websocket_events_type ON bybit_websocket_events(event_type);
CREATE INDEX IF NOT EXISTS idx_bybit_websocket_events_topic ON bybit_websocket_events(topic);
CREATE INDEX IF NOT EXISTS idx_bybit_websocket_events_processed ON bybit_websocket_events(processed);
CREATE INDEX IF NOT EXISTS idx_bybit_websocket_events_created_at ON bybit_websocket_events(created_at);

-- =============================================================================
-- 7. CREATE BYBIT ERROR LOG TABLE
-- =============================================================================

CREATE TABLE IF NOT EXISTS bybit_error_log (
    id SERIAL PRIMARY KEY,
    error_category VARCHAR(50) NOT NULL,
    error_severity VARCHAR(20) NOT NULL,
    error_type VARCHAR(100) NOT NULL,
    error_message TEXT NOT NULL,
    error_context JSONB,
    recovery_actions TEXT[],
    recovery_success BOOLEAN,
    circuit_breaker_state VARCHAR(20),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_bybit_error_log_category ON bybit_error_log(error_category);
CREATE INDEX IF NOT EXISTS idx_bybit_error_log_severity ON bybit_error_log(error_severity);
CREATE INDEX IF NOT EXISTS idx_bybit_error_log_created_at ON bybit_error_log(created_at);

-- =============================================================================
-- 8. CREATE VIEWS FOR BYBIT ANALYTICS
-- =============================================================================

-- View for Bybit order summary
CREATE OR REPLACE VIEW bybit_order_summary AS
SELECT 
    symbol,
    side,
    order_type,
    COUNT(*) as total_orders,
    COUNT(CASE WHEN order_status = 'Filled' THEN 1 END) as filled_orders,
    COUNT(CASE WHEN order_status = 'PartiallyFilled' THEN 1 END) as partial_orders,
    COUNT(CASE WHEN order_status = 'Cancelled' THEN 1 END) as cancelled_orders,
    SUM(cum_exec_qty) as total_executed_qty,
    SUM(cum_exec_fee) as total_executed_fees,
    AVG(avg_price) as avg_execution_price
FROM bybit_order_history
GROUP BY symbol, side, order_type;

-- View for Bybit execution summary
CREATE OR REPLACE VIEW bybit_execution_summary AS
SELECT 
    symbol,
    side,
    COUNT(*) as total_executions,
    SUM(qty) as total_executed_qty,
    SUM(exec_fee) as total_executed_fees,
    AVG(price) as avg_execution_price,
    MIN(exec_time) as first_execution,
    MAX(exec_time) as last_execution
FROM bybit_order_executions
GROUP BY symbol, side;

-- View for Bybit position summary
CREATE OR REPLACE VIEW bybit_position_summary AS
SELECT 
    symbol,
    side,
    COUNT(*) as position_count,
    SUM(size) as total_size,
    AVG(avg_price) as avg_entry_price,
    SUM(unrealized_pnl) as total_unrealized_pnl,
    AVG(mark_price) as current_mark_price
FROM bybit_position_history
GROUP BY symbol, side;

-- =============================================================================
-- 9. CREATE TRIGGERS FOR AUTOMATIC UPDATES
-- =============================================================================

-- Trigger to update updated_at timestamp on bybit_order_executions
CREATE OR REPLACE FUNCTION update_bybit_executions_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_bybit_executions_updated_at
    BEFORE UPDATE ON bybit_order_executions
    FOR EACH ROW
    EXECUTE FUNCTION update_bybit_executions_updated_at();

-- Trigger to update updated_at timestamp on bybit_order_history
CREATE OR REPLACE FUNCTION update_bybit_order_history_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_bybit_order_history_updated_at
    BEFORE UPDATE ON bybit_order_history
    FOR EACH ROW
    EXECUTE FUNCTION update_bybit_order_history_updated_at();

-- Trigger to update updated_at timestamp on bybit_position_history
CREATE OR REPLACE FUNCTION update_bybit_position_history_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_bybit_position_history_updated_at
    BEFORE UPDATE ON bybit_position_history
    FOR EACH ROW
    EXECUTE FUNCTION update_bybit_position_history_updated_at();

-- =============================================================================
-- 10. CREATE FUNCTIONS FOR BYBIT DATA MANAGEMENT
-- =============================================================================

-- Function to insert or update Bybit order execution
CREATE OR REPLACE FUNCTION upsert_bybit_execution(
    p_execution_id VARCHAR(100),
    p_order_id VARCHAR(100),
    p_order_link_id VARCHAR(100),
    p_symbol VARCHAR(20),
    p_side VARCHAR(10),
    p_price DECIMAL(20, 8),
    p_qty DECIMAL(20, 8),
    p_exec_fee DECIMAL(20, 8),
    p_exec_time TIMESTAMP WITH TIME ZONE,
    p_fee_rate DECIMAL(20, 8) DEFAULT NULL,
    p_exec_type VARCHAR(20) DEFAULT NULL,
    p_is_maker BOOLEAN DEFAULT FALSE,
    p_fee_currency VARCHAR(10) DEFAULT 'USDT'
)
RETURNS INTEGER AS $$
DECLARE
    execution_id INTEGER;
BEGIN
    INSERT INTO bybit_order_executions (
        execution_id, order_id, order_link_id, symbol, side, price, qty, 
        exec_fee, exec_time, fee_rate, exec_type, is_maker, fee_currency
    ) VALUES (
        p_execution_id, p_order_id, p_order_link_id, p_symbol, p_side, p_price, p_qty,
        p_exec_fee, p_exec_time, p_fee_rate, p_exec_type, p_is_maker, p_fee_currency
    )
    ON CONFLICT (execution_id) DO UPDATE SET
        order_id = EXCLUDED.order_id,
        order_link_id = EXCLUDED.order_link_id,
        symbol = EXCLUDED.symbol,
        side = EXCLUDED.side,
        price = EXCLUDED.price,
        qty = EXCLUDED.qty,
        exec_fee = EXCLUDED.exec_fee,
        exec_time = EXCLUDED.exec_time,
        fee_rate = EXCLUDED.fee_rate,
        exec_type = EXCLUDED.exec_type,
        is_maker = EXCLUDED.is_maker,
        fee_currency = EXCLUDED.fee_currency,
        updated_at = CURRENT_TIMESTAMP
    RETURNING id INTO execution_id;
    
    RETURN execution_id;
END;
$$ LANGUAGE plpgsql;

-- Function to insert or update Bybit order history
CREATE OR REPLACE FUNCTION upsert_bybit_order_history(
    p_order_id VARCHAR(100),
    p_order_link_id VARCHAR(100),
    p_symbol VARCHAR(20),
    p_side VARCHAR(10),
    p_order_type VARCHAR(20),
    p_price DECIMAL(20, 8),
    p_qty DECIMAL(20, 8),
    p_cum_exec_qty DECIMAL(20, 8),
    p_cum_exec_fee DECIMAL(20, 8),
    p_avg_price DECIMAL(20, 8),
    p_order_status VARCHAR(20),
    p_last_exec_price DECIMAL(20, 8) DEFAULT NULL,
    p_last_exec_qty DECIMAL(20, 8) DEFAULT NULL,
    p_exec_time TIMESTAMP WITH TIME ZONE DEFAULT NULL,
    p_created_time TIMESTAMP WITH TIME ZONE DEFAULT NULL,
    p_updated_time TIMESTAMP WITH TIME ZONE DEFAULT NULL
)
RETURNS INTEGER AS $$
DECLARE
    order_id INTEGER;
BEGIN
    INSERT INTO bybit_order_history (
        order_id, order_link_id, symbol, side, order_type, price, qty,
        cum_exec_qty, cum_exec_fee, avg_price, order_status, last_exec_price,
        last_exec_qty, exec_time, created_time, updated_time
    ) VALUES (
        p_order_id, p_order_link_id, p_symbol, p_side, p_order_type, p_price, p_qty,
        p_cum_exec_qty, p_cum_exec_fee, p_avg_price, p_order_status, p_last_exec_price,
        p_last_exec_qty, p_exec_time, p_created_time, p_updated_time
    )
    ON CONFLICT (order_id) DO UPDATE SET
        order_link_id = EXCLUDED.order_link_id,
        symbol = EXCLUDED.symbol,
        side = EXCLUDED.side,
        order_type = EXCLUDED.order_type,
        price = EXCLUDED.price,
        qty = EXCLUDED.qty,
        cum_exec_qty = EXCLUDED.cum_exec_qty,
        cum_exec_fee = EXCLUDED.cum_exec_fee,
        avg_price = EXCLUDED.avg_price,
        order_status = EXCLUDED.order_status,
        last_exec_price = EXCLUDED.last_exec_price,
        last_exec_qty = EXCLUDED.last_exec_qty,
        exec_time = EXCLUDED.exec_time,
        created_time = EXCLUDED.created_time,
        updated_time = EXCLUDED.updated_time,
        updated_at = CURRENT_TIMESTAMP
    RETURNING id INTO order_id;
    
    RETURN order_id;
END;
$$ LANGUAGE plpgsql;

-- =============================================================================
-- 11. MIGRATION COMPLETION
-- =============================================================================

-- Insert migration record
INSERT INTO schema_migrations (version, description, applied_at) 
VALUES ('2.6.0_bybit_enhanced_tracking', 'Bybit Enhanced Order Tracking Schema', CURRENT_TIMESTAMP)
ON CONFLICT (version) DO NOTHING;

-- Log migration completion
DO $$
BEGIN
    RAISE NOTICE 'Bybit Enhanced Order Tracking migration completed successfully';
    RAISE NOTICE 'Tables created: bybit_order_executions, bybit_order_history, bybit_position_history, bybit_wallet_balance_history, bybit_websocket_events, bybit_error_log';
    RAISE NOTICE 'Views created: bybit_order_summary, bybit_execution_summary, bybit_position_summary';
    RAISE NOTICE 'Functions created: upsert_bybit_execution, upsert_bybit_order_history';
    RAISE NOTICE 'Triggers created: Automatic updated_at timestamp updates';
END $$;
