"""StochRSI indicator column detection (pandas_ta uppercase names)."""

import numpy as np
import pandas as pd

from strategy.indicators.stoch_rsi import compute_stoch_rsi


def test_compute_stoch_rsi_reads_pandas_ta_columns():
    n = 80
    close = pd.Series(np.linspace(100.0, 110.0, n) + np.random.default_rng(0).normal(0, 0.2, n))
    k, d = compute_stoch_rsi(close, rsi_length=14, stoch_length=14, k_smooth=3, d_smooth=3)
    assert k is not None and d is not None
    assert not np.isnan(float(k.iloc[-2]))
    assert not np.isnan(float(d.iloc[-2]))
