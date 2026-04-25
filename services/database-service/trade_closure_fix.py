"""
Trade Closure Fix - Phase 1 Implementation
Ensures all fill events trigger proper trade status updates
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Callable
from enum import Enum
import json
import uuid

logger = logging.getLogger(__name__)

class TradeClosureEventType(Enum):
    """Types of trade closure events"""
    ORDER_FILLED = "order_filled"
    TRADE_CLOSED = "trade_closed"
    TRADE_CANCELLED = "trade_cancelled"
    TRADE_FAILED = "trade_failed"

class TradeClosureFix:
    """
    Fixed trade closure system that ensures all fill events trigger proper trade status updates
    """
    
    def __init__(self, db_manager, config: Dict[str, Any]):
        self.db_manager = db_manager
        self.config = config
        
        # Event processing
        self.is_running = False
        self.event_queue = asyncio.Queue()
        
        # Callbacks
        self.trade_closure_callbacks: List[Callable] = []
        self.fill_event_callbacks: List[Callable] = []
        
        # Metrics
        self.metrics = {
            'fill_events_processed': 0,
            'trades_closed': 0,
            'trades_cancelled': 0,
            'trades_failed': 0,
            'processing_errors': 0,
            'last_activity': None
        }
        
        logger.info("🔧 Trade Closure Fix initialized")
    
    async def start(self):
        """Start the trade closure system"""
        if self.is_running:
            logger.warning("Trade Closure Fix already running")
            return
        
        self.is_running = True
        logger.info("🚀 Starting Trade Closure Fix")
        
        # Start event processing loop
        asyncio.create_task(self._event_processing_loop())
        
        logger.info("✅ Trade Closure Fix started")
    
    async def stop(self):
        """Stop the trade closure system"""
        if not self.is_running:
            return
        
        self.is_running = False
        logger.info("🛑 Stopping Trade Closure Fix")
        
        # Process remaining events
        while not self.event_queue.empty():
            try:
                event = self.event_queue.get_nowait()
                await self._process_event(event)
            except asyncio.QueueEmpty:
                break
            except Exception as e:
                logger.error(f"❌ Error processing remaining event: {e}")
        
        logger.info("✅ Trade Closure Fix stopped")
    
    async def handle_order_filled_event(self, event_data: Dict[str, Any]):
        """Handle OrderFilled event and trigger trade closure"""
        try:
            logger.info(f"📊 Processing OrderFilled event: {event_data.get('local_order_id')}")
            
            # Create standardized fill event
            fill_event = {
                'event_id': str(uuid.uuid4()),
                'event_type': TradeClosureEventType.ORDER_FILLED.value,
                'local_order_id': event_data.get('local_order_id'),
                'exchange_order_id': event_data.get('exchange_order_id'),
                'exchange': event_data.get('exchange'),
                'symbol': event_data.get('symbol'),
                'side': event_data.get('side'),
                'quantity': event_data.get('quantity', 0),
                'price': event_data.get('price', 0),
                'fee': event_data.get('fee', 0),
                'fully_filled': event_data.get('fully_filled', True),
                'timestamp': event_data.get('timestamp', datetime.utcnow().isoformat()),
                'trade_id': event_data.get('trade_id')
            }
            
            # Add to processing queue
            await self.event_queue.put(fill_event)
            
            self.metrics['fill_events_processed'] += 1
            self.metrics['last_activity'] = datetime.utcnow()
            
        except Exception as e:
            logger.error(f"❌ Error handling OrderFilled event: {e}")
            self.metrics['processing_errors'] += 1
    
    async def _event_processing_loop(self):
        """Main event processing loop"""
        while self.is_running:
            try:
                # Wait for events with timeout
                event = await asyncio.wait_for(self.event_queue.get(), timeout=1.0)
                await self._process_event(event)
                
            except asyncio.TimeoutError:
                # No events, continue
                continue
            except Exception as e:
                logger.error(f"❌ Error in event processing loop: {e}")
                self.metrics['processing_errors'] += 1
                await asyncio.sleep(1)
    
    async def _process_event(self, event: Dict[str, Any]):
        """Process a single event"""
        try:
            event_type = event.get('event_type')
            
            if event_type == TradeClosureEventType.ORDER_FILLED.value:
                await self._process_order_filled_event(event)
            else:
                logger.warning(f"Unknown event type: {event_type}")
        
        except Exception as e:
            logger.error(f"❌ Error processing event: {e}")
            self.metrics['processing_errors'] += 1
    
    async def _process_order_filled_event(self, event: Dict[str, Any]):
        """Process OrderFilled event and close trade if needed"""
        try:
            local_order_id = event['local_order_id']
            side = event.get('side', '').lower()
            fully_filled = event.get('fully_filled', True)
            
            # Only process fully filled orders
            if not fully_filled:
                logger.debug(f"Order {local_order_id} partially filled - not closing trade")
                return
            
            # Find the trade associated with this order
            trade = await self._find_trade_by_order(local_order_id)
            
            if not trade:
                logger.debug(f"No trade found for order {local_order_id}")
                return
            
            trade_id = trade['trade_id']
            current_status = trade['status']
            
            # Only close OPEN trades
            if current_status != 'OPEN':
                logger.debug(f"Trade {trade_id} not OPEN (status: {current_status}) - not closing")
                return
            
            # Determine if this is an entry or exit order
            if side == 'sell':
                # Exit order - close the trade
                await self._close_trade(trade, event)
            elif side == 'buy':
                # Entry order - update trade with entry details
                await self._update_trade_entry(trade, event)
            else:
                logger.warning(f"Unknown order side: {side}")
        
        except Exception as e:
            logger.error(f"❌ Error processing OrderFilled event: {e}")
            self.metrics['processing_errors'] += 1
    
    async def _find_trade_by_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        """Find trade associated with order ID"""
        try:
            # First try to find by entry_id
            query = """
                SELECT trade_id, pair, exchange, entry_price, position_size, status, 
                       entry_id, exit_id, strategy, entry_reason
                FROM trading.trades 
                WHERE entry_id = %s AND status = 'OPEN'
                LIMIT 1
            """
            trade = await self.db_manager.execute_single_query(query, (order_id,))
            
            if trade:
                return trade
            
            # If not found by entry_id, try exit_id
            query = """
                SELECT trade_id, pair, exchange, entry_price, position_size, status, 
                       entry_id, exit_id, strategy, entry_reason
                FROM trading.trades 
                WHERE exit_id = %s AND status = 'OPEN'
                LIMIT 1
            """
            trade = await self.db_manager.execute_single_query(query, (order_id,))
            
            return trade
        
        except Exception as e:
            logger.error(f"❌ Error finding trade by order: {e}")
            return None
    
    async def _close_trade(self, trade: Dict[str, Any], fill_event: Dict[str, Any]):
        """Close trade with fill event data - REFACTORED to use centralized closure"""
        try:
            # Import centralized service
            try:
                from .centralized_trade_closure import get_trade_closure_service
            except ImportError:
                # Handle relative import when running as script in Docker
                from centralized_trade_closure import get_trade_closure_service
            
            trade_id = trade['trade_id']
            # CRITICAL FIX: Use filled_price instead of price for accurate exit price
            exit_price = fill_event.get('filled_price', fill_event.get('price', 0))
            exit_time = fill_event['timestamp']
            fees = fill_event['fee']
            order_id = fill_event['local_order_id']
            
            # Use centralized trade closure service
            closure_service = get_trade_closure_service(self.db_manager)
            
            # Parse exit_time if needed
            if isinstance(exit_time, str):
                try:
                    from datetime import datetime
                    exit_time = datetime.fromisoformat(exit_time.replace('Z', '+00:00'))
                except ValueError:
                    exit_time = datetime.utcnow()
            elif not exit_time:
                exit_time = datetime.utcnow()
            
            result = await closure_service.close_trade(
                trade_id=trade_id,
                exit_price=float(exit_price),
                exit_reason='order_filled_closure_refactored',
                exit_time=exit_time,
                exit_order_id=order_id,
                fees=float(fees),
                validated_by_exchange=True
            )
            
            if result.get('success'):
                logger.info(f"✅ CENTRALIZED CLOSURE: Trade {trade_id} closed - "
                           f"exit_price=${result['exit_price']:.4f}, "
                           f"PnL=${result['realized_pnl']:.2f} ({result['pnl_percentage']:.2f}%)")
            else:
                logger.error(f"❌ Centralized closure failed for {trade_id}: {result.get('reason', 'unknown')}")
                # Fallback to original logic
                entry_price = trade['entry_price']
                position_size = trade['position_size']
                
                if entry_price and position_size:
                    realized_pnl = (exit_price - entry_price) * position_size - fees
                else:
                    realized_pnl = 0
                    logger.warning(f"Missing entry_price or position_size for trade {trade_id}")
                
                # Update trade status to CLOSED (fallback)
                update_query = """
                    UPDATE trading.trades 
                    SET exit_price = %s, exit_time = %s, exit_id = %s,
                        realized_pnl = %s, status = 'CLOSED',
                        exit_reason = 'order_filled_closure_fallback',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE trade_id = %s
                """
                
                await self.db_manager.execute_query(update_query, (
                    exit_price, exit_time, order_id, realized_pnl, trade_id
                ))
                
                logger.info(f"✅ Fallback: Trade {trade_id} closed successfully")
            
            # Create trade closure event
            closure_event = {
                'event_id': str(uuid.uuid4()),
                'event_type': TradeClosureEventType.TRADE_CLOSED.value,
                'trade_id': trade_id,
                'exit_price': exit_price,
                'exit_time': exit_time,
                'realized_pnl': realized_pnl,
                'exit_reason': 'order_filled_closure',
                'timestamp': datetime.utcnow().isoformat()
            }
            
            # Emit trade closure event
            await self._emit_trade_closure_event(closure_event)
            
            self.metrics['trades_closed'] += 1
            self.metrics['last_activity'] = datetime.utcnow()
        
        except Exception as e:
            logger.error(f"❌ Error closing trade: {e}")
            self.metrics['processing_errors'] += 1
    
    async def _update_trade_entry(self, trade: Dict[str, Any], fill_event: Dict[str, Any]):
        """Update trade with entry order details"""
        try:
            trade_id = trade['trade_id']
            entry_price = fill_event['price']
            entry_time = fill_event['timestamp']
            position_size = fill_event['quantity']
            order_id = fill_event['local_order_id']
            
            # Update trade with actual entry details
            update_query = """
                UPDATE trading.trades 
                SET entry_price = %s, 
                    entry_time = %s,
                    position_size = %s,
                    entry_id = %s,
                    status = 'OPEN',
                    updated_at = CURRENT_TIMESTAMP
                WHERE trade_id = %s
            """
            
            await self.db_manager.execute_query(update_query, (
                entry_price, entry_time, position_size, order_id, trade_id
            ))
            
            logger.info(f"✅ Trade {trade_id} updated with entry details")
            
            # Emit fill event
            await self._emit_fill_event(fill_event)
        
        except Exception as e:
            logger.error(f"❌ Error updating trade entry: {e}")
            self.metrics['processing_errors'] += 1
    
    async def _emit_trade_closure_event(self, event: Dict[str, Any]):
        """Emit trade closure event to callbacks"""
        for callback in self.trade_closure_callbacks:
            try:
                await callback(event)
            except Exception as e:
                logger.error(f"❌ Error in trade closure callback: {e}")
    
    async def _emit_fill_event(self, event: Dict[str, Any]):
        """Emit fill event to callbacks"""
        for callback in self.fill_event_callbacks:
            try:
                await callback(event)
            except Exception as e:
                logger.error(f"❌ Error in fill event callback: {e}")
    
    def add_trade_closure_callback(self, callback: Callable):
        """Add trade closure callback"""
        self.trade_closure_callbacks.append(callback)
    
    def add_fill_event_callback(self, callback: Callable):
        """Add fill event callback"""
        self.fill_event_callbacks.append(callback)
    
    def get_status(self) -> Dict[str, Any]:
        """Get system status"""
        return {
            'is_running': self.is_running,
            'queue_size': self.event_queue.qsize(),
            'metrics': self.metrics
        }
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get system metrics"""
        return {
            **self.metrics,
            'queue_size': self.event_queue.qsize(),
            'is_running': self.is_running
        }

