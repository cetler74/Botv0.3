"""
Crypto.com Error Handlers - Version 2.6.0
Comprehensive error categorization and recovery system for Crypto.com WebSocket integration
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Callable, List
from enum import Enum
import json

logger = logging.getLogger(__name__)

class CryptocomErrorType(Enum):
    """Categorized error types for Crypto.com API"""
    
    # Connection Errors
    WEBSOCKET_CONNECTION_FAILED = "websocket_connection_failed"
    WEBSOCKET_DISCONNECTED = "websocket_disconnected"
    NETWORK_TIMEOUT = "network_timeout"
    DNS_RESOLUTION_FAILED = "dns_resolution_failed"
    
    # Authentication Errors
    INVALID_API_CREDENTIALS = "invalid_api_credentials"
    SIGNATURE_VERIFICATION_FAILED = "signature_verification_failed"
    API_KEY_PERMISSIONS_INSUFFICIENT = "api_key_permissions_insufficient"
    AUTHENTICATION_TIMEOUT = "authentication_timeout"
    
    # API Errors
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
    API_ENDPOINT_UNAVAILABLE = "api_endpoint_unavailable"
    INVALID_REQUEST_FORMAT = "invalid_request_format"
    SERVER_ERROR = "server_error"
    
    # WebSocket Protocol Errors
    HEARTBEAT_TIMEOUT = "heartbeat_timeout"
    SUBSCRIPTION_FAILED = "subscription_failed"
    MESSAGE_PARSING_ERROR = "message_parsing_error"
    PROTOCOL_VIOLATION = "protocol_violation"
    
    # Data Errors
    INVALID_SYMBOL = "invalid_symbol"
    INVALID_ORDER_ID = "invalid_order_id"
    MISSING_REQUIRED_FIELD = "missing_required_field"
    DATA_CORRUPTION = "data_corruption"
    
    # Recovery Errors
    MAX_RECONNECT_ATTEMPTS_EXCEEDED = "max_reconnect_attempts_exceeded"
    CIRCUIT_BREAKER_OPEN = "circuit_breaker_open"
    RECOVERY_TIMEOUT = "recovery_timeout"
    
    # Unknown/Generic
    UNKNOWN_ERROR = "unknown_error"

class CryptocomErrorSeverity(Enum):
    """Error severity levels for appropriate response"""
    LOW = "low"           # Informational, no action needed
    MEDIUM = "medium"     # Warning, retry recommended
    HIGH = "high"         # Error, immediate retry required
    CRITICAL = "critical" # Critical, escalate and fallback required

class CryptocomError:
    """Comprehensive error representation"""
    
    def __init__(self, 
                 error_type: CryptocomErrorType,
                 severity: CryptocomErrorSeverity,
                 message: str,
                 details: Dict[str, Any] = None,
                 recoverable: bool = True,
                 retry_delay: float = 1.0):
        self.error_type = error_type
        self.severity = severity
        self.message = message
        self.details = details or {}
        self.recoverable = recoverable
        self.retry_delay = retry_delay
        self.timestamp = datetime.utcnow()
        self.attempts = 0
        
    def to_dict(self) -> Dict[str, Any]:
        return {
            "error_type": self.error_type.value,
            "severity": self.severity.value,
            "message": self.message,
            "details": self.details,
            "recoverable": self.recoverable,
            "retry_delay": self.retry_delay,
            "timestamp": self.timestamp.isoformat(),
            "attempts": self.attempts
        }

class CryptocomErrorClassifier:
    """Classifies exceptions into structured Crypto.com errors"""
    
    def __init__(self):
        self.error_patterns = {
            # Connection patterns
            "Connection refused": (CryptocomErrorType.WEBSOCKET_CONNECTION_FAILED, CryptocomErrorSeverity.HIGH),
            "Connection closed": (CryptocomErrorType.WEBSOCKET_DISCONNECTED, CryptocomErrorSeverity.MEDIUM),
            "timeout": (CryptocomErrorType.NETWORK_TIMEOUT, CryptocomErrorSeverity.MEDIUM),
            "Name resolution failed": (CryptocomErrorType.DNS_RESOLUTION_FAILED, CryptocomErrorSeverity.HIGH),
            
            # Authentication patterns
            "Invalid API key": (CryptocomErrorType.INVALID_API_CREDENTIALS, CryptocomErrorSeverity.CRITICAL),
            "Invalid signature": (CryptocomErrorType.SIGNATURE_VERIFICATION_FAILED, CryptocomErrorSeverity.CRITICAL),
            "Insufficient permissions": (CryptocomErrorType.API_KEY_PERMISSIONS_INSUFFICIENT, CryptocomErrorSeverity.CRITICAL),
            "Authentication failed": (CryptocomErrorType.INVALID_API_CREDENTIALS, CryptocomErrorSeverity.CRITICAL),
            
            # API patterns
            "Rate limit": (CryptocomErrorType.RATE_LIMIT_EXCEEDED, CryptocomErrorSeverity.MEDIUM),
            "Too many requests": (CryptocomErrorType.RATE_LIMIT_EXCEEDED, CryptocomErrorSeverity.MEDIUM),
            "Service unavailable": (CryptocomErrorType.API_ENDPOINT_UNAVAILABLE, CryptocomErrorSeverity.HIGH),
            "Internal server error": (CryptocomErrorType.SERVER_ERROR, CryptocomErrorSeverity.HIGH),
            
            # WebSocket patterns
            "Heartbeat timeout": (CryptocomErrorType.HEARTBEAT_TIMEOUT, CryptocomErrorSeverity.MEDIUM),
            "Subscription failed": (CryptocomErrorType.SUBSCRIPTION_FAILED, CryptocomErrorSeverity.HIGH),
            "JSON decode error": (CryptocomErrorType.MESSAGE_PARSING_ERROR, CryptocomErrorSeverity.LOW),
            "Invalid message format": (CryptocomErrorType.PROTOCOL_VIOLATION, CryptocomErrorSeverity.MEDIUM),
            
            # Data patterns
            "Invalid symbol": (CryptocomErrorType.INVALID_SYMBOL, CryptocomErrorSeverity.MEDIUM),
            "Order not found": (CryptocomErrorType.INVALID_ORDER_ID, CryptocomErrorSeverity.MEDIUM),
            "Missing required parameter": (CryptocomErrorType.MISSING_REQUIRED_FIELD, CryptocomErrorSeverity.MEDIUM),
        }
        
        logger.info("🛡️ Crypto.com Error Classifier initialized")
    
    def classify_error(self, exception: Exception, context: str = "") -> CryptocomError:
        """
        Classify an exception into a structured Crypto.com error
        
        Args:
            exception: The caught exception
            context: Additional context about where the error occurred
            
        Returns:
            CryptocomError: Structured error object
        """
        error_message = str(exception).lower()
        error_type = CryptocomErrorType.UNKNOWN_ERROR
        severity = CryptocomErrorSeverity.MEDIUM
        
        # Pattern matching for error classification
        for pattern, (detected_type, detected_severity) in self.error_patterns.items():
            if pattern.lower() in error_message:
                error_type = detected_type
                severity = detected_severity
                break
        
        # Determine recoverability and retry delay based on error type
        recoverable = self._is_recoverable(error_type)
        retry_delay = self._get_retry_delay(error_type, severity)
        
        return CryptocomError(
            error_type=error_type,
            severity=severity,
            message=f"{context}: {str(exception)}" if context else str(exception),
            details={
                "exception_type": type(exception).__name__,
                "context": context,
                "raw_message": str(exception)
            },
            recoverable=recoverable,
            retry_delay=retry_delay
        )
    
    def _is_recoverable(self, error_type: CryptocomErrorType) -> bool:
        """Determine if an error type is recoverable"""
        non_recoverable_errors = {
            CryptocomErrorType.INVALID_API_CREDENTIALS,
            CryptocomErrorType.API_KEY_PERMISSIONS_INSUFFICIENT,
            CryptocomErrorType.MAX_RECONNECT_ATTEMPTS_EXCEEDED,
            CryptocomErrorType.INVALID_SYMBOL,
            CryptocomErrorType.INVALID_REQUEST_FORMAT
        }
        return error_type not in non_recoverable_errors
    
    def _get_retry_delay(self, error_type: CryptocomErrorType, severity: CryptocomErrorSeverity) -> float:
        """Get appropriate retry delay for error type and severity"""
        base_delays = {
            CryptocomErrorSeverity.LOW: 1.0,
            CryptocomErrorSeverity.MEDIUM: 5.0,
            CryptocomErrorSeverity.HIGH: 15.0,
            CryptocomErrorSeverity.CRITICAL: 60.0
        }
        
        # Special cases
        if error_type == CryptocomErrorType.RATE_LIMIT_EXCEEDED:
            return 30.0  # Wait longer for rate limits
        elif error_type == CryptocomErrorType.HEARTBEAT_TIMEOUT:
            return 2.0   # Quick retry for heartbeat
        
        return base_delays.get(severity, 5.0)

class CryptocomErrorRecoveryStrategy:
    """Manages recovery strategies for different error types"""
    
    def __init__(self):
        self.recovery_handlers: Dict[CryptocomErrorType, Callable] = {}
        self.max_recovery_attempts = {
            CryptocomErrorType.WEBSOCKET_CONNECTION_FAILED: 5,
            CryptocomErrorType.WEBSOCKET_DISCONNECTED: 3,
            CryptocomErrorType.HEARTBEAT_TIMEOUT: 2,
            CryptocomErrorType.RATE_LIMIT_EXCEEDED: 3,
            CryptocomErrorType.SERVER_ERROR: 2,
            CryptocomErrorType.SUBSCRIPTION_FAILED: 3
        }
        
        logger.info("🔧 Crypto.com Error Recovery Strategy initialized")
    
    def register_recovery_handler(self, error_type: CryptocomErrorType, handler: Callable):
        """Register a recovery handler for specific error type"""
        self.recovery_handlers[error_type] = handler
        logger.debug(f"📝 Registered recovery handler for {error_type.value}")
    
    async def execute_recovery(self, error: CryptocomError) -> bool:
        """
        Execute recovery strategy for a given error
        
        Args:
            error: The CryptocomError to recover from
            
        Returns:
            bool: True if recovery succeeded, False otherwise
        """
        if not error.recoverable:
            logger.error(f"❌ Error not recoverable: {error.error_type.value}")
            return False
        
        # Check if we've exceeded max attempts
        max_attempts = self.max_recovery_attempts.get(error.error_type, 3)
        if error.attempts >= max_attempts:
            logger.error(f"❌ Max recovery attempts ({max_attempts}) exceeded for {error.error_type.value}")
            return False
        
        # Get recovery handler
        handler = self.recovery_handlers.get(error.error_type)
        if not handler:
            logger.warning(f"⚠️ No recovery handler for {error.error_type.value}, using generic recovery")
            handler = self._generic_recovery_handler
        
        try:
            error.attempts += 1
            logger.info(f"🔄 Attempting recovery for {error.error_type.value} (attempt {error.attempts}/{max_attempts})")
            
            # Wait for retry delay
            await asyncio.sleep(error.retry_delay)
            
            # Execute recovery
            success = await handler(error)
            
            if success:
                logger.info(f"✅ Recovery successful for {error.error_type.value}")
            else:
                logger.warning(f"⚠️ Recovery failed for {error.error_type.value}")
            
            return success
            
        except Exception as e:
            logger.error(f"❌ Recovery handler exception for {error.error_type.value}: {e}")
            return False
    
    async def _generic_recovery_handler(self, error: CryptocomError) -> bool:
        """Generic recovery handler - basic retry logic"""
        logger.info(f"🔄 Generic recovery for {error.error_type.value}")
        
        # Basic strategy: wait and indicate retry needed
        await asyncio.sleep(error.retry_delay)
        return True  # Indicate that retry should be attempted

class CryptocomErrorReporter:
    """Reports and tracks error metrics"""
    
    def __init__(self):
        self.error_counts: Dict[CryptocomErrorType, int] = {}
        self.severity_counts: Dict[CryptocomErrorSeverity, int] = {}
        self.recent_errors: List[CryptocomError] = []
        self.max_recent_errors = 100
        
        logger.info("📊 Crypto.com Error Reporter initialized")
    
    def report_error(self, error: CryptocomError):
        """Report an error occurrence"""
        # Update counts
        self.error_counts[error.error_type] = self.error_counts.get(error.error_type, 0) + 1
        self.severity_counts[error.severity] = self.severity_counts.get(error.severity, 0) + 1
        
        # Add to recent errors
        self.recent_errors.append(error)
        if len(self.recent_errors) > self.max_recent_errors:
            self.recent_errors.pop(0)
        
        # Log based on severity
        if error.severity == CryptocomErrorSeverity.CRITICAL:
            logger.critical(f"🚨 CRITICAL ERROR - {error.error_type.value}: {error.message}")
        elif error.severity == CryptocomErrorSeverity.HIGH:
            logger.error(f"❌ HIGH ERROR - {error.error_type.value}: {error.message}")
        elif error.severity == CryptocomErrorSeverity.MEDIUM:
            logger.warning(f"⚠️ MEDIUM ERROR - {error.error_type.value}: {error.message}")
        else:
            logger.info(f"ℹ️ LOW ERROR - {error.error_type.value}: {error.message}")
    
    def get_error_metrics(self) -> Dict[str, Any]:
        """Get comprehensive error metrics"""
        total_errors = sum(self.error_counts.values())
        
        return {
            "total_errors": total_errors,
            "error_counts_by_type": {k.value: v for k, v in self.error_counts.items()},
            "error_counts_by_severity": {k.value: v for k, v in self.severity_counts.items()},
            "recent_errors_count": len(self.recent_errors),
            "most_common_error": max(self.error_counts, key=self.error_counts.get).value if self.error_counts else None,
            "error_rate_by_severity": {
                k.value: (v / total_errors * 100) if total_errors > 0 else 0 
                for k, v in self.severity_counts.items()
            }
        }
    
    def get_recent_errors(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent errors as dictionaries"""
        recent = self.recent_errors[-limit:] if limit else self.recent_errors
        return [error.to_dict() for error in recent]
    
    def clear_error_history(self):
        """Clear error history (useful for testing)"""
        self.error_counts.clear()
        self.severity_counts.clear()
        self.recent_errors.clear()
        logger.info("🧹 Error history cleared")

