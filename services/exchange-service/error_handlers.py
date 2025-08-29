#!/usr/bin/env python3
"""
Error Handlers for WebSocket Services - Version 2.5.0
Comprehensive error handling, logging, and recovery strategies
"""

import asyncio
import logging
import traceback
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Callable, Union
from enum import Enum
import json
import aiohttp

logger = logging.getLogger(__name__)

class ErrorSeverity(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class ErrorCategory(Enum):
    NETWORK = "network"
    AUTHENTICATION = "authentication"
    RATE_LIMIT = "rate_limit"
    DATA_FORMAT = "data_format"
    API_ERROR = "api_error"
    SYSTEM = "system"
    UNKNOWN = "unknown"

class ErrorAction(Enum):
    IGNORE = "ignore"
    RETRY = "retry"
    RECONNECT = "reconnect"
    ALERT = "alert"
    FALLBACK = "fallback"
    SHUTDOWN = "shutdown"

class WebSocketError:
    """Represents a WebSocket-related error with context"""
    
    def __init__(
        self,
        error: Exception,
        service: str,
        context: Optional[Dict[str, Any]] = None,
        category: Optional[ErrorCategory] = None,
        severity: Optional[ErrorSeverity] = None
    ):
        self.error = error
        self.service = service
        self.context = context or {}
        self.category = category or self._classify_error(error)
        self.severity = severity or self._determine_severity(error, self.category)
        self.timestamp = datetime.utcnow()
        self.error_id = f"{self.service}_{self.timestamp.strftime('%Y%m%d_%H%M%S')}_{id(self)}"
        
    def _classify_error(self, error: Exception) -> ErrorCategory:
        """Classify error based on exception type and message"""
        error_str = str(error).lower()
        
        # Network-related errors
        if any(keyword in error_str for keyword in [
            'connection', 'timeout', 'unreachable', 'network', 'dns', 'socket'
        ]):
            return ErrorCategory.NETWORK
        
        # Authentication errors
        if any(keyword in error_str for keyword in [
            'authentication', 'unauthorized', 'invalid key', 'permission'
        ]):
            return ErrorCategory.AUTHENTICATION
        
        # Rate limiting
        if any(keyword in error_str for keyword in [
            'rate limit', 'too many requests', '429', 'exceeded'
        ]):
            return ErrorCategory.RATE_LIMIT
        
        # Data format errors
        if any(keyword in error_str for keyword in [
            'json', 'format', 'parse', 'invalid data', 'decode'
        ]):
            return ErrorCategory.DATA_FORMAT
        
        # API errors
        if any(keyword in error_str for keyword in [
            'api error', 'bad request', '400', '500', 'server error'
        ]):
            return ErrorCategory.API_ERROR
        
        return ErrorCategory.UNKNOWN
    
    def _determine_severity(self, error: Exception, category: ErrorCategory) -> ErrorSeverity:
        """Determine error severity"""
        if category == ErrorCategory.AUTHENTICATION:
            return ErrorSeverity.CRITICAL
        
        if category == ErrorCategory.RATE_LIMIT:
            return ErrorSeverity.MEDIUM
        
        if category == ErrorCategory.NETWORK:
            return ErrorSeverity.HIGH
        
        if category == ErrorCategory.DATA_FORMAT:
            return ErrorSeverity.LOW
        
        return ErrorSeverity.MEDIUM
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert error to dictionary for logging/storage"""
        return {
            'error_id': self.error_id,
            'service': self.service,
            'timestamp': self.timestamp.isoformat(),
            'category': self.category.value,
            'severity': self.severity.value,
            'error_type': type(self.error).__name__,
            'error_message': str(self.error),
            'context': self.context,
            'traceback': traceback.format_exception(type(self.error), self.error, self.error.__traceback__)
        }

class ErrorHandler:
    """
    Comprehensive error handler with recovery strategies
    """
    
    def __init__(self, service_name: str, alert_webhook_url: Optional[str] = None):
        self.service_name = service_name
        self.alert_webhook_url = alert_webhook_url
        
        # Error tracking
        self.error_count = 0
        self.errors_by_category: Dict[ErrorCategory, int] = {cat: 0 for cat in ErrorCategory}
        self.errors_by_severity: Dict[ErrorSeverity, int] = {sev: 0 for sev in ErrorSeverity}
        self.recent_errors: List[WebSocketError] = []
        self.max_recent_errors = 100
        
        # Recovery strategies
        self.recovery_strategies: Dict[ErrorCategory, List[ErrorAction]] = {
            ErrorCategory.NETWORK: [ErrorAction.RETRY, ErrorAction.RECONNECT],
            ErrorCategory.AUTHENTICATION: [ErrorAction.ALERT, ErrorAction.SHUTDOWN],
            ErrorCategory.RATE_LIMIT: [ErrorAction.RETRY],
            ErrorCategory.DATA_FORMAT: [ErrorAction.IGNORE],
            ErrorCategory.API_ERROR: [ErrorAction.RETRY, ErrorAction.FALLBACK],
            ErrorCategory.SYSTEM: [ErrorAction.ALERT, ErrorAction.RECONNECT],
            ErrorCategory.UNKNOWN: [ErrorAction.RETRY]
        }
        
        # Error callbacks
        self.error_callbacks: Dict[ErrorCategory, List[Callable]] = {cat: [] for cat in ErrorCategory}
        self.severity_callbacks: Dict[ErrorSeverity, List[Callable]] = {sev: [] for sev in ErrorSeverity}
        
        # Rate limiting for alerts
        self.alert_rate_limit = 60  # seconds between similar alerts
        self.last_alerts: Dict[str, datetime] = {}
        
        logger.info(f"🛡️ ErrorHandler initialized for {service_name}")
    
    def add_error_callback(self, category: ErrorCategory, callback: Callable):
        """Add callback for specific error category"""
        self.error_callbacks[category].append(callback)
    
    def add_severity_callback(self, severity: ErrorSeverity, callback: Callable):
        """Add callback for specific error severity"""
        self.severity_callbacks[severity].append(callback)
    
    async def handle_error(
        self,
        error: Exception,
        context: Optional[Dict[str, Any]] = None,
        category: Optional[ErrorCategory] = None,
        severity: Optional[ErrorSeverity] = None
    ) -> List[ErrorAction]:
        """
        Handle an error and return recommended actions
        """
        # Create WebSocket error object
        ws_error = WebSocketError(error, self.service_name, context, category, severity)
        
        # Track error statistics
        self.error_count += 1
        self.errors_by_category[ws_error.category] += 1
        self.errors_by_severity[ws_error.severity] += 1
        
        # Add to recent errors (with rotation)
        self.recent_errors.append(ws_error)
        if len(self.recent_errors) > self.max_recent_errors:
            self.recent_errors.pop(0)
        
        # Log error
        await self._log_error(ws_error)
        
        # Send alerts if necessary
        await self._send_alert_if_needed(ws_error)
        
        # Execute callbacks
        await self._execute_callbacks(ws_error)
        
        # Determine recovery actions
        recovery_actions = self.recovery_strategies.get(ws_error.category, [ErrorAction.RETRY])
        
        logger.info(f"🔧 Error recovery actions for {ws_error.error_id}: {[a.value for a in recovery_actions]}")
        
        return recovery_actions
    
    async def _log_error(self, ws_error: WebSocketError):
        """Log error with appropriate level based on severity"""
        error_dict = ws_error.to_dict()
        
        if ws_error.severity == ErrorSeverity.CRITICAL:
            logger.critical(f"🚨 CRITICAL ERROR in {ws_error.service}: {ws_error.error}")
        elif ws_error.severity == ErrorSeverity.HIGH:
            logger.error(f"❌ HIGH severity error in {ws_error.service}: {ws_error.error}")
        elif ws_error.severity == ErrorSeverity.MEDIUM:
            logger.warning(f"⚠️ MEDIUM severity error in {ws_error.service}: {ws_error.error}")
        else:
            logger.info(f"ℹ️ LOW severity error in {ws_error.service}: {ws_error.error}")
        
        # Log full context for debugging
        logger.debug(f"🐛 Error details: {json.dumps(error_dict, indent=2)}")
    
    async def _send_alert_if_needed(self, ws_error: WebSocketError):
        """Send alert for high severity errors (with rate limiting)"""
        if ws_error.severity not in [ErrorSeverity.HIGH, ErrorSeverity.CRITICAL]:
            return
        
        if not self.alert_webhook_url:
            return
        
        # Rate limit alerts
        alert_key = f"{ws_error.category.value}_{ws_error.severity.value}"
        now = datetime.utcnow()
        
        if alert_key in self.last_alerts:
            time_since_last = (now - self.last_alerts[alert_key]).total_seconds()
            if time_since_last < self.alert_rate_limit:
                logger.debug(f"⏰ Alert rate limited for {alert_key}")
                return
        
        self.last_alerts[alert_key] = now
        
        # Send alert
        try:
            alert_data = {
                'service': ws_error.service,
                'error_id': ws_error.error_id,
                'category': ws_error.category.value,
                'severity': ws_error.severity.value,
                'message': str(ws_error.error),
                'timestamp': ws_error.timestamp.isoformat(),
                'context': ws_error.context
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.alert_webhook_url,
                    json=alert_data,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        logger.info(f"📱 Alert sent for {ws_error.error_id}")
                    else:
                        logger.warning(f"⚠️ Failed to send alert: {response.status}")
                        
        except Exception as e:
            logger.error(f"❌ Error sending alert: {e}")
    
    async def _execute_callbacks(self, ws_error: WebSocketError):
        """Execute registered callbacks for error category and severity"""
        # Category callbacks
        for callback in self.error_callbacks.get(ws_error.category, []):
            try:
                await callback(ws_error)
            except Exception as e:
                logger.error(f"❌ Error in category callback: {e}")
        
        # Severity callbacks
        for callback in self.severity_callbacks.get(ws_error.severity, []):
            try:
                await callback(ws_error)
            except Exception as e:
                logger.error(f"❌ Error in severity callback: {e}")
    
    def get_error_stats(self) -> Dict[str, Any]:
        """Get error statistics"""
        return {
            'service': self.service_name,
            'total_errors': self.error_count,
            'errors_by_category': {cat.value: count for cat, count in self.errors_by_category.items()},
            'errors_by_severity': {sev.value: count for sev, count in self.errors_by_severity.items()},
            'recent_errors_count': len(self.recent_errors),
            'last_error_time': self.recent_errors[-1].timestamp.isoformat() if self.recent_errors else None
        }
    
    def get_recent_errors(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent errors"""
        recent = self.recent_errors[-limit:] if self.recent_errors else []
        return [error.to_dict() for error in recent]
    
    def clear_error_history(self):
        """Clear error history (useful for testing)"""
        self.recent_errors.clear()
        self.error_count = 0
        self.errors_by_category = {cat: 0 for cat in ErrorCategory}
        self.errors_by_severity = {sev: 0 for sev in ErrorSeverity}
        logger.info(f"🧹 Error history cleared for {self.service_name}")

class RecoveryManager:
    """
    Manages error recovery strategies and actions
    """
    
    def __init__(self, service_name: str):
        self.service_name = service_name
        self.recovery_attempts: Dict[str, int] = {}
        self.max_recovery_attempts = 3
        self.recovery_delay = 5.0  # seconds
        
        # Recovery action implementations
        self.action_handlers: Dict[ErrorAction, Callable] = {}
        
    def register_action_handler(self, action: ErrorAction, handler: Callable):
        """Register handler for specific recovery action"""
        self.action_handlers[action] = handler
        logger.info(f"🔧 Registered handler for {action.value} in {self.service_name}")
    
    async def execute_recovery_actions(
        self,
        actions: List[ErrorAction],
        error_context: Dict[str, Any]
    ) -> bool:
        """
        Execute recovery actions in order
        
        Returns:
            bool: True if recovery was successful, False otherwise
        """
        recovery_key = f"{error_context.get('error_id', 'unknown')}"
        
        # Check recovery attempt limit
        attempts = self.recovery_attempts.get(recovery_key, 0)
        if attempts >= self.max_recovery_attempts:
            logger.warning(f"⚠️ Max recovery attempts reached for {recovery_key}")
            return False
        
        self.recovery_attempts[recovery_key] = attempts + 1
        
        logger.info(f"🔧 Executing recovery actions for {recovery_key} (attempt {attempts + 1})")
        
        for action in actions:
            try:
                logger.info(f"🔧 Executing recovery action: {action.value}")
                
                handler = self.action_handlers.get(action)
                if handler:
                    result = await handler(error_context)
                    if result:
                        logger.info(f"✅ Recovery action {action.value} succeeded")
                        # Reset attempt count on success
                        if recovery_key in self.recovery_attempts:
                            del self.recovery_attempts[recovery_key]
                        return True
                    else:
                        logger.warning(f"⚠️ Recovery action {action.value} failed")
                else:
                    logger.warning(f"⚠️ No handler registered for action {action.value}")
                
                # Add delay between recovery actions
                await asyncio.sleep(self.recovery_delay)
                
            except Exception as e:
                logger.error(f"❌ Error executing recovery action {action.value}: {e}")
        
        logger.warning(f"⚠️ All recovery actions failed for {recovery_key}")
        return False
    
    def reset_recovery_attempts(self, error_id: str):
        """Reset recovery attempt count for specific error"""
        if error_id in self.recovery_attempts:
            del self.recovery_attempts[error_id]
            logger.info(f"🔄 Reset recovery attempts for {error_id}")
    
    def get_recovery_stats(self) -> Dict[str, Any]:
        """Get recovery statistics"""
        return {
            'service': self.service_name,
            'active_recoveries': len(self.recovery_attempts),
            'recovery_attempts': dict(self.recovery_attempts),
            'registered_handlers': list(self.action_handlers.keys())
        }

class ErrorAggregator:
    """
    Aggregates errors across multiple services for system-wide monitoring
    """
    
    def __init__(self):
        self.service_errors: Dict[str, ErrorHandler] = {}
        self.global_error_callbacks: List[Callable] = []
        
    def add_service(self, service_name: str, error_handler: ErrorHandler):
        """Add service error handler to aggregation"""
        self.service_errors[service_name] = error_handler
        logger.info(f"📊 Added {service_name} to error aggregation")
    
    def remove_service(self, service_name: str):
        """Remove service from aggregation"""
        if service_name in self.service_errors:
            del self.service_errors[service_name]
            logger.info(f"📊 Removed {service_name} from error aggregation")
    
    def add_global_callback(self, callback: Callable):
        """Add callback for system-wide error events"""
        self.global_error_callbacks.append(callback)
    
    def get_system_health(self) -> Dict[str, Any]:
        """Get system-wide error health"""
        total_errors = 0
        critical_services = []
        error_summary = {}
        
        for service_name, handler in self.service_errors.items():
            stats = handler.get_error_stats()
            total_errors += stats['total_errors']
            error_summary[service_name] = stats
            
            # Check for critical error levels
            critical_count = handler.errors_by_severity.get(ErrorSeverity.CRITICAL, 0)
            if critical_count > 0:
                critical_services.append(service_name)
        
        return {
            'system_healthy': len(critical_services) == 0,
            'total_errors': total_errors,
            'critical_services': critical_services,
            'service_count': len(self.service_errors),
            'error_summary': error_summary,
            'timestamp': datetime.utcnow().isoformat()
        }
    
    def get_error_trends(self, hours: int = 24) -> Dict[str, Any]:
        """Get error trends over specified time period"""
        # This would typically query a time-series database
        # For now, return basic recent error information
        trends = {}
        
        for service_name, handler in self.service_errors.items():
            recent_errors = handler.get_recent_errors(50)  # Get more for trend analysis
            
            # Simple trend calculation based on recent errors
            if recent_errors:
                now = datetime.utcnow()
                cutoff = now - timedelta(hours=hours)
                
                recent_in_window = [
                    error for error in recent_errors
                    if datetime.fromisoformat(error['timestamp']) > cutoff
                ]
                
                trends[service_name] = {
                    'errors_in_window': len(recent_in_window),
                    'error_rate_per_hour': len(recent_in_window) / hours,
                    'most_common_category': self._get_most_common_category(recent_in_window),
                    'severity_distribution': self._get_severity_distribution(recent_in_window)
                }
            else:
                trends[service_name] = {
                    'errors_in_window': 0,
                    'error_rate_per_hour': 0,
                    'most_common_category': None,
                    'severity_distribution': {}
                }
        
        return {
            'time_window_hours': hours,
            'service_trends': trends,
            'timestamp': datetime.utcnow().isoformat()
        }
    
    def _get_most_common_category(self, errors: List[Dict[str, Any]]) -> Optional[str]:
        """Get most common error category from error list"""
        if not errors:
            return None
        
        category_counts = {}
        for error in errors:
            category = error.get('category', 'unknown')
            category_counts[category] = category_counts.get(category, 0) + 1
        
        return max(category_counts, key=category_counts.get) if category_counts else None
    
    def _get_severity_distribution(self, errors: List[Dict[str, Any]]) -> Dict[str, int]:
        """Get severity distribution from error list"""
        distribution = {}
        for error in errors:
            severity = error.get('severity', 'unknown')
            distribution[severity] = distribution.get(severity, 0) + 1
        
        return distribution