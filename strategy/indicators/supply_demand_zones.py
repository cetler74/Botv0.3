"""Supply and demand zone detection from pre-impulse consolidation candles."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

import numpy as np
import pandas as pd

from strategy.indicators.market_structure import TrendDirection

ZoneKind = Literal["demand", "supply"]
ZoneStatus = Literal["active", "tested", "mitigated"]


@dataclass
class SupplyDemandZone:
    kind: ZoneKind
    zone_high: float
    zone_low: float
    bar_index: int
    impulse_bar_index: int
    status: ZoneStatus = "active"
    timestamp: Optional[str] = None

    @property
    def mid(self) -> float:
        return (self.zone_high + self.zone_low) / 2.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "zone_high": self.zone_high,
            "zone_low": self.zone_low,
            "bar_index": self.bar_index,
            "impulse_bar_index": self.impulse_bar_index,
            "status": self.status,
            "timestamp": self.timestamp,
        }


@dataclass
class ZoneScanResult:
    zones: List[SupplyDemandZone] = field(default_factory=list)
    active_zones: List[SupplyDemandZone] = field(default_factory=list)
    retest_zone: Optional[SupplyDemandZone] = None
    step2_pass: bool = False
    step2_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "zones": [z.to_dict() for z in self.zones],
            "active_zones": [z.to_dict() for z in self.active_zones],
            "retest_zone": self.retest_zone.to_dict() if self.retest_zone else None,
            "step2_pass": self.step2_pass,
            "step2_reason": self.step2_reason,
        }


def _candle_body_pct(open_: float, close: float, high: float, low: float) -> float:
    rng = max(high - low, 1e-12)
    body = abs(close - open_)
    return body / rng


def _candle_range(high: float, low: float) -> float:
    return max(high - low, 0.0)


def _median_range(df: pd.DataFrame, window: int = 20) -> float:
    ranges = (df["high"].astype(float) - df["low"].astype(float)).values
    if len(ranges) < 3:
        return float(np.median(ranges)) if len(ranges) else 0.0
    tail = ranges[-window:] if len(ranges) >= window else ranges
    return float(np.median(tail))


def _is_consolidation(
    open_: float,
    close: float,
    high: float,
    low: float,
    mid: float,
    *,
    max_body_ratio: float,
    max_width_pct: float,
) -> bool:
    if mid <= 0:
        return False
    width_pct = (high - low) / mid
    body_ratio = _candle_body_pct(open_, close, high, low)
    return width_pct <= max_width_pct and body_ratio <= max_body_ratio


def _is_bullish_impulse(
    open_: float,
    close: float,
    high: float,
    low: float,
    median_rng: float,
    *,
    min_body_pct: float,
    min_range_mult: float,
) -> bool:
    if close <= open_:
        return False
    rng = _candle_range(high, low)
    if median_rng <= 0:
        return False
    body_ratio = _candle_body_pct(open_, close, high, low)
    return body_ratio >= min_body_pct and rng >= median_rng * min_range_mult


def _is_bearish_impulse(
    open_: float,
    close: float,
    high: float,
    low: float,
    median_rng: float,
    *,
    min_body_pct: float,
    min_range_mult: float,
) -> bool:
    if close >= open_:
        return False
    rng = _candle_range(high, low)
    if median_rng <= 0:
        return False
    body_ratio = _candle_body_pct(open_, close, high, low)
    return body_ratio >= min_body_pct and rng >= median_rng * min_range_mult


def _update_zone_status(
    zones: List[SupplyDemandZone],
    df: pd.DataFrame,
    from_bar: int,
) -> None:
    """Mark zones tested/mitigated based on subsequent price action."""
    closes = df["close"].astype(float).values
    lows = df["low"].astype(float).values
    highs = df["high"].astype(float).values
    for zone in zones:
        if zone.status == "mitigated":
            continue
        for i in range(max(zone.impulse_bar_index + 1, from_bar), len(df)):
            if zone.kind == "demand":
                if lows[i] <= zone.zone_high and highs[i] >= zone.zone_low:
                    zone.status = "tested"
                if closes[i] < zone.zone_low:
                    zone.status = "mitigated"
            else:
                if highs[i] >= zone.zone_low and lows[i] <= zone.zone_high:
                    zone.status = "tested"
                if closes[i] > zone.zone_high:
                    zone.status = "mitigated"


def scan_supply_demand_zones(
    df: pd.DataFrame,
    trend: TrendDirection,
    *,
    max_consolidation_body_ratio: float = 0.45,
    max_zone_width_pct: float = 0.012,
    impulse_min_body_pct: float = 0.55,
    impulse_min_range_atr_mult: float = 1.8,
    zone_retest_tolerance_pct: float = 0.002,
    lookback_bars: int = 120,
    range_median_window: int = 20,
) -> ZoneScanResult:
    """
    Detect demand zones in uptrend and supply zones in downtrend.
    Zone = pre-impulse consolidation candle range; entry on retest.
    """
    result = ZoneScanResult(
        step2_pass=False,
        step2_reason="step1_trend_neutral_no_zones",
    )
    if df is None or len(df) < 10:
        result.step2_reason = "insufficient_candles"
        return result
    if trend == "neutral":
        return result

    work = df.tail(lookback_bars).copy().reset_index(drop=False)
    if "index" in work.columns and work.columns[0] != "timestamp":
        ts_col = work.columns[0]
    else:
        ts_col = None

    opens = work["open"].astype(float).values
    highs = work["high"].astype(float).values
    lows = work["low"].astype(float).values
    closes = work["close"].astype(float).values
    median_rng = _median_range(work, range_median_window)

    zones: List[SupplyDemandZone] = []
    zone_kind: ZoneKind = "demand" if trend == "uptrend" else "supply"

    for i in range(1, len(work) - 1):
        mid = (highs[i] + lows[i]) / 2.0
        if not _is_consolidation(
            opens[i],
            closes[i],
            highs[i],
            lows[i],
            mid,
            max_body_ratio=max_consolidation_body_ratio,
            max_width_pct=max_zone_width_pct,
        ):
            continue

        j = i + 1
        if trend == "uptrend":
            if not _is_bullish_impulse(
                opens[j],
                closes[j],
                highs[j],
                lows[j],
                median_rng,
                min_body_pct=impulse_min_body_pct,
                min_range_mult=impulse_min_range_atr_mult,
            ):
                continue
        else:
            if not _is_bearish_impulse(
                opens[j],
                closes[j],
                highs[j],
                lows[j],
                median_rng,
                min_body_pct=impulse_min_body_pct,
                min_range_mult=impulse_min_range_atr_mult,
            ):
                continue

        ts = str(work.iloc[i][ts_col]) if ts_col else None
        zones.append(
            SupplyDemandZone(
                kind=zone_kind,
                zone_high=float(highs[i]),
                zone_low=float(lows[i]),
                bar_index=i,
                impulse_bar_index=j,
                timestamp=ts,
            )
        )

    if not zones:
        result.step2_reason = f"no_{zone_kind}_zones_detected"
        return result

    _update_zone_status(zones, work, 0)
    active = [z for z in zones if z.status in ("active", "tested")]

    last_idx = len(work) - 1
    last_close = float(closes[last_idx])
    last_low = float(lows[last_idx])
    last_high = float(highs[last_idx])
    retest: Optional[SupplyDemandZone] = None

    for zone in reversed(active):
        tol = zone.mid * zone_retest_tolerance_pct
        if trend == "uptrend":
            if last_low <= zone.zone_high + tol and last_close >= zone.zone_low - tol:
                retest = zone
                break
        else:
            if last_high >= zone.zone_low - tol and last_close <= zone.zone_high + tol:
                retest = zone
                break

    result.zones = zones
    result.active_zones = active
    result.retest_zone = retest

    if retest is not None:
        result.step2_pass = True
        result.step2_reason = (
            f"{retest.kind}_zone_retest: zone [{retest.zone_low:.6f}, {retest.zone_high:.6f}] "
            f"from bar {retest.bar_index}; price {last_close:.6f} retesting zone"
        )
    elif active:
        result.step2_reason = (
            f"{len(active)} active {zone_kind} zone(s) but no retest on current bar "
            f"(close={last_close:.6f})"
        )
    else:
        result.step2_reason = f"all_{zone_kind}_zones_mitigated"

    return result
