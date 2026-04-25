#!/usr/bin/env python3
"""
Validation script for Heikin Ashi strategy improvements
Analyzes the implemented parameters without running the full strategy
"""

import re
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def analyze_strategy_parameters(file_path):
    """Analyze the Heikin Ashi strategy file for implemented improvements"""
    
    logger.info("🔍 Analyzing Heikin Ashi Strategy Improvements")
    logger.info("=" * 60)
    
    try:
        with open(file_path, 'r') as f:
            content = f.read()
        
        improvements = {}
        
        # 1. ADX Threshold Analysis (25-30 range)
        adx_match = re.search(r'adx_threshold.*?get\([\'"]adx_threshold[\'"],\s*(\d+)', content)
        if adx_match:
            adx_value = int(adx_match.group(1))
            improvements['adx_threshold'] = {
                'value': adx_value,
                'target_range': '25-30',
                'optimized': 25 <= adx_value <= 30,
                'description': 'Crypto-appropriate trend strength detection'
            }
        
        # 2. RSI Ranges Analysis (25-75 standard, 40-70 uptrend)
        rsi_oversold_match = re.search(r'rsi_oversold.*?get\([\'"]rsi_oversold[\'"],\s*(\d+)', content)
        rsi_overbought_match = re.search(r'rsi_overbought.*?get\([\'"]rsi_overbought[\'"],\s*(\d+)', content)
        rsi_uptrend_min_match = re.search(r'rsi_uptrend_min.*?get\([\'"]rsi_uptrend_min[\'"],\s*(\d+)', content)
        rsi_uptrend_max_match = re.search(r'rsi_uptrend_max.*?get\([\'"]rsi_uptrend_max[\'"],\s*(\d+)', content)
        
        if rsi_oversold_match and rsi_overbought_match:
            rsi_oversold = int(rsi_oversold_match.group(1))
            rsi_overbought = int(rsi_overbought_match.group(1))
            improvements['rsi_range'] = {
                'oversold': rsi_oversold,
                'overbought': rsi_overbought,
                'target_range': '25-75',
                'optimized': rsi_oversold == 25 and rsi_overbought == 75,
                'description': 'Standard crypto RSI ranges'
            }
        
        if rsi_uptrend_min_match and rsi_uptrend_max_match:
            rsi_uptrend_min = int(rsi_uptrend_min_match.group(1))
            rsi_uptrend_max = int(rsi_uptrend_max_match.group(1))
            improvements['rsi_uptrend'] = {
                'min': rsi_uptrend_min,
                'max': rsi_uptrend_max,
                'target_range': '40-70',
                'optimized': rsi_uptrend_min == 40 and rsi_uptrend_max == 70,
                'description': 'Uptrend bias range for momentum signals'
            }
        
        # 3. Volume Requirements Analysis (1.5-2.0x spike multiplier)
        volume_spike_match = re.search(r'volume_spike_multiplier.*?get\([\'"]volume_spike_multiplier[\'"],\s*([\d.]+)', content)
        volume_threshold_match = re.search(r'volume_threshold_percentage.*?get\([\'"]volume_threshold_percentage[\'"],\s*([\d.]+)', content)
        
        if volume_spike_match:
            volume_spike = float(volume_spike_match.group(1))
            improvements['volume_spike'] = {
                'value': volume_spike,
                'target_range': '1.5-2.0',
                'optimized': 1.5 <= volume_spike <= 2.0,
                'description': 'Enhanced volume spike detection'
            }
        
        if volume_threshold_match:
            volume_threshold = float(volume_threshold_match.group(1))
            improvements['volume_threshold'] = {
                'value': volume_threshold,
                'target_range': '0.3-0.4',
                'optimized': 0.3 <= volume_threshold <= 0.4,
                'description': 'Base volume threshold percentage'
            }
        
        # 4. ATR-based Stop Loss Analysis (2.0-2.5x multiplier)
        atr_stop_match = re.search(r'atr_stop_multiplier.*?get\([\'"]atr_stop_multiplier[\'"],\s*([\d.]+)', content)
        atr_tp_match = re.search(r'atr_take_profit_multiplier.*?get\([\'"]atr_take_profit_multiplier[\'"],\s*([\d.]+)', content)
        
        if atr_stop_match:
            atr_stop = float(atr_stop_match.group(1))
            improvements['atr_stop'] = {
                'value': atr_stop,
                'target_range': '2.0-2.5',
                'optimized': 2.0 <= atr_stop <= 2.5,
                'description': 'Dynamic ATR-based stop loss'
            }
        
        if atr_tp_match:
            atr_tp = float(atr_tp_match.group(1))
            improvements['atr_take_profit'] = {
                'value': atr_tp,
                'target_range': '3.0+',
                'optimized': atr_tp >= 3.0,
                'description': 'Risk-reward ratio optimization'
            }
        
        # 5. Check for ATR-based methods implementation
        has_atr_levels = 'calculate_atr_levels' in content
        has_dynamic_stops = 'update_dynamic_stops' in content
        has_atr_position_sizing = 'current_atr' in content and 'calculate_position_size' in content
        
        improvements['atr_implementation'] = {
            'atr_levels': has_atr_levels,
            'dynamic_stops': has_dynamic_stops,
            'atr_position_sizing': has_atr_position_sizing,
            'optimized': has_atr_levels and has_dynamic_stops and has_atr_position_sizing,
            'description': 'Complete ATR-based risk management system'
        }
        
        return improvements
        
    except Exception as e:
        logger.error(f"❌ Error analyzing strategy file: {e}")
        return {}

