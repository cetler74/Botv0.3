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


def test_long_signal_allows_stoch_k_equal_d():
    params = EngineParams(rsi_oversold=30.0, stoch_oversold=30.0)
    md = {"5m": _ohlcv(120)}
    with patch(
        "strategy.playbooks.rsi_stoch_reversal_5m_engine._rsi_series",
        return_value=pd.Series([25.0] * 120),
    ), patch(
        "strategy.playbooks.rsi_stoch_reversal_5m_engine.compute_stoch_rsi",
        return_value=(pd.Series([18.0] * 120), pd.Series([18.0] * 120)),
    ):
        result = evaluate_rsi_stoch_reversal_5m(md, params, allow_short=True)

    assert result.signal == "buy"
    assert result.indicators.get("stoch_k_gte_d") is True


def test_1m_strong_cross_requires_gap_and_rising_k():
    params = EngineParams(
        entry_timeframe="1m",
        min_stoch_cross_gap=4.0,
        require_stoch_cross_momentum=True,
    )
    md = {"1m": _ohlcv(120)}
    k = pd.Series([20.0] * 120)
    d = pd.Series([18.0] * 120)
    k.iloc[-2:] = [19.0, 22.0]
    with patch(
        "strategy.playbooks.rsi_stoch_reversal_5m_engine._rsi_series",
        return_value=pd.Series([25.0] * 120),
    ), patch(
        "strategy.playbooks.rsi_stoch_reversal_5m_engine.compute_stoch_rsi",
        return_value=(k, d),
    ):
        result = evaluate_rsi_stoch_reversal_5m(md, params, allow_short=True)

    assert result.signal == "buy"
    assert result.indicators["long_stoch_cross_ok"] is True


def test_1m_expected_move_gate_blocks_low_range_entry():
    params = EngineParams(entry_timeframe="1m", min_expected_move_pct=0.8)
    md = {"1m": _ohlcv(120)}
    with patch(
        "strategy.playbooks.rsi_stoch_reversal_5m_engine._rsi_series",
        return_value=pd.Series([25.0] * 120),
    ), patch(
        "strategy.playbooks.rsi_stoch_reversal_5m_engine.compute_stoch_rsi",
        return_value=(pd.Series([22.0] * 120), pd.Series([18.0] * 120)),
    ):
        result = evaluate_rsi_stoch_reversal_5m(md, params, allow_short=True)

    assert result.signal == "hold"
    assert result.indicators["expected_move_pct"] < 0.8
    assert "expected_move_" in result.invalidation_reason


def test_1m_required_confirmation_blocks_when_timeframe_missing():
    params = EngineParams(
        entry_timeframe="1m",
        confirmation_timeframe="5m",
        require_confirmation=True,
    )
    md = {"1m": _ohlcv(120)}
    with patch(
        "strategy.playbooks.rsi_stoch_reversal_5m_engine._rsi_series",
        return_value=pd.Series([25.0] * 120),
    ), patch(
        "strategy.playbooks.rsi_stoch_reversal_5m_engine.compute_stoch_rsi",
        return_value=(pd.Series([22.0] * 120), pd.Series([18.0] * 120)),
    ):
        result = evaluate_rsi_stoch_reversal_5m(md, params, allow_short=True)

    assert result.signal == "hold"
    assert result.indicators["confirmation_missing"] is True


def test_long_signal_allows_tight_rsi_reclaim_after_recent_oversold():
    params = EngineParams(
        rsi_oversold=30.0,
        stoch_oversold=30.0,
        rsi_reclaim_enabled=True,
        rsi_reclaim_lookback_bars=3,
        rsi_reclaim_ceiling=32.0,
    )
    md = {"5m": _ohlcv(120)}
    rsi = pd.Series([50.0] * 120)
    rsi.iloc[-3:] = [29.4, 30.6, 31.7]
    with patch(
        "strategy.playbooks.rsi_stoch_reversal_5m_engine._rsi_series",
        return_value=rsi,
    ), patch(
        "strategy.playbooks.rsi_stoch_reversal_5m_engine.compute_stoch_rsi",
        return_value=(pd.Series([22.0] * 120), pd.Series([18.0] * 120)),
    ):
        result = evaluate_rsi_stoch_reversal_5m(md, params, allow_short=False)

    assert result.signal == "buy"
    assert result.indicators.get("rsi_reclaim_ok") is True
    assert result.indicators.get("rsi_recent_min") == pytest.approx(29.4)


def test_long_reclaim_can_bypass_configured_blocked_regime():
    params = EngineParams(
        rsi_oversold=30.0,
        stoch_oversold=30.0,
        rsi_reclaim_enabled=True,
        rsi_reclaim_lookback_bars=3,
        rsi_reclaim_ceiling=32.0,
        blocked_regimes=["trending_down"],
        blocked_regime_reclaim_bypass=["trending_down"],
    )
    md = {"5m": _ohlcv(120)}
    rsi = pd.Series([50.0] * 120)
    rsi.iloc[-3:] = [28.8, 30.2, 31.5]
    with patch(
        "strategy.playbooks.rsi_stoch_reversal_5m_engine._rsi_series",
        return_value=rsi,
    ), patch(
        "strategy.playbooks.rsi_stoch_reversal_5m_engine.compute_stoch_rsi",
        return_value=(pd.Series([21.0] * 120), pd.Series([18.0] * 120)),
    ):
        result = evaluate_rsi_stoch_reversal_5m(
            md, params, market_regime="trending_down", allow_short=False
        )

    assert result.signal == "buy"
    assert result.indicators.get("rsi_reclaim_ok") is True


