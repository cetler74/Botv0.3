#!/usr/bin/env python3
"""
SIMPLE LIVE MONITORING: Track database changes to catch missed fills
Run this while trading to see discrepancies appear in real-time
"""
import requests
import time
import json
from datetime import datetime

DATABASE_URL = "http://localhost:8002"
EXCHANGE_URL = "http://localhost:8003"

class SimpleTradeMonitor:
    def __init__(self):
        self.last_trades_snapshot = {}
        self.known_exchange_balances = {}
        
    def get_current_trades(self):
        """Get current trades from database"""
        try:
            response = requests.get(f"{DATABASE_URL}/api/v1/trades")
            if response.status_code == 200:
                return response.json().get("trades", [])
            return []
        except Exception as e:
            print(f"❌ Error getting trades: {e}")
            return []
    
    def check_for_new_trades(self):
        """Check for new or changed trades"""
        current_trades = self.get_current_trades()
        
        for trade in current_trades:
            trade_id = trade["trade_id"]
            
            if trade_id not in self.last_trades_snapshot:
                # New trade
                print(f"🆕 NEW TRADE: {trade_id[:8]} - {trade['pair']} {trade['status']}")
                print(f"   Entry: ${trade.get('entry_price', 0)} × {trade.get('position_size', 0)}")
                
            else:
                # Check for changes
                old_trade = self.last_trades_snapshot[trade_id]
                if old_trade["status"] != trade["status"]:
                    print(f"🔄 STATUS CHANGE: {trade_id[:8]} - {old_trade['status']} → {trade['status']}")
                    
                    if trade["status"] == "CLOSED":
                        exit_price = trade.get('exit_price', 0)
                        pnl = trade.get('realized_pnl', 0)
                        print(f"   Exit: ${exit_price}, PnL: ${pnl}")
        
        # Update snapshot
        self.last_trades_snapshot = {t["trade_id"]: t for t in current_trades}
    
    def check_open_trades_health(self):
        """Check if open trades still make sense"""
        current_trades = self.get_current_trades()
        open_trades = [t for t in current_trades if t["status"] == "OPEN"]
        
        if not open_trades:
            print("✅ No open trades")
            return
            
        print(f"📊 Monitoring {len(open_trades)} OPEN trades:")
        
        for trade in open_trades:
            trade_id = trade["trade_id"][:8]
            pair = trade["pair"]
            position_size = trade.get("position_size", 0)
            entry_time = trade.get("entry_time", "")
            
            # Calculate age
            try:
                entry_dt = datetime.fromisoformat(entry_time.replace('Z', '+00:00'))
                age_hours = (datetime.now().replace(tzinfo=entry_dt.tzinfo) - entry_dt).total_seconds() / 3600
            except:
                age_hours = 0
                
            status_icon = "⚠️" if age_hours > 24 else "📈"
            print(f"   {status_icon} {trade_id} - {pair}: {position_size:.6f} (Age: {age_hours:.1f}h)")
    
    def simple_balance_check(self):
        """Basic balance reality check"""
        # This is where we'd check exchange balances vs database expectations
        # For now, just flag old open trades
        current_trades = self.get_current_trades()
        old_open_trades = []
        
        for trade in current_trades:
            if trade["status"] != "OPEN":
                continue
                
            try:
                entry_time = trade.get("entry_time", "")
                entry_dt = datetime.fromisoformat(entry_time.replace('Z', '+00:00'))
                age_hours = (datetime.now().replace(tzinfo=entry_dt.tzinfo) - entry_dt).total_seconds() / 3600
                
                if age_hours > 48:  # 2+ days old
                    old_open_trades.append({
                        'trade_id': trade["trade_id"][:8],
                        'pair': trade["pair"],
                        'age_hours': age_hours,
                        'position_size': trade.get("position_size", 0)
                    })
            except:
                continue
        
        if old_open_trades:
            print(f"🚨 {len(old_open_trades)} SUSPICIOUS OLD TRADES:")
            for old_trade in old_open_trades:
                print(f"   - {old_trade['trade_id']} {old_trade['pair']}: {old_trade['age_hours']:.1f}h old")

def main():
    """Main monitoring loop"""
    print("🔍 SIMPLE TRADE MONITORING STARTED")
    print("Watching for trade changes and suspicious patterns...")
    print("Press Ctrl+C to stop")
    print("-" * 60)
    
    monitor = SimpleTradeMonitor()
    
    try:
        while True:
            print(f"\n⏰ {datetime.now().strftime('%H:%M:%S')} - Checking trades...")
            
            # Check for new/changed trades
            monitor.check_for_new_trades()
            
            # Check health of open trades
            monitor.check_open_trades_health()
            
            # Basic balance sanity check
            monitor.simple_balance_check()
            
            print("-" * 40)
            
            # Wait 30 seconds before next check
            time.sleep(30)
            
    except KeyboardInterrupt:
        print("\n🛑 Monitoring stopped")
    except Exception as e:
        print(f"❌ Error in monitoring: {e}")

if __name__ == "__main__":
    main()