#!/usr/bin/env python3
"""
Enhanced PnL calculation module with fee inclusion.
This module provides fee-aware unrealized PnL calculations.
"""

def calculate_unrealized_pnl_with_fees(entry_price: float, current_price: float, position_size: float, 
                                     entry_fee: float = 0.0, exit_fee: float = 0.0, fee_rate: float = 0.001) -> float:
    """
    Calculate unrealized PnL including fees.
    
    Args:
        entry_price: Entry price of the position
        current_price: Current market price
        position_size: Size of the position
        entry_fee: Actual entry fee (if known)
        exit_fee: Actual exit fee (if known, otherwise estimated)
        fee_rate: Fee rate for estimating exit fee (default 0.1%)
        
    Returns:
        Unrealized PnL including fees
    """
    # Calculate gross PnL
    gross_pnl = (current_price - entry_price) * position_size
    
    # Use actual entry fee if provided, otherwise estimate
    actual_entry_fee = entry_fee if entry_fee > 0 else entry_price * position_size * fee_rate
    
    # Use actual exit fee if provided, otherwise estimate
    actual_exit_fee = exit_fee if exit_fee > 0 else current_price * position_size * fee_rate
    
    # Calculate net PnL after fees
    unrealized_pnl = gross_pnl - actual_entry_fee - actual_exit_fee
    
    return unrealized_pnl

def calculate_realized_pnl_with_fees(entry_price: float, exit_price: float, position_size: float,
                                   entry_fee: float = 0.0, exit_fee: float = 0.0) -> float:
    """
    Calculate realized PnL including fees.
    
    Args:
        entry_price: Entry price of the position
        exit_price: Exit price of the position
        position_size: Size of the position
        entry_fee: Actual entry fee
        exit_fee: Actual exit fee
        
    Returns:
        Realized PnL including fees
    """
    # Calculate gross PnL
    gross_pnl = (exit_price - entry_price) * position_size
    
    # Calculate net PnL after fees
    realized_pnl = gross_pnl - entry_fee - exit_fee
    
    return realized_pnl
