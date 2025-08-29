"""
Binance Event Processors - Version 2.5.0
Processes different types of events from Binance User Data Stream
"""

import logging
import aiohttp
from typing import Dict, Any, Optional
from datetime import datetime
from binance_user_data_stream import ExecutionReport

logger = logging.getLogger(__name__)

class ExecutionReportProcessor:
    """
    Processes executionReport events from Binance User Data Stream
    Handles order fills, status updates, and fee information
    """
    
    def __init__(self, fill_detection_service_url: str = "http://fill-detection-service:8008"):
        self.fill_detection_url = fill_detection_service_url
        
        # Metrics
        self.metrics = {
            'total_processed': 0,
            'trades_processed': 0,
            'new_orders': 0,
            'filled_orders': 0,
            'cancelled_orders': 0,
            'processing_errors': 0,
            'callback_errors': 0
        }
    
    async def process_execution_report(self, execution_report: ExecutionReport):
        """Process an execution report from Binance"""
        try:
            self.metrics['total_processed'] += 1
            
            logger.info(f"🔄 Processing execution report: {execution_report.symbol} "
                       f"{execution_report.execution_type} {execution_report.order_status}")
            
            # Handle different execution types
            if execution_report.execution_type == "NEW":
                await self._handle_new_order(execution_report)
            elif execution_report.execution_type == "TRADE":
                await self._handle_trade_execution(execution_report)
            elif execution_report.execution_type == "CANCELED":
                await self._handle_order_cancellation(execution_report)
            elif execution_report.execution_type == "REJECTED":
                await self._handle_order_rejection(execution_report)
            elif execution_report.execution_type == "EXPIRED":
                await self._handle_order_expiration(execution_report)
            else:
                logger.debug(f"📋 Unhandled execution type: {execution_report.execution_type}")
            
        except Exception as e:
            logger.error(f"❌ Error processing execution report: {e}")
            self.metrics['processing_errors'] += 1
    
    async def _handle_new_order(self, execution_report: ExecutionReport):
        """Handle new order creation"""
        self.metrics['new_orders'] += 1
        
        logger.info(f"📝 New order: {execution_report.order_id} {execution_report.symbol} "
                   f"{execution_report.side} {execution_report.order_quantity}")
        
        # Send order creation event to fill detection service
        await self._send_to_fill_detection({
            'event_type': 'order_created',
            'exchange': 'binance',
            'order_id': str(execution_report.order_id),
            'client_order_id': execution_report.client_order_id,
            'symbol': execution_report.symbol,
            'side': execution_report.side.lower(),
            'order_type': execution_report.order_type.lower(),
            'quantity': execution_report.order_quantity,
            'price': execution_report.order_price,
            'status': 'new',
            'timestamp': execution_report.event_time
        })
    
    async def _handle_trade_execution(self, execution_report: ExecutionReport):
        """Handle trade execution (partial or full fill)"""
        self.metrics['trades_processed'] += 1
        
        is_filled = execution_report.order_status == "FILLED"
        if is_filled:
            self.metrics['filled_orders'] += 1
        
        logger.info(f"💰 Trade execution: {execution_report.symbol} "
                   f"{execution_report.last_executed_quantity} @ {execution_report.last_executed_price} "
                   f"(Fee: {execution_report.commission_amount} {execution_report.commission_asset}) "
                   f"Status: {execution_report.order_status}")
        
        # Prepare fill event data
        fill_event = {
            'event_type': 'order_filled',
            'exchange': 'binance',
            'order_id': str(execution_report.order_id),
            'client_order_id': execution_report.client_order_id,
            'trade_id': str(execution_report.trade_id),
            'symbol': execution_report.symbol,
            'side': execution_report.side.lower(),
            'executed_quantity': execution_report.last_executed_quantity,
            'executed_price': execution_report.last_executed_price,
            'cumulative_quantity': execution_report.cumulative_filled_quantity,
            'remaining_quantity': execution_report.order_quantity - execution_report.cumulative_filled_quantity,
            'fee_amount': execution_report.commission_amount,
            'fee_currency': execution_report.commission_asset,
            'is_maker': execution_report.is_maker,
            'order_status': execution_report.order_status.lower(),
            'transaction_time': execution_report.transaction_time,
            'timestamp': execution_report.event_time,
            
            # Enhanced fee information
            'fee_precision': self._get_fee_precision(execution_report.commission_amount),
            'quote_quantity': execution_report.last_quote_qty,
            'cumulative_quote_quantity': execution_report.cumulative_quote_qty,
            
            # Execution metadata
            'execution_id': f"{execution_report.order_id}_{execution_report.trade_id}",
            'is_full_fill': is_filled,
            'order_creation_time': execution_report.order_creation_time
        }
        
        # Send to fill detection service
        await self._send_to_fill_detection(fill_event)
    
    async def _handle_order_cancellation(self, execution_report: ExecutionReport):
        """Handle order cancellation"""
        self.metrics['cancelled_orders'] += 1
        
        logger.info(f"❌ Order cancelled: {execution_report.order_id} {execution_report.symbol}")
        
        await self._send_to_fill_detection({
            'event_type': 'order_cancelled',
            'exchange': 'binance',
            'order_id': str(execution_report.order_id),
            'client_order_id': execution_report.client_order_id,
            'symbol': execution_report.symbol,
            'status': 'cancelled',
            'filled_quantity': execution_report.cumulative_filled_quantity,
            'remaining_quantity': execution_report.order_quantity - execution_report.cumulative_filled_quantity,
            'timestamp': execution_report.event_time
        })
    
    async def _handle_order_rejection(self, execution_report: ExecutionReport):
        """Handle order rejection"""
        logger.warning(f"⚠️ Order rejected: {execution_report.order_id} {execution_report.symbol}")
        
        await self._send_to_fill_detection({
            'event_type': 'order_rejected',
            'exchange': 'binance',
            'order_id': str(execution_report.order_id),
            'client_order_id': execution_report.client_order_id,
            'symbol': execution_report.symbol,
            'status': 'rejected',
            'timestamp': execution_report.event_time
        })
    
    async def _handle_order_expiration(self, execution_report: ExecutionReport):
        """Handle order expiration"""
        logger.info(f"⏰ Order expired: {execution_report.order_id} {execution_report.symbol}")
        
        await self._send_to_fill_detection({
            'event_type': 'order_expired',
            'exchange': 'binance',
            'order_id': str(execution_report.order_id),
            'client_order_id': execution_report.client_order_id,
            'symbol': execution_report.symbol,
            'status': 'expired',
            'filled_quantity': execution_report.cumulative_filled_quantity,
            'timestamp': execution_report.event_time
        })
    
    async def _send_to_fill_detection(self, event_data: Dict[str, Any]):
        """Send event data to fill detection service"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.fill_detection_url}/api/v1/events/execution",
                    json=event_data,
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as response:
                    if response.status == 200:
                        logger.debug(f"✅ Event sent to fill detection: {event_data['event_type']}")
                    else:
                        error_text = await response.text()
                        logger.error(f"❌ Failed to send event to fill detection: {response.status} - {error_text}")
                        
        except Exception as e:
            logger.error(f"❌ Error sending event to fill detection service: {e}")
            self.metrics['callback_errors'] += 1
    
    def _get_fee_precision(self, fee_amount: float) -> int:
        """Calculate fee precision for better storage"""
        if fee_amount == 0:
            return 0
        
        # Convert to string and count decimal places
        fee_str = str(fee_amount)
        if '.' in fee_str:
            return len(fee_str.split('.')[1])
        return 0
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get processing metrics"""
        return {
            **self.metrics,
            'processing_rate': self.metrics['total_processed'] / max(1, self.metrics.get('uptime_seconds', 1))
        }

