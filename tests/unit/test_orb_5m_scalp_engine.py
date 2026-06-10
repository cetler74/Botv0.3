"""Unit tests for ORB 5m scalp engine."""

from __future__ import annotations

import pandas as pd

from strategy.playbooks.orb_5m_scalp_engine import EngineParams, evaluate_orb_5m_scalp


def _bullish_session_df() -> pd.DataFrame:
    session_date = "2025-06-02"
    rows = []
    times = []
    base = pd.Timestamp(f"{session_date} 12:00:00", tz="UTC")
    for i in range(80):
        ts = base + pd.Timedelta(minutes=5 * i)
        times.append(ts)
        rows.append({"open": 99.0, "high": 100.0, "low": 98.0, "close": 99.0, "volume": 1000})
    or_idx = 18
    times[or_idx] = pd.Timestamp(f"{session_date} 13:30:00", tz="UTC")
    rows[or_idx] = {"open": 99.0, "high": 100.0, "low": 98.0, "close": 99.5, "volume": 1000}
    rows[or_idx + 1] = {"open": 99.5, "high": 99.8, "low": 98.5, "close": 99.2, "volume": 1000}
    rows[or_idx + 2] = {"open": 99.2, "high": 101.5, "low": 99.0, "close": 101.0, "volume": 1000}
    rows[or_idx + 3] = {"open": 101.0, "high": 101.8, "low": 99.8, "close": 101.5, "volume": 1000}
    return pd.DataFrame(rows, index=times)


def test_long_signal_emits_buy_with_contract_fields():
    df = _bullish_session_df()
    now = pd.Timestamp("2025-06-02 14:00:00", tz="UTC").to_pydatetime()
    params = EngineParams(min_candles=30)
    result = evaluate_orb_5m_scalp({"5m": df}, params, now=now)
    ind = result.indicators
    assert ind.get("setup") == "orb_5m_scalp"
    if result.signal == "buy":
        assert ind.get("session_state") == "signal"
        assert ind.get("breakout_valid") is True
        assert ind.get("retest_valid") is True
        assert ind.get("reward_risk") == 2.0
        assert ind.get("or_high") == 100.0
        assert ind.get("or_low") == 98.0
        assert ind.get("or_mid") == 99.0
        assert "ORB 5m LONG" in (ind.get("entry_reason") or "")


def test_hold_when_or_candle_missing():
    df = _bullish_session_df()
    df = df.iloc[:10]
    params = EngineParams(min_candles=5)
    result = evaluate_orb_5m_scalp({"5m": df}, params)
    assert result.signal == "hold"
    assert result.indicators.get("session_state") == "invalid"
    assert "or_candle_missing" in (result.indicators.get("rejection_reason") or "")


def test_hold_when_regime_blocked():
    df = _bullish_session_df()
    params = EngineParams(min_candles=30, blocked_regimes=["low_volatility"])
    result = evaluate_orb_5m_scalp({"5m": df}, params, market_regime="low_volatility")
    assert result.signal == "hold"
    assert "regime_blocked" in result.invalidation_reason
