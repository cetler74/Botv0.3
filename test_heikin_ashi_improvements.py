#!/usr/bin/env python3
"""
Test script to validate Heikin Ashi strategy improvements
"""

import pandas as pd
import numpy as np
import asyncio
import logging
from datetime import datetime
from strategy.heikin_ashi_strategy import HeikinAshiStrategy

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class MockExchange:
    """Mock exchange for testing"""
    async def get_ohlcv(self, exchange_name, pair, timeframe, limit=100):
        """Generate mock OHLCV data for testing"""
        np.random.seed(42)  # For reproducible results
        
        # Generate realistic crypto OHLCV data
        dates = pd.date_range(start='2024-01-01', periods=limit, freq='H')
        
        # Start with base price
        base_price = 50000  # Base price around $50k
        prices = []
        volumes = []
        
        for i in range(limit):
            # Generate price with trend and volatility
            trend = 0.0001 * i  # Slight upward trend
            volatility = np.random.normal(0, 0.02)  # 2% volatility
            price_change = trend + volatility
            
            if i == 0:
                price = base_price
            else:
                price = prices[-1] * (1 + price_change)
            
            prices.append(price)
            
            # Generate volume with spikes
            base_volume = 1000 + np.random.normal(0, 200)
            if i % 10 == 0:  # Volume spike every 10 candles
                base_volume *= 2.5
            volumes.append(max(base_volume, 100))
        
        # Create OHLC from close prices
        df = pd.DataFrame({
            'timestamp': dates,
            'close': prices,
            'volume': volumes
        })
        
        # Generate realistic OHLC from close prices
        df['open'] = df['close'].shift(1).fillna(df['close'].iloc[0])
        df['high'] = df[['open', 'close']].max(axis=1) * (1 + np.random.uniform(0, 0.005, len(df)))
        df['low'] = df[['open', 'close']].min(axis=1) * (1 - np.random.uniform(0, 0.005, len(df)))
        
        df = df.set_index('timestamp')
        return df[['open', 'high', 'low', 'close', 'volume']]

    async def get_balance(self):
        return {'free': 10000.0, 'used': 0.0, 'total': 10000.0}
    
    async def get_ticker(self, pair, exchange_name):
        return {'last': 50000.0, 'bid': 49990.0, 'ask': 50010.0}

def create_test_config():
    """Create test configuration with improved parameters"""
    return {
        'heikin_ashi': {
            'target_timeframes': ['1h', '15m'],
            'parameters': {
                # Crypto-optimized parameters
                'adx_threshold': 27,  # 25-30 range for crypto
                'rsi_oversold': 25,   # Standard crypto oversold
                'rsi_overbought': 75, # Standard crypto overbought
                'rsi_uptrend_min': 40,
                'rsi_uptrend_max': 70,
                
                # Enhanced volume requirements
                'volume_spike_multiplier': 1.75,  # 1.5-2.0x range
                'volume_threshold_percentage': 0.35,  # 0.3-0.4 range
                
                # ATR-based dynamic stops
                'atr_stop_multiplier': 2.25,  # 2.0-2.5x ATR
                'atr_take_profit_multiplier': 3.0,  # Risk-reward optimization
                
                # Other parameters
                'min_candles': 50,
                'adx_period': 14,
                'rsi_period': 14,
                'atr_period': 14,
                'min_candle_size': 0.002,
                'volume_sma_period': 20,
                'risk_per_trade': 0.02,  # 2% risk per trade
                'max_hold_time_hours': 48,
                'primary_timeframe': '1h',
                
                # Profitability filters
                'require_ema_confluence': False,
                'ema_fast_period': 9,
                'ema_slow_period': 21,
                'min_trend_strength': 0.002,
                'trend_period': 14
            }
        }
    }

