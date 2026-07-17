"""Donchian/ATR pullback — 1h trend bias + 15m breakout-pullback reclaim.

Spot emits buy only; perps map buy->long and sell->short.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

try:
    import pandas_ta as ta
except Exception:  # pragma: no cover — pandas_ta can fail beyond ImportError
    ta = None

from strategy.playbooks.ohlcv_closed_bar import prepare_closed_ohlcv


@dataclass
class EngineParams:
    bias_timeframe: str = "1h"
    entry_timeframe: str = "15m"
    bias_ema_period: int = 20
    donchian_period: int = 20
    atr_period: int = 14
    atr_stop_mult: float = 1.2
    pullback_lookback_bars: int = 8
    min_reward_risk: float = 1.5
    buy_confidence: float = 0.74
    buy_strength: float = 0.72
    sell_confidence: float = 0.74
    sell_strength: float = 0.72
    allow_long: bool = True
    allow_short: bool = True
    blocked_regimes: List[str] = field(
        default_factory=lambda: ["low_volatility", "sideways"]
    )
    min_candles_bias: int = 40
    min_candles_entry: int = 40


@dataclass
class EngineResult:
    signal: str
    confidence: float
    strength: float
    indicators: Dict[str, Any]
    invalidation_reason: str = ""


def params_from_config(config: Dict[str, Any]) -> EngineParams:
    p = dict(config.get("parameters") or {}) if isinstance(config, dict) else {}
    kw = {k: v for k, v in p.items() if k in EngineParams.__dataclass_fields__}
    base = EngineParams()
    for key, val in kw.items():
        if key == "blocked_regimes" and val is not None:
            setattr(base, key, [str(x) for x in val])
        else:
            setattr(base, key, val)
    return base


def _df(market_data: Any, key: str) -> Optional[pd.DataFrame]:
    if isinstance(market_data, dict):
        frame = market_data.get(key)
        return frame if isinstance(frame, pd.DataFrame) and not frame.empty else None
    return market_data if isinstance(market_data, pd.DataFrame) and not market_data.empty else None


def _hold(reason: str, extra: Optional[Dict[str, Any]] = None) -> EngineResult:
    payload: Dict[str, Any] = {
        "invalidation_reason": reason,
        "skip_reason": reason,
        "entry_reason": "",
        "setup": "donchian_atr_pullback",
    }
    if extra:
        payload.update(extra)
    return EngineResult("hold", 0.0, 0.0, payload, reason)


def _atr_series(df: pd.DataFrame, period: int) -> Optional[pd.Series]:
    if ta is None:
        high = df["high"].astype(float)
        low = df["low"].astype(float)
        close = df["close"].astype(float)
        prev_close = close.shift(1)
        tr = pd.concat(
            [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
            axis=1,
        ).max(axis=1)
        return tr.rolling(period, min_periods=period).mean()
    out = ta.atr(df["high"], df["low"], df["close"], length=period)
    return out if out is not None and not out.empty else None


def _ema_series(close: pd.Series, period: int) -> Optional[pd.Series]:
    if ta is None:
        return close.astype(float).ewm(span=period, adjust=False).mean()
    out = ta.ema(close.astype(float), length=period)
    return out if out is not None and not out.empty else None


def _bias_direction(bias_df: pd.DataFrame, params: EngineParams) -> str:
    ema = _ema_series(bias_df["close"], params.bias_ema_period)
    if ema is None or len(ema) < params.bias_ema_period + 3:
        return "none"
    close = float(bias_df["close"].iloc[-1])
    ema_now = float(ema.iloc[-1])
    ema_prev = float(ema.iloc[-4])
    if pd.isna(ema_now) or pd.isna(ema_prev):
        return "none"
    slope = ema_now - ema_prev
    if close > ema_now and slope > 0:
        return "up"
    if close < ema_now and slope < 0:
        return "down"
    return "none"


def _donchian_high_low(df: pd.DataFrame, period: int, end_idx: int) -> tuple[float, float]:
    window = df.iloc[max(0, end_idx - period) : end_idx]
    return float(window["high"].max()), float(window["low"].min())


def evaluate_donchian_atr_pullback(
    market_data: Dict[str, pd.DataFrame],
    params: EngineParams,
    *,
    market_regime: str = "unknown",
    allow_short: bool = True,
) -> EngineResult:
    base = {
        "setup": "donchian_atr_pullback",
        "bias_timeframe": params.bias_timeframe,
        "entry_timeframe": params.entry_timeframe,
        "market_regime": market_regime,
    }

    blocked = {str(x).strip().lower() for x in (params.blocked_regimes or [])}
    if blocked and str(market_regime or "").lower() in blocked:
        return _hold("blocked_regime", base)

    bias_raw = _df(market_data, params.bias_timeframe)
    entry_raw = _df(market_data, params.entry_timeframe)
    if bias_raw is None or entry_raw is None:
        return _hold("missing_market_data", base)

    bias_df = prepare_closed_ohlcv(bias_raw, params.bias_timeframe)
    entry_df = prepare_closed_ohlcv(entry_raw, params.entry_timeframe)
    if len(bias_df) < params.min_candles_bias or len(entry_df) < params.min_candles_entry:
        return _hold("insufficient_candles", base)

    bias = _bias_direction(bias_df, params)
    base["bias_direction"] = bias
    if bias == "none":
        return _hold("1h_trend_missing", {**base, "step1_pass": False})

    atr = _atr_series(entry_df, params.atr_period)
    if atr is None or pd.isna(atr.iloc[-1]) or float(atr.iloc[-1]) <= 0:
        return _hold("atr_unavailable", base)
    atr_val = float(atr.iloc[-1])

    lookback = max(3, int(params.pullback_lookback_bars))
    if len(entry_df) < params.donchian_period + lookback + 2:
        return _hold("insufficient_candles", base)

    # Prior channel excluding the recent pullback window and current bar.
    channel_end = len(entry_df) - lookback - 1
    if channel_end <= params.donchian_period:
        return _hold("insufficient_candles", base)
    don_hi, don_lo = _donchian_high_low(entry_df, params.donchian_period, channel_end)

    recent = entry_df.iloc[-(lookback + 1) : -1]
    last = entry_df.iloc[-1]
    entry_price = float(last["close"])
    base.update(
        {
            "donchian_high": don_hi,
            "donchian_low": don_lo,
            "atr": atr_val,
            "entry_price": entry_price,
        }
    )

    want_long = bias == "up" and params.allow_long
    want_short = bias == "down" and params.allow_short and allow_short

    if want_long:
        broke = bool((recent["high"] > don_hi).any())
        pullback_low = float(recent["low"].min())
        reclaimed = entry_price > don_hi and float(last["close"]) > float(last["open"])
        if not broke:
            return _hold("no_15m_donchian_breakout", {**base, "broke": False})
        if pullback_low >= don_hi:
            return _hold("no_pullback_after_breakout", {**base, "pullback_low": pullback_low})
        if not reclaimed:
            return _hold("no_reclaim_after_pullback", {**base, "pullback_low": pullback_low})

        stop = pullback_low - params.atr_stop_mult * atr_val
        risk = entry_price - stop
        if risk <= 0:
            return _hold("invalid_stop", base)
        target = entry_price + max(params.min_reward_risk, 1.0) * risk
        rr = (target - entry_price) / risk
        if rr + 1e-9 < params.min_reward_risk:
            return _hold("reward_risk_below_min", {**base, "reward_risk": rr})

        reason = (
            f"1h uptrend confirmed; 15m Donchian breakout above {don_hi:.4f} "
            f"pulled back to {pullback_low:.4f} then reclaimed with ATR stop"
        )
        return EngineResult(
            "buy",
            params.buy_confidence,
            params.buy_strength,
            {
                **base,
                "side": "long",
                "stop_hint": stop,
                "target_hint": target,
                "reward_risk": rr,
                "pullback_low": pullback_low,
                "entry_reason": reason,
                "invalidation_reason": "none",
                "skip_reason": "",
            },
            "none",
        )

    if want_short:
        broke = bool((recent["low"] < don_lo).any())
        pullback_high = float(recent["high"].max())
        reclaimed = entry_price < don_lo and float(last["close"]) < float(last["open"])
        if not broke:
            return _hold("no_15m_donchian_breakout", {**base, "broke": False})
        if pullback_high <= don_lo:
            return _hold("no_pullback_after_breakout", {**base, "pullback_high": pullback_high})
        if not reclaimed:
            return _hold("no_reclaim_after_pullback", {**base, "pullback_high": pullback_high})

        stop = pullback_high + params.atr_stop_mult * atr_val
        risk = stop - entry_price
        if risk <= 0:
            return _hold("invalid_stop", base)
        target = entry_price - max(params.min_reward_risk, 1.0) * risk
        rr = (entry_price - target) / risk
        if rr + 1e-9 < params.min_reward_risk:
            return _hold("reward_risk_below_min", {**base, "reward_risk": rr})

        reason = (
            f"1h downtrend confirmed; 15m Donchian breakdown below {don_lo:.4f} "
            f"pulled back to {pullback_high:.4f} then reclaimed with ATR stop"
        )
        return EngineResult(
            "sell",
            params.sell_confidence,
            params.sell_strength,
            {
                **base,
                "side": "short",
                "stop_hint": stop,
                "target_hint": target,
                "reward_risk": rr,
                "pullback_high": pullback_high,
                "entry_reason": reason,
                "invalidation_reason": "none",
                "skip_reason": "",
            },
            "none",
        )

    return _hold("side_disabled_or_bias_mismatch", base)
