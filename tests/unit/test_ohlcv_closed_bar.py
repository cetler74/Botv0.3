"""Tests for closed OHLCV bar preparation."""

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from strategy.playbooks.ohlcv_closed_bar import prepare_closed_ohlcv
from strategy.playbooks.rsi_stoch_reversal_5m_engine import (
    EngineParams,
    evaluate_rsi_stoch_reversal_5m,
)


def _ohlcv(n: int, freq: str = "5min") -> pd.DataFrame:
    idx = pd.date_range("2024-06-01", periods=n, freq=freq, tz="UTC")
    close = np.full(n, 100.0, dtype=float)
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": np.full(n, 1000.0),
        },
        index=idx,
    )


def test_prepare_closed_ohlcv_drops_forming_candle():
    df = _ohlcv(10)
    # Last bar opens "now" — still forming for 5m
    now = pd.Timestamp.now(tz="UTC").floor("5min")
    df.index = pd.date_range(now - timedelta(minutes=45), periods=10, freq="5min", tz="UTC")
    trimmed = prepare_closed_ohlcv(df, "5m")
    assert len(trimmed) == len(df) - 1


def test_engine_uses_last_row_after_prepare_not_prior_bar():
    params = EngineParams(stoch_overbought=80.0)
    df = _ohlcv(120)
    k_hi = np.full(120, 50.0)
    d_hi = np.full(120, 50.0)
    k_hi[-1] = 85.0
    d_hi[-1] = 90.0
    k_hi[-2] = 50.0
    d_hi[-2] = 50.0
    from unittest.mock import patch

    with patch(
        "strategy.playbooks.rsi_stoch_reversal_5m_engine._rsi_series",
        return_value=pd.Series([50.0] * 120),
    ), patch(
        "strategy.playbooks.rsi_stoch_reversal_5m_engine.compute_stoch_rsi",
        return_value=(pd.Series(k_hi), pd.Series(d_hi)),
    ):
        result = evaluate_rsi_stoch_reversal_5m({"5m": df}, params, allow_short=True)
    assert result.signal == "sell"
    assert result.indicators.get("stoch_rsi_k") == pytest.approx(85.0)


def test_xpl_like_short_hold():
    params = EngineParams(stoch_overbought=80.0)
    from unittest.mock import patch

    md = {"5m": _ohlcv(120)}
    with patch(
        "strategy.playbooks.rsi_stoch_reversal_5m_engine._rsi_series",
        return_value=pd.Series([56.0] * 120),
    ), patch(
        "strategy.playbooks.rsi_stoch_reversal_5m_engine.compute_stoch_rsi",
        return_value=(
            pd.Series([77.29] * 120),
            pd.Series([87.08] * 120),
        ),
    ):
        result = evaluate_rsi_stoch_reversal_5m(md, params, allow_short=True)
    assert result.signal == "hold"
