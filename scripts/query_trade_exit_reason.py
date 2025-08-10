import asyncio
from core.database_manager import DatabaseManager
from core.config_manager import ConfigManager

async def main():
    config = ConfigManager('config/config.yaml')
    db_config = config.get_database_config()
    db = DatabaseManager(db_config)
    # Query for the specific trade
    trade_id = '4f04f52e-244a-445c-bad0-2cdddccb85bf'
    result = await db.execute_query(f"""
        SELECT trade_id, pair, entry_price, exit_price, realized_pnl, exit_reason, status, entry_time, exit_time 
        FROM trades WHERE trade_id = '{trade_id}'
    """)
    if result:
        for row in result:
            print(f"Trade {row['trade_id']} | Pair: {row['pair']} | Entry: {row['entry_price']} | Exit: {row['exit_price']} | PnL: {row['realized_pnl']} | Exit Reason: {row['exit_reason']} | Status: {row['status']} | Entry Time: {row['entry_time']} | Exit Time: {row['exit_time']}")
    else:
        print(f"No trade found with ID {trade_id}")
    await db.close()

if __name__ == "__main__":
    asyncio.run(main()) 