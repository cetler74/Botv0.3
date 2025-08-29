"""
Bybit Error Handlers - Version 2.6.0
Comprehensive error handling and categorization for Bybit WebSocket connections
"""

import asyncio
import logging
import time
from typing import Dict, Any, List, Optional, Callable
from datetime import datetime, timezone
from enum import Enum

logger = logging.getLogger(__name__)

class ErrorCategory(Enum):
    """Error categories for Bybit WebSocket"""
    AUTHENTICATION = "authentication"
    CONNECTION = "connection"
    NETWORK = "network"
    RATE_LIMIT = "rate_limit"
    SUBSCRIPTION = "subscription"
    HEARTBEAT = "heartbeat"
    PROCESSING = "processing"
    UNKNOWN = "unknown"

class ErrorSeverity(Enum):
    """Error severity levels"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

class BybitErrorHandler:
    """
    Comprehensive error handling for Bybit WebSocket connections
    
    Features:
    - Error categorization and severity assessment
    - Recovery action recommendations
    - Error tracking and metrics
    - Automatic recovery strategies
    - Circuit breaker pattern implementation
    """
    
    def __init__(self, service_name: str = "bybit-websocket"):
        """
        Initialize Bybit error handler
        
        Args:
            service_name: Name of the service for logging
        """
        self.service_name = service_name
        
        # Error tracking
        self.error_counts = {category: 0 for category in ErrorCategory}
        self.error_history: List[Dict[str, Any]] = []
        self.last_error_time = None
        
        # Circuit breaker state
        self.circuit_breaker_state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN
        self.circuit_breaker_threshold = 5
        self.circuit_breaker_timeout = 60  # seconds
        self.circuit_breaker_last_failure = None
        
        # Recovery strategies
        self.recovery_strategies = {
            ErrorCategory.AUTHENTICATION: ["refresh_authentication", "reconnect"],
            ErrorCategory.CONNECTION: ["reconnect", "fallback_to_rest"],
            ErrorCategory.NETWORK: ["retry", "reconnect", "fallback_to_rest"],
            ErrorCategory.RATE_LIMIT: ["backoff", "retry"],
            ErrorCategory.SUBSCRIPTION: ["resubscribe", "validate_channels"],
            ErrorCategory.HEARTBEAT: ["reconnect", "restart_heartbeat"],
            ErrorCategory.PROCESSING: ["retry", "skip_event"],
            ErrorCategory.UNKNOWN: ["log_error", "continue"]
        }
        
        # Error callbacks
        self.error_callbacks: List[Callable] = []
        
        logger.info(f"🛡️ Bybit Error Handler initialized for {service_name}")
    
    async def handle_error(self, error: Exception, context: Dict[str, Any] = None) -> List[str]:
        """
        Handle error and return recommended recovery actions
        
        Args:
            error: The exception that occurred
            context: Additional context about the error
            
        Returns:
            List of recommended recovery actions
        """
        try:
            # Categorize error
            category = self._categorize_error(error)
            severity = self._assess_severity(error, category)
            
            # Update error tracking
            self._update_error_tracking(category, error, context)
            
            # Check circuit breaker
            if self._should_open_circuit_breaker(category, severity):
                await self._open_circuit_breaker()
                return ["circuit_breaker_open", "fallback_to_rest"]
            
            # Get recovery actions
            recovery_actions = self._get_recovery_actions(category, severity)
            
            # Log error
            self._log_error(error, category, severity, context)
            
            # Notify error callbacks
            await self._notify_error_callbacks(error, category, severity, context)
            
            logger.warning(f"🛡️ Error handled: {category.value} ({severity.value}) - Actions: {recovery_actions}")
            return recovery_actions
            
        except Exception as e:
            logger.error(f"❌ Error in error handler: {e}")
            return ["log_error", "continue"]
    
    def _categorize_error(self, error: Exception) -> ErrorCategory:
        """Categorize error based on type and message"""
        error_message = str(error).lower()
        error_type = type(error).__name__.lower()
        
        # Authentication errors
        if any(keyword in error_message for keyword in ["auth", "signature", "api_key", "unauthorized"]):
            return ErrorCategory.AUTHENTICATION
        
        # Connection errors
        if any(keyword in error_message for keyword in ["connection", "websocket", "disconnect", "timeout"]):
            return ErrorCategory.CONNECTION
        
        # Network errors
        if any(keyword in error_message for keyword in ["network", "dns", "host", "unreachable"]):
            return ErrorCategory.NETWORK
        
        # Rate limit errors
        if any(keyword in error_message for keyword in ["rate limit", "too many requests", "429"]):
            return ErrorCategory.RATE_LIMIT
        
        # Subscription errors
        if any(keyword in error_message for keyword in ["subscription", "channel", "topic"]):
            return ErrorCategory.SUBSCRIPTION
        
        # Heartbeat errors
        if any(keyword in error_message for keyword in ["heartbeat", "ping", "pong"]):
            return ErrorCategory.HEARTBEAT
        
        # Processing errors
        if any(keyword in error_message for keyword in ["parse", "json", "decode", "process"]):
            return ErrorCategory.PROCESSING
        
        return ErrorCategory.UNKNOWN
    
    def _assess_severity(self, error: Exception, category: ErrorCategory) -> ErrorSeverity:
        """Assess error severity based on category and context"""
        error_message = str(error).lower()
        
        # Critical errors
        if category == ErrorCategory.AUTHENTICATION:
            return ErrorSeverity.CRITICAL
        
        if "connection refused" in error_message or "connection reset" in error_message:
            return ErrorSeverity.HIGH
        
        # High severity errors
        if category in [ErrorCategory.CONNECTION, ErrorCategory.NETWORK]:
            return ErrorSeverity.HIGH
        
        # Medium severity errors
        if category in [ErrorCategory.RATE_LIMIT, ErrorCategory.SUBSCRIPTION]:
            return ErrorSeverity.MEDIUM
        
        # Low severity errors
        if category in [ErrorCategory.HEARTBEAT, ErrorCategory.PROCESSING]:
            return ErrorSeverity.LOW
        
        return ErrorSeverity.MEDIUM
    
    def _update_error_tracking(self, category: ErrorCategory, error: Exception, context: Dict[str, Any] = None):
        """Update error tracking metrics"""
        self.error_counts[category] += 1
        self.last_error_time = datetime.now(timezone.utc)
        
        # Store error in history
        error_record = {
            "timestamp": self.last_error_time.isoformat(),
            "category": category.value,
            "error_type": type(error).__name__,
            "error_message": str(error),
            "context": context or {}
        }
        
        self.error_history.append(error_record)
        
        # Keep only last 100 errors
        if len(self.error_history) > 100:
            self.error_history = self.error_history[-100:]
    
    def _should_open_circuit_breaker(self, category: ErrorCategory, severity: ErrorSeverity) -> bool:
        """Determine if circuit breaker should be opened"""
        if self.circuit_breaker_state == "OPEN":
            # Check if timeout has passed
            if self.circuit_breaker_last_failure:
                time_since_failure = (datetime.now(timezone.utc) - self.circuit_breaker_last_failure).total_seconds()
                if time_since_failure > self.circuit_breaker_timeout:
                    self.circuit_breaker_state = "HALF_OPEN"
                    return False
        
        # Open circuit breaker for critical errors or high error count
        if severity == ErrorSeverity.CRITICAL:
            return True
        
        if self.error_counts[category] >= self.circuit_breaker_threshold:
            return True
        
        return False
    
    async def _open_circuit_breaker(self):
        """Open circuit breaker"""
        self.circuit_breaker_state = "OPEN"
        self.circuit_breaker_last_failure = datetime.now(timezone.utc)
        logger.warning(f"🛡️ Circuit breaker opened for {self.service_name}")
    
    async def close_circuit_breaker(self):
        """Close circuit breaker"""
        self.circuit_breaker_state = "CLOSED"
        self.circuit_breaker_last_failure = None
        logger.info(f"🛡️ Circuit breaker closed for {self.service_name}")
    
    def _get_recovery_actions(self, category: ErrorCategory, severity: ErrorSeverity) -> List[str]:
        """Get recommended recovery actions for error category and severity"""
        base_actions = self.recovery_strategies.get(category, ["log_error", "continue"])
        
        # Add severity-specific actions
        if severity == ErrorSeverity.CRITICAL:
            base_actions.insert(0, "immediate_fallback")
        elif severity == ErrorSeverity.HIGH:
            base_actions.insert(0, "aggressive_retry")
        elif severity == ErrorSeverity.LOW:
            base_actions.append("monitor")
        
        return base_actions
    
    def _log_error(self, error: Exception, category: ErrorCategory, severity: ErrorSeverity, context: Dict[str, Any] = None):
        """Log error with appropriate level"""
        error_msg = f"🛡️ {self.service_name} - {category.value} error ({severity.value}): {str(error)}"
        
        if context:
            error_msg += f" - Context: {context}"
        
        if severity == ErrorSeverity.CRITICAL:
            logger.critical(error_msg)
        elif severity == ErrorSeverity.HIGH:
            logger.error(error_msg)
        elif severity == ErrorSeverity.MEDIUM:
            logger.warning(error_msg)
        else:
            logger.info(error_msg)
    
    async def _notify_error_callbacks(self, error: Exception, category: ErrorCategory, severity: ErrorSeverity, context: Dict[str, Any] = None):
        """Notify error callbacks"""
        for callback in self.error_callbacks:
            try:
                await callback(error, category, severity, context)
            except Exception as e:
                logger.error(f"❌ Error in error callback: {e}")
    
    def add_error_callback(self, callback: Callable):
        """Add error callback"""
        self.error_callbacks.append(callback)
    
    def get_error_metrics(self) -> Dict[str, Any]:
        """Get error metrics"""
        return {
            "error_counts": {category.value: count for category, count in self.error_counts.items()},
            "circuit_breaker_state": self.circuit_breaker_state,
            "last_error_time": self.last_error_time.isoformat() if self.last_error_time else None,
            "total_errors": sum(self.error_counts.values()),
            "recent_errors": len([e for e in self.error_history if 
                                (datetime.now(timezone.utc) - datetime.fromisoformat(e["timestamp"])).total_seconds() < 3600])
        }
    
    def reset_error_metrics(self):
        """Reset error metrics"""
        self.error_counts = {category: 0 for category in ErrorCategory}
        self.error_history = []
        self.last_error_time = None
        logger.info(f"📊 Error metrics reset for {self.service_name}")
    
    def is_healthy(self) -> bool:
        """Check if service is healthy based on error metrics"""
        if self.circuit_breaker_state == "OPEN":
            return False
        
        # Check for too many recent errors
        recent_errors = len([e for e in self.error_history if 
                           (datetime.now(timezone.utc) - datetime.fromisoformat(e["timestamp"])).total_seconds() < 300])
        
        return recent_errors < 10
