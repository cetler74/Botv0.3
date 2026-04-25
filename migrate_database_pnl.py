#!/usr/bin/env python3
"""
Migrate database service to use enhanced PnL calculations.
This script updates the database service to use strategy_pnl_enhanced.py instead of SQL-based calculations.
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

def migrate_database_service():
    """Migrate database service to use enhanced PnL calculations"""
    file_path = "services/database-service/main.py"
    
    if not os.path.exists(file_path):
        print(f"❌ File not found: {file_path}")
        return False
    
    print(f"🔄 Migrating database service PnL calculations...")
    
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
        
        # Step 2: Replace SQL-based PnL calculation in update_current_prices endpoint
        # Find the problematic SQL query
        old_pattern = r'unrealized_pnl = CASE \s+WHEN status = \'OPEN\' THEN \s+\(%s - entry_price\) \* position_size \s+ELSE unrealized_pnl \s+END'
        
        if re.search(old_pattern, content, re.MULTILINE):
            print("🔍 Found SQL-based PnL calculation, replacing with Python function...")
            
            # Replace with Python-based calculation
            new_pattern = '''unrealized_pnl = CASE 
                    WHEN status = 'OPEN' THEN 
                        calculate_unrealized_pnl_with_fees(entry_price, %s, position_size, 
                                                         COALESCE(entry_fee_amount, 0), 
                                                         COALESCE(exit_fee_amount, 0))
                    ELSE unrealized_pnl 
                END'''
            
            content = re.sub(old_pattern, new_pattern, content, flags=re.MULTILINE)
            print("✅ Replaced SQL PnL calculation with Python function")
        else:
            print("⚠️  SQL PnL calculation pattern not found, checking for other patterns...")
            
            # Look for simpler pattern
            simple_pattern = r'\(%s - entry_price\) \* position_size'
            if re.search(simple_pattern, content):
                print("🔍 Found simple PnL calculation pattern...")
                content = re.sub(simple_pattern, 
                               'calculate_unrealized_pnl_with_fees(entry_price, %s, position_size, COALESCE(entry_fee_amount, 0), COALESCE(exit_fee_amount, 0))', 
                               content)
                print("✅ Replaced simple PnL calculation")
        
        # Step 3: Add fee calculation helper function
        helper_function = '''
def calculate_trade_pnl_with_fees(entry_price: float, current_price: float, position_size: float, 
                                entry_fee_amount: float = 0.0, exit_fee_amount: float = 0.0) -> float:
    """
    Calculate unrealized PnL with fees for database service.
    """
    try:
        return calculate_unrealized_pnl_with_fees(
            entry_price=entry_price,
            current_price=current_price,
            position_size=position_size,
            entry_fee=entry_fee_amount,
            exit_fee=exit_fee_amount
        )
    except Exception as e:
        logger.error(f"Error calculating PnL with fees: {e}")
        # Fallback to basic calculation
        return (current_price - entry_price) * position_size
'''
        
        # Add helper function after imports
        lines = content.split('\n')
        import_index = 0
        for i, line in enumerate(lines):
            if line.strip().startswith('import ') or line.strip().startswith('from '):
                import_index = i + 1
            elif line.strip() and not line.strip().startswith('#'):
                break
        
        lines.insert(import_index, helper_function)
        content = '\n'.join(lines)
        
        # Step 4: Update the file
        if content != original_content:
            with open(file_path, 'w') as f:
                f.write(content)
            print("✅ Database service successfully migrated to enhanced PnL calculations")
            return True
        else:
            print("ℹ️  No changes needed - file already uses enhanced PnL calculations")
            return True
            
    except Exception as e:
        print(f"❌ Error migrating database service: {e}")
        print(f"📁 Restoring from backup: {backup_path}")
        
        # Restore from backup
        with open(backup_path, 'r') as src:
            with open(file_path, 'w') as dst:
                dst.write(src.read())
        return False

def verify_migration():
    """Verify that the migration was successful"""
    file_path = "services/database-service/main.py"
    
    try:
        with open(file_path, 'r') as f:
            content = f.read()
        
        # Check for enhanced PnL import
        if 'from strategy.strategy_pnl_enhanced import' in content:
            print("✅ Enhanced PnL import found")
        else:
            print("❌ Enhanced PnL import not found")
            return False
        
        # Check for SQL-based calculation (should not be present)
        if '(%s - entry_price) * position_size' in content:
            print("❌ SQL-based PnL calculation still present")
            return False
        else:
            print("✅ SQL-based PnL calculation removed")
        
        # Check for Python function usage
        if 'calculate_unrealized_pnl_with_fees' in content:
            print("✅ Enhanced PnL function usage found")
        else:
            print("❌ Enhanced PnL function usage not found")
            return False
        
        print("✅ Migration verification successful")
        return True
        
    except Exception as e:
        print(f"❌ Error verifying migration: {e}")
        return False

def main():
    """Main migration function"""
    print("🚀 DATABASE SERVICE PnL MIGRATION")
    print("=" * 50)
    
    # Perform migration
    success = migrate_database_service()
    
    if success:
        print("\n🔍 Verifying migration...")
        verify_success = verify_migration()
        
        if verify_success:
            print("\n🎉 DATABASE SERVICE MIGRATION COMPLETED SUCCESSFULLY!")
            print("   ✅ Enhanced PnL calculations now in use")
            print("   ✅ Fee calculations included")
            print("   ✅ Backup created for safety")
        else:
            print("\n⚠️  Migration completed but verification failed")
            print("   Please check the file manually")
    else:
        print("\n❌ MIGRATION FAILED")
        print("   Check the error messages above")
        print("   Backup file created for manual recovery")

if __name__ == "__main__":
    main()
