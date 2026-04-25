"""
RSI Oversold Checklist strategy.

Checklist-based long-entry strategy (15m execution + 1h bias):
- 15m RSI reclaim above 30 (avoid pure falling-knife entries)
- Price above EMA20 with optional macro trend filter (EMA50/EMA200)
- Volume spike confirmation (>= 1.2x 20-period average)
- Optional support proximity / bounce check
- Optional bullish RSI divergence confirmation
- Optional multi-timeframe RSI bias (1h RSI > 40)

The strategy emits BUY only when all enabled checklist conditions pass.
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
        self.volume_multiplier = float(params.get("volume_multiplier", 1.2))

        self.support_lookback = int(params.get("support_lookback", 30))
        self.support_tolerance_pct = float(params.get("support_tolerance_pct", 0.012))
        self.divergence_lookback = int(params.get("divergence_lookback", 14))

        self.require_volume_confirmation = bool(params.get("require_volume_confirmation", True))
        self.require_support_proximity = bool(params.get("require_support_proximity", False))
        self.require_bullish_divergence = bool(params.get("require_bullish_divergence", False))
        self.require_multi_tf_rsi = bool(params.get("require_multi_tf_rsi", True))
        self.require_macro_trend_filter = bool(params.get("require_macro_trend_filter", True))

        self.buy_confidence = float(params.get("buy_confidence", 0.97))
        self.buy_strength = float(params.get("buy_strength", 0.92))

        self._current_ohlcv = None

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

            min_len = max(self.ema_slow_period + 5, self.volume_window + 5, self.support_lookback + 5, self.rsi_period + 5)
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
            if exec_rsi is None or trend_rsi is None or pd.isna(exec_rsi.iloc[-1]) or pd.isna(exec_rsi.iloc[-2]) or pd.isna(trend_rsi.iloc[-1]):
                return "hold", 0.0, 0.0
            exec_rsi_now = float(exec_rsi.iloc[-1])
            exec_rsi_prev = float(exec_rsi.iloc[-2])
            trend_rsi_now = float(trend_rsi.iloc[-1])

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

            rolling_support = float(exec_df["low"].rolling(self.support_lookback).min().iloc[-1] or 0.0)
            near_support = rolling_support > 0 and price_now <= rolling_support * (1.0 + self.support_tolerance_pct)
            support_bounce = near_support and exec_rsi_now > self.rsi_reclaim_level

            bullish_divergence = self._detect_bullish_divergence(exec_df, exec_rsi)

            rsi_reclaimed = exec_rsi_prev <= self.rsi_reclaim_level and exec_rsi_now > self.rsi_reclaim_level
            exec_rsi_ok = self.exec_rsi_min <= exec_rsi_now <= self.exec_rsi_max
            trend_rsi_ok = (trend_rsi_now > self.trend_rsi_min) if self.require_multi_tf_rsi else True
            trend_filter_ok = price_now > ema20_now
            macro_trend_ok = (ema50_now > ema200_now) if self.require_macro_trend_filter else True
            volume_gate_ok = volume_ok if self.require_volume_confirmation else True
            support_gate_ok = support_bounce if self.require_support_proximity else True
            divergence_gate_ok = bullish_divergence if self.require_bullish_divergence else True

            checklist_buy = all(
                [
                    rsi_reclaimed,
                    exec_rsi_ok,
                    trend_rsi_ok,
                    trend_filter_ok,
                    macro_trend_ok,
                    volume_gate_ok,
                    support_gate_ok,
                    divergence_gate_ok,
                ]
            )

            signal = "buy" if checklist_buy else "hold"
            confidence = self.buy_confidence if checklist_buy else 0.0
            strength = self.buy_strength if checklist_buy else 0.0

            self.state.indicators.update(
                {
                    "exec_rsi_15m": exec_rsi_now,
                    "exec_rsi_15m_prev": exec_rsi_prev,
                    "trend_rsi_1h": trend_rsi_now,
                    "rsi_reclaimed_30": rsi_reclaimed,
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
                    "checklist_buy": checklist_buy,
                }
            )
            self.state.last_signal = signal

            reason = "rsi_checklist_buy" if checklist_buy else "checklist_not_satisfied"
            self.logger.info(
                "[RSIOversoldChecklistStrategy] %s %s conf=%.2f strength=%.2f reason=%s "
                "rsi15=%.2f rsi1h=%.2f vol_ok=%s trend_ok=%s",
                pair or self.state.pair,
                signal.upper(),
                confidence,
                strength,
                reason,
                exec_rsi_now,
                trend_rsi_now,
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

