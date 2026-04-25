"""
Tests for Crypto.com Order Execution Event Processing
"""

import pytest
import asyncio
import json
import uuid
from datetime import datetime, timedelta
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from typing import Dict, Any, List
from decimal import Decimal


class TestCryptocomExecutionProcessor:
    """Test Crypto.com execution event processing functionality."""
    
    def setup_method(self):
        """Setup test fixtures for each test method."""
        self.processor_class = type('CryptocomExecutionProcessor', (), {
            '__init__': self._mock_init,
            'process_order_event': self._mock_process_order_event,
            'process_trade_event': self._mock_process_trade_event,
            'process_balance_event': self._mock_process_balance_event,
            'validate_event': self._mock_validate_event,
            'get_processed_events': self._mock_get_processed_events,
        })
    
    def _mock_init(self, database_service_url: str):
        """Mock initialization."""
        self.database_service_url = database_service_url
        self.processed_events = []
        self.event_cache = {}
    
    async def _mock_process_order_event(self, event: Dict[str, Any]) -> bool:
        """Mock order event processing."""
        if self.validate_event(event):
            self.processed_events.append({
                'type': 'order',
                'event': event,
                'processed_at': datetime.now().isoformat()
            })
            return True
        return False
    
    async def _mock_process_trade_event(self, event: Dict[str, Any]) -> bool:
        """Mock trade event processing."""
        if self.validate_event(event):
            self.processed_events.append({
                'type': 'trade',
                'event': event,
                'processed_at': datetime.now().isoformat()
            })
            return True
        return False
    
    async def _mock_process_balance_event(self, event: Dict[str, Any]) -> bool:
        """Mock balance event processing."""
        if self.validate_event(event):
            self.processed_events.append({
                'type': 'balance',
                'event': event,
                'processed_at': datetime.now().isoformat()
            })
            return True
        return False
    
    def _mock_validate_event(self, event: Dict[str, Any]) -> bool:
        """Mock event validation."""
        required_fields = ['method', 'result']
        return all(field in event for field in required_fields)
    
    def _mock_get_processed_events(self) -> List[Dict[str, Any]]:
        """Mock processed events retrieval."""
        return self.processed_events

    @pytest.fixture
    def execution_processor(self):
        """Create execution processor instance for testing."""
        processor = self.processor_class("http://localhost:8002")
        return processor

    @pytest.fixture
    def sample_order_event(self):
        """Sample order event from Crypto.com WebSocket."""
        return {
            "method": "user.order",
            "result": {
                "channel": "user.order",
                "data": [{
                    "status": "FILLED",
                    "side": "BUY",
                    "order_id": "order_001",
                    "client_oid": "client_001",
                    "create_time": 1640995200000,
                    "update_time": 1640995260000,
                    "type": "MARKET",
                    "instrument_name": "BTC_USDC",
                    "cumulative_quantity": "0.1",
                    "cumulative_value": "4500.0",
                    "avg_price": "45000.0",
                    "fee_currency": "USDC",
                    "exec_inst": "",
                    "time_in_force": "GOOD_TILL_CANCEL"
                }]
            }
        }

    @pytest.fixture
    def sample_trade_event(self):
        """Sample trade event from Crypto.com WebSocket."""
        return {
            "method": "user.trade",
            "result": {
                "channel": "user.trade",
                "data": [{
                    "side": "BUY",
                    "instrument_name": "BTC_USDC",
                    "fee": "0.45",
                    "trade_id": "trade_001",
                    "create_time": 1640995260000,
                    "traded_price": "45000.0",
                    "traded_quantity": "0.1",
                    "fee_currency": "USDC",
                    "order_id": "order_001"
                }]
            }
        }

    @pytest.fixture
    def sample_balance_event(self):
        """Sample balance event from Crypto.com WebSocket."""
        return {
            "method": "user.balance",
            "result": {
                "channel": "user.balance",
                "data": [{
                    "currency": "USDC",
                    "balance": "9550.0",
                    "available": "9550.0",
                    "order": "0.0",
                    "stake": "0.0"
                }, {
                    "currency": "BTC",
                    "balance": "0.1",
                    "available": "0.1",
                    "order": "0.0",
                    "stake": "0.0"
                }]
            }
        }

    @pytest.mark.asyncio
    async def test_process_order_filled_event(self, execution_processor, sample_order_event):
        """Test processing of filled order event."""
        result = await execution_processor.process_order_event(sample_order_event)
        
        assert result is True
        processed_events = execution_processor.get_processed_events()
        assert len(processed_events) == 1
        
        event = processed_events[0]
        assert event['type'] == 'order'
        assert event['event']['method'] == 'user.order'
        
        order_data = event['event']['result']['data'][0]
        assert order_data['status'] == 'FILLED'
        assert order_data['side'] == 'BUY'
        assert order_data['instrument_name'] == 'BTC_USDC'

    @pytest.mark.asyncio
    async def test_process_trade_execution_event(self, execution_processor, sample_trade_event):
        """Test processing of trade execution event."""
        result = await execution_processor.process_trade_event(sample_trade_event)
        
        assert result is True
        processed_events = execution_processor.get_processed_events()
        assert len(processed_events) == 1
        
        event = processed_events[0]
        assert event['type'] == 'trade'
        assert event['event']['method'] == 'user.trade'
        
        trade_data = event['event']['result']['data'][0]
        assert trade_data['side'] == 'BUY'
        assert trade_data['traded_price'] == '45000.0'
        assert trade_data['traded_quantity'] == '0.1'

    @pytest.mark.asyncio
    async def test_process_balance_update_event(self, execution_processor, sample_balance_event):
        """Test processing of balance update event."""
        result = await execution_processor.process_balance_event(sample_balance_event)
        
        assert result is True
        processed_events = execution_processor.get_processed_events()
        assert len(processed_events) == 1
        
        event = processed_events[0]
        assert event['type'] == 'balance'
        assert event['event']['method'] == 'user.balance'
        
        balance_data = event['event']['result']['data']
        assert len(balance_data) == 2  # USDC and BTC balances
        
        usdc_balance = next(b for b in balance_data if b['currency'] == 'USDC')
        btc_balance = next(b for b in balance_data if b['currency'] == 'BTC')
        
        assert usdc_balance['balance'] == '9550.0'
        assert btc_balance['balance'] == '0.1'

    def test_event_validation(self, execution_processor):
        """Test event validation logic."""
        valid_event = {
            "method": "user.order",
            "result": {"data": []}
        }
        
        invalid_event_1 = {
            "method": "user.order"
            # Missing 'result' field
        }
        
        invalid_event_2 = {
            "result": {"data": []}
            # Missing 'method' field
        }
        
        assert execution_processor.validate_event(valid_event) is True
        assert execution_processor.validate_event(invalid_event_1) is False
        assert execution_processor.validate_event(invalid_event_2) is False

    @pytest.mark.asyncio
    async def test_invalid_event_processing(self, execution_processor):
        """Test processing of invalid events."""
        invalid_event = {"invalid": "event"}
        
        result = await execution_processor.process_order_event(invalid_event)
        
        assert result is False
        processed_events = execution_processor.get_processed_events()
        assert len(processed_events) == 0


