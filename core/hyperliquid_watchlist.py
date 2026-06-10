"""
Build Hyperliquid perp pair watchlist rows for the portfolio dashboard.

Always includes: active selector coins, open positions, runtime-blocked coins,
and config-blacklisted coins. When a global entry halt applies, expands to all
HL markets that pass pair-selector liquidity filters.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import httpx

HYPERLIQUID_INFO_URL = "https://api.hyperliquid.xyz/info"
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


def pair_to_hyperliquid_coin(pair: str) -> str:
    raw = str(pair or "").strip()
    if "/" in raw:
        return raw.split("/", 1)[0]
    if ":" in raw:
        dex, base = raw.split(":", 1)
        raw = f"{dex.lower()}:{base.upper()}"
    else:
        raw = raw.upper()
    for suffix in ("USDC", "USDT", "USD"):
        if raw.endswith(suffix):
            return raw[: -len(suffix)]
    return raw


def _split_hl_coin(coin: str) -> Tuple[Optional[str], str]:
    raw = pair_to_hyperliquid_coin(coin)
    if ":" in raw:
        dex, base = raw.split(":", 1)
        return dex, base.upper()
    return None, raw


def display_hyperliquid_coin(coin: str) -> str:
    _, base = _split_hl_coin(coin)
    return base


def display_hyperliquid_pair(pair_or_coin: str) -> str:
    base = display_hyperliquid_coin(pair_or_coin)
    return f"{base}/USD-PERP" if base else ""


def _enabled(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    return value is not False and str(value).lower() not in {"0", "false", "no", "off"}


def _tradfi_category_map_from_cfg(hl_cfg: Dict[str, Any]) -> Dict[str, str]:
    tradfi_cfg = ((hl_cfg or {}).get("pair_selector") or {}).get("tradfi") or {}
    categories = tradfi_cfg.get("categories")
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
    for symbol in tradfi_cfg.get("symbols") or []:
        base = str(symbol or "").upper().strip()
        if base:
            out.setdefault(base, "tradfi")
    return out


def _tradfi_dexes_from_cfg(hl_cfg: Dict[str, Any]) -> Set[str]:
    tradfi_cfg = ((hl_cfg or {}).get("pair_selector") or {}).get("tradfi") or {}
    raw = tradfi_cfg.get("dexes") or DEFAULT_TRADFI_DEXES
    return {str(dex).strip().lower() for dex in raw if str(dex).strip()}


def _tradfi_info_for_coin(coin: str, hl_cfg: Dict[str, Any]) -> Tuple[bool, str]:
    dex, base = _split_hl_coin(coin)
    if not dex or dex not in _tradfi_dexes_from_cfg(hl_cfg):
        return False, "crypto"
    category = _tradfi_category_map_from_cfg(hl_cfg).get(base)
    return bool(category), category or "crypto"


def _parse_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    except Exception:
        return None


def coin_entry_block(
    coin: str,
    open_trades: Iterable[Dict[str, Any]],
    closed_trades: Iterable[Dict[str, Any]],
    *,
    realized_block_hours: float = 4.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Per-coin entry block (open underwater or recent realized loss cooldown)."""
    normalized = pair_to_hyperliquid_coin(coin)
    now_dt = now or datetime.utcnow()
    block_hours = max(0.0, float(realized_block_hours or 0.0))

    for trade in open_trades or []:
        trade_coin = pair_to_hyperliquid_coin(
            str(trade.get("coin") or trade.get("pair") or trade.get("source_pair") or "")
        )
        if trade_coin != normalized:
            continue
        upnl = _safe_float(trade.get("unrealized_pnl"))
        if upnl < 0:
            return {
                "entryBlocked": True,
                "entryBlockReason": "open_unrealized_negative",
                "entryBlockUntil": None,
                "entryBlockMessage": f"open paper position is underwater (${upnl:.2f})",
            }

    latest_loss_time: Optional[datetime] = None
    latest_loss_pnl = 0.0
    for trade in closed_trades or []:
        trade_coin = pair_to_hyperliquid_coin(
            str(trade.get("coin") or trade.get("pair") or trade.get("source_pair") or "")
        )
        if trade_coin != normalized:
            continue
        rpnl = _safe_float(trade.get("realized_pnl"))
        if rpnl >= 0:
            continue
        exit_time = _parse_dt(trade.get("exit_time") or trade.get("updated_at"))
        if exit_time is None:
            continue
        if latest_loss_time is None or exit_time > latest_loss_time:
            latest_loss_time = exit_time
            latest_loss_pnl = rpnl

    if latest_loss_time and block_hours > 0:
        until_dt = latest_loss_time + timedelta(hours=block_hours)
        if until_dt > now_dt:
            return {
                "entryBlocked": True,
                "entryBlockReason": "recent_negative_realized",
                "entryBlockUntil": until_dt.isoformat() + "+00:00",
                "entryBlockMessage": (
                    f"realized loss ${latest_loss_pnl:.2f}; cooldown until {until_dt.isoformat()} UTC"
                ),
            }

    return {
        "entryBlocked": False,
        "entryBlockReason": None,
        "entryBlockUntil": None,
        "entryBlockMessage": "",
    }


