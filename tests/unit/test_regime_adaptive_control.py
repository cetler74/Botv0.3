import importlib.util
from datetime import datetime, timedelta
from pathlib import Path
import sys
import types


def _install_pandas_ta_stub():
    """Unit tests here only need module import, not indicator math."""
    if "pandas_ta" in sys.modules:
        return
    ta_stub = types.ModuleType("pandas_ta")
    ta_stub.adx = lambda *args, **kwargs: None
    ta_stub.ema = lambda *args, **kwargs: None
    ta_stub.rsi = lambda *args, **kwargs: None
    ta_stub.bbands = lambda *args, **kwargs: None
    ta_stub.atr = lambda *args, **kwargs: None
    sys.modules["pandas_ta"] = ta_stub


def _load_strategy_service_module():
    _install_pandas_ta_stub()
    module_path = (
        Path(__file__).resolve().parents[2]
        / "services"
        / "strategy-service"
        / "main.py"
    )
    spec = importlib.util.spec_from_file_location("strategy_service_mod", str(module_path))
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_regime_detector_module():
    _install_pandas_ta_stub()
    module_path = (
        Path(__file__).resolve().parents[2]
        / "strategy"
        / "market_regime_detector.py"
    )
    spec = importlib.util.spec_from_file_location("market_regime_detector_mod", str(module_path))
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_regime_detector_rankings_include_primary_and_runner_up():
    mod = _load_regime_detector_module()
    detector = mod.MarketRegimeDetector()
    rankings = detector.get_regime_rankings(
        {
            "regime_scores": {
                "high_volatility": 0.82,
                "breakout": 0.66,
                "sideways": 0.22,
            }
        }
    )
    assert rankings[0]["regime"] == "high_volatility"
    assert rankings[0]["score"] == 0.82
    assert rankings[1]["regime"] == "breakout"


def test_regime_stability_holds_regime_inside_dwell_window():
    mod = _load_strategy_service_module()
    manager = mod.StrategyManager(
        {
            "regime_stability": {
                "enabled": True,
                "min_dwell_seconds": 300,
                "hysteresis_delta": 0.10,
                "emergency_volatility_score": 0.95,
            }
        }
    )
    key = ("binance", "BTC/USDC")
    manager._regime_state[key] = {
        "stable_regime": "trending_up",
        "changed_at": datetime.utcnow() - timedelta(seconds=60),
    }
    result = manager._resolve_stable_regime(
        exchange_name="binance",
        pair="BTC/USDC",
        detected_regime="breakout",
        regime_score=0.83,
        runner_up_score=0.76,
    )
    assert result["stable_regime"] == "trending_up"
    assert result["previous_regime"] == "trending_up"


def test_regime_stability_switches_after_dwell_and_delta():
    mod = _load_strategy_service_module()
    manager = mod.StrategyManager(
        {
            "regime_stability": {
                "enabled": True,
                "min_dwell_seconds": 120,
                "hysteresis_delta": 0.08,
                "emergency_volatility_score": 0.95,
            }
        }
    )
    key = ("bybit", "ETH/USDC")
    manager._regime_state[key] = {
        "stable_regime": "sideways",
        "changed_at": datetime.utcnow() - timedelta(seconds=600),
    }
    result = manager._resolve_stable_regime(
        exchange_name="bybit",
        pair="ETH/USDC",
        detected_regime="trending_down",
        regime_score=0.84,
        runner_up_score=0.65,
    )
    assert result["stable_regime"] == "trending_down"
