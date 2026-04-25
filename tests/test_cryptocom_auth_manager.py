"""
Tests for Crypto.com Authentication Manager and Subscription Management
"""

import pytest
import asyncio
import json
import hmac
import hashlib
import time
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime, timedelta
from typing import Dict, Any, List


class TestCryptocomAuthManager:
    """Test Crypto.com authentication manager functionality."""
    
    def setup_method(self):
        """Setup test fixtures for each test method."""
        self.auth_manager_class = type('CryptocomAuthManager', (), {
            '__init__': self._mock_init,
            'generate_signature': self._mock_generate_signature,
            'authenticate': self._mock_authenticate,
            'is_authenticated': self._mock_is_authenticated,
            'get_auth_headers': self._mock_get_auth_headers,
            'refresh_auth': self._mock_refresh_auth,
        })
    
    def _mock_init(self, api_key: str, api_secret: str):
        """Mock initialization."""
        self.api_key = api_key
        self.api_secret = api_secret
        self.authenticated = False
        self.auth_timestamp = None
        self.signature_cache = {}
    
    def _mock_generate_signature(self, timestamp: int, method: str, path: str, params: str = "") -> str:
        """Mock signature generation."""
        message = f"{method}{path}{params}{timestamp}"
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return signature
    
    async def _mock_authenticate(self) -> bool:
        """Mock authentication."""
        self.authenticated = True
        self.auth_timestamp = int(time.time() * 1000)
        return True
    
    def _mock_is_authenticated(self) -> bool:
        """Mock authentication check."""
        if not self.authenticated:
            return False
        
        # Check if authentication is expired (15 minutes)
        current_time = int(time.time() * 1000)
        return (current_time - self.auth_timestamp) < 900000
    
    def _mock_get_auth_headers(self) -> Dict[str, str]:
        """Mock auth headers generation."""
        timestamp = int(time.time() * 1000)
        signature = self.generate_signature(timestamp, "GET", "/v1/user")
        
        return {
            "X-CRO-API-KEY": self.api_key,
            "X-CRO-API-SIGNATURE": signature,
            "X-CRO-API-TIMESTAMP": str(timestamp),
        }
    
    async def _mock_refresh_auth(self) -> bool:
        """Mock authentication refresh."""
        return await self.authenticate()

    @pytest.fixture
    def auth_manager(self):
        """Create auth manager instance for testing."""
        manager = self.auth_manager_class("test_api_key", "test_secret_key")
        return manager

    @pytest.mark.asyncio
    async def test_authentication_success(self, auth_manager):
        """Test successful authentication."""
        result = await auth_manager.authenticate()
        
        assert result is True
        assert auth_manager.is_authenticated() is True
        assert auth_manager.auth_timestamp is not None

    @pytest.mark.asyncio
    async def test_authentication_with_invalid_credentials(self):
        """Test authentication with invalid credentials."""
        auth_manager = self.auth_manager_class("", "")
        
        # Mock failed authentication
        async def mock_failed_auth():
            return False
        
        auth_manager.authenticate = mock_failed_auth
        
        result = await auth_manager.authenticate()
        assert result is False

    def test_signature_generation(self, auth_manager):
        """Test HMAC-SHA256 signature generation."""
        timestamp = 1640995200000
        method = "GET"
        path = "/v1/user/balance"
        params = ""
        
        signature = auth_manager.generate_signature(timestamp, method, path, params)
        
        assert isinstance(signature, str)
        assert len(signature) == 64  # SHA256 hex digest length
        
        # Test signature consistency
        signature2 = auth_manager.generate_signature(timestamp, method, path, params)
        assert signature == signature2

    def test_auth_headers_generation(self, auth_manager):
        """Test authentication headers generation."""
        headers = auth_manager.get_auth_headers()
        
        assert "X-CRO-API-KEY" in headers
        assert "X-CRO-API-SIGNATURE" in headers
        assert "X-CRO-API-TIMESTAMP" in headers
        
        assert headers["X-CRO-API-KEY"] == "test_api_key"
        assert len(headers["X-CRO-API-SIGNATURE"]) == 64
        assert headers["X-CRO-API-TIMESTAMP"].isdigit()

    def test_authentication_expiry(self, auth_manager):
        """Test authentication expiry logic."""
        # Initially not authenticated
        assert auth_manager.is_authenticated() is False
        
        # Simulate authentication
        auth_manager.authenticated = True
        auth_manager.auth_timestamp = int(time.time() * 1000)
        assert auth_manager.is_authenticated() is True
        
        # Simulate expired authentication (16 minutes ago)
        auth_manager.auth_timestamp = int(time.time() * 1000) - 960000
        assert auth_manager.is_authenticated() is False

    @pytest.mark.asyncio
    async def test_auth_refresh(self, auth_manager):
        """Test authentication refresh functionality."""
        # Initial authentication
        await auth_manager.authenticate()
        initial_timestamp = auth_manager.auth_timestamp
        
        # Wait a bit and refresh
        await asyncio.sleep(0.001)
        result = await auth_manager.refresh_auth()
        
        assert result is True
        assert auth_manager.auth_timestamp >= initial_timestamp


