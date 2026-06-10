"""Prioritize spot entry scans for pairs with recent standalone BUY audit rows."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import httpx

logger = logging.getLogger(__name__)

DEFAULT_AUDIT_ENDPOINTS = (
    "ema50-breakout-pullback-analysis-logs",
    "orb-5m-scalp-analysis-logs",
    "arc-analysis-logs",
    "dual-sma-analysis-logs",
    "supply-demand-analysis-logs",
)

_PRIORITY_CACHE: Dict[Tuple[str, int], Tuple[float, Set[str]]] = {}


def order_pairs_by_priority(pairs: Iterable[str], priority: Set[str]) -> List[str]:
    """Stable sort: priority pairs first, preserving original order within each tier."""
    if not priority:
        return list(pairs)
    pri = {str(p).strip() for p in priority if str(p).strip()}
    indexed = list(enumerate(pairs))
    indexed.sort(key=lambda item: (0 if item[1] in pri else 1, item[0]))
    return [pair for _, pair in indexed]


async def _fetch_priority_entry_pairs_uncached(
    database_service_url: str,
    exchange_name: str,
    cfg: Dict[str, Any],
) -> Set[str]:
    lookback_hours = max(1, int(cfg.get("lookback_hours", 4) or 4))
    endpoints = cfg.get("audit_endpoints") or list(DEFAULT_AUDIT_ENDPOINTS)
    venue = str(exchange_name or "").strip().lower()
    if not venue:
        return set()

    priority: Set[str] = set()
    base = database_service_url.rstrip("/")
    timeout = float(cfg.get("fetch_timeout_seconds", 8.0) or 8.0)

    async with httpx.AsyncClient(timeout=timeout) as client:
        for endpoint in endpoints:
            path = str(endpoint or "").strip().lstrip("/")
            if not path:
                continue
            url = f"{base}/api/v1/analytics/{path}"
            try:
                resp = await client.get(
                    url,
                    params={"venue": venue, "hours": lookback_hours, "limit": 500},
                )
                if resp.status_code != 200:
                    continue
                payload = resp.json() or {}
                rows = payload.get("rows") or []
                if not isinstance(rows, list):
                    continue
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    if str(row.get("signal") or "").lower() != "buy":
                        continue
                    symbol = str(row.get("symbol") or "").strip()
                    if symbol:
                        priority.add(symbol)
            except Exception as exc:
                logger.debug(
                    "[EntryPairPriority] audit fetch failed venue=%s endpoint=%s: %s",
                    venue,
                    path,
                    exc,
                )
    return priority


async def fetch_priority_entry_pairs(
    database_service_url: str,
    exchange_name: str,
    cfg: Optional[Dict[str, Any]] = None,
) -> Set[str]:
    """Return symbols with signal=buy in recent standalone strategy audit logs."""
    root = cfg if isinstance(cfg, dict) else {}
    if not root.get("enabled", True):
        return set()

    lookback_hours = max(1, int(root.get("lookback_hours", 4) or 4))
    cache_ttl = float(root.get("cache_ttl_seconds", 120.0) or 120.0)
    venue = str(exchange_name or "").strip().lower()
    if not venue:
        return set()

    cache_key = (venue, lookback_hours)
    if cache_ttl > 0:
        cached = _PRIORITY_CACHE.get(cache_key)
        if cached and (time.monotonic() - cached[0]) < cache_ttl:
            return set(cached[1])

    priority = await _fetch_priority_entry_pairs_uncached(
        database_service_url, exchange_name, root
    )
    if cache_ttl > 0:
        _PRIORITY_CACHE[cache_key] = (time.monotonic(), set(priority))
    return priority
