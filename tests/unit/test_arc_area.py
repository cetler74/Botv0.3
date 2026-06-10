"""Unit tests for ARC area indicators."""

from __future__ import annotations

import pandas as pd

from strategy.indicators.arc_area import (
    apply_utc_gap_rebox,
    classify_arc_zone,
    compute_daily_box,
    find_external_swings,
)


def test_compute_daily_box_uses_previous_bar():
    daily = pd.DataFrame(
        {
            "open": [90, 100, 105],
            "high": [95, 110, 108],
            "low": [88, 95, 102],
            "close": [92, 108, 106],
            "volume": [1, 1, 1],
        },
        index=pd.date_range("2025-06-01", periods=3, freq="1d"),
    )
    box = compute_daily_box(daily)
    assert box is not None
    assert box.box_high == 110
    assert box.box_low == 95


def test_classify_arc_zone_buy_at_low():
    zone = classify_arc_zone(96, 110, 95, 115, 90, tolerance_pct=0.10, middle_no_trade_pct=0.30)
    assert zone.zone == "buy"


def test_classify_arc_zone_no_trade_middle():
    zone = classify_arc_zone(102.5, 110, 95, 115, 90, middle_no_trade_pct=0.40)
    assert zone.zone == "no_trade"


def test_find_external_swings():
    entry = pd.DataFrame(
        {
            "open": [100] * 20,
            "high": [101 + (1 if i == 10 else 0) for i in range(20)],
            "low": [99 - (1 if i == 5 else 0) for i in range(20)],
            "close": [100] * 20,
            "volume": [1] * 20,
        },
        index=pd.date_range("2025-06-07", periods=20, freq="5min"),
    )
    sh, sl = find_external_swings(entry, box_high=100, box_low=90, lookback=20, pivot_order=2)
    assert sh is None or sh >= 100
    assert sl is None or sl <= 90


def test_gap_rebox_up():
    daily_box = compute_daily_box(
        pd.DataFrame(
            {
                "open": [90, 100],
                "high": [95, 110],
                "low": [88, 95],
                "close": [92, 108],
                "volume": [1, 1],
            },
            index=pd.date_range("2025-06-06", periods=2, freq="1d"),
        )
    )
    entry = pd.DataFrame(
        {
            "open": [112, 113],
            "high": [114, 115],
            "low": [111, 112],
            "close": [113, 114],
            "volume": [1, 1],
        },
        index=pd.date_range("2025-06-07 00:00", periods=2, freq="5min"),
    )
    hi, lo, info = apply_utc_gap_rebox(daily_box, entry, enabled=True)
    assert info.get("gap_type") == "gap_up"
    assert hi >= 114
