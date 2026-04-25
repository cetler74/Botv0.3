#!/usr/bin/env python3
"""
Test Price Service

This service provides a test price API that allows developers to override
real market prices for testing entry and exit strategies without affecting
production trading flows.

Author: Claude Code
Created: 2025-01-27
"""

import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Union
from dataclasses import dataclass, asdict
from enum import Enum
import json
import httpx
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import uvicorn

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Service configuration
SERVICE_NAME = "test-price-service"
SERVICE_VERSION = "1.0.0"
SERVICE_PORT = int(os.getenv("SERVICE_PORT", 8008))
CONFIG_SERVICE_URL = os.getenv("CONFIG_SERVICE_URL", "http://config-service:8001")

class TestModeStatus(str, Enum):
    """Test mode status enumeration"""
    DISABLED = "disabled"
    ENABLED = "enabled"
    ACTIVE = "active"

class PriceOverrideType(str, Enum):
    """Price override type enumeration"""
    FIXED = "fixed"           # Fixed price value
    PERCENTAGE = "percentage"  # Percentage change from current price
    SCENARIO = "scenario"      # Predefined scenario (e.g., "profit_2_percent")

@dataclass
class TestPriceOverride:
    """Test price override data structure"""
    exchange: str
    pair: str
    override_type: PriceOverrideType
    value: Union[float, str]  # Price value or scenario name
    original_price: Optional[float] = None
    created_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    description: Optional[str] = None

class TestPriceRequest(BaseModel):
    """Request model for setting test prices"""
    exchange: str = Field(..., description="Exchange name (e.g., 'binance', 'bybit')")
    pair: str = Field(..., description="Trading pair (e.g., 'BTC/USDC', 'ETH/USDC')")
    override_type: PriceOverrideType = Field(..., description="Type of price override")
    value: Union[float, str] = Field(..., description="Price value or scenario name")
    duration_minutes: Optional[int] = Field(60, description="Override duration in minutes")
    description: Optional[str] = Field(None, description="Description of the test scenario")

class TestPriceResponse(BaseModel):
    """Response model for test price operations"""
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None

class TestModeConfig(BaseModel):
    """Test mode configuration model"""
    enabled: bool = Field(False, description="Whether test mode is enabled")
    global_override: bool = Field(False, description="Whether to override all prices globally")
    default_duration_minutes: int = Field(60, description="Default override duration")

