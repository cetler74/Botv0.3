"""
Configuration Service for the Multi-Exchange Trading Bot
Centralized configuration management and validation
"""

import os
import yaml
import logging
from typing import Dict, Any, Optional, List
from pathlib import Path
from datetime import datetime
import json
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ValidationError
import uvicorn
import copy
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="Configuration Service",
    description="Centralized configuration management for the trading bot",
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

# Load .env if present
load_dotenv()

# Utility: Redact sensitive fields
SENSITIVE_PATHS = [
    ("database", "password"),
    ("redis", "password"),
    ("exchanges", None),  # All exchanges: redact api_key, api_secret
]

def redact_config(config: dict, testing: bool = False) -> dict:
    if testing:
        return config
    redacted = copy.deepcopy(config)
    # Redact database password
    if "database" in redacted and "password" in redacted["database"]:
        redacted["database"]["password"] = "***REDACTED***"
    # Redact redis password
    if "redis" in redacted and "password" in redacted["redis"]:
        redacted["redis"]["password"] = "***REDACTED***"
    # Redact all exchange api_key/api_secret
    if "exchanges" in redacted:
        for ex in redacted["exchanges"].values():
            if "api_key" in ex:
                ex["api_key"] = "***REDACTED***"
            if "api_secret" in ex:
                ex["api_secret"] = "***REDACTED***"
    return redacted

# Override sensitive config fields with environment variables
SENSITIVE_ENV_MAP = {
    "database.password": "DB_PASSWORD",
    "redis.password": "REDIS_PASSWORD",
    # Exchanges: handled below
}

def override_sensitive_fields():
    # Database and Redis
    for path, env_var in SENSITIVE_ENV_MAP.items():
        keys = path.split('.')
        d = config_data
        for k in keys[:-1]:
            d = d.get(k, {})
        if keys[-1] in d and os.environ.get(env_var):
            d[keys[-1]] = os.environ[env_var]
    # Exchanges
    if "exchanges" in config_data:
        for ex_name, ex in config_data["exchanges"].items():
            env_key = f"EXCHANGE_{ex_name.upper()}_API_KEY"
            env_secret = f"EXCHANGE_{ex_name.upper()}_API_SECRET"
            if os.environ.get(env_key):
                ex["api_key"] = os.environ[env_key]
            if os.environ.get(env_secret):
                ex["api_secret"] = os.environ[env_secret]

# Data Models
class DatabaseConfig(BaseModel):
    host: str
    port: int
    name: str
    user: str
    password: str
    pool_size: int
    max_overflow: int

class RedisConfig(BaseModel):
    host: str
    port: int
    db: int
    password: str
    max_connections: int

class ExchangeConfig(BaseModel):
    api_key: str
    api_secret: str
    sandbox: bool
    base_currency: str
    max_pairs: int
    min_volume_24h: float
    min_volatility: float

class TradingConfig(BaseModel):
    mode: str  # 'simulation' or 'live'
    max_concurrent_trades: int
    max_trades_per_exchange: int
    position_size_percentage: float
    max_daily_trades: int
    max_daily_loss: float
    max_total_loss: float
    stop_loss_percentage: float
    take_profit_percentage: float

class StrategyConfig(BaseModel):
    enabled: bool
    parameters: Dict[str, Any]

class WebUIConfig(BaseModel):
    host: str
    port: int
    debug: bool
    refresh_interval_seconds: int

class ConfigUpdateRequest(BaseModel):
    path: str
    value: Any
    component: str = "system"

class ConfigValidationResponse(BaseModel):
    valid: bool
    errors: List[str]
    warnings: List[str]

class HealthResponse(BaseModel):
    status: str
    timestamp: datetime
    version: str
    config_loaded: bool

# Global configuration state
config_data: Dict[str, Any] = {}
config_path: Path = Path("config/config.yaml")
last_modified: Optional[datetime] = None
_config_cache: Optional[Dict[str, Any]] = None

def load_configuration() -> bool:
    """Load configuration from YAML file"""
    global config_data, last_modified, _config_cache
    
    try:
        if not config_path.exists():
            logger.error(f"Configuration file not found: {config_path}")
            return False
            
        # Check if file has been modified
        current_mtime = datetime.fromtimestamp(config_path.stat().st_mtime)
        if last_modified and current_mtime <= last_modified and _config_cache:
            return True  # No changes, use cache
            
        with open(config_path, 'r') as file:
            config_data = yaml.safe_load(file)
            _config_cache = config_data.copy()  # Cache the full config
            
        last_modified = current_mtime
        logger.info(f"Configuration loaded successfully from {config_path}")
        override_sensitive_fields()  # <-- Add this line
        # Validate configuration
        validate_configuration()
        return True
        
    except Exception as e:
        logger.error(f"Failed to load configuration: {e}")
        return False

