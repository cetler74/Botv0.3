#!/usr/bin/env python3
"""
Test script for MCP integration with Perplexity
Verifies web search, scraping, and real-time data capabilities
"""

import asyncio
import os
import sys
from datetime import datetime

# Add the project root to the path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.mcp_client import MCPClient, TradingMCPIntegration

async def test_mcp_basic_functionality():
    """Test basic MCP functionality"""
    print("🔍 Testing MCP Basic Functionality...")
    
    client = MCPClient()
    integration = TradingMCPIntegration(client)
    
    # Test health check
    print("1. Testing health check...")
    is_healthy = await client.health_check()
    print(f"   Health check result: {'✅ PASS' if is_healthy else '❌ FAIL'}")
    
    if not is_healthy:
        print("   ⚠️  MCP service is not healthy. Please check if the service is running.")
        return False
    
    # Test configuration
    print("2. Testing configuration...")
    config = await client.get_config()
    print(f"   Configuration: {'✅ PASS' if config else '❌ FAIL'}")
    if config:
        print(f"   MCP enabled: {config.get('enabled', False)}")
        print(f"   Perplexity enabled: {config.get('perplexity', {}).get('enabled', False)}")
    
    return True

async def test_web_search():
    """Test web search functionality"""
    print("\n🌐 Testing Web Search...")
    
    client = MCPClient()
    integration = TradingMCPIntegration(client)
    
    # Test market news search
    print("1. Testing market news search...")
    try:
        news = await integration.search_market_news("BTC")
        print(f"   Found {len(news)} news articles: {'✅ PASS' if news else '❌ FAIL'}")
        if news:
            for i, article in enumerate(news[:2], 1):
                print(f"   {i}. {article.title[:50]}...")
    except Exception as e:
        print(f"   ❌ FAIL: {e}")
    
    # Test technical analysis search
    print("2. Testing technical analysis search...")
    try:
        analysis = await integration.search_technical_analysis("ETH")
        print(f"   Found {len(analysis)} analysis articles: {'✅ PASS' if analysis else '❌ FAIL'}")
        if analysis:
            for i, article in enumerate(analysis[:2], 1):
                print(f"   {i}. {article.title[:50]}...")
    except Exception as e:
        print(f"   ❌ FAIL: {e}")
    
    # Test regulatory news search
    print("3. Testing regulatory news search...")
    try:
        regulatory = await integration.search_regulatory_news()
        print(f"   Found {len(regulatory)} regulatory articles: {'✅ PASS' if regulatory else '❌ FAIL'}")
        if regulatory:
            for i, article in enumerate(regulatory[:2], 1):
                print(f"   {i}. {article.title[:50]}...")
    except Exception as e:
        print(f"   ❌ FAIL: {e}")

async def test_web_scraping():
    """Test web scraping functionality"""
    print("\n📄 Testing Web Scraping...")
    
    client = MCPClient()
    
    # Test scraping a simple website
    print("1. Testing basic web scraping...")
    try:
        result = await client.scrape_url("https://httpbin.org/html")
        print(f"   Scraping result: {'✅ PASS' if result else '❌ FAIL'}")
        if result:
            print(f"   Title: {result.title[:50]}...")
            print(f"   Content length: {len(result.content)} characters")
    except Exception as e:
        print(f"   ❌ FAIL: {e}")

async def test_real_time_data():
    """Test real-time data functionality"""
    print("\n⏰ Testing Real-time Data...")
    
    client = MCPClient()
    integration = TradingMCPIntegration(client)
    
    # Test crypto market data
    print("1. Testing crypto market data...")
    try:
        market_data = await integration.get_crypto_market_data()
        print(f"   Market data: {'✅ PASS' if market_data else '❌ FAIL'}")
        if market_data:
            print(f"   Data items: {len(market_data)}")
    except Exception as e:
        print(f"   ❌ FAIL: {e}")

async def test_trading_integration():
    """Test trading-specific MCP integration"""
    print("\n📈 Testing Trading Integration...")
    
    client = MCPClient()
    integration = TradingMCPIntegration(client)
    
    # Test exchange news
    print("1. Testing exchange news...")
    try:
        exchange_news = await integration.get_exchange_news("Binance")
        print(f"   Exchange news: {'✅ PASS' if exchange_news else '❌ FAIL'}")
        if exchange_news:
            print(f"   Found {len(exchange_news)} articles")
    except Exception as e:
        print(f"   ❌ FAIL: {e}")
    
    # Test market sentiment
    print("2. Testing market sentiment...")
    try:
        sentiment = await integration.search_market_sentiment("BTC")
        print(f"   Market sentiment: {'✅ PASS' if sentiment else '❌ FAIL'}")
        if sentiment:
            print(f"   Found {len(sentiment)} sentiment articles")
    except Exception as e:
        print(f"   ❌ FAIL: {e}")

async def test_cursor_ai_integration():
    """Test Cursor AI MCP integration"""
    print("\n🤖 Testing Cursor AI Integration...")
    
    # Check if MCP is properly configured for Cursor AI
    print("1. Checking Cursor AI MCP configuration...")
    
    # Check for required files
    required_files = [
        '.cursorrules',
        '.cursor/settings.json',
        'package.json'
    ]
    
    for file_path in required_files:
        exists = os.path.exists(file_path)
        print(f"   {file_path}: {'✅ EXISTS' if exists else '❌ MISSING'}")
    
    # Check environment variable
    api_key = os.getenv('PERPLEXITY_API_KEY')
    print(f"   PERPLEXITY_API_KEY: {'✅ SET' if api_key else '❌ MISSING'}")
    
    if not api_key:
        print("   ⚠️  Please set PERPLEXITY_API_KEY in your .env file")

async def main():
    """Main test function"""
    print("🚀 Starting MCP Integration Tests...")
    print("=" * 50)
    
    # Check prerequisites
    print("📋 Checking Prerequisites...")
    api_key = os.getenv('PERPLEXITY_API_KEY')
    if not api_key:
        print("❌ PERPLEXITY_API_KEY not found in environment variables")
        print("   Please add it to your .env file:")
        print("   PERPLEXITY_API_KEY=your_api_key_here")
        return
    
    print("✅ PERPLEXITY_API_KEY found")
    
    # Run tests
    basic_ok = await test_mcp_basic_functionality()
    
    if basic_ok:
        await test_web_search()
        await test_web_scraping()
        await test_real_time_data()
        await test_trading_integration()
    
    await test_cursor_ai_integration()
    
    print("\n" + "=" * 50)
    print("🏁 MCP Integration Tests Complete!")
    print("\nNext Steps:")
    print("1. Ensure all tests pass")
    print("2. Configure Cursor AI to use MCP")
    print("3. Test MCP functionality in Cursor AI chat")
    print("4. Integrate MCP data into trading strategies")

if __name__ == "__main__":
    asyncio.run(main()) 