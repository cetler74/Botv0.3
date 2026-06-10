"""
Hyperliquid perpetual pair selector.

Selects tradable HL perp coins from live market data (metaAndAssetCtxs), using
HL-specific liquidity metrics aligned with root pair_selector thresholds.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Set, Tuple

import httpx

from core.pair_filters import DEFAULT_STABLECOIN_BASES, is_stablecoin_base_asset

logger = logging.getLogger(__name__)

HYPERLIQUID_INFO_URL = "https://api.hyperliquid.xyz/info"
DEFAULT_MAX_PAIRS = 20
DEFAULT_MIN_DAY_NOTIONAL = 2_000_000.0
DEFAULT_MIN_OPEN_INTEREST = 50_000.0
DEFAULT_MAX_IMPACT_SPREAD_PCT = 0.5
DEFAULT_TRADFI_DEXES = ("xyz",)
DEFAULT_TRADFI_CATEGORIES: Dict[str, Tuple[str, ...]] = {
    "stocks": (
        "AAPL", "AMD", "AMZN", "ARM", "ASML", "AVGO", "BABA", "BB", "BIRD",
        "BX", "CBRS", "COIN", "COST", "CRCL", "CRWV", "DELL", "DKNG",
        "EBAY", "GME", "GOOGL", "HIMS", "HOOD", "IBM", "INTC", "LITE",
        "LLY", "META", "MRVL", "MSFT", "MSTR", "MU", "NFLX", "NVDA",
        "ORCL", "PLTR", "RIVN", "RKLB", "SMSN", "SNDK", "SOFTBANK",
        "TSLA", "TSM", "ZM",
    ),
    "indices": (
        "IBOV", "JP225", "KR200", "NIFTY", "SP500", "USA100", "USA500",
        "VIX", "XLE", "XYZ100",
    ),
    "commodities": (
        "ALUMINIUM", "BRENTOIL", "CL", "COPPER", "CORN", "GOLD", "NATGAS",
        "PALLADIUM", "PLATINUM", "SILVER", "TTF", "URANIUM", "URNM",
        "WHEAT",
    ),
    "fx": ("DXY", "EUR", "GBP", "JPY", "KRW"),
    "pre_ipo": ("ANTHROPIC", "MINIMAX", "OPENAI", "SPACEX", "SPCX"),
}
DEFAULT_TRADFI_MAX_PAIRS = 10
DEFAULT_TRADFI_MIN_DAY_NOTIONAL = 1_000_000.0
DEFAULT_TRADFI_MIN_OPEN_INTEREST = 50_000.0
DEFAULT_TRADFI_MAX_IMPACT_SPREAD_PCT = 1.0


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


async def fetch_hyperliquid_meta_and_ctxs(
    client: Optional[httpx.AsyncClient] = None,
    *,
    dexes: Optional[Iterable[str]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Return combined native and requested HIP-3 (universe_assets, asset_contexts)."""
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=15.0)
    try:
        universe: List[Dict[str, Any]] = []
        ctxs: List[Dict[str, Any]] = []
        requests: List[Tuple[str, Dict[str, Any]]] = [("native", {"type": "metaAndAssetCtxs"})]
        for dex in dexes or []:
            dex_name = str(dex or "").strip().lower()
            if dex_name:
                requests.append((dex_name, {"type": "metaAndAssetCtxs", "dex": dex_name}))
        for dex_name, payload_req in requests:
            response = await client.post(HYPERLIQUID_INFO_URL, json=payload_req)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list) or len(payload) < 2:
                continue
            meta = payload[0] if isinstance(payload[0], dict) else {}
            dex_ctxs = payload[1] if isinstance(payload[1], list) else []
            dex_universe = meta.get("universe") or []
            if not isinstance(dex_universe, list):
                continue
            for idx, asset in enumerate(dex_universe):
                if idx >= len(dex_ctxs) or not isinstance(asset, dict):
                    continue
                row = dict(asset)
                row["_perpDex"] = dex_name
                universe.append(row)
                ctxs.append(dex_ctxs[idx] if isinstance(dex_ctxs[idx], dict) else {})
    finally:
        if owns_client:
            await client.aclose()
    return universe, ctxs


def _split_hl_coin(coin: str) -> Tuple[Optional[str], str]:
    raw = str(coin or "").strip()
    if ":" in raw:
        dex, base = raw.split(":", 1)
        return dex.lower(), base.upper()
    raw = raw.upper()
    if ":" in raw:
        dex, base = raw.split(":", 1)
        return dex.lower(), base
    return None, raw


