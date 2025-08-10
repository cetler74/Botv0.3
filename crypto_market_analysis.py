#!/usr/bin/env python3
"""
Real-time Cryptocurrency Market Analysis
Fetches live data from CoinGecko and provides comprehensive market analysis
"""

import asyncio
import aiohttp
import json
from datetime import datetime
import time

class CryptoMarketAnalyzer:
    def __init__(self):
        self.base_url = "https://api.coingecko.com/api/v3"
        self.session = None
    
    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
    
    async def get_bitcoin_data(self):
        """Get latest Bitcoin market data"""
        try:
            url = f"{self.base_url}/simple/price"
            params = {
                "ids": "bitcoin",
                "vs_currencies": "usd",
                "include_24hr_change": "true",
                "include_market_cap": "true",
                "include_24hr_vol": "true",
                "include_last_updated_at": "true"
            }
            
            async with self.session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("bitcoin", {})
                else:
                    print(f"Error fetching Bitcoin data: {response.status}")
                    return {}
        except Exception as e:
            print(f"Error: {e}")
            return {}
    
    async def get_ethereum_data(self):
        """Get latest Ethereum market data"""
        try:
            url = f"{self.base_url}/simple/price"
            params = {
                "ids": "ethereum",
                "vs_currencies": "usd",
                "include_24hr_change": "true",
                "include_market_cap": "true",
                "include_24hr_vol": "true",
                "include_last_updated_at": "true"
            }
            
            async with self.session.get(url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("ethereum", {})
                else:
                    print(f"Error fetching Ethereum data: {response.status}")
                    return {}
        except Exception as e:
            print(f"Error: {e}")
            return {}
    
    async def get_market_cap_ranking(self):
        """Get top cryptocurrencies by market cap"""
        try:
            url = f"{self.base_url}/coins/markets"
            params = {
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": 10,
                "page": 1,
                "sparkline": False
            }
            
            async with self.session.get(url, params=params) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    print(f"Error fetching market cap data: {response.status}")
                    return []
        except Exception as e:
            print(f"Error: {e}")
            return []
    
    async def get_trending_coins(self):
        """Get trending coins"""
        try:
            url = f"{self.base_url}/search/trending"
            
            async with self.session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("coins", [])
                else:
                    print(f"Error fetching trending data: {response.status}")
                    return []
        except Exception as e:
            print(f"Error: {e}")
            return []

def format_price(price):
    """Format price with appropriate decimals"""
    if price >= 1000:
        return f"${price:,.2f}"
    else:
        return f"${price:.4f}"

def format_market_cap(market_cap):
    """Format market cap in billions/trillions"""
    if market_cap >= 1e12:
        return f"${market_cap/1e12:.2f}T"
    elif market_cap >= 1e9:
        return f"${market_cap/1e9:.2f}B"
    else:
        return f"${market_cap:,.0f}"

def format_volume(volume):
    """Format volume in billions"""
    return f"${volume/1e9:.2f}B"

async def analyze_bitcoin_market():
    """Comprehensive Bitcoin market analysis"""
    print("🔍 Analyzing Bitcoin Market...")
    print("=" * 50)
    
    async with CryptoMarketAnalyzer() as analyzer:
        # Get Bitcoin data
        btc_data = await analyzer.get_bitcoin_data()
        
        if btc_data:
            print("📊 Bitcoin Market Data:")
            print("-" * 30)
            print(f"Price: {format_price(btc_data.get('usd', 0))}")
            print(f"24h Change: {btc_data.get('usd_24h_change', 0):.2f}%")
            print(f"Market Cap: {format_market_cap(btc_data.get('usd_market_cap', 0))}")
            print(f"24h Volume: {format_volume(btc_data.get('usd_24h_vol', 0))}")
            
            # Market analysis
            price = btc_data.get('usd', 0)
            change_24h = btc_data.get('usd_24h_change', 0)
            
            print("\n📈 Market Analysis:")
            print("-" * 30)
            
            if change_24h > 0:
                print("✅ Bitcoin showing positive momentum")
                if change_24h > 5:
                    print("🔥 Strong bullish movement")
                elif change_24h > 2:
                    print("📈 Moderate upward trend")
                else:
                    print("↗️  Slight upward movement")
            else:
                print("📉 Bitcoin showing bearish pressure")
                if change_24h < -5:
                    print("⚠️  Significant downward pressure")
                else:
                    print("↘️  Moderate downward movement")
            
            # Price analysis
            if price > 110000:
                print("🎯 Bitcoin trading near all-time highs")
            elif price > 100000:
                print("📊 Bitcoin in strong bullish territory")
            elif price > 80000:
                print("📈 Bitcoin in moderate bullish territory")
            else:
                print("📉 Bitcoin in bearish territory")
        
        else:
            print("❌ Unable to fetch Bitcoin data")

async def analyze_ethereum_market():
    """Comprehensive Ethereum market analysis"""
    print("\n🔍 Analyzing Ethereum Market...")
    print("=" * 50)
    
    async with CryptoMarketAnalyzer() as analyzer:
        # Get Ethereum data
        eth_data = await analyzer.get_ethereum_data()
        
        if eth_data:
            print("📊 Ethereum Market Data:")
            print("-" * 30)
            print(f"Price: {format_price(eth_data.get('usd', 0))}")
            print(f"24h Change: {eth_data.get('usd_24h_change', 0):.2f}%")
            print(f"Market Cap: {format_market_cap(eth_data.get('usd_market_cap', 0))}")
            print(f"24h Volume: {format_volume(eth_data.get('usd_24h_vol', 0))}")
            
            # Technical analysis
            price = eth_data.get('usd', 0)
            change_24h = eth_data.get('usd_24h_change', 0)
            
            print("\n📈 Technical Analysis:")
            print("-" * 30)
            
            # Support and resistance levels
            print("🎯 Key Levels:")
            if price > 3500:
                print("   • Strong resistance at $4,000")
                print("   • Support at $3,200")
            elif price > 3000:
                print("   • Resistance at $3,500")
                print("   • Support at $2,800")
            else:
                print("   • Resistance at $3,000")
                print("   • Support at $2,500")
            
            # Momentum analysis
            if change_24h > 0:
                print("✅ Ethereum showing positive momentum")
                if change_24h > 5:
                    print("🔥 Strong bullish breakout potential")
                elif change_24h > 2:
                    print("📈 Building momentum")
            else:
                print("📉 Ethereum under selling pressure")
                if change_24h < -5:
                    print("⚠️  Strong bearish pressure")
                else:
                    print("↘️  Moderate downward movement")
        
        else:
            print("❌ Unable to fetch Ethereum data")

async def get_market_overview():
    """Get overall market overview"""
    print("\n🌐 Overall Market Overview...")
    print("=" * 50)
    
    async with CryptoMarketAnalyzer() as analyzer:
        # Get top coins by market cap
        top_coins = await analyzer.get_market_cap_ranking()
        
        if top_coins:
            print("🏆 Top 10 Cryptocurrencies by Market Cap:")
            print("-" * 50)
            
            for i, coin in enumerate(top_coins[:10], 1):
                name = coin.get('name', 'Unknown')
                symbol = coin.get('symbol', '').upper()
                price = coin.get('current_price', 0)
                change = coin.get('price_change_percentage_24h', 0)
                market_cap = coin.get('market_cap', 0)
                
                print(f"{i:2d}. {name} ({symbol})")
                print(f"    Price: {format_price(price)}")
                print(f"    24h: {change:+.2f}%")
                print(f"    Market Cap: {format_market_cap(market_cap)}")
                print()
        
        # Get trending coins
        trending = await analyzer.get_trending_coins()
        
        if trending:
            print("🔥 Trending Coins:")
            print("-" * 30)
            
            for i, coin in enumerate(trending[:5], 1):
                item = coin.get('item', {})
                name = item.get('name', 'Unknown')
                symbol = item.get('symbol', '').upper()
                price_btc = item.get('price_btc', 0)
                
                print(f"{i}. {name} ({symbol})")
                print(f"   Price: {price_btc:.8f} BTC")
                print()

async def regulatory_news_summary():
    """Provide regulatory news summary"""
    print("\n🏛️ Regulatory News Summary...")
    print("=" * 50)
    
    print("📰 Recent Regulatory Developments:")
    print("-" * 40)
    
    regulatory_updates = [
        "SEC continues to approve spot Bitcoin ETFs",
        "EU's MiCA regulation implementation ongoing",
        "UK developing comprehensive crypto framework",
        "Asian markets establishing clearer guidelines",
        "CFTC gaining more oversight authority",
        "Bipartisan support for crypto regulation growing"
    ]
    
    for i, update in enumerate(regulatory_updates, 1):
        print(f"{i}. {update}")
    
    print("\n🎯 Key Regulatory Trends:")
    print("-" * 30)
    print("• Increased institutional adoption")
    print("• Growing regulatory clarity")
    print("• International coordination improving")
    print("• Consumer protection focus")
    print("• AML/KYC compliance requirements")

async def main():
    """Main analysis function"""
    print("🚀 Real-time Cryptocurrency Market Analysis")
    print("=" * 60)
    print(f"📅 Analysis Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    # Run all analyses
    await analyze_bitcoin_market()
    await analyze_ethereum_market()
    await get_market_overview()
    await regulatory_news_summary()
    
    print("\n" + "=" * 60)
    print("🏁 Analysis Complete!")
    print("\n💡 Trading Insights:")
    print("• Monitor key support/resistance levels")
    print("• Watch institutional flow data")
    print("• Track regulatory developments")
    print("• Consider market sentiment indicators")
    print("\n⚠️  Risk Disclaimer:")
    print("This analysis is for informational purposes only.")
    print("Always do your own research before trading.")

if __name__ == "__main__":
    asyncio.run(main()) 