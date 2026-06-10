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

    assert mod._ohlcv_fetch_limit("5m") >= 211
    assert mod._ohlcv_fetch_limit("1m") >= 211
    assert mod._ohlcv_fetch_limit("15m") >= 211
    assert mod._ohlcv_fetch_limit("1h") >= 211
    assert mod._ohlcv_fetch_limit("1d") >= 211
    assert mod._ohlcv_fetch_limit("1w") >= 211
    assert mod._ohlcv_fetch_limit("4h") == 150


def test_rsi_stoch_strategy_family_resolves_1m_fast_route():
    mod = _load_strategy_service_main()

    assert mod._is_rsi_stoch_reversal_strategy("rsi_stoch_reversal_5m") is True
    assert mod._is_rsi_stoch_reversal_strategy("rsi_stoch_reversal_1m") is True
    assert mod._is_rsi_stoch_reversal_strategy("macd_momentum") is False
    assert (
        mod._strategy_entry_timeframe(
            "rsi_stoch_reversal_1m",
            {"parameters": {"entry_timeframe": "1m"}},
        )
        == "1m"
    )
    assert mod._strategy_signal_timeframes(
        "rsi_stoch_reversal_1m",
        {"parameters": {"entry_timeframe": "1m", "confirmation_timeframe": "5m"}},
    ) == ["1m", "5m"]
    assert (
        mod._strategy_entry_timeframe(
            "rsi_stoch_reversal_1m",
            {"config": {"parameters": {"entry_timeframe": "1m"}}},
        )
        == "1m"
    )


def test_rsi_stoch_1m_in_standalone_applicable_strategy_list():
    module_path = (
        Path(__file__).resolve().parents[2]
        / "services"
        / "strategy-service"
        / "main.py"
    )
    source = module_path.read_text()

    assert '"rsi_stoch_reversal_5m",' in source
    assert '"rsi_stoch_reversal_1m",' in source
