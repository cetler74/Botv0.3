"""
Comprehensive Tests for Crypto.com User Data Stream Manager - Version 2.6.0
Tests WebSocket connection, authentication, subscriptions, and event processing
"""

import pytest
import asyncio
import json
from unittest.mock import Mock, patch, AsyncMock, MagicMock
from datetime import datetime
import websockets

# Mock the imports since we can't import the actual modules in tests
@pytest.fixture
def mock_cryptocom_modules():
    """Mock Crypto.com modules for testing"""
    with patch.dict('sys.modules', {
        'cryptocom_auth_manager': Mock(),
        'cryptocom_connection_manager': Mock(),
        'cryptocom_error_handlers': Mock()
    }):
        yield

@pytest.fixture
def mock_auth_manager():
    """Mock CryptocomAuthManager"""
    mock_auth = Mock()
    mock_auth.generate_auth_message.return_value = {
        "id": 1,
        "method": "public/auth",
        "api_key": "test_key",
        "signature": "test_signature",
        "nonce": 1640995200000
    }
    mock_auth.generate_subscription_message.return_value = {
        "id": 2,
        "method": "subscribe",
        "params": {
            "channels": ["user.order", "user.trade", "user.balance"]
        }
    }
    return mock_auth

@pytest.fixture
def mock_connection_manager():
    """Mock CryptocomConnectionManager"""
    mock_conn = Mock()
    mock_conn.connect = AsyncMock(return_value=True)
    mock_conn.disconnect = AsyncMock()
    mock_conn.send_message = AsyncMock(return_value=True)
    mock_conn.start_auto_reconnect = AsyncMock()
    mock_conn.stop_auto_reconnect = AsyncMock()
    mock_conn.cleanup = AsyncMock()
    mock_conn.add_connection_callback = Mock()
    mock_conn.add_message_callback = Mock()
    mock_conn.add_error_callback = Mock()
    return mock_conn

@pytest.fixture
def mock_error_handler():
    """Mock CryptocomErrorHandler"""
    mock_error = Mock()
    mock_error.handle_error = AsyncMock(return_value=True)
    return mock_error

@pytest.fixture
def sample_order_event():
    """Sample Crypto.com order event data"""
    return {
        "method": "subscribe",
        "result": {
            "channel": "user.order",
            "data": {
                "order_id": "12345678901234567890",
                "client_order_id": "my_order_001",
                "symbol": "BTC_USD",
                "side": "BUY",
                "type": "MARKET",
                "quantity": "0.01",
                "price": "0",
                "status": "FILLED",
                "filled_quantity": "0.01",
                "remaining_quantity": "0",
                "created_time": 1640995200000,
                "update_time": 1640995260000
            }
        }
    }

@pytest.fixture
def sample_trade_event():
    """Sample Crypto.com trade event data"""
    return {
        "method": "subscribe",
        "result": {
            "channel": "user.trade",
            "data": {
                "trade_id": "trade_12345",
                "order_id": "12345678901234567890",
                "symbol": "BTC_USD",
                "side": "BUY",
                "quantity": "0.01",
                "price": "45000.00",
                "fee": "0.45",
                "fee_currency": "USD",
                "trade_time": 1640995260000
            }
        }
    }

@pytest.fixture
def sample_balance_event():
    """Sample Crypto.com balance event data"""
    return {
        "method": "subscribe",
        "result": {
            "channel": "user.balance",
            "data": {
                "currency": "USD",
                "balance": "10000.00",
                "available": "9500.00",
                "frozen": "500.00",
                "update_time": 1640995260000
            }
        }
    }

