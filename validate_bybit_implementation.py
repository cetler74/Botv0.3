#!/usr/bin/env python3
"""
Bybit WebSocket Implementation Validation - Version 2.6.0
Simple validation script to test available components
"""

import os
import sys
import importlib.util
from pathlib import Path

def check_file_exists(file_path, description):
    """Check if a file exists and report status"""
    if os.path.exists(file_path):
        print(f"✅ {description}: {file_path}")
        return True
    else:
        print(f"❌ {description}: {file_path} - NOT FOUND")
        return False

def check_module_import(module_path, module_name, description):
    """Check if a module can be imported"""
    try:
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            print(f"✅ {description}: {module_path}")
            return True
        else:
            print(f"❌ {description}: {module_path} - INVALID MODULE")
            return False
    except Exception as e:
        print(f"❌ {description}: {module_path} - IMPORT ERROR: {e}")
        return False

def main():
    """Main validation function"""
    print("🔍 Bybit WebSocket Implementation Validation")
    print("=" * 60)
    
    # Track results
    total_checks = 0
    passed_checks = 0
    
    # Check core implementation files
    print("\n📁 Core Implementation Files:")
    print("-" * 40)
    
    core_files = [
        ("services/exchange-service/bybit_auth_manager.py", "Bybit Authentication Manager"),
        ("services/exchange-service/bybit_connection_manager.py", "Bybit Connection Manager"),
        ("services/exchange-service/bybit_user_data_stream.py", "Bybit User Data Stream"),
        ("services/exchange-service/bybit_event_processors.py", "Bybit Event Processors"),
        ("services/exchange-service/bybit_websocket_integration.py", "Bybit WebSocket Integration"),
        ("services/exchange-service/bybit_health_monitor.py", "Bybit Health Monitor"),
        ("services/exchange-service/bybit_error_handlers.py", "Bybit Error Handlers"),
        ("services/exchange-service/bybit_recovery_manager.py", "Bybit Recovery Manager"),
        ("services/exchange-service/bybit_fallback_manager.py", "Bybit Fallback Manager"),
    ]
    
    for file_path, description in core_files:
        total_checks += 1
        if check_file_exists(file_path, description):
            passed_checks += 1
    
    # Check fill detection integration
    print("\n🔍 Fill Detection Integration:")
    print("-" * 40)
    
    fill_detection_files = [
        ("services/fill-detection-service/bybit_websocket_consumer.py", "Bybit WebSocket Consumer"),
    ]
    
    for file_path, description in fill_detection_files:
        total_checks += 1
        if check_file_exists(file_path, description):
            passed_checks += 1
    
    # Check database integration
    print("\n🗄️ Database Integration:")
    print("-" * 40)
    
    database_files = [
        ("services/database-service/bybit_data_manager.py", "Bybit Data Manager"),
        ("scripts/migrate_bybit_enhanced_tracking.sql", "Bybit Database Migration"),
    ]
    
    for file_path, description in database_files:
        total_checks += 1
        if check_file_exists(file_path, description):
            passed_checks += 1
    
    # Check dashboard integration
    print("\n📊 Dashboard Integration:")
    print("-" * 40)
    
    dashboard_files = [
        ("services/web-dashboard-service/static/js/enhanced-dashboard.js", "Enhanced Dashboard JS"),
        ("services/web-dashboard-service/templates/enhanced-dashboard.html", "Enhanced Dashboard HTML"),
        ("services/web-dashboard-service/main.py", "Web Dashboard Service"),
    ]
    
    for file_path, description in dashboard_files:
        total_checks += 1
        if check_file_exists(file_path, description):
            passed_checks += 1
    
    # Check test files
    print("\n🧪 Test Files:")
    print("-" * 40)
    
    test_files = [
        ("test_bybit_websocket_integration.py", "Comprehensive Test Suite"),
        ("test_bybit_config.yaml", "Test Configuration"),
        ("run_bybit_tests.py", "Test Runner"),
    ]
    
    for file_path, description in test_files:
        total_checks += 1
        if check_file_exists(file_path, description):
            passed_checks += 1
    
    # Check documentation
    print("\n📚 Documentation:")
    print("-" * 40)
    
    doc_files = [
        ("BYBIT_WEBSOCKET_IMPLEMENTATION_TASKS.md", "Implementation Tasks"),
        ("docs/BYBIT_WEBSOCKET_INTEGRATION.md", "Integration Documentation"),
    ]
    
    for file_path, description in doc_files:
        total_checks += 1
        if check_file_exists(file_path, description):
            passed_checks += 1
    
    # Test module imports (for key components)
    print("\n🔧 Module Import Tests:")
    print("-" * 40)
    
    # Add services/exchange-service to Python path
    exchange_service_path = os.path.join(os.path.dirname(__file__), 'services', 'exchange-service')
    if exchange_service_path not in sys.path:
        sys.path.insert(0, exchange_service_path)
    
    import_tests = [
        ("services/exchange-service/bybit_auth_manager.py", "bybit_auth_manager", "BybitAuthManager Import"),
        ("services/exchange-service/bybit_health_monitor.py", "bybit_health_monitor", "BybitHealthMonitor Import"),
    ]
    
    for file_path, module_name, description in import_tests:
        total_checks += 1
        if check_module_import(file_path, module_name, description):
            passed_checks += 1
    
    # Print summary
    print("\n" + "=" * 60)
    print("📊 VALIDATION SUMMARY")
    print("=" * 60)
    print(f"Total Checks: {total_checks}")
    print(f"Passed: {passed_checks}")
    print(f"Failed: {total_checks - passed_checks}")
    success_rate = (passed_checks / total_checks * 100) if total_checks > 0 else 0
    print(f"Success Rate: {success_rate:.1f}%")
    
    if success_rate >= 90:
        print("🎉 EXCELLENT! Bybit WebSocket implementation is complete!")
    elif success_rate >= 80:
        print("👍 GOOD! Bybit WebSocket implementation is mostly complete.")
    elif success_rate >= 70:
        print("⚠️ FAIR! Bybit WebSocket implementation needs some work.")
    else:
        print("🚨 POOR! Bybit WebSocket implementation needs significant work.")
    
    print("=" * 60)
    
    return 0 if success_rate >= 80 else 1

if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