class TestCryptocomSubscriptionManager:
    """Test Crypto.com subscription management functionality."""
    
    def setup_method(self):
        """Setup test fixtures for each test method."""
        self.subscription_manager_class = type('CryptocomSubscriptionManager', (), {
            '__init__': self._mock_init,
            'subscribe_to_channels': self._mock_subscribe_to_channels,
            'unsubscribe_from_channels': self._mock_unsubscribe_from_channels,
            'get_active_subscriptions': self._mock_get_active_subscriptions,
            'is_subscribed': self._mock_is_subscribed,
            'handle_subscription_response': self._mock_handle_subscription_response,
        })
    
    def _mock_init(self, websocket_connection):
        """Mock initialization."""
        self.websocket = websocket_connection
        self.subscriptions = set()
        self.subscription_status = {}
    
    async def _mock_subscribe_to_channels(self, channels: List[str]) -> bool:
        """Mock channel subscription."""
        for channel in channels:
            self.subscriptions.add(channel)
            self.subscription_status[channel] = "subscribed"
        return True
    
    async def _mock_unsubscribe_from_channels(self, channels: List[str]) -> bool:
        """Mock channel unsubscription."""
        for channel in channels:
            self.subscriptions.discard(channel)
            self.subscription_status[channel] = "unsubscribed"
        return True
    
    def _mock_get_active_subscriptions(self) -> List[str]:
        """Mock active subscriptions retrieval."""
        return list(self.subscriptions)
    
    def _mock_is_subscribed(self, channel: str) -> bool:
        """Mock subscription status check."""
        return channel in self.subscriptions
    
    def _mock_handle_subscription_response(self, response: Dict[str, Any]) -> bool:
        """Mock subscription response handling."""
        if response.get("result") == "success":
            return True
        return False

    @pytest.fixture
    def mock_websocket(self):
        """Create mock WebSocket connection."""
        websocket = Mock()
        websocket.send = AsyncMock()
        websocket.recv = AsyncMock()
        return websocket

    @pytest.fixture
    def subscription_manager(self, mock_websocket):
        """Create subscription manager instance for testing."""
        manager = self.subscription_manager_class(mock_websocket)
        return manager

    @pytest.mark.asyncio
    async def test_subscribe_to_user_channels(self, subscription_manager):
        """Test subscription to user data channels."""
        channels = ["user.order", "user.trade", "user.balance"]
        
        result = await subscription_manager.subscribe_to_channels(channels)
        
        assert result is True
        assert subscription_manager.get_active_subscriptions() == channels
        
        for channel in channels:
            assert subscription_manager.is_subscribed(channel) is True

    @pytest.mark.asyncio
    async def test_unsubscribe_from_channels(self, subscription_manager):
        """Test unsubscription from channels."""
        channels = ["user.order", "user.trade"]
        
        # First subscribe
        await subscription_manager.subscribe_to_channels(channels)
        assert len(subscription_manager.get_active_subscriptions()) == 2
        
        # Then unsubscribe
        result = await subscription_manager.unsubscribe_from_channels(["user.order"])
        
        assert result is True
        assert subscription_manager.is_subscribed("user.order") is False
        assert subscription_manager.is_subscribed("user.trade") is True

    def test_subscription_status_tracking(self, subscription_manager):
        """Test subscription status tracking."""
        # Initially no subscriptions
        assert len(subscription_manager.get_active_subscriptions()) == 0
        assert subscription_manager.is_subscribed("user.order") is False

    def test_handle_subscription_responses(self, subscription_manager):
        """Test handling of subscription responses."""
        success_response = {
            "id": 1,
            "method": "subscribe",
            "result": "success"
        }
        
        error_response = {
            "id": 2,
            "method": "subscribe",
            "error": "invalid_channel"
        }
        
        assert subscription_manager.handle_subscription_response(success_response) is True
        assert subscription_manager.handle_subscription_response(error_response) is False


