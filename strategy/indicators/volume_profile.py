"""Volume profile approximation (price-binned histogram)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd


@dataclass
class VolumeProfileResult:
    poc_price: float
    bin_edges: np.ndarray
    hist: np.ndarray
    total_volume: float


def compute_volume_profile(
    df: pd.DataFrame,
    *,
    num_bins: int = 240,
    lookback_bars: Optional[int] = None,
) -> Optional[VolumeProfileResult]:
    """Histogram of volume by price over the lookback window."""
    if df is None or df.empty:
        return None
    work = df.tail(lookback_bars) if lookback_bars else df
    if len(work) < 5:
        return None
    required = {"high", "low", "close", "volume"}
    if not required.issubset(work.columns):
        return None

    low = float(work["low"].min())
    high = float(work["high"].max())
    if high <= low:
        return None

    typical = (
        (work["high"].astype(float) + work["low"].astype(float) + work["close"].astype(float)) / 3.0
    )
    vol = work["volume"].astype(float).values
    hist, bin_edges = np.histogram(typical.values, bins=num_bins, range=(low, high), weights=vol)
    if hist.sum() <= 0:
        return None
    poc_idx = int(np.argmax(hist))
    poc_price = float((bin_edges[poc_idx] + bin_edges[poc_idx + 1]) / 2.0)
    return VolumeProfileResult(
        poc_price=poc_price,
        bin_edges=bin_edges,
        hist=hist,
        total_volume=float(hist.sum()),
    )


def nearest_hvn_below(
    profile: VolumeProfileResult,
    reference_price: float,
    *,
    min_volume_pct: float = 0.05,
) -> Optional[float]:
    """Nearest high-volume node (bin center) below reference_price."""
    if profile is None or profile.hist.sum() <= 0:
        return None
    threshold = float(profile.hist.max()) * min_volume_pct
    centers = (profile.bin_edges[:-1] + profile.bin_edges[1:]) / 2.0
    candidates = []
    for vol, center in zip(profile.hist, centers):
        if vol < threshold:
            continue
        if float(center) < reference_price:
            candidates.append(float(center))
    if not candidates:
        return None
    return max(candidates)


def volume_profile_to_dict(profile: Optional[VolumeProfileResult]) -> Dict[str, Any]:
    if profile is None:
        return {}
    return {
        "volume_profile_poc": profile.poc_price,
        "volume_profile_total": profile.total_volume,
    }
