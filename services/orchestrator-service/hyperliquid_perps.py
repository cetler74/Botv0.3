"""
Hyperliquid perpetual paper-trading helpers.

This module intentionally has no live-order path. It mirrors existing strategy
signals into isolated paper positions so spot trading remains untouched.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, Optional


def pair_to_hyperliquid_coin(pair: str) -> str:
    """Convert BTC/USDC or BTCUSD-style symbols to Hyperliquid perp coin names."""
    raw = str(pair or "").upper().strip()
    if "/" in raw:
        return raw.split("/", 1)[0]
    for suffix in ("USDC", "USDT", "USD"):
        if raw.endswith(suffix):
            return raw[: -len(suffix)]
    return raw


def position_sides_from_signal(signal: str) -> Optional[str]:
    sig = str(signal or "").lower().strip()
    if sig == "buy":
        return "long"
    if sig == "sell":
        return "short"
    return None


def calculate_perp_pnl(
    position_side: str,
    entry_price: float,
    current_price: float,
    size: float,
    fees: float = 0.0,
) -> float:
    """Side-aware gross PnL less supplied fees."""
    side = str(position_side or "").lower()
    if entry_price <= 0 or current_price <= 0 or size <= 0:
        return 0.0
    if side == "short":
        gross = (entry_price - current_price) * size
    else:
        gross = (current_price - entry_price) * size
    return gross - float(fees or 0.0)


def pnl_percentage(position_side: str, entry_price: float, current_price: float) -> float:
    if entry_price <= 0 or current_price <= 0:
        return 0.0
    if str(position_side or "").lower() == "short":
        return ((entry_price - current_price) / entry_price) * 100.0
    return ((current_price - entry_price) / entry_price) * 100.0


def select_mirrored_signal(signals_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Pick the best actionable buy/sell signal from a strategy-service payload.

    Consensus buy/sell gets priority. If consensus is hold, use the strongest
    individual buy/sell strategy so sell signals can be paper-tested as shorts.
    """
    if not isinstance(signals_data, dict):
        return None

    strategies = signals_data.get("strategies") or {}
    if not isinstance(strategies, dict):
        strategies = {}

    def best_strategy_for(side: str) -> Dict[str, Any]:
        candidates = []
        for name, data in strategies.items():
            if not isinstance(data, dict):
                continue
            if str(data.get("signal", "")).lower() != side:
                continue
            candidates.append(
                (
                    float(data.get("confidence", 0) or 0),
                    float(data.get("strength", 0) or 0),
                    str(name),
                    data,
                )
            )
        if not candidates:
            return {}
        conf, strength, name, data = sorted(candidates, reverse=True)[0]
        return {
            "strategy": name,
            "signal": side,
            "confidence": conf,
            "strength": strength,
            "details": data,
        }

    consensus = signals_data.get("consensus") or {}
    c_signal = str(consensus.get("signal", "")).lower()
    if c_signal in {"buy", "sell"}:
        best = best_strategy_for(c_signal)
        return {
            "strategy": best.get("strategy") or "consensus",
            "signal": c_signal,
            "confidence": float(consensus.get("confidence", 0) or best.get("confidence", 0) or 0),
            "strength": float(best.get("strength", 0) or consensus.get("strength", 0) or 0),
            "consensus_confidence": float(consensus.get("confidence", 0) or 0),
            "consensus_agreement": float(consensus.get("agreement", 0) or 0),
        }

    candidates = []
    for side in ("buy", "sell"):
        best = best_strategy_for(side)
        if best:
            candidates.append(best)
    if not candidates:
        return None
    best = sorted(
        candidates,
        key=lambda row: (float(row.get("confidence", 0)), float(row.get("strength", 0))),
        reverse=True,
    )[0]
    best["consensus_confidence"] = float(consensus.get("confidence", 0) or 0)
    best["consensus_agreement"] = float(consensus.get("agreement", 0) or 0)
    return best


def should_close_paper_perp(
    trade: Dict[str, Any],
    current_price: float,
    *,
    stop_loss_pct: float,
    take_profit_pct: float,
    max_holding_minutes: int,
    now: Optional[datetime] = None,
) -> Optional[str]:
    """Return an exit reason when a paper position should close."""
    if current_price <= 0:
        return None
    entry_price = float(trade.get("entry_price") or 0.0)
    side = str(trade.get("position_side") or "long").lower()
    pct = pnl_percentage(side, entry_price, current_price)
    if take_profit_pct > 0 and pct >= take_profit_pct:
        return "paper_take_profit"
    if stop_loss_pct > 0 and pct <= -abs(stop_loss_pct):
        return "paper_stop_loss"

    if max_holding_minutes > 0:
        raw_entry = trade.get("entry_time")
        try:
            entry_time = (
                raw_entry
                if isinstance(raw_entry, datetime)
                else datetime.fromisoformat(str(raw_entry).replace("Z", "+00:00")).replace(tzinfo=None)
            )
            now_dt = now or datetime.utcnow()
            if now_dt.tzinfo:
                now_dt = now_dt.replace(tzinfo=None)
            if (now_dt - entry_time).total_seconds() >= max_holding_minutes * 60:
                return "paper_max_holding_time"
        except Exception:
            return None
    return None


def filter_allowed_coin(coin: str, allowed_symbols: Iterable[str]) -> bool:
    allowed = {str(x).upper().strip() for x in (allowed_symbols or []) if str(x).strip()}
    return not allowed or str(coin or "").upper().strip() in allowed

