"""Selective long-only spot swing entries from a seven-day Fibonacci range."""

from typing import Any, Dict, Optional, Tuple
import logging

import pandas as pd

from strategy.base_strategy import BaseStrategy


class WeeklyFibonacciSpotStrategy(BaseStrategy):
    """Buy a 15m-confirmed pullback in a bullish seven-day 1h range."""

    STRATEGY_NAME = "Weekly Fibonacci Spot"

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
        p = config.get("parameters", {})
        self.range_bars = max(168, int(p.get("range_bars", 168)))
        self.trend_fast = max(5, int(p.get("trend_fast_ema", 20)))
        self.trend_slow = max(self.trend_fast + 1, int(p.get("trend_slow_ema", 50)))
        self.entry_fib_shallow = float(p.get("entry_fib_shallow", 0.50))
        self.entry_fib_deep = float(p.get("entry_fib_deep", 0.786))
        self.stop_atr_buffer = float(p.get("stop_atr_buffer", 0.15))
        self.min_target_pct = float(p.get("min_target_pct", 3.0))
        self.max_stop_pct = float(p.get("max_stop_pct", 3.0))
        self.min_reward_risk = float(p.get("min_reward_risk", 2.0))
        self.rsi_period = max(2, int(p.get("rsi_period", 14)))
        self.rsi_min = float(p.get("rsi_min", 35.0))
        self.rsi_max = float(p.get("rsi_max", 58.0))
        self.volume_window = max(2, int(p.get("volume_window", 20)))
        self.min_volume_ratio = float(p.get("min_volume_ratio", 1.0))
        self.buy_confidence = float(p.get("buy_confidence", 0.88))
        self.buy_strength = float(p.get("buy_strength", 0.82))
        self._current_ohlcv = None

    async def initialize(self, pair: str) -> None:
        self.state.pair = pair
        self.state.last_signal = "hold"
        self.state.indicators = {}

    async def update(self, ohlcv: pd.DataFrame) -> None:
        self._current_ohlcv = ohlcv
        self.state.last_signal_time = pd.Timestamp.utcnow().to_pydatetime()

    @staticmethod
    def _rsi(close: pd.Series, period: int) -> pd.Series:
        delta = close.diff()
        gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
        rs = gain / loss.replace(0, float("nan"))
        return 100.0 - (100.0 / (1.0 + rs))

    async def generate_signal(
        self,
        market_data,
        indicators_cache: Optional[dict] = None,
        pair: Optional[str] = None,
        timeframe: Optional[str] = None,
        exchange_adapter=None,
    ) -> Tuple[str, float, float]:
        try:
            if not isinstance(market_data, dict):
                return "hold", 0.0, 0.0
            hourly = market_data.get("1h")
            trigger = market_data.get("15m")
            if hourly is None or trigger is None:
                return "hold", 0.0, 0.0
            required = {"open", "high", "low", "close", "volume"}
            min_hourly = max(self.trend_slow + 2, self.rsi_period + 2, self.range_bars)
            if len(hourly) < min_hourly or len(trigger) < self.volume_window + 2:
                return "hold", 0.0, 0.0
            if not required.issubset(hourly.columns) or not required.issubset(trigger.columns):
                return "hold", 0.0, 0.0

            hclose = hourly["close"].astype(float)
            tclose = trigger["close"].astype(float)
            price = float(tclose.iloc[-1])
            weekly = hourly.iloc[-self.range_bars :]
            swing_high = float(weekly["high"].astype(float).max())
            swing_low = float(weekly["low"].astype(float).min())
            swing_range = swing_high - swing_low
            if price <= 0 or swing_range <= 0:
                return "hold", 0.0, 0.0

            zone_high = swing_high - self.entry_fib_shallow * swing_range
            zone_low = swing_high - self.entry_fib_deep * swing_range
            in_entry_zone = zone_low <= price <= zone_high

            ema_fast = float(hclose.ewm(span=self.trend_fast, adjust=False).mean().iloc[-1])
            ema_slow = float(hclose.ewm(span=self.trend_slow, adjust=False).mean().iloc[-1])
            bullish_trend = ema_fast > ema_slow and float(hclose.iloc[-1]) > ema_fast

            rsi = self._rsi(tclose, self.rsi_period)
            rsi_now = float(rsi.iloc[-1])
            rsi_prev = float(rsi.iloc[-2])
            rsi_recovering = self.rsi_min <= rsi_now <= self.rsi_max and rsi_now > rsi_prev
            bullish_confirmation = (
                float(trigger["close"].iloc[-1]) > float(trigger["open"].iloc[-1])
                and float(trigger["close"].iloc[-1]) > float(trigger["close"].iloc[-2])
            )
            avg_volume = float(trigger["volume"].astype(float).iloc[-self.volume_window :].mean())
            volume_ratio = float(trigger["volume"].iloc[-1]) / avg_volume if avg_volume > 0 else 0.0
            volume_ok = volume_ratio >= self.min_volume_ratio

            tr = pd.concat(
                [
                    hourly["high"].astype(float) - hourly["low"].astype(float),
                    (hourly["high"].astype(float) - hclose.shift()).abs(),
                    (hourly["low"].astype(float) - hclose.shift()).abs(),
                ],
                axis=1,
            ).max(axis=1)
            atr = float(tr.rolling(14).mean().iloc[-1])
            stop_hint = swing_low - self.stop_atr_buffer * atr
            stop_pct = max(0.0, (price - stop_hint) / price * 100.0)

            extensions = [swing_high, swing_high + 0.272 * swing_range, swing_high + 0.618 * swing_range]
            required_target_pct = max(self.min_target_pct, stop_pct * self.min_reward_risk)
            minimum_target = price * (1.0 + required_target_pct / 100.0)
            target_hint = next((level for level in extensions if level >= minimum_target), extensions[-1])
            target_pct = max(0.0, (target_hint - price) / price * 100.0)
            reward_risk = target_pct / stop_pct if stop_pct > 0 else 0.0
            risk_ok = 0 < stop_pct <= self.max_stop_pct and reward_risk >= self.min_reward_risk

            buy = all(
                [bullish_trend, in_entry_zone, rsi_recovering, bullish_confirmation, volume_ok, risk_ok]
            )
            entry_reason = (
                f"Weekly Fibonacci long from 168 closed 1h bars: bullish EMA{self.trend_fast}"
                f">EMA{self.trend_slow} context; price {price:.6f} inside "
                f"{self.entry_fib_shallow:.3f}-{self.entry_fib_deep:.3f} retracement "
                f"[{zone_low:.6f}, {zone_high:.6f}]; 15m confirmation bullish with "
                f"RSI {rsi_prev:.2f}->{rsi_now:.2f} and volume ratio {volume_ratio:.2f}; "
                f"structural SL {stop_hint:.6f}, Fibonacci TP {target_hint:.6f}, R:R {reward_risk:.2f}"
            )
            self.state.indicators.update(
                {
                    "setup": "weekly_fibonacci_spot",
                    "entry_reason": entry_reason if buy else "",
                    "entry_price": price,
                    "stop_hint": stop_hint,
                    "target_hint": target_hint,
                    "stop_pct": stop_pct,
                    "target_pct": target_pct,
                    "reward_risk": reward_risk,
                    "expected_move_pct": target_pct,
                    "weekly_swing_high": swing_high,
                    "weekly_swing_low": swing_low,
                    "fib_entry_zone_low": zone_low,
                    "fib_entry_zone_high": zone_high,
                    "in_entry_zone": in_entry_zone,
                    "range_timeframe": "1h",
                    "range_bars": self.range_bars,
                    "confirmation_timeframe": "15m",
                    "bullish_1h_trend": bullish_trend,
                    "bullish_15m_confirmation": bullish_confirmation,
                    "rsi_15m": rsi_now,
                    "rsi_recovering": rsi_recovering,
                    "volume_ratio": volume_ratio,
                    "volume_ok": volume_ok,
                    "risk_ok": risk_ok,
                    "weekly_fibonacci_buy": buy,
                }
            )
            signal = "buy" if buy else "hold"
            self.state.last_signal = signal
            return (
                signal,
                self.buy_confidence if buy else 0.0,
                self.buy_strength if buy else 0.0,
            )
        except Exception as exc:
            self.logger.error("Weekly Fibonacci signal failed for %s: %s", pair, exc)
            return "hold", 0.0, 0.0

    async def calculate_position_size(self, signal_type: str) -> float:
        return 0.0

    async def should_exit(self) -> bool:
        return False