def _trade_side(trade: Dict[str, Any]) -> str:
    raw = str(trade.get("position_side") or trade.get("source_signal") or "").lower()
    if raw in {"long", "buy"}:
        return "long"
    if raw in {"short", "sell"}:
        return "short"
    return raw


def _empty_side_block(side: str) -> Dict[str, Any]:
    return {
        "entryBlocked": False,
        "entryBlockReason": None,
        "entryBlockUntil": None,
        "entryBlockMessage": "",
        "entryBlockSide": side,
    }


def coin_side_entry_block(
    coin: str,
    side: str,
    open_trades: Iterable[Dict[str, Any]],
    closed_trades: Iterable[Dict[str, Any]],
    *,
    realized_block_hours: float = 4.0,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Per-coin+side entry block for open underwater or recent realized loss."""
    normalized = pair_to_hyperliquid_coin(coin)
    normalized_side = str(side or "").strip().lower()
    if not normalized or normalized_side not in {"long", "short"}:
        return _empty_side_block(normalized_side or "")

    now_dt = now or datetime.utcnow()
    block_hours = max(0.0, float(realized_block_hours or 0.0))

    for trade in open_trades or []:
        trade_coin = pair_to_hyperliquid_coin(
            str(trade.get("coin") or trade.get("pair") or trade.get("source_pair") or "")
        )
        if trade_coin != normalized or _trade_side(trade) != normalized_side:
            continue
        upnl = _safe_float(trade.get("unrealized_pnl"))
        if upnl < 0:
            return {
                "entryBlocked": True,
                "entryBlockReason": "open_unrealized_negative",
                "entryBlockUntil": None,
                "entryBlockMessage": (
                    f"open {normalized_side} paper position is underwater (${upnl:.2f})"
                ),
                "entryBlockSide": normalized_side,
            }

    latest_loss_time: Optional[datetime] = None
    latest_loss_pnl = 0.0
    for trade in closed_trades or []:
        trade_coin = pair_to_hyperliquid_coin(
            str(trade.get("coin") or trade.get("pair") or trade.get("source_pair") or "")
        )
        if trade_coin != normalized or _trade_side(trade) != normalized_side:
            continue
        rpnl = _safe_float(trade.get("realized_pnl"))
        if rpnl >= 0:
            continue
        exit_time = _parse_dt(trade.get("exit_time") or trade.get("updated_at"))
        if exit_time is None:
            continue
        if latest_loss_time is None or exit_time > latest_loss_time:
            latest_loss_time = exit_time
            latest_loss_pnl = rpnl

    if latest_loss_time and block_hours > 0:
        until_dt = latest_loss_time + timedelta(hours=block_hours)
        if until_dt > now_dt:
            return {
                "entryBlocked": True,
                "entryBlockReason": "recent_negative_realized",
                "entryBlockUntil": until_dt.isoformat() + "+00:00",
                "entryBlockMessage": (
                    f"{normalized_side} realized loss ${latest_loss_pnl:.2f}; "
                    f"cooldown until {until_dt.isoformat()} UTC"
                ),
                "entryBlockSide": normalized_side,
            }

    return _empty_side_block(normalized_side)


def coin_entry_block_by_side(
    coin: str,
    open_trades: Iterable[Dict[str, Any]],
    closed_trades: Iterable[Dict[str, Any]],
    *,
    realized_block_hours: float = 4.0,
    now: Optional[datetime] = None,
) -> Dict[str, Dict[str, Any]]:
    return {
        side: coin_side_entry_block(
            coin,
            side,
            open_trades,
            closed_trades,
            realized_block_hours=realized_block_hours,
            now=now,
        )
        for side in ("long", "short")
    }


def _blocked_sides(block_by_side: Dict[str, Dict[str, Any]]) -> List[str]:
    return [
        side
        for side in ("long", "short")
        if (block_by_side.get(side) or {}).get("entryBlocked")
    ]


def _status_detail(blocked_sides: List[str]) -> str:
    if set(blocked_sides) == {"long", "short"}:
        return "both_blocked"
    if blocked_sides == ["long"]:
        return "long_blocked"
    if blocked_sides == ["short"]:
        return "short_blocked"
    return "none"


def global_entry_block(
    hl_cfg: Dict[str, Any],
    *,
    trading_status: Optional[Dict[str, Any]] = None,
    open_trades: Optional[List[Dict[str, Any]]] = None,
    closed_trades: Optional[List[Dict[str, Any]]] = None,
    paper_summary: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Halt that applies to every coin (engine off, bot stopped, daily loss, max positions)."""
    if not bool(hl_cfg.get("enabled", False)):
        return {
            "entryBlocked": True,
            "entryBlockReason": "engine_disabled",
            "entryBlockMessage": "Hyperliquid perp engine is disabled in config",
        }

    status = str((trading_status or {}).get("status") or "").lower()
    if status and status not in {"running", "active"}:
        return {
            "entryBlocked": True,
            "entryBlockReason": "trading_stopped",
            "entryBlockMessage": f"orchestrator trading status is {status}",
        }

    max_open = int(hl_cfg.get("max_open_positions") or 0)
    open_count = len(open_trades or [])
    if max_open > 0 and open_count >= max_open:
        return {
            "entryBlocked": True,
            "entryBlockReason": "max_open_positions",
            "entryBlockMessage": f"{open_count} open positions (max {max_open})",
        }

    halt_cfg = hl_cfg.get("daily_loss_halt") or {}
    halt_enabled = halt_cfg.get("enabled", True)
    if halt_enabled is not False and str(halt_enabled).lower() not in {"0", "false", "no", "off"}:
        equity = _safe_float(
            (paper_summary or {}).get("equity")
            or (paper_summary or {}).get("balance")
            or hl_cfg.get("starting_balance_usd"),
            5000.0,
        )
        max_pct = _safe_float(
            halt_cfg.get("max_daily_loss_pct", hl_cfg.get("max_daily_loss_pct")),
            0.03,
        )
        if max_pct > 0 and equity > 0:
            now_dt = now or datetime.utcnow()
            if now_dt.tzinfo is not None:
                now_dt = now_dt.replace(tzinfo=None)
            today = now_dt.date()
            daily_pnl = 0.0
            for row in closed_trades or []:
                if str(row.get("status") or "").upper() != "CLOSED":
                    continue
                ts = _parse_dt(row.get("exit_time")) or _parse_dt(row.get("entry_time"))
                if not ts or ts.date() != today:
                    continue
                daily_pnl += _safe_float(row.get("realized_pnl"))
            limit = -equity * max_pct
            if daily_pnl <= limit:
                return {
                    "entryBlocked": True,
                    "entryBlockReason": "daily_loss_halt",
                    "entryBlockMessage": (
                        f"daily realized PnL ${daily_pnl:.2f} at/below limit ${limit:.2f}"
                    ),
                }

    if bool(hl_cfg.get("live_kill_switch", False)):
        return {
            "entryBlocked": True,
            "entryBlockReason": "live_kill_switch",
            "entryBlockMessage": "live_kill_switch is active",
        }

    return {
        "entryBlocked": False,
        "entryBlockReason": None,
        "entryBlockMessage": "",
    }


def runtime_blocked_coins(
    open_trades: Iterable[Dict[str, Any]],
    closed_trades: Iterable[Dict[str, Any]],
    *,
    realized_block_hours: float = 4.0,
) -> Dict[str, Dict[str, Any]]:
    """Coins with both long and short currently blocked."""
    coins = {
        pair_to_hyperliquid_coin(
            str(t.get("coin") or t.get("pair") or t.get("source_pair") or "")
        )
        for t in list(open_trades or []) + list(closed_trades or [])
    }
    blocked: Dict[str, Dict[str, Any]] = {}
    for coin in sorted(c for c in coins if c):
        block_by_side = coin_entry_block_by_side(
            coin,
            open_trades,
            closed_trades,
            realized_block_hours=realized_block_hours,
        )
        sides = _blocked_sides(block_by_side)
        if set(sides) == {"long", "short"}:
            blocked[coin] = {
                "entryBlocked": True,
                "entryBlockReason": "both_sides_blocked",
                "entryBlockUntil": None,
                "entryBlockMessage": "long and short entries are both blocked",
                "entryBlockBySide": block_by_side,
                "blockedSides": sides,
            }
    return blocked


def _impact_spread_pct(ctx: Dict[str, Any]) -> float:
    mid = _safe_float(ctx.get("midPx") or ctx.get("markPx"))
    impact = ctx.get("impactPxs") or []
    if mid <= 0 or not isinstance(impact, list) or len(impact) < 2:
        return 999.0
    buy_impact = _safe_float(impact[0])
    sell_impact = _safe_float(impact[1])
    if buy_impact <= 0 or sell_impact <= 0:
        return 999.0
    return ((sell_impact - buy_impact) / mid) * 100.0


def _selector_config(hl_cfg: Dict[str, Any], global_pair_selector: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    hl_sel = dict(hl_cfg.get("pair_selector") or {})
    global_sel = dict(global_pair_selector or {})
    for key, default in (
        ("min_day_notional_volume", DEFAULT_MIN_DAY_NOTIONAL),
        ("min_open_interest", DEFAULT_MIN_OPEN_INTEREST),
        ("max_impact_spread_pct", DEFAULT_MAX_IMPACT_SPREAD_PCT),
        ("exclude_stablecoin_bases", True),
        ("use_open_interest_filter", False),
    ):
        if key not in hl_sel:
            hl_sel[key] = global_sel.get(key, default)
    tradfi = dict(hl_sel.get("tradfi") or {})
    hl_sel["tradfi"] = {
        "enabled": tradfi.get("enabled", True),
        "dexes": [
            str(dex).strip().lower()
            for dex in (tradfi.get("dexes") or DEFAULT_TRADFI_DEXES)
            if str(dex).strip()
        ],
        "symbols": [
            str(symbol).upper().strip()
            for symbol in (tradfi.get("symbols") or [])
            if str(symbol).strip()
        ],
        "categories": {
            category: [
                str(symbol).upper().strip()
                for symbol in (symbols or [])
                if str(symbol).strip()
            ]
            for category, symbols in (
                (tradfi.get("categories") or DEFAULT_TRADFI_CATEGORIES).items()
            )
        },
        "max_pairs": int(tradfi.get("max_pairs") or DEFAULT_TRADFI_MAX_PAIRS),
        "min_day_notional_volume": _safe_float(
            tradfi.get("min_day_notional_volume"),
            DEFAULT_TRADFI_MIN_DAY_NOTIONAL,
        ),
        "min_open_interest": _safe_float(
            tradfi.get("min_open_interest"),
            DEFAULT_TRADFI_MIN_OPEN_INTEREST,
        ),
        "max_impact_spread_pct": _safe_float(
            tradfi.get("max_impact_spread_pct"),
            DEFAULT_TRADFI_MAX_IMPACT_SPREAD_PCT,
        ),
        "use_open_interest_filter": bool(tradfi.get("use_open_interest_filter", False)),
    }
    return hl_sel


def rank_hl_liquid_candidates(
    universe: List[Dict[str, Any]],
    ctxs: List[Dict[str, Any]],
    sel_cfg: Dict[str, Any],
    *,
    excluded_coins: Optional[Set[str]] = None,
) -> List[str]:
    """All HL perps passing selector liquidity filters, sorted by day volume (no max_pairs cap)."""
    excluded = {str(c or "").upper() for c in (excluded_coins or set()) if str(c or "").strip()}
    blacklisted = {str(c or "").upper() for c in (sel_cfg.get("blacklisted_coins") or []) if str(c or "").strip()}
    min_vol = _safe_float(sel_cfg.get("min_day_notional_volume"), DEFAULT_MIN_DAY_NOTIONAL)
    min_oi = _safe_float(sel_cfg.get("min_open_interest"), DEFAULT_MIN_OPEN_INTEREST)
    use_oi_filter = bool(sel_cfg.get("use_open_interest_filter", False))
    max_impact = _safe_float(sel_cfg.get("max_impact_spread_pct"), DEFAULT_MAX_IMPACT_SPREAD_PCT)
    tradfi_cfg = sel_cfg.get("tradfi") or {}
    tradfi_enabled = tradfi_cfg.get("enabled", True) is not False and str(
        tradfi_cfg.get("enabled", True)
    ).lower() not in {"0", "false", "no", "off"}
    tradfi_dexes = {
        str(dex).strip().lower()
        for dex in (tradfi_cfg.get("dexes") or DEFAULT_TRADFI_DEXES)
        if str(dex).strip()
    }
    tradfi_category_map = _tradfi_category_map_from_cfg(
        {"pair_selector": {"tradfi": tradfi_cfg}}
    )

    rows: List[Tuple[str, float]] = []
    for idx, asset in enumerate(universe):
        if idx >= len(ctxs):
            break
        if not isinstance(asset, dict):
            continue
        coin = pair_to_hyperliquid_coin(str(asset.get("name") or ""))
        if not coin or coin in excluded or coin in blacklisted:
            continue
        ctx = ctxs[idx] if isinstance(ctxs[idx], dict) else {}
        dex, base = _split_hl_coin(coin)
        is_tradfi = bool(dex and dex in tradfi_dexes and tradfi_category_map.get(base))
        if dex and dex in tradfi_dexes and not is_tradfi:
            continue
        if is_tradfi:
            if not tradfi_enabled:
                continue
            day_ntl = _safe_float(ctx.get("dayNtlVlm"))
            oi = _safe_float(ctx.get("openInterest"))
            spread = _impact_spread_pct(ctx)
            if (
                day_ntl >= _safe_float(
                    tradfi_cfg.get("min_day_notional_volume"),
                    DEFAULT_TRADFI_MIN_DAY_NOTIONAL,
                )
                and (
                    not bool(tradfi_cfg.get("use_open_interest_filter", False))
                    or oi >= _safe_float(
                        tradfi_cfg.get("min_open_interest"),
                        DEFAULT_TRADFI_MIN_OPEN_INTEREST,
                    )
                )
                and spread <= _safe_float(
                    tradfi_cfg.get("max_impact_spread_pct"),
                    DEFAULT_TRADFI_MAX_IMPACT_SPREAD_PCT,
                )
            ):
                rows.append((coin, day_ntl))
            continue
        day_ntl = _safe_float(ctx.get("dayNtlVlm"))
        oi = _safe_float(ctx.get("openInterest"))
        spread = _impact_spread_pct(ctx)
        if day_ntl < min_vol or (use_oi_filter and oi < min_oi) or spread > max_impact:
            continue
        rows.append((coin, day_ntl))
    rows.sort(key=lambda item: item[1], reverse=True)
    return [coin for coin, _ in rows]


async def fetch_hl_liquid_candidate_coins(
    hl_cfg: Dict[str, Any],
    global_pair_selector: Optional[Dict[str, Any]] = None,
    *,
    client: Optional[httpx.AsyncClient] = None,
    excluded_coins: Optional[Set[str]] = None,
) -> List[str]:
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=15.0)
    try:
        sel_cfg = _selector_config(hl_cfg, global_pair_selector)
        tradfi_cfg = sel_cfg.get("tradfi") or {}
        requests: List[Dict[str, Any]] = [{"type": "metaAndAssetCtxs"}]
        if _enabled(tradfi_cfg.get("enabled"), True):
            requests.extend(
                {"type": "metaAndAssetCtxs", "dex": str(dex).strip().lower()}
                for dex in (tradfi_cfg.get("dexes") or DEFAULT_TRADFI_DEXES)
                if str(dex).strip()
            )
        universe: List[Dict[str, Any]] = []
        ctxs: List[Dict[str, Any]] = []
        for req in requests:
            response = await client.post(HYPERLIQUID_INFO_URL, json=req)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list) or len(payload) < 2:
                continue
            meta = payload[0] if isinstance(payload[0], dict) else {}
            req_ctxs = payload[1] if isinstance(payload[1], list) else []
            req_universe = meta.get("universe") or []
            if not isinstance(req_universe, list):
                continue
            for idx, asset in enumerate(req_universe):
                if idx >= len(req_ctxs) or not isinstance(asset, dict):
                    continue
                universe.append(asset)
                ctxs.append(req_ctxs[idx] if isinstance(req_ctxs[idx], dict) else {})
    except Exception:
        return []
    finally:
        if owns_client and client is not None:
            await client.aclose()
    return rank_hl_liquid_candidates(universe, ctxs, sel_cfg, excluded_coins=excluded_coins)


