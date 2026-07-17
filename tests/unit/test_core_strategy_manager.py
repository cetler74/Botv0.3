import types

from core.strategy_manager import StrategyManager


class FakeWeeklyFibonacciStrategy:
    def __init__(self, config, exchange, database, redis_client=None):
        self.config = config


def test_strategy_manager_loads_enabled_weekly_fibonacci(monkeypatch):
    import core.strategy_manager as strategy_manager_module

    monkeypatch.setattr(
        strategy_manager_module.importlib,
        "import_module",
        lambda module_name: types.SimpleNamespace(
            WeeklyFibonacciSpotStrategy=FakeWeeklyFibonacciStrategy
        ),
    )

    manager = StrategyManager(
        {"strategies": {"weekly_fibonacci_spot": {"enabled": True}}},
    )

    assert "weekly_fibonacci_spot" in manager.strategies
