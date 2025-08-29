-- Crypto.com Enhanced Order Tracking Migration - Version 2.6.0
-- Migration script to support Crypto.com specific order data and enhanced tracking

-- Begin Transaction
BEGIN;

-- ================================================
-- 1. Add Crypto.com specific columns to orders table
-- ================================================

-- Add exchange-specific columns to orders table if they don't exist
DO $$ 
BEGIN
    -- Add exchange column if it doesn't exist
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='orders' AND column_name='exchange') THEN
        ALTER TABLE orders ADD COLUMN exchange VARCHAR(50) DEFAULT 'binance';
    END IF;
    
    -- Add Crypto.com specific order ID tracking
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='orders' AND column_name='exchange_order_id') THEN
        ALTER TABLE orders ADD COLUMN exchange_order_id VARCHAR(100);
    END IF;
    
    -- Add Crypto.com client order ID tracking
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='orders' AND column_name='exchange_client_order_id') THEN
        ALTER TABLE orders ADD COLUMN exchange_client_order_id VARCHAR(100);
    END IF;
    
    -- Add WebSocket event tracking
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='orders' AND column_name='websocket_event_received') THEN
        ALTER TABLE orders ADD COLUMN websocket_event_received BOOLEAN DEFAULT FALSE;
    END IF;
    
    -- Add WebSocket event timestamp
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='orders' AND column_name='websocket_event_time') THEN
        ALTER TABLE orders ADD COLUMN websocket_event_time TIMESTAMP WITH TIME ZONE;
    END IF;
    
    -- Add order update source tracking
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='orders' AND column_name='update_source') THEN
        ALTER TABLE orders ADD COLUMN update_source VARCHAR(20) DEFAULT 'rest_api';
    END IF;
END $$;

-- ================================================
-- 2. Create Crypto.com specific order events table
-- ================================================

