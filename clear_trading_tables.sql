-- Clear all records from trading schema tables
-- This script will delete all data but preserve table structure

-- Start transaction for safety
BEGIN;

-- Disable foreign key constraints temporarily to avoid dependency issues
SET session_replication_role = replica;

-- Clear all trading tables
TRUNCATE TABLE trading.alerts CASCADE;
TRUNCATE TABLE trading.asset_positions CASCADE;explain how 
TRUNCATE TABLE trading.balance CASCADE;
TRUNCATE TABLE trading.config_audit CASCADE;
TRUNCATE TABLE trading.events CASCADE;
TRUNCATE TABLE trading.fills CASCADE;
TRUNCATE TABLE trading.market_data_cache CASCADE;
TRUNCATE TABLE trading.materializer_checkpoint CASCADE;
TRUNCATE TABLE trading.order_mappings CASCADE;
TRUNCATE TABLE trading.orders CASCADE;
TRUNCATE TABLE trading.pairs CASCADE;
TRUNCATE TABLE trading.real_time_prices CASCADE;
TRUNCATE TABLE trading.strategy_performance CASCADE;
TRUNCATE TABLE trading.trades CASCADE;
TRUNCATE TABLE trading.trailing_stops CASCADE;
TRUNCATE TABLE trading.websocket_status CASCADE;

-- Re-enable foreign key constraints
SET session_replication_role = DEFAULT;

-- Reset sequences to start from 1 for tables with auto-incrementing IDs
-- Note: Only reset sequences for tables that actually have them
SELECT CASE 
    WHEN EXISTS (SELECT 1 FROM information_schema.sequences WHERE sequence_schema = 'trading' AND sequence_name = 'alerts_id_seq') 
    THEN 'ALTER SEQUENCE trading.alerts_id_seq RESTART WITH 1;'
    ELSE '-- No alerts_id_seq found'
END;

SELECT CASE 
    WHEN EXISTS (SELECT 1 FROM information_schema.sequences WHERE sequence_schema = 'trading' AND sequence_name = 'asset_positions_id_seq') 
    THEN 'ALTER SEQUENCE trading.asset_positions_id_seq RESTART WITH 1;'
    ELSE '-- No asset_positions_id_seq found'
END;

SELECT CASE 
    WHEN EXISTS (SELECT 1 FROM information_schema.sequences WHERE sequence_schema = 'trading' AND sequence_name = 'balance_id_seq') 
    THEN 'ALTER SEQUENCE trading.balance_id_seq RESTART WITH 1;'
    ELSE '-- No balance_id_seq found'
END;

SELECT CASE 
    WHEN EXISTS (SELECT 1 FROM information_schema.sequences WHERE sequence_schema = 'trading' AND sequence_name = 'config_audit_id_seq') 
    THEN 'ALTER SEQUENCE trading.config_audit_id_seq RESTART WITH 1;'
    ELSE '-- No config_audit_id_seq found'
END;

SELECT CASE 
    WHEN EXISTS (SELECT 1 FROM information_schema.sequences WHERE sequence_schema = 'trading' AND sequence_name = 'events_id_seq') 
    THEN 'ALTER SEQUENCE trading.events_id_seq RESTART WITH 1;'
    ELSE '-- No events_id_seq found'
END;

SELECT CASE 
    WHEN EXISTS (SELECT 1 FROM information_schema.sequences WHERE sequence_schema = 'trading' AND sequence_name = 'fills_id_seq') 
    THEN 'ALTER SEQUENCE trading.fills_id_seq RESTART WITH 1;'
    ELSE '-- No fills_id_seq found'
END;

SELECT CASE 
    WHEN EXISTS (SELECT 1 FROM information_schema.sequences WHERE sequence_schema = 'trading' AND sequence_name = 'order_mappings_id_seq') 
    THEN 'ALTER SEQUENCE trading.order_mappings_id_seq RESTART WITH 1;'
    ELSE '-- No order_mappings_id_seq found'
END;

SELECT CASE 
    WHEN EXISTS (SELECT 1 FROM information_schema.sequences WHERE sequence_schema = 'trading' AND sequence_name = 'orders_id_seq') 
    THEN 'ALTER SEQUENCE trading.orders_id_seq RESTART WITH 1;'
    ELSE '-- No orders_id_seq found'
END;

SELECT CASE 
    WHEN EXISTS (SELECT 1 FROM information_schema.sequences WHERE sequence_schema = 'trading' AND sequence_name = 'pairs_id_seq') 
    THEN 'ALTER SEQUENCE trading.pairs_id_seq RESTART WITH 1;'
    ELSE '-- No pairs_id_seq found'
END;

SELECT CASE 
    WHEN EXISTS (SELECT 1 FROM information_schema.sequences WHERE sequence_schema = 'trading' AND sequence_name = 'strategy_performance_id_seq') 
    THEN 'ALTER SEQUENCE trading.strategy_performance_id_seq RESTART WITH 1;'
    ELSE '-- No strategy_performance_id_seq found'
END;

SELECT CASE 
    WHEN EXISTS (SELECT 1 FROM information_schema.sequences WHERE sequence_schema = 'trading' AND sequence_name = 'trades_id_seq') 
    THEN 'ALTER SEQUENCE trading.trades_id_seq RESTART WITH 1;'
    ELSE '-- No trades_id_seq found'
END;

SELECT CASE 
    WHEN EXISTS (SELECT 1 FROM information_schema.sequences WHERE sequence_schema = 'trading' AND sequence_name = 'trailing_stops_id_seq') 
    THEN 'ALTER SEQUENCE trading.trailing_stops_id_seq RESTART WITH 1;'
    ELSE '-- No trailing_stops_id_seq found'
END;

-- Commit the transaction
COMMIT;

-- Show confirmation message
SELECT 'All trading tables have been cleared successfully!' AS status;
