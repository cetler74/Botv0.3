#!/usr/bin/env python3
"""
Connection Manager for WebSocket Reliability - Version 2.5.0
Manages WebSocket connections with advanced reconnection and error handling
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Callable, List
from enum import Enum
import json

logger = logging.getLogger(__name__)

class ConnectionState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting" 
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    FAILED = "failed"

class ConnectionManager:
    """
    Advanced WebSocket connection manager with exponential backoff,
    circuit breaker pattern, and health monitoring
    """
    
    def __init__(self, service_name: str = "unknown"):
        self.service_name = service_name
        self.state = ConnectionState.DISCONNECTED
        self.connection_attempts = 0
        self.max_connection_attempts = 10
        self.last_connection_time: Optional[datetime] = None
        self.last_disconnect_time: Optional[datetime] = None
        self.consecutive_failures = 0
        self.total_connections = 0
        self.total_disconnections = 0
        
        # Reconnection settings
        self.base_retry_delay = 1.0  # seconds
        self.max_retry_delay = 300.0  # 5 minutes
        self.exponential_base = 2.0
        self.jitter_factor = 0.1
        
        # Circuit breaker settings
        self.circuit_breaker_threshold = 5
        self.circuit_breaker_timeout = 600  # 10 minutes
        self.circuit_breaker_open = False
        self.circuit_breaker_open_time: Optional[datetime] = None
        
        # Health monitoring
        self.last_message_time: Optional[datetime] = None
        self.message_timeout = 300  # 5 minutes without messages = unhealthy
        self.ping_interval = 30  # Send ping every 30 seconds
        self.pong_timeout = 10  # Wait 10 seconds for pong response
        
        # Callbacks
        self.on_connected: List[Callable] = []
        self.on_disconnected: List[Callable] = []
        self.on_error: List[Callable] = []
        self.on_message: List[Callable] = []
        
        # Metrics
        self.metrics = {
            'total_connections': 0,
            'total_disconnections': 0,
            'consecutive_failures': 0,
            'uptime_seconds': 0,
            'messages_received': 0,
            'errors_count': 0,
            'reconnections': 0,
            'circuit_breaker_trips': 0
        }
        
        logger.info(f"🔧 ConnectionManager initialized for {service_name}")
    
    def add_connected_callback(self, callback: Callable):
        """Add callback for connection events"""
        self.on_connected.append(callback)
    
    def add_disconnected_callback(self, callback: Callable):
        """Add callback for disconnection events"""
        self.on_disconnected.append(callback)
    
    def add_error_callback(self, callback: Callable):
        """Add callback for error events"""
        self.on_error.append(callback)
    
    def add_message_callback(self, callback: Callable):
        """Add callback for message events"""
        self.on_message.append(callback)
    
    async def connect(self, connect_func: Callable) -> bool:
        """
        Attempt to establish connection with retry logic and circuit breaker
        
        Args:
            connect_func: Async function that returns connection object or raises exception
        """
        if self.circuit_breaker_open:
            if self._should_attempt_circuit_breaker_reset():
                logger.info(f"🔄 Circuit breaker reset attempted for {self.service_name}")
                self.circuit_breaker_open = False
                self.circuit_breaker_open_time = None
            else:
                logger.warning(f"🚫 Circuit breaker open for {self.service_name}, connection blocked")
                return False
        
        self.state = ConnectionState.CONNECTING
        self.connection_attempts += 1
        
        try:
            logger.info(f"🔌 Connecting to {self.service_name} (attempt {self.connection_attempts})")
            
            # Call the provided connect function
            connection = await connect_func()
            
            # Success
            self.state = ConnectionState.CONNECTED
            self.last_connection_time = datetime.utcnow()
            self.consecutive_failures = 0
            self.total_connections += 1
            self.metrics['total_connections'] += 1
            
            logger.info(f"✅ Connected to {self.service_name} successfully")
            
            # Notify callbacks
            for callback in self.on_connected:
                try:
                    await callback()
                except Exception as e:
                    logger.error(f"❌ Error in connection callback: {e}")
            
            return True
            
        except Exception as e:
            await self._handle_connection_error(e)
            return False
    
    async def disconnect(self, disconnect_func: Optional[Callable] = None):
        """Gracefully disconnect"""
        try:
            if disconnect_func:
                await disconnect_func()
            
            self.state = ConnectionState.DISCONNECTED
            self.last_disconnect_time = datetime.utcnow()
            self.total_disconnections += 1
            self.metrics['total_disconnections'] += 1
            
            logger.info(f"🔌 Disconnected from {self.service_name}")
            
            # Notify callbacks
            for callback in self.on_disconnected:
                try:
                    await callback()
                except Exception as e:
                    logger.error(f"❌ Error in disconnection callback: {e}")
                    
        except Exception as e:
            logger.error(f"❌ Error during disconnect from {self.service_name}: {e}")
    
    async def reconnect_with_backoff(self, connect_func: Callable) -> bool:
        """Reconnect with exponential backoff"""
        if self.state in [ConnectionState.CONNECTING, ConnectionState.RECONNECTING]:
            logger.warning(f"⚠️ Already attempting connection to {self.service_name}")
            return False
        
        self.state = ConnectionState.RECONNECTING
        self.metrics['reconnections'] += 1
        
        for attempt in range(1, self.max_connection_attempts + 1):
            # Calculate delay with exponential backoff and jitter
            delay = min(
                self.base_retry_delay * (self.exponential_base ** (attempt - 1)),
                self.max_retry_delay
            )
            
            # Add jitter to prevent thundering herd
            import random
            jitter = delay * self.jitter_factor * random.random()
            total_delay = delay + jitter
            
            logger.info(f"🔄 Reconnection attempt {attempt}/{self.max_connection_attempts} "
                       f"for {self.service_name} in {total_delay:.1f}s")
            
            await asyncio.sleep(total_delay)
            
            if await self.connect(connect_func):
                return True
            
            logger.warning(f"⚠️ Reconnection attempt {attempt} failed for {self.service_name}")
        
        # All attempts failed
        self.state = ConnectionState.FAILED
        await self._trigger_circuit_breaker()
        
        logger.error(f"❌ All reconnection attempts failed for {self.service_name}")
        return False
    
    def record_message_received(self):
        """Record that a message was received (for health monitoring)"""
        self.last_message_time = datetime.utcnow()
        self.metrics['messages_received'] += 1
    
    def is_healthy(self) -> bool:
        """Check if connection is healthy based on various metrics"""
        if self.state != ConnectionState.CONNECTED:
            return False
        
        if self.circuit_breaker_open:
            return False
        
        # Check message timeout
        if self.last_message_time:
            time_since_message = (datetime.utcnow() - self.last_message_time).total_seconds()
            if time_since_message > self.message_timeout:
                return False
        
        return True
    
    def get_status(self) -> Dict[str, Any]:
        """Get detailed connection status"""
        uptime = 0
        if self.last_connection_time and self.state == ConnectionState.CONNECTED:
            uptime = (datetime.utcnow() - self.last_connection_time).total_seconds()
            self.metrics['uptime_seconds'] = uptime
        
        return {
            'service': self.service_name,
            'state': self.state.value,
            'healthy': self.is_healthy(),
            'connection_attempts': self.connection_attempts,
            'consecutive_failures': self.consecutive_failures,
            'uptime_seconds': uptime,
            'last_connection': self.last_connection_time.isoformat() if self.last_connection_time else None,
            'last_disconnect': self.last_disconnect_time.isoformat() if self.last_disconnect_time else None,
            'last_message': self.last_message_time.isoformat() if self.last_message_time else None,
            'circuit_breaker_open': self.circuit_breaker_open,
            'circuit_breaker_open_since': self.circuit_breaker_open_time.isoformat() if self.circuit_breaker_open_time else None,
            'metrics': self.metrics.copy()
        }
    
    async def _handle_connection_error(self, error: Exception):
        """Handle connection errors with circuit breaker logic"""
        self.consecutive_failures += 1
        self.metrics['errors_count'] += 1
        
        logger.error(f"❌ Connection error for {self.service_name}: {error}")
        
        # Check if we should open circuit breaker
        if self.consecutive_failures >= self.circuit_breaker_threshold:
            await self._trigger_circuit_breaker()
        
        # Notify error callbacks
        for callback in self.on_error:
            try:
                await callback(error)
            except Exception as callback_error:
                logger.error(f"❌ Error in error callback: {callback_error}")
        
        self.state = ConnectionState.DISCONNECTED
    
    async def _trigger_circuit_breaker(self):
        """Trigger circuit breaker to prevent cascading failures"""
        if not self.circuit_breaker_open:
            self.circuit_breaker_open = True
            self.circuit_breaker_open_time = datetime.utcnow()
            self.metrics['circuit_breaker_trips'] += 1
            
            logger.error(f"🚫 Circuit breaker opened for {self.service_name} "
                        f"after {self.consecutive_failures} consecutive failures")
    
    def _should_attempt_circuit_breaker_reset(self) -> bool:
        """Check if enough time has passed to attempt circuit breaker reset"""
        if not self.circuit_breaker_open or not self.circuit_breaker_open_time:
            return False
        
        time_open = (datetime.utcnow() - self.circuit_breaker_open_time).total_seconds()
        return time_open >= self.circuit_breaker_timeout
    
    def reset_failure_count(self):
        """Reset failure count (call after successful operations)"""
        if self.consecutive_failures > 0:
            logger.info(f"🔄 Resetting failure count for {self.service_name}")
            self.consecutive_failures = 0

class HealthMonitor:
    """
    Monitors multiple connections and provides aggregated health status
    """
    
    def __init__(self):
        self.connections: Dict[str, ConnectionManager] = {}
        self.global_health_callbacks: List[Callable] = []
        self.monitoring_task: Optional[asyncio.Task] = None
        self.is_monitoring = False
    
    def add_connection(self, name: str, connection_manager: ConnectionManager):
        """Add a connection to monitor"""
        self.connections[name] = connection_manager
        logger.info(f"📊 Added {name} to health monitoring")
    
    def remove_connection(self, name: str):
        """Remove a connection from monitoring"""
        if name in self.connections:
            del self.connections[name]
            logger.info(f"📊 Removed {name} from health monitoring")
    
    def add_health_callback(self, callback: Callable):
        """Add callback for overall health changes"""
        self.global_health_callbacks.append(callback)
    
    async def start_monitoring(self, check_interval: int = 30):
        """Start continuous health monitoring"""
        if self.is_monitoring:
            logger.warning("⚠️ Health monitoring already started")
            return
        
        self.is_monitoring = True
        self.monitoring_task = asyncio.create_task(self._monitor_health(check_interval))
        logger.info(f"📊 Started health monitoring with {check_interval}s interval")
    
    async def stop_monitoring(self):
        """Stop health monitoring"""
        self.is_monitoring = False
        
        if self.monitoring_task:
            self.monitoring_task.cancel()
            try:
                await self.monitoring_task
            except asyncio.CancelledError:
                pass
            self.monitoring_task = None
        
        logger.info("📊 Stopped health monitoring")
    
    async def _monitor_health(self, check_interval: int):
        """Background health monitoring task"""
        previous_overall_health = True
        
        while self.is_monitoring:
            try:
                current_overall_health = self.is_overall_healthy()
                
                # Check for health state changes
                if previous_overall_health != current_overall_health:
                    logger.info(f"🏥 Overall health changed: {current_overall_health}")
                    
                    # Notify callbacks
                    for callback in self.global_health_callbacks:
                        try:
                            await callback(current_overall_health)
                        except Exception as e:
                            logger.error(f"❌ Error in health callback: {e}")
                    
                    previous_overall_health = current_overall_health
                
                # Log unhealthy connections
                for name, conn in self.connections.items():
                    if not conn.is_healthy():
                        logger.warning(f"⚠️ Connection {name} is unhealthy: {conn.state.value}")
                
                await asyncio.sleep(check_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ Error in health monitoring: {e}")
                await asyncio.sleep(check_interval)
    
    def is_overall_healthy(self) -> bool:
        """Check if all connections are healthy"""
        if not self.connections:
            return True
        
        return all(conn.is_healthy() for conn in self.connections.values())
    
    def get_overall_status(self) -> Dict[str, Any]:
        """Get overall status of all connections"""
        connection_statuses = {}
        healthy_count = 0
        total_count = len(self.connections)
        
        for name, conn in self.connections.items():
            status = conn.get_status()
            connection_statuses[name] = status
            if status['healthy']:
                healthy_count += 1
        
        return {
            'overall_healthy': self.is_overall_healthy(),
            'healthy_connections': healthy_count,
            'total_connections': total_count,
            'health_percentage': (healthy_count / total_count * 100) if total_count > 0 else 0,
            'connections': connection_statuses,
            'monitoring_active': self.is_monitoring
        }