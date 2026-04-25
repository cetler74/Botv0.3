#!/usr/bin/env python3
"""
Script to update the orchestrator's main.py to use the enhanced pair selector
"""

import docker
import os

def update_main_with_enhanced_selector():
    """Update the orchestrator's main.py to use enhanced pair selector"""
    
    # Connect to Docker
    client = docker.from_env()
    
    # Get the orchestrator container
    container = client.containers.get('trading-bot-orchestrator')
    
    # Read the current main.py
    result = container.exec_run('cat /app/main.py')
    current_content = result.output.decode('utf-8')
    
    # Find the _initialize_pair_selections method and replace it
    lines = current_content.split('\n')
    new_lines = []
    in_method = False
    method_start = -1
    method_end = -1
    indent_level = 0
    
    for i, line in enumerate(lines):
        if 'async def _initialize_pair_selections(self) -> None:' in line:
            in_method = True
            method_start = i
            indent_level = len(line) - len(line.lstrip())
            new_lines.append(line)
            continue
            
        if in_method:
            # Check if we've reached the end of the method (next method or class)
            current_indent = len(line) - len(line.lstrip()) if line.strip() else indent_level + 1
            if line.strip() and current_indent <= indent_level and not line.startswith(' ' * (indent_level + 1)):
                method_end = i
                in_method = False
                # Add the new enhanced pair selector implementation
                new_lines.extend([
                    '        """Initialize pair selections for all exchanges using enhanced selector"""',
                    '        try:',
                    '            # Import enhanced pair selector',
                    '            from core.enhanced_pair_selector import EnhancedPairSelector',
                    '            from core.config_manager import ConfigManager',
                    '',
                    '            # Initialize config manager and enhanced pair selector',
                    '            config_manager = ConfigManager()',
                    '            config_manager.config_service_url = config_service_url',
                    '            self.enhanced_pair_selector = EnhancedPairSelector(config_manager)',
                    '',
                    '            exchanges = []',
                    '            async with httpx.AsyncClient(timeout=60.0) as client:',
                    '                response = await client.get(f"{config_service_url}/api/v1/config/exchanges/list")',
                    '                response.raise_for_status()',
                    '                exchanges = response.json()[\'exchanges\']',
                    '',
                    '                for exchange_name in exchanges:',
                    '                    try:',
                    '                        # Get exchange configuration',
                    '                        exchange_config_response = await client.get(f"{config_service_url}/api/v1/config/exchanges/{exchange_name}")',
                    '                        if exchange_config_response.status_code == 200:',
                    '                            exchange_config = exchange_config_response.json()',
                    '                            max_pairs = exchange_config.get(\'max_pairs\', 15)',
                    '                            base_currency = exchange_config.get(\'base_currency\', \'USDC\')',
                    '                            logger.info(f"[DEBUG] {exchange_name} config from service: {exchange_config}")',
                    '                            logger.info(f"[DEBUG] {exchange_name} max_pairs extracted: {max_pairs}")',
                    '                        else:',
                    '                            max_pairs = 15  # Default fallback',
                    '                            base_currency = \'USDC\'  # Default fallback',
                    '                            logger.warning(f"[DEBUG] {exchange_name} config service error: {exchange_config_response.status_code}, using fallback max_pairs: {max_pairs}, base_currency: {base_currency}")',
                    '',
                    '                        # Use enhanced pair selector to select top scalping pairs',
                    '                        logger.info(f"🎯 Using Enhanced Pair Selector for {exchange_name}")',
                    '                        selected_pairs_with_scores = await self.enhanced_pair_selector.select_top_scalping_pairs(',
                    '                            exchange_name, base_currency, max_pairs',
                    '                        )',
                    '',
                    '                        if selected_pairs_with_scores:',
                    '                            selected_pairs = [pair[0] for pair in selected_pairs_with_scores]',
                    '                            scores = [pair[1] for pair in selected_pairs_with_scores]',
                    '                            logger.info(f"✅ Enhanced selector selected {len(selected_pairs)} pairs for {exchange_name}: {list(zip(selected_pairs, scores))}")',
                    '                            self.pair_selections[exchange_name] = selected_pairs',
                    '                        else:',
                    '                            logger.warning(f"⚠️ Enhanced selector found no suitable pairs for {exchange_name}, falling back to legacy selector")',
                    '                            await self._fallback_to_legacy_selector(exchange_name, base_currency, max_pairs)',
                    '',
                    '                    except Exception as e:',
                    '                        logger.error(f"❌ Error with enhanced selector for {exchange_name}: {e}")',
                    '                        logger.info(f"🔄 Falling back to legacy selector for {exchange_name}")',
                    '                        await self._fallback_to_legacy_selector(exchange_name, base_currency, max_pairs)',
                    '',
                    '            logger.info(f"Initialized pair selections for {len(self.pair_selections)} exchanges")',
                    '',
                    '        except Exception as e:',
                    '            logger.error(f"Failed to initialize pair selections: {e}")',
                    '            # Fallback to legacy method',
                    '            await self._fallback_to_legacy_selector_all()',
                    '',
                    '    async def _fallback_to_legacy_selector(self, exchange_name: str, base_currency: str, max_pairs: int) -> None:',
                    '        """Fallback to legacy pair selection method"""',
                    '        try:',
                    '            # Get latest pair selection from database',
                    '            async with httpx.AsyncClient(timeout=60.0) as client:',
                    '                pairs_response = await client.get(f"{database_service_url}/api/v1/pairs/{exchange_name}")',
                    '                if pairs_response.status_code == 200:',
                    '                    pairs_data = pairs_response.json()',
                    '                    all_pairs = pairs_data.get(\'pairs\', [])',
                    '                    ',
                    '                    if all_pairs and len(all_pairs) > 0:',
                    '                        if len(all_pairs) > max_pairs:',
                    '                            logger.warning(f"Exchange {exchange_name} has {len(all_pairs)} pairs, limiting to {max_pairs}")',
                    '                            self.pair_selections[exchange_name] = all_pairs[:max_pairs]',
                    '                        else:',
                    '                            self.pair_selections[exchange_name] = all_pairs',
                    '                        logger.info(f"Exchange {exchange_name}: {len(self.pair_selections[exchange_name])}/{max_pairs} pairs selected from database")',
                    '                    else:',
                    '                        logger.warning(f"No pairs found in database for {exchange_name}")',
                    '                        self.pair_selections[exchange_name] = []',
                    '                else:',
                    '                    logger.error(f"Failed to get pairs from database for {exchange_name}: {pairs_response.status_code}")',
                    '                    self.pair_selections[exchange_name] = []',
                    '        except Exception as e:',
                    '            logger.error(f"Fallback selector failed for {exchange_name}: {e}")',
                    '            self.pair_selections[exchange_name] = []',
                    '',
                    '    async def _fallback_to_legacy_selector_all(self) -> None:',
                    '        """Fallback to legacy pair selection for all exchanges"""',
                    '        try:',
                    '            exchanges = []',
                    '            async with httpx.AsyncClient(timeout=60.0) as client:',
                    '                response = await client.get(f"{config_service_url}/api/v1/config/exchanges/list")',
                    '                response.raise_for_status()',
                    '                exchanges = response.json()[\'exchanges\']',
                    '',
                    '                for exchange_name in exchanges:',
                    '                    exchange_config_response = await client.get(f"{config_service_url}/api/v1/config/exchanges/{exchange_name}")',
                    '                    if exchange_config_response.status_code == 200:',
                    '                        exchange_config = exchange_config_response.json()',
                    '                        max_pairs = exchange_config.get(\'max_pairs\', 15)',
                    '                        base_currency = exchange_config.get(\'base_currency\', \'USDC\')',
                    '                    else:',
                    '                        max_pairs = 15',
                    '                        base_currency = \'USDC\'',
                    '',
                    '                    await self._fallback_to_legacy_selector(exchange_name, base_currency, max_pairs)',
                    '',
                    '        except Exception as e:',
                    '            logger.error(f"Complete fallback failed: {e}")',
                    '            # Set empty selections as last resort',
                    '            for exchange_name in exchanges:',
                    '                self.pair_selections[exchange_name] = []',
                    '',
                    line
                ])
                continue
            else:
                # Skip the old method content
                continue
        else:
            new_lines.append(line)
    
    # Write the updated content back to the container
    updated_content = '\n'.join(new_lines)
    
    # Write to a temporary file first
    with open('/tmp/updated_main.py', 'w') as f:
        f.write(updated_content)
    
    # Copy the updated file to the container
    with open('/tmp/updated_main.py', 'rb') as f:
        container.put_archive('/app/', {'main.py': f.read()})
    
    print("✅ Successfully updated main.py with enhanced pair selector")
    
    # Clean up
    os.remove('/tmp/updated_main.py')

if __name__ == "__main__":
    update_main_with_enhanced_selector()
