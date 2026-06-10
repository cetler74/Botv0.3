"""
RSI + StochRSI reversal on 5m — shared engine for spot and Hyperliquid perps.

Long: RSI(14) < oversold, StochRSI %K and %D both < stoch_oversold, and %K > %D.
Short (perps): RSI(14) > overbought, StochRSI %K and %D both > stoch_overbought, and %D > %K.
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
    rsi_overbought: float = 80.0
    stoch_oversold: float = 30.0
    stoch_overbought: float = 80.0
    stoch_rsi_length: int = 14
    stoch_k_smooth: int = 3
    stoch_d_smooth: int = 3
    buy_confidence: float = 0.72
    buy_strength: float = 0.70
    sell_confidence: float = 0.72
    sell_strength: float = 0.70
    rsi_reclaim_enabled: bool = False
    rsi_reclaim_lookback_bars: int = 3
    rsi_reclaim_ceiling: float = 32.0
    min_stoch_cross_gap: float = 0.0
    require_stoch_cross_momentum: bool = False
    confirmation_timeframe: str = ""
    confirmation_rsi_period: int = 14
    confirmation_ema_period: int = 9
    require_confirmation: bool = False
    expected_move_lookback_bars: int = 14
    min_expected_move_pct: float = 0.0
    blocked_regime_reclaim_bypass: List[str] = field(default_factory=list)
    blocked_regimes: List[str] = field(
        default_factory=lambda: ["trending_down", "low_volatility"]
    )
    short_blocked_regimes: List[str] = field(default_factory=list)
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
        if key in {
            "blocked_regimes",
            "short_blocked_regimes",
            "blocked_regime_reclaim_bypass",
        } and val is not None:
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
    """Evaluate entry on last closed entry-timeframe bar."""
    tf = str(params.entry_timeframe or "5m").lower()
    setup_name = f"rsi_stoch_reversal_{tf}"
    df = _df(market_data, tf)
    if df is None and isinstance(market_data, pd.DataFrame) and not market_data.empty:
        df = market_data
    hold_indicators: Dict[str, Any] = {
        "setup": setup_name,
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
    k_previous = float("nan")
    if k_series is not None and d_series is not None and len(k_series) > abs(closed_idx):
        try:
            k_closed = float(k_series.iloc[closed_idx])
            d_closed = float(d_series.iloc[closed_idx])
            k_previous = float(k_series.iloc[closed_idx - 1])
        except (TypeError, ValueError, IndexError):
            pass

    entry_price = float(close.iloc[closed_idx])
    bar_time = None
    try:
        bar_time = str(df.index[closed_idx])
    except Exception:
        bar_time = None

    rsi_recent_min = rsi_closed
    try:
        lookback = max(1, int(params.rsi_reclaim_lookback_bars or 1))
        start = max(0, len(rsi) + closed_idx - lookback + 1)
        end = len(rsi) + closed_idx + 1
        rsi_recent_min = float(rsi.iloc[start:end].min())
    except Exception:
        rsi_recent_min = rsi_closed

    base_ind: Dict[str, Any] = {
        "setup": setup_name,
        "entry_timeframe": tf,
        "rsi": rsi_closed,
        "rsi_recent_min": rsi_recent_min,
        "stoch_rsi_k": k_closed,
        "stoch_rsi_d": d_closed,
        "stoch_rsi_k_previous": k_previous,
        "stoch_cross_gap": abs(k_closed - d_closed),
        "entry_price": entry_price,
        "bar_close_time": bar_time,
        "market_regime": regime_lc,
    }

    expected_move_pct = 0.0
    try:
        lookback = max(2, int(params.expected_move_lookback_bars or 14))
        recent = df.iloc[max(0, len(df) + closed_idx - lookback + 1) : len(df) + closed_idx + 1]
        ranges = (
            (recent["high"].astype(float) - recent["low"].astype(float))
            / recent["close"].astype(float).replace(0.0, float("nan"))
            * 100.0
        )
        expected_move_pct = float(ranges.mean())
    except Exception:
        expected_move_pct = 0.0
    base_ind["expected_move_pct"] = expected_move_pct

    confirmation_long = confirmation_short = not bool(params.require_confirmation)
    confirmation_tf = str(params.confirmation_timeframe or "").strip().lower()
    if confirmation_tf:
        confirmation = _df(market_data, confirmation_tf)
        if confirmation is not None:
            confirmation = prepare_closed_ohlcv(confirmation, confirmation_tf)
        min_confirmation_bars = max(
            int(params.confirmation_rsi_period or 14),
            int(params.confirmation_ema_period or 9),
        ) + 3
        if confirmation is not None and len(confirmation) >= min_confirmation_bars:
            c_idx = last_closed_bar_index(confirmation)
            c_close = confirmation["close"].astype(float)
            c_rsi = _rsi_series(c_close, int(params.confirmation_rsi_period or 14))
            c_ema = c_close.ewm(
                span=max(2, int(params.confirmation_ema_period or 9)),
                adjust=False,
            ).mean()
            c_rsi_now = float(c_rsi.iloc[c_idx])
            c_rsi_prev = float(c_rsi.iloc[c_idx - 1])
            c_close_now = float(c_close.iloc[c_idx])
            c_open_now = float(confirmation["open"].astype(float).iloc[c_idx])
            c_ema_now = float(c_ema.iloc[c_idx])
            confirmation_long = bool(
                c_rsi_now > c_rsi_prev
                or c_close_now > c_ema_now
                or c_close_now > c_open_now
            )
            confirmation_short = bool(
                c_rsi_now < c_rsi_prev
                or c_close_now < c_ema_now
                or c_close_now < c_open_now
            )
            base_ind.update(
                {
                    "confirmation_timeframe": confirmation_tf,
                    "confirmation_rsi": c_rsi_now,
                    "confirmation_rsi_previous": c_rsi_prev,
                    "confirmation_ema": c_ema_now,
                    "confirmation_close": c_close_now,
                    "confirmation_long": confirmation_long,
                    "confirmation_short": confirmation_short,
                }
            )
        else:
            base_ind.update(
                {
                    "confirmation_timeframe": confirmation_tf,
                    "confirmation_long": False,
                    "confirmation_short": False,
                    "confirmation_missing": True,
                }
            )

    min_cross_gap = max(0.0, float(params.min_stoch_cross_gap or 0.0))
    long_cross_ok = k_closed - d_closed >= min_cross_gap
    short_cross_ok = d_closed - k_closed >= min_cross_gap
    if bool(params.require_stoch_cross_momentum):
        long_cross_ok = long_cross_ok and k_closed > k_previous
        short_cross_ok = short_cross_ok and k_closed < k_previous
    expected_move_ok = expected_move_pct >= max(
        0.0, float(params.min_expected_move_pct or 0.0)
    )
    base_ind.update(
        {
            "long_stoch_cross_ok": bool(long_cross_ok),
            "short_stoch_cross_ok": bool(short_cross_ok),
            "expected_move_ok": bool(expected_move_ok),
        }
    )

    if regime_lc in blocked and not (
        regime_lc
        in {
            str(r).strip().lower()
            for r in (params.blocked_regime_reclaim_bypass or [])
            if str(r).strip()
        }
        and bool(params.rsi_reclaim_enabled)
    ):
        return EngineResult(
            signal="hold",
            confidence=0.0,
            strength=0.0,
            indicators={**base_ind, "skip_reason": f"regime_blocked:{regime_lc}"},
            invalidation_reason=f"regime_blocked:{regime_lc};regime_{regime_lc}",
        )

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
    rsi_ob = float(params.rsi_overbought)
    short_blocked = {
        str(r).strip().lower() for r in (params.short_blocked_regimes or [])
    }
    short_regime_allowed = regime_lc not in short_blocked
    rsi_reclaim_ok = (
        bool(params.rsi_reclaim_enabled)
        and rsi_recent_min < rsi_os
        and rsi_closed <= float(params.rsi_reclaim_ceiling)
    )
    blocked_reclaim_bypass = {
        str(r).strip().lower()
        for r in (params.blocked_regime_reclaim_bypass or [])
        if str(r).strip()
    }
    long_regime_allowed = regime_lc not in blocked or (
        regime_lc in blocked_reclaim_bypass and rsi_reclaim_ok
    )
    entry_regime_allowed = regime_lc not in blocked
    long_ok = (
        long_regime_allowed
        and
        (rsi_closed < rsi_os or rsi_reclaim_ok)
        and k_closed < stoch_os
        and d_closed < stoch_os
        and long_cross_ok
        and confirmation_long
        and expected_move_ok
    )
    short_ok = (
        allow_short
        and entry_regime_allowed
        and short_regime_allowed
        and rsi_closed > rsi_ob
        and k_closed > stoch_ob
        and d_closed > stoch_ob
        and short_cross_ok
        and confirmation_short
        and expected_move_ok
    )

    if long_ok and short_ok:
        long_score = (rsi_os - rsi_closed) + (stoch_os - k_closed) + (stoch_os - d_closed)
        short_score = (rsi_closed - rsi_ob) + (min(k_closed, d_closed) - stoch_ob)
        if long_score >= short_score:
            short_ok = False
        else:
            long_ok = False

    if long_ok:
        if rsi_closed < rsi_os:
            rsi_clause = f"RSI={rsi_closed:.2f} < {rsi_os}"
        else:
            rsi_clause = (
                f"RSI reclaim={rsi_closed:.2f} <= {float(params.rsi_reclaim_ceiling):.2f} "
                f"after recent RSI min={rsi_recent_min:.2f} < {rsi_os}"
            )
        reason = (
            f"RSI+StochRSI long ({tf}): {rsi_clause}, "
            f"K={k_closed:.2f} < {stoch_os}, D={d_closed:.2f} < {stoch_os}, K >= D"
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
                "rsi_reclaim_ok": bool(rsi_reclaim_ok),
                "stoch_k_oversold_ok": True,
                "stoch_d_oversold_ok": True,
                "stoch_k_gte_d": True,
            },
            invalidation_reason="",
        )

    if short_ok:
        reason = (
            f"RSI+StochRSI short ({tf}): RSI={rsi_closed:.2f} > {rsi_ob}, "
            f"K={k_closed:.2f} > {stoch_ob}, "
            f"D={d_closed:.2f} > {stoch_ob}, D >= K"
        )
        return EngineResult(
            signal="sell",
            confidence=float(params.sell_confidence),
            strength=float(params.sell_strength),
            indicators={
                **base_ind,
                "side_intent": "short",
                "entry_reason": reason,
                "rsi_overbought_ok": True,
                "stoch_k_overbought_ok": True,
                "stoch_d_overbought_ok": True,
                "stoch_d_gte_k": True,
            },
            invalidation_reason="",
        )

    fail_parts = []
    if regime_lc in blocked and not long_regime_allowed:
        fail_parts.append(f"regime_blocked:{regime_lc}")
        fail_parts.append(f"regime_{regime_lc}")
    if allow_short:
        if regime_lc in blocked:
            fail_parts.append(f"entry_regime_{regime_lc}_blocked")
        if not short_regime_allowed:
            fail_parts.append(f"short_regime_{regime_lc}_blocked")
        if rsi_closed <= rsi_ob:
            fail_parts.append(f"rsi_not_overbought({rsi_closed:.2f}<={rsi_ob})")
        if k_closed <= stoch_ob:
            fail_parts.append(f"stoch_k_not_above_{stoch_ob:.0f}(K={k_closed:.2f})")
        if d_closed <= stoch_ob:
            fail_parts.append(f"stoch_d_not_above_{stoch_ob:.0f}(D={d_closed:.2f})")
        if d_closed < k_closed:
            fail_parts.append(f"stoch_d_not_at_or_above_k(D={d_closed:.2f},K={k_closed:.2f})")
    if rsi_closed >= rsi_os and not rsi_reclaim_ok:
        fail_parts.append(f"rsi_not_oversold({rsi_closed:.2f}>={rsi_os})")
    if bool(params.rsi_reclaim_enabled) and rsi_closed >= rsi_os and not rsi_reclaim_ok:
        fail_parts.append(
            f"rsi_reclaim_not_confirmed(current={rsi_closed:.2f},recent_min={rsi_recent_min:.2f},ceiling={float(params.rsi_reclaim_ceiling):.2f})"
        )
    if k_closed >= stoch_os:
        fail_parts.append(f"stoch_k_not_below_{stoch_os:.0f}(K={k_closed:.2f})")
    if d_closed >= stoch_os:
        fail_parts.append(f"stoch_d_not_below_{stoch_os:.0f}(D={d_closed:.2f})")
    if k_closed < d_closed:
        fail_parts.append(f"stoch_k_not_at_or_above_d_for_long(K={k_closed:.2f},D={d_closed:.2f})")
    if not long_cross_ok:
        fail_parts.append("long_stoch_cross_not_strong_or_turning")
    if allow_short and not short_cross_ok:
        fail_parts.append("short_stoch_cross_not_strong_or_turning")
    if bool(params.require_confirmation) and not confirmation_long and not confirmation_short:
        fail_parts.append(f"confirmation_{confirmation_tf or 'timeframe'}_missing_or_neutral")
    if not expected_move_ok:
        fail_parts.append(
            f"expected_move_{expected_move_pct:.2f}_lt_{float(params.min_expected_move_pct):.2f}"
        )
    skip = "; ".join(fail_parts) if fail_parts else "no_entry_conditions"
    return EngineResult(
        signal="hold",
        confidence=0.0,
        strength=0.0,
        indicators={**base_ind, "skip_reason": skip},
        invalidation_reason=skip,
    )
