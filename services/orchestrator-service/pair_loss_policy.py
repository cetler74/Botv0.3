"""Helpers for pair loss cooldowns, rotation scans, and DB trade classification."""

from __future__ import annotations

from typing import Optional, Tuple


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
