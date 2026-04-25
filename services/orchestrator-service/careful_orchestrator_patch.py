#!/usr/bin/env python3
"""
Careful Orchestrator Patch
Applies order book integration with proper indentation
"""

import re

def apply_careful_patch():
    """
    Apply a careful patch that preserves exact indentation
    """
    print("🔧 Applying Careful Order Book Integration Patch")
    
    # Read the current main.py
    with open('/app/main.py', 'r') as f:
        content = f.read()
    
    # Find the exact _generate_and_store_pairs method with proper indentation
    pattern = r'(\s+)async def _generate_and_store_pairs\(self, client, exchange_name, max_pairs, base_currency\):'
    match = re.search(pattern, content)
    
    if match:
        indent = match.group(1)  # Capture the exact indentation
        print(f"✅ Found _generate_and_store_pairs method with indentation: '{indent}'")
        
        # Find the method body and replace it
        method_pattern = rf'({re.escape(indent)}async def _generate_and_store_pairs\(self, client, exchange_name, max_pairs, base_currency\):.*?)(?={indent}async def|\Z)'
        method_match = re.search(method_pattern, content, re.DOTALL)
        
        if method_match:
            # Create the new method with proper indentation
            new_method = f'''{indent}async def _generate_and_store_pairs(self, client, exchange_name, max_pairs, base_currency):
{indent}    """Generate and store pairs for an exchange with order book-based selection"""
{indent}    try:
{indent}        # First, check if we already have order book-based selections in database
{indent}        pairs_response = await client.get(f"{{database_service_url}}/api/v1/pairs/{{exchange_name}}")
{indent}        if pairs_response.status_code == 200:
{indent}            existing_pairs = pairs_response.json().get('pairs', [])
{indent}            
{indent}            # Check if these are order book-based selections (they should be the ones we created)
{indent}            if existing_pairs and len(existing_pairs) > 0:
{indent}                # Verify these are our order book-based selections by checking if they match our known good pairs
{indent}                known_order_book_pairs = {{
{indent}                    'binance': ['VET/USDC', 'SHIB/USDC', 'SUI/USDC', 'XRP/USDC', 'XLM/USDC'],
{indent}                    'bybit': ['ARB/USDC', 'DOGE/USDC', 'SHIB/USDC', 'USDE/USDC', 'ADA/USDC'],
{indent}                    'cryptocom': ['USDT/USD', 'ADA/USD', 'XLM/USD', 'DOGE/USD', 'HBAR/USD']
{indent}                }}
{indent}                
{indent}                expected_pairs = known_order_book_pairs.get(exchange_name.lower(), [])
{indent}                
{indent}                # If we have order book-based selections, use them
{indent}                if any(pair in existing_pairs for pair in expected_pairs):
{indent}                    logger.info(f"🎯 Using existing order book-based selections for {{exchange_name}}: {{len(existing_pairs)}} pairs")
{indent}                    self.pair_selections[exchange_name] = existing_pairs[:max_pairs]
{indent}                    return
{indent}        
{indent}        # Fallback to legacy method if no order book selections found
{indent}        logger.info(f"📊 No order book selections found for {{exchange_name}}, using legacy method")
{indent}        
{indent}        from core.pair_selector import select_top_pairs_ccxt
{indent}        from core.dynamic_blacklist_manager import blacklist_manager
{indent}        
{indent}        # Use the provided base_currency parameter
{indent}        logger.info(f"Generating pairs for {{exchange_name}} with base currency: {{base_currency}}")
{indent}            
{indent}        # Get active blacklisted pairs
{indent}        blacklisted_pairs = await blacklist_manager.get_active_blacklist()
{indent}        logger.info(f"[Blacklist] Active blacklisted pairs for {{exchange_name}}: {{blacklisted_pairs}}")
{indent}        
{indent}        # Generate new pairs excluding blacklisted ones
{indent}        try:
{indent}            pair_result = await select_top_pairs_ccxt(exchange_name, base_currency, max_pairs * 2, 'spot')  # Get extra pairs
{indent}            
{indent}            # Handle case where pair selector fails
{indent}            if pair_result is None or not isinstance(pair_result, dict) or 'selected_pairs' not in pair_result:
{indent}                logger.error(f"Pair selector failed for {{exchange_name}}, using fallback method")
{indent}                all_selected_pairs = []
{indent}            else:
{indent}                all_selected_pairs = pair_result['selected_pairs']
{indent}                logger.info(f"CCXT pair selector found {{len(all_selected_pairs)}} pairs for {{exchange_name}}")
{indent}                
{indent}        except Exception as selector_error:
{indent}            logger.error(f"Pair selector exception for {{exchange_name}}: {{selector_error}}")
{indent}            all_selected_pairs = []
{indent}        
{indent}        # If pair selector failed or returned insufficient pairs, use predefined high-quality pairs
{indent}        if len(all_selected_pairs) < max_pairs:
{indent}            logger.info(f"Insufficient pairs from selector ({{len(all_selected_pairs)}}), using predefined pairs for {{exchange_name}}")
{indent}            
{indent}            # Predefined high-quality pairs for each exchange
{indent}            predefined_pairs = {{
{indent}                'binance': ['BNB/USDC', 'BTC/USDC', 'ETH/USDC', 'XRP/USDC', 'XLM/USDC', 'LINK/USDC', 'LTC/USDC', 
{indent}                           'TRX/USDC', 'ADA/USDC', 'NEO/USDC', 'DOT/USDC', 'MATIC/USDC', 'UNI/USDC', 'AVAX/USDC', 'ALGO/USDC'],
{indent}                'bybit': ['ETH/USDC', 'BTC/USDC', 'XLM/USDC', 'SOL/USDC', 'XRP/USDC', 'DOT/USDC', 'MATIC/USDC', 
{indent}                         'UNI/USDC', 'AVAX/USDC', 'LINK/USDC', 'LTC/USDC', 'ADA/USDC', 'ALGO/USDC', 'ATOM/USDC', 'FTM/USDC'],
{indent}                'cryptocom': ['CRO/USD', '1INCH/USD', 'AAVE/USD', 'ACA/USD', 'ACH/USD', 'ACT/USD', 'BTC/USD', 'ETH/USD', 
{indent}                             'XRP/USD', 'DOT/USD', 'MATIC/USD', 'UNI/USDC', 'AVAX/USD', 'LINK/USD', 'LTC/USD']
{indent}            }}
{indent}            
{indent}            # Use predefined pairs and merge with selector results
{indent}            exchange_predefined = predefined_pairs.get(exchange_name.lower(), [])
{indent}            
{indent}            # Combine selector results with predefined pairs, avoiding duplicates
{indent}            combined_pairs = list(all_selected_pairs)  # Start with selector results
{indent}            for pair in exchange_predefined:
{indent}                if pair not in combined_pairs and len(combined_pairs) < max_pairs:
{indent}                    combined_pairs.append(pair)
{indent}            
{indent}            all_selected_pairs = combined_pairs[:max_pairs]
{indent}            logger.info(f"Using {{len(all_selected_pairs)}} combined pairs for {{exchange_name}}")
{indent}        
{indent}        # Ensure CRO/USD is always first for crypto.com
{indent}        if exchange_name.lower() == "cryptocom" and "CRO/USD" not in all_selected_pairs:
{indent}            if len(all_selected_pairs) >= max_pairs:
{indent}                all_selected_pairs = all_selected_pairs[:-1]  # Remove last to make room
{indent}            all_selected_pairs.insert(0, "CRO/USD")
{indent}            logger.info(f"✅ Ensured CRO/USD is included for cryptocom: {{all_selected_pairs[:3]}}...")
{indent}        elif exchange_name.lower() == "cryptocom":
{indent}            # Move CRO/USD to first position if it exists
{indent}            if "CRO/USD" in all_selected_pairs:
{indent}                all_selected_pairs.remove("CRO/USD")
{indent}                all_selected_pairs.insert(0, "CRO/USD")
{indent}                logger.info(f"✅ Moved CRO/USD to first position for cryptocom")
{indent}        
{indent}        # Filter out blacklisted pairs
{indent}        selected_pairs = [pair for pair in all_selected_pairs if pair not in blacklisted_pairs][:max_pairs]
{indent}        
{indent}        # Get replacement pairs if we have blacklisted pairs
{indent}        if len(blacklisted_pairs) > 0:
{indent}            replacement_pairs = await blacklist_manager.get_replacement_pairs(exchange_name, max_pairs, blacklisted_pairs)
{indent}            # Merge with selected pairs, avoiding duplicates
{indent}            for replacement in replacement_pairs:
{indent}                if replacement not in selected_pairs and len(selected_pairs) < max_pairs:
{indent}                    selected_pairs.append(replacement)
{indent}                    
{indent}            logger.info(f"[Blacklist] Replaced {{len(blacklisted_pairs)}} blacklisted pairs with {{len(replacement_pairs)}} alternatives")
{indent}        
{indent}        # Always forcibly add CRO/USD for cryptocom if not already present and not blacklisted
{indent}        if exchange_name.lower() == "cryptocom" and "CRO/USD" not in blacklisted_pairs:
{indent}            logger.info(f"[DEBUG] cryptocom pairs before force-add: {{selected_pairs}}")
{indent}            if "CRO/USD" not in selected_pairs:
{indent}                selected_pairs.append("CRO/USD")
{indent}                logger.info(f"[FORCE] Added CRO/USD to cryptocom pairs")
{indent}            logger.info(f"[FORCE] cryptocom pairs to be saved: {{selected_pairs}}")
{indent}        
{indent}        # Store pairs in database
{indent}        db_response = await client.post(f"{{database_service_url}}/api/v1/pairs/{{exchange_name}}", json=selected_pairs)
{indent}        if db_response.status_code in [200, 201]:
{indent}            logger.info(f"Successfully stored {{len(selected_pairs)}} pairs for {{exchange_name}} in database")
{indent}            self.pair_selections[exchange_name] = selected_pairs
{indent}        else:
{indent}            logger.warning(f"Failed to store pairs for {{exchange_name}} in database: {{db_response.status_code}}")
{indent}            self.pair_selections[exchange_name] = selected_pairs  # Use them anyway
{indent}            
{indent}        logger.info(f"Exchange {{exchange_name}}: {{len(self.pair_selections[exchange_name])}}/{{max_pairs}} pairs selected and stored")
{indent}    except Exception as pair_error:
{indent}        logger.error(f"Error generating pairs for {{exchange_name}}: {{pair_error}}")
{indent}        self.pair_selections[exchange_name] = []
'''
            
            # Replace the method
            new_content = content.replace(method_match.group(0), new_method)
            
            # Write the patched content
            with open('/app/main.py', 'w') as f:
                f.write(new_content)
            
            print("✅ Successfully applied careful order book integration patch")
            return True
        else:
            print("❌ Could not find method body")
            return False
    else:
        print("❌ Could not find _generate_and_store_pairs method")
        return False

if __name__ == "__main__":
    apply_careful_patch()
