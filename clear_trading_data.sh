#!/bin/bash

# Script to clear all trading data
echo "Clearing all data from trading.* tables..."

# Execute the clear command
docker exec -it trading-bot-postgres psql -U carloslarramba -d trading_bot_futures << 'EOF'
BEGIN;

-- Clear all trading tables
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

COMMIT;

-- Show counts to verify
SELECT 'alerts' as table_name, COUNT(*) as count FROM trading.alerts
UNION ALL
SELECT 'asset_positions', COUNT(*) FROM trading.asset_positions
UNION ALL
SELECT 'balance', COUNT(*) FROM trading.balance
UNION ALL
SELECT 'config_audit', COUNT(*) FROM trading.config_audit
UNION ALL
SELECT 'events', COUNT(*) FROM trading.events
UNION ALL
SELECT 'fills', COUNT(*) FROM trading.fills
UNION ALL
SELECT 'market_data_cache', COUNT(*) FROM trading.market_data_cache
UNION ALL
SELECT 'materializer_checkpoint', COUNT(*) FROM trading.materializer_checkpoint
UNION ALL
SELECT 'order_mappings', COUNT(*) FROM trading.order_mappings
UNION ALL
SELECT 'orders', COUNT(*) FROM trading.orders
UNION ALL
SELECT 'pairs', COUNT(*) FROM trading.pairs
UNION ALL
SELECT 'real_time_prices', COUNT(*) FROM trading.real_time_prices
UNION ALL
SELECT 'strategy_performance', COUNT(*) FROM trading.strategy_performance
UNION ALL
SELECT 'trades', COUNT(*) FROM trading.trades
UNION ALL
SELECT 'trailing_stops', COUNT(*) FROM trading.trailing_stops
UNION ALL
SELECT 'websocket_status', COUNT(*) FROM trading.websocket_status
ORDER BY table_name;

EOF

echo "Done! All trading tables have been cleared."