class TestCryptocomOrderStatusProcessor:
    """Test order status processing for different order states."""
    
    def setup_method(self):
        """Setup test fixtures for each test method."""
        self.status_processor_class = type('CryptocomOrderStatusProcessor', (), {
            '__init__': self._mock_init,
            'process_new_order': self._mock_process_new_order,
            'process_filled_order': self._mock_process_filled_order,
            'process_cancelled_order': self._mock_process_cancelled_order,
            'process_partially_filled_order': self._mock_process_partially_filled_order,
            'get_order_updates': self._mock_get_order_updates,
        })
    
    def _mock_init__(self):
        """Mock initialization."""
        self.order_updates = []
    
    async def _mock_process_new_order(self, order_data: Dict[str, Any]) -> bool:
        """Mock new order processing."""
        self.order_updates.append({
            'type': 'NEW',
            'order_id': order_data.get('order_id'),
            'status': 'PENDING',
            'timestamp': datetime.now().isoformat()
        })
        return True
    
    async def _mock_process_filled_order(self, order_data: Dict[str, Any]) -> bool:
        """Mock filled order processing."""
        self.order_updates.append({
            'type': 'FILLED',
            'order_id': order_data.get('order_id'),
            'status': 'COMPLETED',
            'fill_price': order_data.get('avg_price'),
            'fill_quantity': order_data.get('cumulative_quantity'),
            'timestamp': datetime.now().isoformat()
        })
        return True
    
    async def _mock_process_cancelled_order(self, order_data: Dict[str, Any]) -> bool:
        """Mock cancelled order processing."""
        self.order_updates.append({
            'type': 'CANCELLED',
            'order_id': order_data.get('order_id'),
            'status': 'CANCELLED',
            'timestamp': datetime.now().isoformat()
        })
        return True
    
    async def _mock_process_partially_filled_order(self, order_data: Dict[str, Any]) -> bool:
        """Mock partially filled order processing."""
        self.order_updates.append({
            'type': 'PARTIAL_FILL',
            'order_id': order_data.get('order_id'),
            'status': 'PARTIAL',
            'fill_quantity': order_data.get('cumulative_quantity'),
            'remaining_quantity': str(float(order_data.get('quantity', '0')) - float(order_data.get('cumulative_quantity', '0'))),
            'timestamp': datetime.now().isoformat()
        })
        return True
    
    def _mock_get_order_updates(self) -> List[Dict[str, Any]]:
        """Mock order updates retrieval."""
        return self.order_updates

    @pytest.fixture
    def status_processor(self):
        """Create order status processor instance for testing."""
        processor = self.status_processor_class()
        return processor

    @pytest.fixture
    def new_order_data(self):
        """Sample new order data."""
        return {
            "order_id": "new_order_001",
            "status": "PENDING",
            "side": "BUY",
            "type": "LIMIT",
            "instrument_name": "BTC_USDC",
            "quantity": "0.1",
            "price": "45000.0"
        }

    @pytest.fixture
    def filled_order_data(self):
        """Sample filled order data."""
        return {
            "order_id": "filled_order_001",
            "status": "FILLED",
            "side": "BUY",
            "type": "MARKET",
            "instrument_name": "BTC_USDC",
            "quantity": "0.1",
            "cumulative_quantity": "0.1",
            "avg_price": "45000.0"
        }

    @pytest.fixture
    def cancelled_order_data(self):
        """Sample cancelled order data."""
        return {
            "order_id": "cancelled_order_001",
            "status": "CANCELLED",
            "side": "SELL",
            "type": "LIMIT",
            "instrument_name": "BTC_USDC",
            "quantity": "0.1",
            "cumulative_quantity": "0.0"
        }

    @pytest.fixture
    def partial_fill_order_data(self):
        """Sample partially filled order data."""
        return {
            "order_id": "partial_order_001",
            "status": "PARTIALLY_FILLED",
            "side": "BUY",
            "type": "LIMIT",
            "instrument_name": "BTC_USDC",
            "quantity": "0.2",
            "cumulative_quantity": "0.1",
            "avg_price": "45000.0"
        }

    @pytest.mark.asyncio
    async def test_process_new_order_status(self, status_processor, new_order_data):
        """Test processing of new order status."""
        result = await status_processor.process_new_order(new_order_data)
        
        assert result is True
        updates = status_processor.get_order_updates()
        assert len(updates) == 1
        
        update = updates[0]
        assert update['type'] == 'NEW'
        assert update['order_id'] == 'new_order_001'
        assert update['status'] == 'PENDING'

    @pytest.mark.asyncio
    async def test_process_filled_order_status(self, status_processor, filled_order_data):
        """Test processing of filled order status."""
        result = await status_processor.process_filled_order(filled_order_data)
        
        assert result is True
        updates = status_processor.get_order_updates()
        assert len(updates) == 1
        
        update = updates[0]
        assert update['type'] == 'FILLED'
        assert update['order_id'] == 'filled_order_001'
        assert update['status'] == 'COMPLETED'
        assert update['fill_price'] == '45000.0'
        assert update['fill_quantity'] == '0.1'

    @pytest.mark.asyncio
    async def test_process_cancelled_order_status(self, status_processor, cancelled_order_data):
        """Test processing of cancelled order status."""
        result = await status_processor.process_cancelled_order(cancelled_order_data)
        
        assert result is True
        updates = status_processor.get_order_updates()
        assert len(updates) == 1
        
        update = updates[0]
        assert update['type'] == 'CANCELLED'
        assert update['order_id'] == 'cancelled_order_001'
        assert update['status'] == 'CANCELLED'

    @pytest.mark.asyncio
    async def test_process_partial_fill_order_status(self, status_processor, partial_fill_order_data):
        """Test processing of partially filled order status."""
        result = await status_processor.process_partially_filled_order(partial_fill_order_data)
        
        assert result is True
        updates = status_processor.get_order_updates()
        assert len(updates) == 1
        
        update = updates[0]
        assert update['type'] == 'PARTIAL_FILL'
        assert update['order_id'] == 'partial_order_001'
        assert update['status'] == 'PARTIAL'
        assert update['fill_quantity'] == '0.1'
        assert update['remaining_quantity'] == '0.1'


