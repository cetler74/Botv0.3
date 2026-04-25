"""
Web UI for the Multi-Exchange Trading Bot
Provides a beautiful interface for monitoring and controlling the bot
"""

import asyncio
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta
import json
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import uvicorn
import os
import sys

# Add the project root to the path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config_manager import ConfigManager
from core.database_manager import DatabaseManager
from core.exchange_manager import ExchangeManager
from core.strategy_manager import StrategyManager

logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(title="Trading Bot Dashboard", version="1.0.0")

# Mount static files
app.mount("/static", StaticFiles(directory="web/static"), name="static")

# Templates
templates = Jinja2Templates(directory="web/templates")

# Global managers
config_manager = None
database_manager = None
exchange_manager = None
strategy_manager = None

# WebSocket connections
active_connections: List[WebSocket] = []


class WebSocketManager:
    """Manages WebSocket connections for real-time updates"""
    
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        
    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        
    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            
    async def broadcast(self, message: Dict[str, Any]):
        """Broadcast message to all connected clients"""
        for connection in self.active_connections:
            try:
                await connection.send_text(json.dumps(message))
            except Exception as e:
                logger.error(f"Error broadcasting message: {e}")
                self.disconnect(connection)


websocket_manager = WebSocketManager()


@app.on_event("startup")
async def startup_event():
    """Initialize components on startup"""
    global config_manager, database_manager, exchange_manager, strategy_manager
    
    try:
        # Initialize configuration manager
        config_manager = ConfigManager("config/config.yaml")
        
        # Initialize database manager
        db_config = config_manager.get_database_config()
        database_manager = DatabaseManager(db_config)
        
        # Initialize exchange manager
        exchange_manager = ExchangeManager(
            config_manager.config_data, 
            database_manager
        )
        
        # Initialize strategy manager
        strategy_manager = StrategyManager(
            config_manager.config_data,
            exchange_manager,
            database_manager
        )
        
        logger.info("Web UI components initialized successfully")
        
    except Exception as e:
        logger.error(f"Failed to initialize Web UI components: {e}")


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    try:
        if exchange_manager:
            await exchange_manager.close()
        if database_manager:
            await database_manager.close()
        logger.info("Web UI components cleaned up")
    except Exception as e:
        logger.error(f"Error during Web UI shutdown: {e}")


# Routes
@app.get("/", response_class=HTMLResponse)
async def dashboard(request):
    """Main dashboard page"""
    return templates.TemplateResponse("dashboard.html", {"request": request})


@app.get("/api/portfolio")
async def get_portfolio():
    """Get portfolio summary"""
    try:
        if not database_manager:
            raise HTTPException(status_code=500, detail="Database manager not initialized")
            
        portfolio = await database_manager.get_portfolio_summary()
        return portfolio
        
    except Exception as e:
        logger.error(f"Error getting portfolio: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/exchanges")
async def get_exchanges():
    """Get exchange information"""
    try:
        if not exchange_manager:
            raise HTTPException(status_code=500, detail="Exchange manager not initialized")
            
        exchanges = config_manager.get_all_exchanges()
        exchange_info = {}
        
        for exchange_name in exchanges:
            try:
                info = await exchange_manager.get_exchange_info(exchange_name)
                exchange_info[exchange_name] = info
            except Exception as e:
                logger.error(f"Error getting info for {exchange_name}: {e}")
                exchange_info[exchange_name] = {"error": str(e)}
                
        return exchange_info
        
    except Exception as e:
        logger.error(f"Error getting exchanges: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/trades")
async def get_trades(exchange: Optional[str] = None, limit: int = 50):
    """Get recent trades"""
    try:
        if not database_manager:
            raise HTTPException(status_code=500, detail="Database manager not initialized")
            
        if exchange:
            trades = await database_manager.get_trades_by_exchange(exchange, limit)
        else:
            trades = await database_manager.get_open_trades()
            
        return {"trades": trades}
        
    except Exception as e:
        logger.error(f"Error getting trades: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/strategies")
