#!/usr/bin/env python3
"""
Test runner script for the trading bot microservices
"""

import os
import sys
import subprocess
import argparse
import time
from pathlib import Path

def run_command(command, description):
    """Run a command and handle errors."""
    print(f"\n{'='*60}")
    print(f"Running: {description}")
    print(f"Command: {command}")
    print(f"{'='*60}")
    
    try:
        result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
        print("✅ Success!")
        if result.stdout:
            print("Output:")
            print(result.stdout)
        return True
    except subprocess.CalledProcessError as e:
        print("❌ Failed!")
        print(f"Error: {e}")
        if e.stdout:
            print("Stdout:")
            print(e.stdout)
        if e.stderr:
            print("Stderr:")
            print(e.stderr)
        return False

def setup_test_environment():
    """Setup test environment."""
    print("Setting up test environment...")
    
    # Create test database if it doesn't exist
    db_setup_commands = [
        "createdb -U carloslarramba trading_bot_test 2>/dev/null || echo 'Database already exists'",
        "psql -U carloslarramba -d trading_bot_test -f scripts/create_all_trading_bot_tables.sql"
    ]
    
    for command in db_setup_commands:
        if not run_command(command, f"Database setup: {command}"):
            print("Warning: Database setup failed, but continuing...")
    
    # Install test dependencies
    test_deps = [
        "pytest",
        "pytest-asyncio",
        "pytest-cov",
        "httpx",
        "psutil",
        "fastapi[testing]"
    ]
    
    for dep in test_deps:
        run_command(f"pip install {dep}", f"Installing {dep}")

def run_unit_tests():
    """Run unit tests."""
    print("\n🧪 Running Unit Tests...")
    
    unit_test_commands = [
        "python -m pytest tests/unit/ -v --tb=short",
        "python -m pytest tests/unit/ --cov=services --cov-report=html --cov-report=term"
    ]
    
    success = True
    for command in unit_test_commands:
        if not run_command(command, "Unit tests"):
            success = False
    
    return success

def run_integration_tests():
    """Run integration tests."""
    print("\n🔗 Running Integration Tests...")
    
    integration_test_commands = [
        "python -m pytest tests/integration/ -v --tb=short",
        "python -m pytest tests/integration/ --cov=services --cov-report=html --cov-report=term"
    ]
    
    success = True
    for command in integration_test_commands:
        if not run_command(command, "Integration tests"):
            success = False
    
    return success

def run_e2e_tests():
    """Run end-to-end tests."""
    print("\n🔄 Running End-to-End Tests...")
    
    e2e_test_commands = [
        "python -m pytest tests/e2e/ -v --tb=short",
        "python -m pytest tests/e2e/ --cov=services --cov-report=html --cov-report=term"
    ]
    
    success = True
    for command in e2e_test_commands:
        if not run_command(command, "E2E tests"):
            success = False
    
    return success

def run_performance_tests():
    """Run performance tests."""
    print("\n⚡ Running Performance Tests...")
    
    performance_test_commands = [
        "python -m pytest tests/performance/ -v --tb=short",
        "python -m pytest tests/performance/ --cov=services --cov-report=html --cov-report=term"
    ]
    
    success = True
    for command in performance_test_commands:
        if not run_command(command, "Performance tests"):
            success = False
    
    return success

def run_all_tests():
    """Run all tests."""
    print("\n🚀 Running All Tests...")
    
    all_test_commands = [
        "python -m pytest tests/ -v --tb=short",
        "python -m pytest tests/ --cov=services --cov-report=html --cov-report=term --cov-report=xml"
    ]
    
    success = True
    for command in all_test_commands:
        if not run_command(command, "All tests"):
            success = False
    
    return success

def generate_test_report():
    """Generate test report."""
    print("\n📊 Generating Test Report...")
    
    report_commands = [
        "python -m pytest tests/ --cov=services --cov-report=html --cov-report=term --cov-report=xml --junitxml=test-results.xml",
        "echo 'Test report generated in htmlcov/ and test-results.xml'"
    ]
    
    for command in report_commands:
        run_command(command, "Test report generation")

def cleanup_test_environment():
    """Cleanup test environment."""
    print("\n🧹 Cleaning up test environment...")
    
    cleanup_commands = [
        "rm -rf htmlcov/",
        "rm -f test-results.xml",
        "rm -rf .pytest_cache/",
        "rm -rf __pycache__/",
        "find . -name '*.pyc' -delete",
        "find . -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true"
    ]
    
    for command in cleanup_commands:
        run_command(command, f"Cleanup: {command}")

def main():
    """Main test runner function."""
    parser = argparse.ArgumentParser(description="Trading Bot Microservices Test Runner")
    parser.add_argument("--setup", action="store_true", help="Setup test environment")
    parser.add_argument("--unit", action="store_true", help="Run unit tests only")
    parser.add_argument("--integration", action="store_true", help="Run integration tests only")
    parser.add_argument("--e2e", action="store_true", help="Run end-to-end tests only")
    parser.add_argument("--performance", action="store_true", help="Run performance tests only")
    parser.add_argument("--all", action="store_true", help="Run all tests")
    parser.add_argument("--report", action="store_true", help="Generate test report")
    parser.add_argument("--cleanup", action="store_true", help="Cleanup test environment")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    
    args = parser.parse_args()
    
    # Set verbose mode
    if args.verbose:
        os.environ["PYTEST_VERBOSE"] = "1"
    
    start_time = time.time()
    
    try:
        # Setup if requested
        if args.setup:
            setup_test_environment()
        
        # Run specific test types
        if args.unit:
            success = run_unit_tests()
        elif args.integration:
            success = run_integration_tests()
        elif args.e2e:
            success = run_e2e_tests()
        elif args.performance:
            success = run_performance_tests()
        elif args.all:
            success = run_all_tests()
        elif args.report:
            generate_test_report()
            success = True
        elif args.cleanup:
            cleanup_test_environment()
            success = True
        else:
            # Default: run all tests
            print("No specific test type specified, running all tests...")
            success = run_all_tests()
        
        end_time = time.time()
        duration = end_time - start_time
        
        print(f"\n{'='*60}")
        print(f"Test execution completed in {duration:.2f} seconds")
        
        if success:
            print("✅ All tests passed!")
            sys.exit(0)
        else:
            print("❌ Some tests failed!")
            sys.exit(1)
    
    except KeyboardInterrupt:
        print("\n\n⚠️  Test execution interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n💥 Unexpected error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main() 