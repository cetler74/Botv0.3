"""Lightweight grid: regime detector on synthetic OHLCV patterns."""
import numpy as np
import pandas as pd
import pytest

pytest.importorskip(
    "pandas_ta",
    reason=(
        "Install: python3 -m pip install -r requirements-test.txt "
        "(pandas-ta on 3.12, pandas-ta-openbb on 3.13+), or use strategy-service Docker."
    ),
)
from strategy.market_regime_detector import MarketRegimeDetector, MarketRegime


def _trending_up_frame(n: int = 80) -> pd.DataFrame:
    t = np.arange(n, dtype=float)
    close = 100.0 + 0.4 * t + np.random.default_rng(1).normal(0, 0.15, n)
    high = close + 0.5
    low = close - 0.5
    open_ = np.r_[close[0], close[:-1]]
    vol = np.full(n, 1e5) * (1.0 + 0.01 * np.sin(t / 3.0))
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": vol})


def _chop_frame(n: int = 80) -> pd.DataFrame:
    close = 100.0 + np.sin(np.linspace(0, 8 * np.pi, n)) * 0.8
    high = close + 0.3
    low = close - 0.3
    open_ = np.r_[close[0], close[:-1]]
    vol = np.full(n, 8e4)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": vol})


def test_regime_detector_uptrend_not_low_vol_only():
    det = MarketRegimeDetector()
    df = _trending_up_frame()
    regime, analysis = det.detect_regime(df, pair="TEST/USDC")
    assert regime in {
        MarketRegime.TRENDING_UP,
        MarketRegime.BREAKOUT,
        MarketRegime.HIGH_VOLATILITY,
    }, f"got {regime} scores={analysis.get('regime_scores')}"


def test_regime_detector_chop_produces_scores():
    det = MarketRegimeDetector()
    df = _chop_frame()
    regime, analysis = det.detect_regime(df, pair="TEST/USDC")
    scores = analysis.get("regime_scores") or {}
    assert isinstance(regime, MarketRegime)
    assert len(scores) >= 1
