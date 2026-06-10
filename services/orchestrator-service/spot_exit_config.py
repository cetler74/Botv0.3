"""Per-strategy spot exit rules merged from trading.exit_profiles."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


def _merge_dicts(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base or {})
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return merged


def _find_spot_exit_profile(
    trading_cfg: Dict[str, Any],
    strategy_name: str,
) -> tuple[str, Dict[str, Any]]:
    strategy_key = str(strategy_name or "").strip().lower()
    profiles = (trading_cfg or {}).get("exit_profiles") or {}
    if not isinstance(profiles, dict):
        return "", {}

    for profile_name, profile in profiles.items():
        if not isinstance(profile, dict):
            continue
        strategies = {
            str(item or "").strip().lower() for item in (profile.get("strategies") or [])
        }
        if strategy_key and strategy_key in strategies:
            return str(profile_name), profile
    return "", {}


@dataclass(frozen=True)
class SpotStrategyExitRules:
    """Resolved trailing / profit-protection config for one open spot trade."""

    profile_name: str = ""
    trailing_stop: Dict[str, Any] = field(default_factory=dict)
    profit_protection: Dict[str, Any] = field(default_factory=dict)
    stagnant_loser_enabled: Optional[bool] = None
    use_setup_targets: Optional[bool] = None
    max_holding_minutes: Optional[int] = None


def spot_strategy_exit_rules_from_trading_config(
    trading_cfg: Dict[str, Any],
    strategy_name: str = "",
) -> SpotStrategyExitRules:
    """Merge trading.exit_profiles[strategy] over global trailing_stop / profit_protection."""
    trading_cfg = trading_cfg or {}
    profile_name, profile = _find_spot_exit_profile(trading_cfg, strategy_name)

    trailing = dict(trading_cfg.get("trailing_stop") or {})
    profit_protection = dict(trading_cfg.get("profit_protection") or {})

    if profile:
        profile_trailing = profile.get("trailing_stop")
        if isinstance(profile_trailing, dict):
            trailing = _merge_dicts(trailing, profile_trailing)
        profile_pp = profile.get("profit_protection")
        if isinstance(profile_pp, dict):
            profit_protection = _merge_dicts(profit_protection, profile_pp)

    stagnant_enabled: Optional[bool] = None
    if profile and "stagnant_loser_enabled" in profile:
        stagnant_enabled = bool(profile.get("stagnant_loser_enabled"))

    use_setup_targets: Optional[bool] = None
    if profile and "use_setup_targets" in profile:
        use_setup_targets = bool(profile.get("use_setup_targets"))

    max_holding_minutes: Optional[int] = None
    if profile and profile.get("max_holding_minutes") is not None:
        try:
            max_holding_minutes = int(profile.get("max_holding_minutes") or 0)
        except (TypeError, ValueError):
            max_holding_minutes = None

    return SpotStrategyExitRules(
        profile_name=profile_name,
        trailing_stop=trailing,
        profit_protection=profit_protection,
        stagnant_loser_enabled=stagnant_enabled,
        use_setup_targets=use_setup_targets,
        max_holding_minutes=max_holding_minutes,
    )


def is_stagnant_loser_disabled_for_strategy(
    trading_cfg: Dict[str, Any],
    strategy_name: str,
) -> bool:
    strategy_key = str(strategy_name or "").strip().lower()
    stagnant_cfg = (trading_cfg or {}).get("stagnant_loser") or {}
    disabled = {
        str(item or "").strip().lower()
        for item in (stagnant_cfg.get("disabled_strategies") or [])
    }
    if strategy_key in disabled:
        return True
    rules = spot_strategy_exit_rules_from_trading_config(trading_cfg, strategy_name)
    return rules.stagnant_loser_enabled is False
