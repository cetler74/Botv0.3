"""Resolve Hyperliquid EMA50 entry block reasons for dashboard audit rows."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

try:
    from hyperliquid_watchlist import coin_side_entry_block, pair_to_hyperliquid_coin
except ImportError:  # pragma: no cover
    from core.hyperliquid_watchlist import coin_side_entry_block, pair_to_hyperliquid_coin


def _normalize_signal_side(signal: Any) -> Optional[str]:
    side = str(signal or "").strip().lower()
    if side in {"buy", "long"}:
        return "long"
    if side in {"sell", "short"}:
        return "short"
    return None


def _has_open_position(
    coin: str,
    side: str,
    open_trades: Iterable[Dict[str, Any]],
) -> bool:
    normalized_coin = pair_to_hyperliquid_coin(coin)
    normalized_side = str(side or "").strip().lower()
    for trade in open_trades or []:
        trade_coin = pair_to_hyperliquid_coin(
            str(trade.get("coin") or trade.get("pair") or trade.get("source_pair") or "")
        )
        if trade_coin != normalized_coin:
            continue
        trade_side = str(
            trade.get("position_side")
            or trade.get("side")
            or ""
        ).strip().lower()
        if trade_side == normalized_side:
            return True
    return False


def _setup_block_detail(row: Dict[str, Any]) -> str:
    invalidation = str(row.get("invalidation_reason") or "").strip()
    if invalidation and invalidation.lower() not in {"", "none"}:
        return invalidation
    state = str(row.get("setup_state") or "unknown").strip().lower()
    if state == "waiting_trigger":
        trigger_reason = str(row.get("trigger_reason") or "").strip()
        if trigger_reason:
            return trigger_reason
        direction = str(row.get("direction") or "none").strip().lower()
        if direction in {"long", "short"}:
            return f"awaiting_{direction}_trigger"
        return "setup:waiting_trigger"
    if state:
        return f"setup:{state}"
    return "no_actionable_signal"


def resolve_ema50_entry_block_reason(
    row: Dict[str, Any],
    *,
    global_entry_block: Optional[Dict[str, Any]] = None,
    open_trades: Optional[Iterable[Dict[str, Any]]] = None,
    closed_trades: Optional[Iterable[Dict[str, Any]]] = None,
    hl_cfg: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> Dict[str, str]:
    """Return {reason, detail} explaining why an EMA50 row would not enter now."""
    global_block = global_entry_block or {}
    if global_block.get("entryBlocked"):
        return {
            "reason": str(global_block.get("entryBlockReason") or "global_block"),
            "detail": str(global_block.get("entryBlockMessage") or global_block.get("entryBlockReason") or ""),
        }

    side = _normalize_signal_side(row.get("signal"))
    if not side:
        detail = _setup_block_detail(row)
        return {"reason": "setup_incomplete", "detail": detail}

    coin = pair_to_hyperliquid_coin(str(row.get("symbol") or ""))
    if not coin:
        return {"reason": "invalid_symbol", "detail": "Missing or invalid symbol"}

    realized_block_hours = float((hl_cfg or {}).get("realized_loss_block_hours") or 4.0)
    side_block = coin_side_entry_block(
        coin,
        side,
        list(open_trades or []),
        list(closed_trades or []),
        realized_block_hours=realized_block_hours,
        now=now,
    )
    if side_block.get("entryBlocked"):
        return {
            "reason": str(side_block.get("entryBlockReason") or "entry_blocked"),
            "detail": str(side_block.get("entryBlockMessage") or side_block.get("entryBlockReason") or ""),
        }

    if _has_open_position(coin, side, open_trades or []):
        return {
            "reason": "position_already_open",
            "detail": f"Open {side} position already exists for {coin}",
        }

    return {"reason": "eligible", "detail": "Passes HL pre-entry guards"}


def enrich_ema50_audit_entry_blocks(
    audit_payload: Dict[str, Any],
    *,
    global_entry_block: Optional[Dict[str, Any]] = None,
    open_trades: Optional[Iterable[Dict[str, Any]]] = None,
    closed_trades: Optional[Iterable[Dict[str, Any]]] = None,
    hl_cfg: Optional[Dict[str, Any]] = None,
    summary_builder=None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Attach entry_block_reason/detail to each audit row and rebuild summary."""
    if not isinstance(audit_payload, dict):
        return {"rows": [], "count": 0, "summary": {}}

    if summary_builder is None:
        try:
            from ema50_breakout_pullback_audit_summary import (
                build_ema50_breakout_pullback_audit_summary,
            )
        except ImportError:  # pragma: no cover
            from core.ema50_breakout_pullback_audit_summary import (
                build_ema50_breakout_pullback_audit_summary,
            )
        summary_builder = build_ema50_breakout_pullback_audit_summary

    enriched: List[Dict[str, Any]] = []
    for row in audit_payload.get("rows") or []:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        block = resolve_ema50_entry_block_reason(
            item,
            global_entry_block=global_entry_block,
            open_trades=open_trades,
            closed_trades=closed_trades,
            hl_cfg=hl_cfg,
            now=now,
        )
        item["entry_block_reason"] = block["reason"]
        item["entry_block_detail"] = block["detail"]
        enriched.append(item)

    summary = summary_builder(enriched)
    return {"rows": enriched, "count": len(enriched), "summary": summary}
