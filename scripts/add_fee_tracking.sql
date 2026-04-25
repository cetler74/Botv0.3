-- Migration: Add detailed fee tracking to trades table
-- Date: 2025-08-26
-- Description: Add separate entry and exit fee columns for better fee tracking

SET search_path TO trading;

-- Add new fee tracking columns to trades table
ALTER TABLE trading.trades 
ADD COLUMN IF NOT EXISTS entry_fee_amount DECIMAL(20, 8) DEFAULT 0,
ADD COLUMN IF NOT EXISTS entry_fee_currency VARCHAR(10),
ADD COLUMN IF NOT EXISTS exit_fee_amount DECIMAL(20, 8) DEFAULT 0,
ADD COLUMN IF NOT EXISTS exit_fee_currency VARCHAR(10),
ADD COLUMN IF NOT EXISTS total_fees_usd DECIMAL(20, 8) DEFAULT 0;

-- Add comments for documentation
COMMENT ON COLUMN trading.trades.entry_fee_amount IS 'Fee amount paid for entry order';
COMMENT ON COLUMN trading.trades.entry_fee_currency IS 'Currency in which entry fee was paid (e.g., USD, BTC, APE)';
COMMENT ON COLUMN trading.trades.exit_fee_amount IS 'Fee amount paid for exit order';
COMMENT ON COLUMN trading.trades.exit_fee_currency IS 'Currency in which exit fee was paid';
COMMENT ON COLUMN trading.trades.total_fees_usd IS 'Total fees converted to USD for reporting';

-- Keep the original fees column for backward compatibility (will sum entry + exit fees)
COMMENT ON COLUMN trading.trades.fees IS 'Legacy total fees column (backward compatibility)';

-- Create index for fee queries
CREATE INDEX IF NOT EXISTS idx_trades_total_fees_usd ON trading.trades(total_fees_usd) WHERE total_fees_usd > 0;
CREATE INDEX IF NOT EXISTS idx_trades_entry_fee ON trading.trades(entry_fee_amount) WHERE entry_fee_amount > 0;
CREATE INDEX IF NOT EXISTS idx_trades_exit_fee ON trading.trades(exit_fee_amount) WHERE exit_fee_amount > 0;

-- Verify the changes
\d trading.trades;