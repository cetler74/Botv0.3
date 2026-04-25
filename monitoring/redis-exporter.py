#!/usr/bin/env python3
"""
Redis Streams Exporter for Prometheus
Exposes Redis streams data as Prometheus metrics for monitoring trading activity
"""

import redis
import time
import json
from prometheus_client import start_http_server, Gauge, Counter, Histogram, Info
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Prometheus metrics
TRADE_LIFECYCLE_EVENTS = Counter(
    'trading_trade_lifecycle_events_total',
    'Total trade lifecycle events',
    ['event_type', 'status']
)

ORDER_STATE_CHANGES = Counter(
    'trading_order_state_changes_total',
    'Total order state changes',
    ['old_state', 'new_state', 'exchange']
)

ORDER_FILLS = Counter(
    'trading_order_fills_total',
    'Total order fills',
    ['exchange', 'symbol', 'side']
)

FILL_AMOUNT = Gauge(
    'trading_fill_amount',
    'Order fill amount',
    ['exchange', 'symbol', 'side']
)

FILL_PRICE = Gauge(
    'trading_fill_price',
    'Order fill price',
    ['exchange', 'symbol', 'side']
)

REDIS_STREAM_LENGTH = Gauge(
    'redis_stream_length',
    'Number of messages in Redis stream',
    ['stream_name']
)

WORKER_HEARTBEAT = Gauge(
    'trading_worker_heartbeat_timestamp',
    'Last heartbeat timestamp for workers',
    ['worker_name']
)

TRADE_PROCESSING_DURATION = Histogram(
    'trading_trade_processing_duration_seconds',
    'Time to process trades',
    ['exchange', 'symbol']
)

