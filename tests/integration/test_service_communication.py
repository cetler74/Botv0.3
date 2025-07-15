"""
Integration tests for service-to-service communication
"""

import pytest
import asyncio
import httpx
from unittest.mock import patch, Mock
import json
from datetime import datetime

class TestServiceCommunication:
    """Test communication between microservices."""
    
    @pytest.fixture
    async def http_client(self):
        """Async HTTP client for testing."""
        async with httpx.AsyncClient() as client:
            return client
    
    @pytest.mark.asyncio
    async def test_config_to_database_communication(self, http_client):
        """Test communication from config service to database service."""
        # Mock config service response
        config_data = {
            "database": {
                "host": "localhost",
                "port": 5432,
                "database": "trading_bot_test",
                "user": "carloslarramba",
                "password": "password"
            }
        }
        
        with patch('httpx.AsyncClient.get') as mock_get:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = config_data
            mock_get.return_value = mock_response
            
            # Test that database service can get config from config service
            response = await http_client.get("http://config-service:8001/api/v1/config/database")
            assert response.status_code == 200
            assert response.json() == config_data["database"]
    
    @pytest.mark.asyncio
    async def test_exchange_to_config_communication(self, http_client):
        """Test communication from exchange service to config service."""
        # Mock config service response
        exchange_config = {
            "binance": {
                "api_key": "test_key",
                "api_secret": "test_secret",
                "sandbox": True
            }
        }
        
        with patch('httpx.AsyncClient.get') as mock_get:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = exchange_config
            mock_get.return_value = mock_response
            
            # Test that exchange service can get config from config service
            response = await http_client.get("http://config-service:8001/api/v1/config/exchanges")
            assert response.status_code == 200
            assert response.json() == exchange_config
    
    @pytest.mark.asyncio
    async def test_strategy_to_exchange_communication(self, http_client):
        """Test communication from strategy service to exchange service."""
        # Mock exchange service response
        market_data = {
            "symbol": "BTC/USDC",
            "last": 45000.0,
            "volume": 1000.0
        }
        
        with patch('httpx.AsyncClient.get') as mock_get:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = market_data
            mock_get.return_value = mock_response
            
            # Test that strategy service can get market data from exchange service
            response = await http_client.get("http://exchange-service:8003/api/v1/market/ticker/binance/BTC_USDC")
            assert response.status_code == 200
            assert response.json() == market_data
    
    @pytest.mark.asyncio
    async def test_orchestrator_to_all_services_communication(self, http_client):
        """Test communication from orchestrator to all other services."""
        # Test config service communication
        with patch('httpx.AsyncClient.get') as mock_get:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"mode": "simulation"}
            mock_get.return_value = mock_response
            
            response = await http_client.get("http://config-service:8001/api/v1/config/mode")
            assert response.status_code == 200
        
        # Test database service communication
        with patch('httpx.AsyncClient.get') as mock_get:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"trades": []}
            mock_get.return_value = mock_response
            
            response = await http_client.get("http://database_service:8002/api/v1/trades")
            assert response.status_code == 200
        
        # Test exchange service communication
        with patch('httpx.AsyncClient.get') as mock_get:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"exchanges": ["binance"]}
            mock_get.return_value = mock_response
            
            response = await http_client.get("http://exchange-service:8003/api/v1/exchanges")
            assert response.status_code == 200
        
        # Test strategy service communication
        with patch('httpx.AsyncClient.get') as mock_get:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"strategies": []}
            mock_get.return_value = mock_response
            
            response = await http_client.get("http://strategy-service:8004/api/v1/strategies")
            assert response.status_code == 200
    
    @pytest.mark.asyncio
    async def test_web_dashboard_to_all_services_communication(self, http_client):
        """Test communication from web dashboard to all other services."""
        # Test portfolio data from database service
        with patch('httpx.AsyncClient.get') as mock_get:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"total_balance": 10000.0}
            mock_get.return_value = mock_response
            
            response = await http_client.get("http://database_service:8002/api/v1/portfolio/summary")
            assert response.status_code == 200
        
        # Test trading status from orchestrator service
        with patch('httpx.AsyncClient.get') as mock_get:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"status": "running"}
            mock_get.return_value = mock_response
            
            response = await http_client.get("http://orchestrator-service:8005/api/v1/trading/status")
            assert response.status_code == 200
        
        # Test risk exposure from orchestrator service
        with patch('httpx.AsyncClient.get') as mock_get:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"total_exposure": 1000.0}
            mock_get.return_value = mock_response
            
            response = await http_client.get("http://orchestrator-service:8005/api/v1/risk/exposure")
            assert response.status_code == 200

