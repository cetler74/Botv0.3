"""
RSI + StochRSI reversal on 5m — shared engine for spot and Hyperliquid perps.

Long: RSI(14) < oversold, StochRSI %K and %D both < stoch_oversold, and %K > %D.
Short (perps): StochRSI %K and %D both > stoch_overbought and %D > %K (no RSI gate).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd

try:
    import pandas_ta as ta
except ImportError:  # pragma: no cover
    ta = None

from strategy.indicators.stoch_rsi import compute_stoch_rsi
from strategy.playbooks.ohlcv_closed_bar import (
    last_closed_bar_index,
    prepare_closed_ohlcv,
)


@dataclass
class EngineParams:
    entry_timeframe: str = "5m"
    rsi_period: int = 14
    rsi_oversold: float = 30.0
    rsi_overbought: float = 80.0  # legacy; unused for entries
    stoch_oversold: float = 30.0
    stoch_overbought: float = 80.0
    stoch_rsi_length: int = 14
    stoch_k_smooth: int = 3
    stoch_d_smooth: int = 3
    buy_confidence: float = 0.72
    buy_strength: float = 0.70
    sell_confidence: float = 0.72
    sell_strength: float = 0.70
    blocked_regimes: List[str] = field(
        default_factory=lambda: ["trending_down", "low_volatility"]
    )
    allowed_exchanges: List[str] = field(default_factory=list)


@dataclass
class EngineResult:
    signal: str  # buy | sell | hold (spot); perp maps buy->long, sell->short
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
            setattr(
                base,
                key,
                [str(x).strip().lower() for x in val if str(x).strip()],
            )
        elif key == "allowed_exchanges" and val is not None:
            setattr(
                base,
                key,
                [str(x).strip().lower() for x in val if str(x).strip()],
            )
        else:
            setattr(base, key, val)
    return base


def _df(market_data: Any, key: str) -> Optional[pd.DataFrame]:
    if isinstance(market_data, dict):
        df = market_data.get(key)
        return df if isinstance(df, pd.DataFrame) and not df.empty else None
    return None


def _rsi_series(close: pd.Series, length: int) -> pd.Series:
    if ta is not None:
        out = ta.rsi(close.astype(float), length=length)
        if out is not None and not out.empty:
            return out.astype(float)
    close = close.astype(float)
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    avg_loss = loss.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    rs = avg_gain / avg_loss.replace(0.0, float("nan"))
    return (100.0 - (100.0 / (1.0 + rs))).fillna(50.0)


def _min_bars(params: EngineParams) -> int:
    return max(
        params.rsi_period,
        params.stoch_rsi_length + params.stoch_k_smooth + params.stoch_d_smooth,
    ) + 10


def evaluate_rsi_stoch_reversal_5m(
    market_data: Any,
    params: EngineParams,
    *,
    market_regime: str = "unknown",
    allow_short: bool = False,
    exchange_name: Optional[str] = None,
) -> EngineResult:
    """Evaluate entry on last closed 5m bar."""
    tf = str(params.entry_timeframe or "5m").lower()
    df = _df(market_data, tf)
    if df is None and isinstance(market_data, pd.DataFrame) and not market_data.empty:
        df = market_data
    hold_indicators: Dict[str, Any] = {
        "setup": "rsi_stoch_reversal_5m",
        "entry_timeframe": tf,
    }
    if df is None:
        return EngineResult(
            signal="hold",
            confidence=0.0,
            strength=0.0,
            indicators={**hold_indicators, "skip_reason": "insufficient_candles"},
            invalidation_reason="insufficient_candles",
        )

    df = prepare_closed_ohlcv(df, tf)
    if len(df) < _min_bars(params):
        return EngineResult(
            signal="hold",
            confidence=0.0,
            strength=0.0,
            indicators={**hold_indicators, "skip_reason": "insufficient_candles"},
            invalidation_reason="insufficient_candles",
        )

    regime_lc = str(market_regime or "unknown").strip().lower()
    blocked = {str(r).strip().lower() for r in (params.blocked_regimes or [])}
    if regime_lc in blocked:
        return EngineResult(
            signal="hold",
            confidence=0.0,
            strength=0.0,
            indicators={**hold_indicators, "skip_reason": f"regime_{regime_lc}"},
            invalidation_reason=f"regime_blocked:{regime_lc}",
        )

    if params.allowed_exchanges:
        ex_lc = str(exchange_name or "").strip().lower()
        allowed = {str(x).strip().lower() for x in params.allowed_exchanges}
        if ex_lc and ex_lc not in allowed:
            return EngineResult(
                signal="hold",
                confidence=0.0,
                strength=0.0,
                indicators={**hold_indicators, "skip_reason": "exchange_not_allowed"},
                invalidation_reason="exchange_not_allowed",
            )

    closed_idx = last_closed_bar_index(df)
    if len(df) < abs(closed_idx) + 1:
        return EngineResult(
            signal="hold",
            confidence=0.0,
            strength=0.0,
            indicators={**hold_indicators, "skip_reason": "insufficient_closed_bar"},
            invalidation_reason="insufficient_closed_bar",
        )

    close = df["close"].astype(float)
    rsi = _rsi_series(close, params.rsi_period)
    k_series, d_series = compute_stoch_rsi(
        close,
        rsi_length=params.rsi_period,
        stoch_length=params.stoch_rsi_length,
        k_smooth=params.stoch_k_smooth,
        d_smooth=params.stoch_d_smooth,
    )

    try:
        rsi_closed = float(rsi.iloc[closed_idx])
    except (TypeError, ValueError, IndexError):
        rsi_closed = float("nan")

    k_closed = d_closed = float("nan")
    if k_series is not None and d_series is not None and len(k_series) > abs(closed_idx):
        try:
            k_closed = float(k_series.iloc[closed_idx])
            d_closed = float(d_series.iloc[closed_idx])
        except (TypeError, ValueError, IndexError):
            pass

    entry_price = float(close.iloc[closed_idx])
    bar_time = None
    try:
        bar_time = str(df.index[closed_idx])
    except Exception:
        bar_time = None

    base_ind: Dict[str, Any] = {
        "setup": "rsi_stoch_reversal_5m",
        "entry_timeframe": tf,
        "rsi": rsi_closed,
        "stoch_rsi_k": k_closed,
        "stoch_rsi_d": d_closed,
        "entry_price": entry_price,
        "bar_close_time": bar_time,
        "market_regime": regime_lc,
    }

    if rsi_closed != rsi_closed or k_closed != k_closed or d_closed != d_closed:
        return EngineResult(
            signal="hold",
            confidence=0.0,
            strength=0.0,
            indicators={**base_ind, "skip_reason": "nan_indicators"},
            invalidation_reason="nan_indicators",
        )

    stoch_os = float(params.stoch_oversold)
    stoch_ob = float(params.stoch_overbought)
    rsi_os = float(params.rsi_oversold)
    long_ok = (
        rsi_closed < rsi_os
        and k_closed < stoch_os
        and d_closed < stoch_os
        and k_closed > d_closed
    )
    short_ok = (
        allow_short
        and k_closed > stoch_ob
        and d_closed > stoch_ob
        and d_closed > k_closed
    )

    if long_ok and short_ok:
        long_score = (rsi_os - rsi_closed) + (stoch_os - k_closed) + (stoch_os - d_closed)
        short_score = min(k_closed, d_closed) - stoch_ob
        if long_score >= short_score:
            short_ok = False
        else:
            long_ok = False

    if long_ok:
        reason = (
            f"RSI+StochRSI long ({tf}): RSI={rsi_closed:.2f} < {rsi_os}, "
            f"K={k_closed:.2f} < {stoch_os}, D={d_closed:.2f} < {stoch_os}, K > D"
        )
        return EngineResult(
            signal="buy",
            confidence=float(params.buy_confidence),
            strength=float(params.buy_strength),
            indicators={
                **base_ind,
                "side_intent": "long",
                "entry_reason": reason,
                "rsi_oversold_ok": True,
                "stoch_k_oversold_ok": True,
                "stoch_d_oversold_ok": True,
                "stoch_k_gt_d": True,
            },
            invalidation_reason="",
        )

    if short_ok:
        reason = (
            f"StochRSI short ({tf}): K={k_closed:.2f} > {stoch_ob}, "
            f"D={d_closed:.2f} > {stoch_ob}, D > K"
        )
        return EngineResult(
            signal="sell",
            confidence=float(params.sell_confidence),
            strength=float(params.sell_strength),
            indicators={
                **base_ind,
                "side_intent": "short",
                "entry_reason": reason,
                "stoch_k_overbought_ok": True,
                "stoch_d_overbought_ok": True,
                "stoch_d_gt_k": True,
            },
            invalidation_reason="",
        )

    fail_parts = []
    if allow_short:
        if k_closed <= stoch_ob:
            fail_parts.append(f"stoch_k_not_above_{stoch_ob:.0f}(K={k_closed:.2f})")
        if d_closed <= stoch_ob:
            fail_parts.append(f"stoch_d_not_above_{stoch_ob:.0f}(D={d_closed:.2f})")
        if d_closed <= k_closed:
            fail_parts.append(f"stoch_d_not_above_k(D={d_closed:.2f},K={k_closed:.2f})")
    if rsi_closed >= rsi_os:
        fail_parts.append(f"rsi_not_oversold({rsi_closed:.2f}>={rsi_os})")
    if k_closed >= stoch_os:
        fail_parts.append(f"stoch_k_not_below_{stoch_os:.0f}(K={k_closed:.2f})")
    if d_closed >= stoch_os:
        fail_parts.append(f"stoch_d_not_below_{stoch_os:.0f}(D={d_closed:.2f})")
    if k_closed <= d_closed:
        fail_parts.append(f"stoch_k_not_above_d_for_long(K={k_closed:.2f},D={d_closed:.2f})")
    skip = "; ".join(fail_parts) if fail_parts else "no_entry_conditions"
    return EngineResult(
        signal="hold",
        confidence=0.0,
        strength=0.0,
        indicators={**base_ind, "skip_reason": skip},
        invalidation_reason=skip,
    )
