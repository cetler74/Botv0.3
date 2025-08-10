# Perplexity MCP Setup for Cursor AI

## Overview
This guide sets up the Perplexity MCP (Model Context Protocol) to enable Cursor AI to access real-time internet data, perform web searches, and scrape websites for enhanced trading bot capabilities.

## Prerequisites

1. **Perplexity API Key**: Get your API key from [Perplexity AI](https://www.perplexity.ai/)
2. **Node.js**: Required for MCP server (version 16 or higher)
3. **Cursor AI**: Latest version with MCP support

## Setup Steps

### 1. Install MCP Server

```bash
# Install the Perplexity MCP server globally
npm install -g @modelcontextprotocol/server-perplexity

# Or install locally in your project
npm install @modelcontextprotocol/server-perplexity
```

### 2. Configure Environment Variables

Add your Perplexity API key to your `.env` file:

```bash
# .env file
PERPLEXITY_API_KEY=your_perplexity_api_key_here
```

### 3. Cursor AI Configuration

The following files have been created for Cursor AI MCP integration:

- `.cursorrules`: Project-specific rules and MCP usage guidelines
- `.cursor/settings.json`: Cursor AI settings for MCP integration

### 4. Verify Setup

Test the MCP connection:

```bash
# Test MCP server
npx @modelcontextprotocol/server-perplexity --help
```

## MCP Capabilities

### Web Search
- Real-time web search results
- News and market data
- Technical analysis articles
- Regulatory updates

### Web Scraping
- Extract content from trading websites
- Parse market data from financial sites
- Get real-time price information
- Scrape news articles

### Real-time Data
- Live cryptocurrency prices
- Market cap information
- Trading volume data
- Exchange status updates

## Usage Examples

### In Cursor AI Chat

```
# Search for market news
Search for latest Bitcoin market news and price analysis

# Get technical analysis
Find technical analysis articles for Ethereum trading

# Scrape trading data
Scrape current market data from CoinGecko

# Get regulatory news
Search for latest cryptocurrency regulation news
```

### In Code

```python
# The MCP client is available in core/mcp_client.py
from core.mcp_client import MCPClient, TradingMCPIntegration

# Initialize client
client = MCPClient()
integration = TradingMCPIntegration(client)

# Search for market news
news = await integration.search_market_news("BTC")

# Get technical analysis
analysis = await integration.search_technical_analysis("ETH")

# Scrape trading view
trading_view = await integration.scrape_trading_view_analysis("BTCUSDT")
```

## Configuration Files

### config/config.yaml
MCP configuration has been added to the main config file:

```yaml
mcp:
  enabled: true
  perplexity:
    enabled: true
    api_key: ""  # Set in .env file
    base_url: "https://api.perplexity.ai"
    timeout: 30
    max_retries: 3
    rate_limit_delay: 1.0
    features:
      web_search: true
      web_scraping: true
      real_time_data: true
      news_analysis: true
    search_settings:
      max_results: 10
      include_domains: []
      exclude_domains: []
      search_type: "web"
    scraping_settings:
      max_content_length: 50000
      include_images: false
      include_links: true
      timeout: 15
    cache:
      enabled: true
      ttl_seconds: 3600
      max_size: 1000
```

### Docker Integration
The MCP service has been added to docker-compose.yml:

```yaml
mcp-service:
  build: ./services/mcp-service
  container_name: trading-bot-mcp
  ports:
    - "8007:8001"
  environment:
    - PERPLEXITY_API_KEY=${PERPLEXITY_API_KEY}
    - REDIS_HOST=redis
    - REDIS_PORT=6379
```

## Troubleshooting

### Common Issues

1. **API Key Not Found**
   - Ensure PERPLEXITY_API_KEY is set in .env file
   - Restart Cursor AI after setting environment variables

2. **MCP Server Not Starting**
   - Check Node.js version (requires 16+)
   - Verify npm installation
   - Check network connectivity

3. **Rate Limiting**
   - Perplexity has rate limits
   - Implement caching for repeated requests
   - Use appropriate delays between requests

### Debug Commands

```bash
# Test MCP server connection
curl -X GET http://localhost:8007/health

# Check MCP configuration
curl -X GET http://localhost:8007/config

# Test search functionality
curl -X POST http://localhost:8007/search \
  -H "Content-Type: application/json" \
  -d '{"query": "Bitcoin price", "search_type": "web", "max_results": 5}'
```

## Security Considerations

1. **API Key Protection**
   - Never commit API keys to version control
   - Use environment variables for all sensitive data
   - Rotate API keys regularly

2. **Rate Limiting**
   - Implement proper rate limiting
   - Cache responses to reduce API calls
   - Monitor usage to stay within limits

3. **Data Validation**
   - Validate all scraped data
   - Implement error handling for failed requests
   - Log all MCP interactions for debugging

## Next Steps

1. **Test the Setup**: Run the test script to verify MCP integration
2. **Configure Trading Strategies**: Integrate MCP data into trading decisions
3. **Monitor Usage**: Track API usage and performance
4. **Expand Capabilities**: Add more MCP features as needed

## Support

For issues with:
- **Perplexity API**: Contact Perplexity support
- **MCP Protocol**: Check Model Context Protocol documentation
- **Cursor AI**: Check Cursor AI documentation
- **Trading Bot Integration**: Check the project documentation 