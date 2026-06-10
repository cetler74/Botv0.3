import pandas as pd
import pytest
import importlib
import inspect
import yaml
from pathlib import Path

from strategy.hyperliquid.macd_momentum_perp import MacdMomentumPerpStrategy
import strategy.hyperliquid.sma_reclaim_bull_flag_perp as sma_reclaim_perp_module
from strategy.hyperliquid.mapping import HYPERLIQUID_STRATEGY_MAPPING
from strategy.hyperliquid.intraday_base_perp import IntradayPerpBaseStrategy
from strategy.hyperliquid.rsi_stoch_reversal_1m_perp import RsiStochReversal1mPerpStrategy
from strategy.hyperliquid.sma_reclaim_bull_flag_perp import SmaReclaimBullFlagPerpStrategy
from strategy.hyperliquid.vwma_hull_perp import VwmaHullPerpStrategy
from strategy.playbooks.rsi_stoch_reversal_5m_engine import EngineResult as RsiStochEngineResult
from strategy.playbooks.sma_reclaim_bull_flag_engine import EngineResult


def _synthetic_ohlcv(n: int = 260, trend: float = 0.001) -> pd.DataFrame:
    import numpy as np

    idx = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
    close = 100 * (1 + np.linspace(0, trend * n, n))
    vol = np.full(n, 1000.0)
    return pd.DataFrame(
        {
            "open": close * 0.999,
            "high": close * 1.002,
            "low": close * 0.998,
            "close": close,
            "volume": vol,
        },
        index=idx,
    )


@pytest.mark.asyncio
async def test_sma_reclaim_bull_flag_perp_long_only():
    strat = SmaReclaimBullFlagPerpStrategy(
        config={
            "parameters": {
                "allow_long": True,
                "allow_short": False,
                "session_filter": {"enabled": False},
            }
        },
        exchange=None,
        database=None,
    )
    await strat.initialize("BTC")
    strat.state.market_regime = "trending_up"
    md = {"5m": _synthetic_ohlcv(n=260, trend=0.0005), "1m": _synthetic_ohlcv(n=260, trend=0.0005)}
    signal, conf, strength = await strat.generate_signal(md, pair="BTC")
    assert signal in ("long", "hold")
    assert signal != "short"
    assert 0 <= conf <= 1


@pytest.mark.asyncio
async def test_sma_reclaim_bull_flag_perp_long_signal_sets_risk_metadata(monkeypatch):
    def fake_evaluate(*args, **kwargs):
        return EngineResult(
            "buy",
            0.82,
            0.77,
            {
                "entry_price": 100.0,
                "stop_hint": 98.5,
                "target_hint": 104.0,
                "reward_risk": 2.67,
                "position_size_hint": 66.6667,
                "entry_reason": "synthetic sma reclaim bull flag",
                "invalidation_reason": "none",
            },
            "none",
        )

    monkeypatch.setattr(sma_reclaim_perp_module, "evaluate_sma_reclaim_bull_flag", fake_evaluate)
    strat = SmaReclaimBullFlagPerpStrategy(
        config={"parameters": {"allow_long": True, "allow_short": False}},
        exchange=None,
        database=None,
    )
    await strat.initialize("BTC")

    signal, conf, strength = await strat.generate_signal({"5m": _synthetic_ohlcv()}, pair="BTC")

    assert signal == "long"
    assert conf == 0.82
    assert strength == 0.77
    assert strat.state.stop_loss == 98.5
    assert strat.state.take_profit == 104.0
    assert strat.state.indicators["reward_risk"] == 2.67
    assert await strat.calculate_position_size("long") == pytest.approx(66.6667)
    assert await strat.calculate_position_size("short") == 0.0


@pytest.mark.asyncio
async def test_sma_reclaim_bull_flag_perp_respects_long_disabled():
    strat = SmaReclaimBullFlagPerpStrategy(
        config={"parameters": {"allow_long": False, "allow_short": False}},
        exchange=None,
        database=None,
    )
    await strat.initialize("BTC")

    signal, conf, strength = await strat.generate_signal({"5m": _synthetic_ohlcv()}, pair="BTC")

    assert signal == "hold"
    assert conf == 0.0
    assert strength == 0.0
    assert strat.state.indicators["invalidation_reason"] == "long_disabled"


@pytest.mark.asyncio
async def test_macd_perp_returns_tuple():
    strat = MacdMomentumPerpStrategy(
        config={"parameters": {"allow_short": True, "min_confidence_score": 0.5}},
        exchange=None,
        database=None,
    )
    await strat.initialize("ETH")
    strat.state.market_regime = "trending_up"
    md = {"15m": _synthetic_ohlcv(), "1h": _synthetic_ohlcv(trend=0.002)}
    signal, conf, strength = await strat.generate_signal(md, pair="ETH")
    assert signal in ("long", "short", "hold")
    assert 0 <= conf <= 1