def test_blocked_regime_still_blocks_without_reclaim_bypass_confirmation():
    params = EngineParams(
        rsi_oversold=30.0,
        stoch_oversold=30.0,
        rsi_reclaim_enabled=True,
        rsi_reclaim_lookback_bars=3,
        rsi_reclaim_ceiling=32.0,
        blocked_regimes=["trending_down"],
        blocked_regime_reclaim_bypass=["trending_down"],
    )
    md = {"5m": _ohlcv(120)}
    with patch(
        "strategy.playbooks.rsi_stoch_reversal_5m_engine._rsi_series",
        return_value=pd.Series([31.5] * 120),
    ), patch(
        "strategy.playbooks.rsi_stoch_reversal_5m_engine.compute_stoch_rsi",
        return_value=(pd.Series([21.0] * 120), pd.Series([18.0] * 120)),
    ):
        result = evaluate_rsi_stoch_reversal_5m(
            md, params, market_regime="trending_down", allow_short=False
        )

    assert result.signal == "hold"
    assert "regime_trending_down" in result.invalidation_reason


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
    params = EngineParams(rsi_overbought=80.0, stoch_overbought=80.0)
    md = {"5m": _ohlcv(120)}
    rsi = pd.Series([50.0] * 120)
    k = pd.Series([50.0] * 120)
    d = pd.Series([50.0] * 120)
    rsi.iloc[-1] = 85.0
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


def test_short_signal_when_rsi_and_stoch_overbought_and_d_above_k():
    params = EngineParams(rsi_overbought=80.0, stoch_overbought=80.0)
    md = {"5m": _ohlcv(120)}
    with patch(
        "strategy.playbooks.rsi_stoch_reversal_5m_engine._rsi_series"
    ) as mock_rsi, patch(
        "strategy.playbooks.rsi_stoch_reversal_5m_engine.compute_stoch_rsi"
    ) as mock_stoch:
        mock_rsi.return_value = pd.Series([85.0] * 120)
        mock_stoch.return_value = (
            pd.Series([85.0] * 120),
            pd.Series([90.0] * 120),
        )
        result = evaluate_rsi_stoch_reversal_5m(md, params, allow_short=True)
    assert result.signal == "sell"
    assert result.indicators.get("side_intent") == "short"
    assert "RSI+StochRSI short" in (result.indicators.get("entry_reason") or "")


def test_short_signal_allows_stoch_d_equal_k():
    params = EngineParams(rsi_overbought=80.0, stoch_overbought=80.0)
    md = {"5m": _ohlcv(120)}
    with patch(
        "strategy.playbooks.rsi_stoch_reversal_5m_engine._rsi_series",
        return_value=pd.Series([85.0] * 120),
    ), patch(
        "strategy.playbooks.rsi_stoch_reversal_5m_engine.compute_stoch_rsi",
        return_value=(pd.Series([90.0] * 120), pd.Series([90.0] * 120)),
    ):
        result = evaluate_rsi_stoch_reversal_5m(md, params, allow_short=True)

    assert result.signal == "sell"
    assert result.indicators.get("stoch_d_gte_k") is True


def test_setup_label_uses_entry_timeframe():
    params = EngineParams(entry_timeframe="1m")
    md = {"1m": _ohlcv(120)}
    with patch(
        "strategy.playbooks.rsi_stoch_reversal_5m_engine._rsi_series",
        return_value=pd.Series([31.0] * 120),
    ), patch(
        "strategy.playbooks.rsi_stoch_reversal_5m_engine.compute_stoch_rsi",
        return_value=(pd.Series([10.0] * 120), pd.Series([5.0] * 120)),
    ):
        result = evaluate_rsi_stoch_reversal_5m(md, params, allow_short=True)

    assert result.signal == "hold"
    assert result.indicators.get("setup") == "rsi_stoch_reversal_1m"


def test_short_hold_when_stoch_overbought_but_rsi_not_above_80():
    params = EngineParams(rsi_overbought=80.0, stoch_overbought=80.0)
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
    assert result.signal == "hold"
    assert "rsi_not_overbought" in result.invalidation_reason


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


def test_short_blocked_regime_blocks_only_short():
    params = EngineParams(short_blocked_regimes=["high_volatility"])
    md = {"5m": _ohlcv(120)}
    with patch(
        "strategy.playbooks.rsi_stoch_reversal_5m_engine._rsi_series"
    ) as mock_rsi, patch(
        "strategy.playbooks.rsi_stoch_reversal_5m_engine.compute_stoch_rsi"
    ) as mock_stoch:
        mock_rsi.return_value = pd.Series([85.0] * 120)
        mock_stoch.return_value = (
            pd.Series([85.0] * 120),
            pd.Series([90.0] * 120),
        )
        blocked = evaluate_rsi_stoch_reversal_5m(
            md, params, market_regime="high_volatility", allow_short=True
        )
        allowed = evaluate_rsi_stoch_reversal_5m(
            md, params, market_regime="sideways", allow_short=True
        )

    assert blocked.signal == "hold"
    assert "short_regime_high_volatility_blocked" in blocked.invalidation_reason
    assert allowed.signal == "sell"
