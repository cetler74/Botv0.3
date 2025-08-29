"""
Bybit Event Processors - Version 2.6.0
Processes Bybit WebSocket events for order updates, executions, positions, and wallet changes
"""

import asyncio
import json
import logging
from typing import Dict, Any, List, Optional, Callable
from datetime import datetime, timezone
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class BybitOrderEvent:
    """Bybit order event data structure"""
    order_id: str
    order_link_id: str
    symbol: str
    side: str
    order_type: str
    price: float
    qty: float
    cum_exec_qty: float
    cum_exec_fee: float
    avg_price: float
    order_status: str
    last_exec_price: float
    last_exec_qty: float
    exec_time: str
    timestamp: int

@dataclass
class BybitExecutionEvent:
    """Bybit execution event data structure"""
    symbol: str
    side: str
    order_id: str
    exec_id: str
    order_link_id: str
    price: float
    qty: float
    exec_fee: float
    exec_time: str
    timestamp: int

@dataclass
class BybitPositionEvent:
    """Bybit position event data structure"""
    symbol: str
    side: str
    size: float
    avg_price: float
    unrealized_pnl: float
    mark_price: float
    position_value: float
    timestamp: int

@dataclass
class BybitWalletEvent:
    """Bybit wallet event data structure"""
    currency: str
    wallet_balance: float
    available_balance: float
    timestamp: int

