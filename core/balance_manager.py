#!/usr/bin/env python3
"""
Balance Manager

This module provides a comprehensive balance management system that:
1. Tracks available balance for each exchange
2. Manages balance during trade entry and exit
3. Updates total and daily PnL from closed trades
4. Validates balance before trade entry
5. Supports both simulation and live modes
"""

import logging
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple
from decimal import Decimal

logger = logging.getLogger(__name__)

class BalanceManager:
    """Manages balance tracking and updates for all exchanges"""
    
    def __init__(self, database_manager, config_manager):
        self.database_manager = database_manager
        self.config_manager = config_manager
        self.balances: Dict[str, Dict[str, float]] = {}
        
    async def initialize_balances(self) -> None:
        """Initialize balance tracking for all exchanges"""
        try:
            exchanges = self.config_manager.get_all_exchanges()
            
            for exchange_name in exchanges:
                if self.config_manager.is_simulation_mode():
                    # In simulation mode, get balance from database
                    balance = await self.database_manager.get_balance(exchange_name)
                    if balance:
                        self.balances[exchange_name] = {
                            'total': float(balance['balance']),
                            'available': float(balance['available_balance']),
                            'total_pnl': float(balance['total_pnl']),
                            'daily_pnl': float(balance['daily_pnl'])
                        }
                    else:
                        # Initialize with default values
                        self.balances[exchange_name] = {
                            'total': 10000.0,  # Default simulation balance
                            'available': 10000.0,
                            'total_pnl': 0.0,
                            'daily_pnl': 0.0
                        }
                        # Create initial balance record
                        await self.database_manager.update_balance(
                            exchange_name, 10000.0, 10000.0, 0.0, 0.0
                        )
                else:
                    # In live mode, get balance from exchange
                    # This would be implemented when live trading is enabled
                    pass
                    
            logger.info(f"Initialized balances for {len(exchanges)} exchanges: {list(self.balances.keys())}")
            
        except Exception as e:
            logger.error(f"Error initializing balances: {e}")
            
    async def check_available_balance(self, exchange_name: str, required_amount: float) -> bool:
        """Check if there's sufficient available balance for a trade"""
        try:
            if exchange_name not in self.balances:
                logger.error(f"Exchange {exchange_name} not found in balance manager")
                return False
                
            available = self.balances[exchange_name]['available']
            has_sufficient = available >= required_amount
            
            logger.info(f"[BalanceCheck] {exchange_name}: available={available:.2f}, required={required_amount:.2f}, sufficient={has_sufficient}")
            
            return has_sufficient
            
        except Exception as e:
            logger.error(f"Error checking available balance for {exchange_name}: {e}")
            return False
            
    async def reserve_balance(self, exchange_name: str, amount: float) -> bool:
        """Reserve balance for a trade entry"""
        try:
            if exchange_name not in self.balances:
                logger.error(f"Exchange {exchange_name} not found in balance manager")
                return False
                
            current_available = self.balances[exchange_name]['available']
            
            if current_available < amount:
                logger.error(f"Insufficient balance on {exchange_name}: available={current_available:.2f}, required={amount:.2f}")
                return False
                
            # Update available balance
            new_available = current_available - amount
            self.balances[exchange_name]['available'] = new_available
            
            # Update database
            await self.database_manager.update_balance(
                exchange_name,
                self.balances[exchange_name]['total'],
                new_available,
                self.balances[exchange_name]['total_pnl'],
                self.balances[exchange_name]['daily_pnl']
            )
            
            logger.info(f"[BalanceReserve] {exchange_name}: {current_available:.2f} -> {new_available:.2f} (reserved {amount:.2f})")
            return True
            
        except Exception as e:
            logger.error(f"Error reserving balance for {exchange_name}: {e}")
            return False
            
    async def release_balance(self, exchange_name: str, amount: float, pnl: float = 0.0) -> bool:
        """Release balance and add PnL after trade exit"""
        try:
            if exchange_name not in self.balances:
                logger.error(f"Exchange {exchange_name} not found in balance manager")
                return False
                
            current_available = self.balances[exchange_name]['available']
            current_total_pnl = self.balances[exchange_name]['total_pnl']
            current_daily_pnl = self.balances[exchange_name]['daily_pnl']
            
            # Calculate new values
            new_available = current_available + amount + pnl
            new_total_pnl = current_total_pnl + pnl
            new_daily_pnl = current_daily_pnl + pnl
            
            # Update in-memory balances
            self.balances[exchange_name]['available'] = new_available
            self.balances[exchange_name]['total_pnl'] = new_total_pnl
            self.balances[exchange_name]['daily_pnl'] = new_daily_pnl
            
            # Update database
            await self.database_manager.update_balance(
                exchange_name,
                self.balances[exchange_name]['total'],
                new_available,
                new_total_pnl,
                new_daily_pnl
            )
            
            logger.info(f"[BalanceRelease] {exchange_name}: available={current_available:.2f} -> {new_available:.2f}, pnl={pnl:.2f}")
            return True
            
        except Exception as e:
            logger.error(f"Error releasing balance for {exchange_name}: {e}")
            return False
            
    async def get_balance_summary(self) -> Dict[str, Dict[str, float]]:
        """Get current balance summary for all exchanges"""
        return self.balances.copy()
        
    async def validate_balances(self) -> bool:
        """Validate that all balances are consistent"""
        try:
            for exchange_name, balance in self.balances.items():
                # Get actual balance from database
                db_balance = await self.database_manager.get_balance(exchange_name)
                if not db_balance:
                    logger.error(f"Could not get balance from database for {exchange_name}")
                    continue
                    
                # Compare values
                db_available = float(db_balance['available_balance'])
                mem_available = balance['available']
                
                if abs(db_available - mem_available) > 0.01:
                    logger.warning(f"Balance mismatch for {exchange_name}: memory={mem_available:.2f}, database={db_available:.2f}")
                    # Sync with database
                    self.balances[exchange_name]['available'] = db_available
                    self.balances[exchange_name]['total_pnl'] = float(db_balance['total_pnl'])
                    self.balances[exchange_name]['daily_pnl'] = float(db_balance['daily_pnl'])
                    
            return True
            
        except Exception as e:
            logger.error(f"Error validating balances: {e}")
            return False
            
    async def reset_daily_pnl(self) -> None:
        """Reset daily PnL at the start of a new day"""
        try:
            for exchange_name in self.balances:
                self.balances[exchange_name]['daily_pnl'] = 0.0
                
                # Update database
                await self.database_manager.update_balance(
                    exchange_name,
                    self.balances[exchange_name]['total'],
                    self.balances[exchange_name]['available'],
                    self.balances[exchange_name]['total_pnl'],
                    0.0  # Reset daily PnL
                )
                
            logger.info("Reset daily PnL for all exchanges")
            
        except Exception as e:
            logger.error(f"Error resetting daily PnL: {e}")
            
    async def get_total_portfolio_value(self) -> float:
        """Calculate total portfolio value across all exchanges"""
        try:
            total_value = 0.0
            
            for exchange_name, balance in self.balances.items():
                # Total value = base balance + total PnL
                exchange_value = balance['total'] + balance['total_pnl']
                total_value += exchange_value
                
            return total_value
            
        except Exception as e:
            logger.error(f"Error calculating total portfolio value: {e}")
            return 0.0 