def build_hyperliquid_watchlist(
    hl_cfg: Dict[str, Any],
    mids: Dict[str, float],
    open_trades: List[Dict[str, Any]],
    closed_trades: Optional[List[Dict[str, Any]]] = None,
    hl_selected_pairs: Optional[List[str]] = None,
    *,
    trading_status: Optional[Dict[str, Any]] = None,
    paper_summary: Optional[Dict[str, Any]] = None,
    global_pair_selector: Optional[Dict[str, Any]] = None,
    liquid_candidate_coins: Optional[List[str]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Build watchlist rows and global block metadata.

    Universe = active selector + open positions + blacklisted + runtime-blocked;
    when global_entry_block is active, also includes all liquid HL candidates.
    """
    closed_trades = list(closed_trades or [])
    sel_cfg = _selector_config(hl_cfg, global_pair_selector)
    blacklisted: Set[str] = {
        str(c or "").upper().strip()
        for c in (sel_cfg.get("blacklisted_coins") or [])
        if str(c or "").strip()
    }

    active_selected = [
        pair_to_hyperliquid_coin(p) for p in (hl_selected_pairs or []) if str(p).strip()
    ]
    active_set = set(active_selected)

    open_by_coin: Dict[str, List[Dict[str, Any]]] = {}
    for trade in open_trades or []:
        coin = pair_to_hyperliquid_coin(str(trade.get("coin") or trade.get("pair") or ""))
        if coin:
            open_by_coin.setdefault(coin, []).append(trade)

    block_hours = _safe_float(hl_cfg.get("block_after_negative_realized_hours"), 4.0)
    per_coin_blocks = runtime_blocked_coins(
        open_trades, closed_trades, realized_block_hours=block_hours
    )
    runtime_blocked_set = set(per_coin_blocks.keys())
    side_blocks_by_coin: Dict[str, Dict[str, Dict[str, Any]]] = {}
    side_blocked_set: Set[str] = set()
    side_block_source_coins = {
        pair_to_hyperliquid_coin(
            str(t.get("coin") or t.get("pair") or t.get("source_pair") or "")
        )
        for t in list(open_trades or []) + list(closed_trades or [])
    }
    for coin in sorted(c for c in side_block_source_coins if c):
        block_by_side = coin_entry_block_by_side(
            coin,
            open_trades,
            closed_trades,
            realized_block_hours=block_hours,
        )
        if _blocked_sides(block_by_side):
            side_blocks_by_coin[coin] = block_by_side
            side_blocked_set.add(coin)

    global_block = global_entry_block(
        hl_cfg,
        trading_status=trading_status,
        open_trades=open_trades,
        closed_trades=closed_trades,
        paper_summary=paper_summary,
    )
    global_active = bool(global_block.get("entryBlocked"))

    universe: Set[str] = set(active_set)
    universe.update(open_by_coin.keys())
    universe.update(blacklisted)
    universe.update(runtime_blocked_set)
    universe.update(side_blocked_set)
    if global_active and liquid_candidate_coins:
        universe.update(liquid_candidate_coins)

    watchlist: List[Dict[str, Any]] = []
    for coin in sorted(universe):
        if not coin:
            continue
        mid = mids.get(coin)
        coin_opens = open_by_coin.get(coin, [])
        has_open = bool(coin_opens)
        first_open = coin_opens[0] if coin_opens else {}

        block_by_side = side_blocks_by_coin.get(coin) or coin_entry_block_by_side(
            coin, open_trades, closed_trades, realized_block_hours=block_hours
        )
        blocked_sides = _blocked_sides(block_by_side)
        first_side_block = next(
            (
                block_by_side[side]
                for side in ("long", "short")
                if block_by_side.get(side, {}).get("entryBlocked")
            ),
            {
                "entryBlocked": False,
                "entryBlockReason": None,
                "entryBlockUntil": None,
                "entryBlockMessage": "",
            },
        )

        in_active = coin in active_set
        is_blacklisted = coin in blacklisted
        excluded_from_selector = coin in runtime_blocked_set and not in_active

        if is_blacklisted:
            list_membership = "blacklisted"
            entry_block = {
                "entryBlocked": True,
                "entryBlockReason": "blacklisted",
                "entryBlockUntil": None,
                "entryBlockMessage": "coin is in pair_selector.blacklisted_coins",
            }
            effective_blocked_sides = ["long", "short"]
        elif global_active:
            list_membership = "global_halt"
            entry_block = dict(global_block)
            entry_block["entryBlocked"] = True
            effective_blocked_sides = ["long", "short"]
        elif excluded_from_selector:
            list_membership = "excluded_selector"
            entry_block = dict(per_coin_blocks.get(coin) or first_side_block)
            effective_blocked_sides = blocked_sides
        elif in_active:
            list_membership = "active_selector"
            entry_block = dict(first_side_block)
            effective_blocked_sides = blocked_sides
        elif has_open:
            list_membership = "open_only"
            entry_block = dict(first_side_block)
            effective_blocked_sides = blocked_sides
        elif blocked_sides:
            list_membership = "side_blocked"
            entry_block = dict(first_side_block)
            effective_blocked_sides = blocked_sides
        else:
            list_membership = "candidate_only"
            entry_block = dict(first_side_block)
            effective_blocked_sides = blocked_sides

        if not mid:
            entry_block = {
                "entryBlocked": True,
                "entryBlockReason": "no_price",
                "entryBlockUntil": None,
                "entryBlockMessage": "no Hyperliquid mid price available",
            }
            effective_blocked_sides = ["long", "short"]

        entry_block_by_side = {
            "long": dict(block_by_side.get("long") or _empty_side_block("long")),
            "short": dict(block_by_side.get("short") or _empty_side_block("short")),
        }
        if entry_block.get("entryBlocked") and not blocked_sides:
            entry_block_by_side = {
                side: {
                    **dict(entry_block),
                    "entryBlockSide": side,
                }
                for side in ("long", "short")
            }
        status_detail = _status_detail(effective_blocked_sides)

        if has_open:
            status = "open"
        elif entry_block.get("entryBlocked") and (
            set(effective_blocked_sides) == {"long", "short"}
            or entry_block.get("entryBlockReason") in {
                "blacklisted",
                "no_price",
                "engine_disabled",
                "trading_stopped",
                "max_open_positions",
                "daily_loss_halt",
                "live_kill_switch",
            }
        ):
            status = "blocked"
        elif in_active and mid:
            status = "under_analysis"
        elif mid:
            status = (
                "under_analysis"
                if list_membership in {"candidate_only", "side_blocked"}
                else "inactive"
            )
        else:
            status = "no_price"

        is_tradfi, asset_category = _tradfi_info_for_coin(coin, hl_cfg)
        display_coin = display_hyperliquid_coin(coin)
        position_price = _safe_float(first_open.get("current_price")) if first_open else 0.0
        if position_price <= 0 and first_open:
            position_price = _safe_float(first_open.get("entry_price"))
        watchlist.append({
            "coin": coin,
            "pair": f"{coin}/USD-PERP",
            "displayCoin": display_coin,
            "displayPair": f"{display_coin}/USD-PERP",
            "midPrice": mid,
            "lastPrice": position_price if position_price > 0 else None,
            "priceSource": "hl" if mid else ("position" if position_price > 0 else None),
            "status": status,
            "statusDetail": status_detail,
            "listMembership": list_membership,
            "assetClass": "tradfi" if is_tradfi else "crypto",
            "assetCategory": asset_category,
            "inActiveUniverse": in_active,
            "excludedFromSelector": excluded_from_selector,
            "isBlacklisted": is_blacklisted,
            "globalBlockApplied": global_active,
            "entryBlockBySide": entry_block_by_side,
            "blockedSides": effective_blocked_sides,
            "hasOpenPosition": has_open,
            "openSide": first_open.get("position_side"),
            "openTradeId": first_open.get("trade_id"),
            "entryPrice": _safe_float(first_open.get("entry_price")),
            "marginUsed": _safe_float(first_open.get("margin_used")),
            "notional": _safe_float(first_open.get("notional_size")),
            "leverage": _safe_float(first_open.get("leverage")),
            "unrealizedPnl": sum(_safe_float(t.get("unrealized_pnl")) for t in coin_opens),
            **entry_block,
        })

    status_order = {
        "open": 0,
        "blocked": 1,
        "under_analysis": 2,
        "inactive": 3,
        "no_price": 4,
    }
    watchlist.sort(
        key=lambda row: (
            status_order.get(str(row.get("status") or ""), 9),
            0 if row.get("inActiveUniverse") else 1,
            str(row.get("coin") or ""),
        )
    )
    return watchlist, global_block
