"""
Configuration Manager for the Multi-Exchange Trading Bot
Centralized configuration management with validation and alerts
"""

import os
import yaml
import logging
from typing import Dict, Any, Optional, List
from pathlib import Path
from datetime import datetime
import json
from dataclasses import dataclass, asdict
from pydantic import BaseModel, ValidationError

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


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
    mode: str
    max_concurrent_trades: int
    max_trades_per_exchange: int
    position_size_percentage: float
    max_daily_trades: int
    max_daily_loss: float
    max_total_loss: float
    stop_loss_percentage: float
    take_profit_percentage: float


class ProfitProtectionConfig(BaseModel):
    enabled: bool
    trigger_percentage: float
    lock_percentage: float


class TrailingStopConfig(BaseModel):
    enabled: bool
    trigger_percentage: float
    step_percentage: float


class StrategyConfig(BaseModel):
    enabled: bool
    parameters: Dict[str, Any]


class PairSelectorConfig(BaseModel):
    update_interval_minutes: int
    selection_criteria: Dict[str, Any]
    scoring_weights: Dict[str, float]


class BalanceManagerConfig(BaseModel):
    check_interval_seconds: int
    min_balance_threshold: float
    max_balance_usage: float


class WebUIConfig(BaseModel):
    host: str
    port: int
    debug: bool
    refresh_interval_seconds: int


class LoggingConfig(BaseModel):
    level: str
    format: str
    file: str
    max_file_size: int
    backup_count: int


class AlertsConfig(BaseModel):
    enabled: bool
    channels: Dict[str, bool]
    thresholds: Dict[str, float]


