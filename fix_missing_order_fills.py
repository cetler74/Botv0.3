#!/usr/bin/env python3
"""
Recovery script for missing order fills
Identifies orders that are PENDING in database but FILLED on exchange
and updates the database with correct fill information
"""

import asyncio
import httpx
import json
from typing import List, Dict, Any, Optional
from datetime import datetime

DATABASE_SERVICE_URL = "http://localhost:8002"
EXCHANGE_SERVICE_URL = "http://localhost:8003"

class OrderFillRecovery:
    def __init__(self):
        self.recovered_orders = []
        self.failed_recoveries = []
    
    async def get_pending_orders(self, exchange: str) -> List[Dict[str, Any]]:
        """Get all PENDING orders from database for specific exchange"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{DATABASE_SERVICE_URL}/api/v1/orders?status=PENDING")
            if response.status_code == 200:
                orders = response.json().get('orders', [])
                return [order for order in orders if order.get('exchange') == exchange]
            return []
    
    async def check_order_on_exchange(self, exchange: str, order_id: str, symbol: str) -> Optional[Dict[str, Any]]:
        """Check if order exists and is filled on exchange"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Try order history first
                response = await client.get(f"{EXCHANGE_SERVICE_URL}/api/v1/trading/orders/history/{exchange}?symbol={symbol}")
                if response.status_code == 200:
                    orders = response.json().get('orders', [])
                    for order in orders:
                        if order.get('id') == order_id:
                            return order
                
                # Try specific order lookup
                response = await client.get(f"{EXCHANGE_SERVICE_URL}/api/v1/trading/order/{exchange}/{order_id}?symbol={symbol}")
                if response.status_code == 200:
                    order = response.json().get('order', {})
                    if order.get('id') == order_id:
                        return order
                        
                return None
        except Exception as e:
            print(f"Error checking order {order_id} on {exchange}: {e}")
            return None
    
    async def update_order_in_database(self, order_id: str, exchange_order: Dict[str, Any]) -> bool:
        """Update order in database with fill information"""
        try:
            # Extract fill information
            status = exchange_order.get('status', '').lower()
            filled_amount = float(exchange_order.get('filled', 0))
            filled_price = float(exchange_order.get('average', 0)) if exchange_order.get('average') else None
            
            # Map exchange status to internal status
            status_mapping = {
                'closed': 'FILLED',
                'filled': 'FILLED', 
                'canceled': 'CANCELLED',
                'cancelled': 'CANCELLED',
                'expired': 'CANCELLED'
            }
            
            new_status = status_mapping.get(status, 'PENDING')
            
            if new_status == 'FILLED' and filled_amount > 0:
                update_data = {
                    'status': new_status,
                    'filled_amount': filled_amount,
                    'filled_price': filled_price,
                    'updated_at': datetime.utcnow().isoformat() + 'Z',
                    'filled_at': datetime.utcnow().isoformat() + 'Z'
                }
                
                # Add fee information if available
                if 'fee' in exchange_order and exchange_order['fee']:
                    fee_info = exchange_order['fee']
                    if isinstance(fee_info, dict):
                        update_data['fees'] = fee_info.get('cost', 0)
                        update_data['fee_rate'] = fee_info.get('rate', 0)
                
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.put(
                        f"{DATABASE_SERVICE_URL}/api/v1/orders/{order_id}",
                        json=update_data
                    )
                    
                    if response.status_code == 200:
                        print(f"✅ Successfully updated order {order_id}: {filled_amount} at ${filled_price}")
                        return True
                    else:
                        print(f"❌ Failed to update order {order_id}: {response.status_code}")
                        return False
            else:
                print(f"⚠️ Order {order_id} not filled or no fill data available")
                return False
                
        except Exception as e:
            print(f"❌ Error updating order {order_id}: {e}")
            return False
    
    async def recover_exchange_orders(self, exchange: str) -> Dict[str, Any]:
        """Recover all missing fills for specific exchange"""
        print(f"\n🔍 Checking {exchange} for missing order fills...")
        
        pending_orders = await self.get_pending_orders(exchange)
        print(f"Found {len(pending_orders)} PENDING orders in database")
        
        recovered_count = 0
        failed_count = 0
        
        for db_order in pending_orders:
            order_id = db_order.get('exchange_order_id') or db_order.get('order_id')
            symbol = db_order.get('symbol')
            created_at = db_order.get('created_at')
            
            print(f"\n📋 Checking order {order_id} ({symbol}) created at {created_at}")
            
            # Check order on exchange
            exchange_order = await self.check_order_on_exchange(exchange, order_id, symbol)
            
            if exchange_order:
                exchange_status = exchange_order.get('status', '').lower()
                filled_amount = float(exchange_order.get('filled', 0))
                
                print(f"   Exchange status: {exchange_status}, filled: {filled_amount}")
                
                if exchange_status in ['filled', 'closed'] and filled_amount > 0:
                    # Order is filled on exchange but pending in database
                    success = await self.update_order_in_database(order_id, exchange_order)
                    if success:
                        recovered_count += 1
                        self.recovered_orders.append({
                            'order_id': order_id,
                            'symbol': symbol,
                            'filled_amount': filled_amount,
                            'filled_price': exchange_order.get('average'),
                            'exchange_status': exchange_status
                        })
                    else:
                        failed_count += 1
                        self.failed_recoveries.append(order_id)
                else:
                    print(f"   Order {order_id} not filled on exchange")
            else:
                print(f"   ⚠️ Order {order_id} not found on {exchange}")
        
        return {
            'exchange': exchange,
            'pending_orders_checked': len(pending_orders),
            'orders_recovered': recovered_count,
            'recovery_failures': failed_count
        }
    
    async def run_recovery(self) -> Dict[str, Any]:
        """Run recovery for all exchanges"""
        print("🚀 Starting order fill recovery process...")
        
        exchanges = ['binance', 'cryptocom', 'bybit']
        results = {}
        
        for exchange in exchanges:
            try:
                result = await self.recover_exchange_orders(exchange)
                results[exchange] = result
            except Exception as e:
                print(f"❌ Error recovering {exchange} orders: {e}")
                results[exchange] = {'error': str(e)}
        
        # Summary
        total_recovered = sum(r.get('orders_recovered', 0) for r in results.values() if 'error' not in r)
        total_failed = sum(r.get('recovery_failures', 0) for r in results.values() if 'error' not in r)
        
        summary = {
            'timestamp': datetime.utcnow().isoformat(),
            'total_orders_recovered': total_recovered,
            'total_recovery_failures': total_failed,
            'exchange_results': results,
            'recovered_orders': self.recovered_orders,
            'failed_order_ids': self.failed_recoveries
        }
        
        print(f"\n📊 RECOVERY SUMMARY:")
        print(f"   Total orders recovered: {total_recovered}")
        print(f"   Total recovery failures: {total_failed}")
        
        return summary

async def main():
    recovery = OrderFillRecovery()
    summary = await recovery.run_recovery()
    
    # Save results to file
    with open('order_recovery_results.json', 'w') as f:
        json.dump(summary, f, indent=2)
    
    print(f"\n💾 Recovery results saved to 'order_recovery_results.json'")

if __name__ == "__main__":
    asyncio.run(main())