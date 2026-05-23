"""
Standalone intraday long playbook strategies.

Implements the user playbook as independent entry lanes:
- Pullback Long Scalping
- Breakout Retest Long
- VWAP Bounce Scalping
- Small-Size Momentum Scalp

The orchestrator owns order placement and exits. These strategies publish
stop/target/risk hints in state.indicators for dashboard snapshots and logs.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

import pandas as pd

from strategy.base_strategy import BaseStrategy
from strategy.vwap_utils import session_vwap_hlc3

logger = logging.getLogger(__name__)


class _IntradayLongBaseStrategy(BaseStrategy):
    STRATEGY_NAME = "Intraday Long Base"
    SETUP_TYPE = "base"

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
        self.trend_timeframe = str(params.get("trend_timeframe", "15m")).lower()
        self.entry_timeframe = str(params.get("entry_timeframe", "5m")).lower()
        self.execution_timeframe = str(params.get("execution_timeframe", "1m")).lower()
        self.session_tz = str(params.get("session_tz", "UTC"))
        self.rsi_period = int(params.get("rsi_period", 14))
        self.volume_window = int(params.get("volume_window", 20))
        self.min_volume_multiplier = float(params.get("min_volume_multiplier", 1.15))
        self.min_rr = float(params.get("min_reward_risk", 1.5))
        self.max_stop_pct = float(params.get("max_stop_pct", 0.025))
        self.min_stop_pct = float(params.get("min_stop_pct", 0.0025))
        self.vwap_hold_tolerance_pct = float(params.get("vwap_hold_tolerance_pct", 0.0025))
        self.ema_touch_tolerance_pct = float(params.get("ema_touch_tolerance_pct", 0.003))
        self.breakout_lookback_bars = int(params.get("breakout_lookback_bars", 30))
        self.retest_tolerance_pct = float(params.get("retest_tolerance_pct", 0.003))
        self.max_rsi = float(params.get("max_rsi", 75.0))
        self.min_rsi = float(params.get("min_rsi", 40.0))
        self.buy_confidence = float(params.get("buy_confidence", 0.74))
        self.buy_strength = float(params.get("buy_strength", 0.72))
        self.blocked_regimes = {
            str(x).strip().lower()
            for x in params.get("blocked_regimes", ["trending_down", "low_volatility"])
            if str(x).strip()
        }
        self.allowed_exchanges = {
            str(x).strip().lower()
            for x in params.get("allowed_exchanges", [])
            if str(x).strip()
        }
        self.focus_tokens = {
            str(x).strip().upper()
            for x in params.get("focus_tokens", [])
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
    def _base_token(pair: Optional[str]) -> str:
        return str(pair or "").split("/", 1)[0].replace("-", "").upper()

    @staticmethod
    def _ema(close: pd.Series, length: int) -> pd.Series:
        return close.astype(float).ewm(span=length, adjust=False, min_periods=length).mean()

    @staticmethod
    def _rsi(close: pd.Series, length: int = 14) -> pd.Series:
        close = close.astype(float)
        delta = close.diff()
        gain = delta.clip(lower=0.0)
        loss = -delta.clip(upper=0.0)
        avg_gain = gain.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
        avg_loss = loss.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
        rs = avg_gain / avg_loss.replace(0.0, float("nan"))
        rsi = 100.0 - (100.0 / (1.0 + rs))
        return rsi.fillna(50.0)

    def _hold(self, reason: str, details: Optional[Dict[str, Any]] = None) -> Tuple[str, float, float]:
        payload = {"setup": self.SETUP_TYPE, "skip_reason": reason}
        if details:
            payload.update(details)
        self.state.indicators = payload
        logger.info("[%s] %s HOLD: %s", self.__class__.__name__, self.state.pair, reason)
        return "hold", 0.0, 0.0

    def _buy(self, confidence: float, strength: float, details: Dict[str, Any]) -> Tuple[str, float, float]:
        payload = {"setup": self.SETUP_TYPE, "entry_reason": details.get("entry_reason", self.SETUP_TYPE)}
        payload.update(details)
        self.state.indicators = payload
        logger.info(
            "[%s] %s BUY: setup=%s rr=%.2f entry=%.8f stop=%.8f target=%.8f",
            self.__class__.__name__,
            self.state.pair,
            self.SETUP_TYPE,
            float(details.get("reward_risk", 0.0) or 0.0),
            float(details.get("entry_price", 0.0) or 0.0),
            float(details.get("stop_hint", 0.0) or 0.0),
            float(details.get("target_hint", 0.0) or 0.0),
        )
        return "buy", confidence, strength

    def _common_context(self, market_data: Any, pair: Optional[str]) -> Optional[Dict[str, Any]]:
        trend_df = self._df(market_data, self.trend_timeframe)
        entry_df = self._df(market_data, self.entry_timeframe)
        exec_df = self._df(market_data, self.execution_timeframe)
        if exec_df is None:
            exec_df = entry_df
        if trend_df is None or entry_df is None or exec_df is None:
            self._hold("missing_required_timeframes")
            return None
        if len(trend_df) < 60 or len(entry_df) < 55 or len(exec_df) < 20:
            self._hold("insufficient_candles")
            return None

        required = {"open", "high", "low", "close", "volume"}
        if not required.issubset(trend_df.columns) or not required.issubset(entry_df.columns):
            self._hold("missing_ohlcv_columns")
            return None

        token = self._base_token(pair or self.state.pair)
        if self.focus_tokens and token not in self.focus_tokens:
            self._hold("token_not_in_focus_list", {"token": token})
            return None
        if self.allowed_exchanges:
            ex = str(self.exchange_name or "").lower()
            if ex not in self.allowed_exchanges:
                self._hold("exchange_not_allowed", {"exchange": self.exchange_name})
                return None

        regime = str(getattr(self.state, "market_regime", "unknown") or "unknown").lower()
        if regime in self.blocked_regimes:
            self._hold("blocked_regime", {"market_regime": regime})
            return None

        t_close = trend_df["close"].astype(float)
        t_ema9 = self._ema(t_close, length=9)
        t_ema20 = self._ema(t_close, length=20)
        t_ema50 = self._ema(t_close, length=50)
        t_rsi = self._rsi(t_close, length=self.rsi_period)

        e_close = entry_df["close"].astype(float)
        e_open = entry_df["open"].astype(float)
        e_high = entry_df["high"].astype(float)
        e_low = entry_df["low"].astype(float)
        e_volume = entry_df["volume"].astype(float)
        e_ema9 = self._ema(e_close, length=9)
        e_ema20 = self._ema(e_close, length=20)
        e_ema50 = self._ema(e_close, length=50)
        e_rsi = self._rsi(e_close, length=self.rsi_period)
        e_vwap = session_vwap_hlc3(entry_df, session_tz=self.session_tz)

        series = [t_ema9, t_ema20, t_ema50, t_rsi, e_ema9, e_ema20, e_ema50, e_rsi, e_vwap]
        if any(s is None or len(s) < 3 or pd.isna(s.iloc[-1]) for s in series):
            self._hold("indicator_not_ready")
            return None

        price = float(e_close.iloc[-1])
        trend_ok = (
            float(t_close.iloc[-1]) > float(t_ema20.iloc[-1])
            and float(t_close.iloc[-1]) > float(t_ema50.iloc[-1])
            and float(t_ema9.iloc[-1]) > float(t_ema20.iloc[-1]) > float(t_ema50.iloc[-1])
        )
        above_vwap = price > float(e_vwap.iloc[-1])
        entry_structure_ok = float(e_close.iloc[-1]) > float(e_close.iloc[-3])
        volume_ratio = float(e_volume.iloc[-1] / max(e_volume.rolling(self.volume_window).mean().iloc[-2], 1e-12))
        bullish_candle = float(e_close.iloc[-1]) > float(e_open.iloc[-1])
        rsi_now = float(e_rsi.iloc[-1])

        if not trend_ok:
            self._hold("15m_trend_not_bullish")
            return None
        if not above_vwap:
            self._hold("price_not_above_vwap")
            return None
        if not entry_structure_ok:
            self._hold("5m_structure_not_rising")
            return None
        if not bullish_candle:
            self._hold("5m_confirmation_not_bullish")
            return None
        if rsi_now < self.min_rsi or rsi_now > self.max_rsi:
            self._hold("rsi_out_of_range", {"rsi": rsi_now})
            return None
        if volume_ratio < self.min_volume_multiplier:
            self._hold("bounce_volume_too_low", {"volume_ratio": volume_ratio})
            return None

        return {
            "trend_df": trend_df,
            "entry_df": entry_df,
            "exec_df": exec_df,
            "price": price,
            "ema9": float(e_ema9.iloc[-1]),
            "ema20": float(e_ema20.iloc[-1]),
            "ema50": float(e_ema50.iloc[-1]),
            "vwap": float(e_vwap.iloc[-1]),
            "rsi": rsi_now,
            "volume_ratio": volume_ratio,
            "low": float(e_low.iloc[-1]),
            "high": float(e_high.iloc[-1]),
        }

    def _risk_check(self, entry: float, stop: float, target: float) -> Optional[Dict[str, float]]:
        risk = entry - stop
        reward = target - entry
        if risk <= 0 or reward <= 0:
            return None
        stop_pct = risk / entry
        rr = reward / risk
        if stop_pct > self.max_stop_pct or stop_pct < self.min_stop_pct or rr < self.min_rr:
            return None
        return {"stop_pct": stop_pct, "reward_risk": rr}

    async def generate_signal(
        self,
        market_data,
        indicators_cache: Optional[dict] = None,
        pair: Optional[str] = None,
        timeframe: Optional[str] = None,
        exchange_adapter=None,
    ) -> Tuple[str, float, float]:
        ctx = self._common_context(market_data, pair)
        if ctx is None:
            return "hold", 0.0, 0.0
        return self._evaluate(ctx)

    def _evaluate(self, ctx: Dict[str, Any]) -> Tuple[str, float, float]:
        raise NotImplementedError


class PullbackLongScalpingStrategy(_IntradayLongBaseStrategy):
    STRATEGY_NAME = "Pullback Long Scalping"
    SETUP_TYPE = "pullback_long"

    def _evaluate(self, ctx: Dict[str, Any]) -> Tuple[str, float, float]:
        df = ctx["entry_df"]
        price = ctx["price"]
        held_ema20 = ctx["low"] <= ctx["ema20"] * (1 + self.ema_touch_tolerance_pct)
        held_vwap = ctx["low"] <= ctx["vwap"] * (1 + self.vwap_hold_tolerance_pct) and price > ctx["vwap"]
        if not (held_ema20 or held_vwap):
            return self._hold("no_pullback_to_vwap_or_ema20", ctx)
        stop = min(float(df["low"].tail(4).min()), ctx["vwap"], ctx["ema20"]) * 0.997
        target = max(float(df["high"].tail(24).max()), price * (1 + self.min_rr * max((price - stop) / price, self.min_stop_pct)))
        risk = self._risk_check(price, stop, target)
        if not risk:
            return self._hold("stop_too_wide_or_rr_below_1_5", {"entry_price": price, "stop_hint": stop, "target_hint": target})
        return self._buy(self.buy_confidence, self.buy_strength, {
            **ctx, **risk, "entry_price": price, "stop_hint": stop, "target_hint": target,
            "entry_reason": "Pullback held VWAP/EMA20 with bullish 5m volume confirmation",
        })


class BreakoutRetestLongStrategy(_IntradayLongBaseStrategy):
    STRATEGY_NAME = "Breakout Retest Long"
    SETUP_TYPE = "breakout_retest"

    def _evaluate(self, ctx: Dict[str, Any]) -> Tuple[str, float, float]:
        df = ctx["entry_df"]
        price = ctx["price"]
        lookback = max(10, self.breakout_lookback_bars)
        resistance = float(df["high"].iloc[-lookback:-3].max())
        recent_retest_low = float(df["low"].tail(3).min())
        broke = float(df["close"].iloc[-2]) > resistance or price > resistance
        retested = recent_retest_low <= resistance * (1 + self.retest_tolerance_pct)
        reclaimed = price > resistance
        if not (broke and retested and reclaimed):
            return self._hold("no_confirmed_breakout_retest", {**ctx, "resistance": resistance})
        stop = min(recent_retest_low, resistance * (1 - self.retest_tolerance_pct))
        range_height = max(resistance - float(df["low"].iloc[-lookback:-3].min()), price * 0.01)
        target = max(price + range_height, price + self.min_rr * (price - stop))
        risk = self._risk_check(price, stop, target)
        if not risk:
            return self._hold("stop_too_wide_or_rr_below_1_5", {"entry_price": price, "stop_hint": stop, "target_hint": target, "resistance": resistance})
        return self._buy(self.buy_confidence + 0.02, self.buy_strength + 0.03, {
            **ctx, **risk, "entry_price": price, "stop_hint": stop, "target_hint": target,
            "resistance": resistance,
            "entry_reason": "Breakout retested old resistance and reclaimed support",
        })


class VWAPBounceScalpingStrategy(_IntradayLongBaseStrategy):
    STRATEGY_NAME = "VWAP Bounce Scalping"
    SETUP_TYPE = "vwap_bounce"

    def _evaluate(self, ctx: Dict[str, Any]) -> Tuple[str, float, float]:
        df = ctx["entry_df"]
        price = ctx["price"]
        touched_vwap = ctx["low"] <= ctx["vwap"] * (1 + self.vwap_hold_tolerance_pct)
        ema_above_vwap = ctx["ema9"] > ctx["vwap"] and ctx["ema20"] > ctx["vwap"]
        if not (touched_vwap and ema_above_vwap and price > ctx["vwap"]):
            return self._hold("no_vwap_bounce_reclaim", ctx)
        stop = min(float(df["low"].tail(3).min()), ctx["vwap"] * 0.997)
        target = max(float(df["high"].tail(18).max()), price + self.min_rr * (price - stop))
        risk = self._risk_check(price, stop, target)
        if not risk:
            return self._hold("stop_too_wide_or_rr_below_1_5", {"entry_price": price, "stop_hint": stop, "target_hint": target})
        return self._buy(self.buy_confidence, self.buy_strength, {
            **ctx, **risk, "entry_price": price, "stop_hint": stop, "target_hint": target,
            "entry_reason": "VWAP touch/reclaim with rising volume",
        })


class SmallSizeMomentumScalpStrategy(_IntradayLongBaseStrategy):
    STRATEGY_NAME = "Small-Size Momentum Scalp"
    SETUP_TYPE = "small_size_momentum_scalp"

    def _evaluate(self, ctx: Dict[str, Any]) -> Tuple[str, float, float]:
        df = ctx["entry_df"]
        price = ctx["price"]
        impulse = (price / float(df["close"].iloc[-6]) - 1.0) if len(df) >= 7 else 0.0
        shallow_pullback = float(df["low"].tail(3).min()) >= ctx["ema20"] * (1 - self.ema_touch_tolerance_pct)
        if impulse < 0.006 or not shallow_pullback:
            return self._hold("momentum_or_shallow_pullback_missing", {**ctx, "impulse_6bar_pct": impulse * 100})
        if ctx["rsi"] > min(self.max_rsi, 78.0):
            return self._hold("momentum_rsi_too_hot", {"rsi": ctx["rsi"]})
        stop = float(df["low"].tail(3).min()) * 0.997
        target = max(price * 1.012, price + self.min_rr * (price - stop))
        risk = self._risk_check(price, stop, target)
        if not risk:
            return self._hold("stop_too_wide_or_rr_below_1_5", {"entry_price": price, "stop_hint": stop, "target_hint": target})
        return self._buy(self.buy_confidence + 0.03, self.buy_strength + 0.04, {
            **ctx, **risk, "entry_price": price, "stop_hint": stop, "target_hint": target,
            "position_size_multiplier": 0.5,
            "entry_reason": "Reduced-size momentum scalp after shallow pullback confirmation",
        })