@pytest.mark.asyncio
async def test_vwma_hull_perp_respects_short_disabled(monkeypatch):
    strat = VwmaHullPerpStrategy(
        config={
            "parameters": {
                "allow_long": True,
                "allow_short": False,
                "adx_threshold": 0,
                "min_confidence_score": 0.1,
                "min_absolute_volume": 1,
                "volume_method": "legacy",
                "require_close_above_vwma_hull_for_long": False,
                "require_rsi_above_floor_for_long": False,
            },
            "require_multi_timeframe_confirmation": False,
        },
        exchange=None,
        database=None,
    )
    await strat.initialize("BTC")
    strat.state.market_regime = "trending_up"
    monkeypatch.setattr(strat, "_validate_volume_conditions", lambda _ohlcv: (True, "ok"))
    monkeypatch.setattr(strat, "_detect_crossover", lambda _vwma, _hull: "bearish")
    md = {"1h": _synthetic_ohlcv(n=120, trend=-0.001), "4h": _synthetic_ohlcv(n=120, trend=0.001)}

    signal, conf, strength = await strat.generate_signal(md, pair="BTC")

    assert signal == "hold"
    assert conf == 0.0
    assert strength == 0.0


LONG_ONLY_HL_STRATEGIES = frozenset(
    {"sma_reclaim_bull_flag", "vwap_bounce_scalping", "vwma_hull"}
)


def test_all_mapped_strategies_have_modules():
    assert "supply_demand_3step" in HYPERLIQUID_STRATEGY_MAPPING
    assert "dual_sma_daytrade" in HYPERLIQUID_STRATEGY_MAPPING
    assert "arc_daytrade" in HYPERLIQUID_STRATEGY_MAPPING
    for name, (mod, cls) in HYPERLIQUID_STRATEGY_MAPPING.items():
        assert mod
        assert cls


def test_all_enabled_hyperliquid_strategies_are_short_capable():
    config_path = Path(__file__).resolve().parents[2] / "config" / "config.yaml"
    cfg = yaml.safe_load(config_path.read_text())
    hl_cfg = cfg.get("strategies_hyperliquid", {})

    for name, (module_name, class_name) in HYPERLIQUID_STRATEGY_MAPPING.items():
        strategy_cfg = hl_cfg.get(name, {})
        if not strategy_cfg.get("enabled", False):
            continue
        if name in LONG_ONLY_HL_STRATEGIES:
            assert strategy_cfg.get("parameters", {}).get("allow_short") is False
            continue
        assert strategy_cfg.get("parameters", {}).get("allow_short") is True

        module = importlib.import_module(module_name)
        strategy_cls = getattr(module, class_name)
        source = inspect.getsource(strategy_cls)
        short_capable = "'short'" in source or '"short"' in source or "_sell(" in source
        assert short_capable, f"{name} cannot emit short-entry analysis"


def test_hl_rsi_stoch_blocks_high_volatility_shorts_only():
    config_path = Path(__file__).resolve().parents[2] / "config" / "config.yaml"
    cfg = yaml.safe_load(config_path.read_text())
    params = (
        cfg.get("strategies_hyperliquid", {})
        .get("rsi_stoch_reversal_5m", {})
        .get("parameters", {})
    )

    assert params.get("allow_short") is True
    assert "breakout" in params.get("blocked_regimes", [])
    assert "high_volatility" in params.get("short_blocked_regimes", [])


def test_hl_rsi_stoch_1m_registered_and_has_unblocked_breakout_high_vol_short():
    module_name, class_name = HYPERLIQUID_STRATEGY_MAPPING["rsi_stoch_reversal_1m"]
    module = importlib.import_module(module_name)
    assert getattr(module, class_name) is RsiStochReversal1mPerpStrategy

    config_path = Path(__file__).resolve().parents[2] / "config" / "config.yaml"
    cfg = yaml.safe_load(config_path.read_text())
    params = (
        cfg.get("strategies_hyperliquid", {})
        .get("rsi_stoch_reversal_1m", {})
        .get("parameters", {})
    )

    assert params.get("allow_long") is True
    assert params.get("allow_short") is True
    assert str(params.get("entry_timeframe")) == "1m"
    assert str(params.get("confirmation_timeframe")) == "5m"
    assert params.get("require_confirmation") is True
    assert float(params.get("min_stoch_cross_gap")) == pytest.approx(4.0)
    assert float(params.get("min_expected_move_pct")) == pytest.approx(0.8)
    assert "breakout" not in params.get("blocked_regimes", [])
    assert "high_volatility" not in params.get("short_blocked_regimes", [])


