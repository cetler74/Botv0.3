#!/usr/bin/env python3
"""
CRITICAL MONITORING: Continuous check for missed fill detection
This should run every 5 minutes to catch missed trades early
"""
import requests
import json
import time
from datetime import datetime, timezone
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATABASE_URL = "http://localhost:8002"
EXCHANGE_URL = "http://localhost:8003"

def check_for_missed_trades():
    """Check for trades that might have been missed by fill-detection"""
    try:
        # Get open trades
        response = requests.get(f"{DATABASE_URL}/api/v1/trades?status=OPEN")
        if response.status_code != 200:
            logger.error(f"Failed to get trades: {response.status_code}")
            return
            
        open_trades = response.json().get("trades", [])
        if not open_trades:
            logger.info("✅ No open trades - all good")
            return
            
        logger.info(f"📊 Monitoring {len(open_trades)} open trades")
        
        # For each trade, check if the position still exists on exchange
        for trade in open_trades:
            trade_id = trade["trade_id"][:8]
            exchange = trade["exchange"]
            pair = trade["pair"]
            position_size = float(trade.get("position_size", 0))
            entry_time = trade.get("entry_time", "")
            
            # Extract base currency
            if "/" not in pair:
                continue
                
            base_currency = pair.split("/")[0]
            
            # Check exchange balance (if balance endpoints work)
            # For now, flag old trades for manual review
            entry_dt = datetime.fromisoformat(entry_time.replace('Z', '+00:00'))
            hours_old = (datetime.now(timezone.utc) - entry_dt).total_seconds() / 3600
            
            if hours_old > 24:  # Trades older than 24 hours need scrutiny
                logger.warning(f"🚨 OLD TRADE ALERT: {trade_id} {pair} - {hours_old:.1f}h old, position: {position_size:.6f}")
                
            if hours_old > 72:  # Trades older than 3 days are highly suspicious
                logger.error(f"🚨🚨 CRITICAL: {trade_id} {pair} - {hours_old:.1f}h old! Likely missed fill!")
                
    except Exception as e:
        logger.error(f"❌ Error in monitoring: {e}")

def monitor_fill_detection_health():
    """Check if fill-detection service is working properly"""
    try:
        response = requests.get("http://localhost:8008/health")
        if response.status_code == 200:
            logger.info("✅ Fill-detection service is healthy")
        else:
            logger.error(f"❌ Fill-detection service unhealthy: {response.status_code}")
    except Exception as e:
        logger.error(f"❌ Fill-detection service unreachable: {e}")

def main():
    """Main monitoring loop - run every 5 minutes"""
    logger.info("🚨 CRITICAL MONITORING STARTED")
    
    while True:
        try:
            logger.info(f"🔍 Running critical trade monitoring...")
            
            # Check for missed trades
            check_for_missed_trades()
            
            # Check fill-detection health
            monitor_fill_detection_health()
            
            logger.info("✅ Monitoring cycle complete")
            
            # Sleep for 5 minutes
            time.sleep(300)
            
        except KeyboardInterrupt:
            logger.info("🛑 Monitoring stopped by user")
            break
        except Exception as e:
            logger.error(f"❌ Monitoring error: {e}")
            time.sleep(60)  # Wait 1 minute before retrying

if __name__ == "__main__":
    main()