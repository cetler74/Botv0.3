#!/usr/bin/env node

/**
 * Custom MCP Server for Perplexity API Integration
 * Provides web search, scraping, and real-time data capabilities
 */

const { Server } = require('@modelcontextprotocol/sdk/server/index.js');
const { StdioServerTransport } = require('@modelcontextprotocol/sdk/server/stdio.js');
const fetch = require('node-fetch');

class PerplexityMCPServer {
    constructor() {
        this.server = new Server(
            {
                name: 'perplexity-mcp-server',
                version: '1.0.0',
            },
            {
                capabilities: {
                    tools: {},
                },
            }
        );
        
        this.apiKey = process.env.PERPLEXITY_API_KEY;
        this.baseUrl = 'https://api.perplexity.ai';
        
        this.setupTools();
    }
    
    setupTools() {
        // Web Search Tool
        this.server.setRequestHandler('tools/call', async (request) => {
            const { name, arguments: args } = request.params;
            
            switch (name) {
                case 'web_search':
                    return await this.webSearch(args);
                case 'scrape_url':
                    return await this.scrapeUrl(args);
                case 'get_real_time_data':
                    return await this.getRealTimeData(args);
                default:
                    throw new Error(`Unknown tool: ${name}`);
            }
        });
        
        // List available tools
        this.server.setRequestHandler('tools/list', async () => {
            return {
                tools: [
                    {
                        name: 'web_search',
                        description: 'Search the web for information',
                        inputSchema: {
                            type: 'object',
                            properties: {
                                query: {
                                    type: 'string',
                                    description: 'Search query'
                                },
                                search_type: {
                                    type: 'string',
                                    enum: ['web', 'news', 'academic'],
                                    default: 'web',
                                    description: 'Type of search to perform'
                                },
                                max_results: {
                                    type: 'number',
                                    default: 10,
                                    description: 'Maximum number of results'
                                }
                            },
                            required: ['query']
                        }
                    },
                    {
                        name: 'scrape_url',
                        description: 'Scrape content from a URL',
                        inputSchema: {
                            type: 'object',
                            properties: {
                                url: {
                                    type: 'string',
                                    description: 'URL to scrape'
                                },
                                include_images: {
                                    type: 'boolean',
                                    default: false,
                                    description: 'Include images in results'
                                },
                                include_links: {
                                    type: 'boolean',
                                    default: true,
                                    description: 'Include links in results'
                                }
                            },
                            required: ['url']
                        }
                    },
                    {
                        name: 'get_real_time_data',
                        description: 'Get real-time data and information',
                        inputSchema: {
                            type: 'object',
                            properties: {
                                query: {
                                    type: 'string',
                                    description: 'Query for real-time data'
                                }
                            },
                            required: ['query']
                        }
                    }
                ]
            };
        });
    }
    
    async webSearch(args) {
        const { query, search_type = 'web', max_results = 10 } = args;
        
        if (!this.apiKey) {
            throw new Error('PERPLEXITY_API_KEY environment variable is required');
        }
        
        try {
            const response = await fetch(`${this.baseUrl}/search`, {
                method: 'POST',
                headers: {
                    'Authorization': `Bearer ${this.apiKey}`,
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    query,
                    search_type,
                    max_results
                })
            });
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
            
            const data = await response.json();
            
            return {
                content: [
                    {
                        type: 'text',
                        text: JSON.stringify(data, null, 2)
                    }
                ]
            };
        } catch (error) {
            throw new Error(`Web search failed: ${error.message}`);
        }
    }
    
    async scrapeUrl(args) {
        const { url, include_images = false, include_links = true } = args;
        
        if (!this.apiKey) {
            throw new Error('PERPLEXITY_API_KEY environment variable is required');
        }
        
        try {
            const response = await fetch(`${this.baseUrl}/scrape`, {
                method: 'POST',
                headers: {
                    'Authorization': `Bearer ${this.apiKey}`,
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    url,
                    include_images,
                    include_links
                })
            });
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
            
            const data = await response.json();
            
            return {
                content: [
                    {
                        type: 'text',
                        text: JSON.stringify(data, null, 2)
                    }
                ]
            };
        } catch (error) {
            throw new Error(`URL scraping failed: ${error.message}`);
        }
    }
    
    async getRealTimeData(args) {
        const { query } = args;
        
        if (!this.apiKey) {
            throw new Error('PERPLEXITY_API_KEY environment variable is required');
        }
        
        try {
            const response = await fetch(`${this.baseUrl}/search`, {
                method: 'POST',
                headers: {
                    'Authorization': `Bearer ${this.apiKey}`,
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    query,
                    search_type: 'real_time'
                })
            });
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
            
            const data = await response.json();
            
            return {
                content: [
                    {
                        type: 'text',
                        text: JSON.stringify(data, null, 2)
                    }
                ]
            };
        } catch (error) {
            throw new Error(`Real-time data fetch failed: ${error.message}`);
        }
    }
    
    async run() {
        const transport = new StdioServerTransport();
        await this.server.connect(transport);
        console.error('Perplexity MCP Server started');
    }
}

// Run the server
if (require.main === module) {
    const server = new PerplexityMCPServer();
    server.run().catch(console.error);
}

module.exports = PerplexityMCPServer; 