"""Daily box break + retest playbook (previous-day H/L, 4h bias, 1h entry)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

from strategy.indicators.arc_area import compute_daily_box
from strategy.playbooks.ohlcv_closed_bar import prepare_closed_ohlcv


@dataclass
class EngineParams:
    bias_timeframe: str = "4h"
    entry_timeframe: str = "1h"
    daily_timeframe: str = "1d"
    bias_ema_period: int = 20
    min_daily_candles: int = 3
    min_bias_candles: int = 40
    min_entry_candles: int = 40
    break_lookback_bars: int = 12
    retest_lookback_bars: int = 8
    retest_tolerance_pct: float = 0.0025
    confirmation_buffer_pct: float = 0.0005
    stop_buffer_pct: float = 0.0015
    min_stop_pct: float = 0.006
    max_stop_pct: float = 0.040
    min_reward_risk: float = 2.5
    min_box_range_pct: float = 0.004
    max_box_range_pct: float = 0.120
    allow_long: bool = True
    allow_short: bool = True
    buy_confidence: float = 0.78
    buy_strength: float = 0.75
    sell_confidence: float = 0.78
    sell_strength: float = 0.75
    blocked_regimes: List[str] = field(
        default_factory=lambda: ["sideways", "low_volatility"]
    )
    long_allowed_regimes: List[str] = field(
        default_factory=lambda: ["trending_up", "breakout", "high_volatility"]
    )
    short_allowed_regimes: List[str] = field(
        default_factory=lambda: ["trending_down", "breakout", "high_volatility"]
    )


@dataclass
class EngineResult:
    signal: str
    confidence: float
    strength: float
    indicators: Dict[str, Any]
    invalidation_reason: str = ""


def params_from_config(config: Dict[str, Any]) -> EngineParams:
    p = dict(config.get("parameters") or {}) if isinstance(config, dict) else {}
    base = EngineParams()
    for key, val in p.items():
        if key in EngineParams.__dataclass_fields__:
            setattr(base, key, val)
    return base


def _df(market_data: Any, key: str) -> Optional[pd.DataFrame]:
    if isinstance(market_data, dict):
        df = market_data.get(key)
        return df if isinstance(df, pd.DataFrame) and not df.empty else None
    return market_data if isinstance(market_data, pd.DataFrame) and not market_data.empty else None


def _ema(close: pd.Series, length: int) -> pd.Series:
    return close.astype(float).ewm(span=length, adjust=False, min_periods=length).mean()


def _hold(reason: str, extra: Optional[Dict[str, Any]] = None) -> EngineResult:
    payload: Dict[str, Any] = {
        "setup": "daily_box_break_retest",
        "skip_reason": reason,
        "invalidation_reason": reason,
        "rejection_reason": reason,
        "entry_reason": "",
    }
    if extra:
        payload.update(extra)
    return EngineResult("hold", 0.0, 0.0, payload, reason)


def _risk_targets(
    *,
    side: str,
    entry: float,
    box_high: float,
    box_low: float,
    params: EngineParams,
) -> Optional[Dict[str, float]]:
    if entry <= 0 or box_high <= box_low:
        return None
    if side == "long":
        raw_stop = box_low * (1.0 - float(params.stop_buffer_pct))
        stop_pct = (entry - raw_stop) / entry
        stop_pct = max(float(params.min_stop_pct), min(float(params.max_stop_pct), stop_pct))
        stop = entry * (1.0 - stop_pct)
        target = entry + (entry - stop) * float(params.min_reward_risk)
    else:
        raw_stop = box_high * (1.0 + float(params.stop_buffer_pct))
        stop_pct = (raw_stop - entry) / entry
        stop_pct = max(float(params.min_stop_pct), min(float(params.max_stop_pct), stop_pct))
        stop = entry * (1.0 + stop_pct)
        target = entry - (stop - entry) * float(params.min_reward_risk)
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    reward = abs(target - entry)
    rr = reward / risk
    if rr + 1e-9 < float(params.min_reward_risk):
        return None
    return {
        "entry_price": entry,
        "stop_hint": stop,
        "target_hint": target,
        "stop_pct": stop_pct * 100.0,
        "target_pct": (reward / entry) * 100.0,
        "reward_risk": rr,
    }


def _scan_break_retest(
    entry_df: pd.DataFrame,
    *,
    side: str,
    box_high: float,
    box_low: float,
    params: EngineParams,
) -> Dict[str, Any]:
    """Scan recent closed 1h bars for break → retest → confirmation."""
    highs = entry_df["high"].astype(float)
    lows = entry_df["low"].astype(float)
    closes = entry_df["close"].astype(float)
    n = len(entry_df)
    lookback = min(n - 2, int(params.break_lookback_bars) + int(params.retest_lookback_bars) + 2)
    start = max(1, n - lookback)
    tol = float(params.retest_tolerance_pct)
    conf_buf = float(params.confirmation_buffer_pct)
    level = box_high if side == "long" else box_low

    break_idx: Optional[int] = None
    for i in range(start, n - 1):
        c = float(closes.iloc[i])
        if side == "long" and c > box_high * (1.0 + conf_buf):
            break_idx = i
            break
        if side == "short" and c < box_low * (1.0 - conf_buf):
            break_idx = i
            break
    if break_idx is None:
        return {"ok": False, "reason": "no_break"}

    retest_idx: Optional[int] = None
    retest_end = min(n - 1, break_idx + int(params.retest_lookback_bars) + 1)
    for j in range(break_idx + 1, retest_end):
        hi = float(highs.iloc[j])
        lo = float(lows.iloc[j])
        if side == "long":
            # Pullback tags the broken PDH from above.
            if lo <= level * (1.0 + tol) and lo >= level * (1.0 - tol * 2.0):
                retest_idx = j
                break
        else:
            if hi >= level * (1.0 - tol) and hi <= level * (1.0 + tol * 2.0):
                retest_idx = j
                break
    if retest_idx is None:
        return {"ok": False, "reason": "no_retest", "break_idx": break_idx}

    # Confirmation must be the latest closed bar (or the bar after retest if last).
    conf_idx = n - 1
    if conf_idx <= retest_idx:
        return {"ok": False, "reason": "waiting_confirmation", "break_idx": break_idx, "retest_idx": retest_idx}
    conf_close = float(closes.iloc[conf_idx])
    if side == "long" and conf_close <= level * (1.0 + conf_buf):
        return {"ok": False, "reason": "confirmation_failed", "break_idx": break_idx, "retest_idx": retest_idx}
    if side == "short" and conf_close >= level * (1.0 - conf_buf):
        return {"ok": False, "reason": "confirmation_failed", "break_idx": break_idx, "retest_idx": retest_idx}

    return {
        "ok": True,
        "break_idx": break_idx,
        "retest_idx": retest_idx,
        "conf_idx": conf_idx,
        "entry_price": conf_close,
        "level": level,
    }


def evaluate_daily_box_break_retest(
    market_data: Any,
    params: EngineParams,
    *,
    market_regime: str = "unknown",
    allow_short: Optional[bool] = None,
) -> EngineResult:
    regime = str(market_regime or "unknown").lower()
    blocked = {str(x).strip().lower() for x in (params.blocked_regimes or []) if str(x).strip()}
    if regime in blocked:
        return _hold("blocked_regime", {"market_regime": regime})

    short_ok = params.allow_short if allow_short is None else bool(allow_short)

    daily_raw = _df(market_data, params.daily_timeframe)
    bias_raw = _df(market_data, params.bias_timeframe)
    entry_raw = _df(market_data, params.entry_timeframe)
    if daily_raw is None:
        return _hold(f"missing_timeframe:{params.daily_timeframe}")
    if bias_raw is None:
        return _hold(f"missing_timeframe:{params.bias_timeframe}")
    if entry_raw is None:
        return _hold(f"missing_timeframe:{params.entry_timeframe}")

    daily = prepare_closed_ohlcv(daily_raw, params.daily_timeframe)
    bias = prepare_closed_ohlcv(bias_raw, params.bias_timeframe)
    entry = prepare_closed_ohlcv(entry_raw, params.entry_timeframe)
    if len(daily) < int(params.min_daily_candles):
        return _hold("insufficient_daily_candles", {"candle_count": len(daily)})
    if len(bias) < int(params.min_bias_candles):
        return _hold("insufficient_bias_candles", {"candle_count": len(bias)})
    if len(entry) < int(params.min_entry_candles):
        return _hold("insufficient_entry_candles", {"candle_count": len(entry)})

    box = compute_daily_box(daily)
    if box is None:
        return _hold("daily_box_unavailable")
    box_high = float(box.box_high)
    box_low = float(box.box_low)
    mid = (box_high + box_low) / 2.0
    range_pct = (box_high - box_low) / mid if mid > 0 else 0.0
    if range_pct < float(params.min_box_range_pct):
        return _hold("box_range_too_small", {"box_range_pct": range_pct})
    if range_pct > float(params.max_box_range_pct):
        return _hold("box_range_too_wide", {"box_range_pct": range_pct})

    bias_ema = _ema(bias["close"], int(params.bias_ema_period))
    bias_close = float(bias["close"].iloc[-1])
    bias_ema_val = float(bias_ema.iloc[-1])
    if pd.isna(bias_ema_val):
        return _hold("bias_ema_unavailable")

    base_extra = {
        "market_regime": regime,
        "box_high": box_high,
        "box_low": box_low,
        "box_date": box.box_date,
        "box_range_pct": range_pct,
        "bias_ema": bias_ema_val,
        "bias_close": bias_close,
        "bias_timeframe": params.bias_timeframe,
        "entry_timeframe": params.entry_timeframe,
        "daily_timeframe": params.daily_timeframe,
    }

    long_regimes = {str(x).lower() for x in (params.long_allowed_regimes or [])}
    short_regimes = {str(x).lower() for x in (params.short_allowed_regimes or [])}

    # Prefer long if bias up; short if bias down.
    candidates: List[str] = []
    if params.allow_long and bias_close > bias_ema_val:
        if not long_regimes or regime in long_regimes or regime == "unknown":
            candidates.append("long")
    if short_ok and bias_close < bias_ema_val:
        if not short_regimes or regime in short_regimes or regime == "unknown":
            candidates.append("short")
    if not candidates:
        return _hold("no_trend_bias", base_extra)

    for side in candidates:
        scan = _scan_break_retest(
            entry, side=side, box_high=box_high, box_low=box_low, params=params
        )
        if not scan.get("ok"):
            continue
        entry_px = float(scan["entry_price"])
        risk = _risk_targets(
            side=side,
            entry=entry_px,
            box_high=box_high,
            box_low=box_low,
            params=params,
        )
        if risk is None:
            continue
        direction = "buy" if side == "long" else "sell"
        conf = float(params.buy_confidence if side == "long" else params.sell_confidence)
        strength = float(params.buy_strength if side == "long" else params.sell_strength)
        reason = (
            f"Daily box {side}: prev-day H {box_high:.6f} / L {box_low:.6f} "
            f"({box.box_date}); 4h close {bias_close:.6f} vs EMA{params.bias_ema_period} "
            f"{bias_ema_val:.6f}; 1h break→retest→confirm @ {entry_px:.6f}; "
            f"stop {risk['stop_hint']:.6f} target {risk['target_hint']:.6f} "
            f"R:R {risk['reward_risk']:.2f}"
        )
        indicators = {
            "setup": "daily_box_break_retest",
            "direction": side,
            "entry_reason": reason,
            "break_idx": scan.get("break_idx"),
            "retest_idx": scan.get("retest_idx"),
            "level": scan.get("level"),
            **base_extra,
            **risk,
        }
        return EngineResult(direction, conf, strength, indicators)

    return _hold("no_break_retest_confirm", base_extra)
