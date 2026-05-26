"""Shared filters for trading pair selection (stablecoins, fiat-pegged bases)."""

from __future__ import annotations

import logging
from typing import FrozenSet, Iterable, List, Optional, Sequence, Set

import httpx

logger = logging.getLogger(__name__)

# Base assets that are stablecoins or fiat-pegged — never trade these as the long leg.
DEFAULT_STABLECOIN_BASES: FrozenSet[str] = frozenset(
    {
        "USDT",
        "USDC",
        "USDE",
        "DAI",
        "BUSD",
        "TUSD",
        "FDUSD",
        "PYUSD",
        "USDD",
        "FRAX",
        "LUSD",
        "GUSD",
        "USDP",
        "USD1",
        "RLUSD",
        "USN",
        "USTC",
        "SUSD",
        "CUSD",
        "USDJ",
        "USDK",
        "USDS",
        "USDX",
        "EUR",
        "EURS",
        "EURI",
        "AEUR",
        "EURC",
        "EURT",
        "GBP",
        "JPY",
        "AUD",
        "CAD",
        "SGD",
        "TRY",
        "BRL",
        "PAX",
    }
)


def parse_pair_base(pair: str) -> str:
    if not pair or "/" not in pair:
        return ""
    return pair.split("/", 1)[0].strip().upper()


def is_stablecoin_base_asset(
    symbol: str,
    stable_bases: Optional[Set[str] | FrozenSet[str]] = None,
) -> bool:
    """True when a coin/base symbol (e.g. HL perp name or spot base) is stablecoin/fiat-pegged."""
    if stable_bases is None:
        stable_bases = DEFAULT_STABLECOIN_BASES
    if not stable_bases:
        return False
    base = str(symbol or "").strip().upper()
    if not base:
        return False
    if base in stable_bases:
        return True
    if "/" in base:
        return parse_pair_base(base) in stable_bases
    return False


def is_stablecoin_pair(pair: str, stable_bases: Optional[Set[str] | FrozenSet[str]] = None) -> bool:
    """True when the pair's base asset is a stablecoin / fiat-pegged token."""
    if stable_bases is None:
        stable_bases = DEFAULT_STABLECOIN_BASES
    base = parse_pair_base(pair)
    return bool(base) and base in stable_bases


def filter_stablecoin_pairs(
    pairs: Sequence[str],
    stable_bases: Optional[Set[str] | FrozenSet[str]] = None,
) -> List[str]:
    """Drop pairs whose base asset is a stablecoin."""
    if stable_bases is None:
        stable_bases = DEFAULT_STABLECOIN_BASES
    if not stable_bases:
        return list(pairs)
    kept: List[str] = []
    removed: List[str] = []
    for pair in pairs:
        if is_stablecoin_pair(pair, stable_bases):
            removed.append(pair)
        else:
            kept.append(pair)
    if removed:
        logger.info("[PairFilter] Excluded stablecoin pairs: %s", sorted(removed))
    return kept


async def load_stablecoin_bases_from_config(
    config_service_url: str = "http://config-service:8001",
) -> FrozenSet[str]:
    """Load stablecoin base list from pair_selector config; empty set = filter disabled."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(f"{config_service_url}/api/v1/config/all")
            if response.status_code != 200:
                return DEFAULT_STABLECOIN_BASES
            pair_selector = response.json().get("pair_selector") or {}
            if not pair_selector.get("exclude_stablecoin_bases", True):
                return frozenset()
            assets = pair_selector.get("stablecoin_base_assets")
            if not assets:
                return DEFAULT_STABLECOIN_BASES
            return frozenset(str(a).strip().upper() for a in assets if str(a).strip())
    except Exception as exc:
        logger.warning("[PairFilter] Config load failed (%s); using defaults", exc)
        return DEFAULT_STABLECOIN_BASES


def filter_stablecoin_pairs_with_bases(
    pairs: Iterable[str],
    stable_bases: FrozenSet[str],
) -> List[str]:
    if not stable_bases:
        return list(pairs)
    return filter_stablecoin_pairs(list(pairs), stable_bases)
