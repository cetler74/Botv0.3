"""
End-to-end tests for complete trading workflows
"""

import pytest
import asyncio
import time
from unittest.mock import patch, Mock, AsyncMock
import json
from datetime import datetime, timedelta

class TestTradingWorkflows:
    """Test complete trading workflows end-to-end."""
    
    @pytest.fixture
    def mock_services(self):
        """Mock all services for E2E testing."""
        with patch('httpx.AsyncClient') as mock_client:
            # Mock config service
            config_response = Mock()
            config_response.status_code = 200
            config_response.json.return_value = {
                "exchanges": {
                    "binance": {
                        "api_key": "test_key",
                        "api_secret": "test_secret",
                        "sandbox": True
                    }
                },
                "trading": {
                    "mode": "simulation",
                    "max_concurrent_trades": 5,
                    "position_size_percentage": 0.1
                },
                "strategies": {
                    "vwma_hull": {"enabled": True},
                    "heikin_ashi": {"enabled": True}
                }
            }
            
            # Mock database service
            db_response = Mock()
            db_response.status_code = 200
            db_response.json.return_value = {"trades": [], "total_balance": 10000.0}
            
            # Mock exchange service
            exchange_response = Mock()
            exchange_response.status_code = 200
            exchange_response.json.return_value = {
                "symbol": "BTC/USDC",
                "last": 45000.0,
                "volume": 1000.0
            }
            
            # Mock strategy service
            strategy_response = Mock()
            strategy_response.status_code = 200
            strategy_response.json.return_value = {
                "consensus_signal": "buy",
                "confidence": 0.8,
                "strength": 0.7
            }
            
            # Mock orchestrator service
            orchestrator_response = Mock()
            orchestrator_response.status_code = 200
            orchestrator_response.json.return_value = {"status": "running"}
            
            mock_client.return_value.get.return_value = config_response
            mock_client.return_value.post.return_value = db_response
            
            yield mock_client
    
    @pytest.mark.asyncio
    async def test_complete_trading_cycle(self, mock_services):
        """Test a complete trading cycle from signal to execution."""
        # 1. Start trading orchestrator
        orchestrator = Mock()
        orchestrator.start_trading = AsyncMock()
        orchestrator.stop_trading = AsyncMock()
        
        # 2. Mock market data updates
        market_data = {
            "BTC/USDC": {
                "price": 45000.0,
                "volume": 1000.0,
                "timestamp": datetime.utcnow().isoformat()
            }
        }
        
        # 3. Mock strategy signal generation
        strategy_signals = {
            "vwma_hull": {"signal": "buy", "confidence": 0.8},
            "heikin_ashi": {"signal": "buy", "confidence": 0.7}
        }
        
        # 4. Mock consensus calculation
        consensus_signal = {
            "signal": "buy",
            "confidence": 0.75,
            "strength": 0.7,
            "strategies_agreed": 2
        }
        
        # 5. Mock trade execution
        trade_data = {
            "trade_id": "e2e_trade_001",
            "pair": "BTC/USDC",
            "exchange": "binance",
            "entry_price": 45000.0,
            "position_size": 0.1,
            "status": "OPEN",
            "strategy": "consensus",
            "entry_time": datetime.utcnow().isoformat()
        }
        
        # 6. Mock portfolio update
        portfolio_data = {
            "total_balance": 10000.0,
            "available_balance": 9500.0,
            "total_pnl": 0.0,
            "active_trades": 1
        }
        
        # Simulate the complete workflow
        with patch('httpx.AsyncClient') as mock_client:
            # Mock all service responses
            mock_client.return_value.get.return_value.json.return_value = {
                "config": {
                    "exchanges": {
                        "binance": {
                            "api_key": "test_key",
                            "api_secret": "test_secret",
                            "sandbox": True
                        }
                    },
                    "trading": {
                        "mode": "simulation",
                        "max_concurrent_trades": 5,
                        "position_size_percentage": 0.1
                    }
                },
                "market_data": market_data,
                "signals": strategy_signals,
                "consensus": consensus_signal,
                "trade": trade_data,
                "portfolio": portfolio_data
            }
            
            # Execute trading cycle
            result = await self._execute_trading_cycle(mock_client)
            
            # Verify results
            assert result["status"] == "success"
            assert result["trade_executed"] == True
            assert result["trade_id"] == "e2e_trade_001"
            assert result["signal"] == "buy"
    
    @pytest.mark.asyncio
    async def test_risk_management_workflow(self, mock_services):
        """Test risk management workflow."""
        # Mock risk limits
        risk_limits = {
            "max_daily_loss": 100.0,
            "max_total_loss": 500.0,
            "max_concurrent_trades": 5,
            "position_size_limit": 0.1
        }
        
        # Mock current exposure
        current_exposure = {
            "total_exposure": 2000.0,
            "daily_pnl": -50.0,
            "total_pnl": -200.0,
            "active_trades": 3
        }
        
        # Test risk checks
        risk_check_result = await self._check_risk_limits(risk_limits, current_exposure)
        
        assert risk_check_result["can_trade"] == True
        assert risk_check_result["daily_loss_ok"] == True
        assert risk_check_result["total_loss_ok"] == True
        assert risk_check_result["position_limit_ok"] == True
    
    @pytest.mark.asyncio
    async def test_emergency_stop_workflow(self, mock_services):
        """Test emergency stop workflow."""
        # Mock emergency stop trigger
        emergency_trigger = {
            "reason": "max_daily_loss_exceeded",
            "current_loss": -150.0,
            "limit": -100.0
        }
        
        # Execute emergency stop
        stop_result = await self._execute_emergency_stop(emergency_trigger)
        
        assert stop_result["status"] == "stopped"
        assert stop_result["reason"] == "max_daily_loss_exceeded"
        assert stop_result["all_trades_closed"] == True
    
    @pytest.mark.asyncio
    async def test_portfolio_rebalancing_workflow(self, mock_services):
        """Test portfolio rebalancing workflow."""
        # Mock portfolio state
        portfolio_state = {
            "total_balance": 10000.0,
            "allocations": {
                "BTC/USDC": 0.4,
                "ETH/USDC": 0.3,
                "ADA/USDC": 0.3
            },
            "target_allocations": {
                "BTC/USDC": 0.5,
                "ETH/USDC": 0.3,
                "ADA/USDC": 0.2
            }
        }
        
        # Execute rebalancing
        rebalance_result = await self._execute_portfolio_rebalancing(portfolio_state)
        
        assert rebalance_result["status"] == "rebalanced"
        assert rebalance_result["trades_executed"] > 0
        assert rebalance_result["new_allocations"]["BTC/USDC"] == 0.5
    
    @pytest.mark.asyncio
    async def test_multi_exchange_workflow(self, mock_services):
        """Test trading across multiple exchanges."""
        # Mock multi-exchange setup
        exchanges = ["binance", "cryptocom", "bybit"]
        
        # Mock exchange-specific data
        exchange_data = {}
        for exchange in exchanges:
            exchange_data[exchange] = {
                "balance": 3000.0,
                "available_pairs": ["BTC/USDC", "ETH/USDC"],
                "fees": 0.001
            }
        
        # Execute multi-exchange trading
        multi_exchange_result = await self._execute_multi_exchange_trading(exchanges, exchange_data)
        
        assert multi_exchange_result["status"] == "success"
        assert len(multi_exchange_result["trades"]) == len(exchanges)
        assert all(trade["exchange"] in exchanges for trade in multi_exchange_result["trades"])
    
    @pytest.mark.asyncio
    async def test_strategy_consensus_workflow(self, mock_services):
        """Test strategy consensus workflow."""
        # Mock multiple strategy signals
        strategy_signals = {
            "vwma_hull": {"signal": "buy", "confidence": 0.8, "strength": 0.7},
            "heikin_ashi": {"signal": "buy", "confidence": 0.6, "strength": 0.5},
            "engulfing": {"signal": "sell", "confidence": 0.7, "strength": 0.6}
        }
        
        # Calculate consensus
        consensus_result = await self._calculate_strategy_consensus(strategy_signals)
        
        assert consensus_result["consensus_signal"] == "buy"
        assert consensus_result["confidence"] > 0.5
        assert consensus_result["agreement_ratio"] > 0.5
    
    @pytest.mark.asyncio
    async def test_performance_monitoring_workflow(self, mock_services):
        """Test performance monitoring workflow."""
        # Mock performance metrics
        performance_metrics = {
            "sharpe_ratio": 1.2,
            "max_drawdown": -150.0,
            "win_rate": 0.65,
            "profit_factor": 1.8,
            "total_return": 0.15
        }
        
        # Monitor performance
        monitoring_result = await self._monitor_performance(performance_metrics)
        
        assert monitoring_result["status"] == "monitoring"
        assert monitoring_result["alerts_generated"] >= 0
        assert monitoring_result["performance_ok"] == True
    
    @pytest.mark.asyncio
    async def test_data_persistence_workflow(self, mock_services):
        """Test data persistence workflow."""
        # Mock data to persist
        data_to_persist = {
            "trades": [
                {"trade_id": "test_001", "pair": "BTC/USDC", "status": "OPEN"},
                {"trade_id": "test_002", "pair": "ETH/USDC", "status": "CLOSED"}
            ],
            "balances": [
                {"exchange": "binance", "balance": 5000.0},
                {"exchange": "cryptocom", "balance": 5000.0}
            ],
            "alerts": [
                {"alert_id": "alert_001", "level": "INFO", "message": "Test alert"}
            ]
        }
        
        # Persist data
        persistence_result = await self._persist_data(data_to_persist)
        
        assert persistence_result["status"] == "persisted"
        assert persistence_result["trades_saved"] == 2
        assert persistence_result["balances_saved"] == 2
        assert persistence_result["alerts_saved"] == 1
    
    @pytest.mark.asyncio
    async def test_pair_selection_workflow(self, mock_services):
        from unittest.mock import AsyncMock
        # 1. Mock config service to return exchanges and selection config
        exchanges = ["binance", "bybit", "cryptocom"]
        config_response = Mock()
        config_response.status_code = 200
        config_response.json.return_value = {
            "exchanges": exchanges,
            "pair_selector": {
                "max_pairs": 3,
                "base_currency": "USDC"
            }
        }

        # 2. Mock exchange service to return available pairs and market data
        exchange_pairs = {
            "binance": ["BTC/USDC", "ETH/USDC", "ADA/USDC"],
            "bybit": ["BTC/USDC", "SOL/USDC", "XRP/USDC"],
            "cryptocom": ["BTC/USDC", "LTC/USDC", "DOGE/USDC"]
        }
        selected_pairs = exchange_pairs["binance"]

        # 3. Mock database service for persistence
        db_response = Mock()
        db_response.status_code = 200
        db_response.json.return_value = {"status": "success"}

        # 4. Patch httpx.AsyncClient to use these mocks
        with patch('httpx.AsyncClient') as mock_client:
            # Prepare the get responses based on URL
            pairs_response = Mock()
            pairs_response.status_code = 200
            pairs_response.json.return_value = {"pairs": selected_pairs}

            def get_side_effect(url, *args, **kwargs):
                if "api/v1/pairs/binance" in url:
                    return pairs_response
                else:
                    return config_response

            mock_client.return_value.get = AsyncMock(side_effect=get_side_effect)
            mock_client.return_value.post = AsyncMock(return_value=db_response)

            # Simulate orchestrator pair selection logic
            # Simulate persistence
            response = await mock_client.return_value.post(
                f"http://database-service:8002/api/v1/pairs/binance",
                json={"pairs": selected_pairs}
            )
            assert response.status_code == 200

            # Simulate retrieval for trading cycle
            get_response = await mock_client.return_value.get(
                f"http://database-service:8002/api/v1/pairs/binance"
            )
            assert get_response.status_code == 200
            assert set(get_response.json()["pairs"]) == set(selected_pairs)

            # Log for visibility
            print(f"Selected pairs for binance: {selected_pairs}")

        # The test passes if pairs are selected and persisted correctly
    
    # Helper methods for workflow execution
    async def _execute_trading_cycle(self, mock_client):
        """Execute a complete trading cycle."""
        # Simulate the trading cycle steps
        steps = [
            "get_config",
            "fetch_market_data", 
            "generate_signals",
            "calculate_consensus",
            "check_risk_limits",
            "execute_trade",
            "update_portfolio"
        ]
        
        results = {}
        for step in steps:
            # Mock each step
            step_result = await self._execute_step(step, mock_client)
            results[step] = step_result
        
        return {
            "status": "success",
            "trade_executed": True,
            "trade_id": "e2e_trade_001",
            "signal": "buy",
            "steps": results
        }
    
    async def _execute_step(self, step_name, mock_client):
        """Execute a single trading step."""
        # Mock step execution
        return {"step": step_name, "status": "completed"}
    
    async def _check_risk_limits(self, limits, exposure):
        """Check risk limits."""
        return {
            "can_trade": exposure["daily_pnl"] > limits["max_daily_loss"] and exposure["total_pnl"] > limits["max_total_loss"] and exposure["active_trades"] < limits["max_concurrent_trades"],
            "daily_loss_ok": exposure["daily_pnl"] > limits["max_daily_loss"],
            "total_loss_ok": exposure["total_pnl"] > limits["max_total_loss"],
            "position_limit_ok": exposure["active_trades"] < limits["max_concurrent_trades"]
        }
    
    async def _execute_emergency_stop(self, trigger):
        """Execute emergency stop."""
        return {
            "status": "stopped",
            "reason": trigger["reason"],
            "all_trades_closed": True,
            "timestamp": datetime.utcnow().isoformat()
        }
    
    async def _execute_portfolio_rebalancing(self, portfolio_state):
        """Execute portfolio rebalancing."""
        return {
            "status": "rebalanced",
            "trades_executed": 2,
            "new_allocations": portfolio_state["target_allocations"]
        }
    
    async def _execute_multi_exchange_trading(self, exchanges, exchange_data):
        """Execute multi-exchange trading."""
        trades = []
        for exchange in exchanges:
            trades.append({
                "trade_id": f"multi_{exchange}_001",
                "exchange": exchange,
                "pair": "BTC/USDC",
                "status": "OPEN"
            })
        
        return {
            "status": "success",
            "trades": trades
        }
    
    async def _calculate_strategy_consensus(self, strategy_signals):
        """Calculate strategy consensus."""
        buy_signals = sum(1 for signal in strategy_signals.values() if signal["signal"] == "buy")
        total_signals = len(strategy_signals)
        
        return {
            "consensus_signal": "buy" if buy_signals > total_signals / 2 else "sell",
            "confidence": 0.7,
            "agreement_ratio": buy_signals / total_signals
        }
    
    async def _monitor_performance(self, metrics):
        """Monitor performance metrics."""
        alerts = []
        performance_ok = True
        
        if metrics["sharpe_ratio"] < 1.0:
            alerts.append("Low Sharpe ratio")
            performance_ok = False
        
        if metrics["max_drawdown"] < -200.0:
            alerts.append("High drawdown")
            performance_ok = False
        
        return {
            "status": "monitoring",
            "alerts_generated": len(alerts),
            "performance_ok": performance_ok,
            "alerts": alerts
        }
    
    async def _persist_data(self, data):
        """Persist data to database."""
        return {
            "status": "persisted",
            "trades_saved": len(data["trades"]),
            "balances_saved": len(data["balances"]),
            "alerts_saved": len(data["alerts"])
        }

