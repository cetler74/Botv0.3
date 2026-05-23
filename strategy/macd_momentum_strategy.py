"""
MACD Momentum strategy tuned for crypto intraday execution.

Single long entry path (all gates must pass):
  1. 15m + 1h OHLCV available with sufficient history.
  2. Mandatory (15m): two consecutive green rising MACD histogram bars.
  3. Mandatory (15m): at least one of the last N closed RSI values < rsi_max_for_buy.
  4. 1h MACD line > 1h signal (long_macd_bias_timeframe: trend).
  5. 15m close > EMA(ema_filter_period).
  6. 15m volume >= dynamic volume_multiplier × rolling mean (when enabled).

Exits remain orchestrator-managed (trailing stop / profit protection / global risk).
"""

from typing import Dict, Any, List, Optional, Tuple
import logging

import pandas as pd
import numpy as np
import pandas_ta as ta

from strategy.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


class MACDMomentumStrategy(BaseStrategy):
    """Momentum strategy based on multi-timeframe MACD confirmation."""

    STRATEGY_NAME = "MACD Momentum"

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

        # Execution timeframe MACD (faster than classic 12/26/9).
        self.exec_fast = int(params.get("execution_fast_period", 8))
        self.exec_slow = int(params.get("execution_slow_period", 21))
        self.exec_signal = int(params.get("execution_signal_period", 5))

        # Trend timeframe MACD (slower, more stable bias filter).
        self.trend_fast = int(params.get("trend_fast_period", 12))
        self.trend_slow = int(params.get("trend_slow_period", 26))
        self.trend_signal = int(params.get("trend_signal_period", 9))

        self.ema_filter_period = int(params.get("ema_filter_period", 20))
        self.volume_window = int(params.get("volume_window", 20))
        self.volume_multiplier = float(params.get("volume_multiplier", 1.2))
        self.histogram_min_delta = float(params.get("histogram_min_delta", 0.0))
        self.min_confidence_score = float(params.get("min_confidence_score", 0.55))
        self.min_hist_rising_bars = int(params.get("min_hist_rising_bars", 2))
        self.min_hist_falling_bars = int(params.get("min_hist_falling_bars", 2))
        self.require_hist_positive_for_long = bool(params.get("require_hist_positive_for_long", True))
        self.require_hist_negative_for_short = bool(params.get("require_hist_negative_for_short", True))
        self.use_volume_confirmation = bool(params.get("use_volume_confirmation", True))
        self.skip_sideways_regime = bool(params.get("skip_sideways_regime", True))
        ar = params.get("allowed_regimes")
        if ar is None:
            ar = []
        self.allowed_regimes = {str(x).strip().lower() for x in ar if str(x).strip()}
        br = params.get("blocked_regimes")
        if br is None:
            br = []
        self.blocked_regimes = {str(x).strip().lower() for x in br if str(x).strip()}
        ae = params.get("allowed_exchanges")
        if ae is None:
            ae = []
        self.allowed_exchanges = {str(x).strip().lower() for x in ae if str(x).strip()}
        self.dynamic_volume_enabled = bool(params.get("dynamic_volume_enabled", True))
        self.dynamic_volume_floor = float(params.get("dynamic_volume_floor", 1.05))
        self.dynamic_volume_ceiling = float(params.get("dynamic_volume_ceiling", 1.15))
        self.enable_divergence_detection = bool(params.get("enable_divergence_detection", True))
        self.divergence_lookback = int(params.get("divergence_lookback", 12))
        self.divergence_confidence_boost = float(params.get("divergence_confidence_boost", 0.05))
        self.rsi_period = int(params.get("rsi_period", 14))
        # Mandatory long gates (15m): 2 green rising histogram bars.
        self.require_two_green_hist_bars = bool(params.get("require_two_green_hist_bars", True))
        # ANY-of-last-N: at least one closed 15m RSI in this window must be < rsi_max_for_buy.
        self.rsi_last_n_below = int(params.get("rsi_last_n_below", 6))
        self.rsi_max_for_buy = float(
            params.get("rsi_max_for_buy", params.get("macd_green_rsi_buy_threshold", 30.0))
        )
        # Dashboard / analytics aliases (same gate as rsi_last_n_below / rsi_max_for_buy).
        self.macd_green_rsi_lookback = int(
            params.get("macd_green_rsi_lookback", self.rsi_last_n_below)
        )
        self.macd_green_rsi_buy_threshold = float(
            params.get("macd_green_rsi_buy_threshold", self.rsi_max_for_buy)
        )
        self.buy_strength_floor = float(params.get("buy_strength_floor", 0.30))
        self.continuation_long_enabled = bool(params.get("continuation_long_enabled", False))
        continuation_regimes = params.get("continuation_allowed_regimes", ["trending_up", "breakout"])
        self.continuation_allowed_regimes = {
            str(x).strip().lower() for x in continuation_regimes if str(x).strip()
        }
        self.continuation_min_rsi = float(params.get("continuation_min_rsi", 45.0))
        self.continuation_max_rsi = float(params.get("continuation_max_rsi", 68.0))
        self.continuation_min_trend_rsi = float(params.get("continuation_min_trend_rsi", 45.0))
        self.continuation_require_exec_macd_bias = bool(
            params.get("continuation_require_exec_macd_bias", True)
        )
        self.continuation_confidence = float(params.get("continuation_confidence", self.min_confidence_score))
        self.continuation_strength_floor = float(params.get("continuation_strength_floor", self.buy_strength_floor))
        self.continuation_volume_multiplier = float(
            params.get("continuation_volume_multiplier", self.volume_multiplier)
        )
        self.continuation_requires_volume_ok = bool(
            params.get("continuation_requires_volume_ok", False)
        )
        self.continuation_confirmation_pct = float(
            params.get("continuation_confirmation_pct", 0.0025)
        )
        self.continuation_confirm_lookback = int(
            params.get("continuation_confirm_lookback", 2)
        )
        # Long bias: "trend" => 1h MACD > 1h signal (canonical single-path design).
        self.long_macd_bias_timeframe = str(params.get("long_macd_bias_timeframe", "trend")).lower()
        self.short_macd_bias_timeframe = str(params.get("short_macd_bias_timeframe", "trend")).lower()

        self._current_ohlcv = None

    async def initialize(self, pair: str) -> None:
        self.state.pair = pair
        self.state.last_signal = "hold"
        self.state.indicators = {}
        self.logger.info(f"Initialized MACD Momentum strategy for {pair}")

    async def update(self, ohlcv: pd.DataFrame) -> None:
        self._current_ohlcv = ohlcv
        self.state.last_signal_time = pd.Timestamp.utcnow().to_pydatetime()

    @staticmethod
    def _extract_macd_components(macd_df: pd.DataFrame) -> Tuple[Optional[pd.Series], Optional[pd.Series], Optional[pd.Series]]:
        if macd_df is None or macd_df.empty:
            return None, None, None
        macd_col = next((c for c in macd_df.columns if str(c).startswith("MACD_")), None)
        signal_col = next((c for c in macd_df.columns if str(c).startswith("MACDs_")), None)
        hist_col = next((c for c in macd_df.columns if str(c).startswith("MACDh_")), None)
        if not macd_col or not signal_col or not hist_col:
            return None, None, None
        return macd_df[macd_col], macd_df[signal_col], macd_df[hist_col]

    def _resolve_volume_multiplier(self, exec_df: pd.DataFrame, regime: str) -> float:
        """Dynamic volume baseline for intraday crypto execution."""
        base = self.volume_multiplier
        if not self.dynamic_volume_enabled:
            return base

        # Volatility-aware adjustment: in higher realized volatility, requiring
        # too-large volume spikes misses continuation entries.
        realized_vol = float(exec_df["close"].pct_change().rolling(14).std().iloc[-1] or 0.0)
        adjusted = base
        if realized_vol >= 0.02:
            adjusted -= 0.05
        elif realized_vol <= 0.006:
            adjusted += 0.03

        regime = str(regime or "").lower()
        if regime in ("trending_up", "breakout", "high_volatility"):
            adjusted -= 0.03
        elif regime in ("low_volatility", "sideways"):
            adjusted += 0.02

        return float(min(self.dynamic_volume_ceiling, max(self.dynamic_volume_floor, adjusted)))

    def _detect_divergence(self, exec_df: pd.DataFrame, exec_hist: pd.Series) -> Tuple[bool, bool]:
        """Detect simple bullish/bearish divergence over recent lookback."""
        lookback = max(8, self.divergence_lookback)
        if len(exec_df) < lookback + 2 or len(exec_hist) < lookback + 2:
            return False, False

        closes = exec_df["close"].tail(lookback).astype(float)
        hist = exec_hist.tail(lookback).astype(float)
        split = lookback // 2
        if split < 3:
            return False, False

        price_old = closes.iloc[:split]
        price_new = closes.iloc[split:]
        hist_old = hist.iloc[:split]
        hist_new = hist.iloc[split:]

        bullish = (price_new.min() < price_old.min()) and (hist_new.min() > hist_old.min())
        bearish = (price_new.max() > price_old.max()) and (hist_new.max() < hist_old.max())
        return bool(bullish), bool(bearish)

    def _regime_allowed(self, current_regime: str) -> bool:
        regime = str(current_regime or "unknown").strip().lower()
        if self.allowed_regimes:
            return regime in self.allowed_regimes
        if self.skip_sideways_regime and regime == "sideways":
            return False
        return regime not in self.blocked_regimes

    def _exchange_allowed(self) -> bool:
        if not self.allowed_exchanges:
            return True
        ex = str(self.exchange_name or "").strip().lower()
        return ex in self.allowed_exchanges

    async def generate_signal(
        self,
        market_data,
        indicators_cache: Optional[dict] = None,
        pair: Optional[str] = None,
        timeframe: Optional[str] = None,
        exchange_adapter=None,
    ) -> Tuple[str, float, float]:
        try:
            self._apply_regime_overrides(getattr(self.state, "market_regime", None))
            current_regime = str(getattr(self.state, "market_regime", "unknown") or "unknown").lower()
            if not self._exchange_allowed():
                self.logger.info(
                    "[MACDMomentumStrategy] %s HOLD: exchange=%s not in allowed_exchanges",
                    pair or self.state.pair,
                    self.exchange_name,
                )
                return "hold", 0.0, 0.0
            if not self._regime_allowed(current_regime):
                self.logger.info(
                    "[MACDMomentumStrategy] %s HOLD conf=0.00 strength=0.00 reason=regime_blocked (%s)",
                    pair or self.state.pair,
                    current_regime,
                )
                return "hold", 0.0, 0.0

            if isinstance(market_data, dict):
                exec_df = market_data.get("15m")
                if exec_df is None:
                    exec_df = market_data.get("1h")
                trend_df = market_data.get("1h")
                if trend_df is None:
                    trend_df = market_data.get("15m")
            else:
                exec_df = market_data
                trend_df = market_data

            min_exec = max(self.exec_slow + self.exec_signal + 5, self.ema_filter_period + 2, self.volume_window + 2)
            min_trend = self.trend_slow + self.trend_signal + 5
            if exec_df is None or trend_df is None or len(exec_df) < min_exec or len(trend_df) < min_trend:
                return "hold", 0.0, 0.0

            required_cols = {"open", "high", "low", "close", "volume"}
            if not required_cols.issubset(exec_df.columns) or not required_cols.issubset(trend_df.columns):
                return "hold", 0.0, 0.0

            exec_macd_df = ta.macd(exec_df["close"], fast=self.exec_fast, slow=self.exec_slow, signal=self.exec_signal)
            trend_macd_df = ta.macd(trend_df["close"], fast=self.trend_fast, slow=self.trend_slow, signal=self.trend_signal)
            exec_macd, exec_signal, exec_hist = self._extract_macd_components(exec_macd_df)
            trend_macd, trend_signal, _trend_hist = self._extract_macd_components(trend_macd_df)
            if any(x is None for x in (exec_macd, exec_signal, exec_hist, trend_macd, trend_signal)):
                return "hold", 0.0, 0.0

            if len(exec_macd) < 4 or len(exec_signal) < 3 or len(exec_hist) < 4:
                return "hold", 0.0, 0.0

            # Strategy-service already drops the in-progress candle for each timeframe.
            # Use the latest available row as the latest CLOSED candle.
            macd_now = float(exec_macd.iloc[-1])
            macd_prev = float(exec_macd.iloc[-2])
            sig_now = float(exec_signal.iloc[-1])
            sig_prev = float(exec_signal.iloc[-2])
            hist_now = float(exec_hist.iloc[-1])
            hist_prev = float(exec_hist.iloc[-2])
            hist_prev2 = float(exec_hist.iloc[-3])

            bullish_cross = macd_prev <= sig_prev and macd_now > sig_now
            bearish_cross = macd_prev >= sig_prev and macd_now < sig_now
            hist_expanding_up = hist_now > hist_prev and (hist_now - hist_prev) >= self.histogram_min_delta
            hist_expanding_down = hist_now < hist_prev and (hist_prev - hist_now) >= self.histogram_min_delta
            hist_rising_two_bars = hist_now > hist_prev > hist_prev2
            hist_falling_two_bars = hist_now < hist_prev < hist_prev2
            hist_flipped_positive = hist_prev <= 0.0 and hist_now > 0.0
            hist_flipped_negative = hist_prev >= 0.0 and hist_now < 0.0
            bullish_divergence, bearish_divergence = self._detect_divergence(exec_df, exec_hist) if self.enable_divergence_detection else (False, False)

            # Higher-TF trend (typically 1h): MACD vs signal for optional long/short bias.
            trend_macd_now = float(trend_macd.iloc[-1])
            trend_sig_now = float(trend_signal.iloc[-1])
            trend_bullish = trend_macd_now > trend_sig_now
            trend_bearish = trend_macd_now < trend_sig_now
            # Execution TF (15m): MACD vs signal — primary bias when *_macd_bias_timeframe is "execution".
            exec_bias_bullish = macd_now > sig_now
            exec_bias_bearish = macd_now < sig_now
            long_macd_bias_ok = trend_bullish if self.long_macd_bias_timeframe == "trend" else exec_bias_bullish
            short_macd_bias_ok = trend_bearish if self.short_macd_bias_timeframe == "trend" else exec_bias_bearish

            # Price + volume filters on execution timeframe.
            ema_exec = ta.ema(exec_df["close"], length=self.ema_filter_period)
            ema_now = float(ema_exec.iloc[-1]) if ema_exec is not None and not pd.isna(ema_exec.iloc[-1]) else float(exec_df["close"].iloc[-1])
            price_now = float(exec_df["close"].iloc[-1])
            price_above_ema = price_now > ema_now
            price_below_ema = price_now < ema_now
            previous_close = float(exec_df["close"].iloc[-2])

            avg_volume = float(exec_df["volume"].rolling(window=self.volume_window).mean().iloc[-1])
            current_volume = float(exec_df["volume"].iloc[-1])
            required_volume_multiplier = self._resolve_volume_multiplier(exec_df, current_regime)
            volume_ratio = (current_volume / avg_volume) if avg_volume > 0 else 0.0
            volume_ok = avg_volume > 0 and current_volume >= (avg_volume * required_volume_multiplier)

            exec_rsi_series = ta.rsi(exec_df["close"], length=self.rsi_period)
            trend_rsi_series = ta.rsi(trend_df["close"], length=self.rsi_period)
            exec_rsi_now = (
                float(exec_rsi_series.iloc[-1])
                if exec_rsi_series is not None and len(exec_rsi_series) > 0 and not pd.isna(exec_rsi_series.iloc[-1])
                else 50.0
            )
            trend_rsi_now = (
                float(trend_rsi_series.iloc[-1])
                if trend_rsi_series is not None and len(trend_rsi_series) > 0 and not pd.isna(trend_rsi_series.iloc[-1])
                else 50.0
            )
            macd_rsi_window_values = []
            macd_rsi_window_timestamps = []
            green_consecutive_increasing = (
                hist_now > 0.0 and hist_prev > 0.0 and hist_now > hist_prev
            )
            hist_rising_two_bars_gate = hist_now > hist_prev > hist_prev2
            rsi_lookback = max(1, self.rsi_last_n_below)
            rsi_any_of_last_n_below = False
            rsi_gate_fail_detail = "insufficient_rsi_history"
            if exec_rsi_series is not None and len(exec_rsi_series) >= rsi_lookback:
                recent_rsi = exec_rsi_series.iloc[-rsi_lookback:]
                macd_rsi_window_values = [
                    round(float(v), 2)
                    for v in recent_rsi.tolist()
                    if not pd.isna(v)
                ]
                macd_rsi_window_timestamps = [
                    ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
                    for ts in recent_rsi.index.tolist()
                ]
                if len(macd_rsi_window_values) < rsi_lookback:
                    rsi_gate_fail_detail = "rsi_window_has_nan"
                elif (recent_rsi < self.rsi_max_for_buy).any():
                    rsi_any_of_last_n_below = True
                else:
                    rsi_gate_fail_detail = (
                        f"rsi_none_of_last{rsi_lookback}_lt_{int(self.rsi_max_for_buy)}"
                    )
            self.logger.info(
                "[MACDMomentumStrategy] %s buy_gates hist_now=%.6f hist_prev=%.6f "
                "green_consecutive_increasing=%s require_two_green=%s "
                "rsi_last_n=%s rsi_max=%.2f rsi_any_below=%s rsi_fail=%s "
                "rsi_window_ts=%s rsi_window=%s",
                pair or self.state.pair,
                hist_now,
                hist_prev,
                green_consecutive_increasing,
                self.require_two_green_hist_bars,
                rsi_lookback,
                self.rsi_max_for_buy,
                rsi_any_of_last_n_below,
                rsi_gate_fail_detail,
                macd_rsi_window_timestamps,
                macd_rsi_window_values,
            )

            signal = "hold"
            confidence = 0.0
            strength = 0.0
            reasons: List[str] = []
            gate_fail_reasons: List[str] = []

            # Mandatory 15m gates: two rising histogram bars (allows negative-but-recovering hist).
            if self.require_two_green_hist_bars:
                buy_hist_gate_ok = hist_rising_two_bars_gate
                if self.require_hist_positive_for_long:
                    buy_hist_gate_ok = buy_hist_gate_ok and hist_now > 0.0 and hist_prev > 0.0
            else:
                buy_hist_gate_ok = (
                    hist_rising_two_bars
                    and (not self.require_hist_positive_for_long or hist_now > 0.0)
                )
            buy_rsi_gate_ok = rsi_any_of_last_n_below
            long_volume_ok = (not self.use_volume_confirmation) or volume_ok
            continuation_regime_ok = (
                current_regime in self.continuation_allowed_regimes
                if self.continuation_allowed_regimes
                else True
            )
            continuation_exec_bias_ok = (not self.continuation_require_exec_macd_bias) or exec_bias_bullish
            continuation_rsi_ok = (
                self.continuation_min_rsi <= exec_rsi_now <= self.continuation_max_rsi
                and trend_rsi_now >= self.continuation_min_trend_rsi
            )
            continuation_hist_ok = hist_rising_two_bars_gate or (
                hist_now > hist_prev and hist_expanding_up
            )
            continuation_volume_ok = (
                (not self.use_volume_confirmation)
                or (avg_volume > 0 and volume_ratio >= self.continuation_volume_multiplier)
            )
            continuation_full_volume_ok = (not self.use_volume_confirmation) or volume_ok
            continuation_confirm_ok = True
            if self.continuation_confirmation_pct > 0:
                lookback = max(1, min(self.continuation_confirm_lookback, len(exec_df) - 1))
                prior_close_high = float(exec_df["close"].iloc[-(lookback + 1):-1].max())
                confirm_base = max(previous_close, prior_close_high)
                continuation_confirm_ok = (
                    confirm_base > 0
                    and price_now >= confirm_base * (1.0 + self.continuation_confirmation_pct)
                )
            continuation_long_ok = (
                self.continuation_long_enabled
                and continuation_regime_ok
                and continuation_hist_ok
                and long_macd_bias_ok
                and continuation_exec_bias_ok
                and price_above_ema
                and continuation_volume_ok
                and (continuation_full_volume_ok or not self.continuation_requires_volume_ok)
                and continuation_rsi_ok
                and continuation_confirm_ok
            )

            short_hist_ok = (
                (hist_falling_two_bars and (not self.require_hist_negative_for_short or hist_now < 0.0))
                or hist_flipped_negative
                or (bearish_cross and hist_expanding_down)
            )
            short_volume_ok = (not self.use_volume_confirmation) or volume_ok

            if not buy_hist_gate_ok:
                gate_fail_reasons.append("gate_fail:hist_2rising")
            if not buy_rsi_gate_ok:
                gate_fail_reasons.append(
                    f"gate_fail:rsi_none_of_last{rsi_lookback}_lt_{int(self.rsi_max_for_buy)}"
                    f"({rsi_gate_fail_detail})"
                )
            if not long_macd_bias_ok:
                gate_fail_reasons.append(
                    "gate_fail:1h_macd_lte_signal"
                    if self.long_macd_bias_timeframe == "trend"
                    else "gate_fail:exec_macd_lte_signal"
                )
            if not price_above_ema:
                gate_fail_reasons.append("gate_fail:price_below_ema")
            if not long_volume_ok:
                gate_fail_reasons.append("gate_fail:volume")

            long_entry_ok = (
                buy_hist_gate_ok
                and buy_rsi_gate_ok
                and long_macd_bias_ok
                and price_above_ema
                and long_volume_ok
            )

            if long_entry_ok:
                signal = "buy"
                confidence = self.min_confidence_score
                strength = max(
                    self.buy_strength_floor,
                    min(1.0, abs(hist_now) * 8),
                )
                reasons = [
                    "gate:hist_2rising",
                    f"gate:rsi_any_of_last{rsi_lookback}_lt_{int(self.rsi_max_for_buy)}",
                    (
                        "1h_macd_gt_signal"
                        if self.long_macd_bias_timeframe == "trend"
                        else "15m_macd_gt_signal"
                    ),
                    "price>ema",
                    "vol_ok",
                ]
                if self.enable_divergence_detection and bullish_divergence:
                    confidence = min(1.0, confidence + self.divergence_confidence_boost)
                    reasons.append("bull_divergence")
            elif continuation_long_ok:
                signal = "buy"
                confidence = max(self.min_confidence_score, self.continuation_confidence)
                strength = max(
                    self.continuation_strength_floor,
                    min(1.0, max(abs(hist_now) * 8, (exec_rsi_now - self.continuation_min_rsi) / 50.0)),
                )
                reasons = [
                    "continuation_long",
                    "hist_rising",
                    (
                        "1h_macd_gt_signal"
                        if self.long_macd_bias_timeframe == "trend"
                        else "15m_macd_gt_signal"
                    ),
                    "price>ema",
                    "vol_ok",
                    f"rsi_{self.continuation_min_rsi:.0f}_{self.continuation_max_rsi:.0f}",
                ]
            elif gate_fail_reasons:
                self.logger.info(
                    "[MACDMomentumStrategy] %s HOLD conf=0.00 strength=0.00 reason=%s",
                    pair or self.state.pair,
                    "+".join(gate_fail_reasons),
                )
            elif short_macd_bias_ok and price_below_ema and short_hist_ok and short_volume_ok:
                signal = "sell"
                confidence = max(0.5, self.min_confidence_score - 0.05)
                strength = min(1.0, abs(hist_now) * 8)
                reasons = (
                    ["trend_1h_bear_bias", "price<ema", "hist_short_setup"]
                    if self.short_macd_bias_timeframe == "trend"
                    else ["exec_tf_bear_bias", "price<ema", "hist_short_setup"]
                )
                if volume_ok:
                    reasons.append("vol_ok")
                if self.enable_divergence_detection and bearish_divergence:
                    confidence = min(1.0, confidence + self.divergence_confidence_boost)
                    reasons.append("bear_divergence")

            self.state.indicators.update(
                {
                    "exec_macd": macd_now,
                    "exec_macd_signal": sig_now,
                    "exec_macd_hist": hist_now,
                    "trend_macd": trend_macd_now,
                    "trend_macd_signal": trend_sig_now,
                    "volume_ratio": volume_ratio,
                    "required_volume_multiplier": required_volume_multiplier,
                    "continuation_volume_multiplier": self.continuation_volume_multiplier,
                    "continuation_requires_volume_ok": self.continuation_requires_volume_ok,
                    "continuation_confirmation_pct": self.continuation_confirmation_pct,
                    "continuation_confirm_ok": continuation_confirm_ok,
                    "ema_exec": ema_now,
                    "price_now": price_now,
                    "price_above_ema": price_above_ema,
                    "hist_rising_two_bars": hist_rising_two_bars,
                    "hist_falling_two_bars": hist_falling_two_bars,
                    "hist_flipped_positive": hist_flipped_positive,
                    "hist_flipped_negative": hist_flipped_negative,
                    "bullish_divergence": bullish_divergence,
                    "bearish_divergence": bearish_divergence,
                    "exec_rsi": exec_rsi_now,
                    "trend_rsi": trend_rsi_now,
                    "buy_hist_gate_ok": buy_hist_gate_ok,
                    "buy_rsi_gate_ok": buy_rsi_gate_ok,
                    "macd_green_rsi_buy_ok": buy_rsi_gate_ok,
                    "long_entry_ok": long_entry_ok,
                    "continuation_long_enabled": self.continuation_long_enabled,
                    "continuation_long_ok": continuation_long_ok,
                    "continuation_regime_ok": continuation_regime_ok,
                    "continuation_hist_ok": continuation_hist_ok,
                    "continuation_rsi_ok": continuation_rsi_ok,
                    "continuation_exec_bias_ok": continuation_exec_bias_ok,
                    "continuation_volume_ok": continuation_volume_ok,
                    "continuation_full_volume_ok": continuation_full_volume_ok,
                    "continuation_min_rsi": self.continuation_min_rsi,
                    "continuation_max_rsi": self.continuation_max_rsi,
                    "long_volume_ok": long_volume_ok,
                    "require_two_green_hist_bars": self.require_two_green_hist_bars,
                    "rsi_last_n_below": rsi_lookback,
                    "rsi_max_for_buy": self.rsi_max_for_buy,
                    "rsi_any_of_last_n_below": rsi_any_of_last_n_below,
                    "macd_green_rsi_lookback": rsi_lookback,
                    "macd_green_rsi_threshold": self.rsi_max_for_buy,
                    "macd_green_consecutive_increasing": green_consecutive_increasing,
                    "macd_hist_prev": hist_prev,
                    "macd_hist_ts": (
                        exec_df.index[-1].isoformat()
                        if hasattr(exec_df.index[-1], "isoformat")
                        else str(exec_df.index[-1])
                    ),
                    "macd_rsi_window_values": macd_rsi_window_values,
                    "macd_rsi_window_timestamps": macd_rsi_window_timestamps,
                    "exec_bias_bullish": exec_bias_bullish,
                    "exec_bias_bearish": exec_bias_bearish,
                    "long_macd_bias_ok": long_macd_bias_ok,
                    "short_macd_bias_ok": short_macd_bias_ok,
                    "long_macd_bias_timeframe": self.long_macd_bias_timeframe,
                }
            )
            self.state.last_signal = signal

            reason_str = "+".join(reasons) if reasons else (
                "+".join(gate_fail_reasons) if gate_fail_reasons else "no_signal"
            )
            self.logger.info(
                f"[MACDMomentumStrategy] {pair or self.state.pair} {signal.upper()} "
                f"conf={confidence:.2f} strength={strength:.2f} reason={reason_str}"
            )
            return signal, float(confidence), float(strength)
        except Exception as e:
            self.logger.error(f"[MACDMomentumStrategy] Error generating signal: {e}")
            return "hold", 0.0, 0.0

    async def calculate_position_size(self, signal_type: str) -> float:
        """Return a conservative SPOT size; exits remain globally managed."""
        if signal_type != "buy":
            return 0.0
        if not self.exchange:
            return 0.0
        try:
            balance = await self.exchange.get_balance()
            free_usd = float(balance.get("free", 0.0) or 0.0)
            ticker = await self.exchange.get_ticker(self.state.pair, self.exchange_name)
            last_price = float((ticker or {}).get("last", 0.0) or 0.0)
            if free_usd <= 0 or last_price <= 0:
                return 0.0
            # Keep sizing modest; orchestrator/risk manager applies final checks.
            risk_usd = free_usd * 0.01
            return max(0.0, risk_usd / last_price)
        except Exception as e:
            self.logger.debug(f"[MACDMomentumStrategy] Position size fallback to 0: {e}")
            return 0.0

    async def should_exit(self) -> bool:
        """
        Exit decision remains centralized (trailing stop / profit protection /
        orchestrator rules). Strategy-level exit does not interfere.
        """
        return False
