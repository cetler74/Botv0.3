#!/usr/bin/env python3
"""
Test script to verify PnL migration was successful.
Tests enhanced PnL calculations with fees for SPOT trading.
"""

import sys
import os

# Add the project root to the path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

def test_enhanced_pnl_import():
    """Test that enhanced PnL module can be imported"""
    try:
        from strategy.strategy_pnl_enhanced import calculate_unrealized_pnl, calculate_realized_pnl_with_fees
        print("✅ Enhanced PnL module imported successfully")
        return True
    except ImportError as e:
        print(f"❌ Failed to import enhanced PnL module: {e}")
        return False

def test_deprecated_pnl_import():
    """Test that deprecated PnL module shows warning"""
    try:
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            from strategy.strategy_pnl_deprecated import calculate_unrealized_pnl
            if len(w) > 0:
                print("✅ Deprecated PnL module shows deprecation warning")
                return True
            else:
                print("❌ Deprecated PnL module should show warning")
                return False
    except ImportError as e:
        print(f"❌ Failed to import deprecated PnL module: {e}")
        return False

def test_unrealized_pnl_calculation():
    """Test unrealized PnL calculation with fees"""
    try:
        from strategy.strategy_pnl_enhanced import calculate_unrealized_pnl
        
        # Test case: Buy 100 ADA at $0.80, current price $0.85
        # Create a mock position object
        class MockPosition:
            def __init__(self, entry_price, position_size, entry_fee=0.08):
                self.entry_price = entry_price
                self.position_size = position_size
                self.entry_fee_amount = entry_fee
                self.exit_fee_amount = 0.085
                self.position = 'long'  # SPOT trading - always long
                self.side = 'buy'       # SPOT trading - always buy
        
        position = MockPosition(0.80, 100, 0.08)
        current_price = 0.85
        
        # Expected calculation:
        # Gross PnL = (0.85 - 0.80) * 100 = $5.00
        # Net PnL = $5.00 - $0.08 - $0.085 = $4.835
        expected_pnl = 4.835
        
        result = calculate_unrealized_pnl(position, current_price)
        
        if abs(result - expected_pnl) < 0.001:
            print(f"✅ Unrealized PnL calculation correct: ${result:.3f}")
            return True
        else:
            print(f"❌ Unrealized PnL calculation incorrect: expected ${expected_pnl:.3f}, got ${result:.3f}")
            return False
            
    except Exception as e:
        print(f"❌ Error testing unrealized PnL: {e}")
        return False

def test_realized_pnl_calculation():
    """Test realized PnL calculation with fees"""
    try:
        from strategy.strategy_pnl_enhanced import calculate_realized_pnl_with_fees
        
        # Test case: Buy 100 ADA at $0.80, sell at $0.85
        entry_price = 0.80
        exit_price = 0.85
        position_size = 100
        entry_fee = 0.08  # $0.08 entry fee
        exit_fee = 0.085  # $0.085 exit fee
        
        # Expected calculation:
        # Gross PnL = (0.85 - 0.80) * 100 = $5.00
        # Net PnL = $5.00 - $0.08 - $0.085 = $4.835
        expected_pnl = 4.835
        
        result = calculate_realized_pnl_with_fees(
            entry_price=entry_price,
            exit_price=exit_price,
            position_size=position_size,
            entry_fee=entry_fee,
            exit_fee=exit_fee
        )
        
        if abs(result - expected_pnl) < 0.001:
            print(f"✅ Realized PnL calculation correct: ${result:.3f}")
            return True
        else:
            print(f"❌ Realized PnL calculation incorrect: expected ${expected_pnl:.3f}, got ${result:.3f}")
            return False
            
    except Exception as e:
        print(f"❌ Error testing realized PnL: {e}")
        return False

def test_spot_trading_pnl_module():
    """Test the SPOT trading PnL module"""
    try:
        from spot_trading_pnl import calculate_unrealized_pnl_spot, calculate_realized_pnl_spot
        
        # Test unrealized PnL
        entry_price = 0.80
        current_price = 0.85
        position_size = 100
        entry_fee = 0.08
        exit_fee = 0.085
        
        expected_pnl = 4.835
        
        result = calculate_unrealized_pnl_spot(
            entry_price, current_price, position_size, entry_fee, exit_fee
        )
        
        if abs(result - expected_pnl) < 0.001:
            print(f"✅ SPOT trading unrealized PnL correct: ${result:.3f}")
        else:
            print(f"❌ SPOT trading unrealized PnL incorrect: expected ${expected_pnl:.3f}, got ${result:.3f}")
            return False
        
        # Test realized PnL
        exit_price = 0.85
        result = calculate_realized_pnl_spot(
            entry_price, exit_price, position_size, entry_fee, exit_fee
        )
        
        if abs(result - expected_pnl) < 0.001:
            print(f"✅ SPOT trading realized PnL correct: ${result:.3f}")
            return True
        else:
            print(f"❌ SPOT trading realized PnL incorrect: expected ${expected_pnl:.3f}, got ${result:.3f}")
            return False
            
    except Exception as e:
        print(f"❌ Error testing SPOT trading PnL module: {e}")
        return False

def test_database_service_migration():
    """Test that database service uses enhanced PnL"""
    try:
        # Check if database service file has enhanced PnL import
        with open('services/database-service/main.py', 'r') as f:
            content = f.read()
        
        if 'from strategy.strategy_pnl_enhanced import' in content:
            print("✅ Database service uses enhanced PnL import")
        else:
            print("❌ Database service missing enhanced PnL import")
            return False
        
        if 'calculate_unrealized_pnl' in content:
            print("✅ Database service uses enhanced PnL function")
        else:
            print("❌ Database service missing enhanced PnL function")
            return False
        
        # Check that old SQL calculation is removed
        if '(%s - entry_price) * position_size' in content:
            print("❌ Database service still has old SQL PnL calculation")
            return False
        else:
            print("✅ Database service old SQL PnL calculation removed")
        
        return True
        
    except Exception as e:
        print(f"❌ Error testing database service migration: {e}")
        return False

def main():
    """Run all tests"""
    print("🧪 TESTING PnL MIGRATION")
    print("=" * 50)
    
    tests = [
        ("Enhanced PnL Import", test_enhanced_pnl_import),
        ("Deprecated PnL Warning", test_deprecated_pnl_import),
        ("Unrealized PnL Calculation", test_unrealized_pnl_calculation),
        ("Realized PnL Calculation", test_realized_pnl_calculation),
        ("SPOT Trading PnL Module", test_spot_trading_pnl_module),
        ("Database Service Migration", test_database_service_migration),
    ]
    
    passed = 0
    total = len(tests)
    
    for test_name, test_func in tests:
        print(f"\n🔍 Testing: {test_name}")
        if test_func():
            passed += 1
        else:
            print(f"❌ Test failed: {test_name}")
    
    print(f"\n📊 TEST RESULTS:")
    print(f"   Tests passed: {passed}/{total}")
    print(f"   Tests failed: {total - passed}")
    
    if passed == total:
        print(f"\n🎉 ALL TESTS PASSED!")
        print("   ✅ PnL migration completed successfully")
        print("   ✅ Enhanced PnL calculations working")
        print("   ✅ SPOT trading focus implemented")
        print("   ✅ Fee calculations included")
        print("   ✅ Database service migrated")
    else:
        print(f"\n⚠️  SOME TESTS FAILED")
        print("   Please check the failed tests above")

if __name__ == "__main__":
    main()