-- Create order events table for detailed WebSocket event tracking
CREATE TABLE IF NOT EXISTS cryptocom_order_events (
    id SERIAL PRIMARY KEY,
    event_id VARCHAR(100) UNIQUE,
    order_id UUID REFERENCES orders(id) ON DELETE CASCADE,
    exchange_order_id VARCHAR(100) NOT NULL,
    exchange_client_order_id VARCHAR(100),
    
    -- Event details
    event_type VARCHAR(50) NOT NULL, -- order_update, trade_execution, balance_update
    event_channel VARCHAR(50), -- user.order, user.trade, user.balance
    event_timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
    
    -- Order status information
    order_status VARCHAR(50), -- NEW, FILLED, PARTIALLY_FILLED, CANCELLED, REJECTED
    order_side VARCHAR(10), -- BUY, SELL
    order_type VARCHAR(20), -- MARKET, LIMIT, STOP_MARKET, etc.
    
    -- Quantities and pricing
    quantity DECIMAL(20, 8),
    filled_quantity DECIMAL(20, 8),
    remaining_quantity DECIMAL(20, 8),
    price DECIMAL(20, 8),
    average_price DECIMAL(20, 8),
    
    -- Fee information
    fee_amount DECIMAL(20, 8),
    fee_currency VARCHAR(10),
    
    -- Trade execution details (for trade events)
    trade_id VARCHAR(100),
    trade_price DECIMAL(20, 8),
    trade_quantity DECIMAL(20, 8),
    trade_time TIMESTAMP WITH TIME ZONE,
    
    -- Raw event data
    raw_event_data JSONB,
    
    -- Metadata
    processed BOOLEAN DEFAULT FALSE,
    processing_errors TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Create indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_cryptocom_order_events_order_id ON cryptocom_order_events(order_id);
CREATE INDEX IF NOT EXISTS idx_cryptocom_order_events_exchange_order_id ON cryptocom_order_events(exchange_order_id);
CREATE INDEX IF NOT EXISTS idx_cryptocom_order_events_event_type ON cryptocom_order_events(event_type);
CREATE INDEX IF NOT EXISTS idx_cryptocom_order_events_timestamp ON cryptocom_order_events(event_timestamp);
CREATE INDEX IF NOT EXISTS idx_cryptocom_order_events_processed ON cryptocom_order_events(processed);

-- ================================================
-- 3. Create WebSocket connection status tracking table
-- ================================================

-- Track WebSocket connection health and metrics
CREATE TABLE IF NOT EXISTS websocket_connection_status (
    id SERIAL PRIMARY KEY,
    exchange VARCHAR(50) NOT NULL,
    
    -- Connection status
    is_connected BOOLEAN DEFAULT FALSE,
    connection_state VARCHAR(20), -- connected, disconnected, connecting, reconnecting, failed
    last_connected_at TIMESTAMP WITH TIME ZONE,
    last_disconnected_at TIMESTAMP WITH TIME ZONE,
    connection_duration_seconds INTEGER,
    
    -- Health metrics
    total_connections INTEGER DEFAULT 0,
    total_disconnections INTEGER DEFAULT 0,
    total_reconnections INTEGER DEFAULT 0,
    connection_failures INTEGER DEFAULT 0,
    
    -- Message metrics
    messages_sent INTEGER DEFAULT 0,
    messages_received INTEGER DEFAULT 0,
    last_message_at TIMESTAMP WITH TIME ZONE,
    
    -- Error tracking
    last_error TEXT,
    last_error_at TIMESTAMP WITH TIME ZONE,
    error_count INTEGER DEFAULT 0,
    
    -- Circuit breaker status
    circuit_breaker_state VARCHAR(20) DEFAULT 'closed', -- closed, open, half_open
    circuit_breaker_failure_count INTEGER DEFAULT 0,
    circuit_breaker_last_failure TIMESTAMP WITH TIME ZONE,
    
    -- Metadata
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    UNIQUE(exchange)
);

-- Insert initial records for supported exchanges
INSERT INTO websocket_connection_status (exchange) VALUES ('binance') ON CONFLICT (exchange) DO NOTHING;
INSERT INTO websocket_connection_status (exchange) VALUES ('cryptocom') ON CONFLICT (exchange) DO NOTHING;
INSERT INTO websocket_connection_status (exchange) VALUES ('bybit') ON CONFLICT (exchange) DO NOTHING;

-- ================================================
-- 4. Create enhanced trade tracking with WebSocket correlation
-- ================================================

-- Add WebSocket correlation columns to trades table
DO $$ 
BEGIN
    -- Add WebSocket fill detection flag
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='trades' AND column_name='websocket_fill_detected') THEN
        ALTER TABLE trades ADD COLUMN websocket_fill_detected BOOLEAN DEFAULT FALSE;
    END IF;
    
    -- Add WebSocket fill detection time
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='trades' AND column_name='websocket_fill_time') THEN
        ALTER TABLE trades ADD COLUMN websocket_fill_time TIMESTAMP WITH TIME ZONE;
    END IF;
    
    -- Add fill detection latency (time between order and WebSocket notification)
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='trades' AND column_name='fill_detection_latency_ms') THEN
        ALTER TABLE trades ADD COLUMN fill_detection_latency_ms INTEGER;
    END IF;
    
    -- Add exchange-specific trade IDs
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='trades' AND column_name='exchange_trade_id') THEN
        ALTER TABLE trades ADD COLUMN exchange_trade_id VARCHAR(100);
    END IF;
    
    -- Add enhanced fee tracking for multiple currencies
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='trades' AND column_name='entry_fee_currency') THEN
        ALTER TABLE trades ADD COLUMN entry_fee_currency VARCHAR(10);
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='trades' AND column_name='exit_fee_currency') THEN
        ALTER TABLE trades ADD COLUMN exit_fee_currency VARCHAR(10);
    END IF;
    
    -- Add total fees in base currency (for PnL calculations)
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns 
                   WHERE table_name='trades' AND column_name='total_fees_base_currency') THEN
        ALTER TABLE trades ADD COLUMN total_fees_base_currency DECIMAL(20, 8);
    END IF;
END $$;

-- ================================================
-- 5. Create WebSocket event processing metrics table
-- ================================================

CREATE TABLE IF NOT EXISTS websocket_event_metrics (
    id SERIAL PRIMARY KEY,
    exchange VARCHAR(50) NOT NULL,
    metric_date DATE NOT NULL DEFAULT CURRENT_DATE,
    
    -- Event processing counts
    events_received INTEGER DEFAULT 0,
    events_processed INTEGER DEFAULT 0,
    events_failed INTEGER DEFAULT 0,
    
    -- Event type breakdown
    order_events INTEGER DEFAULT 0,
    trade_events INTEGER DEFAULT 0,
    balance_events INTEGER DEFAULT 0,
    
    -- Processing performance
    avg_processing_time_ms DECIMAL(10, 2),
    max_processing_time_ms INTEGER,
    min_processing_time_ms INTEGER,
    
    -- Fill detection performance
    fills_detected INTEGER DEFAULT 0,
    partial_fills_detected INTEGER DEFAULT 0,
    fill_detection_accuracy DECIMAL(5, 2), -- Percentage
    
    -- Error breakdown
    connection_errors INTEGER DEFAULT 0,
    authentication_errors INTEGER DEFAULT 0,
    processing_errors INTEGER DEFAULT 0,
    timeout_errors INTEGER DEFAULT 0,
    
    -- Updated timestamp
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    UNIQUE(exchange, metric_date)
);

