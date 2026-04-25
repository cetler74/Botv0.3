#!/usr/bin/env python3
"""
EMERGENCY RECOVERY: Missing filled orders from last night
Critical data integrity recovery for orders that were filled but never recorded
"""

import asyncio
import httpx
import json
import uuid
from datetime import datetime, timezone

DATABASE_SERVICE_URL = "http://localhost:8002"
EXCHANGE_SERVICE_URL = "http://localhost:8003"

# Missing orders identified from logs
MISSING_ORDERS = {
    "binance": [
        {"order_id": "141242031", "symbol": "NEO/USDC"},
        {"order_id": "141243277", "symbol": "NEO/USDC"},
        {"order_id": "141243829", "symbol": "NEO/USDC"}, 
        {"order_id": "141247802", "symbol": "NEO/USDC"},
        {"order_id": "141252234", "symbol": "NEO/USDC"},
        {"order_id": "496762342", "symbol": "LINK/USDC"},
    ],
    "cryptocom": [
        {"order_id": "6530219584096551927", "symbol": "1INCH/USD"},
        {"order_id": "6530219584096611243", "symbol": "1INCH/USD"},
        {"order_id": "6530219584096681743", "symbol": "1INCH/USD"},
        {"order_id": "6530219584096754348", "symbol": "1INCH/USD"},
        {"order_id": "6530219584096889061", "symbol": "1INCH/USD"},
        {"order_id": "6530219584096941072", "symbol": "1INCH/USD"},
        {"order_id": "6530219584097005764", "symbol": "1INCH/USD"},
        {"order_id": "6530219584097070408", "symbol": "1INCH/USD"},
        {"order_id": "6530219584097125730", "symbol": "1INCH/USD"},
        {"order_id": "6530219584097188967", "symbol": "1INCH/USD"},
    ]
}