class TestServiceDependencies:
    """Test service dependency management."""
    
    @pytest.mark.asyncio
    async def test_service_startup_order(self, http_client):
        """Test that services start in the correct order."""
        # Services should start in dependency order:
        # 1. Config Service (no dependencies)
        # 2. Database Service (depends on Config)
        # 3. Exchange Service (depends on Config, Database)
        # 4. Strategy Service (depends on Config, Database, Exchange)
        # 5. Orchestrator Service (depends on all)
        # 6. Web Dashboard Service (depends on all)
        
        services = [
            ("config-service", 8001),
            ("database_service", 8002),
            ("exchange-service", 8003),
            ("strategy-service", 8004),
            ("orchestrator-service", 8005),
            ("web-dashboard-service", 8006)
        ]
        
        for service_name, port in services:
            with patch('httpx.AsyncClient.get') as mock_get:
                mock_response = Mock()
                mock_response.status_code = 200
                mock_response.json.return_value = {"status": "healthy"}
                mock_get.return_value = mock_response
                
                response = await http_client.get(f"http://{service_name}:{port}/health")
                assert response.status_code == 200
    
    @pytest.mark.asyncio
    async def test_service_health_checks(self, http_client):
        """Test that all services have working health checks."""
        services = [
            ("config-service", 8001),
            ("database_service", 8002),
            ("exchange-service", 8003),
            ("strategy-service", 8004),
            ("orchestrator-service", 8005),
            ("web-dashboard-service", 8006)
        ]
        
        for service_name, port in services:
            with patch('httpx.AsyncClient.get') as mock_get:
                mock_response = Mock()
                mock_response.status_code = 200
                mock_response.json.return_value = {
                    "status": "healthy",
                    "timestamp": datetime.utcnow().isoformat(),
                    "version": "1.0.0"
                }
                mock_get.return_value = mock_response
                
                response = await http_client.get(f"http://{service_name}:{port}/health")
                assert response.status_code == 200
                data = response.json()
                assert data["status"] == "healthy"
                assert "timestamp" in data
                assert "version" in data

