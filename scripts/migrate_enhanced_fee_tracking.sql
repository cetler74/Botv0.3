-- Enhanced Fee Tracking Migration - Version 2.5.0
-- Adds support for detailed execution tracking and enhanced fee information

-- Create order_executions table for granular execution tracking
CREATE TABLE IF NOT EXISTS trading.order_executions (
    id SERIAL PRIMARY KEY,
    execution_id VARCHAR(255) NOT NULL UNIQUE,           -- Composite: order_id_trade_id
    trade_id VARCHAR(255) REFERENCES trading.trades(trade_id) ON DELETE CASCADE,
    exchange VARCHAR(50) NOT NULL,
    order_id VARCHAR(255) NOT NULL,                      -- Exchange order ID
    client_order_id VARCHAR(255),                        -- Client order ID
    binance_trade_id BIGINT,                            -- Binance trade ID
    
    -- Execution details
    symbol VARCHAR(50) NOT NULL,
    side VARCHAR(10) NOT NULL,                          -- 'buy' or 'sell'
    executed_quantity DECIMAL(20, 8) NOT NULL,
    executed_price DECIMAL(20, 8) NOT NULL,
    quote_quantity DECIMAL(20, 8),                      -- Quote asset quantity
    
    -- Fee information (enhanced)
    fee_amount DECIMAL(20, 8) DEFAULT 0,
    fee_currency VARCHAR(20),
    fee_precision INTEGER DEFAULT 8,
    is_maker BOOLEAN DEFAULT false,
    
    -- Timestamps
    execution_time TIMESTAMP WITH TIME ZONE NOT NULL,
    transaction_time TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    
    -- Metadata
    execution_type VARCHAR(50),                         -- 'trade', 'partial_fill', 'complete_fill'
    order_status VARCHAR(50),                          -- Order status at time of execution
    
    -- Constraints
    CONSTRAINT chk_side CHECK (side IN ('buy', 'sell')),
    CONSTRAINT chk_executed_quantity_positive CHECK (executed_quantity > 0),
    CONSTRAINT chk_executed_price_positive CHECK (executed_price > 0)
);

-- Add indexes for performance
CREATE INDEX IF NOT EXISTS idx_order_executions_trade_id ON trading.order_executions(trade_id);
CREATE INDEX IF NOT EXISTS idx_order_executions_order_id ON trading.order_executions(order_id);
CREATE INDEX IF NOT EXISTS idx_order_executions_execution_time ON trading.order_executions(execution_time DESC);
CREATE INDEX IF NOT EXISTS idx_order_executions_exchange_symbol ON trading.order_executions(exchange, symbol);
CREATE INDEX IF NOT EXISTS idx_order_executions_binance_trade_id ON trading.order_executions(binance_trade_id) WHERE binance_trade_id IS NOT NULL;

-- Add enhanced fee tracking columns to trades table
ALTER TABLE trading.trades 
ADD COLUMN IF NOT EXISTS execution_count INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS total_executed_quantity DECIMAL(20, 8) DEFAULT 0,
ADD COLUMN IF NOT EXISTS average_execution_price DECIMAL(20, 8) DEFAULT 0,
ADD COLUMN IF NOT EXISTS first_execution_time TIMESTAMP WITH TIME ZONE,
ADD COLUMN IF NOT EXISTS last_execution_time TIMESTAMP WITH TIME ZONE,
ADD COLUMN IF NOT EXISTS maker_fee_rate DECIMAL(10, 6),
ADD COLUMN IF NOT EXISTS taker_fee_rate DECIMAL(10, 6),
ADD COLUMN IF NOT EXISTS total_maker_fees DECIMAL(20, 8) DEFAULT 0,
ADD COLUMN IF NOT EXISTS total_taker_fees DECIMAL(20, 8) DEFAULT 0;

-- Add comments for documentation
COMMENT ON TABLE trading.order_executions IS 'Individual order executions for detailed tracking';
COMMENT ON COLUMN trading.order_executions.execution_id IS 'Unique execution identifier (order_id_trade_id)';
COMMENT ON COLUMN trading.order_executions.binance_trade_id IS 'Binance-specific trade ID from executionReport';
COMMENT ON COLUMN trading.order_executions.is_maker IS 'True if execution was maker side, false if taker';
COMMENT ON COLUMN trading.order_executions.fee_precision IS 'Number of decimal places in fee amount';

