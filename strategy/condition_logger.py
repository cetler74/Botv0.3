import logging
import json
from datetime import datetime
from typing import Dict, Any, Optional, List
import asyncio

class ConditionLogger:
    """
    Enhanced async condition logger that stores results in Redis for analysis.
    Provides logging for validation and condition checks in strategies.
    Now supports async/await and aioredis for non-blocking operation.
    """
    def __init__(self, strategy_name=None, logger_instance=None, detailed_mode=False, redis_client=None):
        self.strategy_name = strategy_name or "UnknownStrategy"
        self.logger = logger_instance or logging.getLogger(__name__)
        self.detailed_mode = detailed_mode
        self.redis = redis_client  # Should be an aioredis client for async
        self.conditions = []
        self.start_time = datetime.now()
        self.pair = None
        
    def start_validation(self, pair):
        """Start validation process for a pair."""
        self.logger.info(f"[ConditionLogger][{self.strategy_name}] Start validation for pair: {pair}")
        self.pair = pair
        self.conditions = []
        self.start_time = datetime.now()

    async def log_condition(self, name, value, description, result=True, condition_type=None, market_regime=None, volatility=None, context=None, exchange=None, target_value=None, current_value=None):
        """
        Enhanced async log a condition check result with detailed information.
        Args:
            name: Name of the condition
            value: Value being checked
            description: Description of the condition
            result: Whether the condition passed
            condition_type: Type of condition (entry, exit, filter, etc.)
            market_regime: Current market regime (bull, bear, sideways, etc.)
            volatility: Current volatility context
            context: Additional context dict
            exchange: Exchange name
            target_value: Target value for comparison
            current_value: Current value being compared
        """
        # Enhanced logging with detailed information
        exchange_info = f"[{exchange}]" if exchange else ""
        target_info = f"Target: {target_value}" if target_value is not None else ""
        current_info = f"Current: {current_value}" if current_value is not None else ""
        
        # Build detailed log message
        log_parts = [
            f"[ConditionLogger][{self.strategy_name}]{exchange_info}",
            f"Condition: {name}",
            f"Value: {value}",
            f"Result: {'✅ PASS' if result else '❌ FAIL'}"
        ]
        
        if target_info and current_info:
            log_parts.append(f"{current_info} vs {target_info}")
        elif target_info:
            log_parts.append(target_info)
        elif current_info:
            log_parts.append(current_info)
            
        if market_regime:
            log_parts.append(f"Regime: {market_regime}")
        if volatility:
            log_parts.append(f"Vol: {volatility}")
        if condition_type:
            log_parts.append(f"Type: {condition_type}")
            
        log_parts.append(f"Desc: {description}")
        
        if context:
            context_str = ", ".join([f"{k}={v}" for k, v in context.items() if v is not None])
            if context_str:
                log_parts.append(f"Context: {context_str}")
        
        # Log the detailed message
        self.logger.info(" | ".join(log_parts))
        # Store condition in memory
        condition = {
            "name": name,
            "value": value,
            "description": description,
            "result": result,
            "type": condition_type,
            "market_regime": market_regime,
            "volatility": volatility,
            "pair": self.pair,
            "context": context or {},
            "timestamp": datetime.now().isoformat()
        }
        self.conditions.append(condition)
        # Store in Redis if available
        if self.redis and self.pair:
            try:
                key = f"condition:{self.strategy_name}:{self.pair}:{name}"
                # Get existing data
                existing_data = await self.redis.get(key)
                if existing_data:
                    data = json.loads(existing_data)
                else:
                    data = {
                        "checked": 0,
                        "passed": 0,
                        "failed": 0,
                        "recent_values": [],
                        "success_rate": 0.0
                    }
                data["checked"] += 1
                if result:
                    data["passed"] += 1
                else:
                    data["failed"] += 1
                data["success_rate"] = data["passed"] / data["checked"] if data["checked"] > 0 else 0
                data["recent_values"].append({
                    "value": str(value),
                    "result": result,
                    "market_regime": market_regime,
                    "volatility": volatility,
                    "pair": self.pair,
                    "context": context or {},
                    "timestamp": datetime.now().isoformat()
                })
                if len(data["recent_values"]) > 100:
                    data["recent_values"] = data["recent_values"][-100:]
                await self.redis.setex(key, 86400, json.dumps(data))
                await self._update_strategy_condition_stats(name, result, market_regime, volatility)
            except Exception as e:
                self.logger.error(f"Error storing condition in Redis (async): {str(e)}")

    async def _update_strategy_condition_stats(self, condition_name: str, result: bool, market_regime=None, volatility=None):
        if not self.redis:
            return
        try:
            key = f"strategy_conditions:{self.strategy_name}"
            existing_data = await self.redis.get(key)
            if existing_data:
                data = json.loads(existing_data)
            else:
                data = {"conditions": {}}
            if condition_name not in data["conditions"]:
                data["conditions"][condition_name] = {
                    "checked": 0,
                    "passed": 0,
                    "failed": 0,
                    "success_rate": 0.0,
                    "regime_breakdown": {},
                    "volatility_breakdown": {}
                }
            cond_data = data["conditions"][condition_name]
            cond_data["checked"] += 1
            if result:
                cond_data["passed"] += 1
            else:
                cond_data["failed"] += 1
            cond_data["success_rate"] = cond_data["passed"] / cond_data["checked"] if cond_data["checked"] > 0 else 0
            # Regime breakdown
            if market_regime:
                if market_regime not in cond_data["regime_breakdown"]:
                    cond_data["regime_breakdown"][market_regime] = {"checked": 0, "passed": 0, "failed": 0}
                cond_data["regime_breakdown"][market_regime]["checked"] += 1
                if result:
                    cond_data["regime_breakdown"][market_regime]["passed"] += 1
                else:
                    cond_data["regime_breakdown"][market_regime]["failed"] += 1
            # Volatility breakdown
            if volatility:
                if volatility not in cond_data["volatility_breakdown"]:
                    cond_data["volatility_breakdown"][volatility] = {"checked": 0, "passed": 0, "failed": 0}
                cond_data["volatility_breakdown"][volatility]["checked"] += 1
                if result:
                    cond_data["volatility_breakdown"][volatility]["passed"] += 1
                else:
                    cond_data["volatility_breakdown"][volatility]["failed"] += 1
            await self.redis.setex(key, 7 * 86400, json.dumps(data))
        except Exception as e:
            self.logger.error(f"Error updating strategy condition stats in Redis (async): {str(e)}")

    async def end_validation(self, result, reason=None, market_regime=None, volatility=None, context=None):
        """Async end validation process and log final result."""
        self.logger.info(f"[ConditionLogger][{self.strategy_name}] End validation | Result: {result} | Reason: {reason}")
        if self.redis and self.pair:
            try:
                key = f"validation:{self.strategy_name}:{self.pair}"
                validation_data = {
                    "result": result,
                    "reason": reason,
                    "conditions": self.conditions,
                    "market_regime": market_regime,
                    "volatility": volatility,
                    "context": context or {},
                    "duration_ms": (datetime.now() - self.start_time).total_seconds() * 1000,
                    "timestamp": datetime.now().isoformat()
                }
                existing_data = await self.redis.get(key)
                if existing_data:
                    data = json.loads(existing_data)
                    data["recent_validations"] = data.get("recent_validations", [])
                else:
                    data = {
                        "passed_count": 0,
                        "failed_count": 0,
                        "recent_validations": []
                    }
                if result:
                    data["passed_count"] = data.get("passed_count", 0) + 1
                else:
                    data["failed_count"] = data.get("failed_count", 0) + 1
                data["recent_validations"].append(validation_data)
                if len(data["recent_validations"]) > 50:
                    data["recent_validations"] = data["recent_validations"][-50:]
                await self.redis.setex(key, 86400, json.dumps(data))
                await self._update_strategy_validation_stats(result)
            except Exception as e:
                self.logger.error(f"Error storing validation in Redis (async): {str(e)}")

    async def _update_strategy_validation_stats(self, result: bool):
        if not self.redis:
            return
        try:
            key = f"strategy_validations:{self.strategy_name}"
            existing_data = await self.redis.get(key)
            if existing_data:
                data = json.loads(existing_data)
            else:
                data = {"passed_count": 0, "failed_count": 0}
            if result:
                data["passed_count"] += 1
            else:
                data["failed_count"] += 1
            await self.redis.setex(key, 7 * 86400, json.dumps(data))
        except Exception as e:
            self.logger.error(f"Error updating strategy validation stats in Redis (async): {str(e)}")
    
    def get_condition_results(self) -> List[Dict[str, Any]]:
        """Get the list of condition results for this validation."""
        return self.conditions
    
    @staticmethod
    def get_strategy_stats(strategy_name: str, redis_client=None) -> Dict[str, Any]:
        """
        Get statistics for a strategy from Redis.
        
        Args:
            strategy_name: Name of the strategy
            redis_client: Redis client instance
            
        Returns:
            Dictionary with strategy statistics
        """
        if not redis_client:
            return {"error": "Redis client not available"}
            
        try:
            # Get validation stats
            validation_key = f"strategy_validations:{strategy_name}"
            validation_data = redis_client.get(validation_key)
            
            # Get condition stats
            condition_key = f"strategy_conditions:{strategy_name}"
            condition_data = redis_client.get(condition_key)
            
            # Combine data
            stats = {
                "strategy": strategy_name,
                "validations": json.loads(validation_data) if validation_data else {},
                "conditions": json.loads(condition_data) if condition_data else {}
            }
            
            return stats
            
        except Exception as e:
            logging.getLogger(__name__).error(f"Error getting strategy stats from Redis: {str(e)}")
            return {"error": str(e)} 