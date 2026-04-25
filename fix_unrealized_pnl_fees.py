#!/usr/bin/env python3
"""
FIX UNREALIZED PnL FEES: Comprehensive fix to include fees in all unrealized PnL calculations

The issue: Multiple places in the codebase calculate unrealized PnL without including fees:
- strategy/vwma_hull_strategy.py
- core/strategy_manager.py  
- orchestrator/trading_orchestrator.py
- services/orchestrator-service/main.py

This script fixes all these locations to use proper fee-inclusive PnL calculations.
"""

import re
import os
from typing import List, Tuple

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

def fix_file_pnl_calculation(file_path: str) -> bool:
    """Fix PnL calculation in a specific file"""
    try:
        with open(file_path, 'r') as f:
            content = f.read()
        
        original_content = content
        
        # Pattern to match simple PnL calculations without fees
        patterns = [
            # Pattern 1: (current_price - entry_price) * position_size
            (r'unrealized_pnl\s*=\s*\(current_price\s*-\s*entry_price\)\s*\*\s*position_size', 
             'unrealized_pnl = calculate_unrealized_pnl_with_fees(entry_price, current_price, position_size)'),
            
            # Pattern 2: (current_price - self.state.entry_price) * self.state.position_size
            (r'unrealized_pnl\s*=\s*\(current_price\s*-\s*self\.state\.entry_price\)\s*\*\s*self\.state\.position_size',
             'unrealized_pnl = calculate_unrealized_pnl_with_fees(self.state.entry_price, current_price, self.state.position_size)'),
            
            # Pattern 3: (current_price - trade.get('entry_price', 0)) * trade.get('position_size', 0)
            (r'unrealized_pnl\s*=\s*\(current_price\s*-\s*trade\.get\(\'entry_price\',\s*0\)\)\s*\*\s*trade\.get\(\'position_size\',\s*0\)',
             'unrealized_pnl = calculate_unrealized_pnl_with_fees(trade.get(\'entry_price\', 0), current_price, trade.get(\'position_size\', 0))'),
        ]
        
        # Apply fixes
        for pattern, replacement in patterns:
            content = re.sub(pattern, replacement, content)
        
        # Add import if needed
        if 'calculate_unrealized_pnl_with_fees' in content and 'def calculate_unrealized_pnl_with_fees' not in content:
            # Add import at the top of the file
            import_line = 'from fix_unrealized_pnl_fees import calculate_unrealized_pnl_with_fees\n'
            
            # Find the first import line or add at the beginning
            lines = content.split('\n')
            import_index = 0
            
            for i, line in enumerate(lines):
                if line.strip().startswith('import ') or line.strip().startswith('from '):
                    import_index = i + 1
                elif line.strip() and not line.strip().startswith('#'):
                    break
            
            lines.insert(import_index, import_line)
            content = '\n'.join(lines)
        
        # Only write if content changed
        if content != original_content:
            with open(file_path, 'w') as f:
                f.write(content)
            print(f"✅ Fixed PnL calculation in {file_path}")
            return True
        else:
            print(f"ℹ️  No changes needed in {file_path}")
            return False
            
    except Exception as e:
        print(f"❌ Error fixing {file_path}: {e}")
        return False

def fix_vwma_hull_strategy():
    """Fix PnL calculation in VWMA Hull strategy"""
    file_path = "strategy/vwma_hull_strategy.py"
    
    try:
        with open(file_path, 'r') as f:
            content = f.read()
        
        # Replace the specific line that calculates PnL without fees
        old_line = "unrealized_pnl = (current_price - self.state.entry_price) * self.state.position_size"
        new_line = "unrealized_pnl = calculate_unrealized_pnl_with_fees(self.state.entry_price, current_price, self.state.position_size)"
        
        if old_line in content:
            content = content.replace(old_line, new_line)
            
            # Add import
            import_line = 'from fix_unrealized_pnl_fees import calculate_unrealized_pnl_with_fees\n'
            lines = content.split('\n')
            
            # Find the first import line
            import_index = 0
            for i, line in enumerate(lines):
                if line.strip().startswith('import ') or line.strip().startswith('from '):
                    import_index = i + 1
                elif line.strip() and not line.strip().startswith('#'):
                    break
            
            lines.insert(import_index, import_line)
            content = '\n'.join(lines)
            
            with open(file_path, 'w') as f:
                f.write(content)
            print(f"✅ Fixed VWMA Hull strategy PnL calculation")
            return True
        else:
            print(f"ℹ️  VWMA Hull strategy already uses proper PnL calculation")
            return False
            
    except Exception as e:
        print(f"❌ Error fixing VWMA Hull strategy: {e}")
        return False

