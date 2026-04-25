#!/usr/bin/env python3
"""
SIMPLE FILL MONITORING: Track fill-detection failures in real-time
Monitor database vs exchange status to catch discrepancies immediately
"""
import requests
import time
import json
from datetime import datetime

DATABASE_URL = "http://localhost:8002"
EXCHANGE_URL = "http://localhost:8003"

class SimpleFillMonitor:
    def __init__(self):
        self.known_open_trades = {}
        
    def get_open_trades(self):
        """Get current open trades from database"""
        try:
            response = requests.get(f"{DATABASE_URL}/api/v1/trades", timeout=10)
            if response.status_code == 200:
                trades = response.json().get("trades", [])
                return [t for t in trades if t.get("status") == "OPEN"]
            return []
        except Exception as e:
            print(f"❌ Error getting trades: {e}")
            return []
    
    def check_exchange_status(self, exchange, order_id):
        """Check order status on exchange"""
        try:
            response = requests.get(
                f"{EXCHANGE_URL}/api/v1/trading/order/{exchange}/{order_id}",
                timeout=10
            )
            if response.status_code == 200:
                order_data = response.json().get("order", {})
                return {
                    "status": order_data.get("status", "").lower(),
                    "filled": order_data.get("filled", 0),
                    "remaining": order_data.get("remaining", 0)
                }
        except Exception as e:
            print(f"⚠️ Error checking {exchange} order {order_id}: {e}")
        return None
    
    def monitor_cycle(self):
        """Single monitoring cycle"""
        print(f"\n⏰ {datetime.now().strftime('%H:%M:%S')} - Checking for fill-detection failures...")
        
        open_trades = self.get_open_trades()
        
        if not open_trades:
            print("✅ No open trades to monitor")
            return
            
        print(f"📊 Monitoring {len(open_trades)} open trades...")
        
        failures_found = 0
        
        for trade in open_trades:
            trade_id = trade["trade_id"][:8]
            exchange = trade.get("exchange")
            entry_id = trade.get("entry_id")
            pair = trade.get("pair")
            
            if not entry_id or not exchange:
                continue
                
            # Check if this order is actually filled on exchange
            exchange_status = self.check_exchange_status(exchange, entry_id)
            
            if exchange_status:
                status = exchange_status["status"]
                filled = exchange_status["filled"]
                
                if status in ["filled", "closed"] or filled > 0:
                    print(f"🚨 FILL-DETECTION FAILURE DETECTED!")
                    print(f"   Trade ID: {trade_id}")
                    print(f"   Exchange: {exchange}")
                    print(f"   Order ID: {entry_id}")
                    print(f"   Pair: {pair}")
                    print(f"   Database Status: OPEN")
                    print(f"   Exchange Status: {status}")
                    print(f"   Filled Quantity: {filled}")
                    print(f"   ⚡ This order should be CLOSED in database!")
                    failures_found += 1
                else:
                    print(f"✅ {trade_id} - {pair} - Correctly OPEN ({status})")
            else:
                print(f"⚠️ {trade_id} - {pair} - Could not verify exchange status")
        
        if failures_found > 0:
            print(f"\n🚨 FOUND {failures_found} FILL-DETECTION FAILURES!")
            print("   These orders are filled on exchange but OPEN in database")
            print("   Manual recovery required!")
        else:
            print(f"✅ All {len(open_trades)} open trades are correctly tracked")
            
        return failures_found

def main():
    """Main monitoring loop"""
    print("🔍 SIMPLE FILL-DETECTION MONITOR STARTED")
    print("This monitors for orders that are filled on exchange but OPEN in database")
    print("Press Ctrl+C to stop")
    print("-" * 70)
    
    monitor = SimpleFillMonitor()
    
    try:
        while True:
            failures = monitor.monitor_cycle()
            
            if failures > 0:
                print(f"\n💀 CRITICAL: {failures} fill-detection failures found!")
                print("Continuing to monitor for more failures...")
            
            print("-" * 50)
            time.sleep(30)  # Check every 30 seconds
            
    except KeyboardInterrupt:
        print("\n🛑 Monitoring stopped")
    except Exception as e:
        print(f"❌ Fatal error: {e}")

if __name__ == "__main__":
    main()