#!/usr/bin/env python3
"""
Test script to verify the Multi-Exchange Trading Bot setup
Checks imports, configurations, and basic functionality
"""

import sys
import os
import logging
from pathlib import Path

# Add the project root to the path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

def test_imports():
    """Test that all required modules can be imported"""
    print("Testing imports...")
    
    try:
        from core.config_manager import ConfigManager
        print("✓ ConfigManager imported successfully")
    except ImportError as e:
        print(f"✗ Failed to import ConfigManager: {e}")
        return False
    
    try:
        from core.database_manager import DatabaseManager
        print("✓ DatabaseManager imported successfully")
    except ImportError as e:
        print(f"✗ Failed to import DatabaseManager: {e}")
        return False
    
    try:
        from core.exchange_manager import ExchangeManager
        print("✓ ExchangeManager imported successfully")
    except ImportError as e:
        print(f"✗ Failed to import ExchangeManager: {e}")
        return False
    
    try:
        from core.strategy_manager import StrategyManager
        print("✓ StrategyManager imported successfully")
    except ImportError as e:
        print(f"✗ Failed to import StrategyManager: {e}")
        return False
    
    try:
        from orchestrator.trading_orchestrator import TradingOrchestrator
        print("✓ TradingOrchestrator imported successfully")
    except ImportError as e:
        print(f"✗ Failed to import TradingOrchestrator: {e}")
        return False
    
    return True

def test_configuration():
    """Test configuration file and basic setup"""
    print("\nTesting configuration...")
    
    config_path = Path("config/config.yaml")
    if not config_path.exists():
        print(f"✗ Configuration file not found: {config_path}")
        return False
    
    print("✓ Configuration file exists")
    
    try:
        from core.config_manager import ConfigManager
        config = ConfigManager(str(config_path))
        print("✓ Configuration loaded successfully")
        
        # Test basic config access
        db_config = config.get_database_config()
        if db_config:
            print("✓ Database configuration accessible")
        
        exchanges = config.get_all_exchanges()
        if exchanges:
            print(f"✓ Found {len(exchanges)} configured exchanges")
        
        return True
        
    except Exception as e:
        print(f"✗ Failed to load configuration: {e}")
        return False

def test_strategy_files():
    """Test that strategy files exist and are accessible"""
    print("\nTesting strategy files...")
    
    strategy_files = [
        "vwma_hull_strategy.py",
        "heikin_ashi_strategy.py", 
        "engulfing_multi_tf.py",
        "multi_timeframe_confluence_strategy.py",
        "strategy_pnl.py",
        "strategy_pnl_enhanced.py"
    ]
    
    all_exist = True
    for strategy_file in strategy_files:
        if Path(strategy_file).exists():
            print(f"✓ {strategy_file} exists")
        else:
            print(f"✗ {strategy_file} not found")
            all_exist = False
    
    return all_exist

def test_directories():
    """Test that required directories exist"""
    print("\nTesting directories...")
    
    required_dirs = [
        "core",
        "orchestrator", 
        "web",
        "web/templates",
        "web/static",
        "config",
        "scripts",
        "logs"
    ]
    
    all_exist = True
    for dir_path in required_dirs:
        if Path(dir_path).exists():
            print(f"✓ {dir_path}/ exists")
        else:
            print(f"✗ {dir_path}/ not found")
            all_exist = False
    
    return all_exist

def test_dependencies():
    """Test that required Python packages are installed"""
    print("\nTesting dependencies...")
    
    required_packages = [
        "fastapi",
        "uvicorn", 
        "pydantic",
        "sqlalchemy",
        "psycopg2",
        "redis",
        "ccxt",
        "pandas",
        "numpy",
        "ta"
    ]
    
    all_installed = True
    for package in required_packages:
        try:
            __import__(package)
            print(f"✓ {package} installed")
        except ImportError:
            print(f"✗ {package} not installed")
            all_installed = False
    
    return all_installed

def main():
    """Run all tests"""
    print("Multi-Exchange Trading Bot Setup Test")
    print("=" * 40)
    
    tests = [
        ("Dependencies", test_dependencies),
        ("Directories", test_directories),
        ("Strategy Files", test_strategy_files),
        ("Configuration", test_configuration),
        ("Imports", test_imports)
    ]
    
    results = []
    for test_name, test_func in tests:
        print(f"\n{test_name}:")
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"✗ {test_name} failed with error: {e}")
            results.append((test_name, False))
    
    # Summary
    print("\n" + "=" * 40)
    print("Test Summary:")
    
    passed = 0
    total = len(results)
    
    for test_name, result in results:
        status = "PASS" if result else "FAIL"
        print(f"{test_name}: {status}")
        if result:
            passed += 1
    
    print(f"\nOverall: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 All tests passed! The bot is ready to run.")
        print("\nNext steps:")
        print("1. Configure your API keys in config/config.yaml")
        print("2. Set up PostgreSQL database")
        print("3. Run: python3 -m orchestrator.trading_orchestrator")
        print("4. Start web dashboard: cd web && python app.py")
    else:
        print("❌ Some tests failed. Please fix the issues above before running the bot.")
        return 1
    
    return 0

if __name__ == "__main__":
    sys.exit(main()) 