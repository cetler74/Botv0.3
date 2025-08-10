import asyncio
from strategy.heikin_ashi_strategy import HeikinAshiStrategy
from core.exchange_manager import ExchangeManager
import yaml

# Mock database manager (if you don't want to use the real one)
class MockDatabaseManager:
    async def cache_market_data(self, *args, **kwargs): pass
    async def get_cached_market_data(self, *args, **kwargs): return None
    async def create_alert(self, *args, **kwargs): pass

async def main():
    # Load config
    with open("config/config.yaml") as f:
        config = yaml.safe_load(f)

    # Initialize exchange manager
    db_manager = MockDatabaseManager()
    exchange_manager = ExchangeManager(config, database_manager=db_manager)

    # Choose an exchange and print all available trading pairs
    exchange_name = list(config['exchanges'].keys())[0]
    pairs = await exchange_manager.get_trading_pairs(exchange_name)
    print(f"Available pairs for {exchange_name}: {pairs}")
    # Print raw market symbols
    markets = await exchange_manager.get_markets(exchange_name)
    print(f"\nRaw market symbols for {exchange_name}:")
    for symbol in markets.keys():
        print(symbol)
    if not pairs:
        print("No trading pairs found.")
        return

    # Initialize strategy
    strategy = HeikinAshiStrategy(config, exchange_manager, db_manager)

    try:
        for pair in pairs:
            print(f"\nTesting pair: {pair}")
            signal, confidence, indicators = await strategy.analyze_pair(pair, timeframe='1h', min_candles=50)
            print(f"Signal: {signal}, Confidence: {confidence:.2f}, Indicators: {list(indicators.keys()) if indicators else {}}")
            if signal != 'hold':
                print(f"First actionable signal found for {pair}: {signal}")
                break
    finally:
        await exchange_manager.close()

if __name__ == "__main__":
    asyncio.run(main()) 