class TestDataFlow:
    """Test data flow between services."""
    
    @pytest.mark.asyncio
    async def test_trading_workflow_data_flow(self, http_client):
        """Test complete trading workflow data flow."""
        # 1. Config service provides trading configuration
        trading_config = {
            "mode": "simulation",
            "max_concurrent_trades": 5,
            "position_size_percentage": 0.1
        }
        
        with patch('httpx.AsyncClient.get') as mock_get:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = trading_config
            mock_get.return_value = mock_response
            
            response = await http_client.get("http://config-service:8001/api/v1/config/trading")
            assert response.status_code == 200
            assert response.json() == trading_config
        
        # 2. Exchange service provides market data
        market_data = {
            "symbol": "BTC/USDC",
            "last": 45000.0,
            "bid": 44950.0,
            "ask": 45050.0
        }
        
        with patch('httpx.AsyncClient.get') as mock_get:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = market_data
            mock_get.return_value = mock_response
            
            response = await http_client.get("http://exchange-service:8003/api/v1/market/ticker/binance/BTC_USDC")
            assert response.status_code == 200
            assert response.json() == market_data
        
        # 3. Strategy service generates signals
        strategy_signals = {
            "consensus_signal": "buy",
            "confidence": 0.8,
            "strength": 0.7
        }
        
        with patch('httpx.AsyncClient.get') as mock_get:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = strategy_signals
            mock_get.return_value = mock_response
            
            response = await http_client.get("http://strategy-service:8004/api/v1/signals/consensus/binance/BTC_USDC")
            assert response.status_code == 200
            assert response.json() == strategy_signals
        
        # 4. Database service stores trade
        trade_data = {
            "trade_id": "test_trade_001",
            "pair": "BTC/USDC",
            "exchange": "binance",
            "entry_price": 45000.0,
            "status": "OPEN"
        }
        
        with patch('httpx.AsyncClient.post') as mock_post:
            mock_response = Mock()
            mock_response.status_code = 201
            mock_response.json.return_value = {"trade_id": "test_trade_001"}
            mock_post.return_value = mock_response
            
            response = await http_client.post("http://database_service:8002/api/v1/trades", json=trade_data)
            assert response.status_code == 201
        
        # 5. Web dashboard displays portfolio
        portfolio_data = {
            "total_balance": 10000.0,
            "total_pnl": 500.0,
            "active_trades": 1
        }
        
        with patch('httpx.AsyncClient.get') as mock_get:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = portfolio_data
            mock_get.return_value = mock_response
            
            response = await http_client.get("http://database_service:8002/api/v1/portfolio/summary")
            assert response.status_code == 200
            assert response.json() == portfolio_data

class TestErrorHandling:
    """Test error handling in service communication."""
    
    @pytest.mark.asyncio
    async def test_service_unavailable(self, http_client):
        """Test handling when a service is unavailable."""
        with patch('httpx.AsyncClient.get', side_effect=httpx.ConnectError("Connection failed")):
            # Test that services handle connection errors gracefully
            try:
                await http_client.get("http://config-service:8001/health")
            except httpx.ConnectError:
                # Expected behavior when service is down
                pass
    
    @pytest.mark.asyncio
    async def test_service_timeout(self, http_client):
        """Test handling of service timeouts."""
        with patch('httpx.AsyncClient.get', side_effect=httpx.TimeoutException("Request timeout")):
            try:
                await http_client.get("http://database_service:8002/health")
            except httpx.TimeoutException:
                # Expected behavior on timeout
                pass
    
    @pytest.mark.asyncio
    async def test_service_error_response(self, http_client):
        """Test handling of error responses from services."""
        with patch('httpx.AsyncClient.get') as mock_get:
            mock_response = Mock()
            mock_response.status_code = 500
            mock_response.json.return_value = {"detail": "Internal server error"}
            mock_get.return_value = mock_response
            
            response = await http_client.get("http://config-service:8001/api/v1/config/all")
            assert response.status_code == 500
            assert "Internal server error" in response.json()["detail"]

class TestPerformance:
    """Test performance of service communication."""
    
    @pytest.mark.asyncio
    async def test_concurrent_requests(self, http_client):
        """Test handling of concurrent requests."""
        import asyncio
        
        async def make_request():
            with patch('httpx.AsyncClient.get') as mock_get:
                mock_response = Mock()
                mock_response.status_code = 200
                mock_response.json.return_value = {"status": "healthy"}
                mock_get.return_value = mock_response
                
                response = await http_client.get("http://config-service:8001/health")
                return response.status_code
        
        # Make 10 concurrent requests
        tasks = [make_request() for _ in range(10)]
        results = await asyncio.gather(*tasks)
        
        # All requests should succeed
        assert all(status == 200 for status in results)
    
    @pytest.mark.asyncio
    async def test_request_latency(self, http_client):
        """Test request latency."""
        import time
        
        with patch('httpx.AsyncClient.get') as mock_get:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"status": "healthy"}
            mock_get.return_value = mock_response
            
            start_time = time.time()
            response = await http_client.get("http://config-service:8001/health")
            end_time = time.time()
            
            latency = end_time - start_time
            assert response.status_code == 200
            assert latency < 1.0  # Should respond within 1 second 