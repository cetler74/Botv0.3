#!/usr/bin/env python3
"""
WebSocket Health Monitor - Permanent Fix
Monitors WebSocket health and automatically restarts connections when issues are detected
Prevents error accumulation that causes system degradation
"""

import asyncio
import aiohttp
import logging
from datetime import datetime, timedelta
import json

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class WebSocketHealthMonitor:
    def __init__(self):
        self.services = {
            "exchange": "http://localhost:8003",
            "orchestrator": "http://localhost:8005"
        }
        
        # Health thresholds
        self.thresholds = {
            "max_errors": 500,  # Max errors before restart
            "max_error_rate": 30,  # Max errors per minute
            "max_unhealthy_duration": 300,  # Max unhealthy duration (5 minutes)
            "heartbeat_timeout": 120  # Max time since last message (2 minutes)
        }
        
        self.last_restart = {}
        self.restart_cooldown = 300  # 5 minutes between restarts
        
    async def monitor_websocket_health(self):
        """Main monitoring loop"""
        logger.info("🔍 WebSocket Health Monitor started")
        
        while True:
            try:
                async with aiohttp.ClientSession() as session:
                    # Monitor Crypto.com WebSocket
                    await self._check_cryptocom_health(session)
                    
                    # Monitor orchestrator WebSocket system
                    await self._check_orchestrator_health(session)
                    
                # Wait before next check
                await asyncio.sleep(30)  # Check every 30 seconds
                
            except Exception as e:
                logger.error(f"❌ Monitor error: {e}")
                await asyncio.sleep(60)
    
    async def _check_cryptocom_health(self, session):
        """Check Crypto.com WebSocket health"""
        try:
            async with session.get(f"{self.services['exchange']}/api/v1/websocket/cryptocom/status") as response:
                if response.status != 200:
                    logger.warning("⚠️ Could not get Crypto.com WebSocket status")
                    return
                
                status = await response.json()
                
                # Check if unhealthy
                is_healthy = status.get('stream_status', {}).get('is_healthy', False)
                is_connected = status.get('user_data_connected', False)
                
                if not is_healthy or not is_connected:
                    logger.warning(f"⚠️ Crypto.com WebSocket unhealthy - healthy: {is_healthy}, connected: {is_connected}")
                    await self._restart_if_needed('cryptocom', session, "unhealthy status")
                    return
                
                # Check error count
                connection_status = status.get('stream_status', {}).get('connection_manager_status', {})
                total_errors = connection_status.get('metrics', {}).get('total_errors', 0)
                
                if total_errors > self.thresholds['max_errors']:
                    logger.warning(f"⚠️ Crypto.com WebSocket has {total_errors} errors (threshold: {self.thresholds['max_errors']})")
                    await self._restart_if_needed('cryptocom', session, f"excessive errors ({total_errors})")
                    return
                
                # Check last message time
                last_message = status.get('stream_status', {}).get('last_message')
                if last_message:
                    try:
                        last_msg_time = datetime.fromisoformat(last_message.replace('Z', '+00:00'))
                        time_since_message = (datetime.now(last_msg_time.tzinfo) - last_msg_time).total_seconds()
                        
                        if time_since_message > self.thresholds['heartbeat_timeout']:
                            logger.warning(f"⚠️ Crypto.com WebSocket no messages for {time_since_message}s")
                            await self._restart_if_needed('cryptocom', session, f"no messages for {time_since_message}s")
                            return
                    except Exception as e:
                        logger.debug(f"Could not parse last message time: {e}")
                
                logger.debug("✅ Crypto.com WebSocket health check passed")
                
        except Exception as e:
            logger.error(f"❌ Error checking Crypto.com WebSocket health: {e}")
    
    async def _check_orchestrator_health(self, session):
        """Check orchestrator WebSocket system health"""
        try:
            async with session.get(f"{self.services['orchestrator']}/api/v1/orders/websocket/status") as response:
                if response.status != 200:
                    logger.warning("⚠️ Could not get orchestrator WebSocket status")
                    return
                
                status = await response.json()
                
                # Check if WebSocket is enabled
                websocket_enabled = status.get('websocket_enabled', False)
                if not websocket_enabled:
                    logger.warning("⚠️ Orchestrator WebSocket tracking disabled")
                    return
                
                # Check connection status
                connections = status.get('connections', {})
                cryptocom_connected = connections.get('cryptocom', {}).get('connected', False)
                
                if not cryptocom_connected:
                    logger.warning("⚠️ Orchestrator not connected to Crypto.com WebSocket")
                    # Try to re-register callbacks
                    await self._reregister_callbacks(session)
                
                logger.debug("✅ Orchestrator WebSocket health check passed")
                
        except Exception as e:
            logger.error(f"❌ Error checking orchestrator WebSocket health: {e}")
    
    async def _restart_if_needed(self, exchange, session, reason):
        """Restart WebSocket if cooldown period has passed"""
        now = datetime.utcnow()
        last_restart_time = self.last_restart.get(exchange)
        
        if last_restart_time:
            time_since_restart = (now - last_restart_time).total_seconds()
            if time_since_restart < self.restart_cooldown:
                logger.info(f"⏰ Restart cooldown active for {exchange} ({time_since_restart:.0f}s < {self.restart_cooldown}s)")
                return
        
        logger.warning(f"🔧 Restarting {exchange} WebSocket due to: {reason}")
        
        try:
            async with session.post(f"{self.services['exchange']}/api/v1/websocket/{exchange}/restart") as response:
                result = await response.json()
                
                if response.status == 200 and result.get('status') == 'restarted':
                    logger.info(f"✅ {exchange} WebSocket restarted successfully")
                    self.last_restart[exchange] = now
                    
                    # Wait and re-register callbacks
                    await asyncio.sleep(10)
                    await self._reregister_callbacks(session)
                else:
                    logger.error(f"❌ Failed to restart {exchange} WebSocket: {result}")
                    
        except Exception as e:
            logger.error(f"❌ Error restarting {exchange} WebSocket: {e}")
    
    async def _reregister_callbacks(self, session):
        """Re-register WebSocket callbacks"""
        try:
            async with session.post(f"{self.services['orchestrator']}/api/v1/websocket/register") as response:
                if response.status == 200:
                    result = await response.json()
                    logger.info("✅ WebSocket callbacks re-registered")
                else:
                    logger.warning(f"⚠️ Failed to re-register callbacks: {response.status}")
        except Exception as e:
            logger.error(f"❌ Error re-registering callbacks: {e}")

async def main():
    """Main monitoring function"""
    monitor = WebSocketHealthMonitor()
    
    logger.info("🚀 Starting WebSocket Health Monitor")
    logger.info(f"📊 Monitoring thresholds:")
    logger.info(f"   - Max errors: {monitor.thresholds['max_errors']}")
    logger.info(f"   - Max error rate: {monitor.thresholds['max_error_rate']}/min")
    logger.info(f"   - Heartbeat timeout: {monitor.thresholds['heartbeat_timeout']}s")
    logger.info(f"   - Restart cooldown: {monitor.restart_cooldown}s")
    
    try:
        await monitor.monitor_websocket_health()
    except KeyboardInterrupt:
        logger.info("🛑 Health monitor stopped")

if __name__ == "__main__":
    asyncio.run(main())