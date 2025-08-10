-- Enhanced Trailing Stop Schema
-- Adds comprehensive trailing stop persistence and real-time price tracking

SET search_path TO trading;

-- Enhanced trailing stop tracking table for redundant persistence
CREATE TABLE IF NOT EXISTS trading.trailing_stops (
    id SERIAL PRIMARY KEY,
    trade_id UUID NOT NULL REFERENCES trading.trades(trade_id) ON DELETE CASCADE,
    exchange VARCHAR(50) NOT NULL,
    pair VARCHAR(20) NOT NULL,
    
    -- Trailing stop configuration
    trailing_enabled BOOLEAN DEFAULT FALSE,
    trailing_trigger_percentage DECIMAL(10, 6), -- Percentage profit needed to activate trailing
    trailing_step_percentage DECIMAL(10, 6), -- Step size for trailing adjustments
    max_trail_distance_percentage DECIMAL(10, 6), -- Maximum trail distance
    
    -- Current trailing stop state
    is_active BOOLEAN DEFAULT FALSE,
    current_stop_price DECIMAL(20, 8), -- Current trailing stop price
    highest_price_seen DECIMAL(20, 8), -- Highest price achieved for long positions
    lowest_price_seen DECIMAL(20, 8), -- Lowest price achieved for short positions
    entry_price DECIMAL(20, 8) NOT NULL,
    current_price DECIMAL(20, 8), -- Last known current price
    position_side VARCHAR(10) DEFAULT 'long', -- 'long' or 'short'
    
    -- Profit protection integration
    profit_protection_enabled BOOLEAN DEFAULT FALSE,
    profit_lock_percentage DECIMAL(10, 6), -- Percentage to lock when profit protection triggers
    profit_protection_active BOOLEAN DEFAULT FALSE,
    
    -- Tracking and audit
    last_price_update TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    last_adjustment TIMESTAMP WITH TIME ZONE,
    adjustment_count INTEGER DEFAULT 0,
    triggered_at TIMESTAMP WITH TIME ZONE,
    
    -- Recovery and resilience
    recovery_data JSONB, -- Store additional recovery information
    websocket_connected BOOLEAN DEFAULT TRUE, -- Track WebSocket connection status
    price_source VARCHAR(50) DEFAULT 'websocket', -- 'websocket', 'rest_api', 'fallback'
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Real-time price tracking table for WebSocket feeds
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
    source VARCHAR(20) DEFAULT 'websocket', -- 'websocket', 'rest_api'
    sequence_id BIGINT, -- For ordering WebSocket messages
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- WebSocket connection status tracking
CREATE TABLE IF NOT EXISTS trading.websocket_status (
    id SERIAL PRIMARY KEY,
    exchange VARCHAR(50) NOT NULL UNIQUE,
    status VARCHAR(20) NOT NULL, -- 'connected', 'disconnected', 'reconnecting', 'error'
    last_message_time TIMESTAMP WITH TIME ZONE,
    connection_start_time TIMESTAMP WITH TIME ZONE,
    reconnection_count INTEGER DEFAULT 0,
    error_message TEXT,
    latency_ms INTEGER, -- Last measured latency
    message_count BIGINT DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Add new columns to existing trades table for enhanced tracking
ALTER TABLE trading.trades 
ADD COLUMN IF NOT EXISTS trailing_stop_history JSONB DEFAULT '[]'::jsonb,
ADD COLUMN IF NOT EXISTS price_updates_count INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS last_price_update TIMESTAMP WITH TIME ZONE,
ADD COLUMN IF NOT EXISTS websocket_price_source BOOLEAN DEFAULT FALSE;

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_trailing_stops_trade_id ON trading.trailing_stops(trade_id);
CREATE INDEX IF NOT EXISTS idx_trailing_stops_exchange_pair ON trading.trailing_stops(exchange, pair);
CREATE INDEX IF NOT EXISTS idx_trailing_stops_active ON trading.trailing_stops(is_active);
CREATE INDEX IF NOT EXISTS idx_trailing_stops_updated ON trading.trailing_stops(updated_at);

CREATE INDEX IF NOT EXISTS idx_real_time_prices_exchange_pair ON trading.real_time_prices(exchange, pair);
CREATE INDEX IF NOT EXISTS idx_real_time_prices_timestamp ON trading.real_time_prices(timestamp);
CREATE INDEX IF NOT EXISTS idx_real_time_prices_sequence ON trading.real_time_prices(sequence_id);

CREATE INDEX IF NOT EXISTS idx_websocket_status_exchange ON trading.websocket_status(exchange);
CREATE INDEX IF NOT EXISTS idx_websocket_status_updated ON trading.websocket_status(updated_at);

-- Create unique constraints
CREATE UNIQUE INDEX IF NOT EXISTS idx_trailing_stops_trade_unique ON trading.trailing_stops(trade_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_websocket_status_exchange_unique ON trading.websocket_status(exchange);

-- Create partitioning for real_time_prices table for better performance (optional)
-- This table can grow very large with high-frequency price updates

-- Create triggers for automatic timestamp updates
CREATE TRIGGER update_trailing_stops_updated_at BEFORE UPDATE ON trading.trailing_stops
    FOR EACH ROW EXECUTE FUNCTION trading.update_updated_at_column();

CREATE TRIGGER update_websocket_status_updated_at BEFORE UPDATE ON trading.websocket_status
    FOR EACH ROW EXECUTE FUNCTION trading.update_updated_at_column();

-- Function to get latest price for a trading pair
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
    ORDER BY timestamp DESC, sequence_id DESC
    LIMIT 1;
    
    RETURN COALESCE(latest_price, 0);
END;
$$ LANGUAGE plpgsql;

-- Function to update trailing stop based on current price
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
    -- Get current trailing stop record
    SELECT * INTO trailing_record
    FROM trading.trailing_stops
    WHERE trade_id = trade_uuid;
    
    IF NOT FOUND OR NOT trailing_record.trailing_enabled THEN
        RETURN FALSE;
    END IF;
    
    -- Update current price
    UPDATE trading.trailing_stops
    SET current_price = current_market_price,
        last_price_update = CURRENT_TIMESTAMP
    WHERE trade_id = trade_uuid;
    
    -- Check if price improved (for long positions)
    IF trailing_record.position_side = 'long' THEN
        IF current_market_price > trailing_record.highest_price_seen THEN
            new_highest_price := current_market_price;
            price_improved := TRUE;
            
            -- Calculate new trailing stop price
            new_stop_price := current_market_price * (1 - trailing_record.trailing_step_percentage / 100);
            
            -- Only update if new stop is higher than current stop
            IF new_stop_price > trailing_record.current_stop_price THEN
                adjustment_made := TRUE;
            END IF;
        END IF;
    END IF;
    
    -- Update trailing stop if adjustment needed
    IF adjustment_made THEN
        UPDATE trading.trailing_stops
        SET current_stop_price = new_stop_price,
            highest_price_seen = COALESCE(new_highest_price, highest_price_seen),
            last_adjustment = CURRENT_TIMESTAMP,
            adjustment_count = adjustment_count + 1
        WHERE trade_id = trade_uuid;
        
        -- Also update the main trades table
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

-- Function to check if trailing stop should trigger exit
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
    
    -- For long positions, trigger if price drops below stop
    IF trailing_record.position_side = 'long' THEN
        RETURN current_market_price <= trailing_record.current_stop_price;
    END IF;
    
    -- For short positions, trigger if price rises above stop
    IF trailing_record.position_side = 'short' THEN
        RETURN current_market_price >= trailing_record.current_stop_price;
    END IF;
    
    RETURN FALSE;
END;
$$ LANGUAGE plpgsql;

-- Insert initial WebSocket status records
INSERT INTO trading.websocket_status (exchange, status, connection_start_time)
VALUES 
    ('binance', 'disconnected', CURRENT_TIMESTAMP),
    ('bybit', 'disconnected', CURRENT_TIMESTAMP),
    ('cryptocom', 'disconnected', CURRENT_TIMESTAMP)
ON CONFLICT (exchange) DO NOTHING;

-- Create view for active trailing stops with current status
CREATE OR REPLACE VIEW trading.active_trailing_stops AS
SELECT 
    ts.*,
    t.position_size,
    t.strategy,
    t.status as trade_status,
    rtp.price as latest_market_price,
    rtp.timestamp as latest_price_time,
    ws.status as websocket_status,
    CASE 
        WHEN ts.position_side = 'long' THEN 
            ROUND(((ts.current_price - ts.entry_price) / ts.entry_price * 100)::numeric, 4)
        ELSE 
            ROUND(((ts.entry_price - ts.current_price) / ts.entry_price * 100)::numeric, 4)
    END as current_pnl_percentage
FROM trading.trailing_stops ts
JOIN trading.trades t ON ts.trade_id = t.trade_id
LEFT JOIN trading.real_time_prices rtp ON (ts.exchange = rtp.exchange AND ts.pair = rtp.pair)
LEFT JOIN trading.websocket_status ws ON ts.exchange = ws.exchange
WHERE ts.is_active = TRUE AND t.status = 'OPEN'
ORDER BY ts.updated_at DESC;