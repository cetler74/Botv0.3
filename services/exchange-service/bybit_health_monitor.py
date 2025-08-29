"""
Bybit Health Monitor - Version 2.6.0
Comprehensive health monitoring and alerting for Bybit WebSocket connections
"""

import asyncio
import logging
import time
from typing import Dict, Any, List, Optional, Callable
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

class HealthStatus(Enum):
    """Health status enumeration"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    CRITICAL = "critical"

@dataclass
class HealthMetric:
    """Health metric data structure"""
    name: str
    value: float
    unit: str
    threshold: float
    status: HealthStatus
    timestamp: datetime
    description: str

class BybitHealthMonitor:
    """
    Comprehensive health monitoring for Bybit WebSocket connections
    
    Features:
    - Connection health monitoring
    - Authentication status tracking
    - Event processing performance
    - Error rate monitoring
    - Latency tracking
    - Automatic alerting
    """
    
    def __init__(self, service_name: str = "bybit-websocket"):
        """
        Initialize Bybit health monitor
        
        Args:
            service_name: Name of the service for monitoring
        """
        self.service_name = service_name
        
        # Health metrics storage
        self.metrics: Dict[str, HealthMetric] = {}
        self.health_history: List[Dict[str, Any]] = []
        
        # Monitoring thresholds
        self.thresholds = {
            "connection_uptime": 0.95,  # 95% uptime required
            "authentication_success_rate": 0.99,  # 99% success rate
            "event_processing_latency": 1000,  # 1 second max latency
            "error_rate": 0.05,  # 5% max error rate
            "heartbeat_interval": 30,  # 30 seconds max heartbeat interval
            "message_processing_rate": 10,  # 10 messages per minute minimum
            "recovery_success_rate": 0.8,  # 80% recovery success rate
            "circuit_breaker_trips": 3  # Max 3 circuit breaker trips per hour
        }
        
        # Monitoring state
        self.monitoring_active = False
        self.last_health_check = None
        self.health_check_interval = 30  # seconds
        self.alert_callbacks: List[Callable] = []
        
        # Performance tracking
        self.performance_data = {
            "connection_attempts": 0,
            "successful_connections": 0,
            "failed_connections": 0,
            "authentication_attempts": 0,
            "successful_authentications": 0,
            "failed_authentications": 0,
            "events_processed": 0,
            "events_failed": 0,
            "processing_times": [],
            "error_counts": {},
            "recovery_attempts": 0,
            "successful_recoveries": 0,
            "circuit_breaker_trips": 0
        }
        
        logger.info(f"🏥 Bybit Health Monitor initialized for {service_name}")
    
    async def start_monitoring(self):
        """Start health monitoring"""
        if self.monitoring_active:
            logger.warning("⚠️ Health monitoring already active")
            return
        
        self.monitoring_active = True
        logger.info(f"🏥 Started health monitoring for {self.service_name}")
        
        # Start monitoring loop
        asyncio.create_task(self._monitoring_loop())
    
    async def stop_monitoring(self):
        """Stop health monitoring"""
        self.monitoring_active = False
        logger.info(f"🏥 Stopped health monitoring for {self.service_name}")
    
    async def _monitoring_loop(self):
        """Main monitoring loop"""
        while self.monitoring_active:
            try:
                await self._perform_health_check()
                await asyncio.sleep(self.health_check_interval)
            except Exception as e:
                logger.error(f"❌ Error in health monitoring loop: {e}")
                await asyncio.sleep(5)
    
    async def _perform_health_check(self):
        """Perform comprehensive health check"""
        try:
            # Calculate health metrics
            await self._calculate_connection_health()
            await self._calculate_authentication_health()
            await self._calculate_event_processing_health()
            await self._calculate_error_health()
            await self._calculate_recovery_health()
            
            # Determine overall health status
            overall_status = self._determine_overall_health()
            
            # Store health check result
            health_check = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "overall_status": overall_status.value,
                "metrics": {name: metric.__dict__ for name, metric in self.metrics.items()},
                "performance_data": self.performance_data.copy()
            }
            
            self.health_history.append(health_check)
            self.last_health_check = health_check
            
            # Keep only last 100 health checks
            if len(self.health_history) > 100:
                self.health_history = self.health_history[-100:]
            
            # Check for alerts
            await self._check_alerts(health_check)
            
            logger.debug(f"🏥 Health check completed: {overall_status.value}")
            
        except Exception as e:
            logger.error(f"❌ Error performing health check: {e}")
    
    async def _calculate_connection_health(self):
        """Calculate connection health metrics"""
        total_attempts = self.performance_data["connection_attempts"]
        successful = self.performance_data["successful_connections"]
        
        if total_attempts > 0:
            uptime_ratio = successful / total_attempts
        else:
            uptime_ratio = 1.0
        
        status = self._get_metric_status(uptime_ratio, self.thresholds["connection_uptime"], higher_is_better=True)
        
        self.metrics["connection_uptime"] = HealthMetric(
            name="connection_uptime",
            value=uptime_ratio * 100,
            unit="%",
            threshold=self.thresholds["connection_uptime"] * 100,
            status=status,
            timestamp=datetime.now(timezone.utc),
            description="WebSocket connection uptime ratio"
        )
    
    async def _calculate_authentication_health(self):
        """Calculate authentication health metrics"""
        total_attempts = self.performance_data["authentication_attempts"]
        successful = self.performance_data["successful_authentications"]
        
        if total_attempts > 0:
            success_rate = successful / total_attempts
        else:
            success_rate = 1.0
        
        status = self._get_metric_status(success_rate, self.thresholds["authentication_success_rate"], higher_is_better=True)
        
        self.metrics["authentication_success_rate"] = HealthMetric(
            name="authentication_success_rate",
            value=success_rate * 100,
            unit="%",
            threshold=self.thresholds["authentication_success_rate"] * 100,
            status=status,
            timestamp=datetime.now(timezone.utc),
            description="Authentication success rate"
        )
    
    async def _calculate_event_processing_health(self):
        """Calculate event processing health metrics"""
        # Calculate average processing latency
        if self.performance_data["processing_times"]:
            avg_latency = sum(self.performance_data["processing_times"]) / len(self.performance_data["processing_times"])
        else:
            avg_latency = 0
        
        status = self._get_metric_status(avg_latency, self.thresholds["event_processing_latency"], higher_is_better=False)
        
        self.metrics["event_processing_latency"] = HealthMetric(
            name="event_processing_latency",
            value=avg_latency,
            unit="ms",
            threshold=self.thresholds["event_processing_latency"],
            status=status,
            timestamp=datetime.now(timezone.utc),
            description="Average event processing latency"
        )
        
        # Calculate processing rate (events per minute)
        recent_events = sum(1 for check in self.health_history[-10:] if check.get("performance_data", {}).get("events_processed", 0) > 0)
        processing_rate = recent_events / 10 if self.health_history else 0
        
        status_rate = self._get_metric_status(processing_rate, self.thresholds["message_processing_rate"], higher_is_better=True)
        
        self.metrics["message_processing_rate"] = HealthMetric(
            name="message_processing_rate",
            value=processing_rate,
            unit="events/min",
            threshold=self.thresholds["message_processing_rate"],
            status=status_rate,
            timestamp=datetime.now(timezone.utc),
            description="Event processing rate"
        )
    
    async def _calculate_error_health(self):
        """Calculate error health metrics"""
        total_events = self.performance_data["events_processed"]
        failed_events = self.performance_data["events_failed"]
        
        if total_events > 0:
            error_rate = failed_events / total_events
        else:
            error_rate = 0
        
        status = self._get_metric_status(error_rate, self.thresholds["error_rate"], higher_is_better=False)
        
        self.metrics["error_rate"] = HealthMetric(
            name="error_rate",
            value=error_rate * 100,
            unit="%",
            threshold=self.thresholds["error_rate"] * 100,
            status=status,
            timestamp=datetime.now(timezone.utc),
            description="Event processing error rate"
        )
    
    async def _calculate_recovery_health(self):
        """Calculate recovery health metrics"""
        total_attempts = self.performance_data["recovery_attempts"]
        successful = self.performance_data["successful_recoveries"]
        
        if total_attempts > 0:
            recovery_rate = successful / total_attempts
        else:
            recovery_rate = 1.0
        
        status = self._get_metric_status(recovery_rate, self.thresholds["recovery_success_rate"], higher_is_better=True)
        
        self.metrics["recovery_success_rate"] = HealthMetric(
            name="recovery_success_rate",
            value=recovery_rate * 100,
            unit="%",
            threshold=self.thresholds["recovery_success_rate"] * 100,
            status=status,
            timestamp=datetime.now(timezone.utc),
            description="Recovery success rate"
        )
        
        # Circuit breaker health
        circuit_trips = self.performance_data["circuit_breaker_trips"]
        status_circuit = self._get_metric_status(circuit_trips, self.thresholds["circuit_breaker_trips"], higher_is_better=False)
        
        self.metrics["circuit_breaker_trips"] = HealthMetric(
            name="circuit_breaker_trips",
            value=circuit_trips,
            unit="trips/hour",
            threshold=self.thresholds["circuit_breaker_trips"],
            status=status_circuit,
            timestamp=datetime.now(timezone.utc),
            description="Circuit breaker trips per hour"
        )
    
    def _get_metric_status(self, value: float, threshold: float, higher_is_better: bool) -> HealthStatus:
        """Determine metric status based on threshold"""
        if higher_is_better:
            if value >= threshold:
                return HealthStatus.HEALTHY
            elif value >= threshold * 0.8:
                return HealthStatus.DEGRADED
            elif value >= threshold * 0.5:
                return HealthStatus.UNHEALTHY
            else:
                return HealthStatus.CRITICAL
        else:
            if value <= threshold:
                return HealthStatus.HEALTHY
            elif value <= threshold * 1.2:
                return HealthStatus.DEGRADED
            elif value <= threshold * 2:
                return HealthStatus.UNHEALTHY
            else:
                return HealthStatus.CRITICAL
    
    def _determine_overall_health(self) -> HealthStatus:
        """Determine overall health status"""
        if not self.metrics:
            return HealthStatus.UNHEALTHY
        
        # Count statuses
        status_counts = {
            HealthStatus.HEALTHY: 0,
            HealthStatus.DEGRADED: 0,
            HealthStatus.UNHEALTHY: 0,
            HealthStatus.CRITICAL: 0
        }
        
        for metric in self.metrics.values():
            status_counts[metric.status] += 1
        
        # Determine overall status
        if status_counts[HealthStatus.CRITICAL] > 0:
            return HealthStatus.CRITICAL
        elif status_counts[HealthStatus.UNHEALTHY] > 0:
            return HealthStatus.UNHEALTHY
        elif status_counts[HealthStatus.DEGRADED] > 0:
            return HealthStatus.DEGRADED
        else:
            return HealthStatus.HEALTHY
    
    async def _check_alerts(self, health_check: Dict[str, Any]):
        """Check for alerts and notify callbacks"""
        overall_status = health_check["overall_status"]
        
        # Check for critical or unhealthy status
        if overall_status in ["critical", "unhealthy"]:
            alert = {
                "timestamp": health_check["timestamp"],
                "service": self.service_name,
                "severity": "high" if overall_status == "critical" else "medium",
                "status": overall_status,
                "message": f"Bybit WebSocket health check failed: {overall_status}",
                "metrics": health_check["metrics"]
            }
            
            await self._notify_alert_callbacks(alert)
    
    async def _notify_alert_callbacks(self, alert: Dict[str, Any]):
        """Notify alert callbacks"""
        for callback in self.alert_callbacks:
            try:
                await callback(alert)
            except Exception as e:
                logger.error(f"❌ Error in alert callback: {e}")
    
    def add_alert_callback(self, callback: Callable):
        """Add alert callback"""
        self.alert_callbacks.append(callback)
    
    def record_connection_attempt(self, successful: bool):
        """Record connection attempt"""
        self.performance_data["connection_attempts"] += 1
        if successful:
            self.performance_data["successful_connections"] += 1
        else:
            self.performance_data["failed_connections"] += 1
    
    def record_authentication_attempt(self, successful: bool):
        """Record authentication attempt"""
        self.performance_data["authentication_attempts"] += 1
        if successful:
            self.performance_data["successful_authentications"] += 1
        else:
            self.performance_data["failed_authentications"] += 1
    
    def record_event_processed(self, processing_time: float = None, failed: bool = False):
        """Record event processing"""
        if failed:
            self.performance_data["events_failed"] += 1
        else:
            self.performance_data["events_processed"] += 1
            
        if processing_time is not None:
            self.performance_data["processing_times"].append(processing_time)
            # Keep only last 100 processing times
            if len(self.performance_data["processing_times"]) > 100:
                self.performance_data["processing_times"] = self.performance_data["processing_times"][-100:]
    
    def record_error(self, error_type: str):
        """Record error occurrence"""
        if error_type not in self.performance_data["error_counts"]:
            self.performance_data["error_counts"][error_type] = 0
        self.performance_data["error_counts"][error_type] += 1
    
    def record_recovery_attempt(self, successful: bool):
        """Record recovery attempt"""
        self.performance_data["recovery_attempts"] += 1
        if successful:
            self.performance_data["successful_recoveries"] += 1
    
    def record_circuit_breaker_trip(self):
        """Record circuit breaker trip"""
        self.performance_data["circuit_breaker_trips"] += 1
    
    def get_health_status(self) -> Dict[str, Any]:
        """Get current health status"""
        return {
            "service": self.service_name,
            "monitoring_active": self.monitoring_active,
            "last_health_check": self.last_health_check,
            "current_metrics": {name: metric.__dict__ for name, metric in self.metrics.items()},
            "performance_data": self.performance_data.copy(),
            "thresholds": self.thresholds.copy()
        }
    
    def get_health_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get health check history"""
        return self.health_history[-limit:] if self.health_history else []
    
    def reset_metrics(self):
        """Reset all metrics and performance data"""
        self.metrics = {}
        self.health_history = []
        self.performance_data = {
            "connection_attempts": 0,
            "successful_connections": 0,
            "failed_connections": 0,
            "authentication_attempts": 0,
            "successful_authentications": 0,
            "failed_authentications": 0,
            "events_processed": 0,
            "events_failed": 0,
            "processing_times": [],
            "error_counts": {},
            "recovery_attempts": 0,
            "successful_recoveries": 0,
            "circuit_breaker_trips": 0
        }
        logger.info(f"📊 Health metrics reset for {self.service_name}")
    
    def is_healthy(self) -> bool:
        """Check if service is healthy"""
        if not self.last_health_check:
            return False
        
        overall_status = self.last_health_check.get("overall_status", "unhealthy")
        return overall_status in ["healthy", "degraded"]
