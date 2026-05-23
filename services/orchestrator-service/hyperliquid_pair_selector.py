"""
Hyperliquid perpetual pair selector.

Selects tradable HL perp coins from live market data (metaAndAssetCtxs), using
HL-specific liquidity metrics — not the spot exchange pair_selector output.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

HYPERLIQUID_INFO_URL = "https://api.hyperliquid.xyz/info"


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


async def fetch_hyperliquid_meta_and_ctxs(
    client: Optional[httpx.AsyncClient] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Return (universe_assets, asset_contexts) from Hyperliquid info API."""
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=15.0)
    try:
        response = await client.post(HYPERLIQUID_INFO_URL, json={"type": "metaAndAssetCtxs"})
        response.raise_for_status()
        payload = response.json()
    finally:
        if owns_client:
            await client.aclose()

    if not isinstance(payload, list) or len(payload) < 2:
        return [], []
    meta = payload[0] if isinstance(payload[0], dict) else {}
    ctxs = payload[1] if isinstance(payload[1], list) else []
    universe = meta.get("universe") or []
    if not isinstance(universe, list):
        universe = []
    return universe, ctxs


def impact_spread_pct(ctx: Dict[str, Any]) -> float:
    """Estimate round-trip impact spread % from impact prices vs mid."""
    mid = _safe_float(ctx.get("midPx") or ctx.get("markPx"))
    impact = ctx.get("impactPxs") or []
    if mid <= 0 or not isinstance(impact, list) or len(impact) < 2:
        return 999.0
    buy_impact = _safe_float(impact[0])
    sell_impact = _safe_float(impact[1])
    if buy_impact <= 0 or sell_impact <= 0:
        return 999.0
    return ((sell_impact - buy_impact) / mid) * 100.0


def _merge_selector_config(hl_cfg: Dict[str, Any], global_selector: Dict[str, Any]) -> Dict[str, Any]:
    hl_sel = dict(hl_cfg.get("pair_selector") or {})
    global_criteria = (global_selector or {}).get("selection_criteria") or {}
    min_vol = hl_sel.get("min_day_notional_volume")
    if min_vol is None:
        min_vol = global_criteria.get("min_volume_24h", 1_000_000)
    min_oi = hl_sel.get("min_open_interest", 50_000)
    max_impact = hl_sel.get("max_impact_spread_pct", 0.5)
    max_pairs = hl_sel.get("max_pairs", hl_cfg.get("max_selected_pairs", 12))
    return {
        "enabled": hl_sel.get("enabled", True),
        "max_pairs": int(max_pairs or 12),
        "min_day_notional_volume": _safe_float(min_vol, 1_000_000),
        "min_open_interest": _safe_float(min_oi, 50_000),
        "max_impact_spread_pct": _safe_float(max_impact, 0.5),
        "blacklisted_coins": [str(c).upper() for c in (hl_sel.get("blacklisted_coins") or [])],
        "candidate_cap": [str(c).upper() for c in (hl_cfg.get("allowed_symbols") or []) if str(c).strip()],
    }


async def select_top_hyperliquid_perps(
    hl_cfg: Dict[str, Any],
    global_pair_selector: Optional[Dict[str, Any]] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> Dict[str, Any]:
    """
    Rank Hyperliquid perpetual markets by dayNtlVlm and apply HL liquidity filters.

    Returns selected coin symbols (e.g. BTC, ETH) and per-coin market stats.
    """
    sel_cfg = _merge_selector_config(hl_cfg, global_pair_selector or {})
    if not sel_cfg.get("enabled", True):
        cap = sel_cfg.get("candidate_cap") or []
        return {
            "selected_coins": cap[: sel_cfg["max_pairs"]],
            "selected_pairs": [f"{c}/USD-PERP" for c in cap[: sel_cfg["max_pairs"]]],
            "candidates": [],
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "selector": "disabled",
        }

    universe, ctxs = await fetch_hyperliquid_meta_and_ctxs(client)
    if not universe or not ctxs:
        cap = sel_cfg.get("candidate_cap") or []
        logger.warning("[HLPairSelector] metaAndAssetCtxs empty; using config candidate_cap fallback")
        return {
            "selected_coins": cap[: sel_cfg["max_pairs"]],
            "selected_pairs": [f"{c}/USD-PERP" for c in cap[: sel_cfg["max_pairs"]]],
            "candidates": [],
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "selector": "fallback_config",
            "error": "empty_hyperliquid_meta",
        }

    blacklisted = set(sel_cfg.get("blacklisted_coins") or [])
    candidate_cap = set(sel_cfg.get("candidate_cap") or [])
    min_vol = sel_cfg["min_day_notional_volume"]
    min_oi = sel_cfg["min_open_interest"]
    max_impact = sel_cfg["max_impact_spread_pct"]
    max_pairs = sel_cfg["max_pairs"]

    candidates: List[Dict[str, Any]] = []
    for idx, asset in enumerate(universe):
        if idx >= len(ctxs):
            break
        if not isinstance(asset, dict):
            continue
        coin = str(asset.get("name") or "").upper().strip()
        if not coin or coin in blacklisted:
            continue
        if candidate_cap and coin not in candidate_cap:
            continue
        ctx = ctxs[idx] if isinstance(ctxs[idx], dict) else {}
        day_ntl = _safe_float(ctx.get("dayNtlVlm"))
        oi = _safe_float(ctx.get("openInterest"))
        spread = impact_spread_pct(ctx)
        if day_ntl < min_vol:
            continue
        if oi < min_oi:
            continue
        if spread > max_impact:
            continue
        candidates.append({
            "coin": coin,
            "pair": f"{coin}/USD-PERP",
            "dayNotionalVolume": day_ntl,
            "openInterest": oi,
            "impactSpreadPct": spread,
            "markPx": _safe_float(ctx.get("markPx") or ctx.get("midPx")),
            "maxLeverage": asset.get("maxLeverage"),
        })

    candidates.sort(key=lambda row: row["dayNotionalVolume"], reverse=True)
    selected = candidates[:max_pairs]
    selected_coins = [row["coin"] for row in selected]

    logger.info(
        "[HLPairSelector] Selected %s HL perps (min_vol=%.0f min_oi=%.0f max_impact=%.2f%%): %s",
        len(selected_coins),
        min_vol,
        min_oi,
        max_impact,
        selected_coins,
    )

    return {
        "selected_coins": selected_coins,
        "selected_pairs": [row["pair"] for row in selected],
        "candidates": selected,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "selector": "hyperliquid_metaAndAssetCtxs",
        "criteria": {
            "min_day_notional_volume": min_vol,
            "min_open_interest": min_oi,
            "max_impact_spread_pct": max_impact,
            "max_pairs": max_pairs,
        },
    }
