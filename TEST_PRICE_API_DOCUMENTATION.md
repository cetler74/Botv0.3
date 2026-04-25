# Test Price API Documentation

## Overview

The Test Price API provides developers with the ability to override real market prices for testing entry and exit strategies without affecting production trading flows. This service allows you to simulate various market conditions and test how your trading strategies respond to different price scenarios.

## Features

- **Price Override**: Override real market prices with test values
- **Multiple Override Types**: Fixed prices, percentage changes, or predefined scenarios
- **Time-based Expiration**: Automatic cleanup of expired overrides
- **Production Safety**: Completely isolated from production trading flows
- **Predefined Scenarios**: Common test scenarios like profit/loss triggers
- **Real-time Integration**: Seamlessly integrates with existing pricing flows

## Service Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   Test Price    │    │   Exchange       │    │   Trading       │
│   Service       │◄───┤   Service        │◄───┤   System        │
│   (Port 8008)   │    │   (Port 8003)    │    │                 │
└─────────────────┘    └──────────────────┘    └─────────────────┘
```

## API Endpoints

### Base URL
```
http://localhost:8008
```

### Health Check
```http
GET /health
```

**Response:**
```json
{
  "status": "healthy",
  "service": "test-price-service",
  "version": "1.0.0",
  "timestamp": "2025-01-27T14:00:00Z"
}
```

### Test Mode Management

#### Enable Test Mode
```http
POST /api/v1/test-mode/enable
```

**Response:**
```json
{
  "success": true,
  "message": "Test mode enabled"
}
```

#### Disable Test Mode
```http
POST /api/v1/test-mode/disable
```

**Response:**
```json
{
  "success": true,
  "message": "Test mode disabled and all overrides cleared"
}
```

#### Get Test Mode Status
```http
GET /api/v1/test-mode/status
```

**Response:**
```json
{
  "test_mode_status": "enabled",
  "active_overrides": 2
}
```

### Price Override Management

#### Set Price Override
```http
POST /api/v1/prices/override
```

**Request Body:**
```json
{
  "exchange": "binance",
  "pair": "BTC/USDC",
  "override_type": "percentage",
  "value": 0.02,
  "duration_minutes": 60,
  "description": "Test 2% profit scenario"
}
```

**Override Types:**
- `fixed`: Set a specific price value
- `percentage`: Percentage change from current price (e.g., 0.02 = +2%)
- `scenario`: Use predefined scenario (see scenarios below)

**Response:**
```json
{
  "success": true,
  "message": "Price override set successfully",
  "data": {
    "exchange": "binance",
    "pair": "BTC/USDC",
    "original_price": 50000.0,
    "override_price": 51000.0,
    "percentage_change": 2.0,
    "expires_at": "2025-01-27T15:00:00Z",
    "scenario": "Test 2% profit scenario"
  }
}
```

#### Get Price Override
```http
GET /api/v1/prices/override/{exchange}/{pair}
```

**Response:**
```json
{
  "found": true,
  "override": {
    "exchange": "binance",
    "pair": "BTC/USDC",
    "type": "percentage",
    "value": 0.02,
    "original_price": 50000.0,
    "created_at": "2025-01-27T14:00:00Z",
    "expires_at": "2025-01-27T15:00:00Z",
    "description": "Test 2% profit scenario"
  }
}
```

#### Clear Price Override
```http
DELETE /api/v1/prices/override/{exchange}/{pair}
```

**Response:**
```json
{
  "success": true,
  "message": "Price override cleared for binance/BTC/USDC"
}
```

#### Clear All Overrides
```http
DELETE /api/v1/prices/override
```

**Response:**
```json
{
  "success": true,
  "message": "Cleared all 3 price overrides"
}
```

#### Get Test Price
```http
GET /api/v1/prices/test/{exchange}/{pair}
```

**Response:**
```json
{
  "test_price": 51000.0,
  "is_override": true,
  "source": "test_override"
}
```

### Predefined Scenarios

#### Get Available Scenarios
```http
GET /api/v1/scenarios
```

**Response:**
```json
{
  "scenarios": {
    "profit_0_5_percent": {
      "description": "0.5% profit scenario",
      "percentage_change": 0.005
    },
    "profit_1_percent": {
      "description": "1% profit scenario",
      "percentage_change": 0.01
    },
    "profit_2_percent": {
      "description": "2% profit scenario",
      "percentage_change": 0.02
    },
    "profit_5_percent": {
      "description": "5% profit scenario",
      "percentage_change": 0.05
    },
    "loss_1_percent": {
      "description": "1% loss scenario",
      "percentage_change": -0.01
    },
    "loss_2_percent": {
      "description": "2% loss scenario",
      "percentage_change": -0.02
    },
    "loss_5_percent": {
      "description": "5% loss scenario",
      "percentage_change": -0.05
    },
    "trailing_trigger": {
      "description": "Trailing trigger activation (0.7% profit)",
      "percentage_change": 0.007
    },
    "stop_loss_trigger": {
      "description": "Stop loss trigger (-3% loss)",
      "percentage_change": -0.03
    }
  }
}
```

## Usage Examples

### Example 1: Test Trailing Trigger Activation

```bash
# Enable test mode
curl -X POST http://localhost:8008/api/v1/test-mode/enable