-- ================================================
-- 6. Create functions and triggers for automatic updates
-- ================================================

-- Function to update websocket_connection_status
CREATE OR REPLACE FUNCTION update_websocket_status_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger for websocket_connection_status
DROP TRIGGER IF EXISTS tr_websocket_status_update ON websocket_connection_status;
CREATE TRIGGER tr_websocket_status_update
    BEFORE UPDATE ON websocket_connection_status
    FOR EACH ROW
    EXECUTE FUNCTION update_websocket_status_timestamp();

-- Function to update cryptocom_order_events
CREATE OR REPLACE FUNCTION update_cryptocom_events_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger for cryptocom_order_events
DROP TRIGGER IF EXISTS tr_cryptocom_events_update ON cryptocom_order_events;
CREATE TRIGGER tr_cryptocom_events_update
    BEFORE UPDATE ON cryptocom_order_events
    FOR EACH ROW
    EXECUTE FUNCTION update_cryptocom_events_timestamp();

-- ================================================
-- 7. Create views for easy monitoring and reporting
-- ================================================

-- View for WebSocket connection health summary
CREATE OR REPLACE VIEW v_websocket_health_summary AS
SELECT 
    exchange,
    is_connected,
    connection_state,
    last_connected_at,
    last_disconnected_at,
    CASE 
        WHEN last_connected_at IS NOT NULL AND is_connected 
        THEN EXTRACT(EPOCH FROM (NOW() - last_connected_at))::INTEGER
        ELSE connection_duration_seconds
    END as current_connection_duration_seconds,
    total_connections,
    total_disconnections,
    total_reconnections,
    connection_failures,
    messages_sent,
    messages_received,
    last_message_at,
    CASE 
        WHEN last_message_at IS NOT NULL 
        THEN EXTRACT(EPOCH FROM (NOW() - last_message_at))::INTEGER
        ELSE NULL
    END as seconds_since_last_message,
    circuit_breaker_state,
    error_count,
    last_error,
    updated_at
FROM websocket_connection_status;

-- View for recent Crypto.com order events
CREATE OR REPLACE VIEW v_recent_cryptocom_events AS
SELECT 
    coe.event_id,
    coe.exchange_order_id,
    coe.event_type,
    coe.event_channel,
    coe.order_status,
    coe.order_side,
    coe.quantity,
    coe.filled_quantity,
    coe.price,
    coe.fee_amount,
    coe.fee_currency,
    coe.event_timestamp,
    coe.processed,
    o.symbol as order_symbol,
    o.status as order_status_db
FROM cryptocom_order_events coe
LEFT JOIN orders o ON coe.order_id = o.id
ORDER BY coe.event_timestamp DESC
LIMIT 100;

-- View for WebSocket fill detection performance
CREATE OR REPLACE VIEW v_fill_detection_performance AS
SELECT 
    DATE(t.created_at) as trade_date,
    t.exchange,
    COUNT(*) as total_trades,
    COUNT(*) FILTER (WHERE t.websocket_fill_detected = true) as websocket_detected_fills,
    ROUND(
        (COUNT(*) FILTER (WHERE t.websocket_fill_detected = true)::DECIMAL / COUNT(*)) * 100, 2
    ) as websocket_detection_rate_percent,
    AVG(t.fill_detection_latency_ms) FILTER (WHERE t.fill_detection_latency_ms IS NOT NULL) as avg_latency_ms,
    MAX(t.fill_detection_latency_ms) as max_latency_ms,
    MIN(t.fill_detection_latency_ms) FILTER (WHERE t.fill_detection_latency_ms > 0) as min_latency_ms
FROM trades t
WHERE t.created_at >= CURRENT_DATE - INTERVAL '30 days'
GROUP BY DATE(t.created_at), t.exchange
ORDER BY trade_date DESC, t.exchange;

-- ================================================
-- 8. Create indexes for optimal performance
-- ================================================

-- Indexes for orders table new columns
CREATE INDEX IF NOT EXISTS idx_orders_exchange ON orders(exchange);
CREATE INDEX IF NOT EXISTS idx_orders_exchange_order_id ON orders(exchange_order_id);
CREATE INDEX IF NOT EXISTS idx_orders_websocket_event ON orders(websocket_event_received, websocket_event_time);
CREATE INDEX IF NOT EXISTS idx_orders_update_source ON orders(update_source);

