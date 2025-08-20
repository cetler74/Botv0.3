-- Add current_price field to trades table for real-time PnL calculation
-- This allows the dashboard to show current PnL percentages

SET search_path TO trading;

-- Add current_price column to trades table if it doesn't exist
ALTER TABLE trading.trades 
ADD COLUMN IF NOT EXISTS current_price DECIMAL(20, 8);

-- Add index for performance on current_price queries
CREATE INDEX IF NOT EXISTS idx_trades_current_price ON trading.trades(current_price);

-- Update existing trades to set current_price to entry_price as a fallback
UPDATE trading.trades 
SET current_price = entry_price 
WHERE current_price IS NULL AND entry_price IS NOT NULL;

-- Add comment to document the field
COMMENT ON COLUMN trading.trades.current_price IS 'Current market price for real-time PnL calculation';
