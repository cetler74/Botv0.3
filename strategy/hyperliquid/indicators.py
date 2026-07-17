"""OHLCV helpers for Hyperliquid perp strategies."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd


def ohlcv_dict_to_df(data: Dict[str, Any]) -> Optional[pd.DataFrame]:
    if not data:
        return None
    try:
        df = pd.DataFrame(
            {
                "timestamp": data.get("timestamp", []),
                "open": data.get("open", []),
                "high": data.get("high", []),
                "low": data.get("low", []),
                "close": data.get("close", []),
                "volume": data.get("volume", []),
            }
        )
        if df.empty:
            return None
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True, errors="coerce")
            df = df.set_index("timestamp")
        return df
    except Exception:
        return None


def closed_bar_snapshot(
    frame: pd.DataFrame,
    timeframe: str,
    *,
    now: Optional[datetime] = None,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Return finite OHLCV bars whose configured interval has closed."""
    intervals = {
        "1m": pd.Timedelta(minutes=1),
        "1h": pd.Timedelta(hours=1),
        "15m": pd.Timedelta(minutes=15),
    }
    if timeframe not in intervals:
        raise ValueError("timeframe must be 1m, 15m, or 1h")
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise ValueError("OHLCV frame cannot be empty")
    required = ("open", "high", "low", "close", "volume")
    if missing := [column for column in required if column not in frame.columns]:
        raise ValueError(f"OHLCV frame missing columns: {', '.join(missing)}")
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise ValueError("OHLCV frame requires a DatetimeIndex")
    normalized = frame.loc[:, required].copy().sort_index()
    normalized.index = (
        normalized.index.tz_localize("UTC")
        if normalized.index.tz is None
        else normalized.index.tz_convert("UTC")
    )
    if normalized.index.has_duplicates:
        raise ValueError("OHLCV timestamps must be unique")
    normalized = normalized.apply(pd.to_numeric, errors="coerce")
    if not np.isfinite(normalized.to_numpy(dtype=float)).all():
        raise ValueError("OHLCV values must be finite")

    cutoff = pd.Timestamp(now or datetime.now(timezone.utc))
    if cutoff.tzinfo is None:
        cutoff = cutoff.tz_localize("UTC")
    else:
        cutoff = cutoff.tz_convert("UTC")
    closed = normalized.loc[normalized.index + intervals[timeframe] <= cutoff]
    if closed.empty:
        raise ValueError("no closed OHLCV bars available")
    return closed, {
        "bar_closed": True,
        "bar_time": closed.index[-1].isoformat(),
        "bar_count": int(len(closed)),
        "dropped_forming_bar": bool(len(closed) != len(normalized)),
    }
