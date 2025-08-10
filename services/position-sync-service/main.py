"""
Position Synchronization Service
Synchronizes individual asset positions between exchanges and database
"""

import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any
import json
import httpx
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
import uvicorn
import os

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="Position Sync Service",
    description="Real-time asset position synchronization between exchanges and database",
    version="1.0.0"
)

# Service URLs
database_service_url = os.getenv("DATABASE_SERVICE_URL", "http://database-service:8002")
exchange_service_url = os.getenv("EXCHANGE_SERVICE_URL", "http://exchange-service:8003")

class AssetPosition(BaseModel):
    """Individual asset position model"""
    exchange: str
    asset: str
    free: float
    used: float
    total: float
    last_updated: str

class PositionSyncService:
    """Core position synchronization service"""
    
    def __init__(self):
        self.sync_interval = 60  # 1 minute for position sync
        self.is_running = False
        self.positions_cache = {}
        
    async def start_sync_loop(self):
        """Start the continuous position synchronization loop"""
        logger.info("🔄 Starting position synchronization service...")
        
        self.is_running = True
        
        while self.is_running:
            try:
                await self.sync_all_positions()
                await asyncio.sleep(self.sync_interval)
            except Exception as e:
                logger.error(f"❌ Error in position sync loop: {e}")
                await asyncio.sleep(60)  # Wait longer on error
    
    async def sync_all_positions(self):
        """Sync positions for all exchanges"""
        exchanges = ['cryptocom', 'binance', 'bybit']
        
        for exchange in exchanges:
            try:
                await self.sync_exchange_positions(exchange)
            except Exception as e:
                logger.error(f"❌ Failed to sync {exchange} positions: {e}")
    
    async def sync_exchange_positions(self, exchange: str):
        """Sync positions for a specific exchange"""
        logger.info(f"🔄 Syncing {exchange} asset positions...")
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                # Get current balances from exchange
                exchange_response = await client.get(f"{exchange_service_url}/api/v1/account/balance/{exchange}")
                if exchange_response.status_code != 200:
                    logger.error(f"❌ Failed to get {exchange} balances: {exchange_response.status_code}")
                    return
                
                exchange_data = exchange_response.json()
                
                # Extract individual asset balances
                free_balances = exchange_data.get('free', {})
                used_balances = exchange_data.get('used', {})
                total_balances = exchange_data.get('total', {})
                
                if not free_balances:
                    logger.warning(f"⚠️  No free balances found for {exchange}")
                    return
                
                # Sync each asset position
                sync_count = 0
                for asset, free_amount in free_balances.items():
                    try:
                        # Handle None values from exchange APIs
                        free_val = free_amount if free_amount is not None else 0
                        used_val = used_balances.get(asset, 0) if used_balances.get(asset) is not None else 0
                        total_val = total_balances.get(asset, free_val) if total_balances.get(asset) is not None else free_val
                        
                        # Convert to float safely
                        free_float = float(free_val)
                        used_float = float(used_val)
                        total_float = float(total_val)
                        
                        if free_float > 0:  # Only sync assets with available balance
                            position = AssetPosition(
                                exchange=exchange,
                                asset=asset,
                                free=free_float,
                                used=used_float,
                                total=total_float,
                                last_updated=datetime.utcnow().isoformat() + 'Z'
                            )
                            
                            await self.update_database_position(position)
                            self.positions_cache[f"{exchange}_{asset}"] = position
                            sync_count += 1
                            
                    except Exception as e:
                        logger.error(f"❌ Failed to sync {exchange} {asset}: {e}")
                
                logger.info(f"✅ Synced {sync_count} asset positions for {exchange}")
                
            except Exception as e:
                logger.error(f"❌ Error syncing {exchange} positions: {e}")
    
    async def update_database_position(self, position: AssetPosition):
        """Update or create asset position in database"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Try to update existing position first
                update_data = {
                    "exchange": position.exchange,
                    "asset": position.asset,
                    "free_balance": position.free,
                    "used_balance": position.used,
                    "total_balance": position.total,
                    "last_updated": position.last_updated
                }
                
                # POST to create/update position
                response = await client.post(
                    f"{database_service_url}/api/v1/asset-positions",
                    json=update_data
                )
                
                if response.status_code in [200, 201]:
                    logger.debug(f"✅ Updated {position.exchange} {position.asset}: {position.free} free")
                    return True
                else:
                    logger.error(f"❌ Failed to update {position.exchange} {position.asset}: {response.status_code}")
                    logger.debug(f"Response: {response.text}")
                    return False
                    
        except Exception as e:
            logger.error(f"❌ Database update error for {position.exchange} {position.asset}: {e}")
            return False
    
    async def get_available_balance(self, exchange: str, asset: str) -> float:
        """Get available balance for a specific asset on an exchange"""
        cache_key = f"{exchange}_{asset}"
        
        if cache_key in self.positions_cache:
            cached_position = self.positions_cache[cache_key]
            # Check if cache is recent (less than 5 minutes old)
            cache_time = datetime.fromisoformat(cached_position.last_updated.replace('Z', ''))
            if (datetime.utcnow() - cache_time).total_seconds() < 300:
                return cached_position.free
        
        # Cache miss or stale - fetch fresh data
        try:
            await self.sync_exchange_positions(exchange)
            if cache_key in self.positions_cache:
                return self.positions_cache[cache_key].free
        except Exception as e:
            logger.error(f"❌ Error getting fresh balance for {exchange} {asset}: {e}")
        
        return 0.0
    
    async def validate_order_amount(self, exchange: str, asset: str, required_amount: float) -> bool:
        """Validate if an order amount is possible given available balance"""
        available = await self.get_available_balance(exchange, asset)
        
        if available >= required_amount:
            logger.info(f"✅ Order validation passed: {required_amount} {asset} <= {available} available on {exchange}")
            return True
        else:
            logger.warning(f"⚠️  Order validation failed: {required_amount} {asset} > {available} available on {exchange}")
            return False

# Global sync service instance
sync_service = PositionSyncService()

# API Endpoints
@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy" if sync_service.is_running else "stopped",
        "service": "position-sync-service",
        "version": "1.0.0",
        "sync_interval": sync_service.sync_interval,
        "is_running": sync_service.is_running,
        "cached_positions": len(sync_service.positions_cache)
    }

@app.post("/sync/start")
async def start_sync(background_tasks: BackgroundTasks):
    """Start the position synchronization service"""
    if not sync_service.is_running:
        background_tasks.add_task(sync_service.start_sync_loop)
        return {"message": "Position sync service started"}
    return {"message": "Position sync service already running"}

@app.post("/sync/stop")
async def stop_sync():
    """Stop the position synchronization service"""
    sync_service.is_running = False
    return {"message": "Position sync service stopped"}

@app.post("/sync/manual")
async def manual_sync():
    """Manually trigger position synchronization"""
    try:
        await sync_service.sync_all_positions()
        return {"message": "Manual position sync completed"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/positions/{exchange}")
async def get_exchange_positions(exchange: str):
    """Get all cached positions for an exchange"""
    positions = {}
    for key, position in sync_service.positions_cache.items():
        if position.exchange == exchange:
            positions[position.asset] = {
                "free": position.free,
                "used": position.used,
                "total": position.total,
                "last_updated": position.last_updated
            }
    return {"exchange": exchange, "positions": positions}

@app.get("/positions/{exchange}/{asset}")
async def get_asset_position(exchange: str, asset: str):
    """Get position for a specific asset on an exchange"""
    cache_key = f"{exchange}_{asset}"
    if cache_key in sync_service.positions_cache:
        position = sync_service.positions_cache[cache_key]
        return {
            "exchange": exchange,
            "asset": asset,
            "free": position.free,
            "used": position.used,
            "total": position.total,
            "last_updated": position.last_updated
        }
    else:
        # Try to get fresh data
        balance = await sync_service.get_available_balance(exchange, asset)
        return {
            "exchange": exchange,
            "asset": asset,
            "free": balance,
            "used": 0.0,
            "total": balance,
            "last_updated": datetime.utcnow().isoformat() + 'Z'
        }

@app.post("/validate-order")
async def validate_order(order_data: dict):
    """Validate if an order is possible given available balances"""
    exchange = order_data.get('exchange')
    symbol = order_data.get('symbol', '')
    side = order_data.get('side')
    amount = float(order_data.get('amount', 0))
    
    if not all([exchange, symbol, side, amount]):
        raise HTTPException(status_code=400, detail="Missing required fields: exchange, symbol, side, amount")
    
    # Determine which asset is needed
    if '/' in symbol:
        base_asset, quote_asset = symbol.split('/')
    else:
        raise HTTPException(status_code=400, detail="Invalid symbol format, expected BASE/QUOTE")
    
    if side.lower() == 'buy':
        # For buy orders, we need quote asset (usually USD)
        required_asset = quote_asset
        # For buy orders, amount is in quote currency, but we need to estimate
        # For now, we'll check if quote balance > amount (approximation)
        required_amount = amount
    else:
        # For sell orders, we need base asset
        required_asset = base_asset
        required_amount = amount
    
    is_valid = await sync_service.validate_order_amount(exchange, required_asset, required_amount)
    available = await sync_service.get_available_balance(exchange, required_asset)
    
    return {
        "valid": is_valid,
        "exchange": exchange,
        "asset": required_asset,
        "required_amount": required_amount,
        "available_amount": available,
        "validation_timestamp": datetime.utcnow().isoformat() + 'Z'
    }

@app.get("/sync/status")
async def get_sync_status():
    """Get current sync service status"""
    return {
        "is_running": sync_service.is_running,
        "sync_interval": sync_service.sync_interval,
        "cached_positions": len(sync_service.positions_cache),
        "exchanges": list(set(pos.exchange for pos in sync_service.positions_cache.values())),
        "last_sync": datetime.utcnow().isoformat() + 'Z'
    }

# Startup event
@app.on_event("startup")
async def startup_event():
    """Start sync service on startup"""
    logger.info("🚀 Position Sync Service starting up...")
    # Auto-start sync service
    asyncio.create_task(sync_service.start_sync_loop())

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8009,
        reload=True,
        log_level="info"
    )