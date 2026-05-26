"""
Hyperliquid perp — Breakout retest (full intraday TA + short mirror).
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

from strategy.hyperliquid.intraday_base_perp import IntradayPerpBaseStrategy


class BreakoutRetestPerpStrategy(IntradayPerpBaseStrategy):
    STRATEGY_NAME = "Breakout Retest Perp"
    SETUP_TYPE = "breakout_retest"

    async def _evaluate(self, ctx: Dict[str, Any]) -> Tuple[str, float, float]:
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
            return self._hold(
                "stop_too_wide_or_rr_below_1_5",
                {"entry_price": price, "stop_hint": stop, "target_hint": target, "resistance": resistance},
            )
        return self._buy(
            self.buy_confidence + 0.02,
            self.buy_strength + 0.03,
            {
                **ctx,
                **risk,
                "entry_price": price,
                "stop_hint": stop,
                "target_hint": target,
                "resistance": resistance,
                "entry_reason": "Breakout retested old resistance and reclaimed support",
            },
        )

    async def _evaluate_short(self, ctx: Dict[str, Any]) -> Tuple[str, float, float]:
        df = ctx["entry_df"]
        price = ctx["price"]
        lookback = max(10, self.breakout_lookback_bars)
        support = float(df["low"].iloc[-lookback:-3].min())
        recent_retest_high = float(df["high"].tail(3).max())
        broke = float(df["close"].iloc[-2]) < support or price < support
        retested = recent_retest_high >= support * (1 - self.retest_tolerance_pct)
        reclaimed = price < support
        if not (broke and retested and reclaimed):
            return self._hold("no_confirmed_breakdown_retest", {**ctx, "support": support})
        stop = max(recent_retest_high, support * (1 + self.retest_tolerance_pct))
        range_height = max(float(df["high"].iloc[-lookback:-3].max()) - support, price * 0.01)
        target = min(price - range_height, price - self.min_rr * (stop - price))
        risk = self._risk_check_short(price, stop, target)
        if not risk:
            return self._hold("short_rr_fail", {"entry_price": price, "stop_hint": stop, "target_hint": target})
        return self._sell(
            self.sell_confidence + 0.02,
            self.sell_strength + 0.03,
            {
                **ctx,
                **risk,
                "entry_price": price,
                "stop_hint": stop,
                "target_hint": target,
                "support": support,
                "entry_reason": "HL breakdown retest: support failed and held as resistance",
            },
        )
