"""Stochastic RSI via pandas_ta."""

from __future__ import annotations

from typing import Optional, Tuple

import pandas as pd

try:
    import pandas_ta as ta
except ImportError:  # pragma: no cover
    ta = None


def compute_stoch_rsi(
    close: pd.Series,
    *,
    rsi_length: int = 14,
    stoch_length: int = 14,
    k_smooth: int = 3,
    d_smooth: int = 3,
) -> Tuple[Optional[pd.Series], Optional[pd.Series]]:
    """Return (%K, %D) StochRSI series."""
    if ta is None or close is None or len(close) < rsi_length + stoch_length + 2:
        return None, None
    try:
        result = ta.stochrsi(
            close.astype(float),
            length=stoch_length,
            rsi_length=rsi_length,
            k=k_smooth,
            d=d_smooth,
        )
    except Exception:
        return None, None
    if result is None or result.empty:
        return None, None
    # pandas_ta columns are STOCHRSIk_* / STOCHRSId_*; .upper() yields STOCHRSIK / STOCHRSID.
    k_col = next(
        (c for c in result.columns if str(c).upper().startswith("STOCHRSIK")),
        None,
    )
    d_col = next(
        (c for c in result.columns if str(c).upper().startswith("STOCHRSID")),
        None,
    )
    if not k_col or not d_col:
        return None, None
    return result[k_col], result[d_col]


def stoch_rsi_bullish(k: float, d: float, *, oversold: float = 0.2) -> bool:
    """Soft bullish: rising from oversold zone with K above D."""
    if k != k or d != d:  # NaN
        return False
    return k <= oversold * 100 or (k > d and k < 50)