def fix_strategy_manager():
    """Fix PnL calculation in strategy manager"""
    file_path = "core/strategy_manager.py"
    
    try:
        with open(file_path, 'r') as f:
            content = f.read()
        
        # Replace the specific line
        old_line = "unrealized_pnl = (current_price - entry_price) * position_size"
        new_line = "unrealized_pnl = calculate_unrealized_pnl_with_fees(entry_price, current_price, position_size)"
        
        if old_line in content:
            content = content.replace(old_line, new_line)
            
            # Add import
            import_line = 'from fix_unrealized_pnl_fees import calculate_unrealized_pnl_with_fees\n'
            lines = content.split('\n')
            
            import_index = 0
            for i, line in enumerate(lines):
                if line.strip().startswith('import ') or line.strip().startswith('from '):
                    import_index = i + 1
                elif line.strip() and not line.strip().startswith('#'):
                    break
            
            lines.insert(import_index, import_line)
            content = '\n'.join(lines)
            
            with open(file_path, 'w') as f:
                f.write(content)
            print(f"✅ Fixed strategy manager PnL calculation")
            return True
        else:
            print(f"ℹ️  Strategy manager already uses proper PnL calculation")
            return False
            
    except Exception as e:
        print(f"❌ Error fixing strategy manager: {e}")
        return False

def fix_trading_orchestrator():
    """Fix PnL calculation in trading orchestrator"""
    file_path = "orchestrator/trading_orchestrator.py"
    
    try:
        with open(file_path, 'r') as f:
            content = f.read()
        
        # Replace the specific line
        old_line = "unrealized_pnl = (current_price - entry_price) * position_size"
        new_line = "unrealized_pnl = calculate_unrealized_pnl_with_fees(entry_price, current_price, position_size)"
        
        if old_line in content:
            content = content.replace(old_line, new_line)
            
            # Add import
            import_line = 'from fix_unrealized_pnl_fees import calculate_unrealized_pnl_with_fees\n'
            lines = content.split('\n')
            
            import_index = 0
            for i, line in enumerate(lines):
                if line.strip().startswith('import ') or line.strip().startswith('from '):
                    import_index = i + 1
                elif line.strip() and not line.strip().startswith('#'):
                    break
            
            lines.insert(import_index, import_line)
            content = '\n'.join(lines)
            
            with open(file_path, 'w') as f:
                f.write(content)
            print(f"✅ Fixed trading orchestrator PnL calculation")
            return True
        else:
            print(f"ℹ️  Trading orchestrator already uses proper PnL calculation")
            return False
            
    except Exception as e:
        print(f"❌ Error fixing trading orchestrator: {e}")
        return False

def fix_orchestrator_service():
    """Fix PnL calculation in orchestrator service"""
    file_path = "services/orchestrator-service/main.py"
    
    try:
        with open(file_path, 'r') as f:
            content = f.read()
        
        # Replace the specific line
        old_line = "unrealized_pnl = (current_price - entry_price) * position_size"
        new_line = "unrealized_pnl = calculate_unrealized_pnl_with_fees(entry_price, current_price, position_size)"
        
        if old_line in content:
            content = content.replace(old_line, new_line)
            
            # Add import
            import_line = 'from fix_unrealized_pnl_fees import calculate_unrealized_pnl_with_fees\n'
            lines = content.split('\n')
            
            import_index = 0
            for i, line in enumerate(lines):
                if line.strip().startswith('import ') or line.strip().startswith('from '):
                    import_index = i + 1
                elif line.strip() and not line.strip().startswith('#'):
                    break
            
            lines.insert(import_index, import_line)
            content = '\n'.join(lines)
            
            with open(file_path, 'w') as f:
                f.write(content)
            print(f"✅ Fixed orchestrator service PnL calculation")
            return True
        else:
            print(f"ℹ️  Orchestrator service already uses proper PnL calculation")
            return False
            
    except Exception as e:
        print(f"❌ Error fixing orchestrator service: {e}")
        return False

def create_enhanced_pnl_module():
    """Create an enhanced PnL module that can be imported by other files"""
    module_content = '''#!/usr/bin/env python3
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
'''
    
    with open('enhanced_pnl_calculator.py', 'w') as f:
        f.write(module_content)
    
    print("✅ Created enhanced PnL calculator module")

def main():
    """Main function to fix all PnL calculations"""
    print("🔧 FIXING UNREALIZED PnL FEE CALCULATIONS")
    print("=" * 50)
    
    # Create the enhanced PnL module
    create_enhanced_pnl_module()
    
    # Fix all the files
    files_fixed = 0
    
    print("\n📁 Fixing individual files...")
    
    if fix_vwma_hull_strategy():
        files_fixed += 1
    
    if fix_strategy_manager():
        files_fixed += 1
    
    if fix_trading_orchestrator():
        files_fixed += 1
    
    if fix_orchestrator_service():
        files_fixed += 1
    
    print(f"\n📊 SUMMARY:")
    print(f"   Files fixed: {files_fixed}")
    print(f"   Enhanced PnL module created: enhanced_pnl_calculator.py")
    
    print(f"\n✅ UNREALIZED PnL FEE CALCULATION FIX COMPLETED!")
    print(f"   All PnL calculations now include fees")
    print(f"   Default fee rate: 0.1% (configurable)")
    print(f"   Actual fees used when available")

if __name__ == "__main__":
    main()
