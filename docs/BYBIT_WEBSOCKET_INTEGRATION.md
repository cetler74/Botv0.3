# Bybit User Data Stream Integration - Version 2.6.0

## Overview

This document outlines the complete integration of Bybit's User Data Stream WebSocket API for real-time order tracking, position updates, and trade management in the multi-exchange trading bot.

## Architecture Overview

### **Data Flow**
```
Bybit Exchange → WebSocket Stream → Event Processing → Database → Dashboard
     ↓              ↓                    ↓              ↓          ↓
  Order Fill → Real-time Event → Trade Update → PnL Calc → UI Update
```

### **Component Architecture**
```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   Bybit API     │    │  WebSocket       │    │  Event          │
│   (REST)        │◄──►│  Manager         │◄──►│  Processor      │
└─────────────────┘    └──────────────────┘    └─────────────────┘
                              │                        │
                              ▼                        ▼
                       ┌──────────────────┐    ┌─────────────────┐
                       │  Authentication  │    │  Fill Detection │
                       │  Manager         │    │  Service        │
                       └──────────────────┘    └─────────────────┘
                              │                        │
                              ▼                        ▼
                       ┌──────────────────┐    ┌─────────────────┐
                       │  Connection      │    │  Database       │
                       │  Manager         │    │  Service        │
                       └──────────────────┘    └─────────────────┘
```

## Bybit WebSocket API Specifications

### **Authentication**
Bybit uses HMAC-SHA256 signature-based authentication with timestamp validation:

```python
import hmac
import hashlib
import time

def generate_bybit_signature(api_secret: str, timestamp: str) -> str:
    """Generate HMAC-SHA256 signature for Bybit WebSocket authentication"""
    message = f"GET/realtime{timestamp}"
    signature = hmac.new(
        api_secret.encode('utf-8'),
        message.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return signature

# Authentication payload
auth_payload = {
    "op": "auth",
    "args": [api_key, timestamp, signature]
}
```

### **WebSocket URLs**
- **Private Stream**: `wss://stream.bybit.com/v5/private`
- **Public Stream**: `wss://stream.bybit.com/v5/public/spot`

### **Channel Subscriptions**
```json
{
  "op": "subscribe",
  "args": ["order", "execution", "position", "wallet"]
}
```

### **Event Types**

#### **Order Updates**
```json
{
  "topic": "order",
  "type": "snapshot",
  "ts": 1672304486868,
  "data": [
    {
      "orderId": "1234567890",
      "orderLinkId": "test-001",
      "symbol": "BTCUSDT",
      "side": "Buy",
      "orderType": "Limit",
      "price": "20000",
      "qty": "0.001",
      "cumExecQty": "0.001",
      "cumExecFee": "0.000001",
      "avgPrice": "20000",
      "orderStatus": "Filled",
      "lastExecPrice": "20000",
      "lastExecQty": "0.001",
      "execTime": "1672304486868"
    }
  ]
}
```

#### **Execution Reports**
```json
{
  "topic": "execution",
  "type": "snapshot",
  "ts": 1672304486868,
  "data": [
    {
      "symbol": "BTCUSDT",
      "side": "Buy",
      "orderId": "1234567890",
      "execId": "1234567890",
      "orderLinkId": "test-001",
      "price": "20000",
      "qty": "0.001",
      "execFee": "0.000001",
      "execTime": "1672304486868"
    }
  ]
}
```

#### **Position Updates**
```json
{
  "topic": "position",
  "type": "snapshot",
  "ts": 1672304486868,
  "data": [
    {
      "symbol": "BTCUSDT",
      "side": "Buy",
      "size": "0.001",
      "avgPrice": "20000",
      "unrealizedPnl": "0.00",
      "markPrice": "20000",
      "positionValue": "20.00"
    }
  ]
}
```

## Implementation Details

