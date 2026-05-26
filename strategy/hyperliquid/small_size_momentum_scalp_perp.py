"""
Hyperliquid perp — Small momentum scalp (full intraday TA + short mirror).
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

from strategy.hyperliquid.intraday_base_perp import IntradayPerpBaseStrategy


class SmallSizeMomentumScalpPerpStrategy(IntradayPerpBaseStrategy):
    STRATEGY_NAME = "Small-Size Momentum Scalp Perp"
    SETUP_TYPE = "small_size_momentum_scalp"

    async def _evaluate(self, ctx: Dict[str, Any]) -> Tuple[str, float, float]:
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
        return self._buy(
            self.buy_confidence + 0.03,
            self.buy_strength + 0.04,
            {
                **ctx,
                **risk,
                "entry_price": price,
                "stop_hint": stop,
                "target_hint": target,
                "position_size_multiplier": 0.5,
                "entry_reason": "Reduced-size momentum scalp after shallow pullback confirmation",
            },
        )

    async def _evaluate_short(self, ctx: Dict[str, Any]) -> Tuple[str, float, float]:
        df = ctx["entry_df"]
        price = ctx["price"]
        impulse = (price / float(df["close"].iloc[-6]) - 1.0) if len(df) >= 7 else 0.0
        shallow_pullback = float(df["high"].tail(3).max()) <= ctx["ema20"] * (1 + self.ema_touch_tolerance_pct)
        if impulse > -0.006 or not shallow_pullback:
            return self._hold("short_momentum_missing", {**ctx, "impulse_6bar_pct": impulse * 100})
        if ctx["rsi"] < max(22.0, 100 - self.max_rsi):
            return self._hold("short_rsi_too_cold", {"rsi": ctx["rsi"]})
        stop = float(df["high"].tail(3).max()) * 1.003
        target = min(price * 0.988, price - self.min_rr * (stop - price))
        risk = self._risk_check_short(price, stop, target)
        if not risk:
            return self._hold("short_rr_fail", {"entry_price": price, "stop_hint": stop, "target_hint": target})
        return self._sell(
            self.sell_confidence + 0.03,
            self.sell_strength + 0.04,
            {
                **ctx,
                **risk,
                "entry_price": price,
                "stop_hint": stop,
                "target_hint": target,
                "position_size_multiplier": 0.5,
                "entry_reason": "HL reduced-size momentum short after shallow rejection",
            },
        )