class BybitEventProcessor:
    """
    Processes Bybit WebSocket events with comprehensive handling
    
    Features:
    - Order event processing and status tracking
    - Execution report processing for fill detection
    - Position update processing for portfolio tracking
    - Wallet event processing for balance monitoring
    - Event validation and error handling
    - Callback system for event notifications
    """
    
    def __init__(self):
        """Initialize Bybit event processor"""
        # Event handlers
        self.event_handlers = {
            'order': self._handle_order_update,
            'execution': self._handle_execution_report,
            'position': self._handle_position_update,
            'wallet': self._handle_wallet_update
        }
        
        # Event callbacks
        self.order_callbacks: List[Callable] = []
        self.execution_callbacks: List[Callable] = []
        self.position_callbacks: List[Callable] = []
        self.wallet_callbacks: List[Callable] = []
        self.error_callbacks: List[Callable] = []
        
        # Processing metrics
        self.metrics = {
            "events_processed": 0,
            "events_by_type": {
                "order": 0,
                "execution": 0,
                "position": 0,
                "wallet": 0
            },
            "processing_errors": 0,
            "last_event_time": None,
            "uptime_start": datetime.now(timezone.utc).isoformat()
        }
        
        logger.info("📊 Bybit Event Processor initialized")
    
    async def process_event(self, event_data: Dict[str, Any]) -> bool:
        """
        Process incoming Bybit WebSocket event
        
        Args:
            event_data: Raw event data from WebSocket
            
        Returns:
            True if processed successfully, False otherwise
        """
        try:
            topic = event_data.get("topic")
            event_type = event_data.get("type")
            timestamp = event_data.get("ts")
            data = event_data.get("data", [])
            
            if not topic:
                logger.debug(f"📨 Received non-event message: {event_data}")
                return True
            
            logger.debug(f"📊 Processing {topic} event: {event_type}")
            
            # Update metrics
            self.metrics["events_processed"] += 1
            self.metrics["last_event_time"] = datetime.now(timezone.utc).isoformat()
            
            if topic in self.metrics["events_by_type"]:
                self.metrics["events_by_type"][topic] += 1
            
            # Process event based on topic
            if topic in self.event_handlers:
                await self.event_handlers[topic](data, event_type, timestamp)
                return True
            else:
                logger.warning(f"⚠️ Unknown event topic: {topic}")
                return False
                
        except Exception as e:
            self.metrics["processing_errors"] += 1
            logger.error(f"❌ Error processing event: {e}")
            await self._notify_error_callbacks(e, event_data)
            return False
    
    async def _handle_order_update(self, data: List[Dict[str, Any]], event_type: str, timestamp: int):
        """Handle order update events"""
        for order in data:
            try:
                # Parse order event
                order_event = self._parse_order_event(order, timestamp)
                
                # Validate order event
                if not self._validate_order_event(order_event):
                    logger.warning(f"⚠️ Invalid order event: {order_event.order_id}")
                    continue
                
                # Process order status
                await self._process_order_status(order_event)
                
                # Notify callbacks
                await self._notify_order_callbacks(order_event)
                
                logger.info(f"📋 Order event processed: {order_event.order_id} - {order_event.order_status}")
                
            except Exception as e:
                logger.error(f"❌ Error processing order event: {e}")
                await self._notify_error_callbacks(e, order)
    
    async def _handle_execution_report(self, data: List[Dict[str, Any]], event_type: str, timestamp: int):
        """Handle execution report events"""
        for execution in data:
            try:
                # Parse execution event
                execution_event = self._parse_execution_event(execution, timestamp)
                
                # Validate execution event
                if not self._validate_execution_event(execution_event):
                    logger.warning(f"⚠️ Invalid execution event: {execution_event.exec_id}")
                    continue
                
                # Process execution
                await self._process_execution(execution_event)
                
                # Notify callbacks
                await self._notify_execution_callbacks(execution_event)
                
                logger.info(f"⚡ Execution event processed: {execution_event.order_id} - {execution_event.exec_id}")
                
            except Exception as e:
                logger.error(f"❌ Error processing execution event: {e}")
                await self._notify_error_callbacks(e, execution)
    
    async def _handle_position_update(self, data: List[Dict[str, Any]], event_type: str, timestamp: int):
        """Handle position update events"""
        for position in data:
            try:
                # Parse position event
                position_event = self._parse_position_event(position, timestamp)
                
                # Validate position event
                if not self._validate_position_event(position_event):
                    logger.warning(f"⚠️ Invalid position event: {position_event.symbol}")
                    continue
                
                # Process position update
                await self._process_position_update(position_event)
                
                # Notify callbacks
                await self._notify_position_callbacks(position_event)
                
                logger.info(f"📈 Position event processed: {position_event.symbol} - {position_event.size}")
                
            except Exception as e:
                logger.error(f"❌ Error processing position event: {e}")
                await self._notify_error_callbacks(e, position)
    
    async def _handle_wallet_update(self, data: List[Dict[str, Any]], event_type: str, timestamp: int):
        """Handle wallet update events"""
        for wallet in data:
            try:
                # Parse wallet event
                wallet_event = self._parse_wallet_event(wallet, timestamp)
                
                # Validate wallet event
                if not self._validate_wallet_event(wallet_event):
                    logger.warning(f"⚠️ Invalid wallet event: {wallet_event.currency}")
                    continue
                
                # Process wallet update
                await self._process_wallet_update(wallet_event)
                
                # Notify callbacks
                await self._notify_wallet_callbacks(wallet_event)
                
                logger.info(f"💰 Wallet event processed: {wallet_event.currency} - {wallet_event.wallet_balance}")
                
            except Exception as e:
                logger.error(f"❌ Error processing wallet event: {e}")
                await self._notify_error_callbacks(e, wallet)
    
    def _parse_order_event(self, order_data: Dict[str, Any], timestamp: int) -> BybitOrderEvent:
        """Parse order event data"""
        return BybitOrderEvent(
            order_id=order_data.get('orderId', ''),
            order_link_id=order_data.get('orderLinkId', ''),
            symbol=order_data.get('symbol', ''),
            side=order_data.get('side', ''),
            order_type=order_data.get('orderType', ''),
            price=float(order_data.get('price', 0)),
            qty=float(order_data.get('qty', 0)),
            cum_exec_qty=float(order_data.get('cumExecQty', 0)),
            cum_exec_fee=float(order_data.get('cumExecFee', 0)),
            avg_price=float(order_data.get('avgPrice', 0)),
            order_status=order_data.get('orderStatus', ''),
            last_exec_price=float(order_data.get('lastExecPrice', 0)),
            last_exec_qty=float(order_data.get('lastExecQty', 0)),
            exec_time=order_data.get('execTime', ''),
            timestamp=timestamp
        )
    
    def _parse_execution_event(self, execution_data: Dict[str, Any], timestamp: int) -> BybitExecutionEvent:
        """Parse execution event data"""
        return BybitExecutionEvent(
            symbol=execution_data.get('symbol', ''),
            side=execution_data.get('side', ''),
            order_id=execution_data.get('orderId', ''),
            exec_id=execution_data.get('execId', ''),
            order_link_id=execution_data.get('orderLinkId', ''),
            price=float(execution_data.get('price', 0)),
            qty=float(execution_data.get('qty', 0)),
            exec_fee=float(execution_data.get('execFee', 0)),
            exec_time=execution_data.get('execTime', ''),
            timestamp=timestamp
        )
    
    def _parse_position_event(self, position_data: Dict[str, Any], timestamp: int) -> BybitPositionEvent:
        """Parse position event data"""
        return BybitPositionEvent(
            symbol=position_data.get('symbol', ''),
            side=position_data.get('side', ''),
            size=float(position_data.get('size', 0)),
            avg_price=float(position_data.get('avgPrice', 0)),
            unrealized_pnl=float(position_data.get('unrealizedPnl', 0)),
            mark_price=float(position_data.get('markPrice', 0)),
            position_value=float(position_data.get('positionValue', 0)),
            timestamp=timestamp
        )
    
    def _parse_wallet_event(self, wallet_data: Dict[str, Any], timestamp: int) -> BybitWalletEvent:
        """Parse wallet event data"""
        return BybitWalletEvent(
            currency=wallet_data.get('currency', ''),
            wallet_balance=float(wallet_data.get('walletBalance', 0)),
            available_balance=float(wallet_data.get('availableBalance', 0)),
            timestamp=timestamp
        )
    
    def _validate_order_event(self, order_event: BybitOrderEvent) -> bool:
        """Validate order event data"""
        if not order_event.order_id:
            return False
        if not order_event.symbol:
            return False
        if order_event.price < 0:
            return False
        if order_event.qty < 0:
            return False
        return True
    
    def _validate_execution_event(self, execution_event: BybitExecutionEvent) -> bool:
        """Validate execution event data"""
        if not execution_event.exec_id:
            return False
        if not execution_event.order_id:
            return False
        if not execution_event.symbol:
            return False
        if execution_event.price < 0:
            return False
        if execution_event.qty < 0:
            return False
        return True
    
    def _validate_position_event(self, position_event: BybitPositionEvent) -> bool:
        """Validate position event data"""
        if not position_event.symbol:
            return False
        if position_event.size < 0:
            return False
        if position_event.avg_price < 0:
            return False
        return True
    
    def _validate_wallet_event(self, wallet_event: BybitWalletEvent) -> bool:
        """Validate wallet event data"""
        if not wallet_event.currency:
            return False
        if wallet_event.wallet_balance < 0:
            return False
        if wallet_event.available_balance < 0:
            return False
        return True
    
    async def _process_order_status(self, order_event: BybitOrderEvent):
        """Process order status update"""
        # Handle different order statuses
        if order_event.order_status == 'Filled':
            logger.info(f"✅ Order {order_event.order_id} filled: {order_event.cum_exec_qty} @ {order_event.avg_price}")
        elif order_event.order_status == 'Cancelled':
            logger.info(f"❌ Order {order_event.order_id} cancelled")
        elif order_event.order_status == 'Rejected':
            logger.error(f"❌ Order {order_event.order_id} rejected")
        elif order_event.order_status == 'New':
            logger.info(f"📝 New order {order_event.order_id} created")
        elif order_event.order_status == 'PartiallyFilled':
            logger.info(f"🔄 Order {order_event.order_id} partially filled: {order_event.cum_exec_qty}/{order_event.qty}")
    
    async def _process_execution(self, execution_event: BybitExecutionEvent):
        """Process execution event"""
        logger.info(f"⚡ Execution: {execution_event.exec_id} for order {execution_event.order_id}")
        logger.info(f"   Price: {execution_event.price}, Qty: {execution_event.qty}, Fee: {execution_event.exec_fee}")
    
    async def _process_position_update(self, position_event: BybitPositionEvent):
        """Process position update"""
        logger.info(f"📈 Position update for {position_event.symbol}: {position_event.size} @ {position_event.avg_price}")
        logger.info(f"   Unrealized PnL: {position_event.unrealized_pnl}, Mark Price: {position_event.mark_price}")
    
    async def _process_wallet_update(self, wallet_event: BybitWalletEvent):
        """Process wallet update"""
        logger.info(f"💰 Wallet update for {wallet_event.currency}")
        logger.info(f"   Wallet Balance: {wallet_event.wallet_balance}, Available: {wallet_event.available_balance}")
    
    def add_order_callback(self, callback: Callable):
        """Add order event callback"""
        self.order_callbacks.append(callback)
    
    def add_execution_callback(self, callback: Callable):
        """Add execution event callback"""
        self.execution_callbacks.append(callback)
    
    def add_position_callback(self, callback: Callable):
        """Add position event callback"""
        self.position_callbacks.append(callback)
    
    def add_wallet_callback(self, callback: Callable):
        """Add wallet event callback"""
        self.wallet_callbacks.append(callback)
    
    def add_error_callback(self, callback: Callable):
        """Add error callback"""
        self.error_callbacks.append(callback)
    
    async def _notify_order_callbacks(self, order_event: BybitOrderEvent):
        """Notify order callbacks"""
        for callback in self.order_callbacks:
            try:
                await callback(order_event)
            except Exception as e:
                logger.error(f"❌ Order callback error: {e}")
    
    async def _notify_execution_callbacks(self, execution_event: BybitExecutionEvent):
        """Notify execution callbacks"""
        for callback in self.execution_callbacks:
            try:
                await callback(execution_event)
            except Exception as e:
                logger.error(f"❌ Execution callback error: {e}")
    
    async def _notify_position_callbacks(self, position_event: BybitPositionEvent):
        """Notify position callbacks"""
        for callback in self.position_callbacks:
            try:
                await callback(position_event)
            except Exception as e:
                logger.error(f"❌ Position callback error: {e}")
    
    async def _notify_wallet_callbacks(self, wallet_event: BybitWalletEvent):
        """Notify wallet callbacks"""
        for callback in self.wallet_callbacks:
            try:
                await callback(wallet_event)
            except Exception as e:
                logger.error(f"❌ Wallet callback error: {e}")
    
    async def _notify_error_callbacks(self, error: Exception, event_data: Dict[str, Any]):
        """Notify error callbacks"""
        for callback in self.error_callbacks:
            try:
                await callback(error, event_data)
            except Exception as e:
                logger.error(f"❌ Error callback error: {e}")
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get processing metrics"""
        return self.metrics.copy()
    
    def reset_metrics(self):
        """Reset processing metrics"""
        self.metrics = {
            "events_processed": 0,
            "events_by_type": {
                "order": 0,
                "execution": 0,
                "position": 0,
                "wallet": 0
            },
            "processing_errors": 0,
            "last_event_time": None,
            "uptime_start": datetime.now(timezone.utc).isoformat()
        }
        logger.info("📊 Event processor metrics reset")
