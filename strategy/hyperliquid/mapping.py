"""Hyperliquid perp strategy registry (full forks)."""

HYPERLIQUID_STRATEGY_MAPPING = {
    "vwma_hull": ("strategy.hyperliquid.vwma_hull_perp", "VwmaHullPerpStrategy"),
    "rsi_oversold_checklist": ("strategy.hyperliquid.rsi_oversold_checklist_perp", "RsiOversoldChecklistPerpStrategy"),
    "rsi_oversold_override": ("strategy.hyperliquid.rsi_oversold_override_perp", "RsiOversoldOverridePerpStrategy"),
    "macd_momentum": ("strategy.hyperliquid.macd_momentum_perp", "MacdMomentumPerpStrategy"),
    "heikin_ashi": ("strategy.hyperliquid.heikin_ashi_perp", "HeikinAshiPerpStrategy"),
    "multi_timeframe_confluence": ("strategy.hyperliquid.multi_timeframe_confluence_perp", "MultiTimeframeConfluencePerpStrategy"),
    "engulfing_multi_tf": ("strategy.hyperliquid.engulfing_multi_tf_perp", "EngulfingMultiTfPerpStrategy"),
    "macd_ema_vwap_scalper": ("strategy.hyperliquid.macd_ema_vwap_scalper_perp", "MacdEmaVwapScalperPerpStrategy"),
    "supertrend": ("strategy.hyperliquid.supertrend_perp", "SuperTrendPerpStrategy"),
    "swing_hull_rsi_ema": ("strategy.hyperliquid.swing_hull_rsi_ema_perp", "SwingHullRsiEmaPerpStrategy"),
    "pullback_long_scalping": ("strategy.hyperliquid.pullback_long_scalping_perp", "PullbackLongScalpingPerpStrategy"),
    "breakout_retest_long": ("strategy.hyperliquid.breakout_retest_perp", "BreakoutRetestPerpStrategy"),
    "vwap_bounce_scalping": ("strategy.hyperliquid.vwap_bounce_scalping_perp", "VwapBounceScalpingPerpStrategy"),
    "small_size_momentum_scalp": ("strategy.hyperliquid.small_size_momentum_scalp_perp", "SmallSizeMomentumScalpPerpStrategy"),
    "sma_reclaim_bull_flag": (
        "strategy.hyperliquid.sma_reclaim_bull_flag_perp",
        "SmaReclaimBullFlagPerpStrategy",
    ),
    "rsi_stoch_reversal_5m": (
        "strategy.hyperliquid.rsi_stoch_reversal_5m_perp",
        "RsiStochReversal5mPerpStrategy",
    ),
}
