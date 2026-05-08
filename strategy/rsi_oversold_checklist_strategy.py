"""
RSI Oversold Checklist strategy.

Relaxed checklist (15m execution + 1h bias):
- Core BUY gates: RSI reclaim, exec RSI band, optional 1h RSI bias, optional price > EMA20,
  optional macro EMA50>EMA200, optional volume confirmation.
- Support proximity / bullish divergence boost confidence/strength when present;
  they are not hard blockers unless explicitly required via config.
- Optional ``dynamic_reclaim_by_regime``: YAML can use detector names
  (``trending_up``, …) or groups: ``trending`` → up/down, ``volatile`` →
  high_volatility/breakout, ``ranging`` → low_volatility/reversal_zone.
- Optional RSI slope (exec RSI now vs N bars ago): soft momentum boost, or
  hard gate if ``require_rsi_slope`` is true.
- ``require_price_above_ema20`` (default True): when False, mean-reversion entries
  are allowed while price is still under the fast EMA (common on first reclaim).
- ``reclaim_cross_bars`` (default 1): RSI reclaim can be satisfied over the last N
  closed bars (min of prior N-1 <= reclaim, current > reclaim) instead of exactly
  the previous bar only.
- ``require_rsi_reclaim`` (default True): when False, uses ``simple_oversold_*`` —
  within the last ``simple_oversold_lookback_bars`` bars RSI must have touched
  ``reclaim_eff`` (regime-aware), and the current RSI must lie in
  ``[exec_rsi_min, simple_oversold_rsi_max]``. Avoids the rare exact cross while
  still requiring a recent dip into the oversold zone.
- ``simple_touch_rsi_max`` (optional): if set, recent touch uses
  ``min(RSI) <= simple_touch_rsi_max`` instead of ``<= reclaim_eff`` (higher = looser).
- ``require_simple_recent_touch`` (default True): when False with ``require_rsi_reclaim``
  false, only the current RSI band is required (no recent-dip rule).
"""

from typing import Dict, Any, Optional, Tuple
import logging

import pandas as pd
import pandas_ta as ta

from strategy.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


