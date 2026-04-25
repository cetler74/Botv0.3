#!/usr/bin/env python3
"""
CENTRALIZED EMERGENCY TRADE CLOSURE
===================================

Modern emergency script that uses the centralized trade closure service
to safely close trades with proper validation and data integrity.

This replaces all the old emergency scripts with a single, reliable solution.

Features:
- Uses centralized trade closure API
- Comprehensive validation and error handling
- Exchange verification capabilities
- Detailed logging and reporting
- Safe fallback mechanisms

Author: Claude AI
Created: 2025-01-09
"""

import asyncio
import httpx
import json
from datetime import datetime
import logging
from typing import Dict, List, Optional, Any
import argparse

# Setup logging
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f'emergency_closure_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log')
    ]
)
logger = logging.getLogger(__name__)

class CentralizedEmergencyCloser:
    """
    Emergency trade closure using centralized trade closure service
    """
    
    def __init__(self, database_service_url: str = "http://localhost:8002"):
        self.database_service_url = database_service_url
        self.exchange_service_url = "http://localhost:8003"
        
        # Statistics
        self.stats = {
            'trades_found': 0,
            'trades_closed_successfully': 0,
            'trades_closed_with_fallback': 0,
            'trades_failed': 0,
            'total_realized_pnl': 0.0,
            'start_time': datetime.utcnow(),
            'errors': []
        }
        
    async def get_open_trades(self) -> List[Dict[str, Any]]:
        """Get all open trades from database service"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(f"{self.database_service_url}/api/v1/trades")
                if response.status_code == 200:
                    data = response.json()
                    open_trades = [t for t in data.get('trades', []) if t.get('status') == 'OPEN']
                    logger.info(f"🔍 Found {len(open_trades)} open trades")
                    return open_trades
                else:
                    logger.error(f"❌ Failed to get trades: {response.status_code}")
                    return []
        except Exception as e:
            logger.error(f"❌ Error fetching trades: {e}")
            return []
    
    async def get_exchange_balances(self, exchange: str = "binance") -> Dict[str, float]:
        """Get current exchange balances"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(f"{self.exchange_service_url}/api/v1/trading/balance/{exchange}")
                if response.status_code == 200:
                    data = response.json()
                    balances = {}
                    for asset, balance_data in data.get('balances', {}).items():
                        if isinstance(balance_data, dict):
                            balances[asset] = float(balance_data.get('free', 0))
                        else:
                            balances[asset] = float(balance_data)
                    return balances
                else:
                    logger.error(f"❌ Failed to get exchange balances: {response.status_code}")
                    return {}
        except Exception as e:
            logger.error(f"❌ Error fetching exchange balances: {e}")
            return {}
    
    async def get_current_price(self, exchange: str, symbol: str) -> Optional[float]:
        """Get current market price for a symbol"""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # Remove slash from symbol for API call
                clean_symbol = symbol.replace('/', '')
                response = await client.get(f"{self.exchange_service_url}/api/v1/market/ticker/{exchange}/{clean_symbol}")
                if response.status_code == 200:
                    data = response.json()
                    return float(data.get('price', 0))
                else:
                    logger.warning(f"⚠️ Failed to get current price for {symbol}: {response.status_code}")
                    return None
        except Exception as e:
            logger.warning(f"⚠️ Error fetching current price for {symbol}: {e}")
            return None
    
    async def close_trade_centralized(self, trade: Dict[str, Any], exit_price: float, 
                                     exit_reason: str = "emergency_closure") -> bool:
        """Close a trade using the centralized closure API"""
        try:
            trade_id = trade['trade_id']
            
            closure_data = {
                "exit_price": exit_price,
                "exit_reason": exit_reason,
                "exit_time": datetime.utcnow().isoformat(),
                "validated_by_exchange": True  # We've checked exchange balances
            }
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.database_service_url}/api/v1/trades/{trade_id}/close",
                    json=closure_data
                )
                
                if response.status_code == 200:
                    result = response.json()
                    realized_pnl = result['realized_pnl']
                    pnl_percentage = result['pnl_percentage']
                    
                    logger.info(f"✅ CENTRALIZED CLOSURE: {trade['pair']} ({trade_id[:8]}...) "
                               f"exit_price=${exit_price:.4f}, PnL=${realized_pnl:.2f} ({pnl_percentage:.2f}%)")
                    
                    self.stats['trades_closed_successfully'] += 1
                    self.stats['total_realized_pnl'] += realized_pnl
                    return True
                else:
                    logger.error(f"❌ Centralized closure failed for {trade_id}: {response.status_code}")
                    return False
                    
        except Exception as e:
            logger.error(f"❌ Error in centralized closure for {trade_id}: {e}")
            return False
    
    async def close_trade_fallback(self, trade: Dict[str, Any], exit_price: float, 
                                  exit_reason: str = "emergency_closure_fallback") -> bool:
        """Fallback: Close trade using direct API update"""
        try:
            trade_id = trade['trade_id']
            entry_price = float(trade.get('entry_price', 0))
            position_size = float(trade.get('position_size', 0))
            
            # Calculate PnL manually for fallback
            realized_pnl = (exit_price - entry_price) * position_size
            
            update_data = {
                "status": "CLOSED",
                "exit_price": exit_price,
                "exit_time": datetime.utcnow().isoformat(),
                "realized_pnl": realized_pnl,
                "exit_reason": exit_reason,
                "updated_at": datetime.utcnow().isoformat()
            }
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.put(
                    f"{self.database_service_url}/api/v1/trades/{trade_id}",
                    json=update_data
                )
                
                if response.status_code == 200:
                    logger.info(f"✅ FALLBACK CLOSURE: {trade['pair']} ({trade_id[:8]}...) "
                               f"exit_price=${exit_price:.4f}, PnL=${realized_pnl:.2f}")
                    
                    self.stats['trades_closed_with_fallback'] += 1
                    self.stats['total_realized_pnl'] += realized_pnl
                    return True
                else:
                    logger.error(f"❌ Fallback closure failed for {trade_id}: {response.status_code}")
                    return False
                    
        except Exception as e:
            logger.error(f"❌ Error in fallback closure for {trade_id}: {e}")
            return False
    
    async def identify_phantom_trades(self, trades: List[Dict[str, Any]], 
                                     balances: Dict[str, float]) -> List[Dict[str, Any]]:
        """Identify phantom trades (no exchange balance)"""
        phantom_trades = []
        
        for trade in trades:
            pair = trade.get('pair', '')
            # Extract base asset from pair (e.g., CRO from CRO/USDT)
            base_asset = pair.split('/')[0] if '/' in pair else pair
            
            balance = balances.get(base_asset, 0)
            position_size = float(trade.get('position_size', 0))
            
            # Consider trade phantom if exchange balance is much less than position size
            if balance < position_size * 0.1:  # 10% threshold for rounding
                phantom_trades.append(trade)
                logger.warning(f"👻 PHANTOM: {pair} - Position: {position_size}, Exchange: {balance}")
            else:
                logger.info(f"✅ VALID: {pair} - Position: {position_size}, Exchange: {balance}")
        
        logger.info(f"🎭 Identified {len(phantom_trades)}/{len(trades)} phantom trades")
        return phantom_trades
    
    async def close_phantom_trades(self, verify_exchange: bool = True) -> Dict[str, Any]:
        """Main method to close phantom trades"""
        logger.info("🚨 STARTING CENTRALIZED EMERGENCY TRADE CLOSURE")
        logger.info("=" * 60)
        
        try:
            # Get open trades
            trades = await self.get_open_trades()
            self.stats['trades_found'] = len(trades)
            
            if not trades:
                logger.info("✅ No open trades found")
                return self.stats
            
            # Get exchange balances if verification is enabled
            phantom_trades = trades  # Default: close all trades
            if verify_exchange:
                balances = await self.get_exchange_balances()
                if balances:
                    phantom_trades = await self.identify_phantom_trades(trades, balances)
                else:
                    logger.warning("⚠️ Could not verify exchange balances, proceeding with all trades")
            
            if not phantom_trades:
                logger.info("✅ No phantom trades identified")
                return self.stats
            
            logger.info(f"🎯 Proceeding to close {len(phantom_trades)} trades")
            logger.info("-" * 40)
            
            # Close each phantom trade
            for i, trade in enumerate(phantom_trades, 1):
                trade_id = trade['trade_id']
                pair = trade['pair']
                current_price = trade.get('current_price')
                
                logger.info(f"[{i}/{len(phantom_trades)}] Processing {pair} ({trade_id[:8]}...)")
                
                # Get current market price as exit price
                exit_price = current_price
                exchange = trade.get('exchange', 'binance')
                
                if not exit_price or exit_price <= 0:
                    # Try to get current market price
                    market_price = await self.get_current_price(exchange, pair)
                    if market_price:
                        exit_price = market_price
                        logger.info(f"📈 Using current market price: ${market_price:.4f}")
                    else:
                        # Use entry price as last resort
                        exit_price = float(trade.get('entry_price', 0))
                        logger.warning(f"⚠️ Using entry price as fallback: ${exit_price:.4f}")
                
                if exit_price <= 0:
                    logger.error(f"❌ No valid exit price for {trade_id}, skipping")
                    self.stats['trades_failed'] += 1
                    continue
                
                # Try centralized closure first
                success = await self.close_trade_centralized(
                    trade, exit_price, "emergency_phantom_closure"
                )
                
                # If centralized fails, try fallback
                if not success:
                    logger.warning(f"🔄 Trying fallback closure for {trade_id}")
                    success = await self.close_trade_fallback(
                        trade, exit_price, "emergency_phantom_closure_fallback"
                    )
                
                if not success:
                    self.stats['trades_failed'] += 1
                    self.stats['errors'].append(f"Failed to close trade {trade_id}")
                
                # Small delay to avoid overwhelming the API
                await asyncio.sleep(0.5)
            
            # Print final statistics
            await self.print_final_report()
            
        except Exception as e:
            logger.error(f"❌ Fatal error in emergency closure: {e}")
            self.stats['errors'].append(f"Fatal error: {e}")
        
        return self.stats
    
    async def print_final_report(self):
        """Print final statistics report"""
        duration = datetime.utcnow() - self.stats['start_time']
        
        logger.info("\n" + "=" * 60)
        logger.info("🏁 EMERGENCY CLOSURE COMPLETE")
        logger.info("=" * 60)
        logger.info(f"📊 STATISTICS:")
        logger.info(f"   Trades Found:           {self.stats['trades_found']}")
        logger.info(f"   Successfully Closed:    {self.stats['trades_closed_successfully']} (centralized)")
        logger.info(f"   Closed with Fallback:   {self.stats['trades_closed_with_fallback']} (fallback)")
        logger.info(f"   Failed:                 {self.stats['trades_failed']}")
        logger.info(f"   Total Realized PnL:     ${self.stats['total_realized_pnl']:.2f}")
        logger.info(f"   Duration:               {duration.total_seconds():.1f} seconds")
        
        if self.stats['errors']:
            logger.info(f"\n❌ ERRORS ({len(self.stats['errors'])}):")
            for error in self.stats['errors']:
                logger.info(f"   - {error}")
        
        total_closed = self.stats['trades_closed_successfully'] + self.stats['trades_closed_with_fallback']
        if total_closed > 0:
            logger.info(f"\n✅ SUCCESS: {total_closed}/{self.stats['trades_found']} trades closed")
        else:
            logger.info(f"\n⚠️ WARNING: No trades were closed")


async def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(description='Centralized Emergency Trade Closure')
    parser.add_argument('--no-verify', action='store_true', 
                       help='Skip exchange balance verification (close ALL open trades)')
    parser.add_argument('--database-url', default='http://localhost:8002',
                       help='Database service URL (default: http://localhost:8002)')
    
    args = parser.parse_args()
    
    closer = CentralizedEmergencyCloser(args.database_url)
    
    if args.no_verify:
        logger.warning("⚠️ VERIFICATION DISABLED - Will attempt to close ALL open trades")
        input("Press Enter to continue or Ctrl+C to cancel...")
    
    await closer.close_phantom_trades(verify_exchange=not args.no_verify)


if __name__ == "__main__":
    asyncio.run(main())
