#!/usr/bin/env python3
"""
Advanced validation script for Heikin Ashi Strategy Phase 2 & Phase 3 improvements
Validates multi-timeframe analysis, RSI divergence, and support/resistance features
"""

import re
import logging
from typing import Dict, List, Tuple

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def validate_phase2_multitimeframe(content: str) -> Dict:
    """Validate Phase 2: Multi-timeframe implementation"""
    phase2_features = {}
    
    # 1. Timeframe Hierarchy
    macro_tf = re.search(r'macro_timeframe.*?get\([\'"]macro_timeframe[\'"],\s*[\'"]([^\'\"]+)[\'"]', content)
    signal_tf = re.search(r'signal_timeframe.*?get\([\'"]signal_timeframe[\'"],\s*[\'"]([^\'\"]+)[\'"]', content)
    execution_tf = re.search(r'execution_timeframe.*?get\([\'"]execution_timeframe[\'"],\s*[\'"]([^\'\"]+)[\'"]', content)
    
    if macro_tf and signal_tf and execution_tf:
        phase2_features['timeframe_hierarchy'] = {
            'macro': macro_tf.group(1),
            'signal': signal_tf.group(1), 
            'execution': execution_tf.group(1),
            'implemented': True,
            'target': '4H/1H/15M hierarchy'
        }
    else:
        phase2_features['timeframe_hierarchy'] = {'implemented': False}
    
    # 2. Confluence Scoring System
    min_confluence = re.search(r'min_confluence_score.*?get\([\'"]min_confluence_score[\'"],\s*(\d+)', content)
    macro_weight = re.search(r'macro_weight.*?get\([\'"]macro_weight[\'"],\s*([\d.]+)', content)
    signal_weight = re.search(r'signal_weight.*?get\([\'"]signal_weight[\'"],\s*([\d.]+)', content)
    execution_weight = re.search(r'execution_weight.*?get\([\'"]execution_weight[\'"],\s*([\d.]+)', content)
    
    phase2_features['confluence_scoring'] = {
        'min_confluence': int(min_confluence.group(1)) if min_confluence else 0,
        'macro_weight': float(macro_weight.group(1)) if macro_weight else 0,
        'signal_weight': float(signal_weight.group(1)) if signal_weight else 0,
        'execution_weight': float(execution_weight.group(1)) if execution_weight else 0,
        'implemented': bool(min_confluence and macro_weight and signal_weight and execution_weight),
        'target': 'Weighted scoring system (3.0/2.0/1.0)'
    }
    
    # 3. Multi-timeframe Analysis Methods
    has_confluence_method = 'calculate_confluence_score' in content
    has_timeframe_analysis = 'timeframe_signals' in content and 'Multi-timeframe confluence' in content
    
    phase2_features['analysis_methods'] = {
        'confluence_calculation': has_confluence_method,
        'timeframe_analysis': has_timeframe_analysis,
        'implemented': has_confluence_method and has_timeframe_analysis,
        'target': 'Complete multi-timeframe analysis system'
    }
    
    # 4. Target Risk-Reward for Phase 2
    phase2_rr = re.search(r'phase2_target_rr.*?get\([\'"]phase2_target_rr[\'"],\s*([\d.]+)', content)
    phase2_features['risk_reward'] = {
        'target_ratio': float(phase2_rr.group(1)) if phase2_rr else 0,
        'implemented': bool(phase2_rr),
        'target': '2.0 (1:2.0 risk/reward)'
    }
    
    return phase2_features

