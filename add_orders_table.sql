-- Add Orders table to existing trading schema
SET search_path TO trading;

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

-- Create indexes for orders table
CREATE INDEX IF NOT EXISTS idx_orders_trade_id ON trading.orders(trade_id);
CREATE INDEX IF NOT EXISTS idx_orders_exchange ON trading.orders(exchange);
CREATE INDEX IF NOT EXISTS idx_orders_status ON trading.orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_created_at ON trading.orders(created_at);
CREATE INDEX IF NOT EXISTS idx_orders_symbol ON trading.orders(symbol);
CREATE INDEX IF NOT EXISTS idx_orders_order_type ON trading.orders(order_type);

-- Create trigger for orders table
CREATE TRIGGER update_orders_updated_at BEFORE UPDATE ON trading.orders
    FOR EACH ROW EXECUTE FUNCTION trading.update_updated_at_column(); 