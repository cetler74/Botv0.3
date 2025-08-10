-- Create asset_positions table for individual token/coin balances
CREATE TABLE IF NOT EXISTS trading.asset_positions (
    id SERIAL PRIMARY KEY,
    exchange VARCHAR(50) NOT NULL,
    asset VARCHAR(20) NOT NULL,
    free_balance DECIMAL(18,8) NOT NULL DEFAULT 0,
    used_balance DECIMAL(18,8) NOT NULL DEFAULT 0,
    total_balance DECIMAL(18,8) NOT NULL DEFAULT 0,
    last_updated TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    
    -- Unique constraint to prevent duplicates
    UNIQUE(exchange, asset)
);

-- Create index for faster lookups
CREATE INDEX IF NOT EXISTS idx_asset_positions_exchange_asset ON trading.asset_positions(exchange, asset);
CREATE INDEX IF NOT EXISTS idx_asset_positions_last_updated ON trading.asset_positions(last_updated);

-- Add trigger to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_asset_position_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Drop trigger if it exists, then create it
DROP TRIGGER IF EXISTS update_asset_position_updated_at ON trading.asset_positions;
CREATE TRIGGER update_asset_position_updated_at
    BEFORE UPDATE ON trading.asset_positions
    FOR EACH ROW
    EXECUTE FUNCTION update_asset_position_updated_at();

-- Insert some comments for documentation
COMMENT ON TABLE trading.asset_positions IS 'Individual asset/token balances for each exchange';
COMMENT ON COLUMN trading.asset_positions.exchange IS 'Exchange name (cryptocom, binance, bybit)';
COMMENT ON COLUMN trading.asset_positions.asset IS 'Asset symbol (BTC, ETH, A2Z, etc.)';
COMMENT ON COLUMN trading.asset_positions.free_balance IS 'Available balance for trading';
COMMENT ON COLUMN trading.asset_positions.used_balance IS 'Balance locked in orders';
COMMENT ON COLUMN trading.asset_positions.total_balance IS 'Total balance (free + used)';
COMMENT ON COLUMN trading.asset_positions.last_updated IS 'When this position was last synced from exchange';