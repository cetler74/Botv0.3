"""
Web Dashboard Service for the Multi-Exchange Trading Bot
User interface and real-time monitoring
"""

import asyncio
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
import json
import httpx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import uvicorn
import os

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="Web Dashboard Service",
    description="User interface and real-time monitoring",
    version="1.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Templates
templates = Jinja2Templates(directory="templates")

# Service URLs
config_service_url = os.getenv("CONFIG_SERVICE_URL", "http://config-service:8001")
database_service_url = os.getenv("DATABASE_SERVICE_URL", "http://database_service:8002")
exchange_service_url = os.getenv("EXCHANGE_SERVICE_URL", "http://exchange-service:8003")
strategy_service_url = os.getenv("STRATEGY_SERVICE_URL", "http://strategy-service:8004")
orchestrator_service_url = os.getenv("ORCHESTRATOR_SERVICE_URL", "http://orchestrator-service:8005")

# WebSocket connections
active_connections: List[WebSocket] = []

class WebSocketManager:
    """Manages WebSocket connections for real-time updates"""
    
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"WebSocket connected. Total connections: {len(self.active_connections)}")
        
    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"WebSocket disconnected. Total connections: {len(self.active_connections)}")
            
    async def broadcast(self, message: Dict[str, Any]):
        """Broadcast message to all connected clients"""
        if not self.active_connections:
            return
            
        message_json = json.dumps(message)
        disconnected = []
        
        for connection in self.active_connections:
            try:
                await connection.send_text(message_json)
            except Exception as e:
                logger.error(f"Error broadcasting message: {e}")
                disconnected.append(connection)
                
        # Remove disconnected connections
        for connection in disconnected:
            self.disconnect(connection)

websocket_manager = WebSocketManager()

# Data Models
class HealthResponse(BaseModel):
    status: str
    timestamp: datetime
    version: str
    services_connected: int
    total_services: int

# API Endpoints
@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint"""
    # Check all service health
    services = [
        ("config", config_service_url),
        ("database", database_service_url),
        ("exchange", exchange_service_url),
        ("strategy", strategy_service_url),
        ("orchestrator", orchestrator_service_url)
    ]
    
    connected_count = 0
    for service_name, service_url in services:
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{service_url}/health", timeout=5.0)
                if response.status_code == 200:
                    connected_count += 1
        except Exception as e:
            logger.warning(f"Service {service_name} health check failed: {e}")
    
    return HealthResponse(
        status="healthy" if connected_count >= 3 else "degraded",
        timestamp=datetime.utcnow(),
        version="1.0.0",
        services_connected=connected_count,
        total_services=len(services)
    )

@app.get("/ready")
async def readiness_check():
    """Readiness check endpoint"""
    return {"status": "ready"}

@app.get("/live")
async def liveness_check():
    """Liveness check endpoint"""
    return {"status": "alive"}

# Dashboard Pages
@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Main dashboard page"""
    return templates.TemplateResponse("dashboard.html", {"request": request})

@app.get("/trades", response_class=HTMLResponse)
async def trades_page(request: Request):
    """Trades page"""
    return templates.TemplateResponse("trades.html", {"request": request})

@app.get("/portfolio", response_class=HTMLResponse)
async def portfolio_page(request: Request):
    """Portfolio page"""
    return templates.TemplateResponse("portfolio.html", {"request": request})

@app.get("/strategies", response_class=HTMLResponse)
async def strategies_page(request: Request):
    """Strategies page"""
    return templates.TemplateResponse("strategies.html", {"request": request})

@app.get("/alerts", response_class=HTMLResponse)
async def alerts_page(request: Request):
    """Alerts page"""
    return templates.TemplateResponse("alerts.html", {"request": request})

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    """Settings page"""
    return templates.TemplateResponse("settings.html", {"request": request})