def _display_coin(coin: str) -> str:
    _, base = _split_hl_coin(coin)
    return base


def _enabled(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    return value is not False and str(value).lower() not in {"0", "false", "no", "off"}


def _tradfi_category_map(tradfi_raw: Dict[str, Any]) -> Dict[str, str]:
    categories = tradfi_raw.get("categories")
    if not isinstance(categories, dict) or not categories:
        categories = DEFAULT_TRADFI_CATEGORIES
    out: Dict[str, str] = {}
    for category, symbols in categories.items():
        cat = str(category or "").strip().lower()
        if not cat:
            continue
        for symbol in symbols or []:
            base = str(symbol or "").upper().strip()
            if base:
                out[base] = cat
    for symbol in tradfi_raw.get("symbols") or []:
        base = str(symbol or "").upper().strip()
        if base:
            out.setdefault(base, "tradfi")
    return out


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


def _stable_bases_from_configs(
    hl_cfg: Dict[str, Any],
    global_pair_selector: Dict[str, Any],
    stable_bases_override: Optional[FrozenSet[str]],
) -> FrozenSet[str]:
    if stable_bases_override is not None:
        return stable_bases_override
    hl_sel = hl_cfg.get("pair_selector") or {}
    global_ps = global_pair_selector or {}
    exclude = hl_sel.get("exclude_stablecoin_bases")
    if exclude is None:
        exclude = global_ps.get("exclude_stablecoin_bases", True)
    if not exclude:
        return frozenset()
    assets = hl_sel.get("stablecoin_base_assets") or global_ps.get("stablecoin_base_assets")
    if not assets:
        return DEFAULT_STABLECOIN_BASES
    return frozenset(str(a).strip().upper() for a in assets if str(a).strip())


def _merge_selector_config(hl_cfg: Dict[str, Any], global_selector: Dict[str, Any]) -> Dict[str, Any]:
    hl_sel = dict(hl_cfg.get("pair_selector") or {})
    global_ps = global_selector or {}
    global_criteria = global_ps.get("selection_criteria") or {}
    tradfi_raw = dict(hl_sel.get("tradfi") or {})

    min_vol = hl_sel.get("min_day_notional_volume")
    if min_vol is None:
        min_vol = global_criteria.get("min_volume_24h", DEFAULT_MIN_DAY_NOTIONAL)

    max_impact = hl_sel.get("max_impact_spread_pct")
    if max_impact is None:
        max_impact = global_criteria.get("max_spread_percentage", DEFAULT_MAX_IMPACT_SPREAD_PCT)

    max_pairs = hl_sel.get("max_pairs", hl_cfg.get("max_selected_pairs", DEFAULT_MAX_PAIRS))

    exclude_stable = hl_sel.get("exclude_stablecoin_bases")
    if exclude_stable is None:
        exclude_stable = global_ps.get("exclude_stablecoin_bases", True)

    return {
        "enabled": hl_sel.get("enabled", True),
        "max_pairs": int(max_pairs or DEFAULT_MAX_PAIRS),
        "min_day_notional_volume": _safe_float(min_vol, DEFAULT_MIN_DAY_NOTIONAL),
        "min_open_interest": _safe_float(hl_sel.get("min_open_interest"), DEFAULT_MIN_OPEN_INTEREST),
        "max_impact_spread_pct": _safe_float(max_impact, DEFAULT_MAX_IMPACT_SPREAD_PCT),
        "blacklisted_coins": [str(c).upper() for c in (hl_sel.get("blacklisted_coins") or [])],
        "exclude_stablecoin_bases": bool(exclude_stable),
        "use_open_interest_filter": bool(hl_sel.get("use_open_interest_filter", False)),
        "tradfi": {
            "enabled": tradfi_raw.get("enabled", True),
            "dexes": [
                str(dex).strip().lower()
                for dex in (tradfi_raw.get("dexes") or DEFAULT_TRADFI_DEXES)
                if str(dex).strip()
            ],
            "symbols": [
                str(symbol).upper().strip()
                for symbol in (tradfi_raw.get("symbols") or [])
                if str(symbol).strip()
            ],
            "categories": {
                category: [
                    str(symbol).upper().strip()
                    for symbol in (symbols or [])
                    if str(symbol).strip()
                ]
                for category, symbols in (
                    (tradfi_raw.get("categories") or DEFAULT_TRADFI_CATEGORIES).items()
                )
            },
            "category_map": _tradfi_category_map(tradfi_raw),
            "max_pairs": int(tradfi_raw.get("max_pairs") or DEFAULT_TRADFI_MAX_PAIRS),
            "min_day_notional_volume": _safe_float(
                tradfi_raw.get("min_day_notional_volume"),
                DEFAULT_TRADFI_MIN_DAY_NOTIONAL,
            ),
            "min_open_interest": _safe_float(
                tradfi_raw.get("min_open_interest"),
                DEFAULT_TRADFI_MIN_OPEN_INTEREST,
            ),
            "max_impact_spread_pct": _safe_float(
                tradfi_raw.get("max_impact_spread_pct"),
                DEFAULT_TRADFI_MAX_IMPACT_SPREAD_PCT,
            ),
            "use_open_interest_filter": bool(
                tradfi_raw.get("use_open_interest_filter", False)
            ),
        },
    }


def _passes_liquidity(
    ctx: Dict[str, Any],
    *,
    min_day_notional_volume: float,
    min_open_interest: float,
    max_impact_spread_pct: float,
    use_open_interest_filter: bool = False,
) -> bool:
    day_notional = _safe_float(ctx.get("dayNtlVlm"))
    open_interest = _safe_float(ctx.get("openInterest"))
    if day_notional < min_day_notional_volume:
        return False
    if use_open_interest_filter and open_interest < min_open_interest:
        return False
    return impact_spread_pct(ctx) <= max_impact_spread_pct


def _candidate_row(
    asset: Dict[str, Any],
    ctx: Dict[str, Any],
    asset_class: str,
    asset_category: Optional[str] = None,
) -> Dict[str, Any]:
    dex, base = _split_hl_coin(str(asset.get("name") or ""))
    coin = f"{dex}:{base}" if dex else base
    return {
        "coin": coin,
        "pair": f"{coin}/USD-PERP",
        "displayCoin": base,
        "displayPair": f"{base}/USD-PERP",
        "baseSymbol": base,
        "perpDex": asset.get("_perpDex") or dex or "native",
        "assetClass": asset_class,
        "assetCategory": asset_category or asset_class,
        "dayNotionalVolume": _safe_float(ctx.get("dayNtlVlm")),
        "openInterest": _safe_float(ctx.get("openInterest")),
        "impactSpreadPct": impact_spread_pct(ctx),
        "markPx": _safe_float(ctx.get("markPx") or ctx.get("midPx")),
        "maxLeverage": asset.get("maxLeverage"),
    }


def _empty_selection(selector: str, error: Optional[str] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "selected_coins": [],
        "selected_pairs": [],
        "candidates": [],
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "selector": selector,
    }
    if error:
        out["error"] = error
    return out


async def select_top_hyperliquid_perps(
    hl_cfg: Dict[str, Any],
    global_pair_selector: Optional[Dict[str, Any]] = None,
    client: Optional[httpx.AsyncClient] = None,
    stable_bases: Optional[FrozenSet[str]] = None,
    excluded_coins: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """
    Rank Hyperliquid perpetual markets by dayNtlVlm and apply HL liquidity filters.

    Returns selected coin symbols (e.g. BTC, ETH) and per-coin market stats.
    """
    global_sel = global_pair_selector or {}
    sel_cfg = _merge_selector_config(hl_cfg, global_sel)
    stable_set = _stable_bases_from_configs(hl_cfg, global_sel, stable_bases)

    if not sel_cfg.get("enabled", True):
        return _empty_selection("disabled")

    tradfi_cfg = sel_cfg.get("tradfi") or {}
    tradfi_enabled = _enabled(tradfi_cfg.get("enabled"), True)
    tradfi_dexes = [
        str(dex).strip().lower()
        for dex in (tradfi_cfg.get("dexes") or DEFAULT_TRADFI_DEXES)
        if str(dex).strip()
    ]
    universe, ctxs = await fetch_hyperliquid_meta_and_ctxs(
        client, dexes=tradfi_dexes if tradfi_enabled else []
    )
    if not universe or not ctxs:
        logger.warning("[HLPairSelector] metaAndAssetCtxs empty; returning no selection")
        return _empty_selection("fallback_empty", error="empty_hyperliquid_meta")

    blacklisted = set(sel_cfg.get("blacklisted_coins") or [])
    runtime_excluded = {
        str(c or "").upper().strip()
        for c in (excluded_coins or [])
        if str(c or "").strip()
    }
    excluded = blacklisted | runtime_excluded
    min_vol = sel_cfg["min_day_notional_volume"]
    min_oi = sel_cfg["min_open_interest"]
    max_impact = sel_cfg["max_impact_spread_pct"]
    max_pairs = sel_cfg["max_pairs"]
    exclude_stable = sel_cfg.get("exclude_stablecoin_bases", True)
    tradfi_category_map = dict(tradfi_cfg.get("category_map") or {})
    tradfi_symbol_set = set(tradfi_category_map)
    tradfi_dex_set = set(tradfi_dexes)

    crypto_candidates: List[Dict[str, Any]] = []
    tradfi_candidates: List[Dict[str, Any]] = []
    for idx, asset in enumerate(universe):
        if idx >= len(ctxs):
            break
        if not isinstance(asset, dict):
            continue
        dex, base_symbol = _split_hl_coin(str(asset.get("name") or ""))
        coin = f"{dex}:{base_symbol}" if dex else base_symbol
        if not coin or coin in excluded:
            continue
        category = tradfi_category_map.get(base_symbol)
        is_tradfi = bool(dex and dex in tradfi_dex_set and category)
        if dex and dex in tradfi_dex_set and not is_tradfi:
            continue
        if is_tradfi and not tradfi_enabled:
            continue
        if not is_tradfi and exclude_stable and is_stablecoin_base_asset(coin, stable_set):
            continue
        ctx = ctxs[idx] if isinstance(ctxs[idx], dict) else {}
        if is_tradfi:
            if _passes_liquidity(
                ctx,
                min_day_notional_volume=tradfi_cfg["min_day_notional_volume"],
                min_open_interest=tradfi_cfg["min_open_interest"],
                max_impact_spread_pct=tradfi_cfg["max_impact_spread_pct"],
                use_open_interest_filter=tradfi_cfg.get("use_open_interest_filter", False),
            ):
                tradfi_candidates.append(_candidate_row(asset, ctx, "tradfi", category))
            continue
        if _passes_liquidity(
            ctx,
            min_day_notional_volume=min_vol,
            min_open_interest=min_oi,
            max_impact_spread_pct=max_impact,
            use_open_interest_filter=sel_cfg.get("use_open_interest_filter", False),
        ):
            crypto_candidates.append(_candidate_row(asset, ctx, "crypto", "crypto"))

    crypto_candidates.sort(key=lambda row: row["dayNotionalVolume"], reverse=True)
    tradfi_candidates.sort(key=lambda row: row["dayNotionalVolume"], reverse=True)
    tradfi_limit = min(int(tradfi_cfg["max_pairs"]), max_pairs) if tradfi_enabled else 0
    selected_tradfi = tradfi_candidates[:tradfi_limit]
    selected_crypto = crypto_candidates[: max(0, max_pairs - len(selected_tradfi))]
    selected = selected_crypto + selected_tradfi
    selected_coins = [row["coin"] for row in selected]

    logger.info(
        "[HLPairSelector] Selected %s HL perps (crypto=%s tradfi=%s min_vol=%.0f min_oi=%.0f max_impact=%.2f%% exclude_stable=%s runtime_excluded=%s): %s",
        len(selected_coins),
        len([row for row in selected if row.get("assetClass") == "crypto"]),
        len([row for row in selected if row.get("assetClass") == "tradfi"]),
        min_vol,
        min_oi,
        max_impact,
        exclude_stable,
        sorted(runtime_excluded),
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
            "use_open_interest_filter": sel_cfg.get("use_open_interest_filter", False),
            "max_impact_spread_pct": max_impact,
            "max_pairs": max_pairs,
            "exclude_stablecoin_bases": exclude_stable,
            "runtime_excluded_coins": sorted(runtime_excluded),
            "tradfi": {
                "enabled": tradfi_enabled,
                "dexes": sorted(tradfi_dex_set),
                "symbols": sorted(tradfi_symbol_set),
                "categories": tradfi_cfg.get("categories") or {},
                "max_pairs": int(tradfi_cfg["max_pairs"]),
                "min_day_notional_volume": tradfi_cfg["min_day_notional_volume"],
                "min_open_interest": tradfi_cfg["min_open_interest"],
                "use_open_interest_filter": tradfi_cfg.get("use_open_interest_filter", False),
                "max_impact_spread_pct": tradfi_cfg["max_impact_spread_pct"],
                "selected_coins": [
                    row["coin"] for row in selected if row.get("assetClass") == "tradfi"
                ],
                "selected_display_coins": [
                    _display_coin(row["coin"])
                    for row in selected
                    if row.get("assetClass") == "tradfi"
                ],
            },
        },
    }
