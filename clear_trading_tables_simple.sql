-- Clear all records from trading schema tables (Simple Version)
-- This script will delete all data but preserve table structure

-- Start transaction for safety
BEGIN;

-- Clear all trading tables using TRUNCATE CASCADE to handle foreign keys
TRUNCATE TABLE trading.alerts RESTART IDENTITY CASCADE;
TRUNCATE TABLE trading.asset_positions RESTART IDENTITY CASCADE;
TRUNCATE TABLE trading.balance RESTART IDENTITY CASCADE;
TRUNCATE TABLE trading.config_audit RESTART IDENTITY CASCADE;
TRUNCATE TABLE trading.events RESTART IDENTITY CASCADE;
TRUNCATE TABLE trading.fills RESTART IDENTITY CASCADE;
TRUNCATE TABLE trading.market_data_cache RESTART IDENTITY CASCADE;
TRUNCATE TABLE trading.materializer_checkpoint RESTART IDENTITY CASCADE;
TRUNCATE TABLE trading.order_mappings RESTART IDENTITY CASCADE;
TRUNCATE TABLE trading.orders RESTART IDENTITY CASCADE;
TRUNCATE TABLE trading.pairs RESTART IDENTITY CASCADE;
TRUNCATE TABLE trading.real_time_prices RESTART IDENTITY CASCADE;
TRUNCATE TABLE trading.strategy_performance RESTART IDENTITY CASCADE;
TRUNCATE TABLE trading.trades RESTART IDENTITY CASCADE;
TRUNCATE TABLE trading.trailing_stops RESTART IDENTITY CASCADE;
TRUNCATE TABLE trading.websocket_status RESTART IDENTITY CASCADE;

-- Commit the transaction
COMMIT;

-- Show confirmation message
SELECT 'All trading tables have been cleared successfully!' AS status;
