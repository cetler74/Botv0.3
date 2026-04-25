#!/usr/bin/env python3
"""
Migrate utility scripts to use enhanced PnL calculations for SPOT trading only.
This script updates all utility scripts to use strategy_pnl_enhanced.py with SPOT trading focus.
"""

import re
import os
from datetime import datetime

def backup_file(file_path):
    """Create a backup of the file before modification"""
    backup_path = f"{file_path}.backup.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    with open(file_path, 'r') as src:
        with open(backup_path, 'w') as dst:
            dst.write(src.read())
    print(f"📁 Created backup: {backup_path}")
    return backup_path

def migrate_script(file_path):
    """Migrate a single script to use enhanced PnL calculations"""
    if not os.path.exists(file_path):
        print(f"⚠️  Script {file_path} not found, skipping")
        return False
    
    print(f"🔄 Migrating {file_path}...")
    
    # Create backup
    backup_path = backup_file(file_path)
    
    try:
        with open(file_path, 'r') as f:
            content = f.read()
        
        original_content = content
        
        # Step 1: Add import for enhanced PnL functions
        import_line = 'from strategy.strategy_pnl_enhanced import calculate_unrealized_pnl_with_fees, calculate_realized_pnl_with_fees\n'
        
        # Find the first import line
        lines = content.split('\n')
        import_index = 0
        
        for i, line in enumerate(lines):
            if line.strip().startswith('import ') or line.strip().startswith('from '):
                import_index = i + 1
            elif line.strip() and not line.strip().startswith('#'):
                break
        
        lines.insert(import_index, import_line)
        content = '\n'.join(lines)
        
        # Step 2: Replace manual PnL calculations with enhanced functions
        
        # Replace unrealized PnL calculations (SPOT trading - long positions only)
        patterns_to_replace = [
            # Pattern 1: Basic unrealized PnL calculation
            (r'pnl = \(current_price - entry_price\) \* position_size',
             'pnl = calculate_unrealized_pnl_with_fees(entry_price, current_price, position_size)'),
            
            # Pattern 2: Unrealized PnL with different variable names
            (r'unrealized_pnl = \(current_price - entry_price\) \* position_size',
             'unrealized_pnl = calculate_unrealized_pnl_with_fees(entry_price, current_price, position_size)'),
            
            # Pattern 3: PnL calculation in calculate_pnl function
            (r'return \(current_price - entry_price\) \* position_size',
             'return calculate_unrealized_pnl_with_fees(entry_price, current_price, position_size)'),
            
            # Pattern 4: Realized PnL calculations
            (r'realized_pnl = \(exit_price - entry_price\) \* position_size',
             'realized_pnl = calculate_realized_pnl_with_fees(entry_price, exit_price, position_size)'),
            
            # Pattern 5: Gross PnL calculations
            (r'gross_pnl = \(exit_price - entry_price\) \* position_size',
             'gross_pnl = calculate_realized_pnl_with_fees(entry_price, exit_price, position_size)'),
        ]
        
        for pattern, replacement in patterns_to_replace:
            if re.search(pattern, content):
                content = re.sub(pattern, replacement, content)
                print(f"✅ Replaced pattern: {pattern[:50]}...")
        
        # Step 3: Remove any short position logic (not needed for SPOT trading)
        # Remove side parameter from function calls since SPOT trading is always long
        content = re.sub(r'calculate_unrealized_pnl_with_fees\([^,]+,\s*[^,]+,\s*[^,]+,\s*[^,]*side[^,]*\)',
                        'calculate_unrealized_pnl_with_fees(entry_price, current_price, position_size)',
                        content)
        
        content = re.sub(r'calculate_realized_pnl_with_fees\([^,]+,\s*[^,]+,\s*[^,]+,\s*[^,]*side[^,]*\)',
                        'calculate_realized_pnl_with_fees(entry_price, exit_price, position_size)',
                        content)
        
        # Step 4: Update function signatures to remove side parameter
        # Replace function definitions that have side parameter
        content = re.sub(r'def calculate_pnl\([^)]*side[^)]*\):',
                        'def calculate_pnl(entry_price, current_price, position_size):',
                        content)
        
        content = re.sub(r'def calculate_realized_pnl\([^)]*side[^)]*\):',
                        'def calculate_realized_pnl(entry_price, exit_price, position_size):',
                        content)
        
        # Step 5: Add SPOT trading comment
        if 'SPOT trading' not in content:
            # Add comment at the top of the file
            lines = content.split('\n')
            if lines[0].startswith('#!/usr/bin/env python3'):
                lines.insert(1, '"""')
                lines.insert(2, 'SPOT Trading PnL Calculator - Long positions only')
                lines.insert(3, '"""')
            else:
                lines.insert(0, '"""')
                lines.insert(1, 'SPOT Trading PnL Calculator - Long positions only')
                lines.insert(2, '"""')
            content = '\n'.join(lines)
        
        # Step 6: Update the file
        if content != original_content:
            with open(file_path, 'w') as f:
                f.write(content)
            print(f"✅ Successfully migrated {file_path}")
            return True
        else:
            print(f"ℹ️  No changes needed for {file_path}")
            return True
            
    except Exception as e:
        print(f"❌ Error migrating {file_path}: {e}")
        print(f"📁 Restoring from backup: {backup_path}")
        
        # Restore from backup
        with open(backup_path, 'r') as src:
            with open(file_path, 'w') as dst:
                dst.write(src.read())
        return False

