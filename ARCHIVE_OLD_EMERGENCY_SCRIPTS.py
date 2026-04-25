#!/usr/bin/env python3
"""
ARCHIVE OLD EMERGENCY SCRIPTS
=============================

This script archives the old emergency closure scripts and replaces them
with the new centralized emergency trade closure system.

Author: Claude AI
Created: 2025-01-09
"""

import os
import shutil
from datetime import datetime

def archive_old_scripts():
    """Archive old emergency scripts"""
    
    # Create archive directory
    archive_dir = f"archived_emergency_scripts_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    os.makedirs(archive_dir, exist_ok=True)
    
    # List of old emergency scripts to archive
    old_scripts = [
        "CLOSE_ALL_PHANTOM_TRADES_SIMPLE.py",
        "EMERGENCY_CLOSE_ALL_PHANTOM_TRADES.py", 
        "EMERGENCY_PHANTOM_TRADE_FIX.py",
        "FIX_PHANTOM_TRADES_IMMEDIATE.py",
        "COMPREHENSIVE_PHANTOM_TRADE_FIX.py",
        "FIX_PHANTOM_TRADES.py"
    ]
    
    archived_count = 0
    
    print("🗂️  ARCHIVING OLD EMERGENCY SCRIPTS")
    print("=" * 50)
    
    for script in old_scripts:
        if os.path.exists(script):
            try:
                # Move to archive directory
                shutil.move(script, os.path.join(archive_dir, script))
                print(f"✅ Archived: {script}")
                archived_count += 1
            except Exception as e:
                print(f"❌ Failed to archive {script}: {e}")
        else:
            print(f"⚠️  Not found: {script}")
    
    print(f"\n📁 Archived {archived_count} scripts to: {archive_dir}")
    
    # Create README in archive directory
    readme_content = f"""# Archived Emergency Scripts

These scripts were archived on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} 
as part of the centralized trade closure migration.

## Why Archived?

These scripts contained scattered trade closure logic with:
- Direct SQL UPDATE statements
- Inconsistent PnL calculations  
- Limited error handling
- No validation or constraints

## Replacement

Use the new centralized emergency script instead:
```bash
python3 CENTRALIZED_EMERGENCY_TRADE_CLOSURE.py
```

## Features of New Script

✅ Uses centralized trade closure service
✅ Proper validation and data integrity
✅ Exchange verification capabilities
✅ Comprehensive error handling
✅ Detailed logging and reporting
✅ Safe fallback mechanisms

## Migration Date
{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
    
    with open(os.path.join(archive_dir, "README.md"), "w") as f:
        f.write(readme_content)
    
    print(f"📄 Created README.md in archive directory")
    print("\n🎉 ARCHIVAL COMPLETE")

if __name__ == "__main__":
    archive_old_scripts()