def validate_configuration() -> List[str]:
    """Validate configuration and return list of errors"""
    errors = []
    
    required_sections = [
        'database', 'redis', 'exchanges', 'trading', 'strategies',
        'pair_selector', 'balance_manager', 'web_ui', 'logging', 'alerts'
    ]
    
    # Check for required sections
    for section in required_sections:
        if section not in config_data:
            errors.append(f"Missing required configuration section: {section}")
    
    # Validate database configuration
    if 'database' in config_data:
        db_config = config_data['database']
        required_db_fields = ['host', 'port', 'name', 'user']
        for field in required_db_fields:
            if not db_config.get(field):
                errors.append(f"Missing database configuration field: {field}")
    
    # Validate exchange configurations
    if 'exchanges' in config_data:
        exchanges = config_data['exchanges']
        if not exchanges:
            errors.append("No exchange configurations found")
        else:
            for exchange_name, config in exchanges.items():
                required_fields = ['base_currency', 'max_pairs', 'min_volume_24h', 'min_volatility']
                for field in required_fields:
                    if not config.get(field):
                        errors.append(f"Missing configuration for {exchange_name}: {field}")
    
    # Validate trading configuration
    if 'trading' in config_data:
        trading_config = config_data['trading']
        required_fields = ['mode', 'max_concurrent_trades', 'position_size_percentage']
        for field in required_fields:
            if not trading_config.get(field):
                errors.append(f"Missing trading configuration field: {field}")
    
    if errors:
        logger.error(f"Configuration validation errors: {errors}")
    
    return errors

def get_config_value(path: str, default: Any = None) -> Any:
    """Get configuration value by dot notation path"""
    try:
        keys = path.split('.')
        value = config_data
        
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
                
        return value
    except Exception:
        return default

def update_config_value(path: str, new_value: Any, component: str = "system") -> bool:
    """Update configuration value by dot notation path"""
    try:
        keys = path.split('.')
        current = config_data
        
        # Navigate to the parent of the target key
        for key in keys[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]
        
        # Update the value
        current[keys[-1]] = new_value
        
        # Save to file
        with open(config_path, 'w') as file:
            yaml.dump(config_data, file, default_flow_style=False)
        
        logger.info(f"Configuration updated by {component}: {path} = {new_value}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to update configuration: {e}")
        return False

# Load configuration on startup (always, even in testing)
load_configuration()

