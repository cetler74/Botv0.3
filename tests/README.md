# Trading Bot Microservices - Testing Framework

This directory contains comprehensive tests for the trading bot microservices architecture.

## Test Structure

```
tests/
├── __init__.py                 # Test package initialization
├── conftest.py                 # Shared pytest fixtures and configuration
├── unit/                       # Unit tests for individual services
│   ├── test_config_service.py
│   ├── test_database_service.py
│   ├── test_exchange_service.py
│   ├── test_strategy_service.py
│   ├── test_orchestrator_service.py
│   └── test_web_dashboard_service.py
├── integration/                # Integration tests for service communication
│   ├── test_service_communication.py
│   ├── test_data_flow.py
│   └── test_error_handling.py
├── e2e/                        # End-to-end tests for complete workflows
│   ├── test_trading_workflows.py
│   ├── test_risk_management.py
│   └── test_performance_monitoring.py
├── performance/                # Performance and load tests
│   ├── test_load_performance.py
│   ├── test_stress_tests.py
│   └── test_benchmarks.py
├── run_tests.py                # Test runner script
└── README.md                   # This file
```

## Test Categories

### 1. Unit Tests (`tests/unit/`)
- **Purpose**: Test individual service components in isolation
- **Coverage**: API endpoints, business logic, data validation
- **Dependencies**: Mocked external services and databases
- **Execution**: Fast, reliable, focused

**Test Files:**
- `test_config_service.py` - Configuration management tests
- `test_database_service.py` - Database operations tests
- `test_exchange_service.py` - Exchange integration tests
- `test_strategy_service.py` - Strategy execution tests
- `test_orchestrator_service.py` - Trading orchestration tests
- `test_web_dashboard_service.py` - Web UI tests

### 2. Integration Tests (`tests/integration/`)
- **Purpose**: Test communication between services
- **Coverage**: Service-to-service APIs, data flow, error handling
- **Dependencies**: Mocked external APIs, real service communication
- **Execution**: Medium speed, tests service boundaries

**Test Files:**
- `test_service_communication.py` - Inter-service communication
- `test_data_flow.py` - Data flow between services
- `test_error_handling.py` - Error scenarios and recovery

### 3. End-to-End Tests (`tests/e2e/`)
- **Purpose**: Test complete trading workflows
- **Coverage**: Full trading cycles, risk management, portfolio operations
- **Dependencies**: All services running, test database
- **Execution**: Slower, comprehensive workflow testing

**Test Files:**
- `test_trading_workflows.py` - Complete trading cycles
- `test_risk_management.py` - Risk management workflows
- `test_performance_monitoring.py` - Performance monitoring

### 4. Performance Tests (`tests/performance/`)
- **Purpose**: Test system behavior under load
- **Coverage**: Concurrent users, database load, memory usage
- **Dependencies**: Performance monitoring tools
- **Execution**: Long-running, resource-intensive

**Test Files:**
- `test_load_performance.py` - Load testing scenarios
- `test_stress_tests.py` - Stress testing
- `test_benchmarks.py` - Performance benchmarks

## Running Tests

### Prerequisites

1. **Install test dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Setup test database:**
   ```bash
   createdb -U carloslarramba trading_bot_test
   psql -U carloslarramba -d trading_bot_test -f scripts/create_all_trading_bot_tables.sql
   ```

### Test Runner Script

Use the test runner script for easy test execution:

```bash
# Setup test environment
python tests/run_tests.py --setup

# Run specific test types
python tests/run_tests.py --unit
python tests/run_tests.py --integration
python tests/run_tests.py --e2e
python tests/run_tests.py --performance

# Run all tests
python tests/run_tests.py --all

# Generate test report
python tests/run_tests.py --report

# Cleanup test environment
python tests/run_tests.py --cleanup
```

### Direct Pytest Commands

```bash
# Run all tests
pytest

# Run specific test categories
pytest tests/unit/
pytest tests/integration/
pytest tests/e2e/
pytest tests/performance/

# Run with coverage
pytest --cov=services --cov-report=html

# Run specific test file
pytest tests/unit/test_config_service.py

# Run specific test function
pytest tests/unit/test_config_service.py::TestConfigServiceHealth::test_health_check

# Run with markers
pytest -m unit
pytest -m integration
pytest -m e2e
pytest -m performance
```

## Test Configuration

### Pytest Configuration (`pytest.ini`)