class AccountUpdateProcessor:
    """
    Processes account-related events (balance updates, position changes)
    """
    
    def __init__(self, database_service_url: str = "http://database-service:8002"):
        self.database_url = database_service_url
        self.metrics = {
            'balance_updates': 0,
            'position_updates': 0,
            'processing_errors': 0
        }
    
    async def process_balance_update(self, balance_data: Dict[str, Any]):
        """Process balance update from Binance"""
        try:
            self.metrics['balance_updates'] += 1
            
            asset = balance_data.get('a', '')
            delta = float(balance_data.get('d', 0))
            
            logger.info(f"💰 Balance update: {asset} {delta:+.8f}")
            
            # Send to database service for balance tracking
            await self._send_balance_update({
                'exchange': 'binance',
                'asset': asset,
                'balance_delta': delta,
                'timestamp': balance_data.get('E', 0),
                'event_type': 'balance_update'
            })
            
        except Exception as e:
            logger.error(f"❌ Error processing balance update: {e}")
            self.metrics['processing_errors'] += 1
    
    async def process_position_update(self, position_data: Dict[str, Any]):
        """Process outbound account position update"""
        try:
            self.metrics['position_updates'] += 1
            
            balances = position_data.get('B', [])
            
            for balance in balances:
                asset = balance.get('a', '')
                free = float(balance.get('f', 0))
                locked = float(balance.get('l', 0))
                
                logger.debug(f"📊 Position update: {asset} Free: {free} Locked: {locked}")
            
            # Send to database service
            await self._send_position_update({
                'exchange': 'binance',
                'balances': balances,
                'timestamp': position_data.get('E', 0),
                'event_type': 'position_update'
            })
            
        except Exception as e:
            logger.error(f"❌ Error processing position update: {e}")
            self.metrics['processing_errors'] += 1
    
    async def _send_balance_update(self, balance_data: Dict[str, Any]):
        """Send balance update to database service"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.database_url}/api/v1/balances/update",
                    json=balance_data,
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"❌ Failed to send balance update: {response.status} - {error_text}")
                        
        except Exception as e:
            logger.error(f"❌ Error sending balance update: {e}")
    
    async def _send_position_update(self, position_data: Dict[str, Any]):
        """Send position update to database service"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.database_url}/api/v1/positions/update",
                    json=position_data,
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"❌ Failed to send position update: {response.status} - {error_text}")
                        
        except Exception as e:
            logger.error(f"❌ Error sending position update: {e}")
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get processing metrics"""
        return self.metrics