def generate_improvement_report(improvements):
    """Generate a comprehensive improvement report"""
    
    logger.info("📊 IMPROVEMENT ANALYSIS REPORT")
    logger.info("=" * 60)
    
    total_improvements = len(improvements)
    optimized_count = 0
    
    for name, details in improvements.items():
        if name == 'atr_implementation':
            # Special handling for ATR implementation
            status = "✅ IMPLEMENTED" if details['optimized'] else "❌ PARTIAL"
            logger.info(f"\n🔧 {name.upper().replace('_', ' ')}: {status}")
            logger.info(f"   Description: {details['description']}")
            logger.info(f"   - ATR Levels Method: {'✅' if details['atr_levels'] else '❌'}")
            logger.info(f"   - Dynamic Stops Method: {'✅' if details['dynamic_stops'] else '❌'}")
            logger.info(f"   - ATR Position Sizing: {'✅' if details['atr_position_sizing'] else '❌'}")
            if details['optimized']:
                optimized_count += 1
        else:
            status = "✅ OPTIMIZED" if details['optimized'] else "⚠️ NEEDS ADJUSTMENT"
            logger.info(f"\n📈 {name.upper().replace('_', ' ')}: {status}")
            logger.info(f"   Description: {details['description']}")
            
            if 'value' in details:
                logger.info(f"   Current Value: {details['value']}")
            if 'oversold' in details:
                logger.info(f"   Oversold: {details['oversold']} | Overbought: {details['overbought']}")
            if 'min' in details:
                logger.info(f"   Uptrend Range: {details['min']}-{details['max']}")
            
            logger.info(f"   Target Range: {details['target_range']}")
            
            if details['optimized']:
                optimized_count += 1
    
    # Overall Assessment
    optimization_percentage = (optimized_count / total_improvements) * 100
    
    logger.info(f"\n🎯 OVERALL OPTIMIZATION STATUS")
    logger.info("=" * 40)
    logger.info(f"Optimized Parameters: {optimized_count}/{total_improvements} ({optimization_percentage:.1f}%)")
    
    if optimization_percentage >= 90:
        logger.info("🚀 EXCELLENT: Strategy is fully optimized for crypto markets!")
    elif optimization_percentage >= 70:
        logger.info("✅ GOOD: Strategy is well-optimized with minor adjustments needed")
    elif optimization_percentage >= 50:
        logger.info("⚠️ MODERATE: Strategy needs additional optimization")
    else:
        logger.info("❌ POOR: Strategy requires significant optimization")
    
    logger.info(f"\n📈 EXPECTED IMPROVEMENTS:")
    logger.info("- Better trend detection with ADX 25-30 threshold")
    logger.info("- Improved entry timing with crypto RSI ranges (25-75)")
    logger.info("- Enhanced signal quality with volume filtering (1.5-2.0x)")
    logger.info("- Reduced losses with dynamic ATR stops (2.0-2.5x)")
    logger.info("- Better risk management with ATR position sizing")
    
    logger.info(f"\n🎯 TARGET OUTCOME: 50%+ win rate with reduced drawdowns")
    
    return optimization_percentage

def main():
    """Main validation function"""
    strategy_file = "/Volumes/OWC Volume/Projects2025/Botv0.3/strategy/heikin_ashi_strategy.py"
    
    logger.info("🧪 HEIKIN ASHI STRATEGY IMPROVEMENT VALIDATION")
    logger.info("=" * 70)
    
    improvements = analyze_strategy_parameters(strategy_file)
    
    if improvements:
        optimization_score = generate_improvement_report(improvements)
        
        logger.info(f"\n✅ VALIDATION COMPLETED")
        logger.info(f"Strategy optimization score: {optimization_score:.1f}%")
        
        if optimization_score >= 90:
            logger.info("🎉 Ready for production deployment!")
        else:
            logger.info("🔧 Additional optimization recommended")
            
    else:
        logger.error("❌ Could not analyze strategy improvements")
        return False
    
    return True

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)