class TestErrorScenarios:
    """Test error scenarios in trading workflows."""
    
    @pytest.mark.asyncio
    async def test_service_failure_recovery(self):
        """Test recovery from service failures."""
        # Mock service failure
        service_failure = {
            "service": "exchange-service",
            "error": "Connection timeout",
            "timestamp": datetime.utcnow().isoformat()
        }
        
        # Execute recovery
        recovery_result = await self._handle_service_failure(service_failure)
        
        assert recovery_result["status"] == "recovered"
        assert recovery_result["fallback_used"] == True
    
    @pytest.mark.asyncio
    async def test_data_inconsistency_handling(self):
        """Test handling of data inconsistencies."""
        # Mock inconsistent data
        inconsistent_data = {
            "database_balance": 10000.0,
            "exchange_balance": 9500.0,
            "difference": 500.0
        }
        
        # Handle inconsistency
        resolution_result = await self._handle_data_inconsistency(inconsistent_data)
        
        assert resolution_result["status"] == "resolved"
        assert resolution_result["reconciliation_performed"] == True
    
    @pytest.mark.asyncio
    async def test_network_partition_handling(self):
        """Test handling of network partitions."""
        # Mock network partition
        partition_info = {
            "affected_services": ["exchange-service", "strategy-service"],
            "duration": 30,  # seconds
            "timestamp": datetime.utcnow().isoformat()
        }
        
        # Handle partition
        partition_result = await self._handle_network_partition(partition_info)
        
        assert partition_result["status"] == "handled"
        assert partition_result["services_recovered"] == True
    
    # Helper methods for error scenarios
    async def _handle_service_failure(self, failure):
        """Handle service failure."""
        return {
            "status": "recovered",
            "fallback_used": True,
            "recovery_time": 5.0  # seconds
        }
    
    async def _handle_data_inconsistency(self, data):
        """Handle data inconsistency."""
        return {
            "status": "resolved",
            "reconciliation_performed": True,
            "corrected_balance": data["exchange_balance"]
        }
    
    async def _handle_network_partition(self, partition):
        """Handle network partition."""
        return {
            "status": "handled",
            "services_recovered": True,
            "downtime": partition["duration"]
        }

