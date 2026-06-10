"""Infer Hyperliquid asset class/category from coin symbol."""

from __future__ import annotations

_TRADFI_CATEGORIES = {
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


def infer_hl_asset_class(coin: str) -> str:
    """Return asset class for HL coin (crypto, stocks, indices, fx, commodities)."""
    raw = str(coin or "").upper()
    base = raw.split(":", 1)[-1]
    for category, symbols in _TRADFI_CATEGORIES.items():
        if base in symbols:
            return category
    return "crypto"