class TestPriceService:
    """Test Price Service - Manages price overrides for testing"""
    
    def __init__(self):
        self.test_mode_status = TestModeStatus.DISABLED
        self.price_overrides: Dict[str, TestPriceOverride] = {}
        self.config = TestModeConfig()
        self.exchange_service_url = os.getenv("EXCHANGE_SERVICE_URL", "http://exchange-service:8003")
        self.scenarios = self._initialize_scenarios()
        
    def _initialize_scenarios(self) -> Dict[str, Dict[str, Any]]:
        """Initialize predefined test scenarios"""
        return {
            "profit_0_5_percent": {
                "description": "0.5% profit scenario",
                "percentage_change": 0.005
            },
            "profit_1_percent": {
                "description": "1% profit scenario", 
                "percentage_change": 0.01
            },
            "profit_2_percent": {
                "description": "2% profit scenario",
                "percentage_change": 0.02
            },
            "profit_5_percent": {
                "description": "5% profit scenario",
                "percentage_change": 0.05
            },
            "loss_1_percent": {
                "description": "1% loss scenario",
                "percentage_change": -0.01
            },
            "loss_2_percent": {
                "description": "2% loss scenario", 
                "percentage_change": -0.02
            },
            "loss_5_percent": {
                "description": "5% loss scenario",
                "percentage_change": -0.05
            },
            "trailing_trigger": {
                "description": "Trailing trigger activation (0.7% profit)",
                "percentage_change": 0.007
            },
            "stop_loss_trigger": {
                "description": "Stop loss trigger (-3% loss)",
                "percentage_change": -0.03
            }
        }
    
    async def get_current_price(self, exchange: str, pair: str) -> Optional[float]:
        """Get current market price from exchange service"""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Convert pair format for exchange service
                exchange_symbol = pair.replace('/', '')
                
                response = await client.get(
                    f"{self.exchange_service_url}/api/v1/market/ticker/{exchange}/{exchange_symbol}"
                )
                
                if response.status_code == 200:
                    ticker_data = response.json()
                    return float(ticker_data.get('last', 0))
                else:
                    logger.warning(f"Failed to get price for {exchange}/{pair}: {response.status_code}")
                    return None
                    
        except Exception as e:
            logger.error(f"Error getting current price for {exchange}/{pair}: {e}")
            return None
    
    def _get_override_key(self, exchange: str, pair: str) -> str:
        """Generate unique key for price override"""
        return f"{exchange}:{pair}"
    
    async def set_price_override(self, request: TestPriceRequest) -> TestPriceResponse:
        """Set a price override for testing"""
        try:
            # Validate test mode is enabled
            if self.test_mode_status == TestModeStatus.DISABLED:
                return TestPriceResponse(
                    success=False,
                    message="Test mode is disabled. Enable test mode first."
                )
            
            # Get current price for percentage calculations
            current_price = await self.get_current_price(request.exchange, request.pair)
            if not current_price:
                return TestPriceResponse(
                    success=False,
                    message=f"Could not get current price for {request.exchange}/{request.pair}"
                )
            
            # Calculate override price based on type
            override_price = None
            if request.override_type == PriceOverrideType.FIXED:
                override_price = float(request.value)
            elif request.override_type == PriceOverrideType.PERCENTAGE:
                percentage = float(request.value)
                override_price = current_price * (1 + percentage)
            elif request.override_type == PriceOverrideType.SCENARIO:
                scenario_name = str(request.value)
                if scenario_name not in self.scenarios:
                    return TestPriceResponse(
                        success=False,
                        message=f"Unknown scenario: {scenario_name}. Available scenarios: {list(self.scenarios.keys())}"
                    )
                scenario = self.scenarios[scenario_name]
                percentage = scenario['percentage_change']
                override_price = current_price * (1 + percentage)
            
            # Create override
            override_key = self._get_override_key(request.exchange, request.pair)
            expires_at = datetime.utcnow() + timedelta(minutes=request.duration_minutes)
            
            override = TestPriceOverride(
                exchange=request.exchange,
                pair=request.pair,
                override_type=request.override_type,
                value=request.value,
                original_price=current_price,
                created_at=datetime.utcnow(),
                expires_at=expires_at,
                description=request.description
            )
            
            self.price_overrides[override_key] = override
            
            logger.info(f"✅ Set price override: {request.exchange}/{request.pair} = ${override_price:.6f} (was ${current_price:.6f})")
            
            return TestPriceResponse(
                success=True,
                message=f"Price override set successfully",
                data={
                    "exchange": request.exchange,
                    "pair": request.pair,
                    "original_price": current_price,
                    "override_price": override_price,
                    "percentage_change": ((override_price - current_price) / current_price) * 100,
                    "expires_at": expires_at.isoformat(),
                    "scenario": request.description or f"{request.override_type.value} override"
                }
            )
            
        except Exception as e:
            logger.error(f"Error setting price override: {e}")
            return TestPriceResponse(
                success=False,
                message=f"Error setting price override: {str(e)}"
            )
    
    async def get_price_override(self, exchange: str, pair: str) -> Optional[TestPriceOverride]:
        """Get price override for a specific exchange/pair"""
        override_key = self._get_override_key(exchange, pair)
        override = self.price_overrides.get(override_key)
        
        if override and override.expires_at and datetime.utcnow() > override.expires_at:
            # Override expired, remove it
            del self.price_overrides[override_key]
            return None
            
        return override
    
    async def clear_price_override(self, exchange: str, pair: str) -> TestPriceResponse:
        """Clear price override for a specific exchange/pair"""
        override_key = self._get_override_key(exchange, pair)
        
        if override_key in self.price_overrides:
            del self.price_overrides[override_key]
            logger.info(f"✅ Cleared price override for {exchange}/{pair}")
            return TestPriceResponse(
                success=True,
                message=f"Price override cleared for {exchange}/{pair}"
            )
        else:
            return TestPriceResponse(
                success=False,
                message=f"No price override found for {exchange}/{pair}"
            )
    
    async def clear_all_overrides(self) -> TestPriceResponse:
        """Clear all price overrides"""
        count = len(self.price_overrides)
        self.price_overrides.clear()
        logger.info(f"✅ Cleared all {count} price overrides")
        return TestPriceResponse(
            success=True,
            message=f"Cleared all {count} price overrides"
        )
    
    async def get_test_price(self, exchange: str, pair: str) -> Optional[float]:
        """Get test price (override if exists, otherwise None)"""
        override = await self.get_price_override(exchange, pair)
        
        if override:
            # Calculate current override price
            if override.override_type == PriceOverrideType.FIXED:
                return float(override.value)
            elif override.override_type == PriceOverrideType.PERCENTAGE:
                percentage = float(override.value)
                return override.original_price * (1 + percentage)
            elif override.override_type == PriceOverrideType.SCENARIO:
                scenario_name = str(override.value)
                if scenario_name in self.scenarios:
                    scenario = self.scenarios[scenario_name]
                    percentage = scenario['percentage_change']
                    return override.original_price * (1 + percentage)
        
        return None
    
    async def cleanup_expired_overrides(self):
        """Clean up expired price overrides"""
        current_time = datetime.utcnow()
        expired_keys = []
        
        for key, override in self.price_overrides.items():
            if override.expires_at and current_time > override.expires_at:
                expired_keys.append(key)
        
        for key in expired_keys:
            del self.price_overrides[key]
            logger.info(f"🧹 Cleaned up expired override: {key}")
    
    async def get_status(self) -> Dict[str, Any]:
        """Get service status and statistics"""
        await self.cleanup_expired_overrides()
        
        return {
            "service": SERVICE_NAME,
            "version": SERVICE_VERSION,
            "test_mode_status": self.test_mode_status,
            "active_overrides": len(self.price_overrides),
            "overrides": [
                {
                    "exchange": override.exchange,
                    "pair": override.pair,
                    "type": override.override_type,
                    "value": override.value,
                    "original_price": override.original_price,
                    "created_at": override.created_at.isoformat() if override.created_at else None,
                    "expires_at": override.expires_at.isoformat() if override.expires_at else None,
                    "description": override.description
                }
                for override in self.price_overrides.values()
            ],
            "available_scenarios": list(self.scenarios.keys())
        }

