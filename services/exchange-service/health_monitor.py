#!/usr/bin/env python3
"""
Health Monitor for WebSocket Services - Version 2.5.0
Comprehensive health monitoring and alerting system
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Callable
import json
import aiohttp
from dataclasses import dataclass, asdict
from enum import Enum

logger = logging.getLogger(__name__)

class HealthStatus(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"

@dataclass
class HealthCheck:
    """Individual health check result"""
    name: str
    status: HealthStatus
    response_time_ms: float
    message: str
    details: Dict[str, Any]
    timestamp: datetime
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'name': self.name,
            'status': self.status.value,
            'response_time_ms': self.response_time_ms,
            'message': self.message,
            'details': self.details,
            'timestamp': self.timestamp.isoformat()
        }

@dataclass
class SystemHealth:
    """Overall system health status"""
    status: HealthStatus
    healthy_services: int
    total_services: int
    health_percentage: float
    checks: List[HealthCheck]
    timestamp: datetime
    uptime_seconds: float
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'status': self.status.value,
            'healthy_services': self.healthy_services,
            'total_services': self.total_services,
            'health_percentage': self.health_percentage,
            'checks': [check.to_dict() for check in self.checks],
            'timestamp': self.timestamp.isoformat(),
            'uptime_seconds': self.uptime_seconds
        }

class HealthMonitorService:
    """
    Comprehensive health monitoring service for WebSocket infrastructure
    """
    
    def __init__(self, check_interval: int = 30, alert_webhook_url: Optional[str] = None):
        self.check_interval = check_interval
        self.alert_webhook_url = alert_webhook_url
        self.start_time = datetime.utcnow()
        
        # Health checks registry
        self.health_checks: Dict[str, Callable] = {}
        self.health_history: List[SystemHealth] = []
        self.max_history = 1000  # Keep last 1000 health checks
        
        # Alert management
        self.alert_thresholds = {
            'health_percentage_warning': 80.0,
            'health_percentage_critical': 50.0,
            'response_time_warning': 5000.0,  # 5 seconds
            'response_time_critical': 10000.0  # 10 seconds
        }
        
        self.last_alerts: Dict[str, datetime] = {}
        self.alert_cooldown = 300  # 5 minutes between similar alerts
        
        # Monitoring state
        self.is_monitoring = False
        self.monitoring_task: Optional[asyncio.Task] = None
        
        # Metrics
        self.metrics = {
            'total_checks': 0,
            'healthy_checks': 0,
            'degraded_checks': 0,
            'unhealthy_checks': 0,
            'alerts_sent': 0,
            'average_response_time': 0.0
        }
        
        logger.info("🏥 Health Monitor Service initialized")
    
    def register_health_check(self, name: str, check_func: Callable):
        """Register a health check function"""
        self.health_checks[name] = check_func
        logger.info(f"📊 Registered health check: {name}")
    
    def unregister_health_check(self, name: str):
        """Remove a health check"""
        if name in self.health_checks:
            del self.health_checks[name]
            logger.info(f"📊 Unregistered health check: {name}")
    
    async def start_monitoring(self):
        """Start the health monitoring service"""
        if self.is_monitoring:
            logger.warning("⚠️ Health monitoring already started")
            return
        
        self.is_monitoring = True
        self.monitoring_task = asyncio.create_task(self._monitoring_loop())
        logger.info(f"🚀 Health monitoring started with {self.check_interval}s interval")
    
    async def stop_monitoring(self):
        """Stop the health monitoring service"""
        self.is_monitoring = False
        
        if self.monitoring_task:
            self.monitoring_task.cancel()
            try:
                await self.monitoring_task
            except asyncio.CancelledError:
                pass
            self.monitoring_task = None
        
        logger.info("🛑 Health monitoring stopped")
    
    async def _monitoring_loop(self):
        """Main monitoring loop"""
        while self.is_monitoring:
            try:
                # Perform health checks
                system_health = await self.perform_health_checks()
                
                # Store in history
                self.health_history.append(system_health)
                if len(self.health_history) > self.max_history:
                    self.health_history.pop(0)
                
                # Check for alerts
                await self._check_alerts(system_health)
                
                # Update metrics
                self._update_metrics(system_health)
                
                # Log health status
                self._log_health_status(system_health)
                
                await asyncio.sleep(self.check_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ Error in health monitoring loop: {e}")
                await asyncio.sleep(self.check_interval)
    
    async def perform_health_checks(self) -> SystemHealth:
        """Perform all registered health checks"""
        checks = []
        response_times = []
        
        for name, check_func in self.health_checks.items():
            try:
                start_time = datetime.utcnow()
                result = await check_func()
                end_time = datetime.utcnow()
                
                response_time = (end_time - start_time).total_seconds() * 1000
                response_times.append(response_time)
                
                # Ensure result is a HealthCheck object
                if isinstance(result, HealthCheck):
                    result.response_time_ms = response_time
                    result.timestamp = end_time
                    checks.append(result)
                else:
                    # Convert dict result to HealthCheck
                    if isinstance(result, dict):
                        status = HealthStatus.HEALTHY if result.get('healthy', False) else HealthStatus.UNHEALTHY
                        check = HealthCheck(
                            name=name,
                            status=status,
                            response_time_ms=response_time,
                            message=result.get('message', 'Health check completed'),
                            details=result.get('details', {}),
                            timestamp=end_time
                        )
                        checks.append(check)
                    else:
                        # Simple boolean result
                        status = HealthStatus.HEALTHY if result else HealthStatus.UNHEALTHY
                        check = HealthCheck(
                            name=name,
                            status=status,
                            response_time_ms=response_time,
                            message="Health check completed",
                            details={},
                            timestamp=end_time
                        )
                        checks.append(check)
                        
            except Exception as e:
                logger.error(f"❌ Health check '{name}' failed: {e}")
                checks.append(HealthCheck(
                    name=name,
                    status=HealthStatus.UNHEALTHY,
                    response_time_ms=0,
                    message=f"Health check failed: {str(e)}",
                    details={'error': str(e)},
                    timestamp=datetime.utcnow()
                ))
        
        # Calculate overall health
        healthy_count = sum(1 for check in checks if check.status == HealthStatus.HEALTHY)
        total_count = len(checks)
        health_percentage = (healthy_count / total_count * 100) if total_count > 0 else 100
        
        # Determine overall status
        if health_percentage >= 90:
            overall_status = HealthStatus.HEALTHY
        elif health_percentage >= 70:
            overall_status = HealthStatus.DEGRADED
        else:
            overall_status = HealthStatus.UNHEALTHY
        
        uptime = (datetime.utcnow() - self.start_time).total_seconds()
        
        return SystemHealth(
            status=overall_status,
            healthy_services=healthy_count,
            total_services=total_count,
            health_percentage=health_percentage,
            checks=checks,
            timestamp=datetime.utcnow(),
            uptime_seconds=uptime
        )
    
    async def get_current_health(self) -> SystemHealth:
        """Get current health status (perform checks immediately)"""
        return await self.perform_health_checks()
    
    def get_health_history(self, hours: int = 1) -> List[SystemHealth]:
        """Get health history for the specified number of hours"""
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        return [health for health in self.health_history if health.timestamp >= cutoff]
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get health monitoring metrics"""
        return {
            **self.metrics,
            'monitoring_active': self.is_monitoring,
            'registered_checks': len(self.health_checks),
            'check_interval': self.check_interval,
            'history_size': len(self.health_history),
            'uptime_seconds': (datetime.utcnow() - self.start_time).total_seconds()
        }
    
    async def _check_alerts(self, system_health: SystemHealth):
        """Check if alerts should be sent based on health status"""
        alerts_to_send = []
        
        # Overall health percentage alerts
        health_percentage = system_health.health_percentage
        if health_percentage <= self.alert_thresholds['health_percentage_critical']:
            alerts_to_send.append({
                'type': 'health_critical',
                'message': f"System health critical: {health_percentage:.1f}%",
                'severity': 'critical',
                'data': system_health.to_dict()
            })
        elif health_percentage <= self.alert_thresholds['health_percentage_warning']:
            alerts_to_send.append({
                'type': 'health_warning',
                'message': f"System health degraded: {health_percentage:.1f}%",
                'severity': 'warning',
                'data': system_health.to_dict()
            })
        
        # Individual service response time alerts
        for check in system_health.checks:
            if check.response_time_ms > self.alert_thresholds['response_time_critical']:
                alerts_to_send.append({
                    'type': 'response_time_critical',
                    'message': f"Service {check.name} response time critical: {check.response_time_ms:.1f}ms",
                    'severity': 'critical',
                    'data': check.to_dict()
                })
            elif check.response_time_ms > self.alert_thresholds['response_time_warning']:
                alerts_to_send.append({
                    'type': 'response_time_warning',
                    'message': f"Service {check.name} response time high: {check.response_time_ms:.1f}ms",
                    'severity': 'warning',
                    'data': check.to_dict()
                })
        
        # Send alerts (with cooldown)
        for alert in alerts_to_send:
            await self._send_alert_with_cooldown(alert)
    
    async def _send_alert_with_cooldown(self, alert: Dict[str, Any]):
        """Send alert with cooldown period"""
        alert_key = f"{alert['type']}_{alert.get('data', {}).get('name', 'system')}"
        now = datetime.utcnow()
        
        # Check cooldown
        if alert_key in self.last_alerts:
            time_since_last = (now - self.last_alerts[alert_key]).total_seconds()
            if time_since_last < self.alert_cooldown:
                return
        
        self.last_alerts[alert_key] = now
        
        # Send alert
        await self._send_alert(alert)
        self.metrics['alerts_sent'] += 1
    
    async def _send_alert(self, alert: Dict[str, Any]):
        """Send alert via webhook"""
        if not self.alert_webhook_url:
            logger.warning(f"📱 Alert would be sent but no webhook configured: {alert['message']}")
            return
        
        try:
            alert_payload = {
                **alert,
                'service': 'health-monitor',
                'timestamp': datetime.utcnow().isoformat()
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.alert_webhook_url,
                    json=alert_payload,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        logger.info(f"📱 Alert sent: {alert['message']}")
                    else:
                        logger.warning(f"⚠️ Failed to send alert: {response.status}")
                        
        except Exception as e:
            logger.error(f"❌ Error sending alert: {e}")
    
    def _update_metrics(self, system_health: SystemHealth):
        """Update internal metrics"""
        self.metrics['total_checks'] += 1
        
        if system_health.status == HealthStatus.HEALTHY:
            self.metrics['healthy_checks'] += 1
        elif system_health.status == HealthStatus.DEGRADED:
            self.metrics['degraded_checks'] += 1
        else:
            self.metrics['unhealthy_checks'] += 1
        
        # Update average response time
        if system_health.checks:
            avg_response_time = sum(check.response_time_ms for check in system_health.checks) / len(system_health.checks)
            # Exponential moving average
            if self.metrics['average_response_time'] == 0:
                self.metrics['average_response_time'] = avg_response_time
            else:
                alpha = 0.1  # Smoothing factor
                self.metrics['average_response_time'] = (alpha * avg_response_time) + ((1 - alpha) * self.metrics['average_response_time'])
    
    def _log_health_status(self, system_health: SystemHealth):
        """Log health status at appropriate level"""
        status_emoji = {
            HealthStatus.HEALTHY: "✅",
            HealthStatus.DEGRADED: "⚠️",
            HealthStatus.UNHEALTHY: "❌",
            HealthStatus.UNKNOWN: "❓"
        }
        
        emoji = status_emoji.get(system_health.status, "❓")
        message = (f"{emoji} System Health: {system_health.status.value.upper()} "
                  f"({system_health.health_percentage:.1f}% - "
                  f"{system_health.healthy_services}/{system_health.total_services} services healthy)")
        
        if system_health.status == HealthStatus.HEALTHY:
            logger.debug(message)
        elif system_health.status == HealthStatus.DEGRADED:
            logger.warning(message)
        else:
            logger.error(message)
        
        # Log unhealthy services
        unhealthy_services = [check.name for check in system_health.checks if check.status != HealthStatus.HEALTHY]
        if unhealthy_services:
            logger.warning(f"⚠️ Unhealthy services: {', '.join(unhealthy_services)}")

# Pre-defined health check functions for common services
async def binance_websocket_health_check() -> HealthCheck:
    """Health check for Binance WebSocket integration"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "http://exchange-service:8003/api/v1/websocket/binance/health",
                timeout=aiohttp.ClientTimeout(total=5)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return HealthCheck(
                        name="binance-websocket",
                        status=HealthStatus.HEALTHY,
                        response_time_ms=0,  # Will be set by monitor
                        message="Binance WebSocket integration healthy",
                        details=data,
                        timestamp=datetime.utcnow()
                    )
                else:
                    return HealthCheck(
                        name="binance-websocket",
                        status=HealthStatus.UNHEALTHY,
                        response_time_ms=0,
                        message=f"Binance WebSocket unhealthy: HTTP {response.status}",
                        details={'status_code': response.status},
                        timestamp=datetime.utcnow()
                    )
    except Exception as e:
        return HealthCheck(
            name="binance-websocket",
            status=HealthStatus.UNHEALTHY,
            response_time_ms=0,
            message=f"Binance WebSocket check failed: {str(e)}",
            details={'error': str(e)},
            timestamp=datetime.utcnow()
        )

async def cryptocom_websocket_health_check() -> HealthCheck:
    """Health check for Crypto.com WebSocket integration"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "http://exchange-service:8003/api/v1/websocket/cryptocom/health",
                timeout=aiohttp.ClientTimeout(total=5)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return HealthCheck(
                        name="cryptocom-websocket",
                        status=HealthStatus.HEALTHY,
                        response_time_ms=0,  # Will be set by monitor
                        message="Crypto.com WebSocket integration healthy",
                        details=data,
                        timestamp=datetime.utcnow()
                    )
                else:
                    return HealthCheck(
                        name="cryptocom-websocket",
                        status=HealthStatus.UNHEALTHY,
                        response_time_ms=0,
                        message=f"Crypto.com WebSocket unhealthy: HTTP {response.status}",
                        details={'status_code': response.status},
                        timestamp=datetime.utcnow()
                    )
    except Exception as e:
        return HealthCheck(
            name="cryptocom-websocket",
            status=HealthStatus.UNHEALTHY,
            response_time_ms=0,
            message=f"Crypto.com WebSocket check failed: {str(e)}",
            details={'error': str(e)},
            timestamp=datetime.utcnow()
        )

async def fill_detection_health_check() -> HealthCheck:
    """Health check for Fill Detection Service"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "http://fill-detection-service:8008/health",
                timeout=aiohttp.ClientTimeout(total=5)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return HealthCheck(
                        name="fill-detection-service",
                        status=HealthStatus.HEALTHY,
                        response_time_ms=0,
                        message="Fill Detection Service healthy",
                        details=data,
                        timestamp=datetime.utcnow()
                    )
                else:
                    return HealthCheck(
                        name="fill-detection-service",
                        status=HealthStatus.UNHEALTHY,
                        response_time_ms=0,
                        message=f"Fill Detection Service unhealthy: HTTP {response.status}",
                        details={'status_code': response.status},
                        timestamp=datetime.utcnow()
                    )
    except Exception as e:
        return HealthCheck(
            name="fill-detection-service",
            status=HealthStatus.UNHEALTHY,
            response_time_ms=0,
            message=f"Fill Detection Service check failed: {str(e)}",
            details={'error': str(e)},
            timestamp=datetime.utcnow()
        )

async def redis_health_check() -> HealthCheck:
    """Health check for Redis connection"""
    try:
        import redis.asyncio as redis
        client = redis.Redis(host='redis', port=6379, decode_responses=True)
        
        # Test ping
        await client.ping()
        
        # Test basic operations
        await client.set('health_check', 'test', ex=60)  # Expire in 60 seconds
        result = await client.get('health_check')
        
        await client.close()
        
        if result == 'test':
            return HealthCheck(
                name="redis",
                status=HealthStatus.HEALTHY,
                response_time_ms=0,
                message="Redis connection healthy",
                details={'ping_successful': True, 'set_get_test': True},
                timestamp=datetime.utcnow()
            )
        else:
            return HealthCheck(
                name="redis",
                status=HealthStatus.DEGRADED,
                response_time_ms=0,
                message="Redis ping successful but set/get failed",
                details={'ping_successful': True, 'set_get_test': False},
                timestamp=datetime.utcnow()
            )
    except Exception as e:
        return HealthCheck(
            name="redis",
            status=HealthStatus.UNHEALTHY,
            response_time_ms=0,
            message=f"Redis health check failed: {str(e)}",
            details={'error': str(e)},
            timestamp=datetime.utcnow()
        )