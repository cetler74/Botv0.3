# Strategy PnL Migration Plan: System-Wide Enhanced PnL Implementation

## Project Overview

**Goal:** Ensure only `strategy_pnl_enhanced.py` is used system-wide for SPOT trading, with `strategy_pnl.py` completely deprecated and removed. Simplify PnL calculations to focus on long positions only (no short positions needed for SPOT trading).

**Timeline:** 2-3 days
**Priority:** HIGH (Critical for accurate PnL calculations)
**Trading Type:** SPOT trading only (long positions)

## Current State Analysis

### ✅ **Already Using Enhanced Version:**
- `strategy/vwma_hull_strategy.py`
- `strategy/multi_timeframe_confluence_strategy.py`
- `strategy/base_strategy.py`
- `strategy/engulfing_multi_tf.py`
- `orchestrator/trading_orchestrator.py`
- `core/strategy_manager.py`
- `services/orchestrator-service/main.py`

### ❌ **Still Using Basic Version or Manual Calculations:**
- `services/database-service/main.py` (SQL-based calculation without fees)
- `calculate_actual_pnl.py` (manual calculation)
- `validate_realized_pnl.py` (manual calculation)
- `fix_realized_pnl_closed_trades.py` (manual calculation)
- `fix_closed_trade_exit_price.py` (manual calculation)

## Phase 1: Database Service Migration (Day 1)

### 1.1 Update Database Service PnL Calculation
**File:** `services/database-service/main.py`

**Current Issue:**
```sql
unrealized_pnl = (%s - entry_price) * position_size  -- NO FEES
```

**Solution:**
- Replace SQL-based PnL calculation with Python-based enhanced calculation
- Add fee calculation logic to database service
- Import enhanced PnL functions

**Implementation:**
```python
from strategy.strategy_pnl_enhanced import calculate_unrealized_pnl_with_fees

# Replace SQL calculation with Python function call
unrealized_pnl = calculate_unrealized_pnl_with_fees(
    entry_price=entry_price,
    current_price=current_price,
    position_size=position_size,
    entry_fee=entry_fee_amount,
    exit_fee=exit_fee_amount
)
```

### 1.2 Update Database Schema (if needed)
**Check if fee fields exist:**
- `entry_fee_amount`
- `entry_fee_currency`
- `exit_fee_amount`
- `exit_fee_currency`

**Add if missing:**
```sql
ALTER TABLE trading.trades 
ADD COLUMN IF NOT EXISTS entry_fee_amount DECIMAL(20,8) DEFAULT 0,
ADD COLUMN IF NOT EXISTS entry_fee_currency VARCHAR(10) DEFAULT 'USD',
ADD COLUMN IF NOT EXISTS exit_fee_amount DECIMAL(20,8) DEFAULT 0,
ADD COLUMN IF NOT EXISTS exit_fee_currency VARCHAR(10) DEFAULT 'USD';
```

## Phase 2: Utility Script Migration (Day 1-2)

### 2.1 Update `calculate_actual_pnl.py`
**Current:** Manual PnL calculation
**Target:** Use enhanced PnL functions for SPOT trading

```python
from strategy.strategy_pnl_enhanced import calculate_unrealized_pnl_with_fees

def calculate_pnl(entry_price, current_price, position_size):
    # SPOT trading - always long positions, no side parameter needed
    return calculate_unrealized_pnl_with_fees(
        entry_price=entry_price,
        current_price=current_price,
        position_size=position_size
    )
```

### 2.2 Update `validate_realized_pnl.py`
**Current:** Manual realized PnL calculation
**Target:** Use enhanced PnL functions for SPOT trading

```python
from strategy.strategy_pnl_enhanced import calculate_realized_pnl_with_fees

def calculate_realized_pnl(entry_price, exit_price, position_size):
    # SPOT trading - always long positions, no side parameter needed
    return calculate_realized_pnl_with_fees(
        entry_price=entry_price,
        exit_price=exit_price,
        position_size=position_size
    )
```

### 2.3 Update Other PnL Scripts
- `fix_realized_pnl_closed_trades.py`
- `fix_closed_trade_exit_price.py`
- Any other scripts with manual PnL calculations

## Phase 3: Enhanced PnL Module Enhancement (Day 2)

