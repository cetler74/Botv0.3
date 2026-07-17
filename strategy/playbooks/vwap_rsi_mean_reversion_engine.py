"""VWAP/RSI mean-reversion — 1h range bias + 15m VWAP/Bollinger excursion reclaim.

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
    max_bias_slope_pct: float = 0.004
    vwap_lookback_bars: int = 48
    bb_period: int = 20
    bb_std: float = 2.0
    rsi_period: int = 14
    rsi_oversold: float = 35.0
    rsi_reclaim: float = 40.0
    rsi_overbought: float = 65.0
    rsi_release: float = 60.0
    atr_period: int = 14
    atr_stop_mult: float = 1.0
    min_reward_risk: float = 1.2
    buy_confidence: float = 0.72
    buy_strength: float = 0.70
    sell_confidence: float = 0.72
    sell_strength: float = 0.70
    allow_long: bool = True
    allow_short: bool = True
    blocked_regimes: List[str] = field(
        default_factory=lambda: ["trending_up", "trending_down", "breakout"]
    )
    min_candles_bias: int = 40
    min_candles_entry: int = 50


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
        "setup": "vwap_rsi_mean_reversion",
    }
    if extra:
        payload.update(extra)
    return EngineResult("hold", 0.0, 0.0, payload, reason)


def _ema_series(close: pd.Series, period: int) -> Optional[pd.Series]:
    if ta is None:
        return close.astype(float).ewm(span=period, adjust=False).mean()
    out = ta.ema(close.astype(float), length=period)
    return out if out is not None and not out.empty else None


def _rsi_series(close: pd.Series, period: int) -> Optional[pd.Series]:
    if ta is None:
        delta = close.astype(float).diff()
        gain = delta.clip(lower=0.0).rolling(period, min_periods=period).mean()
        loss = (-delta.clip(upper=0.0)).rolling(period, min_periods=period).mean()
        rs = gain / loss.replace(0, pd.NA)
        return 100.0 - (100.0 / (1.0 + rs))
    out = ta.rsi(close.astype(float), length=period)
    return out if out is not None and not out.empty else None


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


def _session_vwap(df: pd.DataFrame, lookback: int) -> pd.Series:
    window = df.iloc[-lookback:] if len(df) >= lookback else df
    typical = (window["high"].astype(float) + window["low"].astype(float) + window["close"].astype(float)) / 3.0
    vol = window["volume"].astype(float).clip(lower=1e-9)
    cum_pv = (typical * vol).cumsum()
    cum_v = vol.cumsum()
    vwap = cum_pv / cum_v
    # Align to full frame index with NaN prefix.
    out = pd.Series(index=df.index, dtype=float)
    out.loc[vwap.index] = vwap
    return out.ffill()


def _bollinger(close: pd.Series, period: int, std_mult: float) -> tuple[pd.Series, pd.Series, pd.Series]:
    mid = close.astype(float).rolling(period, min_periods=period).mean()
    std = close.astype(float).rolling(period, min_periods=period).std()
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    return lower, mid, upper


def _is_1h_range(bias_df: pd.DataFrame, params: EngineParams) -> tuple[bool, Dict[str, Any]]:
    ema = _ema_series(bias_df["close"], params.bias_ema_period)
    if ema is None or len(ema) < params.bias_ema_period + 3:
        return False, {"reason": "bias_ema_unavailable"}
    ema_now = float(ema.iloc[-1])
    ema_prev = float(ema.iloc[-4])
    close = float(bias_df["close"].iloc[-1])
    if pd.isna(ema_now) or pd.isna(ema_prev) or close <= 0:
        return False, {"reason": "bias_ema_nan"}
    slope_pct = abs(ema_now - ema_prev) / close
    ok = slope_pct <= float(params.max_bias_slope_pct)
    return ok, {"bias_slope_pct": slope_pct, "bias_ema": ema_now, "close": close}


def evaluate_vwap_rsi_mean_reversion(
    market_data: Dict[str, pd.DataFrame],
    params: EngineParams,
    *,
    market_regime: str = "unknown",
    allow_short: bool = True,
) -> EngineResult:
    base: Dict[str, Any] = {
        "setup": "vwap_rsi_mean_reversion",
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

    range_ok, range_meta = _is_1h_range(bias_df, params)
    base.update(range_meta)
    if not range_ok:
        return _hold("1h_not_range_qualified", base)

    close = entry_df["close"].astype(float)
    vwap = _session_vwap(entry_df, params.vwap_lookback_bars)
    lower, mid, upper = _bollinger(close, params.bb_period, params.bb_std)
    rsi = _rsi_series(close, params.rsi_period)
    atr = _atr_series(entry_df, params.atr_period)
    if rsi is None or atr is None:
        return _hold("indicators_unavailable", base)

    entry_price = float(close.iloc[-1])
    vwap_now = float(vwap.iloc[-1])
    lower_now = float(lower.iloc[-1])
    mid_now = float(mid.iloc[-1])
    upper_now = float(upper.iloc[-1])
    rsi_now = float(rsi.iloc[-1])
    rsi_prev = float(rsi.iloc[-2])
    atr_val = float(atr.iloc[-1])
    if any(pd.isna(x) for x in (vwap_now, lower_now, mid_now, upper_now, rsi_now, rsi_prev, atr_val)):
        return _hold("indicators_nan", base)
    if atr_val <= 0:
        return _hold("atr_unavailable", base)

    base.update(
        {
            "entry_price": entry_price,
            "vwap": vwap_now,
            "bb_lower": lower_now,
            "bb_mid": mid_now,
            "bb_upper": upper_now,
            "rsi": rsi_now,
            "atr": atr_val,
        }
    )

    recent_low = float(entry_df["low"].iloc[-6:].min())
    recent_high = float(entry_df["high"].iloc[-6:].max())

    long_excursion = recent_low <= min(lower_now, vwap_now) or any(
        float(close.iloc[i]) < min(float(lower.iloc[i]), float(vwap.iloc[i]))
        for i in range(-6, -1)
        if not pd.isna(lower.iloc[i]) and not pd.isna(vwap.iloc[i])
    )
    short_excursion = recent_high >= max(upper_now, vwap_now) or any(
        float(close.iloc[i]) > max(float(upper.iloc[i]), float(vwap.iloc[i]))
        for i in range(-6, -1)
        if not pd.isna(upper.iloc[i]) and not pd.isna(vwap.iloc[i])
    )

    long_reclaim = (
        params.allow_long
        and long_excursion
        and rsi_prev <= params.rsi_oversold
        and rsi_now >= params.rsi_reclaim
        and entry_price > vwap_now * 0.998
        and float(entry_df["close"].iloc[-1]) > float(entry_df["open"].iloc[-1])
    )
    short_reclaim = (
        params.allow_short
        and allow_short
        and short_excursion
        and rsi_prev >= params.rsi_overbought
        and rsi_now <= params.rsi_release
        and entry_price < vwap_now * 1.002
        and float(entry_df["close"].iloc[-1]) < float(entry_df["open"].iloc[-1])
    )

    if long_reclaim:
        stop = recent_low - params.atr_stop_mult * atr_val
        risk = entry_price - stop
        if risk <= 0:
            return _hold("invalid_stop", base)
        target = max(mid_now, vwap_now)
        if target <= entry_price:
            target = entry_price + params.min_reward_risk * risk
        rr = (target - entry_price) / risk
        if rr < params.min_reward_risk:
            target = entry_price + params.min_reward_risk * risk
            rr = params.min_reward_risk
        reason = (
            f"1h range qualified; 15m downside VWAP/Bollinger excursion with RSI reclaim "
            f"({rsi_prev:.1f}->{rsi_now:.1f}) targeting mean {target:.4f}"
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
                "entry_reason": reason,
                "invalidation_reason": "none",
                "skip_reason": "",
            },
            "none",
        )

    if short_reclaim:
        stop = recent_high + params.atr_stop_mult * atr_val
        risk = stop - entry_price
        if risk <= 0:
            return _hold("invalid_stop", base)
        target = min(mid_now, vwap_now)
        if target >= entry_price:
            target = entry_price - params.min_reward_risk * risk
        rr = (entry_price - target) / risk
        if rr < params.min_reward_risk:
            target = entry_price - params.min_reward_risk * risk
            rr = params.min_reward_risk
        reason = (
            f"1h range qualified; 15m upside VWAP/Bollinger excursion with RSI release "
            f"({rsi_prev:.1f}->{rsi_now:.1f}) targeting mean {target:.4f}"
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
                "entry_reason": reason,
                "invalidation_reason": "none",
                "skip_reason": "",
            },
            "none",
        )

    return _hold("no_vwap_rsi_reclaim", base)
