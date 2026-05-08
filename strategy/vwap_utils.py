"""Bar-based VWAP helpers (rolling window vs cumulative session anchor).

Session VWAP is computed from typical price (H+L+C)/3 and bar volume — not
exchange tape. Suitable for aligning with common day-trading course definitions
when anchored to a calendar day in a chosen timezone.
"""
from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

import pandas as pd

logger = logging.getLogger(__name__)


def rolling_vwap_hlc3(df: pd.DataFrame, period: int) -> pd.Series:
    """N-bar volume-weighted typical price."""
    typical_price = (df["high"] + df["low"] + df["close"]) / 3.0
    return (
        (typical_price * df["volume"]).rolling(window=period).sum()
        / df["volume"].rolling(window=period).sum()
    )


def session_vwap_hlc3(df: pd.DataFrame, session_tz: str = "UTC") -> pd.Series:
    """Cumulative VWAP from local midnight in ``session_tz``, reset each day."""
    typical_price = (df["high"] + df["low"] + df["close"]) / 3.0
    vol = df["volume"].astype(float)
    if isinstance(df.index, pd.DatetimeIndex):
        t = df.index
    elif "timestamp" in df.columns:
        t = pd.to_datetime(df["timestamp"], utc=True)
    else:
        logger.warning("[vwap_utils] No DatetimeIndex/timestamp; falling back to rolling(20)")
        return rolling_vwap_hlc3(df, min(20, max(2, len(df) // 2)))
    try:
        tz = ZoneInfo(session_tz)
    except Exception:
        tz = ZoneInfo("UTC")
    if getattr(t, "tz", None) is None:
        t = t.tz_localize("UTC", ambiguous="infer", nonexistent="shift_forward")
    t_local = t.tz_convert(tz)
    day = t_local.normalize()
    frame = pd.DataFrame({"tpv": typical_price * vol, "vol": vol}, index=df.index)
    frame["day"] = day
    cum_tpv = frame.groupby("day")["tpv"].cumsum()
    cum_vol = frame.groupby("day")["vol"].cumsum()
    return cum_tpv / cum_vol.replace(0.0, float("nan"))
