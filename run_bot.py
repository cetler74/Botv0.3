#!/usr/bin/env python3
"""
Startup script for the Multi-Exchange Trading Bot
Handles initialization, logging, and graceful shutdown
"""

import asyncio
import logging
import sys
import os
from pathlib import Path

# Add the project root to the path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from orchestrator.trading_orchestrator import TradingOrchestrator

def setup_logging():
    """Setup comprehensive logging configuration"""
    # Create logs directory if it doesn't exist
    Path("logs").mkdir(exist_ok=True)
    
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler('logs/trading_bot.log'),
            logging.StreamHandler(sys.stdout)
        ]
    )
    
    # Set specific log levels for noisy libraries
    logging.getLogger('ccxt').setLevel(logging.WARNING)
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('asyncio').setLevel(logging.WARNING)

async def main():
    """Main entry point with error handling"""
    try:
        # Setup logging
        setup_logging()
        logger = logging.getLogger(__name__)
        
        logger.info("Starting Multi-Exchange Trading Bot...")
        logger.info("=" * 50)
        
        # Check if configuration file exists
        config_path = "config/config.yaml"
        if not os.path.exists(config_path):
            logger.error(f"Configuration file not found: {config_path}")
            logger.error("Please create the configuration file before starting the bot")
            sys.exit(1)
        
        # Create and run orchestrator
        orchestrator = TradingOrchestrator(config_path)
        
        # Run the bot
        await orchestrator.run()
        
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt, shutting down gracefully...")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)
    finally:
        logger.info("Trading bot shutdown complete")

if __name__ == "__main__":
    asyncio.run(main()) 