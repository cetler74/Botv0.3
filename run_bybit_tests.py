#!/usr/bin/env python3
"""
Bybit WebSocket Test Runner - Version 2.6.0
Simple script to run Bybit WebSocket integration tests
"""

import asyncio
import sys
import os

# Add the project root to the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

async def main():
    """Run Bybit WebSocket tests"""
    try:
        from test_bybit_websocket_integration import TestBybitWebSocketIntegration
        
        print("🚀 Starting Bybit WebSocket Integration Tests...")
        print("=" * 60)
        
        test_suite = TestBybitWebSocketIntegration()
        results = await test_suite.run_all_tests()
        
        # Print final summary
        total = results['total']
        passed = results['passed']
        failed = results['failed']
        success_rate = (passed / total * 100) if total > 0 else 0
        
        print(f"\n📊 FINAL RESULTS:")
        print(f"   Total Tests: {total}")
        print(f"   Passed: {passed}")
        print(f"   Failed: {failed}")
        print(f"   Success Rate: {success_rate:.1f}%")
        
        if success_rate >= 80:
            print("🎉 SUCCESS: Bybit WebSocket integration is ready for production!")
            return 0
        else:
            print("⚠️  WARNING: Some tests failed. Review the results above.")
            return 1
            
    except ImportError as e:
        print(f"❌ Import Error: {e}")
        print("Make sure all required modules are available.")
        return 1
    except Exception as e:
        print(f"❌ Test execution failed: {e}")
        return 1

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