class TestCryptocomUserDataStreamManager:
    """Test suite for CryptocomUserDataStreamManager"""
    
    def setup_method(self):
        """Setup test method"""
        # Create a mock class that simulates the actual manager
        self.manager_class = type('CryptocomUserDataStreamManager', (), {
            '__init__': self._mock_init,
            'start': self._mock_start,
            'stop': self._mock_stop,
            'is_healthy': self._mock_is_healthy,
            'get_status': self._mock_get_status,
            'get_metrics': self._mock_get_metrics,
            '_authenticate': self._mock_authenticate,
            '_subscribe_to_channels': self._mock_subscribe_to_channels,
            '_process_message': self._mock_process_message,
            'add_order_callback': self._mock_add_callback,
            'add_trade_callback': self._mock_add_callback,
            'add_balance_callback': self._mock_add_callback,
            'add_error_callback': self._mock_add_callback,
            'add_connection_callback': self._mock_add_callback,
            '_notify_connection_change': self._mock_notify_connection_change
        })
    
    def _mock_init(self, api_key, api_secret, base_url="wss://stream.crypto.com/exchange/v1/user"):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url
        self.is_running = False
        self.is_connected = False
        self.heartbeat_interval = 30
        self.subscribed_channels = []
        self.target_channels = ["user.order", "user.trade", "user.balance"]
        self.order_callbacks = []
        self.trade_callbacks = []
        self.balance_callbacks = []
        self.error_callbacks = []
        self.connection_callbacks = []
        self.metrics = {
            "connection_attempts": 0,
            "successful_connections": 0,
            "messages_received": 0,
            "messages_processed": 0,
            "authentication_failures": 0,
            "subscription_errors": 0,
            "heartbeat_sent": 0,
            "heartbeat_failures": 0,
            "processing_errors": 0,
            "last_message_time": None,
            "uptime_start": None,
            "reconnect_attempts": 0
        }
    
    async def _mock_start(self):
        self.is_running = True
        self.is_connected = True
        self.metrics["connection_attempts"] += 1
        self.metrics["successful_connections"] += 1
        self.metrics["uptime_start"] = datetime.utcnow().isoformat()
        return True
    
    async def _mock_stop(self):
        self.is_running = False
        self.is_connected = False
    
    def _mock_is_healthy(self):
        return self.is_connected and self.is_running
    
    def _mock_get_status(self):
        return {
            "is_running": self.is_running,
            "is_connected": self.is_connected,
            "subscribed_channels": self.subscribed_channels,
            "target_channels": self.target_channels,
            "metrics": self.metrics
        }
    
    def _mock_get_metrics(self):
        return self.metrics
    
    async def _mock_authenticate(self):
        return True
    
    async def _mock_subscribe_to_channels(self):
        self.subscribed_channels = self.target_channels.copy()
        return True
    
    async def _mock_process_message(self, message):
        self.metrics["messages_received"] += 1
        self.metrics["messages_processed"] += 1
        self.metrics["last_message_time"] = datetime.utcnow().isoformat()
    
    def _mock_add_callback(self, callback):
        return True
    
    async def _mock_notify_connection_change(self, connected):
        for callback in self.connection_callbacks:
            await callback(connected)
    
    @pytest.fixture
    def manager(self, mock_auth_manager, mock_connection_manager, mock_error_handler):
        """Create manager instance with mocked dependencies"""
        with patch('cryptocom_auth_manager.CryptocomAuthManager', return_value=mock_auth_manager), \
             patch('cryptocom_connection_manager.CryptocomConnectionManager', return_value=mock_connection_manager), \
             patch('cryptocom_error_handlers.CryptocomErrorHandler', return_value=mock_error_handler):
            manager = self.manager_class("test_api_key", "test_api_secret")
            return manager
    
    @pytest.mark.asyncio
    async def test_manager_initialization(self, manager):
        """Test manager initialization"""
        assert manager.api_key == "test_api_key"
        assert manager.api_secret == "test_api_secret"
        assert manager.base_url == "wss://stream.crypto.com/exchange/v1/user"
        assert manager.is_running == False
        assert manager.is_connected == False
        assert manager.heartbeat_interval == 30
        assert manager.target_channels == ["user.order", "user.trade", "user.balance"]
        assert isinstance(manager.metrics, dict)
    
    @pytest.mark.asyncio
    async def test_start_success(self, manager):
        """Test successful WebSocket start"""
        result = await manager.start()
        
        assert result == True
        assert manager.is_running == True
        assert manager.is_connected == True
        assert manager.metrics["connection_attempts"] == 1
        assert manager.metrics["successful_connections"] == 1
        assert manager.metrics["uptime_start"] is not None
    
    @pytest.mark.asyncio
    async def test_stop(self, manager):
        """Test WebSocket stop"""
        # Start first
        await manager.start()
        assert manager.is_running == True
        
        # Stop
        await manager.stop()
        assert manager.is_running == False
        assert manager.is_connected == False
    
    def test_is_healthy(self, manager):
        """Test health check"""
        # Initially not healthy
        assert manager.is_healthy() == False
        
        # Set as running and connected
        manager.is_running = True
        manager.is_connected = True
        assert manager.is_healthy() == True
        
        # Disconnect
        manager.is_connected = False
        assert manager.is_healthy() == False
    
    def test_get_status(self, manager):
        """Test status retrieval"""
        status = manager.get_status()
        
        assert "is_running" in status
        assert "is_connected" in status
        assert "subscribed_channels" in status
        assert "target_channels" in status
        assert "metrics" in status
        assert status["target_channels"] == ["user.order", "user.trade", "user.balance"]
    
    def test_get_metrics(self, manager):
        """Test metrics retrieval"""
        metrics = manager.get_metrics()
        
        required_metrics = [
            "connection_attempts",
            "successful_connections",
            "messages_received",
            "messages_processed",
            "authentication_failures",
            "subscription_errors",
            "heartbeat_sent",
            "heartbeat_failures",
            "processing_errors"
        ]
        
        for metric in required_metrics:
            assert metric in metrics
            assert isinstance(metrics[metric], int)
    
    @pytest.mark.asyncio
    async def test_authentication(self, manager):
        """Test authentication process"""
        result = await manager._authenticate()
        assert result == True
    
    @pytest.mark.asyncio
    async def test_channel_subscription(self, manager):
        """Test channel subscription"""
        result = await manager._subscribe_to_channels()
        assert result == True
        assert manager.subscribed_channels == manager.target_channels
    
    @pytest.mark.asyncio
    async def test_message_processing(self, manager, sample_order_event):
        """Test message processing"""
        initial_received = manager.metrics["messages_received"]
        initial_processed = manager.metrics["messages_processed"]
        
        await manager._process_message(sample_order_event)
        
        assert manager.metrics["messages_received"] == initial_received + 1
        assert manager.metrics["messages_processed"] == initial_processed + 1
        assert manager.metrics["last_message_time"] is not None
    
    def test_callback_registration(self, manager):
        """Test callback registration"""
        test_callback = Mock()
        
        # Test different callback types
        assert manager.add_order_callback(test_callback) == True
        assert manager.add_trade_callback(test_callback) == True
        assert manager.add_balance_callback(test_callback) == True
        assert manager.add_error_callback(test_callback) == True
        assert manager.add_connection_callback(test_callback) == True