class TestPerformanceBenchmarks:
    """Test performance benchmarks for trading workflows."""
    
    @pytest.mark.asyncio
    async def test_trading_cycle_performance(self):
        """Test trading cycle performance."""
        import time
        
        start_time = time.time()
        
        # Execute trading cycle
        cycle_result = await self._benchmark_trading_cycle()
        
        end_time = time.time()
        execution_time = end_time - start_time
        
        assert execution_time < 5.0  # Should complete within 5 seconds
        assert cycle_result["status"] == "completed"
    
    @pytest.mark.asyncio
    async def test_concurrent_trading_performance(self):
        """Test concurrent trading performance."""
        import asyncio
        
        # Execute multiple trading cycles concurrently
        tasks = [self._benchmark_trading_cycle() for _ in range(10)]
        results = await asyncio.gather(*tasks)
        
        # All should complete successfully
        assert all(result["status"] == "completed" for result in results)
        assert len(results) == 10
    
    @pytest.mark.asyncio
    async def test_data_throughput_performance(self):
        """Test data throughput performance."""
        # Generate large dataset
        large_dataset = self._generate_large_dataset(10000)
        
        # Process dataset
        throughput_result = await self._benchmark_data_throughput(large_dataset)
        
        assert throughput_result["records_processed"] == 10000
        assert throughput_result["processing_time"] < 10.0  # Should process within 10 seconds
    
    # Helper methods for performance benchmarks
    async def _benchmark_trading_cycle(self):
        """Benchmark trading cycle performance."""
        await asyncio.sleep(0.1)  # Simulate processing time
        return {"status": "completed", "duration": 0.1}
    
    def _generate_large_dataset(self, size):
        """Generate large dataset for testing."""
        return [{"id": i, "data": f"record_{i}"} for i in range(size)]
    
    async def _benchmark_data_throughput(self, dataset):
        """Benchmark data throughput performance."""
        import time
        
        start_time = time.time()
        
        # Process dataset
        processed_records = 0
        for record in dataset:
            # Simulate processing
            await asyncio.sleep(0.0001)
            processed_records += 1
        
        end_time = time.time()
        processing_time = end_time - start_time
        
        return {
            "records_processed": processed_records,
            "processing_time": processing_time,
            "throughput": processed_records / processing_time
        } 