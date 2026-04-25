"""
MACD Momentum strategy tuned for crypto intraday execution.

Design:
- Uses 1h MACD for trend bias and 15m MACD for execution timing.
- Applies volume and EMA filters to reduce false breakouts.
- Emits buy/sell/hold signals only; exits remain orchestrator-managed
  (profit protection + trailing stop + global risk controls).
"""

from typing import Dict, Any, Optional, Tuple
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
        self.adx_threshold = float(params.get("adx_threshold", 18.0))
        self.adx_period = int(params.get("adx_period", 14))
        self.min_hist_rising_bars = int(params.get("min_hist_rising_bars", 2))
        self.min_hist_falling_bars = int(params.get("min_hist_falling_bars", 2))
        self.require_hist_positive_for_long = bool(params.get("require_hist_positive_for_long", True))
        self.require_hist_negative_for_short = bool(params.get("require_hist_negative_for_short", True))
        self.use_volume_confirmation = bool(params.get("use_volume_confirmation", True))
        self.adx_boost_threshold = float(params.get("adx_boost_threshold", self.adx_threshold))
        self.adx_confidence_boost = float(params.get("adx_confidence_boost", 0.06))
        self.skip_sideways_regime = bool(params.get("skip_sideways_regime", True))
        self.dynamic_volume_enabled = bool(params.get("dynamic_volume_enabled", True))
        self.dynamic_volume_floor = float(params.get("dynamic_volume_floor", 1.05))
        self.dynamic_volume_ceiling = float(params.get("dynamic_volume_ceiling", 1.15))
        self.enable_divergence_detection = bool(params.get("enable_divergence_detection", True))
        self.divergence_lookback = int(params.get("divergence_lookback", 12))
        self.divergence_confidence_boost = float(params.get("divergence_confidence_boost", 0.05))
        self.use_rsi_buy_filter = bool(params.get("use_rsi_buy_filter", True))
        self.rsi_period = int(params.get("rsi_period", 14))
        self.rsi_buy_max = float(params.get("rsi_buy_max", 50.0))

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
            if self.skip_sideways_regime and current_regime in ("sideways",):
                self.logger.info(
                    "[MACDMomentumStrategy] %s HOLD conf=0.00 strength=0.00 reason=sideways_regime_skip",
                    pair or self.state.pair
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

            if len(exec_macd) < 3 or len(exec_signal) < 2 or len(exec_hist) < 3:
                return "hold", 0.0, 0.0

            # 15m execution trigger.
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

            # 1h trend filter.
            trend_macd_now = float(trend_macd.iloc[-1])
            trend_sig_now = float(trend_signal.iloc[-1])
            # Soft bias: MACD relationship to signal line only (no zero-line hard gate).
            trend_bullish = trend_macd_now > trend_sig_now
            trend_bearish = trend_macd_now < trend_sig_now

            # Price + volume filters on execution timeframe.
            ema_exec = ta.ema(exec_df["close"], length=self.ema_filter_period)
            ema_now = float(ema_exec.iloc[-1]) if ema_exec is not None and not pd.isna(ema_exec.iloc[-1]) else float(exec_df["close"].iloc[-1])
            price_now = float(exec_df["close"].iloc[-1])
            price_above_ema = price_now > ema_now
            price_below_ema = price_now < ema_now

            avg_volume = float(exec_df["volume"].rolling(window=self.volume_window).mean().iloc[-1])
            current_volume = float(exec_df["volume"].iloc[-1])
            required_volume_multiplier = self._resolve_volume_multiplier(exec_df, current_regime)
            volume_ok = avg_volume > 0 and current_volume >= (avg_volume * required_volume_multiplier)

            adx_df = ta.adx(exec_df["high"], exec_df["low"], exec_df["close"], length=self.adx_period)
            adx_col = next((c for c in adx_df.columns if str(c).startswith("ADX")) if adx_df is not None else (), None)
            adx_now = float(adx_df[adx_col].iloc[-1]) if adx_col and not pd.isna(adx_df[adx_col].iloc[-1]) else 0.0
            adx_boost_active = adx_now >= self.adx_boost_threshold
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
            rsi_buy_ok = (
                (exec_rsi_now < self.rsi_buy_max) and (trend_rsi_now < self.rsi_buy_max)
                if self.use_rsi_buy_filter
                else True
            )

            signal = "hold"
            confidence = 0.0
            strength = 0.0
            reasons = []

            long_hist_ok = (
                (hist_rising_two_bars and (not self.require_hist_positive_for_long or hist_now > 0.0))
                or hist_flipped_positive
                or (bullish_cross and hist_expanding_up)
            )
            short_hist_ok = (
                (hist_falling_two_bars and (not self.require_hist_negative_for_short or hist_now < 0.0))
                or hist_flipped_negative
                or (bearish_cross and hist_expanding_down)
            )

            long_volume_ok = (not self.use_volume_confirmation) or volume_ok
            short_volume_ok = (not self.use_volume_confirmation) or volume_ok

            if trend_bullish and price_above_ema and long_hist_ok and long_volume_ok and rsi_buy_ok:
                signal = "buy"
                confidence = self.min_confidence_score
                strength = min(1.0, abs(hist_now) * 8)
                reasons = ["trend_1h_bull_bias", "price>ema", "hist_long_setup"]
                if volume_ok:
                    reasons.append("vol_ok")
                if self.use_rsi_buy_filter:
                    reasons.append("rsi_buy_ok")
                if adx_boost_active:
                    confidence = min(1.0, confidence + self.adx_confidence_boost)
                    reasons.append("adx_boost")
                if bullish_divergence:
                    confidence = min(1.0, confidence + self.divergence_confidence_boost)
                    reasons.append("bull_divergence")
            elif trend_bearish and price_below_ema and short_hist_ok and short_volume_ok:
                signal = "sell"
                confidence = max(0.5, self.min_confidence_score - 0.05)
                strength = min(1.0, abs(hist_now) * 8)
                reasons = ["trend_1h_bear_bias", "price<ema", "hist_short_setup"]
                if volume_ok:
                    reasons.append("vol_ok")
                if adx_boost_active:
                    confidence = min(1.0, confidence + self.adx_confidence_boost)
                    reasons.append("adx_boost")
                if bearish_divergence:
                    confidence = min(1.0, confidence + self.divergence_confidence_boost)
                    reasons.append("bear_divergence")

            self.state.indicators.update(
                {
                    "exec_macd": macd_now,
                    "exec_macd_signal": sig_now,
                    "exec_macd_hist": hist_now,
                    "trend_macd": trend_macd_now,
                    "trend_macd_signal": trend_sig_now,
                    "adx": adx_now,
                    "volume_ratio": (current_volume / avg_volume) if avg_volume > 0 else 0.0,
                    "required_volume_multiplier": required_volume_multiplier,
                    "ema_exec": ema_now,
                    "hist_rising_two_bars": hist_rising_two_bars,
                    "hist_falling_two_bars": hist_falling_two_bars,
                    "hist_flipped_positive": hist_flipped_positive,
                    "hist_flipped_negative": hist_flipped_negative,
                    "bullish_divergence": bullish_divergence,
                    "bearish_divergence": bearish_divergence,
                    "exec_rsi": exec_rsi_now,
                    "trend_rsi": trend_rsi_now,
                    "rsi_buy_ok": rsi_buy_ok,
                }
            )
            self.state.last_signal = signal

            reason_str = "+".join(reasons) if reasons else "no_signal"
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