class RSIOversoldChecklistStrategy(BaseStrategy):
    """RSI checklist strategy for higher-quality long entries."""

    STRATEGY_NAME = "RSI Oversold Checklist"

    def __init__(
        self,
        config: Dict[str, Any],
        exchange: Any,
        database: Any,
        redis_client=None,
        exchange_name=None,
    ):
        super().__init__(config, exchange, database, redis_client)
        self.exchange_name = exchange_name or "binance"
        self.logger = logging.getLogger(__name__)

        params = config.get("parameters", {})
        self.rsi_period = int(params.get("rsi_period", 14))
        self.rsi_reclaim_level = float(params.get("rsi_reclaim_level", 30.0))
        self.exec_rsi_min = float(params.get("exec_rsi_min", 30.0))
        self.exec_rsi_max = float(params.get("exec_rsi_max", 60.0))
        self.trend_rsi_min = float(params.get("trend_rsi_min", 40.0))

        self.ema_fast_period = int(params.get("ema_fast_period", 20))
        self.ema_mid_period = int(params.get("ema_mid_period", 50))
        self.ema_slow_period = int(params.get("ema_slow_period", 200))

        self.volume_window = int(params.get("volume_window", 20))
        # Default aligned with 15m crypto relaxed band (~1.05–1.10), not legacy 1.2
        self.volume_multiplier = float(params.get("volume_multiplier", 1.07))

        self.support_lookback = int(params.get("support_lookback", 30))
        self.support_tolerance_pct = float(params.get("support_tolerance_pct", 0.012))
        self.divergence_lookback = int(params.get("divergence_lookback", 14))

        self.require_volume_confirmation = bool(params.get("require_volume_confirmation", True))
        self.require_support_proximity = bool(params.get("require_support_proximity", False))
        self.require_bullish_divergence = bool(params.get("require_bullish_divergence", False))
        self.require_multi_tf_rsi = bool(params.get("require_multi_tf_rsi", True))
        self.require_macro_trend_filter = bool(params.get("require_macro_trend_filter", True))
        self.require_price_above_ema20 = bool(params.get("require_price_above_ema20", True))
        self.reclaim_cross_bars = max(1, int(params.get("reclaim_cross_bars", 1)))
        self.require_rsi_reclaim = bool(params.get("require_rsi_reclaim", True))
        self.simple_oversold_rsi_max = float(params.get("simple_oversold_rsi_max", 42.0))
        self.simple_oversold_lookback_bars = max(3, int(params.get("simple_oversold_lookback_bars", 12)))
        _stm = params.get("simple_touch_rsi_max", None)
        self.simple_touch_rsi_max: Optional[float] = float(_stm) if _stm is not None else None
        self.require_simple_recent_touch = bool(params.get("require_simple_recent_touch", True))

        self.buy_confidence = float(params.get("buy_confidence", 0.97))
        self.buy_strength = float(params.get("buy_strength", 0.92))

        # Optional: per-regime reclaim threshold (deeper in trends, shallower in ranges)
        self.dynamic_reclaim_by_regime = bool(params.get("dynamic_reclaim_by_regime", False))
        raw_map = params.get("reclaim_by_regime") or {}
        self.reclaim_by_regime: Dict[str, float] = {}
        if isinstance(raw_map, dict):
            for k, v in raw_map.items():
                try:
                    self.reclaim_by_regime[str(k).lower().strip()] = float(v)
                except (TypeError, ValueError):
                    continue

        self.support_confidence_boost = float(params.get("support_confidence_boost", 0.02))
        self.support_strength_boost = float(params.get("support_strength_boost", 0.02))
        self.divergence_confidence_boost = float(params.get("divergence_confidence_boost", 0.03))
        self.divergence_strength_boost = float(params.get("divergence_strength_boost", 0.03))
        self.max_confidence = float(params.get("max_confidence_cap", 0.99))
        self.max_strength = float(params.get("max_strength_cap", 0.99))

        self.rsi_slope_bars_ago = max(1, int(params.get("rsi_slope_bars_ago", 2)))
        self.require_rsi_slope = bool(params.get("require_rsi_slope", False))
        self.rsi_slope_confidence_boost = float(params.get("rsi_slope_confidence_boost", 0.015))
        self.rsi_slope_strength_boost = float(params.get("rsi_slope_strength_boost", 0.015))

        self._current_ohlcv = None

    def _effective_reclaim_level(self, regime: str) -> Tuple[float, str]:
        """Resolve reclaim from exact regime key, then grouped aliases, then default."""
        default = float(self.rsi_reclaim_level)
        if not self.dynamic_reclaim_by_regime or not self.reclaim_by_regime:
            return default, "default"
        m = self.reclaim_by_regime
        r = (regime or "").lower().strip()
        if r and r in m:
            return float(m[r]), f"exact:{r}"
        if "trending" in m and r in ("trending_up", "trending_down"):
            return float(m["trending"]), "group:trending"
        if "volatile" in m and r in ("high_volatility", "breakout"):
            return float(m["volatile"]), "group:volatile"
        if "ranging" in m and r in ("low_volatility", "reversal_zone"):
            return float(m["ranging"]), "group:ranging"
        return default, "default"

    async def initialize(self, pair: str) -> None:
        self.state.pair = pair
        self.state.last_signal = "hold"
        self.state.indicators = {}
        self.logger.info(f"Initialized RSI Oversold Checklist strategy for {pair}")

    async def update(self, ohlcv: pd.DataFrame) -> None:
        self._current_ohlcv = ohlcv
        self.state.last_signal_time = pd.Timestamp.utcnow().to_pydatetime()

    def _detect_bullish_divergence(self, exec_df: pd.DataFrame, rsi_series: pd.Series) -> bool:
        lookback = max(10, self.divergence_lookback)
        if len(exec_df) < lookback + 2 or len(rsi_series) < lookback + 2:
            return False
        closes = exec_df["close"].tail(lookback).astype(float)
        rsi_tail = rsi_series.tail(lookback).astype(float)
        split = lookback // 2
        if split < 4:
            return False
        price_old, price_new = closes.iloc[:split], closes.iloc[split:]
        rsi_old, rsi_new = rsi_tail.iloc[:split], rsi_tail.iloc[split:]
        return bool((price_new.min() < price_old.min()) and (rsi_new.min() > rsi_old.min()))

    async def generate_signal(
        self,
        market_data,
        indicators_cache: Optional[dict] = None,
        pair: Optional[str] = None,
        timeframe: Optional[str] = None,
        exchange_adapter=None,
    ) -> Tuple[str, float, float]:
        try:
            if isinstance(market_data, dict):
                exec_df = market_data.get("15m")
                trend_df = market_data.get("1h")
                if exec_df is None:
                    exec_df = market_data.get("1h")
                if trend_df is None:
                    trend_df = market_data.get("15m")
            else:
                exec_df = market_data
                trend_df = market_data

            min_len = max(
                self.ema_slow_period + 5,
                self.volume_window + 5,
                self.support_lookback + 5,
                self.rsi_period + 5,
                self.rsi_slope_bars_ago + 5,
            )
            if exec_df is None or trend_df is None or len(exec_df) < min_len or len(trend_df) < self.rsi_period + 5:
                return "hold", 0.0, 0.0

            required_cols = {"open", "high", "low", "close", "volume"}
            if not required_cols.issubset(exec_df.columns) or not required_cols.issubset(trend_df.columns):
                return "hold", 0.0, 0.0

            exec_close = exec_df["close"].astype(float)
            trend_close = trend_df["close"].astype(float)
            price_now = float(exec_close.iloc[-1])

            exec_rsi = ta.rsi(exec_close, length=self.rsi_period)
            trend_rsi = ta.rsi(trend_close, length=self.rsi_period)
            slope_idx = -(self.rsi_slope_bars_ago + 1)
            if (
                exec_rsi is None
                or trend_rsi is None
                or pd.isna(exec_rsi.iloc[-1])
                or pd.isna(exec_rsi.iloc[-2])
                or pd.isna(trend_rsi.iloc[-1])
            ):
                return "hold", 0.0, 0.0
            exec_rsi_now = float(exec_rsi.iloc[-1])
            exec_rsi_prev = float(exec_rsi.iloc[-2])
            trend_rsi_now = float(trend_rsi.iloc[-1])
            slope_ref_raw = exec_rsi.iloc[slope_idx]
            rsi_slope_ref_valid = not pd.isna(slope_ref_raw)
            exec_rsi_slope_ref = float(slope_ref_raw) if rsi_slope_ref_valid else float("nan")
            rsi_slope_ok = rsi_slope_ref_valid and exec_rsi_now > exec_rsi_slope_ref

            ema20 = ta.ema(exec_close, length=self.ema_fast_period)
            ema50 = ta.ema(exec_close, length=self.ema_mid_period)
            ema200 = ta.ema(exec_close, length=self.ema_slow_period)
            if ema20 is None or ema50 is None or ema200 is None or pd.isna(ema20.iloc[-1]) or pd.isna(ema50.iloc[-1]) or pd.isna(ema200.iloc[-1]):
                return "hold", 0.0, 0.0
            ema20_now = float(ema20.iloc[-1])
            ema50_now = float(ema50.iloc[-1])
            ema200_now = float(ema200.iloc[-1])

            avg_volume = float(exec_df["volume"].rolling(self.volume_window).mean().iloc[-1] or 0.0)
            cur_volume = float(exec_df["volume"].iloc[-1] or 0.0)
            volume_ok = avg_volume > 0 and cur_volume >= (avg_volume * self.volume_multiplier)

            regime = str(getattr(self.state, "market_regime", "") or "").lower().strip()
            reclaim_eff, reclaim_source = self._effective_reclaim_level(regime)

            rolling_support = float(exec_df["low"].rolling(self.support_lookback).min().iloc[-1] or 0.0)
            near_support = rolling_support > 0 and price_now <= rolling_support * (1.0 + self.support_tolerance_pct)
            support_bounce = near_support and exec_rsi_now > reclaim_eff

            bullish_divergence = self._detect_bullish_divergence(exec_df, exec_rsi)

            rsi_trigger_ok = False
            rsi_trigger_mode = "none"
            if self.require_rsi_reclaim:
                if self.reclaim_cross_bars <= 1:
                    rsi_trigger_ok = exec_rsi_prev <= reclaim_eff and exec_rsi_now > reclaim_eff
                    rsi_trigger_mode = "reclaim_cross_1"
                else:
                    span = min(self.reclaim_cross_bars, len(exec_rsi) - 1)
                    tail = exec_rsi.iloc[-(span + 1) :].dropna()
                    if len(tail) >= 2:
                        rsi_now_tail = float(tail.iloc[-1])
                        prior_min = float(tail.iloc[:-1].min())
                        rsi_trigger_ok = rsi_now_tail > reclaim_eff and prior_min <= reclaim_eff
                    rsi_trigger_mode = f"reclaim_cross_{self.reclaim_cross_bars}"
            else:
                # Simple mode: optional recent touch + current RSI in recovery band
                hi = min(self.simple_oversold_rsi_max, self.exec_rsi_max)
                in_band = self.exec_rsi_min <= exec_rsi_now <= hi
                if not self.require_simple_recent_touch:
                    rsi_trigger_ok = bool(in_band)
                    rsi_trigger_mode = "simple_band_only"
                else:
                    lb = min(self.simple_oversold_lookback_bars, len(exec_rsi))
                    recent = exec_rsi.iloc[-lb:].dropna()
                    touch_level = (
                        float(self.simple_touch_rsi_max)
                        if self.simple_touch_rsi_max is not None
                        else float(reclaim_eff)
                    )
                    if len(recent) >= 2 and not pd.isna(recent.min()):
                        touched = float(recent.min()) <= touch_level
                        rsi_trigger_ok = bool(touched and in_band)
                    rsi_trigger_mode = "simple_oversold_lookback"

            rsi_reclaimed = rsi_trigger_ok  # backward compat for logs / indicators

            exec_rsi_ok = self.exec_rsi_min <= exec_rsi_now <= self.exec_rsi_max
            trend_rsi_ok = (trend_rsi_now > self.trend_rsi_min) if self.require_multi_tf_rsi else True
            trend_filter_ok = (price_now > ema20_now) if self.require_price_above_ema20 else True
            macro_trend_ok = (ema50_now > ema200_now) if self.require_macro_trend_filter else True
            volume_gate_ok = volume_ok if self.require_volume_confirmation else True
            rsi_slope_gate_ok = rsi_slope_ok if self.require_rsi_slope else True
            # Hard gates only when explicitly required; otherwise soft (boost only).
            support_gate_ok = support_bounce if self.require_support_proximity else True
            divergence_gate_ok = bullish_divergence if self.require_bullish_divergence else True

            checklist_buy = all(
                [
                    rsi_trigger_ok,
                    exec_rsi_ok,
                    trend_rsi_ok,
                    trend_filter_ok,
                    macro_trend_ok,
                    volume_gate_ok,
                    rsi_slope_gate_ok,
                    support_gate_ok,
                    divergence_gate_ok,
                ]
            )

            signal = "buy" if checklist_buy else "hold"
            confidence = 0.0
            strength = 0.0
            if checklist_buy:
                confidence = self.buy_confidence
                strength = self.buy_strength
                if not self.require_support_proximity and support_bounce:
                    confidence += self.support_confidence_boost
                    strength += self.support_strength_boost
                if not self.require_bullish_divergence and bullish_divergence:
                    confidence += self.divergence_confidence_boost
                    strength += self.divergence_strength_boost
                if not self.require_rsi_slope and rsi_slope_ok:
                    confidence += self.rsi_slope_confidence_boost
                    strength += self.rsi_slope_strength_boost
                confidence = min(self.max_confidence, confidence)
                strength = min(self.max_strength, strength)

            self.state.indicators.update(
                {
                    "market_regime_used": regime,
                    "rsi_reclaim_effective": reclaim_eff,
                    "rsi_reclaim_source": reclaim_source,
                    "exec_rsi_slope_ref": exec_rsi_slope_ref if rsi_slope_ref_valid else None,
                    "rsi_slope_ok": rsi_slope_ok,
                    "exec_rsi_15m": exec_rsi_now,
                    "exec_rsi_15m_prev": exec_rsi_prev,
                    "trend_rsi_1h": trend_rsi_now,
                    "rsi_reclaimed_30": rsi_reclaimed,
                    "rsi_trigger_ok": rsi_trigger_ok,
                    "rsi_trigger_mode": rsi_trigger_mode,
                    "require_rsi_reclaim": self.require_rsi_reclaim,
                    "require_simple_recent_touch": self.require_simple_recent_touch,
                    "simple_touch_rsi_max": self.simple_touch_rsi_max,
                    "ema20": ema20_now,
                    "ema50": ema50_now,
                    "ema200": ema200_now,
                    "price_above_ema20": trend_filter_ok,
                    "macro_trend_ok": macro_trend_ok,
                    "volume_ratio": (cur_volume / avg_volume) if avg_volume > 0 else 0.0,
                    "volume_ok": volume_ok,
                    "rolling_support": rolling_support,
                    "support_bounce": support_bounce,
                    "bullish_divergence": bullish_divergence,
                    "support_soft_boost_applied": bool(
                        checklist_buy and not self.require_support_proximity and support_bounce
                    ),
                    "divergence_soft_boost_applied": bool(
                        checklist_buy and not self.require_bullish_divergence and bullish_divergence
                    ),
                    "rsi_slope_soft_boost_applied": bool(
                        checklist_buy and not self.require_rsi_slope and rsi_slope_ok
                    ),
                    "checklist_buy": checklist_buy,
                    "require_price_above_ema20": self.require_price_above_ema20,
                    "reclaim_cross_bars": self.reclaim_cross_bars,
                }
            )
            self.state.last_signal = signal

            reason = "rsi_checklist_buy" if checklist_buy else "checklist_not_satisfied"
            vol_ratio = (cur_volume / avg_volume) if avg_volume > 0 else 0.0
            self.logger.info(
                "[RSIOversoldChecklistStrategy] %s %s conf=%.3f str=%.3f reason=%s | "
                "regime=%s reclaim=%.2f(%s) rsi15=%.2f>%s slope_ok=%s rsi_trigger=%s | "
                "vol_mult=%.2f vol_ratio=%.3f vol_ok=%s ema_trend_ok=%s",
                pair or self.state.pair,
                signal.upper(),
                confidence,
                strength,
                reason,
                regime or "unknown",
                reclaim_eff,
                reclaim_source,
                exec_rsi_now,
                f"{exec_rsi_slope_ref:.2f}" if rsi_slope_ref_valid else "na",
                rsi_slope_ok,
                rsi_trigger_ok,
                self.volume_multiplier,
                vol_ratio,
                volume_ok,
                trend_filter_ok and macro_trend_ok,
            )
            return signal, float(confidence), float(strength)
        except Exception as e:
            self.logger.error(f"[RSIOversoldChecklistStrategy] Error generating signal: {e}")
            return "hold", 0.0, 0.0

    async def calculate_position_size(self, signal_type: str) -> float:
        if signal_type != "buy":
            return 0.0
        return 0.0

    async def should_exit(self) -> bool:
        return False

