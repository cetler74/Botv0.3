"""Drop in-progress OHLCV rows so strategies evaluate the last fully closed bar."""

from __future__ import annotations

from typing import Optional

import pandas as pd

TIMEFRAME_SECONDS: dict[str, int] = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "8h": 28800,
    "12h": 43200,
    "1d": 86400,
    "3d": 259200,
    "1w": 604800,
}


def timeframe_seconds(timeframe: str) -> int:
    return TIMEFRAME_SECONDS.get(str(timeframe or "").lower().strip(), 0)


def prepare_closed_ohlcv(
    df: pd.DataFrame,
    timeframe: str = "5m",
) -> pd.DataFrame:
    """
    Return OHLCV with only fully closed candles.

    If the last row's bar close time (open + interval) is still in the future,
  that row is the exchange's forming candle and is removed.
    """
    if df is None or df.empty:
        return df
    out = df.copy()
    tf_sec = timeframe_seconds(timeframe)
    if tf_sec <= 0 or len(out) < 1:
        return out

    try:
        last_ts = out.index[-1]
        now_utc = pd.Timestamp.utcnow()
        if getattr(last_ts, "tzinfo", None) is None:
            last_ts = pd.Timestamp(last_ts).tz_localize("UTC")
        else:
            last_ts = pd.Timestamp(last_ts).tz_convert("UTC")
        bar_close = last_ts + pd.Timedelta(seconds=tf_sec)
        if bar_close > now_utc:
            out = out.iloc[:-1]
    except (TypeError, ValueError, IndexError):
        pass
    return out


def last_closed_bar_index(df: pd.DataFrame) -> int:
    """Index of the most recent closed bar after ``prepare_closed_ohlcv``."""
    if df is None or len(df) < 1:
        return -1
    return -1
