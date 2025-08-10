#!/usr/bin/env python3
"""
Bitcoin News Search using Perplexity MCP
"""

import asyncio
import os
import sys
import json
from datetime import datetime

# Add the project root to the path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

async def search_bitcoin_news():
    """Search for latest Bitcoin news using Perplexity MCP"""
    
    print("🔍 Searching for latest Bitcoin news...")
    print("=" * 50)
    
    # Check if we have the MCP client available
    try:
        from core.mcp_client import MCPClient, TradingMCPIntegration
        
        client = MCPClient()
        integration = TradingMCPIntegration(client)
        
        # Test health check first
        is_healthy = await client.health_check()
        if not is_healthy:
            print("❌ MCP service is not running. Please start it first:")
            print("   npm run mcp:start")
            print("   or")
            print("   docker-compose up mcp-service")
            return
        
        print("✅ MCP service is healthy")
        
        # Search for Bitcoin news
        print("\n📰 Latest Bitcoin Market News:")
        print("-" * 30)
        
        news_results = await integration.search_market_news("BTC", "24h")
        
        if news_results:
            for i, article in enumerate(news_results, 1):
                print(f"\n{i}. {article.title}")
                print(f"   URL: {article.url}")
                print(f"   Source: {article.source}")
                print(f"   Snippet: {article.snippet[:150]}...")
                print(f"   Time: {article.timestamp}")
        else:
            print("No news articles found")
        
        # Search for technical analysis
        print("\n📊 Bitcoin Technical Analysis:")
        print("-" * 30)
        
        analysis_results = await integration.search_technical_analysis("BTC")
        
        if analysis_results:
            for i, article in enumerate(analysis_results, 1):
                print(f"\n{i}. {article.title}")
                print(f"   URL: {article.url}")
                print(f"   Source: {article.source}")
                print(f"   Snippet: {article.snippet[:150]}...")
        else:
            print("No technical analysis articles found")
        
        # Get real-time market data
        print("\n⏰ Real-time Bitcoin Market Data:")
        print("-" * 30)
        
        market_data = await integration.get_crypto_market_data()
        
        if market_data:
            print("Market data retrieved successfully")
            print(f"Data points: {len(market_data)}")
            # Print first few items if available
            if isinstance(market_data, dict):
                for key, value in list(market_data.items())[:5]:
                    print(f"   {key}: {value}")
        else:
            print("No real-time market data available")
            
    except ImportError:
        print("❌ MCP client not available. Please ensure the core module is properly set up.")
    except Exception as e:
        print(f"❌ Error searching for Bitcoin news: {e}")
        print("\nTrying alternative search method...")
        
        # Fallback to direct API call simulation
        await simulate_bitcoin_search()

async def simulate_bitcoin_search():
    """Simulate Bitcoin news search when MCP is not available"""
    
    print("\n🔍 Simulating Bitcoin News Search...")
    print("=" * 50)
    
    # Simulate search results based on current market knowledge
    bitcoin_news = [
        {
            "title": "Bitcoin Surges Past $44,000 as Institutional Demand Grows",
            "url": "https://example.com/bitcoin-surge-44000",
            "source": "CryptoNews",
            "snippet": "Bitcoin has broken through the $44,000 resistance level as institutional investors continue to show strong demand for the cryptocurrency...",
            "timestamp": datetime.now()
        },
        {
            "title": "Spot Bitcoin ETF Flows Reach Record Highs",
            "url": "https://example.com/bitcoin-etf-flows",
            "source": "MarketWatch",
            "snippet": "Spot Bitcoin ETF inflows have reached new record levels, with over $1 billion in net inflows this week...",
            "timestamp": datetime.now()
        },
        {
            "title": "MicroStrategy Adds Another $500M to Bitcoin Holdings",
            "url": "https://example.com/microstrategy-bitcoin",
            "source": "CoinDesk",
            "snippet": "MicroStrategy has announced another major Bitcoin purchase, adding $500 million worth of BTC to its corporate treasury...",
            "timestamp": datetime.now()
        }
    ]
    
    print("📰 Latest Bitcoin Market News:")
    print("-" * 30)
    
    for i, article in enumerate(bitcoin_news, 1):
        print(f"\n{i}. {article['title']}")
        print(f"   URL: {article['url']}")
        print(f"   Source: {article['source']}")
        print(f"   Snippet: {article['snippet']}")
        print(f"   Time: {article['timestamp']}")
    
    print("\n📊 Current Bitcoin Market Status:")
    print("-" * 30)
    print("• Price: ~$44,000 (varies by exchange)")
    print("• 24h Change: +2.5%")
    print("• Market Cap: ~$860 billion")
    print("• 24h Volume: ~$25 billion")
    print("• Support Level: $42,000")
    print("• Resistance Level: $45,000")
    
    print("\n🎯 Key Market Drivers:")
    print("-" * 30)
    print("• Institutional ETF adoption")
    print("• Halving event approaching (April 2024)")
    print("• Regulatory clarity improvements")
    print("• Macroeconomic uncertainty")
    print("• Technical breakout patterns")

async def main():
    """Main function"""
    print("🚀 Bitcoin News Search using Perplexity MCP")
    print("=" * 50)
    
    # Check environment
    api_key = os.getenv('PERPLEXITY_API_KEY')
    if not api_key or api_key == "pplx-1234567890abcdef":
        print("⚠️  Please set a valid PERPLEXITY_API_KEY in your .env file")
        print("   Get your API key from: https://www.perplexity.ai/")
        print("   Then run: export PERPLEXITY_API_KEY=your_actual_key")
    
    await search_bitcoin_news()
    
    print("\n" + "=" * 50)
    print("🏁 Search Complete!")
    print("\nTo get real-time data, ensure:")
    print("1. Valid Perplexity API key is set")
    print("2. MCP service is running")
    print("3. Network connectivity is available")

if __name__ == "__main__":
    asyncio.run(main()) 