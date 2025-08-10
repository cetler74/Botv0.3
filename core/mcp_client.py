#!/usr/bin/env python3
"""
MCP Client for Trading Bot Integration
Provides easy access to Perplexity MCP services for web search and scraping
"""

import asyncio
import json
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime

import httpx
import yaml
from pydantic import BaseModel

logger = logging.getLogger(__name__)

class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str
    source: str
    timestamp: datetime

class ScrapingResult(BaseModel):
    url: str
    title: str
    content: str
    links: List[str] = []
    images: List[str] = []
    timestamp: datetime

class MCPClient:
    """Client for interacting with MCP services"""
    
    def __init__(self, base_url: str = "http://localhost:8001"):
        self.base_url = base_url
        self.timeout = 30
        
    async def health_check(self) -> bool:
        """Check if MCP service is healthy"""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(f"{self.base_url}/health")
                return response.status_code == 200
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False
    
    async def search(self, query: str, search_type: str = "web", 
                    max_results: int = 10, include_domains: List[str] = None,
                    exclude_domains: List[str] = None) -> List[SearchResult]:
        """Perform web search"""
        try:
            payload = {
                "query": query,
                "search_type": search_type,
                "max_results": max_results,
                "include_domains": include_domains or [],
                "exclude_domains": exclude_domains or []
            }
            
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/search",
                    json=payload
                )
                response.raise_for_status()
                
                data = response.json()
                return [SearchResult(**item) for item in data]
                
        except Exception as e:
            logger.error(f"Search failed: {e}")
            return []
    
    async def scrape_url(self, url: str, include_images: bool = False,
                        include_links: bool = True, max_content_length: int = 50000) -> Optional[ScrapingResult]:
        """Scrape content from URL"""
        try:
            payload = {
                "url": url,
                "include_images": include_images,
                "include_links": include_links,
                "max_content_length": max_content_length
            }
            
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/scrape",
                    json=payload
                )
                response.raise_for_status()
                
                data = response.json()
                return ScrapingResult(**data)
                
        except Exception as e:
            logger.error(f"Scraping failed: {e}")
            return None
    
    async def get_real_time_data(self, query: str) -> Dict[str, Any]:
        """Get real-time data"""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/real-time",
                    params={"query": query}
                )
                response.raise_for_status()
                return response.json()
                
        except Exception as e:
            logger.error(f"Real-time data failed: {e}")
            return {}
    
    async def get_config(self) -> Dict[str, Any]:
        """Get MCP service configuration"""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(f"{self.base_url}/config")
                response.raise_for_status()
                return response.json()
                
        except Exception as e:
            logger.error(f"Config fetch failed: {e}")
            return {}

class TradingMCPIntegration:
    """Integration class for trading-specific MCP operations"""
    
    def __init__(self, mcp_client: MCPClient):
        self.mcp_client = mcp_client
    
    async def search_market_news(self, symbol: str, timeframe: str = "24h") -> List[SearchResult]:
        """Search for market news related to a trading symbol"""
        query = f"{symbol} cryptocurrency news market analysis {timeframe}"
        return await self.mcp_client.search(
            query=query,
            search_type="news",
            max_results=5
        )
    
    async def search_technical_analysis(self, symbol: str) -> List[SearchResult]:
        """Search for technical analysis on a trading symbol"""
        query = f"{symbol} technical analysis chart patterns indicators"
        return await self.mcp_client.search(
            query=query,
            search_type="web",
            max_results=5
        )
    
    async def search_market_sentiment(self, symbol: str) -> List[SearchResult]:
        """Search for market sentiment analysis"""
        query = f"{symbol} market sentiment social media reddit twitter"
        return await self.mcp_client.search(
            query=query,
            search_type="web",
            max_results=5
        )
    
    async def get_exchange_news(self, exchange: str) -> List[SearchResult]:
        """Get news about specific exchanges"""
        query = f"{exchange} exchange news updates announcements"
        return await self.mcp_client.search(
            query=query,
            search_type="news",
            max_results=3
        )
    
    async def scrape_trading_view_analysis(self, symbol: str) -> Optional[ScrapingResult]:
        """Scrape TradingView analysis for a symbol"""
        url = f"https://www.tradingview.com/symbols/{symbol}"
        return await self.mcp_client.scrape_url(url)
    
    async def get_crypto_market_data(self) -> Dict[str, Any]:
        """Get real-time crypto market data"""
        query = "cryptocurrency market cap price bitcoin ethereum real-time data"
        return await self.mcp_client.get_real_time_data(query)
    
    async def search_regulatory_news(self) -> List[SearchResult]:
        """Search for regulatory news affecting crypto markets"""
        query = "cryptocurrency regulation SEC CFTC crypto news"
        return await self.mcp_client.search(
            query=query,
            search_type="news",
            max_results=5
        )

# Example usage and testing
async def test_mcp_integration():
    """Test the MCP integration"""
    client = MCPClient()
    integration = TradingMCPIntegration(client)
    
    # Test health check
    is_healthy = await client.health_check()
    print(f"MCP Service Health: {is_healthy}")
    
    if is_healthy:
        # Test market news search
        news = await integration.search_market_news("BTC")
        print(f"Found {len(news)} news articles")
        
        # Test technical analysis search
        analysis = await integration.search_technical_analysis("ETH")
        print(f"Found {len(analysis)} analysis articles")
        
        # Test real-time data
        market_data = await integration.get_crypto_market_data()
        print(f"Market data: {len(market_data)} items")

if __name__ == "__main__":
    asyncio.run(test_mcp_integration()) 