COMMENT ON COLUMN trading.trades.execution_count IS 'Number of individual executions for this trade';
COMMENT ON COLUMN trading.trades.total_executed_quantity IS 'Sum of all execution quantities';
COMMENT ON COLUMN trading.trades.average_execution_price IS 'Volume-weighted average execution price';
COMMENT ON COLUMN trading.trades.maker_fee_rate IS 'Maker fee rate applied to this trade';
COMMENT ON COLUMN trading.trades.taker_fee_rate IS 'Taker fee rate applied to this trade';
COMMENT ON COLUMN trading.trades.total_maker_fees IS 'Total fees from maker executions';
COMMENT ON COLUMN trading.trades.total_taker_fees IS 'Total fees from taker executions';

-- Create function to update trade statistics from executions
CREATE OR REPLACE FUNCTION trading.update_trade_from_executions(p_trade_id VARCHAR(255))
RETURNS VOID AS $$
DECLARE
    v_execution_count INTEGER;
    v_total_quantity DECIMAL(20, 8);
    v_total_value DECIMAL(20, 8);
    v_avg_price DECIMAL(20, 8);
    v_first_exec_time TIMESTAMP WITH TIME ZONE;
    v_last_exec_time TIMESTAMP WITH TIME ZONE;
    v_total_maker_fees DECIMAL(20, 8);
    v_total_taker_fees DECIMAL(20, 8);
    v_total_fees DECIMAL(20, 8);
BEGIN
    -- Calculate aggregated values from executions
    SELECT 
        COUNT(*),
        COALESCE(SUM(executed_quantity), 0),
        COALESCE(SUM(executed_quantity * executed_price), 0),
        CASE WHEN SUM(executed_quantity) > 0 
             THEN SUM(executed_quantity * executed_price) / SUM(executed_quantity) 
             ELSE 0 END,
        MIN(execution_time),
        MAX(execution_time),
        COALESCE(SUM(CASE WHEN is_maker THEN fee_amount ELSE 0 END), 0),
        COALESCE(SUM(CASE WHEN NOT is_maker THEN fee_amount ELSE 0 END), 0)
    INTO 
        v_execution_count,
        v_total_quantity, 
        v_total_value,
        v_avg_price,
        v_first_exec_time,
        v_last_exec_time,
        v_total_maker_fees,
        v_total_taker_fees
    FROM trading.order_executions 
    WHERE trade_id = p_trade_id;
    
    -- Calculate total fees
    v_total_fees := COALESCE(v_total_maker_fees, 0) + COALESCE(v_total_taker_fees, 0);
    
    -- Update trade record
    UPDATE trading.trades 
    SET 
        execution_count = COALESCE(v_execution_count, 0),
        total_executed_quantity = COALESCE(v_total_quantity, 0),
        average_execution_price = COALESCE(v_avg_price, 0),
        first_execution_time = v_first_exec_time,
        last_execution_time = v_last_exec_time,
        total_maker_fees = COALESCE(v_total_maker_fees, 0),
        total_taker_fees = COALESCE(v_total_taker_fees, 0),
        fees = COALESCE(v_total_fees, 0),  -- Update total fees
        updated_at = CURRENT_TIMESTAMP
    WHERE trade_id = p_trade_id;
END;
$$ LANGUAGE plpgsql;

-- Create trigger to auto-update trade statistics when executions change
CREATE OR REPLACE FUNCTION trading.trigger_update_trade_from_executions()
RETURNS TRIGGER AS $$
BEGIN
    -- Handle INSERT and UPDATE
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        PERFORM trading.update_trade_from_executions(NEW.trade_id);
        RETURN NEW;
    END IF;
    
    -- Handle DELETE
    IF TG_OP = 'DELETE' THEN
        PERFORM trading.update_trade_from_executions(OLD.trade_id);
        RETURN OLD;
    END IF;
    
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- Create trigger
DROP TRIGGER IF EXISTS trigger_update_trade_stats ON trading.order_executions;
CREATE TRIGGER trigger_update_trade_stats
    AFTER INSERT OR UPDATE OR DELETE ON trading.order_executions
    FOR EACH ROW
    EXECUTE FUNCTION trading.trigger_update_trade_from_executions();