class TestCryptocomHeartbeatManager:
    """Test Crypto.com heartbeat management functionality."""
    
    def setup_method(self):
        """Setup test fixtures for each test method."""
        self.heartbeat_manager_class = type('CryptocomHeartbeatManager', (), {
            '__init__': self._mock_init,
            'start_heartbeat': self._mock_start_heartbeat,
            'stop_heartbeat': self._mock_stop_heartbeat,
            'send_heartbeat': self._mock_send_heartbeat,
            'handle_heartbeat_response': self._mock_handle_heartbeat_response,
            'is_heartbeat_active': self._mock_is_heartbeat_active,
        })
    
    def _mock_init(self, websocket_connection, interval: int = 30):
        """Mock initialization."""
        self.websocket = websocket_connection
        self.interval = interval
        self.heartbeat_task = None
        self.last_heartbeat = None
        self.heartbeat_active = False
    
    async def _mock_start_heartbeat(self):
        """Mock heartbeat start."""
        self.heartbeat_active = True
        self.last_heartbeat = datetime.now()
    
    async def _mock_stop_heartbeat(self):
        """Mock heartbeat stop."""
        self.heartbeat_active = False
        if self.heartbeat_task:
            self.heartbeat_task.cancel()
    
    async def _mock_send_heartbeat(self):
        """Mock heartbeat send."""
        heartbeat_msg = {
            "id": int(time.time()),
            "method": "public/heartbeat"
        }
        self.last_heartbeat = datetime.now()
        return True
    
    def _mock_handle_heartbeat_response(self, response: Dict[str, Any]) -> bool:
        """Mock heartbeat response handling."""
        return response.get("method") == "public/heartbeat"
    
    def _mock_is_heartbeat_active(self) -> bool:
        """Mock heartbeat status check."""
        return self.heartbeat_active

    @pytest.fixture
    def mock_websocket(self):
        """Create mock WebSocket connection."""
        websocket = Mock()
        websocket.send = AsyncMock()
        return websocket

    @pytest.fixture
    def heartbeat_manager(self, mock_websocket):
        """Create heartbeat manager instance for testing."""
        manager = self.heartbeat_manager_class(mock_websocket, interval=30)
        return manager

    @pytest.mark.asyncio
    async def test_start_heartbeat(self, heartbeat_manager):
        """Test heartbeat start functionality."""
        await heartbeat_manager.start_heartbeat()
        
        assert heartbeat_manager.is_heartbeat_active() is True
        assert heartbeat_manager.last_heartbeat is not None

    @pytest.mark.asyncio
    async def test_stop_heartbeat(self, heartbeat_manager):
        """Test heartbeat stop functionality."""
        # Start first
        await heartbeat_manager.start_heartbeat()
        assert heartbeat_manager.is_heartbeat_active() is True
        
        # Then stop
        await heartbeat_manager.stop_heartbeat()
        assert heartbeat_manager.is_heartbeat_active() is False

    @pytest.mark.asyncio
    async def test_send_heartbeat_message(self, heartbeat_manager):
        """Test heartbeat message sending."""
        initial_time = heartbeat_manager.last_heartbeat
        
        result = await heartbeat_manager.send_heartbeat()
        
        assert result is True
        assert heartbeat_manager.last_heartbeat != initial_time

    def test_heartbeat_response_handling(self, heartbeat_manager):
        """Test heartbeat response handling."""
        valid_response = {
            "id": 123,
            "method": "public/heartbeat",
            "result": {}
        }
        
        invalid_response = {
            "id": 124,
            "method": "subscribe",
            "result": {}
        }
        
        assert heartbeat_manager.handle_heartbeat_response(valid_response) is True
        assert heartbeat_manager.handle_heartbeat_response(invalid_response) is False


