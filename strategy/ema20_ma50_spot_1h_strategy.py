"""EMA20/MA50 1h spot trend-pullback strategy."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import pandas as pd

from strategy.base_strategy import BaseStrategy


class Ema20Ma50Spot1hStrategy(BaseStrategy):
    """Long-only 1h EMA20/MA50 continuation after a shallow pullback."""

    STRATEGY_NAME = "EMA20 MA50 Spot 1h"

    def __init__(
        self,
        config: Dict[str, Any],
        exchange: Any,
        database: Any,
        redis_client=None,
        exchange_name=None,
    ):
        super().__init__(config, exchange, database, redis_client)
        self.exchange_name = exchange_name
        params = config.get("parameters", {}) if isinstance(config, dict) else {}
        self.primary_timeframe = str(params.get("primary_timeframe", "1h")).lower()
        self.ema_period = int(params.get("ema_period", 20))
        self.ma_period = int(params.get("ma_period", 50))
        self.min_candles = int(params.get("min_candles", 80))
        self.pullback_lookback_bars = int(params.get("pullback_lookback_bars", 4))
        self.pullback_tolerance_pct = float(params.get("pullback_tolerance_pct", 0.004))
        self.max_extension_pct = float(params.get("max_extension_pct", 0.020))
        self.min_ma_gap_pct = float(params.get("min_ma_gap_pct", 0.001))
        self.min_reward_risk = float(params.get("min_reward_risk", 2.0))
        self.swing_lookback_bars = int(params.get("swing_lookback_bars", 8))
        self.stop_buffer_pct = float(params.get("stop_buffer_pct", 0.002))
        self.max_stop_pct = float(params.get("max_stop_pct", 0.035))
        self.min_stop_pct = float(params.get("min_stop_pct", 0.006))
        self.buy_confidence = float(params.get("buy_confidence", 0.78))
        self.buy_strength = float(params.get("buy_strength", 0.76))
        self.blocked_regimes = {
            str(x).strip().lower()
            for x in params.get("blocked_regimes", ["trending_down", "high_volatility"])
            if str(x).strip()
        }
        self.allowed_regimes = {
            str(x).strip().lower()
            for x in params.get("allowed_regimes", [])
            if str(x).strip()
        }
        self._current_ohlcv = None

    async def initialize(self, pair: str) -> None:
        self.state.pair = pair
        self.state.last_signal = "hold"
        self.state.indicators = {}

    async def update(self, ohlcv: pd.DataFrame) -> None:
        self._current_ohlcv = ohlcv
        self.state.last_signal_time = pd.Timestamp.utcnow().to_pydatetime()

    async def calculate_position_size(self, signal_type: str) -> float:
        params = self.config.get("parameters", {}) if isinstance(self.config, dict) else {}
        return float(params.get("position_size", 0.0) or 0.0)

    async def should_exit(self) -> bool:
        return False

    @staticmethod
    def _df(market_data: Any, key: str) -> Optional[pd.DataFrame]:
        if isinstance(market_data, dict):
            df = market_data.get(key)
            return df if isinstance(df, pd.DataFrame) and not df.empty else None
        return market_data if isinstance(market_data, pd.DataFrame) and not market_data.empty else None

    @staticmethod
    def _ema(close: pd.Series, length: int) -> pd.Series:
        return close.astype(float).ewm(span=length, adjust=False, min_periods=length).mean()

    @staticmethod
    def _ma(close: pd.Series, length: int) -> pd.Series:
        return close.astype(float).rolling(window=length, min_periods=length).mean()

    def _hold(self, reason: str, extra: Optional[Dict[str, Any]] = None) -> Tuple[str, float, float]:
        payload: Dict[str, Any] = {
            "setup": "ema20_ma50_spot_1h",
            "skip_reason": reason,
            "primary_timeframe": self.primary_timeframe,
            "entry_reason": "",
        }
        if extra:
            payload.update(extra)
        self.state.indicators = payload
        return "hold", 0.0, 0.0

    async def generate_signal(
        self,
        market_data,
        indicators_cache: Optional[dict] = None,
        pair: Optional[str] = None,
        timeframe: Optional[str] = None,
        exchange_adapter=None,
    ) -> Tuple[str, float, float]:
        regime = str(getattr(self.state, "market_regime", "unknown") or "unknown").lower()
        if self.allowed_regimes and regime not in self.allowed_regimes:
            return self._hold("regime_not_allowed", {"market_regime": regime})
        if regime in self.blocked_regimes:
            return self._hold("blocked_regime", {"market_regime": regime})

        df = self._df(market_data, self.primary_timeframe)
        if df is None:
            return self._hold(f"missing_timeframe:{self.primary_timeframe}")
        if len(df) < self.min_candles:
            return self._hold("insufficient_candles", {"candle_count": len(df)})
        required = {"open", "high", "low", "close"}
        if not required.issubset(df.columns):
            return self._hold("missing_ohlcv_columns")

        close = df["close"].astype(float)
        open_ = df["open"].astype(float)
        low = df["low"].astype(float)
        high = df["high"].astype(float)
        ema20 = self._ema(close, self.ema_period)
        ma50 = self._ma(close, self.ma_period)
        if pd.isna(ema20.iloc[-1]) or pd.isna(ma50.iloc[-1]):
            return self._hold("indicator_not_ready")

        entry = float(close.iloc[-1])
        prev_close = float(close.iloc[-2])
        prev_ema = float(ema20.iloc[-2])
        ema_now = float(ema20.iloc[-1])
        ma_now = float(ma50.iloc[-1])
        ema_prev = float(ema20.iloc[-2])
        ma_prev = float(ma50.iloc[-2])
        ma_gap_pct = (ema_now - ma_now) / entry if entry > 0 else 0.0
        extension_pct = (entry - ema_now) / ema_now if ema_now > 0 else 0.0
        pullback_low = float(low.tail(max(1, self.pullback_lookback_bars)).min())
        touched_ema = pullback_low <= ema_now * (1.0 + self.pullback_tolerance_pct)
        reclaimed_ema = prev_close <= prev_ema * (1.0 + self.pullback_tolerance_pct) and entry > ema_now
        bullish_close = entry > float(open_.iloc[-1])
        higher_close = entry > prev_close

        base = {
            "market_regime": regime,
            "primary_timeframe": self.primary_timeframe,
            "entry_price": entry,
            "ema20": ema_now,
            "ma50": ma_now,
            "ma_gap_pct": ma_gap_pct,
            "extension_pct": extension_pct,
            "pullback_low": pullback_low,
        }
        if not (entry > ema_now > ma_now):
            return self._hold("trend_stack_not_bullish", base)
        if ema_now <= ema_prev or ma_now < ma_prev:
            return self._hold("moving_averages_not_rising", base)
        if ma_gap_pct < self.min_ma_gap_pct:
            return self._hold("ma_gap_too_small", base)
        if extension_pct > self.max_extension_pct:
            return self._hold("price_too_extended", base)
        if not (touched_ema or reclaimed_ema):
            return self._hold("no_recent_pullback_to_ema20", base)
        if not bullish_close or not higher_close:
            return self._hold("no_bullish_reclaim_close", base)

        lookback = max(3, min(self.swing_lookback_bars, len(df) - 1))
        swing_low = float(low.iloc[-lookback:-1].min())
        stop = min(swing_low, ma_now) * (1.0 - self.stop_buffer_pct)
        risk = entry - stop
        if risk <= 0:
            return self._hold("invalid_stop", {**base, "stop_hint": stop})
        stop_pct = risk / entry
        if stop_pct < self.min_stop_pct or stop_pct > self.max_stop_pct:
            return self._hold("stop_distance_out_of_range", {**base, "stop_hint": stop, "stop_pct": stop_pct})

        recent_high = float(high.iloc[-lookback:-1].max())
        target = max(recent_high, entry + self.min_reward_risk * risk)
        reward_risk = (target - entry) / risk if risk > 0 else 0.0
        if reward_risk + 1e-9 < self.min_reward_risk:
            return self._hold("reward_risk_too_low", {**base, "stop_hint": stop, "target_hint": target})
        if abs(reward_risk - self.min_reward_risk) <= 1e-9:
            reward_risk = self.min_reward_risk

        entry_reason = (
            f"EMA20/MA50 1h long: close {entry:.6f} above rising EMA20 {ema_now:.6f} "
            f"and MA50 {ma_now:.6f}; pullback held EMA20; stop {stop:.6f}; "
            f"target {target:.6f}; R:R {reward_risk:.2f}"
        )
        self.state.stop_loss = stop
        self.state.take_profit = target
        self.state.indicators = {
            **base,
            "setup": "ema20_ma50_spot_1h",
            "entry_reason": entry_reason,
            "stop_hint": stop,
            "target_hint": target,
            "stop_pct": stop_pct,
            "reward_risk": reward_risk,
            "target_pct": (target - entry) / entry,
        }
        return "buy", self.buy_confidence, self.buy_strength
