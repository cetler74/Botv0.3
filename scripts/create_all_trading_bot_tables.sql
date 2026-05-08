-- Trading Bot Database Schema
-- Create all tables for the multi-exchange perpetual futures trading bot

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE SCHEMA IF NOT EXISTS trading;

-- Set search path after schema exists
SET search_path TO trading;

-- Balance table to track account balances per exchange
CREATE TABLE IF NOT EXISTS trading.balance (
    id SERIAL PRIMARY KEY,
    exchange VARCHAR(50) NOT NULL,
    balance DECIMAL(20, 8) NOT NULL DEFAULT 0,
    available_balance DECIMAL(20, 8) NOT NULL DEFAULT 0,
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    total_pnl DECIMAL(20, 8) NOT NULL DEFAULT 0,
    daily_pnl DECIMAL(20, 8) NOT NULL DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Pairs table to store selected trading pairs per exchange
CREATE TABLE IF NOT EXISTS trading.pairs (
    id SERIAL PRIMARY KEY,
    exchange VARCHAR(50) NOT NULL,
    pair_list JSONB NOT NULL, -- Array of selected pairs
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Trades table to track all trading activities
CREATE TABLE IF NOT EXISTS trading.trades (
    id SERIAL PRIMARY KEY,
    trade_id UUID DEFAULT public.uuid_generate_v4(),
    pair VARCHAR(20) NOT NULL,
    entry_price DECIMAL(20, 8),
    exit_price DECIMAL(20, 8),
    status VARCHAR(20) NOT NULL DEFAULT 'OPEN', -- OPEN, CLOSED
    entry_id VARCHAR(100), -- Exchange order ID or internal ID for simulation
    exit_id VARCHAR(100), -- Exchange order ID or internal ID for simulation
    entry_time TIMESTAMP WITH TIME ZONE,
    exit_time TIMESTAMP WITH TIME ZONE,
    unrealized_pnl DECIMAL(20, 8) DEFAULT 0,
    realized_pnl DECIMAL(20, 8) DEFAULT 0,
    current_price DECIMAL(20, 8),
    highest_price DECIMAL(20, 8),
    profit_protection VARCHAR(20) DEFAULT 'inactive', -- active, inactive
    profit_protection_trigger DECIMAL(10, 4), -- Configured value to trigger profit protection
    trail_stop VARCHAR(20) DEFAULT 'inactive', -- active, inactive
    trail_stop_trigger DECIMAL(10, 4), -- Configured value to trigger trailing stop
    exchange VARCHAR(50) NOT NULL,
    entry_reason TEXT, -- Strategy and conditions that triggered entry
    exit_reason TEXT, -- Conditions that triggered exit
    position_size DECIMAL(20, 8),
    fees DECIMAL(20, 8) DEFAULT 0,
    strategy VARCHAR(50), -- Strategy used for this trade
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Live mark for open trades (dashboard / PnL); safe on re-run for existing DBs
ALTER TABLE trading.trades ADD COLUMN IF NOT EXISTS current_price DECIMAL(20, 8);

-- Required for orders.trade_id foreign key
ALTER TABLE trading.trades ADD CONSTRAINT trades_trade_id_key UNIQUE (trade_id);

-- Orders table to track individual order details and status
CREATE TABLE IF NOT EXISTS trading.orders (
    id SERIAL PRIMARY KEY,
    order_id VARCHAR(255) UNIQUE NOT NULL,
    trade_id UUID REFERENCES trading.trades(trade_id),
    exchange VARCHAR(50) NOT NULL,
    symbol VARCHAR(50) NOT NULL,
    order_type VARCHAR(20) NOT NULL, -- 'market', 'limit', 'stop'
    side VARCHAR(10) NOT NULL, -- 'buy', 'sell'
    amount DECIMAL(20,8) NOT NULL,
    price DECIMAL(20,8),
    filled_amount DECIMAL(20,8) DEFAULT 0,
    filled_price DECIMAL(20,8),
    status VARCHAR(20) NOT NULL, -- 'pending', 'filled', 'cancelled', 'rejected'
    fees DECIMAL(20,8) DEFAULT 0,
    fee_rate DECIMAL(10,6),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    filled_at TIMESTAMP WITH TIME ZONE,
    cancelled_at TIMESTAMP WITH TIME ZONE,
    timeout_seconds INTEGER DEFAULT 300,
    retry_count INTEGER DEFAULT 0,
    error_message TEXT,
    exchange_order_id VARCHAR(255),
    client_order_id VARCHAR(255)
);

-- Alerts table to store system alerts and notifications
CREATE TABLE IF NOT EXISTS trading.alerts (
    id SERIAL PRIMARY KEY,
    alert_id UUID DEFAULT public.uuid_generate_v4(),
    level VARCHAR(20) NOT NULL, -- INFO, WARNING, ERROR, CRITICAL
    category VARCHAR(50) NOT NULL, -- CONFIG, TRADE, BALANCE, EXCHANGE, SYSTEM
    message TEXT NOT NULL,
    details JSONB, -- Additional alert details
    exchange VARCHAR(50), -- Exchange name if applicable
    resolved BOOLEAN DEFAULT FALSE,
    resolved_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Market data cache table for storing volatile market information
CREATE TABLE IF NOT EXISTS trading.market_data_cache (
    id SERIAL PRIMARY KEY,
    exchange VARCHAR(50) NOT NULL,
    pair VARCHAR(20) NOT NULL,
    data_type VARCHAR(50) NOT NULL, -- OHLCV, TICKER, ORDERBOOK, etc.
    data JSONB NOT NULL,
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Real-time price ticks (price-feed service; must match services/price-feed-service INSERT columns)
CREATE TABLE IF NOT EXISTS trading.real_time_prices (
    id SERIAL PRIMARY KEY,
    exchange VARCHAR(50) NOT NULL,
    pair VARCHAR(20) NOT NULL,
    price DECIMAL(20, 8) NOT NULL,
    bid DECIMAL(20, 8),
    ask DECIMAL(20, 8),
    volume_24h DECIMAL(20, 8),
    price_change_24h DECIMAL(10, 6),
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL,
    source VARCHAR(20) DEFAULT 'websocket',
    sequence_id BIGINT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_real_time_prices_exchange_pair ON trading.real_time_prices(exchange, pair);
CREATE INDEX IF NOT EXISTS idx_real_time_prices_timestamp ON trading.real_time_prices(timestamp);
CREATE INDEX IF NOT EXISTS idx_real_time_prices_sequence ON trading.real_time_prices(sequence_id);

-- Latest price lookup for trailing-stop / price-feed fallbacks
CREATE OR REPLACE FUNCTION trading.get_latest_price(
    exchange_name VARCHAR(50),
    pair_name VARCHAR(20)
) RETURNS DECIMAL(20, 8) AS $$
DECLARE
    latest_price DECIMAL(20, 8);
BEGIN
    SELECT price INTO latest_price
    FROM trading.real_time_prices
    WHERE exchange = exchange_name AND pair = pair_name
    ORDER BY timestamp DESC, sequence_id DESC NULLS LAST
    LIMIT 1;
    RETURN COALESCE(latest_price, 0);
END;
$$ LANGUAGE plpgsql;

ALTER TABLE trading.trades
ADD COLUMN IF NOT EXISTS trailing_stop_history JSONB DEFAULT '[]'::jsonb,
ADD COLUMN IF NOT EXISTS price_updates_count INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS last_price_update TIMESTAMP WITH TIME ZONE,
ADD COLUMN IF NOT EXISTS websocket_price_source BOOLEAN DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS trading.websocket_status (
    id SERIAL PRIMARY KEY,
    exchange VARCHAR(50) NOT NULL UNIQUE,
    status VARCHAR(20) NOT NULL,
    last_message_time TIMESTAMP WITH TIME ZONE,
    connection_start_time TIMESTAMP WITH TIME ZONE,
    reconnection_count INTEGER DEFAULT 0,
    error_message TEXT,
    latency_ms INTEGER,
    message_count BIGINT DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS trading.trailing_stops (
    id SERIAL PRIMARY KEY,
    trade_id UUID NOT NULL REFERENCES trading.trades(trade_id) ON DELETE CASCADE,
    exchange VARCHAR(50) NOT NULL,
    pair VARCHAR(20) NOT NULL,
    trailing_enabled BOOLEAN DEFAULT FALSE,
    trailing_trigger_percentage DECIMAL(10, 6),
    trailing_step_percentage DECIMAL(10, 6),
    max_trail_distance_percentage DECIMAL(10, 6),
    is_active BOOLEAN DEFAULT FALSE,
    current_stop_price DECIMAL(20, 8),
    highest_price_seen DECIMAL(20, 8),
    lowest_price_seen DECIMAL(20, 8),
    entry_price DECIMAL(20, 8) NOT NULL,
    current_price DECIMAL(20, 8),
    position_side VARCHAR(10) DEFAULT 'long',
    profit_protection_enabled BOOLEAN DEFAULT FALSE,
    profit_lock_percentage DECIMAL(10, 6),
    profit_protection_active BOOLEAN DEFAULT FALSE,
    last_price_update TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    last_adjustment TIMESTAMP WITH TIME ZONE,
    adjustment_count INTEGER DEFAULT 0,
    triggered_at TIMESTAMP WITH TIME ZONE,
    recovery_data JSONB,
    websocket_connected BOOLEAN DEFAULT TRUE,
    price_source VARCHAR(50) DEFAULT 'websocket',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_trailing_stops_trade_id ON trading.trailing_stops(trade_id);
CREATE INDEX IF NOT EXISTS idx_trailing_stops_exchange_pair ON trading.trailing_stops(exchange, pair);
CREATE INDEX IF NOT EXISTS idx_trailing_stops_active ON trading.trailing_stops(is_active);
CREATE INDEX IF NOT EXISTS idx_trailing_stops_updated ON trading.trailing_stops(updated_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_trailing_stops_trade_unique ON trading.trailing_stops(trade_id);
CREATE INDEX IF NOT EXISTS idx_websocket_status_exchange ON trading.websocket_status(exchange);
CREATE INDEX IF NOT EXISTS idx_websocket_status_updated ON trading.websocket_status(updated_at);

CREATE OR REPLACE FUNCTION trading.update_trailing_stop(
    trade_uuid UUID,
    current_market_price DECIMAL(20, 8)
) RETURNS BOOLEAN AS $$
DECLARE
    trailing_record RECORD;
    new_stop_price DECIMAL(20, 8);
    new_highest_price DECIMAL(20, 8);
    price_improved BOOLEAN := FALSE;
    adjustment_made BOOLEAN := FALSE;
BEGIN
    SELECT * INTO trailing_record
    FROM trading.trailing_stops
    WHERE trade_id = trade_uuid;
    IF NOT FOUND OR NOT trailing_record.trailing_enabled THEN
        RETURN FALSE;
    END IF;
    UPDATE trading.trailing_stops
    SET current_price = current_market_price,
        last_price_update = CURRENT_TIMESTAMP
    WHERE trade_id = trade_uuid;
    IF trailing_record.position_side = 'long' THEN
        IF current_market_price > trailing_record.highest_price_seen THEN
            new_highest_price := current_market_price;
            price_improved := TRUE;
            new_stop_price := current_market_price * (1 - trailing_record.trailing_step_percentage / 100);
            IF new_stop_price > trailing_record.current_stop_price THEN
                adjustment_made := TRUE;
            END IF;
        END IF;
    END IF;
    IF adjustment_made THEN
        UPDATE trading.trailing_stops
        SET current_stop_price = new_stop_price,
            highest_price_seen = COALESCE(new_highest_price, highest_price_seen),
            last_adjustment = CURRENT_TIMESTAMP,
            adjustment_count = adjustment_count + 1
        WHERE trade_id = trade_uuid;
        UPDATE trading.trades
        SET trail_stop_trigger = new_stop_price,
            highest_price = COALESCE(new_highest_price, highest_price),
            price_updates_count = price_updates_count + 1,
            last_price_update = CURRENT_TIMESTAMP
        WHERE trade_id = trade_uuid;
    END IF;
    RETURN adjustment_made;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION trading.should_trigger_trailing_stop(
    trade_uuid UUID,
    current_market_price DECIMAL(20, 8)
) RETURNS BOOLEAN AS $$
DECLARE
    trailing_record RECORD;
BEGIN
    SELECT * INTO trailing_record
    FROM trading.trailing_stops
    WHERE trade_id = trade_uuid AND is_active = TRUE;
    IF NOT FOUND THEN
        RETURN FALSE;
    END IF;
    IF trailing_record.position_side = 'long' THEN
        RETURN current_market_price <= trailing_record.current_stop_price;
    END IF;
    IF trailing_record.position_side = 'short' THEN
        RETURN current_market_price >= trailing_record.current_stop_price;
    END IF;
    RETURN FALSE;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE VIEW trading.active_trailing_stops AS
SELECT
    ts.*,
    t.position_size,
    t.strategy,
    t.status AS trade_status,
    rtp.price AS latest_market_price,
    rtp.timestamp AS latest_price_time,
    ws.status AS websocket_status,
    CASE
        WHEN ts.position_side = 'long' THEN
            ROUND(((ts.current_price - ts.entry_price) / ts.entry_price * 100)::numeric, 4)
        ELSE
            ROUND(((ts.entry_price - ts.current_price) / ts.entry_price * 100)::numeric, 4)
    END AS current_pnl_percentage
FROM trading.trailing_stops ts
JOIN trading.trades t ON ts.trade_id = t.trade_id
LEFT JOIN trading.real_time_prices rtp ON (ts.exchange = rtp.exchange AND ts.pair = rtp.pair)
LEFT JOIN trading.websocket_status ws ON ts.exchange = ws.exchange
WHERE ts.is_active = TRUE AND t.status = 'OPEN';

-- Strategy performance tracking table
CREATE TABLE IF NOT EXISTS trading.strategy_performance (
    id SERIAL PRIMARY KEY,
    strategy_name VARCHAR(50) NOT NULL,
    exchange VARCHAR(50) NOT NULL,
    pair VARCHAR(20) NOT NULL,
    total_trades INTEGER DEFAULT 0,
    winning_trades INTEGER DEFAULT 0,
    losing_trades INTEGER DEFAULT 0,
    total_pnl DECIMAL(20, 8) DEFAULT 0,
    win_rate DECIMAL(5, 2) DEFAULT 0,
    avg_win DECIMAL(20, 8) DEFAULT 0,
    avg_loss DECIMAL(20, 8) DEFAULT 0,
    max_drawdown DECIMAL(20, 8) DEFAULT 0,
    sharpe_ratio DECIMAL(10, 4) DEFAULT 0,
    last_updated TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Configuration audit table to track configuration changes
CREATE TABLE IF NOT EXISTS trading.config_audit (
    id SERIAL PRIMARY KEY,
    component VARCHAR(50) NOT NULL,
    config_key VARCHAR(100) NOT NULL,
    old_value TEXT,
    new_value TEXT,
    changed_by VARCHAR(50) DEFAULT 'system',
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Create indexes for better performance
CREATE INDEX IF NOT EXISTS idx_balance_exchange ON trading.balance(exchange);
CREATE INDEX IF NOT EXISTS idx_balance_timestamp ON trading.balance(timestamp);
CREATE INDEX IF NOT EXISTS idx_pairs_exchange ON trading.pairs(exchange);
CREATE INDEX IF NOT EXISTS idx_pairs_timestamp ON trading.pairs(timestamp);
CREATE INDEX IF NOT EXISTS idx_trades_exchange ON trading.trades(exchange);
CREATE INDEX IF NOT EXISTS idx_trades_status ON trading.trades(status);
CREATE INDEX IF NOT EXISTS idx_trades_pair ON trading.trades(pair);
CREATE INDEX IF NOT EXISTS idx_trades_entry_time ON trading.trades(entry_time);
CREATE INDEX IF NOT EXISTS idx_trades_exit_time ON trading.trades(exit_time);
CREATE INDEX IF NOT EXISTS idx_orders_trade_id ON trading.orders(trade_id);
CREATE INDEX IF NOT EXISTS idx_orders_exchange ON trading.orders(exchange);
CREATE INDEX IF NOT EXISTS idx_orders_status ON trading.orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_created_at ON trading.orders(created_at);
CREATE INDEX IF NOT EXISTS idx_orders_symbol ON trading.orders(symbol);
CREATE INDEX IF NOT EXISTS idx_orders_order_type ON trading.orders(order_type);
CREATE INDEX IF NOT EXISTS idx_alerts_level ON trading.alerts(level);
CREATE INDEX IF NOT EXISTS idx_alerts_category ON trading.alerts(category);
CREATE INDEX IF NOT EXISTS idx_alerts_resolved ON trading.alerts(resolved);
CREATE INDEX IF NOT EXISTS idx_market_data_cache_exchange_pair ON trading.market_data_cache(exchange, pair);
CREATE INDEX IF NOT EXISTS idx_market_data_cache_expires ON trading.market_data_cache(expires_at);
CREATE INDEX IF NOT EXISTS idx_strategy_performance_strategy ON trading.strategy_performance(strategy_name);
CREATE INDEX IF NOT EXISTS idx_strategy_performance_exchange ON trading.strategy_performance(exchange);

-- Create unique constraints
CREATE UNIQUE INDEX IF NOT EXISTS idx_balance_exchange_unique ON trading.balance(exchange);
CREATE UNIQUE INDEX IF NOT EXISTS idx_market_data_cache_unique ON trading.market_data_cache(exchange, pair, data_type);
CREATE UNIQUE INDEX IF NOT EXISTS idx_strategy_performance_unique ON trading.strategy_performance(strategy_name, exchange, pair);

-- Create functions for automatic timestamp updates
CREATE OR REPLACE FUNCTION trading.update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Create triggers for automatic timestamp updates
CREATE TRIGGER update_balance_updated_at BEFORE UPDATE ON trading.balance
    FOR EACH ROW EXECUTE FUNCTION trading.update_updated_at_column();

CREATE TRIGGER update_trades_updated_at BEFORE UPDATE ON trading.trades
    FOR EACH ROW EXECUTE FUNCTION trading.update_updated_at_column();

CREATE TRIGGER update_orders_updated_at BEFORE UPDATE ON trading.orders
    FOR EACH ROW EXECUTE FUNCTION trading.update_updated_at_column();

-- Create function to calculate daily PnL
CREATE OR REPLACE FUNCTION trading.calculate_daily_pnl(exchange_name VARCHAR(50))
RETURNS DECIMAL(20, 8) AS $$
DECLARE
    daily_pnl DECIMAL(20, 8);
BEGIN
    SELECT COALESCE(SUM(realized_pnl), 0)
    INTO daily_pnl
    FROM trading.trades
    WHERE exchange = exchange_name
    AND exit_time >= CURRENT_DATE
    AND status = 'CLOSED';
    
    RETURN daily_pnl;
END;
$$ LANGUAGE plpgsql;

-- Create function to calculate total PnL
CREATE OR REPLACE FUNCTION trading.calculate_total_pnl(exchange_name VARCHAR(50))
RETURNS DECIMAL(20, 8) AS $$
DECLARE
    total_pnl DECIMAL(20, 8);
BEGIN
    SELECT COALESCE(SUM(realized_pnl), 0)
    INTO total_pnl
    FROM trading.trades
    WHERE exchange = exchange_name
    AND status = 'CLOSED';
    
    RETURN total_pnl;
END;
$$ LANGUAGE plpgsql;

-- Ensure 'entry_time' column exists in trading.trades
ALTER TABLE IF EXISTS trading.trades
ADD COLUMN IF NOT EXISTS entry_time TIMESTAMP;

-- Insert initial data
INSERT INTO trading.balance (exchange, balance, available_balance, total_pnl, daily_pnl)
VALUES
    ('binance', 10000, 10000, 0, 0),
    ('cryptocom', 10000, 10000, 0, 0),
    ('bybit', 10000, 10000, 0, 0)
ON CONFLICT (exchange) DO NOTHING;

-- Grant permissions (adjust as needed for your setup)
-- GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA trading TO carloslarramba;
-- GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA trading TO carloslarramba; 