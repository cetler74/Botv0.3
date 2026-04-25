"""
Centralized Trade Closure Service

This is the ONLY place where trades should be closed. All other services
must use this centralized service to ensure consistency and data integrity.

Author: System Architect
Date: 2025-09-08
"""

import asyncio
import logging
from datetime import datetime
from typing import Dict, Any, Optional, List
from decimal import Decimal
import httpx

# DatabaseManager will be passed as a parameter - no import needed

logger = logging.getLogger(__name__)

class TradeClosureService:
    """
    Centralized service for closing trades with complete validation and consistency.
    
    This is the ONLY place where trade status should be changed to 'CLOSED'.
    All other services MUST use this service to close trades.
    """
    
    def __init__(self, db_manager: Any):
        self.db_manager = db_manager
        self.http_client = httpx.AsyncClient(timeout=30.0)
        
    async def close_trade(
        self,
        trade_id: str,
        exit_price: float,
        exit_reason: str,
        exit_time: Optional[datetime] = None,
        exit_order_id: Optional[str] = None,
        fees: Optional[float] = None,
        validated_by_exchange: bool = False,
        additional_data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Close a trade with complete validation and data integrity.
        
        Args:
            trade_id: The trade ID to close
            exit_price: The exit price (REQUIRED)
            exit_reason: Reason for closure (REQUIRED)
            exit_time: Exit timestamp (defaults to now)
            exit_order_id: Exchange order ID for the exit
            fees: Exit fees
            validated_by_exchange: Whether exit was validated by exchange
            additional_data: Any additional data to store
            
        Returns:
            Dict with closure results and calculated PnL
            
        Raises:
            ValueError: If required data is missing or invalid
            RuntimeError: If trade cannot be closed
        """
        
        try:
            # 1. Validate inputs
            if not trade_id:
                raise ValueError("trade_id is required")
            if not exit_price or exit_price <= 0:
                raise ValueError("exit_price must be a positive number")
            if not exit_reason:
                raise ValueError("exit_reason is required")
                
            # 2. Get current trade data
            trade = await self._get_trade_by_id(trade_id)
            if not trade:
                raise RuntimeError(f"Trade {trade_id} not found")
                
            if trade['status'] == 'CLOSED':
                logger.warning(f"Trade {trade_id} is already CLOSED")
                return {
                    "success": False,
                    "reason": "already_closed",
                    "trade_id": trade_id
                }
                
            if trade['status'] not in ['OPEN', 'PENDING']:
                raise RuntimeError(f"Cannot close trade {trade_id} with status {trade['status']}")
                
            # 3. Calculate realized PnL
            pnl_data = await self._calculate_realized_pnl(trade, exit_price, fees or 0)
            
            # 4. Prepare closure data
            closure_time = exit_time or datetime.utcnow()
            
            closure_data = {
                'status': 'CLOSED',
                'exit_price': float(exit_price),
                'exit_time': closure_time.isoformat() + '+00:00' if isinstance(closure_time, datetime) else closure_time,
                'exit_reason': exit_reason,
                'realized_pnl': pnl_data['realized_pnl'],
                'updated_at': datetime.utcnow().isoformat() + '+00:00'
            }
            
            # Add optional fields
            if exit_order_id:
                closure_data['exit_id'] = exit_order_id
            if fees is not None:
                closure_data['fees'] = float(fees)
            if validated_by_exchange:
                closure_data['exchange_validated'] = True
            if additional_data:
                closure_data.update(additional_data)
            
            # 5. Execute the closure with transaction safety
            result = await self._execute_trade_closure(trade_id, closure_data, pnl_data)
            
            # 6. Log the closure
            logger.info(
                f"✅ TRADE CLOSED: {trade['pair']} ({trade_id[:8]}...) "
                f"Entry: ${trade['entry_price']:.4f} → Exit: ${exit_price:.4f} "
                f"PnL: ${pnl_data['realized_pnl']:.2f} ({pnl_data['pnl_percentage']:.2f}%) "
                f"Reason: {exit_reason}"
            )
            
            # 7. Trigger post-closure events
            await self._trigger_post_closure_events(trade_id, trade, closure_data, pnl_data)
            
            return {
                "success": True,
                "trade_id": trade_id,
                "exit_price": exit_price,
                "realized_pnl": pnl_data['realized_pnl'],
                "pnl_percentage": pnl_data['pnl_percentage'],
                "closure_time": closure_time,
                "exit_reason": exit_reason
            }
            
        except Exception as e:
            logger.error(f"❌ Failed to close trade {trade_id}: {e}")
            raise
    
    async def close_trades_by_order_id(
        self, 
        order_id: str, 
        exit_price: float,
        exit_reason: str = "order_filled",
        fees: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        """
        Close all trades associated with a specific order ID.
        Used when an exit order is filled.
        """
        try:
            # Find trades by order ID (could be entry_id or exit_id)
            trades = await self._get_trades_by_order_id(order_id)
            
            if not trades:
                logger.warning(f"No trades found for order_id {order_id}")
                return []
            
            results = []
            for trade in trades:
                try:
                    result = await self.close_trade(
                        trade_id=trade['trade_id'],
                        exit_price=exit_price,
                        exit_reason=exit_reason,
                        exit_order_id=order_id,
                        fees=fees,
                        validated_by_exchange=True
                    )
                    results.append(result)
                except Exception as e:
                    logger.error(f"Failed to close trade {trade['trade_id']} for order {order_id}: {e}")
                    results.append({
                        "success": False,
                        "trade_id": trade['trade_id'],
                        "error": str(e)
                    })
            
            return results
            
        except Exception as e:
            logger.error(f"❌ Failed to close trades by order_id {order_id}: {e}")
            raise
    
    async def emergency_close_trade(
        self,
        trade_id: str,
        exit_reason: str = "emergency_closure",
        use_current_price: bool = True
    ) -> Dict[str, Any]:
        """
        Emergency closure when normal closure process fails.
        Uses current market price or last known price.
        """
        try:
            trade = await self._get_trade_by_id(trade_id)
            if not trade:
                raise RuntimeError(f"Trade {trade_id} not found")
            
            # Determine exit price
            if use_current_price and trade.get('current_price'):
                exit_price = trade['current_price']
            elif trade.get('highest_price'):
                exit_price = trade['highest_price'] 
            else:
                # Fallback to entry price (break-even)
                exit_price = trade['entry_price']
                exit_reason += "_breakeven_fallback"
            
            return await self.close_trade(
                trade_id=trade_id,
                exit_price=exit_price,
                exit_reason=exit_reason,
                validated_by_exchange=False
            )
            
        except Exception as e:
            logger.error(f"❌ Emergency closure failed for trade {trade_id}: {e}")
            raise
    
    # Private helper methods
    
    async def _get_trade_by_id(self, trade_id: str) -> Optional[Dict[str, Any]]:
        """Get trade data by ID"""
        query = """
            SELECT trade_id, pair, exchange, status, entry_price, entry_time, 
                   position_size, current_price, highest_price, fees,
                   entry_id, exit_id, strategy
            FROM trading.trades 
            WHERE trade_id = %s
        """
        return await self.db_manager.execute_single_query(query, (trade_id,))
    
    async def _get_trades_by_order_id(self, order_id: str) -> List[Dict[str, Any]]:
        """Get trades associated with an order ID"""
        query = """
            SELECT trade_id, pair, exchange, status, entry_price, position_size
            FROM trading.trades 
            WHERE (entry_id = %s OR exit_id = %s) AND status IN ('OPEN', 'PENDING')
        """
        result = await self.db_manager.execute_query(query, (order_id, order_id))
        return result if isinstance(result, list) else []
    
    async def _calculate_realized_pnl(
        self,
        trade: Dict[str, Any],
        exit_price: float,
        fees: float
    ) -> Dict[str, Any]:
        """Calculate realized PnL and related metrics.

        PnL-FIX v9 — In simulation mode the round-trip fee is normalized to
        `simulation.fee_rate_per_side * 2 * entry_notional` on every exchange,
        regardless of what (often inconsistent) entry/exit fees the exchange-specific
        simulator stamped on the trade. This guarantees the cash ledger and
        realized_pnl reconcile, and makes per-exchange paper P&L directly
        comparable.
        """
        entry_price = float(trade['entry_price'])
        position_size = float(trade['position_size'])

        gross_pnl = (exit_price - entry_price) * position_size

        sim_mode = False
        sim_fee_rate = 0.0005
        try:
            import os
            cfg_url = os.getenv("CONFIG_SERVICE_URL", "http://config-service:8001")
            async with httpx.AsyncClient(timeout=5.0) as client:
                m = await client.get(f"{cfg_url}/api/v1/config/mode")
                if m.status_code == 200:
                    sim_mode = bool(m.json().get("is_simulation", False))
                if sim_mode:
                    s = await client.get(f"{cfg_url}/api/v1/config/simulation")
                    if s.status_code == 200:
                        sim_fee_rate = float(
                            (s.json() or {}).get("fee_rate_per_side", 0.0005)
                        )
        except Exception as e:
            logger.debug("simulation fee-rate fetch failed: %s", e)

        if sim_mode:
            entry_notional = entry_price * position_size
            exit_notional = exit_price * position_size
            total_fees = (entry_notional + exit_notional) * sim_fee_rate
        else:
            existing_fees = float(trade.get('fees', 0))
            total_fees = existing_fees + (fees or 0)

        net_pnl = gross_pnl - total_fees

        pnl_percentage = ((exit_price - entry_price) / entry_price * 100) if entry_price > 0 else 0

        return {
            'realized_pnl': net_pnl,
            'gross_pnl': gross_pnl,
            'total_fees': total_fees,
            'pnl_percentage': pnl_percentage
        }
    
    async def _execute_trade_closure(
        self, 
        trade_id: str, 
        closure_data: Dict[str, Any],
        pnl_data: Dict[str, Any]
    ) -> bool:
        """Execute the trade closure with transaction safety"""
        
        # Build update query dynamically
        set_clauses = []
        params = []
        
        for key, value in closure_data.items():
            set_clauses.append(f"{key} = %s")
            params.append(value)
        
        # Add total fees if calculated and not already set
        if 'total_fees' in pnl_data and 'fees' not in closure_data:
            set_clauses.append("fees = %s")
            params.append(pnl_data['total_fees'])
        
        params.append(trade_id)
        
        query = f"""
            UPDATE trading.trades 
            SET {', '.join(set_clauses)}
            WHERE trade_id = %s
        """
        
        await self.db_manager.execute_query(query, params)
        
        # Verify the closure worked
        verification = await self._verify_trade_closure(trade_id)
        if not verification:
            raise RuntimeError(f"Trade closure verification failed for {trade_id}")
        
        return True
    
    async def _verify_trade_closure(self, trade_id: str) -> bool:
        """Verify trade was properly closed"""
        query = """
            SELECT status, exit_price, exit_time, realized_pnl 
            FROM trading.trades 
            WHERE trade_id = %s
        """
        result = await self.db_manager.execute_single_query(query, (trade_id,))
        
        if not result:
            return False
        
        # Check all required fields are present
        required_fields = ['exit_price', 'exit_time', 'realized_pnl']
        for field in required_fields:
            if result.get(field) is None:
                logger.error(f"Missing {field} after trade closure for {trade_id}")
                return False
        
        if result['status'] != 'CLOSED':
            logger.error(f"Trade {trade_id} status is {result['status']}, not CLOSED")
            return False
        
        return True
    
    async def _trigger_post_closure_events(
        self, 
        trade_id: str, 
        trade: Dict[str, Any],
        closure_data: Dict[str, Any], 
        pnl_data: Dict[str, Any]
    ):
        """Trigger events after successful trade closure"""
        try:
            # Create closure event for other services
            event_data = {
                'event_type': 'trade_closed',
                'trade_id': trade_id,
                'pair': trade['pair'],
                'exchange': trade['exchange'],
                'entry_price': trade['entry_price'],
                'exit_price': closure_data['exit_price'],
                'realized_pnl': pnl_data['realized_pnl'],
                'exit_reason': closure_data['exit_reason'],
                'timestamp': closure_data['updated_at']
            }
            
            # TODO: Publish to Redis streams or message queue
            # await self._publish_closure_event(event_data)
            
            logger.debug(f"Trade closure event created for {trade_id}")
            
        except Exception as e:
            logger.warning(f"Failed to trigger post-closure events for {trade_id}: {e}")
            # Don't fail the closure if event publishing fails
    
    async def close(self):
        """Clean up resources"""
        await self.http_client.aclose()


# Singleton instance - ONLY way to close trades
_trade_closure_service: Optional[TradeClosureService] = None

def get_trade_closure_service(db_manager: Any) -> TradeClosureService:
    """Get the singleton trade closure service instance"""
    global _trade_closure_service
    if _trade_closure_service is None:
        _trade_closure_service = TradeClosureService(db_manager)
    return _trade_closure_service

# Convenience functions for common operations

async def close_trade(trade_id: str, exit_price: float, exit_reason: str, **kwargs) -> Dict[str, Any]:
    """Convenience function to close a trade"""
    # This would need to get db_manager from context or dependency injection
    from services.database_service.main import db_manager
    service = get_trade_closure_service(db_manager)
    return await service.close_trade(trade_id, exit_price, exit_reason, **kwargs)

async def close_trades_by_order(order_id: str, exit_price: float, **kwargs) -> List[Dict[str, Any]]:
    """Convenience function to close trades by order ID"""
    from services.database_service.main import db_manager
    service = get_trade_closure_service(db_manager)
    return await service.close_trades_by_order_id(order_id, exit_price, **kwargs)
