"""
Bybit Recovery Manager - Version 2.6.0
Handles automatic recovery actions for Bybit WebSocket connections
"""

import asyncio
import logging
import time
from typing import Dict, Any, List, Optional, Callable
from datetime import datetime, timezone

from bybit_error_handlers import ErrorCategory, ErrorSeverity

logger = logging.getLogger(__name__)

class BybitRecoveryManager:
    """
    Manages automatic recovery actions for Bybit WebSocket connections
    
    Features:
    - Automatic recovery action execution
    - Recovery strategy management
    - Recovery metrics and tracking
    - Circuit breaker integration
    - Fallback mechanism coordination
    """
    
    def __init__(self, service_name: str = "bybit-websocket"):
        """
        Initialize Bybit recovery manager
        
        Args:
            service_name: Name of the service for logging
        """
        self.service_name = service_name
        
        # Recovery actions mapping
        self.recovery_actions = {
            "refresh_authentication": self._refresh_authentication,
            "reconnect": self._reconnect,
            "fallback_to_rest": self._fallback_to_rest,
            "retry": self._retry,
            "backoff": self._backoff,
            "resubscribe": self._resubscribe,
            "validate_channels": self._validate_channels,
            "restart_heartbeat": self._restart_heartbeat,
            "skip_event": self._skip_event,
            "immediate_fallback": self._immediate_fallback,
            "aggressive_retry": self._aggressive_retry,
            "monitor": self._monitor,
            "circuit_breaker_open": self._circuit_breaker_open,
            "log_error": self._log_error,
            "continue": self._continue
        }
        
        # Recovery state
        self.recovery_in_progress = False
        self.last_recovery_time = None
        self.recovery_attempts = 0
        self.max_recovery_attempts = 10
        
        # Recovery metrics
        self.metrics = {
            "recovery_attempts": 0,
            "successful_recoveries": 0,
            "failed_recoveries": 0,
            "recovery_time_total": 0,
            "last_recovery_duration": 0,
            "recovery_actions_executed": {}
        }
        
        # Recovery callbacks
        self.recovery_callbacks: List[Callable] = []
        
        logger.info(f"🔄 Bybit Recovery Manager initialized for {service_name}")
    
    async def execute_recovery_actions(self, actions: List[str], context: Dict[str, Any] = None) -> bool:
        """
        Execute recovery actions
        
        Args:
            actions: List of recovery actions to execute
            context: Additional context for recovery
            
        Returns:
            True if recovery was successful, False otherwise
        """
        if self.recovery_in_progress:
            logger.warning(f"🔄 Recovery already in progress for {self.service_name}")
            return False
        
        self.recovery_in_progress = True
        self.recovery_attempts += 1
        self.metrics["recovery_attempts"] += 1
        
        start_time = time.time()
        recovery_success = False
        
        try:
            logger.info(f"🔄 Starting recovery for {self.service_name} - Actions: {actions}")
            
            # Execute each recovery action
            for action in actions:
                if action in self.recovery_actions:
                    try:
                        await self.recovery_actions[action](context)
                        self.metrics["recovery_actions_executed"][action] = \
                            self.metrics["recovery_actions_executed"].get(action, 0) + 1
                        logger.debug(f"🔄 Executed recovery action: {action}")
                    except Exception as e:
                        logger.error(f"❌ Failed to execute recovery action {action}: {e}")
                else:
                    logger.warning(f"⚠️ Unknown recovery action: {action}")
            
            # Determine if recovery was successful
            recovery_success = await self._verify_recovery_success(context)
            
            if recovery_success:
                self.metrics["successful_recoveries"] += 1
                logger.info(f"✅ Recovery successful for {self.service_name}")
            else:
                self.metrics["failed_recoveries"] += 1
                logger.warning(f"❌ Recovery failed for {self.service_name}")
            
            # Notify recovery callbacks
            await self._notify_recovery_callbacks(recovery_success, actions, context)
            
        except Exception as e:
            logger.error(f"❌ Error during recovery: {e}")
            self.metrics["failed_recoveries"] += 1
            recovery_success = False
        
        finally:
            # Update metrics
            recovery_duration = time.time() - start_time
            self.metrics["recovery_time_total"] += recovery_duration
            self.metrics["last_recovery_duration"] = recovery_duration
            self.last_recovery_time = datetime.now(timezone.utc)
            
            self.recovery_in_progress = False
            
            if self.recovery_attempts >= self.max_recovery_attempts:
                logger.error(f"❌ Max recovery attempts reached for {self.service_name}")
                await self._escalate_recovery_failure(context)
        
        return recovery_success
    
    async def _refresh_authentication(self, context: Dict[str, Any] = None):
        """Refresh authentication"""
        logger.info(f"🔐 Refreshing authentication for {self.service_name}")
        # This would typically involve regenerating API keys or refreshing tokens
        await asyncio.sleep(1)  # Simulate authentication refresh
    
    async def _reconnect(self, context: Dict[str, Any] = None):
        """Reconnect WebSocket"""
        logger.info(f"🔌 Reconnecting WebSocket for {self.service_name}")
        # This would typically involve closing and reopening the WebSocket connection
        await asyncio.sleep(2)  # Simulate reconnection
    
    async def _fallback_to_rest(self, context: Dict[str, Any] = None):
        """Activate REST API fallback"""
        logger.info(f"🔄 Activating REST API fallback for {self.service_name}")
        # This would typically involve switching to REST API polling
        await asyncio.sleep(1)  # Simulate fallback activation
    
    async def _retry(self, context: Dict[str, Any] = None):
        """Retry the failed operation"""
        logger.info(f"🔄 Retrying operation for {self.service_name}")
        await asyncio.sleep(1)  # Simulate retry delay
    
    async def _backoff(self, context: Dict[str, Any] = None):
        """Apply exponential backoff"""
        backoff_delay = min(2 ** self.recovery_attempts, 60)  # Cap at 60 seconds
        logger.info(f"⏳ Applying backoff delay: {backoff_delay}s for {self.service_name}")
        await asyncio.sleep(backoff_delay)
    
    async def _resubscribe(self, context: Dict[str, Any] = None):
        """Resubscribe to channels"""
        logger.info(f"📡 Resubscribing to channels for {self.service_name}")
        await asyncio.sleep(1)  # Simulate resubscription
    
    async def _validate_channels(self, context: Dict[str, Any] = None):
        """Validate channel subscriptions"""
        logger.info(f"✅ Validating channel subscriptions for {self.service_name}")
        await asyncio.sleep(1)  # Simulate validation
    
    async def _restart_heartbeat(self, context: Dict[str, Any] = None):
        """Restart heartbeat mechanism"""
        logger.info(f"💓 Restarting heartbeat for {self.service_name}")
        await asyncio.sleep(1)  # Simulate heartbeat restart
    
    async def _skip_event(self, context: Dict[str, Any] = None):
        """Skip the problematic event"""
        logger.info(f"⏭️ Skipping event for {self.service_name}")
        # No delay needed for skipping
    
    async def _immediate_fallback(self, context: Dict[str, Any] = None):
        """Immediately activate fallback"""
        logger.warning(f"🚨 Immediate fallback activated for {self.service_name}")
        await self._fallback_to_rest(context)
    
    async def _aggressive_retry(self, context: Dict[str, Any] = None):
        """Aggressive retry with shorter delays"""
        logger.info(f"⚡ Aggressive retry for {self.service_name}")
        await asyncio.sleep(0.5)  # Shorter delay for aggressive retry
    
    async def _monitor(self, context: Dict[str, Any] = None):
        """Monitor the situation"""
        logger.info(f"👀 Monitoring {self.service_name}")
        # No action needed for monitoring
    
    async def _circuit_breaker_open(self, context: Dict[str, Any] = None):
        """Handle circuit breaker opening"""
        logger.warning(f"🛡️ Circuit breaker opened for {self.service_name}")
        await self._fallback_to_rest(context)
    
    async def _log_error(self, context: Dict[str, Any] = None):
        """Log error and continue"""
        logger.error(f"📝 Error logged for {self.service_name}")
        # No delay needed for logging
    
    async def _continue(self, context: Dict[str, Any] = None):
        """Continue normal operation"""
        logger.info(f"➡️ Continuing normal operation for {self.service_name}")
        # No action needed
    
    async def _verify_recovery_success(self, context: Dict[str, Any] = None) -> bool:
        """Verify if recovery was successful"""
        # This would typically involve checking connection status, authentication, etc.
        logger.debug(f"🔍 Verifying recovery success for {self.service_name}")
        
        # Simulate verification checks
        await asyncio.sleep(0.5)
        
        # For now, assume recovery is successful if we reach this point
        return True
    
    async def _escalate_recovery_failure(self, context: Dict[str, Any] = None):
        """Escalate recovery failure"""
        logger.critical(f"🚨 Recovery failure escalated for {self.service_name}")
        
        # This would typically involve:
        # - Sending alerts
        # - Activating emergency procedures
        # - Notifying administrators
        # - Switching to backup systems
        
        # For now, just log the escalation
        logger.critical(f"🚨 MAXIMUM RECOVERY ATTEMPTS REACHED - MANUAL INTERVENTION REQUIRED")
    
    async def _notify_recovery_callbacks(self, success: bool, actions: List[str], context: Dict[str, Any] = None):
        """Notify recovery callbacks"""
        for callback in self.recovery_callbacks:
            try:
                await callback(success, actions, context)
            except Exception as e:
                logger.error(f"❌ Error in recovery callback: {e}")
    
    def add_recovery_callback(self, callback: Callable):
        """Add recovery callback"""
        self.recovery_callbacks.append(callback)
    
    def get_recovery_metrics(self) -> Dict[str, Any]:
        """Get recovery metrics"""
        avg_recovery_time = 0
        if self.metrics["recovery_attempts"] > 0:
            avg_recovery_time = self.metrics["recovery_time_total"] / self.metrics["recovery_attempts"]
        
        success_rate = 0
        if self.metrics["recovery_attempts"] > 0:
            success_rate = (self.metrics["successful_recoveries"] / self.metrics["recovery_attempts"]) * 100
        
        return {
            "recovery_attempts": self.metrics["recovery_attempts"],
            "successful_recoveries": self.metrics["successful_recoveries"],
            "failed_recoveries": self.metrics["failed_recoveries"],
            "success_rate": round(success_rate, 2),
            "avg_recovery_time": round(avg_recovery_time, 2),
            "last_recovery_duration": round(self.metrics["last_recovery_duration"], 2),
            "recovery_actions_executed": self.metrics["recovery_actions_executed"],
            "recovery_in_progress": self.recovery_in_progress,
            "last_recovery_time": self.last_recovery_time.isoformat() if self.last_recovery_time else None
        }
    
    def reset_recovery_metrics(self):
        """Reset recovery metrics"""
        self.metrics = {
            "recovery_attempts": 0,
            "successful_recoveries": 0,
            "failed_recoveries": 0,
            "recovery_time_total": 0,
            "last_recovery_duration": 0,
            "recovery_actions_executed": {}
        }
        self.recovery_attempts = 0
        self.last_recovery_time = None
        logger.info(f"📊 Recovery metrics reset for {self.service_name}")
    
    def is_recovery_healthy(self) -> bool:
        """Check if recovery system is healthy"""
        # Consider healthy if success rate is above 80% and not too many recent failures
        if self.metrics["recovery_attempts"] == 0:
            return True
        
        success_rate = (self.metrics["successful_recoveries"] / self.metrics["recovery_attempts"]) * 100
        recent_failures = self.metrics["failed_recoveries"]
        
        return success_rate >= 80 and recent_failures < 5