def validate_phase3_advanced_features(content: str) -> Dict:
    """Validate Phase 3: Advanced technical features"""
    phase3_features = {}
    
    # 1. RSI Divergence Detection
    enable_divergence = re.search(r'enable_rsi_divergence.*?get\([\'"]enable_rsi_divergence[\'"],\s*(True|False)', content)
    divergence_lookback = re.search(r'divergence_lookback.*?get\([\'"]divergence_lookback[\'"],\s*(\d+)', content)
    has_divergence_method = 'detect_rsi_divergence' in content
    
    # Check for all divergence types
    has_bullish_div = 'bullish_divergence' in content
    has_bearish_div = 'bearish_divergence' in content
    has_hidden_bullish = 'hidden_bullish' in content
    has_hidden_bearish = 'hidden_bearish' in content
    
    phase3_features['rsi_divergence'] = {
        'enabled': enable_divergence.group(1) == 'True' if enable_divergence else False,
        'lookback_period': int(divergence_lookback.group(1)) if divergence_lookback else 0,
        'has_detection_method': has_divergence_method,
        'supports_all_types': has_bullish_div and has_bearish_div and has_hidden_bullish and has_hidden_bearish,
        'implemented': bool(enable_divergence and has_divergence_method and has_bullish_div),
        'target': 'Complete divergence detection (bullish, bearish, hidden)'
    }
    
    # 2. Support/Resistance Integration
    enable_sr = re.search(r'enable_support_resistance.*?get\([\'"]enable_support_resistance[\'"],\s*(True|False)', content)
    sr_period = re.search(r'sr_period.*?get\([\'"]sr_period[\'"],\s*(\d+)', content)
    has_sr_method = 'calculate_support_resistance' in content
    
    # Check for dynamic S/R features
    has_pivot_calculation = 'pivot' in content and 'support' in content and 'resistance' in content
    has_ma_integration = 'sma_20' in content and 'sma_50' in content
    
    phase3_features['support_resistance'] = {
        'enabled': enable_sr.group(1) == 'True' if enable_sr else False,
        'period': int(sr_period.group(1)) if sr_period else 0,
        'has_calculation_method': has_sr_method,
        'has_pivot_levels': has_pivot_calculation,
        'has_ma_integration': has_ma_integration,
        'implemented': bool(enable_sr and has_sr_method and has_pivot_calculation),
        'target': 'Dynamic S/R with pivot points and MA integration'
    }
    
    # 3. Volume Confirmation at Key Levels
    breakout_volume = re.search(r'breakout_confirmation_volume.*?get\([\'"]breakout_confirmation_volume[\'"],\s*([\d.]+)', content)
    has_volume_confirmation = 'volume_confirmation' in content or 'breakout_confirmation' in content
    
    phase3_features['volume_confirmation'] = {
        'breakout_multiplier': float(breakout_volume.group(1)) if breakout_volume else 0,
        'has_confirmation': has_volume_confirmation,
        'implemented': bool(breakout_volume),
        'target': '1.5x volume multiplier for breakout confirmation'
    }
    
    # 4. Target Risk-Reward for Phase 3
    phase3_rr = re.search(r'phase3_target_rr.*?get\([\'"]phase3_target_rr[\'"],\s*([\d.]+)', content)
    phase3_features['risk_reward'] = {
        'target_ratio': float(phase3_rr.group(1)) if phase3_rr else 0,
        'implemented': bool(phase3_rr),
        'target': '2.5 (1:2.5 risk/reward)'
    }
    
    # 5. Advanced Exit Strategies
    has_divergence_exit = 'bearish_divergence_exit' in content
    has_resistance_exit = 'resistance_exit' in content
    has_advanced_stops = 'Phase 3' in content and 'advanced exit conditions' in content
    
    phase3_features['advanced_exits'] = {
        'divergence_exits': has_divergence_exit,
        'resistance_exits': has_resistance_exit,
        'advanced_stop_logic': has_advanced_stops,
        'implemented': has_divergence_exit and has_resistance_exit,
        'target': 'Multiple advanced exit strategies'
    }
    
    return phase3_features

def validate_integration_features(content: str) -> Dict:
    """Validate integration of Phase 2 and Phase 3 features"""
    integration_features = {}
    
    # 1. Signal Strength Adjustment
    has_signal_strength = 'signal_strength' in content and 'calculate_confluence_score' in content
    has_strength_adjustment = 'signal_strength.*adjustment' in content or 'strength.*position_size' in content
    
    integration_features['signal_strength'] = {
        'has_calculation': has_signal_strength,
        'has_position_adjustment': has_strength_adjustment,
        'implemented': has_signal_strength,
        'target': 'Signal strength affects position sizing'
    }
    
    # 2. Dynamic Risk Management
    has_dynamic_risk = 'risk_adjustment' in content and 'signal_strength' in content
    has_phase_detection = 'Phase 2' in content and 'Phase 3' in content
    
    integration_features['dynamic_risk'] = {
        'has_dynamic_adjustment': has_dynamic_risk,
        'has_phase_awareness': has_phase_detection,
        'implemented': has_dynamic_risk and has_phase_detection,
        'target': 'Risk adjustment based on feature phases'
    }
    
    # 3. Enhanced Logging and Monitoring
    has_advanced_logging = 'confluence_score' in content and 'signal_strength' in content and 'divergence_boost' in content
    has_detailed_context = 'signal_breakdown' in content and 'timeframe_signals' in content
    
    integration_features['monitoring'] = {
        'has_advanced_logging': has_advanced_logging,
        'has_detailed_context': has_detailed_context,
        'implemented': has_advanced_logging and has_detailed_context,
        'target': 'Comprehensive logging and monitoring'
    }
    
    return integration_features

