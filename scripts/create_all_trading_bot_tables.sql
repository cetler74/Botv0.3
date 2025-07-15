-- Trading Bot Database Schema
-- Create all tables for the multi-exchange perpetual futures trading bot

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Set search path to trading schema
SET search_path TO trading;

CREATE SCHEMA IF NOT EXISTS trading;

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

-- Insert initial data
INSERT INTO trading.balance (exchange, balance, available_balance, total_pnl, daily_pnl)
VALUES 
    ('binance', 0, 0, 0, 0),
    ('cryptocom', 0, 0, 0, 0),
    ('bybit', 0, 0, 0, 0)
ON CONFLICT (exchange) DO NOTHING;

-- Grant permissions (adjust as needed for your setup)
-- GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA trading TO carloslarramba;
-- GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA trading TO carloslarramba; 