-- Indexes for trades table new columns
CREATE INDEX IF NOT EXISTS idx_trades_websocket_fill ON trades(websocket_fill_detected, websocket_fill_time);
CREATE INDEX IF NOT EXISTS idx_trades_exchange_trade_id ON trades(exchange_trade_id);
CREATE INDEX IF NOT EXISTS idx_trades_fill_latency ON trades(fill_detection_latency_ms);

-- Composite indexes for common queries
CREATE INDEX IF NOT EXISTS idx_orders_exchange_status ON orders(exchange, status);
CREATE INDEX IF NOT EXISTS idx_trades_exchange_status ON trades(exchange, status);
CREATE INDEX IF NOT EXISTS idx_cryptocom_events_order_status ON cryptocom_order_events(order_id, order_status, event_timestamp);

-- ================================================
-- 9. Insert sample configuration data
-- ================================================

-- Insert default WebSocket configuration if not exists
CREATE TABLE IF NOT EXISTS websocket_configuration (
    id SERIAL PRIMARY KEY,
    exchange VARCHAR(50) UNIQUE NOT NULL,
    enabled BOOLEAN DEFAULT true,
    websocket_url VARCHAR(500),
    heartbeat_interval_seconds INTEGER DEFAULT 30,
    reconnect_delay_seconds INTEGER DEFAULT 5,
    max_reconnect_attempts INTEGER DEFAULT 10,
    circuit_breaker_failure_threshold INTEGER DEFAULT 5,
    circuit_breaker_recovery_timeout_seconds INTEGER DEFAULT 60,
    configuration JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Insert default configurations
INSERT INTO websocket_configuration (exchange, websocket_url, configuration) VALUES 
    ('cryptocom', 'wss://stream.crypto.com/exchange/v1/user', 
     '{"channels": ["user.order", "user.trade", "user.balance"], "auth_required": true}'::jsonb) 
    ON CONFLICT (exchange) DO NOTHING;

INSERT INTO websocket_configuration (exchange, websocket_url, configuration) VALUES 
    ('binance', 'wss://stream.binance.com:9443/ws/', 
     '{"streams": ["executionReport", "outboundAccountPosition"], "auth_required": true}'::jsonb) 
    ON CONFLICT (exchange) DO NOTHING;

-- ================================================
-- 10. Create data cleanup procedures
-- ================================================

-- Function to cleanup old event data (keep last 30 days)
CREATE OR REPLACE FUNCTION cleanup_old_websocket_data()
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    -- Delete old cryptocom_order_events (older than 30 days)
    DELETE FROM cryptocom_order_events 
    WHERE event_timestamp < NOW() - INTERVAL '30 days';
    
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    
    -- Delete old websocket_event_metrics (older than 90 days)
    DELETE FROM websocket_event_metrics 
    WHERE metric_date < CURRENT_DATE - INTERVAL '90 days';
    
    -- Update statistics
    ANALYZE cryptocom_order_events;
    ANALYZE websocket_event_metrics;
    
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- ================================================
-- Migration completion
-- ================================================

-- Update version tracking
INSERT INTO schema_migrations (version, description, applied_at) 
VALUES ('20250827_cryptocom_websocket_v2.6.0', 'Crypto.com WebSocket integration enhanced order tracking', NOW())
ON CONFLICT (version) DO UPDATE SET 
    applied_at = NOW(),
    description = EXCLUDED.description;

-- Commit transaction
COMMIT;

-- ================================================
-- Post-migration verification queries
-- ================================================

-- Verify new tables exist
SELECT 
    schemaname, 
    tablename, 
    tableowner 
FROM pg_tables 
WHERE tablename IN (
    'cryptocom_order_events', 
    'websocket_connection_status', 
    'websocket_event_metrics',
    'websocket_configuration'
);

-- Verify new columns exist in orders table
SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns 
WHERE table_name = 'orders' 
    AND column_name IN (
        'exchange', 
        'exchange_order_id', 
        'exchange_client_order_id',
        'websocket_event_received',
        'websocket_event_time',
        'update_source'
    );

-- Verify new columns exist in trades table
SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns 
WHERE table_name = 'trades' 
    AND column_name IN (
        'websocket_fill_detected',
        'websocket_fill_time', 
        'fill_detection_latency_ms',
        'exchange_trade_id',
        'entry_fee_currency',
        'exit_fee_currency',
        'total_fees_base_currency'
    );

-- Verify views exist
SELECT schemaname, viewname, viewowner
FROM pg_views 
WHERE viewname IN (
    'v_websocket_health_summary',
    'v_recent_cryptocom_events',
    'v_fill_detection_performance'
);

-- Show initial WebSocket connection status
SELECT * FROM v_websocket_health_summary ORDER BY exchange;

ANALYZE;