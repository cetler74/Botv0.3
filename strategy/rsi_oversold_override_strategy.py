"""
RSI Oversold Override strategy (15m execution, optional 1h trend guards).

Independent override strategy:
- Base trigger: 15m RSI below threshold.
- Optional safety filters reduce knife-catching:
  * 15m RSI reclaim cross (back above threshold)
  * 15m close > EMA20
  * 1h MACD line >= signal line
  * 1h RSI >= floor
  * regime allow/block lists
- Designed to be displayed as a separate strategy signal, while consensus
  integration is handled by strategy-service override logic.
"""

from typing import Dict, Any, Optional, Tuple
import logging

import pandas as pd
import pandas_ta as ta

from strategy.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


class RSIOversoldOverrideStrategy(BaseStrategy):
    """Independent RSI-oversold strategy for aggressive long entries."""

    STRATEGY_NAME = "RSI Oversold Override"

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
        self.rsi_buy_threshold = float(params.get("rsi_buy_threshold", 30.0))
        self.buy_confidence = float(params.get("buy_confidence", 0.95))
        self.buy_strength = float(params.get("buy_strength", 0.90))
        self.require_reclaim_cross = bool(params.get("require_reclaim_cross", True))
        self.reclaim_lookback_bars = max(1, int(params.get("reclaim_lookback_bars", 1)))
        self.require_price_above_ema20 = bool(params.get("require_price_above_ema20", True))
        self.ema_filter_period = int(params.get("ema_filter_period", 20))
        self.require_trend_macd_bias = bool(params.get("require_trend_macd_bias", True))
        self.trend_fast = int(params.get("trend_fast_period", 12))
        self.trend_slow = int(params.get("trend_slow_period", 26))
        self.trend_signal = int(params.get("trend_signal_period", 9))
        self.require_trend_rsi_floor = bool(params.get("require_trend_rsi_floor", False))
        self.trend_rsi_floor = float(params.get("trend_rsi_floor", 40.0))
        raw_block = params.get("blocked_regimes", ["trending_down"])
        raw_allow = params.get("allowed_regimes", [])
        self.blocked_regimes = {str(x).lower() for x in raw_block} if isinstance(raw_block, (list, tuple, set)) else set()
        self.allowed_regimes = {str(x).lower() for x in raw_allow} if isinstance(raw_allow, (list, tuple, set)) else set()

        self._current_ohlcv = None

    async def initialize(self, pair: str) -> None:
        self.state.pair = pair
        self.state.last_signal = "hold"
        self.state.indicators = {}
        self.logger.info(f"Initialized RSI Oversold Override strategy for {pair}")

    async def update(self, ohlcv: pd.DataFrame) -> None:
        self._current_ohlcv = ohlcv
        self.state.last_signal_time = pd.Timestamp.utcnow().to_pydatetime()

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
                    trend_df = exec_df
            else:
                exec_df = market_data
                trend_df = market_data

            if exec_df is None or trend_df is None or len(exec_df) < max(self.rsi_period + 2, 20):
                return "hold", 0.0, 0.0

            required_cols = {"open", "high", "low", "close", "volume"}
            if not required_cols.issubset(exec_df.columns) or not required_cols.issubset(trend_df.columns):
                return "hold", 0.0, 0.0

            rsi_series = ta.rsi(exec_df["close"], length=self.rsi_period)
            if rsi_series is None or len(rsi_series) == 0 or pd.isna(rsi_series.iloc[-1]):
                return "hold", 0.0, 0.0
            rsi_now = float(rsi_series.iloc[-1])

            current_regime = str(getattr(self.state, "market_regime", "unknown") or "unknown").lower()
            if self.allowed_regimes and current_regime not in self.allowed_regimes:
                self.state.last_signal = "hold"
                return "hold", 0.0, 0.0
            if self.blocked_regimes and current_regime in self.blocked_regimes:
                self.state.last_signal = "hold"
                return "hold", 0.0, 0.0

            oversold_now = rsi_now < self.rsi_buy_threshold
            reclaim_ok = True
            if self.require_reclaim_cross:
                if len(rsi_series) < self.reclaim_lookback_bars + 2:
                    reclaim_ok = False
                else:
                    prior = rsi_series.iloc[-(self.reclaim_lookback_bars + 1):-1]
                    reclaim_ok = bool(prior.min() <= self.rsi_buy_threshold and rsi_now > self.rsi_buy_threshold)

            ema_ok = True
            ema_exec = ta.ema(exec_df["close"], length=self.ema_filter_period)
            price_now = float(exec_df["close"].iloc[-1])
            ema_now = float(ema_exec.iloc[-1]) if ema_exec is not None and len(ema_exec) > 0 and not pd.isna(ema_exec.iloc[-1]) else price_now
            if self.require_price_above_ema20:
                ema_ok = price_now > ema_now

            trend_macd_ok = True
            trend_macd_df = ta.macd(trend_df["close"], fast=self.trend_fast, slow=self.trend_slow, signal=self.trend_signal)
            trend_macd_now = 0.0
            trend_signal_now = 0.0
            if trend_macd_df is not None and not trend_macd_df.empty:
                macd_col = next((c for c in trend_macd_df.columns if str(c).startswith("MACD_") and not str(c).startswith("MACDs") and not str(c).startswith("MACDh")), None)
                sig_col = next((c for c in trend_macd_df.columns if str(c).startswith("MACDs_")), None)
                if macd_col and sig_col and not pd.isna(trend_macd_df[macd_col].iloc[-1]) and not pd.isna(trend_macd_df[sig_col].iloc[-1]):
                    trend_macd_now = float(trend_macd_df[macd_col].iloc[-1])
                    trend_signal_now = float(trend_macd_df[sig_col].iloc[-1])
            if self.require_trend_macd_bias:
                trend_macd_ok = trend_macd_now >= trend_signal_now

            trend_rsi_ok = True
            trend_rsi_now = 50.0
            if self.require_trend_rsi_floor:
                trend_rsi_series = ta.rsi(trend_df["close"], length=self.rsi_period)
                if trend_rsi_series is None or len(trend_rsi_series) == 0 or pd.isna(trend_rsi_series.iloc[-1]):
                    trend_rsi_ok = False
                else:
                    trend_rsi_now = float(trend_rsi_series.iloc[-1])
                    trend_rsi_ok = trend_rsi_now >= self.trend_rsi_floor

            buy_ok = oversold_now and reclaim_ok and ema_ok and trend_macd_ok and trend_rsi_ok
            signal = "buy" if buy_ok else "hold"
            confidence = self.buy_confidence if signal == "buy" else 0.0
            strength = self.buy_strength if signal == "buy" else 0.0

            self.state.indicators.update(
                {
                    "market_regime": current_regime,
                    "rsi_15m": rsi_now,
                    "rsi_buy_threshold": self.rsi_buy_threshold,
                    "rsi_reclaim_ok": bool(reclaim_ok),
                    "oversold_now": bool(oversold_now),
                    "price_above_ema": bool(ema_ok),
                    "ema_exec": float(ema_now),
                    "trend_macd": float(trend_macd_now),
                    "trend_macd_signal": float(trend_signal_now),
                    "trend_macd_ok": bool(trend_macd_ok),
                    "trend_rsi_1h": float(trend_rsi_now),
                    "trend_rsi_ok": bool(trend_rsi_ok),
                    "rsi_oversold_buy": bool(signal == "buy"),
                }
            )
            self.state.last_signal = signal

            if signal == "buy":
                reason = "rsi_override_filtered_buy"
            else:
                failed = []
                if not oversold_now:
                    failed.append("not_oversold")
                if self.require_reclaim_cross and not reclaim_ok:
                    failed.append("no_reclaim")
                if self.require_price_above_ema20 and not ema_ok:
                    failed.append("below_ema")
                if self.require_trend_macd_bias and not trend_macd_ok:
                    failed.append("trend_macd_bearish")
                if self.require_trend_rsi_floor and not trend_rsi_ok:
                    failed.append("trend_rsi_weak")
                reason = ",".join(failed) if failed else "gated"
            self.logger.info(
                "[RSIOversoldOverrideStrategy] %s %s conf=%.2f strength=%.2f reason=%s "
                "rsi_15m=%.2f regime=%s reclaim=%s ema_ok=%s macd_ok=%s trend_rsi_ok=%s",
                pair or self.state.pair,
                signal.upper(),
                confidence,
                strength,
                reason,
                rsi_now,
                current_regime,
                reclaim_ok,
                ema_ok,
                trend_macd_ok,
                trend_rsi_ok,
            )
            return signal, float(confidence), float(strength)
        except Exception as e:
            self.logger.error(f"[RSIOversoldOverrideStrategy] Error generating signal: {e}")
            return "hold", 0.0, 0.0

    async def calculate_position_size(self, signal_type: str) -> float:
        if signal_type != "buy":
            return 0.0
        return 0.0

    async def should_exit(self) -> bool:
        return False

