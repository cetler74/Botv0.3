#!/usr/bin/env python3
"""
LIVE DEBUGGING: Monitor fill-detection in real-time to catch failures
Run this continuously to see exactly where the system breaks
"""
import asyncio
import aioredis
import json
import requests
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

REDIS_URL = "redis://localhost:6379"
DATABASE_URL = "http://localhost:8002"
EXCHANGE_URL = "http://localhost:8003"

class LiveFillMonitor:
    def __init__(self):
        self.redis = None
        self.monitored_trades = {}
        
    async def connect_redis(self):
        """Connect to Redis to monitor streams"""
        try:
            self.redis = aioredis.from_url(REDIS_URL)
            logger.info("✅ Connected to Redis")
            return True
        except Exception as e:
            logger.error(f"❌ Redis connection failed: {e}")
            return False
    
    async def monitor_order_events(self):
        """Monitor order events in real-time"""
        try:
            # Monitor the order events stream
            while True:
                # Read from Redis streams where orders are published
                streams = await self.redis.xread(
                    {"order_events": "$"},  # Read new events
                    block=1000,  # Block for 1 second
                    count=1
                )
                
                for stream_name, messages in streams:
                    for message_id, fields in messages:
                        logger.info(f"🔍 NEW ORDER EVENT: {fields}")
                        await self.process_order_event(fields)
                        
        except Exception as e:
            logger.error(f"❌ Error monitoring order events: {e}")
    
    async def monitor_fill_events(self):
        """Monitor fill events to see what fill-detection processes"""
        try:
            while True:
                streams = await self.redis.xread(
                    {"fill_events": "$"},  # Fill events stream
                    block=1000,
                    count=1
                )
                
                for stream_name, messages in streams:
                    for message_id, fields in messages:
                        logger.info(f"✅ FILL EVENT DETECTED: {fields}")
                        await self.verify_fill_processing(fields)
                        
        except Exception as e:
            logger.error(f"❌ Error monitoring fill events: {e}")
    
    async def process_order_event(self, event_data):
        """Process new order events and track them"""
        try:
            order_id = event_data.get(b'order_id', b'').decode()
            exchange_order_id = event_data.get(b'exchange_order_id', b'').decode()
            side = event_data.get(b'side', b'').decode()
            pair = event_data.get(b'symbol', b'').decode()
            
            if not order_id or not exchange_order_id:
                return
                
            # Track this order
            self.monitored_trades[order_id] = {
                'exchange_order_id': exchange_order_id,
                'side': side,
                'pair': pair,
                'status': 'PENDING',
                'created_at': datetime.now()
            }
            
            logger.info(f"📝 TRACKING ORDER: {order_id[:8]} -> {exchange_order_id} ({side} {pair})")
            
            # Start monitoring this specific order
            asyncio.create_task(self.monitor_specific_order(order_id, exchange_order_id))
            
        except Exception as e:
            logger.error(f"❌ Error processing order event: {e}")
    
    async def monitor_specific_order(self, order_id, exchange_order_id):
        """Monitor a specific order until it's filled or cancelled"""
        logger.info(f"🔍 Starting specific monitoring for {order_id[:8]} -> {exchange_order_id}")
        
        for attempt in range(120):  # Monitor for 10 minutes (5s intervals)
            try:
                # Check order status on exchange
                exchange = self.get_exchange_from_order_id(exchange_order_id)
                if not exchange:
                    continue
                    
                response = requests.get(f"{EXCHANGE_URL}/api/v1/trading/order/{exchange}/{exchange_order_id}")
                if response.status_code == 200:
                    order_data = response.json().get("order", {})
                    status = order_data.get("status", "").lower()
                    filled = order_data.get("filled", 0)
                    
                    logger.info(f"🔍 Order {order_id[:8]}: status={status}, filled={filled}")
                    
                    if status == "closed" or (filled > 0):
                        logger.warning(f"🚨 ORDER FILLED ON EXCHANGE: {order_id[:8]} -> {exchange_order_id}")
                        logger.warning(f"   Status: {status}, Filled: {filled}")
                        
                        # Wait a bit then check if fill-detection caught it
                        await asyncio.sleep(10)
                        await self.verify_fill_detection(order_id, exchange_order_id)
                        break
                        
                await asyncio.sleep(5)  # Check every 5 seconds
                
            except Exception as e:
                logger.error(f"❌ Error monitoring order {order_id[:8]}: {e}")
                await asyncio.sleep(5)
    
    async def verify_fill_detection(self, order_id, exchange_order_id):
        """Verify if fill-detection service caught the fill"""
        try:
            # Check database for trade updates
            response = requests.get(f"{DATABASE_URL}/api/v1/trades")
            if response.status_code == 200:
                trades = response.json().get("trades", [])
                
                # Find trade by order_id or exchange_order_id
                found_trade = None
                for trade in trades:
                    if (trade.get("entry_id") == exchange_order_id or 
                        trade.get("exit_id") == exchange_order_id):
                        found_trade = trade
                        break
                
                if found_trade:
                    logger.info(f"✅ Trade found in database: {found_trade['trade_id'][:8]} - {found_trade['status']}")
                else:
                    logger.error(f"🚨 CRITICAL: Order {exchange_order_id} filled on exchange but NOT found in database!")
                    logger.error(f"   This is a MISSED FILL - exactly what we're looking for!")
                    
        except Exception as e:
            logger.error(f"❌ Error verifying fill detection: {e}")
    
    def get_exchange_from_order_id(self, order_id):
        """Determine exchange from order ID format"""
        if len(order_id) > 15:  # Crypto.com has long order IDs
            return "cryptocom"
        elif order_id.isdigit():
            return "binance"
        else:
            return "bybit"
    
    async def verify_fill_processing(self, fill_data):
        """Verify that fill events are properly processed"""
        logger.info(f"🔍 Verifying fill processing: {fill_data}")
        # Add verification logic here

async def main():
    """Main monitoring function"""
    logger.info("🚨 STARTING LIVE FILL-DETECTION DEBUGGING")
    logger.info("This will monitor trades in real-time to catch failures")
    
    monitor = LiveFillMonitor()
    
    if not await monitor.connect_redis():
        logger.error("❌ Cannot connect to Redis - debugging impossible")
        return
    
    try:
        # Run multiple monitoring tasks concurrently
        await asyncio.gather(
            monitor.monitor_order_events(),
            monitor.monitor_fill_events(),
        )
    except KeyboardInterrupt:
        logger.info("🛑 Monitoring stopped by user")
    except Exception as e:
        logger.error(f"❌ Fatal error in monitoring: {e}")

if __name__ == "__main__":
    asyncio.run(main())