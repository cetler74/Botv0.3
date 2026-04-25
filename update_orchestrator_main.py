#!/usr/bin/env python3
"""
Script to update the orchestrator's main.py to use the enhanced pair selector
"""

import docker
import os

def update_orchestrator_main():
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
            # Check if we're still in the method (same or less indentation)
            current_indent = len(line) - len(line.lstrip()) if line.strip() else indent_level + 4
            if line.strip() and current_indent <= indent_level:
                method_end = i
                in_method = False
                # Add the new enhanced pair selection method
                new_lines.extend([
                    '        """Dynamically select and persist pairs for all exchanges using enhanced scalping-optimized selection"""',
                    '        assert self.config_manager is not None, "config_manager must be initialized"',
                    '        assert self.database_manager is not None, "database_manager must be initialized"',
                    '        assert self.enhanced_pair_selector is not None, "enhanced_pair_selector must be initialized"',
                    '        ',
                    '        try:',
                    '            logger.info("[ENHANCED] Starting scalping-optimized pair selection...")',
                    '            exchanges = self.config_manager.get_all_exchanges()',
                    '            ',
                    '            for exchange_name in exchanges:',
                    '                # Get config for this exchange',
                    '                exchange_config = self.config_manager.get_exchange_config(exchange_name)',
                    '                base_pair = exchange_config.get("base_currency", "USDC")',
                    '                num_pairs = exchange_config.get("max_pairs", 15)',
                    '',
                    '                logger.info(f"[ENHANCED] Selecting {num_pairs} scalping-optimized pairs for {exchange_name} with {base_pair}")',
                    '                ',
                    '                # Use enhanced pair selector for scalping optimization',
                    '                try:',
                    '                    selected_pairs_with_scores = await self.enhanced_pair_selector.select_top_scalping_pairs(',
                    '                        exchange_name, base_pair, num_pairs',
                    '                    )',
                    '                    ',
                    '                    if selected_pairs_with_scores:',
                    '                        selected_pairs = [pair[0] for pair in selected_pairs_with_scores]',
                    '                        scores = [pair[1] for pair in selected_pairs_with_scores]',
                    '                        ',
                    '                        logger.info(f"[ENHANCED] {exchange_name} selected pairs with scores: {list(zip(selected_pairs, scores))}")',
                    '                        ',
                    '                        # Always forcibly add CRO/USD for cryptocom if not already selected',
                    '                        if exchange_name.lower() == "cryptocom" and "CRO/USD" not in selected_pairs:',
                    '                            selected_pairs.append("CRO/USD")',
                    '                            logger.info(f"[FORCE] Added CRO/USD to cryptocom pairs")',
                    '                        ',
                    '                        # Persist to database',
                    '                        await self.database_manager.save_pairs(',
                    '                            exchange=exchange_name,',
                    '                            pairs=selected_pairs',
                    '                        )',
                    '                        ',
                    '                        # Update in-memory',
                    '                        self.pair_selections[exchange_name] = selected_pairs',
                    '                        ',
                    '                        logger.info(f"[ENHANCED] Successfully selected {len(selected_pairs)} pairs for {exchange_name}")',
                    '                        ',
                    '                    else:',
                    '                        logger.warning(f"[ENHANCED] No suitable pairs found for {exchange_name}, falling back to legacy selector")',
                    '                        await self._fallback_to_legacy_selector(exchange_name, base_pair, num_pairs)',
                    '                        ',
                    '                except Exception as e:',
                    '                    logger.error(f"[ENHANCED] Error in enhanced pair selection for {exchange_name}: {e}")',
                    '                    logger.info(f"[FALLBACK] Falling back to legacy selector for {exchange_name}")',
                    '                    await self._fallback_to_legacy_selector(exchange_name, base_pair, num_pairs)',
                    '',
                    '            logger.info(f"[ENHANCED] Scalping-optimized pair selection complete for {len(exchanges)} exchanges")',
                    '',
                    '        except Exception as e:',
                    '            logger.error(f"Error in dynamic pair selection: {e}")',
                    '            # Fallback to loading from database',
                    '            await self._load_pairs_from_database()',
                    '',
                    '    async def _fallback_to_legacy_selector(self, exchange_name: str, base_pair: str, num_pairs: int) -> None:',
                    '        """Fallback to legacy pair selector if enhanced selector fails"""',
                    '        try:',
                    '            logger.info(f"[FALLBACK] Using legacy selector for {exchange_name}")',
                    '            ',
                    '            # Use the correct legacy PairSelector for this exchange',
                    '            if exchange_name.lower() == "cryptocom":',
                    '                from core.pair_selector import PairSelector',
                    '                selector = PairSelector(base_pair=base_pair, num_pairs=num_pairs)',
                    '            elif exchange_name.lower() == "binance":',
                    '                from core.pair_selector import BinancePairSelector',
                    '                selector = BinancePairSelector(base_pair=base_pair, num_pairs=num_pairs)',
                    '            elif exchange_name.lower() == "bybit":',
                    '                from core.pair_selector import BybitPairSelector',
                    '                selector = BybitPairSelector(base_pair=base_pair, num_pairs=num_pairs)',
                    '            else:',
                    '                logger.warning(f"[FALLBACK] No legacy pair selector implemented for {exchange_name}")',
                    '                return',
                    '',
                    '            result = await selector.select_top_pairs()',
                    '',
                    '            # Always forcibly add CRO/USD for cryptocom',
                    '            if exchange_name.lower() == "cryptocom":',
                    '                if "CRO/USD" not in result["selected_pairs"]:',
                    '                    result["selected_pairs"].append("CRO/USD")',
                    '                logger.info(f"[FALLBACK] cryptocom pairs: {result[\'selected_pairs\']}")',
                    '',
                    '            # Persist to database',
                    '            await self.database_manager.save_pairs(',
                    '                exchange=exchange_name,',
                    '                pairs=result["selected_pairs"]',
                    '            )',
                    '',
                    '            # Update in-memory',
                    '            self.pair_selections[exchange_name] = result["selected_pairs"]',
                    '            ',
                    '            logger.info(f"[FALLBACK] Legacy selector completed for {exchange_name}: {result[\'selected_pairs\']}")',
                    '            ',
                    '        except Exception as e:',
                    '            logger.error(f"[FALLBACK] Error in legacy pair selection for {exchange_name}: {e}")',
                    '',
                    '    async def _load_pairs_from_database(self) -> None:',
                    '        """Load pairs from database as final fallback"""',
                    '        try:',
                    '            logger.info("[FALLBACK] Loading pairs from database...")',
                    '            exchanges = self.config_manager.get_all_exchanges()',
                    '            ',
                    '            for exchange_name in exchanges:',
                    '                exchange_config = self.config_manager.get_exchange_config(exchange_name)',
                    '                max_pairs = exchange_config.get("max_pairs", 10)',
                    '                ',
                    '                # Get pairs from database',
                    '                pairs_data = await self.database_manager.get_pairs(exchange_name)',
                    '                if pairs_data and pairs_data.get("pairs"):',
                    '                    all_pairs = pairs_data["pairs"]',
                    '                    if len(all_pairs) > max_pairs:',
                    '                        self.pair_selections[exchange_name] = all_pairs[:max_pairs]',
                    '                    else:',
                    '                        self.pair_selections[exchange_name] = all_pairs',
                    '                    logger.info(f"[FALLBACK] Loaded {len(self.pair_selections[exchange_name])} pairs for {exchange_name} from database")',
                    '                else:',
                    '                    logger.warning(f"[FALLBACK] No pairs found in database for {exchange_name}")',
                    '                    ',
                    '        except Exception as e:',
                    '            logger.error(f"Error loading pairs from database: {e}")',
                ])
                new_lines.append(line)
            else:
                # Skip the old method content
                continue
        else:
            new_lines.append(line)
    
    # Add the enhanced pair selector initialization to the __init__ method
    new_content = '\n'.join(new_lines)
    
    # Find the __init__ method and add enhanced pair selector initialization
    init_lines = new_content.split('\n')
    final_lines = []
    in_init = False
    init_indent = 0
    
    for i, line in enumerate(init_lines):
        if 'def __init__(self' in line:
            in_init = True
            init_indent = len(line) - len(line.lstrip())
            final_lines.append(line)
            continue
            
        if in_init:
            current_indent = len(line) - len(line.lstrip()) if line.strip() else init_indent + 4
            if line.strip() and current_indent <= init_indent and 'def ' in line:
                # End of __init__ method, add enhanced pair selector initialization
                final_lines.extend([
                    '        # Enhanced pair selection',
                    '        self.enhanced_pair_selector = None',
                ])
                in_init = False
                final_lines.append(line)
            else:
                final_lines.append(line)
        else:
            final_lines.append(line)
    
    # Find the initialize method and add enhanced pair selector initialization
    final_content = '\n'.join(final_lines)
    init_lines = final_content.split('\n')
    final_lines = []
    in_initialize = False
    init_indent = 0
    
    for i, line in enumerate(init_lines):
        if 'async def initialize(self' in line:
            in_initialize = True
            init_indent = len(line) - len(line.lstrip())
            final_lines.append(line)
            continue
            
        if in_initialize:
            current_indent = len(line) - len(line.lstrip()) if line.strip() else init_indent + 4
            if line.strip() and current_indent <= init_indent and ('def ' in line or 'class ' in line):
                # End of initialize method, add enhanced pair selector initialization
                final_lines.extend([
                    '            # Initialize enhanced pair selector',
                    '            from core.enhanced_pair_selector import EnhancedPairSelector',
                    '            self.enhanced_pair_selector = EnhancedPairSelector(self.config_manager)',
                    '            logger.info("Enhanced pair selector initialized")',
                ])
                in_initialize = False
                final_lines.append(line)
            else:
                final_lines.append(line)
        else:
            final_lines.append(line)
    
    # Write the updated content back to the container
    final_content = '\n'.join(final_lines)
    
    # Create a temporary file with the new content
    with open('/tmp/updated_main.py', 'w') as f:
        f.write(final_content)
    
    # Copy the updated file to the container
    with open('/tmp/updated_main.py', 'rb') as f:
        container.put_archive('/app/', {'main.py': f.read()})
    
    print("✅ Successfully updated orchestrator main.py with enhanced pair selector")
    
    # Clean up
    os.remove('/tmp/updated_main.py')

if __name__ == "__main__":
    update_orchestrator_main()