### 3.1 Add Missing Functions to `strategy_pnl_enhanced.py`
**Add realized PnL calculation for SPOT trading (long positions only):**
```python
def calculate_realized_pnl_with_fees(entry_price: float, exit_price: float, position_size: float,
                                   entry_fee: float = 0.0, exit_fee: float = 0.0) -> float:
    """
    Calculate realized PnL for SPOT trading (long positions only) including fees.
    """
    # SPOT trading - always long positions
    gross_pnl = (exit_price - entry_price) * position_size
    realized_pnl = gross_pnl - entry_fee - exit_fee
    return realized_pnl
```

### 3.2 Add Database Integration Functions
```python
async def update_trade_pnl_with_fees(trade_id: str, current_price: float, 
                                   entry_fee: float = 0.0, exit_fee: float = 0.0) -> bool:
    """
    Update trade PnL using enhanced calculation with fees.
    """
    # Implementation here
```

## Phase 4: Strategy PnL Deprecation (Day 2-3)

### 4.1 Rename `strategy_pnl.py` to `strategy_pnl_deprecated.py`
```bash
mv strategy/strategy_pnl.py strategy/strategy_pnl_deprecated.py
```

### 4.2 Add Deprecation Warning
**File:** `strategy/strategy_pnl_deprecated.py`
```python
"""
DEPRECATED: This module is deprecated and will be removed.
Use strategy_pnl_enhanced.py instead for accurate PnL calculations with fees.
"""

import warnings
warnings.warn(
    "strategy_pnl.py is deprecated. Use strategy_pnl_enhanced.py instead.",
    DeprecationWarning,
    stacklevel=2
)
```

### 4.3 Search and Replace Any Remaining Imports
```bash
# Find any remaining imports of the old module
grep -r "from strategy.strategy_pnl import" .
grep -r "import strategy.strategy_pnl" .
```

## Phase 5: Testing and Validation (Day 3)

### 5.1 Create Test Suite
**File:** `tests/test_pnl_calculations.py`
```python
import pytest
from strategy.strategy_pnl_enhanced import calculate_unrealized_pnl_with_fees

def test_pnl_calculation_with_fees():
    # Test cases with known expected results
    entry_price = 0.80
    current_price = 0.85
    position_size = 100
    entry_fee = 0.08
    exit_fee = 0.085
    
    expected_pnl = 4.835  # 5.00 - 0.08 - 0.085
    
    result = calculate_unrealized_pnl_with_fees(
        entry_price, current_price, position_size, entry_fee, exit_fee
    )
    
    assert abs(result - expected_pnl) < 0.001
```

### 5.2 Integration Testing
- Test database service PnL updates
- Test dashboard PnL display
- Test all utility scripts
- Verify fee calculations are correct

### 5.3 Performance Testing
- Ensure enhanced PnL calculations don't impact performance
- Test with large numbers of trades
- Verify database update performance

## Phase 6: Documentation and Cleanup (Day 3)

### 6.1 Update Documentation
- Update all README files
- Update API documentation
- Update strategy documentation
- Create migration guide

### 6.2 Remove Deprecated File
**After 30 days (safety period):**
```bash
rm strategy/strategy_pnl_deprecated.py
```

## Implementation Scripts

### Script 1: Database Service Migration
**File:** `migrate_database_pnl.py`
```python
#!/usr/bin/env python3
"""
Migrate database service to use enhanced PnL calculations.
"""

import re

def migrate_database_service():
    file_path = "services/database-service/main.py"
    
    with open(file_path, 'r') as f:
        content = f.read()
    
    # Add import
    import_line = 'from strategy.strategy_pnl_enhanced import calculate_unrealized_pnl_with_fees\n'
    
    # Find first import line
    lines = content.split('\n')
    import_index = 0
    for i, line in enumerate(lines):
        if line.strip().startswith('import ') or line.strip().startswith('from '):
            import_index = i + 1
        elif line.strip() and not line.strip().startswith('#'):
            break
    
    lines.insert(import_index, import_line)
    
    # Replace SQL PnL calculation
    old_pattern = r'unrealized_pnl = CASE \s+WHEN status = \'OPEN\' THEN \s+\(%s - entry_price\) \* position_size'
    new_pattern = '''unrealized_pnl = CASE 
                    WHEN status = 'OPEN' THEN 
                        calculate_unrealized_pnl_with_fees(entry_price, %s, position_size, 
                                                         COALESCE(entry_fee_amount, 0), 
                                                         COALESCE(exit_fee_amount, 0))'''
    
    content = re.sub(old_pattern, new_pattern, content, flags=re.MULTILINE)
    
    with open(file_path, 'w') as f:
        f.write(content)
    
    print("✅ Database service migrated to enhanced PnL calculation")

if __name__ == "__main__":
    migrate_database_service()
```

