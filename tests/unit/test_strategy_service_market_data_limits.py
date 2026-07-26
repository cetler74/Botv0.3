"""Regression coverage for strategy-service OHLCV fetch sizing."""

import importlib.util
import sys
import types
from pathlib import Path


class _MetricStub:
    def __init__(self, *args, **kwargs):
        pass

    def labels(self, *args, **kwargs):
        return self

    def inc(self, *args, **kwargs):
        return None

    def observe(self, *args, **kwargs):
        return None

    def set(self, *args, **kwargs):
        return None


def _load_strategy_service_main():
    sys.modules.setdefault(
        "prometheus_client",
        types.SimpleNamespace(
            Counter=_MetricStub,
            Histogram=_MetricStub,
            Gauge=_MetricStub,
            CollectorRegistry=_MetricStub,
            CONTENT_TYPE_LATEST="text/plain",
            generate_latest=lambda *args, **kwargs: b"",
        ),
    )
    module_path = (
        Path(__file__).resolve().parents[2]
        / "services"
        / "strategy-service"
        / "main.py"
    )
    spec = importlib.util.spec_from_file_location("strategy_service_main", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_ohlcv_fetch_limit_covers_sma200_entry_frames():
    mod = _load_strategy_service_main()

    assert mod._ohlcv_fetch_limit("15m") >= 211
    assert mod._ohlcv_fetch_limit("1h") >= 211
    assert mod._ohlcv_fetch_limit("1m") >= 130
    assert mod.DEFAULT_ANALYSIS_TIMEFRAMES == ["1h", "15m"]


def test_rsi_stoch_strategy_family_resolves_versioned_15m_route_only():
    mod = _load_strategy_service_main()

    assert mod._is_rsi_stoch_reversal_strategy("rsi_stoch_reversal_15m") is True
    assert mod._is_rsi_stoch_reversal_strategy("rsi_stoch_reversal_5m") is False
    assert mod._is_rsi_stoch_reversal_strategy("rsi_stoch_reversal_1m") is False
    assert mod._is_rsi_stoch_reversal_strategy("macd_momentum") is False
    assert mod._strategy_signal_timeframes(
        "rsi_stoch_reversal_15m",
        {"parameters": {"entry_timeframe": "15m", "confirmation_timeframe": "1h"}},
    ) == ["15m", "1h"]


def test_strategy_signal_timeframes_rejects_noncanonical_root_timeframes():
    mod = _load_strategy_service_main()

    cfg = {
        "target_timeframes": ["4h"],
        "parameters": {"primary_timeframe": "4h"},
    }
    assert mod._strategy_signal_timeframes("ema50_breakout_pullback", cfg) == ["15m"]

    wrapped = {"config": cfg, "enabled": True}
    assert mod._strategy_signal_timeframes("ema50_breakout_pullback", wrapped) == ["15m"]


def test_strategy_signal_timeframes_allows_heikin_ashi_1m_scalper():
    mod = _load_strategy_service_main()

    cfg = {
        "target_timeframes": ["1m"],
        "parameters": {"entry_timeframe": "1m"},
    }
    assert mod._strategy_signal_timeframes("heikin_ashi_1m_scalper", cfg) == ["1m"]


def test_strategy_signal_timeframes_allows_ema20_ma50_spot_1h():
    mod = _load_strategy_service_main()

    cfg = {
        "target_timeframes": ["1h"],
        "parameters": {"primary_timeframe": "1h"},
    }
    assert mod._strategy_signal_timeframes("ema20_ma50_spot_1h", cfg) == ["1h"]


def test_rsi_stoch_15m_in_standalone_applicable_strategy_list():
    module_path = (
        Path(__file__).resolve().parents[2]
        / "services"
        / "strategy-service"
        / "main.py"
    )
    source = module_path.read_text()

    assert "rsi_stoch_reversal_15m" in source
    assert "ema20_ma50_spot_1h" in source
