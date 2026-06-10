"""Helpers for pair loss cooldowns, rotation scans, and DB trade classification."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Tuple


def loss_cooldown_key(exchange_name: str, pair: str) -> Tuple[str, str]:
    """Canonical in-process key for (exchange, pair) loss cooldown maps."""
    ex = str(exchange_name or "").strip().lower()
    p = str(pair or "").strip()
    return (ex, p)


def is_macd_momentum_strategy(strategy: Optional[str]) -> bool:
    """True if trade row strategy refers to macd_momentum (allows future naming variants)."""
    return "macd_momentum" in str(strategy or "").lower()


def qualifies_for_hard_loss_cooldown(realized_pnl_pct: float, loss_pct_threshold: float) -> bool:
    """
    Whether a closed trade should start the hard entry cooldown.

    loss_pct_threshold (from config ``trading.pair_rotation.hard_entry_cooldown_loss_pct_threshold``):

    - ``0.0`` — any strict loss on the position: ``realized_pnl_pct < 0``.
    - Negative (e.g. ``-1.0``) — cooldown only when ``realized_pnl_pct <= threshold``
      (same style as ``remove_pair_loss_threshold_pct``).
    Positive values are treated as ``0.0`` (any loss).
    """
    thr = float(loss_pct_threshold if loss_pct_threshold is not None else 0.0)
    if thr > 0:
        thr = 0.0
    if thr == 0.0:
        return realized_pnl_pct < 0.0
    return realized_pnl_pct <= thr


def infer_trade_notional_usd(
    entry_price: float,
    position_size: float,
    total_investment: Optional[float] = None,
    entry_notional: Optional[float] = None,
) -> float:
    """
    Resolve the best available notional basis for PnL% calculations.

    Some closed rows can miss either ``entry_price`` or ``position_size`` while still
    containing a usable investment/notional field. Falling back keeps loss cooldown
    enforcement active instead of silently skipping those trades.
    """
    direct = float(entry_price or 0.0) * float(position_size or 0.0)
    if direct > 0.0:
        return direct
    from_total = float(total_investment or 0.0)
    if from_total > 0.0:
        return from_total
    from_entry_notional = float(entry_notional or 0.0)
    if from_entry_notional > 0.0:
        return from_entry_notional
    return 0.0


def loss_scaled_cooldown_hours(
    realized_pnl_pct: float,
    cfg: Optional[Mapping[str, Any]] = None,
) -> float:
    """
    Map realized loss % to blacklist duration in hours.

    Tiers use ``min_loss_pct`` as the most-negative boundary included in that
    band (loss must be <= min_loss_pct). Scan from least negative to most
    negative; the last matching tier wins. Returns 0 for breakeven/wins.
    """
    try:
        loss_pct = float(realized_pnl_pct)
    except (TypeError, ValueError):
        return 0.0
    if loss_pct >= 0.0:
        return 0.0

    root = cfg if isinstance(cfg, dict) else {}
    if not root.get("enabled", True):
        base = float(root.get("base_hours", 1.0) or 1.0)
        max_hours = float(root.get("max_hours", 12.0) or 12.0)
        return max(0.0, min(max_hours, base))

    base_hours = float(root.get("base_hours", 1.0) or 1.0)
    max_hours = float(root.get("max_hours", 12.0) or 12.0)
    raw_tiers = root.get("tiers") or []
    tiers: List[Dict[str, Any]] = [
        t for t in raw_tiers if isinstance(t, dict)
    ]
    if not tiers:
        return max(0.0, min(max_hours, base_hours))

    def _min_loss(tier: Dict[str, Any]) -> float:
        try:
            return float(tier.get("min_loss_pct", 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    tiers.sort(key=_min_loss)
    for tier in tiers:
        if loss_pct <= _min_loss(tier):
            try:
                tier_hours = float(tier.get("hours", base_hours) or base_hours)
            except (TypeError, ValueError):
                tier_hours = base_hours
            return max(0.0, min(max_hours, tier_hours))
    return max(0.0, min(max_hours, base_hours))