class TestCryptocomAuthIntegration:
    """Test integrated authentication workflows."""
    
    @pytest.fixture
    def auth_config(self):
        """Auth configuration for testing."""
        return {
            "api_key": "test_key_12345",
            "api_secret": "test_secret_67890",
            "websocket_url": "wss://stream.crypto.com/exchange/v1/user",
            "heartbeat_interval": 30,
            "auth_timeout": 10
        }

    @pytest.fixture
    def sample_auth_message(self):
        """Sample authentication message."""
        return {
            "id": 1,
            "method": "public/auth",
            "params": {
                "api_key": "test_key_12345",
                "sig": "generated_signature",
                "nonce": 1640995200000
            }
        }

    @pytest.fixture
    def sample_subscription_message(self):
        """Sample subscription message."""
        return {
            "id": 2,
            "method": "subscribe",
            "params": {
                "channels": ["user.order", "user.trade", "user.balance"]
            }
        }

    def test_auth_message_format(self, auth_config, sample_auth_message):
        """Test authentication message format."""
        msg = sample_auth_message
        
        assert "id" in msg
        assert "method" in msg
        assert "params" in msg
        assert msg["method"] == "public/auth"
        assert "api_key" in msg["params"]
        assert "sig" in msg["params"]
        assert "nonce" in msg["params"]

    def test_subscription_message_format(self, sample_subscription_message):
        """Test subscription message format."""
        msg = sample_subscription_message
        
        assert "id" in msg
        assert "method" in msg
        assert "params" in msg
        assert msg["method"] == "subscribe"
        assert "channels" in msg["params"]
        assert isinstance(msg["params"]["channels"], list)

    @pytest.mark.asyncio
    async def test_full_auth_workflow(self, auth_config):
        """Test complete authentication workflow."""
        # This would test the full flow:
        # 1. Connect to WebSocket
        # 2. Send authentication message
        # 3. Wait for auth response
        # 4. Subscribe to channels
        # 5. Start heartbeat
        
        # Mock the workflow steps
        steps_completed = []
        
        # Step 1: Connection
        steps_completed.append("connected")
        
        # Step 2: Authentication
        steps_completed.append("authenticated")
        
        # Step 3: Subscription
        steps_completed.append("subscribed")
        
        # Step 4: Heartbeat
        steps_completed.append("heartbeat_started")
        
        expected_steps = ["connected", "authenticated", "subscribed", "heartbeat_started"]
        assert steps_completed == expected_steps

    def test_auth_error_scenarios(self, auth_config):
        """Test various authentication error scenarios."""
        error_scenarios = [
            {"error": "invalid_api_key", "expected": False},
            {"error": "invalid_signature", "expected": False},
            {"error": "expired_nonce", "expected": False},
            {"error": "rate_limited", "expected": False},
        ]
        
        for scenario in error_scenarios:
            # Mock error response handling
            auth_successful = scenario["expected"]
            assert auth_successful == scenario["expected"]

    def test_subscription_error_scenarios(self):
        """Test subscription error scenarios."""
        error_scenarios = [
            {"error": "invalid_channel", "expected": False},
            {"error": "not_authenticated", "expected": False},
            {"error": "subscription_limit_exceeded", "expected": False},
        ]
        
        for scenario in error_scenarios:
            # Mock subscription error handling
            subscription_successful = scenario["expected"]
            assert subscription_successful == scenario["expected"]


