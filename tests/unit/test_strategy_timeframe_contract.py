"""Repository contract for executable strategy timeframes."""

from pathlib import Path

import yaml

from strategy.hyperliquid.mapping import HYPERLIQUID_STRATEGY_MAPPING


ROOT = Path(__file__).resolve().parents[2]
CONFIG = yaml.safe_load((ROOT / "config" / "config.yaml").read_text())
ALLOWED = {"1h", "15m", "1m"}
RSI_STOCH_15M_ALLOWED = {"1h", "15m"}
LEGACY_RSI_STOCH_ALIASES = {
    "rsi_stoch_reversal_1m",
    "rsi_stoch_reversal_5m",
}
TIMEFRAME_KEYS = {
    "target_timeframes",
    "context_timeframes",
    "primary_timeframe",
    "structure_timeframe",
    "trend_timeframe",
    "bias_timeframe",
    "confirmation_timeframe",
    "entry_timeframe",
    "precision_timeframe",
    "execution_timeframe",
    "structural_exit_timeframe",
}


def _declared_timeframes(strategy_cfg):
    found = set()
    params = strategy_cfg.get("parameters") or {}
    for source in (strategy_cfg, params):
        for key in TIMEFRAME_KEYS:
            raw = source.get(key)
            values = raw if isinstance(raw, list) else [raw]
            found.update(str(value).lower() for value in values if value)
    return found


def _enabled_strategy_configs(section):
    return {
        name: cfg
        for name, cfg in (CONFIG.get(section) or {}).items()
        if name != "regime_stability"
        and isinstance(cfg, dict)
        and cfg.get("enabled") is True
    }


def test_enabled_spot_and_perp_strategies_only_declare_supported_execution_frames():
    offenders = {}
    for section in ("strategies", "strategies_hyperliquid"):
        for name, cfg in _enabled_strategy_configs(section).items():
            invalid = _declared_timeframes(cfg) - ALLOWED
            if invalid:
                offenders[f"{section}.{name}"] = sorted(invalid)
    assert offenders == {}


def test_versioned_rsi_stoch_15m_is_executable_and_old_aliases_are_not():
    for section in ("strategies", "strategies_hyperliquid"):
        strategy_cfg = CONFIG[section]
        assert strategy_cfg["rsi_stoch_reversal_15m"]["enabled"] is True
        assert _declared_timeframes(strategy_cfg["rsi_stoch_reversal_15m"]) == RSI_STOCH_15M_ALLOWED
        for alias in LEGACY_RSI_STOCH_ALIASES:
            assert strategy_cfg[alias]["enabled"] is False

    assert "rsi_stoch_reversal_15m" in HYPERLIQUID_STRATEGY_MAPPING
    assert LEGACY_RSI_STOCH_ALIASES.isdisjoint(HYPERLIQUID_STRATEGY_MAPPING)


def test_legacy_aliases_are_absent_from_executable_and_shadow_gates():
    hl = CONFIG["trading"]["hyperliquid_perps"]
    executable_values = [
        *(hl.get("live_strategy_allowlist") or []),
        *((hl.get("shadow_cohort_promotion") or {}).get("strategies") or []),
        *[
            row.get("strategy")
            for row in (
                (hl.get("shadow_cohort_promotion") or {}).get("require_promotion_for")
                or []
            )
            if isinstance(row, dict)
        ],
    ]
    assert LEGACY_RSI_STOCH_ALIASES.isdisjoint(executable_values)


def test_disabled_non_retimed_strategies_have_disabled_execution_gates():
    disabled = {"arc_daytrade", "ema50_breakout_pullback", "orb_5m_scalp", "vwma_hull"}
    hl_strategies = CONFIG["strategies_hyperliquid"]
    hl = CONFIG["trading"]["hyperliquid_perps"]
    specialist = hl.get("specialist_strategy_gates") or {}
    standalone = hl.get("standalone_strategy_gates") or {}

    for name in disabled:
        assert hl_strategies[name]["enabled"] is False
        if name in specialist:
            assert specialist[name]["enabled"] is False
        if name in standalone:
            assert standalone[name]["enabled"] is False