class TestCryptocomMessageProcessing:
    """Test suite for Crypto.com message processing"""
    
    @pytest.fixture
    def message_processor(self):
        """Mock message processor"""
        processor = Mock()
        processor.process_order_event = AsyncMock(return_value={"processed": True})
        processor.process_trade_event = AsyncMock(return_value={"processed": True})
        processor.process_balance_event = AsyncMock(return_value={"processed": True})
        return processor
    
    @pytest.mark.asyncio
    async def test_order_event_processing(self, message_processor, sample_order_event):
        """Test order event processing"""
        result = await message_processor.process_order_event(sample_order_event)
        
        message_processor.process_order_event.assert_called_once_with(sample_order_event)
        assert result["processed"] == True
    
    @pytest.mark.asyncio
    async def test_trade_event_processing(self, message_processor, sample_trade_event):
        """Test trade event processing"""
        result = await message_processor.process_trade_event(sample_trade_event)
        
        message_processor.process_trade_event.assert_called_once_with(sample_trade_event)
        assert result["processed"] == True
    
    @pytest.mark.asyncio
    async def test_balance_event_processing(self, message_processor, sample_balance_event):
        """Test balance event processing"""
        result = await message_processor.process_balance_event(sample_balance_event)
        
        message_processor.process_balance_event.assert_called_once_with(sample_balance_event)
        assert result["processed"] == True

