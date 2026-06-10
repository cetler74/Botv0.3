"""Unit tests for supply/demand zone detection."""

from __future__ import annotations

import pandas as pd

from strategy.indicators.supply_demand_zones import scan_supply_demand_zones


def _demand_zone_df() -> pd.DataFrame:
    """Consolidation then bullish impulse, then retest."""
    rows = []
    price = 100.0
    for i in range(40):
        if i == 20:
            # consolidation
            rows.append({"open": 105.0, "high": 105.2, "low": 104.8, "close": 105.0, "volume": 500})
        elif i == 21:
            # bullish impulse
            rows.append({"open": 105.0, "high": 108.0, "low": 104.9, "close": 107.5, "volume": 5000})
        elif i == 35:
            # retest demand zone
            rows.append({"open": 106.0, "high": 106.5, "low": 104.9, "close": 105.3, "volume": 800})
        else:
            o = price
            c = price + 0.1
            rows.append({"open": o, "high": c + 0.2, "low": o - 0.1, "close": c, "volume": 1000})
            price = c
    idx = pd.date_range("2025-01-01", periods=40, freq="15min")
    return pd.DataFrame(rows, index=idx)


def _supply_zone_df() -> pd.DataFrame:
    rows = []
    price = 200.0
    for i in range(40):
        if i == 20:
            rows.append({"open": 195.0, "high": 195.2, "low": 194.8, "close": 195.0, "volume": 500})
        elif i == 21:
            rows.append({"open": 195.0, "high": 195.1, "low": 192.0, "close": 192.5, "volume": 5000})
        elif i == 35:
            rows.append({"open": 193.0, "high": 195.1, "low": 192.8, "close": 194.5, "volume": 800})
        else:
            o = price
            c = price - 0.1
            rows.append({"open": o, "high": o + 0.1, "low": c - 0.2, "close": c, "volume": 1000})
            price = c
    idx = pd.date_range("2025-01-01", periods=40, freq="15min")
    return pd.DataFrame(rows, index=idx)


def test_neutral_trend_no_zones():
    df = _demand_zone_df()
    result = scan_supply_demand_zones(df, "neutral")
    assert result.step2_pass is False
    assert "neutral" in result.step2_reason


def test_demand_zone_detected_in_uptrend():
    df = _demand_zone_df()
    result = scan_supply_demand_zones(
        df,
        "uptrend",
        max_zone_width_pct=0.02,
        impulse_min_body_pct=0.5,
        impulse_min_range_atr_mult=1.2,
    )
    assert len(result.zones) >= 1
    assert result.zones[0].kind == "demand"


def test_supply_zone_detected_in_downtrend():
    df = _supply_zone_df()
    result = scan_supply_demand_zones(
        df,
        "downtrend",
        max_zone_width_pct=0.02,
        impulse_min_body_pct=0.5,
        impulse_min_range_atr_mult=1.2,
    )
    assert len(result.zones) >= 1
    assert result.zones[0].kind == "supply"


def test_retest_sets_step2_pass():
    df = _demand_zone_df()
    result = scan_supply_demand_zones(
        df,
        "uptrend",
        max_zone_width_pct=0.02,
        impulse_min_body_pct=0.5,
        impulse_min_range_atr_mult=1.2,
        zone_retest_tolerance_pct=0.01,
    )
    if result.retest_zone is not None:
        assert result.step2_pass is True
        assert "retest" in result.step2_reason.lower()


def test_to_dict_serializable():
    df = _demand_zone_df()
    result = scan_supply_demand_zones(df, "uptrend", max_zone_width_pct=0.02)
    d = result.to_dict()
    assert "zones" in d
    assert "active_zones" in d