class ConfigManager:
    """Centralized configuration manager for all bot components"""
    
    def __init__(self, config_path: str = "config/config.yaml"):
        self.config_path = Path(config_path)
        self.config_data: Dict[str, Any] = {}
        self.database_manager = None  # Will be set by dependency injection
        self._load_configuration()
        
    def _load_configuration(self) -> None:
        """Load configuration from YAML file"""
        try:
            if not self.config_path.exists():
                raise FileNotFoundError(f"Configuration file not found: {self.config_path}")
                
            with open(self.config_path, 'r') as file:
                self.config_data = yaml.safe_load(file)
                
            logger.info(f"Configuration loaded successfully from {self.config_path}")
            self._validate_configuration()
            
        except Exception as e:
            logger.error(f"Failed to load configuration: {e}")
            self._create_alert("CONFIG", "ERROR", f"Configuration load failed: {e}")
            raise
            
    def _validate_configuration(self) -> None:
        """Validate all configuration sections"""
        required_sections = [
            'database', 'redis', 'exchanges', 'trading', 'strategies',
            'pair_selector', 'balance_manager', 'web_ui', 'logging', 'alerts'
        ]
        
        missing_sections = []
        for section in required_sections:
            if section not in self.config_data:
                missing_sections.append(section)
                
        if missing_sections:
            error_msg = f"Missing required configuration sections: {missing_sections}"
            logger.error(error_msg)
            self._create_alert("CONFIG", "ERROR", error_msg)
            raise ValueError(error_msg)
            
        # Validate specific configurations
        self._validate_database_config()
        self._validate_exchange_configs()
        self._validate_trading_config()
        self._validate_strategy_configs()
        
    def _validate_database_config(self) -> None:
        """Validate database configuration"""
        db_config = self.config_data.get('database', {})
        required_db_fields = ['host', 'port', 'name', 'user']
        
        missing_fields = [field for field in required_db_fields if not db_config.get(field)]
        if missing_fields:
            error_msg = f"Missing database configuration fields: {missing_fields}"
            logger.error(error_msg)
            self._create_alert("CONFIG", "ERROR", error_msg)
            raise ValueError(error_msg)
            
    def _validate_exchange_configs(self) -> None:
        """Validate exchange configurations"""
        exchanges = self.config_data.get('exchanges', {})
        if not exchanges:
            error_msg = "No exchange configurations found"
            logger.error(error_msg)
            self._create_alert("CONFIG", "ERROR", error_msg)
            raise ValueError(error_msg)
            
        for exchange_name, config in exchanges.items():
            required_fields = ['base_currency', 'max_pairs', 'min_volume_24h', 'min_volatility']
            missing_fields = [field for field in required_fields if not config.get(field)]
            
            if missing_fields:
                error_msg = f"Missing configuration for {exchange_name}: {missing_fields}"
                logger.error(error_msg)
                self._create_alert("CONFIG", "ERROR", error_msg)
                raise ValueError(error_msg)
                
    def _validate_trading_config(self) -> None:
        """Validate trading configuration"""
        trading_config = self.config_data.get('trading', {})
        required_fields = ['mode', 'max_concurrent_trades', 'position_size_percentage']
        
        missing_fields = [field for field in required_fields if not trading_config.get(field)]
        if missing_fields:
            error_msg = f"Missing trading configuration fields: {missing_fields}"
            logger.error(error_msg)
            self._create_alert("CONFIG", "ERROR", error_msg)
            raise ValueError(error_msg)
            
        # Validate mode
        mode = trading_config.get('mode')
        if mode not in ['simulation', 'live']:
            error_msg = f"Invalid trading mode: {mode}. Must be 'simulation' or 'live'"
            logger.error(error_msg)
            self._create_alert("CONFIG", "ERROR", error_msg)
            raise ValueError(error_msg)
            
    def _validate_strategy_configs(self) -> None:
        """Validate strategy configurations"""
        strategies = self.config_data.get('strategies', {})
        if not strategies:
            error_msg = "No strategy configurations found"
            logger.error(error_msg)
            self._create_alert("CONFIG", "ERROR", error_msg)
            raise ValueError(error_msg)
            
        for strategy_name, config in strategies.items():
            if not isinstance(config, dict) or 'enabled' not in config:
                error_msg = f"Invalid strategy configuration for {strategy_name}"
                logger.error(error_msg)
                self._create_alert("CONFIG", "ERROR", error_msg)
                raise ValueError(error_msg)
                
    def _create_alert(self, category: str, level: str, message: str, details: Optional[Dict] = None) -> None:
        """Create an alert in the database if database manager is available"""
        if self.database_manager:
            try:
                alert_data = {
                    'level': level,
                    'category': category,
                    'message': message,
                    'details': details or {},
                    'created_at': datetime.utcnow()
                }
                # This will be implemented when database manager is available
                # self.database_manager.create_alert(alert_data)
                logger.warning(f"Alert created: {level} - {category} - {message}")
            except Exception as e:
                logger.error(f"Failed to create alert: {e}")
        else:
            logger.warning(f"Alert (no DB): {level} - {category} - {message}")
            
    def get_database_config(self) -> Dict[str, Any]:
        """Get database configuration"""
        db_config = self.config_data.get('database', {})
        logger.info(f"get_database_config() returning: {db_config}")
        return db_config
        
    def get_redis_config(self) -> Dict[str, Any]:
        """Get Redis configuration"""
        return self.config_data.get('redis', {})
        
    def get_exchange_config(self, exchange_name: str) -> Dict[str, Any]:
        """Get configuration for specific exchange"""
        exchanges = self.config_data.get('exchanges', {})
        if exchange_name not in exchanges:
            error_msg = f"Exchange configuration not found: {exchange_name}"
            logger.error(error_msg)
            self._create_alert("CONFIG", "ERROR", error_msg)
            raise ValueError(error_msg)
        return exchanges[exchange_name]
        
    def get_all_exchanges(self) -> List[str]:
        """Get list of all configured exchanges"""
        return list(self.config_data.get('exchanges', {}).keys())
        
    def get_trading_config(self) -> Dict[str, Any]:
        """Get trading configuration"""
        return self.config_data.get('trading', {})
        
    def get_strategy_config(self, strategy_name: str) -> Dict[str, Any]:
        """Get configuration for specific strategy"""
        strategies = self.config_data.get('strategies', {})
        if strategy_name not in strategies:
            error_msg = f"Strategy configuration not found: {strategy_name}"
            logger.error(error_msg)
            self._create_alert("CONFIG", "ERROR", error_msg)
            raise ValueError(error_msg)
        return strategies[strategy_name]
        
    def get_enabled_strategies(self) -> List[str]:
        """Get list of enabled strategies"""
        strategies = self.config_data.get('strategies', {})
        return [name for name, config in strategies.items() if config.get('enabled', False)]
        
    def get_pair_selector_config(self) -> Dict[str, Any]:
        """Get pair selector configuration"""
        return self.config_data.get('pair_selector', {})
        
    def get_balance_manager_config(self) -> Dict[str, Any]:
        """Get balance manager configuration"""
        return self.config_data.get('balance_manager', {})
        
    def get_web_ui_config(self) -> Dict[str, Any]:
        """Get web UI configuration"""
        return self.config_data.get('web_ui', {})
        
    def get_logging_config(self) -> Dict[str, Any]:
        """Get logging configuration"""
        return self.config_data.get('logging', {})
        
    def get_alerts_config(self) -> Dict[str, Any]:
        """Get alerts configuration"""
        return self.config_data.get('alerts', {})
        
    def is_simulation_mode(self) -> bool:
        """Check if bot is running in simulation mode"""
        return self.config_data.get('trading', {}).get('mode') == 'simulation'
        
    def is_live_mode(self) -> bool:
        """Check if bot is running in live mode"""
        return self.config_data.get('trading', {}).get('mode') == 'live'
        
    def get_config_value(self, path: str, default: Any = None) -> Any:
        """Get configuration value by dot notation path"""
        keys = path.split('.')
        value = self.config_data
        
        try:
            for key in keys:
                value = value[key]
            return value
        except (KeyError, TypeError):
            if default is not None:
                return default
            error_msg = f"Configuration path not found: {path}"
            logger.error(error_msg)
            self._create_alert("CONFIG", "ERROR", error_msg)
            raise ValueError(error_msg)
            
    def update_config_value(self, path: str, new_value: Any, component: str = "system") -> None:
        """Update configuration value and log the change"""
        keys = path.split('.')
        config_section = self.config_data
        
        # Navigate to the parent of the target key
        for key in keys[:-1]:
            if key not in config_section:
                config_section[key] = {}
            config_section = config_section[key]
            
        # Store old value and update
        old_value = config_section.get(keys[-1])
        config_section[keys[-1]] = new_value
        
        # Log the change
        logger.info(f"Configuration updated: {path} = {new_value} (was: {old_value})")
        
        # Create audit record
        if self.database_manager:
            try:
                audit_data = {
                    'component': component,
                    'config_key': path,
                    'old_value': str(old_value) if old_value is not None else None,
                    'new_value': str(new_value),
                    'changed_by': component,
                    'timestamp': datetime.utcnow()
                }
                # This will be implemented when database manager is available
                # self.database_manager.create_config_audit(audit_data)
            except Exception as e:
                logger.error(f"Failed to create config audit: {e}")
                
    def reload_configuration(self) -> None:
        """Reload configuration from file"""
        logger.info("Reloading configuration...")
        self._load_configuration()
        logger.info("Configuration reloaded successfully")
        
    def export_configuration(self) -> str:
        """Export current configuration as JSON string"""
        return json.dumps(self.config_data, indent=2, default=str)
        
    def set_database_manager(self, database_manager) -> None:
        """Set database manager for alerts and audit logging"""
        self.database_manager = database_manager 