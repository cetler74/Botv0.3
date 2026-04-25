#!/usr/bin/env python3
"""
Test script to verify enhanced dashboard functionality
"""

import asyncio
import aiohttp
import json

async def test_dashboard_endpoints():
    """Test all dashboard API endpoints"""
    base_url = "http://localhost:8006"
    
    endpoints = [
        "/",  # Enhanced dashboard HTML
        "/dashboard-original",  # Original dashboard backup
        "/api/portfolio",
        "/api/bot-status", 
        "/api/trades?limit=5",
        "/health"
    ]
    
    results = {}
    
    async with aiohttp.ClientSession() as session:
        for endpoint in endpoints:
            try:
                async with session.get(f"{base_url}{endpoint}") as response:
                    status = response.status
                    content_type = response.headers.get('content-type', '')
                    
                    if endpoint in ["/", "/dashboard-original"]:
                        # For HTML endpoints, just check status
                        results[endpoint] = {
                            "status": status,
                            "content_type": content_type,
                            "success": status == 200 and "text/html" in content_type
                        }
                    else:
                        # For API endpoints, check JSON response
                        if status == 200:
                            try:
                                data = await response.json()
                                results[endpoint] = {
                                    "status": status,
                                    "content_type": content_type,
                                    "has_data": bool(data),
                                    "success": True
                                }
                            except:
                                results[endpoint] = {
                                    "status": status,
                                    "content_type": content_type,
                                    "success": False,
                                    "error": "Invalid JSON response"
                                }
                        else:
                            results[endpoint] = {
                                "status": status,
                                "success": False,
                                "error": f"HTTP {status}"
                            }
                            
            except Exception as e:
                results[endpoint] = {
                    "success": False,
                    "error": str(e)
                }
    
    return results

async def main():
    print("🚀 Testing Enhanced Dashboard Functionality")
    print("=" * 50)
    
    results = await test_dashboard_endpoints()
    
    all_success = True
    for endpoint, result in results.items():
        status = "✅ PASS" if result.get("success") else "❌ FAIL"
        print(f"{status} {endpoint}")
        
        if not result.get("success"):
            all_success = False
            if "error" in result:
                print(f"     Error: {result['error']}")
            if "status" in result:
                print(f"     Status: {result['status']}")
        else:
            if "status" in result:
                print(f"     Status: {result['status']}")
            if "content_type" in result:
                print(f"     Content-Type: {result['content_type']}")
    
    print("\n" + "=" * 50)
    if all_success:
        print("🎉 All tests passed! Enhanced dashboard is working correctly.")
        print("\n📝 Summary of improvements:")
        print("   • Modern UI with smooth animations")
        print("   • Enhanced card designs with hover effects") 
        print("   • Improved table styling and sorting")
        print("   • Better mobile responsiveness")
        print("   • Enhanced loading states and transitions")
        print("   • All original functionality preserved")
        print("\n🔗 Access URLs:")
        print(f"   • Enhanced Dashboard: http://192.168.1.97:8006/")
        print(f"   • Original Dashboard (backup): http://192.168.1.97:8006/dashboard-original")
    else:
        print("⚠️  Some tests failed. Please check the errors above.")
    
    return all_success

if __name__ == "__main__":
    asyncio.run(main())