- **Test discovery**: Automatically finds test files
- **Async support**: Configured for async test execution
- **Coverage**: 80% minimum coverage requirement
- **Output**: Verbose output with color coding
- **Timeouts**: 300-second timeout for long-running tests

### Shared Fixtures (`conftest.py`)

Common test fixtures available to all tests:

- `test_config` - Test configuration
- `mock_exchange_data` - Mock exchange data
- `mock_strategy_signals` - Mock strategy signals
- `mock_trade_data` - Mock trade data
- `http_client` - Async HTTP client
- `temp_config_file` - Temporary configuration file
- `mock_database_connection` - Mock database connection
- `mock_ccxt_exchange` - Mock CCXT exchange
- `test_database` - Test database setup/teardown

## Test Data Management

### Test Database

- **Database**: `trading_bot_test`
- **User**: `carloslarramba`
- **Tables**: Test-specific tables with `test_` prefix
- **Cleanup**: Automatic cleanup after tests

### Mock Data

- **Exchange Data**: Realistic market data for testing
- **Strategy Signals**: Various signal types and confidence levels
- **Trade Data**: Complete trade lifecycle data
- **Portfolio Data**: Balance and performance metrics

## Performance Testing

### Load Test Scenarios

1. **Concurrent Users**: 100 users making 50 requests each
2. **Database Load**: 1000 concurrent database operations
3. **Market Data**: 10,000 data points processing
4. **Strategy Execution**: 10 strategies with 100 executions each
5. **Memory Usage**: Monitor memory consumption under load
6. **CPU Usage**: Monitor CPU utilization under load
7. **Network Throughput**: Test network performance
8. **Stress Test**: Extreme load conditions

### Performance Metrics

- **Response Time**: Average, median, P95, P99
- **Throughput**: Requests per second
- **Error Rate**: Percentage of failed requests
- **Resource Usage**: Memory, CPU, network
- **Success Rate**: Percentage of successful operations

## Continuous Integration

### GitHub Actions Integration

```yaml
# .github/workflows/test.yml
name: Tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - name: Setup Python
        uses: actions/setup-python@v2
        with:
          python-version: 3.9
      - name: Install dependencies
        run: pip install -r requirements.txt
      - name: Setup test database
        run: |
          sudo -u postgres createdb trading_bot_test
          psql -U postgres -d trading_bot_test -f scripts/create_all_trading_bot_tables.sql
      - name: Run tests
        run: python tests/run_tests.py --all
      - name: Upload coverage
        uses: codecov/codecov-action@v1
```

### Test Reports

- **Coverage Report**: HTML coverage report in `htmlcov/`
- **JUnit XML**: Test results in `test-results.xml`
- **Performance Report**: Performance metrics and benchmarks

## Best Practices

### Writing Tests

1. **Test Naming**: Use descriptive test names
2. **Arrange-Act-Assert**: Structure tests clearly
3. **Mocking**: Mock external dependencies
4. **Isolation**: Each test should be independent
5. **Coverage**: Aim for high test coverage
6. **Performance**: Keep tests fast and efficient

### Test Data

1. **Realistic Data**: Use realistic test data
2. **Edge Cases**: Test boundary conditions
3. **Error Scenarios**: Test error handling
4. **Cleanup**: Clean up test data after tests

### Maintenance

1. **Regular Updates**: Keep tests up to date with code changes
2. **Performance Monitoring**: Monitor test execution time
3. **Coverage Tracking**: Track test coverage trends
4. **Documentation**: Keep test documentation current

## Troubleshooting

### Common Issues

1. **Database Connection**: Ensure PostgreSQL is running
2. **Dependencies**: Install all test dependencies
3. **Permissions**: Check database user permissions
4. **Port Conflicts**: Ensure test ports are available

### Debug Mode

```bash
# Run tests with debug output
pytest -v -s --tb=long

# Run specific failing test
pytest tests/unit/test_config_service.py::TestConfigServiceHealth::test_health_check -v -s
```

### Test Environment

```bash
# Check test environment
python -c "import pytest; print(pytest.__version__)"
python -c "import httpx; print(httpx.__version__)"
python -c "import psutil; print(psutil.__version__)"
```

## Contributing

When adding new tests:

1. Follow the existing test structure
2. Use appropriate test categories
3. Add comprehensive test coverage
4. Update documentation
5. Ensure tests pass in CI

For questions or issues, please refer to the main project documentation or create an issue in the repository. 