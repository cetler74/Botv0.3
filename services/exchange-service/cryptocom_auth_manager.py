"""
Crypto.com Authentication Manager - Version 2.6.0
Manages authentication and subscription for Crypto.com WebSocket API
"""

import hmac
import hashlib
import time
import json
import logging
from typing import Dict, Any, List
from datetime import datetime

logger = logging.getLogger(__name__)

class CryptocomAuthManager:
    """
    Manages Crypto.com WebSocket authentication and subscriptions
    
    Features:
    - HMAC-SHA256 signature generation
    - Channel subscription management
    - Authentication message creation
    - Request signing for private endpoints
    """
    
    def __init__(self, api_key: str, api_secret: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.request_id_counter = 1
        
        logger.info("🔐 Crypto.com Authentication Manager initialized")
    
    def generate_signature(self, timestamp: int, method: str, path: str, body: str = "") -> str:
        """
        Generate HMAC-SHA256 signature for Crypto.com API authentication
        
        Args:
            timestamp: Unix timestamp in milliseconds
            method: HTTP method (usually empty for WebSocket)
            path: API path (usually empty for WebSocket)
            body: Request body (usually empty for WebSocket)
            
        Returns:
            HMAC-SHA256 signature as hex string
        """
        try:
            # Create the message string for signing
            message = f"{method}{path}{body}{timestamp}"
            
            # Generate HMAC-SHA256 signature
            signature = hmac.new(
                self.api_secret.encode('utf-8'),
                message.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()
            
            logger.debug(f"🔐 Generated signature for timestamp {timestamp}")
            return signature
            
        except Exception as e:
            logger.error(f"❌ Error generating signature: {e}")
            raise
    
    def generate_auth_message(self) -> Dict[str, Any]:
        """
        Generate authentication message for WebSocket connection
        
        Returns:
            Authentication message dictionary
        """
        try:
            timestamp = int(time.time() * 1000)
            signature = self.generate_signature(timestamp, "", "")
            
            auth_message = {
                "id": self._get_next_request_id(),
                "method": "auth",
                "api_key": self.api_key,
                "timestamp": timestamp,
                "signature": signature
            }
            
            logger.info("🔐 Generated authentication message")
            return auth_message
            
        except Exception as e:
            logger.error(f"❌ Error generating auth message: {e}")
            raise
    
    def generate_subscription_message(self, channels: List[str]) -> Dict[str, Any]:
        """
        Generate subscription message for user data channels
        
        Args:
            channels: List of channels to subscribe to (e.g., ["user.order", "user.trade"])
            
        Returns:
            Subscription message dictionary
        """
        try:
            timestamp = int(time.time() * 1000)
            signature = self.generate_signature(timestamp, "", "")
            
            subscription_message = {
                "id": self._get_next_request_id(),
                "method": "subscribe",
                "params": {
                    "channels": channels
                },
                "api_key": self.api_key,
                "timestamp": timestamp,
                "signature": signature
            }
            
            logger.info(f"📡 Generated subscription message for channels: {', '.join(channels)}")
            return subscription_message
            
        except Exception as e:
            logger.error(f"❌ Error generating subscription message: {e}")
            raise
    
    def generate_unsubscription_message(self, channels: List[str]) -> Dict[str, Any]:
        """
        Generate unsubscription message for user data channels
        
        Args:
            channels: List of channels to unsubscribe from
            
        Returns:
            Unsubscription message dictionary
        """
        try:
            timestamp = int(time.time() * 1000)
            signature = self.generate_signature(timestamp, "", "")
            
            unsubscription_message = {
                "id": self._get_next_request_id(),
                "method": "unsubscribe",
                "params": {
                    "channels": channels
                },
                "api_key": self.api_key,
                "timestamp": timestamp,
                "signature": signature
            }
            
            logger.info(f"📡 Generated unsubscription message for channels: {', '.join(channels)}")
            return unsubscription_message
            
        except Exception as e:
            logger.error(f"❌ Error generating unsubscription message: {e}")
            raise
    
    def validate_signature(self, message: Dict[str, Any]) -> bool:
        """
        Validate signature in a received message
        
        Args:
            message: Message dictionary with signature
            
        Returns:
            True if signature is valid, False otherwise
        """
        try:
            timestamp = message.get("timestamp")
            received_signature = message.get("signature")
            
            if not timestamp or not received_signature:
                logger.warning("⚠️ Missing timestamp or signature in message")
                return False
            
            # Recreate the signature
            expected_signature = self.generate_signature(timestamp, "", "")
            
            # Compare signatures
            is_valid = hmac.compare_digest(expected_signature, received_signature)
            
            if is_valid:
                logger.debug("✅ Signature validation successful")
            else:
                logger.warning("⚠️ Signature validation failed")
            
            return is_valid
            
        except Exception as e:
            logger.error(f"❌ Error validating signature: {e}")
            return False
    
    def _get_next_request_id(self) -> int:
        """Get next request ID for message tracking"""
        request_id = self.request_id_counter
        self.request_id_counter += 1
        return request_id
    
    def get_supported_channels(self) -> List[str]:
        """Get list of supported user data channels"""
        return [
            "user.order",      # Order status updates
            "user.trade",      # Trade execution notifications
            "user.balance"     # Account balance updates
        ]
    
    def is_private_channel(self, channel: str) -> bool:
        """Check if a channel requires authentication"""
        private_channels = [
            "user.order",
            "user.trade", 
            "user.balance"
        ]
        return channel in private_channels
    
    def get_channel_description(self, channel: str) -> str:
        """Get human-readable description for a channel"""
        descriptions = {
            "user.order": "Order status updates (NEW, FILLED, CANCELLED, etc.)",
            "user.trade": "Trade execution notifications with fill details",
            "user.balance": "Account balance updates after trades"
        }
        return descriptions.get(channel, f"Unknown channel: {channel}")
    
    def create_heartbeat_message(self) -> Dict[str, Any]:
        """Create heartbeat message to maintain connection"""
        return {
            "id": self._get_next_request_id(),
            "method": "heartbeat"
        }
    
    def get_auth_status(self) -> Dict[str, Any]:
        """Get authentication manager status"""
        return {
            "api_key_configured": bool(self.api_key),
            "api_secret_configured": bool(self.api_secret),
            "supported_channels": self.get_supported_channels(),
            "request_counter": self.request_id_counter,
            "initialized_at": datetime.utcnow().isoformat()
        }
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get authentication metrics"""
        return {
            "requests_generated": self.request_id_counter - 1,
            "api_key_length": len(self.api_key) if self.api_key else 0,
            "api_secret_configured": bool(self.api_secret),
            "supported_channels_count": len(self.get_supported_channels())
        }