-- Create view for comprehensive execution analysis
CREATE OR REPLACE VIEW trading.trade_execution_summary AS
SELECT 
    t.trade_id,
    t.pair,
    t.exchange,
    t.status,
    t.position_size,
    t.entry_price,
    t.exit_price,
    
    -- Execution statistics
    t.execution_count,
    t.total_executed_quantity,
    t.average_execution_price,
    t.first_execution_time,
    t.last_execution_time,
    
    -- Fee breakdown
    t.fees as total_fees,
    t.total_maker_fees,
    t.total_taker_fees,
    CASE WHEN t.fees > 0 
         THEN ROUND((t.total_maker_fees / t.fees * 100), 2) 
         ELSE 0 END as maker_fee_percentage,
    
    -- Execution efficiency metrics
    CASE WHEN t.position_size > 0 
         THEN ROUND((t.total_executed_quantity / t.position_size * 100), 2) 
         ELSE 0 END as fill_percentage,
    
    -- Price performance
    CASE WHEN t.entry_price > 0 AND t.average_execution_price > 0
         THEN ROUND(((t.average_execution_price - t.entry_price) / t.entry_price * 100), 4)
         ELSE 0 END as execution_slippage_pct,
    
    -- Timestamps
    t.entry_time,
    t.exit_time,
    t.created_at,
    t.updated_at
    
FROM trading.trades t
WHERE t.execution_count > 0;

-- Grant permissions
GRANT SELECT ON trading.order_executions TO trading_bot_user;
GRANT INSERT, UPDATE, DELETE ON trading.order_executions TO trading_bot_user;
GRANT USAGE ON SEQUENCE trading.order_executions_id_seq TO trading_bot_user;
GRANT SELECT ON trading.trade_execution_summary TO trading_bot_user;

-- Create indexes for the new view
CREATE INDEX IF NOT EXISTS idx_trades_execution_count ON trading.trades(execution_count) WHERE execution_count > 0;
CREATE INDEX IF NOT EXISTS idx_trades_first_execution_time ON trading.trades(first_execution_time) WHERE first_execution_time IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_trades_last_execution_time ON trading.trades(last_execution_time) WHERE last_execution_time IS NOT NULL;

-- Performance optimization: partial indexes
CREATE INDEX IF NOT EXISTS idx_order_executions_active_trades 
ON trading.order_executions(trade_id, execution_time DESC) 
WHERE execution_time >= CURRENT_TIMESTAMP - INTERVAL '7 days';

CREATE INDEX IF NOT EXISTS idx_order_executions_fee_tracking 
ON trading.order_executions(fee_currency, fee_amount, is_maker) 
WHERE fee_amount > 0;

-- Create materialized view for daily fee analytics (optional, for reporting)
CREATE MATERIALIZED VIEW IF NOT EXISTS trading.daily_fee_analytics AS
SELECT 
    DATE(execution_time) as trade_date,
    exchange,
    fee_currency,
    COUNT(*) as execution_count,
    SUM(executed_quantity) as total_quantity,
    SUM(quote_quantity) as total_notional,
    SUM(fee_amount) as total_fees,
    SUM(CASE WHEN is_maker THEN fee_amount ELSE 0 END) as maker_fees,
    SUM(CASE WHEN NOT is_maker THEN fee_amount ELSE 0 END) as taker_fees,
    AVG(fee_amount) as avg_fee_per_execution,
    COUNT(DISTINCT trade_id) as unique_trades
FROM trading.order_executions
WHERE execution_time >= CURRENT_DATE - INTERVAL '30 days'
GROUP BY DATE(execution_time), exchange, fee_currency
ORDER BY trade_date DESC, exchange, fee_currency;

-- Index for materialized view
CREATE UNIQUE INDEX IF NOT EXISTS idx_daily_fee_analytics_unique 
ON trading.daily_fee_analytics(trade_date, exchange, fee_currency);

-- Grant permission for materialized view
GRANT SELECT ON trading.daily_fee_analytics TO trading_bot_user;

-- Add refresh function for the materialized view
CREATE OR REPLACE FUNCTION trading.refresh_daily_fee_analytics()
RETURNS VOID AS $$
BEGIN
    REFRESH MATERIALIZED VIEW CONCURRENTLY trading.daily_fee_analytics;
END;
$$ LANGUAGE plpgsql;

-- Final verification query
DO $$
BEGIN
    RAISE NOTICE 'Enhanced Fee Tracking Migration v2.5.0 completed successfully!';
    RAISE NOTICE 'New tables: order_executions';
    RAISE NOTICE 'New columns added to trades table: execution_count, total_executed_quantity, average_execution_price, etc.';
    RAISE NOTICE 'New views: trade_execution_summary, daily_fee_analytics';
    RAISE NOTICE 'Triggers and functions created for automatic trade statistics updates';
END;
$$;