@pytest.mark.asyncio
async def test_rsi_stoch_1m_perp_maps_engine_signals_and_passes_required_timeframes(monkeypatch):
    captured = {}

    def fake_eval(market_data, params, **kwargs):
        captured.setdefault("keys", list(market_data.keys()))
        captured.setdefault("entry_timeframe", params.entry_timeframe)
        if not captured.get("sold"):
            captured["sold"] = True
            return RsiStochEngineResult(
                "sell",
                0.72,
                0.70,
                {"setup": "rsi_stoch_reversal_5m", "side_intent": "short"},
            )
        return RsiStochEngineResult(
            "buy",
            0.72,
            0.70,
            {"setup": "rsi_stoch_reversal_5m", "side_intent": "long"},
        )

    monkeypatch.setattr(
        "strategy.hyperliquid.rsi_stoch_reversal_5m_perp.evaluate_rsi_stoch_reversal_5m",
        fake_eval,
    )
    strat = RsiStochReversal1mPerpStrategy(
        config={"parameters": {"allow_long": True, "allow_short": True}},
        exchange=None,
        database=None,
    )
    await strat.initialize("BTC")

    short_signal, short_conf, _ = await strat.generate_signal(
        {"1m": _synthetic_ohlcv(), "5m": _synthetic_ohlcv()}, pair="BTC"
    )
    long_signal, long_conf, _ = await strat.generate_signal(
        {"1m": _synthetic_ohlcv(), "5m": _synthetic_ohlcv()}, pair="BTC"
    )

    assert captured["keys"] == ["1m", "5m"]
    assert captured["entry_timeframe"] == "1m"
    assert short_signal == "short"
    assert short_conf == pytest.approx(0.72)
    assert long_signal == "long"
    assert long_conf == pytest.approx(0.72)


def test_hyperliquid_strategy_sources_do_not_emit_spot_entry_verbs():
    strategy_dir = Path(__file__).resolve().parents[2] / "strategy" / "hyperliquid"
    forbidden = [
        "return 'buy'",
        'return "buy"',
        "return 'sell'",
        'return "sell"',
        "signal = 'buy'",
        'signal = "buy"',
        "signal = 'sell'",
        'signal = "sell"',
    ]
    allowed_files = {"base_perp_strategy.py", "consensus.py", "intraday_base_perp.py"}
    offenders = []
    for path in strategy_dir.glob("*_perp.py"):
        if path.name in allowed_files:
            continue
        source = path.read_text()
        for pattern in forbidden:
            if pattern in source:
                offenders.append(f"{path.name}: {pattern}")
    assert offenders == []


def test_hyperliquid_strategy_sources_do_not_reject_canonical_short_sizing():
    strategy_dir = Path(__file__).resolve().parents[2] / "strategy" / "hyperliquid"
    forbidden = [
        'signal_type != "buy"',
        "signal_type != 'buy'",
        'signal_type not in {"buy", "sell"}',
        "signal_type not in {'buy', 'sell'}",
    ]
    offenders = []
    for path in strategy_dir.glob("*_perp.py"):
        source = path.read_text()
        for pattern in forbidden:
            if pattern in source:
                offenders.append(f"{path.name}: {pattern}")
    assert offenders == []


@pytest.mark.asyncio
async def test_intraday_perp_base_evaluates_short_when_long_context_fails():
    class ShortProbeStrategy(IntradayPerpBaseStrategy):
        async def initialize(self, pair: str) -> None:
            await super().initialize(pair)

        async def update(self, ohlcv: pd.DataFrame) -> None:
            await super().update(ohlcv)

        def _common_context(self, market_data, pair):
            return {
                "trend_up": False,
                "above_vwap": False,
                "entry_structure_rising": False,
                "bullish_candle": False,
                "rsi_long_ok": False,
                "trend_down": True,
                "below_vwap": True,
                "entry_structure_falling": True,
                "bearish_candle": True,
                "rsi_short_ok": True,
            }

        async def _evaluate(self, ctx):
            raise AssertionError("bullish long path should not run for bearish context")

        async def _evaluate_short(self, ctx):
            return "short", 0.8, 0.7

    strat = ShortProbeStrategy(
        config={"parameters": {"allow_short": True}},
        exchange=None,
        database=None,
    )

    signal, confidence, strength = await strat.generate_signal({})

    assert signal == "short"
    assert confidence == 0.8
    assert strength == 0.7


def test_ema50_breakout_pullback_in_hyperliquid_mapping():
    assert "ema50_breakout_pullback" in HYPERLIQUID_STRATEGY_MAPPING


@pytest.mark.asyncio
async def test_ema50_breakout_pullback_perp_smoke():
    pytest.importorskip("pandas_ta")
    from strategy.hyperliquid.ema50_breakout_pullback_perp import Ema50BreakoutPullbackPerpStrategy

    strat = Ema50BreakoutPullbackPerpStrategy(
        config={
            "parameters": {
                "allow_long": True,
                "allow_short": True,
                "min_candles": 80,
            }
        },
        exchange=None,
        database=None,
    )
    await strat.initialize("BTC")
    strat.state.market_regime = "trending_up"
    n = 90
    idx = pd.date_range("2024-01-01", periods=n, freq="4h", tz="UTC")
    close = pd.Series([100 - i * 0.1 for i in range(n)], index=idx)
    md = {
        "4h": pd.DataFrame(
            {
                "open": close * 1.001,
                "high": close * 1.005,
                "low": close * 0.995,
                "close": close,
                "volume": 1000.0,
            },
            index=idx,
        )
    }
    signal, conf, strength = await strat.generate_signal(md, pair="BTC")
    assert signal in ("long", "short", "hold")
    assert 0 <= conf <= 1
    assert strat.state.indicators.get("setup") == "ema50_breakout_pullback"