class RedisStreamsExporter:
    def __init__(self, redis_host='redis', redis_port=6379):
        self.redis_client = redis.Redis(host=redis_host, port=redis_port, decode_responses=True)
        self.last_positions = {
            'trading:trade_lifecycle:stream': '0-0',
            'trading:order_state:stream': '0-0',
            'trading:fills:stream': '0-0'
        }
        
    def collect_metrics(self):
        """Collect metrics from Redis streams"""
        try:
            # Collect stream lengths
            self._collect_stream_lengths()
            
            # Collect trade lifecycle events
            self._collect_trade_lifecycle_events()
            
            # Collect order state changes
            self._collect_order_state_changes()
            
            # Collect order fills
            self._collect_order_fills()
            
            # Collect worker heartbeats
            self._collect_worker_heartbeats()
            
            logger.info("Metrics collection completed successfully")
            
        except Exception as e:
            logger.error(f"Error collecting metrics: {e}")
    
    def _collect_stream_lengths(self):
        """Collect stream length metrics"""
        streams = [
            'trading:trade_lifecycle:stream',
            'trading:order_state:stream', 
            'trading:fills:stream'
        ]
        
        for stream in streams:
            try:
                length = self.redis_client.xlen(stream)
                REDIS_STREAM_LENGTH.labels(stream_name=stream).set(length)
            except Exception as e:
                logger.error(f"Error getting length for stream {stream}: {e}")
    
    def _collect_trade_lifecycle_events(self):
        """Collect trade lifecycle events"""
        stream = 'trading:trade_lifecycle:stream'
        try:
            # Get new messages since last position
            messages = self.redis_client.xread(
                {stream: self.last_positions[stream]},
                count=100,
                block=0
            )
            
            if messages:
                for stream_name, stream_messages in messages:
                    for msg_id, fields in stream_messages:
                        event_type = fields.get('lifecycle_event', 'unknown')
                        status = 'success' if 'completed' in event_type else 'pending'
                        
                        TRADE_LIFECYCLE_EVENTS.labels(
                            event_type=event_type,
                            status=status
                        ).inc()
                        
                        # Track processing duration for completed trades
                        if 'completed' in event_type:
                            try:
                                data = json.loads(fields.get('data', '{}'))
                                if 'monitoring_duration_seconds' in data:
                                    duration = float(data['monitoring_duration_seconds'])
                                    exchange = data.get('exchange', 'unknown')
                                    symbol = data.get('symbol', 'unknown')
                                    TRADE_PROCESSING_DURATION.labels(
                                        exchange=exchange,
                                        symbol=symbol
                                    ).observe(duration)
                            except (json.JSONDecodeError, KeyError, ValueError):
                                pass
                        
                        self.last_positions[stream] = msg_id
                        
        except Exception as e:
            logger.error(f"Error collecting trade lifecycle events: {e}")
    
    def _collect_order_state_changes(self):
        """Collect order state change events"""
        stream = 'trading:order_state:stream'
        try:
            messages = self.redis_client.xread(
                {stream: self.last_positions[stream]},
                count=100,
                block=0
            )
            
            if messages:
                for stream_name, stream_messages in messages:
                    for msg_id, fields in stream_messages:
                        old_state = fields.get('old_state', 'unknown')
                        new_state = fields.get('new_state', 'unknown')
                        
                        # Extract exchange from metadata
                        exchange = 'unknown'
                        try:
                            metadata = json.loads(fields.get('metadata', '{}'))
                            exchange = metadata.get('exchange', 'unknown')
                        except (json.JSONDecodeError, KeyError):
                            pass
                        
                        ORDER_STATE_CHANGES.labels(
                            old_state=old_state,
                            new_state=new_state,
                            exchange=exchange
                        ).inc()
                        
                        self.last_positions[stream] = msg_id
                        
        except Exception as e:
            logger.error(f"Error collecting order state changes: {e}")
    
    def _collect_order_fills(self):
        """Collect order fill events"""
        stream = 'trading:fills:stream'
        try:
            messages = self.redis_client.xread(
                {stream: self.last_positions[stream]},
                count=100,
                block=0
            )
            
            if messages:
                for stream_name, stream_messages in messages:
                    for msg_id, fields in stream_messages:
                        exchange = fields.get('exchange', 'unknown')
                        symbol = fields.get('symbol', 'unknown')
                        side = fields.get('side', 'unknown')
                        amount = float(fields.get('amount', 0))
                        price = float(fields.get('avg_price', 0))
                        
                        ORDER_FILLS.labels(
                            exchange=exchange,
                            symbol=symbol,
                            side=side
                        ).inc()
                        
                        FILL_AMOUNT.labels(
                            exchange=exchange,
                            symbol=symbol,
                            side=side
                        ).set(amount)
                        
                        FILL_PRICE.labels(
                            exchange=exchange,
                            symbol=symbol,
                            side=side
                        ).set(price)
                        
                        self.last_positions[stream] = msg_id
                        
        except Exception as e:
            logger.error(f"Error collecting order fills: {e}")
    
    def _collect_worker_heartbeats(self):
        """Collect worker heartbeat timestamps"""
        try:
            heartbeat_keys = self.redis_client.keys('trading:workers:heartbeat:*')
            
            for key in heartbeat_keys:
                worker_name = key.split(':')[-1]
                heartbeat_str = self.redis_client.get(key)
                
                if heartbeat_str:
                    try:
                        # Parse timestamp and convert to Unix timestamp
                        import datetime
                        heartbeat_dt = datetime.datetime.fromisoformat(heartbeat_str.replace('Z', '+00:00'))
                        timestamp = heartbeat_dt.timestamp()
                        
                        WORKER_HEARTBEAT.labels(worker_name=worker_name).set(timestamp)
                    except Exception as e:
                        logger.error(f"Error parsing heartbeat for {worker_name}: {e}")
                        
        except Exception as e:
            logger.error(f"Error collecting worker heartbeats: {e}")

def main():
    """Main function to run the exporter"""
    # Start HTTP server for Prometheus metrics
    start_http_server(8009)
    logger.info("Redis Streams Exporter started on port 8009")
    
    # Initialize exporter
    exporter = RedisStreamsExporter()
    
    # Collect metrics every 15 seconds
    while True:
        try:
            exporter.collect_metrics()
            time.sleep(15)
        except KeyboardInterrupt:
            logger.info("Exporter stopped by user")
            break
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            time.sleep(15)

if __name__ == '__main__':
    main()
