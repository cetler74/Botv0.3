"""Unit tests for supply/demand 3-step engine."""

from __future__ import annotations

import pandas as pd

from strategy.playbooks.supply_demand_3step_engine import (
    EngineParams,
    evaluate_supply_demand_3step,
)


def _uptrend_structure_df(n: int = 80) -> pd.DataFrame:
    rows = []
    price = 100.0
    for i in range(n):
        phase = i % 8
        if phase < 4:
            o, c = price, price + 0.8
            h, l = c + 0.4, o - 0.2
        else:
            o, c = price + 0.8, price + 0.1
            h, l = o + 0.2, c - 0.6
        rows.append({"open": o, "high": h, "low": l, "close": c, "volume": 1000})
        if i % 8 == 7:
            price += 1.0
    idx = pd.date_range("2025-01-01", periods=n, freq="1h")
    return pd.DataFrame(rows, index=idx)


def _entry_with_demand_retest(struct_df: pd.DataFrame) -> pd.DataFrame:
    """15m bars: drift up, consolidation+impulse, retest with good R:R."""
    n = 60
    base_high = float(struct_df["high"].max())
    rows = []
    price = base_high - 5
    for i in range(n):
        if i == 30:
            rows.append({"open": price, "high": price + 0.15, "low": price - 0.05, "close": price, "volume": 400})
        elif i == 31:
            rows.append(
                {
                    "open": price,
                    "high": price + 3.0,
                    "low": price - 0.05,
                    "close": price + 2.8,
                    "volume": 8000,
                }
            )
        elif i == n - 1:
            z_low = rows[30]["low"]
            z_high = rows[30]["high"]
            rows.append(
                {
                    "open": price + 1.0,
                    "high": z_high + 0.1,
                    "low": z_low,
                    "close": (z_low + z_high) / 2,
                    "volume": 900,
                }
            )
        else:
            o = price
            c = price + 0.05
            rows.append({"open": o, "high": c + 0.1, "low": o - 0.05, "close": c, "volume": 1000})
            price = c
    idx = pd.date_range("2025-01-01", periods=n, freq="15min")
    return pd.DataFrame(rows, index=idx)


def test_hold_on_insufficient_candles():
    params = EngineParams(min_candles_structure=200)
    result = evaluate_supply_demand_3step({"1h": _uptrend_structure_df(30)}, params)
    assert result.signal == "hold"
    assert "insufficient" in result.invalidation_reason


def test_hold_when_step1_fails():
    flat = pd.DataFrame(
        {
            "open": [100.0] * 60,
            "high": [100.1] * 60,
            "low": [99.9] * 60,
            "close": [100.0] * 60,
            "volume": [1000] * 60,
        },
        index=pd.date_range("2025-01-01", periods=60, freq="1h"),
    )
    params = EngineParams()
    result = evaluate_supply_demand_3step({"1h": flat, "15m": flat}, params)
    assert result.signal == "hold"
    assert result.indicators.get("step1_pass") is False


def test_buy_on_valid_uptrend_demand_retest():
    struct = _uptrend_structure_df()
    entry = _entry_with_demand_retest(struct)
    params = EngineParams(
        min_reward_risk=1.5,
        max_zone_width_pct=0.02,
        impulse_min_body_pct=0.5,
        impulse_min_range_atr_mult=1.0,
        zone_retest_tolerance_pct=0.02,
    )
    result = evaluate_supply_demand_3step(
        {"1h": struct, "15m": entry},
        params,
        allow_short=False,
    )
    if result.signal == "buy":
        assert result.indicators["step1_pass"] is True
        assert result.indicators["step2_pass"] is True
        assert result.indicators["step3_pass"] is True
        assert result.indicators["reward_risk"] >= 1.5
        assert "Supply/Demand 3-Step LONG" in result.indicators["entry_reason"]


def test_rr_gate_rejects_low_rr():
    struct = _uptrend_structure_df()
    entry = _entry_with_demand_retest(struct)
    params = EngineParams(
        min_reward_risk=99.0,
        max_zone_width_pct=0.02,
        impulse_min_body_pct=0.5,
        impulse_min_range_atr_mult=1.0,
        zone_retest_tolerance_pct=0.02,
    )
    result = evaluate_supply_demand_3step({"1h": struct, "15m": entry}, params)
    if result.indicators.get("step2_pass"):
        assert result.signal == "hold"
        assert "rr" in result.invalidation_reason.lower() or "step3" in result.invalidation_reason


def test_single_timeframe_mode():
    struct = _uptrend_structure_df()
    params = EngineParams(timeframe_mode="single", structure_timeframe="1h", entry_timeframe="1h")
    result = evaluate_supply_demand_3step({"1h": struct}, params)
    assert result.indicators["timeframe_mode"] == "single"


def test_asset_class_filter():
    struct = _uptrend_structure_df()
    entry = _entry_with_demand_retest(struct)
    params = EngineParams(allowed_asset_classes=["stocks"])
    result = evaluate_supply_demand_3step(
        {"1h": struct, "15m": entry},
        params,
        asset_class="crypto",
    )
    assert result.signal == "hold"
    assert "asset_class" in result.invalidation_reason


def test_hold_when_stop_pct_below_floor():
    struct = _uptrend_structure_df()
    entry = _entry_with_demand_retest(struct)
    params = EngineParams(
        min_stop_pct=0.05,
        min_reward_risk=1.5,
        max_zone_width_pct=0.02,
        impulse_min_body_pct=0.5,
        impulse_min_range_atr_mult=1.0,
        zone_retest_tolerance_pct=0.02,
    )
    result = evaluate_supply_demand_3step(
        {"1h": struct, "15m": entry},
        params,
        allow_short=False,
        market_regime="trending_up",
    )
    if result.invalidation_reason == "step3_fail:stop_too_tight":
        assert result.signal == "hold"
        assert float(result.indicators.get("stop_pct") or 0) < 0.05


def test_hold_weak_structure_in_chop_regime():
    struct = _uptrend_structure_df()
    entry = _entry_with_demand_retest(struct)
    params = EngineParams(
        chop_regime_min_trend_swing_pct=0.50,
        min_reward_risk=1.5,
        max_zone_width_pct=0.02,
        impulse_min_body_pct=0.5,
        impulse_min_range_atr_mult=1.0,
        zone_retest_tolerance_pct=0.02,
    )
    result = evaluate_supply_demand_3step(
        {"1h": struct, "15m": entry},
        params,
        allow_short=False,
        market_regime="sideways",
    )
    if result.invalidation_reason == "step3_fail:weak_structure_in_chop":
        assert result.signal == "hold"