# Initialize FastAPI app
app = FastAPI(
    title="Test Price Service",
    description="Service for overriding prices during testing",
    version=SERVICE_VERSION
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize service
test_price_service = TestPriceService()

# Background task for cleanup
async def cleanup_task():
    """Background task to clean up expired overrides"""
    while True:
        try:
            await test_price_service.cleanup_expired_overrides()
            await asyncio.sleep(60)  # Clean up every minute
        except Exception as e:
            logger.error(f"Error in cleanup task: {e}")
            await asyncio.sleep(60)

@app.on_event("startup")
async def startup_event():
    """Startup event handler"""
    logger.info(f"🚀 Starting {SERVICE_NAME} v{SERVICE_VERSION} on port {SERVICE_PORT}")
    # Start background cleanup task
    asyncio.create_task(cleanup_task())

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": SERVICE_NAME,
        "version": SERVICE_VERSION,
        "timestamp": datetime.utcnow().isoformat()
    }

@app.get("/api/v1/status")
async def get_status():
    """Get service status and statistics"""
    return await test_price_service.get_status()

@app.post("/api/v1/test-mode/enable")
async def enable_test_mode():
    """Enable test mode"""
    test_price_service.test_mode_status = TestModeStatus.ENABLED
    logger.info("✅ Test mode enabled")
    return {"success": True, "message": "Test mode enabled"}

@app.post("/api/v1/test-mode/disable")
async def disable_test_mode():
    """Disable test mode and clear all overrides"""
    test_price_service.test_mode_status = TestModeStatus.DISABLED
    await test_price_service.clear_all_overrides()
    logger.info("❌ Test mode disabled - all overrides cleared")
    return {"success": True, "message": "Test mode disabled and all overrides cleared"}

@app.get("/api/v1/test-mode/status")
async def get_test_mode_status():
    """Get test mode status"""
    return {
        "test_mode_status": test_price_service.test_mode_status,
        "active_overrides": len(test_price_service.price_overrides)
    }

@app.post("/api/v1/prices/override", response_model=TestPriceResponse)
async def set_price_override(request: TestPriceRequest):
    """Set a price override for testing"""
    return await test_price_service.set_price_override(request)

@app.get("/api/v1/prices/override/{exchange}/{pair}")
async def get_price_override(exchange: str, pair: str):
    """Get price override for a specific exchange/pair"""
    override = await test_price_service.get_price_override(exchange, pair)
    
    if override:
        return {
            "found": True,
            "override": {
                "exchange": override.exchange,
                "pair": override.pair,
                "type": override.override_type,
                "value": override.value,
                "original_price": override.original_price,
                "created_at": override.created_at.isoformat() if override.created_at else None,
                "expires_at": override.expires_at.isoformat() if override.expires_at else None,
                "description": override.description
            }
        }
    else:
        return {"found": False}

@app.delete("/api/v1/prices/override/{exchange}/{pair}")
async def clear_price_override(exchange: str, pair: str):
    """Clear price override for a specific exchange/pair"""
    return await test_price_service.clear_price_override(exchange, pair)

@app.delete("/api/v1/prices/override")
async def clear_all_overrides():
    """Clear all price overrides"""
    return await test_price_service.clear_all_overrides()

@app.get("/api/v1/prices/test/{exchange}/{pair}")
async def get_test_price(exchange: str, pair: str):
    """Get test price for a specific exchange/pair (override if exists)"""
    test_price = await test_price_service.get_test_price(exchange, pair)
    
    if test_price:
        return {
            "test_price": test_price,
            "is_override": True,
            "source": "test_override"
        }
    else:
        return {
            "test_price": None,
            "is_override": False,
            "source": "no_override"
        }

@app.get("/api/v1/scenarios")
async def get_available_scenarios():
    """Get available test scenarios"""
    return {
        "scenarios": test_price_service.scenarios
    }

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=SERVICE_PORT,
        log_level="info",
        reload=False
    )
