#!/usr/bin/env python3
"""
Comprehensive System Validation Report

This generates a complete report on the trading system's data integrity and health,
specifically validating all the critical issues that were causing production failures.
"""

import asyncio
import httpx
import json
from datetime import datetime
import subprocess

class SystemValidationReport:
    def __init__(self):
        self.orchestrator_url = "http://localhost:8005"
        self.database_url = "http://localhost:8002" 
        self.exchange_url = "http://localhost:8003"
        
    async def generate_comprehensive_report(self):
        """Generate comprehensive validation report"""
        print("📋 COMPREHENSIVE SYSTEM VALIDATION REPORT")
        print("=" * 60)
        print(f"Generated: {datetime.utcnow().isoformat()}")
        print("")
        
        report = {
            "timestamp": datetime.utcnow().isoformat(),
            "system_health": {},
            "data_integrity": {},
            "critical_issues_resolved": {},
            "recommendations": []
        }
        
        try:
            # 1. System Health Check
            report["system_health"] = await self.check_system_health()
            
            # 2. Data Integrity Validation
            report["data_integrity"] = await self.validate_data_integrity()
            
            # 3. Critical Issues Validation
            report["critical_issues_resolved"] = await self.validate_critical_fixes()
            
            # 4. Generate recommendations
            report["recommendations"] = self.generate_recommendations(report)
            
            # Print summary
            self.print_report_summary(report)
            
            return report
            
        except Exception as e:
            print(f"❌ VALIDATION REPORT FAILED: {e}")
            raise
    
    async def check_system_health(self):
        """Check overall system health"""
        print("🔍 SYSTEM HEALTH CHECK")
        print("-" * 30)
        
        health = {}
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Check orchestrator status
            try:
                response = await client.get(f"{self.orchestrator_url}/api/v1/trading/status")
                if response.status_code == 200:
                    status_data = response.json()
                    health["orchestrator"] = {
                        "status": "healthy",
                        "running": status_data.get("status") == "running",
                        "cycle_count": status_data.get("cycle_count", 0),
                        "active_trades": status_data.get("active_trades", 0)
                    }
                    print(f"✅ Orchestrator: {status_data.get('status')} ({status_data.get('cycle_count', 0)} cycles)")
                else:
                    health["orchestrator"] = {"status": "unhealthy", "error": response.status_code}
                    print(f"❌ Orchestrator: HTTP {response.status_code}")
            except Exception as e:
                health["orchestrator"] = {"status": "error", "error": str(e)}
                print(f"❌ Orchestrator: {e}")
            
            # Check database service
            try:
                response = await client.get(f"{self.database_url}/health")
                health["database"] = {"status": "healthy" if response.status_code == 200 else "unhealthy"}
                print(f"✅ Database Service: HTTP {response.status_code}")
            except Exception as e:
                health["database"] = {"status": "error", "error": str(e)}
                print(f"❌ Database Service: {e}")
            
            # Check exchange service
            try:
                response = await client.get(f"{self.exchange_url}/health")
                if response.status_code == 200:
                    health_data = response.json()
                    health["exchange"] = {
                        "status": "healthy",
                        "exchanges_connected": health_data.get("exchanges_connected", 0),
                        "total_exchanges": health_data.get("total_exchanges", 0)
                    }
                    print(f"✅ Exchange Service: {health_data.get('exchanges_connected', 0)}/{health_data.get('total_exchanges', 0)} exchanges")
                else:
                    health["exchange"] = {"status": "unhealthy", "error": response.status_code}
                    print(f"❌ Exchange Service: HTTP {response.status_code}")
            except Exception as e:
                health["exchange"] = {"status": "error", "error": str(e)}
                print(f"❌ Exchange Service: {e}")
        
        return health
    
    async def validate_data_integrity(self):
        """Validate critical data integrity"""
        print(f"\n🔍 DATA INTEGRITY VALIDATION")
        print("-" * 30)
        
        integrity = {}
        
        # Check database trade counts and status distribution
        result = subprocess.run([
            'docker', 'exec', 'trading-bot-postgres', 'psql', '-U', 'carloslarramba',
            '-d', 'trading_bot_futures', '-t', '-c',
            """SELECT 
                COUNT(*) as total_trades,
                COUNT(CASE WHEN status = 'OPEN' THEN 1 END) as open_trades,
                COUNT(CASE WHEN status = 'CLOSED' THEN 1 END) as closed_trades,
                COUNT(CASE WHEN status = 'PENDING' THEN 1 END) as pending_trades,
                COUNT(CASE WHEN entry_price IS NULL OR entry_price = 0 THEN 1 END) as missing_entry_price,
                COUNT(CASE WHEN status = 'OPEN' AND position_size IS NULL OR position_size = 0 THEN 1 END) as missing_position_size
               FROM trading.trades;"""
        ], capture_output=True, text=True)
        
        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')
            data_line = None
            for line in lines:
                if '|' in line and not line.strip().startswith('-') and 'total_trades' not in line:
                    data_line = line.strip()
                    break
            
            if data_line:
                parts = [p.strip() for p in data_line.split('|')]
                if len(parts) >= 6:
                    integrity["database"] = {
                        "total_trades": int(parts[0]),
                        "open_trades": int(parts[1]),
                        "closed_trades": int(parts[2]),
                        "pending_trades": int(parts[3]),  # THIS IS THE CRITICAL METRIC
                        "missing_entry_price": int(parts[4]),
                        "missing_position_size": int(parts[5])
                    }
                    
                    # Print validation results
                    print(f"Total trades: {parts[0]}")
                    print(f"OPEN trades: {parts[1]}")
                    print(f"CLOSED trades: {parts[2]}")
                    print(f"⚠️ PENDING trades: {parts[3]} (CRITICAL: Should be 0)")
                    print(f"Missing entry prices: {parts[4]}")
                    print(f"Missing position sizes: {parts[5]}")
                    
                    # Critical validation
                    pending_count = int(parts[3])
                    if pending_count == 0:
                        print("✅ CRITICAL SUCCESS: No PENDING trades stuck in database")
                        integrity["pending_trades_issue"] = "resolved"
                    else:
                        print(f"❌ CRITICAL ISSUE: {pending_count} trades stuck in PENDING status")
                        integrity["pending_trades_issue"] = "active"
        
        # Check for data consistency
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(f"{self.database_url}/api/v1/trades")
                if response.status_code == 200:
                    response_data = response.json()
                    all_trades = response_data.get("trades", response_data) if isinstance(response_data, dict) else response_data
                    
                    open_trades = [t for t in all_trades if t.get("status") == "OPEN"]
                    
                    # Check OPEN trades for required fields
                    missing_fields = 0
                    for trade in open_trades:
                        if not all([trade.get("entry_price"), trade.get("position_size"), trade.get("entry_id")]):
                            missing_fields += 1
                    
                    integrity["open_trade_data_quality"] = {
                        "total_open": len(open_trades),
                        "missing_required_fields": missing_fields,
                        "data_completeness": f"{((len(open_trades) - missing_fields) / len(open_trades) * 100):.1f}%" if open_trades else "N/A"
                    }
                    
                    if missing_fields == 0:
                        print(f"✅ All {len(open_trades)} OPEN trades have complete data")
                    else:
                        print(f"❌ {missing_fields}/{len(open_trades)} OPEN trades missing required fields")
                        
            except Exception as e:
                print(f"❌ Error validating trade data: {e}")
        
        return integrity
    
    async def validate_critical_fixes(self):
        """Validate that critical production issues are resolved"""
        print(f"\n🔍 CRITICAL ISSUES VALIDATION")
        print("-" * 30)
        
        fixes = {}
        
        # 1. PENDING trades mass recovery validation
        result = subprocess.run([
            'docker', 'exec', 'trading-bot-postgres', 'psql', '-U', 'carloslarramba',
            '-d', 'trading_bot_futures', '-t', '-c',
            "SELECT COUNT(*) FROM trading.trades WHERE status = 'PENDING';"
        ], capture_output=True, text=True)
        
        if result.returncode == 0:
            pending_count = 0
            for line in result.stdout.strip().split('\n'):
                if line.strip().isdigit():
                    pending_count = int(line.strip())
                    break
            
            fixes["mass_recovery_success"] = pending_count == 0
            if pending_count == 0:
                print("✅ Mass recovery complete: 0 PENDING trades")
            else:
                print(f"❌ Mass recovery incomplete: {pending_count} PENDING trades remain")
        
        # 2. Automatic recovery worker validation
        try:
            result = subprocess.run([
                'docker-compose', 'logs', 'fill-detection-service', '--tail=10'
            ], capture_output=True, text=True, cwd="/Volumes/OWC Volume/Projects2025/Botv0.3")
            
            logs = result.stdout
            recovery_worker_running = "Pending trade recovery worker started" in logs
            fixes["automatic_recovery_worker"] = recovery_worker_running
            
            if recovery_worker_running:
                print("✅ Automatic recovery worker is running")
            else:
                print("❌ Automatic recovery worker not detected")
                
        except Exception as e:
            print(f"⚠️ Could not check recovery worker: {e}")
            fixes["automatic_recovery_worker"] = "unknown"
        
        # 3. Fill detection service validation
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                response = await client.get("http://localhost:8008/health")
                fills_service_healthy = response.status_code == 200
                fixes["fill_detection_service"] = fills_service_healthy
                
                if fills_service_healthy:
                    print("✅ Fill detection service is healthy")
                else:
                    print(f"❌ Fill detection service unhealthy: {response.status_code}")
                    
            except Exception as e:
                print(f"❌ Fill detection service error: {e}")
                fixes["fill_detection_service"] = False
        
        return fixes
    
    def generate_recommendations(self, report):
        """Generate recommendations based on report findings"""
        recommendations = []
        
        # Check system health
        if report["system_health"].get("orchestrator", {}).get("status") != "healthy":
            recommendations.append("CRITICAL: Fix orchestrator service connectivity")
        
        # Check data integrity
        if report["data_integrity"].get("database", {}).get("pending_trades", 0) > 0:
            recommendations.append("URGENT: Investigate remaining PENDING trades")
        
        if report["data_integrity"].get("open_trade_data_quality", {}).get("missing_required_fields", 0) > 0:
            recommendations.append("HIGH: Fix OPEN trades with missing required fields")
        
        # Check critical fixes
        if not report["critical_issues_resolved"].get("automatic_recovery_worker", False):
            recommendations.append("HIGH: Ensure automatic recovery worker is deployed and running")
        
        if not report["critical_issues_resolved"].get("fill_detection_service", False):
            recommendations.append("CRITICAL: Fix fill detection service")
        
        # Success recommendations
        if (report["data_integrity"].get("database", {}).get("pending_trades", 0) == 0 and
            report["critical_issues_resolved"].get("mass_recovery_success", False)):
            recommendations.append("SUCCESS: Mass recovery completed - maintain monitoring")
            recommendations.append("SUCCESS: Implement regular validation tests to prevent regression")
        
        return recommendations
    
    def print_report_summary(self, report):
        """Print executive summary of the report"""
        print(f"\n📊 EXECUTIVE SUMMARY")
        print("=" * 40)
        
        # Overall system status
        orchestrator_healthy = report["system_health"].get("orchestrator", {}).get("status") == "healthy"
        database_healthy = report["system_health"].get("database", {}).get("status") == "healthy"
        exchange_healthy = report["system_health"].get("exchange", {}).get("status") == "healthy"
        
        services_healthy = sum([orchestrator_healthy, database_healthy, exchange_healthy])
        print(f"Services Health: {services_healthy}/3 services healthy")
        
        # Data integrity status
        pending_trades = report["data_integrity"].get("database", {}).get("pending_trades", "unknown")
        total_trades = report["data_integrity"].get("database", {}).get("total_trades", "unknown")
        
        print(f"Trade Data: {total_trades} total trades, {pending_trades} PENDING")
        
        # Critical issues status
        mass_recovery = "✅" if report["critical_issues_resolved"].get("mass_recovery_success") else "❌"
        auto_recovery = "✅" if report["critical_issues_resolved"].get("automatic_recovery_worker") else "❌"
        fill_service = "✅" if report["critical_issues_resolved"].get("fill_detection_service") else "❌"
        
        print(f"Critical Fixes: {mass_recovery} Mass Recovery | {auto_recovery} Auto Recovery | {fill_service} Fill Service")
        
        # Overall assessment
        critical_issues_resolved = (
            report["critical_issues_resolved"].get("mass_recovery_success", False) and
            report["critical_issues_resolved"].get("automatic_recovery_worker", False) and
            report["critical_issues_resolved"].get("fill_detection_service", False)
        )
        
        if critical_issues_resolved and pending_trades == 0:
            print("\n🎉 OVERALL STATUS: EXCELLENT")
            print("All critical production issues have been resolved successfully")
        elif pending_trades == 0:
            print("\n✅ OVERALL STATUS: GOOD")
            print("Main data integrity issues resolved, minor services need attention")
        else:
            print("\n⚠️ OVERALL STATUS: NEEDS ATTENTION")
            print("Critical issues remain that need immediate resolution")
        
        # Recommendations
        if report["recommendations"]:
            print(f"\n📋 TOP RECOMMENDATIONS:")
            for i, rec in enumerate(report["recommendations"][:3], 1):
                print(f"{i}. {rec}")

async def main():
    """Generate comprehensive validation report"""
    validator = SystemValidationReport()
    await validator.generate_comprehensive_report()

if __name__ == "__main__":
    asyncio.run(main())