# API Proxies (forward to other services)
@app.get("/api/portfolio")
async def get_portfolio():
    """Get portfolio summary"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{database_service_url}/api/v1/portfolio/summary")
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.error(f"Error getting portfolio: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/trades")
async def get_trades(exchange: Optional[str] = None, limit: int = 50):
    """Get recent trades"""
    try:
        async with httpx.AsyncClient() as client:
            if exchange:
                response = await client.get(f"{database_service_url}/api/v1/trades/exchange/{exchange}", params={"limit": limit})
            else:
                response = await client.get(f"{database_service_url}/api/v1/trades", params={"limit": limit})
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.error(f"Error getting trades: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/exchanges")
async def get_exchanges():
    """Get exchange information"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{exchange_service_url}/api/v1/exchanges")
            response.raise_for_status()
            exchanges = response.json()['exchanges']
            
            exchange_info = {}
            for exchange_name in exchanges:
                try:
                    health_response = await client.get(f"{exchange_service_url}/api/v1/exchanges/{exchange_name}/health")
                    if health_response.status_code == 200:
                        exchange_info[exchange_name] = health_response.json()
                    else:
                        exchange_info[exchange_name] = {"status": "unknown"}
                except Exception as e:
                    logger.error(f"Error getting info for {exchange_name}: {e}")
                    exchange_info[exchange_name] = {"error": str(e)}
                    
            return exchange_info
    except Exception as e:
        logger.error(f"Error getting exchanges: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/strategies")
async def get_strategies():
    """Get strategy information"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{strategy_service_url}/api/v1/strategies")
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.error(f"Error getting strategies: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/alerts")
async def get_alerts(level: Optional[str] = None, limit: int = 50):
    """Get alerts"""
    try:
        async with httpx.AsyncClient() as client:
            params = {"limit": limit}
            if level:
                params["level"] = level
            response = await client.get(f"{database_service_url}/api/v1/alerts", params=params)
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.error(f"Error getting alerts: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/trading/status")
async def get_trading_status():
    """Get trading status"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{orchestrator_service_url}/api/v1/trading/status")
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.error(f"Error getting trading status: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/trading/active-trades")
async def get_active_trades():
    """Get active trades"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{orchestrator_service_url}/api/v1/trading/active-trades")
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.error(f"Error getting active trades: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/risk/exposure")
async def get_risk_exposure():
    """Get risk exposure"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{orchestrator_service_url}/api/v1/risk/exposure")
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.error(f"Error getting risk exposure: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Control Endpoints
@app.post("/api/control/start")
async def start_bot():
    """Start the trading bot"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{orchestrator_service_url}/api/v1/trading/start")
            response.raise_for_status()
            
        # Broadcast update
        await websocket_manager.broadcast({
            "type": "trading_status_update",
            "data": {"status": "started", "timestamp": datetime.utcnow().isoformat()}
        })
        
        return {"message": "Trading bot started successfully"}
    except Exception as e:
        logger.error(f"Error starting bot: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/control/stop")
async def stop_bot():
    """Stop the trading bot"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{orchestrator_service_url}/api/v1/trading/stop")
            response.raise_for_status()
            
        # Broadcast update
        await websocket_manager.broadcast({
            "type": "trading_status_update",
            "data": {"status": "stopped", "timestamp": datetime.utcnow().isoformat()}
        })
        
        return {"message": "Trading bot stopped successfully"}
    except Exception as e:
        logger.error(f"Error stopping bot: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/control/emergency-stop")
async def emergency_stop():
    """Emergency stop the trading bot"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{orchestrator_service_url}/api/v1/trading/emergency-stop")
            response.raise_for_status()
            
        # Broadcast update
        await websocket_manager.broadcast({
            "type": "emergency_stop",
            "data": {"status": "emergency_stop", "timestamp": datetime.utcnow().isoformat()}
        })
        
        return {"message": "Emergency stop executed successfully"}
    except Exception as e:
        logger.error(f"Error executing emergency stop: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# WebSocket Endpoints
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time updates"""
    await websocket_manager.connect(websocket)
    try:
        while True:
            # Keep connection alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        websocket_manager.disconnect(websocket)

