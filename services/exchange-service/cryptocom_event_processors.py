"""
Crypto.com Event Processors - Version 2.6.0
Processes real-time events from Crypto.com User Data Stream
"""

import logging
import asyncio
from datetime import datetime
from typing import Dict, Any, Optional
import httpx

logger = logging.getLogger(__name__)

class CryptocomOrderEventProcessor:
    """
    Processes Crypto.com order status events
    
    Handles:
    - Order status updates (NEW, FILLED, CANCELLED, PARTIALLY_FILLED)
    - Order modification events
    - Order rejection notifications
    """
    
    def __init__(self):
        self.metrics = {
            "events_processed": 0,
            "new_orders": 0,
            "filled_orders": 0, 
            "partially_filled_orders": 0,
            "cancelled_orders": 0,
            "rejected_orders": 0,
            "processing_errors": 0,
            "callback_errors": 0,
            "last_processed_time": None
        }
        
        logger.info("📋 Crypto.com Order Event Processor initialized")
    
    async def process_order_event(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process a Crypto.com order event and transform to standardized format
        
        Args:
            event_data: Raw order event from Crypto.com WebSocket
            
        Returns:
            Standardized order event dictionary
        """
        try:
            self.metrics["events_processed"] += 1
            self.metrics["last_processed_time"] = datetime.utcnow().isoformat()
            
            # Extract order details
            order_id = event_data.get("order_id")
            client_order_id = event_data.get("client_order_id")
            symbol = event_data.get("symbol", "").replace("_", "/")  # Convert BTC_USDC to BTC/USDC
            side = event_data.get("side", "").upper()
            order_type = event_data.get("type", "").upper()
            quantity = float(event_data.get("quantity", 0))
            price = float(event_data.get("price", 0))
            status = event_data.get("status", "").upper()
            filled_quantity = float(event_data.get("filled_quantity", 0))
            remaining_quantity = float(event_data.get("remaining_quantity", 0))
            created_time = event_data.get("created_time", 0)
            update_time = event_data.get("update_time", 0)
            
            # Update status-specific metrics
            if status == "NEW":
                self.metrics["new_orders"] += 1
            elif status == "FILLED":
                self.metrics["filled_orders"] += 1
            elif status == "PARTIALLY_FILLED":
                self.metrics["partially_filled_orders"] += 1
            elif status == "CANCELLED":
                self.metrics["cancelled_orders"] += 1
            elif status == "REJECTED":
                self.metrics["rejected_orders"] += 1
            
            # Create standardized order event
            standardized_event = {
                "event_type": "order_update",
                "exchange": "cryptocom",
                "order_id": order_id,
                "client_order_id": client_order_id,
                "symbol": symbol,
                "side": side,
                "type": order_type,
                "quantity": quantity,
                "price": price,
                "status": status,
                "filled_quantity": filled_quantity,
                "remaining_quantity": remaining_quantity,
                "average_price": price,  # Crypto.com doesn't provide average price in order events
                "created_time": datetime.fromtimestamp(created_time / 1000).isoformat() if created_time else None,
                "update_time": datetime.fromtimestamp(update_time / 1000).isoformat() if update_time else None,
                "raw_data": event_data,
                "processed_at": datetime.utcnow().isoformat()
            }
            
            logger.debug(f"📋 Processed Crypto.com order event: {order_id} ({status})")
            
            # Send to Fill Detection Service if order is filled
            if status in ["FILLED", "PARTIALLY_FILLED"]:
                await self._notify_fill_detection_service(standardized_event)
            
            return standardized_event
            
        except Exception as e:
            logger.error(f"❌ Error processing Crypto.com order event: {e}")
            self.metrics["processing_errors"] += 1
            raise
    
    async def _notify_fill_detection_service(self, order_event: Dict[str, Any]):
        """Notify Fill Detection Service of filled orders"""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(
                    "http://fill-detection-service:8007/api/v1/events/execution",
                    json={
                        "event_type": "order_filled",
                        "exchange": "cryptocom",
                        "data": order_event
                    }
                )
                
                if response.status_code == 200:
                    logger.debug(f"✅ Notified Fill Detection Service of order fill: {order_event['order_id']}")
                else:
                    logger.warning(f"⚠️ Failed to notify Fill Detection Service: {response.status_code}")
                    
        except Exception as e:
            logger.error(f"❌ Error notifying Fill Detection Service: {e}")
            self.metrics["callback_errors"] += 1
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get processor metrics"""
        return self.metrics.copy()


class CryptocomTradeEventProcessor:
    """
    Processes Crypto.com trade execution events
    
    Handles:
    - Trade execution notifications
    - Fill details and fees
    - Trade confirmations
    """
    
    def __init__(self):
        self.metrics = {
            "events_processed": 0,
            "trades_processed": 0,
            "total_volume": 0.0,
            "total_fees": 0.0,
            "processing_errors": 0,
            "callback_errors": 0,
            "last_processed_time": None
        }
        
        logger.info("💰 Crypto.com Trade Event Processor initialized")
    
    async def process_trade_event(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process a Crypto.com trade event and transform to standardized format
        
        Args:
            event_data: Raw trade event from Crypto.com WebSocket
            
        Returns:
            Standardized trade event dictionary
        """
        try:
            self.metrics["events_processed"] += 1
            self.metrics["trades_processed"] += 1
            self.metrics["last_processed_time"] = datetime.utcnow().isoformat()
            
            # Extract trade details
            trade_id = event_data.get("trade_id")
            order_id = event_data.get("order_id")
            symbol = event_data.get("symbol", "").replace("_", "/")  # Convert BTC_USDC to BTC/USDC
            side = event_data.get("side", "").upper()
            quantity = float(event_data.get("quantity", 0))
            price = float(event_data.get("price", 0))
            fee = float(event_data.get("fee", 0))
            fee_currency = event_data.get("fee_currency", "")
            trade_time = event_data.get("trade_time", 0)
            
            # Update metrics
            notional_value = quantity * price
            self.metrics["total_volume"] += notional_value
            self.metrics["total_fees"] += fee
            
            # Create standardized trade event
            standardized_event = {
                "event_type": "trade_execution",
                "exchange": "cryptocom",
                "trade_id": trade_id,
                "order_id": order_id,
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "price": price,
                "notional_value": notional_value,
                "fee": fee,
                "fee_currency": fee_currency,
                "trade_time": datetime.fromtimestamp(trade_time / 1000).isoformat() if trade_time else None,
                "raw_data": event_data,
                "processed_at": datetime.utcnow().isoformat()
            }
            
            logger.debug(f"💰 Processed Crypto.com trade event: {trade_id} ({quantity} {symbol})")
            
            # Send to Fill Detection Service
            await self._notify_fill_detection_service(standardized_event)
            
            return standardized_event
            
        except Exception as e:
            logger.error(f"❌ Error processing Crypto.com trade event: {e}")
            self.metrics["processing_errors"] += 1
            raise
    
    async def _notify_fill_detection_service(self, trade_event: Dict[str, Any]):
        """Notify Fill Detection Service of trade executions"""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(
                    "http://fill-detection-service:8007/api/v1/events/execution",
                    json={
                        "event_type": "trade_execution",
                        "exchange": "cryptocom",
                        "data": trade_event
                    }
                )
                
                if response.status_code == 200:
                    logger.debug(f"✅ Notified Fill Detection Service of trade: {trade_event['trade_id']}")
                else:
                    logger.warning(f"⚠️ Failed to notify Fill Detection Service: {response.status_code}")
                    
        except Exception as e:
            logger.error(f"❌ Error notifying Fill Detection Service: {e}")
            self.metrics["callback_errors"] += 1
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get processor metrics"""
        return self.metrics.copy()


class CryptocomBalanceEventProcessor:
    """
    Processes Crypto.com balance update events
    
    Handles:
    - Account balance changes
    - Available balance updates
    - Frozen balance notifications
    """
    
    def __init__(self):
        self.metrics = {
            "events_processed": 0,
            "balance_updates": 0,
            "currencies_tracked": set(),
            "processing_errors": 0,
            "callback_errors": 0,
            "last_processed_time": None
        }
        
        logger.info("💳 Crypto.com Balance Event Processor initialized")
    
    async def process_balance_event(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process a Crypto.com balance event and transform to standardized format
        
        Args:
            event_data: Raw balance event from Crypto.com WebSocket
            
        Returns:
            Standardized balance event dictionary
        """
        try:
            self.metrics["events_processed"] += 1
            self.metrics["balance_updates"] += 1
            self.metrics["last_processed_time"] = datetime.utcnow().isoformat()
            
            # Extract balance details
            currency = event_data.get("currency", "")
            balance = float(event_data.get("balance", 0))
            available = float(event_data.get("available", 0))
            frozen = float(event_data.get("frozen", 0))
            update_time = event_data.get("update_time", 0)
            
            # Track currencies
            self.metrics["currencies_tracked"].add(currency)
            
            # Create standardized balance event
            standardized_event = {
                "event_type": "balance_update",
                "exchange": "cryptocom",
                "currency": currency,
                "balance": balance,
                "available": available,
                "frozen": frozen,
                "update_time": datetime.fromtimestamp(update_time / 1000).isoformat() if update_time else None,
                "raw_data": event_data,
                "processed_at": datetime.utcnow().isoformat()
            }
            
            logger.debug(f"💳 Processed Crypto.com balance event: {currency} ({balance})")
            
            # Send to Database Service for balance tracking
            await self._update_balance_in_database(standardized_event)
            
            return standardized_event
            
        except Exception as e:
            logger.error(f"❌ Error processing Crypto.com balance event: {e}")
            self.metrics["processing_errors"] += 1
            raise
    
    async def _update_balance_in_database(self, balance_event: Dict[str, Any]):
        """Update balance in Database Service"""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.put(
                    "http://database-service:8002/api/v1/balances/cryptocom",
                    json={
                        "exchange": "cryptocom",
                        "currency": balance_event["currency"],
                        "balance": balance_event["balance"],
                        "available": balance_event["available"],
                        "frozen": balance_event["frozen"],
                        "timestamp": balance_event["update_time"]
                    }
                )
                
                if response.status_code == 200:
                    logger.debug(f"✅ Updated balance in database: {balance_event['currency']}")
                else:
                    logger.warning(f"⚠️ Failed to update balance in database: {response.status_code}")
                    
        except Exception as e:
            logger.error(f"❌ Error updating balance in database: {e}")
            self.metrics["callback_errors"] += 1
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get processor metrics"""
        metrics = self.metrics.copy()
        metrics["currencies_tracked"] = list(metrics["currencies_tracked"])
        metrics["unique_currencies_count"] = len(self.metrics["currencies_tracked"])
        return metrics


class CryptocomEventProcessorManager:
    """
    Manages all Crypto.com event processors
    
    Coordinates:
    - Order event processing
    - Trade event processing  
    - Balance event processing
    - Error handling and metrics collection
    """
    
    def __init__(self):
        self.order_processor = CryptocomOrderEventProcessor()
        self.trade_processor = CryptocomTradeEventProcessor()
        self.balance_processor = CryptocomBalanceEventProcessor()
        
        self.metrics = {
            "total_events_processed": 0,
            "events_by_type": {
                "order": 0,
                "trade": 0,
                "balance": 0,
                "unknown": 0
            },
            "processing_errors": 0,
            "last_processed_time": None
        }
        
        logger.info("🔄 Crypto.com Event Processor Manager initialized")
    
    async def process_event(self, channel: str, event_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Process event based on channel type
        
        Args:
            channel: Channel name (user.order, user.trade, user.balance)
            event_data: Raw event data
            
        Returns:
            Processed event data or None if processing failed
        """
        try:
            self.metrics["total_events_processed"] += 1
            self.metrics["last_processed_time"] = datetime.utcnow().isoformat()
            
            if channel == "user.order":
                self.metrics["events_by_type"]["order"] += 1
                return await self.order_processor.process_order_event(event_data)
                
            elif channel == "user.trade":
                self.metrics["events_by_type"]["trade"] += 1
                return await self.trade_processor.process_trade_event(event_data)
                
            elif channel == "user.balance":
                self.metrics["events_by_type"]["balance"] += 1
                return await self.balance_processor.process_balance_event(event_data)
                
            else:
                logger.warning(f"⚠️ Unknown channel: {channel}")
                self.metrics["events_by_type"]["unknown"] += 1
                return None
                
        except Exception as e:
            logger.error(f"❌ Error processing event from channel {channel}: {e}")
            self.metrics["processing_errors"] += 1
            return None
    
    def get_comprehensive_metrics(self) -> Dict[str, Any]:
        """Get comprehensive metrics from all processors"""
        return {
            "manager_metrics": self.metrics,
            "order_processor_metrics": self.order_processor.get_metrics(),
            "trade_processor_metrics": self.trade_processor.get_metrics(),
            "balance_processor_metrics": self.balance_processor.get_metrics()
        }
    
    def get_status(self) -> Dict[str, Any]:
        """Get overall processor status"""
        return {
            "initialized": True,
            "processors": {
                "order": True,
                "trade": True,
                "balance": True
            },
            "total_events_processed": self.metrics["total_events_processed"],
            "last_processed": self.metrics["last_processed_time"],
            "processing_healthy": self.metrics["processing_errors"] < 10  # Healthy if < 10 errors
        }