# Set 2% profit scenario for BTC/USDC
curl -X POST http://localhost:8008/api/v1/prices/override \
  -H "Content-Type: application/json" \
  -d '{
    "exchange": "binance",
    "pair": "BTC/USDC",
    "override_type": "scenario",
    "value": "profit_2_percent",
    "duration_minutes": 30,
    "description": "Test trailing trigger activation"
  }'

# Check if override is active
curl http://localhost:8008/api/v1/prices/test/binance/BTCUSDC
```

### Example 2: Test Stop Loss Trigger

```bash
# Set 3% loss scenario
curl -X POST http://localhost:8008/api/v1/prices/override \
  -H "Content-Type: application/json" \
  -d '{
    "exchange": "binance",
    "pair": "ETH/USDC",
    "override_type": "scenario",
    "value": "stop_loss_trigger",
    "duration_minutes": 15,
    "description": "Test stop loss trigger"
  }'
```

### Example 3: Fixed Price Override

```bash
# Set fixed price
curl -X POST http://localhost:8008/api/v1/prices/override \
  -H "Content-Type: application/json" \
  -d '{
    "exchange": "binance",
    "pair": "LINK/USDC",
    "override_type": "fixed",
    "value": 25.50,
    "duration_minutes": 60,
    "description": "Test fixed price scenario"
  }'
```

### Example 4: Percentage Change Override

```bash
# Set 1.5% profit
curl -X POST http://localhost:8008/api/v1/prices/override \
  -H "Content-Type: application/json" \
  -d '{
    "exchange": "binance",
    "pair": "BNB/USDC",
    "override_type": "percentage",
    "value": 0.015,
    "duration_minutes": 45,
    "description": "Test 1.5% profit scenario"
  }'
```

## Integration with Trading System

### How It Works

1. **Test Mode Enabled**: When test mode is enabled, the system checks for price overrides
2. **Price Override Check**: Before returning real market prices, the exchange service checks the test price service
3. **Override Applied**: If an override exists and hasn't expired, the test price is returned instead
4. **Trading System**: The trading system receives the test price and processes it normally
5. **Automatic Cleanup**: Expired overrides are automatically cleaned up

### Configuration

The test price service is configured in `config.yaml`:

```yaml
trading:
  test_mode:
    enabled: false
    test_price_service_url: "http://test-price-service:8008"
    default_override_duration_minutes: 60
    allow_production_override: false
    logging_enabled: true
```

### Environment Variables

```bash
# Test Price Service
TEST_PRICE_SERVICE_URL=http://test-price-service:8008
TEST_MODE_ENABLED=false

