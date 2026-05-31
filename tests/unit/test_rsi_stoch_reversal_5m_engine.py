"""RSI + StochRSI 5m reversal engine tests."""

from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from strategy.playbooks.rsi_stoch_reversal_5m_engine import (
    EngineParams,
    evaluate_rsi_stoch_reversal_5m,
)


def _ohlcv(n: int, *, base: float = 100.0) -> pd.DataFrame:
    idx = pd.date_range("2024-06-01", periods=n, freq="5min", tz="UTC")
    close = np.full(n, base, dtype=float)
    return pd.DataFrame(
        {
            "open": close * 0.999,
            "high": close * 1.001,
            "low": close * 0.999,
            "close": close,
            "volume": np.full(n, 1000.0),
        },
        index=idx,
    )


def test_insufficient_candles_hold():
    params = EngineParams()
    result = evaluate_rsi_stoch_reversal_5m({"5m": _ohlcv(20)}, params)
    assert result.signal == "hold"
    assert result.invalidation_reason == "insufficient_candles"


def test_long_signal_when_rsi_and_stoch_oversold_and_k_above_d():
    params = EngineParams(rsi_oversold=30.0, stoch_oversold=30.0)
    md = {"5m": _ohlcv(120)}
    with patch(
        "strategy.playbooks.rsi_stoch_reversal_5m_engine._rsi_series"
    ) as mock_rsi, patch(
        "strategy.playbooks.rsi_stoch_reversal_5m_engine.compute_stoch_rsi"
    ) as mock_stoch:
        mock_rsi.return_value = pd.Series([25.0] * 120)
        mock_stoch.return_value = (
            pd.Series([22.0] * 120),
            pd.Series([18.0] * 120),
        )
        result = evaluate_rsi_stoch_reversal_5m(md, params, allow_short=True)
    assert result.signal == "buy"
    assert result.confidence == pytest.approx(0.72)
    assert result.indicators.get("side_intent") == "long"


def test_long_hold_when_rsi_low_but_stoch_not_both_below_30():
    params = EngineParams(rsi_oversold=30.0, stoch_oversold=30.0)
    md = {"5m": _ohlcv(120)}
    with patch(
        "strategy.playbooks.rsi_stoch_reversal_5m_engine._rsi_series"
    ) as mock_rsi, patch(
        "strategy.playbooks.rsi_stoch_reversal_5m_engine.compute_stoch_rsi"
    ) as mock_stoch:
        mock_rsi.return_value = pd.Series([25.0] * 120)
        mock_stoch.return_value = (
            pd.Series([40.0] * 120),
            pd.Series([20.0] * 120),
        )
        result = evaluate_rsi_stoch_reversal_5m(md, params, allow_short=True)
    assert result.signal == "hold"


def test_evaluates_last_row_not_prior_closed_bar():
    """After callers drop the forming candle, index -1 is the latest closed bar."""
    params = EngineParams(stoch_overbought=80.0)
    md = {"5m": _ohlcv(120)}
    rsi = pd.Series([50.0] * 120)
    k = pd.Series([50.0] * 120)
    d = pd.Series([50.0] * 120)
    k.iloc[-1] = 85.0
    d.iloc[-1] = 90.0
    d.iloc[-2] = 50.0
    with patch(
        "strategy.playbooks.rsi_stoch_reversal_5m_engine._rsi_series",
        return_value=rsi,
    ), patch(
        "strategy.playbooks.rsi_stoch_reversal_5m_engine.compute_stoch_rsi",
        return_value=(k, d),
    ):
        result = evaluate_rsi_stoch_reversal_5m(md, params, allow_short=True)
    assert result.signal == "sell"
    assert result.indicators.get("stoch_rsi_k") == pytest.approx(85.0)


def test_short_signal_when_stoch_both_above_80_and_d_above_k():
    params = EngineParams(stoch_overbought=80.0)
    md = {"5m": _ohlcv(120)}
    with patch(
        "strategy.playbooks.rsi_stoch_reversal_5m_engine._rsi_series"
    ) as mock_rsi, patch(
        "strategy.playbooks.rsi_stoch_reversal_5m_engine.compute_stoch_rsi"
    ) as mock_stoch:
        mock_rsi.return_value = pd.Series([50.0] * 120)
        mock_stoch.return_value = (
            pd.Series([85.0] * 120),
            pd.Series([90.0] * 120),
        )
        result = evaluate_rsi_stoch_reversal_5m(md, params, allow_short=True)
    assert result.signal == "sell"
    assert result.indicators.get("side_intent") == "short"
    assert "StochRSI short" in (result.indicators.get("entry_reason") or "")


def test_short_hold_when_only_rsi_high_not_stoch():
    params = EngineParams(stoch_overbought=80.0)
    md = {"5m": _ohlcv(120)}
    with patch(
        "strategy.playbooks.rsi_stoch_reversal_5m_engine._rsi_series"
    ) as mock_rsi, patch(
        "strategy.playbooks.rsi_stoch_reversal_5m_engine.compute_stoch_rsi"
    ) as mock_stoch:
        mock_rsi.return_value = pd.Series([85.0] * 120)
        mock_stoch.return_value = (
            pd.Series([70.0] * 120),
            pd.Series([75.0] * 120),
        )
        result = evaluate_rsi_stoch_reversal_5m(md, params, allow_short=True)
    assert result.signal == "hold"


def test_spot_disallows_short():
    params = EngineParams(stoch_overbought=80.0)
    md = {"5m": _ohlcv(120)}
    with patch(
        "strategy.playbooks.rsi_stoch_reversal_5m_engine._rsi_series"
    ) as mock_rsi, patch(
        "strategy.playbooks.rsi_stoch_reversal_5m_engine.compute_stoch_rsi"
    ) as mock_stoch:
        mock_rsi.return_value = pd.Series([50.0] * 120)
        mock_stoch.return_value = (
            pd.Series([85.0] * 120),
            pd.Series([90.0] * 120),
        )
        result = evaluate_rsi_stoch_reversal_5m(md, params, allow_short=False)
    assert result.signal == "hold"


def test_blocked_regime():
    params = EngineParams(blocked_regimes=["trending_down"])
    md = {"5m": _ohlcv(120)}
    result = evaluate_rsi_stoch_reversal_5m(
        md, params, market_regime="trending_down", allow_short=True
    )
    assert result.signal == "hold"
    assert "regime_blocked" in result.invalidation_reason
