"""SMA Reclaim Bull Flag engine and indicator unit tests."""

from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from strategy.indicators.consolidation_patterns import detect_consolidation
from strategy.indicators.rsi_divergence import detect_bullish_rsi_divergence
from strategy.indicators.session_filter import default_lisbon_session_config, is_session_allowed
from strategy.indicators.sma_stack import compute_sma_stack
from strategy.indicators.volume_profile import compute_volume_profile, nearest_hvn_below
from strategy.playbooks.sma_reclaim_bull_flag_engine import (
    EngineParams,
    evaluate_sma_reclaim_bull_flag,
)


def _ohlcv(n: int, *, base: float = 100.0, drift: float = 0.0, noise: float = 0.002) -> pd.DataFrame:
    idx = pd.date_range("2024-06-01", periods=n, freq="5min", tz="UTC")
    rng = np.random.default_rng(42)
    steps = drift + rng.normal(0, noise, n)
    close = base * np.cumprod(1 + steps)
    high = close * 1.001
    low = close * 0.999
    vol = rng.uniform(800, 1200, n)
    return pd.DataFrame(
        {"open": close * 0.9995, "high": high, "low": low, "close": close, "volume": vol},
        index=idx,
    )


def test_insufficient_candles_hold():
    params = EngineParams()
    md = {"5m": _ohlcv(50)}
    result = evaluate_sma_reclaim_bull_flag(md, params)
    assert result.signal == "hold"
    assert result.invalidation_reason == "insufficient_candles"
    assert result.indicators["skip_reason"] == "insufficient_candles"


def test_session_blocked_hold():
    params = EngineParams()
    md = {"5m": _ohlcv(260), "1m": _ohlcv(260)}
    cfg = default_lisbon_session_config()
    mon_am = datetime(2024, 6, 3, 9, 0, tzinfo=ZoneInfo("Europe/Lisbon"))
    result = evaluate_sma_reclaim_bull_flag(md, params, session_cfg=cfg, now=mon_am)
    assert result.signal == "hold"
    assert "session" in result.invalidation_reason


def test_volume_profile_poc_and_hvn():
    df = _ohlcv(150, base=50.0)
    profile = compute_volume_profile(df, num_bins=48, lookback_bars=120)
    assert profile is not None
    assert profile.poc_price > 0
    hvn = nearest_hvn_below(profile, reference_price=float(df["close"].iloc[-1]))
    assert hvn is None or hvn < float(df["close"].iloc[-1])


def test_consolidation_tight_range_detected():
    df = _ohlcv(220, drift=0.0001)
    tail = df.tail(12).copy()
    mid = float(tail["close"].iloc[-1])
    tail["high"] = mid * 1.002
    tail["low"] = mid * 0.998
    df.iloc[-12:] = tail
    cons = detect_consolidation(df, min_bars=8, max_width_pct=0.03)
    assert cons.detected is True
    assert cons.pattern_type in ("bull_flag", "wedge", "channel")


def test_bullish_divergence_synthetic():
    n = 80
    idx = pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC")
    close = np.linspace(110, 95, n)
    close[-15:] = np.linspace(94, 92, 15)
    close[-5:] = np.linspace(93, 94, 5)
    rsi_manual = np.linspace(40, 25, n)
    rsi_manual[-15:] = np.linspace(28, 32, 15)
    df = pd.DataFrame(
        {
            "open": close,
            "high": close * 1.001,
            "low": close * 0.999,
            "close": close,
            "volume": np.full(n, 1000.0),
        },
        index=idx,
    )
    div = detect_bullish_rsi_divergence(df, lookback_bars=60)
    assert isinstance(div.detected, bool)


def test_sma_stack_compression():
    df = _ohlcv(260, drift=0.0002)
    stack = compute_sma_stack(df, selloff_lookback=60)
    assert stack is not None
    assert 21 in stack.smas
    assert stack.compression_spread_pct >= 0


def test_blocked_regime_hold():
    params = EngineParams()
    md = {"5m": _ohlcv(260), "1m": _ohlcv(260)}
    tue = datetime(2024, 6, 4, 14, 0, tzinfo=ZoneInfo("Europe/Lisbon"))
    result = evaluate_sma_reclaim_bull_flag(
        md, params, market_regime="trending_down", now=tue
    )
    assert result.signal == "hold"
    assert result.invalidation_reason == "blocked_regime"