### **1. Authentication Manager**
```python
class BybitAuthManager:
    """Manages Bybit WebSocket authentication"""
    
    def __init__(self, api_key: str, api_secret: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.timestamp_tolerance = 5000  # 5 seconds
    
    def generate_auth_payload(self) -> dict:
        """Generate authentication payload for WebSocket connection"""
        timestamp = str(int(time.time() * 1000))
        signature = self.generate_signature(timestamp)
        
        return {
            "op": "auth",
            "args": [self.api_key, timestamp, signature]
        }
    
    def generate_signature(self, timestamp: str) -> str:
        """Generate HMAC-SHA256 signature"""
        message = f"GET/realtime{timestamp}"
        return hmac.new(
            self.api_secret.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
```

### **2. Connection Manager**
```python
class BybitConnectionManager:
    """Manages Bybit WebSocket connection with automatic reconnection"""
    
    def __init__(self, websocket_url: str, max_reconnect_attempts: int = 10):
        self.websocket_url = websocket_url
        self.max_reconnect_attempts = max_reconnect_attempts
        self.reconnect_delay = 5
        self.heartbeat_interval = 20
        self.is_connected = False
        self.connection_task = None
        self.heartbeat_task = None
    
    async def connect(self) -> bool:
        """Establish WebSocket connection with authentication"""
        try:
            self.websocket = await websockets.connect(
                self.websocket_url,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=10
            )
            
            # Authenticate
            auth_payload = self.auth_manager.generate_auth_payload()
            await self.websocket.send(json.dumps(auth_payload))
            
            # Subscribe to channels
            await self.subscribe_to_channels()
            
            # Start heartbeat
            self.heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            
            self.is_connected = True
            return True
            
        except Exception as e:
            logger.error(f"Failed to connect to Bybit WebSocket: {e}")
            return False
    
    async def _heartbeat_loop(self):
        """Send periodic heartbeat messages"""
        while self.is_connected:
            try:
                heartbeat_payload = {"op": "ping"}
                await self.websocket.send(json.dumps(heartbeat_payload))
                await asyncio.sleep(self.heartbeat_interval)
            except Exception as e:
                logger.error(f"Heartbeat failed: {e}")
                break
```

### **3. Event Processor**
```python
class BybitEventProcessor:
    """Processes Bybit WebSocket events"""
    
    def __init__(self):
        self.event_handlers = {
            'order': self._handle_order_update,
            'execution': self._handle_execution_report,
            'position': self._handle_position_update,
            'wallet': self._handle_wallet_update
        }
    
    async def process_event(self, event_data: dict):
        """Process incoming WebSocket event"""
        topic = event_data.get('topic')
        event_type = event_data.get('type')
        timestamp = event_data.get('ts')
        data = event_data.get('data', [])
        
        if topic in self.event_handlers:
            await self.event_handlers[topic](data, event_type, timestamp)
    
    async def _handle_order_update(self, data: list, event_type: str, timestamp: int):
        """Handle order status updates"""
        for order in data:
            order_id = order.get('orderId')
            order_status = order.get('orderStatus')
            symbol = order.get('symbol')
            
            if order_status == 'Filled':
                await self._process_order_fill(order)
            elif order_status in ['Cancelled', 'Rejected']:
                await self._process_order_cancellation(order)
    
    async def _handle_execution_report(self, data: list, event_type: str, timestamp: int):
        """Handle execution reports"""
        for execution in data:
            await self._process_execution(execution)
    
    async def _process_order_fill(self, order_data: dict):
        """Process order fill event"""
        fill_event = {
            'exchange_order_id': order_data.get('orderId'),
            'exchange': 'bybit',
            'status': 'filled',
            'filled_amount': float(order_data.get('cumExecQty', 0)),
            'price': float(order_data.get('avgPrice', 0)),
            'fee_amount': float(order_data.get('cumExecFee', 0)),
            'fee_currency': 'USDT',
            'timestamp': datetime.fromtimestamp(order_data.get('execTime', 0) / 1000).isoformat()
        }
        
        # Emit to fill detection service
        await self._emit_fill_event(fill_event)
```

