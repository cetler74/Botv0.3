"""Session vs rolling VWAP (strategy/vwap_utils.py) — pandas only."""
import numpy as np
import pandas as pd
import pytest

from strategy.vwap_utils import rolling_vwap_hlc3, session_vwap_hlc3


def test_session_vwap_resets_at_utc_midnight():
    idx = pd.to_datetime(
        [
            "2026-04-28 20:00:00+00:00",
            "2026-04-28 21:00:00+00:00",
            "2026-04-28 22:00:00+00:00",
            "2026-04-28 23:00:00+00:00",
            "2026-04-29 00:00:00+00:00",
            "2026-04-29 01:00:00+00:00",
            "2026-04-29 02:00:00+00:00",
        ]
    )
    df = pd.DataFrame(
        {
            "open": [10.0] * 4 + [20.0] * 3,
            "high": [11.0] * 4 + [21.0] * 3,
            "low": [9.0] * 4 + [19.0] * 3,
            "close": [10.0, 10.0, 10.0, 10.0, 20.0, 20.0, 20.0],
            "volume": [1.0] * 7,
        },
        index=idx,
    )
    v = session_vwap_hlc3(df, "UTC")
    assert abs(float(v.iloc[3]) - 10.0) < 1e-9
    # New UTC day: cumulative VWAP starts at first bar's typical price (20+21+19)/3
    assert abs(float(v.iloc[4]) - 20.0) < 1e-9


def test_rolling_vwap_uses_window():
    n = 10
    period = 4
    idx = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    df = pd.DataFrame(
        {
            "open": np.linspace(100, 109, n),
            "high": np.linspace(101, 110, n),
            "low": np.linspace(99, 108, n),
            "close": np.linspace(100, 109, n),
            "volume": np.ones(n),
        },
        index=idx,
    )
    v = rolling_vwap_hlc3(df, period)
    assert pd.isna(v.iloc[0])
    assert np.isfinite(v.iloc[-1])
