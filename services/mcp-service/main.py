#!/usr/bin/env python3
"""
MCP Service for Perplexity API Integration
Handles web search, scraping, and real-time data fetching
"""

import asyncio
import json
import logging
import os
import time
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta

import httpx
import yaml
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field
import redis.asyncio as redis

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="MCP Service", version="1.0.0")

class SearchRequest(BaseModel):
    query: str
    search_type: str = "web"  # web, news, academic
    max_results: int = 10
    include_domains: List[str] = []
    exclude_domains: List[str] = []

class ScrapingRequest(BaseModel):
    url: str
    include_images: bool = False
    include_links: bool = True
    max_content_length: int = 50000

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

class MCPConfig:
    def __init__(self):
        self.config = self._load_config()
        self.redis_client = None
    
    def _load_config(self) -> Dict:
        """Load configuration from config.yaml"""
        config_path = "/app/config/config.yaml"
        with open(config_path, 'r') as f:
            return yaml.safe_load(f)
    
    async def get_redis_client(self):
        """Get Redis client for caching"""
        if self.redis_client is None:
            redis_config = self.config.get('redis', {})
            self.redis_client = redis.Redis(
                host=redis_config.get('host', 'localhost'),
                port=redis_config.get('port', 6379),
                db=redis_config.get('db', 0),
                password=redis_config.get('password', ''),
                decode_responses=True
            )
        return self.redis_client

class PerplexityMCP:
    def __init__(self, config: MCPConfig):
        self.config = config
        self.mcp_config = config.config.get('mcp', {}).get('perplexity', {})
        self.api_key = os.getenv('PERPLEXITY_API_KEY')
        self.base_url = self.mcp_config.get('base_url', 'https://api.perplexity.ai')
        self.timeout = self.mcp_config.get('timeout', 30)
        self.max_retries = self.mcp_config.get('max_retries', 3)
        self.rate_limit_delay = self.mcp_config.get('rate_limit_delay', 1.0)
        
        if not self.api_key:
            raise ValueError("PERPLEXITY_API_KEY environment variable is required")
    
    async def search(self, request: SearchRequest) -> List[SearchResult]:
        """Perform web search using Perplexity API"""
        cache_key = f"search:{hash(request.query)}"
        redis_client = await self.config.get_redis_client()
        
        # Check cache first
        cached_result = await redis_client.get(cache_key)
        if cached_result:
            return [SearchResult(**json.loads(item)) for item in json.loads(cached_result)]
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "query": request.query,
            "search_type": request.search_type,
            "max_results": request.max_results
        }
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(self.max_retries):
                try:
                    response = await client.post(
                        f"{self.base_url}/search",
                        headers=headers,
                        json=payload
                    )
                    response.raise_for_status()
                    
                    data = response.json()
                    results = []
                    
                    for item in data.get('results', []):
                        result = SearchResult(
                            title=item.get('title', ''),
                            url=item.get('url', ''),
                            snippet=item.get('snippet', ''),
                            source=item.get('source', ''),
                            timestamp=datetime.now()
                        )
                        results.append(result)
                    
                    # Cache results
                    cache_ttl = self.mcp_config.get('cache', {}).get('ttl_seconds', 3600)
                    await redis_client.setex(
                        cache_key,
                        cache_ttl,
                        json.dumps([result.dict() for result in results])
                    )
                    
                    return results
                    
                except httpx.HTTPError as e:
                    logger.error(f"HTTP error on attempt {attempt + 1}: {e}")
                    if attempt == self.max_retries - 1:
                        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")
                    await asyncio.sleep(self.rate_limit_delay)
    
    async def scrape_url(self, request: ScrapingRequest) -> ScrapingResult:
        """Scrape content from a URL using Perplexity API"""
        cache_key = f"scrape:{hash(request.url)}"
        redis_client = await self.config.get_redis_client()
        
        # Check cache first
        cached_result = await redis_client.get(cache_key)
        if cached_result:
            return ScrapingResult(**json.loads(cached_result))
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "url": request.url,
            "include_images": request.include_images,
            "include_links": request.include_links,
            "max_content_length": request.max_content_length
        }
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(self.max_retries):
                try:
                    response = await client.post(
                        f"{self.base_url}/scrape",
                        headers=headers,
                        json=payload
                    )
                    response.raise_for_status()
                    
                    data = response.json()
                    result = ScrapingResult(
                        url=request.url,
                        title=data.get('title', ''),
                        content=data.get('content', ''),
                        links=data.get('links', []),
                        images=data.get('images', []),
                        timestamp=datetime.now()
                    )
                    
                    # Cache result
                    cache_ttl = self.mcp_config.get('cache', {}).get('ttl_seconds', 3600)
                    await redis_client.setex(
                        cache_key,
                        cache_ttl,
                        json.dumps(result.dict())
                    )
                    
                    return result
                    
                except httpx.HTTPError as e:
                    logger.error(f"HTTP error on attempt {attempt + 1}: {e}")
                    if attempt == self.max_retries - 1:
                        raise HTTPException(status_code=500, detail=f"Scraping failed: {str(e)}")
                    await asyncio.sleep(self.rate_limit_delay)
    
    async def get_real_time_data(self, query: str) -> Dict[str, Any]:
        """Get real-time data using Perplexity API"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "query": query,
            "search_type": "real_time"
        }
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}/search",
                headers=headers,
                json=payload
            )
            response.raise_for_status()
            return response.json()

# Initialize services
config = MCPConfig()
mcp_service = PerplexityMCP(config)

@app.on_event("startup")
async def startup_event():
    """Initialize services on startup"""
    logger.info("MCP Service starting up...")
    try:
        await config.get_redis_client()
        logger.info("Redis connection established")
    except Exception as e:
        logger.error(f"Failed to connect to Redis: {e}")

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "mcp-service"}

@app.post("/search", response_model=List[SearchResult])
async def search_endpoint(request: SearchRequest):
    """Perform web search"""
    try:
        results = await mcp_service.search(request)
        return results
    except Exception as e:
        logger.error(f"Search error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/scrape", response_model=ScrapingResult)
async def scrape_endpoint(request: ScrapingRequest):
    """Scrape content from URL"""
    try:
        result = await mcp_service.scrape_url(request)
        return result
    except Exception as e:
        logger.error(f"Scraping error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/real-time")
async def real_time_data_endpoint(query: str):
    """Get real-time data"""
    try:
        data = await mcp_service.get_real_time_data(query)
        return data
    except Exception as e:
        logger.error(f"Real-time data error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/config")
async def get_config():
    """Get current MCP configuration"""
    return {
        "enabled": config.config.get('mcp', {}).get('enabled', False),
        "perplexity": config.config.get('mcp', {}).get('perplexity', {})
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001) 