### **4. Integration with Fill Detection Service**
```python
class BybitWebSocketConsumer:
    """Consumes Bybit WebSocket events for fill detection"""
    
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.event_processor = BybitEventProcessor()
    
    async def handle_bybit_event(self, event_data: dict) -> bool:
        """Handle Bybit WebSocket event"""
        try:
            # Process the event
            await self.event_processor.process_event(event_data)
            
            # Emit to Redis stream for existing pipeline
            await self._emit_to_redis_stream(event_data)
            
            return True
            
        except Exception as e:
            logger.error(f"Error processing Bybit event: {e}")
            return False
    
    async def _emit_to_redis_stream(self, event_data: dict):
        """Emit event to Redis stream for processing"""
        stream_data = {
            "event_type": "bybit_websocket",
            "exchange": "bybit",
            "raw_data": event_data,
            "timestamp": datetime.utcnow().isoformat()
        }
        
        await self.redis.xadd("trading:bybit:stream", stream_data)
```

## Error Handling and Recovery

### **Error Categories**
1. **Authentication Errors**: Invalid API keys, expired signatures
2. **Connection Errors**: Network issues, WebSocket disconnections
3. **Rate Limit Errors**: Exceeding 20 requests per second
4. **Subscription Errors**: Channel subscription failures
5. **Processing Errors**: Event processing failures

### **Recovery Strategies**
```python
class BybitErrorHandler:
    """Handles Bybit-specific errors and recovery"""
    
    async def handle_error(self, error: Exception, context: dict) -> List[str]:
        """Handle error and return recovery actions"""
        error_type = self._categorize_error(error)
        
        if error_type == "authentication":
            return ["refresh_authentication", "reconnect"]
        elif error_type == "connection":
            return ["reconnect", "fallback_to_rest"]
        elif error_type == "rate_limit":
            return ["backoff", "retry"]
        elif error_type == "subscription":
            return ["resubscribe", "validate_channels"]
        else:
            return ["log_error", "continue"]
    
    def _categorize_error(self, error: Exception) -> str:
        """Categorize error for appropriate recovery"""
        error_message = str(error).lower()
        
        if "auth" in error_message or "signature" in error_message:
            return "authentication"
        elif "connection" in error_message or "websocket" in error_message:
            return "connection"
        elif "rate limit" in error_message:
            return "rate_limit"
        elif "subscription" in error_message:
            return "subscription"
        else:
            return "unknown"
```

## Fallback Mechanisms

### **REST API Fallback**
When WebSocket is unavailable, the system falls back to REST API polling:

```python
class BybitFallbackManager:
    """Manages fallback to REST API when WebSocket fails"""
    
    async def poll_orders(self, symbol: str = None) -> List[dict]:
        """Poll orders via REST API"""
        try:
            # Get open orders
            open_orders = await self.bybit_client.get_open_orders(symbol)
            
            # Get recent executions
            executions = await self.bybit_client.get_executions(symbol)
            
            # Process and emit events
            for order in open_orders:
                await self._process_order_via_rest(order)
            
            for execution in executions:
                await self._process_execution_via_rest(execution)
                
        except Exception as e:
            logger.error(f"REST API fallback failed: {e}")
    
    async def _process_order_via_rest(self, order: dict):
        """Process order data from REST API"""
        # Convert REST API response to WebSocket event format
        event_data = self._convert_rest_to_websocket_format(order)
        await self.event_processor.process_event(event_data)
```

## Monitoring and Health Checks

### **Health Check Endpoints**
```python
@app.get("/api/v1/websocket/bybit/health")
async def get_bybit_websocket_health():
    """Get Bybit WebSocket health status"""
    return {
        "connected": bybit_manager.is_connected,
        "last_message": bybit_manager.last_message_time,
        "heartbeat_status": bybit_manager.heartbeat_status,
        "subscription_status": bybit_manager.subscription_status,
        "error_count": bybit_manager.error_count,
        "uptime": bybit_manager.uptime
    }
```

### **Metrics Tracking**
```python
class BybitMetrics:
    """Track Bybit WebSocket metrics"""
    
    def __init__(self):
        self.metrics = {
            "messages_received": 0,
            "messages_processed": 0,
            "authentication_failures": 0,
            "connection_errors": 0,
            "processing_errors": 0,
            "last_message_time": None,
            "uptime_start": datetime.utcnow()
        }
    
    def increment(self, metric: str):
        """Increment metric counter"""
        if metric in self.metrics:
            self.metrics[metric] += 1
    
    def get_metrics(self) -> dict:
        """Get current metrics"""
        return self.metrics.copy()
```