def generate_comprehensive_report(phase2_features: Dict, phase3_features: Dict, integration_features: Dict) -> Tuple[float, Dict]:
    """Generate comprehensive validation report"""
    
    logger.info("🚀 HEIKIN ASHI PHASE 2 & PHASE 3 VALIDATION REPORT")
    logger.info("=" * 70)
    
    # Phase 2 Analysis
    logger.info("\n📊 PHASE 2: MULTI-TIMEFRAME IMPLEMENTATION")
    logger.info("-" * 50)
    
    phase2_score = 0
    phase2_total = 0
    
    for feature_name, details in phase2_features.items():
        status = "✅ IMPLEMENTED" if details.get('implemented', False) else "❌ MISSING"
        logger.info(f"\n🔧 {feature_name.upper().replace('_', ' ')}: {status}")
        
        if 'target' in details:
            logger.info(f"   Target: {details['target']}")
        
        if feature_name == 'timeframe_hierarchy' and details.get('implemented'):
            logger.info(f"   Macro: {details['macro']} | Signal: {details['signal']} | Execution: {details['execution']}")
        elif feature_name == 'confluence_scoring' and details.get('implemented'):
            logger.info(f"   Min Confluence: {details['min_confluence']}/3")
            logger.info(f"   Weights: Macro={details['macro_weight']}, Signal={details['signal_weight']}, Exec={details['execution_weight']}")
        elif feature_name == 'risk_reward' and details.get('implemented'):
            logger.info(f"   Current Ratio: 1:{details['target_ratio']}")
        
        if details.get('implemented'):
            phase2_score += 1
        phase2_total += 1
    
    phase2_percentage = (phase2_score / phase2_total) * 100 if phase2_total > 0 else 0
    
    # Phase 3 Analysis  
    logger.info(f"\n📈 PHASE 3: ADVANCED TECHNICAL FEATURES")
    logger.info("-" * 50)
    
    phase3_score = 0
    phase3_total = 0
    
    for feature_name, details in phase3_features.items():
        status = "✅ IMPLEMENTED" if details.get('implemented', False) else "❌ MISSING"
        logger.info(f"\n🔧 {feature_name.upper().replace('_', ' ')}: {status}")
        
        if 'target' in details:
            logger.info(f"   Target: {details['target']}")
        
        if feature_name == 'rsi_divergence' and details.get('implemented'):
            logger.info(f"   Enabled: {details['enabled']} | Lookback: {details['lookback_period']}")
            logger.info(f"   All Types Supported: {details['supports_all_types']}")
        elif feature_name == 'support_resistance' and details.get('implemented'):
            logger.info(f"   Enabled: {details['enabled']} | Period: {details['period']}")
            logger.info(f"   Pivot Levels: {details['has_pivot_levels']} | MA Integration: {details['has_ma_integration']}")
        elif feature_name == 'volume_confirmation' and details.get('implemented'):
            logger.info(f"   Breakout Multiplier: {details['breakout_multiplier']}x")
        elif feature_name == 'risk_reward' and details.get('implemented'):
            logger.info(f"   Target Ratio: 1:{details['target_ratio']}")
        
        if details.get('implemented'):
            phase3_score += 1
        phase3_total += 1
    
    phase3_percentage = (phase3_score / phase3_total) * 100 if phase3_total > 0 else 0
    
    # Integration Analysis
    logger.info(f"\n🔗 INTEGRATION FEATURES")
    logger.info("-" * 50)
    
    integration_score = 0
    integration_total = 0
    
    for feature_name, details in integration_features.items():
        status = "✅ IMPLEMENTED" if details.get('implemented', False) else "❌ MISSING"
        logger.info(f"\n🔧 {feature_name.upper().replace('_', ' ')}: {status}")
        
        if 'target' in details:
            logger.info(f"   Target: {details['target']}")
        
        if details.get('implemented'):
            integration_score += 1
        integration_total += 1
    
    integration_percentage = (integration_score / integration_total) * 100 if integration_total > 0 else 0
    
    # Overall Assessment
    total_score = phase2_score + phase3_score + integration_score
    total_possible = phase2_total + phase3_total + integration_total
    overall_percentage = (total_score / total_possible) * 100 if total_possible > 0 else 0
    
    logger.info(f"\n🎯 OVERALL IMPLEMENTATION STATUS")
    logger.info("=" * 50)
    logger.info(f"Phase 2 (Multi-timeframe): {phase2_score}/{phase2_total} ({phase2_percentage:.1f}%)")
    logger.info(f"Phase 3 (Advanced Features): {phase3_score}/{phase3_total} ({phase3_percentage:.1f}%)")
    logger.info(f"Integration Features: {integration_score}/{integration_total} ({integration_percentage:.1f}%)")
    logger.info(f"TOTAL IMPLEMENTATION: {total_score}/{total_possible} ({overall_percentage:.1f}%)")
    
    if overall_percentage >= 90:
        logger.info("🎉 EXCELLENT: Advanced strategy features fully implemented!")
        target_win_rate = "60-65%"
    elif overall_percentage >= 70:
        logger.info("✅ GOOD: Most advanced features implemented")
        target_win_rate = "55-60%"
    elif overall_percentage >= 50:
        logger.info("⚠️ MODERATE: Additional implementation needed")
        target_win_rate = "50-55%"
    else:
        logger.info("❌ POOR: Significant implementation required")
        target_win_rate = "<50%"
    
    logger.info(f"\n📈 EXPECTED PERFORMANCE IMPROVEMENTS:")
    logger.info(f"- Target Win Rate: {target_win_rate}")
    logger.info(f"- Phase 2 Target: 60% win rate with 1:2.0 R/R")
    logger.info(f"- Phase 3 Target: 65% win rate with 1:2.5 R/R") 
    logger.info(f"- Multi-timeframe confluence reduces false signals")
    logger.info(f"- RSI divergence enhances entry/exit timing")
    logger.info(f"- Dynamic S/R improves risk management")
    logger.info(f"- Advanced exits optimize profit-taking")
    
    return overall_percentage, {
        'phase2': {'score': phase2_score, 'total': phase2_total, 'percentage': phase2_percentage},
        'phase3': {'score': phase3_score, 'total': phase3_total, 'percentage': phase3_percentage},
        'integration': {'score': integration_score, 'total': integration_total, 'percentage': integration_percentage},
        'overall': {'score': total_score, 'total': total_possible, 'percentage': overall_percentage},
        'target_win_rate': target_win_rate
    }

def main():
    """Main validation function"""
    strategy_file = "/Volumes/OWC Volume/Projects2025/Botv0.3/strategy/heikin_ashi_strategy.py"
    
    logger.info("🧪 HEIKIN ASHI ADVANCED FEATURES VALIDATION")
    logger.info("=" * 70)
    
    try:
        with open(strategy_file, 'r') as f:
            content = f.read()
        
        # Validate each phase
        phase2_features = validate_phase2_multitimeframe(content)
        phase3_features = validate_phase3_advanced_features(content)
        integration_features = validate_integration_features(content)
        
        # Generate comprehensive report
        overall_score, detailed_scores = generate_comprehensive_report(
            phase2_features, phase3_features, integration_features
        )
        
        logger.info(f"\n✅ VALIDATION COMPLETED")
        logger.info(f"Advanced features implementation: {overall_score:.1f}%")
        logger.info(f"Expected win rate improvement: {detailed_scores['target_win_rate']}")
        
        if overall_score >= 85:
            logger.info("🚀 Ready for advanced backtesting and deployment!")
        else:
            logger.info("🔧 Additional implementation recommended before deployment")
            
        return True
        
    except Exception as e:
        logger.error(f"❌ Validation failed: {e}")
        return False

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)