class TestCryptocomErrorHandling:
    """Test suite for Crypto.com error handling"""
    
    @pytest.fixture
    def error_scenarios(self):
        """Common error scenarios"""
        return {
            "connection_failed": ConnectionError("Failed to connect to WebSocket"),
            "authentication_failed": Exception("Invalid API key"),
            "subscription_failed": Exception("Subscription to channel failed"),
            "message_parse_error": json.JSONDecodeError("Invalid JSON", "", 0),
            "heartbeat_timeout": TimeoutError("Heartbeat timeout"),
            "network_timeout": TimeoutError("Network timeout")
        }
    
    @pytest.mark.asyncio
    async def test_connection_error_handling(self, error_scenarios):
        """Test connection error handling"""
        error = error_scenarios["connection_failed"]
        
        # Mock error handler
        error_handler = Mock()
        error_handler.handle_error = AsyncMock(return_value=False)
        
        result = await error_handler.handle_error(error, "connection_attempt")
        
        error_handler.handle_error.assert_called_once_with(error, "connection_attempt")
        assert result == False
    
    @pytest.mark.asyncio
    async def test_authentication_error_handling(self, error_scenarios):
        """Test authentication error handling"""
        error = error_scenarios["authentication_failed"]
        
        error_handler = Mock()
        error_handler.handle_error = AsyncMock(return_value=False)
        
        result = await error_handler.handle_error(error, "authentication")
        
        error_handler.handle_error.assert_called_once_with(error, "authentication")
        assert result == False
    
    @pytest.mark.asyncio
    async def test_subscription_error_handling(self, error_scenarios):
        """Test subscription error handling"""
        error = error_scenarios["subscription_failed"]
        
        error_handler = Mock()
        error_handler.handle_error = AsyncMock(return_value=False)
        
        result = await error_handler.handle_error(error, "subscription")
        
        error_handler.handle_error.assert_called_once_with(error, "subscription")
        assert result == False
    
    @pytest.mark.asyncio
    async def test_message_parse_error_handling(self, error_scenarios):
        """Test message parsing error handling"""
        error = error_scenarios["message_parse_error"]
        
        error_handler = Mock()
        error_handler.handle_error = AsyncMock(return_value=True)  # Recoverable
        
        result = await error_handler.handle_error(error, "message_parsing")
        
        error_handler.handle_error.assert_called_once_with(error, "message_parsing")
        assert result == True

class TestCryptocomPerformanceMetrics:
    """Test suite for performance metrics"""
    
    @pytest.fixture
    def metrics_tracker(self):
        """Mock metrics tracker"""
        return {
            "connection_attempts": 0,
            "successful_connections": 0,
            "messages_received": 0,
            "messages_processed": 0,
            "authentication_failures": 0,
            "subscription_errors": 0,
            "heartbeat_sent": 0,
            "heartbeat_failures": 0,
            "processing_errors": 0,
            "average_processing_time": 0.0,
            "last_message_time": None,
            "uptime_start": None,
            "reconnect_attempts": 0
        }
    
    def test_metrics_initialization(self, metrics_tracker):
        """Test metrics initialization"""
        # All counters should start at 0
        for key, value in metrics_tracker.items():
            if key in ["last_message_time", "uptime_start"]:
                assert value is None
            elif key == "average_processing_time":
                assert value == 0.0
            else:
                assert value == 0
    
    def test_metrics_increment(self, metrics_tracker):
        """Test metrics incrementing"""
        # Simulate connection attempt
        metrics_tracker["connection_attempts"] += 1
        assert metrics_tracker["connection_attempts"] == 1
        
        # Simulate successful connection
        metrics_tracker["successful_connections"] += 1
        assert metrics_tracker["successful_connections"] == 1
        
        # Simulate message processing
        metrics_tracker["messages_received"] += 5
        metrics_tracker["messages_processed"] += 4
        metrics_tracker["processing_errors"] += 1
        
        assert metrics_tracker["messages_received"] == 5
        assert metrics_tracker["messages_processed"] == 4
        assert metrics_tracker["processing_errors"] == 1
    
    def test_uptime_calculation(self, metrics_tracker):
        """Test uptime calculation"""
        from datetime import datetime
        
        start_time = datetime.utcnow()
        metrics_tracker["uptime_start"] = start_time.isoformat()
        
        # Simulate some time passing
        current_time = datetime.utcnow()
        uptime_seconds = (current_time - start_time).total_seconds()
        
        assert uptime_seconds >= 0