@app.websocket("/ws/dashboard")
async def dashboard_websocket(websocket: WebSocket):
    """Dashboard WebSocket endpoint"""
    await websocket_manager.connect(websocket)
    try:
        while True:
            # Keep connection alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        websocket_manager.disconnect(websocket)

@app.websocket("/ws/trades")
async def trades_websocket(websocket: WebSocket):
    """Trades WebSocket endpoint"""
    await websocket_manager.connect(websocket)
    try:
        while True:
            # Keep connection alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        websocket_manager.disconnect(websocket)

@app.websocket("/ws/portfolio")
async def portfolio_websocket(websocket: WebSocket):
    """Portfolio WebSocket endpoint"""
    await websocket_manager.connect(websocket)
    try:
        while True:
            # Keep connection alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        websocket_manager.disconnect(websocket)

@app.websocket("/ws/alerts")
async def alerts_websocket(websocket: WebSocket):
    """Alerts WebSocket endpoint"""
    await websocket_manager.connect(websocket)
    try:
        while True:
            # Keep connection alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        websocket_manager.disconnect(websocket)

# Background task for broadcasting updates
async def broadcast_updates():
    """Background task to broadcast periodic updates"""
    while True:
        try:
            # Get latest data
            portfolio = await get_portfolio()
            trading_status = await get_trading_status()
            risk_exposure = await get_risk_exposure()
            
            # Broadcast portfolio update
            await websocket_manager.broadcast({
                "type": "portfolio_update",
                "data": portfolio
            })
            
            # Broadcast trading status update
            await websocket_manager.broadcast({
                "type": "trading_status_update",
                "data": trading_status
            })
            
            # Broadcast risk exposure update
            await websocket_manager.broadcast({
                "type": "risk_exposure_update",
                "data": risk_exposure
            })
            
            # Wait before next update
            await asyncio.sleep(30)  # Update every 30 seconds
            
        except Exception as e:
            logger.error(f"Error in broadcast updates: {e}")
            await asyncio.sleep(60)  # Wait longer on error

# Configuration endpoints
@app.get("/api/config/all")
async def get_all_config():
    """Get all configuration (safe version)"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{config_service_url}/api/v1/config/all")
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.error(f"Error getting config: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/config/update")
async def update_config(path: str, value: Any):
    """Update configuration"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.put(f"{config_service_url}/api/v1/config/update", 
                                      json={"path": path, "value": value, "component": "web_dashboard"})
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.error(f"Error updating config: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Strategy management endpoints
@app.post("/api/strategies/{strategy_name}/enable")
async def enable_strategy(strategy_name: str):
    """Enable a strategy"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{strategy_service_url}/api/v1/strategies/{strategy_name}/enable")
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.error(f"Error enabling strategy {strategy_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/strategies/{strategy_name}/disable")
async def disable_strategy(strategy_name: str):
    """Disable a strategy"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(f"{strategy_service_url}/api/v1/strategies/{strategy_name}/disable")
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.error(f"Error disabling strategy {strategy_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Risk management endpoints
@app.get("/api/risk/limits")
async def get_risk_limits():
    """Get risk limits"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{orchestrator_service_url}/api/v1/risk/limits")
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.error(f"Error getting risk limits: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/risk/limits")
async def update_risk_limits(limits: Dict[str, Any]):
    """Update risk limits"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.put(f"{orchestrator_service_url}/api/v1/risk/limits", json=limits)
            response.raise_for_status()
            return response.json()
    except Exception as e:
        logger.error(f"Error updating risk limits: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Startup and shutdown events
@app.on_event("startup")
async def startup_event():
    """Start background tasks on startup"""
    asyncio.create_task(broadcast_updates())
    logger.info("Web dashboard service started")

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    # Close all WebSocket connections
    for connection in websocket_manager.active_connections[:]:
        websocket_manager.disconnect(connection)
    logger.info("Web dashboard service shutdown complete")

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8006,
        reload=True,
        log_level="info"
    ) 