class EmergencyRecovery:
    def __init__(self):
        self.recovered_orders = []
        self.recovered_trades = []
        self.failures = []
        
    async def get_order_from_exchange(self, exchange: str, order_id: str, symbol: str):
        """Get order data from exchange using FIXED API endpoints"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Use the CORRECTED API endpoint
                if exchange == "binance":
                    response = await client.get(f"{EXCHANGE_SERVICE_URL}/api/v1/trading/orders/history/{exchange}?symbol={symbol}")
                elif exchange == "cryptocom":
                    # Crypto.com doesn't have working history endpoints, try order lookup
                    response = await client.get(f"{EXCHANGE_SERVICE_URL}/api/v1/trading/order/{exchange}/{order_id}")
                else:
                    return None
                    
                if response.status_code == 200:
                    if exchange == "binance":
                        orders = response.json().get('orders', [])
                        for order in orders:
                            if order.get('id') == order_id:
                                return order
                    elif exchange == "cryptocom":
                        order_data = response.json().get('order', {})
                        if order_data.get('id') == order_id:
                            return order_data
                            
                return None
                
        except Exception as e:
            print(f"❌ Error getting order {order_id} from {exchange}: {e}")
            return None
    
    async def create_order_record(self, exchange: str, order_id: str, exchange_order: dict):
        """Create missing order record in database"""
        try:
            # Extract order data
            symbol = exchange_order.get('symbol', '').replace('/', '')
            side = exchange_order.get('side', '').lower()
            amount = float(exchange_order.get('amount', 0))
            price = float(exchange_order.get('price', 0))
            filled_amount = float(exchange_order.get('filled', 0))
            filled_price = float(exchange_order.get('average', 0))
            
            # Convert timestamps
            timestamp_ms = exchange_order.get('timestamp', 0)
            fill_timestamp_ms = exchange_order.get('lastTradeTimestamp', timestamp_ms)
            
            created_at = datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc).isoformat()
            filled_at = datetime.fromtimestamp(fill_timestamp_ms / 1000, timezone.utc).isoformat()
            
            order_data = {
                "order_id": order_id,
                "trade_id": None,
                "exchange": exchange,
                "symbol": symbol,
                "order_type": exchange_order.get('type', 'limit'),
                "side": side,
                "amount": amount,
                "price": price,
                "filled_amount": filled_amount,
                "filled_price": filled_price,
                "status": "FILLED",
                "fees": exchange_order.get('fee', {}).get('cost', 0),
                "created_at": created_at,
                "filled_at": filled_at,
                "exchange_order_id": order_id,
                "client_order_id": exchange_order.get('clientOrderId', '')
            }
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(f"{DATABASE_SERVICE_URL}/api/v1/orders", json=order_data)
                if response.status_code == 200:
                    print(f"✅ Created order record: {order_id} ({symbol})")
                    return True
                else:
                    print(f"❌ Failed to create order {order_id}: {response.status_code}")
                    return False
                    
        except Exception as e:
            print(f"❌ Error creating order record {order_id}: {e}")
            return False
    
    async def create_trade_record(self, exchange: str, order_id: str, exchange_order: dict):
        """Create missing trade record for filled order"""
        try:
            # Generate trade ID
            trade_id = str(uuid.uuid4())
            
            # Extract trade data
            symbol = exchange_order.get('symbol', '')
            amount = float(exchange_order.get('filled', 0))
            price = float(exchange_order.get('average', 0))
            value = amount * price
            
            # Convert timestamps
            fill_timestamp_ms = exchange_order.get('lastTradeTimestamp', 0)
            filled_at = datetime.fromtimestamp(fill_timestamp_ms / 1000, timezone.utc).isoformat()
            
            trade_data = {
                "trade_id": trade_id,
                "pair": symbol,
                "entry_price": price,
                "status": "OPEN",
                "entry_id": order_id,
                "entry_time": filled_at,
                "exchange": exchange,
                "entry_reason": "emergency_recovery_missing_order",
                "position_size": amount,
                "strategy": "recovery_emergency",
                "notional_value": value,
                "current_price": price
            }
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(f"{DATABASE_SERVICE_URL}/api/v1/trades", json=trade_data)
                if response.status_code == 200:
                    print(f"✅ Created trade record: {trade_id} ({symbol}) - ${value:.2f}")
                    return True
                else:
                    print(f"❌ Failed to create trade {trade_id}: {response.status_code}")
                    return False
                    
        except Exception as e:
            print(f"❌ Error creating trade record for {order_id}: {e}")
            return False
    
    async def recover_missing_order(self, exchange: str, order_info: dict):
        """Recover a single missing order"""
        order_id = order_info["order_id"]
        symbol = order_info["symbol"]
        
        print(f"\n🔍 Recovering {exchange} order {order_id} ({symbol})")
        
        # Get order from exchange
        exchange_order = await self.get_order_from_exchange(exchange, order_id, symbol)
        if not exchange_order:
            print(f"❌ Order {order_id} not found on {exchange}")
            self.failures.append({"order_id": order_id, "reason": "not_found_on_exchange"})
            return False
        
        status = exchange_order.get('status', '').lower()
        filled_amount = float(exchange_order.get('filled', 0))
        
        if status not in ['closed', 'filled'] or filled_amount <= 0:
            print(f"❌ Order {order_id} not filled ({status}, filled: {filled_amount})")
            self.failures.append({"order_id": order_id, "reason": "not_filled"})
            return False
        
        print(f"   ✅ Found filled order: {filled_amount} at ${exchange_order.get('average', 0)}")
        
        # Create order record
        order_created = await self.create_order_record(exchange, order_id, exchange_order)
        if not order_created:
            self.failures.append({"order_id": order_id, "reason": "order_creation_failed"})
            return False
            
        # Create trade record
        trade_created = await self.create_trade_record(exchange, order_id, exchange_order)
        if not trade_created:
            self.failures.append({"order_id": order_id, "reason": "trade_creation_failed"})
            return False
        
        self.recovered_orders.append(order_id)
        return True
    
    async def run_emergency_recovery(self):
        """Run emergency recovery for all missing orders"""
        print("🚨 EMERGENCY RECOVERY: Missing filled orders from last night")
        print("=" * 60)
        
        total_recovered = 0
        total_failed = 0
        
        for exchange, orders in MISSING_ORDERS.items():
            print(f"\n📊 Processing {len(orders)} missing {exchange} orders...")
            
            for order_info in orders:
                success = await self.recover_missing_order(exchange, order_info)
                if success:
                    total_recovered += 1
                else:
                    total_failed += 1
        
        print(f"\n📊 RECOVERY SUMMARY:")
        print(f"   ✅ Recovered: {total_recovered}")
        print(f"   ❌ Failed: {total_failed}")
        print(f"   💰 Check current OPEN trades count in database")
        
        return {
            "recovered": total_recovered,
            "failed": total_failed,
            "recovered_orders": self.recovered_orders,
            "failures": self.failures
        }

async def main():
    recovery = EmergencyRecovery()
    result = await recovery.run_emergency_recovery()
    
    # Save results
    with open('emergency_recovery_results.json', 'w') as f:
        json.dump(result, f, indent=2)
    
    print(f"\n💾 Results saved to emergency_recovery_results.json")

if __name__ == "__main__":
    asyncio.run(main())