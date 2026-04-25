-- ENHANCED PENDING_ORDERS SCHEMA
-- Supports complete WebSocket trading lifecycle for both entry and exit orders

-- Drop existing table if it exists (for clean migration)
DROP TABLE IF EXISTS pending_orders;

-- Create enhanced PENDING_ORDERS table
CREATE TABLE pending_orders (
    id SERIAL PRIMARY KEY,
    
    -- Order identification
    pending_order_id VARCHAR(100) UNIQUE NOT NULL,
    exchange_order_id VARCHAR(100) UNIQUE NOT NULL,
    trade_id VARCHAR(100), -- Links to trades.trade_id when trade is created
    
    -- Order type and basic info
    order_type VARCHAR(10) NOT NULL CHECK (order_type IN ('entry', 'exit')),
    exchange VARCHAR(50) NOT NULL,
    pair VARCHAR(20) NOT NULL,
    side VARCHAR(10) NOT NULL CHECK (side IN ('buy', 'sell')),
    
    -- Order details
    amount DECIMAL(20, 8) NOT NULL,
    price DECIMAL(20, 8) NOT NULL,
    order_type_exchange VARCHAR(20) NOT NULL DEFAULT 'limit', -- limit, market, etc.
    
    -- Status tracking
    status VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (
        status IN ('pending', 'partially_filled', 'filled', 'cancelled', 'failed')
    ),
    
    -- Strategy and reasoning
    strategy VARCHAR(50),
    entry_reason TEXT, -- For entry orders
    exit_reason TEXT,  -- For exit orders (profit_target, stop_loss, etc.)
    
    -- WebSocket tracking
    websocket_subscribed BOOLEAN DEFAULT FALSE,
    last_websocket_update TIMESTAMP WITH TIME ZONE,
    
    -- Fill tracking
    filled_amount DECIMAL(20, 8) DEFAULT 0,
    filled_price DECIMAL(20, 8),
    fill_count INTEGER DEFAULT 0, -- Number of partial fills
    
    -- Fee information (when available)
    fee_amount DECIMAL(20, 8),
    fee_currency VARCHAR(10),
    
    -- Timestamps
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    filled_at TIMESTAMP WITH TIME ZONE,
    cancelled_at TIMESTAMP WITH TIME ZONE,
    
    -- Error tracking
    last_error TEXT,
    retry_count INTEGER DEFAULT 0
);

-- Indexes for performance
CREATE INDEX idx_pending_orders_exchange_order_id ON pending_orders(exchange_order_id);
CREATE INDEX idx_pending_orders_trade_id ON pending_orders(trade_id);
CREATE INDEX idx_pending_orders_status ON pending_orders(status);
CREATE INDEX idx_pending_orders_order_type ON pending_orders(order_type);
CREATE INDEX idx_pending_orders_exchange ON pending_orders(exchange);
CREATE INDEX idx_pending_orders_created_at ON pending_orders(created_at);
CREATE INDEX idx_pending_orders_websocket ON pending_orders(websocket_subscribed) WHERE websocket_subscribed = TRUE;

-- Composite indexes for common queries
CREATE INDEX idx_pending_orders_status_type ON pending_orders(status, order_type);
CREATE INDEX idx_pending_orders_exchange_status ON pending_orders(exchange, status);

-- Add updated_at trigger
CREATE OR REPLACE FUNCTION update_pending_orders_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_pending_orders_updated_at
    BEFORE UPDATE ON pending_orders
    FOR EACH ROW
    EXECUTE FUNCTION update_pending_orders_updated_at();

-- TRADES table enhancements (to support new lifecycle)
-- Add new status values for the WebSocket lifecycle
ALTER TABLE trades 
ADD COLUMN IF NOT EXISTS lifecycle_status VARCHAR(20) 
CHECK (lifecycle_status IN ('pending_entry', 'open', 'pending_exit', 'closed', 'cancelled', 'failed'));

-- Add WebSocket tracking fields
ALTER TABLE trades ADD COLUMN IF NOT EXISTS entry_websocket_confirmed BOOLEAN DEFAULT FALSE;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS exit_websocket_confirmed BOOLEAN DEFAULT FALSE;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS pending_entry_order_id VARCHAR(100);
ALTER TABLE trades ADD COLUMN IF NOT EXISTS pending_exit_order_id VARCHAR(100);

-- Add indexes for new fields
CREATE INDEX IF NOT EXISTS idx_trades_lifecycle_status ON trades(lifecycle_status);
CREATE INDEX IF NOT EXISTS idx_trades_entry_websocket ON trades(entry_websocket_confirmed);
CREATE INDEX IF NOT EXISTS idx_trades_exit_websocket ON trades(exit_websocket_confirmed);

-- WEBSOCKET_SUBSCRIPTIONS table (for tracking active subscriptions)
CREATE TABLE IF NOT EXISTS websocket_subscriptions (
    id SERIAL PRIMARY KEY,
    exchange_order_id VARCHAR(100) UNIQUE NOT NULL,
    exchange VARCHAR(50) NOT NULL,
    order_type VARCHAR(10) NOT NULL CHECK (order_type IN ('entry', 'exit')),
    trade_id VARCHAR(100),
    subscription_id VARCHAR(100), -- WebSocket subscription identifier
    subscribed_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_event_at TIMESTAMP WITH TIME ZONE,
    event_count INTEGER DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE
);

CREATE INDEX idx_websocket_subs_exchange_order ON websocket_subscriptions(exchange_order_id);
CREATE INDEX idx_websocket_subs_active ON websocket_subscriptions(is_active) WHERE is_active = TRUE;

-- Add comments for documentation
COMMENT ON TABLE pending_orders IS 'Tracks all pending orders (entry and exit) before they are filled';
COMMENT ON COLUMN pending_orders.order_type IS 'entry: buy orders to open positions, exit: sell orders to close positions';
COMMENT ON COLUMN pending_orders.trade_id IS 'Links to trades table once trade is created (for exit orders)';
COMMENT ON COLUMN pending_orders.websocket_subscribed IS 'Whether WebSocket is actively monitoring this order';
COMMENT ON COLUMN pending_orders.fill_count IS 'Number of partial fills received';

COMMENT ON TABLE websocket_subscriptions IS 'Tracks active WebSocket subscriptions for order monitoring';

-- Sample queries for the new system:

-- Find all pending entry orders
-- SELECT * FROM pending_orders WHERE order_type = 'entry' AND status = 'pending';

-- Find all pending exit orders for a specific trade
-- SELECT * FROM pending_orders WHERE order_type = 'exit' AND trade_id = 'some-trade-id' AND status = 'pending';

-- Get all orders being monitored by WebSocket
-- SELECT po.*, ws.last_event_at 
-- FROM pending_orders po 
-- JOIN websocket_subscriptions ws ON po.exchange_order_id = ws.exchange_order_id 
-- WHERE ws.is_active = TRUE;

-- Find trades in pending_entry state (orders placed but not filled)
-- SELECT t.*, po.exchange_order_id, po.created_at as order_placed_at
-- FROM trades t 
-- JOIN pending_orders po ON t.trade_id = po.trade_id 
-- WHERE t.lifecycle_status = 'pending_entry';

GRANT ALL PRIVILEGES ON pending_orders TO your_database_user;
GRANT ALL PRIVILEGES ON websocket_subscriptions TO your_database_user;
GRANT USAGE ON SEQUENCE pending_orders_id_seq TO your_database_user;
GRANT USAGE ON SEQUENCE websocket_subscriptions_id_seq TO your_database_user;