class CryptocomErrorHandler:
    """Main error handling coordinator"""
    
    def __init__(self):
        self.classifier = CryptocomErrorClassifier()
        self.recovery_strategy = CryptocomErrorRecoveryStrategy()
        self.reporter = CryptocomErrorReporter()
        
        # Error handler callbacks
        self.error_callbacks: List[Callable] = []
        
        logger.info("🛡️ Crypto.com Error Handler initialized")
    
    def add_error_callback(self, callback: Callable):
        """Add callback to be notified of errors"""
        self.error_callbacks.append(callback)
        logger.debug("📝 Error callback registered")
    
    async def handle_error(self, exception: Exception, context: str = "") -> bool:
        """
        Main error handling entry point
        
        Args:
            exception: The caught exception
            context: Context about where the error occurred
            
        Returns:
            bool: True if error was handled and recovery successful
        """
        # Classify the error
        error = self.classifier.classify_error(exception, context)
        
        # Report the error
        self.reporter.report_error(error)
        
        # Notify callbacks
        for callback in self.error_callbacks:
            try:
                await callback(error)
            except Exception as e:
                logger.error(f"❌ Error callback exception: {e}")
        
        # Attempt recovery if recoverable
        if error.recoverable:
            return await self.recovery_strategy.execute_recovery(error)
        
        return False
    
    def register_recovery_handler(self, error_type: CryptocomErrorType, handler: Callable):
        """Register a recovery handler for specific error type"""
        self.recovery_strategy.register_recovery_handler(error_type, handler)
    
    def get_error_metrics(self) -> Dict[str, Any]:
        """Get comprehensive error metrics"""
        return self.reporter.get_error_metrics()
    
    def get_recent_errors(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent errors"""
        return self.reporter.get_recent_errors(limit)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get error handler statistics (alias for get_error_metrics)"""
        return self.get_error_metrics()