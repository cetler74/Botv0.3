"""
Hyperliquid perp — Pullback scalping (full intraday TA + short mirror).
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

from strategy.hyperliquid.intraday_base_perp import IntradayPerpBaseStrategy


class PullbackLongScalpingPerpStrategy(IntradayPerpBaseStrategy):
    STRATEGY_NAME = "Pullback Long Scalping Perp"
    SETUP_TYPE = "pullback_long"

    async def _evaluate(self, ctx: Dict[str, Any]) -> Tuple[str, float, float]:
        df = ctx["entry_df"]
        price = ctx["price"]
        held_ema20 = ctx["low"] <= ctx["ema20"] * (1 + self.ema_touch_tolerance_pct)
        held_vwap = ctx["low"] <= ctx["vwap"] * (1 + self.vwap_hold_tolerance_pct) and price > ctx["vwap"]
        if not (held_ema20 or held_vwap):
            return self._hold("no_pullback_to_vwap_or_ema20", ctx)
        stop = min(float(df["low"].tail(4).min()), ctx["vwap"], ctx["ema20"]) * 0.997
        target = max(
            float(df["high"].tail(24).max()),
            price * (1 + self.min_rr * max((price - stop) / price, self.min_stop_pct)),
        )
        risk = self._risk_check(price, stop, target)
        if not risk:
            return self._hold(
                "stop_too_wide_or_rr_below_1_5",
                {"entry_price": price, "stop_hint": stop, "target_hint": target},
            )
        return self._buy(
            self.buy_confidence,
            self.buy_strength,
            {
                **ctx,
                **risk,
                "entry_price": price,
                "stop_hint": stop,
                "target_hint": target,
                "entry_reason": "Pullback held VWAP/EMA20 with bullish 5m volume confirmation",
            },
        )

    async def _evaluate_short(self, ctx: Dict[str, Any]) -> Tuple[str, float, float]:
        """Bearish mirror: pullback to VWAP/EMA20 from above in downtrend."""
        df = ctx["entry_df"]
        price = ctx["price"]
        t_close = ctx["trend_df"]["close"].astype(float)
        t_ema20 = self._ema(t_close, length=20)
        t_ema50 = self._ema(t_close, length=50)
        if t_ema20 is None or t_ema50 is None:
            return self._hold("short_indicators_not_ready")
        trend_down = (
            float(t_close.iloc[-1]) < float(t_ema20.iloc[-1])
            and float(t_close.iloc[-1]) < float(t_ema50.iloc[-1])
            and float(t_ema20.iloc[-1]) < float(t_ema50.iloc[-1])
        )
        if not trend_down:
            return self._hold("short_15m_not_bearish")
        touch_ema = ctx["high"] >= ctx["ema20"] * (1 - self.ema_touch_tolerance_pct)
        touch_vwap = ctx["high"] >= ctx["vwap"] * (1 - self.vwap_hold_tolerance_pct) and price < ctx["vwap"]
        if not (touch_ema or touch_vwap):
            return self._hold("no_pullback_reject_vwap_or_ema20", ctx)
        stop = max(float(df["high"].tail(4).max()), ctx["vwap"], ctx["ema20"]) * 1.003
        target = min(
            float(df["low"].tail(24).min()),
            price * (1 - self.min_rr * max((stop - price) / price, self.min_stop_pct)),
        )
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
                "entry_reason": "HL pullback short: rejection at VWAP/EMA20 in downtrend",
            },
        )
