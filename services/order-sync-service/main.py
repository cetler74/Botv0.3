"""
Order Synchronization Service - Reconciler v2 (Phase 4)
Event-driven reconciliation between local views and exchange state
Generates corrective events instead of direct database mutations
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from enum import Enum
import json
import httpx
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
import uvicorn
import os

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="Order Sync Service",
    description="Real-time order status synchronization between exchanges and database",
    version="1.0.0"
)

# Service URLs
database_service_url = os.getenv("DATABASE_SERVICE_URL", "http://database-service:8002")
exchange_service_url = os.getenv("EXCHANGE_SERVICE_URL", "http://exchange-service:8003")
orchestrator_service_url = os.getenv("ORCHESTRATOR_SERVICE_URL", "http://orchestrator-service:8005")

class OrderState(Enum):
    """Order lifecycle states"""
    CREATED = "created"
    SUBMITTED = "submitted"
    PENDING = "pending"
    PARTIALLY_FILLED = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    FAILED = "failed"
    REJECTED = "rejected"

class OrderStatusUpdate(BaseModel):
    """Order status update model"""
    order_id: str
    exchange_order_id: Optional[str] = None
    status: str
    filled_amount: Optional[float] = None
    filled_price: Optional[float] = None
    fees: Optional[float] = None
    fee_rate: Optional[float] = None
    updated_at: str
    sync_source: str = "order_sync_service"
    cancellation_reason: Optional[str] = None

# Phase 4: Event-Driven Reconciler v2
class ReconcilerV2:
    """Event-driven reconciler that generates corrective events instead of direct DB mutations"""
    
    def __init__(self):
        self.reconciler_interval = 60  # seconds - less frequent than old sync
        self.is_running = False
        self.config = None
        self.metrics = {
            'reconciliations_run': 0,
            'mismatches_found': 0,
            'corrective_events_generated': 0,
            'orphaned_orders_imported': 0,
            'alerts_raised': 0
        }
    
    async def emit_corrective_event(self, event_type: str, aggregate_id: str, payload: dict, correlation_id: str = None) -> bool:
        """Emit a corrective event instead of direct database mutation"""
        try:
            import uuid
            event_data = {
                'aggregate_id': aggregate_id if aggregate_id else str(uuid.uuid4()),
                'aggregate_type': 'order',
                'event_type': event_type,
                'payload': payload,
                'correlation_id': correlation_id if correlation_id else str(uuid.uuid4()),
                'causation_id': str(uuid.uuid4())
            }
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(f"{database_service_url}/api/v1/events", json=event_data)
                response.raise_for_status()
                result = response.json()
                
                logger.info(f"📝 Reconciler emitted {event_type} event: {result['event_id']}")
                self.metrics['corrective_events_generated'] += 1
                return True
                
        except Exception as e:
            logger.error(f"Failed to emit corrective event {event_type}: {e}")
            return False
    
    async def reconcile_exchange(self, exchange: str) -> dict:
        """Reconcile orders for a specific exchange using event-driven approach"""
        logger.info(f"🔍 Phase 4 reconciling {exchange}...")
        
        reconcile_result = {
            'exchange': exchange,
            'mismatches_found': 0,
            'corrective_events': 0,
            'orphaned_orders': 0,
            'alerts': []
        }
        
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                # Get local order mappings for this exchange
                local_response = await client.get(
                    f"{database_service_url}/api/v1/order-mappings",
                    params={"exchange": exchange, "status": "ACKNOWLEDGED,PARTIALLY_FILLED"}
                )
                
                if local_response.status_code == 404:
                    # No endpoint exists yet - use fallback method
                    local_orders = await self.get_local_orders_fallback(exchange)
                else:
                    local_response.raise_for_status()
                    local_orders = local_response.json().get('order_mappings', [])
                
                # Get exchange orders snapshot
                exchange_response = await client.get(f"{exchange_service_url}/api/v1/trading/orders/{exchange}")
                exchange_response.raise_for_status()
                exchange_orders = exchange_response.json().get('orders', [])
                
                logger.info(f"📊 {exchange}: {len(local_orders)} local orders, {len(exchange_orders)} exchange orders")
                
                # Create mappings for comparison
                local_by_exchange_id = {order.get('exchange_order_id'): order for order in local_orders if order.get('exchange_order_id')}
                exchange_by_id = {order['id']: order for order in exchange_orders}
                
                # Find mismatches and generate corrective events
                await self.detect_and_correct_mismatches(local_by_exchange_id, exchange_by_id, exchange, reconcile_result)
                
                # Find orphaned exchange orders (exist on exchange but not locally)
                await self.import_orphaned_orders(exchange_by_id, local_by_exchange_id, exchange, reconcile_result)
                
                # Find unknown local orders (exist locally but not on exchange)
                await self.handle_unknown_local_orders(local_by_exchange_id, exchange_by_id, exchange, reconcile_result)
                
        except Exception as e:
            logger.error(f"Failed to reconcile {exchange}: {e}")
            reconcile_result['alerts'].append({
                'type': 'reconciliation_error',
                'message': f"Failed to reconcile {exchange}: {str(e)}",
                'severity': 'error'
            })
        
        self.metrics['reconciliations_run'] += 1
        self.metrics['mismatches_found'] += reconcile_result['mismatches_found']
        self.metrics['orphaned_orders_imported'] += reconcile_result['orphaned_orders']
        self.metrics['alerts_raised'] += len(reconcile_result['alerts'])
        
        return reconcile_result
    
    async def get_local_orders_fallback(self, exchange: str) -> list:
        """Fallback method to get local orders from existing tables"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Use existing orders endpoint as fallback
                response = await client.get(
                    f"{database_service_url}/api/v1/orders",
                    params={"exchange": exchange}
                )
                response.raise_for_status()
                orders = response.json().get('orders', [])
                
                # Convert to order_mappings format
                converted = []
                for order in orders:
                    if order.get('exchange_order_id'):
                        converted.append({
                            'local_order_id': f"legacy-{order['order_id']}",
                            'exchange_order_id': order['exchange_order_id'],
                            'client_order_id': order.get('client_order_id', f"legacy-{order['order_id']}"),
                            'exchange': exchange,
                            'symbol': order['symbol'],
                            'side': order['side'],
                            'order_type': order['order_type'],
                            'amount': order['amount'],
                            'price': order.get('price'),
                            'status': order['status'].upper()
                        })
                
                return converted
        except Exception as e:
            logger.error(f"Fallback local orders fetch failed: {e}")
            return []
    
    async def detect_and_correct_mismatches(self, local_orders: dict, exchange_orders: dict, exchange: str, result: dict):
        """Detect status mismatches and generate corrective events"""
        for exchange_order_id, local_order in local_orders.items():
            if exchange_order_id in exchange_orders:
                exchange_order = exchange_orders[exchange_order_id]
                
                # Compare status
                local_status = local_order.get('status', '').upper()
                exchange_status = self.normalize_exchange_status(exchange_order.get('status', ''))
                
                if local_status != exchange_status:
                    logger.info(f"🔧 Status mismatch {exchange_order_id}: local={local_status}, exchange={exchange_status}")
                    
                    # Generate corrective event
                    await self.emit_status_correction_event(local_order, exchange_order, exchange_status)
                    result['mismatches_found'] += 1
                    result['corrective_events'] += 1
                
                # Check for fills
                exchange_filled = float(exchange_order.get('filled', 0))
                if exchange_filled > 0:
                    await self.emit_fill_correction_event(local_order, exchange_order)
    
    async def emit_status_correction_event(self, local_order: dict, exchange_order: dict, new_status: str):
        """Emit OrderUpdate event for status correction"""
        payload = {
            'local_order_id': local_order.get('local_order_id'),
            'exchange_order_id': local_order.get('exchange_order_id'),
            'status': new_status,
            'exchange_data': exchange_order,
            'correction_reason': f'Reconciler v2 status correction: exchange shows {new_status}',
            'timestamp': datetime.utcnow().isoformat()
        }
        
        await self.emit_corrective_event('OrderUpdate', local_order.get('local_order_id'), payload, str(uuid.uuid4()))
    
    async def emit_fill_correction_event(self, local_order: dict, exchange_order: dict):
        """Emit OrderFilled event for missing fills"""
        filled_qty = float(exchange_order.get('filled', 0))
        if filled_qty <= 0:
            return
        
        payload = {
            'local_order_id': local_order.get('local_order_id'),
            'exchange_order_id': local_order.get('exchange_order_id'),
            'exchange': local_order.get('exchange'),
            'symbol': local_order.get('symbol'),
            'side': local_order.get('side'),
            'quantity': filled_qty,
            'price': float(exchange_order.get('average', 0)) or float(exchange_order.get('price', 0)),
            'fee': exchange_order.get('fee', {}).get('cost', 0),
            'fee_asset': exchange_order.get('fee', {}).get('currency', 'USD'),
            'trade_id': exchange_order.get('id'),  # Use exchange order ID as trade ID
            'timestamp': datetime.utcnow().isoformat(),
            'fully_filled': exchange_order.get('status', '').lower() in ['filled', 'closed'],
            'correction_reason': 'Reconciler v2 detected missing fill from exchange'
        }
        
        await self.emit_corrective_event('OrderFilled', local_order.get('local_order_id'), payload, str(uuid.uuid4()))
    
    async def import_orphaned_orders(self, exchange_orders: dict, local_orders: dict, exchange: str, result: dict):
        """Import orders that exist on exchange but not locally"""
        orphaned = set(exchange_orders.keys()) - set(local_orders.keys())
        
        for exchange_order_id in orphaned:
            exchange_order = exchange_orders[exchange_order_id]
            
            # Only import orders that are significant (not tiny test orders)
            filled_amount = float(exchange_order.get('filled', 0))
            total_amount = float(exchange_order.get('amount', 0))
            
            if total_amount >= 0.001 or filled_amount > 0:  # Import significant orders
                await self.emit_orphaned_order_import_event(exchange_order, exchange)
                result['orphaned_orders'] += 1
                logger.info(f"📥 Importing orphaned order: {exchange_order_id} on {exchange}")
    
    async def emit_orphaned_order_import_event(self, exchange_order: dict, exchange: str):
        """Emit event to import orphaned exchange order"""
        import uuid
        local_order_id = str(uuid.uuid4())
        client_order_id = f"reconciler-import-{exchange_order['id']}"
        
        payload = {
            'local_order_id': local_order_id,
            'client_order_id': client_order_id,
            'exchange_order_id': exchange_order['id'],
            'exchange': exchange,
            'symbol': exchange_order['symbol'],
            'side': exchange_order['side'],
            'order_type': exchange_order.get('type', 'unknown'),
            'amount': exchange_order['amount'],
            'price': exchange_order.get('price'),
            'status': self.normalize_exchange_status(exchange_order.get('status', '')),
            'import_reason': 'Reconciler v2 found orphaned order on exchange',
            'imported_at': datetime.utcnow().isoformat(),
            'exchange_data': exchange_order
        }
        
        await self.emit_corrective_event('OrderImportedFromExchange', local_order_id, payload, str(uuid.uuid4()))
    
    async def handle_unknown_local_orders(self, local_orders: dict, exchange_orders: dict, exchange: str, result: dict):
        """Handle orders that exist locally but not on exchange"""
        unknown_local = set(local_orders.keys()) - set(exchange_orders.keys())
        
        for exchange_order_id in unknown_local:
            local_order = local_orders[exchange_order_id]
            local_status = local_order.get('status', '').upper()
            
            # If order is in a non-terminal state but missing from exchange, investigate
            if local_status in ['ACKNOWLEDGED', 'PARTIALLY_FILLED']:
                result['alerts'].append({
                    'type': 'unknown_local_order',
                    'message': f"Order {exchange_order_id} exists locally ({local_status}) but not on {exchange}",
                    'severity': 'warning',
                    'local_order_id': local_order.get('local_order_id'),
                    'exchange_order_id': exchange_order_id
                })
                
                # Emit corrective event to mark as potentially expired/cancelled
                await self.emit_unknown_order_correction_event(local_order)
                result['corrective_events'] += 1
    
    async def emit_unknown_order_correction_event(self, local_order: dict):
        """Emit correction event for orders missing from exchange"""
        payload = {
            'local_order_id': local_order.get('local_order_id'),
            'exchange_order_id': local_order.get('exchange_order_id'),
            'status': 'CANCELLED',  # Assume cancelled if missing from exchange
            'correction_reason': 'Reconciler v2 marked as cancelled - order missing from exchange',
            'timestamp': datetime.utcnow().isoformat()
        }
        
        await self.emit_corrective_event('OrderUpdate', local_order.get('local_order_id'), payload, str(uuid.uuid4()))
    
    def normalize_exchange_status(self, exchange_status: str) -> str:
        """Normalize exchange status to our internal format"""
        status = exchange_status.lower()
        mapping = {
            'open': 'ACKNOWLEDGED',
            'new': 'ACKNOWLEDGED', 
            'partially_filled': 'PARTIALLY_FILLED',
            'filled': 'FILLED',
            'closed': 'FILLED',
            'canceled': 'CANCELLED',
            'cancelled': 'CANCELLED',
            'expired': 'CANCELLED',
            'rejected': 'REJECTED'
        }
        return mapping.get(status, 'ACKNOWLEDGED')
    
    async def run_reconciliation_cycle(self) -> dict:
        """Run complete reconciliation cycle for all exchanges"""
        logger.info("🔄 Starting Reconciler v2 cycle...")
        
        exchanges = ['binance', 'cryptocom', 'bybit']
        cycle_results = {
            'timestamp': datetime.utcnow().isoformat(),
            'exchanges': {},
            'summary': {
                'total_mismatches': 0,
                'total_corrective_events': 0,
                'total_orphaned_orders': 0,
                'total_alerts': 0
            }
        }
        
        for exchange in exchanges:
            try:
                result = await self.reconcile_exchange(exchange)
                cycle_results['exchanges'][exchange] = result
                
                # Update summary
                cycle_results['summary']['total_mismatches'] += result['mismatches_found']
                cycle_results['summary']['total_corrective_events'] += result['corrective_events']
                cycle_results['summary']['total_orphaned_orders'] += result['orphaned_orders']
                cycle_results['summary']['total_alerts'] += len(result['alerts'])
                
            except Exception as e:
                logger.error(f"Failed to reconcile {exchange}: {e}")
                cycle_results['exchanges'][exchange] = {'error': str(e)}
        
        if cycle_results['summary']['total_mismatches'] > 0:
            logger.info(f"🔧 Reconciler v2 found {cycle_results['summary']['total_mismatches']} mismatches, generated {cycle_results['summary']['total_corrective_events']} corrective events")
        
        return cycle_results
    
    async def start_reconciler_loop(self):
        """Start continuous reconciliation loop"""
        logger.info("🚀 Starting Reconciler v2 loop...")
        self.is_running = True
        
        while self.is_running:
            try:
                await self.run_reconciliation_cycle()
                await asyncio.sleep(self.reconciler_interval)
            except Exception as e:
                logger.error(f"Reconciler v2 cycle error: {e}")
                await asyncio.sleep(120)  # Wait longer on error

