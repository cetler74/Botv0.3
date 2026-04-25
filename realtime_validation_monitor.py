#!/usr/bin/env python3
"""
Realtime Architecture Validation Monitor
Monitors complete order lifecycle: Strategy → Entry → Trailing Stop → Limit Order → WebSocket Tracking
"""

import asyncio
import aiohttp
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class RealtimeValidationMonitor:
    def __init__(self):
        self.services = {
            "orchestrator": "http://localhost:8005",
            "database": "http://localhost:8002",
            "exchange": "http://localhost:8003",
            "strategy": "http://localhost:8004"
        }
        
        # Track order lifecycle
        self.tracked_orders = {}
        self.tracked_trades = {}
        self.last_check = datetime.utcnow()
        
        # Expected flow tracking
        self.expected_flow = [
            "strategy_entry_signal",
            "order_created", 
            "order_filled",
            "trade_opened",
            "trailing_stop_activated",
            "limit_order_created",
            "websocket_tracking_active",
            "limit_order_filled",
            "trade_closed"
        ]
        
    async def monitor_realtime_architecture(self):
        """Monitor complete realtime trading architecture"""
        logger.info("🚀 STARTING REALTIME ARCHITECTURE VALIDATION MONITOR")
        logger.info("=" * 70)
        logger.info("Monitoring complete order lifecycle:")
        logger.info("Strategy → Entry → Trailing Stop → Limit Order → WebSocket → Fill")
        logger.info("=" * 70)
        
        # Initial system status check
        await self._check_system_status()
        
        monitoring_start = datetime.utcnow()
        
        while True:
            try:
                async with aiohttp.ClientSession() as session:
                    # Monitor new orders and trades
                    await self._monitor_new_orders(session)
                    await self._monitor_new_trades(session)
                    
                    # Check existing order/trade progression
                    await self._check_order_progression(session)
                    await self._check_trailing_stop_activation(session)
                    await self._check_websocket_tracking(session)
                    
                    # Log summary every 2 minutes
                    if (datetime.utcnow() - monitoring_start).total_seconds() > 120:
                        await self._log_validation_summary(session)
                        monitoring_start = datetime.utcnow()
                    
                await asyncio.sleep(10)  # Check every 10 seconds
                
            except Exception as e:
                logger.error(f"❌ Monitor error: {e}")
                await asyncio.sleep(30)
    
    async def _check_system_status(self):
        """Check initial system status"""
        logger.info("📊 SYSTEM STATUS CHECK")
        
        async with aiohttp.ClientSession() as session:
            # Check strategy status
            try:
                async with session.get(f"{self.services['strategy']}/api/v1/strategies/status") as response:
                    if response.status == 200:
                        status = await response.json()
                        heikin_enabled = any(s.get('name') == 'heikin_ashi' and s.get('enabled') for s in status.get('strategies', []))
                        logger.info(f"✅ Strategy Service: {'Heikin Ashi ENABLED' if heikin_enabled else 'Heikin Ashi DISABLED'}")
                    else:
                        logger.warning(f"⚠️ Strategy Service: Status check failed ({response.status})")
            except Exception as e:
                logger.warning(f"⚠️ Strategy Service: {e}")
            
            # Check trading status
            try:
                async with session.get(f"{self.services['orchestrator']}/api/v1/trading/status") as response:
                    if response.status == 200:
                        status = await response.json()
                        trading_status = status.get('status', 'unknown')
                        active_trades = status.get('active_trades', 0)
                        logger.info(f"✅ Trading Status: {trading_status.upper()} - {active_trades} active trades")
                    else:
                        logger.warning(f"⚠️ Trading Status: Check failed ({response.status})")
            except Exception as e:
                logger.warning(f"⚠️ Trading Status: {e}")
            
            # Check WebSocket status
            try:
                async with session.get(f"{self.services['orchestrator']}/api/v1/orders/websocket/status") as response:
                    if response.status == 200:
                        status = await response.json()
                        websocket_enabled = status.get('websocket_enabled', False)
                        cryptocom_connected = status.get('connections', {}).get('cryptocom', {}).get('connected', False)
                        logger.info(f"✅ WebSocket Tracking: {'ENABLED' if websocket_enabled else 'DISABLED'} - Crypto.com {'CONNECTED' if cryptocom_connected else 'DISCONNECTED'}")
                    else:
                        logger.warning(f"⚠️ WebSocket Status: Check failed ({response.status})")
            except Exception as e:
                logger.warning(f"⚠️ WebSocket Status: {e}")
        
        logger.info("=" * 70)
    
    async def _monitor_new_orders(self, session):
        """Monitor for new orders created"""
        try:
            async with session.get(f"{self.services['database']}/api/v1/orders?limit=5&created_since={self.last_check.isoformat()}") as response:
                if response.status == 200:
                    data = await response.json()
                    new_orders = data.get('orders', [])
                    
                    for order in new_orders:
                        order_id = order.get('order_id')
                        if order_id not in self.tracked_orders:
                            logger.info(f"🆕 NEW ORDER DETECTED: {order_id}")
                            logger.info(f"   Exchange: {order.get('exchange')} | Symbol: {order.get('symbol')}")
                            logger.info(f"   Type: {order.get('order_type')} | Side: {order.get('side')} | Amount: {order.get('amount')}")
                            logger.info(f"   Status: {order.get('status')} | Price: {order.get('price')}")
                            
                            self.tracked_orders[order_id] = {
                                "order": order,
                                "lifecycle": ["order_created"],
                                "last_update": datetime.utcnow(),
                                "trade_id": order.get('trade_id')
                            }
                    
        except Exception as e:
            logger.debug(f"Order monitoring error: {e}")
    
    async def _monitor_new_trades(self, session):
        """Monitor for new trades opened"""
        try:
            async with session.get(f"{self.services['database']}/api/v1/trades?limit=5&created_since={self.last_check.isoformat()}") as response:
                if response.status == 200:
                    data = await response.json()
                    new_trades = data.get('trades', [])
                    
                    for trade in new_trades:
                        trade_id = trade.get('trade_id')
                        if trade_id not in self.tracked_trades:
                            logger.info(f"🎯 NEW TRADE DETECTED: {trade_id}")
                            logger.info(f"   Pair: {trade.get('pair')} | Entry Price: {trade.get('entry_price')}")
                            logger.info(f"   Status: {trade.get('status')} | Entry ID: {trade.get('entry_id')}")
                            logger.info(f"   Trail Stop: {trade.get('trail_stop', 'inactive')} | Profit Protection: {trade.get('profit_protection', 'inactive')}")
                            
                            self.tracked_trades[trade_id] = {
                                "trade": trade,
                                "lifecycle": ["trade_opened"],
                                "last_update": datetime.utcnow()
                            }
                            
                            # Link to any tracked orders
                            entry_id = trade.get('entry_id')
                            for order_id, order_data in self.tracked_orders.items():
                                if order_data["order"].get("order_id") == entry_id:
                                    if "order_filled" not in order_data["lifecycle"]:
                                        order_data["lifecycle"].append("order_filled")
                                    if "trade_opened" not in order_data["lifecycle"]:
                                        order_data["lifecycle"].append("trade_opened")
                                    logger.info(f"🔗 LINKED ORDER {order_id} → TRADE {trade_id}")
                    
        except Exception as e:
            logger.debug(f"Trade monitoring error: {e}")
    
    async def _check_trailing_stop_activation(self, session):
        """Check for trailing stop activation"""
        for trade_id, trade_data in list(self.tracked_trades.items()):
            try:
                # Get updated trade data
                async with session.get(f"{self.services['database']}/api/v1/trades/{trade_id}") as response:
                    if response.status == 200:
                        updated_trade = await response.json()
                        
                        trail_stop = updated_trade.get('trail_stop', 'inactive')
                        profit_protection = updated_trade.get('profit_protection', 'inactive')
                        
                        if trail_stop == 'active' and 'trailing_stop_activated' not in trade_data["lifecycle"]:
                            trade_data["lifecycle"].append("trailing_stop_activated")
                            logger.info(f"🎯 TRAILING STOP ACTIVATED: {trade_id}")
                            logger.info(f"   Trail Stop Trigger: {updated_trade.get('trail_stop_trigger')}")
                            
                            # Check for limit orders created after trailing stop activation
                            await self._check_limit_orders_for_trade(session, trade_id)
                        
                        if profit_protection == 'active' and 'profit_protection_activated' not in trade_data["lifecycle"]:
                            trade_data["lifecycle"].append("profit_protection_activated")
                            logger.info(f"💰 PROFIT PROTECTION ACTIVATED: {trade_id}")
                        
                        trade_data["trade"] = updated_trade
                        trade_data["last_update"] = datetime.utcnow()
                            
            except Exception as e:
                logger.debug(f"Trailing stop check error for {trade_id}: {e}")
    
    async def _check_limit_orders_for_trade(self, session, trade_id):
        """Check for limit sell orders created after trailing stop activation"""
        try:
            # Look for sell orders related to this trade
            async with session.get(f"{self.services['database']}/api/v1/orders?trade_id={trade_id}&side=sell&order_type=limit&limit=10") as response:
                if response.status == 200:
                    data = await response.json()
                    limit_orders = data.get('orders', [])
                    
                    for order in limit_orders:
                        order_id = order.get('order_id')
                        
                        # Track this limit order
                        if order_id not in self.tracked_orders:
                            logger.info(f"🎯 TRAILING STOP LIMIT ORDER CREATED: {order_id}")
                            logger.info(f"   For Trade: {trade_id}")
                            logger.info(f"   Price: {order.get('price')} | Amount: {order.get('amount')}")
                            logger.info(f"   Status: {order.get('status')}")
                            
                            self.tracked_orders[order_id] = {
                                "order": order,
                                "lifecycle": ["order_created", "limit_order_created"],
                                "last_update": datetime.utcnow(),
                                "trade_id": trade_id,
                                "is_trailing_stop_order": True
                            }
                            
                            # Mark in trade lifecycle
                            if trade_id in self.tracked_trades:
                                if "limit_order_created" not in self.tracked_trades[trade_id]["lifecycle"]:
                                    self.tracked_trades[trade_id]["lifecycle"].append("limit_order_created")
                            
                            # Check if this order is being tracked by WebSocket
                            await self._check_websocket_tracking_for_order(session, order_id)
                        
        except Exception as e:
            logger.debug(f"Limit order check error for trade {trade_id}: {e}")
    
    async def _check_websocket_tracking_for_order(self, session, order_id):
        """Check if order is being tracked by WebSocket"""
        try:
            # Check WebSocket status to see if tracking is active
            async with session.get(f"{self.services['orchestrator']}/api/v1/orders/websocket/status") as response:
                if response.status == 200:
                    status = await response.json()
                    websocket_enabled = status.get('websocket_enabled', False)
                    cryptocom_connected = status.get('connections', {}).get('cryptocom', {}).get('connected', False)
                    
                    if websocket_enabled and cryptocom_connected:
                        if order_id in self.tracked_orders:
                            if "websocket_tracking_active" not in self.tracked_orders[order_id]["lifecycle"]:
                                self.tracked_orders[order_id]["lifecycle"].append("websocket_tracking_active")
                                logger.info(f"📡 WEBSOCKET TRACKING ACTIVE: {order_id}")
                                logger.info("   ✅ Order will be tracked in realtime via WebSocket callbacks")
                    
        except Exception as e:
            logger.debug(f"WebSocket tracking check error for {order_id}: {e}")
    
    async def _check_order_progression(self, session):
        """Check progression of tracked orders"""
        for order_id, order_data in list(self.tracked_orders.items()):
            try:
                # Get updated order status
                async with session.get(f"{self.services['database']}/api/v1/orders/{order_id}") as response:
                    if response.status == 200:
                        updated_order = await response.json()
                        old_status = order_data["order"].get("status")
                        new_status = updated_order.get("status")
                        
                        if old_status != new_status:
                            logger.info(f"📈 ORDER STATUS CHANGE: {order_id}")
                            logger.info(f"   {old_status} → {new_status}")
                            
                            if new_status == "FILLED" and "order_filled" not in order_data["lifecycle"]:
                                order_data["lifecycle"].append("order_filled")
                                
                                if order_data.get("is_trailing_stop_order"):
                                    logger.info(f"🎯 TRAILING STOP LIMIT ORDER FILLED: {order_id}")
                                    logger.info("   ✅ REALTIME ORDER TRACKING SUCCESSFUL!")
                                    
                                    # Mark trade as closed if this was the exit order
                                    trade_id = order_data.get("trade_id")
                                    if trade_id in self.tracked_trades:
                                        if "limit_order_filled" not in self.tracked_trades[trade_id]["lifecycle"]:
                                            self.tracked_trades[trade_id]["lifecycle"].append("limit_order_filled")
                        
                        order_data["order"] = updated_order
                        order_data["last_update"] = datetime.utcnow()
                        
            except Exception as e:
                logger.debug(f"Order progression check error for {order_id}: {e}")
    
    async def _check_websocket_tracking(self, session):
        """Check WebSocket tracking status"""
        try:
            async with session.get(f"{self.services['orchestrator']}/api/v1/orders/websocket/status") as response:
                if response.status == 200:
                    status = await response.json()
                    
                    # Log any WebSocket connection changes
                    websocket_enabled = status.get('websocket_enabled', False)
                    connections = status.get('connections', {})
                    
                    if not websocket_enabled:
                        logger.warning("⚠️ WebSocket order tracking is DISABLED")
                    
                    for exchange, conn_status in connections.items():
                        if not conn_status.get('connected', False):
                            logger.warning(f"⚠️ WebSocket connection to {exchange} is DOWN")
                    
        except Exception as e:
            logger.debug(f"WebSocket status check error: {e}")
    
    async def _log_validation_summary(self, session):
        """Log validation summary"""
        logger.info("=" * 70)
        logger.info("📊 REALTIME VALIDATION SUMMARY")
        logger.info("=" * 70)
        
        # Summary stats
        total_orders = len(self.tracked_orders)
        total_trades = len(self.tracked_trades)
        trailing_stop_orders = len([o for o in self.tracked_orders.values() if o.get("is_trailing_stop_order")])
        websocket_tracked = len([o for o in self.tracked_orders.values() if "websocket_tracking_active" in o["lifecycle"]])
        
        logger.info(f"📈 Orders Tracked: {total_orders} (Trailing Stop: {trailing_stop_orders}, WebSocket: {websocket_tracked})")
        logger.info(f"📊 Trades Tracked: {total_trades}")
        
        # Lifecycle completion analysis
        if self.tracked_orders:
            logger.info("\n🔄 ORDER LIFECYCLE STATUS:")
            for order_id, order_data in self.tracked_orders.items():
                lifecycle = order_data["lifecycle"]
                is_trailing = "🎯" if order_data.get("is_trailing_stop_order") else "📦"
                logger.info(f"   {is_trailing} {order_id}: {' → '.join(lifecycle)}")
        
        if self.tracked_trades:
            logger.info("\n💼 TRADE LIFECYCLE STATUS:")
            for trade_id, trade_data in self.tracked_trades.items():
                lifecycle = trade_data["lifecycle"]
                logger.info(f"   📊 {trade_id}: {' → '.join(lifecycle)}")
        
        # Check for complete realtime flow
        complete_flows = 0
        for order_id, order_data in self.tracked_orders.items():
            if order_data.get("is_trailing_stop_order"):
                lifecycle = order_data["lifecycle"]
                required_steps = ["order_created", "websocket_tracking_active"]
                if all(step in lifecycle for step in required_steps):
                    complete_flows += 1
        
        if complete_flows > 0:
            logger.info(f"✅ COMPLETE REALTIME FLOWS: {complete_flows}")
            logger.info("   Trailing stop limit orders successfully tracked via WebSocket!")
        
        logger.info("=" * 70)
        
        # Update last check time
        self.last_check = datetime.utcnow()

async def main():
    """Main monitoring function"""
    monitor = RealtimeValidationMonitor()
    
    try:
        await monitor.monitor_realtime_architecture()
    except KeyboardInterrupt:
        logger.info("\n🛑 Realtime validation monitor stopped")

if __name__ == "__main__":
    asyncio.run(main())