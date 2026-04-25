#!/usr/bin/env python3
"""
Orchestrator Order Book Integration
Modifies the orchestrator to use existing order book-based selections from database
"""

import re

def create_order_book_integration_patch():
    """
    Create a patch for the orchestrator's _generate_and_store_pairs method
    to use existing order book-based selections from database
    """
    
    patch_code = '''
    async def _generate_and_store_pairs(self, client, exchange_name, max_pairs, base_currency):
        """Generate and store pairs for an exchange with order book-based selection"""
        try:
            # First, check if we already have order book-based selections in database
            pairs_response = await client.get(f"{database_service_url}/api/v1/pairs/{exchange_name}")
            if pairs_response.status_code == 200:
                existing_pairs = pairs_response.json().get('pairs', [])
                
                # Check if these are order book-based selections (they should be the ones we created)
                if existing_pairs and len(existing_pairs) > 0:
                    # Verify these are our order book-based selections by checking if they match our known good pairs
                    known_order_book_pairs = {
                        'binance': ['VET/USDC', 'SHIB/USDC', 'SUI/USDC', 'XRP/USDC', 'XLM/USDC'],
                        'bybit': ['ARB/USDC', 'DOGE/USDC', 'SHIB/USDC', 'USDE/USDC', 'ADA/USDC'],
                        'cryptocom': ['USDT/USD', 'ADA/USD', 'XLM/USD', 'DOGE/USD', 'HBAR/USD']
                    }
                    
                    expected_pairs = known_order_book_pairs.get(exchange_name.lower(), [])
                    
                    # If we have order book-based selections, use them
                    if any(pair in existing_pairs for pair in expected_pairs):
                        logger.info(f"🎯 Using existing order book-based selections for {exchange_name}: {len(existing_pairs)} pairs")
                        self.pair_selections[exchange_name] = existing_pairs[:max_pairs]
                        return
            
            # Fallback to legacy method if no order book selections found
            logger.info(f"📊 No order book selections found for {exchange_name}, using legacy method")
            
            from core.pair_selector import select_top_pairs_ccxt
            from core.dynamic_blacklist_manager import blacklist_manager
            
            # Use the provided base_currency parameter
            logger.info(f"Generating pairs for {exchange_name} with base currency: {base_currency}")
                
            # Get active blacklisted pairs
            blacklisted_pairs = await blacklist_manager.get_active_blacklist()
            logger.info(f"[Blacklist] Active blacklisted pairs for {exchange_name}: {blacklisted_pairs}")
            
            # Generate new pairs excluding blacklisted ones
            try:
                pair_result = await select_top_pairs_ccxt(exchange_name, base_currency, max_pairs * 2, 'spot')  # Get extra pairs
                
                # Handle case where pair selector fails
                if pair_result is None or not isinstance(pair_result, dict) or 'selected_pairs' not in pair_result:
                    logger.error(f"Pair selector failed for {exchange_name}, using fallback method")
                    all_selected_pairs = []
                else:
                    all_selected_pairs = pair_result['selected_pairs']
                    logger.info(f"CCXT pair selector found {len(all_selected_pairs)} pairs for {exchange_name}")
                    
            except Exception as selector_error:
                logger.error(f"Pair selector exception for {exchange_name}: {selector_error}")
                all_selected_pairs = []
            
            # If pair selector failed or returned insufficient pairs, use predefined high-quality pairs
            if len(all_selected_pairs) < max_pairs:
                logger.info(f"Insufficient pairs from selector ({len(all_selected_pairs)}), using predefined pairs for {exchange_name}")
                
                # Predefined high-quality pairs for each exchange
                predefined_pairs = {
                    'binance': ['BNB/USDC', 'BTC/USDC', 'ETH/USDC', 'XRP/USDC', 'XLM/USDC', 'LINK/USDC', 'LTC/USDC', 
                               'TRX/USDC', 'ADA/USDC', 'NEO/USDC', 'DOT/USDC', 'MATIC/USDC', 'UNI/USDC', 'AVAX/USDC', 'ALGO/USDC'],
                    'bybit': ['ETH/USDC', 'BTC/USDC', 'XLM/USDC', 'SOL/USDC', 'XRP/USDC', 'DOT/USDC', 'MATIC/USDC', 
                             'UNI/USDC', 'AVAX/USDC', 'LINK/USDC', 'LTC/USDC', 'ADA/USDC', 'ALGO/USDC', 'ATOM/USDC', 'FTM/USDC'],
                    'cryptocom': ['CRO/USD', '1INCH/USD', 'AAVE/USD', 'ACA/USD', 'ACH/USD', 'ACT/USD', 'BTC/USD', 'ETH/USD', 
                                 'XRP/USD', 'DOT/USD', 'MATIC/USD', 'UNI/USD', 'AVAX/USD', 'LINK/USD', 'LTC/USD']
                }
                
                # Use predefined pairs and merge with selector results
                exchange_predefined = predefined_pairs.get(exchange_name.lower(), [])
                
                # Combine selector results with predefined pairs, avoiding duplicates
                combined_pairs = list(all_selected_pairs)  # Start with selector results
                for pair in exchange_predefined:
                    if pair not in combined_pairs and len(combined_pairs) < max_pairs:
                        combined_pairs.append(pair)
                
                all_selected_pairs = combined_pairs[:max_pairs]
                logger.info(f"Using {len(all_selected_pairs)} combined pairs for {exchange_name}")
            
            # Ensure CRO/USD is always first for crypto.com
            if exchange_name.lower() == "cryptocom" and "CRO/USD" not in all_selected_pairs:
                if len(all_selected_pairs) >= max_pairs:
                    all_selected_pairs = all_selected_pairs[:-1]  # Remove last to make room
                all_selected_pairs.insert(0, "CRO/USD")
                logger.info(f"✅ Ensured CRO/USD is included for cryptocom: {all_selected_pairs[:3]}...")
            elif exchange_name.lower() == "cryptocom":
                # Move CRO/USD to first position if it exists
                if "CRO/USD" in all_selected_pairs:
                    all_selected_pairs.remove("CRO/USD")
                    all_selected_pairs.insert(0, "CRO/USD")
                    logger.info(f"✅ Moved CRO/USD to first position for cryptocom")
            
            # Filter out blacklisted pairs
            selected_pairs = [pair for pair in all_selected_pairs if pair not in blacklisted_pairs][:max_pairs]
            
            # Get replacement pairs if we have blacklisted pairs
            if len(blacklisted_pairs) > 0:
                replacement_pairs = await blacklist_manager.get_replacement_pairs(exchange_name, max_pairs, blacklisted_pairs)
                # Merge with selected pairs, avoiding duplicates
                for replacement in replacement_pairs:
                    if replacement not in selected_pairs and len(selected_pairs) < max_pairs:
                        selected_pairs.append(replacement)
                        
                logger.info(f"[Blacklist] Replaced {len(blacklisted_pairs)} blacklisted pairs with {len(replacement_pairs)} alternatives")
            
            # Always forcibly add CRO/USD for cryptocom if not already present and not blacklisted
            if exchange_name.lower() == "cryptocom" and "CRO/USD" not in blacklisted_pairs:
                logger.info(f"[DEBUG] cryptocom pairs before force-add: {selected_pairs}")
                if "CRO/USD" not in selected_pairs:
                    selected_pairs.append("CRO/USD")
                    logger.info(f"[FORCE] Added CRO/USD to cryptocom pairs")
                logger.info(f"[FORCE] cryptocom pairs to be saved: {selected_pairs}")
            
            # Store pairs in database
            db_response = await client.post(f"{database_service_url}/api/v1/pairs/{exchange_name}", json=selected_pairs)
            if db_response.status_code in [200, 201]:
                logger.info(f"Successfully stored {len(selected_pairs)} pairs for {exchange_name} in database")
                self.pair_selections[exchange_name] = selected_pairs
            else:
                logger.warning(f"Failed to store pairs for {exchange_name} in database: {db_response.status_code}")
                self.pair_selections[exchange_name] = selected_pairs  # Use them anyway
                
            logger.info(f"Exchange {exchange_name}: {len(self.pair_selections[exchange_name])}/{max_pairs} pairs selected and stored")
        except Exception as pair_error:
            logger.error(f"Error generating pairs for {exchange_name}: {pair_error}")
            self.pair_selections[exchange_name] = []
'''
    
    return patch_code

def apply_orchestrator_patch():
    """
    Apply the patch to the orchestrator's main.py file
    """
    print("🔧 Applying Order Book Integration Patch to Orchestrator")
    
    # Read the current main.py
    with open('/app/main.py', 'r') as f:
        content = f.read()
    
    # Find the _generate_and_store_pairs method
    pattern = r'async def _generate_and_store_pairs\(self, client, exchange_name, max_pairs, base_currency\):.*?(?=async def|\Z)'
    match = re.search(pattern, content, re.DOTALL)
    
    if match:
        print("✅ Found _generate_and_store_pairs method")
        
        # Replace with our patched version
        patch_code = create_order_book_integration_patch()
        new_content = content.replace(match.group(0), patch_code)
        
        # Write the patched content
        with open('/app/main.py', 'w') as f:
            f.write(new_content)
        
        print("✅ Successfully applied order book integration patch")
        return True
    else:
        print("❌ Could not find _generate_and_store_pairs method")
        return False

if __name__ == "__main__":
    apply_orchestrator_patch()