class TradeClosureIntegration:
    """
    Integration layer for trade closure with existing database service
    """
    
    def __init__(self, db_manager, config: Dict[str, Any]):
        self.db_manager = db_manager
        self.config = config
        self.trade_closure_fix = TradeClosureFix(db_manager, config)
        
        logger.info("🔧 Trade Closure Integration initialized")
    
    async def start(self):
        """Start the trade closure integration"""
        await self.trade_closure_fix.start()
        logger.info("✅ Trade Closure Integration started")
    
    async def stop(self):
        """Stop the trade closure integration"""
        await self.trade_closure_fix.stop()
        logger.info("✅ Trade Closure Integration stopped")
    
    async def handle_order_filled(self, payload: Dict[str, Any]) -> bool:
        """Handle OrderFilled event from existing database service"""
        try:
            # Process through the trade closure fix
            await self.trade_closure_fix.handle_order_filled_event(payload)
            return True
        
        except Exception as e:
            logger.error(f"❌ Error handling OrderFilled: {e}")
            return False
    
    def get_status(self) -> Dict[str, Any]:
        """Get integration status"""
        return {
            'trade_closure_fix': self.trade_closure_fix.get_status()
        }
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get integration metrics"""
        return {
            'trade_closure_fix': self.trade_closure_fix.get_metrics()
        }