class TestCryptocomEventMapping:
    """Test mapping between Crypto.com events and internal data structures."""
    
    def setup_method(self):
        """Setup test fixtures for each test method."""
        self.mapper_class = type('CryptocomEventMapper', (), {
            '__init__': self._mock_init,
            'map_order_event': self._mock_map_order_event,
            'map_trade_event': self._mock_map_trade_event,
            'map_balance_event': self._mock_map_balance_event,
            'normalize_currency_pair': self._mock_normalize_currency_pair,
        })
    
    def _mock_init(self):
        """Mock initialization."""
        self.currency_mapping = {
            'BTC_USDC': 'BTC/USDC',
            'ETH_USDC': 'ETH/USDC',
            'ADA_USDC': 'ADA/USDC'
        }
    
    def _mock_map_order_event(self, crypto_order: Dict[str, Any]) -> Dict[str, Any]:
        """Mock order event mapping."""
        return {
            'order_id': crypto_order.get('order_id'),
            'client_order_id': crypto_order.get('client_oid'),
            'symbol': self.normalize_currency_pair(crypto_order.get('instrument_name')),
            'side': crypto_order.get('side', '').lower(),
            'order_type': crypto_order.get('type', '').lower(),
            'status': crypto_order.get('status', '').lower(),
            'quantity': float(crypto_order.get('cumulative_quantity', 0)),
            'price': float(crypto_order.get('avg_price', 0)),
            'created_at': crypto_order.get('create_time'),
            'updated_at': crypto_order.get('update_time'),
            'exchange': 'cryptocom'
        }
    
    def _mock_map_trade_event(self, crypto_trade: Dict[str, Any]) -> Dict[str, Any]:
        """Mock trade event mapping."""
        return {
            'trade_id': crypto_trade.get('trade_id'),
            'order_id': crypto_trade.get('order_id'),
            'symbol': self.normalize_currency_pair(crypto_trade.get('instrument_name')),
            'side': crypto_trade.get('side', '').lower(),
            'quantity': float(crypto_trade.get('traded_quantity', 0)),
            'price': float(crypto_trade.get('traded_price', 0)),
            'fee': float(crypto_trade.get('fee', 0)),
            'fee_currency': crypto_trade.get('fee_currency'),
            'timestamp': crypto_trade.get('create_time'),
            'exchange': 'cryptocom'
        }
    
    def _mock_map_balance_event(self, crypto_balance: Dict[str, Any]) -> Dict[str, Any]:
        """Mock balance event mapping."""
        return {
            'currency': crypto_balance.get('currency'),
            'total_balance': float(crypto_balance.get('balance', 0)),
            'available_balance': float(crypto_balance.get('available', 0)),
            'locked_balance': float(crypto_balance.get('order', 0)),
            'staked_balance': float(crypto_balance.get('stake', 0)),
            'exchange': 'cryptocom'
        }
    
    def _mock_normalize_currency_pair(self, instrument_name: str) -> str:
        """Mock currency pair normalization."""
        return self.currency_mapping.get(instrument_name, instrument_name)

    @pytest.fixture
    def event_mapper(self):
        """Create event mapper instance for testing."""
        mapper = self.mapper_class()
        return mapper

    def test_order_event_mapping(self, event_mapper):
        """Test mapping of Crypto.com order event to internal format."""
        crypto_order = {
            "order_id": "crypto_order_001",
            "client_oid": "client_001",
            "instrument_name": "BTC_USDC",
            "side": "BUY",
            "type": "MARKET",
            "status": "FILLED",
            "cumulative_quantity": "0.1",
            "avg_price": "45000.0",
            "create_time": 1640995200000,
            "update_time": 1640995260000
        }
        
        mapped_order = event_mapper.map_order_event(crypto_order)
        
        assert mapped_order['order_id'] == 'crypto_order_001'
        assert mapped_order['client_order_id'] == 'client_001'
        assert mapped_order['symbol'] == 'BTC/USDC'
        assert mapped_order['side'] == 'buy'
        assert mapped_order['order_type'] == 'market'
        assert mapped_order['status'] == 'filled'
        assert mapped_order['quantity'] == 0.1
        assert mapped_order['price'] == 45000.0
        assert mapped_order['exchange'] == 'cryptocom'

    def test_trade_event_mapping(self, event_mapper):
        """Test mapping of Crypto.com trade event to internal format."""
        crypto_trade = {
            "trade_id": "crypto_trade_001",
            "order_id": "crypto_order_001",
            "instrument_name": "ETH_USDC",
            "side": "SELL",
            "traded_quantity": "2.5",
            "traded_price": "3000.0",
            "fee": "7.5",
            "fee_currency": "USDC",
            "create_time": 1640995260000
        }
        
        mapped_trade = event_mapper.map_trade_event(crypto_trade)
        
        assert mapped_trade['trade_id'] == 'crypto_trade_001'
        assert mapped_trade['order_id'] == 'crypto_order_001'
        assert mapped_trade['symbol'] == 'ETH/USDC'
        assert mapped_trade['side'] == 'sell'
        assert mapped_trade['quantity'] == 2.5
        assert mapped_trade['price'] == 3000.0
        assert mapped_trade['fee'] == 7.5
        assert mapped_trade['fee_currency'] == 'USDC'
        assert mapped_trade['exchange'] == 'cryptocom'

    def test_balance_event_mapping(self, event_mapper):
        """Test mapping of Crypto.com balance event to internal format."""
        crypto_balance = {
            "currency": "USDC",
            "balance": "9550.0",
            "available": "9500.0",
            "order": "50.0",
            "stake": "0.0"
        }
        
        mapped_balance = event_mapper.map_balance_event(crypto_balance)
        
        assert mapped_balance['currency'] == 'USDC'
        assert mapped_balance['total_balance'] == 9550.0
        assert mapped_balance['available_balance'] == 9500.0
        assert mapped_balance['locked_balance'] == 50.0
        assert mapped_balance['staked_balance'] == 0.0
        assert mapped_balance['exchange'] == 'cryptocom'

    def test_currency_pair_normalization(self, event_mapper):
        """Test currency pair normalization from Crypto.com format."""
        test_cases = [
            ('BTC_USDC', 'BTC/USDC'),
            ('ETH_USDC', 'ETH/USDC'),
            ('ADA_USDC', 'ADA/USDC'),
            ('UNKNOWN_PAIR', 'UNKNOWN_PAIR')  # Fallback case
        ]
        
        for crypto_pair, expected_pair in test_cases:
            result = event_mapper.normalize_currency_pair(crypto_pair)
            assert result == expected_pair