async def get_strategies():
    """Get strategy information"""
    try:
        if not strategy_manager:
            raise HTTPException(status_code=500, detail="Strategy manager not initialized")
            
        enabled_strategies = await strategy_manager.get_enabled_strategies()
        strategy_status = await strategy_manager.get_strategy_status()
        
        return {
            "enabled_strategies": enabled_strategies,
            "strategy_status": strategy_status
        }
        
    except Exception as e:
        logger.error(f"Error getting strategies: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/alerts")
async def get_alerts(level: Optional[str] = None):
    """Get alerts"""
    try:
        if not database_manager:
            raise HTTPException(status_code=500, detail="Database manager not initialized")
            
        alerts = await database_manager.get_unresolved_alerts(level)
        return {"alerts": alerts}
        
    except Exception as e:
        logger.error(f"Error getting alerts: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/performance/{exchange_name}")
async def get_exchange_performance(exchange_name: str, days: int = 30):
    """Get performance metrics for an exchange"""
    try:
        if not database_manager:
            raise HTTPException(status_code=500, detail="Database manager not initialized")
            
        performance = await database_manager.get_exchange_performance(exchange_name, days)
        return performance
        
    except Exception as e:
        logger.error(f"Error getting performance for {exchange_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/strategy-performance/{strategy_name}/{exchange_name}/{pair}")
async def get_strategy_performance(strategy_name: str, exchange_name: str, pair: str):
    """Get performance metrics for a specific strategy"""
    try:
        if not strategy_manager:
            raise HTTPException(status_code=500, detail="Strategy manager not initialized")
            
        performance = await strategy_manager.get_strategy_performance(strategy_name, exchange_name, pair)
        return performance
        
    except Exception as e:
        logger.error(f"Error getting strategy performance: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/control/start")
async def start_bot():
    """Start the trading bot"""
    try:
        # This would typically start the orchestrator
        # For now, just return success
        return {"status": "success", "message": "Bot started successfully"}
        
    except Exception as e:
        logger.error(f"Error starting bot: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/control/stop")
async def stop_bot():
    """Stop the trading bot"""
    try:
        # This would typically stop the orchestrator
        # For now, just return success
        return {"status": "success", "message": "Bot stopped successfully"}
        
    except Exception as e:
        logger.error(f"Error stopping bot: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/control/emergency-stop")
async def emergency_stop():
    """Emergency stop all trades"""
    try:
        if not database_manager:
            raise HTTPException(status_code=500, detail="Database manager not initialized")
            
        # Get all open trades
        open_trades = await database_manager.get_open_trades()
        
        # Mark all trades for emergency exit
        for trade in open_trades:
            await database_manager.update_trade(trade['trade_id'], {
                'exit_reason': 'emergency_stop',
                'status': 'PENDING_EXIT'
            })
            
        return {
            "status": "success", 
            "message": f"Emergency stop initiated for {len(open_trades)} trades"
        }
        
    except Exception as e:
        logger.error(f"Error emergency stopping: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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


# Background task for real-time updates
async def broadcast_updates():
    """Background task to broadcast real-time updates"""
    while True:
        try:
            if database_manager:
                # Get portfolio summary
                portfolio = await database_manager.get_portfolio_summary()
                
                # Get recent alerts
                alerts = await database_manager.get_unresolved_alerts()
                
                # Broadcast update
                await websocket_manager.broadcast({
                    "type": "update",
                    "timestamp": datetime.utcnow().isoformat(),
                    "portfolio": portfolio,
                    "alerts_count": len(alerts)
                })
                
        except Exception as e:
            logger.error(f"Error broadcasting updates: {e}")
            
        await asyncio.sleep(5)  # Update every 5 seconds


@app.on_event("startup")
async def start_background_tasks():
    """Start background tasks"""
    asyncio.create_task(broadcast_updates())


if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    ) 