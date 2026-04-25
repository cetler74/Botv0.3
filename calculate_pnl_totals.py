#!/usr/bin/env python3
"""
PnL Totals Calculator
Calculates the sum of unrealized_pnl and realized_pnl for today and overall totals
"""

import logging
from datetime import datetime
from typing import Dict, Any, List
import psycopg2
import psycopg2.extras
import json
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class PnLCalculator:
    """Calculate PnL totals from trading database"""
    
    def __init__(self):
        self.connection = None
    
    def get_db_connection(self):
        """Get database connection"""
        try:
            if not self.connection or self.connection.closed:
                self.connection = psycopg2.connect(
                    host=os.getenv('DB_HOST', 'localhost'),
                    port=os.getenv('DB_PORT', '5432'),
                    database=os.getenv('DB_NAME', 'trading_bot_futures'),
                    user=os.getenv('DB_USER', 'carloslarramba'),
                    password=os.getenv('DB_PASSWORD', 'mypassword')
                )
            return self.connection
        except Exception as e:
            logger.error(f"❌ Database connection error: {e}")
            return None
    
    def execute_query(self, query: str) -> List[Dict[str, Any]]:
        """Execute SQL query directly"""
        try:
            connection = self.get_db_connection()
            if not connection:
                return []
                
            with connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
                cursor.execute(query)
                results = cursor.fetchall()
                return [dict(row) for row in results]
                
        except Exception as e:
            logger.error(f"❌ Error executing query: {e}")
            return []
    
    def get_today_pnl_totals(self) -> Dict[str, Any]:
        """Get today's PnL totals"""
        query = """
        SELECT 
            COUNT(*) as total_trades,
            COALESCE(SUM(unrealized_pnl), 0) as total_unrealized_pnl,
            COALESCE(SUM(realized_pnl), 0) as total_realized_pnl,
            COALESCE(SUM(unrealized_pnl + realized_pnl), 0) as total_combined_pnl
        FROM trading.trades 
        WHERE DATE(created_at) = CURRENT_DATE
        """
        
        results = self.execute_query(query)
        return results[0] if results else {
            "total_trades": 0,
            "total_unrealized_pnl": 0,
            "total_realized_pnl": 0,
            "total_combined_pnl": 0
        }
    
    def get_overall_pnl_totals(self) -> Dict[str, Any]:
        """Get overall PnL totals"""
        query = """
        SELECT 
            COUNT(*) as total_trades,
            COALESCE(SUM(unrealized_pnl), 0) as total_unrealized_pnl,
            COALESCE(SUM(realized_pnl), 0) as total_realized_pnl,
            COALESCE(SUM(unrealized_pnl + realized_pnl), 0) as total_combined_pnl
        FROM trading.trades
        """
        
        results = self.execute_query(query)
        return results[0] if results else {
            "total_trades": 0,
            "total_unrealized_pnl": 0,
            "total_realized_pnl": 0,
            "total_combined_pnl": 0
        }
    
    def get_pnl_by_status(self, period: str = "today") -> List[Dict[str, Any]]:
        """Get PnL breakdown by trade status"""
        date_filter = "WHERE DATE(created_at) = CURRENT_DATE" if period == "today" else ""
        
        query = f"""
        SELECT 
            status,
            COUNT(*) as trade_count,
            COALESCE(SUM(unrealized_pnl), 0) as total_unrealized_pnl,
            COALESCE(SUM(realized_pnl), 0) as total_realized_pnl,
            COALESCE(SUM(unrealized_pnl + realized_pnl), 0) as total_combined_pnl
        FROM trading.trades 
        {date_filter}
        GROUP BY status
        ORDER BY status
        """
        
        return self.execute_query(query)
    
    def format_currency(self, amount: float) -> str:
        """Format currency amount with proper sign and decimal places"""
        if amount == 0:
            return "$0.00"
        elif amount > 0:
            return f"${amount:,.2f}"
        else:
            return f"-${abs(amount):,.2f}"
    
    def print_summary(self, today_data: Dict[str, Any], overall_data: Dict[str, Any]):
        """Print formatted summary"""
        print("\n" + "="*80)
        print("📊 PnL TOTALS SUMMARY")
        print("="*80)
        print(f"Generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print()
        
        # Today's summary
        print("📅 TODAY'S TOTALS:")
        print(f"   Total Trades: {today_data['total_trades']}")
        print(f"   Unrealized PnL: {self.format_currency(today_data['total_unrealized_pnl'])}")
        print(f"   Realized PnL: {self.format_currency(today_data['total_realized_pnl'])}")
        print(f"   Combined PnL: {self.format_currency(today_data['total_combined_pnl'])}")
        print()
        
        # Overall summary
        print("📈 OVERALL TOTALS:")
        print(f"   Total Trades: {overall_data['total_trades']}")
        print(f"   Unrealized PnL: {self.format_currency(overall_data['total_unrealized_pnl'])}")
        print(f"   Realized PnL: {self.format_currency(overall_data['total_realized_pnl'])}")
        print(f"   Combined PnL: {self.format_currency(overall_data['total_combined_pnl'])}")
        print()
        
        # Performance indicators
        if overall_data['total_trades'] > 0:
            avg_trade_pnl = overall_data['total_combined_pnl'] / overall_data['total_trades']
            print("📊 PERFORMANCE INDICATORS:")
            print(f"   Average PnL per Trade: {self.format_currency(avg_trade_pnl)}")
            
            if overall_data['total_realized_pnl'] != 0:
                realized_ratio = (overall_data['total_realized_pnl'] / overall_data['total_combined_pnl']) * 100
                print(f"   Realized PnL Ratio: {realized_ratio:.1f}%")
        
        print("="*80)
    
    def print_status_breakdown(self, status_data: List[Dict[str, Any]], period: str):
        """Print status breakdown"""
        if not status_data:
            print(f"⚠️  No {period} data available")
            return
        
        print(f"\n📋 {period.upper()} BREAKDOWN BY STATUS:")
        print("-" * 80)
        print(f"{'Status':<15} {'Trades':<8} {'Unrealized':<12} {'Realized':<12} {'Combined':<12}")
        print("-" * 80)
        
        for row in status_data:
            status = row.get('status', 'UNKNOWN')
            trades = row.get('trade_count', 0)
            unrealized = self.format_currency(row.get('total_unrealized_pnl', 0))
            realized = self.format_currency(row.get('total_realized_pnl', 0))
            combined = self.format_currency(row.get('total_combined_pnl', 0))
            
            print(f"{status:<15} {trades:<8} {unrealized:<12} {realized:<12} {combined:<12}")
        
        print("-" * 80)
    
    def run_full_analysis(self):
        """Run complete PnL analysis"""
        logger.info("🔍 Starting PnL analysis...")
        
        try:
            # Get today's totals
            today_data = self.get_today_pnl_totals()
            logger.info("✅ Retrieved today's PnL totals")
            
            # Get overall totals
            overall_data = self.get_overall_pnl_totals()
            logger.info("✅ Retrieved overall PnL totals")
            
            # Get status breakdowns
            today_status = self.get_pnl_by_status("today")
            overall_status = self.get_pnl_by_status("overall")
            logger.info("✅ Retrieved status breakdowns")
            
            # Print results
            self.print_summary(today_data, overall_data)
            self.print_status_breakdown(today_status, "today")
            self.print_status_breakdown(overall_status, "overall")
            
            # Return structured data for programmatic use
            return {
                "today": today_data,
                "overall": overall_data,
                "today_by_status": today_status,
                "overall_by_status": overall_status
            }
            
        except Exception as e:
            logger.error(f"❌ Error in PnL analysis: {e}")
            return None
        finally:
            if self.connection and not self.connection.closed:
                self.connection.close()

def main():
    """Main function"""
    calculator = PnLCalculator()
    result = calculator.run_full_analysis()
    
    if result:
        # Optionally save to JSON file
        with open("pnl_analysis_report.json", "w") as f:
            json.dump(result, f, indent=2, default=str)
        logger.info("💾 Analysis saved to pnl_analysis_report.json")

if __name__ == "__main__":
    main()
