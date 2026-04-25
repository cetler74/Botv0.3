import types
import importlib.util
from pathlib import Path

import pytest


def _load_trailing_module():
    module_path = Path(__file__).resolve().parents[2] / "services" / "orchestrator-service" / "trailing_stop_manager.py"
    spec = importlib.util.spec_from_file_location("trailing_stop_manager_mod", str(module_path))
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_trailing_manager_skips_activation_on_degraded_feed():
    mod = _load_trailing_module()
    TrailingStopManager = mod.TrailingStopManager

    class FakeDB:
        async def get_open_trades_without_exit(self):
            t = types.SimpleNamespace()
            t.id = "t1"
            t.exchange = "binance"
            t.symbol = "BTC/USDC"
            t.entry_price = 100.0
            t.position_size = 1.0
            t.highest_price = 101.0
            t.profit_protection = "inactive"
            return [t]

        async def update_trade(self, *_args, **_kwargs):
            return True

    cfg = {
        "trading": {
            "trailing_stop": {
                "activation_threshold": 0.005,
                "step_percentage": 0.006,
                "breakeven_floor_percentage": 0.003,
                "feed_quality_policy": {
                    "allow_exit_in_degraded": False,
                    "allow_exit_in_down": False,
                },
            }
        }
    }
    mgr = TrailingStopManager(cfg, database_service=FakeDB(), exchange_service=object())

    activated = {"called": False}

    async def fake_get_price(_exchange, _symbol):
        mgr._last_feed_quality = "DEGRADED"
        mgr._last_price_source = "ohlcv_1m"
        return 101.0

    async def fake_activate(_trade, _price):
        activated["called"] = True

    mgr._get_current_price = fake_get_price
    mgr._activate_trailing_stop = fake_activate

    await mgr._check_activation_candidates()

    assert activated["called"] is False
    assert mgr.stats["activation_skipped_degraded_feed"] == 1


@pytest.mark.asyncio
async def test_trailing_manager_skips_updates_on_degraded_feed():
    mod = _load_trailing_module()
    TrailingStopData = mod.TrailingStopData
    TrailingStopManager = mod.TrailingStopManager
    TrailingStopState = mod.TrailingStopState

    cfg = {
        "trading": {
            "trailing_stop": {
                "activation_threshold": 0.005,
                "step_percentage": 0.006,
                "breakeven_floor_percentage": 0.003,
            }
        }
    }
    mgr = TrailingStopManager(cfg, database_service=object(), exchange_service=object())

    stop = TrailingStopData(
        trade_id="t2",
        exchange="binance",
        symbol="ETH/USDC",
        entry_price=100.0,
        position_size=1.0,
        current_price=100.0,
        highest_price=100.0,
        trail_price=100.0,
        state=TrailingStopState.ACTIVE,
    )
    mgr.active_stops["t2"] = stop

    async def fake_get_price(_exchange, _symbol):
        mgr._last_feed_quality = "DEGRADED"
        mgr._last_price_source = "orderbook_mid"
        return 101.0

    async def fake_update(_stop, _new_price):
        raise AssertionError("trail update should be skipped on degraded feed")

    mgr._get_current_price = fake_get_price
    mgr._update_trailing_order = fake_update

    await mgr._monitor_active_stops()

    assert mgr.stats["updates_skipped_degraded_feed"] == 1