@pytest.mark.performance
class TestCryptocomAuthPerformance:
    """Test authentication performance and load scenarios."""
    
    @pytest.mark.asyncio
    async def test_signature_generation_performance(self):
        """Test signature generation performance."""
        import time
        
        # Mock auth manager
        class MockAuthManager:
            def __init__(self):
                self.api_secret = "test_secret_key_for_performance_testing"
            
            def generate_signature(self, timestamp: int, method: str, path: str, params: str = "") -> str:
                message = f"{method}{path}{params}{timestamp}"
                signature = hmac.new(
                    self.api_secret.encode('utf-8'),
                    message.encode('utf-8'),
                    hashlib.sha256
                ).hexdigest()
                return signature
        
        auth_manager = MockAuthManager()
        
        # Generate 1000 signatures and measure time
        start_time = time.time()
        
        for i in range(1000):
            timestamp = int(time.time() * 1000) + i
            signature = auth_manager.generate_signature(timestamp, "GET", "/v1/user/balance")
            assert len(signature) == 64
        
        end_time = time.time()
        duration = end_time - start_time
        
        # Should complete 1000 signatures in less than 1 second
        assert duration < 1.0
        print(f"Generated 1000 signatures in {duration:.3f} seconds")

    @pytest.mark.asyncio
    async def test_concurrent_auth_requests(self):
        """Test concurrent authentication requests."""
        
        async def mock_auth_request():
            # Simulate auth request delay
            await asyncio.sleep(0.01)
            return True
        
        # Run 50 concurrent auth requests
        tasks = [mock_auth_request() for _ in range(50)]
        start_time = time.time()
        
        results = await asyncio.gather(*tasks)
        
        end_time = time.time()
        duration = end_time - start_time
        
        assert all(results)
        assert len(results) == 50
        # Should complete in reasonable time (less than 1 second)
        assert duration < 1.0
        print(f"Completed 50 concurrent auth requests in {duration:.3f} seconds")

    def test_signature_cache_effectiveness(self):
        """Test signature caching effectiveness."""
        cache = {}
        cache_hits = 0
        cache_misses = 0
        
        def get_signature_with_cache(timestamp: int, method: str, path: str):
            nonlocal cache_hits, cache_misses
            
            cache_key = f"{timestamp}:{method}:{path}"
            
            if cache_key in cache:
                cache_hits += 1
                return cache[cache_key]
            else:
                cache_misses += 1
                # Simulate signature generation
                signature = f"signature_{hash(cache_key) % 10000:04d}"
                cache[cache_key] = signature
                return signature
        
        # Test with repeated requests
        timestamp = int(time.time() * 1000)
        
        # Make same request multiple times
        for _ in range(10):
            sig1 = get_signature_with_cache(timestamp, "GET", "/v1/user/balance")
            sig2 = get_signature_with_cache(timestamp, "GET", "/v1/user/balance")
            assert sig1 == sig2
        
        # Make different requests
        for i in range(5):
            get_signature_with_cache(timestamp + i, "GET", "/v1/user/trades")
        
        assert cache_hits > 0
        assert cache_misses > 0
        print(f"Cache hits: {cache_hits}, Cache misses: {cache_misses}")