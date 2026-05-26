"""
Hyperliquid perp — VWAP bounce (full intraday TA + short mirror).
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

from strategy.hyperliquid.intraday_base_perp import IntradayPerpBaseStrategy


class VwapBounceScalpingPerpStrategy(IntradayPerpBaseStrategy):
    STRATEGY_NAME = "VWAP Bounce Perp"
    SETUP_TYPE = "vwap_bounce"

    async def _evaluate(self, ctx: Dict[str, Any]) -> Tuple[str, float, float]:
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
        return self._buy(
            self.buy_confidence,
            self.buy_strength,
            {
                **ctx,
                **risk,
                "entry_price": price,
                "stop_hint": stop,
                "target_hint": target,
                "entry_reason": "VWAP touch/reclaim with rising volume",
            },
        )

    async def _evaluate_short(self, ctx: Dict[str, Any]) -> Tuple[str, float, float]:
        df = ctx["entry_df"]
        price = ctx["price"]
        touched_vwap = ctx["high"] >= ctx["vwap"] * (1 - self.vwap_hold_tolerance_pct)
        ema_below_vwap = ctx["ema9"] < ctx["vwap"] and ctx["ema20"] < ctx["vwap"]
        if not (touched_vwap and ema_below_vwap and price < ctx["vwap"]):
            return self._hold("no_vwap_reject", ctx)
        stop = max(float(df["high"].tail(3).max()), ctx["vwap"] * 1.003)
        target = min(float(df["low"].tail(18).min()), price - self.min_rr * (stop - price))
        risk = self._risk_check_short(price, stop, target)
        if not risk:
            return self._hold("short_rr_fail", {"entry_price": price, "stop_hint": stop, "target_hint": target})
        return self._sell(
            self.sell_confidence,
            self.sell_strength,
            {
                **ctx,
                **risk,
                "entry_price": price,
                "stop_hint": stop,
                "target_hint": target,
                "entry_reason": "HL VWAP reject short: touch from above, close below VWAP",
            },
        )