## Configuration

### **Environment Variables**
```bash
# Bybit WebSocket Configuration
BYBIT_ENABLE_USER_DATA_STREAM=true
BYBIT_WEBSOCKET_URL=wss://stream.bybit.com/v5/private
BYBIT_WEBSOCKET_PUBLIC_URL=wss://stream.bybit.com/v5/public/spot
BYBIT_API_KEY=<api-key>
BYBIT_API_SECRET=<api-secret>

# Bybit WebSocket Settings
BYBIT_WEBSOCKET_HEARTBEAT_INTERVAL=20
BYBIT_WEBSOCKET_RECONNECT_DELAY=5
BYBIT_WEBSOCKET_MAX_RECONNECT_ATTEMPTS=10
BYBIT_WEBSOCKET_RATE_LIMIT=20

# Bybit Authentication Settings
BYBIT_AUTH_TIMESTAMP_TOLERANCE=5000
BYBIT_AUTH_RETRY_ATTEMPTS=3
```

### **Configuration File**
```yaml
exchanges:
  bybit:
    websocket:
      enabled: true
      private_url: wss://stream.bybit.com/v5/private
      public_url: wss://stream.bybit.com/v5/public/spot
      heartbeat_interval: 20
      reconnect_delay: 5
      max_reconnect_attempts: 10
      rate_limit: 20
    authentication:
      timestamp_tolerance: 5000
      retry_attempts: 3
    channels:
      - order
      - execution
      - position
      - wallet
```

## Testing Strategy

### **Unit Tests**
- Authentication signature generation
- Event processing logic
- Error handling and recovery
- Connection management

### **Integration Tests**
- WebSocket connection and authentication
- Event processing pipeline
- Database updates
- Dashboard integration

### **End-to-End Tests**
- Complete order lifecycle
- Fill detection and trade closure
- Error recovery scenarios
- Performance under load

## Deployment Considerations

### **Production Requirements**
- **High Availability**: Multiple WebSocket connections with failover
- **Monitoring**: Comprehensive health checks and alerting
- **Logging**: Detailed logging for debugging and audit trails
- **Security**: Secure API key management and encryption
- **Performance**: Optimized event processing and database operations

### **Scaling Considerations**
- **Connection Pooling**: Multiple WebSocket connections for high throughput
- **Event Queuing**: Message queues for handling event bursts
- **Database Optimization**: Efficient database operations for high-frequency updates
- **Caching**: Redis caching for frequently accessed data

## Troubleshooting

### **Common Issues**

#### **Authentication Failures**
- **Cause**: Invalid API keys, expired signatures, clock skew
- **Solution**: Verify API keys, check system clock, regenerate signatures

#### **Connection Drops**
- **Cause**: Network issues, heartbeat failures, rate limiting
- **Solution**: Implement automatic reconnection, adjust heartbeat interval

#### **Event Processing Errors**
- **Cause**: Invalid event format, processing logic errors
- **Solution**: Validate event format, add error handling, log detailed errors

#### **Performance Issues**
- **Cause**: High event volume, inefficient processing
- **Solution**: Optimize event processing, implement queuing, scale horizontally

### **Debugging Tools**
- **WebSocket Inspector**: Monitor WebSocket traffic
- **Event Logging**: Detailed event processing logs
- **Health Checks**: Real-time system health monitoring
- **Metrics Dashboard**: Performance and error metrics

## Conclusion

This Bybit WebSocket integration provides real-time order tracking, position updates, and trade management capabilities. The implementation includes comprehensive error handling, fallback mechanisms, and monitoring to ensure reliable operation in production environments.

The integration follows the same patterns as the successful Binance implementation while addressing Bybit-specific requirements such as timestamp-based authentication, channel subscriptions, and heartbeat mechanisms.

---

**Version**: 2.6.0  
**Last Updated**: 2025-08-28  
**Status**: Implementation Ready
