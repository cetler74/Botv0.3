"""
Binance Listen Key Manager - Version 2.5.0
Manages Binance User Data Stream listen keys with automatic refresh and encryption
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import aiohttp
import base64
from cryptography.fernet import Fernet
import os
import json

logger = logging.getLogger(__name__)

class ListenKeyManager:
    """
    Manages Binance User Data Stream listen keys with automatic refresh
    
    Features:
    - Automatic key creation and refresh (every 50 minutes)
    - Encrypted storage of listen keys
    - Error recovery and retry logic
    - Health monitoring and metrics
    """
    
    def __init__(self, api_key: str, api_secret: str, base_url: str = "https://api.binance.com"):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url
        self.listen_key: Optional[str] = None
        self.listen_key_created_at: Optional[datetime] = None
        self.refresh_interval = int(os.getenv("BINANCE_LISTEN_KEY_REFRESH_INTERVAL", "3000"))  # 50 minutes
        self.refresh_task: Optional[asyncio.Task] = None
        self.is_running = False
        
        # Encryption for listen key storage
        encryption_key = os.getenv("BINANCE_LISTEN_KEY_ENCRYPTION_KEY")
        if encryption_key:
            self.cipher = Fernet(encryption_key.encode())
        else:
            # Generate new key if not provided (for development only)
            key = Fernet.generate_key()
            self.cipher = Fernet(key)
            logger.warning("No encryption key provided, generated new key. Set BINANCE_LISTEN_KEY_ENCRYPTION_KEY for production!")
            logger.info(f"Generated encryption key (save this): {key.decode()}")
        
        # Metrics
        self.metrics = {
            "keys_created": 0,
            "keys_refreshed": 0, 
            "refresh_failures": 0,
            "last_refresh_time": None,
            "current_key_age": 0
        }
    
    async def start(self) -> bool:
        """Start the listen key manager with automatic refresh"""
        try:
            logger.info("🔑 Starting Binance Listen Key Manager v2.5.0")
            
            # Create initial listen key
            success = await self.create_listen_key()
            if not success:
                logger.error("❌ Failed to create initial listen key")
                return False
            
            # Start refresh task
            self.is_running = True
            self.refresh_task = asyncio.create_task(self._refresh_loop())
            
            logger.info(f"✅ Listen Key Manager started successfully")
            logger.info(f"   Listen Key: {self.listen_key[:8]}...{self.listen_key[-4:] if self.listen_key else 'None'}")
            logger.info(f"   Refresh Interval: {self.refresh_interval}s ({self.refresh_interval/60:.1f} minutes)")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to start Listen Key Manager: {e}")
            return False
    
    async def stop(self):
        """Stop the listen key manager and cleanup resources"""
        logger.info("🛑 Stopping Listen Key Manager")
        
        self.is_running = False
        
        # Cancel refresh task
        if self.refresh_task and not self.refresh_task.done():
            self.refresh_task.cancel()
            try:
                await self.refresh_task
            except asyncio.CancelledError:
                pass
        
        # Delete listen key from Binance
        if self.listen_key:
            await self.delete_listen_key()
        
        logger.info("✅ Listen Key Manager stopped")
    
    async def create_listen_key(self) -> bool:
        """Create a new listen key from Binance API"""
        try:
            logger.info("🔐 Creating new Binance listen key")
            
            headers = {
                "X-MBX-APIKEY": self.api_key,
                "Content-Type": "application/x-www-form-urlencoded"
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/api/v3/userDataStream",
                    headers=headers
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        self.listen_key = data.get("listenKey")
                        self.listen_key_created_at = datetime.utcnow()
                        
                        # Update metrics
                        self.metrics["keys_created"] += 1
                        self.metrics["last_refresh_time"] = self.listen_key_created_at.isoformat()
                        
                        logger.info(f"✅ Listen key created successfully: {self.listen_key[:8]}...{self.listen_key[-4:]}")
                        return True
                    else:
                        error_text = await response.text()
                        logger.error(f"❌ Failed to create listen key: {response.status} - {error_text}")
                        return False
                        
        except Exception as e:
            logger.error(f"❌ Error creating listen key: {e}")
            return False
    
    async def refresh_listen_key(self) -> bool:
        """Refresh the current listen key to extend its lifetime"""
        if not self.listen_key:
            logger.warning("⚠️ No listen key to refresh, creating new one")
            return await self.create_listen_key()
        
        try:
            logger.info(f"🔄 Refreshing listen key: {self.listen_key[:8]}...{self.listen_key[-4:]}")
            
            headers = {
                "X-MBX-APIKEY": self.api_key,
                "Content-Type": "application/x-www-form-urlencoded"
            }
            
            data = f"listenKey={self.listen_key}"
            
            async with aiohttp.ClientSession() as session:
                async with session.put(
                    f"{self.base_url}/api/v3/userDataStream",
                    headers=headers,
                    data=data
                ) as response:
                    if response.status == 200:
                        self.listen_key_created_at = datetime.utcnow()
                        
                        # Update metrics
                        self.metrics["keys_refreshed"] += 1
                        self.metrics["last_refresh_time"] = self.listen_key_created_at.isoformat()
                        
                        logger.info("✅ Listen key refreshed successfully")
                        return True
                    else:
                        error_text = await response.text()
                        logger.error(f"❌ Failed to refresh listen key: {response.status} - {error_text}")
                        
                        # Update metrics
                        self.metrics["refresh_failures"] += 1
                        
                        # Try to create new key if refresh failed
                        logger.info("🔄 Refresh failed, attempting to create new listen key")
                        return await self.create_listen_key()
                        
        except Exception as e:
            logger.error(f"❌ Error refreshing listen key: {e}")
            self.metrics["refresh_failures"] += 1
            return False
    
    async def delete_listen_key(self) -> bool:
        """Delete the current listen key from Binance"""
        if not self.listen_key:
            return True
            
        try:
            logger.info(f"🗑️ Deleting listen key: {self.listen_key[:8]}...{self.listen_key[-4:]}")
            
            headers = {
                "X-MBX-APIKEY": self.api_key,
                "Content-Type": "application/x-www-form-urlencoded"
            }
            
            data = f"listenKey={self.listen_key}"
            
            async with aiohttp.ClientSession() as session:
                async with session.delete(
                    f"{self.base_url}/api/v3/userDataStream",
                    headers=headers,
                    data=data
                ) as response:
                    if response.status == 200:
                        logger.info("✅ Listen key deleted successfully")
                        self.listen_key = None
                        self.listen_key_created_at = None
                        return True
                    else:
                        error_text = await response.text()
                        logger.warning(f"⚠️ Failed to delete listen key: {response.status} - {error_text}")
                        return False
                        
        except Exception as e:
            logger.error(f"❌ Error deleting listen key: {e}")
            return False
    
    async def _refresh_loop(self):
        """Background task to automatically refresh listen key"""
        logger.info(f"🔄 Starting automatic refresh loop (interval: {self.refresh_interval}s)")
        
        while self.is_running:
            try:
                await asyncio.sleep(self.refresh_interval)
                
                if not self.is_running:
                    break
                
                success = await self.refresh_listen_key()
                if not success:
                    logger.error("❌ Failed to refresh listen key in background task")
                    
                    # Wait shorter interval before retry
                    await asyncio.sleep(60)  # 1 minute retry
                
            except asyncio.CancelledError:
                logger.info("🛑 Refresh loop cancelled")
                break
            except Exception as e:
                logger.error(f"❌ Error in refresh loop: {e}")
                await asyncio.sleep(60)  # Wait before retrying
    
    def get_listen_key(self) -> Optional[str]:
        """Get the current listen key"""
        return self.listen_key
    
    def get_websocket_url(self) -> Optional[str]:
        """Get the WebSocket URL with current listen key"""
        if not self.listen_key:
            return None
        return f"wss://stream.binance.com:9443/ws/{self.listen_key}"
    
    def is_key_valid(self) -> bool:
        """Check if current listen key is valid and not expired"""
        if not self.listen_key or not self.listen_key_created_at:
            return False
        
        # Keys are valid for 60 minutes, we refresh at 50 minutes
        age = (datetime.utcnow() - self.listen_key_created_at).total_seconds()
        self.metrics["current_key_age"] = int(age)
        
        # Consider key invalid if older than 55 minutes (safety buffer)
        return age < 3300  # 55 minutes
    
    def get_encrypted_key(self) -> Optional[str]:
        """Get encrypted version of listen key for secure storage"""
        if not self.listen_key:
            return None
        try:
            encrypted = self.cipher.encrypt(self.listen_key.encode())
            return base64.b64encode(encrypted).decode()
        except Exception as e:
            logger.error(f"❌ Error encrypting listen key: {e}")
            return None
    
    def decrypt_key(self, encrypted_key: str) -> Optional[str]:
        """Decrypt a previously encrypted listen key"""
        try:
            encrypted_bytes = base64.b64decode(encrypted_key.encode())
            decrypted = self.cipher.decrypt(encrypted_bytes)
            return decrypted.decode()
        except Exception as e:
            logger.error(f"❌ Error decrypting listen key: {e}")
            return None
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get listen key manager metrics for monitoring"""
        return {
            **self.metrics,
            "is_running": self.is_running,
            "has_key": self.listen_key is not None,
            "key_valid": self.is_key_valid(),
            "websocket_url": self.get_websocket_url() is not None
        }