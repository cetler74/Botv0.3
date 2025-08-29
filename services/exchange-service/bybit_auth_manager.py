"""
Bybit Authentication Manager - Version 2.6.0
Manages Bybit WebSocket authentication with HMAC-SHA256 signatures and timestamp validation
"""

import hmac
import hashlib
import time
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone
import asyncio

logger = logging.getLogger(__name__)

class BybitAuthManager:
    """
    Manages Bybit WebSocket authentication with HMAC-SHA256 signatures
    
    Features:
    - HMAC-SHA256 signature generation with timestamp
    - Timestamp validation and tolerance handling
    - Authentication payload generation
    - Retry mechanism for failed authentications
    - Security validation and error handling
    """
    
    def __init__(self, api_key: str, api_secret: str, timestamp_tolerance: int = 5000):
        """
        Initialize Bybit authentication manager
        
        Args:
            api_key: Bybit API key
            api_secret: Bybit API secret
            timestamp_tolerance: Timestamp tolerance in milliseconds (default: 5000ms)
        """
        self.api_key = api_key
        self.api_secret = api_secret
        self.timestamp_tolerance = timestamp_tolerance
        
        # Authentication metrics
        self.auth_attempts = 0
        self.auth_successes = 0
        self.auth_failures = 0
        self.last_auth_time = None
        self.last_auth_error = None
        
        # Retry configuration
        self.max_retry_attempts = 3
        self.retry_delay = 1  # seconds
        
        logger.info("🔐 Bybit Authentication Manager initialized")
    
    def generate_signature(self, timestamp: str) -> str:
        """
        Generate HMAC-SHA256 signature for Bybit WebSocket authentication
        
        Args:
            timestamp: Current timestamp in milliseconds
            
        Returns:
            HMAC-SHA256 signature as hex string
        """
        try:
            # Bybit signature format: GET/realtime{timestamp}
            message = f"GET/realtime{timestamp}"
            
            signature = hmac.new(
                self.api_secret.encode('utf-8'),
                message.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()
            
            logger.debug(f"Signature generated for message: {message}")
            logger.debug(f"Signature: {signature[:10]}...")
            
            logger.debug(f"Generated signature for timestamp {timestamp}")
            return signature
            
        except Exception as e:
            logger.error(f"Failed to generate signature: {e}")
            raise
    
    def generate_auth_payload(self) -> Dict[str, Any]:
        """
        Generate authentication payload for WebSocket connection
        
        Returns:
            Authentication payload dictionary
        """
        try:
            # Generate current timestamp in milliseconds
            timestamp = str(int(time.time() * 1000))
            
            # Generate signature
            signature = self.generate_signature(timestamp)
            
            # Create authentication payload
            auth_payload = {
                "op": "auth",
                "args": [self.api_key, timestamp, signature]
            }
            
            # Alternative format if the above doesn't work
            # auth_payload = {
            #     "op": "auth",
            #     "args": [self.api_key, timestamp, signature]
            # }
            
            # Alternative format if the above doesn't work
            # auth_payload = {
            #     "op": "auth",
            #     "args": [self.api_key, timestamp, signature]
            # }
            
            # Log the payload for debugging (without sensitive data)
            logger.debug(f"Auth payload format: op={auth_payload['op']}, args_count={len(auth_payload['args'])}")
            
            self.auth_attempts += 1
            self.last_auth_time = datetime.now(timezone.utc)
            
            logger.info(f"Generated authentication payload for API key: {self.api_key[:8]}...")
            return auth_payload
            
        except Exception as e:
            self.auth_failures += 1
            self.last_auth_error = str(e)
            logger.error(f"Failed to generate authentication payload: {e}")
            raise
    
    def validate_timestamp(self, timestamp: str) -> bool:
        """
        Validate timestamp for authentication
        
        Args:
            timestamp: Timestamp to validate
            
        Returns:
            True if timestamp is valid, False otherwise
        """
        try:
            current_time = int(time.time() * 1000)
            auth_time = int(timestamp)
            time_diff = abs(current_time - auth_time)
            
            if time_diff > self.timestamp_tolerance:
                logger.warning(f"Timestamp validation failed: {time_diff}ms > {self.timestamp_tolerance}ms tolerance")
                return False
            
            logger.debug(f"Timestamp validation passed: {time_diff}ms")
            return True
            
        except Exception as e:
            logger.error(f"Timestamp validation error: {e}")
            return False
    
    async def authenticate_with_retry(self) -> Optional[Dict[str, Any]]:
        """
        Attempt authentication with retry mechanism
        
        Returns:
            Authentication payload if successful, None if failed
        """
        for attempt in range(self.max_retry_attempts):
            try:
                auth_payload = self.generate_auth_payload()
                
                # Validate timestamp
                timestamp = auth_payload["args"][1]
                if not self.validate_timestamp(timestamp):
                    raise ValueError("Timestamp validation failed")
                
                self.auth_successes += 1
                logger.info(f"Authentication successful on attempt {attempt + 1}")
                return auth_payload
                
            except Exception as e:
                self.auth_failures += 1
                self.last_auth_error = str(e)
                logger.warning(f"Authentication attempt {attempt + 1} failed: {e}")
                
                if attempt < self.max_retry_attempts - 1:
                    logger.info(f"Retrying authentication in {self.retry_delay} seconds...")
                    await asyncio.sleep(self.retry_delay)
                    self.retry_delay *= 2  # Exponential backoff
                else:
                    logger.error("All authentication attempts failed")
                    return None
        
        return None
    
    def get_auth_metrics(self) -> Dict[str, Any]:
        """
        Get authentication metrics
        
        Returns:
            Dictionary containing authentication metrics
        """
        success_rate = 0
        if self.auth_attempts > 0:
            success_rate = (self.auth_successes / self.auth_attempts) * 100
        
        return {
            "auth_attempts": self.auth_attempts,
            "auth_successes": self.auth_successes,
            "auth_failures": self.auth_failures,
            "success_rate": round(success_rate, 2),
            "last_auth_time": self.last_auth_time.isoformat() if self.last_auth_time else None,
            "last_auth_error": self.last_auth_error,
            "timestamp_tolerance": self.timestamp_tolerance,
            "max_retry_attempts": self.max_retry_attempts
        }
    
    def reset_metrics(self) -> None:
        """Reset authentication metrics"""
        self.auth_attempts = 0
        self.auth_successes = 0
        self.auth_failures = 0
        self.last_auth_time = None
        self.last_auth_error = None
        logger.info("Authentication metrics reset")
    
    def is_authenticated(self) -> bool:
        """
        Check if authentication is valid
        
        Returns:
            True if recently authenticated, False otherwise
        """
        if not self.last_auth_time:
            return False
        
        # Check if last authentication was within tolerance
        time_since_auth = (datetime.now(timezone.utc) - self.last_auth_time).total_seconds() * 1000
        return time_since_auth < self.timestamp_tolerance
