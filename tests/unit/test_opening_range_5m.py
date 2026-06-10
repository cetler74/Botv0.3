"""Unit tests for opening range 5m indicator helpers."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from strategy.indicators.opening_range_5m import (
    find_session_or_candle,
    scan_orb_retest_fsm,
)


def _session_df(session_date: str = "2025-06-02") -> pd.DataFrame:
    """Build 5m bars for one ET session day (EDT: 9:30 = 13:30 UTC)."""
    rows = []
    times = []
    base = pd.Timestamp(f"{session_date} 12:00:00", tz="UTC")
    for i in range(24):
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


def test_find_session_or_candle_locates_930_et_bar():
    df = _session_df()
    or_candle = find_session_or_candle(df, session_date="2025-06-02")
    assert or_candle is not None
    assert or_candle.or_high == 100.0
    assert or_candle.or_low == 98.0
    assert or_candle.or_mid == 99.0


def test_bullish_breakout_retest_fires_on_current_bar():
    df = _session_df()
    or_candle = find_session_or_candle(df, session_date="2025-06-02")
    assert or_candle is not None
    current_idx = or_candle.or_idx + 3
    result = scan_orb_retest_fsm(df, or_candle, current_idx)
    assert result.fire_signal is True
    assert result.session_state == "signal"
    assert result.direction == "long"
    assert result.breakout_valid is True
    assert result.retest_valid is True
    assert result.entry_price == 101.5
    assert result.stop_hint == 99.0
    assert result.target_hint == 106.5
    assert result.reward_risk == 2.0


def test_wick_only_breakout_stays_waiting():
    df = _session_df()
    or_candle = find_session_or_candle(df, session_date="2025-06-02")
    idx = or_candle.or_idx + 2
    df.iloc[idx] = [99.0, 101.5, 97.5, 99.5, 1000]
    result = scan_orb_retest_fsm(df, or_candle, idx)
    assert result.session_state == "waiting_breakout"
    assert result.breakout_valid is False


def test_invalid_retest_inside_range_keeps_waiting():
    df = _session_df()
    or_candle = find_session_or_candle(df, session_date="2025-06-02")
    bo = or_candle.or_idx + 2
    df.iloc[bo] = [99.0, 101.5, 99.0, 101.0, 1000]
    bad = or_candle.or_idx + 3
    df.iloc[bad] = [101.0, 101.2, 99.5, 99.8, 1000]
    result = scan_orb_retest_fsm(df, or_candle, bad)
    assert result.session_state == "waiting_retest"
    assert result.retest_valid is False
    assert result.retest_reason == "retest_closed_inside_range"


def test_bearish_breakout_retest():
    df = _session_df()
    or_candle = find_session_or_candle(df, session_date="2025-06-02")
    bo = or_candle.or_idx + 2
    df.iloc[bo] = [99.0, 98.5, 97.0, 97.5, 1000]
    rt = or_candle.or_idx + 3
    df.iloc[rt] = [97.5, 98.2, 97.8, 97.2, 1000]
    result = scan_orb_retest_fsm(df, or_candle, rt)
    assert result.fire_signal is True
    assert result.direction == "short"
    assert result.stop_hint == 99.0