class OrderSyncService:
    """Legacy order synchronization service (kept for backward compatibility)"""
    
    def __init__(self):
        self.sync_interval = 30  # seconds
        self.max_retries = 3
        self.timeout_threshold = 300  # 5 minutes
        self.max_cancellation_retries = 3
        self.cancellation_retry_delay = 5
        self.is_running = False
        self.config = None
        
    async def load_config(self):
        """Load configuration from config service"""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                config_url = os.getenv("CONFIG_SERVICE_URL", "http://config-service:8001")
                response = await client.get(f"{config_url}/api/v1/config/all")
                
                if response.status_code == 200:
                    self.config = response.json()
                    order_mgmt = self.config.get('trading', {}).get('order_management', {})
                    
                    # Update settings from config
                    self.timeout_threshold = order_mgmt.get('limit_order_timeout_seconds', 300)
                    self.sync_interval = order_mgmt.get('order_sync_interval_seconds', 30)
                    self.max_cancellation_retries = order_mgmt.get('max_cancellation_retries', 3)
                    self.cancellation_retry_delay = order_mgmt.get('cancellation_retry_delay', 5)
                    
                    logger.info(f"📋 Config loaded - timeout: {self.timeout_threshold}s, sync: {self.sync_interval}s")
                else:
                    logger.warning(f"⚠️  Failed to load config: {response.status_code}, using defaults")
        except Exception as e:
            logger.warning(f"⚠️  Config load error: {e}, using defaults")
    
    async def start_sync_loop(self):
        """Start the continuous order synchronization loop"""
        logger.info("🔄 Starting order synchronization service...")
        
        # Load configuration first
        await self.load_config()
        
        self.is_running = True
        
        while self.is_running:
            try:
                await self.sync_all_orders()
                await asyncio.sleep(self.sync_interval)
            except Exception as e:
                logger.error(f"❌ Error in sync loop: {e}")
                await asyncio.sleep(60)  # Wait longer on error
    
    async def sync_all_orders(self):
        """Sync order status for all exchanges and check for timeout orders"""
        exchanges = ['cryptocom', 'binance', 'bybit']
        
        # First, sync order status from exchanges
        for exchange in exchanges:
            try:
                await self.sync_exchange_orders(exchange)
            except Exception as e:
                logger.error(f"❌ Failed to sync {exchange} orders: {e}")
        
        # Then, check for and cancel timeout orders
        try:
            await self.check_timeout_orders()
        except Exception as e:
            logger.error(f"❌ Failed to check timeout orders: {e}")
    
    async def sync_exchange_orders(self, exchange: str):
        """Sync orders for a specific exchange"""
        logger.info(f"🔍 Syncing {exchange} orders...")
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Get pending orders from database for this exchange
            db_response = await client.get(
                f"{database_service_url}/api/v1/orders",
                params={"exchange": exchange, "status": "pending"}
            )
            
            if db_response.status_code != 200:
                logger.error(f"❌ Failed to get {exchange} orders from database: {db_response.status_code}")
                return
                
            db_orders = db_response.json().get('orders', [])
            logger.info(f"📊 Found {len(db_orders)} pending {exchange} orders in database")
            
            # Get current orders from exchange
            exchange_response = await client.get(f"{exchange_service_url}/api/v1/trading/orders/{exchange}")
            if exchange_response.status_code != 200:
                logger.warning(f"⚠️  Failed to get {exchange} orders from exchange: {exchange_response.status_code}")
                return
                
            exchange_orders = exchange_response.json().get('orders', [])
            exchange_order_map = {order['id']: order for order in exchange_orders}
            
            # Sync each database order with exchange status
            sync_count = 0
            for db_order in db_orders:
                try:
                    synced = await self.sync_order_status(db_order, exchange_order_map, exchange)
                    if synced:
                        sync_count += 1
                except Exception as e:
                    logger.error(f"❌ Failed to sync order {db_order.get('order_id')}: {e}")
            
            logger.info(f"✅ Synced {sync_count} {exchange} orders")
    
    async def sync_order_status(self, db_order: Dict[str, Any], exchange_orders: Dict[str, Any], exchange: str) -> bool:
        """Sync status for a specific order"""
        order_id = db_order.get('order_id')
        exchange_order_id = db_order.get('exchange_order_id')
        symbol = db_order.get('symbol')
        
        if not exchange_order_id:
            logger.warning(f"⚠️  Order {order_id} has no exchange_order_id")
            return False
        
        # Check if order exists on exchange open orders snapshot
        if exchange_order_id in exchange_orders:
            exchange_order = exchange_orders[exchange_order_id]
            return await self.update_order_from_exchange(db_order, exchange_order)
        else:
            # Order not found in open list - attempt immediate resolve by fetching exact order by ID
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    # Try with symbol hint if available
                    url = f"{exchange_service_url}/api/v1/trading/order/{exchange}/{exchange_order_id}"
                    if symbol:
                        url = url + f"?symbol={symbol}"
                    detail_resp = await client.get(url)
                    if detail_resp.status_code == 200:
                        ex_order = detail_resp.json().get('order', {})
                        # Use same mapping as update_order_from_exchange
                        return await self.update_order_from_exchange(db_order, ex_order)
            except Exception as e:
                logger.warning(f"⚠️ Immediate fetch by id failed for {exchange_order_id} on {exchange}: {e}")
            # Fallback: handle as missing order (timeout rules apply inside)
            return await self.handle_missing_order(db_order, exchange)
    
    async def update_order_from_exchange(self, db_order: Dict[str, Any], exchange_order: Dict[str, Any]) -> bool:
        """Update database order from exchange order data"""
        order_id = db_order.get('order_id')
        current_status = db_order.get('status')
        exchange_status = exchange_order.get('status', '').lower()
        
        # Map exchange status to our status
        status_mapping = {
            'open': 'pending',
            'filled': 'filled',
            'closed': 'filled',
            'canceled': 'cancelled',
            'cancelled': 'cancelled',
            'expired': 'expired',
            'rejected': 'rejected'
        }
        
        new_status = status_mapping.get(exchange_status, exchange_status)
        
        # Only update if status changed
        if new_status != current_status:
            logger.info(f"🔄 Order {order_id}: {current_status} -> {new_status}")
            
            update_data = OrderStatusUpdate(
                order_id=order_id,
                exchange_order_id=exchange_order.get('id'),
                status=new_status,
                filled_amount=float(exchange_order.get('filled', 0)),
                filled_price=float(exchange_order.get('average', 0)) if exchange_order.get('average') else None,
                updated_at=datetime.utcnow().isoformat() + 'Z'
            )
            
            # Add fees if available
            if 'fee' in exchange_order and exchange_order['fee']:
                update_data.fees = exchange_order['fee'].get('cost', 0)
                update_data.fee_rate = exchange_order['fee'].get('rate', 0)
            
            return await self.update_database_order(update_data)
        
        return False
    
    async def handle_missing_order(self, db_order: Dict[str, Any], exchange: str) -> bool:
        """Handle orders that are no longer on exchange"""
        order_id = db_order.get('order_id')
        created_at = db_order.get('created_at', '')
        exchange_order_id = db_order.get('exchange_order_id')
        
        try:
            # Parse creation time
            created_time = datetime.fromisoformat(created_at.replace('Z', '+00:00').replace('+00:00', ''))
            time_diff = datetime.utcnow() - created_time
            
            # If order is older than the configured timeout threshold, directly query the exchange for final status
            if time_diff.total_seconds() > self.timeout_threshold:
                logger.info(f"🔍 Order {order_id} missing on list; fetching by id {exchange_order_id} from {exchange}")
                try:
                    async with httpx.AsyncClient(timeout=30.0) as client:
                        # Attempt exact fetch
                        detail_resp = await client.get(f"{exchange_service_url}/api/v1/trading/order/{exchange}/{exchange_order_id}")
                        if detail_resp.status_code == 200:
                            ex_order = detail_resp.json().get('order', {})
                            ex_status = str(ex_order.get('status', '')).lower()
                            status_mapping = {
                                'open': 'pending',
                                'new': 'pending',
                                'filled': 'filled',
                                'closed': 'filled',
                                'canceled': 'cancelled',
                                'cancelled': 'cancelled',
                                'expired': 'cancelled',
                                'rejected': 'rejected'
                            }
                            mapped = status_mapping.get(ex_status, 'cancelled')
                            update_data = OrderStatusUpdate(
                                order_id=order_id,
                                status=mapped,
                                updated_at=datetime.utcnow().isoformat() + 'Z',
                                cancellation_reason='Resolved via direct exchange fetch for missing order'
                            )
                            # Include fill data if available
                            try:
                                fa = float(ex_order.get('filled', 0))
                                if fa > 0:
                                    update_data.filled_amount = fa
                                    avg = ex_order.get('average')
                                    update_data.filled_price = float(avg) if avg else None
                            except Exception:
                                pass
                            return await self.update_database_order(update_data)
                except Exception as fetch_err:
                    logger.warning(f"⚠️ Exchange fetch by id failed for {exchange_order_id}: {fetch_err}")
                # Fallback if fetch fails
                update_data = OrderStatusUpdate(
                    order_id=order_id,
                    status='cancelled',
                    updated_at=datetime.utcnow().isoformat() + 'Z',
                    cancellation_reason='Order not found on exchange after timeout period (treated as cancelled)'
                )
                return await self.update_database_order(update_data)
        
        except Exception as e:
            logger.error(f"❌ Error handling missing order {order_id}: {e}")
        
        return False
    
    async def update_database_order(self, update_data: OrderStatusUpdate) -> bool:
        """Update order in database"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.put(
                    f"{database_service_url}/api/v1/orders/{update_data.order_id}",
                    json=update_data.dict(exclude_none=True)
                )
                
                if response.status_code == 200:
                    logger.info(f"✅ Updated order {update_data.order_id} status to {update_data.status}")
                    return True
                else:
                    logger.error(f"❌ Failed to update order {update_data.order_id}: {response.status_code}")
                    return False
                    
        except Exception as e:
            logger.error(f"❌ Database update error for order {update_data.order_id}: {e}")
            return False
    
    async def check_timeout_orders(self):
        """Check for orders that should be cancelled due to timeout"""
        logger.info("⏰ Checking for timeout orders...")
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Get all pending orders older than timeout threshold
            response = await client.get(
                f"{database_service_url}/api/v1/orders",
                params={"status": "pending"}
            )
            
            if response.status_code != 200:
                logger.error(f"❌ Failed to get pending orders: {response.status_code}")
                return
            
            orders = response.json().get('orders', [])
            timeout_threshold = datetime.utcnow() - timedelta(seconds=self.timeout_threshold)
            
            timeout_orders = []
            for order in orders:
                try:
                    created_at = datetime.fromisoformat(order.get('created_at', '').replace('Z', '+00:00').replace('+00:00', ''))
                    if created_at < timeout_threshold:
                        timeout_orders.append(order)
                except Exception as e:
                    logger.error(f"❌ Error parsing order creation time: {e}")
            
            logger.info(f"⏰ Found {len(timeout_orders)} orders past timeout threshold")
            
            # Cancel timeout orders
            for order in timeout_orders:
                await self.cancel_timeout_order(order)
    
    async def cancel_timeout_order(self, order: Dict[str, Any]):
        """Cancel an order that has timed out with retry logic"""
        order_id = order.get('order_id')
        exchange = order.get('exchange')
        exchange_order_id = order.get('exchange_order_id')
        
        logger.info(f"⏰ Cancelling timeout order {order_id} on {exchange}")
        
        cancellation_success = False
        
        try:
            # Try to cancel on exchange with retry logic
            if exchange_order_id:
                for attempt in range(self.max_cancellation_retries):
                    try:
                        async with httpx.AsyncClient(timeout=30.0) as client:
                            cancel_response = await client.delete(
                                f"{exchange_service_url}/api/v1/trading/order/{exchange}/{exchange_order_id}"
                            )
                            
                            if cancel_response.status_code == 200:
                                cancel_result = cancel_response.json()
                                result_status = cancel_result.get('status', 'cancelled')
                                
                                if result_status == 'already_processed':
                                    logger.info(f"📋 Order {exchange_order_id} on {exchange} was already filled/expired (attempt {attempt + 1})")
                                    # Order was filled - need to check if we should mark as filled instead of cancelled
                                    cancellation_success = True
                                    break
                                else:
                                    logger.info(f"✅ Cancelled order {exchange_order_id} on {exchange} (attempt {attempt + 1})")
                                    cancellation_success = True
                                    break
                            else:
                                logger.warning(f"⚠️  Failed to cancel order on exchange (attempt {attempt + 1}): {cancel_response.status_code}")
                                
                                if attempt < self.max_cancellation_retries - 1:
                                    logger.info(f"🔄 Retrying cancellation in {self.cancellation_retry_delay} seconds...")
                                    await asyncio.sleep(self.cancellation_retry_delay)
                                    
                    except Exception as cancel_error:
                        logger.error(f"❌ Cancellation attempt {attempt + 1} failed: {cancel_error}")
                        if attempt < self.max_cancellation_retries - 1:
                            await asyncio.sleep(self.cancellation_retry_delay)
            
            # CRITICAL: If cancellation failed, verify current order status before marking as cancelled
            if not cancellation_success:
                logger.warning(f"⚠️  Cancellation failed for {exchange_order_id}, verifying current status on exchange...")
                
                try:
                    async with httpx.AsyncClient(timeout=30.0) as client:
                        # Get current order status from exchange
                        status_response = await client.get(f"{exchange_service_url}/api/v1/trading/orders/{exchange}")
                        if status_response.status_code == 200:
                            exchange_orders = status_response.json().get('orders', [])
                            current_order = None
                            
                            for ex_order in exchange_orders:
                                if ex_order.get('id') == exchange_order_id:
                                    current_order = ex_order
                                    break
                            
                            if current_order:
                                # Order still exists on exchange - check if it's filled
                                ex_status = current_order.get('status', '').lower()
                                if ex_status in ['filled', 'closed', 'complete']:
                                    logger.info(f"🎉 Order {exchange_order_id} was FILLED on exchange during cancellation attempt!")
                                    
                                    update_data = OrderStatusUpdate(
                                        order_id=order_id,
                                        status='filled',
                                        filled_amount=float(current_order.get('filled', 0)),
                                        filled_price=float(current_order.get('average', 0)) if current_order.get('average') else None,
                                        updated_at=datetime.utcnow().isoformat() + 'Z',
                                        sync_source='timeout_verification',
                                        cancellation_reason='Order filled during timeout cancellation attempt'
                                    )
                                    
                                    # Add fees if available
                                    if 'fee' in current_order and current_order['fee']:
                                        update_data.fees = current_order['fee'].get('cost', 0)
                                        update_data.fee_rate = current_order['fee'].get('rate', 0)
                                    
                                    await self.update_database_order(update_data)
                                    logger.info(f"✅ Updated order {order_id} status to FILLED based on exchange verification")
                                    return  # Exit early - order was filled, not cancelled
                                else:
                                    logger.info(f"📍 Order {exchange_order_id} still pending on exchange, marking as cancelled in database")
                            else:
                                logger.info(f"📍 Order {exchange_order_id} no longer exists on exchange (likely filled or expired)")
                        
                except Exception as verify_error:
                    logger.error(f"❌ Error verifying order status: {verify_error}")
                
            # Update database status with appropriate reason
            cancellation_reason = (
                'Timeout cancellation after 5 minutes - successfully cancelled on exchange' if cancellation_success
                else 'Timeout cancellation after 5 minutes - failed to cancel on exchange but marked as cancelled'
            )
            
            update_data = OrderStatusUpdate(
                order_id=order_id,
                status='cancelled',
                updated_at=datetime.utcnow().isoformat() + 'Z',
                cancellation_reason=cancellation_reason
            )
            
            await self.update_database_order(update_data)
            
            # Log performance impact
            if not cancellation_success:
                logger.warning(f"⚠️  Order {order_id} marked as cancelled but may still be active on {exchange}")
                
        except Exception as e:
            logger.error(f"❌ Error cancelling timeout order {order_id}: {e}")

# Global service instances
sync_service = OrderSyncService()
reconciler_v2 = ReconcilerV2()

# API Endpoints
@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy" if (sync_service.is_running or reconciler_v2.is_running) else "stopped",
        "service": "order-sync-service",
        "version": "2.0.0-phase4",
        "legacy_sync": {
            "is_running": sync_service.is_running,
            "sync_interval": sync_service.sync_interval
        },
        "reconciler_v2": {
            "is_running": reconciler_v2.is_running,
            "reconciler_interval": reconciler_v2.reconciler_interval,
            "metrics": reconciler_v2.metrics
        }
    }

@app.post("/sync/start")
async def start_sync(background_tasks: BackgroundTasks):
    """Start the order synchronization service"""
    if not sync_service.is_running:
        background_tasks.add_task(sync_service.start_sync_loop)
        return {"message": "Order sync service started"}
    return {"message": "Order sync service already running"}

@app.post("/sync/stop")
async def stop_sync():
    """Stop the order synchronization service"""
    sync_service.is_running = False
    return {"message": "Order sync service stopped"}

@app.post("/sync/manual")
async def manual_sync():
    """Manually trigger order synchronization"""
    try:
        await sync_service.sync_all_orders()
        return {"message": "Manual sync completed"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/sync/timeout-check")
async def manual_timeout_check():
    """Manually trigger timeout order check"""
    try:
        await sync_service.check_timeout_orders()
        return {"message": "Timeout check completed"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/sync/status")
async def get_sync_status():
    """Get current sync service status"""
    return {
        "is_running": sync_service.is_running,
        "sync_interval": sync_service.sync_interval,
        "timeout_threshold": sync_service.timeout_threshold,
        "max_retries": sync_service.max_retries,
        "max_cancellation_retries": sync_service.max_cancellation_retries,
        "cancellation_retry_delay": sync_service.cancellation_retry_delay,
        "config_loaded": sync_service.config is not None
    }

@app.get("/performance/metrics")
async def get_performance_metrics():
    """Get comprehensive order performance metrics"""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Get all orders from database
            db_response = await client.get(f"{database_service_url}/api/v1/orders")
            if db_response.status_code != 200:
                raise HTTPException(status_code=500, detail="Failed to get orders from database")
            
            orders = db_response.json().get('orders', [])
            
            # Calculate comprehensive metrics
            total_orders = len(orders)
            
            # Filter by order type
            limit_orders = [o for o in orders if o.get('order_type') == 'limit']
            market_orders = [o for o in orders if o.get('order_type') == 'market']
            
            # Calculate status breakdown
            def get_status_metrics(order_list):
                filled = [o for o in order_list if o.get('status') == 'filled']
                pending = [o for o in order_list if o.get('status') == 'pending']
                cancelled = [o for o in order_list if o.get('status') == 'cancelled']
                failed = [o for o in order_list if o.get('status') == 'failed']
                expired = [o for o in order_list if o.get('status') == 'expired']
                
                total = len(order_list)
                success_rate = (len(filled) / total * 100) if total > 0 else 0
                
                return {
                    'total': total,
                    'filled': len(filled),
                    'pending': len(pending),
                    'cancelled': len(cancelled),
                    'failed': len(failed),
                    'expired': len(expired),
                    'success_rate': round(success_rate, 2)
                }
            
            limit_metrics = get_status_metrics(limit_orders)
            market_metrics = get_status_metrics(market_orders)
            
            # Exchange breakdown
            exchange_metrics = {}
            exchanges = ['cryptocom', 'binance', 'bybit']
            for exchange in exchanges:
                exchange_orders = [o for o in orders if o.get('exchange') == exchange]
                exchange_metrics[exchange] = get_status_metrics(exchange_orders)
            
            # Time-based analysis
            from datetime import datetime, timedelta
            now = datetime.utcnow()
            
            # Recent orders (last 24 hours)
            recent_orders = []
            for order in orders:
                try:
                    created_at = datetime.fromisoformat(order.get('created_at', '').replace('Z', ''))
                    if (now - created_at).total_seconds() < 86400:  # 24 hours
                        recent_orders.append(order)
                except:
                    pass
            
            recent_metrics = get_status_metrics(recent_orders)
            
            # Performance alerts
            alerts = []
            config = sync_service.config or {}
            order_mgmt = config.get('trading', {}).get('order_management', {})
            
            low_threshold = order_mgmt.get('low_success_rate_threshold', 0.20)
            high_timeout_threshold = order_mgmt.get('high_timeout_rate_threshold', 0.60)
            
            if limit_metrics['success_rate'] < low_threshold * 100:
                alerts.append({
                    'type': 'low_success_rate',
                    'message': f"Limit order success rate ({limit_metrics['success_rate']}%) below threshold ({low_threshold * 100}%)",
                    'severity': 'warning'
                })
            
            timeout_rate = (limit_metrics['cancelled'] + limit_metrics['expired']) / max(limit_metrics['total'], 1) * 100
            if timeout_rate > high_timeout_threshold * 100:
                alerts.append({
                    'type': 'high_timeout_rate',
                    'message': f"Order timeout rate ({timeout_rate:.1f}%) above threshold ({high_timeout_threshold * 100}%)",
                    'severity': 'warning'
                })
            
            return {
                'timestamp': now.isoformat() + 'Z',
                'total_orders': total_orders,
                'limit_orders': limit_metrics,
                'market_orders': market_metrics,
                'exchange_breakdown': exchange_metrics,
                'recent_24h': recent_metrics,
                'alerts': alerts,
                'sync_service_status': {
                    'is_running': sync_service.is_running,
                    'timeout_threshold': sync_service.timeout_threshold,
                    'sync_interval': sync_service.sync_interval
                }
            }
            
    except Exception as e:
        logger.error(f"❌ Error calculating performance metrics: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Phase 4: Reconciler v2 Endpoints
@app.post("/reconciler/start")
async def start_reconciler_v2(background_tasks: BackgroundTasks):
    """Start Reconciler v2 service"""
    if not reconciler_v2.is_running:
        background_tasks.add_task(reconciler_v2.start_reconciler_loop)
        return {"message": "Reconciler v2 started", "version": "phase4"}
    return {"message": "Reconciler v2 already running"}

@app.post("/reconciler/stop")
async def stop_reconciler_v2():
    """Stop Reconciler v2 service"""
    reconciler_v2.is_running = False
    return {"message": "Reconciler v2 stopped"}

@app.post("/reconciler/run")
async def manual_reconciliation():
    """Manually trigger reconciliation cycle"""
    try:
        results = await reconciler_v2.run_reconciliation_cycle()
        return {"message": "Reconciliation completed", "results": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/reconciler/exchange/{exchange}")
async def reconcile_specific_exchange(exchange: str):
    """Manually reconcile a specific exchange"""
    try:
        result = await reconciler_v2.reconcile_exchange(exchange)
        return {"message": f"Reconciliation completed for {exchange}", "result": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/reconciler/status")
async def get_reconciler_status():
    """Get Reconciler v2 status and metrics"""
    return {
        "is_running": reconciler_v2.is_running,
        "reconciler_interval": reconciler_v2.reconciler_interval,
        "metrics": reconciler_v2.metrics,
        "version": "phase4"
    }

@app.get("/reconciler/metrics")
async def get_reconciler_metrics():
    """Get detailed reconciler metrics"""
    return {
        "timestamp": datetime.utcnow().isoformat(),
        "metrics": reconciler_v2.metrics,
        "service_uptime": {
            "reconciler_v2_running": reconciler_v2.is_running,
            "legacy_sync_running": sync_service.is_running
        }
    }

# Startup event
@app.on_event("startup")
async def startup_event():
    """Start services on startup"""
    logger.info("🚀 Order Sync Service (Phase 4) starting up...")
    
    # Start Reconciler v2 (primary service for Phase 4)
    asyncio.create_task(reconciler_v2.start_reconciler_loop())
    
    # Keep legacy sync service for backward compatibility (but don't auto-start)
    logger.info("✅ Reconciler v2 started, legacy sync available on demand")

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8008,
        reload=True,
        log_level="info"
    )