### Script 2: Utility Script Migration
**File:** `migrate_utility_scripts.py`
```python
#!/usr/bin/env python3
"""
Migrate utility scripts to use enhanced PnL calculations.
"""

import re

def migrate_script(file_path):
    with open(file_path, 'r') as f:
        content = f.read()
    
    # Add import
    import_line = 'from strategy.strategy_pnl_enhanced import calculate_unrealized_pnl_with_fees, calculate_realized_pnl_with_fees\n'
    
    lines = content.split('\n')
    import_index = 0
    for i, line in enumerate(lines):
        if line.strip().startswith('import ') or line.strip().startswith('from '):
            import_index = i + 1
        elif line.strip() and not line.strip().startswith('#'):
            break
    
    lines.insert(import_index, import_line)
    
    # Replace manual calculations
    content = '\n'.join(lines)
    
    # Replace unrealized PnL calculations
    content = re.sub(
        r'pnl = \(current_price - entry_price\) \* position_size',
        'pnl = calculate_unrealized_pnl_with_fees(entry_price, current_price, position_size)',
        content
    )
    
    # Replace realized PnL calculations
    content = re.sub(
        r'realized_pnl = \(exit_price - entry_price\) \* position_size',
        'realized_pnl = calculate_realized_pnl_with_fees(entry_price, exit_price, position_size)',
        content
    )
    
    with open(file_path, 'w') as f:
        f.write(content)
    
    print(f"✅ Migrated {file_path}")

def main():
    scripts = [
        'calculate_actual_pnl.py',
        'validate_realized_pnl.py',
        'fix_realized_pnl_closed_trades.py',
        'fix_closed_trade_exit_price.py'
    ]
    
    for script in scripts:
        try:
            migrate_script(script)
        except FileNotFoundError:
            print(f"⚠️  Script {script} not found, skipping")

if __name__ == "__main__":
    main()
```

## Success Criteria

### ✅ **Phase 1 Complete:**
- Database service uses enhanced PnL calculation
- All fee fields exist in database schema
- PnL calculations include fees

### ✅ **Phase 2 Complete:**
- All utility scripts use enhanced PnL functions
- No manual PnL calculations remain
- Consistent fee handling across all scripts

### ✅ **Phase 3 Complete:**
- Enhanced PnL module has all required functions
- Database integration functions available
- Comprehensive PnL calculation coverage

### ✅ **Phase 4 Complete:**
- `strategy_pnl.py` renamed to `strategy_pnl_deprecated.py`
- Deprecation warnings in place
- No remaining imports of deprecated module

### ✅ **Phase 5 Complete:**
- All tests pass
- PnL calculations verified as correct
- Performance acceptable
- Integration working properly

### ✅ **Phase 6 Complete:**
- Documentation updated
- Migration guide created
- System ready for deprecated file removal

## Risk Mitigation

### 🔒 **Backup Strategy:**
- Create backup of all files before migration
- Use version control (git) for rollback capability
- Test in development environment first

### 🔒 **Rollback Plan:**
- Keep original files as `.backup` extensions
- Document exact changes made
- Have rollback scripts ready

### 🔒 **Validation:**
- Compare PnL calculations before/after
- Verify fee calculations are correct
- Test with real trading data

## Timeline Summary

| Day | Phase | Tasks | Deliverables |
|-----|-------|-------|--------------|
| **Day 1** | 1-2 | Database service migration, Utility script migration | Enhanced PnL in database service |
| **Day 2** | 3-4 | Enhanced module updates, Deprecation setup | Complete enhanced PnL system |
| **Day 3** | 5-6 | Testing, Documentation, Cleanup | Production-ready system |

## Post-Migration Verification

### 📊 **PnL Accuracy Check:**
```python
# Test script to verify PnL calculations
def verify_pnl_accuracy():
    # Test with known values and expected results
    # Verify fees are included correctly
    # Compare with manual calculations
```

### 📊 **System Health Check:**
- All services running normally
- Dashboard displaying correct PnL values
- No performance degradation
- All tests passing

This migration plan ensures a complete transition to enhanced PnL calculations system-wide while maintaining system stability and accuracy.
