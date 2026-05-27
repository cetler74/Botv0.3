import os
import sys
import types

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SERVICE = os.path.join(ROOT, "services", "strategy-service")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if SERVICE not in sys.path:
    sys.path.insert(0, SERVICE)


class FakePerpStrategy:
    def __init__(self, config, exchange, database, redis_client=None):
        self.config = config
        self.exchange = exchange
        self.database = database
        self.redis_client = redis_client
        self.state = types.SimpleNamespace(market_regime="unknown")

    async def initialize(self, pair):
        self.state.pair = pair

    async def update(self, market_data):
        return None

    async def generate_signal(self, *args, **kwargs):
        return "sell", 0.8, 0.7


def test_hyperliquid_strategy_manager_builds_fresh_instances(monkeypatch):
    import hyperliquid_strategy_manager as manager_module

    monkeypatch.setitem(
        manager_module.HYPERLIQUID_STRATEGY_MAPPING,
        "fake_perp",
        ("fake_hl_strategy_module", "FakePerpStrategy"),
    )

    def fake_import_module(module_path):
        assert module_path == "fake_hl_strategy_module"
        return types.SimpleNamespace(FakePerpStrategy=FakePerpStrategy)

    monkeypatch.setattr(manager_module.importlib, "import_module", fake_import_module)

    manager = manager_module.HyperliquidStrategyManager(
        {
            "fake_perp": {
                "enabled": True,
                "parameters": {"nested": {"value": 1}},
            }
        },
        "http://exchange-service:8003",
    )

    first = manager._build_strategy_instance("fake_perp")
    second = manager._build_strategy_instance("fake_perp")

    assert first is not second
    first.config["parameters"]["nested"]["value"] = 99
    assert second.config["parameters"]["nested"]["value"] == 1


def test_hyperliquid_strategy_manager_includes_sma_reclaim_required_timeframes(monkeypatch):
    import hyperliquid_strategy_manager as manager_module

    monkeypatch.setitem(
        manager_module.HYPERLIQUID_STRATEGY_MAPPING,
        "sma_reclaim_bull_flag",
        ("fake_hl_strategy_module", "FakePerpStrategy"),
    )
    monkeypatch.setattr(
        manager_module.importlib,
        "import_module",
        lambda module_path: types.SimpleNamespace(FakePerpStrategy=FakePerpStrategy),
    )

    manager = manager_module.HyperliquidStrategyManager(
        {
            "sma_reclaim_bull_flag": {
                "enabled": True,
                "parameters": {
                    "entry_timeframe": "5m",
                    "execution_timeframe": "1m",
                    "context_timeframes": ["1d"],
                },
                "target_timeframes": ["1d", "5m", "1m"],
            }
        },
        "http://exchange-service:8003",
    )

    tfs = manager._resolve_timeframes(None, ["sma_reclaim_bull_flag"])

    assert "5m" in tfs
    assert "1m" in tfs
    assert "1d" in tfs


@pytest.mark.asyncio
async def test_hyperliquid_strategy_manager_normalizes_short_signal(monkeypatch):
    import pandas as pd
    import hyperliquid_strategy_manager as manager_module

    monkeypatch.setitem(
        manager_module.HYPERLIQUID_STRATEGY_MAPPING,
        "fake_perp",
        ("fake_hl_strategy_module", "FakePerpStrategy"),
    )
    monkeypatch.setattr(
        manager_module.importlib,
        "import_module",
        lambda module_path: types.SimpleNamespace(FakePerpStrategy=FakePerpStrategy),
    )

    idx = pd.date_range("2026-01-01", periods=80, freq="1h")
    df = pd.DataFrame(
        {
            "open": [100.0] * 80,
            "high": [101.0] * 80,
            "low": [99.0] * 80,
            "close": [100.0] * 80,
            "volume": [1000.0] * 80,
        },
        index=idx,
    )

    manager = manager_module.HyperliquidStrategyManager(
        {"fake_perp": {"enabled": True, "parameters": {}}},
        "http://exchange-service:8003",
    )

    async def fake_market_data(*args, **kwargs):
        return {"1h": df}

    monkeypatch.setattr(manager, "_get_market_data", fake_market_data)
    result = await manager.analyze_coin("BTC")

    assert result["strategies"]["fake_perp"]["signal"] == "short"
    assert result["consensus"]["signal"] == "short"


def test_deprecated_strategy_logs_warning(monkeypatch, caplog):
    import hyperliquid_strategy_manager as manager_module

    monkeypatch.setitem(
        manager_module.HYPERLIQUID_STRATEGY_MAPPING,
        "heikin_ashi",
        ("fake_hl_strategy_module", "FakePerpStrategy"),
    )
    monkeypatch.setattr(
        manager_module.importlib,
        "import_module",
        lambda module_path: types.SimpleNamespace(FakePerpStrategy=FakePerpStrategy),
    )

    with caplog.at_level("WARNING"):
        manager = manager_module.HyperliquidStrategyManager(
            {"heikin_ashi": {"enabled": True, "parameters": {}}},
            "http://exchange-service:8003",
        )

    assert any("heikin_ashi is deprecated" in msg for msg in caplog.messages)
    assert "heikin_ashi" in manager.strategies


def test_deprecated_engulfing_multi_tf_logs_warning(monkeypatch, caplog):
    import hyperliquid_strategy_manager as manager_module

    monkeypatch.setitem(
        manager_module.HYPERLIQUID_STRATEGY_MAPPING,
        "engulfing_multi_tf",
        ("fake_hl_strategy_module", "FakePerpStrategy"),
    )
    monkeypatch.setattr(
        manager_module.importlib,
        "import_module",
        lambda module_path: types.SimpleNamespace(FakePerpStrategy=FakePerpStrategy),
    )

    with caplog.at_level("WARNING"):
        manager = manager_module.HyperliquidStrategyManager(
            {"engulfing_multi_tf": {"enabled": True, "parameters": {}}},
            "http://exchange-service:8003",
        )

    assert any("engulfing_multi_tf is deprecated" in msg for msg in caplog.messages)
    assert "engulfing_multi_tf" in manager.strategies
