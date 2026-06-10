"""
SMA Reclaim Bull Flag — shared long-only setup engine for spot and HL perps.

Pipeline: selloff → RSI bullish divergence → SMA200 reclaim → tight flag →
entry near flag low with stop below consolidation / volume node.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

try:
    import pandas_ta as ta
except ImportError:  # pragma: no cover
    ta = None

from strategy.indicators.consolidation_patterns import detect_consolidation
from strategy.indicators.rsi_divergence import detect_bullish_rsi_divergence
from strategy.indicators.session_filter import (
    SessionFilterConfig,
    is_session_allowed,
    session_filter_from_params,
)
from strategy.indicators.sma_stack import compute_sma_stack
from strategy.indicators.stoch_rsi import compute_stoch_rsi, stoch_rsi_bullish
from strategy.indicators.volume_profile import (
    compute_volume_profile,
    nearest_hvn_below,
    volume_profile_to_dict,
)


@dataclass
class EngineParams:
    entry_timeframe: str = "5m"
    execution_timeframe: str = "1m"
    context_timeframes: List[str] = field(default_factory=lambda: ["1d"])
    sma_periods: List[int] = field(default_factory=lambda: [21, 50, 80, 100, 200])
    rsi_period: int = 14
    stoch_rsi_enabled: bool = True
    require_stoch_rsi_confirmation: bool = False
    macd_enabled: bool = False
    require_macd_confirmation: bool = False
    volume_profile_bins: int = 240
    volume_profile_lookback_bars: int = 120
    prefer_vp_stop: bool = True
    divergence_lookback_bars: int = 40
    selloff_lookback_bars: int = 60
    reclaim_max_bars: int = 30
    consolidation_min_bars: int = 8
    max_consolidation_width_pct: float = 0.025
    max_distance_to_sma21_pct: float = 0.008
    ma_compression_max_spread_pct: float = 0.015
    require_sma21_above_sma200: bool = True
    allow_sma21_crossing_above_sma200: bool = True
    sma21_crossing_lookback_bars: int = 8
    sma21_crossing_max_gap_pct: float = 0.003
    min_volume_multiplier: float = 1.1
    volume_window: int = 20
    min_atr_pct: float = 0.003
    min_reward_risk: float = 1.8
    max_stop_pct: float = 0.03
    min_stop_pct: float = 0.002
    max_extension_from_consolidation_pct: float = 0.012
    entry_zone_pct: float = 0.35
    max_risk_per_trade_pct: float = 0.01
    account_equity_hint: float = 10000.0
    buy_confidence: float = 0.76
    buy_strength: float = 0.74
    use_macro_filter: bool = False
    stop_buffer_pct: float = 0.001
    partial_profit_sma_extension_pct: float = 0.05
    blocked_regimes: List[str] = field(default_factory=lambda: ["trending_down", "low_volatility"])


@dataclass
class EngineResult:
    signal: str
    confidence: float
    strength: float
    indicators: Dict[str, Any]
    invalidation_reason: str


def params_from_config(config: Dict[str, Any]) -> EngineParams:
    p = dict(config.get("parameters") or {}) if isinstance(config, dict) else {}
    kw = {k: v for k, v in p.items() if k in EngineParams.__dataclass_fields__}
    base = EngineParams()
    for key, val in kw.items():
        setattr(base, key, val)
    return base


def _df(market_data: Any, key: str) -> Optional[pd.DataFrame]:
    if isinstance(market_data, dict):
        df = market_data.get(key)
        return df if isinstance(df, pd.DataFrame) and not df.empty else None
    return market_data if isinstance(market_data, pd.DataFrame) and not market_data.empty else None


def _atr_pct(df: pd.DataFrame, period: int = 14) -> float:
    if ta is not None and len(df) >= period + 2:
        atr = ta.atr(df["high"], df["low"], df["close"], length=period)
        if atr is not None and not pd.isna(atr.iloc[-1]):
            return float(atr.iloc[-1] / max(float(df["close"].iloc[-1]), 1e-12))
    h, l, c = df["high"].astype(float), df["low"].astype(float), df["close"].astype(float)
    tr = pd.concat([(h - l), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr_val = tr.rolling(period).mean().iloc[-1]
    return float(atr_val / max(float(c.iloc[-1]), 1e-12))


def _volume_ratio(df: pd.DataFrame, window: int) -> float:
    vol = df["volume"].astype(float)
    mean = vol.rolling(window).mean().iloc[-2]
    return float(vol.iloc[-1] / max(mean, 1e-12))


def _macd_bullish(df: pd.DataFrame) -> bool:
    if ta is None or len(df) < 35:
        return True
    close = df["close"].astype(float)
    macd = ta.macd(close)
    if macd is None or macd.empty:
        return False
    macd_col = next((c for c in macd.columns if str(c).startswith("MACD_") and "h" not in str(c).lower()), None)
    sig_col = next((c for c in macd.columns if str(c).startswith("MACDs_")), None)
    if not macd_col or not sig_col:
        return False
    return float(macd[macd_col].iloc[-1]) > float(macd[sig_col].iloc[-1])


def _macro_ok(daily_df: Optional[pd.DataFrame], sma_period: int = 200) -> bool:
    if daily_df is None or len(daily_df) < sma_period + 2:
        return True
    close = daily_df["close"].astype(float)
    sma = close.rolling(sma_period).mean().iloc[-1]
    if pd.isna(sma):
        return True
    return float(close.iloc[-1]) > float(sma)


def _reclaimed_sma200(entry_df: pd.DataFrame, max_bars: int) -> bool:
    close = entry_df["close"].astype(float)
    sma200 = close.rolling(200).mean()
    tail = entry_df.tail(max_bars + 1)
    for i in range(len(tail)):
        idx = tail.index[i]
        if idx not in sma200.index or pd.isna(sma200.loc[idx]):
            continue
        if float(tail.loc[idx, "close"]) > float(sma200.loc[idx]):
            return True
    return False


def _hold_payload(reason: str, extra: Optional[Dict[str, Any]] = None) -> EngineResult:
    payload = {"invalidation_reason": reason, "skip_reason": reason, "entry_reason": ""}
    if extra:
        payload.update(extra)
    return EngineResult("hold", 0.0, 0.0, payload, reason)


def evaluate_sma_reclaim_bull_flag(
    market_data: Dict[str, pd.DataFrame],
    params: EngineParams,
    *,
    session_cfg: Optional[SessionFilterConfig] = None,
    now: Optional[datetime] = None,
    market_regime: str = "unknown",
) -> EngineResult:
    """Run full long setup pipeline; returns hold or buy/long signal metadata."""
    entry_df = _df(market_data, params.entry_timeframe)
    exec_df = _df(market_data, params.execution_timeframe)
    if exec_df is None:
        exec_df = entry_df
    if entry_df is None or len(entry_df) < 210:
        return _hold_payload("insufficient_candles")

    blocked = {str(x).strip().lower() for x in getattr(params, "blocked_regimes", []) or []}
    if blocked and str(market_regime or "").lower() in blocked:
        return _hold_payload("blocked_regime", {"market_regime": market_regime})

    session_cfg = session_cfg or session_filter_from_params({"session_filter": {}})
    allowed, session_reason = is_session_allowed(now, session_cfg)
    if not allowed:
        return _hold_payload(session_reason, {"session_ok": False})

    for ctx_tf in params.context_timeframes or []:
        if params.use_macro_filter and ctx_tf in ("1d", "1D"):
            daily = _df(market_data, ctx_tf)
            if not _macro_ok(daily):
                return _hold_payload("macro_downtrend")

    stack = compute_sma_stack(
        entry_df,
        periods=params.sma_periods,
        selloff_lookback=params.selloff_lookback_bars,
        crossing_lookback_bars=params.sma21_crossing_lookback_bars,
        crossing_max_gap_pct=params.sma21_crossing_max_gap_pct,
    )
    if stack is None:
        return _hold_payload("sma_stack_not_ready")

    if not stack.price_below_sma200_recent:
        return _hold_payload("no_recent_selloff", {"sma_stack": stack.smas})

    div = detect_bullish_rsi_divergence(
        entry_df,
        rsi_period=params.rsi_period,
        lookback_bars=params.divergence_lookback_bars,
    )
    if not div.detected:
        return _hold_payload("no_bullish_rsi_divergence")

    sma200 = stack.smas.get(200, 0)
    price = stack.price
    if price <= sma200:
        return _hold_payload(
            "below_sma200_wait_reclaim",
            {"price": price, "sma200": sma200, "divergence_detected": True},
        )

    if not _reclaimed_sma200(entry_df, params.reclaim_max_bars):
        return _hold_payload("sma200_not_reclaimed")

    cons = detect_consolidation(
        entry_df,
        min_bars=params.consolidation_min_bars,
        max_width_pct=params.max_consolidation_width_pct,
    )
    if not cons.detected:
        return _hold_payload("no_tight_consolidation")

    if stack.distance_to_sma21_pct > params.max_distance_to_sma21_pct:
        return _hold_payload("ma_structure_weak", {"distance_to_sma21_pct": stack.distance_to_sma21_pct})
    if stack.compression_spread_pct > params.ma_compression_max_spread_pct:
        return _hold_payload("ma_structure_weak", {"compression_spread_pct": stack.compression_spread_pct})
    sma21_structure_ok = stack.sma21_above_sma200 or (
        params.allow_sma21_crossing_above_sma200 and stack.sma21_crossing_above_sma200
    )
    if params.require_sma21_above_sma200 and not sma21_structure_ok:
        return _hold_payload(
            "ma_structure_weak",
            {
                "sma21_above_sma200": False,
                "sma21_crossing_above_sma200": stack.sma21_crossing_above_sma200,
            },
        )

    vol_ratio = _volume_ratio(entry_df, params.volume_window)
    atr_pct = _atr_pct(entry_df)
    if vol_ratio < params.min_volume_multiplier and atr_pct < params.min_atr_pct:
        return _hold_payload(
            "low_volume_or_volatility",
            {"volume_ratio": vol_ratio, "atr_pct": atr_pct},
        )

    if params.macd_enabled and params.require_macd_confirmation and not _macd_bullish(entry_df):
        return _hold_payload("macd_not_bullish")

    if params.stoch_rsi_enabled:
        k_s, d_s = compute_stoch_rsi(entry_df["close"].astype(float))
        if params.require_stoch_rsi_confirmation and k_s is not None:
            k_val = float(k_s.iloc[-1])
            d_val = float(d_s.iloc[-1]) if d_s is not None else 0.0
            if not stoch_rsi_bullish(k_val, d_val):
                return _hold_payload("stoch_rsi_not_bullish")

    range_size = cons.consolidation_high - cons.consolidation_low
    zone_upper = cons.consolidation_low + range_size * params.entry_zone_pct
    if price > zone_upper:
        return _hold_payload(
            "price_above_entry_zone",
            {"price": price, "zone_upper": zone_upper, "consolidation_low": cons.consolidation_low},
        )
    if price < cons.consolidation_low:
        return _hold_payload("price_below_flag_low")

    ext_ref = float(exec_df["close"].iloc[-1]) if exec_df is not None else price
    if range_size > 0:
        extension = (ext_ref - cons.consolidation_high) / range_size
        if extension > params.max_extension_from_consolidation_pct / max(params.max_consolidation_width_pct, 1e-6):
            return _hold_payload("price_extended_from_consolidation")

    stop = cons.consolidation_low * (1.0 - params.stop_buffer_pct)
    vp_meta: Dict[str, Any] = {}
    vp_stop_used = False
    if params.prefer_vp_stop:
        profile = compute_volume_profile(
            entry_df,
            num_bins=params.volume_profile_bins,
            lookback_bars=params.volume_profile_lookback_bars,
        )
        hvn = nearest_hvn_below(profile, cons.consolidation_low) if profile else None
        if hvn is not None and hvn < cons.consolidation_low:
            stop = min(stop, hvn * (1.0 - params.stop_buffer_pct))
            vp_stop_used = True
        vp_meta = volume_profile_to_dict(profile)

    target = price + max(cons.pole_height, range_size * 1.0)
    risk = price - stop
    reward = target - price
    if risk <= 0 or reward <= 0:
        return _hold_payload("invalid_stop_or_target")
    stop_pct = risk / price
    target_pct = reward / price
    rr = reward / risk
    if stop_pct > params.max_stop_pct or stop_pct < params.min_stop_pct or rr < params.min_reward_risk:
        return _hold_payload(
            "risk_reward_or_stop_pct_fail",
            {"stop_pct": stop_pct, "reward_risk": rr},
        )

    equity = float(params.account_equity_hint or 10000.0)
    position_size_hint = (equity * params.max_risk_per_trade_pct) / risk

    swing_high = float(entry_df["high"].iloc[-params.consolidation_min_bars - 20 : -1].max())

    score = 0.0
    score += 0.2 if div.detected else 0
    score += 0.15 if cons.pattern_type == "bull_flag" else 0.1
    score += 0.15 if stack.sma21_above_sma200 else 0.05
    score += min(0.2, vol_ratio / 3.0)
    score += 0.15 if stack.compression_spread_pct <= params.ma_compression_max_spread_pct * 0.7 else 0.05
    score += 0.15 if price <= zone_upper else 0.05
    confidence = min(0.95, params.buy_confidence + score * 0.12)
    strength = min(0.95, params.buy_strength + score * 0.10)

    entry_reason = (
        f"SMA reclaim bull flag: selloff + bullish RSI divergence; reclaimed SMA200; "
        f"{cons.pattern_type} consolidation (width {cons.width_pct:.2%}); entry near flag low "
        f"with stop below {cons.consolidation_low:.6f}; measured move target {target:.6f} (RR {rr:.2f})"
    )

    indicators: Dict[str, Any] = {
        "entry_price": price,
        "stop_hint": stop,
        "target_hint": target,
        "target_pct": target_pct,
        "reward_risk": rr,
        "stop_pct": stop_pct,
        "entry_reason": entry_reason,
        "invalidation_reason": "none",
        "position_size_hint": position_size_hint,
        "pattern_type": cons.pattern_type,
        "divergence_detected": True,
        "sma200_reclaimed": True,
        "sma21_above_sma200": stack.sma21_above_sma200,
        "sma21_crossed_above_sma200_recent": stack.sma21_crossed_above_sma200_recent,
        "sma21_crossing_above_sma200": stack.sma21_crossing_above_sma200,
        "consolidation_low": cons.consolidation_low,
        "consolidation_high": cons.consolidation_high,
        "measured_move": cons.pole_height,
        "vp_stop_used": vp_stop_used,
        "session_ok": True,
        "volume_ratio": vol_ratio,
        "atr_pct": atr_pct,
        "breakeven_trigger_swing_high": swing_high,
        "partial_profit_sma_extension_pct": params.partial_profit_sma_extension_pct,
        "setup": "sma_reclaim_bull_flag",
        **vp_meta,
    }

    return EngineResult("buy", confidence, strength, indicators, "none")