# Exchange Service Integration
TEST_MODE_ENABLED=true  # Enable test mode in exchange service
```

## Safety Features

### Production Protection

- **Isolated Service**: Test price service runs independently
- **Configurable**: Can be completely disabled via configuration
- **Time-based Expiration**: All overrides automatically expire
- **Clear Separation**: Test prices are clearly marked in logs and responses

### Monitoring

- **Health Checks**: Service health is monitored
- **Logging**: All test price activities are logged
- **Status Endpoints**: Real-time status and statistics available
- **Automatic Cleanup**: Expired overrides are automatically removed

## Best Practices

### Testing Workflow

1. **Enable Test Mode**: Always enable test mode before setting overrides
2. **Set Overrides**: Configure the specific price scenarios you want to test
3. **Monitor Results**: Watch the trading system respond to test prices
4. **Clean Up**: Clear overrides when testing is complete
5. **Disable Test Mode**: Disable test mode to return to production

### Safety Guidelines

- **Never enable test mode in production** without proper safeguards
- **Use short durations** for overrides to prevent accidental long-term effects
- **Monitor logs** to ensure test prices are being applied correctly
- **Clear overrides** when testing is complete
- **Test in isolated environments** when possible

## Troubleshooting

### Common Issues

#### Test Mode Not Working
```bash
# Check if test mode is enabled
curl http://localhost:8008/api/v1/test-mode/status

# Check service health
curl http://localhost:8008/health
```

#### Override Not Applied
```bash
# Check if override exists
curl http://localhost:8008/api/v1/prices/override/binance/BTCUSDC

# Check test price
curl http://localhost:8008/api/v1/prices/test/binance/BTCUSDC
```

#### Service Not Responding
```bash
# Check Docker container status
docker ps | grep test-price

# Check logs
docker logs trading-bot-test-price
```

### Debug Information

#### Get Service Status
```bash
curl http://localhost:8008/api/v1/status
```

**Response:**
```json
{
  "service": "test-price-service",
  "version": "1.0.0",
  "test_mode_status": "enabled",
  "active_overrides": 2,
  "overrides": [
    {
      "exchange": "binance",
      "pair": "BTC/USDC",
      "type": "percentage",
      "value": 0.02,
      "original_price": 50000.0,
      "created_at": "2025-01-27T14:00:00Z",
      "expires_at": "2025-01-27T15:00:00Z",
      "description": "Test 2% profit scenario"
    }
  ],
  "available_scenarios": ["profit_0_5_percent", "profit_1_percent", ...]
}
```

## API Reference

### Request Models

#### TestPriceRequest
```json
{
  "exchange": "string",           // Exchange name (required)
  "pair": "string",              // Trading pair (required)
  "override_type": "string",     // "fixed", "percentage", or "scenario" (required)
  "value": "number|string",      // Price value or scenario name (required)
  "duration_minutes": "number",  // Override duration in minutes (optional, default: 60)
  "description": "string"        // Description of test scenario (optional)
}
```

#### TestPriceResponse
```json
{
  "success": "boolean",          // Whether the operation was successful
  "message": "string",           // Human-readable message
  "data": "object"              // Additional data (optional)
}
```

### Response Models

#### TestPriceOverride
```json
{
  "exchange": "string",          // Exchange name
  "pair": "string",             // Trading pair
  "type": "string",             // Override type
  "value": "number|string",     // Override value
  "original_price": "number",   // Original market price
  "created_at": "string",       // ISO timestamp
  "expires_at": "string",       // ISO timestamp
  "description": "string"       // Description
}
```

## Support

For issues or questions about the Test Price API:

1. Check the service logs: `docker logs trading-bot-test-price`
2. Verify configuration in `config.yaml`
3. Test API endpoints directly
4. Check Docker container health

---

*Documentation generated for Test Price API v1.0.0*