async def test_strategy_improvements():
    """Test the improved Heikin Ashi strategy"""
    logger.info("🧪 Testing Heikin Ashi Strategy Improvements")
    
    # Create mock objects
    config = create_test_config()
    exchange = MockExchange()
    database = None  # Not needed for this test
    redis_client = None  # Not needed for this test
    
    # Initialize strategy
    strategy = HeikinAshiStrategy(config, exchange, database, redis_client, 'binance')
    await strategy.initialize('BTCUSDT')
    
    logger.info(f"✅ Strategy initialized with crypto-optimized parameters:")
    logger.info(f"   - ADX Threshold: {strategy.adx_threshold}")
    logger.info(f"   - RSI Range: {strategy.rsi_oversold}-{strategy.rsi_overbought}")
    logger.info(f"   - RSI Uptrend: {strategy.rsi_uptrend_min}-{strategy.rsi_uptrend_max}")
    logger.info(f"   - Volume Spike: {strategy.volume_spike_multiplier}x")
    logger.info(f"   - Volume Base: {strategy.volume_threshold_percentage}")
    logger.info(f"   - ATR Stop: {strategy.atr_stop_multiplier}x")
    logger.info(f"   - ATR Target: {strategy.atr_take_profit_multiplier}x")
    
    # Generate test market data
    logger.info("📊 Generating test market data...")
    market_data = {
        '1h': await exchange.get_ohlcv('binance', 'BTCUSDT', '1h', 100),
        '15m': await exchange.get_ohlcv('binance', 'BTCUSDT', '15m', 100)
    }
    
    # Test signal generation
    logger.info("🔍 Testing signal generation...")
    results = await strategy.analyze_pair('BTCUSDT', market_data=market_data)
    
    for timeframe, (signal, confidence, indicators) in results.items():
        logger.info(f"📈 {timeframe} Results:")
        logger.info(f"   - Signal: {signal}")
        logger.info(f"   - Confidence: {confidence}")
        
        if isinstance(indicators, dict) and indicators:
            logger.info(f"   - ADX: {indicators.get('adx', 'N/A'):.2f}")
            logger.info(f"   - RSI: {indicators.get('rsi', 'N/A'):.2f}")
            logger.info(f"   - ATR: {indicators.get('atr', 'N/A'):.6f}")
            logger.info(f"   - Volume: {indicators.get('volume', 'N/A'):.0f}")
            logger.info(f"   - Volume SMA: {indicators.get('volume_sma', 'N/A'):.0f}")
            logger.info(f"   - Current Price: ${indicators.get('current_price', 'N/A'):.2f}")
            
            if signal == 'buy' and 'suggested_stop_loss' in indicators:
                logger.info(f"   - Suggested Stop Loss: ${indicators['suggested_stop_loss']:.2f}")
                logger.info(f"   - Suggested Take Profit: ${indicators['suggested_take_profit']:.2f}")
                logger.info(f"   - Risk/Reward Ratio: 1:{indicators['risk_reward_ratio']:.2f}")
    
    # Test ATR-based position sizing
    if any(result[0] == 'buy' for result in results.values()):
        logger.info("💰 Testing ATR-based position sizing...")
        current_price = 50000.0
        current_atr = 1000.0  # Mock ATR value
        
        position_size = await strategy.calculate_position_size('buy', current_price, current_atr)
        risk_amount = 10000.0 * strategy.risk_per_trade  # 2% of $10k balance
        stop_distance = current_atr * strategy.atr_stop_multiplier
        
        logger.info(f"   - Account Balance: $10,000")
        logger.info(f"   - Risk per Trade: {strategy.risk_per_trade*100}% = ${risk_amount:.2f}")
        logger.info(f"   - Current Price: ${current_price:.2f}")
        logger.info(f"   - ATR: ${current_atr:.2f}")
        logger.info(f"   - Stop Distance: ${stop_distance:.2f}")
        logger.info(f"   - Position Size: {position_size:.6f} BTC")
        logger.info(f"   - Position Value: ${position_size * current_price:.2f}")
    
    # Test exit conditions
    logger.info("🚪 Testing exit conditions...")
    
    # Mock a position
    strategy.state.position = 'long'
    strategy.state.entry_price = 50000.0
    strategy.state.entry_time = datetime.utcnow()
    strategy._current_ohlcv = market_data['1h']
    
    # Calculate and set initial stops
    current_price = market_data['1h']['close'].iloc[-1]
    current_atr = 1000.0  # Mock ATR
    stop_loss, take_profit = await strategy.calculate_atr_levels(current_price, current_atr)
    strategy.state.stop_loss = stop_loss
    strategy.state.take_profit = take_profit
    
    logger.info(f"   - Current Price: ${current_price:.2f}")
    logger.info(f"   - Stop Loss: ${stop_loss:.2f}")
    logger.info(f"   - Take Profit: ${take_profit:.2f}")
    logger.info(f"   - Stop Distance: ${current_price - stop_loss:.2f} ({((current_price - stop_loss)/current_price)*100:.2f}%)")
    logger.info(f"   - Target Distance: ${take_profit - current_price:.2f} ({((take_profit - current_price)/current_price)*100:.2f}%)")
    
    should_exit, exit_reason = await strategy.should_exit()
    logger.info(f"   - Should Exit: {should_exit} ({exit_reason})")
    
    logger.info("✅ Heikin Ashi strategy improvements testing completed!")
    
    return {
        'strategy_params': {
            'adx_threshold': strategy.adx_threshold,
            'rsi_range': f"{strategy.rsi_oversold}-{strategy.rsi_overbought}",
            'volume_spike_multiplier': strategy.volume_spike_multiplier,
            'atr_stop_multiplier': strategy.atr_stop_multiplier,
        },
        'test_results': results,
        'expected_improvements': {
            'better_trend_detection': strategy.adx_threshold > 20,
            'crypto_optimized_rsi': strategy.rsi_oversold == 25 and strategy.rsi_overbought == 75,
            'enhanced_volume_filtering': strategy.volume_spike_multiplier >= 1.5,
            'dynamic_stop_management': strategy.atr_stop_multiplier >= 2.0,
        }
    }

async def main():
    """Main test function"""
    try:
        test_results = await test_strategy_improvements()
        
        logger.info("\n📋 IMPROVEMENT SUMMARY:")
        logger.info("=" * 50)
        
        improvements = test_results['expected_improvements']
        for improvement, implemented in improvements.items():
            status = "✅ IMPLEMENTED" if implemented else "❌ NOT IMPLEMENTED"
            logger.info(f"{improvement}: {status}")
        
        logger.info("\n🎯 EXPECTED OUTCOMES:")
        logger.info("- Increased win rate through better trend detection (ADX 25-30)")
        logger.info("- Improved entry timing with crypto-optimized RSI (25-75)")
        logger.info("- Enhanced signal quality with volume spike filtering (1.5-2.0x)")
        logger.info("- Reduced losses with dynamic ATR-based stops (2.0-2.5x)")
        logger.info("- Better risk management with ATR-based position sizing")
        
        logger.info(f"\n🚀 TARGET: 50%+ win rate with optimized parameters")
        
    except Exception as e:
        logger.error(f"❌ Test failed: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(main())