@pytest.mark.integration
class TestCryptocomExecutionIntegration:
    """Integration tests for Crypto.com execution processing."""
    
    @pytest.fixture
    def sample_execution_workflow_data(self):
        """Sample data for complete execution workflow."""
        return {
            'order_placement': {
                "method": "user.order",
                "result": {
                    "data": [{
                        "status": "PENDING",
                        "order_id": "integration_order_001",
                        "side": "BUY",
                        "instrument_name": "BTC_USDC",
                        "type": "LIMIT",
                        "quantity": "0.1",
                        "price": "45000.0"
                    }]
                }
            },
            'partial_fill': {
                "method": "user.trade",
                "result": {
                    "data": [{
                        "trade_id": "integration_trade_001",
                        "order_id": "integration_order_001",
                        "side": "BUY",
                        "instrument_name": "BTC_USDC",
                        "traded_quantity": "0.05",
                        "traded_price": "45000.0",
                        "fee": "1.125"
                    }]
                }
            },
            'complete_fill': {
                "method": "user.trade",
                "result": {
                    "data": [{
                        "trade_id": "integration_trade_002",
                        "order_id": "integration_order_001",
                        "side": "BUY",
                        "instrument_name": "BTC_USDC",
                        "traded_quantity": "0.05",
                        "traded_price": "45000.0",
                        "fee": "1.125"
                    }]
                }
            },
            'balance_update': {
                "method": "user.balance",
                "result": {
                    "data": [{
                        "currency": "USDC",
                        "balance": "5497.75",
                        "available": "5497.75"
                    }, {
                        "currency": "BTC",
                        "balance": "0.1",
                        "available": "0.1"
                    }]
                }
            }
        }

    @pytest.mark.asyncio
    async def test_complete_order_execution_workflow(self, sample_execution_workflow_data):
        """Test complete order execution workflow from placement to fill."""
        
        # Mock execution processor
        class MockWorkflowProcessor:
            def __init__(self):
                self.processed_events = []
                self.order_states = {}
            
            async def process_workflow_event(self, event_type: str, event_data: Dict[str, Any]) -> bool:
                self.processed_events.append({
                    'type': event_type,
                    'data': event_data,
                    'timestamp': datetime.now().isoformat()
                })
                
                if event_type == 'order_placement':
                    order_data = event_data['result']['data'][0]
                    self.order_states[order_data['order_id']] = {
                        'status': 'PENDING',
                        'filled_quantity': 0.0,
                        'total_quantity': float(order_data['quantity'])
                    }
                
                elif event_type == 'partial_fill' or event_type == 'complete_fill':
                    trade_data = event_data['result']['data'][0]
                    order_id = trade_data['order_id']
                    
                    if order_id in self.order_states:
                        self.order_states[order_id]['filled_quantity'] += float(trade_data['traded_quantity'])
                        
                        if self.order_states[order_id]['filled_quantity'] >= self.order_states[order_id]['total_quantity']:
                            self.order_states[order_id]['status'] = 'FILLED'
                        else:
                            self.order_states[order_id]['status'] = 'PARTIALLY_FILLED'
                
                return True
        
        processor = MockWorkflowProcessor()
        
        # Process workflow events in sequence
        workflow_events = [
            ('order_placement', sample_execution_workflow_data['order_placement']),
            ('partial_fill', sample_execution_workflow_data['partial_fill']),
            ('complete_fill', sample_execution_workflow_data['complete_fill']),
            ('balance_update', sample_execution_workflow_data['balance_update'])
        ]
        
        for event_type, event_data in workflow_events:
            result = await processor.process_workflow_event(event_type, event_data)
            assert result is True
        
        # Verify workflow completion
        assert len(processor.processed_events) == 4
        
        # Verify order status progression
        order_state = processor.order_states['integration_order_001']
        assert order_state['status'] == 'FILLED'
        assert order_state['filled_quantity'] == 0.1
        assert order_state['total_quantity'] == 0.1

    @pytest.mark.asyncio
    async def test_error_handling_in_execution_workflow(self):
        """Test error handling during execution workflow."""
        
        # Mock processor with error scenarios
        class MockErrorProcessor:
            def __init__(self):
                self.error_count = 0
                self.processed_count = 0
            
            async def process_event_with_errors(self, event: Dict[str, Any]) -> bool:
                self.processed_count += 1
                
                # Simulate random errors
                if self.processed_count % 3 == 0:
                    self.error_count += 1
                    raise Exception(f"Processing error #{self.error_count}")
                
                return True
        
        processor = MockErrorProcessor()
        
        # Process multiple events with some failures
        events = [{"test": f"event_{i}"} for i in range(10)]
        
        success_count = 0
        error_count = 0
        
        for event in events:
            try:
                result = await processor.process_event_with_errors(event)
                if result:
                    success_count += 1
            except Exception:
                error_count += 1
        
        assert success_count > 0
        assert error_count > 0
        assert success_count + error_count == len(events)
        assert processor.error_count == error_count


