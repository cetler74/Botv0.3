-- Fix for CLOSED trades validation
-- Ensures all CLOSED trades have exit_time and exit_price

-- 1. Add CHECK constraints to enforce that CLOSED trades must have exit_time and exit_price
ALTER TABLE trading.trades 
ADD CONSTRAINT chk_closed_trades_exit_time 
CHECK (
    (status != 'CLOSED') OR 
    (status = 'CLOSED' AND exit_time IS NOT NULL)
);

ALTER TABLE trading.trades 
ADD CONSTRAINT chk_closed_trades_exit_price 
CHECK (
    (status != 'CLOSED') OR 
    (status = 'CLOSED' AND exit_price IS NOT NULL)
);

-- 2. Create a function to validate and fix trades when they are marked as CLOSED
CREATE OR REPLACE FUNCTION validate_trade_closure()
RETURNS TRIGGER AS $$
BEGIN
    -- If status is being changed to CLOSED, ensure required fields are set
    IF NEW.status = 'CLOSED' THEN
        -- Set exit_time if not provided
        IF NEW.exit_time IS NULL THEN
            NEW.exit_time = COALESCE(OLD.exit_time, CURRENT_TIMESTAMP);
        END IF;
        
        -- If exit_price is still NULL, this is an error
        IF NEW.exit_price IS NULL THEN
            RAISE EXCEPTION 'Cannot close trade % without exit_price', NEW.trade_id;
        END IF;
        
        -- Ensure exit_reason is set
        IF NEW.exit_reason IS NULL OR NEW.exit_reason = '' THEN
            NEW.exit_reason = 'closed_without_reason';
        END IF;
    END IF;
    
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 3. Create trigger to run validation function
DROP TRIGGER IF EXISTS trg_validate_trade_closure ON trading.trades;
CREATE TRIGGER trg_validate_trade_closure
    BEFORE UPDATE ON trading.trades
    FOR EACH ROW
    EXECUTE FUNCTION validate_trade_closure();

-- 4. Create a view to easily identify problematic trades
CREATE OR REPLACE VIEW trading.invalid_closed_trades AS
SELECT 
    trade_id,
    pair,
    exchange,
    status,
    entry_time,
    exit_time,
    entry_price,
    exit_price,
    exit_reason,
    exit_id,
    CASE 
        WHEN status = 'CLOSED' AND exit_time IS NULL THEN 'missing_exit_time'
        WHEN status = 'CLOSED' AND exit_price IS NULL THEN 'missing_exit_price'
        WHEN status = 'CLOSED' AND (exit_reason IS NULL OR exit_reason = '') THEN 'missing_exit_reason'
        ELSE 'valid'
    END as validation_issue
FROM trading.trades 
WHERE status = 'CLOSED' 
  AND (exit_time IS NULL OR exit_price IS NULL OR exit_reason IS NULL OR exit_reason = '')
ORDER BY entry_time DESC;