# API Endpoints
@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint"""
    return HealthResponse(
        status="healthy",
        timestamp=datetime.utcnow(),
        version="1.0.0",
        config_loaded=bool(config_data)
    )

@app.get("/ready")
async def readiness_check():
    """Readiness check endpoint"""
    if not config_data:
        raise HTTPException(status_code=503, detail="Configuration not loaded")
    return {"status": "ready"}

@app.get("/live")
async def liveness_check():
    """Liveness check endpoint"""
    return {"status": "alive"}

@app.get("/api/v1/config/database")
async def get_database_config():
    """Get database configuration"""
    if not config_data:
        raise HTTPException(status_code=503, detail="Configuration not loaded")
    
    db_config = config_data.get('database', {}).copy()
    
    # Override host and password for Docker environment
    if os.getenv("DOCKER_ENV"):
        db_config['host'] = 'postgres'
        db_config['password'] = os.getenv("DB_PASSWORD", db_config.get('password', ''))
        return db_config
    
    return redact_config({'database': db_config}, testing=bool(os.getenv("TESTING")))['database']

@app.get("/api/v1/config/redis")
async def get_redis_config():
    """Get Redis configuration"""
    if not config_data:
        raise HTTPException(status_code=503, detail="Configuration not loaded")
    
    redis_config = config_data.get('redis', {})
    return redact_config({'redis': redis_config}, testing=bool(os.getenv("TESTING")))['redis']

@app.get("/api/v1/config/exchanges")
async def get_exchanges_config():
    """Get all exchange configurations"""
    if not config_data:
        raise HTTPException(status_code=503, detail="Configuration not loaded")
    
    return redact_config({'exchanges': config_data.get('exchanges', {})}, testing=bool(os.getenv("TESTING")))['exchanges']

@app.get("/api/v1/config/exchanges/list")
async def get_exchange_list():
    """Get list of configured exchanges"""
    if not config_data:
        raise HTTPException(status_code=503, detail="Configuration not loaded")
    
    exchanges = config_data.get('exchanges', {})
    return {"exchanges": list(exchanges.keys())}

@app.get("/api/v1/config/exchanges/{exchange_name}")
async def get_exchange_config(exchange_name: str):
    """Get specific exchange configuration"""
    if not config_data:
        raise HTTPException(status_code=503, detail="Configuration not loaded")
    
    exchanges = config_data.get('exchanges', {})
    if exchange_name not in exchanges:
        raise HTTPException(status_code=404, detail=f"Exchange {exchange_name} not found")
    
    ex_cfg = {exchange_name: exchanges[exchange_name]}
    return redact_config({'exchanges': ex_cfg}, testing=bool(os.getenv("TESTING")))['exchanges'][exchange_name]

@app.get("/api/v1/config/trading")
async def get_trading_config():
    """Get trading configuration"""
    if not config_data:
        raise HTTPException(status_code=503, detail="Configuration not loaded")
    
    trading_config = config_data.get('trading', {})
    return trading_config

@app.get("/api/v1/config/strategies")
async def get_strategies_config():
    """Get strategies configuration"""
    if not config_data:
        raise HTTPException(status_code=503, detail="Configuration not loaded")
    
    return config_data.get('strategies', {})

@app.get("/api/v1/config/strategies/enabled")
async def get_enabled_strategies():
    """Get list of enabled strategies"""
    if not config_data:
        raise HTTPException(status_code=503, detail="Configuration not loaded")
    
    strategies = config_data.get('strategies', {})
    enabled = [name for name, config in strategies.items() if config.get('enabled', False)]
    return {"enabled_strategies": enabled}

@app.get("/api/v1/config/strategies/{strategy_name}")
async def get_strategy_config(strategy_name: str):
    """Get specific strategy configuration"""
    if not config_data:
        raise HTTPException(status_code=503, detail="Configuration not loaded")
    
    strategies = config_data.get('strategies', {})
    if strategy_name not in strategies:
        raise HTTPException(status_code=404, detail=f"Strategy {strategy_name} not found")
    
    return StrategyConfig(**strategies[strategy_name])

@app.get("/api/v1/config/web-ui")
async def get_web_ui_config():
    """Get web UI configuration"""
    if not config_data:
        raise HTTPException(status_code=503, detail="Configuration not loaded")
    
    web_ui_config = config_data.get('web_ui', {})
    return web_ui_config

@app.get("/api/v1/config/all")
async def get_all_config():
    """Get all configuration (including sensitive data for tests)"""
    try:
        if not config_data:
            raise HTTPException(status_code=503, detail="Configuration not loaded")
        
        # Return full config for tests, but remove sensitive data in production
        if os.getenv("TESTING"):
            return redact_config(config_data, testing=True)
        
        # Create a copy without sensitive data for production
        return redact_config(config_data, testing=False)
    except Exception as e:
        logger.error(f"Error getting all config: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@app.get("/api/v1/config/get")
async def get_specific_config_path(path: str):
    """Get specific configuration value by path"""
    if not config_data:
        raise HTTPException(status_code=503, detail="Configuration not loaded")
    
    value = get_config_value(path)
    if value is None:
        raise HTTPException(status_code=404, detail="Configuration path not found")
    
    return {"path": path, "value": value}

@app.put("/api/v1/config/update")
async def update_config(request: ConfigUpdateRequest):
    """Update configuration value"""
    if not config_data:
        raise HTTPException(status_code=503, detail="Configuration not loaded")
    
    # Validate that the path exists before updating
    current_value = get_config_value(request.path)
    if current_value is None:
        raise HTTPException(status_code=404, detail="Configuration path not found")
    
    success = update_config_value(request.path, request.value, request.component)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update configuration")
    
    return {"message": "Configuration updated successfully", "path": request.path}

@app.post("/api/v1/config/validate", response_model=ConfigValidationResponse)
async def validate_config():
    """Validate current configuration"""
    if not config_data:
        raise HTTPException(status_code=503, detail="Configuration not loaded")
    
    errors = validate_configuration()
    warnings = []  # Could add warning logic here
    
    return ConfigValidationResponse(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings
    )

@app.post("/api/v1/config/reload")
async def reload_config(background_tasks: BackgroundTasks):
    """Reload configuration from file"""
    try:
        success = load_configuration()
        if success:
            return {"message": "Configuration reloaded successfully"}
        else:
            raise HTTPException(status_code=500, detail="Failed to reload configuration")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reloading configuration: {str(e)}")

@app.get("/api/v1/config/mode")
async def get_trading_mode():
    """Get current trading mode"""
    if not config_data:
        raise HTTPException(status_code=503, detail="Configuration not loaded")
    
    trading_config = config_data.get('trading', {})
    mode = trading_config.get('mode', 'simulation')
    return {"mode": mode, "is_simulation": mode == 'simulation', "is_live": mode == 'live'}

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8001,
        reload=True,
        log_level="info"
    ) 