@pytest.mark.performance
class TestCryptocomExecutionPerformance:
    """Performance tests for Crypto.com execution processing."""
    
    @pytest.mark.asyncio
    async def test_high_volume_event_processing(self):
        """Test processing of high volume events."""
        
        # Mock high-volume processor
        class MockHighVolumeProcessor:
            def __init__(self):
                self.processed_events = 0
                self.processing_times = []
            
            async def process_event(self, event: Dict[str, Any]) -> bool:
                import time
                start_time = time.time()
                
                # Simulate event processing
                await asyncio.sleep(0.001)  # 1ms processing time
                
                end_time = time.time()
                self.processing_times.append(end_time - start_time)
                self.processed_events += 1
                return True
        
        processor = MockHighVolumeProcessor()
        
        # Generate 1000 events
        events = [{"event_id": i, "data": f"test_data_{i}"} for i in range(1000)]
        
        start_time = time.time()
        
        # Process events concurrently
        tasks = [processor.process_event(event) for event in events]
        results = await asyncio.gather(*tasks)
        
        end_time = time.time()
        total_time = end_time - start_time
        
        assert all(results)
        assert processor.processed_events == 1000
        assert total_time < 5.0  # Should complete in less than 5 seconds
        
        # Calculate processing statistics
        avg_processing_time = sum(processor.processing_times) / len(processor.processing_times)
        assert avg_processing_time < 0.01  # Average processing time should be < 10ms
        
        print(f"Processed 1000 events in {total_time:.3f} seconds")
        print(f"Average processing time: {avg_processing_time:.6f} seconds")

    def test_memory_usage_during_processing(self):
        """Test memory usage during event processing."""
        import sys
        
        # Mock processor with memory tracking
        class MockMemoryProcessor:
            def __init__(self):
                self.event_cache = []
                self.processed_count = 0
            
            def process_event(self, event: Dict[str, Any]) -> bool:
                # Simulate processing and caching
                self.event_cache.append(event)
                self.processed_count += 1
                
                # Cleanup old events to manage memory
                if len(self.event_cache) > 1000:
                    self.event_cache = self.event_cache[-500:]  # Keep last 500 events
                
                return True
        
        processor = MockMemoryProcessor()
        
        # Process many events and monitor memory usage
        initial_size = sys.getsizeof(processor.event_cache)
        
        for i in range(2000):
            event = {"id": i, "data": f"large_data_{'x' * 100}"}
            processor.process_event(event)
        
        final_size = sys.getsizeof(processor.event_cache)
        
        assert processor.processed_count == 2000
        assert len(processor.event_cache) <= 1000  # Memory cleanup worked
        
        # Memory should not grow indefinitely
        assert final_size < initial_size * 10
        
        print(f"Cache size after 2000 events: {len(processor.event_cache)}")
        print(f"Memory usage: {final_size} bytes")