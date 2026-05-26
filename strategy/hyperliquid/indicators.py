"""OHLCV helpers for Hyperliquid perp strategies."""

from __future__ import annotations

from typing import Any, Dict, Optional

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