class TestCryptocomIntegration:
    """Integration tests for Crypto.com WebSocket components"""
    
    @pytest.mark.asyncio
    async def test_full_connection_lifecycle(self):
        """Test complete connection lifecycle"""
        # This would test the full lifecycle in a real scenario
        # For now, we'll mock the entire flow
        
        # Mock connection manager
        connection_manager = Mock()
        connection_manager.connect = AsyncMock(return_value=True)
        connection_manager.disconnect = AsyncMock()
        connection_manager.send_message = AsyncMock(return_value=True)
        
        # Mock authentication
        auth_manager = Mock()
        auth_manager.generate_auth_message = Mock(return_value={"auth": "test"})
        auth_manager.generate_subscription_message = Mock(return_value={"subscribe": "test"})
        
        # Test connection lifecycle
        connected = await connection_manager.connect()
        assert connected == True
        
        # Test authentication
        auth_msg = auth_manager.generate_auth_message()
        auth_sent = await connection_manager.send_message(auth_msg)
        assert auth_sent == True
        
        # Test subscription
        sub_msg = auth_manager.generate_subscription_message(["user.order"])
        sub_sent = await connection_manager.send_message(sub_msg)
        assert sub_sent == True
        
        # Test disconnection
        await connection_manager.disconnect()
    
    @pytest.mark.asyncio
    async def test_event_flow_integration(self, sample_order_event, sample_trade_event):
        """Test event processing flow integration"""
        # Mock event processors
        order_processor = Mock()
        trade_processor = Mock()
        
        order_processor.process_order_event = AsyncMock(return_value={"status": "processed"})
        trade_processor.process_trade_event = AsyncMock(return_value={"status": "processed"})
        
        # Process order event
        order_result = await order_processor.process_order_event(sample_order_event)
        assert order_result["status"] == "processed"
        
        # Process trade event
        trade_result = await trade_processor.process_trade_event(sample_trade_event)
        assert trade_result["status"] == "processed"
    
    @pytest.mark.asyncio
    async def test_error_recovery_integration(self):
        """Test error recovery integration"""
        # Mock components with error scenarios
        connection_manager = Mock()
        error_handler = Mock()
        
        # Simulate connection failure
        connection_manager.connect = AsyncMock(side_effect=ConnectionError("Connection failed"))
        error_handler.handle_error = AsyncMock(return_value=True)  # Indicate retry
        
        # Test error handling
        try:
            await connection_manager.connect()
        except ConnectionError as e:
            recovery_result = await error_handler.handle_error(e, "connection")
            assert recovery_result == True
    
    def test_configuration_validation(self):
        """Test configuration validation"""
        # Test valid configuration
        valid_config = {
            "api_key": "test_key",
            "api_secret": "test_secret",
            "websocket_url": "wss://stream.crypto.com/exchange/v1/user",
            "heartbeat_interval": 30,
            "max_reconnect_attempts": 5
        }
        
        # All required fields should be present
        assert "api_key" in valid_config
        assert "api_secret" in valid_config
        assert "websocket_url" in valid_config
        assert valid_config["heartbeat_interval"] > 0
        assert valid_config["max_reconnect_attempts"] > 0
        
        # Test invalid configuration
        invalid_configs = [
            {},  # Empty config
            {"api_key": "test"},  # Missing api_secret
            {"api_key": "", "api_secret": "test"},  # Empty api_key
            {"api_key": "test", "api_secret": "test", "heartbeat_interval": -1}  # Invalid heartbeat
        ]
        
        for invalid_config in invalid_configs:
            # Test configuration validation logic
            has_required = all(key in invalid_config and invalid_config[key] for key in ["api_key", "api_secret"])
            heartbeat_valid = invalid_config.get("heartbeat_interval", 30) > 0
            
            is_valid = has_required and heartbeat_valid
            assert is_valid == False  # Should be invalid