def create_spot_trading_pnl_module():
    """Create a simplified PnL module specifically for SPOT trading"""
    module_content = '''#!/usr/bin/env python3
"""
SPOT Trading PnL Calculator - Long positions only
Simplified PnL calculations for SPOT trading without short positions.
"""

def calculate_unrealized_pnl_spot(entry_price: float, current_price: float, position_size: float, 
                                entry_fee: float = 0.0, exit_fee: float = 0.0, fee_rate: float = 0.001) -> float:
    """
    Calculate unrealized PnL for SPOT trading (long positions only).
    
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
    # Calculate gross PnL (SPOT trading - always long)
    gross_pnl = (current_price - entry_price) * position_size
    
    # Use actual entry fee if provided, otherwise estimate
    actual_entry_fee = entry_fee if entry_fee > 0 else entry_price * position_size * fee_rate
    
    # Use actual exit fee if provided, otherwise estimate
    actual_exit_fee = exit_fee if exit_fee > 0 else current_price * position_size * fee_rate
    
    # Calculate net PnL after fees
    unrealized_pnl = gross_pnl - actual_entry_fee - actual_exit_fee
    
    return unrealized_pnl

def calculate_realized_pnl_spot(entry_price: float, exit_price: float, position_size: float,
                              entry_fee: float = 0.0, exit_fee: float = 0.0) -> float:
    """
    Calculate realized PnL for SPOT trading (long positions only).
    
    Args:
        entry_price: Entry price of the position
        exit_price: Exit price of the position
        position_size: Size of the position
        entry_fee: Actual entry fee
        exit_fee: Actual exit fee
        
    Returns:
        Realized PnL including fees
    """
    # Calculate gross PnL (SPOT trading - always long)
    gross_pnl = (exit_price - entry_price) * position_size
    
    # Calculate net PnL after fees
    realized_pnl = gross_pnl - entry_fee - exit_fee
    
    return realized_pnl

def update_trade_pnl_spot(trade_data: dict, current_price: float) -> dict:
    """
    Update trade PnL for SPOT trading.
    
    Args:
        trade_data: Trade data dictionary
        current_price: Current market price
        
    Returns:
        Updated trade data with PnL
    """
    entry_price = trade_data.get('entry_price', 0)
    position_size = trade_data.get('position_size', 0)
    entry_fee = trade_data.get('entry_fee_amount', 0)
    exit_fee = trade_data.get('exit_fee_amount', 0)
    
    if entry_price > 0 and position_size > 0:
        unrealized_pnl = calculate_unrealized_pnl_spot(
            entry_price, current_price, position_size, entry_fee, exit_fee
        )
        trade_data['unrealized_pnl'] = unrealized_pnl
        trade_data['current_price'] = current_price
    
    return trade_data
'''
    
    with open('spot_trading_pnl.py', 'w') as f:
        f.write(module_content)
    
    print("✅ Created SPOT trading PnL module: spot_trading_pnl.py")

def main():
    """Main migration function"""
    print("🚀 UTILITY SCRIPTS PnL MIGRATION (SPOT TRADING)")
    print("=" * 60)
    
    # Create SPOT trading PnL module
    create_spot_trading_pnl_module()
    
    # List of scripts to migrate
    scripts = [
        'calculate_actual_pnl.py',
        'validate_realized_pnl.py',
        'fix_realized_pnl_closed_trades.py',
        'fix_closed_trade_exit_price.py',
        'fix_realized_pnl_values_simple.py'
    ]
    
    migrated_count = 0
    total_scripts = len(scripts)
    
    print(f"\n📁 Migrating {total_scripts} utility scripts...")
    
    for script in scripts:
        if migrate_script(script):
            migrated_count += 1
    
    print(f"\n📊 MIGRATION SUMMARY:")
    print(f"   Scripts processed: {total_scripts}")
    print(f"   Successfully migrated: {migrated_count}")
    print(f"   Failed: {total_scripts - migrated_count}")
    
    if migrated_count == total_scripts:
        print(f"\n🎉 ALL UTILITY SCRIPTS MIGRATED SUCCESSFULLY!")
        print("   ✅ Enhanced PnL calculations now in use")
        print("   ✅ SPOT trading focused (no short positions)")
        print("   ✅ Fee calculations included")
        print("   ✅ Backups created for safety")
    else:
        print(f"\n⚠️  MIGRATION PARTIALLY COMPLETED")
        print("   Check the error messages above for failed scripts")

if __name__ == "__main__":
    main()
